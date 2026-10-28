"""oracle — the GPU price prediction game, played against this module's markets.

forecast.py is the game and knows nothing about compute. This file is the
compute half: it turns one fan-out over every market into a handful of price
*indexes*, ticks them into the board on a timer, and hands the console and
the MCP tools one shape to read.

An index is `gpu:<model>` → the **median per-GPU $/hr** of every available
offer for that card, across every market that answered. Median, because the
cheapest row on a board of 1500 is usually a $0.02 junk listing and the mean
is whatever the most expensive host typed; per-GPU, because an 8x H100 node
and a 1x H100 box are the same card at the same rate. A model is only indexed
when at least `MIN_OFFERS` offers price it — fewer is one host's whim, not a
market.

The ticker runs inside the API server (`start()`), reads the markets with the
operator's own keys like any other search, and never spends anything.
"""

import json
import os
import statistics
import threading
import time

import forecast as F
from providers.base import ProviderError

STATE = os.path.expanduser('~/.mod/compute')
DB = os.environ.get('COMPUTE_ORACLE_DB') or os.path.join(STATE, 'oracle.db')
TICK_EVERY = int(os.environ.get('COMPUTE_TICK_SEC', '1200'))    # 20 min
MIN_OFFERS = 5
MAX_SERIES = 16
SPARK_POINTS = 96

_board = None
_lock = threading.Lock()
_ticker = {'running': False, 'last': None, 'last_error': None, 'next': None}


def board():
    global _board
    with _lock:
        if _board is None or _board.path != DB:
            _board = F.Board(DB, tick_every=TICK_EVERY)
        return _board


def _err(fn, *a, **kw):
    """The game's refusals reach the caller as the module's own error shape."""
    try:
        return fn(*a, **kw)
    except F.ForecastError as e:
        raise ProviderError(str(e), status=e.status) from None


# ── the index ─────────────────────────────────────────────────────────

def index(offers, min_offers=MIN_OFFERS, max_series=MAX_SERIES):
    """Offers → {gpu:<model>: median per-GPU $/hr}, with how each was built."""
    from hub import gpu_model
    per = {}
    for o in offers:
        if o.get('kind') not in (None, 'gpu') or not o.get('usd_hr'):
            continue
        if o.get('available') is False:
            continue
        m = gpu_model(o.get('gpu'))
        if not m:
            continue
        per.setdefault(m, []).append((o['usd_hr'] / max(int(o.get('gpus') or 1), 1),
                                      o.get('provider')))
    picked = sorted(((m, v) for m, v in per.items() if len(v) >= min_offers),
                    key=lambda kv: -len(kv[1]))[:max_series]
    values, meta = {}, {}
    for m, rows in picked:
        prices = sorted(p for p, _ in rows)
        q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0]] * 3
        s = 'gpu:' + m
        values[s] = round(statistics.median(prices), 4)
        meta[s] = json.dumps({'offers': len(prices),
                              'markets': sorted({p for _, p in rows if p}),
                              'cheapest': round(prices[0], 4),
                              'p25': round(q[0], 4), 'p75': round(q[2], 4)})
    return values, meta


def tick(force=False, offers=None):
    """Read every market once, index it, record it, score what came due."""
    b = board()
    last = max((s['t'] for s in b.series()), default=0)
    if not force and time.time() - last < TICK_EVERY * 0.8:
        return {'skipped': True, 'why': f'ticked {int(time.time() - last)}s ago',
                'next_in': int(TICK_EVERY - (time.time() - last))}
    started = time.time()
    report = None
    if offers is None:
        from hub import Hub
        got = Hub().search(kind='gpu', limit=2000, raw=False)
        offers, report = got['offers'], got['providers']
    values, meta = index(offers)
    if not values:
        raise ProviderError('no GPU model had enough priced offers to index — '
                            'are the markets reachable?', status=503)
    out = b.tick(values, meta=meta)
    out['bot_calls'] = b.bots_play()
    out['indexed'] = values
    out['offers'] = len(offers)
    out['took_ms'] = round((time.time() - started) * 1000)
    if report:
        out['providers'] = {k: (v if isinstance(v, int) else 'x') for k, v in report.items()}
    return out


def _loop():
    # A restart inside the interval must not tick twice; tick() checks that.
    time.sleep(5)
    while _ticker['running']:
        try:
            got = tick()
            _ticker['last'] = time.time() if not got.get('skipped') else _ticker['last']
            _ticker['last_error'] = None
        except Exception as e:                   # a dead market is not a dead ticker
            _ticker['last_error'] = f'{type(e).__name__}: {e}'
        _ticker['next'] = time.time() + TICK_EVERY
        time.sleep(TICK_EVERY)


def start():
    """Run the ticker in this process. Idempotent; COMPUTE_ORACLE=0 disables."""
    if os.environ.get('COMPUTE_ORACLE', '1') in ('0', 'false'):
        return False
    if _ticker['running']:
        return True
    _ticker['running'] = True
    threading.Thread(target=_loop, name='oracle-ticker', daemon=True).start()
    return True


# ── what the console and the tools read ──────────────────────────────

def _thin(rows, n=SPARK_POINTS):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    out = [rows[int(i * step)] for i in range(n)]
    out[-1] = rows[-1]
    return out


def _meta(series):
    raw = board().last_meta(series)
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def state(horizon=None, series=None):
    """Everything one screen needs: indexes with sparklines, the board, stats."""
    b = board()
    rows = []
    for s in b.series():
        s['spark'] = [[round(h['t']), h['value']] for h in _thin(b.history(s['series']))]
        s['meta'] = _meta(s['series'])
        s['model'] = s['series'].split(':', 1)[-1].upper()
        rows.append(s)
    rows.sort(key=lambda r: -(r['meta'].get('offers') or 0))
    return {
        'series': rows,
        'horizons': list(F.HORIZONS),
        'board': _err(b.leaderboard, series=series, horizon=horizon)['board'],
        'bots': F.BOTS,
        'min_ranked': F.MIN_RANKED,
        'scoring': {'rule': f'100 x 0.5^(abs % error / {F.HALF_LIFE_PCT}%)',
                    'exact': 100, '5%_off': 50, '10%_off': 25,
                    'rank': f'mean points over scored calls, ranked after '
                            f'{F.MIN_RANKED}',
                    'beat': '% of calls closer than "nothing changes"'},
        'index': 'median per-GPU $/hr of every available offer across every '
                 f'market; a card needs {MIN_OFFERS}+ priced offers to be indexed',
        'ticker': {'every_sec': TICK_EVERY, 'running': _ticker['running'],
                   'last_error': _ticker['last_error'], 'next': _ticker['next']},
        'stats': b.stats(),
    }


def detail(series, days=7):
    """One index: its full history, every open call on it, the last scored."""
    b = board()
    if not b.last(series):
        raise ProviderError(f'no series {series}', status=404)
    since = time.time() - float(days) * 86400
    return {'series': series, 'meta': _meta(series),
            'history': [[round(h['t']), h['value']]
                        for h in _thin(b.history(series, since=since), 400)],
            'open': b.calls(series=series, state='open', limit=200),
            'scored': b.calls(series=series, state='scored', limit=100),
            'void': b.calls(series=series, state='void', limit=100)}


def join(name):
    return _err(board().join, name)


def predict(player, key, series, horizon, value, note=None):
    return _err(board().predict, player, key, series, horizon, value, note=note)


def leaderboard(series=None, horizon=None, days=None):
    since = time.time() - float(days) * 86400 if days else None
    return _err(board().leaderboard, series=series, horizon=horizon, since=since)


def calls(player=None, series=None, state=None, limit=50):
    return {'calls': board().calls(player=player, series=series, state=state,
                                   limit=min(int(limit or 50), 500))}
