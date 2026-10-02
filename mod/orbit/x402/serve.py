"""Server for x402: the console and the REST API on one port.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader. Every route dispatches into ``Mod``, so
``m x402/services q=weather`` and ``GET /x402/api/services?q=weather`` are the same call.

One process answers every spelling of the protocol's URL rule:

    /x402/*            → the console (prefix kept by the gateway; stripped here)
    /x402/api/{fn}     → the API, as the console calls it — one relative path
                          that resolves the same locally and behind the gateway
    /api/x402/{fn}     → the API (prefix stripped by the gateway)
    /{fn}               → the API, bare, for local curl

    python3 serve.py [--port 51110] [--host 0.0.0.0]
"""

import argparse
import importlib.util
import json
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/x402'

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

# Loaded by path under its own name — `mod` belongs to the protocol package.
_spec = importlib.util.spec_from_file_location('x402_anchor',
                                               os.path.join(MODULE_DIR, 'mod.py'))
_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_anchor)


MOD = _anchor.Mod(local=False)

READ_FNS = ('info', 'health', 'readme', 'services', 'service', 'hosts',
            'networks', 'stats', 'sources', 'partners', 'facilitators')
# Writes are POST. Probing fetches a stranger's URL from this box, and sync /
# add_source spend its bandwidth — so they are local-only unless the owner
# sets X402_OPEN=1 (see _allowed).
WRITE_FNS = ('sync', 'discover', 'add_source', 'remove_source', 'probe', 'forget')
API_FNS = READ_FNS + WRITE_FNS

FLAGS = ('priced', 'background', 'pin', 'discover')
JSONS = ()


def _coerce(args):
    out = {}
    for k, v in args.items():
        val = v[0] if isinstance(v, list) else v
        if k in FLAGS:
            out[k] = str(val).lower() in ('1', 'true', 'yes', 'on')
        elif k in JSONS and isinstance(val, str) and val.strip().startswith('['):
            try:
                out[k] = json.loads(val)
            except json.JSONDecodeError:
                out[k] = [t for t in val.split(',') if t]
        elif val != '':
            out[k] = val
    return out


def api(fn, args):
    if fn == 'readme':
        return {'readme': MOD.readme()}
    if fn == 'sync':
        args = dict(args, background=args.get('background', 'true'))
    return getattr(MOD, fn)(**_coerce(args))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):          # quiet — pm2 keeps the logs
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, Authorization, x-mod-token')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self):
        # Behind the gateway every request arrives from loopback with a
        # forwarding header; a direct local call has neither.
        if os.environ.get('X402_OPEN') == '1':
            return True
        fwd = self.headers.get('X-Forwarded-For') or self.headers.get('CF-Connecting-IP')
        return not fwd and self.client_address[0] in ('127.0.0.1', '::1')

    def _fn(self, path):
        if path == PREFIX:
            return None
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in API_FNS:
                return path[len(pre):]
        return None

    def _dispatch(self, fn, args, method):
        if fn in WRITE_FNS and method != 'POST':
            return self._json({'error': f'{fn} is a POST', 'kind': 'bad_method'}, 405)
        if fn in WRITE_FNS and not self._allowed():
            return self._json({'error': f'{fn} is local-only on this node',
                               'kind': 'forbidden'}, 403)
        # 4xx for everything: Cloudflare strips the body off a 5xx, and the
        # reason is the entire content of these replies.
        try:
            return self._json(api(fn, args))
        except (ValueError, TypeError) as e:
            return self._json({'error': str(e), 'kind': 'bad_request'}, 400)
        except Exception as e:                       # noqa: BLE001 — the reason travels
            return self._json({'error': f'{type(e).__name__}: {e}',
                               'kind': 'error'}, 400)

    def do_OPTIONS(self):
        self._json({'ok': True})

    def do_GET(self):
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)
        raw = parsed.path
        path = raw.rstrip('/') or '/'

        fn = self._fn(path)
        if fn:
            return self._dispatch(fn, args, 'GET')

        # The console. The gateway trap is a bare /x402 with no trailing
        # slash plus relative asset paths, so redirect rather than serve.
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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        length = int(self.headers.get('Content-Length') or 0)
        raw_body = self.rfile.read(length).decode() if length else ''
        try:
            body = json.loads(raw_body) if raw_body.strip() else {}
        except json.JSONDecodeError:
            body = {k: v[0] for k, v in parse_qs(raw_body).items()}
        if not isinstance(body, dict):
            body = {}

        fn = self._fn(path)
        if not fn:
            return self._json({'error': f'no route {path}',
                               'routes': list(API_FNS)}, 404)
        args = dict(body)
        args.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
        return self._dispatch(fn, args, 'POST')


def serve(port=None, host='0.0.0.0', autosync=True):
    port = int(port or MOD.port)
    if autosync and os.environ.get('X402_AUTOSYNC', '1') != '0':
        threading.Thread(target=MOD.autosync, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'x402: http://localhost:{port}{PREFIX}/  '
          f'api http://localhost:{port}/x402/api/services')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
