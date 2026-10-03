"""
hyperliquid.strats — modular strategy classes on the canonical Strat
schema shared with the polymarket module (sync → signal → execute,
tick, backtest — see base.py and README.md).

Each concrete strat lives in its own module here. The `REGISTRY` maps
canonical names to classes; `make(name, **kwargs)` is the factory used
by the mod-protocol layer and the UI.

Adding a strategy
-----------------
1. Create `mystrat.py` in this folder with a subclass of `Strat`.
2. Set `name = "..."` and `description = "..."` on the class.
3. Implement `pick_leaders(hl)` — the copy-family defaults for
   `signal()`/`backtest()` then work as-is; override `signal()` (pure!)
   for logic that isn't mirroring.
4. Import + register the class below.

The venue-side live engine stays in Rust (api/src/live_engine.rs). A strat
composes its config via `build_config` — it doesn't replace the hot path.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import (
    BacktestResult, ExecutionResult, Leader, Order, OrderSide, Strat,
    StratConfig, StratParams, SyncResult, TickResult, TraderTrade,
)
from .copy_wallets import CopyWallets
from .high_win_rate import HighWinRate
from .sharpe import Sharpe
from .top_n import TopN
from .whales import Whales

REGISTRY: Dict[str, type] = {
    CopyWallets.name:  CopyWallets,
    TopN.name:         TopN,
    Whales.name:       Whales,
    HighWinRate.name:  HighWinRate,
    Sharpe.name:       Sharpe,
}


def list_strats() -> List[Dict[str, str]]:
    """Enumerate registered strategies — name + one-line description."""
    return [{"name": cls.name, "description": cls.description} for cls in REGISTRY.values()]


def make(name: str, **kwargs: Any) -> Strat:
    """Instantiate a strat by name. Unknown names raise ValueError.

        make('top_n', n=10, days=7, size_pct=5)
        make('copy_wallets', addresses=['0xabc…'])
    """
    cls = REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"unknown strat {name!r}. Known: {sorted(REGISTRY)}")
    return cls(**kwargs)


__all__ = [
    # canonical schema (shared with polymarket)
    "Strat", "StratConfig", "Order", "OrderSide", "TraderTrade",
    "SyncResult", "ExecutionResult", "TickResult", "BacktestResult",
    # hyperliquid live-engine bridge
    "Leader", "StratParams",
    # registry
    "CopyWallets", "TopN", "Whales", "HighWinRate", "Sharpe",
    "REGISTRY", "list_strats", "make",
]
