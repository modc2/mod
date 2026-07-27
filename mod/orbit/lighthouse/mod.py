"""
Lighthouse — perpetual decentralized storage (lighthouse.storage by Lighthouse Labs).

Uploads go straight to the Lighthouse HTTPS node API (Bearer API key) and land
on IPFS/Filecoin with perpetual pinning; retrieval rides the Lighthouse IPFS
gateway with public-gateway fallbacks. A local sqlite index keeps an
owner-keyed record of everything stored through this module.

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
STORE = Path(os.path.expanduser('~/.mod/lighthouse'))

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
        'forward', 'put', 'get', 'pin', 'list', 'rm',
        'status', 'usage', 'uploads', 'set_key', 'key_status', 'install',
    ]

    def __init__(self, api_key: str = None, store_path: str = None, **kw):
        self.module_dir = DIR
        self.store = Path(store_path) if store_path else STORE
        self.store.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store / 'lighthouse.db'
        self.creds_path = self.store / 'credentials.json'
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
        out = Path(os.path.expanduser(out)) if out else (self.store / 'cache' / cid)
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
            'store': str(self.store),
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
