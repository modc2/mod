"""
Lighthouse — perpetual decentralized storage (lighthouse.storage by Lighthouse Labs).

Uploads go straight to the Lighthouse HTTPS node API (Bearer API key) and land
on IPFS/Filecoin with perpetual pinning; retrieval rides the Lighthouse IPFS
gateway with public-gateway fallbacks. A local sqlite index keeps an
owner-keyed record of everything stored through this module.

The module has four faces, all over the same code:

    CLI      `m lighthouse/put …` — this file
    API      api/api.py on :50680 — FastAPI, mod-protocol auth, BYOK header
    console  app/server.py on :50681 at /lighthouse — plain ES modules
    MCP      mcp.py — the same work as tools, on stdio or POST /mcp; the
             schema is served at GET /mcp and shown in the console

…and one seam: every CID can be registered in the **store** module, which is
where visibility, grants, pools and the marketplace live. The bytes never move
into the store — it keeps the gateway url and redirects readers here. See
store_link.py; `m lighthouse/store` says whether the link is usable.

Credentials (checked in order):
    1. LIGHTHOUSE_API_KEY env var
    2. ~/.mod/lighthouse/credentials.json  {"api_key": "..."}   (never committed)

Get a key at https://files.lighthouse.storage (sign in, API Key tab), then:
    m lighthouse/set_key <api_key>

Usage (Python):
    import mod as m
    lh = m.mod('lighthouse')()
    lh.set_key('lh_...')
    lh.put('/path/to/file')
    lh.get('bafy...')
    lh.usage()

Usage (CLI):
    m lighthouse/status
    m lighthouse/set_key lh_...
    m lighthouse/put /path/to/file
    m lighthouse/get bafy...
"""
import json
import os
import sqlite3
import time
from pathlib import Path

import requests

DIR = Path(__file__).resolve().parent
STATE = Path(os.path.expanduser(os.environ.get('LIGHTHOUSE_DIR', '~/.mod/lighthouse')))

UPLOAD_ENDPOINTS = [
    'https://upload.lighthouse.storage/api/v0/add',
    'https://node.api.lighthouse.storage/api/v0/add',
]
API_BASE = 'https://api.lighthouse.storage'
GATEWAY = 'https://gateway.lighthouse.storage'


class Mod:
    description = "Lighthouse Labs perpetual storage (IPFS/Filecoin) — API-key uploads, gateway retrieval."
    path = str(DIR)

    fns = [
        'forward', 'put', 'get', 'preview', 'pin', 'list', 'rm',
        'status', 'usage', 'uploads', 'set_key', 'key_status', 'install',
        'store', 'push', 'mirror', 'mcp', 'mcp_tools', 'mcp_call',
        'serve', 'stop', 'app', 'api',
    ]

    def __init__(self, api_key: str = None, store_path: str = None, **kw):
        self.module_dir = DIR
        # `state` and not `store`: `store` is the *fn* that reports the link to
        # the store module, and an attribute of the same name would silently
        # shadow it (`m lighthouse/store` would hand back a PosixPath).
        self.state = Path(store_path) if store_path else STATE
        self.state.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state / 'lighthouse.db'
        self.creds_path = self.state / 'credentials.json'
        self.gateway = os.environ.get('LIGHTHOUSE_GATEWAY') or GATEWAY
        self._api_key = api_key
        self._init_db()

    # ── credentials ───────────────────────────────────────────────

    @property
    def api_key(self):
        if self._api_key:
            return self._api_key
        env = os.environ.get('LIGHTHOUSE_API_KEY')
        if env:
            return env
        try:
            return json.loads(self.creds_path.read_text()).get('api_key') or None
        except Exception:
            return None

    def set_key(self, api_key: str, **kw) -> dict:
        """Persist the Lighthouse API key off-chain (~/.mod/lighthouse/)."""
        api_key = (api_key or '').strip()
        if not api_key:
            return {'error': 'empty api_key'}
        self.creds_path.write_text(json.dumps({'api_key': api_key}, indent=2))
        os.chmod(self.creds_path, 0o600)
        self._api_key = api_key
        return self.key_status()

    def key_status(self, **kw) -> dict:
        """Is a key configured, and does Lighthouse accept it?"""
        key = self.api_key
        out = {'configured': bool(key), 'needs_key': not key,
               'source': ('init' if self._api_key else
                          'env' if os.environ.get('LIGHTHOUSE_API_KEY') else
                          'file' if self.creds_path.exists() else None)}
        if key:
            try:
                r = requests.get(f'{API_BASE}/api/user/user_data_usage',
                                 headers=self._auth(), timeout=15)
                out['valid'] = r.status_code == 200
                if r.status_code == 200:
                    out['usage'] = r.json()
            except Exception as e:
                out['valid'] = None
                out['error'] = str(e)
        return out

    def _auth(self):
        key = self.api_key
        if not key:
            raise RuntimeError(
                'no Lighthouse API key: m lighthouse/set_key <key> '
                '(create one at https://files.lighthouse.storage) or set LIGHTHOUSE_API_KEY'
            )
        return {'Authorization': f'Bearer {key}'}

    # ── Core API ──────────────────────────────────────────────────

    def forward(self, action: str = None, **kw):
        if not action:
            return self.status()
        fn = getattr(self, action, None)
        if not fn:
            return {'error': f'unknown action: {action}'}
        return fn(**kw)

    def put(self, path: str, owner: str = None, key: str = None, **kw) -> dict:
        """Upload a file to Lighthouse (perpetual IPFS/Filecoin pin). Returns the CID."""
        p = Path(os.path.expanduser(path))
        if not p.exists():
            raise FileNotFoundError(path)
        size = p.stat().st_size
        obj_key = key or p.name
        headers = self._auth()

        last = None
        for url in UPLOAD_ENDPOINTS:
            try:
                with open(p, 'rb') as f:
                    r = requests.post(url, headers=headers,
                                      files={'file': (obj_key, f)}, timeout=300)
                if r.status_code == 200:
                    data = json.loads(r.text.strip().splitlines()[-1])
                    cid = data.get('Hash') or data.get('cid')
                    if not cid:
                        last = f'{url} -> no CID in response: {r.text[:200]}'
                        continue
                    self._record(cid, obj_key, str(p), int(data.get('Size') or size), owner)
                    return {'cid': cid, 'key': obj_key, 'size': size,
                            'backend': 'lighthouse', 'owner': owner,
                            'url': f'{self.gateway}/ipfs/{cid}'}
                last = f'{url} -> {r.status_code}: {r.text[:200]}'
            except Exception as e:
                last = f'{url} -> {e}'
        raise RuntimeError(f'lighthouse upload failed (last: {last})')

    def get(self, cid: str, out: str = None) -> dict:
        """Retrieve by CID via the Lighthouse gateway (public IPFS fallbacks)."""
        out = Path(os.path.expanduser(out)) if out else (self.state / 'cache' / cid)
        out.parent.mkdir(parents=True, exist_ok=True)

        candidates = [
            f"{self.gateway.rstrip('/')}/ipfs/{cid}",
            f'https://gateway.ipfs.io/ipfs/{cid}',
            f'https://ipfs.io/ipfs/{cid}',
        ]
        last = None
        for url in candidates:
            try:
                r = requests.get(url, stream=True, timeout=60)
                if r.status_code == 200:
                    with open(out, 'wb') as f:
                        for chunk in r.iter_content(8192):
                            f.write(chunk)
                    return {'cid': cid, 'path': str(out), 'gateway': url}
                last = f'{url} -> {r.status_code}'
            except Exception as e:
                last = f'{url} -> {e}'
        raise RuntimeError(f'gateway retrieval failed (last: {last})')

    def preview(self, cid: str, max_bytes: int = 65536) -> dict:
        """Peek at a CID without downloading it: decoded text when it is text,
        the size, and whether what came back was cut short.

        One implementation, three faces: the API's /preview, the MCP tool and
        the CLI all land here, so what a console shows and what an agent reads
        can never be two different opinions about the same CID.
        """
        max_bytes = max(1, min(int(max_bytes), 1 << 20))
        url = f"{self.gateway.rstrip('/')}/ipfs/{cid}"
        try:
            r = requests.get(url, stream=True, timeout=60)
        except requests.RequestException as e:
            raise RuntimeError(f'gateway unreachable: {e}')
        if r.status_code >= 400:
            raise LookupError(f'gateway {r.status_code} for {cid}')
        chunk = next(r.iter_content(max_bytes + 1), b'') or b''
        r.close()
        truncated = len(chunk) > max_bytes
        chunk = chunk[:max_bytes]
        try:
            text, is_text = chunk.decode('utf-8'), True
        except UnicodeDecodeError:
            text, is_text = None, False
        return {'cid': cid, 'text': text, 'is_text': is_text, 'bytes': len(chunk),
                'truncated': truncated, 'content_type': r.headers.get('Content-Type'),
                'size': int(r.headers.get('Content-Length') or 0) or None,
                'gateway': url}

    def pin(self, cid: str, owner: str = None) -> dict:
        """Lighthouse pins are perpetual at upload; for foreign CIDs record the intent."""
        self._record(cid, None, None, 0, owner)
        return {'cid': cid, 'pinned': True, 'backend': 'lighthouse',
                'note': 'lighthouse uploads are perpetually pinned; foreign CID recorded locally'}

    def list(self, owner: str = None, limit: int = 100) -> list:
        conn = self._db()
        cols = ['cid', 'key', 'path', 'size', 'owner', 'timestamp', 'meta']
        if owner:
            rows = conn.execute(
                f"SELECT {','.join(cols)} FROM objects WHERE owner=? ORDER BY timestamp DESC LIMIT ?",
                (owner, int(limit))).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {','.join(cols)} FROM objects ORDER BY timestamp DESC LIMIT ?",
                (int(limit),)).fetchall()
        conn.close()
        return [dict(zip(cols, r), backend='lighthouse') for r in rows]

    def rm(self, cid: str) -> dict:
        conn = self._db()
        conn.execute('DELETE FROM objects WHERE cid=?', (cid,))
        conn.commit()
        conn.close()
        return {'cid': cid, 'removed': True}

    # ── account / remote state ────────────────────────────────────

    def usage(self) -> dict:
        """Remote data usage for the configured API key."""
        r = requests.get(f'{API_BASE}/api/user/user_data_usage',
                         headers=self._auth(), timeout=15)
        r.raise_for_status()
        return r.json()

    def uploads(self, page: int = 1) -> dict:
        """Files uploaded under this API key (remote listing)."""
        r = requests.get(f'{API_BASE}/api/user/files_uploaded',
                         params={'pageNo': page}, headers=self._auth(), timeout=15)
        r.raise_for_status()
        return r.json()

    def status(self) -> dict:
        conn = self._db()
        n = conn.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        conn.close()
        key = self.api_key
        return {
            'name': 'lighthouse',
            'objects': n,
            'state_dir': str(self.state),
            'gateway': self.gateway,
            'api': API_BASE,
            'configured': bool(key),
            'needs_key': not key,
        }

    def install(self) -> dict:
        return {
            'docs': 'https://docs.lighthouse.storage',
            'signup': 'https://files.lighthouse.storage',
            'env': 'export LIGHTHOUSE_API_KEY=...  (or m lighthouse/set_key <key>)',
            'note': 'No daemon needed — uploads hit the Lighthouse node API over HTTPS.',
        }

    # ── the store bridge ──────────────────────────────────────────
    #
    # Lighthouse holds bytes; the store module holds who may see them. These
    # three functions are the CLI half of that seam — the API exposes the same
    # ones under /store/*, and both go through store_link.py so there is one
    # place where the two modules meet.

    def _link(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'lighthouse_store_link', DIR / 'store_link.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def store(self, token: str = None, **kw) -> dict:
        """Is the store reachable, and may this key store in it?

        Reports the blockers rather than raising: not being on the store's
        whitelist, or not having signed its terms, are things to fix, not
        crashes. From the CLI the token is minted with this box's own mod key.
        """
        link = self._link()
        return link.StoreLink().status(token or link.local_token())

    def push(self, path: str, key: str = None, public: bool = False,
             pool: str = None, token: str = None, **kw) -> dict:
        """Upload to Lighthouse, then register the CID in the store.

        The upload is the part that cannot be undone, so it happens first and
        the registration result is reported alongside it — a store that is down
        never costs you the CID.
        """
        link = self._link()
        token = token or link.local_token()
        owner = link.local_address()
        result = self.put(path=path, owner=owner, key=key)
        try:
            reg = link.StoreLink().register(
                token, cid=result['cid'], key=result.get('key'),
                size=result.get('size'), url=result.get('url'),
                public=public, pool=pool)
            result['store'] = {'registered': True, **reg}
        except Exception as e:
            result['store'] = {'registered': False, 'error': str(e)}
        return result

    def mirror(self, cid: str, key: str = None, public: bool = False,
               pool: str = None, token: str = None, **kw) -> dict:
        """Make something the store already has perpetual.

        Fetched from the store with the caller's token (their read rights, not
        ours), uploaded to Lighthouse, registered back. The returned CID is
        usually identical — same content, same hash — but chunking can differ,
        so `same_cid` says which happened and the Lighthouse CID is the one
        registered.
        """
        import shutil
        import tempfile

        link = self._link()
        token = token or link.local_token()
        store = link.StoreLink()
        tmpdir = Path(tempfile.mkdtemp(prefix='lighthouse-mirror-'))
        try:
            info = {}
            try:
                info = store.object_info(token, cid) or {}
            except Exception:
                pass
            name = key or info.get('key') or cid
            local = store.fetch(token, cid, tmpdir / Path(str(name)).name)
            result = self.put(path=str(local), owner=link.local_address(),
                              key=str(name))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        result['source_cid'] = cid
        result['same_cid'] = result['cid'] == cid
        try:
            reg = store.register(token, cid=result['cid'], key=result.get('key'),
                                 size=result.get('size'), url=result.get('url'),
                                 public=public, pool=pool)
            result['store'] = {'registered': True, **reg}
        except Exception as e:
            result['store'] = {'registered': False, 'error': str(e)}
        return result

    # ── the MCP server ────────────────────────────────────────────
    #
    # The same tools an agent sees over `POST /mcp` or `python3 mcp.py`, callable
    # from here so the CLI can read the schema without running a client.

    def _mcp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('lighthouse_mcp', DIR / 'mcp.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def mcp(self, url: str = None, **kw) -> dict:
        """The MCP server described: transports, auth, tool names, client config."""
        doc = self._mcp().describe(url)
        doc['tools'] = [{'name': t['name'], 'auth': t['auth'],
                         'transports': t['transports'],
                         'summary': t['description'].split('.')[0] + '.'}
                        for t in doc['tools']]
        return doc

    def mcp_tools(self, name: str = None, **kw) -> dict:
        """The full tool schemas — what `tools/list` returns, one or all."""
        tools = self._mcp().describe()['tools']
        if name:
            tools = [t for t in tools if t['name'] in (name, f'lighthouse_{name}')]
            if not tools:
                return {'error': f'no such tool: {name}'}
        return {'count': len(tools), 'tools': tools}

    def mcp_call(self, tool: str, **kw):
        """Run one MCP tool with this box's own keys (what stdio would do)."""
        mcp = self._mcp()
        name = tool if tool in mcp.TOOLS else f'lighthouse_{tool}'
        try:
            return mcp.call_tool(name, kw)
        except Exception as e:
            return {'error': f'{name}: {e}'}

    # ── the two services ──────────────────────────────────────────

    def serve(self, no_app: bool = False, no_api: bool = False, **kw) -> dict:
        """Launch the API and the console under pm2 (lighthouse-api / -app)."""
        import subprocess
        script = DIR / 'serve.sh'
        args = ['bash', str(script)]
        if no_app:
            args.append('--no-app')
        if no_api:
            args.append('--no-api')
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=300)
        except Exception as e:
            return {'error': str(e)}
        cfg = json.loads((DIR / 'config.json').read_text())
        return {'pm2': ['lighthouse-api', 'lighthouse-app'],
                'api': cfg['urls']['api'], 'app': cfg['urls']['app'],
                'returncode': out.returncode,
                'stdout': out.stdout[-2000:], 'stderr': out.stderr[-2000:]}

    def stop(self, **kw) -> dict:
        import subprocess
        out = subprocess.run(['bash', str(DIR / 'serve.sh'), 'stop'],
                             capture_output=True, text=True)
        return {'returncode': out.returncode, 'stdout': out.stdout[-2000:],
                'stderr': out.stderr[-2000:]}

    def app(self, **kw) -> dict:
        """Launch only the console."""
        return self.serve(no_api=True)

    def api(self, **kw) -> dict:
        """Launch only the API."""
        return self.serve(no_app=True)

    # ── DB helpers ────────────────────────────────────────────────

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute('''CREATE TABLE IF NOT EXISTS objects (
            cid TEXT PRIMARY KEY,
            key TEXT,
            path TEXT,
            size INTEGER,
            owner TEXT,
            timestamp INTEGER NOT NULL,
            meta TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_lh_owner ON objects(owner)')
        conn.commit()
        conn.close()

    def _db(self):
        return sqlite3.connect(str(self.db_path))

    def _record(self, cid, key, path, size, owner, meta=None):
        conn = self._db()
        conn.execute(
            'INSERT OR REPLACE INTO objects (cid,key,path,size,owner,timestamp,meta) '
            'VALUES (?,?,?,?,?,?,?)',
            (cid, key, path, size, owner, int(time.time()),
             json.dumps(meta) if meta else None),
        )
        conn.commit()
        conn.close()
