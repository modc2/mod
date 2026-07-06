from typing import *
from flask import Flask, request, Response, abort
from flask_cors import CORS
import os
import hashlib
import pandas as pd
import json
import re
import subprocess
import inspect
from pathlib import Path
from functools import partial
import time
import mod as m
print = m.print

# ── Security constants ──
MAX_REQUEST_BODY = 10 * 1024 * 1024  # 10 MB max request body
FN_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_/.-]{0,200}$')  # whitelist for fn paths

class Server:
    
    tag_divider = '::' # the divider to use for tags in mod names, e.g. 'mod::tag1' and 'mod::tag2' will be two servers with the same mod but different tags
    # helper functions that are always exposed are always exposed [WARNING: HELPER FNS SHOULD BE CAREFULLY CHOSEN TO AVOID SECURITY RISKS]
    
    def __init__(
        self,
        path = '~/.mod/server', # the path to store the server data
        pm = 'pm.pm2', # the process manager to use
        registry = 'server.namespace',
        timeout = 300):
        self.store = m.mod('store')(path)
        self.pm = self.set_pm(pm)
        self.registry = m.mod(registry)()
        self.timeout = timeout

    def kill(self, name):
        matches = [s for s in self.servers() if s.startswith(name)]
        if not matches:
            matches = [name]
        killed = []
        for s in matches:
            self.pm.kill(s)
            self.registry.dereg(s)
            killed.append(s)
        self._sync_routy()
        return {'status': 'killed', 'killed': killed}
    
    def kill_all(self):
        servers = self.servers()
        for server in servers:
            self.kill(server)
        return {'status': 'killed all', 'servers': servers}
    
    killall = kill_all
    stop = kill
    
    
    def namespace(self, search: Optional[str] = None,  **kwargs) -> Dict[str, str]:
        """
        Get the namespace of registered servers, optionally filtered by a search string.
        """
        return self.registry.namespace(search=search, **kwargs)

    def set_pm(self,  pm: str):
        if pm != None:
            self.pm = m.mod(pm)()
        return self.pm

    def logs(self, name: str, pm=None, **kwargs) -> str:
        self.set_pm(pm)
        return self.pm.logs(name, **kwargs)

    def forward(self, **request: dict):
        """
        runt the function
        """
        return self.gate.forward(**request)

    def get_port(self, port:Optional[int]=None, mod:Union[str, 'Module', Any]=None) -> int:
        if port == None: 
            config = m.config(mod)
            if config != None and 'port' in config:
                port = config['port']
            else:
                port  = mod.port if hasattr(mod, 'port') else None
        port = port or m.free_port()
        return port

    def servers(self, search=None,  **kwargs) -> List[str]:
        return list(self.namespace(search=search, **kwargs).keys())

    def urls(self, search=None,  **kwargs) -> List[str]:
        return list(self.namespace(search=search, **kwargs).values())   

    def mods(self, 
                search=None, 
                max_age=None, 
                update=False, 
                features=['name', 'url', 'key'], 
                timeout=24, 
                path = 'mods.json',
                **kwargs):

        def module_filter(m: dict) -> bool:
            """Filter function to gate if a mod contains the required features."""
            return isinstance(m, dict) and all(feature in m for feature in features )    
        mods = self.store.get(path, None, max_age=max_age, update=update)
        if mods == None :
            names = list(self.namespace().keys())
            print(f'Fetching info for servers: {names}', color='green')
            futures  = [m.submit(m.call, {"fn":name + '/info'}, timeout=timeout, mode='thread') for name in names]
            mods =  m.wait(futures, timeout=timeout)
            mods = list(filter(module_filter, mods))
            self.store.put(path, mods)
        else:
            mods = list(filter(module_filter, mods))

        if search != None:
            mods = [m for m in mods if search in m['name']]
       
        return mods

    def n(self, search=None, **kwargs):
        return len(self.mods(search=search, **kwargs))

    def exists(self, name:str, **kwargs) -> bool:
        """gate if the server exists"""
        return bool(name in self.servers(**kwargs))

    def call(self, fn , params=None, **kwargs): 
        return self.fn('client/forward')(fn, params, **kwargs)

    def wait_for_server(self, name:str, max_time:int=10, trial_backoff:int=0.5, network:str='local',  max_age:int=20):
        # wait for the server to start
        t0 = m.time()
        while m.time() - t0 < max_time:
            namespace = self.namespace(network=network)
            if name in namespace:
                try:
                    return  m.fn('client/call')(namespace[name]+'/info')
                except Exception as e:
                    print(f'Error calling server {name} at {namespace[name]}: {m.detailed_error(e)}', color='red')
            m.sleep(trial_backoff)
        raise Exception(f'Failed to start {name} after {max_time}s')

    def prepare_server(self, mod, fn_options = ['ensure_env']):
        mod_obj = m.mod(mod)
        for fn in fn_options:
            if hasattr(mod_obj, fn):
                print(f'Preparing server: running {fn}()', color='green')
                getattr(mod_obj, fn)()
                break
        return True
    
    def set_mod(self, name, mod):
        if mod == None:
            return
        setattr(self, name, mod)

    def _serve_deps(self, mod):
        """Start dependency modules listed in config.json 'deps' field."""
        config = m.config(mod) or {}
        deps = config.get('deps', [])
        if not deps:
            return
        ns = self.namespace()
        for dep in deps:
            if dep in ns:
                print(f'Dep {dep} already running', color='cyan')
                continue
            print(f'Starting dep: {dep}', color='green')
            dep_port = self.get_port(None, mod=dep)
            self.pm.forward(mod=dep, port=dep_port)
        # Wait for deps to be reachable
        for dep in deps:
            if dep not in ns:
                try:
                    self.wait_for_server(dep, max_time=15)
                    print(f'Dep {dep} ready', color='green')
                except Exception as e:
                    print(f'Dep {dep} failed to start: {e}', color='red')

    def _sync_routy(self):
        """Non-blocking sync to routy gateway (if running)."""
        import threading
        def _do():
            try:
                import requests as _req
                ns = self.registry.namespace() or {}
                store = m.mod('store')('~/.mod/server/registry')
                app_reg = store.get('app_registry.json', {}) or {}
                sync_data = {'apps': [], 'apis': []}
                for name, url in ns.items():
                    sync_data['apis'].append({
                        'name': name,
                        'target_url': url.replace('0.0.0.0', '127.0.0.1'),
                    })
                for name, info in app_reg.items():
                    if isinstance(info, dict) and 'url' in info:
                        sync_data['apps'].append({
                            'name': name,
                            'target_url': info['url'],
                        })
                _req.post('http://localhost:3001/_api/sync', json=sync_data, timeout=3)
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True).start()

    def _find_app_dir(self, mod_dir: Path) -> 'Path | None':
        """Find the Next.js app directory within a module (checks app/, src/app/)."""
        for candidate in [mod_dir / 'app', mod_dir / 'src' / 'app']:
            if candidate.is_dir() and (candidate / 'package.json').exists():
                return candidate
        return None

    def _persist_urls(self, mod: str, port: int, app_port: 'int | None' = None):
        """Write the live api/app urls back to the module's config.json.

        Routy and the caddy generator both scan config.json for `urls.api` /
        `urls.app`, so persisting here means a freshly served module is
        automatically reachable through the gateway (and inherits TLS via
        Caddy) without any per-module wiring.
        """
        try:
            mod_dir = Path(m.dirpath(mod))
            cfg_path = mod_dir / 'config.json'
            if not cfg_path.exists():
                return
            try:
                cfg = json.loads(cfg_path.read_text())
            except (json.JSONDecodeError, OSError):
                return
            if not isinstance(cfg, dict):
                return
            urls = cfg.get('urls') if isinstance(cfg.get('urls'), dict) else {}
            urls['api'] = f'http://localhost:{port}'
            if app_port:
                urls['app'] = f'http://localhost:{app_port}'
            cfg['urls'] = urls
            cfg['port'] = port
            if app_port:
                cfg['app_port'] = app_port
            cfg_path.write_text(json.dumps(cfg, indent=2))
        except Exception as e:
            print(f'Could not persist urls to {mod}/config.json: {e}', color='yellow')

    def get_fns(self, mod):
        config = m.config(mod)
        if config != None and 'fns' in config:
            return config['fns']
        mod_obj = m.mod(mod)
        fns = [fn for fn in dir(mod_obj) if not fn.startswith('_') and callable(getattr(mod_obj, fn))]
        return fns

    def serve(self,
              mod: Union[str, 'Module', Any] = None, # the mod in either a string
              params:Optional[dict] = None,  # kwargs for the mod
              port :Optional[int] = None, # name of the server if None, it will be the mod name
              fns = None, # list of fns to serve, if none, it will be the endpoints of the mod
              key = None, # the key for the server
              remote = False, # whether to run the server remotely
              pm = None,
              run_mode:str='flask',
              paywall = None, # optional x402 payment gate instance
              sandbox:str = 'docker', # 'docker' (default), 'pool' for persistent workers, or 'subprocess' for one-shot
              **extra_params

              ):

        # Parse suffix: bridge.app → serve API + Next.js, bridge.api → API only
        # No suffix → auto-detect: serve app if app/ dir exists
        serve_app = None  # None = auto-detect
        original_mod = mod
        if mod and '.' in mod:
            base, suffix = mod.rsplit('.', 1)
            if suffix in ('app', 'api'):
                serve_app = suffix == 'app'
                mod = base

        mod_obj = m.mod(mod)()
        # Delegate only to modules that define their OWN serve (e.g. bridge,
        # polymarket). The base Mod is the generic dispatcher — calling its
        # serve() here would re-enter with the default mod='app' and recurse.
        # Aliases like 'api' resolve to the base Mod, so check the resolved
        # type rather than the (alias) name.
        is_base_mod = type(mod_obj) is m.Mod
        if not is_base_mod and 'serve' in type(mod_obj).__dict__:
            print(f'Mod {mod} has its own serve function, using it to serve the mod', color='green')
            return mod_obj.serve()
        self.set_pm(pm)
        mod = mod or m.name
        port = self.get_port(port, mod=mod)
        params = {**(params or {}), **extra_params}
        if remote:
            # Preserve .app/.api suffix so the spawned process knows what to serve
            return self.pm.forward(mod=original_mod or mod, params=params, port=port, key=key)

        # ── Start dependency modules ──
        self._serve_deps(mod)

        name = mod
        if  self.tag_divider in mod:
            mod = mod.split(self.tag_divider)[0]
    
        self.mod = m.mod(mod)(**(params or {}))
        self.key = m.key(key)
        fns = fns or self.get_fns(mod)
        
        def get_info(mod, **kwargs):
            info = m.info(mod, **kwargs)
            info['key'] = self.key.address
            info['fns'] = fns
            return info
        
        self.mod.info = get_info(mod)
        self.gate = m.mod('gate')(mod=self.mod, paywall=paywall, sandbox=sandbox)
        self.app = Flask(__name__)
        self.app.config['MAX_CONTENT_LENGTH'] = MAX_REQUEST_BODY

        # CORS: restrict to known origins; override with MOD_CORS_ORIGINS env var
        allowed_origins = os.environ.get('MOD_CORS_ORIGINS', 'http://localhost:*,http://127.0.0.1:*,https://modc2.com').split(',')
        CORS(self.app, resources={r"/*": {
            "origins": allowed_origins,
            "methods": ["POST", "OPTIONS"],
            "allow_headers": ["Content-Type", "Accept", "token", "Authorization"],
            "expose_headers": ["Content-Type"],
            "max_age": 3600
        }})

        @self.app.route("/mod/<name>/<path:fn>", methods=['POST'])
        def mod_fn(name, fn):
            """Route: {url}/mod/{name}/{fn} -> gate.forward('{name}/{fn}')"""
            full_fn = f'{name}/{fn}'
            if not FN_NAME_RE.match(full_fn) or '..' in full_fn:
                return {'error': 'Invalid function name'}, 400
            try:
                headers = dict(request.headers)
                params = request.get_json(silent=True) or {}
                if not isinstance(params, dict):
                    return {'error': 'Request body must be a JSON object'}, 400
                result = self.gate.forward(fn=full_fn, headers=headers, params=params, mod=self.mod)
            except AssertionError as e:
                m.print(f'Denied {full_fn}: {e}', color='yellow')
                return {'error': str(e)}, 403
            except Exception as e:
                m.print(f'Error in server function {full_fn}: {m.detailed_error(e)}', color='red')
                result = {'error': 'Internal server error', 'fn': full_fn}
            try:
                json.dumps(result)
            except (TypeError, ValueError):
                result = json.loads(json.dumps(result, default=str))
            return {'result': result}

        @self.app.route("/mod/<name>", methods=['POST'])
        def mod_info(name):
            """Route: {url}/mod/{name} -> gate.forward('{name}/info')"""
            full_fn = f'{name}/info'
            if not FN_NAME_RE.match(full_fn) or '..' in full_fn:
                return {'error': 'Invalid function name'}, 400
            try:
                headers = dict(request.headers)
                params = request.get_json(silent=True) or {}
                if not isinstance(params, dict):
                    return {'error': 'Request body must be a JSON object'}, 400
                result = self.gate.forward(fn=full_fn, headers=headers, params=params, mod=self.mod)
            except AssertionError as e:
                m.print(f'Denied {full_fn}: {e}', color='yellow')
                return {'error': str(e)}, 403
            except Exception as e:
                m.print(f'Error in server function {full_fn}: {m.detailed_error(e)}', color='red')
                result = {'error': 'Internal server error', 'fn': full_fn}
            try:
                json.dumps(result)
            except (TypeError, ValueError):
                result = json.loads(json.dumps(result, default=str))
            return {'result': result}

        @self.app.route("/<path:fn>", methods=['POST'])
        def server_fn(fn):
            # ── Validate function name ──
            if not FN_NAME_RE.match(fn) or '..' in fn:
                return {'error': 'Invalid function name'}, 400
            try:
                headers = dict(request.headers)
                params = request.get_json(silent=True) or {}
                if not isinstance(params, dict):
                    return {'error': 'Request body must be a JSON object'}, 400
                result = self.gate.forward(fn=fn, headers=headers, params=params, mod=self.mod)
            except AssertionError as e:
                # Permission / role assertion errors → 403
                m.print(f'Denied {fn}: {e}', color='yellow')
                return {'error': str(e)}, 403
            except Exception as e:
                # Log full error server-side, return sanitized message to client
                m.print(f'Error in server function {fn}: {m.detailed_error(e)}', color='red')
                result = {'error': 'Internal server error', 'fn': fn}
            # Ensure result is JSON-serializable
            try:
                json.dumps(result)
            except (TypeError, ValueError):
                result = json.loads(json.dumps(result, default=str))
            return {'result': result}

        # ── Auto-serve Next.js app if .app suffix or app/ dir detected ──
        mod_dir = Path(m.dirpath(mod))
        app_dir = self._find_app_dir(mod_dir)
        has_app = app_dir and (app_dir / 'package.json').exists()
        if serve_app is None:
            serve_app = has_app
        app_port = None
        if serve_app and has_app:
            config = m.config(mod) or {}
            app_port = int(config.get('app_port', 0)) or (port + 1)
            log_dir = Path(f'/tmp/{name}')
            log_dir.mkdir(parents=True, exist_ok=True)
            app_env = os.environ.copy()
            # API_URL_INTERNAL: used by Next.js middleware to proxy /api/* to Flask
            prod_api_url = os.environ.get('MOD_API_URL')
            if prod_api_url:
                app_env['API_URL_INTERNAL'] = f'{prod_api_url}/{name}'
            else:
                app_env['API_URL_INTERNAL'] = f'http://localhost:{port}'
            app_env['PORT'] = str(app_port)
            # basePath so the app routes behind /{name}
            app_env['NEXT_PUBLIC_BASE_PATH'] = f'/{name}'
            app_log = open(log_dir / 'app.log', 'w')
            app_cmd = ['npx', 'next', 'dev', '-p', str(app_port)]
            subprocess.Popen(app_cmd, cwd=str(app_dir), env=app_env,
                             stdout=app_log, stderr=subprocess.STDOUT)
            print(f'App started at http://localhost:{app_port}/{name} (log: {log_dir}/app.log)', color='green')
            # Register app in namespace so the gateway middleware can route to it
            self.registry.reg_app(name, f'http://localhost:{app_port}',
                                  owner=self.key.address, api_url=f'http://localhost:{port}')

        self.registry.reg(name, f'http://0.0.0.0:{port}')
        # Persist urls.api / urls.app to the module config so the caddy
        # generator and routy worker auto-discover this module on their next
        # scan — no per-module Caddy or routy wiring needed.
        self._persist_urls(mod, port, app_port)
        self._sync_routy()
        if run_mode == 'flask':
            self.app.run(
                host="0.0.0.0",
                port=port,
                debug=False,
            )
        else:
            raise Exception(f'Unknown run_mode: {run_mode}. Only "flask" is supported.')

    start = serve
