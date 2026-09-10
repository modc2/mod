#!/usr/bin/env python3
"""near api — REST, MCP and the console on one port, zero dependencies.

Every route is a thin call into the same tools the MCP server exposes, so the
browser, the shell and an agent get the same answer to the same question.
Reads are open; a write over HTTP must carry the token from ~/.mod/near/token
(this port is routed publicly — an open spend endpoint is not an option), and
a non-testnet write additionally refuses without confirm=true.

    python3 api.py [--port 50910]
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
from chain import NETWORKS, NearError                         # noqa: E402

BASE = os.environ.get('BASE_PATH', '/near')
PORT = int(os.environ.get('PORT', 50910))

ROUTE_TOOLS = {
    '/account': 'near_account', '/keys': 'near_keys',
    '/contract': 'near_contract', '/view': 'near_view', '/ft': 'near_ft',
    '/history': 'near_history', '/tx': 'near_tx', '/block': 'near_block',
    '/network': 'near_network', '/validators': 'near_validators',
    '/price': 'near_price', '/rpc': 'near_rpc',
    # writes — token-gated over HTTP, testnet by default, mainnet wants confirm
    '/wallet': 'near_wallet', '/deploy': 'near_deploy', '/call': 'near_call',
    '/send': 'near_send', '/create_account': 'near_create_account',
    '/key': 'near_key',
}
NUMERIC = ('limit', 'amount_near', 'gas_tgas', 'deposit_near',
           'initial_near', 'allowance_near')


def info():
    return {
        'name': 'near',
        'version': mcp.version(),
        'what': 'NEAR Protocol as one mod — accounts by name, balances with '
                'staked and storage-locked NEAR broken out, access keys and '
                'their permissions, contract methods parsed from on-chain WASM, '
                'view calls, NEP-141 tokens, history, transactions, validators '
                '— and the write half: a keystore under ~/.mod/near/, contract '
                'deploys with batched init, signed change calls, transfers, '
                'account creation (testnet faucet or sub-accounts), access-key '
                'management. Writes default to testnet; a non-testnet write '
                'refuses without confirm=true; HTTP writes need the token '
                'from ~/.mod/near/token.',
        'networks': {n: urls[0] for n, urls in NETWORKS.items()},
        'default_network': os.environ.get('NEAR_NETWORK', 'mainnet'),
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'endpoints': {
            'GET /health': 'liveness, block height, tool count',
            'GET /account': 'account_id= — balances, storage, contract flag',
            'GET /keys': 'account_id= — access keys and their permissions',
            'GET /contract': 'account_id= — callable methods from the WASM',
            'GET|POST /view': 'contract=, method=, args= (JSON) — a view call',
            'GET /ft': 'contract=, account_id?= — a NEP-141 token, and a balance',
            'GET /history': 'account_id=, limit= — recent txns (indexer)',
            'GET /tx': 'hash=, sender?= — one transaction, decoded',
            'GET /block': 'block_id?= — a block, or the latest final one',
            'GET /network': 'chain id, height, gas price, stake, price',
            'GET /validators': 'limit= — stake, uptime and Nakamoto',
            'GET /price': 'NEAR/USD, 24h change, market cap',
            'POST /rpc': '{method, params} — any NEAR JSON-RPC method',
            'GET|POST /wallet': 'op=status|generate|import|select|forget — '
                                'the keystore (public halves only)',
            'POST /deploy': '{wasm|wasm_path|wasm_url, init_method?, '
                            'init_args?, account_id?, network?, confirm?, '
                            'token} — deploy a contract',
            'POST /call': '{contract, method, args?, gas_tgas?, deposit_near?, '
                          'token} — a signed change call',
            'POST /send': '{to, amount_near, token} — transfer NEAR',
            'POST /create_account': '{new_account_id, initial_near?, token} — '
                                    'testnet faucet or a sub-account',
            'POST /key': '{op:add|delete, public_key, contract?, methods?, '
                         'token} — access keys',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def route(method, path, query, body):
    """One request → one JSON answer. Raises NearError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    args = {**q, **(body if isinstance(body, dict) else {})}

    if path in ('', '/'):
        return info()
    if path == '/health':
        from chain import Client
        c = Client(network=args.get('network'), rpc=args.get('rpc'))
        s = c.call('status', [])
        return {'ok': True, 'network': c.network, 'rpc': c.endpoints[0],
                'block_height': (s.get('sync_info') or {}).get('latest_block_height'),
                'tools': len(mcp.TOOLS)}
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}

    tool = ROUTE_TOOLS.get(path)
    if not tool:
        raise NearError(f'no route {method} {path} — GET / lists them', status=404)
    # A query string is all strings; the tools want numbers where they count.
    for name in NUMERIC:
        if isinstance(args.get(name), str) and args[name] != '':
            try:
                args[name] = float(args[name])
            except ValueError:
                raise NearError(f'{name} must be a number, got {args[name]!r}')
    if isinstance(args.get('confirm'), str):
        args['confirm'] = args['confirm'].lower() in ('true', '1', 'yes')
    return mcp.call_tool(tool, args)


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module
    # is mounted at /near behind the gateway or served bare at :50910/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/near', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'near/' + mcp.version()

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
            """Strip the gateway prefixes so /near/_api/network == /network."""
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
            except NearError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = _dispatch

        def log_message(self, *a):
            pass

    print(f'near on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
