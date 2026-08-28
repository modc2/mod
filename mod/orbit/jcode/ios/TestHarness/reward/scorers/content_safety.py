"""Scorer A. content_safety - nothing clipped, overflowing, or hidden.

Efficient use of space must not come at the cost of correctness. This scorer
checks that real content is not bleeding into the OS chrome (status bar / home
indicator) and is not pinned hard against the horizontal edges (a sign of
missing safe-area / padding or text that will clip). It complements
space_efficiency: you can fill the canvas, but not by spilling under the clock.

Note: Context.content_pixels already trims the nominal chrome bands. Here we
re-read the FULL frame so we can inspect the chrome rows themselves.
"""

from __future__ import annotations

import numpy as np

from reward.context import Context, TOKENS, hex_to_rgb
from reward.types import CategoryScore, make_unavailable

NAME = "content_safety"
CATEGORY = "A"
WEIGHT = 0.05

STATUS_BAR_FRAC = 0.055
HOME_INDICATOR_FRAC = 0.025
# Status bar legitimately has the clock/wifi/battery glyphs, so allow some ink
# there; we only flag the *app* drawing content into the home-indicator zone or
# jamming the side edges.
EDGE_PX_FRAC = 0.012  # outermost columns considered "the edge"


def score(ctx: Context) -> CategoryScore:
    arr = ctx.pixels
    if arr is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot")

    h, w, _ = arr.shape
    bg = hex_to_rgb(TOKENS["background"])
    dist = np.linalg.norm(arr - bg, axis=2)
    mask = dist > 18.0

    # Home-indicator band: app content here is a clipping risk.
    home_start = int(h * (1 - HOME_INDICATOR_FRAC))
    home_bleed = float(mask[home_start:].mean())

    # Horizontal edge safety: content in the outermost columns suggests no
    # horizontal inset (text/cards clipping at the screen edge).
    edge = max(1, int(w * EDGE_PX_FRAC))
    # Ignore the chrome rows when judging edges.
    body = mask[int(h * STATUS_BAR_FRAC):home_start]
    left_edge = float(body[:, :edge].mean())
    right_edge = float(body[:, -edge:].mean())
    edge_bleed = max(left_edge, right_edge)

    # Each bleed signal drives the score down. 0 bleed -> 100.
    home_pen = min(home_bleed / 0.05, 1.0)      # 5% occupancy in indicator = max penalty
    edge_pen = min(edge_bleed / 0.04, 1.0)      # 4% occupancy at the edge = max penalty
    value = 100 * (1 - 0.5 * home_pen - 0.5 * edge_pen)
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "home_indicator_bleed": round(home_bleed, 4),
            "left_edge_occ": round(left_edge, 4),
            "right_edge_occ": round(right_edge, 4),
        },
    )
