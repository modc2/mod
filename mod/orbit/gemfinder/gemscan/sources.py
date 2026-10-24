"""
gemscan.sources — keyless public feeds, each turned into a list of Signals.

A Signal is one source saying "look at this":
    {asset: 'crypto'|'stock', symbol, name, source, strength 0..1 (rank in
     that source), text (for narrative matching), metrics {...}, url, ids {...}}

Every source is independent and may fail; the engine reports which did.
Stocktwits and reddit .json are Cloudflare-walled from servers, so reddit
attention comes in through ApeWisdom instead.
"""
from __future__ import annotations

import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .http import get_json

CG = 'https://api.coingecko.com/api/v3'
DEX = 'https://api.dexscreener.com'
APE = 'https://apewisdom.io/api/v1.0/filter'
YF = 'https://query1.finance.yahoo.com'
HN = 'https://hn.algolia.com/api/v1/search'

# Never a "gem": majors, stables, index ETFs.
SKIP = {'BTC', 'ETH', 'USDT', 'USDC', 'DAI', 'USDE', 'FDUSD', 'WBTC', 'WETH', 'STETH', 'SOL', 'BNB', 'XRP',
        'SPY', 'QQQ', 'IWM', 'DIA', 'VOO', 'VTI', 'TQQQ', 'SQQQ', 'SPX', 'UVXY', 'VIX', 'GLD', 'SLV', 'TLT'}


def _strength(i: int, n: int) -> float:
    return round(1 - i / max(n, 1), 3)


def _money(v):
    """'$30,509,547' | 3.05e7 | None -> float | None"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace('$', '').replace(',', '').strip() or 'x')
    except ValueError:
        return None


def _sig(asset, symbol, name, source, strength, text='', metrics=None, url='', ids=None):
    return {'asset': asset, 'symbol': (symbol or '').upper().strip(), 'name': name or '', 'source': source,
            'strength': strength, 'text': text or '', 'metrics': {k: v for k, v in (metrics or {}).items()
                                                                  if v is not None},
            'url': url, 'ids': ids or {}}


# ── crypto ────────────────────────────────────────────────────────────────

def coingecko_trending() -> list:
    """CoinGecko's most-searched coins in the last 24h."""
    d = get_json(f'{CG}/search/trending', ttl=600)
    coins = d.get('coins', [])
    out = []
    for i, c in enumerate(coins):
        it = c['item']
        data = it.get('data') or {}
        out.append(_sig('crypto', it.get('symbol'), it.get('name'), 'coingecko_trending', _strength(i, len(coins)),
                        text=f"{it.get('name', '')} {(data.get('content') or {}).get('description', '')}",
                        metrics={'price': data.get('price'), 'mcap': _money(data.get('market_cap')),
                                 'vol24': _money(data.get('total_volume')),
                                 'chg24': (data.get('price_change_percentage_24h') or {}).get('usd'),
                                 'mcap_rank': it.get('market_cap_rank')},
                        url=f"https://www.coingecko.com/en/coins/{it.get('id')}", ids={'coingecko': it.get('id')}))
    return out


def coingecko_categories() -> list:
    """Every CoinGecko category with its 24h market-cap move (narrative heat, not candidates)."""
    return get_json(f'{CG}/coins/categories', ttl=1800)


def coingecko_trending_categories() -> list:
    d = get_json(f'{CG}/search/trending', ttl=600)
    return [c.get('slug') or c.get('name', '') for c in d.get('categories', [])]


def _dex_pairs(tokens: list) -> dict:
    """{(chain, address): best-liquidity pair} for up to 30 addresses per chain per call."""
    by_chain = {}
    for chain, addr in tokens:
        by_chain.setdefault(chain, []).append(addr)
    best = {}
    for chain, addrs in by_chain.items():
        for j in range(0, len(addrs), 30):
            chunk = ','.join(addrs[j:j + 30])
            try:
                pairs = get_json(f'{DEX}/tokens/v1/{chain}/{chunk}', ttl=300)
            except Exception:
                continue
            for p in pairs or []:
                key = (chain, p.get('baseToken', {}).get('address', '').lower())
                liq = (p.get('liquidity') or {}).get('usd') or 0
                if key not in best or liq > ((best[key].get('liquidity') or {}).get('usd') or 0):
                    best[key] = p
    return best


def dexscreener() -> list:
    """Tokens people are paying to boost + freshly listed profiles, priced from their deepest pair."""
    boosts = get_json(f'{DEX}/token-boosts/top/v1', ttl=300) or []
    profiles = get_json(f'{DEX}/token-profiles/latest/v1', ttl=300) or []
    rows, seen = [], set()
    for src, lst in (('boost', boosts), ('profile', profiles)):
        for r in lst:
            k = (r.get('chainId'), (r.get('tokenAddress') or '').lower())
            if k[1] and k not in seen:
                seen.add(k)
                rows.append((src, r))
    pairs = _dex_pairs([(r['chainId'], r['tokenAddress']) for _, r in rows])
    now = time.time() * 1000
    out = []
    for i, (src, r) in enumerate(rows):
        p = pairs.get((r['chainId'], r['tokenAddress'].lower()))
        if not p:
            continue
        bt = p.get('baseToken', {})
        chg, vol, tx = p.get('priceChange') or {}, p.get('volume') or {}, (p.get('txns') or {}).get('h24') or {}
        created = p.get('pairCreatedAt')
        out.append(_sig('crypto', bt.get('symbol'), bt.get('name'), f'dexscreener_{src}', _strength(i, len(rows)),
                        text=f"{bt.get('name', '')} {r.get('description', '')}",
                        metrics={'price': _money(p.get('priceUsd')), 'mcap': p.get('marketCap') or p.get('fdv'),
                                 'fdv': p.get('fdv'), 'liq': (p.get('liquidity') or {}).get('usd'),
                                 'vol24': vol.get('h24'), 'vol1h': vol.get('h1'),
                                 'chg1h': chg.get('h1'), 'chg24': chg.get('h24'),
                                 'buys24': tx.get('buys'), 'sells24': tx.get('sells'),
                                 'boost': r.get('totalAmount'),
                                 'age_h': round((now - created) / 3.6e6, 1) if created else None},
                        url=p.get('url') or r.get('url', ''),
                        ids={'chain': r['chainId'], 'address': r['tokenAddress']}))
    return out


def _apewisdom(filt: str, asset: str, pages: int) -> list:
    res = []
    for page in range(1, pages + 1):
        try:
            res += get_json(f'{APE}/{filt}/page/{page}', ttl=900).get('results', [])
        except Exception:
            if page == 1:
                raise
    out = []
    for i, r in enumerate(res):
        sym = (r.get('ticker') or '').replace('.X', '')
        if asset == 'crypto' and 'tokenized' in (r.get('name') or '').lower():
            continue  # tokenized equities: the stock side already covers them
        out.append(_sig(asset, sym, r.get('name'), 'reddit_apewisdom', _strength(i, len(res)),
                        text=r.get('name', ''),
                        metrics={'mentions': r.get('mentions'), 'mentions_24h_ago': r.get('mentions_24h_ago'),
                                 'upvotes': r.get('upvotes'), 'rank': r.get('rank'),
                                 'rank_24h_ago': r.get('rank_24h_ago')},
                        url=f'https://apewisdom.io/{"stocks" if asset == "stock" else "cryptocurrency"}/{sym}/'))
    return out


def reddit_crypto() -> list:
    """Ticker mentions across crypto subreddits (via ApeWisdom), with 24h-ago baseline."""
    return _apewisdom('all-crypto', 'crypto', 1)


def coingecko_markets(symbols: list) -> dict:
    """{SYMBOL: {name, id, metrics}} for crypto tickers seen without a price; the
    biggest coin wins a shared ticker (CoinGecko returns the top-ranked ones)."""
    out = {}
    syms = sorted({s.lower() for s in symbols if s.isalnum()})
    for j in range(0, len(syms), 50):
        q = urllib.parse.urlencode({'vs_currency': 'usd', 'symbols': ','.join(syms[j:j + 50]),
                                    'price_change_percentage': '1h,24h,7d'})
        for c in get_json(f'{CG}/coins/markets?{q}', ttl=600) or []:
            sym = (c.get('symbol') or '').upper()
            if sym in out and (out[sym]['metrics'].get('mcap') or 0) >= (c.get('market_cap') or 0):
                continue
            out[sym] = {'name': c.get('name', ''), 'id': c.get('id'),
                        'metrics': {'price': c.get('current_price'), 'mcap': c.get('market_cap'),
                                    'vol24': c.get('total_volume'), 'chg24': c.get('price_change_percentage_24h'),
                                    'chg7d': c.get('price_change_percentage_7d_in_currency'),
                                    'mcap_rank': c.get('market_cap_rank')}}
    return out


# ── stocks ────────────────────────────────────────────────────────────────

def reddit_stocks() -> list:
    """Ticker mentions across WSB & stock subreddits (via ApeWisdom), with 24h-ago baseline."""
    return _apewisdom('all-stocks', 'stock', 2)


def yahoo_trending() -> list:
    """Yahoo Finance's trending US tickers."""
    d = get_json(f'{YF}/v1/finance/trending/US?count=25', ttl=600)
    q = ((d.get('finance') or {}).get('result') or [{}])[0].get('quotes', [])
    return [_sig('stock', x['symbol'], '', 'yahoo_trending', _strength(i, len(q)),
                 url=f"https://finance.yahoo.com/quote/{x['symbol']}") for i, x in enumerate(q)
            if '.' not in x['symbol'] and '=' not in x['symbol'] and '^' not in x['symbol']]


def _quote_metrics(q: dict) -> dict:
    avg = q.get('averageDailyVolume3Month')
    return {'price': q.get('regularMarketPrice'), 'chg24': q.get('regularMarketChangePercent'),
            'mcap': q.get('marketCap'), 'vol24': q.get('regularMarketVolume'),
            'relvol': round(q['regularMarketVolume'] / avg, 2) if avg and q.get('regularMarketVolume') else None,
            'off_52w_high': q.get('fiftyTwoWeekHighChangePercent')}


def yahoo_screeners() -> list:
    """Yahoo predefined screens: small-cap gainers, day gainers, most active."""
    out = []
    for scr in ('small_cap_gainers', 'day_gainers', 'most_actives'):
        try:
            d = get_json(f'{YF}/v1/finance/screener/predefined/saved?scrIds={scr}&count=40', ttl=600)
        except Exception:
            continue
        qs = ((d.get('finance') or {}).get('result') or [{}])[0].get('quotes', [])
        for i, q in enumerate(qs):
            name = q.get('longName') or q.get('shortName') or ''
            out.append(_sig('stock', q.get('symbol'), name, f'yahoo_{scr}', _strength(i, len(qs)),
                            text=name, metrics=_quote_metrics(q),
                            url=f"https://finance.yahoo.com/quote/{q.get('symbol')}"))
    if not out:
        raise RuntimeError('all yahoo screeners failed')
    return out


def yahoo_chart(symbol: str) -> dict:
    """Price/volume for one ticker from the (keyless) chart endpoint."""
    d = get_json(f'{YF}/v8/finance/chart/{urllib.parse.quote(symbol)}?range=5d&interval=1d', ttl=900)
    m = (d.get('chart', {}).get('result') or [{}])[0].get('meta', {})
    px, prev = m.get('regularMarketPrice'), m.get('chartPreviousClose')
    closes = ((d['chart']['result'][0].get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
    closes = [c for c in closes if c]
    last_prev = closes[-2] if len(closes) >= 2 else prev
    return {'name': m.get('longName') or m.get('shortName') or '',
            'metrics': {'price': px, 'chg24': round((px / last_prev - 1) * 100, 2) if px and last_prev else None,
                        'chg5d': round((px / prev - 1) * 100, 2) if px and prev else None,
                        'vol24': m.get('regularMarketVolume'),
                        'off_52w_high': round(px / m['fiftyTwoWeekHigh'] - 1, 3)
                        if px and m.get('fiftyTwoWeekHigh') else None}}


def yahoo_charts(symbols: list, workers: int = 8) -> dict:
    def one(s):
        try:
            return s, yahoo_chart(s)
        except Exception:
            return s, None
    with ThreadPoolExecutor(workers) as ex:
        return {s: r for s, r in ex.map(one, symbols) if r}


# ── wider internet ────────────────────────────────────────────────────────

def hn_count(query: str, days: int = 7) -> int:
    """Hacker News stories matching `query` in the last `days`."""
    since = int(time.time()) - days * 86400
    since -= since % 3600  # stable URL for an hour -> cacheable
    q = urllib.parse.urlencode({'query': query, 'tags': 'story', 'hitsPerPage': 0,
                                'numericFilters': f'created_at_i>{since}'})
    return int(get_json(f'{HN}?{q}', ttl=3600).get('nbHits', 0))


# ── typed theme: live search in every venue ──────────────────────────────
# Each takes the theme (see narratives.custom) and returns signals; only hits
# whose name/symbol/category actually contain a typed word survive, so
# CoinGecko's fuzzy "quantum" -> "nextplace" noise is dropped.

def _theme_ok(theme, *parts) -> bool:
    return bool(theme['pattern'].search(' '.join(str(x or '') for x in parts)))


def cg_search(theme: dict) -> list:
    """CoinGecko /search: coins whose name or ticker matches what you typed."""
    d = get_json(f"{CG}/search?{urllib.parse.urlencode({'query': theme['query']})}", ttl=900)
    coins = [c for c in d.get('coins', []) if _theme_ok(theme, c.get('name'), c.get('symbol'), c.get('id'))][:12]
    return [_sig('crypto', c.get('symbol'), c.get('name'), 'cg_search', _strength(i, len(coins)),
                 text=f"{c.get('name', '')} {theme['query']}", metrics={'mcap_rank': c.get('market_cap_rank')},
                 url=f"https://www.coingecko.com/en/coins/{c.get('id')}", ids={'coingecko': c.get('id')})
            for i, c in enumerate(coins)]


def cg_category(theme: dict) -> list:
    """CoinGecko coins inside the categories your words name (e.g. 'ai agents', 'meme', 'depin'), by volume."""
    out, seen = [], set()
    for cat in theme['cg'][:2]:  # CoinGecko's keyless tier is ~10 calls/min: two categories, biggest first
        q = urllib.parse.urlencode({'vs_currency': 'usd', 'category': cat, 'order': 'volume_desc', 'per_page': 40,
                                    'price_change_percentage': '1h,24h,7d'})
        try:
            rows = get_json(f'{CG}/coins/markets?{q}', ttl=900) or []
        except Exception:
            continue
        for i, c in enumerate(rows):
            if c.get('id') in seen:
                continue
            seen.add(c.get('id'))
            out.append(_sig('crypto', c.get('symbol'), c.get('name'), 'cg_category', _strength(i, len(rows)),
                            text=f"{c.get('name', '')} {cat.replace('-', ' ')}",
                            metrics={'price': c.get('current_price'), 'mcap': c.get('market_cap'),
                                     'vol24': c.get('total_volume'), 'chg24': c.get('price_change_percentage_24h'),
                                     'chg1h': c.get('price_change_percentage_1h_in_currency'),
                                     'chg7d': c.get('price_change_percentage_7d_in_currency'),
                                     'mcap_rank': c.get('market_cap_rank')},
                            url=f"https://www.coingecko.com/en/coins/{c.get('id')}", ids={'coingecko': c.get('id')}))
    return out


def dex_search(theme: dict) -> list:
    """DexScreener pair search: on-chain tokens named like what you typed, deepest pool per token."""
    d = get_json(f"{DEX}/latest/dex/search?{urllib.parse.urlencode({'q': theme['query']})}", ttl=600)
    best = {}
    for p in d.get('pairs') or []:
        bt = p.get('baseToken') or {}
        if not _theme_ok(theme, bt.get('name'), bt.get('symbol')):
            continue
        key = (p.get('chainId'), (bt.get('address') or '').lower())
        liq = (p.get('liquidity') or {}).get('usd') or 0
        if key not in best or liq > ((best[key].get('liquidity') or {}).get('usd') or 0):
            best[key] = p
    rows = sorted(best.values(), key=lambda p: -((p.get('volume') or {}).get('h24') or 0))[:20]
    now = time.time() * 1000
    out = []
    for i, p in enumerate(rows):
        bt = p.get('baseToken') or {}
        chg, vol, tx = p.get('priceChange') or {}, p.get('volume') or {}, (p.get('txns') or {}).get('h24') or {}
        created = p.get('pairCreatedAt')
        out.append(_sig('crypto', bt.get('symbol'), bt.get('name'), 'dex_search', _strength(i, len(rows)),
                        text=f"{bt.get('name', '')} {theme['query']}",
                        metrics={'price': _money(p.get('priceUsd')), 'mcap': p.get('marketCap') or p.get('fdv'),
                                 'fdv': p.get('fdv'), 'liq': (p.get('liquidity') or {}).get('usd'),
                                 'vol24': vol.get('h24'), 'vol1h': vol.get('h1'),
                                 'chg1h': chg.get('h1'), 'chg24': chg.get('h24'),
                                 'buys24': tx.get('buys'), 'sells24': tx.get('sells'),
                                 'age_h': round((now - created) / 3.6e6, 1) if created else None},
                        url=p.get('url', ''), ids={'chain': p.get('chainId'), 'address': bt.get('address')}))
    return out


def yahoo_search(theme: dict) -> list:
    """Yahoo Finance search: US-listed stocks (not ETFs) whose name matches what you typed."""
    q = urllib.parse.urlencode({'q': theme['query'], 'quotesCount': 20, 'newsCount': 0})
    d = get_json(f'{YF}/v1/finance/search?{q}', ttl=900)
    qs = [x for x in d.get('quotes', []) if x.get('quoteType') == 'EQUITY' and x.get('symbol')
          and '.' not in x['symbol'] and _theme_ok(theme, x.get('shortname'), x.get('longname'), x.get('symbol'))]
    return [_sig('stock', x['symbol'], x.get('longname') or x.get('shortname') or '', 'yahoo_search',
                 _strength(i, len(qs)), text=f"{x.get('longname') or x.get('shortname') or ''} {theme['query']}",
                 url=f"https://finance.yahoo.com/quote/{x['symbol']}") for i, x in enumerate(qs)]


THEME_SOURCES = {
    'cg_search': {'asset': 'crypto', 'fn': cg_search},
    'cg_category': {'asset': 'crypto', 'fn': cg_category},
    'dex_search': {'asset': 'crypto', 'fn': dex_search},
    'yahoo_search': {'asset': 'stock', 'fn': yahoo_search},
}

CANDIDATE_SOURCES = {
    'coingecko_trending': {'asset': 'crypto', 'fn': coingecko_trending},
    'dexscreener': {'asset': 'crypto', 'fn': dexscreener},
    'reddit_crypto': {'asset': 'crypto', 'fn': reddit_crypto},
    'reddit_stocks': {'asset': 'stock', 'fn': reddit_stocks},
    'yahoo_trending': {'asset': 'stock', 'fn': yahoo_trending},
    'yahoo_screeners': {'asset': 'stock', 'fn': yahoo_screeners},
}
