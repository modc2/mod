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
    3. none of the above → the module MINTS one from this box's own wallet.
       Lighthouse hands out an API key to anyone who signs a nonce with an
       Ethereum key, and the fleet's mod key is one, so a fresh deployment
       needs no dashboard visit, no pasted secret: `m lighthouse/mint_key`,
       or just upload and it happens on the way (LIGHTHOUSE_AUTO_KEY=0 to
       turn that off).

A key from the dashboard still works: m lighthouse/set_key <api_key>

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

# Uploads that arrive with no signed caller are recorded under this owner and
# spend the deployment's key, so they get a budget: per upload, and per rolling
# day across all of them. 0 for either closes the anonymous door entirely.
ANON = 'anon'
ANON_MAX_BYTES = int(os.environ.get('LIGHTHOUSE_ANON_MAX_BYTES', 8 << 20))
ANON_DAILY_BYTES = int(os.environ.get('LIGHTHOUSE_ANON_DAILY_BYTES', 256 << 20))
AUTO_KEY = os.environ.get('LIGHTHOUSE_AUTO_KEY', '1') not in ('0', 'false', '')
GATEWAY = 'https://gateway.lighthouse.storage'
# Lighthouse's gateway answers 402 for CIDs it does not host itself, and the
# big public gateways rate-limit hard (429), so reads walk this list in order.
FALLBACK_GATEWAYS = [
    'https://gateway.pinata.cloud',
    'https://4everland.io',
    'https://dweb.link',
    'https://ipfs.io',
]


class Mod:
    description = "Lighthouse Labs perpetual storage (IPFS/Filecoin) — API-key uploads, gateway retrieval."
    path = str(DIR)

    fns = [
        'forward', 'put', 'get', 'preview', 'pin', 'list', 'rm',
        'status', 'usage', 'uploads', 'set_key', 'mint_key', 'key_status', 'install',
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
        self._call_key = bool(api_key)      # handed in, as opposed to loaded
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

    def creds(self) -> dict:
        try:
            return json.loads(self.creds_path.read_text()) or {}
        except Exception:
            return {}

    def set_key(self, api_key: str, **kw) -> dict:
        """Persist the Lighthouse API key off-chain (~/.mod/lighthouse/)."""
        api_key = (api_key or '').strip()
        if not api_key:
            return {'error': 'empty api_key'}
        self._write_creds({'api_key': api_key, 'source': 'pasted'})
        return self.key_status()

    def _write_creds(self, creds: dict):
        self.creds_path.parent.mkdir(parents=True, exist_ok=True)
        self.creds_path.write_text(json.dumps(creds, indent=2))
        os.chmod(self.creds_path, 0o600)
        self._api_key = creds.get('api_key')

    # ── a key from nothing ────────────────────────────────────────
    #
    # Lighthouse's own sign-in is "prove you hold an Ethereum key": GET a nonce
    # message, sign it (EIP-191 personal_sign), POST the signature back and an
    # API key comes out. This box signs every protocol token with exactly such
    # a key, so the deployment can enrol itself. The account that results is
    # the box's — its free allowance, its file listing — and the key is stored
    # where set_key would have put it, 0600, off-chain.

    _mint_failed_at = 0.0
    _mint_error = None

    def _signer(self):
        """This box's own key, as an eth_account signer (same address the fleet
        token carries)."""
        import importlib.util
        from eth_account import Account
        spec = importlib.util.spec_from_file_location('lighthouse_protocol',
                                                      DIR / 'protocol.py')
        protocol = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(protocol)
        key = protocol.protocol().key()
        pk = key.private_key
        return Account.from_key(pk.hex() if isinstance(pk, bytes) else pk)

    def mint_key(self, **kw) -> dict:
        """Enrol this box with Lighthouse using its own wallet and keep the key.

        No dashboard, no pasted secret: the nonce Lighthouse hands out is signed
        with the same key that signs this deployment's protocol tokens, so the
        Lighthouse account is simply the box's address. Re-running mints a fresh
        key for the same account.
        """
        from eth_account.messages import encode_defunct
        signer = self._signer()
        r = requests.get(f'{API_BASE}/api/auth/get_message',
                         params={'publicKey': signer.address}, timeout=20)
        r.raise_for_status()
        message = r.json() if r.text.startswith('"') else r.text
        sig = signer.sign_message(encode_defunct(text=str(message))).signature.hex()
        if not sig.startswith('0x'):
            sig = '0x' + sig
        r = requests.post(f'{API_BASE}/api/auth/create_api_key',
                          json={'publicKey': signer.address, 'signedMessage': sig},
                          timeout=20)
        r.raise_for_status()
        body = r.json()
        api_key = body.get('apiKey') if isinstance(body, dict) else body
        if not api_key or not isinstance(api_key, str):
            raise RuntimeError(f'lighthouse returned no key: {r.text[:200]}')
        self._write_creds({'api_key': api_key, 'source': 'wallet',
                           'address': signer.address, 'minted': int(time.time())})
        Mod._mint_error = None
        out = self.key_status()
        out['minted'] = True
        return out

    def ensure_key(self) -> str:
        """The key this instance will spend — minting one first if the
        deployment has none and auto-enrolment is on. Never raises: a mint that
        fails is remembered for a minute (so a burst of requests does not hammer
        Lighthouse) and reported by key_status()/status() as `auto_key.error`."""
        key = self.api_key
        if key or not AUTO_KEY or self._call_key:
            return key
        if time.time() - Mod._mint_failed_at < 60:
            return None
        try:
            self.mint_key()
        except Exception as e:
            Mod._mint_failed_at = time.time()
            Mod._mint_error = f'{type(e).__name__}: {e}'
            return None
        return self.api_key

    def key_source(self) -> str:
        """Where the key this instance spends comes from: call, env, wallet,
        pasted, or none."""
        if self._call_key:
            return 'call'
        if os.environ.get('LIGHTHOUSE_API_KEY'):
            return 'env'
        creds = self.creds()
        if creds.get('api_key'):
            return creds.get('source') or 'file'
        return 'none'

    def key_status(self, **kw) -> dict:
        """Is a key configured, and does Lighthouse accept it?"""
        key = self.api_key
        creds = self.creds()
        out = {'configured': bool(key), 'needs_key': not key,
               'source': self.key_source(),
               'address': creds.get('address') if creds.get('source') == 'wallet' else None,
               'auto_key': {'enabled': AUTO_KEY, 'error': Mod._mint_error}}
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
        key = self.ensure_key()
        if not key:
            why = f' (auto-mint failed: {Mod._mint_error})' if Mod._mint_error else ''
            raise RuntimeError(
                'no Lighthouse API key: m lighthouse/mint_key (this box enrols with its '
                'own wallet), m lighthouse/set_key <key> from files.lighthouse.storage, '
                f'or set LIGHTHOUSE_API_KEY{why}'
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

        r, url = self.fetch(cid, timeout=60)
        with open(out, 'wb') as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return {'cid': cid, 'path': str(out), 'gateway': url}

    def gateways(self) -> list:
        """Read gateways in the order they are tried — this deployment's first."""
        out = [self.gateway.rstrip('/')]
        for g in [GATEWAY] + FALLBACK_GATEWAYS:
            if g not in out:
                out.append(g)
        return out

    def fetch(self, cid: str, timeout: int = 60):
        """Open a streaming GET for a CID on the first gateway that serves it.

        Returns (response, url). Raises LookupError when every gateway answered
        with an error status, RuntimeError when none could be reached at all.
        """
        statuses, errors = [], []
        for g in self.gateways():
            url = f'{g}/ipfs/{cid}'
            try:
                r = requests.get(url, stream=True, timeout=timeout)
            except requests.RequestException as e:
                errors.append(f'{g}: {type(e).__name__}')
                continue
            if r.status_code < 400:
                return r, url
            r.close()
            statuses.append(f'{g.split("//")[-1]} {r.status_code}')
        if statuses:
            raise LookupError(f'no gateway served {cid} ({", ".join(statuses + errors)})')
        raise RuntimeError(f'gateway unreachable: {", ".join(errors)}')

    def preview(self, cid: str, max_bytes: int = 65536) -> dict:
        """Peek at a CID without downloading it: decoded text when it is text,
        the size, and whether what came back was cut short.

        One implementation, three faces: the API's /preview, the MCP tool and
        the CLI all land here, so what a console shows and what an agent reads
        can never be two different opinions about the same CID.
        """
        max_bytes = max(1, min(int(max_bytes), 1 << 20))
        r, url = self.fetch(cid, timeout=60)
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

    # ── the anonymous door ────────────────────────────────────────

    def anon_budget(self) -> dict:
        """What the no-token path may still spend today, from the index."""
        conn = self._db()
        used = conn.execute(
            'SELECT COALESCE(SUM(size),0) FROM objects WHERE owner=? AND timestamp>?',
            (ANON, int(time.time()) - 86_400)).fetchone()[0]
        conn.close()
        enabled = ANON_MAX_BYTES > 0 and ANON_DAILY_BYTES > 0
        return {'enabled': enabled, 'owner': ANON,
                'max_bytes': ANON_MAX_BYTES, 'daily_bytes': ANON_DAILY_BYTES,
                'used_today': int(used),
                'remaining_today': max(0, ANON_DAILY_BYTES - int(used)) if enabled else 0}

    def anon_check(self, size: int) -> None:
        """Raise PermissionError with a sentence when an anonymous upload of
        `size` bytes would overspend; return quietly when it fits."""
        b = self.anon_budget()
        if not b['enabled']:
            raise PermissionError('anonymous uploads are off on this deployment '
                                  '(LIGHTHOUSE_ANON_DAILY_BYTES=0) — send a '
                                  'protocol token or your own Lighthouse key')
        if size > b['max_bytes']:
            raise PermissionError(f'{size} bytes is over the anonymous per-upload '
                                  f'cap of {b["max_bytes"]} — sign in, or pass '
                                  'your own Lighthouse key')
        if size > b['remaining_today']:
            raise PermissionError(f'the anonymous daily budget is spent '
                                  f'({b["used_today"]} of {b["daily_bytes"]} bytes '
                                  'in the last 24h) — sign in, or pass your own '
                                  'Lighthouse key')

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
        key = self.ensure_key()
        return {
            'name': 'lighthouse',
            'objects': n,
            'state_dir': str(self.state),
            'gateway': self.gateway,
            'api': API_BASE,
            'configured': bool(key),
            'needs_key': not key,
            'key_source': self.key_source(),
            'key_address': self.creds().get('address'),
            'auto_key': {'enabled': AUTO_KEY, 'error': Mod._mint_error},
            'anon': self.anon_budget(),
        }

    def install(self) -> dict:
        return {
            'docs': 'https://docs.lighthouse.storage',
            'signup': 'https://files.lighthouse.storage',
            'env': 'export LIGHTHOUSE_API_KEY=...  (or m lighthouse/set_key <key>)',
            'keyless': 'm lighthouse/mint_key — this box signs a Lighthouse nonce with '
                       'its own wallet and keeps the key it gets back; uploads do '
                       'this on their own when no key is set (LIGHTHOUSE_AUTO_KEY)',
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
