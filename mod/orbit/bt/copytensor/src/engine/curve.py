"""
Equity / PnL curve — reconstructs a trader's portfolio value over time from
the local snapshot history, and infers the trades that moved it.

Every point comes from a real snapshot (block + timestamp + full allocation
book); nothing is interpolated. Between two consecutive snapshots the change
in portfolio value splits exactly into

    Δvalue = Σ alpha_before * (price_after - price_before)   ← market move
           + Σ (alpha_after - alpha_before) * price_after    ← flow (trade)

so the UI can show "what the book did" next to "what the trader did".

Trades are *inferred*: a per-subnet alpha delta that is both a meaningful
fraction of the position and worth more than a dust threshold. Emission drip
and dividend payouts are deliberately filtered out — the same heuristic the
bt module uses for its tape.
"""

from datetime import datetime
from typing import Dict, List, Optional

from ..chain.client import BLOCKS_PER_DAY
from ..db import Database

# A move under 2% of the position, or worth under 0.05 TAO, reads as
# emission drift rather than a trade.
FLOW_MIN_FRACTION = 0.02
FLOW_MIN_TAO = 0.05

MAX_SNAPSHOTS = 4000
MAX_POINTS = 400


def _ts(timestamp: str) -> int:
    """ISO-8601 (as stored by SnapshotManager) → unix seconds."""
    try:
        return int(datetime.fromisoformat(timestamp).timestamp())
    except (TypeError, ValueError):
        return 0


def _by_netuid(allocations: List[Dict]) -> Dict[int, Dict]:
    """Collapse per-hotkey rows into one row per subnet."""
    out: Dict[int, Dict] = {}
    for a in allocations or []:
        uid = a.get("netuid")
        if uid is None:
            continue
        row = out.setdefault(uid, {"alpha": 0.0, "price": 0.0, "value": 0.0})
        alpha = float(a.get("alpha") or 0.0)
        price = float(a.get("price_tao") or 0.0)
        row["alpha"] += alpha
        row["value"] += float(a.get("value_tao") or alpha * price)
        if price:
            row["price"] = price
    return out


def _step(before: Dict[int, Dict], after: Dict[int, Dict],
          min_tao: float, min_frac: float):
    """One snapshot→snapshot step: (market_tao, flow_tao, [legs])."""
    market = 0.0
    flow = 0.0
    legs: List[Dict] = []

    for uid in set(before) | set(after):
        b = before.get(uid, {"alpha": 0.0, "price": 0.0})
        a = after.get(uid, {"alpha": 0.0, "price": 0.0})
        # A subnet that dropped out of the book keeps its last known price,
        # otherwise the exit would look like it happened at zero.
        price_before = b["price"] or a["price"]
        price_after = a["price"] or b["price"]

        market += b["alpha"] * (price_after - price_before)
        delta = a["alpha"] - b["alpha"]
        if delta == 0:
            continue
        flow += delta * price_after

        tao = abs(delta) * price_after
        if abs(delta) < min_frac * max(b["alpha"], a["alpha"]) or tao < min_tao:
            continue  # emission drift / dust, not a trade
        legs.append({
            "netuid": uid,
            "side": "buy" if delta > 0 else "sell",
            "alpha": abs(delta),
            "price_tao": price_after,
            "tao_value": tao,
        })

    legs.sort(key=lambda l: l["tao_value"], reverse=True)
    return market, flow, legs


def _downsample(points: List[Dict], keep_ts: set, max_points: int) -> List[Dict]:
    """Thin the series for the chart, never dropping a point that has trades."""
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    keep_idx = {int(i * step) for i in range(max_points)}
    keep_idx.add(0)
    keep_idx.add(len(points) - 1)
    return [p for i, p in enumerate(points)
            if i in keep_idx or p["ts"] in keep_ts]


def build_curve(db: Database, ss58: str, days: int, current_block: int,
                subnet_names: Optional[Dict[int, str]] = None,
                min_tao: float = FLOW_MIN_TAO,
                min_frac: float = FLOW_MIN_FRACTION,
                max_points: int = MAX_POINTS) -> Dict:
    """Portfolio value + cumulative PnL over `days`, with trades on the curve.

    Returns points (one per snapshot), individual trade legs, and legs grouped
    into events (one per snapshot boundary) so the chart can mark a single dot
    per rebalance instead of twenty overlapping ones.
    """
    names = subnet_names or {}
    if current_block <= 0:  # chain unreachable — anchor on the newest snapshot
        newest = db.get_snapshots(ss58, limit=1)
        current_block = newest[0]["block"] if newest else 0
    from_block = max(0, current_block - days * BLOCKS_PER_DAY)

    snaps = db.get_snapshots(ss58, from_block=from_block, limit=MAX_SNAPSHOTS)
    truncated = len(snaps) >= MAX_SNAPSHOTS
    snaps.reverse()  # get_snapshots is newest-first

    # Nothing inside the window — fall back to whatever history exists so the
    # panel shows a short honest curve instead of an empty box.
    window_short = False
    if len(snaps) < 2:
        snaps = db.get_snapshots(ss58, limit=MAX_SNAPSHOTS)
        snaps.reverse()
        window_short = len(snaps) >= 2

    if not snaps:
        return {
            "ss58": ss58, "days": days, "points": [], "trades": [],
            "events": [], "warming": True,
            "coverage": {"snapshots": 0, "requested_days": days,
                         "actual_days": 0.0, "interval_sec": None,
                         "window_short": False, "truncated": False},
            "totals": {"start_value_tao": 0.0, "end_value_tao": 0.0,
                       "pnl_tao": 0.0, "pnl_pct": 0.0, "market_pnl_tao": 0.0,
                       "flow_tao": 0.0, "buy_tao": 0.0, "sell_tao": 0.0,
                       "trades": 0, "events": 0},
            "note": "no snapshots yet — the snapshot loop builds this history "
                    "every 30 min once the account is watched",
        }

    books = [_by_netuid(s["allocations"]) for s in snaps]
    values = [float(s["total_value_tao"] or 0.0) for s in snaps]
    start_value = values[0]

    points: List[Dict] = []
    trades: List[Dict] = []
    events: List[Dict] = []
    cum_market = 0.0
    cum_flow = 0.0
    buy_tao = sell_tao = 0.0

    for i, snap in enumerate(snaps):
        ts = _ts(snap["timestamp"])
        value = values[i]
        legs: List[Dict] = []
        if i > 0:
            market, flow, legs = _step(books[i - 1], books[i], min_tao, min_frac)
            cum_market += market
            cum_flow += flow

        pnl = value - start_value
        point = {
            "ts": ts,
            "block": snap["block"],
            "value_tao": round(value, 6),
            "pnl_tao": round(pnl, 6),
            "pnl_pct": round(pnl / start_value * 100, 4) if start_value else 0.0,
            "market_tao": round(cum_market, 6),
            "flow_tao": round(cum_flow, 6),
            "trades": len(legs),
        }
        points.append(point)

        if not legs:
            continue

        ev_buy = sum(l["tao_value"] for l in legs if l["side"] == "buy")
        ev_sell = sum(l["tao_value"] for l in legs if l["side"] == "sell")
        buy_tao += ev_buy
        sell_tao += ev_sell

        for leg in legs:
            trades.append({
                "ts": ts,
                "block": snap["block"],
                "netuid": leg["netuid"],
                "subnet_name": names.get(leg["netuid"], f"SN{leg['netuid']}"),
                "side": leg["side"],
                "alpha": round(leg["alpha"], 6),
                "price_tao": round(leg["price_tao"], 8),
                "tao_value": round(leg["tao_value"], 6),
                # where the marker sits on each curve
                "value_tao": point["value_tao"],
                "pnl_tao": point["pnl_tao"],
            })

        events.append({
            "ts": ts,
            "block": snap["block"],
            "buy_tao": round(ev_buy, 6),
            "sell_tao": round(ev_sell, 6),
            "net_tao": round(ev_buy - ev_sell, 6),
            "legs": len(legs),
            "side": "buy" if ev_buy >= ev_sell else "sell",
            "value_tao": point["value_tao"],
            "pnl_tao": point["pnl_tao"],
            "top": [{"netuid": l["netuid"],
                     "subnet_name": names.get(l["netuid"], f"SN{l['netuid']}"),
                     "side": l["side"],
                     "tao_value": round(l["tao_value"], 6)}
                    for l in legs[:3]],
        })

    end_value = values[-1]
    pnl_tao = end_value - start_value
    span_sec = max(points[-1]["ts"] - points[0]["ts"], 0)
    interval = int(span_sec / (len(points) - 1)) if len(points) > 1 else None

    return {
        "ss58": ss58,
        "days": days,
        "block_start": snaps[0]["block"],
        "block_end": snaps[-1]["block"],
        "from_ts": points[0]["ts"],
        "to_ts": points[-1]["ts"],
        "points": _downsample(points, {t["ts"] for t in trades}, max_points),
        "trades": trades,
        "events": events,
        "totals": {
            "start_value_tao": round(start_value, 6),
            "end_value_tao": round(end_value, 6),
            "pnl_tao": round(pnl_tao, 6),
            "pnl_pct": round(pnl_tao / start_value * 100, 4) if start_value else 0.0,
            "market_pnl_tao": round(cum_market, 6),
            "flow_tao": round(cum_flow, 6),
            "buy_tao": round(buy_tao, 6),
            "sell_tao": round(sell_tao, 6),
            "trades": len(trades),
            "events": len(events),
        },
        "coverage": {
            "snapshots": len(points),
            "requested_days": days,
            "actual_days": round(span_sec / 86400, 3),
            "interval_sec": interval,
            "window_short": window_short,
            "truncated": truncated,
        },
        "note": "curve from local snapshots; trades inferred from per-subnet "
                f"alpha deltas (moves under {int(min_frac * 100)}% of the "
                f"position or {min_tao} TAO are treated as emission drift)",
    }
