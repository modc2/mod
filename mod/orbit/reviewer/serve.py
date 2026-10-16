"""Server for the reviewer console: static bundle + the mod protocol API + MCP.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

    /reviewer/*            → the console (prefix kept by the gateway; stripped here)
    /reviewer/api/{fn}     → the API, as the console calls it
    /api/reviewer/{fn}     → the API (legacy alias, prefix stripped by the gateway)
    /{fn}                  → the API, bare, for local curl
    /mcp                   → MCP JSON-RPC over HTTP (also /reviewer/mcp)

Reads are GET with query args. Writes (review, verdict, delete) are POST
with a JSON body. delete only answers a loopback caller; review with
engine=agent spends on this box, so it is loopback-only too unless
REVIEWER_OPEN_AGENT=1.

    python3 serve.py [--port 51430] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/reviewer'
MAX_BODY = 8 * 1024 * 1024

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import mod as anchor  # noqa: E402
import mcp_server  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'get', 'list', 'rules', 'targets', 'resolve')
WRITE_FNS = ('review', 'verdict', 'delete')
LOCAL_ONLY = ('delete',)
OPEN_AGENT = os.environ.get('REVIEWER_OPEN_AGENT') == '1'

_mod = None


def the_mod():
    global _mod
    if _mod is None:
        _mod = anchor.Mod()
    return _mod


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _int(v, d):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def _confine(m, a, local):
    """Remote callers may only point at fleet modules (orbit/ or core/).
    A path outside those trees is refused before anything is read."""
    if local:
        return
    t = a.get('target') or a.get('path')
    if not t:
        return
    p = m.resolve(t)
    if p and not m.in_fleet(p):
        raise PermissionError('remote callers can only review fleet modules')


def api(fn, a, local=False):
    m = the_mod()
    if fn in ('review', 'resolve'):
        _confine(m, a, local)
    if fn == 'health':
        return m.health(probe_agent=str(a.get('probe_agent', '1')) != '0')
    if fn == 'info':
        return m.info()
    if fn == 'readme':
        return {'readme': m.readme()}
    if fn == 'rules':
        return m.rules()
    if fn == 'targets':
        return m.targets(a.get('q', ''))
    if fn == 'resolve':
        return {'target': a.get('target', ''), 'path': m.resolve(a.get('target') or a.get('path'))}
    if fn == 'get':
        return m.get(a.get('id', ''))
    if fn == 'list':
        return m.list(a.get('target', ''), a.get('engine', ''), _int(a.get('limit'), 50), _int(a.get('offset'), 0))
    if fn == 'review':
        engine = (a.get('engine') or 'static').lower()
        if engine == 'agent' and not local and not OPEN_AGENT:
            raise PermissionError('engine=agent spends on this box: loopback callers only')
        return m.review(target=a.get('target'), path=a.get('path'), code=a.get('code'),
                        filename=a.get('filename') or 'snippet.py', diff=a.get('diff'),
                        engine=engine, focus=a.get('focus', ''), model=a.get('model'),
                        timeout=_int(a.get('timeout'), 600),
                        save=str(a.get('save', '1')) not in ('0', 'false', 'False'))
    if fn == 'verdict':
        return m.verdict(a.get('id', ''), _int(a.get('index'), -1), a.get('verdict', 'fixed'), a.get('note', ''))
    if fn == 'delete':
        return m.delete(a.get('id', ''))
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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.end_headers()

    def _fn(self, path, allowed):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in allowed:
                return path[len(pre):]
        return None

    def _local(self):
        # A gateway on this box also connects from loopback, so a forwarded
        # request is treated as remote whatever its socket says.
        return (self.client_address[0] in ('127.0.0.1', '::1')
                and not self.headers.get('X-Forwarded-For'))

    def _run(self, fn, args, local):
        try:
            out = api(fn, args, local)
        except PermissionError as e:
            return self._json({'error': str(e)}, 403)
        except KeyError as e:
            return self._json({'error': str(e).strip("'")}, 404)
        except ValueError as e:
            return self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001 — the caller gets the reason
            # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
            return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        return self._json(out)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            raise ValueError('body too large')
        raw = self.rfile.read(n) if n else b''
        return json.loads(raw or b'{}')

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/')
        if path in ('/mcp', f'{PREFIX}/mcp', f'/api{PREFIX}/mcp', f'{PREFIX}/api/mcp'):
            try:
                msg = self._body()
            except Exception as e:  # noqa: BLE001
                return self._json({'jsonrpc': '2.0', 'id': None,
                                   'error': {'code': -32700, 'message': str(e)}}, 400)
            resp = mcp_server.handle_message(msg, local=self._local())
            if resp is None:
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            return self._json(resp)
        fn = self._fn(path, WRITE_FNS)
        if not fn:
            return self._json({'error': 'not found'}, 404)
        local = self._local()
        if fn in LOCAL_ONLY and not local:
            return self._json({'error': f'{fn} is local-only'}, 403)
        try:
            args = self._body()
        except ValueError as e:
            return self._json({'error': str(e)}, 413 if 'large' in str(e) else 400)
        except Exception:  # noqa: BLE001
            return self._json({'error': 'body must be JSON'}, 400)
        if not isinstance(args, dict):
            return self._json({'error': 'body must be a JSON object'}, 400)
        return self._run(fn, args, local)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'
        if path in ('/mcp', f'{PREFIX}/mcp'):
            return self._json(mcp_server.describe())
        fn = self._fn(path, READ_FNS)
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._run(fn, args, self._local())

        # The console. Bare /reviewer + relative asset paths is the gateway trap.
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
    port = int(port or _config().get('port', 51430))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'reviewer: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/info  mcp http://localhost:{port}/mcp')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
