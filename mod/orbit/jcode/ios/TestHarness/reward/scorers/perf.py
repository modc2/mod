"""Scorer: E. perf.

Best-effort "feels instant" signal from runtime traces. Today the harness runs
headless and does NOT collect runtime metrics, so the normal case is graceful
degradation: when `ctx.runtime` is missing or empty, this returns
`make_unavailable(...)` and the aggregator drops it and renormalizes weights so
the reward is never tanked by data we simply don't have yet.

Expected `ctx.runtime` schema (a plain dict; all keys OPTIONAL, populate what a
future harness can measure via simctl / Instruments / signposts):

    {
        # Time from app launch to the process being ready, milliseconds.
        # <= 400ms feels instant -> 100; >= 2000ms feels sluggish -> 0.
        "cold_launch_ms": float,

        # Time from launch to the first rendered frame, milliseconds.
        # <= 250ms -> 100; >= 1200ms -> 0.
        "first_frame_ms": float,

        # Fraction of frames dropped / janked during a representative scroll,
        # 0.0 (perfectly smooth) .. 1.0 (every frame janked).
        # <= 0.02 -> 100; >= 0.30 -> 0.
        "scroll_jank_frac": float,
    }

The blended value is the weighted mean over WHICHEVER of the three sub-metrics
are present, with weights renormalized over the present ones. If `runtime` is a
dict but contains none of these keys, we still treat it as "no metrics" and go
unavailable. Pure + deterministic: identical runtime dict -> identical score.
"""

from __future__ import annotations

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "perf"
CATEGORY = "E"
WEIGHT = 0.04

# (good_ms_or_frac, bad_ms_or_frac, sub_weight) per metric. `good` maps to 100,
# `bad` maps to 0, linear in between, clamped to [0, 100].
_METRICS = {
    "cold_launch_ms": (400.0, 2000.0, 0.40),
    "first_frame_ms": (250.0, 1200.0, 0.35),
    "scroll_jank_frac": (0.02, 0.30, 0.25),
}


def _ramp(value: float, good: float, bad: float) -> float:
    """100 at `good`, 0 at `bad`, linear + clamped. Works for good<bad ramps."""
    if bad == good:
        return 100.0 if value <= good else 0.0
    frac = (bad - value) / (bad - good)
    return max(0.0, min(100.0, 100.0 * frac))


def score(ctx: Context) -> CategoryScore:
    runtime = ctx.runtime
    if not isinstance(runtime, dict) or not runtime:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no runtime metrics")

    sub_scores: dict[str, float] = {}
    inputs: dict[str, float] = {}
    weighted_sum = 0.0
    weight_total = 0.0

    for key, (good, bad, w) in _METRICS.items():
        raw = runtime.get(key)
        if raw is None:
            continue
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            continue
        s = _ramp(raw, good, bad)
        inputs[key] = round(raw, 4)
        sub_scores[key] = round(s, 2)
        weighted_sum += s * w
        weight_total += w

    if weight_total <= 0:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no runtime metrics")

    value = max(0.0, min(100.0, weighted_sum / weight_total))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            # Echo the runtime inputs we actually consumed.
            "inputs": inputs,
            # Per-metric 0..100 sub-scores behind the blend.
            "sub_scores": sub_scores,
            # Which metrics were present (so a future harness sees coverage).
            "metrics_used": sorted(sub_scores.keys()),
        },
    )
