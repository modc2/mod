"""starknet/api/stables.py — the stablecoins on Starknet, read live.

A stablecoin is an ERC-20 whose price is held near $1 by something outside
the token contract. On Starknet that something is one of three things:

    fiat     an issuer holds dollars and mints/burns 1:1 (USDC, USDT)
    bridged  a fiat or crypto stablecoin locked on Ethereum and minted here by
             the StarkGate bridge (USDC.e, USDT, DAI) — trust the issuer AND
             the bridge
    cdp      minted on Starknet itself against over-collateralised crypto
             (Opus CASH) — trust the collateral ratio and liquidations

For each token this reads total_supply from the token contract and, where
there is one, the median USD price from the Pragma oracle — Starknet's
native price feed, aggregated on chain from several sources.

    registry(network)   the static list
    stables(network)    live: supply, peg, deviation, oracle freshness
"""

import time
from concurrent.futures import ThreadPoolExecutor

import chain

PRAGMA = {'mainnet': '0x2a85bd616f912537c50a49a4076db02c00b29b2cdc8a197ce92ed1837fa875b'}

MODELS = {
    'fiat': 'Fiat-backed, issued natively: the issuer holds cash and treasuries '
            'and mints or burns 1:1 on Starknet. You trust the issuer.',
    'bridged': 'Locked on Ethereum, minted here by the StarkGate bridge. You trust '
               'the issuer on Ethereum and the bridge contracts.',
    'cdp': 'Minted on Starknet against over-collateralised crypto in a vault. '
           'Liquidations, not a bank account, hold the peg.',
}

REGISTRY = {
    'mainnet': [
        {'id': 'usdc', 'symbol': 'USDC', 'name': 'USD Coin (native)', 'issuer': 'Circle',
         'model': 'fiat', 'decimals': 6, 'pair': 'USDC/USD',
         'address': '0x033068f6539f8e6e6b131e6b2b814e6c34a5224bc66947c47dab9dfee93b35fb',
         'note': 'Issued by Circle directly on Starknet; moves cross-chain through CCTP.'},
        {'id': 'usdc.e', 'symbol': 'USDC.e', 'name': 'USD Coin (bridged)', 'issuer': 'Circle via StarkGate',
         'model': 'bridged', 'decimals': 6, 'pair': 'USDC/USD',
         'address': '0x053c91253bc9682c04929ca02ed00b3e423f6710d2ee7e0d5ebb06f3ecf368a8',
         'note': 'The original bridged USDC from Ethereum, being superseded by native USDC.'},
        {'id': 'usdt', 'symbol': 'USDT', 'name': 'Tether USD', 'issuer': 'Tether via StarkGate',
         'model': 'bridged', 'decimals': 6, 'pair': 'USDT/USD',
         'address': '0x068f5c6a61780768455de69077e07e89787839bf8166decfbf92b645209c0fb8',
         'note': 'Bridged from Ethereum.'},
        {'id': 'dai', 'symbol': 'DAI', 'name': 'Dai', 'issuer': 'Sky (Maker) via StarkGate',
         'model': 'bridged', 'decimals': 18, 'pair': 'DAI/USD',
         'address': '0x05574eb6b8789a91466f902c380d978e472db68170ff82a5b650b95a58ddf4ad',
         'note': 'Crypto-collateralised on Ethereum, bridged here.'},
        {'id': 'dai.v0', 'symbol': 'DAI v0', 'name': 'Dai (legacy bridge)', 'issuer': 'Sky (Maker) via StarkGate',
         'model': 'bridged', 'decimals': 18, 'pair': 'DAI/USD',
         'address': '0x00da114221cb83fa859dbdb4c44beeaa0bb37c7537ad5ae66fe5e0efd20e6eb3',
         'note': 'The pre-Cairo-1 DAI contract; still holds balances.'},
        {'id': 'cash', 'symbol': 'CASH', 'name': 'Opus CASH', 'issuer': 'Opus protocol',
         'model': 'cdp', 'decimals': 18, 'pair': None,
         'address': '0x0498edfaf50ca5855666a700c25dd629d577eb9afccdf3b5977aec79aee55ada',
         'note': 'Borrowed from Opus vaults against ETH, STRK, wBTC and others. No Pragma pair.'},
    ],
    'sepolia': [
        {'id': 'usdc', 'symbol': 'USDC', 'name': 'USD Coin (test)', 'issuer': 'Circle testnet',
         'model': 'fiat', 'decimals': 6, 'pair': None,
         'address': '0x0512feac6339ff7889822cb5aa2a86c848e9d392bb0e3e237c008674feed8343',
         'note': 'Testnet money, worth nothing.'},
        {'id': 'usdc.e', 'symbol': 'USDC.e', 'name': 'USD Coin (bridged test)', 'issuer': 'StarkGate testnet',
         'model': 'bridged', 'decimals': 6, 'pair': None,
         'address': '0x053b40a647cedfca6ca84f542a0fe36736031905a9639a7f19a3c1e66bfd5080',
         'note': 'Testnet money, worth nothing.'},
    ],
}

_cache = {}   # network -> (at, payload)
TTL = 60


def registry(network=None):
    net = network or chain.DEFAULT_NETWORK
    return {'network': net, 'models': MODELS, 'tokens': REGISTRY.get(net, [])}


def _supply(t, net):
    for fn in ('total_supply', 'totalSupply'):
        try:
            out = chain.call(t['address'], fn, network=net)
            raw = chain.uint256(out[0], out[1]) if len(out) >= 2 else int(str(out[0]), 16)
            return raw / 10 ** t['decimals']
        except chain.RpcError:
            continue
    return None


def _price(pair, net):
    """Pragma median: (price, last_updated, sources) or None."""
    if not pair or net not in PRAGMA:
        return None
    pid = hex(int.from_bytes(pair.encode(), 'big'))
    out = chain.call(PRAGMA[net], 'get_data_median', ['0x0', pid], network=net)
    price, dec, ts, n = (int(str(x), 16) for x in out[:4])
    return {'price': price / 10 ** dec, 'updated': ts, 'sources': n}


def stables(network=None):
    net = network or chain.DEFAULT_NETWORK
    hit = _cache.get(net)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    tokens = REGISTRY.get(net, [])
    pairs = sorted({t['pair'] for t in tokens if t['pair']})

    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:  # noqa: BLE001
            return {'error': str(e)}

    with ThreadPoolExecutor(8) as ex:
        sup = list(ex.map(lambda t: safe(_supply, t, net), tokens))
        px = dict(zip(pairs, ex.map(lambda p: safe(_price, p, net), pairs)))
    now = int(time.time())
    rows = []
    for t, s in zip(tokens, sup):
        p = px.get(t['pair']) or None
        row = {**t, 'supply': s if not isinstance(s, dict) else None}
        if isinstance(s, dict):
            row['supply_error'] = s['error']
        if p and 'price' in p:
            row.update(price=p['price'], deviation_bps=round((p['price'] - 1) * 10000, 1),
                       oracle_age_s=now - p['updated'], oracle_sources=p['sources'])
        rows.append(row)
    total = sum(r['supply'] or 0 for r in rows)
    by_model = {}
    for r in rows:
        by_model[r['model']] = by_model.get(r['model'], 0) + (r['supply'] or 0)
    out = {'network': net, 'oracle': PRAGMA.get(net), 'models': MODELS,
           'total_supply_usd': total, 'by_model': by_model,
           'tokens': sorted(rows, key=lambda r: -(r['supply'] or 0)), 'at': now}
    _cache[net] = (time.time(), out)
    return out
