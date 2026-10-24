"""
Leaderboard — rank watched accounts by their N-day subnet token PnL.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional

from ..chain.client import BLOCKS_PER_DAY, SubtensorClient
from ..db import Database
from .pnl import PnlResult, calculate_pnl

log = logging.getLogger("copytensor.leaderboard")

# The pool covers hundreds of accounts, and each one is an independent
# read (bt HTTP, or an archive query bounded by the client's own archive
# pool) — so walk them concurrently instead of one at a time.
DEFAULT_WORKERS = 8


@dataclass
class LeaderboardEntry:
    ss58: str
    label: Optional[str]
    total_stake_tao: float
    pnl_tao: float
    pnl_pct: float
    num_subnets: int
    top_subnet: Optional[int]
    top_subnet_pnl: float
    baseline: bool = True
    # Days actually covered. Equal to the requested horizon when the baseline
    # came from the archive; shorter when the oldest local snapshot is all
    # the history that exists for that account — a 30d column must never
    # silently show 3 days of PnL for one trader and 30 for the next.
    window_days: float = 0.0
    # PnL split into what the book earned and what was deposited/withdrawn.
    # Ranking on raw % puts every fresh deposit above every real trader.
    market_pnl_tao: float = 0.0
    market_pct: float = 0.0
    flow_tao: float = 0.0


def build_leaderboard(client: SubtensorClient, db: Database,
                      days: int = 7, top: int = 50,
                      min_subnets: int = 0,
                      workers: int = DEFAULT_WORKERS) -> List[LeaderboardEntry]:
    """
    Build a leaderboard of watched accounts ranked by PnL.

    Walks every watched account concurrently, calculates N-day PnL for each,
    and returns them sorted by PnL descending.
    """
    accounts = db.list_accounts()
    if not accounts:
        return []

    # Pin one block pair for the whole board: every account is then measured
    # over the identical window, and the client's historical subnet-price
    # cache is hit once instead of once per account.
    try:
        current_block = client.get_block()
        start_block = max(0, current_block - (days * BLOCKS_PER_DAY))
    except Exception as e:
        log.warning("leaderboard: no chain head (%s) — per-account blocks", e)
        current_block = start_block = None

    def _entry(acct) -> Optional[LeaderboardEntry]:
        ss58 = acct["ss58"]
        try:
            pnl = calculate_pnl(client, db, ss58, days,
                                current_block=current_block,
                                start_block=start_block)
        except Exception as e:
            log.warning("leaderboard skip %s: %s", ss58[:8], e)
            return None
        if len(pnl.by_subnet) < min_subnets:
            return None

        # Find top subnet by PnL
        top_sn = None
        top_sn_pnl = 0.0
        for sn in pnl.by_subnet:
            if sn.pnl_tao > top_sn_pnl:
                top_sn = sn.netuid
                top_sn_pnl = sn.pnl_tao

        return LeaderboardEntry(
            ss58=ss58,
            label=acct.get("label"),
            total_stake_tao=pnl.end_value_tao,
            pnl_tao=pnl.pnl_tao,
            pnl_pct=pnl.pnl_pct,
            num_subnets=len(pnl.by_subnet),
            top_subnet=top_sn,
            top_subnet_pnl=top_sn_pnl,
            baseline=pnl.baseline,
            window_days=round(
                max(0, pnl.block_end - pnl.block_start) / BLOCKS_PER_DAY, 2),
            market_pnl_tao=pnl.market_pnl_tao,
            market_pct=pnl.market_pct,
            flow_tao=pnl.flow_tao,
        )

    n = max(1, min(int(workers) or 1, len(accounts)))
    with ThreadPoolExecutor(max_workers=n,
                            thread_name_prefix="lb") as pool:
        entries = [e for e in pool.map(_entry, accounts) if e is not None]

    # Sort by PnL descending; total stake breaks ties (fresh accounts with
    # no baseline all sit at PnL 0 until snapshots accumulate)
    entries.sort(key=lambda e: (e.pnl_tao, e.total_stake_tao), reverse=True)
    return entries[:top]
