"""Scorer A. information_density - useful content vs. chrome.

Space efficiency asks "is the canvas filled?"; this asks "is what fills it
*useful*?". It estimates the share of the content area devoted to the actual
transcript versus fixed chrome (header band at the top, composer band at the
bottom). A dense, efficient screen spends most of its non-empty pixels on the
conversation, not on a tall header or an oversized composer.

Scenario-aware: the `empty` scenario has no transcript by design. Users do not
experience an empty chat as "low density"; they experience it as either a calm
starting point or a wall of chrome. In that mode we grade chrome leanness (how
little of the screen the fixed chrome eats) instead of transcript ink share.
"""

from __future__ import annotations

import numpy as np

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "information_density"
CATEGORY = "A"
WEIGHT = 0.05

# Scenarios that render a deliberate empty state (no transcript yet).
EMPTY_SCENARIOS = {"empty"}
# Empty-state chrome leanness: fraction of content rows carrying ink. Chrome +
# a hint/affordance should stay within this band; more means the chrome itself
# is bloated, which users DO feel on every future screen.
EMPTY_ROWS_OK = 0.35

# Approximate chrome band heights as a fraction of the content area. The header
# (title + status pill) and the composer are fixed overhead.
HEADER_FRAC = 0.11
COMPOSER_FRAC = 0.11


def _score_empty_state(mask) -> CategoryScore:
    """Empty scenario: grade chrome leanness, not transcript density."""
    row_occ = mask.mean(axis=1)
    inked_rows_frac = float((row_occ > 0.01).mean())

    # Some ink must exist (a header/composer affordance); a fully blank frame
    # gives the user nothing to act on.
    if inked_rows_frac <= 0.0:
        value = 0.0
    elif inked_rows_frac <= EMPTY_ROWS_OK:
        value = 100.0
    else:
        # Chrome creep: decay linearly, hitting 0 when chrome covers all rows.
        value = 100.0 * max(0.0, 1.0 - (inked_rows_frac - EMPTY_ROWS_OK)
                            / (1.0 - EMPTY_ROWS_OK))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "mode": "empty_state",
            "inked_rows_frac": round(inked_rows_frac, 4),
            "empty_rows_ok": EMPTY_ROWS_OK,
        },
    )


def score(ctx: Context) -> CategoryScore:
    mask = ctx.content_mask
    if mask is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot")

    if ctx.scenario in EMPTY_SCENARIOS:
        return _score_empty_state(mask)

    ch = mask.shape[0]
    header_end = int(ch * HEADER_FRAC)
    composer_start = int(ch * (1 - COMPOSER_FRAC))

    row_occ = mask.mean(axis=1)
    total = float(row_occ.sum()) + 1e-9

    header_content = float(row_occ[:header_end].sum())
    composer_content = float(row_occ[composer_start:].sum())
    transcript_content = float(row_occ[header_end:composer_start].sum())

    # Share of all content pixels that live in the transcript region.
    transcript_share = transcript_content / total
    chrome_share = (header_content + composer_content) / total

    # A healthy chat spends most ink on the transcript. But a totally empty
    # transcript (everything in chrome) is bad; reward transcript_share while
    # requiring the transcript region to actually contain something.
    transcript_region_occ = float(row_occ[header_end:composer_start].mean())

    # Blend: 70% transcript share of ink, 30% transcript region occupancy.
    value = 100 * (0.7 * transcript_share + 0.3 * min(transcript_region_occ * 3, 1.0))
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "mode": "transcript",
            "transcript_share": round(transcript_share, 4),
            "chrome_share": round(chrome_share, 4),
            "transcript_region_occ": round(transcript_region_occ, 4),
        },
    )
