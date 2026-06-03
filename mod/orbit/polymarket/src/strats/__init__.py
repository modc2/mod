"""
polymarket.strats — programmable Polymarket strategies.

Strategies are Python classes with a canonical interface (Strat), the same
way ERC-20 standardizes `balanceOf` / `transfer`. The engine (live engine
in TS, backtest engine here) speaks to the strat through these methods —
nothing else. Owners write or fork classes to express custom logic.

Each strat lives in its own folder under `strats/` with the source in
`mod.py` — mirrors the on-disk layout of user-uploaded strats
(`<id>/mod.py`) so bundled and uploaded strats are organized the same way.
See `base/mod.py` for the abstract Strat interface and `copytrader/mod.py`
for the reference implementation (mirrors trades from a watchlist with
weights).
"""

from .base.mod import (
    Strat,
    StratConfig,
    Order,
    OrderSide,
    TraderTrade,
    SyncResult,
    ExecutionResult,
    TickResult,
    BacktestResult,
)
from .copytrader.mod import CopyTrader

__all__ = [
    "Strat",
    "StratConfig",
    "Order",
    "OrderSide",
    "TraderTrade",
    "SyncResult",
    "ExecutionResult",
    "TickResult",
    "BacktestResult",
    "CopyTrader",
]
