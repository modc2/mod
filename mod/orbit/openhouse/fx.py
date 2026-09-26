"""
OpenHouse — the currency rail.

The simulator prices everything in Ξ, and a reader thinks in their own money.
This file is the one place that knows what a Ξ is worth: ETH quoted in a dozen
fiat currencies from CoinGecko's keyless simple-price endpoint, cached the same
way peers.py caches the landscape.

Local-first, in three layers — the page always gets an answer:

  live      one keyless HTTP call, cached 15 minutes so the marketing page
            can't hammer somebody else's API
  stale     if the network is down, the last good cache is served with its
            original `fetched` timestamp, honestly marked `source: "stale"`
  fallback  if there has never been a cache, a baked-in snapshot ships with
            the module, marked `source: "fallback"` and `fetched: 0` so the
            UI can say "approximate" instead of pretending it's live
"""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

CACHE_TTL = 15 * 60
HTTP_TIMEOUT = 12

COINGECKO_FX = 'https://api.coingecko.com/api/v3/simple/price'

# The menu. `decimals` is a display hint — yen and won don't do cents.
CURRENCIES = {
    'usd': {'symbol': '$',   'name': 'US Dollar'},
    'eur': {'symbol': '€', 'name': 'Euro'},
    'gbp': {'symbol': '£', 'name': 'British Pound'},
    'jpy': {'symbol': '¥', 'name': 'Japanese Yen', 'decimals': 0},
    'cad': {'symbol': 'C$',  'name': 'Canadian Dollar'},
    'aud': {'symbol': 'A$',  'name': 'Australian Dollar'},
    'chf': {'symbol': 'CHF', 'name': 'Swiss Franc'},
    'cny': {'symbol': 'CN¥', 'name': 'Chinese Yuan'},
    'inr': {'symbol': '₹', 'name': 'Indian Rupee'},
    'brl': {'symbol': 'R$',  'name': 'Brazilian Real'},
    'mxn': {'symbol': 'MX$', 'name': 'Mexican Peso'},
    'krw': {'symbol': '₩', 'name': 'South Korean Won', 'decimals': 0},
}

# Last resort only: a hand-recorded snapshot (2026-09), served when the
# network AND the cache have both failed. The UI must label it approximate.
FALLBACK_RATES = {
    'usd': 2631.0,
    'eur': 2292.0,
    'gbp': 1965.0,
    'jpy': 412812.0,
    'cad': 3684.0,
    'aud': 3690.0,
    'chf': 2164.0,
    'cny': 17630.0,
    'inr': 252595.0,
    'brl': 13531.0,
    'mxn': 45329.0,
    'krw': 3647267.0,
}


def _fetch_live():
    url = f"{COINGECKO_FX}?ids=ethereum&vs_currencies={','.join(CURRENCIES)}"
    req = urllib.request.Request(url, headers={
        'accept': 'application/json',
        'user-agent': 'openhouse-mod/2.1 (+https://github.com/mod-protocol)',
    })
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        rows = json.loads(r.read().decode())
    eth = rows.get('ethereum') or {}
    rates = {c: float(eth[c]) for c in CURRENCIES if eth.get(c)}
    if not rates:
        raise ValueError('coingecko returned no rates')
    return rates


def _read_cache(cache_path):
    try:
        cached = json.loads(cache_path.read_text())
        if cached.get('rates'):
            return cached
    except (json.JSONDecodeError, OSError):
        pass
    return None


def rates(cache_path=None, refresh=False):
    """ETH quoted in every currency on the menu. Never raises.

    {'rates': {code: fiat_per_eth}, 'currencies': CURRENCIES,
     'base': 'ETH', 'fetched': ts, 'source': 'live'|'cache'|'stale'|'fallback'}
    """
    cache_path = Path(cache_path) if cache_path else None
    cached = _read_cache(cache_path) if cache_path else None

    if cached and not refresh and time.time() - cached.get('fetched', 0) < CACHE_TTL:
        return {**cached, 'currencies': CURRENCIES, 'source': 'cache'}

    try:
        out = {
            'rates': _fetch_live(),
            'base': 'ETH',
            'fetched': int(time.time()),
            'source': 'live',
        }
        if cache_path:
            try:
                cache_path.write_text(json.dumps(out, indent=2))
            except OSError:
                pass
        return {**out, 'currencies': CURRENCIES}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        if cached:
            return {**cached, 'currencies': CURRENCIES, 'source': 'stale'}
        return {
            'rates': dict(FALLBACK_RATES),
            'currencies': CURRENCIES,
            'base': 'ETH',
            'fetched': 0,
            'source': 'fallback',
        }
