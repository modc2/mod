import mod as m
from typing import *

class Namespace:

    def __init__(self, path='~/.mod/server/registry'):
        self.store = m.mod('store')(path)
        self.registry_path = 'registry.json'
    def reg(self, name: str, address:str) -> Dict[str, Any]:
        """
        Register a server with its address.
        """
        registry = self.store.get(self.registry_path, {})
        registry[name] = address
        self.store.put(self.registry_path, registry)
        self._caddy_sync()
        return {'status': 'success', 'name': name, 'address': address}

    def dereg(self, name: str) -> Dict[str, Any]:
        """
        Deregister a server by its name.
        """
        registry = self.store.get(self.registry_path, {})
        if name in registry:
            del registry[name]
            self.store.put(self.registry_path, registry)
            self._caddy_sync()
            return {'status': 'success', 'name': name}
        else:
            return {'status': 'error', 'name': name, 'error': 'not found'}

    def exists(self, name: str ) -> bool:
        """
        Check if a server is registered.
        """
        registry = self.store.get(self.registry_path, {})
        return name in registry

    def namespace(self, search: Optional[str] = None, **kwargs) -> Dict[str, str]:
        """
        Get the namespace of registered servers, optionally filtered by a search string.
        """
        registry = self.store.get(self.registry_path, {})
        if search:
            return {name: addr for name, addr in registry.items() if search in name}
        return registry


    def _caddy_sync(self):
        """Trigger Caddy config reload (best-effort)."""
        try:
            m.fn('caddy/sync')()
        except Exception:
            pass

    # --- App Registry (separate Next.js app servers with ownership) ---

    def reg_app(self, name: str, address: str, owner: str = None, port: int = None, path: str = '', api_url: str = None) -> Dict[str, Any]:
        """Register a module app server with its owner. Also marks as installed."""
        registry = self.store.get('app_registry.json', {})
        entry = {'url': address, 'owner': (owner or '').lower()}
        if api_url:
            entry['api_url'] = api_url
        registry[name] = entry
        self.store.put('app_registry.json', registry)
        # Also mark as installed (persists after stop)
        if port is None:
            try:
                port = int(address.split(':')[-1])
            except (ValueError, IndexError):
                port = 0
        self.install_app(name, port=port, owner=owner or '', path=path)
        self._caddy_sync()
        return {'status': 'success', 'name': name, 'address': address, 'owner': owner}

    def dereg_app(self, name: str) -> Dict[str, Any]:
        """Deregister a module app server."""
        registry = self.store.get('app_registry.json', {})
        if name in registry:
            del registry[name]
            self.store.put('app_registry.json', registry)
            self._caddy_sync()
            return {'status': 'success', 'name': name}
        return {'status': 'error', 'name': name, 'error': 'not found'}

    def app_namespace(self, search: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Get registered app servers, optionally filtered by search.
        Returns {name: {url, owner}} for each registered app.
        """
        registry = self.store.get('app_registry.json', {})
        if search:
            registry = {name: data for name, data in registry.items() if search in name}
        return registry

    def app_owner(self, name: str) -> Optional[str]:
        """Get the owner address of a registered app."""
        registry = self.store.get('app_registry.json', {})
        entry = registry.get(name)
        if not entry:
            return None
        if isinstance(entry, dict):
            return entry.get('owner')
        return None

    def is_app_owner(self, name: str, address: str) -> bool:
        """Check if an address is the owner of a registered app."""
        owner = self.app_owner(name)
        if not owner:
            # Check installed apps too
            installed = self.store.get('app_installed.json', {})
            entry = installed.get(name, {})
            owner = entry.get('owner', '')
        if not owner:
            return True  # No owner set — allow (backwards compat)
        return address.lower() == owner.lower()

    # --- Installed Apps (persists even when stopped) ---

    def install_app(self, name: str, port: int, owner: str = '', path: str = '') -> Dict[str, Any]:
        """Register a module app as installed (persists across start/stop)."""
        installed = self.store.get('app_installed.json', {})
        installed[name] = {
            'port': port,
            'owner': (owner or '').lower(),
            'path': path,
        }
        self.store.put('app_installed.json', installed)
        return {'status': 'success', 'name': name}

    def uninstall_app(self, name: str) -> Dict[str, Any]:
        """Remove a module app from the installed list."""
        installed = self.store.get('app_installed.json', {})
        if name in installed:
            del installed[name]
            self.store.put('app_installed.json', installed)
            return {'status': 'success', 'name': name}
        return {'status': 'error', 'name': name, 'error': 'not found'}

    def app_status(self, owner: str = None, **kwargs) -> Dict[str, Any]:
        """Get all module apps with their running status.
        Returns {name: {port, owner, path, running, url}} for each app.
        """
        installed = self.store.get('app_installed.json', {})
        running = self.store.get('app_registry.json', {})
        result = {}

        # Merge installed + running
        all_names = set(list(installed.keys()) + list(running.keys()))
        for name in all_names:
            inst = installed.get(name, {})
            run = running.get(name, {})
            run_url = run.get('url', '') if isinstance(run, dict) else (run if isinstance(run, str) else '')
            entry_owner = (inst.get('owner', '') or (run.get('owner', '') if isinstance(run, dict) else '')).lower()
            entry = {
                'port': inst.get('port', 0),
                'owner': entry_owner,
                'path': inst.get('path', ''),
                'running': name in running,
                'url': run_url,
            }
            if owner and entry['owner'] and entry['owner'] != owner.lower():
                continue
            result[name] = entry

        return result

    def reset(self) -> Dict[str, Any]:
        """
        Reset the registry by clearing all entries.
        """
        self.store.put(self.registry_path, {})
        self.store.put('app_registry.json', {})
        self.store.put('app_installed.json', {})
        return {'status': 'success', 'message': 'registry reset'}


    def test(self):
        self.reg('test_server', 'http://localhost:8000')
        assert self.exists('test_server')
        self.dereg('test_server')
        assert not self.exists('test_server')
        return {'status': 'all tests passed'}
