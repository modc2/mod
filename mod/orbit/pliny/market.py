#!/usr/bin/env python3
"""plinyville market — every elder-plinius repo, materialized as its own mod.

The mirror (`plinyville.py`) answers "what does elder-plinius have and what is in
it". The *market* goes one step further: it takes each repo, **archives its
content into the store mod**, and presents it as a self-contained mod with its
own app, api and MCP server — all multiplexed by the one plinyville process and
addressed under `/m/<repo>`.

Storage: the store mod's tree at `~/.mod/store/plinyville/…` (same JSON files
`m store/put_json` writes, so `m store/get_json plinyville/market` reads them
back). Layout:

    plinyville/market.json            — the catalog index (installed mods, sizes, ids)
    plinyville/mods/<repo>/manifest   — the generated mod descriptor for one repo
    plinyville/mods/<repo>/content    — the archived bundle (meta, tree, files, readme)

This file is zero-dependency (stdlib + `Ville`): the servers import it directly
without pulling in the mod protocol, so `import mod` here can never shadow the
protocol package. The CLI layer (`mod.py`) adds the protocol niceties — a real
localfs CID per bundle, on-chain registration — on top of what is written here.
"""
import hashlib
import json
import os
import re
import time

from plinyville import VERSION, GitHubError, Ville

# The store mod's data root. Writing JSON here == m store/put_json(<ns>/…).
STORE_ROOT = os.path.expanduser(os.environ.get('MOD_STORE_ROOT', '~/.mod/store'))
STORE_NS = 'plinyville'
BASE_PATH = os.environ.get('PLINYVILLE_BASE_PATH', '/pliny')

# Content archive caps — a market of readable mods, not a CDN mirror.
FILES_CAP = int(os.environ.get('PLINYVILLE_FILES_CAP', 60))
FILE_BYTES_CAP = int(os.environ.get('PLINYVILLE_FILE_BYTES_CAP', 200_000))
# Extensions worth storing as readable text; everything else stays tree-only.
TEXT_EXT = {
    '.md', '.mkd', '.markdown', '.txt', '.rst', '.json', '.jsonl', '.yaml', '.yml',
    '.toml', '.ini', '.cfg', '.py', '.js', '.jsx', '.ts', '.tsx', '.rs', '.go',
    '.c', '.h', '.cpp', '.java', '.rb', '.sh', '.bash', '.html', '.css', '.xml',
    '.sql', '.env', '.mkd', '.mdx', '.csv', '.tsv', '.prompt', '',
}


class Market:
    """The plinyville market: repos as mods, archived into the store."""

    def __init__(self, ville: Ville = None, state_path=None, user=None):
        self.ville = ville or Ville(state_path=state_path, user=user)
        self.user = self.ville.user

    # ── store I/O (the store mod's own tree, in its own format) ──────────────

    @staticmethod
    def _store_path(key: str) -> str:
        return os.path.join(STORE_ROOT, STORE_NS, key + '.json')

    def _store_get(self, key: str, default=None):
        try:
            with open(self._store_path(key), encoding='utf-8') as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    def _store_put(self, key: str, data) -> str:
        path = self._store_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return path

    def _store_del(self, key: str):
        for p in (self._store_path(key), self._store_path(key) + '.tmp'):
            try:
                os.remove(p)
            except OSError:
                pass

    # ── the market index ─────────────────────────────────────────────────────

    def _index(self) -> dict:
        return self._store_get('market', {}) or {}

    def _save_index(self, idx: dict):
        idx['store'] = os.path.join(STORE_ROOT, STORE_NS)
        self._store_put('market', idx)

    def installed(self) -> dict:
        """{repo: index-entry} for every repo archived into the store."""
        return (self._index().get('mods') or {})

    # ── the catalog: every repo as a market listing ──────────────────────────

    def catalog(self, search=None, refresh=False) -> dict:
        """The market: one card per elder-plinius repo, joined with what has
        already been archived into the store. Metadata is free (from the mirror
        cache); `install` is what actually pulls a repo's content."""
        repos = self.ville.repos(search=search, n=500, refresh=refresh)['repos']
        inst = self.installed()
        mods = [self._listing(r, inst.get(r['name'])) for r in repos]
        return {
            'market': 'plinyville',
            'user': self.user,
            'count': len(mods),
            'installed': sum(1 for m_ in mods if m_['installed']),
            'store': os.path.join(STORE_ROOT, STORE_NS),
            'updated': self._index().get('updated'),
            'mods': mods,
        }

    def _listing(self, repo: dict, entry: dict = None) -> dict:
        """One market card — the repo metadata plus its mod wiring."""
        name = repo['name']
        return {
            'name': name,
            'slug': self._slug(name),
            'title': name,
            'description': repo.get('description') or '',
            'language': repo.get('language'),
            'stars': repo.get('stars', 0),
            'forks': repo.get('forks', 0),
            'topics': repo.get('topics') or [],
            'url': repo.get('url'),
            'installed': bool(entry),
            'cid': (entry or {}).get('cid'),
            'files_stored': (entry or {}).get('files_stored'),
            'archived_at': (entry or {}).get('archived_at'),
            'source': (entry or {}).get('source'),
            'app': f'{BASE_PATH}/m/{name}',
            'api': f'/api{BASE_PATH}/m/{name}',
            'mcp': f'/api{BASE_PATH}/m/{name}/mcp',
        }

    # ── install: archive one repo's content into the store ───────────────────

    def install(self, name, files_cap=FILES_CAP, byte_cap=FILE_BYTES_CAP,
                refresh=False) -> dict:
        """Pull a repo's content and store it as a mod: the recursive tree, the
        README, and up to `files_cap` readable files. Idempotent — re-runs only
        when `refresh` is set or nothing is archived yet.

        This is the REST path: one API call per repo plus one per file, against a
        60/hr anonymous budget. `clone.Cloner.archive` does the same job over git,
        which that budget does not apply to — see `m pliny/clone`."""
        name = self.ville._safe(name)
        if not refresh and name in self.installed():
            return {**self.installed()[name], 'name': name, 'reused': True}

        meta = self.ville.repo(name)
        branch = meta.get('default_branch') or 'main'
        tree = self._fetch_tree(name, branch)
        readme = self._safe_readme(name)

        files, stored, skipped = {}, 0, 0
        for e in tree:
            if e['type'] != 'blob':
                continue
            if stored >= int(files_cap):
                skipped += 1
                continue
            if not self._texty(e['path']) or e.get('size', 0) > int(byte_cap):
                skipped += 1
                continue
            text = self._fetch_blob(name, e['sha'])
            if text is None:
                skipped += 1
                continue
            files[e['path']] = text
            stored += 1

        return self.store_bundle(
            name, meta=meta, branch=branch, readme=readme,
            tree=[{'path': e['path'], 'type': e['type'], 'size': e.get('size', 0),
                   'sha': e.get('sha')} for e in tree],
            files=files, skipped=skipped, source='github-api')

    # ── the one write path: bundle → store mod → market index ────────────────

    def store_bundle(self, name, meta, branch, readme, tree, files, skipped=0,
                     source='github-api', head=None) -> dict:
        """Write one archived repo into the store mod and index it in the market.

        Both archivers land here — the REST one above and the git-clone one in
        `clone.py` — so a mod looks and reads the same whichever way it arrived."""
        name = self.ville._safe(name)
        bundle = {
            'repo': name, 'user': self.user, 'meta': meta,
            'default_branch': branch, 'readme': readme,
            'tree': tree, 'files': files,
            'files_total': sum(1 for e in tree if e.get('type') == 'blob'),
            'files_stored': len(files), 'files_skipped': skipped,
            'archived_at': time.time(), 'archiver_version': VERSION,
            'source': source, 'head': head,
        }
        cid = self._cid(bundle)
        self._store_put(f'mods/{name}/content', bundle)
        manifest = self._build_manifest(name, meta, bundle, cid)
        self._store_put(f'mods/{name}/manifest', manifest)

        idx = self._index()
        mods = idx.setdefault('mods', {})
        mods[name] = {
            'cid': cid, 'files_stored': bundle['files_stored'],
            'files_total': bundle['files_total'],
            'bytes': sum(len(v) for v in files.values()),
            'archived_at': bundle['archived_at'],
            'source': source, 'head': head,
            'content': f'{STORE_NS}/mods/{name}/content',
            'manifest': f'{STORE_NS}/mods/{name}/manifest',
        }
        idx['updated'] = time.time()
        self._save_index(idx)
        return {'name': name, 'cid': cid, 'files_stored': bundle['files_stored'],
                'files_total': bundle['files_total'], 'files_skipped': skipped,
                'source': source, 'head': head,
                'manifest': manifest, 'installed': True}

    def install_all(self, limit=None, sleep=0.0, skip_installed=True) -> dict:
        """Archive the whole market. Anonymous GitHub is rate-limited (60/hr), so
        this stops cleanly at the first rate-limit wall and reports how far it
        got — re-run later to continue."""
        cat = self.catalog()['mods']
        if limit:
            cat = cat[:int(limit)]
        done, failed = [], []
        for m_ in cat:
            name = m_['name']
            if skip_installed and m_['installed']:
                done.append({'name': name, 'reused': True})
                continue
            try:
                r = self.install(name)
                done.append({'name': name, 'files_stored': r['files_stored']})
            except GitHubError as e:
                failed.append({'name': name, 'error': str(e)})
                if e.status in (403, 429):        # rate wall — stop, don't hammer
                    break
            except Exception as e:                # noqa: BLE001
                failed.append({'name': name, 'error': f'{type(e).__name__}: {e}'})
            if sleep:
                time.sleep(float(sleep))
        return {'installed': len(done), 'failed': len(failed),
                'done': done, 'errors': failed, 'total': len(cat)}

    def uninstall(self, name) -> dict:
        name = self.ville._safe(name)
        self._store_del(f'mods/{name}/content')
        self._store_del(f'mods/{name}/manifest')
        idx = self._index()
        existed = (idx.get('mods') or {}).pop(name, None)
        idx['updated'] = time.time()
        self._save_index(idx)
        return {'name': name, 'removed': bool(existed)}

    # ── one mod: manifest, content, and per-repo reads ───────────────────────

    def mod(self, name) -> dict:
        """A market mod's manifest — its wiring, meta and MCP tool list. Built on
        demand from the mirror if the repo has not been archived yet."""
        name = self.ville._safe(name)
        man = self._store_get(f'mods/{name}/manifest')
        if man:
            return man
        meta = self.ville.repo(name)
        return self._build_manifest(name, meta, None, None)

    def content(self, name) -> dict:
        """The archived bundle, or the live tree if the repo is not installed."""
        name = self.ville._safe(name)
        got = self._store_get(f'mods/{name}/content')
        if got:
            return got
        raise ValueError(f'{name} is not installed — POST /m/{name}/install first')

    def is_installed(self, name) -> bool:
        return self.ville._safe(name) in self.installed()

    # per-repo reads: archive first, live GitHub as the fallback ──────────────

    def repo_info(self, name) -> dict:
        man = self.mod(name)
        entry = self.installed().get(self.ville._safe(name))
        return {**man, 'installed': bool(entry),
                'files_stored': (entry or {}).get('files_stored')}

    def repo_readme(self, name) -> dict:
        name = self.ville._safe(name)
        b = self._store_get(f'mods/{name}/content')
        if b and b.get('readme') is not None:
            return {'repo': name, 'markdown': b['readme'], 'source': 'store'}
        r = self.ville.readme(name)
        r['source'] = 'live'
        return r

    def repo_tree(self, name, path='') -> dict:
        name = self.ville._safe(name)
        b = self._store_get(f'mods/{name}/content')
        if not b:
            t = self.ville.tree(name, path=path)
            t['source'] = 'live'
            return t
        p = str(path or '').strip('/')
        prefix = (p + '/') if p else ''
        children = {}                              # name -> is_dir, collapsing depth
        for e in b['tree']:
            ep = e['path']
            if not ep.startswith(prefix):
                continue
            rest = ep[len(prefix):]
            if not rest:
                continue
            head, _, tail = rest.partition('/')
            is_dir = bool(tail) or e['type'] == 'tree'
            children[head] = children.get(head, False) or is_dir
        entries = [{'name': h, 'path': prefix + h,
                    'type': 'dir' if is_dir else 'file',
                    'stored': (prefix + h) in b['files']}
                   for h, is_dir in children.items()]
        entries.sort(key=lambda x: (x['type'] != 'dir', x['name'].lower()))
        return {'repo': name, 'path': p, 'count': len(entries),
                'entries': entries, 'source': 'store'}

    def repo_file(self, name, path, ref=None) -> dict:
        name = self.ville._safe(name)
        p = str(path or '').strip('/')
        if not p:
            raise ValueError('path is required')
        b = self._store_get(f'mods/{name}/content')
        if b and p in b.get('files', {}):
            return {'repo': name, 'path': p, 'text': b['files'][p],
                    'size': len(b['files'][p]), 'source': 'store'}
        r = self.ville.file(name, p, ref=ref)
        r['source'] = 'live'
        return r

    def repo_search(self, name, q, n=50) -> dict:
        """Grep the archived files of one mod. Offline; no GitHub call."""
        name = self.ville._safe(name)
        b = self._store_get(f'mods/{name}/content')
        if not b:
            raise ValueError(f'{name} is not installed — nothing to grep offline')
        needle = str(q or '').lower()
        hits = []
        for path, text in b.get('files', {}).items():
            low = text.lower()
            if needle not in low:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    hits.append({'path': path, 'line': i, 'text': line.strip()[:200]})
                    if len(hits) >= int(n):
                        break
            if len(hits) >= int(n):
                break
        return {'repo': name, 'query': q, 'hits': hits, 'count': len(hits),
                'source': 'store'}

    # ── github helpers (recursive tree + blobs) ──────────────────────────────

    def _fetch_tree(self, name, branch) -> list:
        data = self.ville._api(
            f'/repos/{self.user}/{name}/git/trees/{branch}', {'recursive': '1'})
        return data.get('tree', []) if isinstance(data, dict) else []

    def _fetch_blob(self, name, sha):
        import base64
        try:
            data = self.ville._api(f'/repos/{self.user}/{name}/git/blobs/{sha}')
        except GitHubError:
            raise
        try:
            return base64.b64decode(data.get('content', '') or '').decode('utf-8')
        except (UnicodeDecodeError, ValueError):
            return None

    def _safe_readme(self, name):
        try:
            return self.ville.readme(name).get('markdown')
        except GitHubError:
            return None

    # ── the mod manifest (a repo, described as a mod) ─────────────────────────

    def _build_manifest(self, name, meta, bundle, cid) -> dict:
        slug = self._slug(name)
        return {
            'name': f'pv-{slug}',
            'title': name,
            'market': 'plinyville',
            'kind': 'mod',
            'version': VERSION,
            'description': (meta.get('description') or '').strip()
                           or f'elder-plinius/{name}, archived as a plinyville mod',
            'icon': '🐉',
            'color': '#b061ff',
            'upstream': meta.get('url') or f'https://github.com/{self.user}/{name}',
            'language': meta.get('language'),
            'topics': meta.get('topics') or [],
            'stars': meta.get('stars', 0),
            'default_branch': meta.get('default_branch') or 'main',
            'cid': cid,
            'app': f'{BASE_PATH}/m/{name}',
            'api': f'/api{BASE_PATH}/m/{name}',
            'mcp': f'/api{BASE_PATH}/m/{name}/mcp',
            'urls': {
                'app': f'https://modc2.com{BASE_PATH}/m/{name}',
                'api': f'https://modc2.com/api{BASE_PATH}/m/{name}',
                'mcp': f'https://modc2.com/api{BASE_PATH}/m/{name}/mcp',
            },
            'endpoints': {
                'GET  info': f'/api{BASE_PATH}/m/{name}',
                'GET  readme': f'/api{BASE_PATH}/m/{name}/readme',
                'GET  tree': f'/api{BASE_PATH}/m/{name}/tree?path=',
                'GET  file': f'/api{BASE_PATH}/m/{name}/file?path=',
                'GET  search': f'/api{BASE_PATH}/m/{name}/search?q=',
                'POST mcp': f'/api{BASE_PATH}/m/{name}/mcp',
            },
            'mcp_tools': [f'{slug}_info', f'{slug}_readme', f'{slug}_tree',
                          f'{slug}_file', f'{slug}_search'],
            'installed': bool(bundle),
            'files_stored': (bundle or {}).get('files_stored'),
            'files_total': (bundle or {}).get('files_total'),
            'archived_at': (bundle or {}).get('archived_at'),
        }

    # ── ids ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _cid(obj) -> str:
        """A deterministic content id for the bundle. The CLI upgrades this to a
        real localfs CID when the protocol is in reach; this keeps the market
        content-addressed even from inside the bare server process."""
        blob = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode('utf-8')
        return 'sha256-' + hashlib.sha256(blob).hexdigest()[:48]

    @staticmethod
    def _slug(name) -> str:
        return re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-') or 'mod'

    @staticmethod
    def _texty(path) -> bool:
        base = os.path.basename(path)
        ext = os.path.splitext(base)[1].lower()
        if ext in TEXT_EXT:
            return True
        return base.lower() in {'readme', 'license', 'makefile', 'dockerfile',
                                'jailbreak', 'system'}
