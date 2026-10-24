"""Server for one of the build console's background agents, as its own app.

The agent itself lives inside orbit/build (its loop, its state, its gates).
This process only serves the console for it and says where build is — the
page talks to build's API directly with the browser's build session, so
every write is gated by build exactly as it is in the AGENTS tab. Nothing
here holds a credential or proxies one.

    /<name>/*    → the console (prefix kept by the gateway; stripped here)
    /info        → what this is + where build is (also /<name>/info)
    /health      → liveness

    python3 serve.py [--port N] [--host 0.0.0.0]
"""

import argparse
import json
import os
import subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
BUILD_CONFIG = os.path.join(MODULE_DIR, '..', 'build', 'config.json')


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


CFG = _load(os.path.join(MODULE_DIR, 'config.json'))
NAME = CFG.get('name') or os.path.basename(MODULE_DIR)
PREFIX = '/' + NAME


def info():
    b = _load(BUILD_CONFIG)
    return {
        'name': NAME,
        'title': CFG.get('title', NAME),
        'description': CFG.get('description', ''),
        'agent': CFG.get('agent', {}),
        'build': {
            'owner': b.get('owner', ''),
            'port': b.get('port', 8890),
            'app': '/build/',
            'api': '/api/build',
        },
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _diff(self):
        qs = parse_qs(urlparse(self.path).query)
        module = (qs.get('module') or [''])[0]
        since = (qs.get('since') or ['0'])[0]
        if not module or '/' in module or module.startswith('.'):
            return self._json({'error': 'invalid module'}, 400)
        orbit_dir = os.path.realpath(os.path.join(MODULE_DIR, '..'))
        mod_path = os.path.realpath(os.path.join(orbit_dir, module))
        if not (mod_path == orbit_dir or mod_path.startswith(orbit_dir + os.sep)):
            return self._json({'error': 'invalid module'}, 400)
        if not os.path.isdir(mod_path):
            return self._json({'error': 'module not found'}, 404)
        try:
            since_ts = int(float(since)) if since and since != '0' else 0
            since_arg = f'@{since_ts}'
            diff_out = subprocess.run(
                ['git', '-C', mod_path, 'diff', 'HEAD'],
                capture_output=True, text=True, timeout=10
            ).stdout
            log_out = subprocess.run(
                ['git', '-C', mod_path, 'log', '--oneline', f'--since={since_arg}'],
                capture_output=True, text=True, timeout=10
            ).stdout
        except Exception as e:
            return self._json({'error': str(e)}, 500)
        return self._json({'diff': diff_out, 'commits': log_out})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == PREFIX:
            self.send_response(308)
            self.send_header('Location', PREFIX + '/')
            self.end_headers()
            return
        if path.startswith(PREFIX + '/'):
            path = path[len(PREFIX):]
        elif path.startswith('/api' + PREFIX):
            path = path[len('/api' + PREFIX):] or '/'
        if path in ('/info', '/api/info'):
            return self._json(info())
        if path in ('/health', '/api/health'):
            return self._json({'ok': True, 'name': NAME})
        if path in ('/diff', '/api/diff'):
            return self._diff()
        self.path = path
        return super().do_GET()

    do_HEAD = do_GET


def serve(port=None, host='0.0.0.0'):
    port = int(port or CFG.get('port'))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'{NAME} on http://{host}:{port}{PREFIX}/', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
