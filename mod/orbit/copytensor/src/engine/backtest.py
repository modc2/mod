"""
Backtest — what a basket of traders would have done to your money.

A copy strat has no positions of its own: it mirrors other people's books.
So the honest replay is a *return* replay. For every trader in the basket we
take the portfolio-value series bt already indexes, turn it into a return
series, and blend those returns at the basket's normalized weights:

    r_basket(t) = Σ w_i · r_i(t)          (weights renormalized over the
                                           traders that have data at t)
    equity(t)   = capital · Π (1 + r_basket)

That is a *rebalanced* mirror: it assumes you hold each trader's allocation
at their weight and top back up to those weights on every step, which is what
the copy engine's rebalance threshold approximates. It deliberately does NOT
model execution lag, slippage, or the fee you pay to follow — a copied trade
lands after the leader's, so the real curve sits under this one. The API
returns `assumptions` so the UI can say that out loud instead of selling the
number as a promise.

Everything comes from real indexed snapshots. Nothing is interpolated: the
series are resampled onto a shared grid by carrying the last observed value
forward, and a trader with no data in the window is reported as `skipped`
rather than silently counted as a flat zero-return holding.
"""

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# Grid resolution — a point per hour keeps a 30-day replay under 800 points,
# which is what the chart wants anyway.
STEP_SEC = 3600
MAX_POINTS = 800
# One bt call per leg (~30ms), and "ALL" on a 400-trader board is one click
# away — past this many legs the replay is truncated to the heaviest ones
# and says so in `truncated`, rather than making every keystroke wait.
MAX_LEGS = 60
# A basket needs at least this many grid points before its stats mean
# anything; the trader index is young, so say so rather than print a Sharpe
# off three samples.
MIN_POINTS = 6
# A book worth less than this isn't something you can mirror, and its
# percentage moves are noise: a wallet going from 0.000001τ to 1τ reads as a
# 100,000,000% "return" and would swamp every honest leg in the basket. Legs
# are only live for the steps where their book is above the floor, and a leg
# that never clears it is skipped outright.
DUST_TAO = 0.5


def _series_points(raw: Dict) -> List[Tuple[int, float]]:
    """bt's trader_history payload → [(unix_ts, total_tao)], ascending."""
    out: List[Tuple[int, float]] = []
    for p in (raw or {}).get("series") or []:
        try:
            ts = int(p.get("t") or 0)
            v = float(p.get("total_tao") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts > 0 and v > 0:
            out.append((ts, v))
    out.sort(key=lambda x: x[0])
    return out


def _resample(points: Sequence[Tuple[int, float]], grid: Sequence[int]) -> List[Optional[float]]:
    """Last-observation-carried-forward onto `grid`. None before the first
    observation — a trader we have no data for yet must not read as flat."""
    out: List[Optional[float]] = []
    i = 0
    last: Optional[float] = None
    for t in grid:
        while i < len(points) and points[i][0] <= t:
            last = points[i][1]
            i += 1
        out.append(last)
    return out


def _max_drawdown(equity: Sequence[float]) -> float:
    peak = float("-inf")
    worst = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return worst


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return var ** 0.5


def backtest_basket(
    traders: Sequence[Dict],
    hours: int,
    fetch_history: Callable[[str, int], Dict],
    capital_tao: float = 100.0,
) -> Dict:
    """Replay `traders` (each {ss58, weight, enabled?, label?}) over `hours`.

    `fetch_history(ss58, hours)` returns bt's trader_history payload. It is
    injected so the API can pass its own bt client and the tests can pass a
    fixture.
    """
    picks = [
        t for t in traders
        if t.get("enabled") is not False and float(t.get("weight") or 0) > 0
    ]
    total_w = sum(float(t["weight"]) for t in picks)
    if not picks or total_w <= 0:
        return _empty("no enabled traders with weight", hours, capital_tao)

    truncated = None
    if len(picks) > MAX_LEGS:
        picks = sorted(picks, key=lambda t: float(t["weight"]), reverse=True)[:MAX_LEGS]
        truncated = {"kept": MAX_LEGS, "dropped": len(traders) - MAX_LEGS}
        total_w = sum(float(t["weight"]) for t in picks)

    # Pull every leg first — one bad address shouldn't sink the replay.
    legs: List[Dict] = []
    skipped: List[Dict] = []
    for t in picks:
        ss58 = str(t.get("ss58") or "")
        try:
            pts = _series_points(fetch_history(ss58, hours))
        except Exception as e:  # bt down, unknown address, timeout
            skipped.append({"ss58": ss58, "reason": str(e)[:120]})
            continue
        if len(pts) < 2:
            skipped.append({"ss58": ss58, "reason": "no indexed history in this window"})
            continue
        if max(v for _, v in pts) < DUST_TAO:
            skipped.append({"ss58": ss58,
                            "reason": f"book never above {DUST_TAO}τ — nothing to mirror"})
            continue
        legs.append({
            "ss58": ss58,
            "label": t.get("label"),
            "weight": float(t["weight"]) / total_w,
            "points": pts,
        })

    if not legs:
        return _empty("none of these traders have indexed history yet", hours,
                      capital_tao, skipped=skipped)

    # Renormalize over the legs that survived, so the share shown against a
    # trader is the share it was actually mirrored at — a skipped leg's
    # weight goes to the rest, it doesn't sit in cash.
    live_w = sum(leg["weight"] for leg in legs)
    if live_w > 0:
        for leg in legs:
            leg["weight"] /= live_w

    # Shared grid across whatever window the data actually covers — asking
    # for 30 days when the index is 5 days old gets you 5 days, labelled.
    start = min(leg["points"][0][0] for leg in legs)
    end = max(leg["points"][-1][0] for leg in legs)
    span = max(end - start, STEP_SEC)
    step = max(STEP_SEC, span // MAX_POINTS)
    grid = list(range(start, end + 1, step))
    if grid[-1] != end:
        grid.append(end)

    for leg in legs:
        leg["values"] = _resample(leg["points"], grid)

    equity = [capital_tao]
    step_returns: List[float] = []
    for leg in legs:
        leg["contrib_tao"] = 0.0
    for i in range(1, len(grid)):
        live: List[Tuple[Dict, float]] = []
        wsum = 0.0
        for leg in legs:
            prev, cur = leg["values"][i - 1], leg["values"][i]
            # Not live yet, or the book was dust going in: skip the step and
            # renormalize the basket around the legs that were real.
            if prev is None or cur is None or prev < DUST_TAO:
                continue
            live.append((leg, cur / prev - 1.0))
            wsum += leg["weight"]
        r = 0.0
        if wsum > 0:
            for leg, leg_r in live:
                share = leg["weight"] / wsum
                r += share * leg_r
                # Attribution in money, against the equity going into the
                # step — these sum to the basket's PnL exactly, which a
                # simple weight × window-return never does once the basket
                # rebalances and legs come and go.
                leg["contrib_tao"] += share * leg_r * equity[-1]
        step_returns.append(r)
        equity.append(equity[-1] * (1.0 + r))

    # Per-leg contribution: its own window return, and that return times the
    # weight it was actually held at — the honest "who carried this basket".
    per_trader = []
    for leg in legs:
        vals = [v for v in leg["values"] if v is not None and v >= DUST_TAO]
        ret = (vals[-1] / vals[0] - 1.0) if len(vals) >= 2 and vals[0] > 0 else 0.0
        per_trader.append({
            "ss58": leg["ss58"],
            "label": leg["label"],
            "weight": round(leg["weight"], 6),
            "return_pct": round(ret * 100, 4),
            "contribution_tao": round(leg["contrib_tao"], 6),
            "contribution_pct": round(leg["contrib_tao"] / capital_tao * 100, 4)
            if capital_tao > 0 else 0.0,
        })
    per_trader.sort(key=lambda r: r["contribution_pct"], reverse=True)

    total_return = equity[-1] / equity[0] - 1.0 if equity[0] > 0 else 0.0
    covered_sec = grid[-1] - grid[0]
    days = max(covered_sec / 86400.0, 1e-9)
    # Annualized only when there's a real window behind it — and only when
    # the arithmetic survives it. A book that went to zero in three days
    # compounds to infinity over a year; that number is noise, not a
    # forecast, so it comes back null instead of blowing up the request.
    apy = None
    if days >= 0.5:
        try:
            apy = (1.0 + total_return) ** (365.0 / days) - 1.0
        except (OverflowError, ValueError):
            apy = None
    if apy is not None and (not math.isfinite(apy) or abs(apy) > 1e6):
        apy = None
    sd = _stdev(step_returns)
    steps_per_year = (365.0 * 86400.0) / step
    sharpe = (
        (sum(step_returns) / len(step_returns)) / sd * (steps_per_year ** 0.5)
        if sd > 0 and len(step_returns) >= MIN_POINTS else None
    )
    if sharpe is not None and not math.isfinite(sharpe):
        sharpe = None

    return {
        "ok": True,
        "note": None,
        "capital_tao": capital_tao,
        "requested_hours": hours,
        "covered_hours": round(covered_sec / 3600.0, 2),
        "from_ts": grid[0],
        "to_ts": grid[-1],
        "step_sec": step,
        "points": len(grid),
        "thin": len(grid) < MIN_POINTS,
        "curve": [
            {"t": t, "equity_tao": round(v, 6) if math.isfinite(v) else None}
            for t, v in zip(grid, equity)
        ],
        "stats": {
            "total_return_pct": round(total_return * 100, 4) if math.isfinite(total_return) else None,
            "end_tao": round(equity[-1], 6),
            "pnl_tao": round(equity[-1] - equity[0], 6),
            "max_drawdown_pct": round(_max_drawdown(equity) * 100, 4),
            "apy_pct": round(apy * 100, 4) if apy is not None else None,
            "sharpe": round(sharpe, 3) if sharpe is not None else None,
            "best_step_pct": round(max(step_returns) * 100, 4) if step_returns else 0.0,
            "worst_step_pct": round(min(step_returns) * 100, 4) if step_returns else 0.0,
        },
        "per_trader": per_trader,
        "skipped": skipped,
        "truncated": truncated,
        "assumptions": [
            "Mirrors each trader's portfolio RETURN at its basket weight, "
            "rebalanced every step.",
            "No execution lag, slippage or fees — a copied trade lands after "
            "the leader's, so live results sit under this curve.",
            "Only traders with indexed history count; the rest are listed "
            "under `skipped` and their weight is redistributed.",
        ],
    }


def _empty(note: str, hours: int, capital_tao: float,
           skipped: Optional[List[Dict]] = None) -> Dict:
    return {
        "ok": False,
        "note": note,
        "capital_tao": capital_tao,
        "requested_hours": hours,
        "covered_hours": 0.0,
        "from_ts": None,
        "to_ts": None,
        "step_sec": STEP_SEC,
        "points": 0,
        "thin": True,
        "curve": [],
        "stats": {},
        "per_trader": [],
        "skipped": skipped or [],
        "truncated": None,
        "assumptions": [],
    }
