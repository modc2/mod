"""Whales — mirror only the largest-volume traders."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class Whales(Strat):
    """Mirror the highest-volume traders. Bets that whales win on net by
    sheer size; allocation is volume-weighted.

    Example:
        Whales(min_volume_usd=1_000_000, n=5, size_pct=2).start(hl, eoa)
    """

    name = "whales"
    description = "Mirror the highest-volume traders (volume-weighted)."

    def __init__(
        self,
        n: int = 5,
        days: int = 7,
        min_volume_usd: float = 500_000.0,
        pool: int = 200,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.min_volume_usd = min_volume_usd
        self.pool = pool
        self._params.update(n=n, days=days, min_volume_usd=min_volume_usd)

    def pick_leaders(self, hl) -> List[Leader]:
        r = hl.top_traders(days=self.days, min_per_day=0.0, pool=self.pool)
        traders = r.get("traders", []) if isinstance(r, dict) else []
        candidates = [t for t in traders if (t.get("volume") or 0) >= self.min_volume_usd]
        candidates.sort(key=lambda t: t.get("volume", 0), reverse=True)
        picked = candidates[: self.n]
        vols = [max(t.get("volume", 0), 0.0) for t in picked]
        total = sum(vols) or 1.0
        return [Leader(address=t["address"], weight=v / total)
                for t, v in zip(picked, vols)]
