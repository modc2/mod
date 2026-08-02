"""
registry — the aggregation layer of the MCP hub.

The Model Context Protocol ecosystem is scattered across half a dozen
directories that each know a different slice of it: the official registry has
canonical install metadata but no popularity signal, GitHub has stars and
licenses but no transport info, npm has download counts, Glama and Smithery
have curated tool lists, the awesome-lists have the long tail, and the local
mod fleet has servers that exist on this box alone. Each provider below is a
small adapter that fetches its slice and normalizes it into ONE record shape,
so a search for "postgres" returns a single ranked list instead of six.

Providers (all public, none require a key):
    official   registry.modelcontextprotocol.io — the canonical MCP registry
    github     GitHub repo search (topic:mcp-server + free text) — stars, license
    npm        npm registry search — download counts, npx install lines
    glama      the Glama MCP directory — SPDX licenses, per-server tool lists
    smithery   the Smithery registry — hosted/remote servers, use counts
    awesome    curated awesome-mcp-servers READMEs — the long tail
    fleet      MCP servers running in this mod fleet (config.json `mcp` key)
    hub        servers published to this hub by wallet-signed submission

Open source is the point: every record carries `open_source` (a reachable
public repo) and `license` when the provider tells us, search defaults to
oss=true, and ranking rewards a known OSI license. Closed/hosted-only servers
are still indexed — you just have to ask for them.

Duplicates are expected and desirable: the same project listed by four
providers merges into one card (keyed on the normalized repo URL) that keeps
the best field from each — GitHub's stars, npm's downloads, Glama's license,
the official registry's install metadata.

Everything is cached to disk (per-provider TTL) because a scan fans out to
every provider at once and GitHub's anonymous budget is 10 searches/minute.
One dead provider never fails a search — it reports its own error alongside
the partial results.
"""
import json
import math
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

UA = 'mod-mcp-hub/1.0 (+https://github.com/mod)'

HUB_DIR = Path(os.environ.get('MCP_HUB_DIR') or os.path.expanduser('~/.mod/mcp'))

# ── provider catalog ────────────────────────────────────────────────
# ttl: seconds a fetch stays fresh on disk. weight: trust applied before
# relevance when ranking (the canonical registry outranks a README scrape).

PROVIDERS: List[Dict[str, Any]] = [
    {'id': 'official', 'label': 'MCP Registry', 'ttl': 1800, 'weight': 1.6,
     'url': 'https://registry.modelcontextprotocol.io',
     'about': 'The official Model Context Protocol registry — canonical names, versions and install metadata'},
    {'id': 'github', 'label': 'GitHub', 'ttl': 900, 'weight': 1.3,
     'url': 'https://github.com/topics/mcp-server',
     'about': 'Repo search across topic:mcp-server and free text — stars, licenses, last push'},
    {'id': 'npm', 'label': 'npm', 'ttl': 1800, 'weight': 1.0,
     'url': 'https://www.npmjs.com/search?q=keywords:mcp-server',
     'about': 'Published npm MCP servers — monthly downloads and npx install lines'},
    {'id': 'glama', 'label': 'Glama', 'ttl': 1800, 'weight': 1.1,
     'url': 'https://glama.ai/mcp/servers',
     'about': 'The Glama MCP directory — SPDX licenses and per-server tool listings'},
    {'id': 'smithery', 'label': 'Smithery', 'ttl': 1800, 'weight': 1.0,
     'url': 'https://smithery.ai',
     'about': 'The Smithery registry — hosted remote servers with use counts'},
    {'id': 'awesome', 'label': 'Awesome Lists', 'ttl': 21600, 'weight': 0.9,
     'url': 'https://github.com/punkpeye/awesome-mcp-servers',
     'about': 'Curated community indexes — the long tail nothing else lists'},
    {'id': 'fleet', 'label': 'Mod Fleet', 'ttl': 60, 'weight': 1.5,
     'url': 'http://localhost',
     'about': 'MCP servers running in this mod fleet, reachable right now'},
    {'id': 'hub', 'label': 'Hub', 'ttl': 0, 'weight': 1.7,
     'url': '',
     'about': 'Servers published here by wallet-signed submission, manifests pinned by CID'},
]

PROVIDER_IDS = [p['id'] for p in PROVIDERS]
PROVIDER_BY_ID = {p['id']: p for p in PROVIDERS}

# (raw markdown url, label) — parsed into entries, cached 6h
AWESOME_LISTS = [
    ('https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md',
     'awesome-mcp-servers (punkpeye)'),
    ('https://raw.githubusercontent.com/wong2/awesome-mcp-servers/main/README.md',
     'awesome-mcp-servers (wong2)'),
]

# GitHub repo topics that mark a repo as an MCP server
MCP_TOPICS = ['mcp-server', 'mcp-servers', 'modelcontextprotocol', 'mcp']

# Licenses we count as open source when deciding the `open_source` flag.
OSI_LICENSES = {
    'mit', 'apache-2.0', 'bsd-2-clause', 'bsd-3-clause', 'gpl-2.0', 'gpl-3.0',
    'agpl-3.0', 'lgpl-2.1', 'lgpl-3.0', 'mpl-2.0', 'isc', 'unlicense', 'cc0-1.0',
    'epl-2.0', 'zlib', 'artistic-2.0', 'bsl-1.0',
}

# Coarse categories, keyed off name/description/tags — enough to browse by,
# cheap enough to compute on every record.
CATEGORIES = {
    'dev': ['git', 'github', 'gitlab', 'code', 'ide', 'editor', 'lint', 'compiler',
            'debug', 'ci', 'test', 'jira', 'linear'],
    'data': ['sql', 'postgres', 'mysql', 'sqlite', 'database', 'duckdb', 'mongo',
             'redis', 'bigquery', 'snowflake', 'analytics', 'warehouse', 'vector'],
    'web': ['browser', 'scrape', 'crawl', 'puppeteer', 'playwright', 'fetch',
            'http', 'search', 'web'],
    'cloud': ['aws', 'gcp', 'azure', 'kubernetes', 'docker', 'terraform', 'deploy',
              'cloudflare', 'serverless', 'infra'],
    'files': ['file', 'filesystem', 'drive', 'dropbox', 's3', 'storage', 'pdf',
              'document', 'notion', 'obsidian'],
    'comms': ['slack', 'discord', 'email', 'gmail', 'telegram', 'sms', 'twilio',
              'calendar', 'teams'],
    'ai': ['llm', 'openai', 'anthropic', 'claude', 'embedding', 'rag', 'agent',
           'memory', 'model', 'inference'],
    'finance': ['stock', 'crypto', 'trading', 'payment', 'stripe', 'finance',
                'market', 'bank', 'wallet', 'blockchain'],
    'security': ['security', 'vault', 'secret', 'auth', 'scan', 'cve', 'pentest'],
}

MD_LINK = re.compile(r'^\s*[-*]\s*\[([^\]]{1,120})\]\((https?://[^)\s]+)\)\s*(.*)$')
MD_IMAGE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
MD_INLINE_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')


def norm_repo(url: str) -> str:
    """Canonical key for a repo URL, so the same project listed by four
    providers collapses to one card."""
    if not url:
        return ''
    u = str(url).strip().lower().rstrip('/')
    u = re.sub(r'^https?://(www\.)?', '', u)
    u = re.sub(r'^git\+', '', u)
    u = re.sub(r'\.git$', '', u)
    m = re.match(r'github\.com/([^/]+)/([^/#?]+)', u)
    return f'github.com/{m.group(1)}/{m.group(2)}' if m else u


def _clean_md(text: str) -> str:
    text = MD_IMAGE.sub('', text or '')
    text = MD_INLINE_LINK.sub(r'\1', text)
    text = re.sub(r'^[\s\-–—:|]+', '', text)
    text = re.sub(r'[*`]', '', text)
    return re.sub(r'\s{2,}', ' ', text).strip()


def categorize(rec: Dict[str, Any]) -> List[str]:
    hay = ' '.join([str(rec.get('name') or ''), str(rec.get('title') or ''),
                    str(rec.get('description') or ''),
                    ' '.join(str(t) for t in (rec.get('tags') or []))]).lower()
    return [cat for cat, words in CATEGORIES.items() if any(w in hay for w in words)]


def record(**kw) -> Dict[str, Any]:
    """The one shape every provider normalizes into."""
    rec: Dict[str, Any] = {
        'id': '', 'source': '', 'name': '', 'title': '', 'description': '',
        'repo': '', 'homepage': '', 'author': '', 'license': None,
        'stars': None, 'downloads': None, 'tags': [], 'transports': [],
        'remotes': [], 'packages': [], 'install': {}, 'updated': None,
        'tools': None, 'cid': None, 'version': None,
    }
    rec.update(kw)
    rec['repo'] = rec['repo'] or ''
    lic = (rec.get('license') or '').lower() if rec.get('license') else None
    # Open source = the code is actually there to read. A public repo counts;
    # a permissive license we recognize counts even harder (used in ranking).
    rec['open_source'] = bool(norm_repo(rec['repo']))
    rec['osi'] = bool(lic and lic in OSI_LICENSES)
    rec['repo_key'] = norm_repo(rec['repo'])
    rec['categories'] = categorize(rec)
    return rec


class Registry:
    """Fan-out search across every MCP directory, cached on disk.

    `hub` is an optional object exposing `records()` — the hub's own
    wallet-signed submissions, folded in as just another provider so they rank
    and dedupe alongside everything else.
    """

    def __init__(self, dir: Optional[str] = None, timeout: int = 12,
                 hub: Any = None, fleet_root: Optional[str] = None):
        self.dir = Path(dir or HUB_DIR)
        self.cache_dir = self.dir / 'cache'
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.hub = hub
        # Repo root that holds core/ and orbit/ — this module is orbit/mcp.
        self.fleet_root = Path(fleet_root or Path(__file__).resolve().parents[3])

    # ── http + cache ────────────────────────────────────────────────

    def github_token(self) -> Optional[str]:
        tok = os.environ.get('MCP_GITHUB_TOKEN') or os.environ.get('GITHUB_TOKEN') \
            or os.environ.get('GH_TOKEN')
        if tok:
            return tok.strip()
        p = self.dir / 'github.token'
        try:
            return p.read_text().strip() or None
        except Exception:
            return None

    def _headers(self, gh: bool = False) -> Dict[str, str]:
        h = {'User-Agent': UA, 'Accept': 'application/json'}
        if gh:
            h['Accept'] = 'application/vnd.github+json'
            tok = self.github_token()
            if tok:
                h['Authorization'] = f'Bearer {tok}'
        return h

    def _get(self, url: str, gh: bool = False, params: Optional[Dict] = None,
             raw: bool = False) -> Any:
        r = requests.get(url, headers=self._headers(gh), params=params,
                         timeout=self.timeout)
        r.raise_for_status()
        return r.text if raw else r.json()

    def _cache_path(self, key: str) -> Path:
        safe = re.sub(r'[^a-z0-9._-]+', '_', key.lower())[:120]
        return self.cache_dir / f'{safe}.json'

    def cache_get(self, key: str, ttl: int) -> Any:
        if ttl <= 0:
            return None
        p = self._cache_path(key)
        try:
            blob = json.loads(p.read_text())
        except Exception:
            return None
        if time.time() - blob.get('ts', 0) > ttl:
            return None
        return blob.get('value')

    def cache_put(self, key: str, value: Any) -> None:
        try:
            self._cache_path(key).write_text(
                json.dumps({'ts': time.time(), 'value': value}))
        except Exception:
            pass  # cache is an optimization; never fail a search over it

    def clear_cache(self) -> Dict[str, int]:
        n = 0
        for p in self.cache_dir.glob('*.json'):
            try:
                p.unlink()
                n += 1
            except Exception:
                pass
        return {'cleared': n}

    def cache_state(self) -> Dict[str, Any]:
        files = list(self.cache_dir.glob('*.json'))
        return {'entries': len(files),
                'bytes': sum(f.stat().st_size for f in files),
                'dir': str(self.cache_dir)}

    # ── providers ───────────────────────────────────────────────────

    def src_official(self, q: str, limit: int) -> List[Dict]:
        """The official MCP registry. It publishes every *version* of a server,
        so keep the newest record per name."""
        params: Dict[str, Any] = {'limit': min(max(limit, 30), 100)}
        if q.strip():
            params['search'] = q.strip()
        data = self._get('https://registry.modelcontextprotocol.io/v0/servers',
                         params=params)
        latest: Dict[str, Dict] = {}
        for rec in data.get('servers', []):
            s = rec.get('server', rec) or {}
            name = s.get('name', '')
            if not name:
                continue
            meta = (rec.get('_meta') or {}).get(
                'io.modelcontextprotocol.registry/official') or {}
            prev = latest.get(name)
            if prev and not meta.get('isLatest') and prev[1].get('isLatest'):
                continue
            latest[name] = (s, meta)
        out = []
        for name, (s, meta) in latest.items():
            remotes = [{'type': r.get('type'), 'url': r.get('url')}
                       for r in (s.get('remotes') or []) if r.get('url')]
            pkgs = [{'registry': p.get('registryType') or p.get('registry_type'),
                     'identifier': p.get('identifier') or p.get('name'),
                     'version': p.get('version')}
                    for p in (s.get('packages') or [])]
            install = {}
            if remotes:
                install['remote'] = remotes[0]['url']
            for p in pkgs:
                reg, ident = (p['registry'] or '').lower(), p['identifier'] or ''
                if reg == 'npm' and ident:
                    install.setdefault('npx', f'npx -y {ident}')
                elif reg in ('pypi', 'pip') and ident:
                    install.setdefault('uvx', f'uvx {ident}')
                elif reg in ('oci', 'docker') and ident:
                    install.setdefault('docker', f'docker run -i --rm {ident}')
            out.append(record(
                id=f'official:{name}', source='official',
                name=name.split('/')[-1] or name,
                title=s.get('title') or name, description=s.get('description', ''),
                repo=(s.get('repository') or {}).get('url', ''),
                homepage=s.get('websiteUrl', '') or '',
                author=name.split('/')[0] if '/' in name else '',
                tags=['mcp'], transports=sorted({r['type'] for r in remotes if r['type']})
                or (['stdio'] if pkgs else []),
                remotes=remotes, packages=pkgs, install=install,
                version=s.get('version'), updated=meta.get('updatedAt'),
            ))
        return out

    def _gh_repo(self, repo: Dict) -> Dict:
        lic = (repo.get('license') or {}).get('spdx_id') or None
        if lic in ('NOASSERTION', 'NONE'):
            lic = None
        full = repo.get('full_name', '')
        return record(
            id=f'github:{full}', source='github', name=repo.get('name', ''),
            title=full, description=repo.get('description') or '',
            repo=repo.get('html_url', ''), homepage=repo.get('homepage') or '',
            author=(repo.get('owner') or {}).get('login', ''),
            license=lic, stars=repo.get('stargazers_count'),
            tags=(repo.get('topics') or [])[:8],
            transports=['stdio'], updated=repo.get('pushed_at'),
            install={'repo': f"git clone {repo.get('clone_url', '')}"},
        )

    def src_github(self, q: str, limit: int) -> List[Dict]:
        """Repo search. Anonymous budget is 10 searches/min, so one query only:
        topic-scoped when browsing, free text (still MCP-scoped) when searching."""
        if q.strip():
            query = f'{q.strip()} mcp server in:name,description,topics'
        else:
            query = 'topic:mcp-server'
        data = self._get('https://api.github.com/search/repositories', gh=True,
                         params={'q': query, 'sort': 'stars', 'order': 'desc',
                                 'per_page': min(max(limit, 20), 50)})
        out = []
        for repo in data.get('items', []):
            topics = [t.lower() for t in (repo.get('topics') or [])]
            hay = f"{repo.get('name', '')} {repo.get('description') or ''}".lower()
            # Free-text search drags in anything mentioning MCP; keep repos that
            # actually claim to be one.
            if not (any(t in topics for t in MCP_TOPICS) or 'mcp' in hay):
                continue
            out.append(self._gh_repo(repo))
        return out

    def src_npm(self, q: str, limit: int) -> List[Dict]:
        """npm — the biggest source of stdio servers, and the only one with
        real download counts."""
        text = f'{q.strip()} keywords:mcp-server' if q.strip() else 'keywords:mcp-server'
        data = self._get('https://registry.npmjs.org/-/v1/search',
                         params={'text': text, 'size': min(max(limit, 20), 50)})
        out = []
        for obj in data.get('objects', []):
            pkg = obj.get('package', {})
            links = pkg.get('links', {}) or {}
            name = pkg.get('name', '')
            kws = [str(k) for k in (pkg.get('keywords') or [])]
            if not name:
                continue
            out.append(record(
                id=f'npm:{name}', source='npm', name=name.split('/')[-1],
                title=name, description=pkg.get('description', '') or '',
                repo=links.get('repository') or '',
                homepage=links.get('homepage') or links.get('npm') or '',
                author=(pkg.get('publisher') or {}).get('username', ''),
                license=pkg.get('license'), tags=kws[:8], transports=['stdio'],
                packages=[{'registry': 'npm', 'identifier': name,
                           'version': pkg.get('version')}],
                install={'npx': f'npx -y {name}'},
                downloads=(obj.get('downloads') or {}).get('monthly'),
                version=pkg.get('version'), updated=pkg.get('date') or obj.get('updated'),
            ))
        return out

    def src_glama(self, q: str, limit: int) -> List[Dict]:
        """Glama — carries SPDX licenses and, for many servers, the tool list."""
        params: Dict[str, Any] = {'first': min(max(limit, 20), 50)}
        if q.strip():
            params['query'] = q.strip()
        data = self._get('https://glama.ai/api/mcp/v1/servers', params=params)
        out = []
        for s in data.get('servers', []):
            lic = s.get('spdxLicense') or {}
            tools = s.get('tools') or []
            attrs = [str(a) for a in (s.get('attributes') or [])]
            remote = any('remote' in a for a in attrs)
            slug = s.get('slug') or s.get('id')
            out.append(record(
                id=f"glama:{s.get('id')}", source='glama',
                name=s.get('name') or slug or '', title=s.get('name') or slug or '',
                description=s.get('description', '') or '',
                repo=(s.get('repository') or {}).get('url', ''),
                homepage=s.get('url') or '', author=s.get('namespace', ''),
                license=(lic.get('name') or '').replace(' License', '') or None,
                tags=['mcp'] + [a.split(':')[-1] for a in attrs][:5],
                transports=['streamable-http'] if remote else ['stdio'],
                tools=len(tools) or None,
                install={'tools': [t.get('name') for t in tools][:30]} if tools else {},
            ))
        return out

    def src_smithery(self, q: str, limit: int) -> List[Dict]:
        """Smithery — mostly hosted remote servers; useful for what you can use
        without installing anything."""
        params: Dict[str, Any] = {'pageSize': min(max(limit, 20), 50)}
        if q.strip():
            params['q'] = q.strip()
        data = self._get('https://registry.smithery.ai/servers', params=params)
        out = []
        for s in data.get('servers', []):
            qual = s.get('qualifiedName') or s.get('namespace') or ''
            if not qual:
                continue
            home = s.get('homepage') or f'https://smithery.ai/server/{qual}'
            remote_url = f'https://server.smithery.ai/{qual}/mcp'
            out.append(record(
                id=f'smithery:{qual}', source='smithery',
                name=s.get('displayName') or qual, title=s.get('displayName') or qual,
                description=s.get('description', '') or '',
                repo=home if 'github.com' in home else '',
                homepage=home, author=s.get('namespace', ''),
                tags=['mcp'] + (['verified'] if s.get('verified') else [])
                + (['remote'] if s.get('remote') else []),
                transports=['streamable-http'] if s.get('remote') else ['stdio'],
                remotes=[{'type': 'streamable-http', 'url': remote_url}]
                if s.get('remote') else [],
                install={'remote': remote_url} if s.get('remote')
                else {'cli': f'npx -y @smithery/cli install {qual}'},
                downloads=s.get('useCount'), updated=s.get('createdAt'),
            ))
        return out

    def _awesome_list(self, url: str, label: str) -> List[Dict]:
        """Parse one awesome-list README into entries. Cached hard: these move
        slowly and are ~1 MB each."""
        cached = self.cache_get(f'awesome_{label}', 21600)
        if cached is not None:
            return cached
        try:
            md = self._get(url, raw=True)
        except Exception:
            return []
        out, section = [], ''
        for line in md.splitlines():
            if line.startswith('#'):
                section = _clean_md(line.lstrip('# ').strip())
                continue
            m = MD_LINK.match(line)
            if not m:
                continue
            name, link, desc = m.group(1), m.group(2), _clean_md(m.group(3))
            key = norm_repo(link)
            if not key.startswith('github.com/'):
                continue
            owner_repo = key[len('github.com/'):]
            out.append(record(
                id=f'awesome:{owner_repo}', source='awesome',
                name=_clean_md(name) or owner_repo.split('/')[-1],
                title=owner_repo, description=desc,
                repo=f'https://{key}', author=owner_repo.split('/')[0],
                tags=['mcp'] + ([section.lower()] if section and len(section) < 30 else []),
                transports=['stdio'],
                install={'repo': f'git clone https://{key}.git'},
                homepage=label,
            ))
        # De-dup within a list (awesome-lists repeat entries across sections).
        seen, uniq = set(), []
        for r in out:
            if r['repo_key'] in seen:
                continue
            seen.add(r['repo_key'])
            uniq.append(r)
        self.cache_put(f'awesome_{label}', uniq)
        return uniq

    def src_awesome(self, q: str, limit: int) -> List[Dict]:
        out: List[Dict] = []
        for url, label in AWESOME_LISTS:
            out.extend(self._awesome_list(url, label))
        if not q.strip():
            return out[:limit * 3]
        terms = q.lower().split()
        return [r for r in out if all(
            t in f"{r['name']} {r['title']} {r['description']} {' '.join(r['tags'])}".lower()
            for t in terms)]

    def src_fleet(self, q: str, limit: int) -> List[Dict]:
        """MCP servers running right here in the mod fleet.

        A module advertises one by putting `mcp` in its config.json — either a
        URL string, an endpoints entry, or the fn name. The gateway serves it
        at /api/{name}/mcp; the local URL is what a client on this box uses.
        """
        out = []
        for group in ('core', 'orbit'):
            for cfg_path in sorted((self.fleet_root / group).glob('*/config.json')):
                try:
                    cfg = json.loads(cfg_path.read_text())
                except Exception:
                    continue
                name = cfg.get('name') or cfg_path.parent.name
                if name == 'mcp':
                    continue  # the hub itself — listed separately by /stats
                endpoints = cfg.get('endpoints') or {}
                declared = ('mcp' in endpoints or 'mcp' in cfg
                            or 'mcp' in (cfg.get('fns') or []))
                if not declared:
                    continue
                port = cfg.get('port') or (cfg.get('ports') or {}).get('api')
                if not port:
                    continue
                url = cfg['mcp'] if isinstance(cfg.get('mcp'), str) and \
                    cfg['mcp'].startswith('http') else f'http://localhost:{port}/mcp'
                docs = ''
                if isinstance(endpoints.get('mcp'), dict):
                    docs = endpoints['mcp'].get('docs', '')
                out.append(record(
                    id=f'fleet:{name}', source='fleet', name=name,
                    title=cfg.get('title') or name,
                    description=cfg.get('description', '') or docs,
                    repo='', homepage=f'/{name}', author='mod',
                    license='MIT', tags=['mcp', 'mod', 'local'],
                    transports=['streamable-http'],
                    remotes=[{'type': 'streamable-http', 'url': url}],
                    install={'remote': url},
                    version=cfg.get('version'),
                ))
                # Fleet modules are local source trees — open source by
                # definition, even though there is no github.com URL to point at.
                out[-1]['open_source'] = True
                out[-1]['osi'] = True
        if q.strip():
            terms = q.lower().split()
            out = [r for r in out if all(
                t in f"{r['name']} {r['description']}".lower() for t in terms)]
        return out

    def src_hub(self, q: str, limit: int) -> List[Dict]:
        """Wallet-signed submissions published to this hub."""
        if not self.hub:
            return []
        recs = [record(**r) for r in self.hub.records()]
        if q.strip():
            terms = q.lower().split()
            recs = [r for r in recs if all(
                t in f"{r['name']} {r['title']} {r['description']} {' '.join(r['tags'])}".lower()
                for t in terms)]
        return recs

    def providers(self) -> List[Dict]:
        """The provider catalog, with auth state for the UI."""
        tok = bool(self.github_token())
        return [{**p, 'auth': ('token' if tok else 'anonymous')
                 if p['id'] == 'github' else 'open'} for p in PROVIDERS]

    # ── fan-out, merge, rank ────────────────────────────────────────

    def _fetch(self, source: str, q: str, limit: int) -> Dict[str, Any]:
        fn: Optional[Callable] = getattr(self, f'src_{source}', None)
        if fn is None:
            return {'source': source, 'error': 'unknown provider', 'items': []}
        ttl = PROVIDER_BY_ID[source]['ttl']
        key = f'{source}_{q.strip().lower()}_{limit}'
        hit = self.cache_get(key, ttl)
        if hit is not None:
            return {'source': source, 'items': hit, 'cached': True}
        try:
            items = fn(q, limit)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else '?'
            hint = ' (rate limited — set a GitHub token)' if code == 403 else ''
            return {'source': source, 'error': f'HTTP {code}{hint}', 'items': []}
        except Exception as e:
            return {'source': source, 'error': f'{type(e).__name__}: {e}', 'items': []}
        self.cache_put(key, items)
        return {'source': source, 'items': items, 'cached': False}

    @staticmethod
    def _merge(a: Dict, b: Dict) -> Dict:
        """Fold b into a: keep a's identity, take whatever field b knows better.

        Providers are lopsided — GitHub has stars, npm has downloads, Glama has
        the license, the official registry has install metadata — so a merged
        card is strictly more informative than any single listing.
        """
        out = dict(a)
        out['sources'] = sorted(set(a.get('sources') or [a['source']]) |
                                set(b.get('sources') or [b['source']]))
        out['ids'] = sorted(set(a.get('ids') or [a['id']]) | set(b.get('ids') or [b['id']]))
        for field in ('license', 'stars', 'downloads', 'tools', 'version', 'homepage',
                      'author', 'updated', 'cid'):
            if not out.get(field) and b.get(field):
                out[field] = b[field]
        if len(b.get('description') or '') > len(out.get('description') or ''):
            out['description'] = b['description']
        out['tags'] = sorted({*(out.get('tags') or []), *(b.get('tags') or [])})[:12]
        out['transports'] = sorted({*(out.get('transports') or []),
                                    *(b.get('transports') or [])})
        out['remotes'] = (out.get('remotes') or []) + [
            r for r in (b.get('remotes') or [])
            if r.get('url') not in {x.get('url') for x in (out.get('remotes') or [])}]
        out['packages'] = (out.get('packages') or []) + [
            p for p in (b.get('packages') or [])
            if p.get('identifier') not in {x.get('identifier') for x in (out.get('packages') or [])}]
        out['install'] = {**(b.get('install') or {}), **(out.get('install') or {})}
        out['open_source'] = bool(out.get('open_source') or b.get('open_source'))
        out['osi'] = bool(out.get('osi') or b.get('osi'))
        lic = (out.get('license') or '').lower()
        if lic and lic in OSI_LICENSES:
            out['osi'] = True
        out['categories'] = sorted({*(out.get('categories') or []),
                                    *(b.get('categories') or [])})
        return out

    def _score(self, rec: Dict, q: str) -> float:
        """Relevance × provider trust × popularity, with an open-source thumb
        on the scale — this hub is for servers you can read the source of."""
        weight = max(PROVIDER_BY_ID[s]['weight'] for s in
                     (rec.get('sources') or [rec['source']]) if s in PROVIDER_BY_ID)
        name = f"{rec.get('name', '')} {rec.get('title', '')}".lower()
        desc = (rec.get('description') or '').lower()
        tags = ' '.join(rec.get('tags') or []).lower()
        rel = 1.0
        for term in [t for t in q.lower().split() if t]:
            if term in name:
                rel += 3.0
            if name.startswith(term):
                rel += 2.0
            if term in tags:
                rel += 1.0
            if term in desc:
                rel += 0.7
        pop = math.log10(1 + (rec.get('stars') or 0)) * 1.2 \
            + math.log10(1 + (rec.get('downloads') or 0)) * 0.6
        oss = (1.4 if rec.get('open_source') else 0.0) + (0.6 if rec.get('osi') else 0.0)
        listed = 0.5 * (len(rec.get('sources') or [rec['source']]) - 1)
        probed = 0.8 if rec.get('tools') else 0.0
        return round(weight * rel + pop + oss + listed + probed, 4)

    def search(self, q: str = '', sources: Optional[List[str]] = None,
               limit: int = 40, oss: bool = True, transport: str = '',
               license: str = '', tag: str = '', category: str = '',
               sort: str = 'relevance') -> Dict[str, Any]:
        """Scan every provider at once and return one ranked, merged list."""
        wanted = [s for s in (sources or PROVIDER_IDS) if s in PROVIDER_IDS]
        if not wanted:
            wanted = list(PROVIDER_IDS)
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            results = list(pool.map(lambda s: self._fetch(s, q, limit), wanted))

        merged: Dict[str, Dict] = {}
        errors, per_source = {}, {}
        for res in results:
            if res.get('error'):
                errors[res['source']] = res['error']
            per_source[res['source']] = len(res['items'])
            for item in res['items']:
                rec = dict(item)
                rec.setdefault('sources', [rec['source']])
                rec.setdefault('ids', [rec['id']])
                key = rec.get('repo_key') or rec['id']
                merged[key] = self._merge(merged[key], rec) if key in merged else rec

        items = list(merged.values())
        if oss:
            items = [r for r in items if r.get('open_source')]
        if transport:
            items = [r for r in items if transport in (r.get('transports') or [])]
        if license:
            items = [r for r in items if (r.get('license') or '').lower() == license.lower()]
        if tag:
            items = [r for r in items if tag.lower() in
                     [t.lower() for t in (r.get('tags') or [])]]
        if category:
            items = [r for r in items if category in (r.get('categories') or [])]

        for r in items:
            r['score'] = self._score(r, q)
        keys = {'relevance': lambda r: -r['score'],
                'stars': lambda r: -(r.get('stars') or 0),
                'downloads': lambda r: -(r.get('downloads') or 0),
                'new': lambda r: str(r.get('updated') or ''),
                'name': lambda r: (r.get('name') or '').lower()}
        items.sort(key=keys.get(sort, keys['relevance']),
                   reverse=(sort == 'new'))
        items = items[:limit]
        self._remember(items)
        return {
            'q': q, 'count': len(items), 'sort': sort,
            'sources': wanted, 'per_source': per_source, 'errors': errors,
            'filters': {'oss': oss, 'transport': transport, 'license': license,
                        'tag': tag, 'category': category},
            'servers': items,
        }

    # ── recall: /server?id= needs a card we already built ───────────

    def _index_path(self) -> Path:
        return self.dir / 'seen.json'

    def _remember(self, items: List[Dict]) -> None:
        """Keep the last few thousand merged cards so a detail lookup by id
        doesn't have to re-scan every provider."""
        try:
            seen = json.loads(self._index_path().read_text())
        except Exception:
            seen = {}
        for r in items:
            for i in (r.get('ids') or [r['id']]):
                seen[i] = r
            if r.get('repo_key'):
                seen[r['repo_key']] = r
        if len(seen) > 4000:
            seen = dict(list(seen.items())[-3000:])
        try:
            self._index_path().write_text(json.dumps(seen))
        except Exception:
            pass

    def recall(self, id: str) -> Optional[Dict]:
        try:
            return json.loads(self._index_path().read_text()).get(id)
        except Exception:
            return None

    def server(self, id: str) -> Dict[str, Any]:
        """One merged card. Falls back to a targeted scan when the id is cold —
        a fresh hub, or a link someone shared from another machine."""
        hit = self.recall(id) or self.recall(norm_repo(id))
        if hit:
            return hit
        source, _, rest = id.partition(':')
        if source == 'hub' and self.hub:
            rec = self.hub.get(rest)
            if rec:
                return record(**rec)
        if source == 'fleet':
            for r in self.src_fleet('', 100):
                if r['id'] == id:
                    return r
        # Cold id: search for its tail and look for an exact match.
        needle = (rest or id).split('/')[-1].split(':')[0]
        res = self.search(needle, limit=60, oss=False)
        for r in res['servers']:
            if id in (r.get('ids') or []) or r['id'] == id:
                return r
        raise KeyError(f'no such server: {id}')
