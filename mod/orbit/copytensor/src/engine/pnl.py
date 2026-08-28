"""
PnL calculation — computes subnet token profit/loss over N days
for any Bittensor account by comparing historical vs current positions.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
    # Δvalue splits exactly into a price move on the book we already held and
    # the stake that came in or out over the window:
    #   market = Σ alpha_start·(price_end − price_start)
    #   flow   = Σ (alpha_end − alpha_start)·price_end
    # Without this a coldkey that merely deposited outranks every real trader
    # on the board — "+1,474,529%" is a wire transfer, not skill.
    market_pnl_tao: float = 0.0
    flow_tao: float = 0.0
    market_pct: float = 0.0
    # False when no historical baseline exists yet (fresh watch, no snapshot,
    # archive query failed) — PnL is reported as 0, never invented.
    baseline: bool = True


def calculate_pnl(client: SubtensorClient, db: Database,
                  ss58: str, days: int,
                  current_block: Optional[int] = None,
                  start_block: Optional[int] = None) -> PnlResult:
    """
    Calculate alpha PnL for an account over the past N days.

    Strategy:
    1. Try to get a snapshot from ~N days ago from the local DB
    2. Fall back to querying the chain at a historical block
    3. Compare with current live positions
    4. Per-subnet PnL = (alpha_now * price_now) - (alpha_then * price_then)

    A caller measuring many accounts over one window (the leaderboard) can
    pin both blocks so every account shares the exact same window.
    """
    if current_block is None:
        current_block = client.get_block()
    if start_block is None:
        start_block = max(0, current_block - (days * BLOCKS_PER_DAY))

    # Get historical data — prefer local snapshot, fall back to chain query
    start_positions, baseline_block = _get_historical_positions(
        client, db, ss58, start_block, current_block,
    )
    baseline = start_positions is not None
    if baseline_block is not None:
        start_block = baseline_block

    # Get current live data
    current = client.get_stake_for_coldkey(ss58)

    # current: netuid -> {alpha, price}
    end_by_subnet: Dict[int, Dict] = {}
    for pos in current.positions:
        if pos.netuid not in end_by_subnet:
            end_by_subnet[pos.netuid] = {"alpha": 0, "price": pos.alpha_price_tao}
        end_by_subnet[pos.netuid]["alpha"] += pos.alpha_amount

    # start: netuid -> {alpha, price}. Without a baseline we mirror the
    # current book so every per-subnet delta is 0 — never a fabricated gain.
    start_by_subnet: Dict[int, Dict] = {}
    if baseline:
        for alloc in start_positions:
            netuid = alloc["netuid"]
            if netuid not in start_by_subnet:
                start_by_subnet[netuid] = {"alpha": 0, "price": alloc.get("price_tao", 0)}
            start_by_subnet[netuid]["alpha"] += alloc.get("alpha", 0)
    else:
        start_by_subnet = {k: dict(v) for k, v in end_by_subnet.items()}
        start_block = current_block

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

    market_total = 0.0
    flow_total = 0.0

    for netuid in sorted(all_netuids):
        s = start_by_subnet.get(netuid, {"alpha": 0, "price": 0})
        e = end_by_subnet.get(netuid, {"alpha": 0, "price": 0})

        value_start = s["alpha"] * s["price"]
        value_end = e["alpha"] * e["price"]
        pnl = value_end - value_start

        # A position that left the book entirely has no end price to mark
        # against — value it at the price it had, so the exit reads as a
        # withdrawal (flow) instead of a 100% market loss.
        price_end = e["price"] or s["price"]
        market_total += s["alpha"] * (price_end - s["price"])
        flow_total += (e["alpha"] - s["alpha"]) * price_end
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
        baseline=baseline,
        market_pnl_tao=market_total,
        flow_tao=flow_total,
        market_pct=(market_total / total_start * 100) if total_start > 0 else 0.0,
    )


def _get_historical_positions(
    client: SubtensorClient, db: Database, ss58: str,
    target_block: int, current_block: int,
) -> Tuple[Optional[List[Dict]], Optional[int]]:
    """Baseline positions for PnL: (allocations, baseline_block).

    Preference order:
    1. local snapshot within a day of the target block
    2. archive-node query at the target block
    3. the oldest local snapshot we have (shorter window, but real)
    4. (None, None) — no baseline exists; caller reports PnL as unknown
    """
    snap = db.get_nearest_snapshot(ss58, target_block)
    if snap and abs(snap["block"] - target_block) < BLOCKS_PER_DAY:
        return snap["allocations"], snap["block"]

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
        ], target_block
    except Exception:
        pass

    # Archive unavailable (or block predates decodable runtime metadata):
    # fall back to the oldest snapshot on record — an honest, shorter window.
    oldest = db.get_first_snapshot(ss58)
    if oldest and oldest["block"] < current_block:
        return oldest["allocations"], oldest["block"]
    return None, None
