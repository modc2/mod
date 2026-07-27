"""
dstore — unified decentralized storage over filecoin + hippius + lighthouse backends.

Wraps the filecoin, hippius and lighthouse orbit modules into a single put/get
interface, keyed by an Ethereum address owner (set via MetaMask SIWE auth on the app).

Usage (Python):
    import mod as m
    s = m.mod('dstore')()
    s.put('/path/to/file', backend='filecoin', owner='0xabc')
    s.put('/path/to/file', backend='both', owner='0xabc')
    s.get('bafy...')
    s.list(owner='0xabc')

Usage (CLI):
    m dstore/status
    m dstore/put /path/to/file backend=filecoin owner=0xabc
    m dstore/get bafy...
    m dstore/list owner=0xabc
    m dstore/backends
"""
import json
import os
import time
import sqlite3
from pathlib import Path

import mod as m

DIR = Path(__file__).resolve().parent.parent
STORE = Path(os.path.expanduser('~/.store-mod'))


class Mod:
    description = "Unified decentralized storage over filecoin + hippius + lighthouse backends."

    fns = [
        'forward', 'put', 'register', 'get', 'pin', 'list', 'rm', 'usage',
        'status', 'backends', 'start', 'stop', 'keys', 'set_key',
    ]

    # 'localfs' is the zero-dependency default: content-addressed files on
    # local disk (IPFS-compatible CIDs, no daemon). 'both' = filecoin+hippius.
    BACKENDS = ['localfs', 'filecoin', 'hippius', 'lighthouse', 'both']

    # Backends that take API credentials, settable at runtime via set_key().
    KEYED_BACKENDS = ['hippius', 'lighthouse']

    def __init__(self, store_path: str = None, **kw):
        self.module_dir = DIR
        self.store = Path(store_path) if store_path else STORE
        self.store.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store / 'store.db'
        self._init_db()
        self.config = self._load_config()
        self._localfs = None
        self._filecoin = None
        self._hippius = None
        self._lighthouse = None

    def _load_config(self):
        cfg = self.module_dir / 'config.json'
        if cfg.exists():
            with open(cfg) as f:
                return json.load(f)
        return {}

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute('''CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cid TEXT NOT NULL,
            backend TEXT NOT NULL,
            owner TEXT,
            key TEXT,
            size INTEGER,
            timestamp INTEGER NOT NULL,
            meta TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_store_owner ON objects(owner)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_store_cid ON objects(cid)')
        conn.commit()
        conn.close()

    def _db(self):
        return sqlite3.connect(str(self.db_path))

    def localfs(self):
        if self._localfs is None:
            self._localfs = m.mod('localfs')()
        return self._localfs

    def filecoin(self):
        if self._filecoin is None:
            self._filecoin = m.mod('filecoin')()
        return self._filecoin

    def hippius(self):
        if self._hippius is None:
            self._hippius = m.mod('hippius')()
        return self._hippius

    def lighthouse(self):
        if self._lighthouse is None:
            self._lighthouse = m.mod('lighthouse')()
        return self._lighthouse

    # ── Core API ──────────────────────────────────────────────────

    def forward(self, action: str = None, **kw):
        if not action:
            return self.status()
        fn = getattr(self, action, None)
        if not fn:
            return {'error': f'unknown action: {action}'}
        return fn(**kw)

    def put(self, path: str, backend: str = 'localfs', owner: str = None, key: str = None, **kw) -> dict:
        """Upload to one backend or both. Returns CIDs."""
        backend = backend.lower()
        if backend not in self.BACKENDS:
            raise ValueError(f'backend must be one of {self.BACKENDS}, got {backend}')

        results = {}
        if backend == 'localfs':
            try:
                r = self.localfs().fs.add_file(path)
                cid, size = r['Hash'], r.get('Size')
                results['localfs'] = {'cid': cid, 'size': size, 'backend': 'localfs'}
                self._record(cid, 'localfs', owner, key or os.path.basename(path), size)
            except Exception as e:
                results['localfs'] = {'error': str(e)}
        if backend in ('filecoin', 'both'):
            try:
                r = self.filecoin().put(path=path, owner=owner)
                results['filecoin'] = r
                self._record(r['cid'], 'filecoin', owner, key or os.path.basename(path), r.get('size'))
            except Exception as e:
                results['filecoin'] = {'error': str(e)}
        if backend in ('hippius', 'both'):
            try:
                r = self.hippius().put(path=path, owner=owner, key=key)
                results['hippius'] = r
                self._record(r['cid'], 'hippius', owner, key or os.path.basename(path), r.get('size'))
            except Exception as e:
                results['hippius'] = {'error': str(e)}
        if backend == 'lighthouse':
            try:
                r = self.lighthouse().put(path=path, owner=owner, key=key)
                results['lighthouse'] = r
                self._record(r['cid'], 'lighthouse', owner, key or os.path.basename(path), r.get('size'))
            except Exception as e:
                results['lighthouse'] = {'error': str(e)}

        return {'owner': owner, 'backend': backend, 'results': results}

    def register(self, cid: str, backend: str = 'external', owner: str = None,
                 key: str = None, size: int = 0, scheme: str = None, url: str = None) -> dict:
        """Index a CID that lives in *some other* system without uploading bytes.

        The store is CID-agnostic: an object can be a native localfs/filecoin/
        hippius CID, or any external identifier (arweave tx, ipfs from another
        node, s3 key, …). `register` makes such a reference a first-class object
        — listable, shareable, poolable — resolved via `url`/scheme rather than
        a local backend. `meta` carries the scheme + gateway url.
        """
        meta = {'scheme': scheme, 'url': url, 'external': backend == 'external'}
        self._record(cid, backend, owner, key, int(size or 0), meta=meta)
        return {'cid': cid, 'backend': backend, 'owner': owner, 'key': key,
                'scheme': scheme, 'url': url, 'registered': True}

    def get(self, cid: str, backend: str = None, out: str = None) -> dict:
        """Retrieve a CID. If backend unspecified, infers from local index, else tries both."""
        if not backend:
            conn = self._db()
            row = conn.execute('SELECT backend FROM objects WHERE cid=? ORDER BY timestamp DESC LIMIT 1', (cid,)).fetchone()
            conn.close()
            backend = row[0] if row else 'localfs'

        if backend == 'localfs':
            try:
                data = self.localfs().fs.get_file(cid)
                if out:
                    with open(out, 'wb') as f:
                        f.write(data)
                return {'backend': 'localfs', 'cid': cid, 'size': len(data), 'out': out}
            except Exception as e:
                return {'cid': cid, 'error': f'localfs: {e}'}
        if backend == 'filecoin':
            try:
                return {'backend': 'filecoin', **self.filecoin().get(cid=cid, out=out)}
            except Exception as e:
                # fall through to hippius
                try:
                    return {'backend': 'hippius', 'fallback': True, **self.hippius().get(cid=cid, out=out)}
                except Exception as e2:
                    return {'cid': cid, 'error': f'both failed: filecoin={e}; hippius={e2}'}
        elif backend == 'hippius':
            try:
                return {'backend': 'hippius', **self.hippius().get(cid=cid, out=out)}
            except Exception as e:
                try:
                    return {'backend': 'filecoin', 'fallback': True, **self.filecoin().get(cid=cid, out=out)}
                except Exception as e2:
                    return {'cid': cid, 'error': f'both failed: hippius={e}; filecoin={e2}'}
        elif backend == 'lighthouse':
            try:
                return {'backend': 'lighthouse', **self.lighthouse().get(cid=cid, out=out)}
            except Exception as e:
                return {'cid': cid, 'error': f'lighthouse: {e}'}
        return {'cid': cid, 'error': f'unknown backend: {backend}'}

    def pin(self, cid: str, backend: str = 'localfs', owner: str = None) -> dict:
        if backend == 'localfs':
            r = self._safe(lambda: self.localfs().pin(cid))
        elif backend == 'filecoin':
            r = self.filecoin().pin(cid=cid, owner=owner)
        elif backend == 'hippius':
            r = self.hippius().pin(cid=cid, owner=owner)
        elif backend == 'lighthouse':
            r = self.lighthouse().pin(cid=cid, owner=owner)
        elif backend == 'both':
            r = {
                'filecoin': self.filecoin().pin(cid=cid, owner=owner),
                'hippius': self.hippius().pin(cid=cid, owner=owner),
            }
        else:
            return {'error': f'unknown backend: {backend}'}
        self._record(cid, backend, owner, None, 0)
        return r

    def list(self, owner: str = None, backend: str = None, limit: int = 100) -> list:
        conn = self._db()
        cols = ['cid', 'backend', 'owner', 'key', 'size', 'timestamp', 'meta']
        q = f"SELECT {','.join(cols)} FROM objects WHERE 1=1"
        params = []
        if owner:
            q += ' AND owner=?'; params.append(owner)
        if backend:
            q += ' AND backend=?'; params.append(backend)
        q += ' ORDER BY timestamp DESC LIMIT ?'
        params.append(int(limit))
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]

    def usage(self, owner: str = None) -> dict:
        """Bytes + object count stored by an owner (or globally if owner is None).

        Sums the recorded sizes in the index. Note a CID written to `both`
        backends is counted once per backend row, mirroring real pin cost.
        """
        conn = self._db()
        if owner:
            row = conn.execute(
                'SELECT COALESCE(SUM(size),0), COUNT(*) FROM objects WHERE owner=?',
                (owner,),
            ).fetchone()
        else:
            row = conn.execute('SELECT COALESCE(SUM(size),0), COUNT(*) FROM objects').fetchone()
        conn.close()
        return {'owner': owner, 'bytes': int(row[0] or 0), 'objects': int(row[1] or 0)}

    def rm(self, cid: str) -> dict:
        conn = self._db()
        conn.execute('DELETE FROM objects WHERE cid=?', (cid,))
        conn.commit()
        conn.close()
        return {'cid': cid, 'removed': True}

    def status(self) -> dict:
        conn = self._db()
        n = conn.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        by_backend = {b: conn.execute('SELECT COUNT(*) FROM objects WHERE backend=?', (b,)).fetchone()[0]
                      for b in ('localfs', 'filecoin', 'hippius', 'lighthouse')}
        conn.close()
        return {
            'name': 'store',
            'objects': n,
            'by_backend': by_backend,
            'store': str(self.store),
            'backends': {
                'localfs': self._safe(lambda: self.localfs().stats()),
                'filecoin': self._safe(lambda: self.filecoin().status()),
                'hippius': self._safe(lambda: self.hippius().status()),
                'lighthouse': self._safe(lambda: self.lighthouse().status()),
            },
        }

    def keys(self) -> dict:
        """Credential status per keyed backend (configured / needs_key / valid)."""
        out = {}
        for b in self.KEYED_BACKENDS:
            out[b] = self._safe(lambda b=b: getattr(self, b)().key_status())
        return out

    def set_key(self, backend: str, **creds) -> dict:
        """Persist API credentials for a keyed backend (hippius S3 pair, lighthouse api_key)."""
        backend = (backend or '').lower()
        if backend not in self.KEYED_BACKENDS:
            return {'error': f'backend must be one of {self.KEYED_BACKENDS}, got {backend!r}'}
        return getattr(self, backend)().set_key(**creds)

    def backends(self) -> list:
        return list(self.BACKENDS)

    def start(self) -> dict:
        """Start both backend daemons (best-effort)."""
        return {
            'filecoin': self._safe(lambda: self.filecoin().start_node()),
            'hippius': self._safe(lambda: self.hippius().start_node()),
        }

    def stop(self) -> dict:
        return {
            'filecoin': self._safe(lambda: self.filecoin().stop_node()),
            'hippius': self._safe(lambda: self.hippius().stop_node()),
        }

    # ── helpers ───────────────────────────────────────────────────

    def _safe(self, fn):
        try:
            return fn()
        except Exception as e:
            return {'error': str(e)}

    def _record(self, cid, backend, owner, key, size, meta=None):
        ts = int(time.time())
        conn = self._db()
        conn.execute(
            'INSERT INTO objects (cid,backend,owner,key,size,timestamp,meta) VALUES (?,?,?,?,?,?,?)',
            (cid, backend, owner, key, size, ts, json.dumps(meta) if meta else None),
        )
        conn.commit()
        conn.close()
