"""HighWinRate — mirror traders with the highest win rate over the window."""

from __future__ import annotations

from typing import Any, List

from .base import Leader, Strat


class HighWinRate(Strat):
    """Mirror traders with the highest win rate, gated by how much evidence
    that win rate actually rests on. Allocation is weighted by the edge over a
    coin-flip, so consistent winners get most of the size.

    The gate that matters is `min_closes`, not `min_trades`. A win rate is a
    ratio over fills that REALISED PnL — opening fills can neither win nor
    lose — so a wallet with 30 fills may have only 3 closes behind its "100%".
    `min_trades` counts every fill and therefore cannot filter the lucky streak
    it was written to filter; `min_closes` counts the denominator itself.

    Ranking and weighting use `win_rate_lo`, the Wilson 95% lower bound: the
    win rate a book has earned the right to claim given its sample size. On the
    point estimate a 3-for-3 wallet (100%) outranks a 180-of-200 one (90%),
    which is precisely backwards. On the lower bound, 44% vs 85%, it does not.

    `min_win_rate` is a PERCENTAGE (0-100) matching the backend's units — so
    `min_win_rate=60` means "the lower bound is at least 60%".

    Example:
        HighWinRate(min_closes=50, min_win_rate=60, n=10).start(hl, eoa)
    """

    name = "high_win_rate"
    description = "Mirror traders with the highest win rate (gated by trade count)."

    def __init__(
        self,
        n: int = 10,
        days: int = 7,
        min_trades: int = 30,
        min_win_rate: float = 55.0,    # percent, matches backend's 0-100 scale
        pool: int = 200,
        min_closes: int = 20,          # realised closes behind the ratio
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self.n = max(1, n)
        self.days = days
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.pool = pool
        self.min_closes = max(0, min_closes)
        self._params.update(n=n, days=days, min_trades=min_trades,
                            min_win_rate=min_win_rate, min_closes=min_closes)

    @staticmethod
    def _defensible(t: dict) -> float:
        """The win rate this row has earned, on the backend's 0-100 scale.

        Falls back to the point estimate for rows scored by an API old enough
        not to publish a lower bound, so a stale board still ranks rather than
        collapsing to all-zeros.
        """
        lo = t.get("win_rate_lo")
        if lo is None or lo < 0:
            return t.get("win_rate") or 0.0
        return lo

    def pick_leaders(self, hl) -> List[Leader]:
        r = hl.top_traders(days=self.days, min_per_day=0.0, pool=self.pool)
        traders = r.get("traders", []) if isinstance(r, dict) else []
        candidates = [
            t for t in traders
            if (t.get("trades") or 0) >= self.min_trades
            # The real sample-size gate. Rows from an older API report no
            # `closes`; fall back to `trades` so they are merely unfiltered
            # rather than silently excluded from every board.
            and (t.get("closes", t.get("trades")) or 0) >= self.min_closes
            and self._defensible(t) >= self.min_win_rate
        ]
        candidates.sort(key=self._defensible, reverse=True)
        picked = candidates[: self.n]
        if not picked:
            return []
        # Edge over coin-flip, measured on the defensible rate rather than the
        # headline one — otherwise size follows luck.
        edges = [max(self._defensible(t) - 50.0, 0.0) for t in picked]
        total = sum(edges) or 1.0
        return [Leader(address=t["address"], weight=e / total)
                for t, e in zip(picked, edges)]
