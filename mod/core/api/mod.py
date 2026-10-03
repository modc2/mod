import requests
import os
import json
from typing import Optional, Dict, Any, List, Union
from pathlib import Path
import time
import glob
import datetime
import inspect
import shutil
import mod as m

class Api:

    port = 8000
    folder_path = m.abspath('~/.mod/api')
    threads = {}

    _worker_started = False
    _config_cache = {}
    _config_cache_time = {}

    def __init__(self, key=None, store=None, auth='auth.base'):
        store = store or m.config('api').get('store', 'localfs')
        self.store = m.mod(store)()
        self.key = m.key(key)
        self.auth = m.mod(auth)()
        self._reg = m.mod('registry')(key=key, store=store)
        try:
            self._meter = m.mod('meter')()
        except Exception:
            self._meter = None
        if not Api._worker_started:
            Api._worker_started = True
            self._reg.start_worker(interval=300)
            # Daily-ish push of the local registry index to CID storage.
            # Pinned to localfs for now; multistore (ipfs/filecoin) follows
            # once the surrounding modules stabilize.
            try:
                self._reg.start_onchain_sync(interval=86400, backends=('localfs',))
            except Exception as e:
                m.print(f'registry onchain_sync init failed: {e}', color='yellow')
            # Start config scanner worker
            self.threads['config_scanner'] = m.thread(self._config_scanner_worker)

    def _record(self, user: str, fn: str, duration: float, status: str = 'success', **kw):
        """Record usage in the meter if available."""
        if self._meter:
            try:
                self._meter.record(user=user or '', fn=fn, duration=duration, status=status, **kw)
            except Exception:
                pass

    def _config_scanner_worker(self):
        """Worker that scans module configs every 1 second and updates registry/routes.

        Uses caching to avoid redundant config reads and only syncs when changes detected.
        """
        import threading
        while True:
            try:
                time.sleep(1)  # Scan every 1 second
                self._scan_and_sync_configs()
            except Exception as e:
                m.print(f'Config scanner error: {e}', color='red')

    def _scan_and_sync_configs(self):
        """Scan all module configs, detect changes, and sync to registry/routy."""
        import hashlib

        # Get all local modules
        try:
            local_mods = m.tree.orbit('orbit') or {}
        except Exception:
            return

        changed_mods = []
        current_time = time.time()

        for mod_name, mod_path in local_mods.items():
            try:
                config_path = os.path.join(mod_path, 'config.json')
                if not os.path.exists(config_path):
                    continue

                # Get file modification time
                mod_time = os.path.getmtime(config_path)

                # Check cache
                cache_key = f'{mod_name}:mtime'
                cached_mtime = Api._config_cache_time.get(cache_key)

                if cached_mtime and cached_mtime == mod_time:
                    # Config hasn't changed, skip
                    continue

                # Read and hash config
                with open(config_path, 'r') as f:
                    config_data = f.read()

                config_hash = hashlib.sha256(config_data.encode()).hexdigest()

                # Check if hash changed
                hash_cache_key = f'{mod_name}:hash'
                cached_hash = Api._config_cache.get(hash_cache_key)

                if cached_hash != config_hash:
                    # Config changed - parse and check for registry/routing updates
                    try:
                        config = json.loads(config_data)

                        # Update cache
                        Api._config_cache[hash_cache_key] = config_hash
                        Api._config_cache_time[cache_key] = mod_time

                        # Check if module has API/app URLs or endpoints that need routing
                        needs_routing = (
                            config.get('port') or
                            config.get('app_port') or
                            config.get('urls') or
                            config.get('endpoints') or
                            config.get('routes')
                        )

                        if needs_routing:
                            changed_mods.append({
                                'name': mod_name,
                                'config': config,
                                'path': mod_path
                            })
                    except json.JSONDecodeError:
                        pass
            except Exception:
                continue

        # Sync changed modules to routy
        if changed_mods:
            self._sync_to_routy(changed_mods)

    def _sync_to_routy(self, changed_mods):
        """Sync changed module configs to routy router."""
        try:
            # Check if routy is running
            import requests
            routy_url = 'http://localhost:3000'

            sync_data = {'apps': [], 'apis': []}

            # Get all namespace/registry data once
            try:
                ns = self.registry.namespace() or {}
            except Exception:
                ns = {}

            try:
                app_store = m.mod('store')('~/.mod/server/registry')
                app_reg = app_store.get('app_registry.json', {}) or {}
            except Exception:
                app_reg = {}

            for mod_info in changed_mods:
                name = mod_info['name']
                config = mod_info['config']

                # Check API namespace
                if name in ns:
                    url = ns[name].replace('0.0.0.0', '127.0.0.1')
                    # Extract storage info from config
                    storage_type = config.get('storage_type')
                    cid = config.get('schema') or config.get('cid')

                    sync_data['apis'].append({
                        'name': name,
                        'target_url': url,
                        'storage_type': storage_type,
                        'cid': cid,
                    })

                # Check app registry
                if name in app_reg and isinstance(app_reg[name], dict):
                    if 'url' in app_reg[name]:
                        storage_type = config.get('storage_type')
                        cid = config.get('schema') or config.get('cid')

                        sync_data['apps'].append({
                            'name': name,
                            'target_url': app_reg[name]['url'],
                            'storage_type': storage_type,
                            'cid': cid,
                        })

            # Send sync request if there's data
            if sync_data['apps'] or sync_data['apis']:
                try:
                    requests.post(
                        f'{routy_url}/_api/sync',
                        json=sync_data,
                        timeout=2
                    )
                    m.print(f'Synced {len(sync_data["apis"])} APIs + {len(sync_data["apps"])} apps to routy', color='green')
                except requests.RequestException:
                    pass  # Routy not running or unavailable

            # Also register discovered endpoints/routes
            self._register_endpoints(changed_mods)
        except Exception as e:
            m.print(f'Routy sync error: {e}', color='yellow')

    def _register_endpoints(self, changed_mods):
        """Register discovered endpoints from config.json to router/registry."""
        for mod_info in changed_mods:
            name = mod_info['name']
            config = mod_info['config']

            # Register explicit endpoints
            endpoints = config.get('endpoints', [])
            if endpoints and isinstance(endpoints, list):
                for endpoint in endpoints:
                    if isinstance(endpoint, str):
                        # Cache endpoint info
                        cache_key = f'{name}:endpoint:{endpoint}'
                        Api._config_cache[cache_key] = {
                            'mod': name,
                            'endpoint': endpoint,
                            'discovered_at': time.time()
                        }

            # Register explicit routes
            routes = config.get('routes', {})
            if routes and isinstance(routes, dict):
                for route_path, route_target in routes.items():
                    # Cache route info
                    cache_key = f'{name}:route:{route_path}'
                    Api._config_cache[cache_key] = {
                        'mod': name,
                        'path': route_path,
                        'target': route_target,
                        'discovered_at': time.time()
                    }

    def get_discovered_endpoints(self, mod: str = None) -> Dict[str, Any]:
        """Get all discovered endpoints from cached config data.

        Args:
            mod: Optional module name filter

        Returns:
            Dict of discovered endpoints and routes
        """
        endpoints = {}
        routes = {}

        for key, value in Api._config_cache.items():
            if ':endpoint:' in key:
                if mod is None or key.startswith(f'{mod}:'):
                    mod_name = value.get('mod')
                    endpoint = value.get('endpoint')
                    if mod_name not in endpoints:
                        endpoints[mod_name] = []
                    endpoints[mod_name].append(endpoint)

            elif ':route:' in key:
                if mod is None or key.startswith(f'{mod}:'):
                    mod_name = value.get('mod')
                    path = value.get('path')
                    target = value.get('target')
                    if mod_name not in routes:
                        routes[mod_name] = {}
                    routes[mod_name][path] = target

        return {
            'endpoints': endpoints,
            'routes': routes,
            'cache_size': len(Api._config_cache),
            'last_scan': max([v.get('discovered_at', 0) for v in Api._config_cache.values() if isinstance(v, dict) and 'discovered_at' in v], default=0)
        }

    @property
    def router(self):
        if not hasattr(self, '_router'):
            self._router = m.mod('router')()
        return self._router

    @property
    def registry(self):
        """Lazy accessor for server.namespace."""
        if not hasattr(self, '_registry'):
            self._registry = m.mod('server.namespace')()
        return self._registry

    def run_job(self, fn: str, params: dict = None, remote: bool = False,
                timeout: int = 300, wait: bool = True, **kwargs) -> Dict[str, Any]:
        """Execute a function locally or remotely via worker pool.

        Args:
            fn: Function path like 'mod/function'
            params: Parameters dict
            remote: If True, execute on worker pool; if False, try local first
            timeout: Timeout in seconds
            wait: If True, block until result ready; if False, return task info
            **kwargs: Additional parameters

        Returns:
            Task result or task info dict
        """
        params = params or {}
        params.update(kwargs)

        # Route through router which handles local vs remote execution
        return self.router.call(fn=fn, params=params, wait=wait, timeout=timeout)

    def submit_job(self, fn: str, params: dict = None, **kwargs) -> str:
        """Submit a job for async execution and return task CID.

        Returns:
            Task CID for tracking
        """
        task = self.run_job(fn, params=params, wait=False, **kwargs)
        return task.get('cid')

    def forward(self, **kwargs):
        return {
            'name': 'api',
            'address': self.key.address,
            'mods': self.n(),
            'namespace': self.namespace(),
        }

    def call(self, fn='chain/balances', params={}, **kwargs):
        return self.router.call(fn, params, **kwargs)

    def token(self, update=False, max_age=3600):
        path = self._reg.path('token.txt')
        token = m.get(path, None, update=update, max_age=max_age)
        if token is None:
            token = self.auth.token()
            m.put(path, token)
        return token

    def txs(self, *args, **kwargs):
        return self.router.txs(*args, **kwargs)

    @property
    def config(self):
        return m.config('api')

    def addy(self, key=None):
        return self.key.address or m.addy(key)

    # --- Registry delegations ---

    def exists(self, mod='store', key=None) -> bool:
        return self._reg.exists(mod=mod, key=key)

    def mod(self, mod='api', key=None, schema=False, expand=False, update=False, **kwargs) -> Dict[str, Any]:
        result = self._reg.mod(mod=mod, key=key, schema=schema, expand=expand, update=update, **kwargs)
        # Auto-register local modules on first access
        if result.get('local') and result.get('name'):
            try:
                self._reg.reg(mod=result['name'], key=key, comment='auto-registered')
                result = self._reg.mod(mod=mod, key=key, schema=schema, expand=expand, update=update, **kwargs)
            except Exception:
                pass
        return result

    def root(self, encrypt=True, update=True, **kwargs) -> str:
        return self._reg.root(encrypt=encrypt, update=update, **kwargs)

    def get_root(self, decrypt=True, **kwargs) -> Dict[str, Any]:
        return self._reg.get_root(decrypt=decrypt, **kwargs)

    def root_hash(self, update=False, max_age=3600, **kwargs) -> Dict[str, Any]:
        """Source root hash of every module on this node (cached, public read).

        `owners` lets a client tell whether it may call commit_root_hash, which
        the gate restricts to the owner role.
        """
        result = self._reg.root_hash(update=update, max_age=int(max_age))
        result['owners'] = self.owner_keys()
        return result

    def commit_root_hash(self, comment=None, **kwargs) -> Dict[str, Any]:
        """Pin the current root hash as the committed one. Owner only (gate)."""
        return self._reg.commit_root_hash(comment=comment)

    def owner_keys(self) -> List[str]:
        """Addresses in the server's owner role."""
        data = m.get(m.abspath('~/.mod/server/roles/owner'), {}) or {}
        return [str(u).lower() for u in data.get('users', []) if u]

    def content(self, mod, key=None, expand=False, depth=None, h=False) -> Dict[str, Any]:
        return self._reg.content(mod, key=key, expand=expand, depth=depth, h=h)

    def key_address(self, key=None):
        return self._reg.key_address(key)

    def cid(self, mod, key=None, default=None) -> str:
        return self._reg.cid(mod, key=key, default=default)

    def reg_info(self, mod: dict):
        return self._reg.reg_info(mod)

    def put(self, data):
        return self._reg.put(data)
    add = put

    def get(self, cid: str) -> Any:
        return self._reg.get(cid)

    def add_content(self, mod: str='store', comment=None) -> Dict[str, str]:
        return self._reg.add_content(mod=mod, comment=comment)

    def wrap(self, mod: str):
        return m.fn('wrap/forward')(mod)

    def add_schema(self, mod: str='store', public=True) -> str:
        return self._reg.add_schema(mod=mod, public=public)

    def get_url(self, url: str) -> str:
        return self._reg.get_url(url)

    def is_git_url(self, url: str) -> bool:
        return self._reg.is_git_url(url)

    def reg_git(self, url: str, name=None, key=None, comment=None, token=None) -> Dict[str, Any]:
        return self._reg.reg_git(url, name=name, key=key, comment=comment, token=token)

    def reg_cid(self, cid: str, key=None, name=None, comment=None) -> Dict[str, Any]:
        return self._reg.reg_cid(cid, key=key, name=name, comment=comment)

    def get_info(self, mod='store', key=None, name=None, comment=None, public=False) -> Dict[str, Any]:
        return self._reg.get_info(mod=mod, key=key, name=name, comment=comment, public=public)

    def is_cid_url(self, url: str) -> bool:
        return self._reg.is_cid_url(url)

    def is_mod_url(self, url: str) -> bool:
        return self._reg.is_git_url(url) or self._reg.is_cid_url(url)

    def reg(self, mod: Union[str, dict] = 'store', key=None, comment=None, public=True, token=None, name=None, suggest=False, auto_modify=False, focus=None) -> Dict[str, Any]:
        t0 = time.time()
        if token:
            key = self.auth.verify(token)['key']
        result = self._reg.reg(mod=mod, key=key, comment=comment, public=public, token=None, name=name)
        if suggest and isinstance(mod, str):
            try:
                suggestions = m.fn('suggest/forward')(mod=mod, focus=focus)
                result['suggestions'] = suggestions.get('suggestions', [])
            except Exception as e:
                result['suggestions'] = []
                result['suggest_error'] = str(e)
        if auto_modify and isinstance(mod, str) and result.get('suggestions'):
            try:
                applied = m.fn('modify/apply_suggestions')(
                    mod=mod, suggestions=result['suggestions'][:3], safety=False
                )
                result['applied'] = applied
                result = self._reg.reg(mod=mod, key=key, comment=f'{comment or ""} [auto-modified]', public=public)
                result['applied'] = applied
            except Exception as e:
                result['modify_error'] = str(e)
        mod_name = mod if isinstance(mod, str) else (mod.get('name', '') if isinstance(mod, dict) else '')
        self._record(user=key or '', fn=f'reg/{mod_name}', duration=time.time() - t0)
        return result

    def update(self):
        self._reg.update()

    def anchor_file(self, mod: str, key=None) -> Dict[str, Any]:
        return self._reg.anchor_file(mod, key=key)

    def reg_payload(self, mod: str = 'store', key=None, comment=None) -> Dict[str, Any]:
        return self._reg.reg_payload(mod=mod, key=key, comment=comment)

    def path(self, path: str) -> str:
        return self._reg.path(path)

    def mods(self, search: str = None, key='all', n: int = None, page: int = None, page_size=10, **kwargs) -> List[Dict[str, Any]]:
        return self._reg.mods(search=search, key=key, n=n, page=page, page_size=page_size, **kwargs)

    def rename(self, old_mod: str, new_mod: str, key=None) -> Dict[str, Any]:
        old_dp = m.dp(old_mod, key=key)
        new_dp = m.dp(new_mod, key=key)
        return {'old_dp': old_dp, 'new_dp': new_dp, 'status': 'success'}

    @property
    def chain(self):
        if not hasattr(self, '_chain'):
            self._chain = m.mod('chain')()
            self._chain.name = 'chain'
            sync_fns = ['balance']
            for fn_name in sync_fns:
                setattr(self, fn_name, getattr(self._chain, fn_name))
        return self._chain

    def versions(self, mod='app', key=None, df=False, n=1000, update=True, max_age=None) -> List[Dict[str, Any]]:
        return self._reg.versions(mod=mod, key=key, df=df, n=n, update=update, max_age=max_age)

    v = versions

    def regall(self, key=None, depth=1, comment=None, public=False, timeout=30) -> Dict[str, Any]:
        return self._reg.regall(key=key, depth=depth, comment=comment, public=public, timeout=timeout)

    def registry(self, key='all', update=False) -> Dict[str, str]:
        return self._reg.registry(key=key, update=update)

    def onchain_history(self, n: int = 20) -> List[Dict[str, Any]]:
        """Recent daily on-chain registry CIDs (linked-list audit trail)."""
        return self._reg.onchain_history(n=n)

    def sync_onchain(self, backends: list = None) -> Dict[str, Any]:
        """One-shot manual trigger of the on-chain registry publish."""
        return self._reg.sync_onchain(backends=tuple(backends or ('localfs',)))

    def _clear(self) -> bool:
        return self._reg._clear()

    def schema(self, mod='store', key=None) -> Dict[str, Any]:
        return self._reg.schema(mod=mod, key=key)

    def sync_schemas(self, search=None, depth=1) -> Dict[str, str]:
        return self._reg.sync_schemas(search=search, depth=depth)

    def start_worker(self, interval=300) -> dict:
        return self._reg.start_worker(interval=interval)

    def stop_worker(self) -> dict:
        return self._reg.stop_worker()

    def worker_status(self) -> dict:
        return self._reg.worker_status()

    def setback(self, mod: str, cid: str, key=None, safety=True) -> Dict[str, Any]:
        return self._reg.setback(mod, cid, key=key, safety=safety)

    def rm_mod(self, mod='store', key=None) -> bool:
        return self._reg.rm_mod(mod=mod, key=key)

    def user_keys(self, key=None) -> List[str]:
        return self._reg.user_keys(key=key)

    def users(self, search=None, update=False, **kwargs) -> List[Dict[str, Any]]:
        return self._reg.users(search=search, update=update, **kwargs)

    def user(self, key: str = None, update=False, expand=False) -> Dict[str, Any]:
        return self._reg.user(key=key, update=update, expand=expand)

    def fork(self, mod: str, key=None, comment=None, public=False) -> Dict[str, Any]:
        t0 = time.time()
        try:
            original_path = m.dp(mod)
            if not original_path or not os.path.exists(original_path):
                return {'error': f'Module {mod} not found'}
            key_address = self._reg.key_address(key)
            new_path = os.path.join(m.paths['orbit']['mods'], key_address, mod.replace('.', '/'))
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            if os.path.exists(new_path):
                shutil.rmtree(new_path)
            shutil.copytree(original_path, new_path)
            m._tree.orbit('mods', update=True)
            reg_result = self._reg.reg(mod=mod, key=key, comment=comment or f'forked from {mod}', public=public)
            self._record(user=key or key_address or '', fn=f'fork/{mod}', duration=time.time() - t0)
            return {
                'status': 'forked',
                'mod': mod,
                'name': mod,
                'key': key_address,
                'path': new_path,
                'reg': reg_result,
            }
        except Exception as e:
            return {'error': str(e)}

    def edit(self, query: str = 'make the readme better', mod='app', key=None, steps=20, **kwargs) -> Dict[str, Any]:
        t0 = time.time()
        status = 'success'
        # Owner check: only the module owner can edit
        caller = self._reg.key_address(key) if key else self.key.address.lower()
        mod_info = self._reg.mod(mod=mod)
        mod_owner = (mod_info.get('key') or '').lower()
        if mod_owner and caller.lower() != mod_owner and caller.lower() != self.key.address.lower():
            return {'error': f'Permission denied: only the module owner can edit {mod}'}
        try:
            m.fn('dev/forward')(query=query, mod=mod, safety=False, key=key, steps=steps, **kwargs)
        except Exception as e:
            status = 'error'
            self._record(user=key or '', fn=f'edit/{mod}', duration=time.time() - t0, status=status)
            raise
        result = self.reg(mod=mod, key=key, comment=query)
        self._record(user=key or '', fn=f'edit/{mod}', duration=time.time() - t0, status=status)
        return result

    def modify(self, mod: str = 'base', query: str = None, focus: str = None, key=None,
               model: str = 'anthropic/claude-sonnet-4-5-20250929', max_apply: int = 3,
               safety: bool = False, **kwargs) -> Dict[str, Any]:
        """
        Suggest improvements and apply them to a module, then register.
        Full pipeline: suggest -> modify -> reg.
        """
        t0 = time.time()
        status = 'success'
        # Owner check: only the module owner can modify
        caller = self._reg.key_address(key) if key else self.key.address.lower()
        mod_info = self._reg.mod(mod=mod)
        mod_owner = (mod_info.get('key') or '').lower()
        if mod_owner and caller.lower() != mod_owner and caller.lower() != self.key.address.lower():
            return {'error': f'Permission denied: only the module owner can modify {mod}'}
        try:
            result = m.fn('modify/suggest_and_apply')(
                mod=mod, focus=focus, model=model,
                max_apply=max_apply, safety=safety, **kwargs
            )
            if query:
                m.fn('modify/forward')(mod=mod, query=query, model=model, safety=safety)
            reg_result = self.reg(mod=mod, key=key, comment=f'modified: {focus or query or "auto"}')
            result['reg'] = reg_result
        except Exception as e:
            status = 'error'
            self._record(user=key or '', fn=f'modify/{mod}', duration=time.time() - t0, status=status)
            raise
        self._record(user=key or '', fn=f'modify/{mod}', duration=time.time() - t0, status=status)
        return result

    def files(self, mod='store', search=None, **kwargs):
        return self._reg.files(mod=mod, search=search, **kwargs)

    def __delete__(self):
        for k, thread in self.threads.items():
            print(f'Killing {k}')
            thread.kill()
        del self.thread

    def namespace(self, *args, **kwargs):
        return m.fn('server/namespace')()

    def app_namespace(self, *args, **kwargs):
        ns = m.mod('server.namespace')()
        return ns.app_namespace()

    def app_owner(self, mod: str = '', **kwargs):
        ns = m.mod('server.namespace')()
        return ns.app_owner(mod)

    def is_app_owner(self, mod: str = '', address: str = '', **kwargs):
        ns = m.mod('server.namespace')()
        return ns.is_app_owner(mod, address)

    def new_app(self, name: str = 'myapp', port: int = None, key=None, **kwargs):
        """One-step: create, configure, install, and serve a module app."""
        return m.new_app(name=name, port=port, **kwargs)

    def kill_app(self, name: str = '', key=None, **kwargs):
        """Kill a module app server. Owner only."""
        return m.kill_app(name=name, key=key)

    def edit_app(self, name: str = '', query: str = '', key=None, **kwargs):
        """Edit a module app. Owner only."""
        return m.edit_app(name=name, query=query, key=key, **kwargs)

    def serve_app(self, name: str = '', port: int = None, api_port: int = None, key=None, **kwargs):
        """Start a stopped module app server. Owner only. Optional port/api_port override."""
        ns = m.mod('server.namespace')()
        address = m.key_address(key) if key else m.owner()
        if not ns.is_app_owner(name, address):
            return {'error': f'Not owner of {name}'}

        # Get installed info for port (auto-install if not found)
        installed = ns.store.get('app_installed.json', {})
        info = installed.get(name, {})

        # Auto-install if module exists but not registered
        if not info:
            try:
                # Check if module exists - try to load it
                mod_path = m.dp(name)
                if not mod_path or not os.path.exists(mod_path):
                    return {'error': f'{name} module not found'}

                # Auto-register the module
                if not installed:
                    installed = {}
                installed[name] = {
                    'port': port or 'auto',
                    'api_port': api_port or 'auto',
                    'owner': address,
                    'auto_installed': True
                }
                ns.store.put('app_installed.json', installed)
                info = installed[name]
            except Exception as e:
                return {'error': f'{name} not found: {str(e)}'}

        # Use user-provided port, fall back to installed port
        serve_port = port or (info.get('port') if info.get('port') != 'auto' else None)
        serve_api_port = api_port or (info.get('api_port') if info.get('api_port') != 'auto' else None)

        try:
            # Load the module more carefully - look for the main module file
            # For claude, this should load claude.mod, not api.mod
            mod_path = m.dp(name)

            # Try to find and import the main mod.py
            possible_paths = [
                os.path.join(mod_path, name, 'mod.py'),  # e.g., claude/claude/mod.py
                os.path.join(mod_path, 'mod.py'),         # e.g., claude/mod.py
            ]

            mod_obj = None
            for path in possible_paths:
                if os.path.exists(path):
                    # Import the module from this path
                    import importlib.util
                    spec = importlib.util.spec_from_file_location(f"{name}.mod", path)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                        if hasattr(module, 'Mod'):
                            mod_obj = module.Mod()
                            break

            if not mod_obj:
                # Fallback to regular mod loading
                mod_obj = m.mod(name)()

            # Check if serve method exists
            if not hasattr(mod_obj, 'serve'):
                return {'error': f'{name} does not have a serve method'}

            # Pass api_port if the module's serve accepts it
            serve_kwargs = {}
            if serve_port:
                serve_kwargs['port'] = serve_port
            if serve_api_port:
                serve_kwargs['api_port'] = serve_api_port

            # Call serve and get result
            result = mod_obj.serve(**serve_kwargs)

            # Update module config.json with URLs if available
            if isinstance(result, dict) and 'urls' in result:
                try:
                    mod_path = m.dp(name)
                    config_path = os.path.join(mod_path, 'config.json')
                    if os.path.exists(config_path):
                        with open(config_path, 'r') as f:
                            config = json.load(f)
                        config['urls'] = result['urls']
                        config['api_url'] = result['urls'].get('api', '')
                        config['app_url'] = result['urls'].get('app', '')
                        with open(config_path, 'w') as f:
                            json.dump(config, f, indent=2)
                except Exception as e:
                    print(f"Warning: Could not update config.json: {e}")

            # Return immediately - don't wait for logs (they can be fetched separately)
            response = {
                'status': 'started',
                'name': name,
                'port': serve_port,
                'api_port': serve_api_port,
                'urls': result.get('urls') if isinstance(result, dict) else {},
                'result': result
            }

            # Optionally include logs if they're quickly available
            try:
                logs = self.app_logs(name, lines=20)
                if logs and not logs.get('error'):
                    response['logs'] = logs
            except:
                pass  # Skip logs if they're not available yet

            return response
        except Exception as e:
            return {'error': str(e)}

    def serve_status(self, name: str = '', **kwargs):
        """Get namespace entries matching or similar to a module name."""
        ns = m.mod('server.namespace')()
        # Get both API and app registries
        api_servers = ns.namespace(search=name) if name else ns.namespace()
        app_servers = ns.app_namespace(search=name) if name else ns.app_namespace()
        return {'api_servers': api_servers, 'app_servers': app_servers}

    def remove_app(self, name: str = '', key=None, **kwargs):
        """Remove a module app entirely. Owner only."""
        ns = m.mod('server.namespace')()
        address = m.key_address(key) if key else m.owner()
        if not ns.is_app_owner(name, address):
            return {'error': f'Not owner of {name}'}
        # Stop if running
        try:
            m.kill_app(name=name, key=key)
        except Exception:
            pass
        # Remove from installed
        ns.uninstall_app(name)
        return {'status': 'removed', 'name': name}

    def app_status(self, key=None, **kwargs):
        """Get all module apps with running/stopped status."""
        owner = m.key_address(key) if key else None
        ns = m.mod('server.namespace')()
        return ns.app_status(owner=owner)

    def app_logs(self, name: str = '', lines: int = 100, **kwargs):
        """Get logs for a module's app/api servers."""
        if not name:
            return {'error': 'Module name required'}
        logs = {}
        # Check PM2 logs
        pm2_home = os.path.expanduser(os.environ.get('PM2_HOME', '~/.pm2'))
        logs_dir = os.path.join(pm2_home, 'logs')
        # Check /tmp logs (from app service route)
        tmp_patterns = {
            'api': f'/tmp/mod-api-{name}.log',
            'app': f'/tmp/mod-app-{name}.log',
        }
        for svc_type, log_path in tmp_patterns.items():
            if os.path.exists(log_path):
                try:
                    import subprocess
                    result = subprocess.run(['tail', '-n', str(lines), log_path], capture_output=True, text=True)
                    if result.stdout:
                        logs[svc_type] = result.stdout
                except Exception as e:
                    logs[svc_type] = f'Error reading log: {e}'
        # Check PM2 logs for the module name
        for suffix in ['out', 'error']:
            for prefix in [name, f'{name}-api', f'{name}-app']:
                log_file = os.path.join(logs_dir, f'{prefix}-{suffix}.log')
                if os.path.exists(log_file):
                    try:
                        import subprocess
                        result = subprocess.run(['tail', '-n', str(lines), log_file], capture_output=True, text=True)
                        if result.stdout:
                            key = f'pm2_{prefix}_{suffix}'
                            logs[key] = result.stdout
                    except Exception:
                        pass
        if not logs:
            return {'error': f'No logs found for {name}'}
        return logs

    def workers(self, **kwargs):
        """Get worker pool status with per-worker details."""
        return self.router.pool.status()

    def deploy_workers(self, min_workers: int = 1, max_workers: int = 10, **kwargs):
        """Deploy/reconfigure the worker pool with min/max scaling limits.

        Args:
            min_workers: Minimum workers always running (floor=1)
            max_workers: Maximum workers to scale up to
        """
        return self.router.pool.set_limits(min_workers=min_workers, max_workers=max_workers)

    def scale_workers(self, n: int = 1, **kwargs):
        """Manually scale the worker pool to n workers (clamped to min/max)."""
        return self.router.pool.scale(n)

    def kill_worker(self, cid: str = '', **kwargs):
        """Kill the worker running a specific task CID."""
        if not cid:
            return {'error': 'CID required'}
        killed = self.router.pool.kill(cid)
        return {'killed': killed, 'cid': cid}

    def kill_all_workers(self, **kwargs):
        """Kill all workers and reset the pool."""
        self.router.pool.kill_all()
        return {'status': 'all workers killed'}

    def n(self, *args, **kwargs):
        return len(self.mods(*args, **kwargs))

    def new(self, url: str, name: str = None, key=None, comment=None, **kwargs) -> Dict[str, Any]:
        """Register a new module from a GitHub URL.

        Usage:
            c api/new https://github.com/user/repo
            c api/new user/repo

        Clones the repo into the local orbit, writes config.json provenance,
        stores content + schema CIDs, and adds an entry to the registry.
        """
        if not isinstance(url, str) or not url.strip():
            return {'error': 'GitHub URL required. Usage: c api/new {github_url}'}
        url = url.strip()

        is_github_full = 'github.com' in url
        is_shorthand = '/' in url and len(url.split('/')) == 2 and 'github.com' not in url
        if not (is_github_full or is_shorthand):
            return {'error': f'Only GitHub URLs are supported. Got: {url!r}. Use https://github.com/user/repo or user/repo.'}

        t0 = time.time()
        info = self._reg.reg_git(url, name=name, key=key, comment=comment)
        self._record(user=key or '', fn=f'new/{info.get("name", url)}', duration=time.time() - t0)
        return info

    def dp(self, path: str, key=None) -> str:
        return self._reg.dp(path, key=key)

    def is_owner(self, address: str):
        return self._reg.is_owner(address)

    def balance(self, address: str = None, token: str = 'market'):
        return self.chain.balance(address, token)

    def get_balances(self, address: str = None, tokens: list = None):
        return self.chain.balances(address=address, tokens=tokens)

    def balances(self, token: str = 'market', from_block: int = 0, to_block: int = None, weeks: int = 2):
        return self.chain.balances(token=token, from_block=from_block, to_block=to_block, weeks=weeks)

    def scan_holders(self, token: str = 'market', weeks: int = 2, from_block: int = 0, to_block: int = None):
        holders = self.chain.scan_token_holders(
            token=token,
            from_block=from_block,
            to_block=to_block,
            weeks=weeks
        )
        return {
            'holders': holders,
            'total_holders': len(holders),
            'token': token.upper(),
            'weeks': weeks
        }

    def credit(self, stable_amount: float, payment_token: str = 'usdt'):
        return self.chain.credit(stable_amount=stable_amount, payment_token=payment_token)

    def register(self, mod: str = None):
        if mod:
            return self.chain.reg(name=mod)
        return {'error': 'mod parameter required'}

    def build_transaction(self, to: str, data: str = '0x', value: int = 0, gas: int = None):
        return self.chain.build_transaction(to=to, data=data, value=value, gas=gas)

    def send_raw_transaction(self, signed_tx: str):
        return self.chain.send_raw_transaction(signed_tx)

    def encode_function_call(self, contract: str, function: str, args: list):
        return self.chain.encode_function_call(contract, function, args)

    def graduate(self, mod: str, key=None, comment=None, public=False) -> Dict[str, Any]:
        key = self._reg.key_address(key)
        if key != self.key.address:
            print(f"Graduating mod {mod} from key {key} to server key {self.key.address}")
            return self.reg(mod=mod, key=self.key.address, comment=comment, public=public)
        else:
            print(f"Mod {mod} is already under the server key. No graduation needed.")
            return self.mod(mod, key=key)
