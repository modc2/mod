#!/usr/bin/env python3
"""polymarket mcp — Model Context Protocol server for the polymarket console.

Turns the module into tools any MCP client (Claude Code / Desktop, an agent
framework, another module) can call: read markets and leaderboards, inspect a
trader's flow, read the strats this deployment runs, read the background
worker's cached backtests — including the entry FUNNEL that says how much of
the observed leader flow each strat actually copies and which gate blocked the
rest — and read what the live engine is doing right now.

READ-ONLY BY DESIGN. There is deliberately no order-placing tool: the console
signs and submits real money through the deposit wallet, and a mis-prompted
agent must not be able to reach that. The one tool with a side effect
(`pm_backtest_run`) spends CPU and data-api calls, nothing else.

Transports:
    python3 src/mcp.py                    # stdio — one JSON-RPC msg per line
    python3 src/mcp.py --http [--port N]  # Streamable HTTP — POST /mcp (:50092)

Auth: this deployment is owner-only (api/src/access.rs). The server mints the
same Bearer token the console's sign-in issues, from the HMAC secret at
~/.mod/polymarket/server.secret — so it works exactly when the local owner's
console works, and not otherwise. Nothing here accepts a token from a caller.
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)

API_URL = os.environ.get('POLYMARKET_API_URL', 'http://127.0.0.1:50091')
APP_URL = os.environ.get('POLYMARKET_APP_URL', 'http://127.0.0.1:3091')
BASE_PATH = os.environ.get('NEXT_PUBLIC_BASE_PATH', '/polymarket')

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Polymarket console: prediction-market data, copy-trading strats and a '
    'live copy engine. Use pm_markets/pm_top_traders/pm_trader to look at the '
    'market and who is trading it; pm_strats for the strategies this '
    'deployment runs and pm_backtests for their cached 1-day backtests — read '
    "the `funnel` on each: it says how many of the leader's entries the strat "
    'actually copied and which gate blocked the rest, which is the answer to '
    'almost every "why is it not trading?" question, and the `settlement`, '
    'which says how much of that P&L was settled against real market '
    'resolutions vs still marked at the last observed price (unverified marks '
    'read high — losers expire quietly). pm_live_sessions and '
    'pm_live_gates show what the engine is doing right now. Read-only: no tool '
    'here can place, cancel or size an order.'
)


# ── owner auth (mirror of api/src/access.rs token minting) ──

def _state_dir() -> str:
    return os.environ.get('POLYMARKET_ACCESS_DIR') or os.path.join(
        os.path.expanduser('~'), '.mod', 'polymarket')


def _owner() -> str:
    env = (os.environ.get('POLYMARKET_OWNER') or '').strip().lower()
    if env.startswith('0x') and len(env) == 42:
        return env
    for p in (os.path.join(_state_dir(), 'owner.json'),
              os.path.join(os.path.expanduser('~'), '.mod', 'claude', 'owner.json')):
        try:
            o = str(json.load(open(p)).get('owner') or '').strip().lower()
            if o.startswith('0x') and len(o) == 42:
                return o
        except Exception:
            continue
    raise RuntimeError('no owner configured — the access gate is locked '
                       f'(write {{"owner": "0x…"}} to {_state_dir()}/owner.json)')


def _token() -> str:
    """A 7-day owner token, minted from the API's persisted HMAC secret."""
    try:
        secret = bytes.fromhex(open(os.path.join(_state_dir(), 'server.secret')).read().strip())
    except Exception as e:
        raise RuntimeError(f'cannot read the access secret — is the API running? ({e})')
    owner, exp = _owner(), int(time.time()) + 7 * 24 * 3600
    sig = hmac.new(secret, f'pma1|{owner}|{exp}'.encode(), hashlib.sha256).hexdigest()
    return f'pma1.{owner}.{exp}.{sig}'


def _get(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={'Authorization': f'Bearer {_token()}'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{url.split("?")[0]} → HTTP {e.code}: {e.read()[:200].decode(errors="replace")}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'{url.split("?")[0]} unreachable: {e.reason} — is the module serving?')


def _post(url: str, body: dict | None = None, method: str = 'POST', timeout: float = 600.0):
    data = json.dumps(body).encode() if body is not None else b''
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': f'Bearer {_token()}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{url.split("?")[0]} → HTTP {e.code}: {e.read()[:200].decode(errors="replace")}')
    except urllib.error.URLError as e:
        raise RuntimeError(f'{url.split("?")[0]} unreachable: {e.reason}')


def _api(endpoint: str, **params) -> object:
    """The API's upstream proxy — gamma markets/events, data-api activity."""
    qs = urllib.parse.urlencode({'endpoint': endpoint, **{k: v for k, v in params.items() if v not in (None, '')}})
    return _get(f'{API_URL}/?{qs}')


def _hub(path: str = '') -> str:
    return f'{APP_URL}{BASE_PATH}/api/hub{path}'


def _req(args: dict, key: str) -> str:
    v = str(args.get(key) or '').strip()
    if not v:
        raise ValueError(f'{key} required')
    return v


# ── tools ──

def _t_health(args):
    out = {'api': API_URL, 'app': APP_URL}
    try:
        out['health'] = _get(f'{API_URL}/health', timeout=5)
    except Exception as e:
        out['health'] = f'unreachable: {e}'
    try:
        out['sync'] = _get(f'{API_URL}/sync/status', timeout=5)
    except Exception as e:
        out['sync'] = f'unavailable: {e}'
    try:
        out['backtest_worker'] = _get(_hub('?days=1'), timeout=10).get('status')
    except Exception as e:
        out['backtest_worker'] = f'unavailable: {e}'
    return out


def _t_markets(args):
    limit = int(args.get('limit') or 20)
    params = {'limit': str(limit), 'active': 'true', 'closed': 'false',
              'order': 'volume24hr', 'ascending': 'false'}
    q = str(args.get('query') or '').strip()
    if q:
        # gamma's own text search endpoint; the plain /markets list ignores it.
        rows = _api('public-search', q=q, limit_per_type=str(limit))
        events = (rows or {}).get('events') if isinstance(rows, dict) else None
        if events is not None:
            return {'query': q, 'events': events[:limit]}
        return {'query': q, 'result': rows}
    rows = _api('markets', **params)
    keep = ('question', 'slug', 'conditionId', 'volume24hr', 'liquidity',
            'outcomePrices', 'outcomes', 'endDate', 'closed')
    return {'markets': [{k: m.get(k) for k in keep if k in m} for m in (rows or [])[:limit]]}


def _t_top_traders(args):
    days = int(args.get('days') or 7)
    limit = int(args.get('limit') or 20)
    qs = urllib.parse.urlencode({'days': days, 'limit': limit, 'format': 'json',
                                 'category': str(args.get('category') or '')})
    return _get(f'{API_URL}/active-traders?{qs}', timeout=180)


def _t_trader(args):
    addr = _req(args, 'address')
    limit = int(args.get('limit') or 100)
    acts = _api('activity', user=addr, limit=str(min(limit, 500)), offset='0') or []
    trades = [a for a in acts if a.get('type') == 'TRADE']
    positions = _api('positions', user=addr, sizeThreshold='.1', limit='100') or []
    # The one thing a copy-trader most needs to know about a leader, and the
    # thing a raw activity dump hides: how much of this flow is sub-hour candle
    # games, which every strat's time-to-close gate refuses by default.
    short = sum(1 for t in trades if '-updown-' in str(t.get('slug') or '')
                or 'up or down' in str(t.get('title') or '').lower())
    return {
        'address': addr,
        'trades_returned': len(trades),
        'short_dated_share': round(short / len(trades), 3) if trades else 0,
        'note': ('most of this trader\'s flow is sub-hour Up/Down candles — a copy strat '
                 'with the default 60m time-to-close gate will refuse ~all of it'
                 if trades and short / len(trades) > 0.5 else None),
        'positions': len(positions),
        'recent': [{'ts': t.get('timestamp'), 'side': t.get('side'), 'title': t.get('title'),
                    'slug': t.get('slug'), 'price': t.get('price'), 'size': t.get('size')}
                   for t in trades[:25]],
    }


def _t_strats(args):
    """The strats the console published for the background worker to replay."""
    path = os.path.join(_state_dir(), 'hub', 'manifest.json')
    try:
        man = json.load(open(path))
    except Exception:
        return {'strats': [], 'note': 'no manifest yet — open the console\'s STRATS tab once '
                                      'so it publishes the roster to the worker'}
    keep = ('id', 'name', 'capital', 'minTrade', 'maxTrade', 'maxPerCycle', 'maxOpenPositions',
            'marketQuery', 'tradeFilters', 'filter', 'minMinutesToClose', 'sizing', 'turnover',
            'stopLoss', 'takeProfit', 'liveEnabled')
    return {
        'days': man.get('days'),
        'published_at': man.get('at'),
        'strats': [{**{k: s.get(k) for k in keep if k in s},
                    'traders': [t.get('address') for t in (s.get('traders') or []) if t.get('enabled') is not False]}
                   for s in man.get('strats') or []],
    }


def _read_manifest():
    try:
        return json.load(open(os.path.join(_state_dir(), 'hub', 'manifest.json')))
    except Exception:
        return {}


def _t_backtests(args):
    days = int(args.get('days') or 1)
    cache = _get(_hub(f'?days={days}'), timeout=30)
    results = cache.get('results') or {}
    name = str(args.get('strat') or '').strip().lower()
    # Strat ids are opaque (`mrjg86gf`); the manifest is what turns them back
    # into the names on the cards, and into the roll-call below.
    owned = {s.get('id'): (s.get('name') or s.get('id'))
             for s in (_read_manifest().get('strats') or []) if s.get('id')}
    rows = []
    for key, bt in results.items():
        if name and name not in key.lower() and name not in owned.get(key, '').lower():
            continue
        f = bt.get('funnel') or {}
        s = bt.get('settlement') or {}
        rows.append({
            'key': key, 'name': owned.get(key), 'pnl': bt.get('pnl'), 'roi': bt.get('roi'),
            'trades': bt.get('trades'), 'capital': bt.get('capital'),
            'traders': bt.get('traders'), 'note': bt.get('note'),
            'ran_at': bt.get('at'), 'by': bt.get('by'),
            # How the replay valued what it was still holding when the leaders
            # went quiet. `unverified_usd` was priced at the last observed
            # trade, which reads HIGH: leaders trade winners and let losers
            # expire, so an unresolved mark is usually a loss not yet booked.
            # A pnl with a large unverified_usd is provisional.
            'settlement': {
                'resolved_positions': s.get('resolved'),
                'unverified_positions': s.get('marked'),
                'unverified_usd': s.get('markedUsd'),
            } if s else None,
            # Traders whose history wasn't cached yet when this replayed. Any
            # non-zero value means the pnl above is a FLOOR, not the result.
            'warming': bt.get('warming'),
            # WALK-FORWARD. The same strat replayed over the window BEFORE this
            # one, with the clock wound back so it knew nothing of what came
            # after — and the verdict of the two side by side. `held` is the
            # only pass: profitable then AND profitable since. Anything ranked
            # by `pnl` alone is ranked by one window, which is how a strat that
            # had one good day ends up at the top of the list.
            'forward': {
                'verdict': (bt.get('forward') or {}).get('verdict'),
                'confirmed': (bt.get('forward') or {}).get('ok'),
                'prior_pnl': (bt.get('forward') or {}).get('pnl'),
                'prior_roi': (bt.get('forward') or {}).get('roi'),
                'prior_trades': (bt.get('forward') or {}).get('trades'),
                'prior_window': [(bt.get('forward') or {}).get('from'),
                                 (bt.get('forward') or {}).get('to')],
            } if bt.get('forward') else None,
            'funnel': {'observed': f.get('observed'), 'copied': f.get('executed'),
                       'blocked_by_filters': f.get('gated'), 'outranked': f.get('outranked'),
                       'unplaceable': f.get('skipped'), 'reasons': f.get('reasons')} if f else None,
        })
    rows.sort(key=lambda r: (r.get('pnl') or 0), reverse=True)
    # An owned strat with NO card for this window is the one thing a list of
    # cards cannot show. Name them: absent is not the same as flat, and the
    # difference used to be invisible.
    covered = set(results)
    missing = [{'key': sid, 'name': nm} for sid, nm in owned.items() if sid not in covered]
    return {'days': days, 'worker': cache.get('status'), 'backtests': rows,
            'owned': len(owned), 'untested': missing,
            **({'note': f'{len(missing)} owned strat(s) have no {days}d replay yet — '
                        'run pm_backtest_run, or wait for the next worker pass'} if missing else {})}


def _t_backtest_run(args):
    """Replay every published strat now, out of the server's cached trader
       feeds — no upstream requests. refresh=true tops that cache up first
       (that IS the upstream work, and it takes minutes)."""
    if args.get('refresh'):
        _post(_hub('?refresh=1'))
    if args.get('wait') is False:
        return _post(_hub('?run=1'))
    return _post(_hub(), method='PUT')


def _t_live_sessions(args):
    eoa = str(args.get('eoa') or '').strip() or _owner()
    sessions = _get(f'{API_URL}/live/sessions?eoa={urllib.parse.quote(eoa)}', timeout=30).get('sessions') or []
    out = []
    for s in sessions:
        cfg, st = s.get('config') or {}, s.get('state') or {}
        out.append({
            'strategyId': cfg.get('strategyId'), 'running': s.get('running'),
            'autoExecute': cfg.get('autoExecute'), 'capital': cfg.get('capital'),
            'traders': [t.get('address') for t in cfg.get('traders') or []],
            'minMinutesToClose': cfg.get('minMinutesToClose'),
            'maxPerCycle': cfg.get('maxOrdersPerCycle'),
            'accountValue': st.get('accountValue'), 'balance': st.get('balance'),
            'positions': len(st.get('positions') or []),
            'error': st.get('error'),
        })
    return {'eoa': eoa, 'sessions': out}


def _t_live_gates(args):
    """Why the engine isn't copying anything — the same tally the console's
       amber warning renders, plus the last decisions it logged."""
    eoa = str(args.get('eoa') or '').strip() or _owner()
    qs = urllib.parse.urlencode({'eoa': eoa, **({'strategyId': args['strategyId']} if args.get('strategyId') else {})})
    # /live/status answers {config, running, state} — the tallies live one
    # level down, and reading the envelope instead reports "nothing gated" for
    # a session that is in fact blocking every entry.
    body = _get(f'{API_URL}/live/status?{qs}', timeout=30) or {}
    state = body.get('state') or body
    gates = state.get('gatedRecently') or state.get('gated_recently') or {}
    log = [l for l in (state.get('log') or []) if l.get('action') in ('SKIP', 'DRY_RUN', 'BUY', 'SELL')]
    return {
        'eoa': eoa,
        'running': body.get('running'),
        'strategyId': (body.get('config') or {}).get('strategyId'),
        'minMinutesToClose': (body.get('config') or {}).get('minMinutesToClose'),
        'gated_recently': gates,
        'total_gated': sum((g or {}).get('count', 0) for g in gates.values()) if isinstance(gates, dict) else 0,
        'hint': ('Every entry blocked by "resolves too soon" means the leaders trade markets '
                 'that resolve inside the strat\'s MIN TIME TO CLOSE window (default 60m). '
                 'Lower it per strat (or set 0) — but check pm_trader first: copying 5-minute '
                 'candle bots one poll late is a measured loss.'),
        'recent_decisions': log[:20],
    }


TOOLS = {
    'pm_health': {
        'description': 'Is the module up? API health, the trader-sync schedule, and the '
                       'background backtest worker (last pass, next pass, strats covered).',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_health,
    },
    'pm_markets': {
        'description': 'Polymarket markets — the busiest open ones by 24h volume, or a text '
                       'search when `query` is given.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'text to search for (optional)'},
            'limit': {'type': 'integer', 'description': 'rows to return (default 20)'},
        }},
        'handler': _t_markets,
    },
    'pm_top_traders': {
        'description': 'The leaderboard: most profitable active traders over a window, with '
                       'PnL, win rate, Sharpe and volume. This is what strats seed their '
                       'watchlists from. Slow (minutes) on a cold cache.',
        'inputSchema': {'type': 'object', 'properties': {
            'days': {'type': 'integer', 'description': 'window in days (default 7, max 30)'},
            'limit': {'type': 'integer', 'description': 'traders to return (default 20)'},
            'category': {'type': 'string', 'description': 'politics|sports|crypto|… (optional)'},
        }},
        'handler': _t_top_traders,
    },
    'pm_trader': {
        'description': "One trader's recent flow and open positions, plus the share of it that "
                       'is sub-hour Up/Down candle games — the single fact that decides whether '
                       'a copy strat can trade this leader at all.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x… wallet address'},
            'limit': {'type': 'integer', 'description': 'activity rows to scan (default 100, max 500)'},
        }, 'required': ['address']},
        'handler': _t_trader,
    },
    'pm_strats': {
        'description': 'The strategies this console runs: watchlist, capital, trade filters, '
                       'trader FILTER, time-to-close gate, sizing model.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_strats,
    },
    'pm_backtests': {
        'description': 'Cached backtests from the background worker (every strat, same window, '
                       'refreshed every 2h). Each carries a FUNNEL: entries observed → copied, '
                       'with a per-gate breakdown of what blocked the rest. Read the funnel '
                       'before concluding a strat is "flat" — it is usually "blocked". Each '
                       'also carries FORWARD: the same strat replayed over the PREVIOUS window '
                       'with no knowledge of this one, and the verdict of the pair — '
                       'held / faded / recovered / no-edge / stalled / untested. Only '
                       'forward.confirmed means "made money then, and still is"; ranking by '
                       'pnl alone ranks by a single window.',
        'inputSchema': {'type': 'object', 'properties': {
            'days': {'type': 'integer', 'description': 'window in days (default 1 — what the worker runs)'},
            'strat': {'type': 'string', 'description': 'filter by strat id / template slug (optional)'},
        }},
        'handler': _t_backtests,
    },
    'pm_backtest_run': {
        'description': 'Replay every published strat NOW instead of waiting for the next '
                       'pass. The replay runs over the server\'s cached trader history and '
                       'costs no upstream requests; pass refresh=true to top that cache up '
                       'first (that part takes minutes and spends the data-api budget). '
                       'wait=false queues it and returns immediately.',
        'inputSchema': {'type': 'object', 'properties': {
            'wait': {'type': 'boolean', 'description': 'false = queue and return (default true)'},
            'refresh': {'type': 'boolean',
                        'description': 'fetch newer trader history before replaying (default false)'},
        }},
        'handler': _t_backtest_run,
    },
    'pm_live_sessions': {
        'description': 'Live copy-engine sessions for the owner wallet: which strats are '
                       'running, whether they are executing or dry-running, capital, account '
                       'value and open positions.',
        'inputSchema': {'type': 'object', 'properties': {
            'eoa': {'type': 'string', 'description': 'wallet (default: the deployment owner)'},
        }},
        'handler': _t_live_sessions,
    },
    'pm_live_gates': {
        'description': 'Why the live engine is not copying: the per-gate tally of blocked '
                       'entries over the last 30 minutes plus its recent decisions. The first '
                       'thing to call when a session is "running but does nothing".',
        'inputSchema': {'type': 'object', 'properties': {
            'eoa': {'type': 'string', 'description': 'wallet (default: the deployment owner)'},
            'strategyId': {'type': 'string', 'description': 'one strat (default: any running session)'},
        }},
        'handler': _t_live_gates,
    },
}


# ── JSON-RPC 2.0 ──

def _result(id_, result: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _call_tool(id_, params: dict) -> dict:
    name = str(params.get('name') or '')
    tool = TOOLS.get(name)
    if not tool:
        return _error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = tool['handler'](args)
    except Exception as e:
        # Tool failures are *successful* JSON-RPC responses carrying isError —
        # per MCP spec — so the calling model reads the message and retries.
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name} failed: {type(e).__name__}: {e}'}],
                             'isError': True})
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object with a method')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        client_ver = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'polymarket', 'version': _version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': [
            {'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]})
    if method == 'tools/call':
        return _call_tool(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def _version() -> str:
    try:
        return json.load(open(os.path.join(MODULE, 'config.json'))).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


# ── transports ──

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()


def serve_http(port: int):
    """Streamable HTTP without SSE: one JSON-RPC message per POST /mcp."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    paths = ('/mcp', f'{BASE_PATH.rstrip("/")}/mcp')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def do_GET(self):
            if self.path.rstrip('/').endswith('/health'):
                return self._send(200, b'ok', 'text/plain')
            self._send(405, b'POST JSON-RPC 2.0 messages to this endpoint', 'text/plain')

        def do_POST(self):
            if self.path.split('?')[0].rstrip('/') not in paths:
                return self._send(404, b'not found', 'text/plain')
            n = int(self.headers.get('content-length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'')
            except Exception:
                return self._send(400, _error(None, -32700, 'parse error: body is not valid JSON'))
            resp = handle(body)
            if resp is None:  # notification — nothing to answer
                return self._send(202, b'', 'text/plain')
            self._send(200, resp)

        def log_message(self, *a):  # quiet: pm2 logs are for real events
            pass

    print(f'polymarket mcp on :{port} — POST {paths[1]}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        i = argv.index('--port') + 1 if '--port' in argv else -1
        port = int(argv[i] if i > 0 else os.environ.get('MCP_PORT', 50092))
        serve_http(port)
    else:
        serve_stdio()
