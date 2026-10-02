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
    /mcp, /x402/mcp, /api/x402/mcp
                        → MCP (streamable HTTP, stateless JSON-RPC): x402_mcp
                          finds the right paid MCP server, x402_find any
                          service, x402_service opens one. Read-only.

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

import x402mcp as mcp            # noqa: E402 — the one MCP dispatch
import x402trust as trust        # noqa: E402 — who is calling

mcp.SYNC, mcp.MOD = MOD.sync, MOD

READ_FNS = ('info', 'health', 'readme', 'services', 'service', 'hosts',
            'networks', 'stats', 'sources', 'partners', 'facilitators',
            'find', 'mcp_servers', 'users', 'trust', 'whoami', 'tools')
# Writes are POST. Probing fetches a stranger's URL from this box, and sync /
# add_source spend its bandwidth — so they are local-only unless the owner
# sets X402_OPEN=1 (see _allowed).
WRITE_FNS = ('sync', 'discover', 'add_source', 'remove_source', 'probe', 'forget',
             'reindex')
# Agent actions run as the CALLER (token / key / anon), gated by their trust
# tier inside x402mcp — never as this box, which is what Mod's methods do.
AGENT_FNS = ('call', 'quote', 'register', 'revoke', 'set_trust', 'policy', 'wallet')
API_FNS = READ_FNS + WRITE_FNS + AGENT_FNS

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


# ── MCP: x402mcp.handle_message is the whole engine (tools, trust gates);
# this file is only its HTTP framing. stdio: python3 x402mcp.py
MCP_PATHS = ('/mcp', f'{PREFIX}/mcp', f'/api{PREFIX}/mcp')


def api(fn, args):
    if fn == 'users':
        a = _coerce(args)
        return trust.users(limit=int(a.get('limit') or 100), kind=a.get('kind'))
    if fn == 'trust':
        return trust.trust(str(_coerce(args).get('user') or ''))
    if fn == 'tools':
        return {'tools': mcp.tool_list()}
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
        self._cors()
        for k, v in self.__dict__.pop('_extra', {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, Authorization, x-mod-token, token, '
                         'Mcp-Session-Id, Mcp-Protocol-Version')
        self.send_header('Access-Control-Expose-Headers', 'Mcp-Session-Id')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    def _accepted(self):
        # Notifications: 202 with an EMPTY body (`null` trips strict clients).
        self.send_response(202)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _allowed(self):
        # Behind the gateway every request arrives from loopback with a
        # forwarding header; a direct local call has neither.
        if os.environ.get('X402_OPEN') == '1':
            return True
        fwd = self.headers.get('X-Forwarded-For') or self.headers.get('CF-Connecting-IP')
        return not fwd and self.client_address[0] in ('127.0.0.1', '::1')

    def _local(self):
        # Owner standing is this box only: never X402_OPEN, never a request
        # the gateway forwarded.
        fwd = self.headers.get('X-Forwarded-For') or self.headers.get('CF-Connecting-IP')
        return not fwd and self.client_address[0] in ('127.0.0.1', '::1')

    def _who(self):
        ip = (self.headers.get('CF-Connecting-IP')
              or (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
              or self.client_address[0])
        return trust.identify(dict(self.headers.items()), ip=ip, local=self._local())

    def _agent(self, fn, args):
        """REST face of an MCP tool, run as the caller."""
        a = {k: (v[0] if isinstance(v, list) else v) for k, v in args.items()}
        if fn == 'policy' and a:
            a = {'set': a.get('set') or a}
        r = mcp.call_tool('x402_' + fn, a, self._who())
        if r.get('isError'):
            msg = r['content'][0]['text']
            return self._json({'error': msg, 'kind': 'refused' if msg.startswith(
                ('refused', 'denied', 'rate limit')) else 'bad_request'}, 403 if msg.startswith(
                ('refused', 'denied')) else 429 if msg.startswith('rate limit') else 400)
        return self._json(r['structuredContent'])

    def _fn(self, path):
        if path == PREFIX:
            return None
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in API_FNS:
                return path[len(pre):]
        return None

    def _dispatch(self, fn, args, method):
        if fn == 'whoami':
            return self._json(trust.standing(self._who()['id']))
        if fn in AGENT_FNS:
            if method != 'POST':
                return self._json({'error': f'{fn} is a POST', 'kind': 'bad_method'}, 405)
            return self._agent(fn, args)
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

        if path in MCP_PATHS:          # no server-initiated stream; POST only
            return self._json({'error': 'MCP here is POST (stateless streamable HTTP)',
                               'tools': [t['name'] for t in mcp.tool_list()]}, 405)
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
        if not isinstance(body, (dict, list)):
            body = {}
        if isinstance(body, list) and path not in MCP_PATHS:
            body = {}

        if path in MCP_PATHS:
            if not raw_body.strip() or (raw_body.strip()[:1] not in '{[' ):
                return self._json({'jsonrpc': '2.0', 'id': None,
                                   'error': {'code': -32700, 'message': 'parse error'}}, 400)
            reply = mcp.handle_message(body, self._who())
            if reply is None:
                return self._accepted()
            if isinstance(body, dict) and body.get('method') == 'initialize':
                self._extra = {'Mcp-Session-Id': 'x402-' + os.urandom(8).hex()}
            return self._json(reply)

        fn = self._fn(path)
        if not fn:
            return self._json({'error': f'no route {path}',
                               'routes': list(API_FNS)}, 404)
        args = dict(body)
        args.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
        return self._dispatch(fn, args, 'POST')


def serve(port=None, host='0.0.0.0', autosync=True):
    port = int(port or MOD.port)
    threading.Thread(target=MOD.index().refresh, daemon=True).start()
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
