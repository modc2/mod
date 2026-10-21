"""
gemscan.engine — fan out to every source, merge per ticker, heat the
narratives, score the candidates.

The score is an ATTENTION score, not a forecast. It ranks what the internet is
starting to pile into, inside a theme that is currently hot, before it has
already gone vertical — the usual shape of a "moon". Most such things still go
to zero. The flags are there so you see why.

    score (0-100) = attention 30   how many independent feeds surface it, how high
                  + velocity  20   mentions / volume / buy pressure vs its own baseline
                  + narrative 25   heat of the hottest theme it belongs to
                  + momentum  15   up, but not already vertical
                  + size      10   small enough to move
                  - flags          thin liquidity, brand-new pair, sell pressure, penny stock

Caching: three layers. Raw HTTP bodies (gemscan.http, SQLite, per-URL TTL);
`base` — gathered + merged + enriched candidates and raw narrative drivers per
(asset, sources, hn); `result` — a finished scan per full filter key. The two
in-process layers share SCAN_TTL (10 min). fresh=True skips both. The server
warms the asset=all base on boot and every TTL so the app opens from cache.

Typed themes: scan(q='nuclear drones') builds a 14th narrative from the words
typed (gemscan.narratives.custom), runs the live search feeds
(sources.THEME_SOURCES) on top of the cached base, tags every candidate the
words hit — and every candidate whose *meaning* is close (gemscan.semantic:
MiniLM embeddings via the modsearch module; "humanoid robots" finds Figure,
"anon" finds privacy coins). Each gem carries `fit` (0-1 similarity to the
theme). The theme also inherits the nearest built-in narrative's seed stocks
and categories, so "drones" gets the defense names.

Upside: every gem also gets `upside` = score x fit x room-to-run (hard flags —
thin liquidity, brand-new pair, already vertical, penny — cut it), and the scan
names one `pick`: the highest-upside gem with no hard flag. Results are sorted
by upside unless sort='score'. `review()` prices past picks now vs then, so the
"most likely to go up" claim can be checked against what happened.
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import narratives as nar
from . import semantic as sem
from . import sources as src
from .http import HOME

WEIGHTS = {'attention': 30, 'velocity': 20, 'narrative': 25, 'momentum': 15, 'size': 10}
SCANS = HOME / 'scans.jsonl'
SCAN_TTL = int(os.environ.get('GEMFINDER_SCAN_TTL', 600))

_cache = {'base': {}, 'result': {}, 'review': {}}
_cache_lock = threading.Lock()
_inflight = {}


# ── cache ────────────────────────────────────────────────────────────────

def _cached(layer: str, key, build, ttl: Optional[int] = None, fresh: bool = False):
    """Memoise build() under key for ttl seconds -> (value, was_cached).
    Concurrent callers for the same key wait for the one build in flight."""
    ttl = SCAN_TTL if ttl is None else ttl
    with _cache_lock:
        hit = _cache[layer].get(key)
        if hit and not fresh and time.time() - hit[0] < ttl:
            return hit[1], True
        ev = _inflight.get((layer, key))
        owner = ev is None
        if owner:
            ev = _inflight[(layer, key)] = threading.Event()
    if not owner:
        ev.wait(120)
        with _cache_lock:
            hit = _cache[layer].get(key)
        if hit:
            return hit[1], True
        return build(), False
    try:
        val = build()
        with _cache_lock:
            _cache[layer][key] = (time.time(), val)
            if len(_cache[layer]) > 64:  # many typed themes: keep the newest
                for k in sorted(_cache[layer], key=lambda k: _cache[layer][k][0])[:-48]:
                    del _cache[layer][k]
        return val, False
    finally:
        with _cache_lock:
            _inflight.pop((layer, key), None)
        ev.set()


def cache_stats() -> dict:
    with _cache_lock:
        now = time.time()
        return {'ttl_s': SCAN_TTL,
                'base': [{'key': list(k), 'age_s': round(now - v[0]), 'candidates': len(v[1]['cands'])}
                         for k, v in _cache['base'].items()],
                'results': [{'key': list(k), 'age_s': round(now - v[0])} for k, v in _cache['result'].items()]}


def clear_scan_cache() -> dict:
    with _cache_lock:
        n = sum(len(v) for v in _cache.values())
        for layer in _cache.values():
            layer.clear()
    return {'cleared': n}


# ── helpers ──────────────────────────────────────────────────────────────

def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _family(source: str) -> str:
    return 'dexscreener' if source.startswith('dexscreener') else source


def _usd(v) -> str:
    if v is None:
        return '?'
    for div, suf in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs(v) >= div:
            return f'${v / div:.1f}{suf}'
    return f'${v:.0f}'


# ── gather ───────────────────────────────────────────────────────────────

def _run_sources(picked: dict, *args) -> tuple:
    status, signals = {}, []

    def run(item):
        name, s = item
        t = time.time()
        try:
            got = s['fn'](*args)
            return name, got, {'ok': True, 'n': len(got), 's': round(time.time() - t, 2)}
        except Exception as e:
            return name, [], {'ok': False, 'error': f'{type(e).__name__}: {e}'[:200], 's': round(time.time() - t, 2)}

    with ThreadPoolExecutor(max(1, len(picked))) as ex:
        for name, got, st in ex.map(run, picked.items()):
            status[name] = st
            signals += got
    return signals, status


def gather(asset: str = 'all', only: Optional[list] = None) -> tuple:
    """Run candidate sources in parallel -> (signals, {source: {ok, n | error, s}})."""
    picked = {n: s for n, s in src.CANDIDATE_SOURCES.items()
              if (asset == 'all' or s['asset'] == asset) and (not only or n in only)}
    return _run_sources(picked)


def theme_signals(theme: dict, asset: str = 'all') -> tuple:
    """Run the live search feeds for a typed theme -> (signals, status)."""
    picked = {n: s for n, s in src.THEME_SOURCES.items() if asset == 'all' or s['asset'] == asset}
    return _run_sources(picked, theme)


def merge(signals: list, into: Optional[dict] = None) -> dict:
    """One candidate per (asset, ticker). DEX tokens that share a ticker with a
    different contract stay separate — symbols are not unique on-chain.
    `into` adds to an existing candidate dict (and returns it)."""
    out = {} if into is None else into
    dex_addr = {k: c['ids']['address'] for k, c in out.items() if c['ids'].get('address') and '@' not in k[1]}
    for s in signals:
        if not s['symbol'] or s['symbol'] in src.SKIP or len(s['symbol']) > 12:
            continue
        key = (s['asset'], s['symbol'])
        addr = s['ids'].get('address')
        if addr:
            prev = dex_addr.setdefault(key, addr)
            if prev != addr:
                key = (s['asset'], f"{s['symbol']}@{addr[:6]}")
        c = out.setdefault(key, {'asset': s['asset'], 'symbol': key[1], 'name': '', 'text': '',
                                 'signals': [], 'metrics': {}, 'urls': {}, 'ids': {}})
        c['name'] = c['name'] or s['name']
        c['text'] += ' ' + s['text']
        c['signals'].append({'source': s['source'], 'strength': s['strength']})
        for k, v in s['metrics'].items():
            c['metrics'].setdefault(k, v)
        c['urls'][s['source']] = s['url']
        c['ids'].update({k: v for k, v in s['ids'].items() if v})
    return out


def enrich_stocks(cands: dict, limit: int = 120) -> None:
    """Stocks seen only on reddit/trending have no price yet: fill from the chart endpoint."""
    need = [c for c in cands.values() if c['asset'] == 'stock' and 'chg24' not in c['metrics']]
    need.sort(key=lambda c: -sum(x['strength'] + (9 if x['source'] == 'narrative_seed' else 0)
                                 for x in c['signals']))
    if not need:
        return
    charts = src.yahoo_charts([c['symbol'] for c in need[:limit]])
    for c in need[:limit]:
        ch = charts.get(c['symbol'])
        if ch:
            c['name'] = c['name'] or ch['name']
            c['text'] += ' ' + ch['name']
            for k, v in ch['metrics'].items():
                if v is not None:
                    c['metrics'].setdefault(k, v)


def enrich_crypto(cands: dict) -> None:
    """Reddit-only crypto tickers: price them from CoinGecko markets."""
    need = [c for c in cands.values() if c['asset'] == 'crypto' and 'chg24' not in c['metrics']
            and '@' not in c['symbol']]
    if not need:
        return
    try:
        mk = src.coingecko_markets([c['symbol'] for c in need])
    except Exception:
        return
    for c in need:
        hit = mk.get(c['symbol'])
        if hit:
            c['name'] = c['name'] or hit['name']
            c['ids'].setdefault('coingecko', hit['id'])
            c['urls'].setdefault('coingecko', f"https://www.coingecko.com/en/coins/{hit['id']}")
            for k, v in hit['metrics'].items():
                if v is not None:
                    c['metrics'].setdefault(k, v)


# ── narrative heat ───────────────────────────────────────────────────────

def _pct_ranks(values: dict) -> dict:
    """{id: value|None} -> {id: 0..1 percentile among the non-None}."""
    have = sorted((v, k) for k, v in values.items() if v is not None)
    n = len(have)
    return {k: (i / (n - 1) if n > 1 else 0.5) for i, (v, k) in enumerate(have)}


def heat_raw(themes: dict, cands: dict, asset: str = 'all', hn: bool = True) -> tuple:
    """Per theme: the raw drivers (before ranking against the other themes) -> (raw, errors)."""
    raw = {nid: {'crypto_move': None, 'trending_cats': 0, 'stock_move': None, 'hn_7d': None,
                 'crowd_crypto': 0.0, 'crowd_stock': 0.0} for nid in themes}
    errors = {}

    if asset in ('all', 'crypto'):
        try:
            cats = src.coingecko_categories()
            trending = ' '.join(src.coingecko_trending_categories()).lower()
            for nid, n in themes.items():
                hit = [c for c in cats if any(s in (c.get('id') or '') for s in n['cg'])
                       and c.get('market_cap') and c.get('market_cap_change_24h') is not None]
                w = sum(c['market_cap'] for c in hit)
                if w:
                    raw[nid]['crypto_move'] = round(sum(c['market_cap'] * c['market_cap_change_24h']
                                                        for c in hit) / w, 2)
                raw[nid]['trending_cats'] = sum(1 for s in n['cg'] if s.split('-')[0] in trending)
        except Exception as e:
            errors['coingecko_categories'] = f'{type(e).__name__}: {e}'[:200]

    if asset in ('all', 'stock'):
        seeds = sorted({t for n in themes.values() for t in n['stocks']})
        charts = src.yahoo_charts(seeds) if seeds else {}
        for nid, n in themes.items():
            mv = [charts[t]['metrics']['chg24'] for t in n['stocks']
                  if t in charts and charts[t]['metrics'].get('chg24') is not None]
            if mv:
                raw[nid]['stock_move'] = round(sum(mv) / len(mv), 2)
        if seeds and not charts:
            errors['yahoo_seeds'] = 'no seed quotes'

    if hn:
        def one(nid):
            try:
                return nid, src.hn_count(themes[nid]['hn'])
            except Exception:
                return nid, None
        with ThreadPoolExecutor(6) as ex:
            for nid, n in ex.map(one, themes):
                raw[nid]['hn_7d'] = n

    crowd(raw, cands)
    return raw, errors


def crowd(raw: dict, cands: dict) -> None:
    """How many scanned candidates (weighted by signal strength) fall into each theme."""
    for r in raw.values():
        r['crowd_crypto'] = r['crowd_stock'] = 0.0
    for c in cands.values():
        w = sum(x['strength'] for x in c['signals'])
        for nid in c['narratives']:
            if nid in raw:
                raw[nid]['crowd_' + c['asset']] += w


def heat_blend(themes: dict, raw: dict) -> dict:
    """Raw drivers -> 0-100 heat per theme for crypto and stocks (percentile blend across themes)."""
    p = {k: _pct_ranks({nid: r[k] for nid, r in raw.items()})
         for k in ('crypto_move', 'stock_move', 'hn_7d', 'crowd_crypto', 'crowd_stock', 'trending_cats')}
    out = {}
    for nid, r in raw.items():
        def blend(keys):
            vals = [p[k][nid] for k in keys if nid in p[k]]
            return round(100 * sum(vals) / len(vals)) if vals else 0
        out[nid] = {'label': themes[nid]['label'],
                    'crypto': blend(['crypto_move', 'crypto_move', 'trending_cats', 'hn_7d', 'crowd_crypto']),
                    'stock': blend(['stock_move', 'stock_move', 'hn_7d', 'crowd_stock']),
                    'drivers': {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}}
    return out


def heat(cands: dict, asset: str = 'all', hn: bool = True, themes: Optional[dict] = None) -> dict:
    """Per narrative: raw drivers + a 0-100 heat for crypto and for stocks."""
    themes = themes or nar.NARRATIVES
    raw, errors = heat_raw(themes, cands, asset, hn)
    return {'narratives': heat_blend(themes, raw), 'raw': raw, 'errors': errors}


# ── score ────────────────────────────────────────────────────────────────

def score(c: dict, heat_map: dict) -> dict:
    m, why, flags = c['metrics'], [], []
    fams = sorted({_family(s['source']) for s in c['signals'] if s['source'] != 'narrative_seed'})
    total = sum(s['strength'] for s in c['signals'])
    attention = 0.6 * _clamp(total / 2) + 0.4 * _clamp((len(fams) - 1) / 2)
    why.append(f"{len(fams)} feed{'s' * (len(fams) != 1)}: {', '.join(fams) or 'narrative seed only'}")

    vel = []
    if m.get('mentions') is not None and m['mentions'] >= 5:
        r = (m['mentions'] + 1) / ((m.get('mentions_24h_ago') or 0) + 1)
        vel.append(_clamp(math.log2(r) / 2))
        if r >= 1.5:
            why.append(f"reddit mentions {m.get('mentions_24h_ago') or 0}->{m['mentions']} ({r:.1f}x)")
    if m.get('rank') and m.get('rank_24h_ago') and m['rank_24h_ago'] > m['rank']:
        vel.append(_clamp((m['rank_24h_ago'] - m['rank']) / m['rank_24h_ago']))
    if m.get('vol1h') and m.get('vol24'):
        accel = m['vol1h'] * 24 / m['vol24']
        vel.append(_clamp((accel - 1) / 2))
        if accel >= 1.5:
            why.append(f'volume accelerating ({accel:.1f}x 24h pace)')
    if m.get('buys24') and m.get('sells24'):
        br = m['buys24'] / (m['buys24'] + m['sells24'])
        vel.append(_clamp((br - 0.5) * 4))
        if m['sells24'] > m['buys24'] * 1.3:
            flags.append('sell pressure')
    if m.get('relvol'):
        vel.append(_clamp((m['relvol'] - 1) / 3))
        if m['relvol'] >= 2:
            why.append(f"volume {m['relvol']}x normal")
    velocity = sum(vel) / len(vel) if vel else 0.0

    hot = sorted(((heat_map[n][c['asset']], n) for n in c['narratives'] if n in heat_map), reverse=True)
    narrative = hot[0][0] / 100 if hot else 0.0
    if hot:
        why.append(f"narrative: {heat_map[hot[0][1]]['label']} (heat {hot[0][0]})")

    ch = m.get('chg24')
    if ch is None:
        momentum = 0.3
    elif ch <= -10:
        momentum = 0.0
    elif ch <= 0:
        momentum = 0.2
    elif ch <= 60:
        momentum = 0.5 + ch / 120
    elif ch <= 150:
        momentum = 1 - (ch - 60) / 180
    else:
        momentum = 0.2
        flags.append(f'already +{ch:.0f}% today')
    if ch is not None:
        why.append(f'{ch:+.1f}% 24h')

    cap = m.get('mcap')
    if cap is None:
        size = 0.4
    elif c['asset'] == 'crypto':
        size = 0.2 if cap < 2.5e5 else 0.7 if cap < 5e6 else 1.0 if cap < 1e8 else 0.6 if cap < 1e9 else 0.2 if cap < 1e10 else 0.0
    else:
        size = 0.8 if cap < 3e8 else 1.0 if cap < 2e9 else 0.5 if cap < 1e10 else 0.15 if cap < 1e11 else 0.0
    if cap:
        why.append(f'mcap {_usd(cap)}')

    penalty = 0
    if c['asset'] == 'crypto' and 'address' in c['ids'] and 'liq' not in m:
        penalty += 10
        flags.append('no pooled liquidity (bonding curve?)')
    if c['asset'] == 'crypto' and cap is not None and cap < 2.5e5:
        flags.append(f'micro cap {_usd(cap)}')
    if c['asset'] == 'crypto' and 'liq' in m:
        if m['liq'] < 25_000:
            penalty += 15
            flags.append(f"thin liquidity {_usd(m['liq'])}")
        if m.get('fdv') and m['liq'] and m['fdv'] / m['liq'] > 100:
            penalty += 6
            flags.append('fdv >100x liquidity')
    if m.get('age_h') is not None and m['age_h'] < 24:
        penalty += 8
        flags.append(f"pair {m['age_h']}h old")
    if 'sell pressure' in flags:
        penalty += 6
    if c['asset'] == 'stock' and m.get('price') is not None and m['price'] < 1:
        penalty += 5
        flags.append('penny stock')

    parts = {'attention': attention, 'velocity': velocity, 'narrative': narrative,
             'momentum': momentum, 'size': size}
    s = sum(WEIGHTS[k] * v for k, v in parts.items()) - penalty
    g = {'asset': c['asset'], 'symbol': c['symbol'], 'name': c['name'].strip(),
         'score': round(_clamp(s, 0, 100), 1),
         'parts': {k: round(WEIGHTS[k] * v, 1) for k, v in parts.items()}, 'penalty': penalty,
         'narratives': [heat_map[n]['label'] for _, n in hot], 'why': why, 'flags': flags,
         'fit': round(c['fit'], 3) if c.get('fit') is not None else 1.0,
         'metrics': m, 'sources': fams, 'urls': c['urls'], 'ids': c['ids']}
    g.update(upside(g))
    return g


# ── upside: which of these is most likely to actually go up ───────────────
# The score says where attention is. Upside asks the second question: given
# that attention, how much room is left and how likely is it to be a trap.
HARD = ('thin liquidity', 'no pooled liquidity', 'pair ', 'already +', 'micro cap', 'penny stock',
        'sell pressure', 'fdv >')


def upside(g: dict) -> dict:
    """score x fit-to-theme x room-to-run -> {upside, risk}. Hard flags (rug-shaped or
    already-vertical) each cut it; a +200% week cuts it too. fit=1 when no theme is typed."""
    risk = sum(1 for f in g['flags'] if f.startswith(HARD))
    room = max(0.3, 0.65 ** risk)
    wk = (g['metrics'] or {}).get('chg7d')
    if wk is not None and wk > 200:
        room *= 0.75
    fit = g.get('fit', 1.0)
    return {'upside': round(_clamp(g['score'] * (0.55 + 0.45 * fit) * room, 0, 100), 1), 'risk': risk}


def pick(gems: list) -> Optional[dict]:
    """The one to look at first: highest upside among gems with no hard flag
    (else the least-flagged). None when there is nothing."""
    if not gems:
        return None
    best = sorted(gems, key=lambda g: (g['risk'], -g['upside'], -g['score']))[0]
    why = [f"upside {best['upside']}: score {best['score']}"]
    if best['fit'] < 1.0:
        why.append(f"fits the theme {best['fit']:.0%}")
    why.append('no hard flags' if not best['risk'] else f"{best['risk']} hard flag{'s' * (best['risk'] > 1)}")
    return {'asset': best['asset'], 'symbol': best['symbol'], 'name': best['name'], 'upside': best['upside'],
            'score': best['score'], 'fit': best['fit'], 'risk': best['risk'],
            'why': ' · '.join(why + best['why'][1:]), 'flags': best['flags'], 'urls': best['urls']}


def near_narratives(q: str) -> list:
    """Built-in narratives whose meaning is close to the typed theme -> [(id, sim)], best first."""
    docs = {nid: f"{n['label']}: {' '.join(n['keywords'])}" for nid, n in nar.NARRATIVES.items()}
    sims = sem.rank(q, docs) or {}
    return sorted(((nid, v) for nid, v in sims.items() if v >= 0.45), key=lambda x: -x[1])[:2]


# ── scan ─────────────────────────────────────────────────────────────────

def base(asset: str = 'all', sources: Optional[list] = None, hn: bool = True, fresh: bool = False) -> tuple:
    """Everything a scan needs before filtering, cached per (asset, sources, hn):
    merged + enriched + narrative-matched candidates, raw heat drivers, feed status."""
    key = (asset, tuple(sorted(sources or [])), bool(hn))

    def build():
        t0 = time.time()
        signals, status = gather(asset, sources)
        cands = merge(signals)
        if asset in ('all', 'crypto'):
            enrich_crypto(cands)
        if asset in ('all', 'stock'):
            enrich_stocks(cands)
        for c in cands.values():
            c['narratives'] = nar.match(c['text'], c['symbol'], c['asset'])
        sem.prime(cands)  # vectors ready before anyone types a theme
        raw, errors = heat_raw(nar.NARRATIVES, cands, asset, hn)
        status.update({n: {'ok': False, 'error': e} for n, e in errors.items()})
        return {'ts': int(time.time()), 'took_s': round(time.time() - t0, 1), 'signals': len(signals),
                'status': status, 'cands': cands, 'raw': raw}
    return _cached('base', key, build, fresh=fresh)


def _add(cands: dict, signals: list, asset: str) -> dict:
    """Merge new signals into cands; enrich + narrative-match the candidates that are new."""
    before = set(cands)
    merge(signals, into=cands)
    added = {k: c for k, c in cands.items() if k not in before}
    if asset in ('all', 'crypto'):
        enrich_crypto(added)
    if asset in ('all', 'stock'):
        enrich_stocks(added)
    for c in added.values():
        c['narratives'] = nar.match(c['text'], c['symbol'], c['asset'])
    return added


def scan(asset: str = 'all', k: int = 20, narrative: Optional[str] = None, min_score: float = 0,
         sources: Optional[list] = None, hn: bool = True, save: bool = True, q: Optional[str] = None,
         fresh: bool = False, sort: str = 'upside') -> dict:
    """Ranked gems. asset: all|crypto|stock. narrative: an id from narratives (e.g. 'quantum').
    q: anything you think is dope ('quantum', 'frog', 'nuclear drones') — becomes a live theme:
    its words AND its meaning are matched against every candidate, and CoinGecko search +
    categories, DexScreener search and Yahoo search pull in more. sort: upside (default,
    most likely to go up first) | score (most attention first). fresh: skip the scan cache."""
    if asset not in ('all', 'crypto', 'stock'):
        raise ValueError("asset must be all|crypto|stock")
    if sort not in ('upside', 'score'):
        raise ValueError("sort must be upside|score")
    if narrative and narrative not in nar.NARRATIVES:
        raise ValueError(f'unknown narrative {narrative!r}; one of {nar.ids()}')
    q = (q or '').strip()[:80] or None
    if q:
        nar.custom(q)  # raises ValueError when nothing usable was typed
    key = (asset, narrative, q.lower() if q else None, tuple(sorted(sources or [])), bool(hn))

    def build():
        t0 = time.time()
        b, b_cached = base(asset, sources, hn, fresh=fresh)
        cands = copy.deepcopy(b['cands'])
        status = dict(b['status'])
        themes = dict(nar.NARRATIVES)
        raw = copy.deepcopy(b['raw'])
        n_signals = b['signals']
        theme = None

        if narrative and asset in ('all', 'stock'):
            # Filtering to one theme: its seed stocks are always in the running, even
            # if no feed surfaced them today. Strength 0 — being a seed earns no attention.
            _add(cands, [src._sig('stock', t, '', 'narrative_seed', 0.0)
                         for t in nar.NARRATIVES[narrative]['stocks'] if ('stock', t) not in cands], asset)

        if q:
            try:
                cats = src.coingecko_categories() if asset in ('all', 'crypto') else []
            except Exception:
                cats = []
            theme = nar.custom(q, cats)
            themes[nar.CUSTOM_ID] = theme
            t_sem = time.time()
            near = near_narratives(q)
            theme['near'] = [nid for nid, _ in near]
            if near:
                nn = nar.NARRATIVES[near[0][0]]
                if not theme['cg']:  # "drones" names no CoinGecko category: borrow defense's
                    theme['cg'] = [c['id'] for c in sorted(cats, key=lambda c: -(c.get('market_cap') or 0))
                                   if any(s in (c.get('id') or '') for s in nn['cg'])][:2]
                if asset in ('all', 'stock'):
                    _add(cands, [src._sig('stock', t, '', 'narrative_seed', 0.0)
                                 for t in nn['stocks'] if ('stock', t) not in cands], asset)
            extra, st = theme_signals(theme, asset)
            status.update(st)
            n_signals += len(extra)
            _add(cands, extra, asset)
            words = semantic = 0
            for key_, c in cands.items():
                if nar.hit(theme, c['text'], c['symbol'], c['name']):
                    if nar.CUSTOM_ID not in c['narratives']:
                        c['narratives'].append(nar.CUSTOM_ID)
                    c['fit'] = 0.75  # a word hit is a strong fit; similarity may raise it below
                    words += 1
                elif near and c['asset'] == 'stock' and c['symbol'] in nn['stocks']:
                    c['narratives'].append(nar.CUSTOM_ID)
                    c['fit'] = round(near[0][1], 3)
            sims = sem.rank(q, {key_: sem.doc_text(c) for key_, c in cands.items()})
            if sims is not None:
                floor = max(sem.FLOOR, 0.6 * max(sims.values(), default=0))
                for key_, v in sims.items():
                    c = cands[key_]
                    if v >= floor and nar.CUSTOM_ID not in c['narratives']:
                        c['narratives'].append(nar.CUSTOM_ID)
                        semantic += 1
                    if nar.CUSTOM_ID in c['narratives']:
                        c['fit'] = max(c.get('fit') or 0, v)
                    else:
                        c['fit'] = v
                status['semantic'] = {'ok': True, 'n': semantic, 's': round(time.time() - t_sem, 2)}
            else:
                status['semantic'] = {'ok': False, 'error': sem.status().get('error') or 'encoder off',
                                      's': round(time.time() - t_sem, 2)}
            theme['word_matches'], theme['semantic_matches'] = words, semantic
            # the theme's own drivers; its stock side is the day move of the stocks it matched
            tr, _ = heat_raw({nar.CUSTOM_ID: theme}, {}, asset, hn)
            raw[nar.CUSTOM_ID] = tr[nar.CUSTOM_ID]
            mv = [c['metrics']['chg24'] for c in cands.values() if c['asset'] == 'stock'
                  and nar.CUSTOM_ID in c['narratives'] and c['metrics'].get('chg24') is not None]
            if mv:
                raw[nar.CUSTOM_ID]['stock_move'] = round(sum(mv) / len(mv), 2)
            crowd(raw, cands)

        hm = heat_blend(themes, raw)
        gems_all = [score(c, hm) for c in cands.values()]
        hot = sorted(({'id': n, 'label': v['label'], 'crypto': v['crypto'], 'stock': v['stock'],
                       'drivers': v['drivers']} for n, v in hm.items()),
                     key=lambda x: (x['id'] != nar.CUSTOM_ID, -max(x['crypto'], x['stock'])))
        return {'ts': int(time.time()), 'asset': asset, 'narrative': narrative, 'q': q,
                'took_s': round(time.time() - t0, 1), 'base_took_s': b['took_s'], 'base_cached': b_cached,
                'signals': n_signals, 'candidates': len(cands), 'scored': len(gems_all), 'sources': status,
                'narratives': hot, 'gems_all': gems_all,
                'theme': {'id': nar.CUSTOM_ID, 'label': theme['label'], 'query': q, 'keywords': theme['keywords'],
                          'categories': theme['cg'], 'hn': theme['hn'],
                          'near': [nar.NARRATIVES[n]['label'] for n in theme['near']],
                          'semantic': status.get('semantic', {}).get('ok', False),
                          'word_matches': theme['word_matches'], 'semantic_matches': theme['semantic_matches'],
                          'crypto': hm[nar.CUSTOM_ID]['crypto'], 'stock': hm[nar.CUSTOM_ID]['stock'],
                          'matches': sum(1 for g in gems_all if theme['label'] in g['narratives'])} if theme else None,
                'disclaimer': 'Attention score, not a forecast. Most narrative pumps round-trip. Not financial advice.'}

    full, cached = _cached('result', key, build, fresh=fresh)
    gems = full['gems_all']
    if narrative:
        label = nar.NARRATIVES[narrative]['label']
        gems = [g for g in gems if label in g['narratives']]
    if q:
        label = full['theme']['label']
        gems = [g for g in gems if label in g['narratives']]
    gems = [g for g in gems if g['score'] >= float(min_score)]
    gems = sorted(gems, key=(lambda g: (-g['upside'], -g['score'])) if sort == 'upside' else (lambda g: -g['score']))[:max(1, int(k))]
    res = {k_: v for k_, v in full.items() if k_ != 'gems_all'}
    res.update({'gems': gems, 'pick': pick(gems), 'sort': sort, 'cached': cached,
                'age_s': int(time.time() - full['ts']), 'ttl_s': SCAN_TTL})
    if save and not cached:
        _save(res)
    return res


def narratives(asset: str = 'all', hn: bool = True) -> dict:
    """Narrative heat only (no candidate scoring beyond crowding)."""
    b, cached = base(asset, None, hn)
    hm = heat_blend(nar.NARRATIVES, b['raw'])
    rows = sorted(({'id': n, **v} for n, v in hm.items()), key=lambda x: -max(x['crypto'], x['stock']))
    return {'narratives': rows, 'sources': b['status'], 'cached': cached, 'age_s': int(time.time() - b['ts'])}


def _save(res: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    slim = {'ts': res['ts'], 'asset': res['asset'], 'narrative': res['narrative'], 'q': res.get('q'),
            'pick': (res.get('pick') or {}).get('symbol'),
            'gems': [{'asset': g['asset'], 'symbol': g['symbol'], 'score': g['score'], 'upside': g.get('upside'),
                      'fit': g.get('fit'), 'price': g['metrics'].get('price'), 'ids': g['ids']} for g in res['gems']]}
    with open(SCANS, 'a') as f:
        f.write(json.dumps(slim) + '\n')


def history(n: int = 10) -> list:
    """Last n saved scans (symbol, score, price at scan time) — the raw material for judging picks later."""
    if not SCANS.exists():
        return []
    lines = SCANS.read_text().splitlines()[-int(n):]
    return [json.loads(x) for x in reversed(lines)]


# ── review: did the picks go up? ──────────────────────────────────────────

def _prices_now(items: list) -> dict:
    """{(asset, symbol): price} for past picks: CoinGecko for listed coins, Yahoo for stocks.
    DEX-only tokens (symbol@addr, or address-only ids) are skipped — no stable price feed."""
    out = {}
    cg = sorted({s for a, s in items if a == 'crypto' and '@' not in s and s.isalnum()})
    if cg:
        try:
            for sym, hit in src.coingecko_markets(cg).items():
                if hit['metrics'].get('price'):
                    out[('crypto', sym)] = hit['metrics']['price']
        except Exception:
            pass
    st = sorted({s for a, s in items if a == 'stock'})
    if st:
        for sym, ch in src.yahoo_charts(st[:60]).items():
            if ch['metrics'].get('price'):
                out[('stock', sym)] = ch['metrics']['price']
    return out


def review(n: int = 12, fresh: bool = False) -> dict:
    """Past scans' picks priced now vs at scan time (first time each was listed) -> per pick
    return % and a summary (hit rate, median). The honest check on 'most likely to go up'.
    Cached 5 min."""
    def build():
        first = {}
        for s_ in reversed(history(int(n))):  # oldest first: keep the first sighting
            for g in s_['gems']:
                key_ = (g['asset'], g['symbol'])
                if g.get('price') and key_ not in first:
                    first[key_] = {'asset': g['asset'], 'symbol': g['symbol'], 'ts': s_['ts'], 'then': g['price'],
                                   'score': g['score'], 'upside': g.get('upside'),
                                   'picked': s_.get('pick') == g['symbol'], 'q': s_.get('q')}
        now = _prices_now(list(first))
        rows = []
        for key_, r in first.items():
            px = now.get(key_)
            r['now'] = px
            r['ret_pct'] = round((px / r['then'] - 1) * 100, 1) if px else None
            r['age_h'] = round((time.time() - r['ts']) / 3600, 1)
            rows.append(r)
        priced = [r for r in rows if r['ret_pct'] is not None]
        rets = sorted(r['ret_pct'] for r in priced)
        picks = [r for r in priced if r['picked']]
        med = rets[len(rets) // 2] if rets else None
        rows.sort(key=lambda r: (r['ret_pct'] is None, -(r['ret_pct'] or 0)))
        return {'ts': int(time.time()), 'scans': min(int(n), len(history(int(n)))), 'picks': rows,
                'summary': {'listed': len(rows), 'priced': len(priced), 'up': sum(1 for r in priced if r['ret_pct'] > 0),
                            'down': sum(1 for r in priced if r['ret_pct'] < 0), 'median_ret_pct': med,
                            'mean_ret_pct': round(sum(rets) / len(rets), 1) if rets else None,
                            'hit_rate': round(sum(1 for r in priced if r['ret_pct'] > 0) / len(priced), 2) if priced else None,
                            'top_picks': {'n': len(picks), 'up': sum(1 for r in picks if r['ret_pct'] > 0),
                                          'mean_ret_pct': round(sum(r['ret_pct'] for r in picks) / len(picks), 1) if picks else None}},
                'note': 'return since the scan that first listed it; hours old, not a track record. DEX-only tokens are unpriced.'}
    val, cached = _cached('review', int(n), build, ttl=300, fresh=fresh)
    return {**val, 'cached': cached}
