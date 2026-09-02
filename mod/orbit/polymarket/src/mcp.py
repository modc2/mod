#!/usr/bin/env python3
"""polymarket mcp — Model Context Protocol server for the polymarket console.

This is the agent's half of the console, and for the COPY DESK it is the
backend: the `pm_copy_*` tools call `/copy/*` on the Rust API, which is exactly
what the browser calls. An agent and a person are looking at one desk — the
copy book lives on the server (api/src/copy.rs), plaintext, so "put $50 on
0xab…" over MCP shows up on the screen at the next poll, and an amount changed
on the screen is what the agent reads next.

What an agent can do here:

  RESEARCH  pm_markets, pm_top_traders, pm_trader — find someone worth copying
            and check the one thing that disqualifies most leaders (sub-hour
            candle flow no poller can copy).
  DECIDE    pm_copy_backtest — replay copying ONE trader over a window, with
            the walk-forward verdict, before any money is committed.
  ALLOCATE  pm_copy_book / pm_copy_allocate / pm_copy_remove /
            pm_copy_rebalance — the desk itself.
  OPERATE   pm_copy_start / pm_copy_stop, pm_live_sessions, pm_live_gates —
            run it and find out why it isn't trading.

SAFETY. There is no order-placing tool, and there never will be: the console
signs real money through the deposit wallet and a mis-prompted agent must not
reach it. The one thing that CAN spend money is `pm_copy_start` with
`autoExecute: true`, and it is refused unless the deployment sets
POLYMARKET_MCP_ALLOW_LIVE=1 — without it, an agent can research, allocate,
backtest and DRY RUN, and a human flips the last switch in the browser. Stopping
is always allowed; it only ever reduces exposure.

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
    'Polymarket COPY DESK: copy individual traders, with a dollar amount '
    'against each name. `pm_copy_book` is the desk and the place to start — '
    'who is copied, with how much, running or not, DRY RUN or real, and what '
    "each has actually made. The normal loop is: pm_top_traders / pm_trader to "
    'find a leader and check what share of their flow is sub-hour Up/Down '
    'candles (a poller cannot copy those — it is a measured loss, and it '
    'disqualifies most high-frequency leaders); pm_copy_backtest to replay '
    'copying THEM specifically, reading `walkForward.verdict` (only "held" '
    'means it worked in the prior window and this one) and `funnel` (how many '
    'of their entries this desk could actually copy, and which gate blocked '
    'the rest — "flat" is usually "blocked"); pm_copy_allocate to put money '
    'against them; pm_copy_start to run it. '
    'Allocation is the whole position-sizing model: the amount on a row is '
    'what the engine budgets against and what the backtest replays with. '
    'Starting DEFAULTS TO DRY RUN — mirrors computed, nothing placed — and '
    'real order placement over MCP is refused unless the deployment opts in '
    'with POLYMARKET_MCP_ALLOW_LIVE=1. There is no order-placing tool at all. '
    'When a session runs but does not fill, call pm_live_gates before '
    'theorising. pm_strats/pm_backtests cover the older multi-trader index '
    'strategies, which are a different thing from the desk.'
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


# Only traders who have filled something in the last N hours count as
# copyable. Same default the console's leaderboard lands on — an agent and a
# person asking "who should I copy" must not get different answers.
DEFAULT_ACTIVE_HOURS = 6


# The ranking metric is parameterized; winRate is the default, matching the
# console's SCORE preset. Keys are the server's sort keys verbatim.
TRADER_SORTS = ('winRate', 'exitEntry', 'sharpe', 'pnl', 'volume', 'history')


def _t_top_traders(args):
    days = int(args.get('days') or 7)
    limit = int(args.get('limit') or 20)
    hours = args.get('active_hours')
    hours = DEFAULT_ACTIVE_HOURS if hours is None else float(hours)
    sort = str(args.get('sort') or TRADER_SORTS[0])
    if sort not in TRADER_SORTS:
        sort = TRADER_SORTS[0]
    params = {
        'days': days,
        # The pool the server's warmup actually aggregates — any other value is
        # a different cache key and answers cold.
        'pool': 2000,
        'paged': '1',
        'pageSize': min(max(limit, 1), 100),
        'page': 0,
        'sort': sort,
        'order': 'desc',
        'category': str(args.get('category') or ''),
    }
    if hours > 0:
        params['maxLastTradeHrs'] = hours
    # Track-record floor. A long `days` over a wallet that opened last week is
    # mostly a flat line; this is how a caller demands N days of record behind
    # the names it gets back. Unresolved ages are kept, not cut.
    min_history = float(args.get('min_history_days') or 0)
    if min_history > 0:
        params['minHistoryDays'] = min_history
    # Paged reads answer from the warm cache and never trigger an aggregation:
    # the filters, the sort and the row count all run over the cached payload.
    paged = _get(f'{API_URL}/active-traders?{urllib.parse.urlencode(params)}', timeout=60)
    if isinstance(paged, dict) and not paged.get('cold'):
        return {
            'traders': paged.get('traders') or [],
            'total': paged.get('total'),
            'activeHours': hours if hours > 0 else None,
            'dormantHidden': paged.get('activityDropped'),
            'minHistoryDays': min_history or None,
            'tooNewHidden': paged.get('historyDropped'),
            'candidatePool': paged.get('candidatePool'),
            'source': paged.get('source'),
            'syncedAt': paged.get('syncedAt'),
        }
    # Cold cache — nothing to serve, so pay for the aggregation once. Minutes.
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
            # Polymarket's taker fee, already deducted from `pnl`. Carried
            # separately because it is the usual reason a strat with a real
            # edge still loses: the fee is `rate x p x (1-p) x shares`, 4-7%
            # by category, and it PEAKS at 50c. A strat trading coin flips in
            # crypto markets pays ~3.5% of notional per side.
            'fees': bt.get('fees'), 'fee_bps_of_volume': bt.get('feeBps'),
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


# ── the copy desk ──
#
# Every tool below is one call to /copy/* — the same routes the browser's COPY
# DESK calls. Nothing here keeps its own state.


def _delete(url: str, timeout: float = 60.0):
    return _post(url, None, method='DELETE', timeout=timeout)


def _copy_eoa(args) -> str:
    """Whose sessions to act on. The book itself is deployment-wide; sessions
       are per wallet, and on a single-owner deployment that wallet is the
       owner unless the caller says otherwise."""
    return str(args.get('eoa') or '').strip() or _owner()


def _copy_url(path: str, eoa: str | None = None) -> str:
    qs = f'?eoa={urllib.parse.quote(eoa)}' if eoa else ''
    return f'{API_URL}/copy{path}{qs}'


def _summarize_book(book: dict) -> dict:
    """The desk, trimmed to what a model should reason about. The raw response
       carries a full engine snapshot per row; most of it is noise here."""
    rows = []
    for a in book.get('allocations') or []:
        live = a.get('live') or {}
        ledger = live.get('ledger') or {}
        rows.append({
            'address': a.get('address'),
            'name': a.get('name'),
            'allocationUsd': a.get('allocationUsd'),
            'enabled': a.get('enabled'),
            'strategyId': a.get('strategyId'),
            'params': a.get('params') or {},
            'notes': a.get('notes'),
            # The state that decides what a follow-up question should be.
            'running': live.get('running', False),
            # DRY RUN vs real money — the answer to most "it isn't trading"
            # questions, and the first thing to check before any other theory.
            'autoExecute': live.get('autoExecute', False),
            'ordersPlaced': live.get('ordersPlaced'),
            # Realized only: open positions are marked at the last observed
            # price, which reads HIGH because leaders sell winners and let
            # losers expire quietly.
            'realizedPnl': ledger.get('realized'),
            'lastFillAt': ledger.get('lastFillAt'),
            'error': live.get('error'),
        })
    return {
        'bankroll': book.get('bankroll'),
        'totals': book.get('totals'),
        'traders': rows,
    }


def _t_copy_book(args):
    eoa = _copy_eoa(args)
    book = _get(_copy_url('/book', eoa), timeout=30)
    out = _summarize_book(book)
    out['eoa'] = eoa
    t = book.get('totals') or {}
    if t.get('running') and not t.get('executing'):
        out['note'] = ('every running session is in DRY RUN — mirrors are computed and '
                       'nothing is placed. That is the default and it is deliberate.')
    if (t.get('unallocatedUsd') or 0) < 0:
        out['warning'] = ('more is allocated than the bankroll says exists — the engine budgets '
                          'per trader, so this over-commits the account')
    return out


def _t_copy_allocate(args):
    """Add a trader to the book, or change what they're copied with."""
    body = {'address': _req(args, 'address'), 'allocationUsd': float(args['allocationUsd'])}
    for k in ('label', 'notes'):
        if args.get(k) is not None:
            body[k] = str(args[k])
    if args.get('enabled') is not None:
        body['enabled'] = bool(args['enabled'])
    if isinstance(args.get('params'), dict):
        body['params'] = args['params']
    eoa = _copy_eoa(args)
    res = _post(_copy_url('/allocations', eoa), body)
    return {
        'ok': res.get('ok'),
        'allocation': res.get('allocation'),
        'strategyId': res.get('strategyId'),
        # A running session picks the new size up immediately rather than at
        # the next manual restart — worth saying, because it means an
        # allocation change is a live change.
        'reconfiguredRunningSession': res.get('reconfigured'),
        'book': _summarize_book(res.get('book') or {}),
        'next': ('backtest them with pm_copy_backtest before starting, then pm_copy_start '
                 '(DRY RUN by default)'),
    }


def _t_copy_remove(args):
    eoa = _copy_eoa(args)
    addr = _req(args, 'address')
    res = _delete(_copy_url(f'/allocations/{urllib.parse.quote(addr)}', eoa))
    return {'ok': res.get('ok'), 'removed': res.get('removed'),
            'stoppedSession': res.get('stopped'),
            'book': _summarize_book(res.get('book') or {})}


def _t_copy_rebalance(args):
    eoa = _copy_eoa(args)
    body = {'bankroll': float(args['bankroll']), 'mode': str(args.get('mode') or 'equal')}
    res = _post(_copy_url('/rebalance', eoa), body)
    return {'ok': res.get('ok'), 'reconfiguredRunningSessions': res.get('reconfigured'),
            'book': _summarize_book(res.get('book') or {})}


def _t_copy_backtest(args):
    """How would copying ONE trader have gone?

       The card comes from the same worker, the same engine and the same window
       as every other backtest — the desk's leaders are replayed as identity
       strats, which is literally the object the live engine runs."""
    addr = _req(args, 'address').lower()
    if not addr.startswith('0x') or len(addr) != 42:
        raise ValueError(f'not an address: {addr}')
    days = int(args.get('days') or 1)
    sid = f'copy-{addr[2:]}'

    book = _get(_copy_url('/book'), timeout=30)
    known = {a.get('address') for a in book.get('allocations') or []}
    added = False
    if addr not in known:
        if not args.get('add'):
            return {'address': addr,
                    'error': f'{addr} is not on the copy desk, so there is nothing to replay',
                    'fix': 'call pm_copy_allocate first, or pass add=true to add them at $100 '
                           'and backtest that'}
        _post(_copy_url('/allocations'), {'address': addr,
                                          'allocationUsd': float(args.get('allocationUsd') or 100)})
        added = True

    # A trader added seconds ago has no card yet; a synchronous pass makes one.
    # It replays out of the server's cached feeds — CPU, not upstream requests.
    if added or args.get('run'):
        # A pass only replays the windows in the manifest (always 1 day, plus
        # whatever the console last asked for). Forcing a pass for a window
        # nobody published would run and still produce nothing — so add the
        # window first, carrying the existing roster through untouched.
        man = _read_manifest()
        windows = [w for w in (man.get('windows') or [man.get('days') or 1]) if w]
        if days not in windows:
            _post(_hub(), {'days': man.get('days') or 1,
                           'windows': sorted(set(windows + [days])),
                           'strats': man.get('strats') or []})
        _post(_hub(), method='PUT')

    cache = _get(_hub(f'?days={days}'), timeout=30)
    bt = (cache.get('results') or {}).get(sid)
    if not bt:
        return {'address': addr, 'strategyId': sid, 'days': days, 'backtest': None,
                'note': ('this window has no card for that trader yet'
                         if args.get('run') or added else
                         'no replay for this window yet — pass run=true to force a pass now, '
                         'or wait for the worker (pm_health shows its schedule)'),
                'why': ('a replay needs the leader\'s trade history in the server\'s feed '
                        'store; a trader added minutes ago may still be warming. '
                        'pm_health shows the fetch loop\'s coverage.')}
    f = bt.get('funnel') or {}
    s = bt.get('settlement') or {}
    fwd = bt.get('forward') or {}
    return {
        'address': addr, 'strategyId': sid, 'days': bt.get('days'),
        'addedToDesk': added,
        'pnl': bt.get('pnl'), 'roi': bt.get('roi'), 'trades': bt.get('trades'),
        # Already inside `pnl`; broken out because it is the cost a copy of a
        # busy leader pays whether or not the leader's edge survives it.
        'fees': bt.get('fees'), 'feeBpsOfVolume': bt.get('feeBps'),
        'capital': bt.get('capital'), 'ranAt': bt.get('at'), 'note': bt.get('note'),
        # The number alone is one window. This is whether it survived being
        # tested on a window it didn't get to see. `held` is the only pass.
        'walkForward': {'verdict': fwd.get('verdict'), 'confirmed': fwd.get('ok'),
                        'priorPnl': fwd.get('pnl'), 'priorTrades': fwd.get('trades')} if fwd else None,
        # How much of the leader's flow this actually copies, and what blocked
        # the rest. A leader whose entries are all gated is not a leader this
        # desk can copy, whatever their own P&L says.
        'funnel': {'observed': f.get('observed'), 'copied': f.get('executed'),
                   'blocked_by_filters': f.get('gated'), 'outranked': f.get('outranked'),
                   'unplaceable': f.get('skipped'), 'reasons': f.get('reasons')} if f else None,
        'settlement': {'resolved': s.get('resolved'), 'unverified': s.get('marked'),
                       'unverifiedUsd': s.get('markedUsd')} if s else None,
        'warming': bt.get('warming'),
    }


def _copytrades(path: str = '') -> str:
    return f'{APP_URL}{BASE_PATH}/api/copytrades{path}'


def _t_copy_trades(args):
    """What the traders I copy actually did, and what I actually got.

       The one question a per-trader backtest and a live status line both dodge:
       of the trades the leaders made, how many landed in MY wallet? The answer
       is a JOIN — my on-chain fills matched to the leader trade they mirror by
       market, side and time (a fill carries no leader tag, so nothing upstream
       links them) — and it reports three numbers:

         coverage      copied / their trades. A desk that looks busy and copies
                       3 of 60 is the failure this module keeps re-finding.
         medianLagSec  how far behind their fill mine landed.
         avgSlipCents  what that lag cost, signed against the leader's price.

       `q` filters the feed the way a person talks: "big buys on crypto under
       30c", "missed longshots", "politics, not candles". The answer echoes
       what the sentence was READ as (`query.chips`) and what of it can be
       armed as a real copy gate (`query.gate`) — arm it with pm_copy_allocate
       params:{marketQuery, tradeFilters}. Reads only; places nothing."""
    days = max(1, min(30, int(args.get('days') or 7)))
    params = {'days': str(days)}
    q = str(args.get('q') or '').strip()
    if q:
        params['q'] = q
    if args.get('eoa'):
        params['eoa'] = str(args['eoa'])
    out = _get(_copytrades('?' + urllib.parse.urlencode(params)), timeout=600) or {}
    s = out.get('summary') or {}
    limit = max(1, min(200, int(args.get('limit') or 40)))
    rows = (out.get('rows') or [])[:limit]
    cov = s.get('coverage')
    if not s.get('leader'):
        verdict = ('none of the traders you copy traded in this window — or their feeds '
                   'have not been fetched yet (see `warming`)')
    elif not s.get('copied'):
        verdict = (f"you copied NONE of their {s.get('leader')} trades. In TEST that is "
                   'expected; in LIVE, pm_copy_backtest\'s funnel names the gate')
    else:
        verdict = (f"you got {s.get('copied')} of {s.get('leader')} trades "
                   f"({round((cov or 0) * 100)}%), median {s.get('medianLagSec')}s behind, "
                   f"{s.get('avgSlipCents')}c worse than their price")
    return {
        'days': out.get('days'),
        'wallet': out.get('wallet'),
        'verdict': verdict,
        'summary': s,
        'byLeader': out.get('leaders'),
        # Fills of mine with no leader behind them — engine exits, or hand
        # trades from the same wallet. Reported, never quietly attributed.
        'unattributedFills': s.get('unattributed'),
        'warming': out.get('warming'),
        'query': out.get('query'),
        'filtered': out.get('filtered'),
        'rows': rows,
        'rowsShown': len(rows),
    }


def _basket(path: str = '') -> str:
    return f'{APP_URL}{BASE_PATH}/api/basket{path}'


def _t_copy_basket(args):
    """Copy a SET of traders, a different amount against each, replayed as one
       basket over N days.

       This is not N calls to pm_copy_backtest glued together, and the
       difference is the point. Each leg is replayed on its OWN capital — which
       is what the desk runs (one allocation = one live session with its own
       budget) — and then the honesty numbers are about the SPLIT:

         legsTrading/legs  how many legs actually placed an order. The rest
                           held cash for the whole window.
         idleUsd           the dollars in those legs. An underfunded leg is not
                           a small position, it is NO position: its
                           proportional mirror lands under the order floor.
         floors            the smallest amount at which each leg would trade
                           at all (floors=true).
         comparison        the same total divided EVENLY. If your amounts don't
                           beat that, the conviction in your sizing didn't pay
                           for itself in this window (compare=true).

       Places nothing and writes nothing — sizing a basket is not committing to
       it. pm_copy_allocate is how a leg becomes real."""
    legs = args.get('legs')
    from_desk = bool(args.get('fromDesk'))
    if not legs and not from_desk:
        raise ValueError('legs required — [{"address": "0x…", "allocationUsd": 250}, …] '
                         'or fromDesk=true to replay what the desk already holds')
    body = {
        'days': int(args.get('days') or 7),
        'compare': bool(args.get('compare')),
        'floors': bool(args.get('floors')),
    }
    if from_desk:
        body['fromDesk'] = True
    else:
        body['legs'] = legs
    if args.get('total'):
        body['total'] = float(args['total'])
    if args.get('split'):
        body['split'] = str(args['split'])
    if args.get('ladder'):
        body['ladder'] = [float(x) for x in args['ladder']][:10]

    out = _post(_basket(), body, timeout=600)
    p = (out or {}).get('portfolio') or {}
    idle = p.get('idleUsd') or 0
    # The one-line reading of the run, so a caller that only looks at `pnl`
    # still can't miss "a third of your money never traded".
    if p.get('legsTrading') == 0:
        verdict = ('this basket copied NOTHING — every leg was refused. '
                   'Re-run with floors=true for the smallest amount each leg needs.')
    elif idle > 0:
        verdict = (f"${idle:,.0f} across {(p.get('legs') or 0) - (p.get('legsTrading') or 0)} "
                   'leg(s) never traded — that capital sat in cash for the whole window')
    elif (p.get('confidence') or 1) < 0.7:
        verdict = (f"only {round((p.get('confidence') or 0) * 100)}% of this result is a settled "
                   'market; the rest values inventory at the last price a leader printed, '
                   'which forgives losers — read it as an upper bound')
    else:
        verdict = 'every funded leg traded'
    out['verdict'] = verdict
    return out


def _live_allowed() -> bool:
    return (os.environ.get('POLYMARKET_MCP_ALLOW_LIVE') or '').strip() == '1'


def _t_copy_start(args):
    eoa = _copy_eoa(args)
    auto = bool(args.get('autoExecute'))
    if auto and not _live_allowed():
        # Refused loudly rather than silently downgraded: an agent told "it
        # started" would report a live desk that is dry-running.
        return {'error': 'real order placement is not available over MCP on this deployment',
                'why': 'POLYMARKET_MCP_ALLOW_LIVE is not set to 1',
                'what_you_can_do': 'start in DRY RUN (omit autoExecute) — the engine computes '
                                   'every mirror it would place and places none. A human turns '
                                   'on real execution from the COPY DESK in the browser.'}
    body = {'eoa': eoa, 'autoExecute': auto}
    if args.get('address'):
        body['address'] = str(args['address'])
    res = _post(_copy_url('/start'), body, timeout=120)
    return {'ok': res.get('ok'), 'mode': res.get('mode'), 'eoa': eoa,
            'tradingWallet': res.get('proxyAddress'),
            'started': res.get('started'),
            'book': _summarize_book(res.get('book') or {}),
            'next': 'pm_live_gates says why a running session is not filling'}


def _t_copy_stop(args):
    eoa = _copy_eoa(args)
    body = {'eoa': eoa}
    if args.get('address'):
        body['address'] = str(args['address'])
    res = _post(_copy_url('/stop'), body, timeout=120)
    return {'ok': res.get('ok'), 'stopped': res.get('stopped'),
            'book': _summarize_book(res.get('book') or {})}


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
        'description': 'The leaderboard: best active traders over a window, ranked by win rate '
                       'by default (sort parameterizes it: winRate, exitEntry = avg exit÷entry '
                       'price on closed trades, sharpe, pnl, volume). `winRate` is the share of '
                       'positions the market SETTLED in the window that returned more than they '
                       'cost, and `decidedPositions` is its denominator; `-1` means nothing '
                       'settled, not zero. This is what strats seed their '
                       'watchlists from. Answers from the warm cache (the server re-aggregates '
                       'on its own schedule); only a cold cache is slow (minutes). Defaults to '
                       'traders who have traded in the last 6 hours — a wallet that went quiet '
                       'is one you cannot copy, however good its 7-day record looks.',
        'inputSchema': {'type': 'object', 'properties': {
            'days': {'type': 'integer', 'description': 'window in days (default 7, max 30)'},
            'limit': {'type': 'integer', 'description': 'traders to return (default 20, max 100)'},
            'category': {'type': 'string', 'description': 'politics|sports|crypto|… (optional)'},
            'active_hours': {'type': 'number', 'description': 'only traders whose last trade is '
                                                             'within this many hours (default 6; '
                                                             '0 = the whole board, dormants too)'},
            'min_history_days': {'type': 'number', 'description': "track-record floor: only traders "
                                                                 "whose FIRST-ever trade is at least "
                                                                 "this many days old (default 0 = off). "
                                                                 "Set it to `days` on a long window — a "
                                                                 "wallet that opened last week can top the "
                                                                 "30-day board on days it did not exist"},
            'sort': {'type': 'string', 'description': 'ranking metric: winRate (default; share '
                                                      'of SETTLED positions that made money — read '
                                                      'it with decidedPositions, a rate off five '
                                                      'legs is noise) | exitEntry | sharpe | pnl | '
                                                      'volume | history (longest track record first)'},
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
    'pm_copy_book': {
        'description': 'THE COPY DESK — which individual traders this deployment copies and '
                       'with how much. One row per leader: allocation in dollars, whether a '
                       'session is running, whether it is placing REAL orders or dry-running, '
                       'orders placed, realized P&L and last fill. This is the same book the '
                       'browser shows; start here for anything about copy-trading.',
        'inputSchema': {'type': 'object', 'properties': {
            'eoa': {'type': 'string', 'description': 'wallet whose sessions to report '
                                                     '(default: the deployment owner)'},
        }},
        'handler': _t_copy_book,
    },
    'pm_copy_allocate': {
        'description': 'Copy a trader with a given number of dollars — adds them to the copy '
                       'book, or changes the amount if they are already on it (idempotent by '
                       'address: one leader, one allocation, one session). The amount IS the '
                       'position sizing: the engine budgets against it and the backtest '
                       'replays with it. Adding costs nothing and places nothing — starting is '
                       'a separate call. `params` optionally overrides the identity template '
                       '(minTrade, maxTrade, maxPerCycle, maxOpenPositions, pollMinutes, '
                       'backtestDays, sizing flow|bankroll, turnover, stopLoss, takeProfit, '
                       'minMinutesToClose, maxTradeAgeSec, marketQuery, tradeFilters) and is '
                       'a PATCH — omitted knobs keep their current value. The two GATE knobs '
                       'are how one leader is copied only in part: marketQuery picks the '
                       'markets by title ("bitcoin, btc, ethereum" — commas are OR, spaces are '
                       'AND), tradeFilters picks the trades inside them ({sides, minPrice, '
                       'maxPrice, minNotional, maxNotional}). pm_copy_trades q=... compiles a '
                       'plain-language sentence into exactly that pair.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x… trader to copy'},
            'allocationUsd': {'type': 'number', 'description': 'dollars to copy them with'},
            'label': {'type': 'string', 'description': 'display name (optional)'},
            'notes': {'type': 'string', 'description': 'why you are copying them (optional)'},
            'enabled': {'type': 'boolean', 'description': 'false pauses them without forgetting them'},
            'params': {'type': 'object', 'description': 'per-trader overrides on the identity '
                                                       'template, including the gate pair '
                                                       '{marketQuery, tradeFilters}'},
            'eoa': {'type': 'string', 'description': 'wallet whose running session to reconfigure'},
        }, 'required': ['address', 'allocationUsd']},
        'handler': _t_copy_allocate,
    },
    'pm_copy_remove': {
        'description': 'Stop copying a trader: stops their session and drops them from the '
                       'book. Their realized P&L stays in the engine ledger.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x… trader to stop copying'},
            'eoa': {'type': 'string', 'description': 'wallet (default: the deployment owner)'},
        }, 'required': ['address']},
        'handler': _t_copy_remove,
    },
    'pm_copy_rebalance': {
        'description': 'Split a bankroll across every enabled trader on the desk. '
                       'mode=equal gives everyone the same dollars; mode=weighted rescales '
                       'the amounts already set, so conviction survives a deposit. Running '
                       'sessions pick up their new size immediately.',
        'inputSchema': {'type': 'object', 'properties': {
            'bankroll': {'type': 'number', 'description': 'total dollars to split'},
            'mode': {'type': 'string', 'description': 'equal (default) | weighted'},
            'eoa': {'type': 'string', 'description': 'wallet (default: the deployment owner)'},
        }, 'required': ['bankroll']},
        'handler': _t_copy_rebalance,
    },
    'pm_copy_backtest': {
        'description': 'How would copying ONE trader have gone? Replays the identity strat for '
                       'that leader — the exact object the live engine runs — over a window, '
                       'and returns pnl/roi/trades plus two things worth more than the pnl: '
                       'walkForward (the same replay over the PREVIOUS window, judged '
                       'held/faded/recovered/no-edge — only "held" means it worked then AND '
                       'since) and funnel (how many of their entries this desk could actually '
                       'copy, and which gate blocked the rest — a leader whose flow is all '
                       'gated cannot be copied whatever their own P&L says). Ask this BEFORE '
                       'starting anyone.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': '0x… trader'},
            'days': {'type': 'integer', 'description': 'window in days (default 1)'},
            'add': {'type': 'boolean', 'description': 'add them to the desk if absent, so they '
                                                      'can be replayed (default false)'},
            'allocationUsd': {'type': 'number', 'description': 'amount to add them with when '
                                                              'add=true (default 100)'},
            'run': {'type': 'boolean', 'description': 'force a replay pass now instead of '
                                                     'reading the cached card (default false)'},
        }, 'required': ['address']},
        'handler': _t_copy_backtest,
    },
    'pm_copy_trades': {
        'description': 'MY COPY TRADES — every trade the traders on the desk made, every '
                       'on-chain fill of mine, joined by market, side and time. Answers the '
                       'question status lines dodge: what share of their flow did I actually '
                       'get (coverage), how far behind (medianLagSec), and what that cost '
                       '(avgSlipCents). Fills with no leader behind them (engine stop-loss / '
                       'take-profit exits, hand trades) are reported as unattributedFills, '
                       'never silently credited to a leader. `q` filters it in plain language '
                       '— "big buys on crypto under 30c", "missed longshots", "politics, not '
                       'candles" — and the answer echoes how the sentence was read plus the '
                       'gate half of it that can be armed with pm_copy_allocate '
                       'params:{marketQuery, tradeFilters}. Reads only.',
        'inputSchema': {'type': 'object', 'properties': {
            'days': {'type': 'integer', 'description': 'window in days (default 7, max 30)'},
            'q': {'type': 'string', 'description': 'plain-language filter over the feed: a '
                                                  'topic (crypto, politics, sports, candles), '
                                                  'a side (buys/sells), a price band (under '
                                                  '30c, longshots, coin flips), a size (over '
                                                  '$500, dust), a window (last 3 days), a '
                                                  'status (missed, copied). Commas mean OR.'},
            'limit': {'type': 'integer', 'description': 'rows to return (default 40, max 200)'},
            'eoa': {'type': 'string', 'description': 'wallet (default: the deployment owner)'},
        }},
        'handler': _t_copy_trades,
    },
    'pm_copy_basket': {
        'description': 'Copy a SET of traders with a DIFFERENT amount against each, replayed '
                       'as one basket over N days. Answers the question a per-trader backtest '
                       "cannot: given these names and this much money, how should it be split? "
                       'Each leg is replayed on its own capital (which is how the desk runs '
                       'them — one allocation, one session, one budget) and the answer reports '
                       'legsTrading/legs and idleUsd: an underfunded leg does not take a small '
                       'position, it takes NO position, because its proportional mirror lands '
                       'under the order floor. floors=true names the smallest amount each leg '
                       'needs; compare=true scores your split against dividing the same total '
                       'evenly. Places nothing and writes nothing — use pm_copy_allocate to '
                       'commit a leg.',
        'inputSchema': {'type': 'object', 'properties': {
            'legs': {'type': 'array', 'description': 'the basket: [{address, allocationUsd, '
                                                     'label?, params?}] — params is the same '
                                                     'per-allocation patch pm_copy_allocate takes',
                     'items': {'type': 'object', 'properties': {
                         'address': {'type': 'string'},
                         'allocationUsd': {'type': 'number'},
                         'label': {'type': 'string'},
                         'params': {'type': 'object'},
                     }, 'required': ['address']}},
            'fromDesk': {'type': 'boolean', 'description': 'replay the copy desk as it stands, '
                                                          'instead of naming legs'},
            'days': {'type': 'integer', 'description': 'window in days (default 7, max 30)'},
            'total': {'type': 'number', 'description': 'rescale the legs to this total, keeping '
                                                      'their proportions'},
            'split': {'type': 'string', 'description': '"equal" to divide `total` evenly instead '
                                                      'of keeping proportions'},
            'compare': {'type': 'boolean', 'description': 'also replay the same total split '
                                                         'EVENLY and report the edge'},
            'floors': {'type': 'boolean', 'description': 'also find the smallest amount at which '
                                                        'each leg trades at all'},
            'ladder': {'type': 'array', 'items': {'type': 'number'},
                       'description': 'also replay the whole split at these totals'},
        }},
        'handler': _t_copy_basket,
    },
    'pm_copy_start': {
        'description': 'Start copying — one trader with `address`, or every enabled trader on '
                       'the desk without it. DEFAULTS TO DRY RUN: the engine computes every '
                       'mirror it would place and places none, which is what you want for '
                       'checking that a leader actually produces copyable entries. '
                       'autoExecute=true means REAL ORDERS with real money and is refused '
                       'unless the deployment sets POLYMARKET_MCP_ALLOW_LIVE=1 — otherwise a '
                       'human turns that on from the browser.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': 'one trader (default: all enabled)'},
            'autoExecute': {'type': 'boolean', 'description': 'true = place real orders '
                                                             '(default false = DRY RUN)'},
            'eoa': {'type': 'string', 'description': 'wallet to run under (default: the owner)'},
        }},
        'handler': _t_copy_start,
    },
    'pm_copy_stop': {
        'description': 'Stop copying — one trader with `address`, or the whole desk without '
                       'it. The allocation and the ledger survive; only the session ends. '
                       'Always permitted: stopping can only reduce exposure.',
        'inputSchema': {'type': 'object', 'properties': {
            'address': {'type': 'string', 'description': 'one trader (default: the whole desk)'},
            'eoa': {'type': 'string', 'description': 'wallet (default: the deployment owner)'},
        }},
        'handler': _t_copy_stop,
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
