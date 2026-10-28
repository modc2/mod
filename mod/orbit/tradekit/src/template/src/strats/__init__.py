"""
__NAME__.strats — strategy classes on the canonical Strat schema shared
with hyperliquid and polymarket (sync → signal → execute, tick, backtest —
see base.py). Copied from the hyperliquid framework by tradekit.

Adding a strategy: subclass `Strat` in its own file, set `name` and
`description`, implement `pick_leaders(venue)`, register it below.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import (
    BacktestResult, ExecutionResult, Leader, Order, OrderSide, Strat,
    StratConfig, StratParams, SyncResult, TickResult, TraderTrade,
)
from .copy_wallets import CopyWallets

REGISTRY: Dict[str, type] = {
    CopyWallets.name: CopyWallets,
}


def list_strats() -> List[Dict[str, str]]:
    return [{"name": cls.name, "description": cls.description} for cls in REGISTRY.values()]


def make(name: str, **kwargs: Any) -> Strat:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown strat {name!r}. Known: {sorted(REGISTRY)}")
    return cls(**kwargs)
