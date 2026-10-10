"""
bt.usd — the TAO/USD price, from free public tickers, no API key.

taostats and tao.app price everything in dollars behind closed backends; this
is the open version: a handful of keyless public exchange tickers (Kraken,
Coinbase, Binance, CoinGecko), the median of whoever answered, cached on disk
so a restart never shows a blank price and a dead exchange never blanks the
console. Pure stdlib, same house style as bt.news.

spot() is lazy: the first caller inside each TTL window pays the ~1s of HTTP,
everyone else reads the cache. Sources are plain functions in SOURCES — add
one by adding a function.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
import urllib.request
from typing import Dict, Optional

TTL_SEC = float(os.environ.get('BT_USD_TTL_SEC', '60'))
TIMEOUT = 6.0
UA = 'bt-open-explorer/1.0 (+modc2.com/bt)'

_lock = threading.Lock()
_mem: Dict = {}          # last good answer, process-local


def _cache_path() -> str:
    from .history import data_dir
    return os.path.join(data_dir(), 'usd.json')


def _get(url: str) -> Dict:
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


# ------------------------------------------------------------- sources
# Each returns {'usd': float, 'change_24h': float|None} or raises.

def _kraken() -> Dict:
    j = _get('https://api.kraken.com/0/public/Ticker?pair=TAOUSD')
    t = next(iter(j['result'].values()))
    last, opn = float(t['c'][0]), float(t['o'])
    return {'usd': last, 'change_24h': (last / opn - 1) * 100 if opn else None}


def _coinbase() -> Dict:
    j = _get('https://api.coinbase.com/v2/prices/TAO-USD/spot')
    return {'usd': float(j['data']['amount']), 'change_24h': None}


def _binance() -> Dict:
    j = _get('https://api.binance.com/api/v3/ticker/24hr?symbol=TAOUSDT')
    return {'usd': float(j['lastPrice']), 'change_24h': float(j['priceChangePercent'])}


def _coingecko() -> Dict:
    j = _get('https://api.coingecko.com/api/v3/simple/price'
             '?ids=bittensor&vs_currencies=usd&include_24hr_change=true')
    b = j['bittensor']
    return {'usd': float(b['usd']), 'change_24h': b.get('usd_24h_change')}


SOURCES = {'kraken': _kraken, 'coinbase': _coinbase,
           'binance': _binance, 'coingecko': _coingecko}


# ------------------------------------------------------------- spot

def _fetch() -> Dict:
    quotes, errors = {}, {}
    for name, fn in SOURCES.items():
        try:
            quotes[name] = fn()
        except Exception as e:
            errors[name] = f'{type(e).__name__}: {e}'
    if not quotes:
        raise RuntimeError('no USD source answered: ' + '; '.join(
            f'{k}: {v}' for k, v in errors.items()))
    changes = [q['change_24h'] for q in quotes.values() if q.get('change_24h') is not None]
    return {
        'usd': statistics.median(q['usd'] for q in quotes.values()),
        'change_24h': statistics.median(changes) if changes else None,
        'sources': {k: round(q['usd'], 4) for k, q in quotes.items()},
        'errors': errors or None,
        'ts': int(time.time()),
    }


def spot(max_age: float = TTL_SEC) -> Dict:
    """The TAO/USD price: fresh inside TTL, else refetched, else last known."""
    now = time.time()
    with _lock:
        if _mem and now - _mem['ts'] <= max_age:
            return dict(_mem, age_sec=int(now - _mem['ts']), stale=False)
        try:
            cur = _fetch()
            _mem.clear(); _mem.update(cur)
            try:
                with open(_cache_path(), 'w') as f:
                    json.dump(cur, f)
            except Exception:
                pass                          # the disk cache is optional
            return dict(cur, age_sec=0, stale=False)
        except Exception as e:
            last = dict(_mem) or _disk()
            if last:
                return dict(last, age_sec=int(now - last['ts']), stale=True,
                            fetch_error=f'{type(e).__name__}: {e}')
            raise


def _disk() -> Optional[Dict]:
    try:
        with open(_cache_path()) as f:
            return json.load(f)
    except Exception:
        return None
