"""Steady — traders whose PnL is trading skill, not deposits."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class Steady(Strat):
    """Mirror traders whose return is MARKET PnL — price moves on the book
    they held — rather than flow (deposits masquerading as performance).
    Same discipline as the front-door trader cards: baseline required,
    real book, real spread, and a sane market % (an emptied wallet reads
    +150% market on −100% total; a warming row reads 0 by honesty).

    Equal-weighted on purpose: the filter IS the edge; weighting by the
    ranking metric would just re-concentrate into its noisiest tail.

    Example:
        Steady(n=5, days=7, capital=50).start(ct)
    """

    name = "steady"
    description = "Mirror consistent market-PnL earners (deposit-driven \"returns\" filtered out)."

    def __init__(
        self,
        n: int = 5,
        days: int = 7,
        pool: int = 200,
        min_book_tao: float = 25.0,
        min_subnets: int = 2,
        max_market_pct: float = 500.0,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.pool = pool
        self.min_book_tao = min_book_tao
        self.min_subnets = min_subnets
        self.max_market_pct = max_market_pct
        self._params.update(n=n, days=days, min_book_tao=min_book_tao,
                            min_subnets=min_subnets)

    def pick_leaders(self, ct) -> List[Leader]:
        rows = ct.leaderboard(days=self.days, top=self.pool) or []
        rows = [r for r in rows
                if r.get("baseline", True)
                and (r.get("total_stake_tao") or 0) >= self.min_book_tao
                and (r.get("num_subnets") or 0) >= self.min_subnets
                and (r.get("market_pnl_tao") or 0) > 0
                and abs(r.get("market_pct") or 0) < self.max_market_pct]
        rows.sort(key=lambda r: r.get("market_pct", 0), reverse=True)
        return [Leader(ss58=r["ss58"], weight=1.0) for r in rows[: self.n]]
