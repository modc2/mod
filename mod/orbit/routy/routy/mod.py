"""
Routy - Local gateway that routes all mod apps and APIs.

Reads from the mod server namespace registry and proxies:
  /{name}/*       → app servers (keeps path prefix for Next.js basePath)
  /api/{name}/*   → API servers (strips prefix)
"""

import subprocess
import requests
import json
import os
import time
from pathlib import Path


class Mod:
    description = "Local gateway router — syncs from mod namespace, proxies apps + APIs."

    # Class-level config cache
    _config_cache = {}
    _config_cache_time = {}
    _sync_worker_running = False

    def __init__(self):
        self.port = 3000
        self.app_port = 3002
        self.base_url = f"http://localhost:{self.port}"
        self.routy_dir = Path(__file__).parent.parent
        self.app_dir = self.routy_dir / 'app'
        self.log_dir = Path('/tmp/routy')

        # Start config sync worker if not already running
        if not Mod._sync_worker_running:
            Mod._sync_worker_running = True
            import threading
            self._worker_thread = threading.Thread(target=self._sync_worker, daemon=True)
            self._worker_thread.start()

    def forward(self, **kwargs):
        action = kwargs.get('action', 'status')
        if action == 'start':
            return self.start()
        elif action == 'serve':
            return self.serve()
        elif action == 'sync':
            return self.sync()
        elif action == 'list':
            return self.list()
        elif action == 'stats':
            return self.stats()
        elif action == 'kill':
            return self.kill()
        else:
            return self.status()

    # ── Serve (background process: Rust binary + Next.js app) ──

    def serve(self):
        """Start routy as a background service: Rust proxy + Next.js dashboard."""
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Kill existing
        for p in [self.port, self.app_port]:
            subprocess.run(f'lsof -ti:{p} | xargs kill -9', shell=True, capture_output=True)
        time.sleep(0.3)

        # Build Rust binary
        print('Building routy...')
        result = subprocess.run(
            ['cargo', 'build', '--release'],
            cwd=self.routy_dir,
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return {'error': 'Build failed', 'stderr': result.stderr[-500:]}

        # Start Rust binary in background
        binary = self.routy_dir / 'target' / 'release' / 'routy'
        env = os.environ.copy()
        env['ROUTY_PORT'] = str(self.port)
        subprocess.Popen(
            [str(binary)],
            env=env,
            stdout=open(self.log_dir / 'api.log', 'w'),
            stderr=subprocess.STDOUT,
        )
        print(f'Proxy started on :{self.port}')

        # Wait for Rust binary
        for _ in range(30):
            time.sleep(0.2)
            try:
                r = requests.get(f'{self.base_url}/_api/stats', timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                pass

        # Start Next.js app in background
        if (self.app_dir / 'package.json').exists():
            app_env = os.environ.copy()
            app_env['PORT'] = str(self.app_port)
            app_env['NEXT_PUBLIC_BASE_PATH'] = '/routy'
            app_env['ROUTY_API_URL'] = self.base_url
            subprocess.Popen(
                ['npx', 'next', 'dev', '-p', str(self.app_port)],
                cwd=str(self.app_dir),
                env=app_env,
                stdout=open(self.log_dir / 'app.log', 'w'),
                stderr=subprocess.STDOUT,
            )
            print(f'App started on :{self.app_port}/routy')

            # Register routy app in mod namespace so it persists across syncs
            import mod as _m
            ns = _m.mod('server.namespace')()
            ns.reg_app('routy', f'http://localhost:{self.app_port}',
                       owner='routy', api_url=self.base_url)

        # Sync from namespace (after app registration so routy itself is included)
        synced = self.sync()

        return {
            'status': 'running',
            'proxy': f'http://localhost:{self.port}',
            'app': f'http://localhost:{self.app_port}/routy',
            'synced': synced,
        }

    def start(self, build=True):
        """Alias for serve."""
        return self.serve()

    # ── Sync ──

    def sync(self, use_cache: bool = True):
        """Pull routes from module configs and the mod namespace, then register in routy.

        Sources, merged in order (later wins):
          1. Module config.json `urls.api` / `urls.app` (declared, always-on routes)
          2. Live `server.namespace` registry (running API servers)
          3. Live `app_registry.json` (running app servers)

        So a module with `urls.api` / `urls.app` in its config is automatically
        routed at `/{name}` and `/api/{name}` even before it is started; once it
        starts, the live URL takes over.
        """
        sync_data = {'apps': {}, 'apis': {}}

        # 1. Config-derived routes — every orbit module with urls.api/urls.app.
        for name, cfg in self._scan_configs().items():
            urls = cfg.get('urls') or {}
            api_url = urls.get('api') or cfg.get('api_url')
            app_url = urls.get('app') or cfg.get('app_url')
            st = cfg.get('storage_type')
            cid = cfg.get('schema') or cfg.get('cid')
            if cid and not st:
                if cid.startswith('Qm'):
                    st = 'ipfs'
                elif cid.startswith('hippius:'):
                    st = 'hippius'
                    cid = cid[len('hippius:'):]

            if api_url:
                sync_data['apis'][name] = {
                    'name': name,
                    'target_url': api_url.replace('0.0.0.0', '127.0.0.1'),
                    'description': f'API: {name}',
                    'storage_type': st,
                    'cid': cid,
                }
            if app_url:
                sync_data['apps'][name] = {
                    'name': name,
                    'target_url': app_url.replace('0.0.0.0', '127.0.0.1'),
                    'description': f'App: {name}',
                    'storage_type': st,
                    'cid': cid,
                }

        # 2/3. Live registries override config-declared targets.
        try:
            import mod as m
            ns = m.mod('server.namespace')()
            api_registry = ns.namespace() or {}
            store = m.mod('store')('~/.mod/server/registry')
            app_registry = store.get('app_registry.json', {}) or {}
        except Exception:
            api_registry, app_registry = {}, {}

        for name, url in api_registry.items():
            entry = sync_data['apis'].get(name, {'name': name, 'description': f'API: {name}'})
            entry['target_url'] = url.replace('0.0.0.0', '127.0.0.1')
            sync_data['apis'][name] = entry

        for name, info in app_registry.items():
            if not (isinstance(info, dict) and 'url' in info):
                continue
            entry = sync_data['apps'].get(name, {'name': name, 'description': f'App: {name}'})
            entry['target_url'] = info['url']
            sync_data['apps'][name] = entry

        payload = {'apps': list(sync_data['apps'].values()),
                   'apis': list(sync_data['apis'].values())}

        try:
            r = requests.post(
                f'{self.base_url}/_api/sync',
                json=payload, timeout=5
            )
            result = r.json()
            apps = len(payload['apps'])
            apis = len(payload['apis'])
            print(f'Synced {apps} apps + {apis} apis')
            return result
        except Exception as e:
            return {'error': str(e)}

    def _orbit_dir(self):
        """Absolute path to the orbit modules directory."""
        return str(self.routy_dir.parent)

    def _scan_configs(self, m=None):
        """Return {name: config_dict} for every orbit module that has a config.json."""
        configs = {}
        orbit_dir = self._orbit_dir()
        if not os.path.isdir(orbit_dir):
            return configs
        for name in os.listdir(orbit_dir):
            cfg_path = os.path.join(orbit_dir, name, 'config.json')
            if not os.path.exists(cfg_path):
                continue
            try:
                with open(cfg_path) as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    configs[name] = cfg
            except Exception:
                continue
        return configs

    def _sync_worker(self):
        """Background worker that scans configs every 1 second and syncs routes.

        Hashes both the live registries AND each module's config.json mtime so a
        config edit (e.g. adding `urls.api`) auto-propagates to routy.
        """
        import hashlib
        import mod as m

        while True:
            try:
                time.sleep(1)

                # Skip if routy isn't running
                try:
                    r = requests.get(f'{self.base_url}/_api/stats', timeout=0.5)
                    if r.status_code != 200:
                        continue
                except Exception:
                    continue

                # Live registries
                try:
                    ns_obj = m.mod('server.namespace')()
                    api_registry = ns_obj.namespace() or {}
                    store = m.mod('store')('~/.mod/server/registry')
                    app_registry = store.get('app_registry.json', {}) or {}
                except Exception:
                    continue

                # Config mtimes — picks up edits to any module's urls.app/urls.api
                config_mtimes = {}
                try:
                    orbit_dir = self._orbit_dir()
                    if os.path.isdir(orbit_dir):
                        for name in os.listdir(orbit_dir):
                            cp = os.path.join(orbit_dir, name, 'config.json')
                            if os.path.exists(cp):
                                config_mtimes[name] = os.path.getmtime(cp)
                except Exception:
                    pass

                state = json.dumps({
                    'apis': sorted(api_registry.items()),
                    'apps': sorted(app_registry.items()),
                    'configs': sorted(config_mtimes.items()),
                }, sort_keys=True)
                state_hash = hashlib.sha256(state.encode()).hexdigest()

                if Mod._config_cache.get('registry_hash') == state_hash:
                    continue

                Mod._config_cache['registry_hash'] = state_hash
                self.sync()

            except Exception:
                pass

    # ── Management ──

    def register(self, name=None, url=None, description=None, website_type='app',
                 storage_type=None, cid=None, **kwargs):
        """Register a single service."""
        if not name or not url:
            return {'error': 'name and url required'}
        try:
            r = requests.post(
                f'{self.base_url}/_api/register',
                json={'name': name, 'target_url': url,
                      'description': description, 'website_type': website_type,
                      'storage_type': storage_type, 'cid': cid},
                timeout=5
            )
            return r.json()
        except Exception as e:
            return {'error': str(e)}

    def list(self):
        """List all registered services."""
        try:
            r = requests.get(f'{self.base_url}/_api/websites', timeout=5)
            data = r.json()
            apps = data.get('apps', [])
            apis = data.get('apis', [])

            if apps:
                print(f'\nApps ({len(apps)}):')
                for w in apps:
                    st = w.get('storage_type') or '-'
                    cid = w.get('cid', '')
                    cid_short = cid[:12] + '...' if cid and len(cid) > 12 else (cid or '-')
                    print(f"  /{w['name']:<20} -> {w['target_url']:<32} [{st}] {cid_short}")

            if apis:
                print(f'\nAPIs ({len(apis)}):')
                for w in apis:
                    st = w.get('storage_type') or '-'
                    cid = w.get('cid', '')
                    cid_short = cid[:12] + '...' if cid and len(cid) > 12 else (cid or '-')
                    print(f"  /api/{w['name']:<16} -> {w['target_url']:<32} [{st}] {cid_short}")

            if not apps and not apis:
                print('No services registered. Run: m routy/sync')

            return data
        except Exception as e:
            return {'error': str(e), 'hint': 'Is routy running? m routy/serve'}

    def stats(self):
        """Get routy stats."""
        try:
            r = requests.get(f'{self.base_url}/_api/stats', timeout=5)
            return r.json()
        except Exception as e:
            return {'error': str(e)}

    def status(self):
        """Check if routy is running."""
        try:
            r = requests.get(f'{self.base_url}/_api/stats', timeout=2)
            if r.status_code == 200:
                return {'status': 'running', 'url': self.base_url, **r.json()}
        except Exception:
            pass
        return {'status': 'not running', 'hint': 'm routy/serve'}

    def kill(self):
        """Stop routy (proxy + app)."""
        for p in [self.port, self.app_port]:
            subprocess.run(f'lsof -ti:{p} | xargs kill -9', shell=True, capture_output=True)
        return {'status': 'killed'}
