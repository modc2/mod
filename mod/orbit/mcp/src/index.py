"""
index — the hub's own shelf: MCP servers people publish here.

Aggregation is only half a hub. The other half is being a place to put
something: anyone with a wallet — a browser extension or a keypair the browser
minted locally — signs a mod protocol token and publishes a server manifest.

Where the manifest lives matters. It is not pasted into this repo and it is not
trapped in this box's SQLite: it is pinned to the **store** mod under the
submitter's *own* address, so the canonical artifact is a CID they own and can
re-pin, share or take down. This index keeps the pointer (cid + the fields we
need to search) under ~/.mod/mcp/ — off-tree, like every other per-user state
in the fleet.

If store is unreachable the submission is still accepted and marked
`pinned: false` with the reason, because losing a listing to a restarting
dependency would be worse than a listing whose CID lands late. `repin()` fills
it in afterwards.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

HUB_DIR = Path(os.environ.get('MCP_HUB_DIR') or os.path.expanduser('~/.mod/mcp'))
STORE_URL = os.environ.get('MCP_STORE_URL') or 'http://localhost:50152'
# Fleets running the activator scale a store to zero when it's idle; going
# through its gateway wakes the module instead of getting a refused connection.
# Only used when the direct URL is unreachable.
STORE_WAKE_URL = os.environ.get('MCP_STORE_WAKE_URL') or 'http://localhost:9000/api/store'

MANIFEST_VERSION = '1.0'
SLUG_RE = re.compile(r'[^a-z0-9-]+')
VALID_TRANSPORTS = ('stdio', 'streamable-http', 'sse')


def slugify(name: str) -> str:
    return SLUG_RE.sub('-', (name or '').strip().lower()).strip('-')[:60]


class SubmitError(ValueError):
    """Bad submission — surfaced to the caller as a 400."""


class StoreError(RuntimeError):
    """store said no (or wasn't there) — surfaced as a 502 with its reason."""


class Index:
    """Community submissions: validate → pin to store → record the pointer."""

    def __init__(self, dir: Optional[str] = None, store_url: Optional[str] = None,
                 wake_url: Optional[str] = None, timeout: int = 15):
        self.dir = Path(dir or HUB_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / 'servers.json'
        self.store_url = (store_url or STORE_URL).rstrip('/')
        self.wake_url = (wake_url or STORE_WAKE_URL).rstrip('/')
        self.timeout = timeout

    def store_bases(self) -> List[str]:
        """Where to reach store, best first: direct, then the wake gateway."""
        return [u for u in (self.store_url, self.wake_url) if u] \
            if self.wake_url != self.store_url else [self.store_url]

    # ── persistence ─────────────────────────────────────────────────

    def _load(self) -> Dict[str, Dict]:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self, data: Dict[str, Dict]) -> None:
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(self.path)

    # ── store pinning ───────────────────────────────────────────────

    def store_status(self) -> Dict[str, Any]:
        for base in self.store_bases():
            try:
                r = requests.get(f'{base}/health', timeout=6)
                if r.ok:
                    return {'url': base, 'up': True,
                            'via': 'wake' if base == self.wake_url else 'direct'}
            except Exception:
                continue
        return {'url': self.store_url, 'up': False, 'error': 'unreachable'}

    def _store_call(self, method: str, path: str, token: Optional[str] = None,
                    **kw) -> Dict[str, Any]:
        """One call against store, trying each base until one answers."""
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        last = 'unreachable'
        for base in self.store_bases():
            try:
                r = requests.request(method, f'{base}{path}', headers=headers,
                                     timeout=self.timeout, **kw)
            except requests.RequestException as e:
                last = f'{type(e).__name__}'
                continue
            try:
                body = r.json()
            except Exception:
                body = {'detail': (r.text or '')[:300]}
            if not r.ok:
                raise StoreError(f"store {r.status_code}: {body.get('detail') or ''}")
            return body
        raise StoreError(f'store unreachable: {last}')

    def terms(self, token: Optional[str] = None) -> Dict[str, Any]:
        """store's terms of service — publishers sign these before a manifest
        can be pinned. Proxied so the publish flow never leaves the hub."""
        return self._store_call('GET', '/terms', token)

    def accept_terms(self, token: str) -> Dict[str, Any]:
        """Record the caller's wallet-signed acceptance with store."""
        return self._store_call('POST', '/terms/accept', token)

    def pin_manifest(self, manifest: Dict, token: str) -> Dict[str, Any]:
        """PUT the manifest into store as the *caller* — their token, their
        address, their quota, their object."""
        blob = json.dumps(manifest, indent=2, sort_keys=True).encode()
        r = None
        for base in self.store_bases():
            try:
                r = requests.post(
                    f'{base}/put',
                    headers={'Authorization': f'Bearer {token}'},
                    files={'file': (f"{manifest['slug']}.mcp.json", blob,
                                    'application/json')},
                    data={'backend': 'localfs', 'public': 'true',
                          'key': f"mcp/{manifest['slug']}.json"},
                    timeout=self.timeout)
                break
            except requests.RequestException as e:
                last = f'{type(e).__name__}'
                continue
        if r is None:
            return {'pinned': False, 'error': f'store unreachable: {last}'}
        if not r.ok:
            detail = ''
            try:
                detail = str(r.json().get('detail') or '')
            except Exception:
                detail = (r.text or '')[:200]
            return {'pinned': False, 'error': f'store {r.status_code}: {detail}'}
        results = (r.json() or {}).get('results') or {}
        for res in results.values():
            if res.get('cid'):
                return {'pinned': True, 'cid': res['cid'],
                        'backend': res.get('backend') or 'localfs',
                        'size': res.get('size')}
        return {'pinned': False, 'error': 'store accepted the upload but returned no CID'}

    # ── submit ──────────────────────────────────────────────────────

    @staticmethod
    def _manifest(address: str, body: Dict[str, Any], slug: str) -> Dict[str, Any]:
        remotes = []
        for r in body.get('remotes') or []:
            url = str((r or {}).get('url') or '').strip()
            if url:
                remotes.append({'type': str(r.get('type') or 'streamable-http'),
                                'url': url})
        if body.get('remote_url'):
            url = str(body['remote_url']).strip()
            if url not in {r['url'] for r in remotes}:
                remotes.insert(0, {'type': 'streamable-http', 'url': url})
        packages = []
        for p in body.get('packages') or []:
            ident = str((p or {}).get('identifier') or '').strip()
            if ident:
                packages.append({'registry': str(p.get('registry') or 'npm'),
                                 'identifier': ident,
                                 'version': p.get('version') or None})
        if body.get('npm'):
            packages.append({'registry': 'npm', 'identifier': str(body['npm']).strip(),
                             'version': None})
        if body.get('pypi'):
            packages.append({'registry': 'pypi', 'identifier': str(body['pypi']).strip(),
                             'version': None})

        install = dict(body.get('install') or {})
        if remotes:
            install.setdefault('remote', remotes[0]['url'])
        for p in packages:
            if p['registry'] == 'npm':
                install.setdefault('npx', f"npx -y {p['identifier']}")
            elif p['registry'] in ('pypi', 'pip'):
                install.setdefault('uvx', f"uvx {p['identifier']}")
            elif p['registry'] in ('oci', 'docker'):
                install.setdefault('docker', f"docker run -i --rm {p['identifier']}")
        repo = str(body.get('repo') or '').strip()
        if repo and not install.get('repo'):
            install['repo'] = f"git clone {repo.rstrip('/')}.git" \
                if not repo.endswith('.git') else f'git clone {repo}'

        transports = [t for t in (body.get('transports') or []) if t in VALID_TRANSPORTS]
        if not transports:
            transports = ['streamable-http'] if remotes else ['stdio']

        return {
            'mcp_hub': MANIFEST_VERSION,
            'slug': slug,
            'name': str(body.get('name') or '').strip(),
            'title': str(body.get('title') or body.get('name') or '').strip(),
            'description': str(body.get('description') or '').strip(),
            'repo': repo,
            'homepage': str(body.get('homepage') or '').strip(),
            'license': (str(body.get('license')).strip() or None)
            if body.get('license') else None,
            'author': address.lower(),
            'tags': sorted({str(t).strip().lower() for t in (body.get('tags') or [])
                            if str(t).strip()})[:12],
            'transports': sorted(set(transports)),
            'remotes': remotes,
            'packages': packages,
            'install': install,
            'version': str(body.get('version') or '').strip() or None,
            'submitted_at': int(time.time()),
        }

    def submit(self, address: str, body: Dict[str, Any], token: str) -> Dict[str, Any]:
        name = str(body.get('name') or '').strip()
        if not name:
            raise SubmitError('name is required')
        if not str(body.get('description') or '').strip():
            raise SubmitError('description is required — say what the server does')
        slug = slugify(body.get('slug') or name)
        if not slug:
            raise SubmitError('name must contain letters or digits')

        data = self._load()
        entry_id = f'hub:{slug}'
        existing = data.get(entry_id)
        if existing and existing.get('author', '').lower() != address.lower():
            raise SubmitError(
                f"'{slug}' is already published by {existing['author'][:10]}… — "
                'pick another name')

        manifest = self._manifest(address, body, slug)
        if not (manifest['repo'] or manifest['remotes'] or manifest['packages']):
            raise SubmitError(
                'a server needs somewhere to come from: a repo URL, a remote '
                'endpoint, or a package (npm/pypi/docker)')

        pin = self.pin_manifest(manifest, token)
        entry = {
            **manifest,
            'id': entry_id,
            'cid': pin.get('cid'),
            'pinned': pin.get('pinned', False),
            'pin_error': pin.get('error'),
            'created': (existing or {}).get('created') or manifest['submitted_at'],
            'updated': manifest['submitted_at'],
            'updates': ((existing or {}).get('updates') or 0) + (1 if existing else 0),
        }
        data[entry_id] = entry
        self._save(data)
        return entry

    def repin(self, id: str, address: str, token: str) -> Dict[str, Any]:
        """Retry a pin that failed (store was down, terms unsigned)."""
        data = self._load()
        entry = data.get(id)
        if not entry:
            raise KeyError(id)
        if entry.get('author', '').lower() != address.lower():
            raise PermissionError('only the publisher can re-pin this server')
        manifest = {k: v for k, v in entry.items()
                    if k not in ('id', 'cid', 'pinned', 'pin_error', 'created',
                                 'updated', 'updates')}
        pin = self.pin_manifest(manifest, token)
        entry.update(cid=pin.get('cid'), pinned=pin.get('pinned', False),
                     pin_error=pin.get('error'))
        data[id] = entry
        self._save(data)
        return entry

    # ── read / remove ───────────────────────────────────────────────

    def get(self, id: str) -> Optional[Dict]:
        data = self._load()
        return data.get(id) or data.get(f'hub:{id}')

    def list(self, author: Optional[str] = None) -> List[Dict]:
        items = list(self._load().values())
        if author:
            items = [e for e in items if e.get('author', '').lower() == author.lower()]
        return sorted(items, key=lambda e: -(e.get('updated') or 0))

    def remove(self, id: str, address: str, admin: bool = False) -> Dict[str, Any]:
        data = self._load()
        entry = data.get(id)
        if not entry:
            raise KeyError(id)
        if not admin and entry.get('author', '').lower() != address.lower():
            raise PermissionError('you can only remove servers you published')
        del data[id]
        self._save(data)
        # The manifest itself stays in store — it belongs to the publisher, and
        # delisting from the hub is not the same as deleting their object.
        return {'removed': id, 'cid': entry.get('cid'),
                'note': 'delisted from the hub; the pinned manifest is still yours in store'}

    def records(self) -> List[Dict]:
        """Submissions in the registry's record shape, for merged search."""
        out = []
        for e in self.list():
            out.append({
                'id': e['id'], 'source': 'hub', 'name': e.get('name', ''),
                'title': e.get('title') or e.get('name', ''),
                'description': e.get('description', ''),
                'repo': e.get('repo', ''), 'homepage': e.get('homepage', ''),
                'author': e.get('author', ''), 'license': e.get('license'),
                'tags': ['mcp', 'published'] + list(e.get('tags') or []),
                'transports': e.get('transports') or [],
                'remotes': e.get('remotes') or [],
                'packages': e.get('packages') or [],
                'install': e.get('install') or {},
                'version': e.get('version'), 'cid': e.get('cid'),
                'updated': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                         time.gmtime(e.get('updated') or 0)),
            })
        return out

    def stats(self) -> Dict[str, Any]:
        items = list(self._load().values())
        return {
            'submissions': len(items),
            'publishers': len({e.get('author') for e in items if e.get('author')}),
            'pinned': sum(1 for e in items if e.get('pinned')),
            'unpinned': sum(1 for e in items if not e.get('pinned')),
            'store': self.store_status(),
        }
