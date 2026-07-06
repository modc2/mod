"""
multistore — unified storage facade over every storage orbit module.

Connects: filecoin, hippius, ipfs, arweave, and the core localfs store under
a single put/get/list/pin/replicate interface. Each backend keeps its own
state; multistore is a thin coordinator with a SQLite index that maps
(cid, backend, owner) so cross-backend lookups stay cheap.

Usage (Python):
    import mod as m
    s = m.mod('multistore')()

    s.backends()                              # which backends are alive
    s.put('/path/to/file', backend='all')     # fan-out to every alive backend
    s.put('/path/to/file', backend='ipfs')    # single target
    s.get('Qm...')                            # try local first, then anywhere
    s.replicate('Qm...', from_='ipfs', to='filecoin')
    s.list(owner='0xabc')
    s.status()

Usage (CLI):
    m multistore/backends
    m multistore/put /path/to/file backend=all
    m multistore/get Qm...
    m multistore/replicate cid=Qm... from_=ipfs to=filecoin
    m multistore/status
"""
import json
import os
import time
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import mod as m

from .backend import StorageBackend, LegacyAdapter

DIR = Path(__file__).resolve().parent.parent
STORE = Path(os.path.expanduser('~/.multistore'))

# Every backend we know how to talk to. Order matters for `get` fallback:
# we try whatever is recorded in our index first, then walk this list.
#
# Adding a new backend:
#   1. Build your module at mod/orbit/<name>/ with a Mod class that
#      either inherits from multistore.backend.StorageBackend or simply
#      exposes put/get/list/rm — LegacyAdapter wraps the latter.
#   2. Append the name here (or to multistore/config.json `backends`).
#   3. That's it — backends(), health(), put(), get(), replicate() pick it up.
BACKEND_NAMES = ['ipfs', 'filecoin', 'hippius', 'arweave', 'localfs']


class Mod:
    description = "Unified storage facade — IPFS + Filecoin + Hippius + Arweave + local FS."

    fns = [
        'forward', 'backends', 'health', 'status',
        'put', 'get', 'pin', 'rm', 'list', 'replicate',
        'start_all', 'stop_all',
    ]

    BACKENDS = list(BACKEND_NAMES)

    def __init__(self, store_path: str = None, **kw):
        self.module_dir = DIR
        self.store = Path(store_path) if store_path else STORE
        self.store.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store / 'index.db'
        self._init_db()
        self._impl_cache: Dict[str, Any] = {}

    # ── DB ────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute('''CREATE TABLE IF NOT EXISTS objects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cid TEXT NOT NULL,
            backend TEXT NOT NULL,
            owner TEXT,
            key TEXT,
            size INTEGER,
            timestamp INTEGER NOT NULL,
            meta TEXT,
            UNIQUE(cid, backend)
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ms_owner ON objects(owner)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ms_cid ON objects(cid)')
        conn.commit()
        conn.close()

    def _db(self):
        return sqlite3.connect(str(self.db_path))

    def _record(self, cid: str, backend: str, owner: str = None,
                key: str = None, size: int = None, meta: dict = None) -> None:
        if not cid:
            return
        conn = self._db()
        conn.execute(
            'INSERT OR REPLACE INTO objects (cid,backend,owner,key,size,timestamp,meta) '
            'VALUES (?,?,?,?,?,?,?)',
            (cid, backend, owner, key, int(size or 0), int(time.time()),
             json.dumps(meta) if meta else None),
        )
        conn.commit()
        conn.close()

    # ── backend resolution ────────────────────────────────────────

    def _impl(self, backend: str):
        """Lazy-resolve a backend module + wrap in a StorageBackend-shaped
        adapter so every call site has the same surface. Returns None if
        the underlying module can't be loaded (offline backend never
        crashes the facade)."""
        if backend in self._impl_cache:
            return self._impl_cache[backend]
        try:
            if backend == 'localfs':
                raw = m.mod('store')()
            else:
                raw = m.mod(backend)()
        except Exception:
            self._impl_cache[backend] = None
            return None
        # If the module already inherits StorageBackend, use it as-is —
        # otherwise wrap it so put/get/list/rm have consistent semantics.
        adapter: StorageBackend = (
            raw if isinstance(raw, StorageBackend) else LegacyAdapter(raw, name=backend)
        )
        self._impl_cache[backend] = adapter
        return adapter

    def _alive(self, backend: str) -> bool:
        return self._impl(backend) is not None

    def _alive_backends(self) -> List[str]:
        return [b for b in BACKEND_NAMES if self._alive(b)]

    # ── API ───────────────────────────────────────────────────────

    def forward(self, action: str = None, **kw):
        if not action:
            return self.status()
        fn = getattr(self, action, None)
        if not fn:
            return {'error': f'unknown action: {action}'}
        return fn(**kw)

    def backends(self) -> Dict[str, Any]:
        """List every known backend with its alive/down state."""
        out = {}
        for b in BACKEND_NAMES:
            impl = self._impl(b)
            out[b] = {'available': impl is not None, 'module': f'orbit/{b}' if b != 'localfs' else 'core/store'}
        return out

    def health(self) -> Dict[str, Any]:
        """Per-backend health: alive + the backend's own status() if it exposes one."""
        out = {}
        for b in BACKEND_NAMES:
            impl = self._impl(b)
            if not impl:
                out[b] = {'alive': False}
                continue
            entry = {'alive': True}
            for hook in ('status', 'node_status', 'id'):
                fn = getattr(impl, hook, None)
                if callable(fn):
                    try:
                        entry[hook] = fn()
                    except Exception as e:
                        entry[hook] = {'error': str(e)}
                    break
            out[b] = entry
        return out

    def status(self) -> Dict[str, Any]:
        conn = self._db()
        total = conn.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        by_backend = {}
        for b in BACKEND_NAMES:
            by_backend[b] = conn.execute(
                'SELECT COUNT(*) FROM objects WHERE backend=?', (b,)
            ).fetchone()[0]
        conn.close()
        return {
            'name': 'multistore',
            'total_objects': total,
            'by_backend': by_backend,
            'alive_backends': self._alive_backends(),
            'store': str(self.store),
        }

    # ── put / get / pin ──────────────────────────────────────────

    def put(self, path: str, backend: str = 'all', owner: str = None,
            key: str = None, **kw) -> Dict[str, Any]:
        """Upload `path` to one or more backends. backend='all' fans out to
        every alive backend (durability). Returns per-backend CIDs."""
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            return {'ok': False, 'error': f'not found: {path}'}
        size = os.path.getsize(path)

        targets = self._alive_backends() if backend == 'all' else [backend]
        results = {}
        for b in targets:
            impl = self._impl(b)
            if not impl:
                results[b] = {'ok': False, 'error': f'{b} unavailable'}
                continue
            try:
                # Try each backend's put() — the canonical method name across
                # filecoin/hippius/dstore. For IPFS use add().
                if b == 'ipfs':
                    fn = getattr(impl, 'add_file', None) or getattr(impl, 'add', None)
                    if not fn:
                        raise RuntimeError('ipfs has no add()')
                    r = fn(path) if fn.__name__ == 'add_file' else fn(open(path, 'rb').read())
                    cid = r['Hash'] if isinstance(r, dict) else r
                    results[b] = {'ok': True, 'cid': cid, 'size': size}
                elif b == 'localfs':
                    # core/store is a KV — use the file basename as the key.
                    k = key or os.path.basename(path)
                    with open(path, 'rb') as f:
                        data = f.read()
                    impl.put(k, data if isinstance(data, (str, bytes)) else data.decode('utf-8', 'ignore'))
                    results[b] = {'ok': True, 'cid': f'localfs:{k}', 'size': size}
                else:
                    fn = getattr(impl, 'put', None)
                    if not fn:
                        raise RuntimeError(f'{b} has no put()')
                    r = fn(path=path, owner=owner, **({'key': key} if key else {}))
                    cid = r.get('cid') if isinstance(r, dict) else r
                    results[b] = {'ok': True, 'cid': cid, 'size': r.get('size', size) if isinstance(r, dict) else size}
                if results[b].get('cid'):
                    self._record(results[b]['cid'], b, owner=owner, key=key, size=size)
            except Exception as e:
                results[b] = {'ok': False, 'error': str(e)}

        cids = [v.get('cid') for v in results.values() if v.get('ok')]
        return {'ok': bool(cids), 'owner': owner, 'targets': targets, 'cids': cids, 'results': results}

    def get(self, cid: str, backend: str = None, out: str = None) -> Dict[str, Any]:
        """Fetch a CID. If backend unset: try the backend our index recorded
        first, then walk every alive backend until one returns the bytes."""
        # 1. Prefer the recorded backend for this CID.
        order: List[str] = []
        if backend:
            order = [backend]
        else:
            conn = self._db()
            row = conn.execute(
                'SELECT backend FROM objects WHERE cid=? ORDER BY timestamp DESC LIMIT 1',
                (cid,),
            ).fetchone()
            conn.close()
            if row:
                order.append(row[0])
            for b in self._alive_backends():
                if b not in order:
                    order.append(b)

        errors = {}
        for b in order:
            impl = self._impl(b)
            if not impl:
                errors[b] = 'unavailable'
                continue
            try:
                fn = getattr(impl, 'get', None)
                if not fn:
                    errors[b] = 'no get()'
                    continue
                # Each backend's get takes slightly different args; pass the
                # things they all understand.
                kw = {'cid': cid}
                if out and b != 'localfs':
                    kw['out'] = out
                r = fn(**kw)
                return {'ok': True, 'cid': cid, 'backend': b, 'result': r}
            except Exception as e:
                errors[b] = str(e)
        return {'ok': False, 'cid': cid, 'errors': errors}

    def pin(self, cid: str, backend: str = 'all', owner: str = None) -> Dict[str, Any]:
        """Pin a CID. backend='all' pins on every backend that knows how."""
        targets = self._alive_backends() if backend == 'all' else [backend]
        results = {}
        for b in targets:
            impl = self._impl(b)
            if not impl:
                results[b] = {'ok': False, 'error': 'unavailable'}
                continue
            try:
                fn = getattr(impl, 'pin', None) or getattr(impl, 'pin_add', None)
                if not fn:
                    results[b] = {'ok': False, 'error': 'no pin()'}
                    continue
                # filecoin/hippius pin(cid, owner=...); ipfs pin_add(cid).
                r = fn(cid=cid, owner=owner) if 'owner' in fn.__code__.co_varnames else fn(cid)
                results[b] = {'ok': True, 'result': r}
                self._record(cid, b, owner=owner)
            except Exception as e:
                results[b] = {'ok': False, 'error': str(e)}
        return {'cid': cid, 'results': results}

    def rm(self, cid: str, backend: str = 'all', caller: str = None) -> Dict[str, Any]:
        """Remove a CID from one or every backend."""
        targets = self._alive_backends() if backend == 'all' else [backend]
        results = {}
        for b in targets:
            impl = self._impl(b)
            if not impl:
                results[b] = {'ok': False, 'error': 'unavailable'}
                continue
            try:
                fn = getattr(impl, 'rm', None) or getattr(impl, 'pin_rm', None)
                if not fn:
                    results[b] = {'ok': False, 'error': 'no rm()'}
                    continue
                r = fn(cid=cid, caller=caller) if 'caller' in fn.__code__.co_varnames else fn(cid)
                results[b] = {'ok': True, 'result': r}
            except Exception as e:
                results[b] = {'ok': False, 'error': str(e)}
        # Drop from our index regardless of per-backend success.
        conn = self._db()
        if backend == 'all':
            conn.execute('DELETE FROM objects WHERE cid=?', (cid,))
        else:
            conn.execute('DELETE FROM objects WHERE cid=? AND backend=?', (cid, backend))
        conn.commit(); conn.close()
        return {'cid': cid, 'results': results}

    def list(self, owner: str = None, backend: str = None,
             limit: int = 100, offset: int = 0, search: str = None) -> Dict[str, Any]:
        """Paginated + searchable index of every recorded (cid, backend) pair."""
        conn = self._db()
        cols = ['cid', 'backend', 'owner', 'key', 'size', 'timestamp', 'meta']
        q = f"SELECT {','.join(cols)} FROM objects WHERE 1=1"
        params: List[Any] = []
        if owner:
            q += ' AND owner=?'; params.append(owner)
        if backend:
            q += ' AND backend=?'; params.append(backend)
        if search:
            q += ' AND (cid LIKE ? OR key LIKE ?)'; params += [f'%{search}%', f'%{search}%']
        total = conn.execute(q.replace(','.join(cols), 'COUNT(*)'), params).fetchone()[0]
        q += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
        params += [int(limit), int(offset)]
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return {
            'total': total,
            'offset': int(offset),
            'limit': int(limit),
            'count': len(rows),
            'query': search,
            'objects': [dict(zip(cols, r)) for r in rows],
        }

    # ── replication ──────────────────────────────────────────────

    def replicate(self, cid: str, from_: str, to: str, owner: str = None) -> Dict[str, Any]:
        """Copy a CID from one backend to another. Useful for moving content
        between durability classes (e.g. ipfs → filecoin for archival)."""
        if from_ == to:
            return {'ok': False, 'error': 'from_ and to must differ'}
        # 1. Fetch from source to a temp file.
        tmp = self.store / 'replicate-cache' / f'{cid}-{int(time.time()*1000)}'
        tmp.parent.mkdir(parents=True, exist_ok=True)
        fetch = self.get(cid=cid, backend=from_, out=str(tmp))
        if not fetch.get('ok'):
            return {'ok': False, 'stage': 'fetch', 'error': fetch.get('errors')}
        # The backend's get may return a different on-disk path — find one.
        local_path = str(tmp)
        if not os.path.exists(local_path):
            inner = fetch.get('result')
            if isinstance(inner, dict) and inner.get('path'):
                local_path = inner['path']
        if not os.path.exists(local_path):
            return {'ok': False, 'stage': 'fetch', 'error': 'no local file produced'}
        # 2. Push to destination.
        push = self.put(path=local_path, backend=to, owner=owner)
        if not push.get('ok'):
            return {'ok': False, 'stage': 'push', 'error': push.get('results')}
        new_cids = [v.get('cid') for v in push['results'].values() if v.get('ok')]
        return {'ok': True, 'cid': cid, 'from': from_, 'to': to, 'new_cids': new_cids}

    # ── batch lifecycle helpers ──────────────────────────────────

    def start_all(self) -> Dict[str, Any]:
        """Best-effort: start any backend daemons (lotus, substrate, kubo)."""
        out = {}
        for b in BACKEND_NAMES:
            impl = self._impl(b)
            if not impl:
                out[b] = {'ok': False, 'error': 'unavailable'}
                continue
            fn = getattr(impl, 'start_node', None)
            if not fn:
                out[b] = {'ok': True, 'note': 'no daemon to start'}
                continue
            try:
                out[b] = {'ok': True, 'result': fn()}
            except Exception as e:
                out[b] = {'ok': False, 'error': str(e)}
        return out

    def stop_all(self) -> Dict[str, Any]:
        """Stop every backend daemon we know how to control."""
        out = {}
        for b in BACKEND_NAMES:
            impl = self._impl(b)
            if not impl:
                continue
            fn = getattr(impl, 'stop_node', None)
            if not fn:
                continue
            try:
                out[b] = {'ok': True, 'result': fn()}
            except Exception as e:
                out[b] = {'ok': False, 'error': str(e)}
        return out
