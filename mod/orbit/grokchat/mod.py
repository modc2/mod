"""grokchat — a Next.js chat app and a Python API over orbit/grokbot.

grokbot already turned the xAI API into a fleet module: identity, keys, bots,
one port. This module is the front door for humans who just want to chat —
a Next.js app at /grokchat and a stdlib Python API on :50930 that speaks to
grokbot on the caller's behalf, forwarding their token and BYOK key untouched.

    m grokchat/serve          # api on :50930 + next app on :3930
    m grokchat/status         # both processes + grokbot reachability
    m grokchat/build          # rebuild the next app
    m grokchat/kill
"""

import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, 'app')
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    grokchat — a Next.js chat app (:3930, /grokchat) plus a Python proxy API
    (:50930) that connects to orbit/grokbot. BYOK: paste an xAI key in the app
    or sign in with a wallet; either way grokbot resolves the key and this
    module never stores anything.
    """

    def __init__(self, port=None, app_port=None, **kwargs):
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50930))
        self.app_port = int(app_port or cfg.get('app_port', 3930))
        self.base = cfg.get('base_path', '/grokchat')

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route the API serves."""
        import api
        return api.info()

    forward = info

    # ── serve ────────────────────────────────────────────────────

    def build(self, **kwargs):
        """`next build` the app. Run once before the first serve."""
        r = subprocess.run(['npm', 'run', 'build'], cwd=APP,
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-3000:]}

    def serve(self, port=None, app_port=None, background=True, **kwargs):
        """Run both halves under pm2: grokchat-api and grokchat-app."""
        port = int(port or self.port)
        app_port = int(app_port or self.app_port)
        if not background:
            import api
            return api.serve(port)
        self.kill()
        subprocess.run(['pm2', 'start', sys.executable, '--name', 'grokchat-api',
                        '--cwd', HERE, '--', os.path.join(HERE, 'api.py'),
                        '--port', str(port)],
                       cwd=HERE, capture_output=True)
        if not os.path.isdir(os.path.join(APP, '.next')):
            self.build()
        subprocess.run(['pm2', 'start', 'npm', '--name', 'grokchat-app',
                        '--cwd', APP, '--', 'start'],
                       cwd=APP, capture_output=True)
        return {'api': f'http://localhost:{port}',
                'app': f'http://localhost:{app_port}{self.base}',
                'processes': ['grokchat-api', 'grokchat-app']}

    def kill(self, **kwargs):
        """Stop both processes."""
        killed = []
        for name in ('grokchat-api', 'grokchat-app'):
            r = subprocess.run(['pm2', 'delete', name], capture_output=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    def status(self, **kwargs):
        """Own health plus grokbot reachability, as the API sees it."""
        try:
            with urllib.request.urlopen(
                    f'http://localhost:{self.port}/health', timeout=12) as r:
                return json.loads(r.read())
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}'}

    def test(self, **kwargs):
        """Smoke test: API info + health + the app answering on its port."""
        out = {'api': None, 'app': None}
        try:
            with urllib.request.urlopen(
                    f'http://localhost:{self.port}/', timeout=8) as r:
                out['api'] = json.loads(r.read()).get('name') == 'grokchat'
        except Exception as e:
            out['api'] = f'{type(e).__name__}: {e}'
        try:
            with urllib.request.urlopen(
                    f'http://localhost:{self.app_port}{self.base}',
                    timeout=12) as r:
                out['app'] = r.status == 200
        except Exception as e:
            out['app'] = f'{type(e).__name__}: {e}'
        out['ok'] = out['api'] is True and out['app'] is True
        return out

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()
