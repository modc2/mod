"""
Basebuilder — forkable module template with environment management,
health checks, and CID registry tracking.

Fork this module to create new app-based modules:
    1. Copy basebuilder/ → yourmod/
    2. Rename yourmod/basebuilder/ → yourmod/yourmod/
    3. Update config.json (name, port, app_port)
    4. Edit app/ for your UI
    5. Extend Mod class in yourmod/mod.py

All forked modules inherit: ensure_env, serve with liveness checks,
CID snapshotting, and registry integration.
"""

import os
import json
import time
import subprocess
import urllib.request
import hashlib
from pathlib import Path
import mod as m


class Mod:
    description = "Forkable module template with env management, health checks, and CID tracking"

    def __init__(self, key=None, **kwargs):
        self.key = m.key(key)
        self._cfg = None

    # ── config ────────────────────────────────────────────────────

    def _module_dir(self) -> str:
        return str(Path(__file__).parent.parent)

    @property
    def config(self):
        if self._cfg is None:
            self._cfg = self._load_config()
        return self._cfg

    @property
    def name(self):
        return self.config.get('name', Path(self._module_dir()).name)

    @property
    def port(self):
        return self.config.get('port', 50200)

    @property
    def app_port(self):
        return self.config.get('app_port', self.port + 1)

    def _load_config(self) -> dict:
        p = os.path.join(self._module_dir(), 'config.json')
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
        return {}

    def _save_config(self, cfg: dict):
        p = os.path.join(self._module_dir(), 'config.json')
        with open(p, 'w') as f:
            json.dump(cfg, f, indent=4)
        self._cfg = cfg

    # ── environment ───────────────────────────────────────────────

    def _find_app_dir(self) -> 'Path | None':
        mod_dir = Path(self._module_dir())
        for candidate in [mod_dir / 'app', mod_dir / 'src' / 'app']:
            if candidate.is_dir() and (candidate / 'package.json').exists():
                return candidate
        return None

    def ensure_env(self):
        """Ensure runtime dependencies are installed.

        Checks node_modules for the Next.js app and installs if missing.
        Override in subclasses to add API/backend dependency checks.
        """
        results = {}
        app_dir = self._find_app_dir()
        if app_dir and (app_dir / 'package.json').exists():
            if not (app_dir / 'node_modules').is_dir():
                print(f'[{self.name}] node_modules missing — running npm install')
                r = subprocess.run(
                    ['npm', 'install'], cwd=str(app_dir),
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode != 0:
                    results['app_install'] = {'ok': False, 'error': r.stderr[-500:]}
                    print(f'[{self.name}] npm install failed: {r.stderr[-200:]}')
                else:
                    results['app_install'] = {'ok': True}
                    print(f'[{self.name}] npm install done')
            else:
                results['app_install'] = {'ok': True, 'cached': True}
        return results

    # ── liveness ──────────────────────────────────────────────────

    def _check_service(self, url: str, retries: int = 20, interval: float = 0.5) -> bool:
        """Poll a URL until it responds or retries are exhausted."""
        for _ in range(retries):
            try:
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status < 500:
                        return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def _tail_log(self, log_path: str, lines: int = 30) -> str:
        try:
            r = subprocess.run(
                ['tail', '-n', str(lines), log_path],
                capture_output=True, text=True,
            )
            return r.stdout
        except Exception:
            return ''

    def status(self) -> dict:
        """Check if services are running."""
        api_url = f'http://localhost:{self.port}'
        app_url = f'http://localhost:{self.app_port}'
        result = {'online': False, 'services': {}}
        for svc, url in [('api', f'{api_url}/health'), ('app', f'{app_url}/{self.name}')]:
            reachable = self._check_service(url, retries=1, interval=0)
            result['services'][svc] = {'reachable': reachable}
        result['online'] = any(s['reachable'] for s in result['services'].values())
        return result

    # ── CID / registry ────────────────────────────────────────────

    def _collect_sources(self) -> dict:
        """Collect source files for CID snapshot."""
        mod_dir = self._module_dir()
        content = {}
        skip = {'node_modules', '.next', 'target', '__pycache__', '.git', 'venv', '.venv'}
        for ext in ('py', 'json', 'toml', 'ts', 'tsx', 'js', 'css', 'md'):
            for fp in Path(mod_dir).rglob(f'*.{ext}'):
                rel = str(fp.relative_to(mod_dir))
                if any(s in rel for s in skip):
                    continue
                try:
                    content[rel] = fp.read_text()
                except Exception:
                    pass
        return content

    def publish(self, description: str = None) -> dict:
        """Snapshot module sources to IPFS and register CID.

        Returns {cid, files, registered}.
        """
        try:
            ipfs = m.mod('ipfs')()
        except Exception as e:
            return {'error': f'IPFS not available: {e}'}

        sources = self._collect_sources()
        cid = ipfs.put(json.dumps(sources))
        result = {'cid': cid, 'files': len(sources), 'registered': False}

        # Update config schema
        try:
            cfg = self._load_config()
            cfg['schema'] = cid
            self._save_config(cfg)
        except Exception:
            pass

        # Register in the module registry
        try:
            reg = m.mod('registry')()
            urls = cfg.get('urls', {})
            reg.register(self.name, {'schema': cid, 'urls': urls}, storage='ipfs')
            result['registered'] = True
            print(f'[{self.name}] CID registered: {cid[:16]}...')
        except Exception as e:
            print(f'[{self.name}] registry update skipped: {e}')

        return result

    # ── serve ─────────────────────────────────────────────────────

    def serve(self, port=None, app_port=None, dev=True):
        """Start the module's Next.js app with env checks and liveness verification.

        Override _start_api() in subclasses to add a backend API server.
        """
        port = int(port or self.port)
        app_port = int(app_port or self.app_port)
        name = self.name
        log_dir = Path(f'/tmp/{name}')
        log_dir.mkdir(parents=True, exist_ok=True)
        results = {}

        api_url = f'http://localhost:{port}'
        app_url = f'http://localhost:{app_port}'
        app_dir = self._find_app_dir()

        # ── Ensure environment ──
        env_result = self.ensure_env()
        results['env'] = env_result

        # ── Optional API (subclass hook) ──
        api_started = self._start_api(port, log_dir)
        if api_started:
            results['api'] = api_url

        # ── App (Next.js) ──
        if app_dir and (app_dir / 'package.json').exists():
            app_env = os.environ.copy()
            app_env['NEXT_PUBLIC_BASE_PATH'] = f'/{name}'
            app_env['NEXT_PUBLIC_API_URL'] = api_url
            app_env['PORT'] = str(app_port)
            app_log = open(log_dir / 'app.log', 'w')
            app_cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
            subprocess.Popen(
                app_cmd, cwd=str(app_dir), env=app_env,
                stdout=app_log, stderr=subprocess.STDOUT,
            )
            results['app'] = app_url
            results['app_log'] = str(log_dir / 'app.log')

        # ── Verify liveness ──
        checks = {}
        if 'app' in results:
            live = self._check_service(f'{app_url}/{name}')
            checks['app'] = {'live': live}
            if not live:
                tail = self._tail_log(str(log_dir / 'app.log'))
                checks['app']['error'] = tail
                print(f'[{name}] App failed to start on :{app_port}')
                # Auto-recover: reinstall + restart once
                if env_result.get('app_install', {}).get('cached'):
                    print(f'[{name}] retrying — reinstalling node_modules')
                    import shutil
                    nm = app_dir / 'node_modules'
                    if nm.is_dir():
                        shutil.rmtree(str(nm), ignore_errors=True)
                    self.ensure_env()
                    app_log2 = open(log_dir / 'app.log', 'w')
                    subprocess.Popen(
                        app_cmd, cwd=str(app_dir), env=app_env,
                        stdout=app_log2, stderr=subprocess.STDOUT,
                    )
                    live = self._check_service(f'{app_url}/{name}')
                    checks['app']['retry'] = True
                    checks['app']['live'] = live
                    if live:
                        print(f'[{name}] App recovered on :{app_port}')
                    else:
                        checks['app']['error'] = self._tail_log(str(log_dir / 'app.log'))
                        print(f'[{name}] App still failing after retry')
            else:
                print(f'[{name}] App live on :{app_port}')

        if 'api' in results:
            live = self._check_service(f'{api_url}/health')
            checks['api'] = {'live': live}
            if not live:
                checks['api']['error'] = self._tail_log(str(log_dir / 'api.log'))
                print(f'[{name}] API failed to start on :{port}')
            else:
                print(f'[{name}] API live on :{port}')

        results['checks'] = checks

        # ── Register in namespace ──
        try:
            ns = m.mod('server.namespace')()
            ns.reg_app(name, app_url, owner=self.key.address, api_url=api_url)
        except Exception as e:
            print(f'[{name}] namespace registration failed: {e}')

        # ── Publish CID if all services live ──
        all_live = all(c.get('live') for c in checks.values())
        if all_live:
            pub = self.publish(description='deploy')
            results['cid'] = pub.get('cid')

        # ── Save urls to config ──
        try:
            cfg = self._load_config()
            cfg.setdefault('urls', {})
            cfg['urls']['api'] = api_url
            cfg['urls']['app'] = app_url
            self._save_config(cfg)
        except Exception:
            pass

        results['logs'] = str(log_dir)
        return results

    def _start_api(self, port: int, log_dir: Path) -> bool:
        """Hook for subclasses to start a backend API server.

        Override this to start Flask, FastAPI, Rust, etc.
        Return True if an API was started, False otherwise.
        """
        return False

    def kill(self):
        """Stop all services on configured ports."""
        import signal
        for p in [self.port, self.app_port]:
            try:
                r = subprocess.run(['lsof', '-ti', f':{p}'], capture_output=True, text=True)
                for pid in r.stdout.strip().split('\n'):
                    if pid.strip():
                        os.kill(int(pid), signal.SIGTERM)
            except Exception:
                pass
        # Deregister
        try:
            ns = m.mod('server.namespace')()
            ns.dereg_app(self.name)
        except Exception:
            pass
        return {'status': 'killed', 'name': self.name}

    # ── info ──────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            'name': self.name,
            'description': self.description,
            'port': self.port,
            'app_port': self.app_port,
            'schema': self.config.get('schema'),
        }

    def __repr__(self):
        return f'<{self.name} port={self.port} app_port={self.app_port}>'
