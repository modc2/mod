"""Whales — mirror the biggest books on the board."""

from __future__ import annotations

import math
from typing import Any, List

from .base import Leader, Strat


class Whales(Strat):
    """Mirror the largest coldkeys by total staked τ, √value-weighted so one
    mega-book doesn't absorb the whole basket. Requires a minimum subnet
    spread — a whale parked 100% in one subnet is a bet, not a book.

    Example:
        Whales(n=5, min_subnets=3, capital=50).start(ct)
    """

    name = "whales"
    description = "Mirror the biggest books by staked τ (√value-weighted)."

    def __init__(
        self,
        n: int = 5,
        days: int = 7,
        pool: int = 200,
        min_subnets: int = 2,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.pool = pool
        self.min_subnets = min_subnets
        self._params.update(n=n, days=days, min_subnets=min_subnets)

    def pick_leaders(self, ct) -> List[Leader]:
        rows = ct.leaderboard(days=self.days, top=self.pool) or []
        rows = [r for r in rows
                if (r.get("num_subnets") or 0) >= self.min_subnets]
        rows.sort(key=lambda r: r.get("total_stake_tao", 0), reverse=True)
        picked = rows[: self.n]
        if not picked:
            return []
        weights = [math.sqrt(max(r.get("total_stake_tao", 0), 0.0)) for r in picked]
        total = sum(weights) or 1.0
        return [Leader(ss58=r["ss58"], weight=w / total)
                for r, w in zip(picked, weights)]
