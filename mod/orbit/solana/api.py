#!/usr/bin/env python3
"""solana api — REST, MCP and the console on one port, zero dependencies.

Every route is a thin call into the same `Client` the MCP tools use, so the
browser, the shell and an agent all get the same answer to the same question.

Reads are open: the chain is public and nothing here needs a key to look at it.
Writes are not. Anything that touches the keystore or moves lamports needs a
bearer token matching ~/.mod/solana/server.secret; with no secret file the
write routes answer only on loopback, so a freshly-started server is usable
from this box and useless from the internet.

    python3 api.py [--port 50710]
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
import keys as K                                              # noqa: E402
from chain import NETWORKS, SPEND_USD, Client                 # noqa: E402
from keys import SolError                                     # noqa: E402

BASE = os.environ.get('BASE_PATH', '/solana')
PORT = int(os.environ.get('PORT', 50710))
SECRET_FILE = os.path.join(K.KEY_DIR, 'server.secret')
# Routes that can move value or read a secret. Everything else is public.
WRITE_ROUTES = {'/transfer', '/swap', '/wallet', '/airdrop', '/deploy',
                '/authority'}
# The same set, as an agent reaches them. A gate on the REST routes alone
# would be no gate at all — /mcp reaches every tool by name.
WRITE_TOOLS = {'sol_transfer', 'sol_swap', 'sol_wallet', 'sol_airdrop',
               'sol_deploy', 'sol_authority'}
# Two tools are a read until an argument makes them a write: simulating a call
# is public, signing one is not, and reading an IDL is public while storing one
# writes to this box. Gating the whole tool would take the interesting half
# away from anyone who is not on loopback.
CONDITIONAL = {
    'sol_invoke': lambda a: bool(a.get('send')),
    'sol_idl': lambda a: str(a.get('action') or 'get').lower() not in ('get', ''),
}


def secret():
    try:
        with open(SECRET_FILE) as f:
            return f.read().strip() or None
    except Exception:
        return None


def info():
    return {
        'name': 'solana',
        'version': mcp.version(),
        'what': 'Solana as one mod — identify any address, price a wallet, decode a '
                'transaction into what actually moved, quote a swap, read the '
                'validator set, sign transfers with a key that never leaves this '
                'box, and deploy, load and call programs',
        'networks': NETWORKS,
        'default_network': os.environ.get('SOLANA_NETWORK', 'mainnet'),
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'signing': {'backend': K.BACKEND, 'keystore': K.KEY_FILE,
                    'guard_usd': SPEND_USD,
                    'rule': 'no house wallet — a transfer signs with a key the caller '
                            'controls, and never leaves this process'},
        'write_auth': ('bearer token required (~/.mod/solana/server.secret)'
                       if secret() else 'loopback only — no server.secret is set'),
        'endpoints': {
            'GET /health': 'liveness, chain slot, tool count',
            'GET /account': 'address= — what an address IS: wallet, mint, token '
                            'account, stake account or program',
            'GET /balance': 'address= (comma-separated for several) — SOL + USD',
            'GET /portfolio': 'address=, min_usd=, include_dust=, limit= — SOL and '
                              'every SPL position, priced and sorted',
            'GET /token': 'mint= — supply, authorities, liquidity, holders, risk',
            'GET /price': 'ids= — mints or symbols, comma-separated',
            'GET /tokens': 'list=, sort=, limit=, offset=, query=, tag=, '
                           'min_liquidity=, safe_only= — every routable token on '
                           'Solana ranked by the liquidity behind it',
            'GET /liquidity': 'mint=, depth=, sizes=, cost_limit_pct= — one token\'s '
                              'liquidity three ways, including the sell size this '
                              'module actually priced',
            'GET /pools': 'mint=, limit= — every pool holding a token, deduped '
                          'across indexes and screened for fake depth',
            'GET /venues': 'tokens=, pages= — where the chain\'s liquidity sits, '
                           'by DEX',
            'GET /history': 'address=, limit=, before=, detail= — recent signatures',
            'GET /tx': 'signature=, logs= — one transaction, decoded',
            'GET /quote': 'input=, output=, amount=, slippage_bps= — Jupiter route',
            'GET /network': 'slot, epoch, TPS, supply, inflation, price',
            'GET /validators': 'limit=, sort=, delinquent= — stake and Nakamoto',
            'GET /stake': 'address= — stake accounts and their state',
            'GET|POST /wallet': 'the local keystore (write-gated)',
            'POST /transfer': '{to, amount, mint?, wallet?, secret?, memo?, confirm?} '
                              '(write-gated)',
            'POST /swap': '{input, output, amount, slippage_bps?, wallet?, '
                          'confirm?, dry_run?} — trade on the DEXes via Jupiter '
                          '(write-gated)',
            'POST /airdrop': '{address?, sol?} — devnet/testnet faucet (write-gated)',
            'POST /rpc': '{method, params} — any Solana JSON-RPC method',
            'GET /program': 'program= — what is deployed there: loader, upgrade '
                            'authority, code size, syscalls, IDL',
            'GET|POST /idl': 'program=, action=get|set|clear — a program\'s '
                             'interface (set is write-gated)',
            'POST /deploy': '{path|data|clone, program?, wallet?, confirm?} — '
                            'deploy or upgrade, as a job (write-gated)',
            'GET /deploy': 'action=list|status, job= — follow a deploy',
            'POST /invoke': '{program, ix?, args?, accounts?, data?, send?} — '
                            'simulate or send one instruction',
            'GET|POST /pda': 'program=, seeds= — derive a program address',
            'POST /authority': '{action, account, ...} — set, revoke or close '
                               '(write-gated)',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def route(method, path, query, body, client_ip=None, headers=None):
    """One request → one JSON answer. Raises SolError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}
    args = {**q, **b}

    def arg(name, default=None):
        return args.get(name, default)

    def flag(name, default=False):
        v = args.get(name)
        return default if v is None else str(v).lower() not in ('0', 'false', 'no', '')

    if path in ('', '/'):
        return info()
    if path == '/health':
        c = Client(network=arg('network'), rpc=arg('rpc'))
        return {'ok': True, 'network': c.network, 'tools': len(mcp.TOOLS),
                'slot': c.call('getSlot'), 'signer': K.BACKEND}
    if path in WRITE_ROUTES and not (path == '/deploy' and method == 'GET'
                                     and str(arg('action') or '') in
                                     ('list', 'status', 'job', 'jobs')):
        _gate(client_ip, headers)

    tool = {
        '/account': 'sol_account', '/balance': 'sol_balance',
        '/portfolio': 'sol_portfolio', '/token': 'sol_token', '/price': 'sol_price',
        '/history': 'sol_history', '/tx': 'sol_tx', '/quote': 'sol_quote',
        '/network': 'sol_network', '/validators': 'sol_validators',
        '/stake': 'sol_stake', '/wallet': 'sol_wallet', '/transfer': 'sol_transfer',
        '/swap': 'sol_swap',
        '/airdrop': 'sol_airdrop', '/rpc': 'sol_rpc', '/swap': 'sol_swap',
        '/tokens': 'sol_tokens', '/liquidity': 'sol_liquidity',
        '/pools': 'sol_pools', '/venues': 'sol_venues',
        '/program': 'sol_program', '/idl': 'sol_idl', '/deploy': 'sol_deploy',
        '/invoke': 'sol_invoke', '/pda': 'sol_pda', '/authority': 'sol_authority',
    }.get(path)
    if tool:
        if CONDITIONAL.get(tool) and CONDITIONAL[tool](args):
            _gate(client_ip, headers)
        # Query strings are all strings; the tools want numbers and booleans.
        for name in ('amount', 'limit', 'min_usd', 'slippage_bps', 'sol',
                     'max_data_len', 'wait', 'offset', 'min_liquidity',
                     'max_liquidity', 'cost_limit_pct', 'pool_limit', 'tokens',
                     'pages'):
            if isinstance(args.get(name), str) and args[name] != '':
                try:
                    args[name] = float(args[name])
                except ValueError:
                    raise SolError(f'{name} must be a number, got {args[name]!r}')
        for name in ('detail', 'logs', 'include_dust', 'confirm', 'wait', 'overwrite',
                     'default', 'delinquent', 'dry_run', 'depth', 'safe_only',
                     'ascending'):
            if isinstance(args.get(name), str):
                args[name] = args[name].lower() not in ('0', 'false', 'no', '')
        if path == '/wallet' and method == 'GET' and not args.get('action'):
            args['action'] = 'list'
        return mcp.call_tool(tool, args)
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise SolError(f'no route {method} {path} — GET / lists them', status=404)


def _gate(client_ip, headers):
    """Signing and keystore access, gated. A secret if one is configured;
    otherwise loopback only, because an ungated transfer route on a public
    address is a wallet drainer waiting for a port scan."""
    want = secret()
    if want:
        auth = ((headers or {}).get('authorization') or '').strip()
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else \
            (headers or {}).get('x-solana-token') or ''
        if token != want:
            raise SolError('this route signs transactions — send '
                           'Authorization: Bearer <the contents of '
                           '~/.mod/solana/server.secret>', status=401)
        return
    if client_ip not in ('127.0.0.1', '::1', 'localhost'):
        raise SolError('signing routes are loopback-only until a secret is set — '
                       f'write one to {SECRET_FILE} and send it as a bearer token',
                       status=403)


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /solana behind the gateway or served bare at :50710/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/solana', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'solana/' + mcp.version()

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
            """Strip the gateway prefixes so /solana/_api/network == /network."""
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
            client sees the refusal in the place it is looking for it.
            """
            if not isinstance(body, dict) or body.get('method') != 'tools/call':
                return None
            params = body.get('params') or {}
            name = params.get('name')
            args = params.get('arguments') or {}
            if name not in WRITE_TOOLS and not (
                    CONDITIONAL.get(name) and CONDITIONAL[name](args)):
                return None
            try:
                _gate(self._client_ip(), self.headers)
                return None
            except SolError as e:
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
            except SolError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'solana on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools, signer {K.BACKEND}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
