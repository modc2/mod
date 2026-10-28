"""traffic — how many people are using each app right now, and over time.

    m traffic                    # info
    m traffic/live               # who is on which app (last 5 min)
    m traffic/series range=7d    # users per bucket per app
    m traffic/serve              # page + API on :51540  (modc2.com/traffic/)
    m traffic/test

Reads Caddy's JSON access log (the caddy module writes it, see its `log`
block). Nothing but an anonymous visitor hash, app and minute is stored.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = "Live users per app across the router, and users over time per app."

    def __init__(self, port=None, **kwargs):
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51540))
        self._store = None

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    @property
    def store(self):
        if self._store is None:
            import collector
            self._store = collector.Store()
            collector.Collector(self._store).step()
        return self._store

    def info(self):
        cfg = self.config()
        return {'name': 'traffic', 'description': cfg.get('description'),
                'app': f'http://localhost:{self.port}/traffic/', 'endpoints': cfg.get('endpoints')}

    forward = info

    def live(self, window=300):
        import collector
        return collector.live(self.store, int(window))

    def series(self, range='24h', top=8, apps=None):
        import collector
        return collector.series(self.store, range, int(top), apps)

    def sparks(self, range='24h'):
        import collector
        return collector.app_series(self.store, range)

    def serve(self, port=None):
        import serve as srv
        return srv.serve(int(port or self.port))

    def kill(self, port=None):
        """Stop whatever holds the port — by port, never by name."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} -sTCP:LISTEN || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': pids}

    def test(self):
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests'],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
