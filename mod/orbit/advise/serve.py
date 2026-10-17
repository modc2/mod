"""Server for advise: the console, the REST API and the MCP endpoint, one port.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

Every route dispatches into ``Mod`` rather than reimplementing it, so
``m advise/brief module=boxd``, ``GET /advise/api/brief?module=boxd`` and the
``advise_brief`` MCP tool are the same call.

One process answers every spelling of the protocol's URL rule:

    /advise/*             → the console (prefix kept by the gateway; stripped here)
    /advise/api/{fn}      → the API, as the console calls it — one relative path
                            that resolves the same locally and behind the gateway
    /api/advise/{fn}      → the API (prefix stripped by the gateway)
    /{fn}                 → the API, bare, for local curl
    /mcp                  → MCP, JSON-RPC 2.0 over POST

Identity arrives as a mod-protocol token in ``x-mod-token`` or
``Authorization: Bearer``. There is no local mode over HTTP: the server holds
``Mod(local=False)``, so an HTTP caller is whoever their signature says, and
an unsigned one is an anon handle that may file and may not decide.

    python3 serve.py [--port 50990] [--host 0.0.0.0]
"""

import argparse
import importlib.util
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/advise'

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

# Loaded by path under its own name — `mod` belongs to the protocol package.
_spec = importlib.util.spec_from_file_location('advise_anchor',
                                               os.path.join(MODULE_DIR, 'mod.py'))
_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_anchor)

import mcp as mcp_server                                       # noqa: E402
import recs as store                                           # noqa: E402
import scan                                                    # noqa: E402

MOD = _anchor.Mod(local=False)

READ_FNS = ('info', 'health', 'readme', 'modules', 'tree', 'file', 'grep',
            'brief', 'recs', 'rec', 'inbox', 'outbox')
WRITE_FNS = ('recommend', 'approve', 'reject', 'withdraw', 'comment', 'relay')
API_FNS = READ_FNS + WRITE_FNS

INTS = ('depth', 'limit', 'lines', 'start')
FLOATS = ('confidence',)
FLAGS = ('relay',)
JSONS = ('anchors', 'evidence')
# Never taken from the caller: `local` is the transport's word, and `ip`/`ua`
# are the server's — a caller who could set them could forge a neighbour's
# anon handle or claim to be the host.
RESERVED = ('local', 'ip', 'ua')


def _coerce(args):
    out = {}
    for k, v in args.items():
        if k in RESERVED:
            continue
        val = v[0] if isinstance(v, list) else v
        if k in FLAGS:
            out[k] = str(val).lower() in ('1', 'true', 'yes', 'on')
        elif k in INTS and str(val).lstrip('-').isdigit():
            out[k] = int(val)
        elif k in FLOATS:
            try:
                out[k] = float(val)
            except (TypeError, ValueError):
                pass
        elif k in JSONS and isinstance(val, str) and val.strip().startswith(('[', '{')):
            try:
                out[k] = json.loads(val)
            except json.JSONDecodeError:
                out[k] = val
        elif val != '':
            out[k] = val
    return out


def api(fn, args, ctx):
    if fn == 'readme':
        return {'readme': MOD.readme()}
    args = _coerce(args)
    method = getattr(MOD, fn)
    # Identity-taking methods get the caller's context; the rest are public
    # and would not know what to do with a token anyway.
    if fn in WRITE_FNS or fn in ('inbox', 'outbox'):
        args.update(ctx)
    else:
        args.pop('token', None)
    return method(**args)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):          # quiet — pm2 keeps the logs
        pass

    # ── helpers ──────────────────────────────────────────────────

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

    def _ctx(self, body_args=None):
        """Who is calling, from headers first and the body only as a fallback
        (an MCP client or a curl one-liner has nowhere else to put a token)."""
        token = (self.headers.get('x-mod-token')
                 or (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
                 or (body_args or {}).get('token'))
        fwd = (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
        return {'token': token or None,
                'ip': fwd or self.client_address[0],
                'ua': self.headers.get('User-Agent', ''),
                'local': False}

    def _fn(self, path):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in API_FNS:
                return path[len(pre):]
        return None

    def _dispatch(self, fn, args, ctx, method):
        if fn in WRITE_FNS and method != 'POST':
            return self._json({'error': f'{fn} is a POST', 'kind': 'bad_method'}, 405)
        # 4xx for everything: Cloudflare strips the body off a 5xx, and the
        # reason is the entire content of these replies.
        try:
            return self._json(api(fn, args, ctx))
        except scan.ScanError as e:
            return self._json({'error': str(e), 'kind': 'not_found'}, 404)
        except store.Denied as e:
            return self._json({'error': str(e), 'kind': 'denied'}, 403)
        except (store.RecError, ValueError, TypeError) as e:
            return self._json({'error': str(e), 'kind': 'bad_request'}, 400)
        except Exception as e:                       # noqa: BLE001 — the reason travels
            return self._json({'error': f'{type(e).__name__}: {e}',
                               'kind': 'error'}, 400)

    # ── verbs ────────────────────────────────────────────────────

    def do_OPTIONS(self):
        self._json({'ok': True})

    def do_GET(self):
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)
        raw = parsed.path
        path = raw.rstrip('/') or '/'

        if path in (f'{PREFIX}/mcp', f'/api{PREFIX}/mcp', '/mcp'):
            return self._json({'transport': 'streamable-http (POST)',
                               'tools': mcp_server.tool_list()})
        fn = self._fn(path)
        if fn:
            flat = {k: v[0] for k, v in args.items()}
            return self._dispatch(fn, args, self._ctx(flat), 'GET')

        # The console. The gateway trap is a bare /advise with no trailing
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

        if path in (f'{PREFIX}/mcp', f'/api{PREFIX}/mcp', '/mcp'):
            resp = mcp_server.handle(body, local=False)
            return self._json(resp if resp is not None else {}, 200)

        fn = self._fn(path)
        if not fn:
            return self._json({'error': f'no route {path}',
                               'routes': list(API_FNS) + ['mcp']}, 404)
        args = dict(body)
        args.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
        ctx = self._ctx(args)
        args.pop('token', None)
        return self._dispatch(fn, args, ctx, 'POST')


def serve(port=None, host='0.0.0.0'):
    port = int(port or MOD.port)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'advise: http://localhost:{port}{PREFIX}/  '
          f'api http://localhost:{port}/modules  mcp http://localhost:{port}/mcp')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
