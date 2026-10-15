"""Server for the cheapy console: static app + the mod protocol API, one port.

Standalone so pm2 can run it directly (``python3 src/serve.py``).

    /cheapy/*            → the console (prefix kept by the gateway; stripped here)
    /cheapy/api/{fn}     → the API, as the console calls it
    /api/cheapy/{fn}     → the API (prefix stripped by the gateway)
    /{fn}                → the API, bare, for local curl

Everything is a read, so everything is GET with query args (POST with a JSON
body also works). Nothing here writes except the price cache.

    python3 serve.py [--port 51370] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
WEB_DIR = os.path.join(SRC, 'web')
PREFIX = '/cheapy'
MAX_BODY = 1024 * 1024

if SRC not in sys.path:
    sys.path.append(SRC)

import cheapy  # noqa: E402

FNS = ('health', 'info', 'readme', 'cheapest', 'compare', 'model',
       'providers', 'estimate', 'refresh')

_c = None


def core():
    global _c
    if _c is None:
        _c = cheapy.Cheapy()
    return _c


def _config():
    try:
        with open(os.path.join(ROOT, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def api(fn, a):
    c = core()
    if fn == 'health':
        return {'ok': True, **c.stats()}
    if fn == 'info':
        cfg = _config()
        return {'name': 'cheapy', 'version': cfg.get('version'),
                'description': cfg.get('description'), 'stats': c.stats(),
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(ROOT, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'cheapest':
        keys = ('input_tokens', 'output_tokens', 'calls', 'min_context', 'tools',
                'vision', 'reasoning', 'free', 'q', 'provider', 'limit', 'offset')
        return c.cheapest(**{k: a[k] for k in keys if k in a})
    if fn == 'compare':
        keys = ('input_tokens', 'output_tokens', 'calls')
        return c.compare(a.get('ids', ''), **{k: a[k] for k in keys if k in a})
    if fn == 'model':
        return c.model(a.get('id', ''))
    if fn == 'providers':
        return {'providers': c.providers()}
    if fn == 'estimate':
        return cheapy.estimate(a.get('text', ''), a.get('chars'))
    if fn == 'refresh':
        rows, source, at = c.load(refresh=True)
        return {'models': len(rows), 'source': source, 'fetched_at': int(at)}
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

    def _fn(self, path):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in FNS:
                return path[len(pre):]
        return None

    def _run(self, fn, args):
        try:
            out = api(fn, args)
        except (ValueError, KeyError, TypeError) as e:
            return self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001 — the caller gets the reason
            # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
            return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        return self._json(out)

    def do_POST(self):
        fn = self._fn(urlparse(self.path).path.rstrip('/'))
        if not fn:
            return self._json({'error': 'not found'}, 404)
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            return self._json({'error': 'body too large'}, 413)
        try:
            args = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._json({'error': 'body must be JSON'}, 400)
        return self._run(fn, args if isinstance(args, dict) else {})

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        fn = self._fn(raw.rstrip('/') or '/')
        if fn:
            return self._run(fn, {k: v[0] for k, v in parse_qs(parsed.query).items()})
        # Bare /cheapy + relative asset paths is the gateway trap: redirect.
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
    port = int(port or _config().get('port', 51370))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'cheapy: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/cheapest')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
