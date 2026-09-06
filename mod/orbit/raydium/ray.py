#!/usr/bin/env python3
"""raydium — one AMM, read properly.

Raydium publishes a good API and almost nobody reads it well. The pool list is
paged, unsorted in the way you want, and every row carries eight nested token
objects; a quote comes back as raw base units with a route plan of program
addresses; and the thing an LP actually wants to know — "what is my position
worth right now" — is not in the API at all, because a concentrated position
lives on chain in an account nobody indexes for you.

So this module does three things the raw API does not:

  * it flattens a pool into a row a human can read, and lets you rank and
    filter the whole book by TVL, volume, fees or APR before it is paged;
  * it resolves symbols to mints, so `SOL` and `USDC` work everywhere an
    address does, and it prices quotes in UI units instead of lamports;
  * it reads concentrated positions off chain — derives the position PDA from
    the NFT in a wallet, decodes it, and works out the token amounts from the
    tick range and the pool's live price.

Everything here is the standard library plus two public HTTP APIs and a Solana
RPC endpoint. Nothing is signed here: `swap_transaction` returns unsigned bytes
for a signer that holds keys (`orbit/solana` does), which is the only shape in
which a DEX module should ever touch a wallet.
"""

import json
import hashlib
import math
import os
import struct
import threading
import time
import urllib.parse
import urllib.request

API = os.environ.get('RAYDIUM_API', 'https://api-v3.raydium.io').rstrip('/')
TX_API = os.environ.get('RAYDIUM_TX_API', 'https://transaction-v1.raydium.io').rstrip('/')
RPC = (os.environ.get('RAYDIUM_RPC') or os.environ.get('SOLANA_RPC')
       or 'https://api.mainnet-beta.solana.com')
RPC_FALLBACKS = [u.strip() for u in os.environ.get(
    'RAYDIUM_RPC_FALLBACKS',
    'https://api.mainnet-beta.solana.com,https://solana-rpc.publicnode.com'
).split(',') if u.strip()]
TIMEOUT = float(os.environ.get('RAYDIUM_TIMEOUT', 25))
CACHE_TTL = float(os.environ.get('RAYDIUM_CACHE_TTL', 30))
UA = 'raydium-mod/0.1 (+https://github.com/mod-protocol)'

CLMM_PROGRAM = 'CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK'
AMM_V4 = '675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8'
CPMM_PROGRAM = 'CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C'
TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
TOKEN_2022 = 'TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb'

PROGRAM_NAMES = {
    CLMM_PROGRAM: 'CLMM (concentrated)',
    AMM_V4: 'AMM v4 (standard)',
    CPMM_PROGRAM: 'CPMM (standard, token-2022)',
    'EhhTKczWMGQt46ynNeRX1WfeagwwJd7ufHvCDjRxjo5Q': 'staking',
    'FarmqiPv5eAj3j1GMdMCMUGXqPUvmquZtMy86QH6rzhG': 'farm v6',
    '9KEPoZmtHUrBbhWN1v1KWLMkkvwY6WLtAVUCPRtRjP4z': 'farm v5',
}

# The handful of mints worth knowing without a lookup — everything else is
# resolved from Raydium's own verified mint list.
KNOWN = {
    'SOL': 'So11111111111111111111111111111111111111112',
    'WSOL': 'So11111111111111111111111111111111111111112',
    'USDC': 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v',
    'USDT': 'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB',
    'RAY': '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R',
}
WSOL = KNOWN['SOL']

POOL_TYPES = {'all': 'all', 'concentrated': 'concentrated', 'clmm': 'concentrated',
              'standard': 'standard', 'amm': 'standard', 'cpmm': 'standard',
              'allfarm': 'allFarm', 'farm': 'allFarm',
              'concentratedfarm': 'concentratedFarm', 'standardfarm': 'standardFarm'}
SORT_FIELDS = {'default', 'liquidity', 'volume24h', 'fee24h', 'apr24h',
               'volume7d', 'fee7d', 'apr7d', 'volume30d', 'fee30d', 'apr30d'}
SORT_ALIASES = {'tvl': 'liquidity', 'volume': 'volume24h', 'vol': 'volume24h',
                'fees': 'fee24h', 'apr': 'apr24h', 'apy': 'apr24h'}


class RayError(Exception):
    """A failure worth showing the caller verbatim."""

    def __init__(self, message, status=400, detail=None):
        super().__init__(message)
        self.status, self.detail = status, detail

    def dict(self):
        out = {'error': str(self)}
        if self.detail is not None:
            out['detail'] = self.detail
        return out


# ── http, with a small cache ─────────────────────────────────────

_CACHE = {}
_LOCK = threading.Lock()


def _cached(key, ttl, make):
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = make()
    with _LOCK:
        _CACHE[key] = (now, value)
    return value


def _fetch(url, body=None, headers=None, tries=2):
    data = json.dumps(body).encode() if body is not None else None
    head = {'user-agent': UA, 'accept': 'application/json', **(headers or {})}
    if data:
        head['content-type'] = 'application/json'
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=head)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            detail = (e.read() or b'')[:400].decode('utf8', 'replace')
            host = urllib.parse.urlparse(url).netloc
            # 429 is the public Solana RPC saying "not from you, not this
            # often". Backing off once is worth it; saying so is worth more,
            # because the fix is an endpoint of your own, not a retry loop.
            if e.code in (429, 502, 503, 504) and attempt + 1 < tries:
                last = e
                time.sleep(1.0 + attempt)
                continue
            if e.code == 429:
                raise RayError(
                    f'{host} is rate-limiting this box — set RAYDIUM_RPC to an '
                    f'endpoint of your own (the public Solana RPC allows very '
                    f'few calls a second, and a wallet scan makes several)',
                    status=429, detail=detail)
            raise RayError(f'{host} returned HTTP {e.code}', status=502,
                           detail=detail)
        except Exception as e:                    # timeouts and DNS, worth a retry
            last = e
            if attempt + 1 < tries:
                time.sleep(0.4)
    raise RayError(f'could not reach {urllib.parse.urlparse(url).netloc}: {last}',
                   status=504)


def _unwrap(payload, what):
    """Raydium wraps everything as {id, success, data}. Unwrap or explain."""
    if not isinstance(payload, dict):
        raise RayError(f'{what}: unexpected response from the Raydium API',
                       status=502)
    if payload.get('success') is False or 'data' not in payload:
        raise RayError(f'{what}: Raydium said no — '
                       f'{payload.get("msg") or payload.get("message") or payload}',
                       status=502)
    return payload['data']


def get(path, ttl=None, **params):
    """GET one Raydium v3 path, cached, unwrapped."""
    clean = {k: v for k, v in params.items() if v not in (None, '')}
    url = API + path + ('?' + urllib.parse.urlencode(clean) if clean else '')
    ttl = CACHE_TTL if ttl is None else ttl
    if ttl <= 0:
        return _unwrap(_fetch(url), path)
    return _cached(url, ttl, lambda: _unwrap(_fetch(url), path))


def rpc(method, params=None, url=None):
    """One Solana JSON-RPC call — the parts of Raydium that only exist on chain.

    A wallet scan is several calls in a row and the free endpoints throttle at
    about that rate, so a 429 falls through to the next endpoint rather than
    failing the whole read. Set RAYDIUM_RPC and none of this matters.
    """
    endpoints = [url] if url else [RPC] + [u for u in RPC_FALLBACKS if u != RPC]
    failures = []
    for endpoint in endpoints:
        try:
            out = _fetch(endpoint, {'jsonrpc': '2.0', 'id': 1, 'method': method,
                                    'params': params or []})
        except RayError as e:
            if e.status not in (429, 502, 504):
                raise
            failures.append((endpoint, e))
            continue
        if isinstance(out, dict) and out.get('error'):
            err = out['error']
            raise RayError(f'RPC {method} failed: {err.get("message") or err}',
                           status=502, detail=err)
        return (out or {}).get('result')
    first = failures[0][1]
    raise RayError(f'no Solana RPC would answer {method}: ' + '; '.join(
        f'{urllib.parse.urlparse(u).netloc} — {e}' for u, e in failures),
        status=first.status, detail=first.detail)


# ── base58 and the position PDA ──────────────────────────────────

B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
B58_INDEX = {c: i for i, c in enumerate(B58)}
_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)
PDA_MARKER = b'ProgramDerivedAddress'


def b58encode(raw):
    n = int.from_bytes(raw, 'big')
    out = ''
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + (out or '')


def b58decode(text):
    if not isinstance(text, str) or not text:
        raise RayError(f'expected a base58 address, got {text!r}')
    n = 0
    for ch in text:
        if ch not in B58_INDEX:
            raise RayError(f'{ch!r} is not base58 — {text[:12]}… is not an address')
        n = n * 58 + B58_INDEX[ch]
    pad = len(text) - len(text.lstrip('1'))
    body = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    return b'\0' * pad + body


def is_address(text):
    try:
        return len(b58decode(text)) == 32
    except Exception:
        return False


def need_address(text, what='address'):
    if not isinstance(text, str) or not is_address(text.strip()):
        raise RayError(f'{what} must be a base58 32-byte Solana address, got {text!r}')
    return text.strip()


def _recover_x(y, sign):
    if y >= _P:
        return None
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    if (x * x - xx) % _P:
        return None
    if x == 0 and sign:
        return None
    return _P - x if x & 1 != sign else x


def _on_curve(raw32):
    """True if the bytes are a real ed25519 point — i.e. a key could exist for
    them. A PDA is chosen precisely because it is not one."""
    if len(raw32) != 32:
        return False
    y = int.from_bytes(raw32, 'little') & (2 ** 255 - 1)
    return _recover_x(y, raw32[31] >> 7) is not None


def find_program_address(seeds, program_id):
    prog = b58decode(program_id)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b''.join(seeds) + bytes([bump]) + prog + PDA_MARKER).digest()
        if not _on_curve(h):
            return b58encode(h), bump
    raise RayError('no bump seed produced an off-curve address', status=500)


def position_pda(nft_mint):
    """Where a concentrated position lives: ["position", nft mint] under CLMM."""
    return find_program_address([b'position', b58decode(need_address(nft_mint))],
                                CLMM_PROGRAM)[0]


# ── tokens: symbols in, mints out ────────────────────────────────

def mint_list(ttl=3600):
    """Raydium's verified mint list — the set of tokens it will show by name."""
    return get('/mint/list', ttl=ttl)


def _symbol_index():
    def build():
        idx = {}
        for m in (mint_list() or {}).get('mintList') or []:
            sym = (m.get('symbol') or '').upper()
            if sym:
                idx.setdefault(sym, []).append(m)
        return idx
    return _cached('symbol-index', 3600, build)


def token_search(query, limit=20):
    """Symbols beyond Raydium's 222-token verified list.

    Raydium only publishes the mints it vouches for, which is the right list to
    trust and the wrong list to search — the pool book is mostly tokens that are
    not on it. Jupiter's public token index fills the gap, and every answer that
    came from there says so, because a symbol match off an open index is a guess
    until you look at the liquidity behind it.
    """
    if os.environ.get('RAYDIUM_TOKEN_SEARCH') == 'off':
        return []
    url = (os.environ.get('RAYDIUM_TOKEN_INDEX', 'https://lite-api.jup.ag')
           .rstrip('/') + '/tokens/v2/search?'
           + urllib.parse.urlencode({'query': str(query)[:64]}))
    try:
        rows = _cached(url, 600, lambda: _fetch(url))
    except RayError:
        return []                      # a missing index is not a failed lookup
    if not isinstance(rows, list):
        return []
    return [{'symbol': r.get('symbol'), 'name': r.get('name'),
             'mint': r.get('id'), 'decimals': r.get('decimals'),
             'liquidity_usd': _f(r.get('liquidity')), 'fdv_usd': _f(r.get('fdv')),
             'holders': r.get('holderCount'), 'source': 'jupiter'}
            for r in rows[:limit] if r.get('id')]


def resolve(token, what='token'):
    """A mint address, or a symbol, in — a mint out.

    Symbols are ambiguous on Solana and always will be, so the order is: known
    majors, then Raydium's verified list, then the open token index — and when
    a symbol is claimed by more than one liquid mint this says so rather than
    picking a memecoin for you.
    """
    if not isinstance(token, str) or not token.strip():
        raise RayError(f'{what} is required — a mint address or a symbol like SOL')
    t = token.strip()
    if is_address(t):
        return t
    up = t.upper()
    if up in KNOWN:
        return KNOWN[up]
    hits = _symbol_index().get(up) or []
    if len(hits) == 1:
        return hits[0]['address']
    if len(hits) > 1:
        raise RayError(
            f'{up} is the symbol of {len(hits)} verified mints — pass the address',
            detail=[{'symbol': h.get('symbol'), 'name': h.get('name'),
                     'mint': h.get('address')} for h in hits[:8]])
    exact = [r for r in token_search(t)
             if (r.get('symbol') or '').upper() == up
             and (r.get('liquidity_usd') or 0) >= 50_000]
    if len(exact) == 1:
        return exact[0]['mint']
    if len(exact) > 1:
        raise RayError(
            f'{up} is the symbol of {len(exact)} mints with real liquidity — '
            f'pass the one you mean',
            detail=exact[:8])
    raise RayError(f'{t!r} is not an address, not on Raydium\'s verified list, '
                   f'and has no liquid mint by that symbol — try ray_search')


def mint_info(mints):
    """Decimals, symbol and token program for any mint, listed or not."""
    ids = [resolve(m) for m in _as_list(mints)]
    if not ids:
        return {}
    out = {}
    for chunk in _chunks(ids, 50):
        rows = get('/mint/ids', ttl=600, mints=','.join(chunk)) or []
        for mint, row in zip(chunk, rows):
            out[mint] = row or {'address': mint, 'symbol': None, 'decimals': None}
    return out


def prices(mints):
    """USD prices keyed by mint. Raydium prices from its own pools."""
    ids = [resolve(m) for m in _as_list(mints)]
    if not ids:
        return {}
    out = {}
    for chunk in _chunks(ids, 50):
        got = get('/mint/price', ttl=15, mints=','.join(chunk)) or {}
        for mint in chunk:
            v = got.get(mint)
            out[mint] = float(v) if v not in (None, '') else None
    return out


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(',') if v.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _chunks(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── shaping: a pool row a human can read ─────────────────────────

def _tok(m):
    if not isinstance(m, dict):
        return {'mint': m}
    return {'symbol': m.get('symbol'), 'name': m.get('name'),
            'mint': m.get('address'), 'decimals': m.get('decimals'),
            'program': m.get('programId'), 'tags': m.get('tags') or []}


def _window(w):
    if not isinstance(w, dict):
        return None
    return {'volume': _f(w.get('volume')), 'fees': _f(w.get('volumeFee')),
            'apr': _f(w.get('apr')), 'fee_apr': _f(w.get('feeApr')),
            'reward_apr': [x for x in (w.get('rewardApr') or []) if x],
            'price_min': _f(w.get('priceMin')), 'price_max': _f(w.get('priceMax'))}


def slim_pool(p, full=False):
    """One pool, flattened. The nested token objects become symbols; the three
    stat windows become day/week/month; everything else is dropped unless you
    ask for it."""
    if not isinstance(p, dict):
        return p
    a, b = _tok(p.get('mintA')), _tok(p.get('mintB'))
    day = _window(p.get('day')) or {}
    row = {
        'id': p.get('id'),
        'pair': f'{a.get("symbol") or "?"}/{b.get("symbol") or "?"}',
        'type': p.get('type'),
        'program': p.get('programId'),
        'program_name': PROGRAM_NAMES.get(p.get('programId')),
        'price': _f(p.get('price')),
        'tvl': _f(p.get('tvl')),
        'fee_rate': _f(p.get('feeRate')),
        'volume_24h': day.get('volume'),
        'fees_24h': day.get('fees'),
        'apr_24h': day.get('apr'),
        'mint_a': a, 'mint_b': b,
        'reserve_a': _f(p.get('mintAmountA')), 'reserve_b': _f(p.get('mintAmountB')),
        'farms': {'ongoing': p.get('farmOngoingCount'),
                  'upcoming': p.get('farmUpcomingCount'),
                  'finished': p.get('farmFinishedCount')},
        'open_time': _time(p.get('openTime')),
    }
    lp = p.get('lpMint')
    if isinstance(lp, dict) and lp.get('address'):
        row['lp'] = {'mint': lp.get('address'), 'decimals': lp.get('decimals'),
                     'price': _f(p.get('lpPrice')), 'supply': _f(p.get('lpAmount')),
                     'burned_pct': _f(p.get('burnPercent'))}
    cfg = p.get('config')
    if isinstance(cfg, dict):
        row['tick_spacing'] = cfg.get('tickSpacing')
        row['config'] = cfg.get('id')
    if full:
        row['day'] = _window(p.get('day'))
        row['week'] = _window(p.get('week'))
        row['month'] = _window(p.get('month'))
        row['market_id'] = p.get('marketId')
        row['rewards'] = [{'symbol': (r.get('mint') or {}).get('symbol'),
                           'mint': (r.get('mint') or {}).get('address'),
                           'per_second': r.get('perSecond'),
                           'start': _time(r.get('startTime')),
                           'end': _time(r.get('endTime'))}
                          for r in (p.get('rewardDefaultInfos') or [])]
        row['burn_percent'] = _f(p.get('burnPercent'))
        row['url'] = f'https://raydium.io/liquidity-pools/?tab=all&search={p.get("id")}'
    return row


def _time(seconds):
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return None
    if s <= 0:
        return None
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(s))


# ── the book ─────────────────────────────────────────────────────

def overview():
    """The protocol in one object: size, throughput, price of its own token."""
    info = get('/main/info', ttl=60) or {}
    px = prices([KNOWN['RAY'], WSOL, KNOWN['USDC']])
    tvl, vol = _f(info.get('tvl')), _f(info.get('volume24'))
    return {
        'tvl_usd': tvl,
        'volume_24h_usd': vol,
        'turnover_24h': round(vol / tvl, 4) if tvl and vol else None,
        'ray_usd': px.get(KNOWN['RAY']),
        'sol_usd': px.get(WSOL),
        'api_version': (get('/main/version', ttl=600) or {}).get('latest'),
        'priority_fee_micro_lamports': (get('/main/auto-fee', ttl=60) or {}).get('default'),
        'chain_time_offset_s': (get('/main/chain-time', ttl=60) or {}).get('offset'),
        'programs': PROGRAM_NAMES,
        'as_of': _time(time.time()),
    }


def pools(type='all', sort='volume24h', order='desc', limit=20, page=1,
          min_tvl=None, min_volume=None, search=None, full=False):
    """The pool book, ranked. Sorting happens at Raydium, filtering happens here.

    `search` matches either symbol, so `search='RAY'` is every RAY pool in the
    ranked page — the API has no text filter of its own.
    """
    kind = POOL_TYPES.get(str(type).lower())
    if not kind:
        raise RayError(f'unknown pool type {type!r} — '
                       f'{", ".join(sorted(set(POOL_TYPES)))}')
    field = SORT_ALIASES.get(str(sort).lower(), str(sort))
    if field not in SORT_FIELDS:
        raise RayError(f'unknown sort {sort!r} — {", ".join(sorted(SORT_FIELDS))}')
    limit = max(1, min(int(limit or 20), 100))
    filtering = any(v not in (None, '') for v in (min_tvl, min_volume, search))
    # Filtering is client-side, so pull a full page when there is one to apply.
    page_size = 100 if filtering else limit
    raw = get('/pools/info/list', ttl=30, poolType=kind, poolSortField=field,
              sortType='desc' if str(order).lower() != 'asc' else 'asc',
              pageSize=page_size, page=max(1, int(page or 1)))
    rows = [slim_pool(p, full=full) for p in (raw or {}).get('data') or []]
    if min_tvl not in (None, ''):
        rows = [r for r in rows if (r.get('tvl') or 0) >= float(min_tvl)]
    if min_volume not in (None, ''):
        rows = [r for r in rows if (r.get('volume_24h') or 0) >= float(min_volume)]
    if search:
        q = str(search).upper()
        rows = [r for r in rows
                if q in (r.get('pair') or '').upper()
                or q in json.dumps([r.get('mint_a'), r.get('mint_b')]).upper()]
    # Raydium's `count` is how many rows came back, not how many exist — only
    # hasNextPage says whether there is more, so that is what gets reported.
    return {'sort': field, 'order': order, 'type': kind, 'page': int(page or 1),
            'scanned': len((raw or {}).get('data') or []),
            'has_more': bool((raw or {}).get('hasNextPage')),
            'count': len(rows[:limit]), 'pools': rows[:limit],
            'filtered': filtering or None,
            'note': ('ranking by liquidity puts pools whose reserves are a '
                     'worthless token at the top — rank by volume24h to see '
                     'where trading actually happens')
                    if field == 'liquidity' else None}


def pool(id, keys=False):
    """One pool by its address — or by its LP mint, which is what a wallet holds."""
    ident = need_address(id, 'pool id')
    rows = [p for p in (get('/pools/info/ids', ttl=20, ids=ident) or []) if p]
    if not rows:
        rows = [p for p in (get('/pools/info/lps', ttl=20, lps=ident) or []) if p]
        if rows:
            ident = rows[0].get('id')
    if not rows:
        raise RayError(f'{ident} is not a Raydium pool or LP mint', status=404)
    out = slim_pool(rows[0], full=True)
    if keys:
        out['keys'] = pool_keys(ident).get('keys')
    return out


def pool_keys(id):
    """The accounts you need to build an instruction against a pool yourself:
    vaults, authority, config, and the lookup table its transactions use."""
    ident = need_address(id, 'pool id')
    rows = [k for k in (get('/pools/key/ids', ttl=600, ids=ident) or []) if k]
    if not rows:
        raise RayError(f'no pool keys for {ident} — is it a pool address?',
                       status=404)
    k = rows[0]
    vault = k.get('vault') or {}
    out = {'id': k.get('id'), 'program': k.get('programId'),
           'program_name': PROGRAM_NAMES.get(k.get('programId')),
           'mint_a': _tok(k.get('mintA')), 'mint_b': _tok(k.get('mintB')),
           'vault_a': vault.get('A'), 'vault_b': vault.get('B'),
           'authority': k.get('authority'), 'config': k.get('config'),
           'observation_id': k.get('observationId'),
           'ex_bitmap_account': k.get('exBitmapAccount'),
           'open_orders': k.get('openOrders'), 'target_orders': k.get('targetOrders'),
           'market_program': k.get('marketProgramId'), 'market_id': k.get('marketId'),
           'lookup_table': k.get('lookupTableAccount'),
           'mint_lp': (k.get('mintLp') or {}).get('address')}
    return {'id': ident, 'keys': {k2: v for k2, v in out.items() if v not in (None, '')},
            'raw_fields': sorted(k)}


def pair(a, b=None, sort='liquidity', limit=10, type='all', full=False):
    """Every pool that trades a token — or a pair — with the aggregate depth.

    A token trades in a dozen pools on Raydium and they do not agree on price.
    This ranks them and adds up what is actually behind the quote.
    """
    mint1 = resolve(a, 'first token')
    mint2 = resolve(b, 'second token') if b else None
    kind = POOL_TYPES.get(str(type).lower(), 'all')
    field = SORT_ALIASES.get(str(sort).lower(), str(sort))
    if field not in SORT_FIELDS:
        raise RayError(f'unknown sort {sort!r} — {", ".join(sorted(SORT_FIELDS))}')
    raw = get('/pools/info/mint', ttl=30, mint1=mint1, mint2=mint2, poolType=kind,
              poolSortField=field, sortType='desc', pageSize=100, page=1)
    rows = [slim_pool(p, full=full) for p in (raw or {}).get('data') or []]
    tvl = sum(r.get('tvl') or 0 for r in rows)
    vol = sum(r.get('volume_24h') or 0 for r in rows)
    top = max(rows, key=lambda r: r.get('tvl') or 0, default=None)
    deepest = top
    busiest = max(rows, key=lambda r: r.get('volume_24h') or 0, default=None)
    names = mint_info([m for m in (mint1, mint2) if m])
    return {
        'tokens': [{'mint': m, 'symbol': (names.get(m) or {}).get('symbol')}
                   for m in (mint1, mint2) if m],
        'pool_count': len(rows), 'more_pools': bool((raw or {}).get('hasNextPage')),
        'tvl_usd': round(tvl, 2), 'volume_24h_usd': round(vol, 2),
        'deepest': {'id': deepest['id'], 'pair': deepest['pair'],
                    'tvl': deepest['tvl'], 'price': deepest['price'],
                    'type': deepest['type']} if deepest else None,
        'busiest': {'id': busiest['id'], 'pair': busiest['pair'],
                    'volume_24h': busiest['volume_24h'],
                    'price': busiest['price']} if busiest else None,
        'price_spread_pct': _spread(rows, top),
        'pools': rows[:max(1, min(int(limit or 10), 100))],
    }


def _spread(rows, deepest):
    """How far apart the real pools price the same pair — a wide spread is where
    the arbitrage is, and where a naive quote goes wrong.

    Two traps make the naive version useless. Half the pools quote the pair the
    other way round (B per A, not A per B), and most of the rest are dust whose
    last trade was months ago. So this compares only pools deep enough to move
    the price, after flipping the ones quoted backwards.
    """
    if not deepest or not deepest.get('price'):
        return None
    base, quote = deepest['mint_a']['mint'], deepest['mint_b']['mint']
    floor = max(10_000.0, (deepest.get('tvl') or 0) * 0.01)
    vals = []
    for r in rows:
        price, tvl = r.get('price'), r.get('tvl') or 0
        if not price or tvl < floor:
            continue
        a, b = r['mint_a']['mint'], r['mint_b']['mint']
        if (a, b) == (base, quote):
            vals.append(price)
        elif (a, b) == (quote, base):
            vals.append(1 / price)
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals)
    return round((hi - lo) / lo * 100, 3) if lo else None


def token(mint, pools_limit=5):
    """A token as Raydium sees it: price, whether it is verified, and where it
    actually trades."""
    ident = resolve(mint)
    info = (mint_info([ident]) or {}).get(ident) or {}
    px = prices([ident]).get(ident)
    depth = pair(ident, limit=pools_limit)
    verified = bool(_symbol_index().get((info.get('symbol') or '').upper()) and
                    any(m.get('address') == ident
                        for m in _symbol_index().get((info.get('symbol') or '').upper(), [])))
    blacklisted = ident in ((mint_list() or {}).get('blacklist') or [])
    return {
        'mint': ident, 'symbol': info.get('symbol'), 'name': info.get('name'),
        'decimals': info.get('decimals'), 'token_program': info.get('programId'),
        'tags': info.get('tags') or [], 'price_usd': px,
        'verified_on_raydium': verified, 'blacklisted': blacklisted or None,
        'pool_count': depth.get('pool_count'), 'more_pools': depth.get('more_pools'),
        'tvl_usd': depth.get('tvl_usd'),
        'volume_24h_usd': depth.get('volume_24h_usd'),
        'price_spread_pct': depth.get('price_spread_pct'),
        'deepest_pool': depth.get('deepest'), 'pools': depth.get('pools'),
    }


def search(q, limit=10):
    """Find a token by symbol or name, and the pools it trades in."""
    if not str(q or '').strip():
        raise RayError('search needs a query — a symbol, a name, or a mint')
    query = str(q).strip()
    if is_address(query):
        return {'query': query, 'tokens': [], 'resolved': token(query)}
    up = query.upper()
    hits = []
    for m in (mint_list() or {}).get('mintList') or []:
        sym, name = (m.get('symbol') or ''), (m.get('name') or '')
        if up == sym.upper():
            score = 0
        elif sym.upper().startswith(up) or name.upper().startswith(up):
            score = 1
        elif up in sym.upper() or up in name.upper():
            score = 2
        else:
            continue
        hits.append((score, {'symbol': sym, 'name': name, 'mint': m.get('address'),
                             'decimals': m.get('decimals'),
                             'tags': m.get('tags') or []}))
    hits.sort(key=lambda h: h[0])
    limit = max(1, min(int(limit or 10), 50))
    rows = [dict(h[1], source='raydium-verified') for h in hits[:limit]]
    seen = {r['mint'] for r in rows}
    for r in token_search(query, limit=limit * 2):
        if len(rows) >= limit:
            break
        if r['mint'] not in seen:
            rows.append(r)
            seen.add(r['mint'])
    if rows:
        px = prices([r['mint'] for r in rows])
        for r in rows:
            r['price_usd'] = px.get(r['mint'])
    return {'query': query, 'count': len(rows), 'tokens': rows,
            'note': 'raydium-verified rows are the mints Raydium vouches for; '
                    'jupiter rows are an open index — check the liquidity before '
                    'you trade one'}


def mints(search=None, limit=50, page=1):
    """Raydium's verified mint list, paged."""
    data = mint_list() or {}
    rows = data.get('mintList') or []
    if search:
        up = str(search).upper()
        rows = [m for m in rows if up in (m.get('symbol') or '').upper()
                or up in (m.get('name') or '').upper()]
    limit = max(1, min(int(limit or 50), 500))
    page = max(1, int(page or 1))
    window = rows[(page - 1) * limit: page * limit]
    return {'count': len(rows), 'page': page, 'limit': limit,
            'blacklisted': len(data.get('blacklist') or []),
            'mints': [{'symbol': m.get('symbol'), 'name': m.get('name'),
                       'mint': m.get('address'), 'decimals': m.get('decimals'),
                       'program': m.get('programId'), 'tags': m.get('tags') or []}
                      for m in window]}


def farms(pool=None, ids=None, limit=20):
    """Emission farms — by pool (via its LP mint) or by farm address."""
    if ids:
        rows = get('/farms/info/ids', ttl=60, ids=','.join(_as_list(ids))) or []
        rows = [r for r in rows if r]
        total = len(rows)
    elif pool:
        ident = need_address(pool, 'pool')
        info = [p for p in (get('/pools/info/ids', ttl=60, ids=ident) or []) if p]
        lp = ((info[0].get('lpMint') if info else None) or {}).get('address')
        if not lp:
            raise RayError(f'{ident} has no LP mint — concentrated pools carry '
                           f'their rewards inline, see ray_pool', status=404)
        raw = get('/farms/info/lp', ttl=60, lp=lp, pageSize=100, page=1) or {}
        rows, total = raw.get('data') or [], raw.get('count')
    else:
        raise RayError('farms needs pool= (a pool address) or ids= (farm addresses)')
    return {'count': total, 'farms': [_slim_farm(f) for f in
                                      rows[:max(1, min(int(limit or 20), 100))]]}


def _slim_farm(f):
    if not isinstance(f, dict):
        return f
    return {
        'id': f.get('id'), 'program': f.get('programId'),
        'program_name': PROGRAM_NAMES.get(f.get('programId')),
        'lp_mint': (f.get('lpMint') or {}).get('address'),
        'symbols': [(m or {}).get('symbol') for m in f.get('symbolMints') or []],
        'tvl_usd': _f(f.get('tvl')), 'apr': _f(f.get('apr')),
        'lp_price': _f(f.get('lpPrice')),
        'rewards': [{'symbol': ((r.get('mint') or {}).get('symbol')),
                     'mint': ((r.get('mint') or {}).get('address')),
                     'per_week': _f(r.get('perWeek')), 'apr': _f(r.get('apr')),
                     'start': _time(r.get('openTime')), 'end': _time(r.get('endTime'))}
                    for r in f.get('rewardInfos') or []],
        'upcoming': f.get('upcoming'),
    }


def stake_pools():
    """Single-sided RAY staking."""
    raw = get('/main/stake-pools', ttl=300) or {}
    return {'count': raw.get('count'),
            'pools': [_slim_farm(f) for f in raw.get('data') or []]}


def raw_api(path, **params):
    """Any Raydium v3 path, unwrapped — the escape hatch for what is not wrapped."""
    if not str(path or '').startswith('/'):
        raise RayError('path must start with / — e.g. /main/info')
    return {'path': path, 'params': params, 'data': get(path, ttl=0, **params)}


# ── quotes ───────────────────────────────────────────────────────

def _decimals(mints):
    info = mint_info(mints)
    out = {}
    for mint, row in info.items():
        d = (row or {}).get('decimals')
        if d is None:
            raise RayError(f'no decimals published for {mint} — it may not be a '
                           f'token mint', status=404)
        out[mint] = int(d)
    return out


def _ui(raw, decimals):
    try:
        return int(raw) / (10 ** int(decimals))
    except (TypeError, ValueError):
        return None


def quote(input, output, amount, slippage_bps=50, mode='in', tx_version='V0',
          raw=False, include_response=False):
    """What a swap would actually get you, priced by Raydium's own router.

    `amount` is in whole tokens, not base units — of the input for mode=in
    (default), of the output for mode=out. Everything comes back in both.
    """
    in_mint, out_mint = resolve(input, 'input'), resolve(output, 'output')
    if in_mint == out_mint:
        raise RayError('input and output are the same mint')
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise RayError(f'amount must be a number, got {amount!r}')
    if amount <= 0:
        raise RayError('amount must be greater than zero')
    dec = _decimals([in_mint, out_mint])
    base_out = str(mode).lower() in ('out', 'baseout', 'swap-base-out')
    side_mint = out_mint if base_out else in_mint
    base_units = int(round(amount * 10 ** dec[side_mint]))
    if base_units <= 0:
        raise RayError(f'{amount} is below one base unit of {side_mint}')
    url = (f'{TX_API}/compute/swap-base-{"out" if base_out else "in"}?'
           + urllib.parse.urlencode({
               'inputMint': in_mint, 'outputMint': out_mint,
               'amount': base_units, 'slippageBps': int(slippage_bps or 50),
               'txVersion': tx_version}))
    data = _unwrap(_fetch(url), 'quote')
    if raw:
        return data
    in_ui = _ui(data.get('inputAmount'), dec[in_mint])
    out_ui = _ui(data.get('outputAmount'), dec[out_mint])
    px = prices([in_mint, out_mint])
    names = mint_info([in_mint, out_mint])
    threshold = _ui(data.get('otherAmountThreshold'),
                    dec[in_mint] if base_out else dec[out_mint])
    usd_in = (px.get(in_mint) or 0) * (in_ui or 0) or None
    usd_out = (px.get(out_mint) or 0) * (out_ui or 0) or None
    shaped = {
        'mode': 'base-out' if base_out else 'base-in',
        'input': {'mint': in_mint, 'symbol': (names.get(in_mint) or {}).get('symbol'),
                  'amount': in_ui, 'base_units': data.get('inputAmount'),
                  'usd': round(usd_in, 4) if usd_in else None},
        'output': {'mint': out_mint, 'symbol': (names.get(out_mint) or {}).get('symbol'),
                   'amount': out_ui, 'base_units': data.get('outputAmount'),
                   'usd': round(usd_out, 4) if usd_out else None},
        'price': round(out_ui / in_ui, 10) if in_ui and out_ui else None,
        'price_inverse': round(in_ui / out_ui, 10) if in_ui and out_ui else None,
        'price_impact_pct': _f(data.get('priceImpactPct')),
        'slippage_bps': data.get('slippageBps'),
        'worst_case': {'label': 'max you would spend' if base_out
                       else 'min you would receive', 'amount': threshold},
        # What the trade costs against spot, which is the number that decides
        # whether to split it — impact alone hides the fee.
        'vs_spot_pct': (round((usd_out - usd_in) / usd_in * 100, 4)
                        if usd_in and usd_out else None),
        'route': _route(data.get('routePlan') or []),
        'hops': len(data.get('routePlan') or []),
        'next': 'ray_swap_tx builds this same quote into an unsigned transaction',
    }
    if include_response:
        # The router's own response object, verbatim: the transaction builder
        # will only accept the one it produced, not a reconstruction of it.
        shaped['swap_response'] = data
    return shaped


def _route(plan):
    mints = {m for hop in plan for m in (hop.get('inputMint'), hop.get('outputMint'))
             if m}
    names = mint_info(sorted(mints)) if mints else {}

    def sym(m):
        return (names.get(m) or {}).get('symbol') or (m[:4] + '…' if m else None)
    return [{'pool': hop.get('poolId'), 'from': sym(hop.get('inputMint')),
             'to': sym(hop.get('outputMint')),
             'fee_rate': _f(hop.get('feeRate')),
             'fee_mint': sym(hop.get('feeMint')),
             'fee_base_units': hop.get('feeAmount')} for hop in plan]


def swap_transaction(wallet, input, output, amount, slippage_bps=50, mode='in',
                     priority='h', tx_version='V0', wrap_sol=None, unwrap_sol=None):
    """Build the swap, do not sign it.

    Returns base64 transactions for the wallet to sign elsewhere — this module
    holds no keys and never will. `orbit/solana` signs, or any wallet does.
    """
    owner = need_address(wallet, 'wallet')
    q = quote(input, output, amount, slippage_bps=slippage_bps, mode=mode,
              tx_version=tx_version, include_response=True)
    in_mint, out_mint = q['input']['mint'], q['output']['mint']
    wrap = (in_mint == WSOL) if wrap_sol is None else bool(wrap_sol)
    unwrap = (out_mint == WSOL) if unwrap_sol is None else bool(unwrap_sol)
    fee = (get('/main/auto-fee', ttl=60) or {}).get('default') or {}
    body = {
        'computeUnitPriceMicroLamports': str(fee.get(str(priority), fee.get('h', 10000))),
        'swapResponse': q.pop('swap_response'),
        'txVersion': tx_version,
        'wallet': owner,
        'wrapSol': wrap,
        'unwrapSol': unwrap,
    }
    if not wrap:
        acct = token_account(owner, in_mint)
        if not acct:
            raise RayError(f'{owner} holds no {q["input"]["symbol"] or in_mint} '
                           f'token account to spend from', status=409)
        body['inputAccount'] = acct
    if not unwrap:
        acct = token_account(owner, out_mint)
        if acct:
            body['outputAccount'] = acct
    base_out = q['mode'] == 'base-out'
    out = _unwrap(_fetch(f'{TX_API}/transaction/swap-base-'
                         f'{"out" if base_out else "in"}', body=body), 'swap tx')
    txs = [t.get('transaction') for t in (out or []) if isinstance(t, dict)]
    return {
        'wallet': owner, 'unsigned': True, 'tx_version': tx_version,
        'transactions': txs, 'count': len(txs),
        'compute_unit_price_micro_lamports': body['computeUnitPriceMicroLamports'],
        'wrap_sol': wrap, 'unwrap_sol': unwrap,
        'quote': {k: q[k] for k in ('mode', 'input', 'output', 'price',
                                    'price_impact_pct', 'worst_case', 'route')},
        'how_to_sign': 'base64 serialised transactions, unsigned. Nothing here '
                       'holds a key: sign and send them with a wallet that does '
                       '— e.g. the solana module\'s keystore, or a browser wallet.',
        'expires': 'a quote is a snapshot — build and send in the same minute or '
                   'the route can move under you',
    }


def token_account(owner, mint):
    """The wallet's largest account for one mint — its ATA, usually."""
    accounts = []
    for program in (TOKEN_PROGRAM, TOKEN_2022):
        res = rpc('getTokenAccountsByOwner',
                  [owner, {'mint': mint, 'programId': program},
                   {'encoding': 'jsonParsed'}]) or {}
        accounts += res.get('value') or []
    best, best_amount = None, -1
    for a in accounts:
        info = (((a.get('account') or {}).get('data') or {}).get('parsed')
                or {}).get('info') or {}
        amount = int(((info.get('tokenAmount') or {}).get('amount')) or 0)
        if amount > best_amount:
            best, best_amount = a.get('pubkey'), amount
    return best


# ── depth: what is actually behind the price ─────────────────────

DEFAULT_BANDS = (0.005, 0.01, 0.02, 0.05, 0.1)


def _tick_sqrt(tick):
    return 1.0001 ** (tick / 2.0)


def depth(id, bands=None, points=48, span=0.25):
    """How much money sits within a few percent of the price.

    TVL is the wrong number for this and everybody quotes it anyway: in a
    concentrated pool most of the liquidity can be parked in a range the price
    has not visited since March. So this integrates the pool's published
    liquidity line into real token amounts on each side of the current price,
    band by band — and for a standard pool it does the constant-product maths
    instead. The full-range total is cross-checked against the reserves Raydium
    reports, and the ratio is returned, so you can see when the line is stale.
    """
    p = pool(id)
    bands = tuple(bands or DEFAULT_BANDS)
    decA = p['mint_a'].get('decimals')
    decB = p['mint_b'].get('decimals')
    price = p.get('price')
    if not price or decA is None or decB is None:
        raise RayError(f'{p["id"]} has no usable price — nothing to measure',
                       status=409)
    px = prices([p['mint_a']['mint'], p['mint_b']['mint']])
    usd_a, usd_b = px.get(p['mint_a']['mint']), px.get(p['mint_b']['mint'])
    out = {'id': p['id'], 'pair': p['pair'], 'type': p['type'], 'price': price,
           'tvl_usd': p.get('tvl'),
           'base': p['mint_a'].get('symbol'), 'quote': p['mint_b'].get('symbol')}

    if p.get('type') == 'Concentrated':
        line = sorted((get('/pools/line/position', ttl=60, id=p['id']) or {})
                      .get('line') or [], key=lambda r: r.get('tick', 0))
        if len(line) < 2:
            raise RayError(f'no liquidity line published for {p["id"]}', status=404)
        sqrt_now = math.sqrt(price * 10 ** (decB - decA))
        out['method'] = 'integrated from the published liquidity line'
        out['bands'] = [_clmm_band(line, sqrt_now, b, decA, decB, usd_a, usd_b)
                        for b in bands]
        whole = _clmm_band(line, sqrt_now, None, decA, decB, usd_a, usd_b)
        got_a, got_b = whole['ask_amount'], whole['bid_amount']
        out['full_range'] = {
            'base_amount': got_a, 'quote_amount': got_b,
            'reserves_reported': {'base': p.get('reserve_a'), 'quote': p.get('reserve_b')},
            'coverage_pct': round(100 * (got_a or 0) / p['reserve_a'], 1)
            if p.get('reserve_a') else None,
            'note': 'the line is sampled, so the integral runs a few percent '
                    'under the reserves — a big gap means it is stale'}
        out['curve'] = _curve(line, price, decA, decB, points, span)
    else:
        x, y = p.get('reserve_a'), p.get('reserve_b')
        if not x or not y:
            raise RayError(f'{p["id"]} reports no reserves', status=409)
        out['method'] = 'constant product (x·y=k) from the reserves'
        out['bands'] = [_amm_band(x, y, b, usd_a, usd_b) for b in bands]
        out['full_range'] = {'base_amount': x, 'quote_amount': y,
                             'reserves_reported': {'base': x, 'quote': y},
                             'coverage_pct': 100.0}
    for band in out['bands']:
        band['total_usd'] = round((band.get('bid_usd') or 0)
                                  + (band.get('ask_usd') or 0), 2) or None
    tight = next((b for b in out['bands'] if b['band_pct'] == 1.0), None)
    if tight and out.get('tvl_usd'):
        out['within_1pct_of_tvl'] = round(
            100 * (tight.get('total_usd') or 0) / out['tvl_usd'], 2)
    return out


def _clmm_band(line, sqrt_now, band, decA, decB, usd_a, usd_b):
    """Integrate L over the segment: token B below the price, token A above."""
    lo = sqrt_now * math.sqrt(1 - band) if band else 0.0
    hi = sqrt_now * math.sqrt(1 + band) if band else float('inf')
    amt_a = amt_b = 0.0
    for i in range(len(line) - 1):
        L = _f(line[i].get('liquidity')) or 0.0
        if L <= 0:
            continue
        s0, s1 = _tick_sqrt(line[i]['tick']), _tick_sqrt(line[i + 1]['tick'])
        # below the price the pool holds the quote token, above it the base
        b0, b1 = max(s0, lo), min(s1, sqrt_now)
        if b1 > b0:
            amt_b += L * (b1 - b0)
        a0, a1 = max(s0, sqrt_now), min(s1, hi)
        if a1 > a0:
            amt_a += L * (1 / a0 - 1 / a1)
    base = amt_a / 10 ** decA
    quote = amt_b / 10 ** decB
    return {'band_pct': round(band * 100, 3) if band else None,
            'ask_amount': round(base, 6), 'bid_amount': round(quote, 6),
            'ask_usd': round(base * usd_a, 2) if usd_a else None,
            'bid_usd': round(quote * usd_b, 2) if usd_b else None}


def _amm_band(x, y, band, usd_a, usd_b):
    """x·y=k: to move the price by f you take out x(1-1/√f) and put in y(√f-1)."""
    f = 1 + band
    quote_in = y * (math.sqrt(f) - 1)            # buying the base, price up
    g = 1 - band
    base_in = x * (1 / math.sqrt(g) - 1)         # selling the base, price down
    return {'band_pct': round(band * 100, 3),
            'ask_amount': round(x * (1 - 1 / math.sqrt(f)), 6),
            'bid_amount': round(quote_in, 6),
            'ask_usd': round(x * (1 - 1 / math.sqrt(f)) * usd_a, 2) if usd_a else None,
            'bid_usd': round(quote_in * usd_b, 2) if usd_b else None,
            'to_move_down': round(base_in, 6)}


def _curve(line, price, decA, decB, points, span):
    """The liquidity line around the price, downsampled for a chart."""
    lo, hi = price * (1 - span), price * (1 + span)
    inside = [r for r in line if lo <= _line_price(r, decA, decB) <= hi]
    if not inside:
        inside = line
    step = max(1, len(inside) // max(4, int(points or 48)))
    return [{'price': round(_line_price(r, decA, decB), 10),
             'liquidity': _f(r.get('liquidity'))} for r in inside[::step]]


def _line_price(row, decA, decB):
    """Raydium publishes the line's price in raw units; make it UI units."""
    raw = _f(row.get('price'))
    if raw is None:
        raw = 1.0001 ** (row.get('tick') or 0)
    return raw * 10 ** (decA - decB)


# ── concentrated positions, read off chain ───────────────────────

POSITION_SIZE = 281        # PersonalPositionState, discriminator included


def decode_position(raw):
    """PersonalPositionState — the account behind a Raydium position NFT.

    Nothing indexes these, so the layout is read by hand: 8 bytes of anchor
    discriminator, the bump, the NFT mint, the pool, the tick range, the
    liquidity, two fee-growth checkpoints and the fees already owed.
    """
    if len(raw) < 145:
        raise RayError(f'position account is {len(raw)} bytes, expected '
                       f'{POSITION_SIZE}', status=422)
    off = 9                                     # discriminator + bump
    nft = b58encode(raw[off:off + 32]); off += 32
    pool_id = b58encode(raw[off:off + 32]); off += 32
    tick_lower, tick_upper = struct.unpack_from('<ii', raw, off); off += 8
    liquidity = int.from_bytes(raw[off:off + 16], 'little'); off += 16
    off += 32                                   # two fee-growth checkpoints
    fees0, fees1 = struct.unpack_from('<QQ', raw, off)
    return {'nft_mint': nft, 'pool': pool_id, 'tick_lower': tick_lower,
            'tick_upper': tick_upper, 'liquidity': liquidity,
            'fees_owed_a_raw': fees0, 'fees_owed_b_raw': fees1}


def _position_amounts(pos, p):
    """Token amounts from a tick range and the pool's live price."""
    decA, decB = p['mint_a'].get('decimals'), p['mint_b'].get('decimals')
    price = p.get('price')
    if decA is None or decB is None or not price:
        return {}
    sqrt_now = math.sqrt(price * 10 ** (decB - decA))
    lo, hi = _tick_sqrt(pos['tick_lower']), _tick_sqrt(pos['tick_upper'])
    L = float(pos['liquidity'])
    if sqrt_now <= lo:                       # price below the range: all base
        amt_a, amt_b = L * (1 / lo - 1 / hi), 0.0
    elif sqrt_now >= hi:                     # above it: all quote
        amt_a, amt_b = 0.0, L * (hi - lo)
    else:
        amt_a, amt_b = L * (1 / sqrt_now - 1 / hi), L * (sqrt_now - lo)
    scale = 10 ** (decA - decB)
    return {
        'in_range': lo < sqrt_now < hi,
        'price_lower': lo * lo * scale, 'price_upper': hi * hi * scale,
        'price_now': price,
        'amount_a': amt_a / 10 ** decA, 'amount_b': amt_b / 10 ** decB,
        'fees_owed_a': pos['fees_owed_a_raw'] / 10 ** decA,
        'fees_owed_b': pos['fees_owed_b_raw'] / 10 ** decB,
    }


def position(nft_mint, pool_info=None):
    """One concentrated position, by the NFT mint that represents it."""
    nft = need_address(nft_mint, 'nft_mint')
    pda = position_pda(nft)
    acc = ((rpc('getAccountInfo', [pda, {'encoding': 'base64'}]) or {})
           .get('value'))
    if not acc:
        raise RayError(f'{nft} is not a Raydium concentrated position — nothing '
                       f'lives at its position address {pda}', status=404)
    if acc.get('owner') != CLMM_PROGRAM:
        raise RayError(f'{pda} is owned by {acc.get("owner")}, not the Raydium '
                       f'CLMM program', status=409)
    import base64
    pos = decode_position(base64.b64decode((acc.get('data') or ['', ''])[0]))
    p = pool_info or pool(pos['pool'])
    amounts = _position_amounts(pos, p)
    px = prices([p['mint_a']['mint'], p['mint_b']['mint']])
    usd_a, usd_b = px.get(p['mint_a']['mint']), px.get(p['mint_b']['mint'])
    value = None
    if amounts and (usd_a is not None or usd_b is not None):
        value = round((amounts['amount_a'] * (usd_a or 0))
                      + (amounts['amount_b'] * (usd_b or 0)), 2)
    fees_usd = None
    if amounts and (usd_a is not None or usd_b is not None):
        fees_usd = round((amounts['fees_owed_a'] * (usd_a or 0))
                         + (amounts['fees_owed_b'] * (usd_b or 0)), 4)
    return {
        'nft_mint': nft, 'position_account': pda, 'pool': p['id'],
        'pair': p['pair'], 'tick_lower': pos['tick_lower'],
        'tick_upper': pos['tick_upper'], 'liquidity': str(pos['liquidity']),
        'closed': pos['liquidity'] == 0,
        **amounts,
        'symbol_a': p['mint_a'].get('symbol'), 'symbol_b': p['mint_b'].get('symbol'),
        'value_usd': value, 'fees_owed_usd': fees_usd,
        'range_width_pct': round((amounts['price_upper'] / amounts['price_lower'] - 1)
                                 * 100, 2) if amounts.get('price_lower') else None,
        'note': 'amounts are computed from the tick range and the pool price, so '
                'they move with it. fees_owed is only what the pool has already '
                'checkpointed to this position — fees earned since the last '
                'touch are not counted here.',
    }


def wallet(address, min_usd=0.01, limit=50):
    """Everything a wallet holds on Raydium — LP tokens and concentrated positions.

    Concentrated liquidity does not show up in a portfolio call: what the wallet
    holds is an NFT with no balance and no price, and the money is in an account
    derived from it. So this takes every NFT in the wallet, derives the position
    address it would have, asks the chain which of those exist, and prices what
    it finds.
    """
    owner = need_address(address, 'wallet')
    accounts = []
    for program in (TOKEN_PROGRAM, TOKEN_2022):
        res = rpc('getTokenAccountsByOwner',
                  [owner, {'programId': program}, {'encoding': 'jsonParsed'}]) or {}
        accounts += res.get('value') or []
    nft_mints, fungible = [], {}
    for a in accounts:
        info = (((a.get('account') or {}).get('data') or {}).get('parsed')
                or {}).get('info') or {}
        amt = (info.get('tokenAmount') or {})
        mint, raw = info.get('mint'), int(amt.get('amount') or 0)
        if not mint or raw <= 0:
            continue
        if int(amt.get('decimals') or 0) == 0 and raw == 1:
            nft_mints.append(mint)
        else:
            fungible[mint] = _f(amt.get('uiAmountString') or amt.get('uiAmount'))

    positions = _wallet_positions(nft_mints, limit)
    lps = _wallet_lps(fungible)
    kept = [p for p in positions if (p.get('value_usd') or 0) >= float(min_usd or 0)
            or p.get('fees_owed_usd')]
    kept.sort(key=lambda p: p.get('value_usd') or 0, reverse=True)
    lps = [l for l in lps if (l.get('value_usd') or 0) >= float(min_usd or 0)]
    lps.sort(key=lambda l: l.get('value_usd') or 0, reverse=True)
    total = sum(p.get('value_usd') or 0 for p in kept) + \
        sum(l.get('value_usd') or 0 for l in lps)
    return {
        'wallet': owner,
        'total_usd': round(total, 2),
        'positions_usd': round(sum(p.get('value_usd') or 0 for p in kept), 2),
        'lp_usd': round(sum(l.get('value_usd') or 0 for l in lps), 2),
        'fees_owed_usd': round(sum(p.get('fees_owed_usd') or 0 for p in kept), 4),
        'out_of_range': [p['nft_mint'] for p in kept if p.get('in_range') is False],
        'positions': kept,
        'lp_tokens': lps,
        'scanned': {'token_accounts': len(accounts), 'nft_candidates': len(nft_mints),
                    'fungible_mints': len(fungible),
                    'closed_positions': len([p for p in positions if p.get('closed')])},
        'note': 'a position whose price has left its range earns nothing until it '
                'comes back or is rebalanced — those are listed in out_of_range',
    }


def _wallet_positions(nft_mints, limit):
    """Derive every candidate position address, then ask the chain in batches
    which ones exist. One RPC round trip per 100 NFTs, not one per NFT."""
    import base64
    if not nft_mints:
        return []
    nft_mints = nft_mints[:max(1, min(int(limit or 50), 400))]
    pdas = {}
    for mint in nft_mints:
        try:
            pdas[position_pda(mint)] = mint
        except RayError:
            continue
    found = []
    keys = list(pdas)
    for chunk in _chunks(keys, 100):
        res = (rpc('getMultipleAccounts', [chunk, {'encoding': 'base64'}]) or {})
        for key, acc in zip(chunk, res.get('value') or []):
            if not acc or acc.get('owner') != CLMM_PROGRAM:
                continue
            try:
                pos = decode_position(base64.b64decode(acc['data'][0]))
            except RayError:
                continue
            pos['position_account'] = key
            found.append(pos)
    if not found:
        return []
    pool_ids = sorted({p['pool'] for p in found})
    pools_by_id = {}
    for chunk in _chunks(pool_ids, 20):
        for p in get('/pools/info/ids', ttl=30, ids=','.join(chunk)) or []:
            if p:
                pools_by_id[p['id']] = slim_pool(p, full=True)
    mints = sorted({m for p in pools_by_id.values()
                    for m in (p['mint_a']['mint'], p['mint_b']['mint'])})
    px = prices(mints) if mints else {}
    rows = []
    for pos in found:
        p = pools_by_id.get(pos['pool'])
        if not p:
            rows.append({'nft_mint': pos['nft_mint'], 'pool': pos['pool'],
                         'error': 'pool not in the Raydium API'})
            continue
        amounts = _position_amounts(pos, p)
        usd_a = px.get(p['mint_a']['mint']) or 0
        usd_b = px.get(p['mint_b']['mint']) or 0
        rows.append({
            'nft_mint': pos['nft_mint'], 'position_account': pos['position_account'],
            'pool': p['id'], 'pair': p['pair'], 'type': p['type'],
            'liquidity': str(pos['liquidity']), 'closed': pos['liquidity'] == 0,
            **amounts,
            'value_usd': round(amounts.get('amount_a', 0) * usd_a
                               + amounts.get('amount_b', 0) * usd_b, 2)
            if amounts else None,
            'fees_owed_usd': round(amounts.get('fees_owed_a', 0) * usd_a
                                   + amounts.get('fees_owed_b', 0) * usd_b, 4)
            if amounts else None,
            'apr_24h': p.get('apr_24h'),
        })
    return rows


def _wallet_lps(fungible):
    """Match the wallet's fungible balances against Raydium LP mints."""
    if not fungible:
        return []
    rows = []
    for chunk in _chunks(sorted(fungible), 20):
        try:
            found = get('/pools/info/lps', ttl=60, lps=','.join(chunk)) or []
        except RayError:
            continue
        for p in found:
            if not p:
                continue
            slim = slim_pool(p, full=True)
            lp = slim.get('lp') or {}
            balance = fungible.get(lp.get('mint'))
            if balance is None:
                continue
            supply, price = lp.get('supply'), lp.get('price')
            rows.append({
                'lp_mint': lp.get('mint'), 'pool': slim['id'], 'pair': slim['pair'],
                'balance': balance,
                'share_pct': round(balance / supply * 100, 6) if supply else None,
                'value_usd': round(balance * price, 2) if price else None,
                'amount_a': round(slim['reserve_a'] * balance / supply, 8)
                if supply and slim.get('reserve_a') else None,
                'amount_b': round(slim['reserve_b'] * balance / supply, 8)
                if supply and slim.get('reserve_b') else None,
                'apr_24h': slim.get('apr_24h'),
            })
    return rows
