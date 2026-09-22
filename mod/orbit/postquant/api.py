#!/usr/bin/env python3
"""postquant api — REST, the console and MCP on one port, stdlib only.

Every route is a thin call into the same tool functions the MCP surface and the
shell reach, so a browser, an agent and a person are never told a different
state root for the same chain. call_tool() is the single door.

    GET  /                  what this chain is and every way in
    GET  /health            liveness, tip height, mempool depth
    GET  /genesis           the genesis file — rules, allocation, validator
    GET  /head /algos /market /keys /get /quote /account /block /tx /history
         /prove /check /mempool /verify        reads, args as query params
    POST /set /del /fund /sweep /list /buy /transfer /wallet /faucet /mine
         writes, args as a JSON body
    GET  /tools             the MCP tool registry
    POST /mcp               MCP JSON-RPC 2.0 (Streamable HTTP)
    GET  /postquant         the console

WHY WRITES ARE GATED AND READS ARE NOT
    A write spends PQ and the faucet spends the treasury. Those routes sit
    behind an optional bearer: write `~/.mod/postquant/server.secret` to turn
    the gate on. With no secret file the node is local and open, which is the
    right default for a devnet whose only caller is the operator's own shell.
    Reading a block never needs permission — a chain nobody can read is not one.

THE BLOCK LOOP
    serve() starts the proposer thread: blocks are produced when the mempool
    has work and on a heartbeat otherwise, because rent is priced in wall-clock
    seconds and has to keep settling even when nobody is transacting.

    python3 api.py [--port 51030] [--bind 0.0.0.0]
"""

import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import mcp as mcpsrv                                            # noqa: E402
import state as S                                               # noqa: E402
from state import StateError                                    # noqa: E402

BASE = os.environ.get('POSTQUANT_BASE_PATH', '/postquant')
PORT = int(os.environ.get('POSTQUANT_PORT', 51030))
BIND = os.environ.get('POSTQUANT_BIND', '0.0.0.0')
SECRET_FILE = os.path.join(os.path.expanduser(
    os.environ.get('POSTQUANT_DATA_DIR', '~/.mod/postquant')), 'server.secret')

# /head -> pq_head and so on: the REST path is the tool name without its
# prefix, which is what keeps the two surfaces from drifting apart.
ROUTES = {name[3:]: name for name in mcpsrv.TOOLS}
WRITE_ROUTES = {name[3:] for name in mcpsrv.WRITE_TOOLS}


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def secret():
    try:
        with open(SECRET_FILE) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None


def info():
    n = mcpsrv.node()
    head = n.head()
    return {
        'name': 'postquant',
        'what': 'a post-quantum L1 whose entire state machine is a market in '
                'key/value space — a choice of post-quantum key type per '
                'account (ML-DSA lattices or SLH-DSA hashes, GET /algos), '
                'SHA3-256 commitments, no elliptic curve anywhere, and every '
                'byte of state pays rent',
        'chain_id': head['chain_id'],
        'height': head['height'],
        'state_root': head['state_root'],
        'base_fee': head['base_fee'],
        'scheme': f'{n.genesis.get("scheme", "ML-DSA-44")} (proposer) — '
                  'accounts choose their own key type at creation, GET /algos',
        'endpoints': {
            'GET /health': 'liveness, tip height, mempool depth',
            'GET /genesis': 'the rules and the allocation, verbatim',
            **{f'GET /{p}': mcpsrv.TOOLS[t]['description'].split('. ')[0]
               for p, t in sorted(ROUTES.items()) if p not in WRITE_ROUTES},
            **{f'POST /{p}': mcpsrv.TOOLS[t]['description'].split('. ')[0]
               for p, t in sorted(ROUTES.items()) if p in WRITE_ROUTES},
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'the console',
        },
        'auth': {
            'gate': 'bearer token on the write routes'
                    if secret() else 'open — no server.secret on this box',
            'header': 'Authorization: Bearer <contents of '
                      '~/.mod/postquant/server.secret>',
            'gated': sorted('/' + p for p in WRITE_ROUTES),
        },
        'mcp': {'endpoint': 'POST /mcp', 'stdio': 'python3 mcp.py',
                'tools': len(mcpsrv.TOOLS)},
    }


def _coerce(v):
    """Query params arrive as strings; the tools expect JSON-ish types for the
    flags. Numbers stay strings — every tool already converts, and an amount
    like "1.5" must not become a float on the way in."""
    low = v.lower()
    if low in ('true', 'yes'):
        return True
    if low in ('false', 'no'):
        return False
    return v


def route(method, path, query, body):
    """One request → one JSON answer. Raises ApiError for a caller's mistake."""
    if path in ('', '/'):
        return info()
    if path == '/health':
        n = mcpsrv.node()
        return {'ok': True, 'chain_id': n.chain_id,
                'height': n.blocks[-1]['height'],
                'mempool': len(n.mempool), 'keys': len(n.state.store),
                'tools': len(mcpsrv.TOOLS), 'data': n.dir}
    if path == '/genesis':
        return mcpsrv.node().genesis
    if path == '/tools':
        return {'tools': mcpsrv.tool_list(), 'count': len(mcpsrv.TOOLS)}

    name = path.lstrip('/')
    tool = ROUTES.get(name)
    if tool is None:
        raise ApiError(f'no route {method} {path} — GET / lists them', 404)

    args = {k: _coerce(v[0]) for k, v in
            urllib.parse.parse_qs(query).items()}
    if isinstance(body, dict):
        args.update(body)

    # pq_wallet is a write tool, but listing or showing the keystore is a
    # read — those two actions are the one GET the write set allows.
    wallet_read = name == 'wallet' and \
        (args.get('action') or 'list') in ('list', 'ls', 'show', 'get')
    if name in WRITE_ROUTES and method != 'POST' and not wallet_read:
        raise ApiError(f'/{name} writes — POST it', 405)
    return mcpsrv.call_tool(tool, args)


# ── the server ────────────────────────────────────────────────────

def serve(port=PORT, bind=None, base=BASE, block_loop=True):
    console = os.path.join(HERE, 'console.html')
    bind = bind if bind is not None else BIND
    # The console calls `<its own path>/_api`, so the same file works mounted
    # at /postquant behind the gateway and served bare on the port. /api is
    # the protocol's live convention, /_api the legacy alias — both stay.
    api_prefixes = (base.rstrip('/') + '/_api', base.rstrip('/') + '/api',
                    '/api/postquant', '/_api', '/api')

    n = mcpsrv.node()
    if block_loop:
        stop = threading.Event()
        threading.Thread(target=n.run, args=(stop,), daemon=True,
                         name='postquant-blocks').start()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'postquant/' + mcpsrv.version()

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self._cors()
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def _cors(self):
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods',
                             'GET,POST,OPTIONS')

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def _read(self):
            length = int(self.headers.get('content-length') or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                return {}

        def _path(self):
            """Strip the gateway prefixes so /postquant/_api/head == /head."""
            raw = urllib.parse.urlparse(self.path)
            p, query = raw.path, raw.query
            for prefix in api_prefixes:
                if p == prefix or p.startswith(prefix + '/'):
                    return p[len(prefix):] or '/', query
            if p in (base, base + '/'):
                return '/console', query
            if p.startswith(base + '/'):
                return p[len(base):], query
            return p, query

        def _authed(self, path, method):
            token_needed = secret()
            if not token_needed:
                return True
            if method == 'GET' or path.lstrip('/') not in WRITE_ROUTES:
                return True
            auth = (self.headers.get('authorization') or '').strip()
            token = auth[7:].strip() if auth.lower().startswith('bearer ') \
                else ''
            return token == token_needed

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here',
                                      'text/plain')
                resp = mcpsrv.handle(self._read())
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p == '/favicon.ico':
                return self._send(204, b'', 'image/x-icon')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(),
                                          'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, info())
            body = self._read()
            if not self._authed(p, self.command):
                return self._send(401, {'error': 'this route spends PQ — send '
                                        'the bearer from '
                                        '~/.mod/postquant/server.secret'})
            try:
                return self._send(200, route(self.command, p, query, body))
            except ApiError as e:
                return self._send(e.status, {'error': str(e)})
            except StateError as e:
                return self._send(e.status, e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:                      # noqa: BLE001
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = _dispatch

        def log_message(self, *a):
            pass

    print(f'postquant on {bind}:{port} — api /, console {base}, '
          f'mcp POST /mcp, {len(mcpsrv.TOOLS)} tools, chain {n.chain_id} '
          f'height {n.blocks[-1]["height"]}', flush=True)
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]

    def opt(name, default):
        return argv[argv.index(name) + 1] if name in argv and \
            argv.index(name) + 1 < len(argv) else default

    serve(int(opt('--port', PORT)), bind=opt('--bind', None))
