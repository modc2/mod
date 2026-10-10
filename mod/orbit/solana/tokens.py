"""solana tokens — every token on the chain, and the liquidity actually behind it.

"How much liquidity does this token have" has no single answer, and every venue
that prints one number is hiding that. Three different quantities get called
liquidity:

  quotable    what an aggregator says it can route through right now
  reserves    the USD sitting in every pool the token trades in, both sides
  executable  what you could actually sell before the price moves against you

They disagree, often by an order of magnitude, and the disagreement is the
interesting part: a token whose pools hold $4M of reserves but which cannot
absorb a $10k sell is not a $4M token. So this module reports all three, says
which source measured which, and — this is the part nobody else does — MEASURES
the third by pricing real sells of increasing size until the cost of getting out
exceeds a threshold. That number is `executable_usd`, and it is the only one
here that is a measurement rather than a report.

The sources are plug-in. A `Source` answers three questions — what tokens exist,
what one token looks like, which pools hold it — and may decline any of them.
`Book` merges whatever answers, dedupes pools by address, throws out the pools
that claim more liquidity than the token has market cap, and never mentions a
vendor name in its own logic. Adding a venue is one class and one line in
SOURCES; nothing else in the module changes.

    Book().universe(sort='liquidity')          # the tradeable universe, ranked
    Book().liquidity(mint)                     # one token, all three numbers
    Book().pools(mint)                         # where that liquidity sits
    Book().venues()                            # where the CHAIN's liquidity sits
"""

import math
import os
import time
import urllib.parse

from chain import JUP, MAJORS, WSOL, Client, _cached, _http
from keys import SolError, is_address

GECKO = 'https://api.geckoterminal.com/api/v2/networks/solana'
DEXSCREENER = 'https://api.dexscreener.com'
USDC = MAJORS['USDC']

# The universe is big and slow to fetch and does not change on the second, so
# it is held far longer than a price. Pools move faster; a quote is never cached
# at all, because a quote is the thing being measured.
TTL_UNIVERSE = float(os.environ.get('SOLANA_UNIVERSE_TTL', '300') or 300)
TTL_POOLS = float(os.environ.get('SOLANA_POOLS_TTL', '120') or 120)
TTL_VENUES = 600
# Calls made per source in the last minute, so a self-imposed budget can be kept
# without discovering the real one the expensive way. It is per PROCESS while the
# published cap is per IP, so a box running this module twice can still trip the
# real one — which is why standing down gracefully matters as much as counting.
_BUDGET = {}

# Jupiter's own curated lists. Each is a different question: `verified` is the
# whole tradeable universe (~3k mints), the rest are leaderboards capped at 100.
LISTS = {
    'verified': ('the tradeable universe — every mint an aggregator will route',
                 '/tokens/v2/tag', {'query': 'verified'}),
    'top': ('ranked by organic score — real traders, not wash volume',
            '/tokens/v2/toporganicscore/24h', {}),
    'traded': ('most 24h volume', '/tokens/v2/toptraded/24h', {}),
    'trending': ('fastest-moving right now', '/tokens/v2/toptrending/24h', {}),
    'new': ('most recently created mints — almost all of it is noise',
            '/tokens/v2/recent', {}),
    'lst': ('liquid staking tokens', '/tokens/v2/tag', {'query': 'lst'}),
    'meme': ('memecoins', '/tokens/v2/tag', {'query': 'meme'}),
    'defi': ('defi protocol tokens', '/tokens/v2/tag', {'query': 'defi'}),
    'major': ('the majors', '/tokens/v2/tag', {'query': 'major'}),
    'equities': ('tokenised equities', '/tokens/v2/tag', {'query': 'equities'}),
    'launchpad': ('launchpad tokens', '/tokens/v2/tag', {'query': 'launchpad'}),
}

# What the headline liquidity number means, in words a human can act on.
TIERS = (
    (5_000_000, 'deep', 'institutional size clears without moving the price'),
    (1_000_000, 'liquid', 'six figures trades comfortably'),
    (100_000, 'tradeable', 'four figures is fine, five will cost you'),
    (10_000, 'thin', 'a few thousand dollars moves the price'),
    (0, 'dust', 'there is no market here — you can buy but not sell'),
)

# Every flag the module can raise, and the one line each is for. Kept beside the
# code that raises them so a filter UI can list them without inventing names.
_FLAG_MEANINGS = {
    'mint': 'the mint authority is live — supply can still be inflated',
    'freeze': 'the freeze authority is live — your account can be frozen',
    'nomarket': 'no routable market at all',
    'thin': 'under $10k of liquidity',
    'exit': 'liquidity is under 1% of market cap',
    'churn': 'volume is more than 20x the liquidity in 24h',
    'inorganic': 'under 5% of volume is organic',
    'whales': 'top holders own more than half of it',
    'new': 'less than a week old',
    'draining': 'liquidity fell more than 30% in 24h',
    'redeemable': 'liquidity equals the whole market cap — a wrapper redeeming, '
                  'not a market trading',
}

SORTS = {
    'liquidity': lambda t: t.get('liquidity_usd') or 0,
    'volume': lambda t: t.get('volume_24h_usd') or 0,
    'mcap': lambda t: t.get('mcap_usd') or 0,
    'fdv': lambda t: t.get('fdv_usd') or 0,
    'holders': lambda t: t.get('holders') or 0,
    'change': lambda t: t.get('change_24h_pct') if t.get('change_24h_pct') is not None else -1e9,
    'turnover': lambda t: t.get('turnover') or 0,
    'organic': lambda t: t.get('organic_score') or 0,
    'depth_ratio': lambda t: t.get('liquidity_to_mcap_pct') or 0,
    'age': lambda t: -(t.get('created_at_ts') or 0),
    'symbol': lambda t: (t.get('symbol') or '').upper(),
}


class Unsupported(Exception):
    """A source was asked something it does not answer. Not an error."""


def _f(v):
    """Anything a JSON API calls a number → a float, or None.

    GeckoTerminal returns reserves as 120-digit decimal strings and DexScreener
    returns them as floats; both also return null, empty string and the string
    "NaN" for "we do not know". All of those must become None rather than 0.0 —
    a zero here would silently read as "no liquidity".
    """
    if v is None or v is True or v is False or v == '':
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _pct(part, whole):
    if not whole or part is None:
        return None
    return round(100.0 * part / whole, 2)


def _age_days(ts):
    return None if not ts else round((time.time() - ts) / 86400.0, 1)


def _iso_ts(s):
    if not s:
        return None
    try:
        import datetime
        return datetime.datetime.strptime(
            str(s).replace('Z', '+0000'), '%Y-%m-%dT%H:%M:%S%z').timestamp()
    except Exception:
        try:
            return float(s) / 1000.0 if float(s) > 1e11 else float(s)
        except Exception:
            return None


def tier(liquidity_usd):
    """A dollar figure → the word for it, and what that word means for a trade."""
    for floor, name, meaning in TIERS:
        if (liquidity_usd or 0) >= floor:
            return {'grade': name, 'means': meaning}
    return {'grade': 'dust', 'means': TIERS[-1][2]}


# ── sources ──────────────────────────────────────────────────────
# Each source answers what it can and raises Unsupported for the rest. None of
# them may raise anything else: Book runs them in a row, and one venue being
# down must degrade the answer, never fail it.

class Source:
    """One market-data venue, normalised.

    Subclasses fill in whichever of the three questions they can answer:

      universe(kind, limit) → [token]     what tokens exist
      tokens(mints)         → {mint: token}   what these tokens look like
      pools(mint, limit)    → [pool]      which pools hold this one

    A `token` is {mint, symbol, name, price_usd, liquidity_usd, volume_24h_usd,
    mcap_usd, fdv_usd, holders, change_24h_pct, source, …}; a `pool` is
    {address, dex, pair, liquidity_usd, token_side_usd, volume_24h_usd,
    created_at_ts, source}. Nothing downstream knows any other shape.
    """

    name = ''
    what = ''
    measures = ''          # which of the three quantities its number is
    home = ''
    mainnet_only = True
    max_batch = 30
    per_minute = 0         # 0 = no local budget; otherwise calls/min, self-imposed

    def universe(self, kind='verified', limit=None):
        raise Unsupported(f'{self.name} has no token list')

    def tokens(self, mints):
        raise Unsupported(f'{self.name} cannot look tokens up by mint')

    def pools(self, mint, limit=50):
        raise Unsupported(f'{self.name} does not break liquidity down by pool')

    def top_pools(self, pages=3):
        raise Unsupported(f'{self.name} has no chain-wide pool list')

    def get(self, url, params=None, ttl=TTL_POOLS, timeout=25):
        if params:
            url += '?' + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, '')})
        return _cached(f'{self.name}:{url}', ttl, lambda: self._spend(url, timeout))

    def _spend(self, url, timeout):
        """One call, against a self-imposed budget.

        These are free public indexes with published per-minute caps, and the
        console can walk through tokens far faster than that. Discovering the
        cap by being 429'd costs a wasted round trip and a retry sleep for every
        request after it, so the budget is counted here instead: when it is
        spent the source stands down and says when it will be back, which
        degrades one column of one panel rather than stalling the page. Cache
        hits never reach this — only real calls are counted.
        """
        if self.per_minute:
            now = time.time()
            recent = [t for t in _BUDGET.setdefault(self.name, []) if now - t < 60]
            if len(recent) >= self.per_minute:
                _BUDGET[self.name] = recent
                raise SolError(f'holding off — {self.per_minute}/min is this box\'s '
                               f'self-imposed budget for {self.name}, free again in '
                               f'{60 - int(now - recent[0])}s', status=429)
            recent.append(now)
            _BUDGET[self.name] = recent
        return _http(url, timeout=timeout)


class Jupiter(Source):
    """The aggregator. Its `liquidity` is what its router believes it can move
    through right now — one-sided and already discounted for the pools it will
    not touch. It is the only source here with a real universe."""

    name = 'jupiter'
    what = 'the aggregator that actually routes the trade'
    measures = 'quotable'
    home = 'https://lite-api.jup.ag'
    max_batch = 50

    def universe(self, kind='verified', limit=None):
        if kind not in LISTS:
            raise Unsupported(f'no list called {kind!r} — try: {", ".join(LISTS)}')
        _, path, params = LISTS[kind]
        # The leaderboards cap at 100 server-side; the tag lists ignore limit and
        # return everything, which is the point of them.
        q = dict(params)
        if path != '/tokens/v2/tag':
            q['limit'] = min(int(limit or 100), 100)
        rows = self.get(JUP + path, q, ttl=TTL_UNIVERSE, timeout=60) or []
        return [self._token(r) for r in rows if isinstance(r, dict) and r.get('id')]

    def tokens(self, mints):
        out = {}
        for i in range(0, len(mints), self.max_batch):
            chunk = mints[i:i + self.max_batch]
            rows = self.get(JUP + '/tokens/v2/search',
                            {'query': ','.join(chunk)}, ttl=TTL_POOLS) or []
            for r in rows:
                if isinstance(r, dict) and r.get('id'):
                    out[r['id']] = self._token(r)
        return out

    @staticmethod
    def _token(r):
        stats = r.get('stats24h') or {}
        audit = r.get('audit') or {}
        buy, sell = _f(stats.get('buyVolume')) or 0, _f(stats.get('sellVolume')) or 0
        first = (r.get('firstPool') or {}).get('createdAt')
        return {
            'mint': r.get('id'),
            'symbol': r.get('symbol'), 'name': r.get('name'), 'icon': r.get('icon'),
            'decimals': r.get('decimals'),
            'price_usd': _f(r.get('usdPrice')),
            'liquidity_usd': _f(r.get('liquidity')),
            'volume_24h_usd': (buy + sell) or None,
            'organic_volume_24h_usd': ((_f(stats.get('buyOrganicVolume')) or 0) +
                                       (_f(stats.get('sellOrganicVolume')) or 0)) or None,
            'change_24h_pct': _f(stats.get('priceChange')),
            'liquidity_change_24h_pct': _f(stats.get('liquidityChange')),
            'mcap_usd': _f(r.get('mcap')), 'fdv_usd': _f(r.get('fdv')),
            'supply': _f(r.get('circSupply')),
            'holders': r.get('holderCount'),
            'traders_24h': stats.get('numTraders'),
            'organic_score': _f(r.get('organicScore')),
            'organic_label': r.get('organicScoreLabel'),
            'verified': bool(r.get('isVerified')),
            'tags': r.get('tags') or [],
            'token_program': r.get('tokenProgram'),
            'mint_authority_live': audit.get('mintAuthorityDisabled') is False,
            'freeze_authority_live': audit.get('freezeAuthorityDisabled') is False,
            'top_holders_pct': _f(audit.get('topHoldersPercentage')),
            'created_at_ts': _iso_ts(first or r.get('createdAt')),
            'first_pool': (r.get('firstPool') or {}).get('id'),
            'source': 'jupiter',
        }


class GeckoTerminal(Source):
    """Pool reserves. Its number counts BOTH sides of every pool the token
    appears in, so it is always the largest of the three and is not what you
    could sell — half of it is the counter-asset."""

    name = 'geckoterminal'
    what = 'pool reserves, indexed per DEX'
    measures = 'reserves'
    home = 'https://www.geckoterminal.com'
    max_batch = 30
    per_minute = 25            # the public cap is 30; leave headroom

    def tokens(self, mints):
        out = {}
        for i in range(0, len(mints), self.max_batch):
            chunk = mints[i:i + self.max_batch]
            data = (self.get(f'{GECKO}/tokens/multi/{",".join(chunk)}') or {}).get('data')
            for row in data or []:
                a = row.get('attributes') or {}
                if not a.get('address'):
                    continue
                out[a['address']] = {
                    'mint': a['address'], 'symbol': a.get('symbol'),
                    'name': a.get('name'), 'icon': a.get('image_url'),
                    'decimals': a.get('decimals'),
                    'price_usd': _f(a.get('price_usd')),
                    'liquidity_usd': _f(a.get('total_reserve_in_usd')),
                    'volume_24h_usd': _f((a.get('volume_usd') or {}).get('h24')),
                    'mcap_usd': _f(a.get('market_cap_usd')), 'fdv_usd': _f(a.get('fdv_usd')),
                    'source': self.name,
                }
        return out

    def pools(self, mint, limit=50):
        rows, page = [], 1
        while len(rows) < limit and page <= 3:
            data = (self.get(f'{GECKO}/tokens/{mint}/pools', {'page': page}) or {}).get('data')
            if not data:
                break
            rows += data
            if len(data) < 20:
                break
            page += 1
        return [p for p in (self._pool(r, mint) for r in rows[:limit]) if p]

    def top_pools(self, pages=3):
        # Sorted by 24h volume, because no index sorts by reserves — so this is
        # the busiest pools, not the deepest ones, and the caller is told that.
        rows = []
        for page in range(1, max(1, int(pages)) + 1):
            data = (self.get(f'{GECKO}/pools',
                             {'page': page, 'sort': 'h24_volume_usd_desc'},
                             ttl=TTL_VENUES) or {}).get('data')
            if not data:
                break
            rows += data
        return [p for p in (self._pool(r, None) for r in rows) if p]

    def venues(self):
        data = (self.get(f'{GECKO}/dexes', ttl=TTL_VENUES) or {}).get('data') or []
        return {d.get('id'): (d.get('attributes') or {}).get('name') for d in data}

    def _pool(self, row, mint):
        a = row.get('attributes') or {}
        rel = row.get('relationships') or {}
        if not a.get('address'):
            return None

        def side(key):
            ident = ((rel.get(key) or {}).get('data') or {}).get('id') or ''
            return ident.split('_', 1)[1] if '_' in ident else None

        base, quote = side('base_token'), side('quote_token')
        return {
            'address': a['address'],
            'dex': ((rel.get('dex') or {}).get('data') or {}).get('id'),
            'pair': a.get('name'),
            'base': base, 'quote': quote,
            'mint': mint or base,
            'liquidity_usd': _f(a.get('reserve_in_usd')),
            'token_side_usd': None,          # gecko reports the pool, not the side
            'volume_24h_usd': _f((a.get('volume_usd') or {}).get('h24')),
            'price_usd': _f(a.get('base_token_price_usd')),
            'change_24h_pct': _f((a.get('price_change_percentage') or {}).get('h24')),
            'txns_24h': sum(v or 0 for v in
                            ((a.get('transactions') or {}).get('h24') or {}).values()),
            'fdv_usd': _f(a.get('fdv_usd')),
            'created_at_ts': _iso_ts(a.get('pool_created_at')),
            'name': a.get('name'),
            'source': self.name,
        }


class DexScreener(Source):
    """Pairs, with the reserves broken into the two sides — the only source
    here that says how much of the pool is the TOKEN rather than the money
    across from it. Capped at 30 pairs per token, so its sum is a floor."""

    name = 'dexscreener'
    what = 'pairs, with both sides of the reserve itemised'
    measures = 'reserves'
    home = 'https://dexscreener.com'
    max_batch = 30
    per_minute = 250           # the public cap is 300

    def pools(self, mint, limit=50):
        rows = self.get(f'{DEXSCREENER}/token-pairs/v1/solana/{mint}') or []
        out = [p for p in (self._pool(r, mint) for r in rows if isinstance(r, dict)) if p]
        # The endpoint tops out at 30 pairs. That is a truncation, not a total —
        # the caller has to know, or it will read a floor as a sum.
        if len(out) >= 30:
            for p in out:
                p['list_truncated'] = True
        return out[:limit]

    def _pool(self, r, mint):
        if not r.get('pairAddress'):
            return None
        liq = r.get('liquidity') or {}
        base, quote = r.get('baseToken') or {}, r.get('quoteToken') or {}
        price = _f(r.get('priceUsd'))
        # base/quote are token AMOUNTS. The token's own side is the one whose
        # mint matches, priced at this pair's own price — which is the honest
        # single-sided number the aggregators never publish.
        side = None
        if price is not None:
            if base.get('address') == mint and _f(liq.get('base')) is not None:
                side = _f(liq.get('base')) * price
            elif quote.get('address') == mint and _f(liq.get('quote')) is not None:
                total, b = _f(liq.get('usd')), _f(liq.get('base'))
                if total is not None and b is not None:
                    side = max(total - b * price, 0.0)
        txns = r.get('txns') or {}
        h24 = txns.get('h24') or {}
        return {
            'address': r['pairAddress'],
            'dex': r.get('dexId'),
            'pair': f"{base.get('symbol') or '?'} / {quote.get('symbol') or '?'}",
            'base': base.get('address'), 'quote': quote.get('address'),
            'mint': mint,
            'liquidity_usd': _f(liq.get('usd')),
            'token_side_usd': side,
            'volume_24h_usd': _f((r.get('volume') or {}).get('h24')),
            'price_usd': price,
            'change_24h_pct': _f((r.get('priceChange') or {}).get('h24')),
            'txns_24h': (h24.get('buys') or 0) + (h24.get('sells') or 0),
            'fdv_usd': _f(r.get('fdv')),
            'created_at_ts': _iso_ts(r.get('pairCreatedAt')),
            'source': self.name,
        }


SOURCES = {s.name: s for s in (Jupiter(), GeckoTerminal(), DexScreener())}


# ── the book ─────────────────────────────────────────────────────

class Book:
    """Every source merged into one answer, with the disagreements kept.

    Nothing in here names a venue: sources are looked up in SOURCES, asked, and
    allowed to fail. What the class owns is the reasoning — deduping pools,
    throwing out the ones that claim impossible reserves, and measuring the
    depth that no source will tell you.
    """

    def __init__(self, client=None, network=None, rpc=None, sources=None):
        self.client = client or Client(network=network, rpc=rpc)
        self.network = self.client.network
        self.sources = [SOURCES[n] for n in (sources or SOURCES) if n in SOURCES]
        self.warnings = []
        if self.network != 'mainnet':
            self._warn(f'{self.network} has no DEX liquidity to index — every market '
                       'data source here covers mainnet only')

    def _warn(self, reason):
        if reason not in self.warnings:
            self.warnings.append(reason)

    def _out(self, payload):
        return {**payload, 'warnings': self.warnings} if self.warnings else payload

    def _ask(self, method, *args, **kw):
        """Run one method across every source. Returns [(source, value)] for the
        ones that answered; a venue that is down or does not do this becomes a
        warning, never an exception."""
        got = []
        for src in self.sources:
            if src.mainnet_only and self.network != 'mainnet':
                continue
            try:
                got.append((src, getattr(src, method)(*args, **kw)))
            except Unsupported:
                continue
            except SolError as e:
                self._warn(f'{src.name}: {e}')
            except Exception as e:                       # a venue changing shape
                self._warn(f'{src.name}: unreadable response ({type(e).__name__})')
        return got

    # ── the universe ─────────────────────────────────────────────

    def universe(self, kind='verified', sort='liquidity', limit=100, offset=0,
                 min_liquidity=None, max_liquidity=None, query=None, tag=None,
                 desc=True, safe_only=False, exclude=None):
        """Every token, ranked. This is the "show me all of them" answer.

        The list itself comes from whichever source has one; the columns that
        matter for reading it — turnover, how much of the market cap the
        liquidity actually is, and the flags — are computed here, because they
        are ratios between numbers no single venue publishes together.
        """
        rows, listed_by = [], None
        for src, got in self._ask('universe', kind, limit=None):
            if got:
                rows, listed_by = got, src.name
                break
        rows = [self.enrich(r) for r in rows]
        universe_total = len(rows)
        universe_liquidity = sum(r.get('liquidity_usd') or 0 for r in rows)

        # No flag is excluded unless someone asks. The API answers the whole
        # question; it is the console that has an opinion about which half of it
        # a person meant, and says which one it is applying.
        if isinstance(exclude, str):
            exclude = [x.strip() for x in exclude.split(',') if x.strip()]
        exclude = set(exclude or ())
        matched = [r for r in rows if self._matches(
            r, min_liquidity, max_liquidity, query, tag, safe_only, exclude)]
        key = SORTS.get(sort, SORTS['liquidity'])
        matched.sort(key=key, reverse=bool(desc))
        offset, limit = max(0, int(offset)), max(1, min(int(limit or 100), 2000))
        page = matched[offset:offset + limit]

        return self._out({
            'network': self.network,
            'list': kind, 'listed_by': listed_by,
            'note': None if rows else (
                'no token list came back — ' +
                ('; '.join(self.warnings) if self.warnings else
                 'no configured source lists tokens for this network') +
                '. This is a source being unreachable, not a chain with no tokens.'),
            'what': LISTS.get(kind, ('',))[0],
            'lists': {k: v[0] for k, v in LISTS.items()},
            'sort': sort, 'desc': bool(desc), 'offset': offset, 'limit': limit,
            'excluded_flags': sorted(exclude) or None,
            'hidden_by_exclude': sum(
                1 for r in rows
                if any(f['flag'] in exclude for f in r.get('flags') or ())) or None,
            'flags': {f: m for f, m in _FLAG_MEANINGS.items()},
            'total': universe_total, 'matched': len(matched), 'returned': len(page),
            'totals': {
                'liquidity_usd': round(universe_liquidity, 2),
                'matched_liquidity_usd': round(
                    sum(r.get('liquidity_usd') or 0 for r in matched), 2),
                'volume_24h_usd': round(
                    sum(r.get('volume_24h_usd') or 0 for r in rows), 2),
                'tokens_over_10k': sum(1 for r in rows
                                       if (r.get('liquidity_usd') or 0) >= 10_000),
                'tokens_over_1m': sum(1 for r in rows
                                      if (r.get('liquidity_usd') or 0) >= 1_000_000),
                'tokens_with_no_market': sum(1 for r in rows
                                             if not r.get('liquidity_usd')),
            },
            'concentration': self._share_of_top(rows),
            'measures': 'quotable',
            'means': 'the USD an aggregator says it can route through this mint '
                     'right now — one-sided. Open one token for the other two '
                     'numbers, including the sell this module actually priced.',
            'tokens': page,
        })

    @staticmethod
    def _matches(r, min_liq, max_liq, query, tag, safe_only, exclude=()):
        liq = r.get('liquidity_usd') or 0
        if min_liq is not None and liq < float(min_liq):
            return False
        if max_liq is not None and liq > float(max_liq):
            return False
        if tag and tag not in (r.get('tags') or []):
            return False
        if safe_only and (r.get('mint_authority_live') or r.get('freeze_authority_live')):
            return False
        if exclude and any(f['flag'] in exclude for f in r.get('flags') or ()):
            return False
        if query:
            q = str(query).lower()
            hay = ' '.join(str(r.get(k) or '') for k in ('symbol', 'name', 'mint')).lower()
            if q not in hay:
                return False
        return True

    @staticmethod
    def _share_of_top(rows):
        """How much of the chain's liquidity sits in the few biggest tokens."""
        liq = sorted((r.get('liquidity_usd') or 0 for r in rows), reverse=True)
        total = sum(liq) or None
        return {'top_10_pct': _pct(sum(liq[:10]), total),
                'top_50_pct': _pct(sum(liq[:50]), total),
                'tokens': len(liq)}

    def enrich(self, t):
        """The derived columns — the ones that turn a liquidity number into a
        judgement. Every one of them is a ratio between two numbers that are
        published separately and never printed side by side."""
        liq = t.get('liquidity_usd') or 0
        vol = t.get('volume_24h_usd') or 0
        mcap = t.get('mcap_usd') or t.get('fdv_usd') or 0
        t = dict(t)
        # Four places, not two: a token doing $30 of volume against $7m of
        # liquidity has a real turnover, and rounding it to 0.00 says something
        # different from "we did not measure it".
        t['turnover'] = round(vol / liq, 4) if liq else None
        t['liquidity_to_mcap_pct'] = _pct(liq, mcap or None)
        t['organic_share_pct'] = _pct(t.get('organic_volume_24h_usd'), vol or None)
        t['age_days'] = _age_days(t.get('created_at_ts'))
        t.update(tier(liq))
        t['flags'] = self.flags(t)
        return t

    @staticmethod
    def flags(t):
        """Everything about this token that should stop you before you buy it.

        Each flag is a fact plus its consequence — "mint authority live" means
        nothing to a reader who does not already know what it implies."""
        liq = t.get('liquidity_usd') or 0
        mcap = t.get('mcap_usd') or t.get('fdv_usd') or 0
        out = []
        if t.get('mint_authority_live'):
            out.append(('mint', 'supply can still be inflated — the mint authority is live'))
        if t.get('freeze_authority_live'):
            out.append(('freeze', 'your account can be frozen — the freeze authority is live'))
        if not liq:
            out.append(('nomarket', 'no routable market — you could buy this and never sell it'))
        elif liq < 10_000:
            out.append(('thin', f'${liq:,.0f} of liquidity — a small sell moves the price'))
        if mcap and liq and (liq / mcap) < 0.01:
            out.append(('exit', f'liquidity is {100 * liq / mcap:.2f}% of market cap — '
                                'the cap is notional, most holders cannot get out'))
        if (t.get('turnover') or 0) > 20:
            out.append(('churn', f"volume is {t['turnover']:.0f}x the liquidity in 24h — "
                                 'usually bots or wash trading, not demand'))
        if t.get('organic_share_pct') is not None and t['organic_share_pct'] < 5 and liq:
            out.append(('inorganic', f"only {t['organic_share_pct']:.1f}% of volume is "
                                     'organic — the rest is routing and bots'))
        if (t.get('liquidity_to_mcap_pct') or 0) >= 99 and (t.get('turnover') or 0) < 0.5:
            out.append(('redeemable', 'the reported liquidity is the entire market cap, '
                                      'which no DEX book ever is — this is a wrapper '
                                      '(staked SOL, a lending receipt) whose liquidity '
                                      'is redemption, not depth. Open it to see what a '
                                      'market order would really get.'))
        if (t.get('top_holders_pct') or 0) > 50:
            out.append(('whales', f"top holders own {t['top_holders_pct']:.0f}% — "
                                  'a handful of wallets can exit into you'))
        if (t.get('age_days') or 999) < 7:
            out.append(('new', f"{t['age_days']} days old — no track record, and "
                               'nothing about the pool has been tested by a sell-off '
                               'yet'))
        if (t.get('liquidity_change_24h_pct') or 0) < -30:
            out.append(('draining', f"liquidity fell {abs(t['liquidity_change_24h_pct']):.0f}% "
                                    'in 24h — someone is pulling the pool'))
        return [{'flag': f, 'means': m} for f, m in out]

    # ── one token ────────────────────────────────────────────────

    def liquidity(self, mint, depth=True, sizes=None, cost_limit_pct=1.0,
                  pool_limit=50):
        """One token's liquidity, all three ways, with the pools underneath.

        The three numbers are reported separately and never averaged. Averaging
        them would produce a figure that no source stands behind and that
        describes nothing — the spread between them IS the finding.
        """
        mint = self.resolve(mint)
        reported = {}
        for src, got in self._ask('tokens', [mint]):
            row = (got or {}).get(mint)
            if row:
                reported[src.name] = {**row, 'measures': src.measures,
                                      'what': src.what}

        pools, sources_seen, truncated = self.merge_pools(mint, pool_limit)
        missing = [s.name for s in self.sources
                   if s.name not in sources_seen and s.measures == 'reserves']
        head = reported.get('jupiter') or next(iter(reported.values()), {})
        price = head.get('price_usd') or next(
            (r.get('price_usd') for r in reported.values() if r.get('price_usd')), None)
        mcap = head.get('mcap_usd') or head.get('fdv_usd')

        real = [p for p in pools if not p.get('suspect')]
        reserves = sum(p.get('liquidity_usd') or 0 for p in real) or None
        side = [p for p in real if p.get('token_side_usd') is not None]
        token_side = sum(p['token_side_usd'] for p in side) or None
        quotable = next((r.get('liquidity_usd') for r in reported.values()
                         if r.get('measures') == 'quotable'), None)

        probe = self.depth(mint, sizes=sizes, price_usd=price,
                           cost_limit_pct=cost_limit_pct) if depth else None
        executable = (probe or {}).get('executable_usd')

        row = self.enrich({**head, 'mint': mint})
        breakdown = self.by_venue(real)
        out = {
            'network': self.network, 'mint': mint,
            'symbol': row.get('symbol'), 'name': row.get('name'),
            'icon': row.get('icon'), 'decimals': row.get('decimals'),
            'price_usd': price,
            'liquidity_usd': quotable if quotable is not None else reserves,
            'grade': row.get('grade'), 'means': row.get('means'),
            'liquidity': {
                'quotable_usd': quotable,
                'pool_reserves_usd': round(reserves, 2) if reserves else None,
                'token_side_usd': round(token_side, 2) if token_side else None,
                'executable_usd': executable,
            },
            'definitions': {
                'quotable_usd': 'what an aggregator says it can route through this '
                                'mint right now. One-sided, already discounted for '
                                'pools it will not touch.',
                'pool_reserves_usd': 'the USD in every pool the token trades in, '
                                     'BOTH sides. Always the largest number, and '
                                     'roughly half of it is the counter-asset.',
                'token_side_usd': 'just the token half of those reserves, where a '
                                  'source itemised the two sides.',
                'executable_usd': f'measured, not reported: the largest sell this '
                                  f'module could price at under {cost_limit_pct}% '
                                  'all-in cost, by quoting real routes at '
                                  'increasing size. The only one of the four that '
                                  'answers "what can I actually get out".',
            },
            'sources': [
                {'source': name, 'measures': r.get('measures'), 'what': r.get('what'),
                 'liquidity_usd': r.get('liquidity_usd'),
                 'price_usd': r.get('price_usd'),
                 'volume_24h_usd': r.get('volume_24h_usd')}
                for name, r in reported.items()],
            'disagreement': self._spread(
                [r.get('liquidity_usd') for r in reported.values()] +
                ([reserves] if reserves else [])),
            'market': {
                'volume_24h_usd': row.get('volume_24h_usd'),
                'organic_share_pct': row.get('organic_share_pct'),
                'turnover': row.get('turnover'),
                'change_24h_pct': row.get('change_24h_pct'),
                'liquidity_change_24h_pct': row.get('liquidity_change_24h_pct'),
                'mcap_usd': row.get('mcap_usd'), 'fdv_usd': row.get('fdv_usd'),
                'supply': row.get('supply'), 'holders': row.get('holders'),
                'traders_24h': row.get('traders_24h'),
                'liquidity_to_mcap_pct': row.get('liquidity_to_mcap_pct'),
                'organic_score': row.get('organic_score'),
                'organic_label': row.get('organic_label'),
                'age_days': row.get('age_days'), 'tags': row.get('tags'),
                'verified': row.get('verified'),
            },
            'concentration': self.concentration(real, breakdown),
            'venues': breakdown,
            'pools': pools,
            'pool_sources': sources_seen,
            'pool_sources_unavailable': missing or None,
            'depth': probe,
            'flags': row.get('flags'),
        }
        if truncated:
            self._warn('at least one pool list was truncated by its source — the '
                       'reserve total is a floor, not a sum')
        return self._out(out)

    def merge_pools(self, mint, limit=50):
        """Every pool from every source, deduped by pool address.

        Two sources describing the same pool is the normal case; taking both
        would double the token's liquidity. The address is the identity, and
        the richer record wins — the one that itemised the two sides beats the
        one that only gave a total.
        """
        merged, seen, truncated = {}, [], False
        for src, got in self._ask('pools', mint, limit=limit):
            seen.append(src.name)
            for p in got or []:
                truncated = truncated or bool(p.get('list_truncated'))
                cur = merged.get(p['address'])
                if not cur:
                    merged[p['address']] = cur = {**p, 'sources': [src.name],
                                                  'liquidity_by_source': {}}
                else:
                    cur['sources'].append(src.name)
                    for k, v in p.items():
                        if cur.get(k) in (None, '') and v not in (None, ''):
                            cur[k] = v
                if p.get('liquidity_usd') is not None:
                    cur['liquidity_by_source'][src.name] = p['liquidity_usd']

        # Where two indexes price the same pool differently, the smaller number
        # is used. This is a one-way bet: overstating depth costs someone real
        # money on a sell that will not fill, understating it costs nothing but
        # caution — and a 5000x disagreement about one pool (it happens, and it
        # is usually a DLMM bin being read as a whole book) should not silently
        # become the token's headline. Both readings are kept on the row.
        for p in merged.values():
            vals = [v for v in p['liquidity_by_source'].values() if v is not None]
            if len(vals) > 1:
                p['liquidity_usd'] = min(vals)
                if max(vals) > max(min(vals), 1) * 2:
                    p['disputed'] = ' vs '.join(
                        f'{k} ${v:,.0f}' for k, v in
                        sorted(p['liquidity_by_source'].items(), key=lambda kv: -kv[1])
                    ) + ' — the smaller is counted'
        pools = self._screen(self._reconcile(list(merged.values())))
        pools.sort(key=lambda p: -(p.get('liquidity_usd') or 0))
        total = sum(p.get('liquidity_usd') or 0 for p in pools if not p.get('suspect'))
        for p in pools:
            p['share_pct'] = None if p.get('suspect') else _pct(p.get('liquidity_usd'), total)
            p['age_days'] = _age_days(p.get('created_at_ts'))
        return pools, seen, truncated

    @staticmethod
    def _reconcile(pools):
        """A merged pool is two sources describing one thing, and they can
        contradict each other. One invariant catches almost all of it: the
        token's side of a pool cannot be worth more than the whole pool. When
        it is, one of the two numbers is wrong and the side — the softer,
        derived one — is the one that goes."""
        for p in pools:
            side, total = p.get('token_side_usd'), p.get('liquidity_usd')
            if side is not None and total and side > total * 1.05:
                p['token_side_usd'] = None
                p['side_dropped'] = (f'one source put ${side:,.0f} of the token in a '
                                     f'${total:,.0f} pool — impossible, so the side is '
                                     'not counted')
        return pools

    def _screen(self, pools, related=True):
        """Throw out the impossible ones.

        A pool that claims more USD than the entire token is worth is a spoof —
        a fake reserve posted to look deep in a screener. It gets kept in the
        list so you can see it, and excluded from every total, with the reason
        attached. The test is against the median of the other pools rather than
        an absolute: on a real token the pools are within an order of magnitude
        of each other, and the fake is three orders above.
        """
        vals = sorted(p.get('liquidity_usd') or 0 for p in pools)
        if len(vals) < 3:
            return pools
        # The median test compares a pool against its siblings, which only means
        # something when they hold the SAME token. Across unrelated tokens the
        # median is a pump.fun micro-pool and the test would throw out SOL/USDC.
        mid = (vals[len(vals) // 2] or 0) if related else 0
        # The fdv a pool is measured against is its OWN token's — taking the
        # largest in the sample would mean SOL's cap excusing every fake pool
        # on the chain. Where a pool did not report one, and only there, the
        # sample's largest stands in.
        fallback = max((p.get('fdv_usd') or 0) for p in pools) or 0
        for p in pools:
            liq = p.get('liquidity_usd') or 0
            fdv = p.get('fdv_usd') or (fallback if related else 0)
            if fdv and liq > fdv * 5:
                p['suspect'] = (f'claims ${liq:,.0f} of reserves against a ${fdv:,.0f} '
                                'token — excluded from the totals')
            elif mid and liq > mid * 1000 and liq > 1_000_000:
                p['suspect'] = (f'claims ${liq:,.0f} while the median pool for this '
                                f'token holds ${mid:,.0f} — excluded from the totals')
        return pools

    @staticmethod
    def by_venue(pools):
        """Which DEXes hold it, biggest first."""
        agg = {}
        for p in pools:
            v = agg.setdefault(p.get('dex') or 'unknown',
                               {'dex': p.get('dex') or 'unknown',
                                'liquidity_usd': 0.0, 'volume_24h_usd': 0.0, 'pools': 0})
            v['liquidity_usd'] += p.get('liquidity_usd') or 0
            v['volume_24h_usd'] += p.get('volume_24h_usd') or 0
            v['pools'] += 1
        total = sum(v['liquidity_usd'] for v in agg.values()) or None
        out = sorted(agg.values(), key=lambda v: -v['liquidity_usd'])
        for v in out:
            v['liquidity_usd'] = round(v['liquidity_usd'], 2)
            v['volume_24h_usd'] = round(v['volume_24h_usd'], 2)
            v['share_pct'] = _pct(v['liquidity_usd'], total)
        return out

    @staticmethod
    def concentration(pools, venues):
        """One deep pool and nine dust ones is not ten pools of liquidity.

        HHI is the sum of squared shares — 10,000 means a single pool holds
        everything, under 1,500 means genuinely spread. It is the difference
        between a token with a market and a token with a market maker.
        """
        liq = sorted((p.get('liquidity_usd') or 0 for p in pools), reverse=True)
        total = sum(liq) or None
        hhi = round(sum((100.0 * v / total) ** 2 for v in liq)) if total else None
        return {
            'pools': len(liq),
            'pools_over_10k': sum(1 for v in liq if v >= 10_000),
            'venues': len(venues),
            'top_venue': venues[0]['dex'] if venues else None,
            'top_pool_share_pct': _pct(liq[0], total) if liq else None,
            'hhi': hhi,
            'means': (None if hhi is None else
                      'one pool is the entire market — if it is pulled there is no bid'
                      if hhi >= 8000 else
                      'concentrated in a couple of pools' if hhi >= 4000 else
                      'spread across several pools' if hhi >= 1500 else
                      'genuinely distributed across venues'),
        }

    @staticmethod
    def _spread(values):
        vals = [v for v in values if v]
        if len(vals) < 2:
            return None
        lo, hi = min(vals), max(vals)
        return {'low_usd': round(lo, 2), 'high_usd': round(hi, 2),
                'ratio': round(hi / lo, 2),
                'means': 'the sources agree' if hi / lo < 1.5 else
                         f'the sources disagree by {hi / lo:.1f}x — they are counting '
                         'different things, see definitions'}

    # ── the measurement ──────────────────────────────────────────

    def depth(self, mint, sizes=None, price_usd=None, cost_limit_pct=1.0,
              into=None, refine=True):
        """Price real sells of increasing size and report what they cost.

        This is the part that is a measurement. Everything else in this module
        is a number some venue chose to publish; this one asks the router to
        actually route $1k, $10k, $100k and $1M of the token into a stablecoin
        and reads back what would come out the other side.

        The honest cost is not the impact figure the router prints — it is
        1 - (what you receive / what it was nominally worth), which includes
        the fees and the route's own spread. A quote that fails is a result too:
        it means there is no route at that size, which is the answer.
        """
        sizes = sizes or [1_000, 10_000, 100_000, 1_000_000]
        sizes = sorted({float(s) for s in sizes if float(s) > 0})[:8]
        out_mint = into or (WSOL if mint in (USDC, MAJORS['USDT']) else USDC)
        if mint == out_mint:
            out_mint = WSOL if out_mint != WSOL else USDC
        if price_usd is None:
            price_usd = (self.client.prices([mint]).get(mint) or {}).get('usd')
        if not price_usd:
            return {'measured': False,
                    'why': 'no price for this mint, so a USD-sized sell cannot be built'}

        out_price = (self.client.prices([out_mint]).get(out_mint) or {}).get('usd')
        limit = float(cost_limit_pct)

        def quote_at(usd):
            """One rung: sell $usd of the token and report what came back."""
            step = {'usd': round(usd, 2), 'ok': False}
            try:
                q = self.client.quote(mint, out_mint, usd / price_usd)
                got = (q.get('buy') or {}).get('amount')
                got_usd = (q.get('buy') or {}).get('usd')
                if got_usd is None and out_price and got is not None:
                    got_usd = got * out_price
                step.update({
                    'ok': got_usd is not None,
                    'sell_tokens': round(usd / price_usd, 6),
                    'receive_usd': round(got_usd, 2) if got_usd is not None else None,
                    'cost_pct': round(100 * (1 - got_usd / usd), 3)
                    if got_usd else None,
                    'cost_usd': round(usd - got_usd, 2) if got_usd else None,
                    'router_impact_pct': round(100 * (q.get('price_impact_pct') or 0), 3),
                    'hops': q.get('hops'),
                    'route': [r.get('amm') for r in (q.get('route') or [])][:4],
                })
            except SolError as e:
                step['why'] = str(e)
                step['no_route'] = 'no route' in str(e).lower() or e.status == 400
            return step

        ladder = [quote_at(usd) for usd in sizes]

        priced = [s for s in ladder if s.get('cost_pct') is not None]
        executable, how = self._interpolate(priced, limit), 'interpolated'
        if refine and priced:
            executable, extra, how = self._bisect(quote_at, priced, limit, executable)
            ladder = sorted(ladder + extra, key=lambda s: s['usd'])
        worst = max(priced, key=lambda s: s['usd']) if priced else None
        return {
            'measured': True,
            'into': {'mint': out_mint,
                     'symbol': 'USDC' if out_mint == USDC else 'SOL'},
            'cost_limit_pct': limit,
            'executable_usd': executable,
            'executable_how': how,
            'means': (None if executable is None else
                      f'${executable:,.0f} is the largest sell that clears at under '
                      f'{limit}% all-in cost. Above that you are paying the market to '
                      'take the position off you.'),
            'largest_priced_usd': worst['usd'] if worst else None,
            'largest_priced_cost_pct': worst['cost_pct'] if worst else None,
            'ladder': ladder,
            'method': 'each rung is a live route quote for that dollar size; cost is '
                      '1 − received/nominal, so it includes fees and spread, not just '
                      'the impact the router reports. executable_usd is then found by '
                      'bisecting between the two rungs either side of the limit and '
                      'quoting again — a measured number, not a curve fit. With '
                      'refine=false it is interpolated between the rungs instead.',
        }

    @staticmethod
    def _bisect(quote_at, priced, limit, seed, rounds=4):
        """Quote the answer instead of inferring it.

        Interpolation between $100k and $1M is a guess across an order of
        magnitude, and depth curves are cliffs rather than slopes — the number
        that matters is exactly where the cliff is. Four more quotes, bisecting
        in log space between the last size that cleared and the first that did
        not, put it within a few percent. Every one of those quotes is a real
        route, so the extra rungs are kept in the ladder rather than thrown
        away: they are the evidence for the headline.
        """
        under = [s for s in priced if s['cost_pct'] <= limit]
        over = [s for s in priced if s['cost_pct'] > limit]
        if not under:
            return 0.0, [], ('even the smallest size priced costs more than the '
                             'limit — there is no size that clears')
        if not over:
            return seed, [], ('deeper than the ladder reached — every size priced '
                              'cleared, so this is a floor')
        lo, hi = float(under[-1]['usd']), float(over[0]['usd'])
        best, extra = lo, []
        for _ in range(max(0, int(rounds))):
            if hi / max(lo, 1e-9) < 1.05:
                break
            mid = math.exp((math.log(lo) + math.log(hi)) / 2)
            step = quote_at(mid)
            extra.append(step)
            if step.get('cost_pct') is None or step['cost_pct'] > limit:
                hi = mid
            else:
                lo = best = mid
        return round(best, 2), extra, 'measured by bisection'

    @staticmethod
    def _interpolate(ladder, limit):
        """The largest size under the cost limit, between two measured rungs.

        Log-log because both size and cost span orders of magnitude, and a
        linear read between $10k and $1M would be wrong by most of the range.
        """
        under = [s for s in ladder if s['cost_pct'] <= limit]
        over = [s for s in ladder if s['cost_pct'] > limit]
        if not under:
            return 0.0
        if not over:
            return float(under[-1]['usd'])       # deeper than we measured
        lo, hi = under[-1], over[0]
        if lo['usd'] >= hi['usd']:
            return float(lo['usd'])
        c_lo, c_hi = max(lo['cost_pct'], 1e-6), max(hi['cost_pct'], 1e-6)
        if c_hi <= c_lo:
            return float(lo['usd'])
        frac = (math.log(limit) - math.log(c_lo)) / (math.log(c_hi) - math.log(c_lo))
        frac = min(max(frac, 0.0), 1.0)
        size = math.exp(math.log(lo['usd']) + frac * (math.log(hi['usd']) - math.log(lo['usd'])))
        return round(size, 2)

    # ── the chain's own liquidity ────────────────────────────────

    def venues(self, tokens=10, pages=1):
        """Where Solana's liquidity actually sits, by DEX.

        Built from pools rather than from any venue's self-reported TVL, which
        is marketing. There is a real limitation here and it is stated in the
        answer rather than hidden: no index sorts pools by reserves, so the
        deep end has to be reached through the tokens instead — the N deepest
        tokens are looked up, every pool holding them is fetched, and the
        result says what fraction of the chain's quotable liquidity that sample
        covers. The busiest pools by volume come along separately, because
        "where the money sits" and "where it moves" are different questions and
        conflating them is how pump.fun ends up looking like the deepest venue
        on Solana.
        """
        tokens = max(1, min(int(tokens or 10), 25))
        top = self.universe(sort='liquidity', limit=tokens)
        chain_liquidity = (top.get('totals') or {}).get('liquidity_usd')
        sampled, covered = [], 0.0
        for t in top.get('tokens') or []:
            pools, _, _ = self.merge_pools(t["mint"], limit=20)
            covered += t.get('liquidity_usd') or 0
            for p in pools:
                if not p.get('suspect'):
                    sampled.append({**p, 'token': t.get('symbol')})

        deduped = {p['address']: p for p in sampled}
        deep = self._screen(list(deduped.values()), related=False)
        deep = [p for p in deep if not p.get('suspect')]

        busy, names = [], {}
        for src, got in self._ask('top_pools', pages=pages):
            busy += got or []
            if hasattr(src, 'venues'):
                try:
                    names.update(src.venues() or {})
                except Exception:
                    pass
        busy = [p for p in self._screen(busy, related=False) if not p.get('suspect')]

        agg = self.by_venue(deep)
        for v in agg:
            v['name'] = names.get(v['dex']) or v['dex']
        return self._out({
            'network': self.network,
            'sample': {
                'tokens': tokens, 'pools': len(deep),
                'liquidity_usd': round(sum(p.get('liquidity_usd') or 0 for p in deep), 2),
                'covers_pct': _pct(covered, chain_liquidity),
                'of_chain_liquidity_usd': chain_liquidity,
                'means': f'the {tokens} deepest tokens on Solana and every pool '
                         'holding them — the venues below are where that depth is '
                         'custodied, not a chain-wide TVL table.',
            },
            'venues': agg,
            'deepest_pools': sorted(deep, key=lambda p: -(p.get('liquidity_usd') or 0))[:25],
            'busiest_pools': sorted(busy, key=lambda p: -(p.get('volume_24h_usd') or 0))[:25],
            'busiest_note': 'sorted by 24h volume across the whole chain. Most of '
                            'these hold almost no reserves — bonding-curve launches '
                            'trade enormous size against a pool that is barely there.',
            'measures': 'reserves',
        })

    def pools_for(self, mint, limit=50):
        """Just the pools, for a caller that only wants the breakdown."""
        mint = self.resolve(mint)
        pools, seen, truncated = self.merge_pools(mint, limit)
        real = [p for p in pools if not p.get('suspect')]
        venues = self.by_venue(real)
        if truncated:
            self._warn('a source truncated its pool list — this is a floor, not a sum')
        return self._out({
            'network': self.network, 'mint': mint,
            'pools': pools, 'count': len(pools),
            'sources': seen,
            'sources_unavailable': [s.name for s in self.sources
                                    if s.name not in seen
                                    and s.measures == 'reserves'] or None,
            'liquidity_usd': round(sum(p.get('liquidity_usd') or 0 for p in real), 2),
            'venues': venues,
            'concentration': self.concentration(real, venues),
        })

    def resolve(self, query):
        """A symbol or a mint in, a mint out — the same rule the rest of the
        module uses, so a symbol never means two things in two places."""
        query = str(query or '').strip()
        if not query:
            raise SolError('give a mint address or a token symbol')
        if is_address(query):
            return query
        mint = self.client.resolve(query)
        if not mint:
            raise SolError(f'no token matched {query!r} — pass a mint address',
                           status=404)
        return mint
