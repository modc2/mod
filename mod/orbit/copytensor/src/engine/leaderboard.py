"""
Leaderboard — rank watched accounts by their N-day subnet token PnL.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from ..chain.client import SubtensorClient
from ..db import Database
from .pnl import PnlResult, calculate_pnl

log = logging.getLogger("copytensor.leaderboard")


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


def build_leaderboard(client: SubtensorClient, db: Database,
                      days: int = 7, top: int = 50,
                      min_subnets: int = 0) -> List[LeaderboardEntry]:
    """
    Build a leaderboard of watched accounts ranked by PnL.

    Iterates through all watched accounts, calculates N-day PnL for each,
    and returns them sorted by PnL descending.
    """
    accounts = db.list_accounts()
    entries: List[LeaderboardEntry] = []

    for acct in accounts:
        ss58 = acct["ss58"]
        label = acct.get("label")
        try:
            pnl = calculate_pnl(client, db, ss58, days)
            if len(pnl.by_subnet) < min_subnets:
                continue

            # Find top subnet by PnL
            top_sn = None
            top_sn_pnl = 0.0
            for sn in pnl.by_subnet:
                if sn.pnl_tao > top_sn_pnl:
                    top_sn = sn.netuid
                    top_sn_pnl = sn.pnl_tao

            entries.append(LeaderboardEntry(
                ss58=ss58,
                label=label,
                total_stake_tao=pnl.end_value_tao,
                pnl_tao=pnl.pnl_tao,
                pnl_pct=pnl.pnl_pct,
                num_subnets=len(pnl.by_subnet),
                top_subnet=top_sn,
                top_subnet_pnl=top_sn_pnl,
            ))
        except Exception as e:
            log.warning("leaderboard skip %s: %s", ss58[:8], e)
            continue

    # Sort by PnL descending
    entries.sort(key=lambda e: e.pnl_tao, reverse=True)
    return entries[:top]
