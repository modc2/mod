#!/usr/bin/env python3
"""swarms chain — the $swarms SPL token on Solana, read-only.

The other half of the module. api.swarms.world runs the agents; this file
answers what the agent economy is worth, who holds it, and what a trade in it
would cost right now.

    mint      74SBV4zDXxTRgv1pEMoECskKBkZHc2yGPnc7GYVepump   (6 decimals)
    launched  17 Dec 2024 on pump.fun, now trading mainly on Raydium

THIS FILE HOLDS NO KEYS AND SIGNS NOTHING. Every call is a read: a JSON-RPC
query to a Solana node, a price from Jupiter, a pool from DexScreener. `quote`
prices a swap and returns the route — it does not build a transaction and could
not submit one, because there is no keypair anywhere in this module to sign it
with. That is a deliberate ceiling, not a missing feature: a module an agent can
call over MCP should not be able to empty a wallet.

Three upstreams, because no single one answers everything and each fails
differently:

    Solana RPC     supply, holders, balances — the chain's own answer
    Jupiter        price and swap routing across every Solana venue
    DexScreener    pools, liquidity, volume, FDV per venue

Stdlib only.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# The $swarms mint. Overridable so the same code can look at any SPL token —
# the console's search box uses this, and it is how you check that a token
# claiming to be $swarms is not a copy with a different mint.
MINT = os.environ.get('SWARMS_MINT', '74SBV4zDXxTRgv1pEMoECskKBkZHc2yGPnc7GYVepump')
DECIMALS = 6

SOL_MINT = 'So11111111111111111111111111111111111111112'
USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

RPC = os.environ.get('SOLANA_RPC', 'https://api.mainnet-beta.solana.com')
JUP = os.environ.get('JUPITER_API', 'https://lite-api.jup.ag').rstrip('/')
DEXSCREENER = 'https://api.dexscreener.com/latest/dex'

TIMEOUT = float(os.environ.get('SWARMS_CHAIN_TIMEOUT', 30))
CACHE_TTL = float(os.environ.get('SWARMS_CHAIN_CACHE_TTL', 30))

TOKEN_PROGRAM = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'

_cache = {}


class ChainError(Exception):
    def __init__(self, message, status=502, hint=None):
        super().__init__(message)
        self.status = status
        self.hint = hint

    def dict(self):
        out = {'error': str(self), 'status': self.status}
        if self.hint:
            out['hint'] = self.hint
        return out


def _get(url, timeout=None):
    req = urllib.request.Request(url, headers={'accept': 'application/json',
                                               'user-agent': 'mod-swarms/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            return json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        raise ChainError(f'{e.code} from {urllib.parse.urlparse(url).netloc}',
                         status=e.code) from None
    except Exception as e:
        raise ChainError(f'cannot reach {urllib.parse.urlparse(url).netloc}: {e}') from None


def _rpc(method, params, timeout=None):
    """One Solana JSON-RPC call.

    The public endpoint rate-limits per method, so a 429 here is normal rather
    than exceptional — it is surfaced with the fix (set SOLANA_RPC to your own
    node) instead of being retried into a stall.
    """
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                       'params': params}).encode()
    req = urllib.request.Request(RPC, data=body,
                                 headers={'content-type': 'application/json',
                                          'user-agent': 'mod-swarms/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            out = json.loads(r.read() or b'{}')
    except urllib.error.HTTPError as e:
        raise ChainError(f'{e.code} from the Solana RPC', status=e.code,
                         hint='set SOLANA_RPC to a node with headroom') from None
    except Exception as e:
        raise ChainError(f'cannot reach the Solana RPC at {RPC}: {e}') from None
    if 'error' in out:
        err = out['error'] or {}
        code = err.get('code')
        raise ChainError(f"{method}: {err.get('message') or code}",
                         status=429 if code == 429 else 502,
                         hint=('the public RPC rate-limits this method — set '
                               'SOLANA_RPC to your own node') if code == 429 else None)
    return out.get('result')


def _cached(key, fn, ttl=None):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < (ttl or CACHE_TTL):
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


# ── the token ──

def supply(mint=None):
    """Circulating supply straight from the chain."""
    mint = mint or MINT
    r = _rpc('getTokenSupply', [mint]) or {}
    v = r.get('value') or {}
    return {'mint': mint, 'supply': v.get('uiAmount'), 'decimals': v.get('decimals'),
            'raw': v.get('amount'), 'slot': (r.get('context') or {}).get('slot')}


def metadata(mint=None):
    """Metaplex metadata via the DAS getAsset call, when the RPC serves it."""
    mint = mint or MINT
    try:
        a = _rpc('getAsset', {'id': mint}) or {}
    except ChainError:
        return {'mint': mint, 'metadata': None,
                'note': 'this RPC does not serve the DAS getAsset method'}
    content = a.get('content') or {}
    meta = content.get('metadata') or {}
    return {'mint': mint, 'name': meta.get('name'), 'symbol': meta.get('symbol'),
            'interface': a.get('interface'),
            'description': meta.get('description'),
            'image': (content.get('links') or {}).get('image'),
            'json_uri': content.get('json_uri'),
            'authorities': a.get('authorities'),
            'mutable': (a.get('mutable')), 'burnt': a.get('burnt')}


def price(mint=None):
    """Spot price in USD from Jupiter's aggregated routing."""
    mint = mint or MINT
    out = _cached(f'price:{mint}',
                  lambda: _get(f'{JUP}/price/v3?ids={urllib.parse.quote(mint)}'))
    row = (out or {}).get(mint) or {}
    if not row:
        raise ChainError(f'Jupiter has no price for {mint}', status=404,
                         hint='the mint may have no routable liquidity')
    return {'mint': mint, 'usd': row.get('usdPrice'),
            'change_24h_pct': row.get('priceChange24h'),
            'liquidity_usd': row.get('liquidity'), 'decimals': row.get('decimals'),
            'launchpad': row.get('launchpad'), 'created_at': row.get('createdAt'),
            'block': row.get('blockId'), 'source': 'jup.ag'}


def pools(mint=None, limit=10):
    """Every venue trading the token, deepest liquidity first."""
    mint = mint or MINT
    out = _cached(f'pools:{mint}', lambda: _get(f'{DEXSCREENER}/tokens/{mint}', timeout=25))
    pairs = (out or {}).get('pairs') or []
    rows = []
    for p in pairs:
        rows.append({
            'dex': p.get('dexId'), 'pair': p.get('pairAddress'),
            'chain': p.get('chainId'),
            'base': (p.get('baseToken') or {}).get('symbol'),
            'quote': (p.get('quoteToken') or {}).get('symbol'),
            'price_usd': _f(p.get('priceUsd')),
            'liquidity_usd': (p.get('liquidity') or {}).get('usd'),
            'volume_24h': (p.get('volume') or {}).get('h24'),
            'change_24h_pct': (p.get('priceChange') or {}).get('h24'),
            'fdv': p.get('fdv'), 'market_cap': p.get('marketCap'),
            'url': p.get('url'),
        })
    rows.sort(key=lambda r: r.get('liquidity_usd') or 0, reverse=True)
    total_liq = sum(r.get('liquidity_usd') or 0 for r in rows)
    total_vol = sum(r.get('volume_24h') or 0 for r in rows)
    return {'mint': mint, 'venues': len(rows),
            'liquidity_usd': round(total_liq, 2), 'volume_24h_usd': round(total_vol, 2),
            'market_cap': rows[0].get('market_cap') if rows else None,
            'pools': rows[:int(limit or 10)], 'source': 'dexscreener.com'}


def token(mint=None):
    """The whole token card: identity, supply, price, depth.

    Each source is fetched independently and a failure is reported in place
    rather than sinking the call — a rate-limited RPC should not cost you the
    price, and a dead price feed should not cost you the supply.
    """
    mint = mint or MINT
    out = {'mint': mint, 'is_swarms': mint == MINT,
           'explorer': f'https://solscan.io/token/{mint}'}
    for name, fn in (('metadata', metadata), ('supply', supply),
                     ('price', price), ('market', pools)):
        try:
            out[name] = fn(mint)
        except ChainError as e:
            out[name] = e.dict()
    p = out.get('price') or {}
    s = out.get('supply') or {}
    if isinstance(p.get('usd'), (int, float)) and isinstance(s.get('supply'), (int, float)):
        out['fdv_usd'] = round(p['usd'] * s['supply'], 2)
    return out


def holders(mint=None, limit=20):
    """The largest token accounts.

    These are token ACCOUNTS, not people: an exchange's hot wallet and a
    liquidity pool both show up as single large holders, so this is a
    concentration signal and not a rich list.
    """
    mint = mint or MINT
    r = _rpc('getTokenLargestAccounts', [mint]) or {}
    rows = (r.get('value') or [])[:int(limit or 20)]
    try:
        total = supply(mint).get('supply') or 0
    except ChainError:
        total = 0
    out = []
    for row in rows:
        amount = row.get('uiAmount') or 0
        out.append({'account': row.get('address'), 'amount': amount,
                    'pct_of_supply': round(amount / total * 100, 4) if total else None})
    return {'mint': mint, 'count': len(out), 'supply': total, 'holders': out,
            'note': 'token accounts, not owners — pools and exchange wallets '
                    'appear as single holders'}


def balance(owner, mint=None):
    """What one wallet holds: SOL, and the token."""
    mint = mint or MINT
    if not owner or len(str(owner)) < 32:
        raise ChainError(f'{owner!r} is not a Solana address', status=400)
    out = {'owner': owner, 'mint': mint}
    try:
        lam = _rpc('getBalance', [owner]) or {}
        out['sol'] = (lam.get('value') or 0) / 1e9
    except ChainError as e:
        out['sol'] = e.dict()
    try:
        r = _rpc('getTokenAccountsByOwner',
                 [owner, {'mint': mint}, {'encoding': 'jsonParsed'}]) or {}
        accounts, total = [], 0.0
        for acc in r.get('value') or []:
            info = (((acc.get('account') or {}).get('data') or {})
                    .get('parsed') or {}).get('info') or {}
            amt = ((info.get('tokenAmount') or {}).get('uiAmount')) or 0
            total += amt
            accounts.append({'account': acc.get('pubkey'), 'amount': amt})
        out['token_accounts'] = accounts
        out['balance'] = total
    except ChainError as e:
        out['balance'] = e.dict()
    if isinstance(out.get('balance'), (int, float)):
        try:
            usd = price(mint).get('usd')
            if isinstance(usd, (int, float)):
                out['value_usd'] = round(out['balance'] * usd, 4)
        except ChainError:
            pass
    return out


def quote(side='buy', amount=1.0, mint=None, slippage_bps=100, pay_with='SOL'):
    """Price a swap. Routes only — nothing is signed and nothing is sent.

    `side` is from the token's point of view: buy spends `pay_with` to get the
    token, sell does the reverse. `amount` is in whatever is being spent.
    """
    mint = mint or MINT
    pay = {'SOL': (SOL_MINT, 9), 'USDC': (USDC_MINT, 6)}.get(str(pay_with).upper())
    if not pay:
        raise ChainError(f'pay_with must be SOL or USDC, not {pay_with}', status=400)
    pay_mint, pay_dec = pay
    if side not in ('buy', 'sell'):
        raise ChainError("side must be 'buy' or 'sell'", status=400)
    if side == 'buy':
        in_mint, out_mint, in_dec, out_dec = pay_mint, mint, pay_dec, DECIMALS
    else:
        in_mint, out_mint, in_dec, out_dec = mint, pay_mint, DECIMALS, pay_dec
    try:
        raw_in = int(round(float(amount) * (10 ** in_dec)))
    except (TypeError, ValueError):
        raise ChainError(f'amount must be a number, not {amount!r}', status=400) from None
    if raw_in <= 0:
        raise ChainError('amount must be positive', status=400)
    url = (f'{JUP}/swap/v1/quote?inputMint={in_mint}&outputMint={out_mint}'
           f'&amount={raw_in}&slippageBps={int(slippage_bps)}')
    q = _get(url, timeout=30)
    if not q or not q.get('outAmount'):
        raise ChainError('Jupiter found no route for that pair and size', status=404)
    got = int(q['outAmount']) / (10 ** out_dec)
    worst = int(q.get('otherAmountThreshold') or q['outAmount']) / (10 ** out_dec)
    route = [step.get('swapInfo', {}).get('label')
             for step in (q.get('routePlan') or []) if isinstance(step, dict)]
    return {
        'side': side, 'mint': mint, 'pay_with': str(pay_with).upper(),
        'in': float(amount), 'out': got, 'worst_case_out': worst,
        'price_impact_pct': _f(q.get('priceImpactPct')),
        'slippage_bps': int(slippage_bps),
        'route': [r for r in route if r] or None,
        'signed': False,
        'note': 'a quote, not a trade — this module holds no key and cannot sign '
                'or submit a Solana transaction',
    }


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def info():
    """What the chain half is, without making a single network call."""
    return {
        'mint': MINT, 'decimals': DECIMALS, 'chain': 'solana-mainnet',
        'rpc': RPC, 'price_source': 'jup.ag', 'pool_source': 'dexscreener.com',
        'read_only': True,
        'signing': 'none — no keypair, no transaction building, no submission',
        'explorer': f'https://solscan.io/token/{MINT}',
    }
