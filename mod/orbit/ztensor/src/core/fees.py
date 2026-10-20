"""What an action costs in dollars on each chain's MAINNET, and how long an
epoch has to be.

Chain-agnostic: every adapter answers `mainnet()` (live gas price, block time,
block capacity, read from a public RPC) and `fee(cost, m)` (native token for
one action, given the cost it reported on the local chain). This file adds the
token price and caches the lot for TTL seconds in data/fees.json; when an RPC
or the price feed is down the last good answer is served, marked stale.
"""
import json
import math
import os
import time
import urllib.request

from .. import DATA
from .util import load, save

CACHE = os.path.join(DATA, 'fees.json')
TTL = 600
MIN_EPOCH_S = 3600   # public nets: an epoch lasts at least an hour
PRICES = 'https://api.coingecko.com/api/v3/simple/price?ids={}&vs_currencies=usd'


def rpc(url, method, params=None, timeout=8):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                       'params': params if params is not None else []}).encode()
    req = urllib.request.Request(url, body, {'Content-Type': 'application/json',
                                             'User-Agent': 'ztensor/fees'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    if out.get('error'):
        raise RuntimeError(str(out['error'])[:200])
    return out['result']


def prices(ids):
    req = urllib.request.Request(PRICES.format(','.join(sorted(ids))),
                                 headers={'User-Agent': 'ztensor/fees'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return {k: v['usd'] for k, v in json.load(r).items()}


def quote(nets, fresh=False):
    """{chain: {token, symbol, usd, block_seconds, ...mainnet()}} — cached."""
    old = load(CACHE, {})
    if not fresh and old.get('at', 0) > time.time() - TTL:
        return old
    out = {'at': int(time.time()), 'chains': {}}
    try:
        px = prices({a.token for a in nets if a.token})
    except Exception as e:  # noqa: BLE001
        px, out['price_error'] = {}, str(e)[:200]
    for a in nets:
        if not a.token:
            continue
        prev = (old.get('chains') or {}).get(a.chain) or {}
        try:
            m = {**a.mainnet(), 'stale': False}
        except Exception as e:  # noqa: BLE001
            m = {**prev, 'stale': True, 'error': str(e)[:200]}
        m.update(token=a.token, symbol=a.symbol,
                 usd=px.get(a.token, prev.get('usd')))
        out['chains'][a.chain] = m
    save(CACHE, out)
    return out


def min_tempo(block_seconds):
    """Blocks in MIN_EPOCH_S, rounded up."""
    return math.ceil(round(MIN_EPOCH_S / block_seconds, 6)) \
        if block_seconds else None
