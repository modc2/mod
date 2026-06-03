"""HighWinRate — mirror traders with the highest win rate over the window."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class HighWinRate(Strat):
    """Mirror traders with the highest win rate, gated by a minimum trade
    count to filter lucky-streak noise. Allocation is weighted by the
    edge over a coin-flip (win_rate − 0.5), so consistent winners get
    most of the size.

    Example:
        HighWinRate(min_trades=50, min_win_rate=0.6, n=10).start(hl, eoa)
    """

    name = "high_win_rate"
    description = "Mirror traders with the highest win rate (gated by trade count)."

    def __init__(
        self,
        n: int = 10,
        days: int = 7,
        min_trades: int = 30,
        min_win_rate: float = 0.55,
        pool: int = 200,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.pool = pool
        self._params.update(n=n, days=days, min_trades=min_trades, min_win_rate=min_win_rate)

    def pick_leaders(self, hl) -> List[Leader]:
        r = hl.top_traders(days=self.days, min_per_day=0.0, pool=self.pool)
        traders = r.get("traders", []) if isinstance(r, dict) else []
        candidates = [
            t for t in traders
            if (t.get("trades") or 0) >= self.min_trades
            and (t.get("win_rate") or 0) >= self.min_win_rate
        ]
        candidates.sort(key=lambda t: t.get("win_rate", 0), reverse=True)
        picked = candidates[: self.n]
        if not picked:
            return []
        edges = [max((t.get("win_rate", 0) - 0.5), 0.0) for t in picked]
        total = sum(edges) or 1.0
        return [Leader(address=t["address"], weight=e / total)
                for t, e in zip(picked, edges)]
