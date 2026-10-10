"""Server for the fabricas console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

Every route dispatches into ``Mod`` rather than reimplementing it, so the
CLI and the API cannot drift: ``m fabricas/quote design='{...}'`` and
``POST /quote`` are the same call.

One process answers both halves of the protocol's URL rule:

    /fabricas/*          → the console (prefix kept by the gateway; stripped here)
    /fabricas/api/{fn}   → the API, as the console calls it — one relative path
                           that resolves the same locally and behind the gateway
    /api/fabricas/{fn}   → the API (prefix stripped by the gateway)
    /{fn}                → the API, bare, for local curl

    python3 serve.py [--port 51050] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/fabricas'

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

from mod import Mod  # noqa: E402

MOD = Mod()

# Methods on the anchor, published as routes. `serve` and `kill` are
# deliberately absent: a public endpoint that restarts the fleet is not a
# route. Writes go through POST; every read also answers GET.
GET_FNS = ('info', 'health', 'readme', 'catalog', 'quote', 'render',
           'design', 'designs', 'gallery', 'remix', 'orders')
POST_FNS = ('save', 'quote', 'order', 'advance')

# Query strings arrive as strings; these are the arguments that aren't.
INTS = ('limit', 'qty', 'width')
BOOLS = ('public',)


def _coerce(args):
    out = {}
    for k, v in args.items():
        val = v[0] if isinstance(v, list) else v
        if k in BOOLS:
            out[k] = str(val).lower() in ('1', 'true', 'yes', 'on')
        elif k in INTS and str(val).lstrip('-').isdigit():
            out[k] = int(val)
        elif val != '' and val is not None:
            out[k] = val
    return out


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

    def _svg(self, svg):
        body = svg.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'image/svg+xml')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fn(self, path, allowed):
        # Every spelling of the API collapses to a bare fn name.
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in allowed:
                return path[len(pre):]
        return None

    def _dispatch(self, fn, kwargs):
        # Answers 4xx, never 5xx: Cloudflare strips the body off a 5xx,
        # and the reason is the entire content of these.
        try:
            if fn == 'readme':
                return self._json({'readme': MOD.readme()})
            if fn == 'render':
                return self._svg(MOD.render(**kwargs))
            return self._json(getattr(MOD, fn)(**kwargs))
        except LookupError as e:
            return self._json({'error': str(e), 'kind': 'not_found'}, 404)
        except (ValueError, TypeError) as e:
            return self._json({'error': str(e), 'kind': 'bad_request'}, 400)
        except Exception as e:  # noqa: BLE001 — the caller gets the reason
            return self._json({'error': f'{type(e).__name__}: {e}',
                               'kind': 'error'}, 400)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/') or '/'
        fn = self._fn(path, POST_FNS)
        if not fn:
            return self._json({'error': f'no POST route {path}',
                               'kind': 'not_found'}, 404)
        try:
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('POST body must be a JSON object')
        except (ValueError, json.JSONDecodeError) as e:
            return self._json({'error': f'bad JSON body: {e}',
                               'kind': 'bad_request'}, 400)
        return self._dispatch(fn, body)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'
        fn = self._fn(path, GET_FNS)
        if fn:
            kwargs = _coerce(parse_qs(parsed.query))
            # quote/render accept an inline design as a JSON query param
            if 'design' in kwargs and isinstance(kwargs['design'], str):
                try:
                    kwargs['design'] = json.loads(kwargs['design'])
                except json.JSONDecodeError:
                    pass  # mod.py answers with the real reason
            return self._dispatch(fn, kwargs)

        # The console. /fabricas and /fabricas/... map into web/; the gateway
        # trap is the bare /fabricas with no slash + relative asset paths.
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
    port = int(port or MOD.port)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'fabricas: http://localhost:{port}{PREFIX}/  '
          f'api http://localhost:{port}/catalog')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
