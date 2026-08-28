"""
bt_board — the leaderboard, ranked by the bt module's trader index.

copytensor used to price the board itself: every watched coldkey walked
against an archive node for a historical baseline, hundreds of accounts per
horizon. Measured on the live pool that is 211s for one 7-day board, so a
freshly started API answered /leaderboard with an empty list for minutes —
the "0 traders ranked" board.

bt already snapshots every coldkey it tracks (free TAO plus the whole alpha
book) into local SQLite, and `bt_trader_board` ranks that index in one SQL
pass — the same window, the same market/flow decomposition, ~260ms for the
whole board. So copytensor asks bt the question instead of re-deriving the
answer, and this module is just the mapping from bt's rows onto the entry
shape the API already serves. `build_leaderboard`'s chain walk stays as the
fallback for when bt is down.

The trade-off is deliberate and visible in /universe: this ranks the traders
bt *indexes*, not every coldkey in the watchlist, and a trader bt has only
followed for two days cannot be priced over thirty — `window_days` reports
what each row actually covers and unpriceable rows come back
`baseline=False` with a PnL of 0 rather than a fabricated number.
"""

import logging
from typing import List

from ..chain.bt_source import BtSource, BtUnavailable
from .leaderboard import LeaderboardEntry

log = logging.getLogger("copytensor.bt_board")


def build_bt_leaderboard(bt: BtSource, days: int = 7, top: int = 50,
                         min_subnets: int = 0) -> List[LeaderboardEntry]:
    """Rank every trader in bt's index by their `days`-day alpha PnL.

    Raises BtUnavailable when bt can't answer at all — the caller falls back
    to the chain walk. Ordering is bt's: market_pct, so a coldkey that merely
    deposited doesn't head the board on a percentage it didn't trade for.
    """
    rows = (bt.board(days=days, top=top, min_subnets=min_subnets)
            or {}).get("rows") or []
    if not rows:
        raise BtUnavailable("bt indexes no traders yet")

    return [
        LeaderboardEntry(
            ss58=r["ss58"],
            label=r.get("label"),
            total_stake_tao=r.get("total_stake_tao") or 0.0,
            pnl_tao=r.get("pnl_tao") or 0.0,
            pnl_pct=r.get("pnl_pct") or 0.0,
            num_subnets=int(r.get("num_subnets") or 0),
            top_subnet=r.get("top_subnet"),
            top_subnet_pnl=r.get("top_subnet_pnl") or 0.0,
            baseline=bool(r.get("baseline")),
            window_days=r.get("window_days") or 0.0,
            market_pnl_tao=r.get("market_pnl_tao") or 0.0,
            market_pct=r.get("market_pct") or 0.0,
            flow_tao=r.get("flow_tao") or 0.0,
        )
        for r in rows
    ]
