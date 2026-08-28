"""Reference scorer: A. space_efficiency.

Grades how well the rendered UI uses the canvas: fill ratio, vertical balance,
and the largest empty "dead zone". This is the worked example every other
scorer should follow (NAME / CATEGORY / WEIGHT / pure score()).

Scenario-aware: the `empty` scenario is a deliberate empty state. Real users
judge an empty chat screen by "can I see where to start?" not "are the pixels
filled?", so in that mode we reward a sparse canvas with a visible composer
affordance instead of a 30-60% fill target.
"""

from __future__ import annotations

import numpy as np

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "space_efficiency"
CATEGORY = "A"
WEIGHT = 0.05

# Scenarios that render a deliberate empty state (no transcript yet).
EMPTY_SCENARIOS = {"empty"}
# Empty-state fill band: some ink must exist (chrome + affordance), but the
# canvas is expected to be mostly calm. Above the ceiling it stops looking
# like an empty state and starts looking like clutter.
EMPTY_FILL_FLOOR = 0.01
EMPTY_FILL_CEIL = 0.22
# Bottom band searched for the start affordance (the composer).
AFFORDANCE_BAND_FRAC = 0.20
AFFORDANCE_MIN_OCC = 0.02


def _longest_run(flags) -> int:
    best = run = 0
    for v in flags:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def _score_empty_state(mask: np.ndarray) -> CategoryScore:
    """Empty scenario: a calm canvas with a clear affordance to start.

    Users landing on an empty chat need exactly one thing: an obvious place to
    type. Reward (a) a visible composer/affordance in the bottom band, and
    (b) a fill ratio inside the calm empty-state band. No dead-zone penalty:
    an empty transcript IS a dead zone by design.
    """
    ch = mask.shape[0]
    fill_ratio = float(mask.mean())
    row_occ = mask.mean(axis=1)

    band_start = int(ch * (1 - AFFORDANCE_BAND_FRAC))
    affordance_occ = float(row_occ[band_start:].mean())
    affordance_score = 100.0 * min(affordance_occ / AFFORDANCE_MIN_OCC, 1.0)

    if fill_ratio < EMPTY_FILL_FLOOR:
        calm_score = 100.0 * fill_ratio / EMPTY_FILL_FLOOR  # truly blank screen
    elif fill_ratio <= EMPTY_FILL_CEIL:
        calm_score = 100.0
    else:
        # Past the ceiling, decay linearly: at 2x the ceiling it is no longer
        # an empty state at all.
        over = (fill_ratio - EMPTY_FILL_CEIL) / EMPTY_FILL_CEIL
        calm_score = 100.0 * max(0.0, 1.0 - over)

    value = 0.6 * affordance_score + 0.4 * calm_score
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "mode": "empty_state",
            "fill_ratio": round(fill_ratio, 4),
            "affordance_band_occ": round(affordance_occ, 4),
            "affordance_score": round(affordance_score, 2),
            "calm_score": round(calm_score, 2),
        },
    )


def score(ctx: Context) -> CategoryScore:
    mask = ctx.content_mask
    if mask is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot")

    if ctx.scenario in EMPTY_SCENARIOS:
        return _score_empty_state(mask)

    ch = mask.shape[0]
    fill_ratio = float(mask.mean())

    row_occ = mask.mean(axis=1)
    ys = np.arange(ch)
    occ_sum = row_occ.sum()
    com = float((ys * row_occ).sum() / occ_sum) / ch if occ_sum > 0 else 0.5
    vertical_balance = 1.0 - abs(com - 0.5) * 2.0

    dead = _longest_run(row_occ < 0.01) / ch

    # An efficient chat fills ~30-60% with content reasonably spread. Reward
    # closeness to that band; penalize a large dead zone hard.
    fill_score = 100 * (1 - min(abs(fill_ratio - 0.45) / 0.45, 1.0))
    value = (0.45 * fill_score
             + 0.35 * (vertical_balance * 100)
             + 0.20 * (100 * (1 - dead)))
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "mode": "transcript",
            "fill_ratio": round(fill_ratio, 4),
            "vertical_balance": round(vertical_balance, 4),
            "dead_zone_frac": round(dead, 4),
        },
    )
