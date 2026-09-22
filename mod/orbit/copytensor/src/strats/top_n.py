"""TopN — pick the top N coldkeys by N-day PnL off the board."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class TopN(Strat):
    """Pick the top N board traders by window PnL and mirror them.

    Sizing uses each trader's normalized positive PnL as weight, so the
    highest-PnL trader gets the largest sleeve. Set `equal_weight=True`
    to ignore PnL and weight each leader equally. Rows without a PnL
    baseline (`baseline: false` — still warming) are never picked: their
    PnL reads 0 by honesty, not by performance.

    Example:
        TopN(days=7, n=5, capital=50).start(ct)
    """

    name = "top_n"
    description = "Mirror the top N coldkeys by N-day PnL (PnL-weighted by default)."

    def __init__(
        self,
        n: int = 5,
        days: int = 7,
        pool: int = 200,
        equal_weight: bool = False,
        min_pnl_tao: float = 0.0,
        min_book_tao: float = 1.0,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.pool = pool
        self.equal_weight = equal_weight
        self.min_pnl_tao = min_pnl_tao
        self.min_book_tao = min_book_tao
        self._params.update(n=n, days=days, equal_weight=equal_weight,
                            min_pnl_tao=min_pnl_tao)

    def pick_leaders(self, ct) -> List[Leader]:
        rows = ct.leaderboard(days=self.days, top=self.pool) or []
        rows = [r for r in rows
                if r.get("baseline", True)
                and (r.get("total_stake_tao") or 0) >= self.min_book_tao
                and (r.get("pnl_tao") or 0) >= self.min_pnl_tao]
        rows.sort(key=lambda r: r.get("pnl_tao", 0), reverse=True)
        picked = rows[: self.n]
        if not picked:
            return []
        if self.equal_weight:
            return [Leader(ss58=r["ss58"], weight=1.0) for r in picked]
        # PnL-weighted: positive PnL only, normalized to sum=1.
        pnls = [max(r.get("pnl_tao", 0), 0.0) for r in picked]
        total = sum(pnls) or 1.0
        return [Leader(ss58=r["ss58"], weight=p / total)
                for r, p in zip(picked, pnls)]
