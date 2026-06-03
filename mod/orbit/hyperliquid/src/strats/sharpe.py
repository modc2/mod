"""Sharpe — risk-adjusted leader selection."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class Sharpe(Strat):
    """Mirror traders with the highest Sharpe over the window. Reward-
    per-unit-of-risk style. Filters dust-volume noise via
    `min_volume_usd`; allocation is Sharpe-weighted.

    Example:
        Sharpe(n=8, days=14, min_sharpe=1.5, min_volume_usd=50_000).start(hl, eoa)
    """

    name = "sharpe"
    description = "Mirror traders with the highest Sharpe (risk-adjusted)."

    def __init__(
        self,
        n: int = 10,
        days: int = 14,
        min_sharpe: float = 1.0,
        min_volume_usd: float = 25_000.0,
        pool: int = 200,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.min_sharpe = min_sharpe
        self.min_volume_usd = min_volume_usd
        self.pool = pool
        self._params.update(n=n, days=days, min_sharpe=min_sharpe, min_volume_usd=min_volume_usd)

    def pick_leaders(self, hl) -> List[Leader]:
        r = hl.top_traders(days=self.days, min_per_day=0.0, pool=self.pool)
        traders = r.get("traders", []) if isinstance(r, dict) else []
        candidates = [
            t for t in traders
            if (t.get("sharpe") or 0) >= self.min_sharpe
            and (t.get("volume") or 0) >= self.min_volume_usd
        ]
        candidates.sort(key=lambda t: t.get("sharpe", 0), reverse=True)
        picked = candidates[: self.n]
        sharpes = [max(t.get("sharpe", 0), 0.0) for t in picked]
        total = sum(sharpes) or 1.0
        return [Leader(address=t["address"], weight=s / total)
                for t, s in zip(picked, sharpes)]
