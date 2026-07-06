"""Multi-chain module registry — Base (EVM), Solana, NEAR, and off-chain."""

import json
import os
import sys

_ONCHAIN_SRC = os.path.join(os.path.dirname(__file__), '..', 'onchain', 'src')

# Supported storage providers for data CIDs
STORAGE_PROVIDERS = ('ipfs', 'lighthouse', 'filecoin')


def parse_data_uri(uri: str) -> tuple:
    """Parse a prefixed data URI like 'ipfs/{cid}' into (provider, cid).

    Returns (None, uri) if no known prefix found.
    """
    for p in STORAGE_PROVIDERS:
        prefix = f'{p}/'
        if uri.startswith(prefix):
            return p, uri[len(prefix):]
    return None, uri


def make_data_uri(provider: str, cid: str) -> str:
    """Build a prefixed data URI like 'ipfs/{cid}'."""
    if provider not in STORAGE_PROVIDERS:
        raise ValueError(f'Unknown storage provider: {provider}. Use one of {STORAGE_PROVIDERS}')
    return f'{provider}/{cid}'


class Mod:
    description = """
    Multi-chain module registry (Base, Solana, NEAR, off-chain).
    Register, query, update, and manage modules across backends.
    Data is stored as JSON on IPFS/Lighthouse/Filecoin, referenced by prefixed CID.
    Auth-gated registration via oath module token verification.
    Usage: m.mod('registry')(name='mymod', data={'version': '1.0'}, storage='ipfs')
    """

    def __init__(self, backend='offchain', network='testnet', storage='ipfs',
                 auth=None, require_auth=False, **kw):
        """
        Args:
            backend: Registry backend ('offchain', 'evm', 'solana', 'near').
            network: Network name ('testnet', 'mainnet', 'devnet').
            storage: Default storage provider ('ipfs', 'lighthouse', 'filecoin').
            auth: Auth instance (oath.Auth) for token verification. If None, loaded lazily.
            require_auth: If True, register/update/remove require valid auth headers.
        """
        self.default_backend = backend
        self.network = network
        self.default_storage = storage
        self.require_auth = require_auth
        self._auth = auth
        self._backends = {}
        self._storage_clients = {}
        self._config = self._load_config()
        # Eagerly init the default backend
        self._get_backend(self.default_backend)

    def _load_config(self):
        config_path = os.path.join(os.path.dirname(__file__), 'config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return json.load(f)
        return {}

    def _get_backend(self, name=None):
        name = name or self.default_backend
        if name in self._backends:
            return self._backends[name]

        if name == 'offchain':
            from .registry.offchain import OffchainRegistry
            cfg = self._config.get('offchain', {})
            backend = OffchainRegistry(storage_path=cfg.get('storage_path'))

        elif name == 'evm':
            if _ONCHAIN_SRC not in sys.path:
                sys.path.insert(0, _ONCHAIN_SRC)
            from evm.evm import EVMRegistry
            cfg = self._config.get('evm', {}).get(self.network, {})
            backend = EVMRegistry(
                rpc_url=cfg.get('rpc'),
                chain_id=cfg.get('chain_id'),
                registry_address=cfg.get('registry'),
                network=self.network,
            )

        elif name == 'solana':
            if _ONCHAIN_SRC not in sys.path:
                sys.path.insert(0, _ONCHAIN_SRC)
            from solana.solana import SolanaRegistry
            cfg = self._config.get('solana', {}).get(self.network, {})
            backend = SolanaRegistry(
                rpc_url=cfg.get('rpc'),
                program_id=cfg.get('program_id'),
                network=self.network,
            )

        elif name == 'near':
            if _ONCHAIN_SRC not in sys.path:
                sys.path.insert(0, _ONCHAIN_SRC)
            from near.near import NearRegistry
            cfg = self._config.get('near', {}).get(self.network, {})
            backend = NearRegistry(
                rpc_url=cfg.get('rpc'),
                account=cfg.get('account'),
                network=self.network,
            )

        else:
            raise ValueError(f'Unknown backend: {name}')

        self._backends[name] = backend
        return backend

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _get_auth(self):
        """Lazy-load the oath Auth module."""
        if self._auth is not None:
            return self._auth
        try:
            import mod as m
            self._auth = m.mod('oath')()
        except Exception as e:
            raise RuntimeError(f'Cannot load oath auth module: {e}')
        return self._auth

    def _verify_auth(self, headers, data=None):
        """Verify auth headers. Raises on failure."""
        if not headers:
            raise PermissionError('Auth headers required for this operation')
        auth = self._get_auth()
        try:
            auth.verify(headers, data=data)
        except (AssertionError, Exception) as e:
            raise PermissionError(f'Auth verification failed: {e}')
        return headers.get('key')  # Return the signer's address

    def _check_auth(self, headers=None, data=None):
        """Check auth if require_auth is enabled. Returns signer address or None."""
        if not self.require_auth:
            return None
        return self._verify_auth(headers, data=data)

    # ── Storage ──────────────────────────────────────────────────────────────

    def _get_storage_client(self, provider=None):
        """Get or create a storage client for the given provider."""
        provider = provider or self.default_storage
        if provider in self._storage_clients:
            return self._storage_clients[provider]

        try:
            import mod as m
            if provider == 'localfs':
                client = m.mod('localfs')()
            elif provider in ('ipfs', 'filecoin'):
                client = m.mod('ipfs')()
            elif provider == 'lighthouse':
                client = m.mod('lighthouse')()
            else:
                raise ValueError(f'Unknown storage provider: {provider}')
            self._storage_clients[provider] = client
            return client
        except Exception as e:
            raise RuntimeError(f'Cannot load storage provider "{provider}": {e}')

    def _upload_json(self, data, storage=None) -> str:
        """Upload JSON data to storage provider. Returns prefixed URI like 'ipfs/{cid}'."""
        storage = storage or self.default_storage
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                raise ValueError('Data must be a JSON object (dict) or valid JSON string')
        if not isinstance(data, dict):
            raise ValueError('Data must be a JSON object (dict)')

        client = self._get_storage_client(storage)
        cid = client.put(data)
        return make_data_uri(storage, cid)

    def resolve(self, data_uri: str) -> dict:
        """Resolve a prefixed data URI (e.g. 'ipfs/{cid}') to its JSON content."""
        provider, cid = parse_data_uri(data_uri)
        if not provider:
            raise ValueError(f'Invalid data URI: {data_uri}. Expected format: ipfs/{{cid}}, lighthouse/{{cid}}, or filecoin/{{cid}}')
        client = self._get_storage_client(provider)
        return client.get(cid)

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def forward(self, name=None, data=None, backend=None, storage=None, headers=None, **kw):
        """Default action: register a mod, or list all if no args."""
        if name and data:
            return self.register(name, data, backend=backend, storage=storage, headers=headers, **kw)
        return self.list_all(backend=backend, **kw)

    def register(self, name: str, data, backend=None, storage=None, headers=None, **kw) -> str:
        """Register a new mod. Data must be a JSON dict — uploaded to storage provider.

        Args:
            name: Module name.
            data: JSON dict, JSON string, or pre-formatted URI ('ipfs/{cid}').
            backend: Registry backend ('offchain', 'evm', 'solana', 'near').
            storage: Storage provider ('ipfs', 'lighthouse', 'filecoin').
            headers: Auth headers from oath module (required if require_auth=True).

        Returns:
            Mod ID string.
        """
        signer = self._check_auth(headers, data={'name': name, 'action': 'register'})
        if signer and 'owner' not in kw:
            kw['owner'] = signer

        if isinstance(data, str) and parse_data_uri(data)[0]:
            data_uri = data
        else:
            data_uri = self._upload_json(data, storage=storage)
        return self._get_backend(backend).register(name, data_uri, **kw)

    def update(self, mod_id: str, data, backend=None, storage=None, headers=None, **kw) -> bool:
        """Update mod data. Data must be a JSON dict — uploaded to storage provider."""
        signer = self._check_auth(headers, data={'mod_id': mod_id, 'action': 'update'})
        if signer and 'owner' not in kw:
            kw['owner'] = signer

        if isinstance(data, str) and parse_data_uri(data)[0]:
            data_uri = data
        else:
            data_uri = self._upload_json(data, storage=storage)
        return self._get_backend(backend).update(mod_id, data_uri, **kw)

    def remove(self, mod_id: str, backend=None, headers=None, **kw) -> bool:
        """Remove a mod."""
        signer = self._check_auth(headers, data={'mod_id': mod_id, 'action': 'remove'})
        if signer and 'owner' not in kw:
            kw['owner'] = signer

        return self._get_backend(backend).remove(mod_id, **kw)

    def get(self, mod_id: str, backend=None, resolve=False, **kw) -> dict:
        """Get mod by ID. If resolve=True, also fetches JSON from storage."""
        mod = self._get_backend(backend).get(mod_id, **kw)
        if mod and resolve:
            provider, cid = parse_data_uri(mod.get('data', ''))
            if provider:
                mod = dict(mod)
                mod['resolved'] = self.resolve(mod['data'])
        return mod

    def list(self, owner: str = None, backend=None, **kw) -> list:
        """List mods for an owner."""
        if owner:
            return self._get_backend(backend).get_user_mods(owner, **kw)
        return self.list_all(backend=backend, **kw)

    def list_all(self, backend=None, **kw) -> list:
        """List all mods."""
        return self._get_backend(backend).list_all(**kw)

    def transfer(self, mod_id: str, new_owner: str, backend=None, headers=None, **kw) -> bool:
        """Transfer mod ownership."""
        signer = self._check_auth(headers, data={'mod_id': mod_id, 'action': 'transfer'})
        if signer and 'owner' not in kw:
            kw['owner'] = signer

        return self._get_backend(backend).transfer(mod_id, new_owner, **kw)

    def is_name_taken(self, owner: str, name: str, backend=None, **kw) -> bool:
        """Check if name is taken for owner."""
        return self._get_backend(backend).is_name_taken(owner, name, **kw)

    def sync(self, source='offchain', target='evm', owner: str = None, **kw):
        """Sync mods from one backend to another."""
        src = self._get_backend(source)
        dst = self._get_backend(target)

        if owner:
            mods = src.get_user_mods(owner)
        else:
            mods = src.list_all()

        synced = []
        for mod in mods:
            if not dst.is_name_taken(mod.get('owner', owner or 'local'), mod['name']):
                new_id = dst.register(
                    mod['name'],
                    mod['data'],
                    owner=mod.get('owner', owner),
                )
                synced.append({'source_id': mod.get('id'), 'target_id': new_id, 'name': mod['name']})
        return synced

    # ── Mods directory (local module registry) ─────────────────────────────

    @property
    def mods_path(self):
        """Path to the local mods directory (registry/mods/)."""
        return os.path.join(os.path.dirname(__file__), '..', 'mods')

    def mods_tree(self, depth=2) -> dict:
        """List all modules in the mods directory. Returns {owner/name: path}."""
        mods_dir = os.path.realpath(self.mods_path)
        if not os.path.isdir(mods_dir):
            return {}
        tree = {}
        for owner in sorted(os.listdir(mods_dir)):
            owner_dir = os.path.join(mods_dir, owner)
            if not os.path.isdir(owner_dir) or owner.startswith('.'):
                continue
            for mod_name in sorted(os.listdir(owner_dir)):
                mod_dir = os.path.join(owner_dir, mod_name)
                if not os.path.isdir(mod_dir) or mod_name.startswith('.'):
                    continue
                tree[f'{owner}/{mod_name}'] = mod_dir
        return tree

    def root_hash(self) -> dict:
        """Compute the root hash of all mods in the local mods directory.

        Walks registry/mods/, hashes every file, builds a Merkle-like tree,
        and returns the root CID plus the file tree.

        Returns:
            {'root': <cid>, 'tree': {owner/mod/file: <hash>}, 'n_files': int, 'n_mods': int}
        """
        import hashlib
        mods_dir = os.path.realpath(self.mods_path)
        if not os.path.isdir(mods_dir):
            return {'root': None, 'tree': {}, 'n_files': 0, 'n_mods': 0}

        file_hashes = {}
        mod_set = set()
        for dirpath, dirnames, filenames in os.walk(mods_dir):
            dirnames[:] = [d for d in dirnames if not d.startswith('.')]
            for fname in sorted(filenames):
                if fname.startswith('.'):
                    continue
                fpath = os.path.join(dirpath, fname)
                relpath = os.path.relpath(fpath, mods_dir)
                with open(fpath, 'rb') as f:
                    file_hashes[relpath] = hashlib.sha256(f.read()).hexdigest()
                # track unique owner/mod pairs
                parts = relpath.split(os.sep)
                if len(parts) >= 2:
                    mod_set.add(f'{parts[0]}/{parts[1]}')

        # root = hash of sorted file hashes (deterministic)
        root_data = json.dumps(file_hashes, sort_keys=True)
        root = hashlib.sha256(root_data.encode()).hexdigest()
        return {
            'root': root,
            'tree': file_hashes,
            'n_files': len(file_hashes),
            'n_mods': len(mod_set),
        }

    def commit_root(self, backend=None, storage=None, **kw) -> dict:
        """Compute the mods root hash and commit it to the registry.

        Stores the full file hash tree on the storage provider, then registers
        a '_mods_root' entry in the backend with the root hash and data URI.

        Returns:
            {'root': <sha256>, 'data_uri': <storage_uri>, 'mod_id': <id>, 'n_files': int, 'n_mods': int}
        """
        info = self.root_hash()
        if not info['root']:
            return {'error': 'no mods found', **info}

        # upload the file tree to storage
        data_uri = self._upload_json({
            'root': info['root'],
            'tree': info['tree'],
            'n_files': info['n_files'],
            'n_mods': info['n_mods'],
        }, storage=storage)

        # register as _mods_root in the backend
        mod_id = self._get_backend(backend).register('_mods_root', data_uri, **kw)

        return {
            'root': info['root'],
            'data_uri': data_uri,
            'mod_id': mod_id,
            'n_files': info['n_files'],
            'n_mods': info['n_mods'],
        }

    def verify_root(self, expected_root=None, **kw) -> dict:
        """Verify the current mods directory matches a committed root hash.

        If no expected_root is given, fetches the latest committed one.

        Returns:
            {'valid': bool, 'current_root': <hash>, 'expected_root': <hash>, 'diff': [...]}
        """
        current = self.root_hash()
        if expected_root is None:
            # fetch latest committed root from backend
            committed = self.get('_mods_root', **kw)
            if not committed:
                return {'valid': False, 'error': 'no committed root found', 'current_root': current['root']}
            resolved = self.resolve(committed['data'])
            expected_root = resolved['root']

        valid = current['root'] == expected_root
        diff = []
        if not valid and expected_root:
            # attempt to resolve committed tree for diff
            try:
                committed = self.get('_mods_root', **kw)
                resolved = self.resolve(committed['data'])
                committed_tree = resolved.get('tree', {})
                all_files = set(current['tree'].keys()) | set(committed_tree.keys())
                for f in sorted(all_files):
                    cur = current['tree'].get(f)
                    exp = committed_tree.get(f)
                    if cur != exp:
                        diff.append({'file': f, 'current': cur, 'expected': exp})
            except Exception:
                pass

        return {
            'valid': valid,
            'current_root': current['root'],
            'expected_root': expected_root,
            'diff': diff,
        }

    def start_worker(self, interval=300, **kw):
        """Reserved for the api's per-300s config-scan worker.

        Kept as a no-op so the existing `Api.__init__` call site continues
        to work. Use `start_onchain_sync()` for the daily CID publish.
        """
        return {'status': 'noop', 'note': 'use start_onchain_sync() for CID publishes'}

    def start_onchain_sync(self, interval=86400, backends=('localfs',), **kw):
        """Periodically push the local registry index to CID storage.

        Default interval is 24h — one daily on-chain (content-addressed)
        sync. First sync runs immediately so a fresh container has a CID
        for its current registry without waiting a full cycle.

        Args:
            interval: seconds between syncs (default 86400 = 1 day).
            backends: which CID backends to publish to. 'localfs' is wired
                today; 'multistore' wraps ipfs/filecoin/hippius and will be
                added once those modules are stable in this repo.
        """
        import threading
        existing = getattr(self, '_onchain_worker', None)
        if existing and existing.is_alive():
            return {'status': 'already_running', 'interval': getattr(self, '_onchain_interval', interval)}

        self._onchain_stop = threading.Event()
        self._onchain_interval = interval
        self._onchain_backends = tuple(backends)

        def loop():
            while True:
                try:
                    self.sync_onchain(backends=self._onchain_backends)
                except Exception as e:
                    print(f'[registry.onchain_sync] failed: {e}', file=sys.stderr)
                if self._onchain_stop.wait(interval):
                    return

        self._onchain_worker = threading.Thread(
            target=loop, daemon=True, name='registry-onchain-sync',
        )
        self._onchain_worker.start()
        return {'status': 'started', 'interval': interval, 'backends': list(backends)}

    def stop_onchain_sync(self):
        """Signal the on-chain sync worker to stop after its current tick."""
        if hasattr(self, '_onchain_stop'):
            self._onchain_stop.set()
        return {'status': 'stopped'}

    def sync_onchain(self, backends=('localfs',)):
        """One-shot publish of ~/.mod/registry/index.json to CID storage.

        Appends `{ts, cid, prev, backends}` to ~/.mod/registry/onchain.json
        so we get a linked-list audit trail of registry snapshots. The
        `prev` pointer lets future readers reconstruct registry history
        without scanning every backend.
        """
        import time
        storage_path = os.path.expanduser('~/.mod/registry')
        os.makedirs(storage_path, exist_ok=True)
        index_path = os.path.join(storage_path, 'index.json')
        log_path = os.path.join(storage_path, 'onchain.json')

        if not os.path.exists(index_path):
            return {'status': 'no_index', 'path': index_path}

        with open(index_path) as f:
            index_data = json.load(f)

        # Lazy import — keeps registry importable when localfs/multistore
        # aren't on disk yet (fresh checkout, partial install).
        from mod.core.mod import m as _m  # noqa: WPS433
        results = {}
        for backend in backends:
            try:
                if backend == 'localfs':
                    fs = _m.mod('localfs')()
                    results[backend] = {'cid': fs.put(index_data)}
                elif backend == 'multistore':
                    ms = _m.mod('multistore')()
                    results[backend] = {'cid': ms.put(index_data)}
                else:
                    results[backend] = {'error': f'unknown backend: {backend}'}
            except Exception as e:
                results[backend] = {'error': str(e)}

        log = []
        if os.path.exists(log_path):
            try:
                with open(log_path) as f:
                    log = json.load(f)
            except Exception:
                log = []
        prev_cid = log[-1].get('cid') if log else None
        # Primary CID is the first successful backend; record all for audit.
        primary_cid = next(
            (r.get('cid') for r in results.values() if isinstance(r, dict) and r.get('cid')),
            None,
        )
        entry = {
            'ts': time.time(),
            'cid': primary_cid,
            'prev': prev_cid,
            'backends': results,
        }
        log.append(entry)
        with open(log_path, 'w') as f:
            json.dump(log, f, indent=2)
        return entry

    def onchain_history(self, n: int = 20):
        """Read the on-chain sync log — recent registry CIDs and their parents."""
        log_path = os.path.expanduser('~/.mod/registry/onchain.json')
        if not os.path.exists(log_path):
            return []
        with open(log_path) as f:
            log = json.load(f)
        return log[-n:]

    def backends(self):
        """List available backends."""
        return ['offchain', 'evm', 'solana', 'near']

    def storage_providers(self):
        """List available storage providers."""
        return list(STORAGE_PROVIDERS)

    def serve(self, api_port=None, app_port=None):
        """Start API server and app from config."""
        import subprocess
        from pathlib import Path

        config = getattr(self, '_config', {})
        api_port = api_port or config.get('port') or None
        app_port = app_port or config.get('app_port') or None
        root = os.path.join(os.path.dirname(__file__), '..')
        log_dir = Path(f'/tmp/registry')
        log_dir.mkdir(parents=True, exist_ok=True)

        for p in [api_port, app_port]:
            if p:
                subprocess.run(f'lsof -ti:{p} | xargs kill -9', shell=True, capture_output=True)

        results = {}

        server_dir = os.path.join(root, 'server')
        if os.path.exists(os.path.join(server_dir, 'server.py')) and api_port:
            env = os.environ.copy()
            env['PYTHONPATH'] = os.path.join(root, '..', '..', '..')
            subprocess.Popen(
                ['python3', '-m', 'uvicorn', 'server:app',
                 '--host', '0.0.0.0', '--port', str(api_port), '--reload'],
                cwd=server_dir, env=env,
                stdout=open(log_dir / 'api.log', 'w'),
                stderr=subprocess.STDOUT,
            )
            results['api'] = f'http://localhost:{api_port}'

        app_dir = os.path.join(root, 'app')
        if os.path.exists(os.path.join(app_dir, 'package.json')) and app_port:
            subprocess.Popen(
                ['npx', 'next', 'dev', '-p', str(app_port)],
                cwd=app_dir,
                stdout=open(log_dir / 'app.log', 'w'),
                stderr=subprocess.STDOUT,
            )
            results['app'] = f'http://localhost:{app_port}'

        return results

