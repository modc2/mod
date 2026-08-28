"""
superhood — SUPER HOOD BROS.: WASHINGTON PARK.

An 8-bit side-scrolling platformer set in Brooklyn. Three courses — Fifth
Avenue in Park Slope, the G train tunnel, and Washington Park itself, where
the landlord is waiting. Pigeons, rats and trash-can lids; pizza, egg creams
and MetroCards; subway tokens instead of coins.

The whole game is static: no build step, no assets on disk, no network calls.
Every sprite is drawn from a pixel grid at load time and every sound is
synthesised in WebAudio, so this module only has to serve ``web/``.

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Everything the module exposes to the CLI, the gateway and other
modules is a public method on that class.

CLI:
    m superhood                # null call → info()
    m superhood/play           # serve the game and open a browser
    m superhood/serve          # run it under pm2, then register the route
    m superhood/url            # where it is
    m superhood/levels         # the course list
    m superhood/health         # liveness
    m superhood/readme         # this module's README
    m superhood/kill           # stop it
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import mod as m

MODULE_DIR = Path(__file__).parent

# The orbit loader imports this file by path, so the module directory is not
# necessarily importable. Put it on the path before reaching for our package.
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


class Mod:
    description = ('SUPER HOOD BROS.: WASHINGTON PARK — an 8-bit Brooklyn '
                   'platformer that runs entirely in the browser.')

    # What the HTTP API exposes. Every public method is a function of this
    # module, but the API answers from the public gateway, so it serves only the
    # ones that read: serve/kill/register and friends stay on the CLI.
    API_FNS = ('info', 'health', 'readme', 'url', 'path', 'levels', 'files')

    # The courses, in the order you play them.
    COURSES = [
        ('1-1', 'PARK SLOPE', 'Fifth Avenue: stoops, scaffolding, bodega '
                              'crates, and a subway entrance that goes '
                              'somewhere.'),
        ('1-2', 'G TRAIN', 'Down on the platform and out along the trackbed. '
                           'The rats live here.'),
        ('1-3', 'WASHINGTON PARK', 'Green at last — until the landlord turns '
                                   'up wanting a raise.'),
    ]

    def __init__(self, key='superhood', network='testnet'):
        self.key = m.key(key)
        self.network = network
        self.module_dir = MODULE_DIR
        cfg = self._config()
        self.port = int(cfg.get('port', 50340))
        self.app_port = int(cfg.get('app_port', 50341))

    def _config(self) -> dict:
        try:
            with (MODULE_DIR / 'config.json').open() as fh:
                return json.load(fh)
        except Exception:
            return {}

    # ── module surface ───────────────────────────────────────────────────

    def forward(self, action=None, **kwargs):
        """CLI entry: ``m superhood <action> [args]``."""
        if action is None:
            return self.info()
        fn = getattr(self, str(action), None)
        if not callable(fn) or str(action).startswith('_'):
            return {'error': f'unknown action {action!r}', 'fns': self._fns()}
        return fn(**kwargs)

    def _fns(self) -> List[str]:
        return [k for k in dir(self)
                if not k.startswith('_') and callable(getattr(self, k))]

    def info(self) -> dict:
        cfg = self._config()
        return {
            'name': 'superhood',
            'title': 'SUPER HOOD BROS.: WASHINGTON PARK',
            'description': self.description,
            'version': cfg.get('version', '0.1.0'),
            'network': self.network,
            'app': self.url(),
            'api': self.api_url(),
            'schema': cfg.get('schema'),
            'courses': [f'{w} {n}' for w, n, _ in self.COURSES],
            'controls': {
                'move': 'arrows / WASD',
                'jump': 'Z, K or space',
                'run + throw': 'X, J or shift',
                'duck / enter subway': 'down',
                'start': 'enter', 'pause': 'P', 'mute': 'M',
            },
            'fns': self._fns(),
        }

    def health(self) -> dict:
        return {'ok': True, 'module': 'superhood'}

    def readme(self) -> str:
        path = self.module_dir / 'README.md'
        return path.read_text() if path.exists() else ''

    def url(self, gateway=None) -> str:
        """Where the game lives."""
        base = self._config().get('base_path', '/superhood')
        if gateway:
            return f'{str(gateway).rstrip("/")}{base}'
        return f'http://localhost:{self.app_port}{base}'

    def api_url(self, gateway=None) -> str:
        """Where the module's functions answer: ``/api/superhood``."""
        if gateway:
            return f'{str(gateway).rstrip("/")}/api/superhood'
        return f'http://localhost:{self.port}'

    def path(self) -> str:
        """The directory the game is served from."""
        return str(self.module_dir / 'web')

    def levels(self) -> list:
        """The course list."""
        return [{'world': w, 'name': n, 'about': a} for w, n, a in self.COURSES]

    def files(self) -> dict:
        """Every file that makes up the game, with its size."""
        web = self.module_dir / 'web'
        out = {}
        for p in sorted(web.rglob('*')):
            if p.is_file():
                out[str(p.relative_to(web))] = p.stat().st_size
        return out

    # ── serve / register ─────────────────────────────────────────────────

    def _pm2_start(self, name, cmd, cwd=None, env=None) -> bool:
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name]
        if cwd:
            pm2_cmd += ['--cwd', cwd]
        pm2_cmd += ['--'] + list(cmd[1:])
        r = subprocess.run(pm2_cmd, capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        if r.returncode != 0:
            print(r.stderr[-800:])
        return r.returncode == 0

    def _pm2_kill(self, name) -> bool:
        return subprocess.run(['pm2', 'delete', name],
                              capture_output=True, text=True).returncode == 0

    def serve_app(self, app_port=None) -> dict:
        """Run the static game server under pm2."""
        app_port = int(app_port or self.app_port)
        script = self.module_dir / 'serve.py'
        if not script.exists():
            return {'error': f'{script} not found'}
        env = {'PORT': str(app_port), 'HOST': '0.0.0.0'}
        cmd = ['python3', str(script), '--port', str(app_port), '--host', '0.0.0.0']
        ok = self._pm2_start('superhood.app', cmd, cwd=str(self.module_dir), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'superhood.app',
                'ok': ok}

    # The game is entirely client side, so the API has no work of its own: the
    # static server answers the protocol routes from the same process and port.
    def serve_api(self, **_) -> dict:
        return {'ok': True, 'api': self.api_url(), 'pm2': 'superhood.app',
                'fns': list(self.API_FNS),
                'note': 'served by superhood.app — the game needs no second process'}

    def serve(self, app_port=None, register=True, **_) -> dict:
        """Start the game server under pm2, then register the gateway route."""
        out = {'app': self.serve_app(app_port=app_port)}
        if register:
            out['registration'] = self.register()
        out['url'] = self.url()
        return out

    def play(self, port=None, open=True, background=True) -> str:
        """Serve the game in this process and open it in a browser.

        Handy for a quick local game; ``serve()`` is what you want for the
        fleet, since that one survives the shell exiting.
        """
        import importlib.util
        import threading
        import webbrowser
        from http.server import HTTPServer

        # Load serve.py by path: `serve` is a common enough name that importing
        # it normally would fight with whatever else is in sys.modules.
        spec = importlib.util.spec_from_file_location(
            'superhood_serve', str(self.module_dir / 'serve.py'))
        _serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_serve)

        port = int(port or self.app_port)
        srv = None
        for p in range(port, port + 20):
            try:
                srv = HTTPServer(('0.0.0.0', p), _serve.Handler)
                port = p
                break
            except OSError:
                continue
        if srv is None:
            raise OSError(f'no free port in {port}-{port + 20}')

        url = f'http://localhost:{port}/superhood/'
        print(f'superhood serving at {url}')
        if open:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        if background:
            self._srv = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        else:
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                srv.shutdown()
        return url

    def stop(self) -> dict:
        """Stop a server started by ``play()`` in this process."""
        srv = getattr(self, '_srv', None)
        if srv is None:
            return {'ok': False, 'error': 'nothing running in this process'}
        srv.shutdown()
        srv.server_close()
        self._srv = None
        return {'ok': True}

    def kill(self) -> dict:
        killed = [n for n in ('superhood.api', 'superhood.app')
                  if self._pm2_kill(n)]
        return {'killed': killed}

    def test(self) -> dict:
        """Smoke test: the game files parse and every level builds.

        Runs the level builder under node when it is available, so a typo in
        levels.js is caught here rather than on a black screen.
        """
        web = self.module_dir / 'web'
        missing = [f for f in ('index.html', 'js/sprites.js', 'js/audio.js',
                               'js/levels.js', 'js/game.js')
                   if not (web / f).exists()]
        if missing:
            return {'ok': False, 'missing': missing}

        checks = {'files': True}
        node = subprocess.run(['bash', '-lc', 'command -v node'],
                              capture_output=True, text=True)
        if node.returncode != 0:
            checks['node'] = 'not installed — skipped syntax + level checks'
            return {'ok': True, 'checks': checks}

        script = (
            "const fs=require('fs');"
            "global.window=global;"
            f"eval(fs.readFileSync('{web}/js/levels.js','utf8'));"
            "const out=[];"
            "for (const id of LEVELS.ids()) {"
            "  const L=LEVELS.make(id);"
            "  out.push({id, w:L.w, h:L.h, foes:L.foes.length, "
            "            decor:L.decor.length, goal:L.goalX});"
            "}"
            "console.log(JSON.stringify(out));"
        )
        r = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        if r.returncode != 0:
            return {'ok': False, 'checks': checks, 'error': r.stderr[-600:]}
        checks['levels'] = json.loads(r.stdout.strip())
        for f in ('js/sprites.js', 'js/audio.js', 'js/game.js'):
            s = subprocess.run(['node', '--check', str(web / f)],
                               capture_output=True, text=True)
            checks[f] = 'ok' if s.returncode == 0 else s.stderr[-400:]
        ok = all(v == 'ok' for k, v in checks.items()
                 if k.startswith('js/'))
        return {'ok': ok, 'checks': checks}

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('superhood', app_url)
            ns.reg_app('superhood', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/superhood'
            print(f'superhood registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'superhood: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('superhood')
            return {'ok': True, 'deregistered': 'superhood'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
