#!/usr/bin/env python3
"""sui api — REST, MCP and the console on one port, zero dependencies.

Every route is a thin call into the same tools the MCP server exposes, so the
browser, the shell and an agent get the same answer to the same question.

Reads are open: the chain is public and nothing here needs a key to look at it.
Writes are not. Anything that touches the keystore or moves value needs a bearer
token matching ~/.mod/sui/server.secret; with no secret file the write routes
answer only on loopback, so a freshly-started server is usable from this box and
useless from the internet. The gate covers /mcp tool calls as well as the REST
routes — gating one and not the other would be no gate at all.

    python3 api.py [--port 50740]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import keys as K                                              # noqa: E402
import mcp                                                    # noqa: E402
from chain import NETWORKS, SPEND_USD, Client                 # noqa: E402
from keys import SuiError                                     # noqa: E402

BASE = os.environ.get('BASE_PATH', '/sui')
PORT = int(os.environ.get('PORT', 50740))
SECRET_FILE = os.path.join(K.KEY_DIR, 'server.secret')
# Routes that can move value or read a secret. Everything else is public.
WRITE_ROUTES = {'/transfer', '/wallet', '/faucet'}
# The same three, as an agent reaches them.
WRITE_TOOLS = {'sui_transfer', 'sui_wallet', 'sui_faucet'}

ROUTE_TOOLS = {
    '/what': 'sui_what', '/balance': 'sui_balance', '/portfolio': 'sui_portfolio',
    '/objects': 'sui_objects', '/object': 'sui_object', '/coin': 'sui_coin',
    '/price': 'sui_price', '/history': 'sui_history', '/tx': 'sui_tx',
    '/network': 'sui_network', '/validators': 'sui_validators',
    '/stake': 'sui_stake', '/package': 'sui_package', '/wallet': 'sui_wallet',
    '/transfer': 'sui_transfer', '/faucet': 'sui_faucet', '/rpc': 'sui_rpc',
}
NUMERIC = ('amount', 'limit', 'min_usd')
BOOLEAN = ('detail', 'events', 'include_dust', 'confirm', 'dry_run', 'overwrite',
           'default')


def secret():
    try:
        with open(SECRET_FILE) as f:
            return f.read().strip() or None
    except Exception:
        return None


def info():
    return {
        'name': 'sui',
        'version': mcp.version(),
        'what': 'Sui as one mod — identify any 0x string (an address and an object '
                'ID look identical), price what an address holds including staked '
                'SUI, decode a transaction into what moved, read a package\'s '
                'callable functions, and sign transfers with a key that never '
                'leaves this box',
        'networks': {n: urls[0] for n, urls in NETWORKS.items()},
        'default_network': os.environ.get('SUI_NETWORK', 'mainnet'),
        'transport_note': 'Mysten\'s public fullnodes have dropped JSON-RPC. This '
                          'module runs against a pool of third-party endpoints and '
                          'fails over between them; set SUI_RPC to your own node.',
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'signing': {'backend': K.BACKEND, 'scheme': 'ed25519',
                    'keystore': K.KEY_FILE, 'guard_usd': SPEND_USD,
                    'rule': 'no house wallet — a transfer signs with a key the '
                            'caller controls, simulated before it is signed, and '
                            'the seed never leaves this process'},
        'write_auth': ('bearer token required (~/.mod/sui/server.secret)'
                       if secret() else 'loopback only — no server.secret is set'),
        'endpoints': {
            'GET /health': 'liveness, epoch, checkpoint, tool count',
            'GET /what': 'query= — what a string IS: address, object, package, '
                         'coin type, digest or SuiNS name',
            'GET /balance': 'address= (comma-separated for several), coin_type=',
            'GET /portfolio': 'address=, min_usd=, include_dust=, limit=',
            'GET /objects': 'address=, type=, limit=, cursor=',
            'GET /object': 'object_id= — one object, and how it is owned',
            'GET /coin': 'coin_type= — decimals, supply, market',
            'GET /price': 'ids= — coin types or symbols, comma-separated',
            'GET /history': 'address=, limit=, direction=, detail=',
            'GET /tx': 'digest=, events= — one transaction, decoded',
            'GET /network': 'epoch, checkpoint, TPS, gas price, stake, price',
            'GET /validators': 'limit=, sort= — stake, APY and Nakamoto',
            'GET /stake': 'address= — delegated SUI, in no balance call',
            'GET /package': 'package=, module= — callable Move functions',
            'GET|POST /wallet': 'the local keystore (write-gated)',
            'POST /transfer': '{to, amount, coin_type?, wallet?, dry_run?, confirm?} '
                              '(write-gated)',
            'POST /faucet': '{address?, network?} — testnet/devnet (write-gated)',
            'POST /rpc': '{method, params} — any Sui JSON-RPC method',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def route(method, path, query, body, client_ip=None, headers=None):
    """One request → one JSON answer. Raises SuiError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    args = {**q, **(body if isinstance(body, dict) else {})}

    if path in ('', '/'):
        return info()
    if path == '/health':
        c = Client(network=args.get('network'), rpc=args.get('rpc'))
        state = c.call('suix_getLatestSuiSystemState')
        return {'ok': True, 'network': c.network, 'rpc': c.endpoints[0],
                'epoch': (state or {}).get('epoch'),
                'checkpoint': c.call('sui_getLatestCheckpointSequenceNumber'),
                'tools': len(mcp.TOOLS), 'signer': K.BACKEND}
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    if path in WRITE_ROUTES:
        _gate(client_ip, headers)

    tool = ROUTE_TOOLS.get(path)
    if not tool:
        raise SuiError(f'no route {method} {path} — GET / lists them', status=404)
    # A query string is all strings; the tools want numbers and booleans.
    for name in NUMERIC:
        if isinstance(args.get(name), str) and args[name] != '':
            try:
                args[name] = float(args[name])
            except ValueError:
                raise SuiError(f'{name} must be a number, got {args[name]!r}')
    for name in BOOLEAN:
        if isinstance(args.get(name), str):
            args[name] = args[name].lower() not in ('0', 'false', 'no', '')
    if path == '/wallet' and method == 'GET' and not args.get('action'):
        args['action'] = 'list'
    return mcp.call_tool(tool, args)


def _gate(client_ip, headers):
    """Signing and keystore access, gated. A secret if one is configured;
    otherwise loopback only, because an ungated transfer route on a public
    address is a wallet drainer waiting for a port scan."""
    want = secret()
    if want:
        auth = ((headers or {}).get('authorization') or '').strip()
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else \
            (headers or {}).get('x-sui-token') or ''
        if token != want:
            raise SuiError('this route signs transactions — send Authorization: '
                           'Bearer <the contents of ~/.mod/sui/server.secret>',
                           status=401)
        return
    if client_ip not in ('127.0.0.1', '::1', 'localhost'):
        raise SuiError('signing routes are loopback-only until a secret is set — '
                       f'write one to {SECRET_FILE} and send it as a bearer token',
                       status=403)


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /sui behind the gateway or served bare at :50740/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/sui', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'sui/' + mcp.version()

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
            self.send_header('access-control-allow-methods', 'GET,POST,DELETE,OPTIONS')

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
            """Strip the gateway prefixes so /sui/_api/network == /network."""
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

        def _client_ip(self):
            return self.client_address[0] if self.client_address else None

        def _gate_mcp(self, body):
            """Apply the write gate to tools/call, not just to the REST routes.

            Returned as an isError tool result rather than an HTTP status, so a
            client sees the refusal where it is looking for it.
            """
            if not isinstance(body, dict) or body.get('method') != 'tools/call':
                return None
            if ((body.get('params') or {}).get('name')) not in WRITE_TOOLS:
                return None
            try:
                _gate(self._client_ip(), self.headers)
                return None
            except SuiError as e:
                return {'jsonrpc': '2.0', 'id': body.get('id'),
                        'result': {'isError': True,
                                   'content': [{'type': 'text',
                                                'text': json.dumps(e.dict())}]}}

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                body = self._read()
                denied = self._gate_mcp(body)
                if denied:
                    return self._send(200, denied)
                resp = mcp.handle(body)
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, json.dumps(info(), indent=2).encode())
            try:
                return self._send(200, route(self.command, p, query, self._read(),
                                             self._client_ip(), self.headers))
            except SuiError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'sui on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools, signer {K.BACKEND}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
