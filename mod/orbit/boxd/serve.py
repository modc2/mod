"""Server for the boxd console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

Every route dispatches into ``Mod`` rather than reimplementing it. The two
surfaces then cannot drift: ``m boxd/taste user=dave`` and
``GET /taste?user=dave`` are the same call, and adding a method to the anchor
is all it takes to publish it here.

One process answers both halves of the protocol's URL rule:

    /boxd/*            → the console (prefix kept by the gateway; stripped here)
    /boxd/api/{fn}     → the API, as the console calls it — one relative path
                         that resolves the same locally and behind the gateway
    /api/boxd/{fn}     → the API (prefix stripped by the gateway, so the
                         protocol routes land at the root)
    /{fn}              → the API, bare, for local curl

    python3 serve.py [--port 50940] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/boxd'

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import letterboxd as lb  # noqa: E402
from mod import Mod  # noqa: E402

MOD = Mod()

# Read-only methods on the anchor, published as routes. `serve` and `kill` are
# deliberately absent: a public endpoint that restarts the fleet is not a route.
API_FNS = ('info', 'health', 'readme', 'sources', 'diary', 'reviews',
           'member', 'taste', 'overlap', 'film', 'films')

# Query strings arrive as strings; these are the arguments that aren't.
FLAGS = ('fresh',)
INTS = ('limit', 'year')


def _coerce(args):
    out = {}
    for k, v in args.items():
        val = v[0]
        if k in FLAGS:
            out[k] = str(val).lower() in ('1', 'true', 'yes', 'on')
        elif k in INTS and str(val).isdigit():
            out[k] = int(val)
        elif val != '':
            out[k] = val
    return out


def api(fn, args):
    if fn == 'readme':
        return {'readme': MOD.readme()}
    return getattr(MOD, fn)(**_coerce(args))


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

    def do_GET(self):
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)
        raw = parsed.path
        path = raw.rstrip('/') or '/'

        # Every spelling of the API collapses to a bare fn name.
        fn = None
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in API_FNS:
                fn = path[len(pre):]
                break
        if fn:
            # All four of these answer 4xx, never 5xx: Cloudflare strips the
            # body off a 5xx, and the reason is the entire content of these.
            try:
                return self._json(api(fn, args))
            except lb.Blocked as e:
                return self._json({'error': str(e), 'kind': 'blocked',
                                   'retry_after_s': 60}, 429)
            except LookupError as e:
                return self._json({'error': str(e), 'kind': 'not_found'}, 404)
            except (ValueError, TypeError) as e:
                return self._json({'error': str(e), 'kind': 'bad_request'}, 400)
            except Exception as e:  # noqa: BLE001 — the caller gets the reason
                return self._json({'error': f'{type(e).__name__}: {e}',
                                   'kind': 'error'}, 400)

        # The console. /boxd and /boxd/... map into web/; the gateway trap is
        # the bare /boxd with no slash + relative asset paths, so redirect.
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
    print(f'boxd: http://localhost:{port}{PREFIX}/  '
          f'api http://localhost:{port}/taste?user=davidehrlich')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
