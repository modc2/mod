"""
Filecoin — Lotus daemon + storage deal manager.

Manages a local lotus daemon for storage deals and CID retrieval. Falls back
to an HTTP gateway (Lighthouse / Estuary / public IPFS) when no daemon is
available, so put/get work even without a full Filecoin node.

Usage (Python):
    import mod as m
    f = m.mod('filecoin')()
    f.start_node()
    f.put('/path/to/file')
    f.get('bafy...')
    f.deals()
    f.wallet()

Usage (CLI):
    m filecoin/start_node
    m filecoin/status
    m filecoin/put /path/to/file
    m filecoin/get bafy...
    m filecoin/deals
    m filecoin/wallet
"""
import json
import os
import time
import hashlib
import shutil
import sqlite3
import subprocess
import requests
from pathlib import Path
from typing import Optional

DIR = Path(__file__).resolve().parent.parent
STORE = Path(os.path.expanduser('~/.filecoin-mod'))


class Mod:
    description = "Lotus daemon + Filecoin storage deals with HTTP gateway fallback."

    fns = [
        'forward', 'put', 'get', 'pin', 'list', 'rm',
        'start_node', 'stop_node', 'status', 'node_status',
        'deals', 'wallet', 'balance', 'install',
    ]

    def __init__(
        self,
        lotus_bin: str = None,
        lotus_path: str = None,
        gateway: str = None,
        gateway_token: str = None,
        store_path: str = None,
        **kw,
    ):
        self.module_dir = DIR
        self.store = Path(store_path) if store_path else STORE
        self.store.mkdir(parents=True, exist_ok=True)
        self.db_path = self.store / 'filecoin.db'
        self.pidfile = self.store / 'lotus.pid'

        self.lotus_bin = lotus_bin or os.environ.get('LOTUS_BIN') or shutil.which('lotus') or 'lotus'
        self.lotus_path = Path(lotus_path or os.environ.get('LOTUS_PATH') or os.path.expanduser('~/.lotus'))

        # HTTP gateway fallback (lighthouse.storage / web3.storage / public).
        self.gateway = gateway or os.environ.get('FILECOIN_GATEWAY') or 'https://node.lighthouse.storage'
        self.gateway_token = gateway_token or os.environ.get('FILECOIN_GATEWAY_TOKEN')

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
            path TEXT,
            size INTEGER,
            owner TEXT,
            backend TEXT,
            deal_id TEXT,
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

    def put(self, path: str, owner: str = None, deal: bool = False, **kw) -> dict:
        """Upload a file. If lotus daemon is up and deal=True, attempt a real storage deal;
        otherwise route to the HTTP gateway (CID-only, pin-style)."""
        p = Path(os.path.expanduser(path))
        if not p.exists():
            raise FileNotFoundError(path)
        size = p.stat().st_size

        if deal and self._lotus_alive():
            cid, deal_id = self._lotus_put(p)
            backend = 'lotus'
        else:
            cid = self._gateway_put(p)
            deal_id = None
            backend = 'gateway'

        self._record(cid, str(p), size, owner, backend, deal_id)
        return {'cid': cid, 'size': size, 'backend': backend, 'deal_id': deal_id, 'owner': owner}

    def get(self, cid: str, out: str = None) -> dict:
        """Retrieve a CID. Returns local path on success."""
        out = Path(os.path.expanduser(out)) if out else (self.store / 'cache' / cid)
        out.parent.mkdir(parents=True, exist_ok=True)

        if self._lotus_alive():
            try:
                self._lotus_get(cid, out)
                return {'cid': cid, 'path': str(out), 'backend': 'lotus'}
            except Exception:
                pass

        self._gateway_get(cid, out)
        return {'cid': cid, 'path': str(out), 'backend': 'gateway'}

    def pin(self, cid: str, owner: str = None) -> dict:
        """Mark a CID as pinned. With lotus, initiates a storage deal; otherwise just records intent."""
        if self._lotus_alive():
            try:
                deal_id = self._lotus_pin(cid)
                self._record(cid, None, 0, owner, 'lotus', deal_id)
                return {'cid': cid, 'pinned': True, 'deal_id': deal_id}
            except Exception as e:
                return {'cid': cid, 'pinned': False, 'error': str(e)}
        self._record(cid, None, 0, owner, 'gateway', None)
        return {'cid': cid, 'pinned': True, 'backend': 'gateway'}

    def list(self, owner: str = None, limit: int = 100) -> list:
        conn = self._db()
        if owner:
            rows = conn.execute(
                'SELECT cid,path,size,owner,backend,deal_id,timestamp,meta FROM objects '
                'WHERE owner=? ORDER BY timestamp DESC LIMIT ?', (owner, int(limit))
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT cid,path,size,owner,backend,deal_id,timestamp,meta FROM objects '
                'ORDER BY timestamp DESC LIMIT ?', (int(limit),)
            ).fetchall()
        conn.close()
        return [dict(zip(['cid', 'path', 'size', 'owner', 'backend', 'deal_id', 'timestamp', 'meta'], r)) for r in rows]

    def rm(self, cid: str) -> dict:
        conn = self._db()
        conn.execute('DELETE FROM objects WHERE cid=?', (cid,))
        conn.commit()
        conn.close()
        return {'cid': cid, 'removed': True}

    # ── Daemon ────────────────────────────────────────────────────

    def start_node(self, network: str = 'mainnet') -> dict:
        """Start lotus daemon (requires lotus installed)."""
        if self._lotus_alive():
            return {'running': True, 'pid': self._pid()}
        if not shutil.which(self.lotus_bin):
            return {'running': False, 'error': f'{self.lotus_bin} not installed (install lotus binary or set LOTUS_BIN)'}

        env = os.environ.copy()
        env['LOTUS_PATH'] = str(self.lotus_path)
        logfile = self.store / 'lotus.log'
        proc = subprocess.Popen(
            [self.lotus_bin, 'daemon'],
            env=env,
            stdout=open(logfile, 'a'),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.pidfile.write_text(str(proc.pid))
        time.sleep(2)
        return {'running': self._lotus_alive(), 'pid': proc.pid, 'log': str(logfile)}

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
        alive = self._lotus_alive()
        out = {'alive': alive, 'pid': self._pid(), 'lotus_bin': self.lotus_bin, 'lotus_path': str(self.lotus_path)}
        if alive:
            try:
                r = self._lotus(['sync', 'status'], timeout=5)
                out['sync'] = r.stdout.strip().splitlines()[:3]
            except Exception as e:
                out['sync_error'] = str(e)
        return out

    def status(self) -> dict:
        conn = self._db()
        n = conn.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
        conn.close()
        return {
            'name': 'filecoin',
            'objects': n,
            'store': str(self.store),
            'daemon': self.node_status(),
            'gateway': self.gateway,
            'gateway_token': bool(self.gateway_token),
        }

    def deals(self, limit: int = 50) -> list:
        if not self._lotus_alive():
            return []
        try:
            r = self._lotus(['client', 'list-deals', '--show-failed'], timeout=10)
            lines = r.stdout.strip().splitlines()
            return lines[:limit]
        except Exception as e:
            return [{'error': str(e)}]

    def wallet(self) -> dict:
        if not self._lotus_alive():
            return {'available': False, 'note': 'lotus daemon not running'}
        try:
            r = self._lotus(['wallet', 'list'], timeout=5)
            addrs = [line.split()[0] for line in r.stdout.strip().splitlines()[1:] if line.strip()]
            return {'available': True, 'addresses': addrs}
        except Exception as e:
            return {'available': False, 'error': str(e)}

    def balance(self, address: str = None) -> dict:
        if not self._lotus_alive():
            return {'available': False}
        try:
            args = ['wallet', 'balance']
            if address:
                args.append(address)
            r = self._lotus(args, timeout=5)
            return {'available': True, 'balance': r.stdout.strip()}
        except Exception as e:
            return {'available': False, 'error': str(e)}

    def install(self) -> dict:
        """Print install instructions — we won't run apt/brew unsupervised."""
        return {
            'macos': 'brew install filecoin (or https://lotus.filecoin.io/lotus/install/macos/)',
            'linux': 'https://lotus.filecoin.io/lotus/install/linux/',
            'env': 'export LOTUS_PATH=~/.lotus; export FILECOIN_GATEWAY_TOKEN=...',
        }

    # ── Lotus subprocess helpers ──────────────────────────────────

    def _lotus(self, args, timeout=30):
        env = os.environ.copy()
        env['LOTUS_PATH'] = str(self.lotus_path)
        return subprocess.run(
            [self.lotus_bin, *args],
            env=env, capture_output=True, text=True, timeout=timeout, check=True,
        )

    def _lotus_alive(self) -> bool:
        pid = self._pid()
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def _pid(self) -> Optional[int]:
        if not self.pidfile.exists():
            return None
        try:
            return int(self.pidfile.read_text().strip())
        except Exception:
            return None

    def _lotus_put(self, path: Path) -> tuple:
        r = self._lotus(['client', 'import', str(path)], timeout=120)
        # Output: "Import 1, Root <cid>"
        for tok in r.stdout.split():
            if tok.startswith('baf') or tok.startswith('Qm'):
                cid = tok
                break
        else:
            raise RuntimeError(f'unparseable lotus import output: {r.stdout!r}')
        # Try a deal (best-effort; will only work if miners + funds are configured)
        deal_id = None
        try:
            r2 = self._lotus(['client', 'deal', cid, '--', 'auto'], timeout=60)
            deal_id = r2.stdout.strip().splitlines()[-1]
        except Exception:
            pass
        return cid, deal_id

    def _lotus_get(self, cid: str, out: Path):
        self._lotus(['client', 'retrieve', cid, str(out)], timeout=300)

    def _lotus_pin(self, cid: str) -> str:
        r = self._lotus(['client', 'deal', cid, '--', 'auto'], timeout=60)
        return r.stdout.strip().splitlines()[-1]

    # ── HTTP gateway (fallback) ───────────────────────────────────

    def _gateway_put(self, path: Path) -> str:
        url = f"{self.gateway.rstrip('/')}/api/v0/add"
        headers = {}
        if self.gateway_token:
            headers['Authorization'] = f'Bearer {self.gateway_token}'
        with open(path, 'rb') as f:
            resp = requests.post(url, headers=headers, files={'file': (path.name, f)}, timeout=120)
        if resp.status_code >= 400:
            raise RuntimeError(f'gateway error {resp.status_code}: {resp.text[:200]}')
        try:
            data = resp.json()
        except Exception:
            # IPFS adds newline-delimited JSON
            data = json.loads(resp.text.strip().splitlines()[-1])
        cid = data.get('Hash') or data.get('cid') or data.get('CID')
        if not cid:
            raise RuntimeError(f'no cid in gateway response: {data}')
        return cid

    def _gateway_get(self, cid: str, out: Path):
        # Try a few gateways
        candidates = [
            f"{self.gateway.rstrip('/')}/ipfs/{cid}",
            f'https://gateway.lighthouse.storage/ipfs/{cid}',
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
                    return
                last = f'{url} -> {r.status_code}'
            except Exception as e:
                last = f'{url} -> {e}'
        raise RuntimeError(f'all gateways failed (last: {last})')

    # ── DB helpers ────────────────────────────────────────────────

    def _record(self, cid, path, size, owner, backend, deal_id, meta=None):
        ts = int(time.time())
        conn = self._db()
        conn.execute(
            'INSERT OR REPLACE INTO objects (cid,path,size,owner,backend,deal_id,timestamp,meta) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (cid, path, size, owner, backend, deal_id, ts, json.dumps(meta) if meta else None),
        )
        conn.commit()
        conn.close()
