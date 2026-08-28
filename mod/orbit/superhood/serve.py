"""Server for the superhood game: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

One process answers both halves of the protocol's URL rule, because the game
has no state to keep and no reason to burn a second port:

    /superhood/*        → the app   (prefix kept; stripped here)
    /api/superhood/*    → the API   (prefix stripped by the gateway, so the
                                      protocol routes land at the root)

    python3 serve.py [--port 50341] [--host 0.0.0.0]
"""

import argparse
import importlib.util
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/superhood'
NAME = 'superhood'

_mod = None
_mod_error = None


def api_fns() -> tuple:
    """The functions the public API exposes — ``Mod.API_FNS`` owns the list."""
    mod = module()
    return tuple(getattr(mod, 'API_FNS', ())) if mod else ()


def module():
    """The Mod instance, loaded lazily from mod.py.

    ``mod.py`` is both this module's anchor file and the name of the framework
    package it imports, so our own directory has to come off sys.path before
    loading it — otherwise ``import mod`` finds the anchor and imports itself.
    """
    global _mod, _mod_error
    if _mod is not None or _mod_error is not None:
        return _mod
    shadow = [p for p in sys.path if p in ('', '.', MODULE_DIR)]
    for p in shadow:
        sys.path.remove(p)
    try:
        spec = importlib.util.spec_from_file_location(
            'superhood_mod', os.path.join(MODULE_DIR, 'mod.py'))
        anchor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(anchor)
        _mod = anchor.Mod()
    except Exception as e:                      # the game still serves without it
        _mod_error = f'{type(e).__name__}: {e}'
        sys.stderr.write(f'superhood: API unavailable — {_mod_error}\n')
    finally:
        sys.path[:0] = shadow
    return _mod


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _strip(self):
        """Drop the app prefix, serving the bare form as the index.

        The bare ``/superhood`` must NOT redirect to ``/superhood/``: the
        gateway 308s the directory form back to the bare one, so a redirect
        here is an infinite loop and the published URL never loads. index.html
        pins its own <base>, which is what keeps relative assets resolving.
        """
        if self.path == PREFIX or self.path.startswith(PREFIX + '?'):
            self.path = '/' + self.path[len(PREFIX):]
        elif self.path.startswith(PREFIX + '/'):
            self.path = self.path[len(PREFIX):] or '/'

    # ── mod protocol ─────────────────────────────────────────────────────

    def _route(self) -> str:
        """The function a protocol path names.

        ``/{fn}``, ``/mod/superhood/{fn}``, and a null ``/mod/superhood``
        that returns info — the same shapes the core Flask server answers.
        """
        path = self.path.split('?')[0].strip('/')
        if path in ('mod/' + NAME, NAME):
            return 'info'
        for prefix in ('mod/' + NAME + '/', NAME + '/'):
            if path.startswith(prefix):
                path = path[len(prefix):].strip('/')
                break
        return path or 'info'

    def _json(self, code, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _call(self, fn):
        mod = module()
        if mod is None:
            return self._json(503, {'error': f'module not loaded: {_mod_error}'})
        if fn not in api_fns():
            return self._json(404, {'error': f'no such function {fn!r}',
                                    'fns': list(api_fns())})
        try:
            kwargs = self._body_args()
        except ValueError as e:
            return self._json(400, {'error': str(e)})
        try:
            return self._json(200, {'result': getattr(mod, fn)(**kwargs)})
        except TypeError as e:
            return self._json(400, {'error': str(e)})
        except Exception as e:
            return self._json(500, {'error': f'{type(e).__name__}: {e}'})

    def _body_args(self) -> dict:
        n = int(self.headers.get('Content-Length') or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            args = json.loads(raw or b'{}')
        except json.JSONDecodeError as e:
            raise ValueError(f'body is not JSON: {e}')
        if args in (None, ''):
            return {}
        if not isinstance(args, dict):
            raise ValueError('body must be a JSON object of keyword arguments')
        return args

    def do_POST(self):
        self._strip()
        return self._call(self._route())

    def do_GET(self):
        self._strip()
        clean = self.path.split('?')[0].rstrip('/')
        if clean in ('/health', '/healthz'):
            return self._json(200, {'ok': True, 'module': NAME})
        # GET is not the protocol's call verb, but a browser pointed at a
        # function should still get its answer rather than a directory listing.
        # '/' stays the game — that path is the app half of the route.
        if clean not in ('', '/') and self._route() in api_fns():
            return self._call(self._route())
        return super().do_GET()

    def do_HEAD(self):
        self._strip()
        return super().do_HEAD()

    def end_headers(self):
        # the game is one static bundle; keep browsers from pinning an old build
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Allow', 'GET, HEAD, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 50341)))
    ap.add_argument('--host', default=os.environ.get('HOST', '0.0.0.0'))
    args = ap.parse_args()

    if not os.path.exists(os.path.join(WEB_DIR, 'index.html')):
        raise SystemExit('superhood: web/index.html is missing')

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print('superhood serving %s on http://%s:%d%s (api: /api/%s)'
          % (WEB_DIR, args.host, args.port, PREFIX, NAME), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
