"""TopN — pick the top N traders by N-day PnL."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class TopN(Strat):
    """Pick the top N traders by N-day PnL and mirror them.

    Sizing uses each trader's normalized PnL as weight, so the highest-
    PnL trader gets the largest allocation. Set `equal_weight=True` to
    ignore PnL and weight each leader equally.

    Example:
        TopN(days=7, n=10, size_pct=5, max_per_trade_usd=200).start(hl, eoa)
    """

    name = "top_n"
    description = "Mirror the top N traders by N-day PnL (PnL-weighted by default)."

    def __init__(
        self,
        n: int = 10,
        days: int = 7,
        min_per_day: float = 1.0,
        pool: int = 150,
        equal_weight: bool = False,
        min_pnl_usd: float = 0.0,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.min_per_day = min_per_day
        self.pool = pool
        self.equal_weight = equal_weight
        self.min_pnl_usd = min_pnl_usd
        self._params.update(n=n, days=days, equal_weight=equal_weight, min_pnl_usd=min_pnl_usd)

    def pick_leaders(self, hl) -> List[Leader]:
        r = hl.top_traders(days=self.days, min_per_day=self.min_per_day, pool=self.pool)
        traders = r.get("traders", []) if isinstance(r, dict) else []
        filtered = [t for t in traders if (t.get("pnl") or 0) >= self.min_pnl_usd]
        filtered.sort(key=lambda t: t.get("pnl", 0), reverse=True)
        picked = filtered[: self.n]
        if not picked:
            return []
        if self.equal_weight:
            return [Leader(address=t["address"], weight=1.0) for t in picked]
        # PnL-weighted: positive PnL only, normalized to sum=1.
        pnls = [max(t.get("pnl", 0), 0.0) for t in picked]
        total = sum(pnls) or 1.0
        return [Leader(address=t["address"], weight=p / total)
                for t, p in zip(picked, pnls)]
