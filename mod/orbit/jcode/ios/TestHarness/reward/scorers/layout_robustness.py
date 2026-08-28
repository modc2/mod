"""Scorer: E. layout_robustness.

Layout fragility is a cross-cell property (variance of per-cell scores), but a
scorer only ever sees ONE matrix cell. So this is a deterministic *intra-cell
PROXY* for fragility: signals that, when present in a single screenshot, predict
a layout that breaks across the device x content matrix.

Three honest signals, all read from the rendered pixels (`ctx.content_mask`,
which is already trimmed of OS chrome via STATUS_BAR_FRAC / HOME_INDICATOR_FRAC):

  (a) chrome bleed - content jammed against the chrome boundary. We measure
      occupancy in a thin band at the very top and very bottom of the content
      region. content_mask already excludes the status bar / home indicator, so
      heavy occupancy in these boundary bands means content is pressed right up
      to the chrome edge (a clip/overlap risk on notched / home-indicator
      devices). Near-zero occupancy is safe.
  (b) edge safety - content not jammed against the extreme left/right screen
      edges. Heavy occupancy in the outermost columns means missing side
      margins, which clips on rounded corners and varies by device width.
  (c) suspicious full-width rows - a row at ~100% occupancy spanning the full
      width is usually an unexpected overflow / error bar rather than intended
      content. A few solid rows (hairline dividers) are fine; a thick solid band
      is a fragility smell.

Everything is a saturating penalty: safe layouts score ~100, clear pathologies
fall toward 0. No randomness, pure function of the pixels.
"""

from __future__ import annotations

import numpy as np

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "layout_robustness"
CATEGORY = "E"
WEIGHT = 0.05

# Boundary band thickness (fraction of the content region) used to detect
# content bleeding into chrome / jammed against screen edges.
BAND_FRAC = 0.025
# Occupancy tolerated in a boundary band before it counts as "bleed". A header
# or composer legitimately touches the boundary a little; only heavy, near-full
# occupancy signals an actual clip/overlap risk.
BLEED_TOL = 0.50
EDGE_TOL = 0.50
# A row counts as "full width" (overflow-bar smell) above this occupancy.
FULL_ROW_THRESH = 0.97
# Fraction of rows that may be solid full-width before it's suspicious, and the
# fraction at which it's clearly a bar (score floors to 0).
SUSP_TOL = 0.005
SUSP_CEIL = 0.05


def _saturating(value: float, tol: float, ceiling: float) -> float:
    """100 at/below `tol`, 0 at/above `ceiling`, linear in between."""
    if value <= tol:
        return 100.0
    if value >= ceiling:
        return 0.0
    return 100.0 * (ceiling - value) / (ceiling - tol)


def score(ctx: Context) -> CategoryScore:
    mask = ctx.content_mask
    if mask is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot")

    h, w = mask.shape
    band_h = max(1, int(h * BAND_FRAC))
    band_w = max(1, int(w * BAND_FRAC))

    # (a) chrome bleed: occupancy in the top / bottom boundary bands.
    bleed_top = float(mask[:band_h].mean())
    bleed_bottom = float(mask[-band_h:].mean())
    bleed_top_score = _saturating(bleed_top, BLEED_TOL, 1.0)
    bleed_bottom_score = _saturating(bleed_bottom, BLEED_TOL, 1.0)

    # (b) edge safety: worst (most occupied) of the extreme left/right columns.
    edge_left = float(mask[:, :band_w].mean())
    edge_right = float(mask[:, -band_w:].mean())
    edge_worst = max(edge_left, edge_right)
    edge_safety = _saturating(edge_worst, EDGE_TOL, 1.0)

    # (c) suspicious full-width rows: count solid, full-width rows and grade the
    # fraction of the content height they cover.
    row_occ = mask.mean(axis=1)
    susp_rows = int((row_occ > FULL_ROW_THRESH).sum())
    susp_frac = susp_rows / h
    susp_score = _saturating(susp_frac, SUSP_TOL, SUSP_CEIL)

    value = (0.28 * bleed_top_score
             + 0.28 * bleed_bottom_score
             + 0.24 * edge_safety
             + 0.20 * susp_score)
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            # Raw "bleed" occupancy fractions (lower = safer).
            "chrome_bleed_top": round(bleed_top, 4),
            "chrome_bleed_bottom": round(bleed_bottom, 4),
            # Edge-margin safety sub-score, 0..100 (higher = safer).
            "edge_safety": round(edge_safety, 2),
            # suspND: count of suspicious near-full-width rows (overflow-bar smell).
            "suspND": susp_rows,
            # Sub-scores behind the blend, for regression diffs.
            "bleed_top_score": round(bleed_top_score, 2),
            "bleed_bottom_score": round(bleed_bottom_score, 2),
            "edge_worst_occ": round(edge_worst, 4),
            "susp_score": round(susp_score, 2),
        },
    )
