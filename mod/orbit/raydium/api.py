#!/usr/bin/env python3
"""raydium api — REST, MCP and the console on one port, zero dependencies.

Every route is a thin call into the same tool the MCP server exposes, so the
browser, the shell and an agent get the same answer to the same question.

Everything here is a read and everything here is public: pool data is published
and the chain is public. There is no write gate because there is nothing to
gate — the module holds no keys, and the one route that touches a wallet
(/swap_tx) returns unsigned bytes that are useless without a signer.

    python3 api.py [--port 50790]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import mcp                                                    # noqa: E402
import ray                                                    # noqa: E402
from ray import RayError                                      # noqa: E402

BASE = os.environ.get('BASE_PATH', '/raydium')
PORT = int(os.environ.get('PORT', 50790))

ROUTES = {
    '/overview': 'ray_overview', '/pools': 'ray_pools', '/pool': 'ray_pool',
    '/pair': 'ray_pair', '/token': 'ray_token', '/price': 'ray_price',
    '/search': 'ray_search', '/mints': 'ray_mints', '/quote': 'ray_quote',
    '/swap_tx': 'ray_swap_tx', '/swap': 'ray_swap_tx', '/depth': 'ray_depth',
    '/keys': 'ray_keys', '/farms': 'ray_farms', '/stake': 'ray_stake',
    '/wallet': 'ray_wallet', '/position': 'ray_position', '/api': 'ray_api',
}
NUMERIC = ('amount', 'limit', 'page', 'min_tvl', 'min_volume', 'min_usd',
           'slippage_bps', 'points')
BOOLEAN = ('keys', 'full', 'wrap_sol', 'unwrap_sol')


def info():
    return {
        'name': 'raydium',
        'version': mcp.version(),
        'what': 'Raydium as one mod — rank the pool book, price a swap in whole '
                'tokens, measure the liquidity that is actually near the price, '
                'and read the concentrated positions no portfolio API shows',
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'sources': {'pools_and_tokens': ray.API, 'router': ray.TX_API,
                    'chain': ray.RPC,
                    'token_index': 'jupiter (symbol fallback only)'},
        'keys': 'none. This module signs nothing — /swap_tx returns unsigned '
                'transactions for a wallet that holds keys',
        'endpoints': {
            'GET /health': 'liveness, tool count, Raydium TVL',
            'GET /overview': 'TVL, 24h volume, RAY and SOL price, priority fee',
            'GET /pools': 'type=, sort=, order=, limit=, page=, min_tvl=, '
                          'min_volume=, search= — the ranked pool book',
            'GET /pool': 'pool= (address or LP mint), keys= — one pool in full',
            'GET /pair': 'token_a=, token_b=, sort=, limit= — every pool for a '
                         'pair, with the price spread between them',
            'GET /token': 'token= — price, verification, where it trades',
            'GET /price': 'tokens= — comma-separated mints or symbols',
            'GET /search': 'query= — find a token by symbol or name',
            'GET /mints': 'search=, limit=, page= — the verified mint list',
            'GET /quote': 'input=, output=, amount=, slippage_bps=, mode= — a '
                          'swap priced in whole tokens, route hop by hop',
            'POST /swap_tx': '{wallet, input, output, amount, …} — UNSIGNED '
                             'transactions for that quote',
            'GET /depth': 'pool=, bands=, points= — money within N% of the price',
            'GET /keys': 'pool= — the on-chain accounts behind a pool',
            'GET /farms': 'pool= or ids= — emission farms and when they end',
            'GET /stake': 'single-sided RAY staking',
            'GET /wallet': 'wallet=, min_usd=, limit= — LP tokens AND '
                           'concentrated positions, priced',
            'GET /position': 'nft_mint= — one concentrated position, decoded',
            'GET /api': 'path=, … — raw Raydium v3 passthrough',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def route(method, path, query, body):
    """One request → one JSON answer. Raises RayError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    args = {**q, **(body if isinstance(body, dict) else {})}

    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'tools': len(mcp.TOOLS),
                'tvl_usd': (ray.get('/main/info', ttl=60) or {}).get('tvl'),
                'rpc': ray.RPC}
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    tool = ROUTES.get(path)
    if not tool:
        raise RayError(f'no route {method} {path} — GET / lists them', status=404)
    # A query string is all strings; the tools want numbers and booleans.
    for name in NUMERIC:
        if isinstance(args.get(name), str) and args[name] != '':
            try:
                args[name] = float(args[name])
            except ValueError:
                raise RayError(f'{name} must be a number, got {args[name]!r}')
    for name in BOOLEAN:
        if isinstance(args.get(name), str):
            args[name] = args[name].lower() not in ('0', 'false', 'no', '')
    if path == '/api':
        # /api?path=/main/info&foo=bar — everything else is passthrough params.
        args = {'path': args.pop('path', None),
                'params': {k: v for k, v in args.items() if k != 'params'}}
    return mcp.call_tool(tool, args)


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /raydium behind the gateway or served bare at :50790/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/raydium', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'raydium/' + mcp.version()

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
            self.send_header('access-control-allow-methods', 'GET,POST,OPTIONS')

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
            """Strip the gateway prefixes so /raydium/_api/pools == /pools."""
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

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcp.handle(self._read())
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, json.dumps(info(), indent=2).encode())
            try:
                return self._send(200, route(self.command, p, query, self._read()))
            except RayError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = _dispatch

        def log_message(self, *a):
            pass

    print(f'raydium on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
