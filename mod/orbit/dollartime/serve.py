"""Server for dollartime: the web console + the mod protocol API on one port.

Standalone so pm2 can run it directly (``python3 serve.py``).

    /dollartime/*            → the console
    /dollartime/api/{fn}     → the API, as the console calls it
    /api/dollartime/{fn}     → the API (prefix stripped by the gateway)
    /{fn}                    → the API, bare, for local curl

Reads are GET with query args; writes are POST with a JSON body. Every
function is a method on mod.Mod — the same ones `m dollartime/<fn>` runs.

    python3 serve.py [--port 51410] [--host 0.0.0.0]
"""

import argparse
import importlib.util
import inspect
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/dollartime'
MAX_BODY = 64 * 1024

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

READ_FNS = ('health', 'info', 'readme', 'stats', 'days', 'holders', 'history',
            'position', 'me', 'chains')
WRITE_FNS = ('join', 'contribute', 'claim', 'transfer', 'revenue', 'withdraw',
             'set_asset', 'spend')

_mod = None


def mod():
    """mod.py loaded by path — never `import mod`, that name is the protocol's."""
    global _mod
    if _mod is None:
        spec = importlib.util.spec_from_file_location('dollartime_mod', os.path.join(MODULE_DIR, 'mod.py'))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _mod = m.Mod()
    return _mod


def api(fn, a):
    f = getattr(mod(), fn)
    params = inspect.signature(f).parameters
    kwargs = {k: v for k, v in a.items() if k in params}
    missing = [k for k, p in params.items() if p.default is inspect.Parameter.empty and k not in kwargs]
    if missing:
        raise ValueError(f'missing: {", ".join(missing)}')
    out = f(**kwargs)
    return {'readme': out} if fn == 'readme' else out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):
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
        except PermissionError as e:
            return self._json({'error': str(e)}, 403)
        except (ValueError, KeyError, TypeError) as e:
            return self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
            return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        return self._json(out)

    def do_POST(self):
        fn = self._fn(urlparse(self.path).path.rstrip('/'), WRITE_FNS)
        if not fn:
            return self._json({'error': 'not found'}, 404)
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
        fn = self._fn(raw.rstrip('/') or '/', READ_FNS)
        if fn:
            return self._run(fn, {k: v[0] for k, v in parse_qs(parsed.query).items()})
        # Bare /dollartime + relative asset paths is the gateway trap.
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
    port = int(port or mod().port)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'dollartime: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/stats')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
