"""
PnL calculation — computes subnet token profit/loss over N days
for any Bittensor account by comparing historical vs current positions.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..chain.client import BLOCKS_PER_DAY, SubtensorClient
from ..db import Database


@dataclass
class SubnetPnl:
    netuid: int
    subnet_name: str
    alpha_start: float
    alpha_end: float
    price_start_tao: float
    price_end_tao: float
    value_start_tao: float
    value_end_tao: float
    pnl_tao: float
    pnl_pct: float


@dataclass
class PnlResult:
    ss58: str
    days: int
    block_start: int
    block_end: int
    start_value_tao: float
    end_value_tao: float
    pnl_tao: float
    pnl_pct: float
    by_subnet: List[SubnetPnl] = field(default_factory=list)


def calculate_pnl(client: SubtensorClient, db: Database,
                  ss58: str, days: int) -> PnlResult:
    """
    Calculate alpha PnL for an account over the past N days.

    Strategy:
    1. Try to get a snapshot from ~N days ago from the local DB
    2. Fall back to querying the chain at a historical block
    3. Compare with current live positions
    4. Per-subnet PnL = (alpha_now * price_now) - (alpha_then * price_then)
    """
    current_block = client.get_block()
    start_block = max(0, current_block - (days * BLOCKS_PER_DAY))

    # Get historical data — prefer local snapshot, fall back to chain query
    start_positions = _get_historical_positions(client, db, ss58, start_block)

    # Get current live data
    current = client.get_stake_for_coldkey(ss58)

    # Build lookup maps
    # start: netuid -> {alpha, price}
    start_by_subnet: Dict[int, Dict] = {}
    for alloc in start_positions:
        netuid = alloc["netuid"]
        if netuid not in start_by_subnet:
            start_by_subnet[netuid] = {"alpha": 0, "price": alloc.get("price_tao", 0)}
        start_by_subnet[netuid]["alpha"] += alloc.get("alpha", 0)

    # current: netuid -> {alpha, price}
    end_by_subnet: Dict[int, Dict] = {}
    for pos in current.positions:
        if pos.netuid not in end_by_subnet:
            end_by_subnet[pos.netuid] = {"alpha": 0, "price": pos.alpha_price_tao}
        end_by_subnet[pos.netuid]["alpha"] += pos.alpha_amount

    # Get subnet names
    subnet_names = {}
    try:
        for info in client.get_all_subnet_info():
            subnet_names[info.netuid] = info.name
    except Exception:
        pass

    # Calculate PnL per subnet
    all_netuids = set(start_by_subnet.keys()) | set(end_by_subnet.keys())
    by_subnet: List[SubnetPnl] = []
    total_start = 0.0
    total_end = 0.0

    for netuid in sorted(all_netuids):
        s = start_by_subnet.get(netuid, {"alpha": 0, "price": 0})
        e = end_by_subnet.get(netuid, {"alpha": 0, "price": 0})

        value_start = s["alpha"] * s["price"]
        value_end = e["alpha"] * e["price"]
        pnl = value_end - value_start
        pnl_pct = (pnl / value_start * 100) if value_start > 0 else (
            100.0 if value_end > 0 else 0.0
        )

        total_start += value_start
        total_end += value_end

        by_subnet.append(SubnetPnl(
            netuid=netuid,
            subnet_name=subnet_names.get(netuid, f"SN{netuid}"),
            alpha_start=s["alpha"],
            alpha_end=e["alpha"],
            price_start_tao=s["price"],
            price_end_tao=e["price"],
            value_start_tao=value_start,
            value_end_tao=value_end,
            pnl_tao=pnl,
            pnl_pct=pnl_pct,
        ))

    total_pnl = total_end - total_start
    total_pnl_pct = (total_pnl / total_start * 100) if total_start > 0 else (
        100.0 if total_end > 0 else 0.0
    )

    return PnlResult(
        ss58=ss58,
        days=days,
        block_start=start_block,
        block_end=current_block,
        start_value_tao=total_start,
        end_value_tao=total_end,
        pnl_tao=total_pnl,
        pnl_pct=total_pnl_pct,
        by_subnet=by_subnet,
    )


def _get_historical_positions(client: SubtensorClient, db: Database,
                               ss58: str, target_block: int) -> List[Dict]:
    """Get positions at a historical point. Try DB snapshot first, then chain."""
    # Try local DB
    snap = db.get_nearest_snapshot(ss58, target_block)
    if snap and abs(snap["block"] - target_block) < BLOCKS_PER_DAY:
        return snap["allocations"]

    # Fall back to chain query at historical block
    try:
        positions = client.get_stake_for_coldkey(ss58, block=target_block)
        return [
            {
                "netuid": p.netuid,
                "hotkey": p.hotkey,
                "alpha": p.alpha_amount,
                "price_tao": p.alpha_price_tao,
                "value_tao": p.value_tao,
            }
            for p in positions.positions
        ]
    except Exception:
        # If historical query fails (no archive node), return empty
        return []
