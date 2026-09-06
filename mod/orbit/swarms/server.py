#!/usr/bin/env python3
"""swarms api — REST + MCP + console on one port, zero dependencies.

Every route is a thin call into the same `Client` and `chain` module the MCP
tools use, so the browser, the CLI and an agent all get the same answer to the
same question. There is no second implementation to drift.

The key is per request and is never stored by this server: send it as
`x-swarms-key: …` (or `authorization: Bearer …`). Absent that, the client falls
back to the operator's own env and off-tree keystore — so a locally-run server
is convenient, and a shared one is BYOK.

The chain routes need no key at all and never will: they are reads, and this
module holds no Solana keypair.

    python3 api.py [--port 50690]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import chain                                            # noqa: E402
import client as C                                      # noqa: E402
import mcp                                              # noqa: E402
from chain import ChainError                            # noqa: E402
from client import SPEND_USD, SWARM_TYPES, Client, SwarmsError  # noqa: E402

BASE = os.environ.get('BASE_PATH', '/swarms')
PORT = int(os.environ.get('PORT', os.environ.get('SWARMS_PORT', 50690)))


def info():
    return {
        'name': 'swarms',
        'version': mcp.version(),
        'what': 'the Swarms protocol behind one mod — sixteen multi-agent '
                'architectures on api.swarms.world, the swarms.world marketplace, '
                'and the $swarms SPL token on Solana: price, supply, holders, '
                'wallet balances and swap quotes',
        'upstreams': {'runtime': C.BASE, 'marketplace': C.MARKET,
                      'solana_rpc': chain.RPC, 'price': chain.JUP,
                      'pools': 'https://api.dexscreener.com'},
        'token': {'mint': chain.MINT, 'decimals': chain.DECIMALS,
                  'chain': 'solana-mainnet', 'read_only': True,
                  'signing': 'none — this module holds no keypair'},
        'swarm_types': list(SWARM_TYPES),
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'byok': {'headers': ['x-swarms-key: …', 'authorization: Bearer …'],
                 'keystore': f'{C.KEY_FILE} (0600, off-tree)',
                 'env': 'SWARMS_API_KEY',
                 'signup': 'https://swarms.world/platform/api-keys',
                 'rule': "every completion spends the caller's own Swarms credits — "
                         'no house key'},
        'spend_guard_usd': SPEND_USD,
        'endpoints': {
            'GET /health': 'liveness, tool count, and whether a key resolved',
            'GET /architectures': 'the sixteen swarm types, what each is for',
            'GET /models': 'models the runtime accepts as model_name (q=, refresh=)',
            'GET /tools': 'hosted tools an agent can be given by name',
            'POST /run': '{task, agents, swarm_type, max_loops, …} — run a swarm',
            'POST /agent': '{task, model_name, system_prompt, …} — run one agent',
            'POST /build': '{task} — task in, agent roster out',
            'POST /reasoning': '{task, swarm_type, num_samples, …}',
            'POST /batch': '{jobs: [{agent_config, task}]} — parallel fan-out',
            'GET /cost': 'agents=, loops=, input_tokens=, output_tokens=',
            'GET /account': 'key state, credits, rate limits, live pricing',
            'GET /credits': 'credit balance',
            'GET /logs': 'past API requests on this account',
            'GET /market': 'kind=agents|prompts|tools, q=, limit= (public, no key)',
            'GET /token': 'the $swarms card: identity, supply, price, venues',
            'GET /price': 'spot price + every pool by liquidity',
            'GET /supply': 'on-chain supply',
            'GET /holders': 'largest token accounts (limit=)',
            'GET /balance': 'owner= — SOL, $swarms and what it is worth',
            'GET /quote': 'side=buy|sell, amount=, pay_with=SOL|USDC — a quote, '
                          'never a trade',
            'GET /state': 'which key this request resolved to, and the spend guard',
            'POST /set_key': '{key, persist}',
            'POST /raw': '{path, method, body, params, market}',
            'GET /mcp_tools': 'the MCP tool registry',
            'GET /mcp_config': 'client=claude|cursor|json — paste-ready client config',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def _key_from(headers):
    """BYOK per request.

    An `authorization` header only counts when there is no dedicated header —
    the gateway puts its own bearer tokens there, and handing one of those to
    the Swarms API would just be a confusing 401.
    """
    key = headers.get('x-swarms-key')
    if not key:
        auth = (headers.get('authorization') or '').strip()
        if auth.lower().startswith('bearer '):
            key = auth[7:].strip()
    return (key or '').strip() or None


def _flag(v, default=False):
    if v is None:
        return default
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def _int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def route(method, path, query, body, key):
    """One request → one JSON answer. Raises SwarmsError/ChainError for failures."""
    c = Client(key=key)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}

    def arg(name, default=None):
        return b.get(name, q.get(name, default))

    # ── the module ──
    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'tools': len(mcp.TOOLS), 'key': c.key_state()['key'],
                'mint': chain.MINT}
    if path == '/state':
        return {**c.key_state(), 'chain': chain.info()}
    if path == '/mcp_tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    if path == '/mcp_config':
        return mcp_config(arg('client', 'json'), arg('url'))

    # ── the runtime ──
    if path == '/architectures':
        return c.swarm_types(refresh=_flag(q.get('refresh')))
    if path == '/models':
        return c.models(q=q.get('q'), refresh=_flag(q.get('refresh')))
    if path == '/tools':
        return c.tools()
    if path == '/run' and method == 'POST':
        return c.swarm(**b)
    if path == '/agent' and method == 'POST':
        return c.agent(**b)
    if path == '/build' and method == 'POST':
        return c.auto_build(_need(b.get('task'), 'task'), model_name=b.get('model_name'),
                            confirm=bool(b.get('confirm')))
    if path == '/reasoning' and method == 'POST':
        return c.reasoning(**b)
    if path == '/batch' and method == 'POST':
        return c.agent_batch(b.get('jobs') or [], confirm=bool(b.get('confirm')))
    if path == '/chat' and method == 'POST':
        return c.chat(**b)
    if path == '/graph' and method == 'POST':
        return c.graph_workflow(b, confirm=bool(b.pop('confirm', False)))
    if path == '/grid' and method == 'POST':
        return c.batched_grid(b, confirm=bool(b.pop('confirm', False)))

    # ── the money ──
    if path == '/cost':
        return c.cost(agents=_int(arg('agents'), 1), loops=_int(arg('loops'), 1),
                      input_tokens=_int(arg('input_tokens'), 2000),
                      output_tokens=_int(arg('output_tokens'), 2000))
    if path == '/account':
        return mcp.call_tool('swarms_account', {'key': key})
    if path == '/credits':
        return c.credits()
    if path == '/pricing':
        return c.pricing(refresh=_flag(q.get('refresh')))
    if path == '/rate_limits':
        return c.rate_limits()
    if path == '/logs':
        return c.logs(limit=_int(q.get('limit')))
    if path == '/metrics':
        return c.metrics()

    # ── the marketplace ──
    if path == '/market':
        return c.market(kind=arg('kind', 'agents'), q=arg('q'),
                        limit=_int(arg('limit'), 25))

    # ── the chain ──
    if path == '/token':
        return chain.token(arg('mint'))
    if path == '/price':
        return mcp.call_tool('swarms_price', {'mint': arg('mint'),
                                              'limit': _int(arg('limit'), 8)})
    if path == '/supply':
        return chain.supply(arg('mint'))
    if path == '/pools':
        return chain.pools(arg('mint'), limit=_int(arg('limit'), 10))
    if path == '/holders':
        return chain.holders(arg('mint'), limit=_int(arg('limit'), 20))
    if path == '/balance':
        return chain.balance(_need(arg('owner'), 'owner'), arg('mint'))
    if path == '/quote':
        return chain.quote(side=arg('side', 'buy'), amount=arg('amount', 1),
                           mint=arg('mint'), slippage_bps=_int(arg('slippage_bps'), 100),
                           pay_with=arg('pay_with', 'SOL'))

    # ── keys and the escape hatch ──
    if path == '/set_key' and method == 'POST':
        return C.set_key(key=_need(b.get('key'), 'key'), persist=b.get('persist', True))
    if path == '/raw' and method == 'POST':
        return c.raw(_need(b.get('path'), 'path'), method=b.get('method') or 'GET',
                     body=b.get('body'), params=b.get('params'),
                     market=bool(b.get('market')))

    raise SwarmsError(f'no route {method} {path} — GET / lists them', status=404)


def _need(v, name):
    if v in (None, ''):
        raise SwarmsError(f'{name} is required', status=400)
    return v


def mcp_config(kind='json', url=None):
    """Paste-ready client config for whatever is pointing at this deployment."""
    url = url or os.environ.get('SWARMS_PUBLIC_URL') or f'http://localhost:{PORT}'
    endpoint = url.rstrip('/') + '/mcp'
    http_block = {'mcpServers': {'swarms': {'type': 'http', 'url': endpoint}}}
    stdio_block = {'mcpServers': {'swarms': {
        'command': 'python3', 'args': [os.path.join(HERE, 'mcp.py')],
        'env': {'SWARMS_API_KEY': 'sk-…'}}}}
    out = {'endpoint': endpoint, 'tools': len(mcp.TOOLS),
           'http': http_block, 'stdio': stdio_block,
           'claude_cli': f'claude mcp add --transport http swarms {endpoint}',
           'note': 'the HTTP transport uses whatever key the server resolved; the '
                   'stdio transport runs with this box\'s own state'}
    if kind in ('claude', 'cursor'):
        out['config'] = http_block
    return out


CONSOLE = os.path.join(HERE, 'console.html')


def serve(port=PORT, base=BASE):
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /swarms behind the gateway or served bare at :50690/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/swarms', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'swarms/' + mcp.version()

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
                             'GET,POST,PUT,DELETE,OPTIONS')

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
            """Strip the gateway prefixes so /swarms/_api/token == /token."""
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
                if self.command == 'GET':
                    return self._send(200, {'name': 'swarms', 'version': mcp.version(),
                                            'transport': 'Streamable HTTP, JSON-RPC 2.0',
                                            'instructions': mcp.INSTRUCTIONS,
                                            'tools': mcp.tool_list()})
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcp.handle(self._read())
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(CONSOLE, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, json.dumps(info(), indent=2).encode(),
                                      'application/json')
            body = self._read()
            key = _key_from(self.headers)
            try:
                return self._send(200, route(self.command, p, query, body, key))
            except (SwarmsError, ChainError) as e:
                status = e.status if e.status in range(400, 600) else 400
                return self._send(status, e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_PUT = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'swarms on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools, mint {chain.MINT[:8]}…', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
