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
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from . import narratives as nar
from . import sources as src
from .http import HOME

WEIGHTS = {'attention': 30, 'velocity': 20, 'narrative': 25, 'momentum': 15, 'size': 10}
SCANS = HOME / 'scans.jsonl'


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _family(source: str) -> str:
    return 'dexscreener' if source.startswith('dexscreener') else source


def _usd(v) -> str:
    if v is None:
        return '?'
    for div, suf in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs(v) >= div:
            return f'${v / div:.1f}{suf}'
    return f'${v:.0f}'


# ── gather ───────────────────────────────────────────────────────────────

def gather(asset: str = 'all', only: Optional[list] = None) -> tuple:
    """Run candidate sources in parallel -> (signals, {source: {ok, n | error, s}})."""
    picked = {n: s for n, s in src.CANDIDATE_SOURCES.items()
              if (asset == 'all' or s['asset'] == asset) and (not only or n in only)}
    status, signals = {}, []

    def run(item):
        name, s = item
        t = time.time()
        try:
            got = s['fn']()
            return name, got, {'ok': True, 'n': len(got), 's': round(time.time() - t, 2)}
        except Exception as e:
            return name, [], {'ok': False, 'error': f'{type(e).__name__}: {e}'[:200], 's': round(time.time() - t, 2)}

    with ThreadPoolExecutor(max(1, len(picked))) as ex:
        for name, got, st in ex.map(run, picked.items()):
            status[name] = st
            signals += got
    return signals, status


def merge(signals: list) -> dict:
    """One candidate per (asset, ticker). DEX tokens that share a ticker with a
    different contract stay separate — symbols are not unique on-chain."""
    out, dex_addr = {}, {}
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


def heat(cands: dict, asset: str = 'all', hn: bool = True) -> dict:
    """Per narrative: raw drivers + a 0-100 heat for crypto and for stocks."""
    raw = {nid: {'crypto_move': None, 'trending_cats': 0, 'stock_move': None, 'hn_7d': None,
                 'crowd_crypto': 0.0, 'crowd_stock': 0.0} for nid in nar.NARRATIVES}
    errors = {}

    if asset in ('all', 'crypto'):
        try:
            cats = src.coingecko_categories()
            trending = ' '.join(src.coingecko_trending_categories()).lower()
            for nid, n in nar.NARRATIVES.items():
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
        seeds = sorted({t for n in nar.NARRATIVES.values() for t in n['stocks']})
        charts = src.yahoo_charts(seeds)
        for nid, n in nar.NARRATIVES.items():
            mv = [charts[t]['metrics']['chg24'] for t in n['stocks']
                  if t in charts and charts[t]['metrics'].get('chg24') is not None]
            if mv:
                raw[nid]['stock_move'] = round(sum(mv) / len(mv), 2)
        if not charts:
            errors['yahoo_seeds'] = 'no seed quotes'

    if hn:
        def one(nid):
            try:
                return nid, src.hn_count(nar.NARRATIVES[nid]['hn'])
            except Exception:
                return nid, None
        with ThreadPoolExecutor(6) as ex:
            for nid, n in ex.map(one, nar.NARRATIVES):
                raw[nid]['hn_7d'] = n

    for c in cands.values():
        w = sum(x['strength'] for x in c['signals'])
        for nid in c['narratives']:
            raw[nid]['crowd_' + c['asset']] += w

    p = {k: _pct_ranks({nid: r[k] for nid, r in raw.items()})
         for k in ('crypto_move', 'stock_move', 'hn_7d', 'crowd_crypto', 'crowd_stock', 'trending_cats')}
    out = {}
    for nid, r in raw.items():
        def blend(keys):
            vals = [p[k][nid] for k in keys if nid in p[k]]
            return round(100 * sum(vals) / len(vals)) if vals else 0
        out[nid] = {'label': nar.NARRATIVES[nid]['label'],
                    'crypto': blend(['crypto_move', 'crypto_move', 'trending_cats', 'hn_7d', 'crowd_crypto']),
                    'stock': blend(['stock_move', 'stock_move', 'hn_7d', 'crowd_stock']),
                    'drivers': {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}}
    return {'narratives': out, 'errors': errors}


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

    hot = sorted(((heat_map[n][c['asset']], n) for n in c['narratives']), reverse=True)
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
    return {'asset': c['asset'], 'symbol': c['symbol'], 'name': c['name'].strip(),
            'score': round(_clamp(s, 0, 100), 1),
            'parts': {k: round(WEIGHTS[k] * v, 1) for k, v in parts.items()}, 'penalty': penalty,
            'narratives': [heat_map[n]['label'] for _, n in hot], 'why': why, 'flags': flags,
            'metrics': m, 'sources': fams, 'urls': c['urls'], 'ids': c['ids']}


# ── scan ─────────────────────────────────────────────────────────────────

def scan(asset: str = 'all', k: int = 20, narrative: Optional[str] = None, min_score: float = 0,
         sources: Optional[list] = None, hn: bool = True, save: bool = True) -> dict:
    """Ranked gems. asset: all|crypto|stock. narrative: an id from narratives (e.g. 'quantum')."""
    if asset not in ('all', 'crypto', 'stock'):
        raise ValueError("asset must be all|crypto|stock")
    if narrative and narrative not in nar.NARRATIVES:
        raise ValueError(f'unknown narrative {narrative!r}; one of {nar.ids()}')
    t0 = time.time()
    signals, status = gather(asset, sources)
    if narrative and asset in ('all', 'stock'):
        # Filtering to one theme: its seed stocks are always in the running, even
        # if no feed surfaced them today. Strength 0 — being a seed earns no attention.
        signals += [src._sig('stock', t, '', 'narrative_seed', 0.0) for t in nar.NARRATIVES[narrative]['stocks']]
    cands = merge(signals)
    if asset in ('all', 'crypto'):
        enrich_crypto(cands)
    if asset in ('all', 'stock'):
        enrich_stocks(cands)
    for c in cands.values():
        c['narratives'] = nar.match(c['text'], c['symbol'], c['asset'])
    h = heat(cands, asset, hn)
    status.update({n: {'ok': False, 'error': e} for n, e in h['errors'].items()})
    gems = [score(c, h['narratives']) for c in cands.values()]
    if narrative:
        label = nar.NARRATIVES[narrative]['label']
        gems = [g for g in gems if label in g['narratives']]
    gems = sorted((g for g in gems if g['score'] >= float(min_score)), key=lambda g: -g['score'])[:int(k)]
    hot = sorted(({'id': n, 'label': v['label'], 'crypto': v['crypto'], 'stock': v['stock']}
                  for n, v in h['narratives'].items()), key=lambda x: -max(x['crypto'], x['stock']))
    res = {'ts': int(time.time()), 'asset': asset, 'narrative': narrative, 'took_s': round(time.time() - t0, 1),
           'candidates': len(cands), 'sources': status, 'narratives': hot, 'gems': gems,
           'disclaimer': 'Attention score, not a forecast. Most narrative pumps round-trip. Not financial advice.'}
    if save:
        _save(res)
    return res


def narratives(asset: str = 'all', hn: bool = True) -> dict:
    """Narrative heat only (no candidate scoring beyond crowding)."""
    signals, status = gather(asset)
    cands = merge(signals)
    for c in cands.values():
        c['narratives'] = nar.match(c['text'], c['symbol'], c['asset'])
    h = heat(cands, asset, hn)
    rows = sorted(({'id': n, **v} for n, v in h['narratives'].items()),
                  key=lambda x: -max(x['crypto'], x['stock']))
    return {'narratives': rows, 'errors': h['errors'], 'sources': status}


def _save(res: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    slim = {'ts': res['ts'], 'asset': res['asset'], 'narrative': res['narrative'],
            'gems': [{'asset': g['asset'], 'symbol': g['symbol'], 'score': g['score'],
                      'price': g['metrics'].get('price'), 'ids': g['ids']} for g in res['gems']]}
    with open(SCANS, 'a') as f:
        f.write(json.dumps(slim) + '\n')


def history(n: int = 10) -> list:
    """Last n saved scans (symbol, score, price at scan time) — the raw material for judging picks later."""
    if not SCANS.exists():
        return []
    lines = SCANS.read_text().splitlines()[-int(n):]
    return [json.loads(x) for x in reversed(lines)]
