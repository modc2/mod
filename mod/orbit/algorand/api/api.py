#!/usr/bin/env python3
"""algorand api — the API half of the module: REST and MCP, zero dependencies.

Every route is a thin call into the same tools the MCP server exposes. Reads
are open; a write over HTTP must carry the token from ~/.mod/algorand/token,
and a non-testnet write additionally refuses without confirm=true.

The console is not served here — it is app/ on its own port (app_port), which
proxies `/algorand/_api/*` to this server. Behind the gateway this API is
`{host}/algorand/api` (prefix stripped) and the app is `{host}/algorand`.

    python3 api/api.py [--port 51340]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The module root holds chain/codec/wallet/mcp. Appended, never prepended: it
# also holds mod.py, which would shadow the protocol's own `mod` package.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.append(ROOT)

import mcp                                                    # noqa: E402
from chain import NETWORKS, AlgoError, Client                 # noqa: E402

BASE = os.environ.get('BASE_PATH', '/algorand')
PORT = int(os.environ.get('ALGORAND_API_PORT') or os.environ.get('PORT') or 51340)
APP_PORT = int(os.environ.get('ALGORAND_APP_PORT') or 51341)

ROUTE_TOOLS = {
    '/account': 'algo_account', '/resolve': 'algo_resolve',
    '/asset': 'algo_asset', '/assets': 'algo_assets',
    '/app': 'algo_app', '/box': 'algo_box', '/local_state': 'algo_local_state',
    '/simulate': 'algo_simulate', '/tx': 'algo_tx', '/history': 'algo_history',
    '/block': 'algo_block', '/network': 'algo_network', '/price': 'algo_price',
    '/compile': 'algo_compile', '/rpc': 'algo_rpc',
    '/asset_holders': 'algo_asset_holders',
    # writes — token-gated over HTTP, testnet by default, mainnet wants confirm
    '/wallet': 'algo_wallet', '/send': 'algo_send',
    '/asset_create': 'algo_asset_create', '/optin': 'algo_asset_optin',
    '/asset_send': 'algo_asset_send', '/app_create': 'algo_app_create',
    '/app_call': 'algo_app_call', '/app_update': 'algo_app_update',
}
NUMERIC = ('limit', 'amount_algo', 'amount', 'pay_algo', 'total', 'decimals',
           'app_id', 'round', 'global_ints', 'global_bytes',
           'local_ints', 'local_bytes', 'extra_pages')
FLAGS = ('confirm', 'program', 'raw', 'indexer', 'default_frozen')


def info():
    return {
        'name': 'algorand', 'version': mcp.version(),
        'what': 'The Algorand protocol as one mod: accounts (and .algo names), '
                'ASAs, smart contracts (state, boxes, TEAL), keyless simulated '
                'app calls, history, blocks — and the write half: a keystore '
                'under ~/.mod/algorand/, payments, ASA mint/opt-in/transfer, '
                'app deploy/call/update, atomic groups. Writes default to '
                'testnet; mainnet needs confirm=true; HTTP writes need the token.',
        'networks': {n: v['algod'] for n, v in NETWORKS.items()},
        'mcp': {'endpoint': 'POST /mcp', 'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'routes': {r: mcp.TOOLS[t]['description'] for r, t in ROUTE_TOOLS.items()},
        'api': BASE + '/api',
        'app': BASE,
    }


def route(method, path, query, body, local=False):
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    args = {**q, **(body if isinstance(body, dict) else {})}
    if path in ('', '/'):
        return info()
    if path == '/health':
        c = Client(args.get('network'))
        return {'ok': True, 'network': c.network, 'algod': c.algod,
                'round': c.status().get('last-round'), 'tools': len(mcp.TOOLS)}
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    tool = ROUTE_TOOLS.get(path)
    if not tool:
        raise AlgoError(f'no route {method} {path} — GET / lists them', status=404)
    for name in NUMERIC:
        if isinstance(args.get(name), str) and args[name] != '':
            try:
                f = float(args[name])
                args[name] = int(f) if f.is_integer() and name not in ('amount_algo', 'pay_algo') else f
            except ValueError:
                raise AlgoError(f'{name} must be a number, got {args[name]!r}')
    for flag in FLAGS:
        if isinstance(args.get(flag), str):
            args[flag] = args[flag].lower() in ('true', '1', 'yes')
    return mcp.call_tool(tool, args, local=local)


def serve(port=PORT, base=BASE):
    # The gateway strips `/algorand/api` before it gets here, but a caller that
    # skips the app's proxy may still send the app-side or legacy prefix.
    base = base.rstrip('/')
    api_prefixes = (base + '/api', base + '/_api', '/api/algorand', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'algorand/' + mcp.version()

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods', 'GET,POST,OPTIONS')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def _read(self):
            n = int(self.headers.get('content-length') or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                return {}

        def _path(self):
            raw = urllib.parse.urlparse(self.path)
            p, query = raw.path, raw.query
            for prefix in api_prefixes:
                if p == prefix or p.startswith(prefix + '/'):
                    return p[len(prefix):] or '/', query
            return p, query

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcp.handle(self._read())
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            try:
                return self._send(200, route(self.command, p, query, self._read()))
            except AlgoError as e:
                # Cloudflare strips 5xx bodies — upstream failures go out as 4xx.
                return self._send(e.status if 400 <= e.status < 500 else 424, e.dict())
            except (TypeError, ValueError) as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = _dispatch

        def log_message(self, *a):
            pass

    print(f'algorand api on :{port} — routes /, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools (console: app/ on :{APP_PORT}{base})', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
