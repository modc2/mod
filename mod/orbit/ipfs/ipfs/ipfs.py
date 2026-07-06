import requests
import os
import json
import subprocess
import platform
import sys
from typing import Optional, Dict, Any, List
from pathlib import Path
import time
import threading
import mod as m

class  IpfsClient:

    prefix = 'ipfs'
    endpoints = [
        'put', 'get', 'pins', 'pin_add', 'pin_rm',
        'peers', 'connect', 'disconnect', 'swarm_addrs', 'bootstrap',
        'sync', 'rm', 'rm_all', 'owner', 'set_owner', 'is_owner',
        'grants', 'grant', 'revoke', 'quota', 'pin_for', 'unpin_for',
        'balance', 'bytes_size',
        'propose_swap', 'accept_swap', 'cancel_swap', 'swaps',
        'id', 'version', 'node_status',
    ]
    """Simple IPFS client using requests library only."""
    node_name = 'ipfs.node'
    # Off-chain owner state — mirrors the claude / store modules.
    _owner_dir = os.path.expanduser('~/.mod/ipfs')
    _owner_file = os.path.expanduser('~/.mod/ipfs/owner.json')
    # Barter ledger: per-peer byte grants ("storage I've granted to them on
    # MY node") + the CIDs I'm holding on their behalf, deducted from quota.
    _grants_file = os.path.expanduser('~/.mod/ipfs/grants.json')
    # Pending 1-for-1 swap proposals: peer_id → {bytes, dir, ts}
    _swaps_file = os.path.expanduser('~/.mod/ipfs/swaps.json')
    connected = False
    _health_thread = None
    _stop_health = False
    _daemon_proc = None
    _pidfile = os.path.expanduser('~/.ipfs/daemon.pid')
    _timeout = 10
    _shared_url = None  # class-level connection cache

    def __init__(self, url: str = None, autostart: bool = True):
        cls = type(self)
        if not url and cls._shared_url:
            self.url = cls._shared_url
            self.connected = True
            return
        if autostart:
            self.ensure_ipfs_running()
        self.set_conn(url)
        cls._shared_url = self.url

    def _post(self, path: str, **kwargs):
        """Make a POST request to the IPFS API with timeout."""
        kwargs.setdefault('timeout', self._timeout)
        return requests.post(f"{self.url}{path}", **kwargs)

    def _post_stream_json(self, path: str, **kwargs):
        """POST to IPFS streaming endpoint, read first JSON line and close."""
        kwargs.setdefault('timeout', self._timeout)
        kwargs['stream'] = True
        resp = requests.post(f"{self.url}{path}", **kwargs)
        resp.raise_for_status()
        # Read first line (IPFS streams newline-delimited JSON)
        for line in resp.iter_lines():
            if line:
                resp.close()
                return json.loads(line)
        resp.close()
        raise ValueError("Empty response from IPFS")

    def _background_connect(self, url: str = None):
        """Try to establish the initial connection in the background."""
        try:
            self.set_conn(url)
            id = self.id()
            self.connected = True
            print(f"[ipfs] connected {id}")
        except Exception as e:
            self.connected = False
            print(f"[ipfs] background connect failed: {e}")

    def _start_health_check(self, interval: int = 30):
        """Start a daemon thread that periodically checks IPFS connectivity."""
        if self._health_thread and self._health_thread.is_alive():
            return
        self._stop_health = False
        self._health_thread = threading.Thread(
            target=self._health_loop, args=(interval,), daemon=True
        )
        self._health_thread.start()

    def _health_loop(self, interval: int):
        """Periodically ping the IPFS node; reconnect if down."""
        while not self._stop_health:
            try:
                self.id()
                if not self.connected:
                    print("[ipfs] reconnected")
                self.connected = True
            except Exception:
                if self.connected:
                    print("[ipfs] connection lost, will retry...")
                self.connected = False
                try:
                    self.set_conn(None)
                    self.id()
                    self.connected = True
                    print("[ipfs] reconnected")
                except Exception:
                    pass
            time.sleep(interval)

    def stop_health_check(self):
        """Stop the background health-check thread."""
        self._stop_health = True

    def _is_api_up(self, host='127.0.0.1', timeout=2):
        """Check if the IPFS API is responding."""
        try:
            r = requests.post(f'http://{host}:5001/api/v0/id', timeout=timeout)
            r.raise_for_status()
            return True
        except Exception:
            return False

    def ensure_ipfs_running(self):
        """Ensure that the IPFS daemon is running, start it if not."""
        if self._is_api_up():
            return True
        print("[ipfs] node not running, starting daemon...")
        if self.start_node():
            for _ in range(10):
                time.sleep(1)
                if self._is_api_up():
                    print("[ipfs] node is ready")
                    return True
            print("[ipfs] daemon started but API not responding yet")
            return False
        print("[ipfs] failed to start daemon")
        return False

    def set_conn(self, url: str = None, host_options=None, timeout=3):
        if host_options is None:
            host_options = ['127.0.0.1', '0.0.0.0', 'host.docker.internal']
        for host in host_options:
            _url = str(url or f"http://{host}:5001/api/v0")
            try:
                resp = requests.post(f"{_url}/id", timeout=timeout)
                resp.raise_for_status()
                self.url = _url
                self.connected = True
                print(f"[ipfs] connected to {self.url}")
                return self.url
            except Exception:
                pass
        # No daemon reachable. Don't crash the import — keep `connected=False`
        # so methods that don't need IPFS (grants/balance/swaps/owner) still
        # work. Methods that DO need it will raise when called.
        self.url = str(url or f"http://{host_options[0]}:5001/api/v0")
        self.connected = False
        print(f"[ipfs] no daemon reachable — running in offline mode ({self.url})")
        return self.url

    def add_file(self, file_path: str) -> Dict[str, Any]:
        """Add a single file to IPFS."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        with open(file_path, 'rb') as f:
            files = {'file': (os.path.basename(file_path), f)}
            return self._post_stream_json('/add', files=files)

    def put(self, data: Dict[str, Any] = None, pin=True) -> Dict[str, Any]:
        """Add a JSON object to IPFS and return its CID."""
        json_str = json.dumps(data)
        files = {'file': ('data.json', json_str)}
        # IPFS /add pins by default; pass pin=false to skip
        params = {} if pin else {'pin': 'false'}
        result = self._post_stream_json('/add', files=files, params=params)
        return result["Hash"]
    add = put

    def rm(self, cid: str) -> Dict[str, Any]:
        """Remove (unpin) content from IPFS by its hash."""
        try:
            self.pin_rm(cid)
        except Exception as e:
            print(f"Error unpinning {cid}: {e}")
        return {"Status": "Removed"}

    def get(self, cid: str) -> Dict[str, Any]:
        """Retrieve a JSON object from IPFS by its hash."""
        if not isinstance(cid, str):
            return cid
        if cid.startswith(self.prefix + '/'):
            cid = cid[len(self.prefix) + 1:]
        content = self.get_file(cid)
        return json.loads(content)

    def resolve_cid(self, ipfs_path: str) -> str:
        return ipfs_path.replace(self.prefix + '/', '')

    def get_file(self, cid: str) -> bytes:
        """Retrieve a file from IPFS by its hash."""
        cid = self.resolve_cid(cid)
        response = self._post('/cat', params={'arg': cid})
        response.raise_for_status()
        return response.content

    def cid(self, data: Dict[str, Any] = None) -> str:
        """Add data to IPFS and return its CID (without pinning)."""
        return self.add(data, pin=False)

    def cat(self, cid: str) -> bytes:
        """Retrieve content from IPFS by hash."""
        response = self._post('/cat', params={'arg': cid})
        response.raise_for_status()
        return response.content

    def pin_add(self, cid: str) -> Dict[str, Any]:
        """Pin content to local IPFS node."""
        response = self._post('/pin/add', params={'arg': cid}, timeout=60)
        response.raise_for_status()
        return response.json()

    def pin_rm(self, cid: str) -> Dict[str, Any]:
        """Unpin content from local IPFS node."""
        response = self._post('/pin/rm', params={'arg': cid}, timeout=30)
        response.raise_for_status()
        return response.json()

    def pinned(self, cid: str) -> bool:
        """Check if content is pinned on local IPFS node."""
        pins = self.pins()
        return cid in pins.get('Keys', {})

    def pins(self, cid: str = None, search: str = None,
             limit: int = 25, offset: int = 0) -> Dict[str, Any]:
        """List pinned content. Paginated + searchable so the UI can show
        a clean window over thousands of pins.

        Args:
            cid: lookup a single CID (returns it or {} if not pinned).
            search: substring filter on the CID.
            limit: page size (default 25).
            offset: starting index after sorting / filtering.

        Returns:
            { total, count, offset, limit, query, keys: { cid: {Type: 'recursive'}, ... } }
        """
        params = {}
        if cid:
            params['arg'] = cid
        response = self._post('/pin/ls', params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if cid:
            keys = data.get('Keys', {})
            return {cid: keys.get(cid)} if cid in keys else {}

        all_keys = data.get('Keys') or {}
        # Stable order = sort by CID so paging is deterministic.
        cids_sorted = sorted(all_keys.keys())
        q = (search or '').strip().lower()
        if q:
            cids_sorted = [c for c in cids_sorted if q in c.lower()]
        total = len(cids_sorted)
        try:
            offset = max(0, int(offset))
            limit = max(1, min(int(limit), 500))
        except (TypeError, ValueError):
            offset, limit = 0, 25
        page = cids_sorted[offset:offset + limit]
        return {
            'total': total,
            'offset': offset,
            'limit': limit,
            'count': len(page),
            'query': q or None,
            'keys': {c: all_keys[c] for c in page},
        }

    def _rm_all_pins(self) -> None:
        """Unpin all content from local IPFS node."""
        pins = self.pins()
        for cid in pins.get('Keys', {}).keys():
            print(f"Unpinning CID: {cid}")
            try:
                self.pin_rm(cid)
            except Exception as e:
                print(f"Error unpinning {cid}: {e}")

    # --- Node management (subprocess, no Docker/pm2) ---

    kubo_version = 'v0.40.1'

    def _read_pid(self):
        """Read the daemon PID from the pidfile."""
        try:
            with open(self._pidfile) as f:
                return int(f.read().strip())
        except Exception:
            return None

    def _write_pid(self, pid):
        os.makedirs(os.path.dirname(self._pidfile), exist_ok=True)
        with open(self._pidfile, 'w') as f:
            f.write(str(pid))

    def _is_pid_alive(self, pid):
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def start_node(self):
        """Install IPFS if needed and start ipfs daemon as a background process."""
        # Check if already running
        pid = self._read_pid()
        if self._is_pid_alive(pid):
            print(f"[ipfs] daemon already running (pid {pid})")
            return True
        # Guard: in slim containers we may have no `ipfs` binary and no
        # network to install one. Bail out instead of crashing so `m serve`
        # (which constructs IpfsClient on import) can still start and surface
        # methods like grants/balance that don't require a live daemon.
        import shutil as _sh
        if not _sh.which('ipfs'):
            try:
                self.install()
            except Exception as e:
                print(f"[ipfs] install skipped: {e}")
            if not _sh.which('ipfs'):
                print("[ipfs] no `ipfs` binary on PATH — node not started")
                return False
        ipfs_path = os.path.expanduser('~/.ipfs')
        if not os.path.exists(ipfs_path):
            subprocess.run(['ipfs', 'init'], capture_output=True)
        # Start daemon as detached subprocess
        log = open(os.path.expanduser('~/.ipfs/daemon.log'), 'a')
        env = os.environ.copy()
        env['IPFS_PATH'] = ipfs_path
        proc = subprocess.Popen(
            ['ipfs', 'daemon'],
            stdout=log, stderr=log,
            env=env,
            start_new_session=True,
        )
        self._daemon_proc = proc
        self._write_pid(proc.pid)
        print(f"[ipfs] daemon started (pid {proc.pid})")
        return True

    def stop_node(self):
        """Stop the IPFS daemon."""
        pid = self._read_pid()
        if pid and self._is_pid_alive(pid):
            import signal
            os.kill(pid, signal.SIGTERM)
            print(f"[ipfs] daemon stopped (pid {pid})")
            try:
                os.remove(self._pidfile)
            except OSError:
                pass
            return True
        # Fallback: kill any ipfs daemon
        subprocess.run(['pkill', '-f', 'ipfs daemon'], capture_output=True)
        print("[ipfs] daemon stopped (pkill)")
        return True

    def restart_node(self):
        """Restart the IPFS daemon."""
        self.stop_node()
        time.sleep(1)
        return self.start_node()

    def node_status(self):
        """Check if the IPFS daemon is running."""
        pid = self._read_pid()
        if self._is_pid_alive(pid):
            return 'online'
        return 'offline'

    def install(self):
        """Install IPFS (Kubo) if not already installed."""
        if self.ipfs_installed():
            return True
        print("Installing IPFS (Kubo)...")
        system = platform.system().lower()
        machine = platform.machine().lower()
        arch_map = {
            'x86_64': 'amd64', 'amd64': 'amd64',
            'aarch64': 'arm64', 'arm64': 'arm64',
        }
        arch = arch_map.get(machine, machine)
        try:
            if system == 'darwin':
                self._install_macos(arch)
            elif system == 'linux':
                self._install_linux(arch)
            elif system == 'windows':
                self._install_windows(arch)
            else:
                print(f"Unsupported platform: {system}")
                return False
            subprocess.run(['ipfs', 'init'], capture_output=True)
            print("IPFS installed and initialized.")
            return True
        except Exception as e:
            print(f"Failed to install IPFS: {e}")
            return False

    def _install_macos(self, arch):
        tarball = f'kubo_{self.kubo_version}_darwin-{arch}.tar.gz'
        url = f'https://dist.ipfs.tech/kubo/{self.kubo_version}/{tarball}'
        tmp = '/tmp/kubo_install'
        os.makedirs(tmp, exist_ok=True)
        subprocess.run(['curl', '-L', '-o', f'{tmp}/{tarball}', url], check=True)
        subprocess.run(['tar', '-xzf', f'{tmp}/{tarball}', '-C', tmp], check=True)
        subprocess.run(['sudo', 'bash', f'{tmp}/kubo/install.sh'], check=True)
        subprocess.run(['rm', '-rf', tmp])

    def _install_linux(self, arch):
        tarball = f'kubo_{self.kubo_version}_linux-{arch}.tar.gz'
        url = f'https://dist.ipfs.tech/kubo/{self.kubo_version}/{tarball}'
        tmp = '/tmp/kubo_install'
        os.makedirs(tmp, exist_ok=True)
        subprocess.run(['curl', '-L', '-o', f'{tmp}/{tarball}', url], check=True)
        subprocess.run(['tar', '-xzf', f'{tmp}/{tarball}', '-C', tmp], check=True)
        subprocess.run(['sudo', 'bash', f'{tmp}/kubo/install.sh'], check=True)
        subprocess.run(['rm', '-rf', tmp])

    def _install_windows(self, arch):
        zipname = f'kubo_{self.kubo_version}_windows-{arch}.zip'
        url = f'https://dist.ipfs.tech/kubo/{self.kubo_version}/{zipname}'
        tmp = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'kubo_install')
        os.makedirs(tmp, exist_ok=True)
        zip_path = os.path.join(tmp, zipname)
        subprocess.run(['powershell', '-Command', f"Invoke-WebRequest -Uri '{url}' -OutFile '{zip_path}'"], check=True)
        subprocess.run(['powershell', '-Command', f"Expand-Archive -Path '{zip_path}' -DestinationPath '{tmp}' -Force"], check=True)
        install_dir = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ipfs')
        os.makedirs(install_dir, exist_ok=True)
        src = os.path.join(tmp, 'kubo', 'ipfs.exe')
        dst = os.path.join(install_dir, 'ipfs.exe')
        subprocess.run(['copy', src, dst], shell=True, check=True)
        current_path = os.environ.get('PATH', '')
        if install_dir not in current_path:
            subprocess.run(['setx', 'PATH', f'{current_path};{install_dir}'], check=True)
            os.environ['PATH'] = f'{current_path};{install_dir}'
        subprocess.run(['rmdir', '/s', '/q', tmp], shell=True)

    def ipfs_installed(self):
        """Check if the ipfs binary is available."""
        try:
            subprocess.run(['ipfs', '--version'], capture_output=True, check=True)
            return True
        except Exception:
            return False

    def iscid(self, text: str = 'fsd') -> bool:
        """Check if the text is an IPFS hash."""
        return isinstance(text, str) and (text.startswith('Qm') and len(text) == 46)

    def id(self) -> Dict[str, Any]:
        """Get IPFS node identity information."""
        response = self._post('/id')
        response.raise_for_status()
        return response.json()

    def version(self) -> Dict[str, Any]:
        """Get IPFS version information."""
        response = self._post('/version')
        response.raise_for_status()
        return response.json()

    def test(self) -> bool:
        """Test connection to IPFS node by adding and retrieving test data."""
        test_obj = {"test_key": "test_value"}
        print("Testing IPFS data connection...", test_obj)
        cid = self.add(test_obj)
        retrieved_obj = self.get(cid)
        return retrieved_obj == test_obj

    def __str__(self):
        return f"IpfsClient(url={self.url})"

    def valid_cid(self, cid: str) -> bool:
        """Validate if a string is a valid IPFS CID."""
        try:
            self.get(cid)
            return True
        except Exception:
            return False

    # ── Swarm / peer discovery + sync ─────────────────────────────────

    def peers(self) -> Dict[str, Any]:
        """List currently connected IPFS peers (swarm peers)."""
        response = self._post('/swarm/peers', params={'verbose': 'true', 'latency': 'true'})
        response.raise_for_status()
        data = response.json()
        peers = data.get('Peers') or []
        return {
            'count': len(peers),
            'peers': [
                {
                    'peer': p.get('Peer'),
                    'addr': p.get('Addr'),
                    'latency': p.get('Latency'),
                    'direction': p.get('Direction'),
                    'muxer': p.get('Muxer'),
                }
                for p in peers
            ],
        }

    def swarm_addrs(self) -> Dict[str, Any]:
        """Show our own listen addresses (multiaddrs other nodes can dial)."""
        node_id = self.id()
        local = self._post('/swarm/addrs/local')
        local.raise_for_status()
        return {
            'id': node_id.get('ID'),
            'agent_version': node_id.get('AgentVersion'),
            'addresses': local.json().get('Strings', []),
        }

    def connect(self, multiaddr: str) -> Dict[str, Any]:
        """Dial another node by multiaddr.
        Example: /ip4/1.2.3.4/tcp/4001/p2p/12D3KooW..."""
        response = self._post('/swarm/connect', params={'arg': multiaddr}, timeout=30)
        response.raise_for_status()
        return response.json()

    def disconnect(self, multiaddr: str) -> Dict[str, Any]:
        """Drop a swarm connection by multiaddr."""
        response = self._post('/swarm/disconnect', params={'arg': multiaddr}, timeout=15)
        response.raise_for_status()
        return response.json()

    def bootstrap(self) -> Dict[str, Any]:
        """List configured bootstrap peers (the nodes we auto-dial on start)."""
        response = self._post('/bootstrap/list')
        response.raise_for_status()
        return response.json()

    def sync(self, peer: str = None, cids: List[str] = None) -> Dict[str, Any]:
        """Sync: connect to a peer (or all bootstrap peers) and refetch the
        given CIDs (or all currently pinned CIDs) so they're cached locally.

        Args:
            peer: multiaddr to dial first (optional).
            cids: list of CIDs to refresh; default = every pin we have.
        """
        result = {'connected': [], 'refreshed': [], 'errors': []}

        # Step 1: ensure we have a swarm path.
        if peer:
            try:
                self.connect(peer)
                result['connected'].append(peer)
            except Exception as e:
                result['errors'].append(f'connect {peer}: {e}')

        # Step 2: choose CIDs to refresh.
        target_cids = cids
        if not target_cids:
            try:
                pins = self.pins()
                target_cids = list((pins.get('Keys') or {}).keys())
            except Exception as e:
                result['errors'].append(f'list pins: {e}')
                target_cids = []

        # Step 3: re-fetch each CID via /refs to pull missing blocks from peers.
        for cid in target_cids:
            try:
                r = self._post('/refs', params={'arg': cid, 'recursive': 'true'}, timeout=30)
                if r.status_code == 200:
                    result['refreshed'].append(cid)
                else:
                    result['errors'].append(f'{cid}: HTTP {r.status_code}')
            except Exception as e:
                result['errors'].append(f'{cid}: {e}')

        return result

    # ── Owner-gated deletion ─────────────────────────────────────────

    def _addr_lc(self, address: str) -> str:
        return (address or '').strip().lower()

    def owner(self) -> Optional[str]:
        """Return the current owner address (lower-cased) or None if unset."""
        try:
            with open(self._owner_file) as f:
                data = json.load(f)
            return self._addr_lc(data.get('owner') or '')
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def is_owner(self, address: str) -> bool:
        """Check if the given address is the registered owner. If no owner is
        set yet, anyone is considered owner (bootstrap)."""
        cur = self.owner()
        if not cur:
            return True
        return self._addr_lc(address) == cur

    def set_owner(self, address: str, caller: str = None) -> Dict[str, Any]:
        """Claim ownership (only once) or transfer it (current owner only).

        Args:
            address: new owner.
            caller: address making the call; required once an owner is set.
        """
        addr = self._addr_lc(address)
        if not addr.startswith('0x') or len(addr) != 42:
            return {'ok': False, 'error': 'address must be 0x-prefixed 42 chars'}
        cur = self.owner()
        if cur and self._addr_lc(caller) != cur:
            return {'ok': False, 'error': 'only current owner can transfer'}
        os.makedirs(self._owner_dir, exist_ok=True)
        with open(self._owner_file, 'w') as f:
            json.dump({'owner': addr}, f, indent=2)
        return {'ok': True, 'owner': addr, 'previous': cur}

    def rm(self, cid: str, caller: str = None) -> Dict[str, Any]:
        """Owner-gated unpin. If an owner is set in ~/.mod/ipfs/owner.json,
        only that address may delete pins. When no owner is set, anyone can
        (bootstrap mode)."""
        if not self.is_owner(caller):
            return {
                'ok': False,
                'error': f'forbidden — only the owner ({self.owner()}) can delete pins',
                'cid': cid,
            }
        try:
            self.pin_rm(cid)
            return {'ok': True, 'cid': cid, 'unpinned': True, 'caller': self._addr_lc(caller) or None}
        except Exception as e:
            return {'ok': False, 'cid': cid, 'error': str(e)}

    def rm_all(self, caller: str = None) -> Dict[str, Any]:
        """Owner-gated unpin-everything. Same gating as rm()."""
        if not self.is_owner(caller):
            return {'ok': False, 'error': f'forbidden — only owner ({self.owner()}) may rm_all'}
        removed = []
        errors = []
        for cid in list((self.pins().get('Keys') or {}).keys()):
            try:
                self.pin_rm(cid)
                removed.append(cid)
            except Exception as e:
                errors.append({'cid': cid, 'error': str(e)})
        return {'ok': True, 'removed': removed, 'errors': errors}

    # ── Barter ledger: data is the currency ────────────────────────
    #
    # Each peer (identified by their libp2p peer-id) can be granted N bytes
    # of pin storage on this node. The owner manages grants; anyone who can
    # prove they're a granted peer (signed multiaddr — out of scope here,
    # trust-on-first-use for now) can pin CIDs against their quota.
    #
    # Layout (~/.mod/ipfs/grants.json):
    #   {
    #     "12D3KooW...": {
    #        "bytes":       104857600,           # quota I grant them
    #        "received":    52428800,            # bytes they granted me  (for net balance)
    #        "label":       "alice",
    #        "granted_at":  1717000000,
    #        "pins":        {"QmCid...": 8192}   # cid -> bytes deducted from quota
    #     }
    #   }

    def _load_grants(self) -> Dict[str, Any]:
        try:
            with open(self._grants_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_grants(self, ledger: Dict[str, Any]) -> None:
        os.makedirs(self._owner_dir, exist_ok=True)
        with open(self._grants_file, 'w') as f:
            json.dump(ledger, f, indent=2, sort_keys=True)

    def _norm_peer(self, peer_id: str) -> str:
        """Normalize a peer identifier — strip a /p2p/ multiaddr down to just
        the libp2p ID so grants work whether you paste the addr or the id."""
        if not peer_id:
            return ''
        s = peer_id.strip()
        if '/p2p/' in s:
            s = s.rsplit('/p2p/', 1)[-1]
        return s

    def bytes_size(self, cid: str) -> int:
        """Deduplicated size on disk for a CID (via /object/stat)."""
        r = self._post('/object/stat', params={'arg': cid}, timeout=15)
        r.raise_for_status()
        d = r.json()
        return int(d.get('CumulativeSize') or d.get('BlockSize') or 0)

    def grants(self) -> Dict[str, Any]:
        """Return the full grant ledger — what I've granted, what they've
        granted me, and how much of each quota is in use."""
        ledger = self._load_grants()
        out = {}
        for peer, rec in ledger.items():
            quota = int(rec.get('bytes', 0))
            used = sum(int(v) for v in (rec.get('pins') or {}).values())
            received = int(rec.get('received', 0))
            out[peer] = {
                'granted_to_them': quota,
                'received_from_them': received,
                'used': used,
                'remaining': max(0, quota - used),
                'net_owed_to_me': received - used,   # negative = I owe them storage
                'pins': list((rec.get('pins') or {}).keys()),
                'label': rec.get('label'),
                'granted_at': rec.get('granted_at'),
            }
        return out

    def grant(self, peer_id: str, bytes_amount: int, label: str = None,
              received: int = None, caller: str = None) -> Dict[str, Any]:
        """Owner-gated. Allocate `bytes_amount` of pin storage on this node to
        `peer_id`. Optionally record `received` (bytes THEY granted you, for
        net-balance tracking — this is how the barter price gets set)."""
        if not self.is_owner(caller):
            return {'ok': False, 'error': f'forbidden — only owner ({self.owner()}) may grant'}
        peer = self._norm_peer(peer_id)
        if not peer:
            return {'ok': False, 'error': 'peer_id is required'}
        try:
            bytes_amount = int(bytes_amount)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'bytes_amount must be an integer'}
        if bytes_amount < 0:
            return {'ok': False, 'error': 'bytes_amount must be >= 0'}

        ledger = self._load_grants()
        rec = ledger.get(peer) or {'pins': {}}
        rec['bytes'] = bytes_amount
        if label is not None:
            rec['label'] = label
        if received is not None:
            try:
                rec['received'] = max(0, int(received))
            except (TypeError, ValueError):
                pass
        rec.setdefault('pins', {})
        rec.setdefault('granted_at', int(time.time()))
        ledger[peer] = rec
        self._save_grants(ledger)
        return {'ok': True, 'peer': peer, 'bytes': bytes_amount,
                'received': rec.get('received', 0), 'label': rec.get('label')}

    def revoke(self, peer_id: str, unpin: bool = False, caller: str = None) -> Dict[str, Any]:
        """Owner-gated. Drop a peer's grant. If `unpin=True` also unpin every
        CID we were holding on their behalf."""
        if not self.is_owner(caller):
            return {'ok': False, 'error': f'forbidden — only owner ({self.owner()}) may revoke'}
        peer = self._norm_peer(peer_id)
        ledger = self._load_grants()
        rec = ledger.pop(peer, None)
        if rec is None:
            return {'ok': False, 'error': f'no grant for {peer}'}
        unpinned = []
        if unpin:
            for cid in list((rec.get('pins') or {}).keys()):
                try:
                    self.pin_rm(cid)
                    unpinned.append(cid)
                except Exception:
                    pass
        self._save_grants(ledger)
        return {'ok': True, 'peer': peer, 'released_bytes': int(rec.get('bytes', 0)),
                'unpinned': unpinned}

    def quota(self, peer_id: str) -> Dict[str, Any]:
        """How much of `peer_id`'s grant is used vs available."""
        peer = self._norm_peer(peer_id)
        ledger = self._load_grants()
        rec = ledger.get(peer)
        if not rec:
            return {'peer': peer, 'granted': False}
        quota = int(rec.get('bytes', 0))
        used = sum(int(v) for v in (rec.get('pins') or {}).values())
        return {
            'peer': peer,
            'granted': True,
            'limit_bytes': quota,
            'used_bytes': used,
            'remaining_bytes': max(0, quota - used),
            'pins': list((rec.get('pins') or {}).keys()),
        }

    def balance(self, peer_id: str = None) -> Dict[str, Any]:
        """Per-peer (or global) barter balance.

        positive net = peer owes me storage / I'm holding less for them than they hold for me;
        negative net = I owe them storage."""
        ledger = self._load_grants()
        if peer_id:
            peer = self._norm_peer(peer_id)
            rec = ledger.get(peer) or {}
            quota = int(rec.get('bytes', 0))
            used = sum(int(v) for v in (rec.get('pins') or {}).values())
            received = int(rec.get('received', 0))
            return {
                'peer': peer,
                'i_grant_them': quota,
                'they_grant_me': received,
                'they_use_of_mine': used,
                'net_owed_to_me': received - used,
            }
        totals = {'granted_out': 0, 'received': 0, 'used_by_others': 0}
        per_peer = []
        for peer, rec in ledger.items():
            quota = int(rec.get('bytes', 0))
            used = sum(int(v) for v in (rec.get('pins') or {}).values())
            received = int(rec.get('received', 0))
            totals['granted_out'] += quota
            totals['received'] += received
            totals['used_by_others'] += used
            per_peer.append({
                'peer': peer, 'i_grant_them': quota, 'they_grant_me': received,
                'they_use_of_mine': used, 'net_owed_to_me': received - used,
            })
        totals['net_owed_to_me'] = totals['received'] - totals['used_by_others']
        return {'totals': totals, 'peers': per_peer}

    def pin_for(self, peer_id: str, cid: str) -> Dict[str, Any]:
        """Pin a CID against a peer's quota. Rejects if the CID's size would
        push them over. Anyone may call this (it's just a pin charged to the
        named peer's grant) — the gate is the quota itself."""
        peer = self._norm_peer(peer_id)
        ledger = self._load_grants()
        rec = ledger.get(peer)
        if not rec:
            return {'ok': False, 'error': f'no grant for {peer}; owner must grant first'}

        quota = int(rec.get('bytes', 0))
        pins = rec.setdefault('pins', {})
        used = sum(int(v) for v in pins.values())

        # If we already track this cid for this peer, treat as no-op success.
        if cid in pins:
            return {'ok': True, 'peer': peer, 'cid': cid, 'bytes': pins[cid],
                    'used': used, 'remaining': max(0, quota - used), 'already': True}

        # Resolve size *before* pinning (object/stat works on cached refs too,
        # but if we can't size it we still try a small probe via pin then back out).
        try:
            size = self.bytes_size(cid)
        except Exception as e:
            return {'ok': False, 'error': f'cannot size {cid}: {e}'}

        if size <= 0:
            return {'ok': False, 'error': f'zero-sized CID {cid}'}
        if used + size > quota:
            return {
                'ok': False, 'peer': peer, 'cid': cid,
                'error': f'quota exceeded: needs {size} bytes; {quota - used} remaining',
                'limit_bytes': quota, 'used_bytes': used,
            }

        try:
            self.pin_add(cid)
        except Exception as e:
            return {'ok': False, 'error': f'pin failed: {e}'}

        pins[cid] = size
        ledger[peer] = rec
        self._save_grants(ledger)
        return {'ok': True, 'peer': peer, 'cid': cid, 'bytes': size,
                'used': used + size, 'remaining': max(0, quota - used - size)}

    def unpin_for(self, peer_id: str, cid: str, caller: str = None) -> Dict[str, Any]:
        """Drop a CID we were holding on behalf of `peer_id` and refund the
        quota. The named peer OR the owner may call this."""
        peer = self._norm_peer(peer_id)
        # Anyone holding the peer-id can release (or the owner can sweep).
        # That keeps the friction low and stays consistent with "they own
        # what they pinned for themselves."
        ledger = self._load_grants()
        rec = ledger.get(peer)
        if not rec:
            return {'ok': False, 'error': f'no grant for {peer}'}
        pins = rec.get('pins') or {}
        if cid not in pins:
            if not self.is_owner(caller):
                return {'ok': False, 'error': f'{cid} not held for {peer}'}
        size = pins.pop(cid, 0)
        try:
            self.pin_rm(cid)
        except Exception:
            pass  # OK if it was already gone; ledger refund still wanted
        self._save_grants(ledger)
        return {'ok': True, 'peer': peer, 'cid': cid, 'refunded_bytes': int(size)}

    # ── 1-for-1 swaps: barter is symmetric ─────────────────────────
    #
    # A "swap" is a mutual-grant agreement: I commit N bytes of my disk to
    # peer X if they commit N bytes back. Two states:
    #   - proposed_out: I made the offer, waiting on peer to accept
    #   - proposed_in:  peer made me an offer, I haven't accepted yet
    # Acceptance triggers a mutual grant() of N bytes on each side.
    #
    # Layout (~/.mod/ipfs/swaps.json):
    #   {
    #     "12D3KooW...": {"bytes": N, "dir": "out"|"in", "ts": ...}
    #   }

    def _load_swaps(self) -> Dict[str, Any]:
        try:
            with open(self._swaps_file) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_swaps(self, data: Dict[str, Any]) -> None:
        os.makedirs(self._owner_dir, exist_ok=True)
        with open(self._swaps_file, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def swaps(self) -> Dict[str, Any]:
        """Return pending swap proposals split by direction."""
        data = self._load_swaps()
        outgoing, incoming = [], []
        for peer, rec in data.items():
            entry = {'peer': peer, 'bytes': int(rec.get('bytes', 0)),
                     'ts': rec.get('ts')}
            if rec.get('dir') == 'out':
                outgoing.append(entry)
            else:
                incoming.append(entry)
        return {
            'outgoing': sorted(outgoing, key=lambda r: r['ts'] or 0, reverse=True),
            'incoming': sorted(incoming, key=lambda r: r['ts'] or 0, reverse=True),
        }

    def propose_swap(self, peer_id: str, bytes_amount: int,
                     caller: str = None) -> Dict[str, Any]:
        """Owner-gated. Offer a 1-for-1 swap with `peer_id`: I'll host
        `bytes_amount` of theirs if they host the same of mine. The grant
        on my side activates only when they accept."""
        if not self.is_owner(caller):
            return {'ok': False, 'error': f'forbidden — only owner ({self.owner()}) may propose'}
        peer = self._norm_peer(peer_id)
        if not peer:
            return {'ok': False, 'error': 'peer_id is required'}
        try:
            n = int(bytes_amount)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'bytes_amount must be an integer'}
        if n <= 0:
            return {'ok': False, 'error': 'bytes_amount must be > 0'}
        data = self._load_swaps()
        data[peer] = {'bytes': n, 'dir': 'out', 'ts': int(time.time())}
        self._save_swaps(data)
        return {'ok': True, 'peer': peer, 'bytes': n,
                'state': 'awaiting_their_acceptance'}

    def accept_swap(self, peer_id: str, bytes_amount: int = None,
                    caller: str = None) -> Dict[str, Any]:
        """Owner-gated. Accept an incoming offer (or symmetrically commit
        to an outgoing one a counterparty has agreed to). Triggers mutual
        grant: their quota on my node = N, my recorded receipt from them = N."""
        if not self.is_owner(caller):
            return {'ok': False, 'error': f'forbidden — only owner ({self.owner()}) may accept'}
        peer = self._norm_peer(peer_id)
        data = self._load_swaps()
        proposal = data.get(peer)
        if not proposal and bytes_amount is None:
            return {'ok': False, 'error': f'no pending swap with {peer} and no bytes_amount provided'}

        n = int(bytes_amount) if bytes_amount is not None else int(proposal.get('bytes', 0))
        if n <= 0:
            return {'ok': False, 'error': 'bytes_amount must be > 0'}

        # Mutual grant: I grant them N bytes AND record that they granted me N.
        grant_res = self.grant(peer_id=peer, bytes_amount=n, received=n, caller=caller)
        if not grant_res.get('ok'):
            return grant_res

        # Clear the pending proposal — swap is now active.
        data.pop(peer, None)
        self._save_swaps(data)
        return {'ok': True, 'peer': peer, 'bytes': n,
                'state': 'active', 'mutual_grant': True}

    def cancel_swap(self, peer_id: str, caller: str = None) -> Dict[str, Any]:
        """Owner-gated. Withdraw a pending proposal."""
        if not self.is_owner(caller):
            return {'ok': False, 'error': f'forbidden — only owner ({self.owner()}) may cancel'}
        peer = self._norm_peer(peer_id)
        data = self._load_swaps()
        rec = data.pop(peer, None)
        self._save_swaps(data)
        if not rec:
            return {'ok': False, 'error': f'no pending swap with {peer}'}
        return {'ok': True, 'peer': peer, 'cancelled': rec}
