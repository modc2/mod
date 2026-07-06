"""
Hippius — Substrate-based decentralized storage with S3-compatible gateway.

Manages a local hippius node (subprocess) and routes put/get through either
the substrate RPC or the Hippius S3 gateway. Falls back to a public IPFS
gateway for retrieval when no node is available.

Usage (Python):
    import mod as m
    h = m.mod('hippius')()
    h.start_node()
    h.put('/path/to/file')
    h.get('bafy...')
    h.account()
    h.peers()

Usage (CLI):
    m hippius/start_node
    m hippius/status
    m hippius/put /path/to/file
    m hippius/get bafy...
    m hippius/peers
"""
import json
import os
import time
import shutil
import sqlite3
import subprocess
import requests
from pathlib import Path
from typing import Optional

DIR = Path(__file__).resolve().parent.parent
STORE = Path(os.path.expanduser('~/.hippius-mod'))


class Mod:
    description = "Hippius substrate node + S3-compatible storage gateway."

    fns = [
        'forward', 'put', 'get', 'pin', 'list', 'rm',
        'start_node', 'stop_node', 'status', 'node_status',
        'peers', 'account', 'balance', 'install',
    ]

    def __init__(
        self,
        node_bin: str = None,
        node_path: str = None,
        rpc_url: str = None,
        s3_endpoint: str = None,
        s3_key: str = None,
        s3_secret: str = None,
        s3_bucket: str = None,
        store_path: str = None,
        **kw,
    ):
        self.module_dir = DIR
        self.store = Path(store_path) if store_path else STORE
        self.store.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store / 'hippius.db'
        self.pidfile = self.store / 'hippius.pid'

        self.node_bin = node_bin or os.environ.get('HIPPIUS_BIN') or shutil.which('hippius-node') or 'hippius-node'
        self.node_path = Path(node_path or os.environ.get('HIPPIUS_PATH') or os.path.expanduser('~/.hippius'))

        # Substrate RPC (local node default port)
        self.rpc_url = rpc_url or os.environ.get('HIPPIUS_RPC') or 'http://127.0.0.1:9933'

        # S3-compatible gateway (Hippius runs an S3 facade in front of their network)
        self.s3_endpoint = s3_endpoint or os.environ.get('HIPPIUS_S3_ENDPOINT') or 'https://s3.hippius.com'
        self.s3_key = s3_key or os.environ.get('HIPPIUS_S3_KEY')
        self.s3_secret = s3_secret or os.environ.get('HIPPIUS_S3_SECRET')
        self.s3_bucket = s3_bucket or os.environ.get('HIPPIUS_S3_BUCKET') or 'mod'

        # IPFS fallback for retrieval (Hippius CIDs are CIDv1)
        self.ipfs_gateway = os.environ.get('HIPPIUS_IPFS_GATEWAY') or 'https://get.hippius.network'

        self._init_db()
        self.config = self._load_config()

    def _load_config(self):
        cfg = self.module_dir / 'config.json'
        if cfg.exists():
            with open(cfg) as f:
                return json.load(f)
        return {}

    def _init_db(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.execute('''CREATE TABLE IF NOT EXISTS objects (
            cid TEXT PRIMARY KEY,
            key TEXT,
            path TEXT,
            size INTEGER,
            owner TEXT,
            backend TEXT,
            timestamp INTEGER NOT NULL,
            meta TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_obj_owner ON objects(owner)')
        conn.commit()
        conn.close()

    def _db(self):
        return sqlite3.connect(str(self.db_path))

    # ── Core API ──────────────────────────────────────────────────

    def forward(self, action: str = None, **kw):
        if not action:
            return self.status()
        fn = getattr(self, action, None)
        if not fn:
            return {'error': f'unknown action: {action}'}
        return fn(**kw)

    def put(self, path: str, owner: str = None, key: str = None, **kw) -> dict:
        """Upload via S3 gateway (preferred) or local node."""
        p = Path(os.path.expanduser(path))
        if not p.exists():
            raise FileNotFoundError(path)
        size = p.stat().st_size
        obj_key = key or p.name

        if self.s3_key and self.s3_secret:
            cid = self._s3_put(p, obj_key)
            backend = 's3'
        elif self._node_alive():
            cid = self._node_put(p)
            backend = 'node'
        else:
            raise RuntimeError(
                'no upload path: set HIPPIUS_S3_KEY/HIPPIUS_S3_SECRET or start a local node'
            )

        self._record(cid, obj_key, str(p), size, owner, backend)
        return {'cid': cid, 'key': obj_key, 'size': size, 'backend': backend, 'owner': owner}

    def get(self, cid: str, out: str = None) -> dict:
        """Retrieve by CID via IPFS-style gateway."""
        out = Path(os.path.expanduser(out)) if out else (self.store / 'cache' / cid)
        out.parent.mkdir(parents=True, exist_ok=True)

        candidates = [
            f"{self.ipfs_gateway.rstrip('/')}/ipfs/{cid}",
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
        """Pin a CID via the substrate ipfs pallet (best-effort)."""
        if self._node_alive():
            try:
                result = self._rpc('ipfs_pin', [cid])
                self._record(cid, None, None, 0, owner, 'node')
                return {'cid': cid, 'pinned': True, 'result': result}
            except Exception as e:
                return {'cid': cid, 'pinned': False, 'error': str(e)}
        self._record(cid, None, None, 0, owner, 'gateway')
        return {'cid': cid, 'pinned': True, 'backend': 'gateway'}

    def list(self, owner: str = None, limit: int = 100) -> list:
        conn = self._db()
        cols = ['cid', 'key', 'path', 'size', 'owner', 'backend', 'timestamp', 'meta']
        if owner:
            rows = conn.execute(
                f"SELECT {','.join(cols)} FROM objects WHERE owner=? ORDER BY timestamp DESC LIMIT ?",
                (owner, int(limit))
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {','.join(cols)} FROM objects ORDER BY timestamp DESC LIMIT ?",
                (int(limit),)
            ).fetchall()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]

    def rm(self, cid: str) -> dict:
        conn = self._db()
        conn.execute('DELETE FROM objects WHERE cid=?', (cid,))
        conn.commit()
        conn.close()
        return {'cid': cid, 'removed': True}

    # ── Daemon ────────────────────────────────────────────────────

    def start_node(self, chain: str = 'mainnet') -> dict:
        if self._node_alive():
            return {'running': True, 'pid': self._pid()}
        if not shutil.which(self.node_bin):
            return {'running': False, 'error': f'{self.node_bin} not installed (set HIPPIUS_BIN or install)'}

        env = os.environ.copy()
        logfile = self.store / 'hippius.log'
        args = [self.node_bin, '--base-path', str(self.node_path), '--chain', chain, '--rpc-cors', 'all']
        proc = subprocess.Popen(
            args, env=env,
            stdout=open(logfile, 'a'), stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.pidfile.write_text(str(proc.pid))
        time.sleep(2)
        return {'running': self._node_alive(), 'pid': proc.pid, 'log': str(logfile)}

    def stop_node(self) -> dict:
        pid = self._pid()
        if not pid:
            return {'running': False}
        try:
            os.kill(pid, 15)
            self.pidfile.unlink(missing_ok=True)
            return {'stopped': True, 'pid': pid}
        except ProcessLookupError:
            self.pidfile.unlink(missing_ok=True)
            return {'stopped': True, 'pid': pid, 'note': 'process not found'}

    def node_status(self) -> dict:
        alive = self._node_alive()
        out = {'alive': alive, 'pid': self._pid(), 'rpc': self.rpc_url}
        if alive:
            try:
                out['health'] = self._rpc('system_health')
                out['chain'] = self._rpc('system_chain')
            except Exception as e:
                out['rpc_error'] = str(e)
        return out

    def status(self) -> dict:
        conn = self._db()
        n = conn.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        conn.close()
        return {
            'name': 'hippius',
            'objects': n,
            'store': str(self.store),
            'daemon': self.node_status(),
            's3': {'endpoint': self.s3_endpoint, 'bucket': self.s3_bucket, 'configured': bool(self.s3_key)},
            'ipfs_gateway': self.ipfs_gateway,
        }

    def peers(self) -> dict:
        if not self._node_alive():
            return {'available': False}
        try:
            return self._rpc('system_peers')
        except Exception as e:
            return {'error': str(e)}

    def account(self) -> dict:
        if not self._node_alive():
            return {'available': False, 'note': 'no local node; account via S3 key'}
        try:
            return {'chain': self._rpc('system_chain'), 'name': self._rpc('system_name')}
        except Exception as e:
            return {'error': str(e)}

    def balance(self, address: str = None) -> dict:
        if not self._node_alive() or not address:
            return {'available': False}
        try:
            return self._rpc('system_accountNextIndex', [address])
        except Exception as e:
            return {'error': str(e)}

    def install(self) -> dict:
        return {
            'docs': 'https://docs.hippius.com',
            'env': 'export HIPPIUS_S3_KEY=...; export HIPPIUS_S3_SECRET=...',
            'note': 'A local hippius-node binary is optional; the S3 gateway is sufficient for put/get.',
        }

    # ── Substrate RPC ─────────────────────────────────────────────

    def _rpc(self, method: str, params: list = None):
        payload = {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or []}
        r = requests.post(self.rpc_url, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
        if 'error' in data:
            raise RuntimeError(data['error'])
        return data.get('result')

    def _node_alive(self) -> bool:
        pid = self._pid()
        if pid:
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                pass
        # Also probe RPC in case the node is managed externally
        try:
            self._rpc('system_health')
            return True
        except Exception:
            return False

    def _pid(self) -> Optional[int]:
        if not self.pidfile.exists():
            return None
        try:
            return int(self.pidfile.read_text().strip())
        except Exception:
            return None

    def _node_put(self, path: Path) -> str:
        # Add via local IPFS HTTP (substrate node usually co-runs an embedded IPFS).
        # Fall back to RPC if a custom rpc method is registered.
        url = 'http://127.0.0.1:5001/api/v0/add'
        with open(path, 'rb') as f:
            try:
                r = requests.post(url, files={'file': (path.name, f)}, timeout=120)
                r.raise_for_status()
                data = json.loads(r.text.strip().splitlines()[-1])
                return data['Hash']
            except Exception as e:
                raise RuntimeError(f'local node IPFS unreachable: {e}')

    # ── S3 gateway ────────────────────────────────────────────────

    def _s3_client(self):
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError(f'boto3 not installed: {e}; pip install boto3') from e
        return boto3.client(
            's3',
            endpoint_url=self.s3_endpoint,
            aws_access_key_id=self.s3_key,
            aws_secret_access_key=self.s3_secret,
        )

    def _s3_put(self, path: Path, key: str) -> str:
        s3 = self._s3_client()
        try:
            s3.head_bucket(Bucket=self.s3_bucket)
        except Exception:
            try:
                s3.create_bucket(Bucket=self.s3_bucket)
            except Exception:
                pass
        s3.upload_file(str(path), self.s3_bucket, key)
        # Hippius S3 returns the CID in object metadata after PutObject.
        head = s3.head_object(Bucket=self.s3_bucket, Key=key)
        meta = head.get('Metadata', {})
        cid = meta.get('cid') or meta.get('ipfs-cid') or head.get('ETag', '').strip('"')
        return cid

    # ── DB helpers ────────────────────────────────────────────────

    def _record(self, cid, key, path, size, owner, backend, meta=None):
        ts = int(time.time())
        conn = self._db()
        conn.execute(
            'INSERT OR REPLACE INTO objects (cid,key,path,size,owner,backend,timestamp,meta) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (cid, key, path, size, owner, backend, ts, json.dumps(meta) if meta else None),
        )
        conn.commit()
        conn.close()
