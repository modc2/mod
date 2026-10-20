"""A build-console background agent as its own module.

The agent's loop runs inside orbit/build; this module is its card and its
app. ``m <name>`` returns what it is; ``m <name>/status`` reads the agent's
live state from build; ``m <name>/serve`` runs the app.
"""

import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)


def _cfg():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


class Mod:
    description = _cfg().get('description', '')

    def __init__(self, port=None, **kwargs):
        cfg = _cfg()
        self.name = cfg.get('name') or os.path.basename(HERE)
        self.port = int(port or os.environ.get('PORT') or cfg.get('port'))
        self.agent = cfg.get('agent', {})

    def forward(self):
        return self.info()

    def info(self):
        """What this agent is and where its app lives."""
        import serve
        out = serve.info()
        out['app'] = f'http://localhost:{self.port}/{self.name}/'
        return out

    def status(self):
        """The agent's live state, straight from build's public read."""
        path = self.agent.get('status')
        if not path:
            return {'error': 'this agent has no public status read — open the app'}
        url = 'http://127.0.0.1:8890' + path
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return json.loads(r.read())
        except Exception as e:
            return {'error': f'build unreachable: {e}'}

    def serve(self, port=None, background=False):
        """Run the app."""
        port = int(port or self.port)
        if not background:
            import serve as srv
            return srv.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'serve.py'), '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=HERE)
        return {'pid': proc.pid, 'port': port, 'app': f'http://localhost:{port}/{self.name}/'}

    def kill(self, port=None):
        """Stop whatever holds this module's port (by port, never by name)."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': pids}
