"""
bt.network — the network itself: supply, issuance, halving clock, staked TAO.

taostats's front page, answered without their backend: two storage reads off
the chain (TotalIssuance, TotalStake) on this module's OWN substrate socket —
never the shared engine websocket, so a slow scan elsewhere can't stall it —
plus pure arithmetic for the halving schedule, cached for CACHE_SEC.

Halving schedule (subtensor): 21M TAO cap; block emission starts at 1 TAO and
halves each time total issuance crosses the next "half of what remains" mark
(10.5M, 15.75M, 18.375M, ...). halving_math() is pure so the tests pin it.
"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Dict, Optional

ENDPOINT = os.environ.get('BT_NETWORK_ENDPOINT',
                          os.environ.get('BT_TRADES_ENDPOINT',
                                         'wss://entrypoint-finney.opentensor.ai:443'))
CACHE_SEC = float(os.environ.get('BT_NETWORK_CACHE_SEC', '300'))
MAX_SUPPLY = 21_000_000.0
BASE_EMISSION = 1.0            # TAO per block before any halving
BLOCK_SEC = 12.0
RAO = 1e9

_lock = threading.Lock()
_sub = None
_mem: Dict = {}


def halving_math(issuance_tao: float) -> Dict:
    """Where the emission schedule stands at a given total issuance."""
    issuance_tao = max(0.0, min(issuance_tao, MAX_SUPPLY))
    remaining = MAX_SUPPLY - issuance_tao
    halvings = int(math.log2(MAX_SUPPLY / remaining)) if remaining > 0 else 64
    emission = BASE_EMISSION / (2 ** halvings)
    nxt = MAX_SUPPLY * (1 - 1 / 2 ** (halvings + 1))
    tao_to_go = max(0.0, nxt - issuance_tao)
    daily = emission * 86400 / BLOCK_SEC
    return {
        'max_supply_tao': MAX_SUPPLY,
        'pct_issued': issuance_tao / MAX_SUPPLY * 100,
        'halvings': halvings,
        'block_emission_tao': emission,
        'daily_emission_tao': daily,
        'next_halving_at_tao': nxt,
        'tao_to_halving': tao_to_go,
        'est_days_to_halving': tao_to_go / daily if daily else None,
    }


def _chain():
    global _sub
    if _sub is None:
        from async_substrate_interface.sync_substrate import SubstrateInterface
        _sub = SubstrateInterface(ENDPOINT, ss58_format=42)
    return _sub


def _read() -> Dict:
    global _sub
    try:
        s = _chain()
        block = int(s.get_block_number(s.get_chain_finalised_head()))
        issuance = int(s.query('SubtensorModule', 'TotalIssuance').value) / RAO
        staked = int(s.query('SubtensorModule', 'TotalStake').value) / RAO
    except Exception:
        try:
            if _sub is not None:
                _sub.close()
        finally:
            _sub = None                     # dead socket: reconnect next call
        raise
    out = {'block': block, 'total_issuance_tao': issuance,
           'total_staked_tao': staked,
           'staked_pct': staked / issuance * 100 if issuance else None,
           'ts': int(time.time())}
    out.update(halving_math(issuance))
    return out


def network(max_age: float = CACHE_SEC) -> Dict:
    """Supply + halving + staked state, with the USD spot folded in."""
    now = time.time()
    with _lock:
        if not _mem or now - _mem['ts'] > max_age:
            try:
                _mem.clear(); _mem.update(_read())
            except Exception as e:
                if not _mem:
                    raise
                _mem['read_error'] = f'{type(e).__name__}: {e}'
        out = dict(_mem, age_sec=int(time.time() - _mem['ts']))
    try:
        from . import usd
        out['usd'] = usd.spot()
    except Exception as e:
        out['usd'] = {'error': f'{type(e).__name__}: {e}'}
    return out
