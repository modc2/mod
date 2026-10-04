"""Server for the judge console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

    /judge/*            → the console (prefix kept by the gateway; stripped here)
    /judge/api/{fn}     → the API, as the console calls it
    /api/judge/{fn}     → the API (prefix stripped by the gateway)
    /{fn}               → the API, bare, for local curl

Reads are GET with query args. Writes are POST with a JSON body. The panel
params are the multisig itself, so create_panel / update_panel / remove_panel
answer only a loopback caller: anyone you share the console with can submit
inputs for judgment, only this box (the creators' box) can change a panel.

    python3 serve.py [--port 51150] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/judge'
MAX_BODY = 1 * 1024 * 1024

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import panel  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'panels', 'panel', 'verdict', 'verdicts')
WRITE_FNS = ('judge', 'create_panel', 'update_panel', 'remove_panel')
LOCAL_ONLY = ('create_panel', 'update_panel', 'remove_panel')

_book = None


def book():
    global _book
    if _book is None:
        _book = panel.Panels()
    return _book


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def api(fn, a):
    b = book()
    if fn == 'health':
        return {'ok': True, **b.stats()}
    if fn == 'info':
        cfg = _config()
        return {'name': 'judge', 'version': cfg.get('version'),
                'description': cfg.get('description'), 'stats': b.stats(),
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'panels':
        return {'panels': b.list()}
    if fn == 'panel':
        return b.get(a.get('name', ''))
    if fn == 'verdict':
        return b.verdict(int(a.get('id', 0)))
    if fn == 'verdicts':
        return {'verdicts': b.verdicts(a.get('panel', ''),
                                       a.get('limit', 50), a.get('offset', 0))}
    if fn == 'judge':
        return b.judge(a.get('panel', ''), a.get('input', ''))
    if fn == 'create_panel':
        return b.create(a.get('name', ''), a.get('creator', ''),
                        a.get('judges'), a.get('threshold', 60),
                        a.get('min_votes'))
    if fn == 'update_panel':
        return b.update(a.get('name', ''), a.get('creator', ''),
                        a.get('judges'), a.get('threshold'), a.get('min_votes'))
    if fn == 'remove_panel':
        return b.remove(a.get('name', ''), a.get('creator', ''))
    return None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):  # quiet — pm2 keeps the logs
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _fn(self, path, allowed):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in allowed:
                return path[len(pre):]
        return None

    def _run(self, fn, args):
        try:
            out = api(fn, args)
        except (ValueError, KeyError) as e:
            return self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001 — the caller gets the reason
            # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
            return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        return self._json(out)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/')
        fn = self._fn(path, WRITE_FNS)
        if not fn:
            return self._json({'error': 'not found'}, 404)
        # A gateway on this box also connects from loopback, so a forwarded
        # request is treated as remote whatever its socket says.
        local = (self.client_address[0] in ('127.0.0.1', '::1')
                 and not self.headers.get('X-Forwarded-For'))
        if fn in LOCAL_ONLY and not local:
            return self._json({'error': f'{fn} is local-only'}, 403)
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            return self._json({'error': 'body too large'}, 413)
        try:
            args = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._json({'error': 'body must be JSON'}, 400)
        if not isinstance(args, dict):
            return self._json({'error': 'body must be a JSON object'}, 400)
        return self._run(fn, args)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'
        fn = self._fn(path, READ_FNS)
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._run(fn, args)

        # The console. Bare /judge + relative asset paths is the gateway trap.
        if raw == PREFIX:
            self.send_response(301)
            self.send_header('Location', PREFIX + '/')
            self.end_headers()
            return
        if raw.startswith(PREFIX + '/'):
            self.path = self.path[len(PREFIX):]
        if urlparse(self.path).path.rstrip('/') in ('', '/'):
            self.path = '/index.html'
        return super().do_GET()


def serve(port=None, host='0.0.0.0'):
    port = int(port or _config().get('port', 51150))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'judge: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/panels')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
