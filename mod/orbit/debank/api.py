#!/usr/bin/env python3
"""debank api — REST + MCP + console on one port, zero dependencies.

Every route is a thin call into the same `Client` the MCP tools use, so a
browser, a shell and an agent get the same answer to the same question.

The key is per request and is never stored by this server: send it as
`x-debank-key: <AccessKey>`. Absent that, the client falls back to the
operator's own env and off-tree keystore — so a locally-run server is
convenient, and a shared one is BYOK.

    python3 api.py [--port 50720]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import client as C   # noqa: E402
import mcp           # noqa: E402
import savings as S  # noqa: E402
from client import Client, DebankError  # noqa: E402

BASE = os.environ.get('BASE_PATH', '/debank')
PORT = int(os.environ.get('PORT', 50720))


def info():
    return {
        'name': 'debank',
        'version': mcp.version(),
        'what': 'what an EVM address actually owns, across every chain — token '
                'balances, DeFi positions net of debt, NFTs, decoded history, and '
                'the live approvals that could take it all away',
        'upstream': C.BASE,
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'byok': {'headers': ['x-debank-key: <AccessKey>'],
                 'keystore': f'{C.KEY_FILE} (0600, off-tree)',
                 'env': 'DEBANK_ACCESS_KEY',
                 'rule': "every call spends the caller's own DeBank units — no house key",
                 'signed_out': 'GET /chains, /balances and /networks answer with no key'},
        'endpoints': {
            'GET /health': 'liveness, tool count, and whether a key resolved',
            'GET /portfolio': 'id, min_usd — net worth and which chains carry it',
            'GET /tokens': 'id, chain, min_usd, limit, all_tokens',
            'GET /protocols': 'id, chain, min_usd, limit, detail — positions net of debt',
            'GET /approvals': 'id, chain, min_usd, limit — ranked by exposure',
            'GET /history': 'id, chain, start_time, page_count, token_id',
            'GET /nfts': 'id, chain, limit, all_nfts',
            'GET /net_curve': 'id, chain — net worth over time',
            'GET /position': 'id, protocol — one protocol position in full',
            'GET /chains_used': 'id — chains this address has ever touched',
            'GET /protocol': 'protocol | chain, limit — the protocol catalog by TVL',
            'GET /token': 'chain, token — metadata and current price',
            'GET /token_price': 'chain, token, date — a past close',
            'GET /holders': 'protocol | chain+token, start, limit',
            'GET /gas': 'chain — the current gas market',
            'GET /chains': 'q, refresh — chain catalog (works signed-out)',
            'GET /balances': 'id, chains, min_usd — native + stablecoins on the bank '
                             'rail via public RPCs (works signed-out)',
            'GET /networks': 'the bank rail: chain ids, RPCs, explorers, stablecoin '
                             'contracts — what a browser wallet needs (works signed-out)',
            'GET /funds': 'amount — the savings index funds: curated baskets of '
                          'yield venues with live projected ROI and the liquidity '
                          'locked in each protocol (works signed-out)',
            'GET /funds/{id}': 'one fund in full; venue:<id> is a fund of one',
            'GET /savings': 'id — idle stablecoins vs money already placed in each '
                            'venue, read from chain, keyless',
            'GET /savings/plan': 'id, fund, amount — the exact approve+deposit '
                                 'transactions the wallet must sign, per sleeve',
            'GET /savings/exit': 'id, venue — the withdraw-everything transaction',
            'POST /savings/record': '{id, fund, venue, amount, tx} — note a placed leg',
            'GET /account': 'does the key work, and what is left on it',
            'POST /set_key': '{key, persist}',
            'GET|POST /raw': 'path, params, public — any Cloud API route',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def _key_from(headers):
    """BYOK per request. An `authorization` bearer is NOT read as a DeBank key —
    the gateway puts its own session tokens there, and forwarding one upstream
    would leak it."""
    return headers.get('x-debank-key') or headers.get('x-access-key') or None


def _b(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def _n(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def route(method, path, query, body, key):
    """One request → one JSON answer. Raises DebankError for real failures."""
    c = Client(key=key)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}

    def arg(name, default=None):
        v = b.get(name, q.get(name, default))
        return default if v in (None, '') else v

    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'tools': len(mcp.TOOLS), 'key': c.key_state()['key'],
                'keyless': ['/chains', '/balances', '/networks']}
    if path == '/portfolio':
        return c.portfolio(_need(arg('id'), 'id'), min_usd=_n(arg('min_usd'), 1.0))
    if path == '/tokens':
        return c.tokens(_need(arg('id'), 'id'), chain=arg('chain'),
                        min_usd=_n(arg('min_usd'), 1.0),
                        limit=int(_n(arg('limit'), 100)),
                        all_tokens=_b(arg('all_tokens')))
    if path == '/protocols':
        return c.protocols(_need(arg('id'), 'id'), chain=arg('chain'),
                           min_usd=_n(arg('min_usd'), 1.0),
                           limit=int(_n(arg('limit'), 50)),
                           detail=_b(arg('detail')))
    if path == '/approvals':
        return c.approvals(_need(arg('id'), 'id'), chain=arg('chain'),
                           min_usd=_n(arg('min_usd'), 0.0),
                           limit=int(_n(arg('limit'), 100)))
    if path == '/history':
        return c.history(_need(arg('id'), 'id'), chain=arg('chain'),
                         start_time=arg('start_time'),
                         page_count=int(_n(arg('page_count'), 20)),
                         token_id=arg('token_id'))
    if path == '/nfts':
        return c.nfts(_need(arg('id'), 'id'), chain=arg('chain'),
                      limit=int(_n(arg('limit'), 50)), all_nfts=_b(arg('all_nfts')))
    if path == '/net_curve':
        return c.net_curve(_need(arg('id'), 'id'), chain=arg('chain'))
    if path == '/position':
        return c.protocol_position(_need(arg('id'), 'id'),
                                   _need(arg('protocol'), 'protocol'))
    if path == '/chains_used':
        return c.chains_used(_need(arg('id'), 'id'))
    if path == '/protocol':
        return c.protocol(id=arg('protocol'), chain=arg('chain'),
                          limit=int(_n(arg('limit'), 100)))
    if path == '/token':
        return c.token(_need(arg('chain'), 'chain'), _need(arg('token'), 'token'))
    if path == '/token_price':
        return c.token_price_history(_need(arg('chain'), 'chain'),
                                     _need(arg('token'), 'token'), date_at=arg('date'))
    if path == '/holders':
        if arg('protocol'):
            return c.protocol_holders(arg('protocol'), start=int(_n(arg('start'), 0)),
                                      limit=int(_n(arg('limit'), 20)))
        return c.token_holders(_need(arg('chain'), 'chain'),
                               _need(arg('token'), 'token'),
                               start=int(_n(arg('start'), 0)),
                               limit=int(_n(arg('limit'), 20)))
    if path == '/gas':
        return c.gas(_need(arg('chain'), 'chain'))
    if path == '/chains':
        return c.chains(q=arg('q'), refresh=_b(arg('refresh')))
    if path == '/balances':
        chains = arg('chains')
        if isinstance(chains, str):
            chains = [x for x in chains.split(',') if x.strip()]
        return c.balances(_need(arg('id'), 'id'), chains=chains or None,
                          min_usd=_n(arg('min_usd'), 0.0))
    if path == '/networks':
        return c.networks()
    if path == '/funds':
        return S.funds(amount=arg('amount'), refresh=_b(arg('refresh')))
    if path.startswith('/funds/'):
        return S.fund(path[len('/funds/'):], amount=arg('amount'),
                      refresh=_b(arg('refresh')))
    if path == '/savings':
        return S.savings(_need(arg('id'), 'id'))
    if path == '/savings/plan':
        return S.plan(_need(arg('id'), 'id'), _need(arg('fund'), 'fund'),
                      _need(arg('amount'), 'amount'))
    if path == '/savings/exit':
        return S.exit_tx(_need(arg('venue'), 'venue'), _need(arg('id'), 'id'),
                         shares=arg('shares'))
    if path == '/savings/record' and method == 'POST':
        return S.record(_need(b.get('id'), 'id'), _need(b.get('fund'), 'fund'),
                        _need(b.get('venue'), 'venue'),
                        _need(b.get('amount'), 'amount'),
                        _need(b.get('tx'), 'tx'), chain=b.get('chain'))
    if path == '/account':
        return c.account()
    if path == '/set_key' and method == 'POST':
        return C.set_key(_need(b.get('key'), 'key'), persist=b.get('persist', True))
    if path == '/raw':
        return c.raw(_need(arg('path'), 'path'),
                     params=b.get('params') or {k: v for k, v in q.items()
                                                if k not in ('path', 'public')},
                     public=_b(arg('public')))
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise DebankError(f'no route {method} {path} — GET / lists them', status=404)


def _need(v, name):
    if v in (None, ''):
        raise DebankError(f'{name} is required', status=400)
    return v


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /debank behind the gateway or served bare at :50720/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/debank', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'debank/' + mcp.version()

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
            """Strip the gateway prefixes so /debank/_api/tokens == /tokens."""
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
                return self._send(200, route(self.command, p, query, self._read(),
                                             _key_from(self.headers)))
            except DebankError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = _dispatch

        def log_message(self, *a):
            pass

    print(f'debank on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
