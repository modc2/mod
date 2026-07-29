"""B. reachability - is the PRIMARY action in the comfortable thumb zone?

On a phone held one-handed, the thumb comfortably reaches the bottom of the
screen; the top corners are the hardest to hit. The primary action in jcode is
the composer send button, which should live in the bottom-right thumb zone, not
stranded at the top.

This scorer uses the rendered content_mask to locate the salient interactive
cluster nearest the bottom-right corner, then scores its vertical position:

  * full credit when the primary action sits in the bottom ~25% of the screen
    (the comfortable zone),
  * a smooth ramp through the mid-screen,
  * heavy penalty if the primary action is up in the top third.

A small right-bias bonus rewards the conventional bottom-right placement for
right-thumb reach. Pure + deterministic: same screenshot -> same score.
"""

from __future__ import annotations

import numpy as np

from reward.context import Context, HOME_INDICATOR_FRAC, STATUS_BAR_FRAC
from reward.types import CategoryScore, make_unavailable

NAME = "reachability"
CATEGORY = "B"
WEIGHT = 0.08

# Thumb-zone band, as a fraction of the *content* height (chrome trimmed). The
# bottom 25% is the comfortable reach; below ~0.45 reachability degrades.
THUMB_ZONE_TOP = 0.75      # content-fraction where the comfortable zone starts
COMFORT_FLOOR = 0.45       # below this fraction the score starts ramping down

# Detect controls in the composer region (bottom of content). A control is a
# compact, dense column cluster: we scan the bottom band for the rightmost
# salient blob, which is the send button.
COMPOSER_SCAN_FRAC = 0.18  # bottom 18% of content is the composer search area


def score(ctx: Context) -> CategoryScore:
    mask = ctx.content_mask
    if mask is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot")

    ch, cw = mask.shape

    # Search the bottom band for the primary action. The send button is the
    # rightmost dense vertical cluster there. Work in content coordinates.
    band_top = int(ch * (1.0 - COMPOSER_SCAN_FRAC))
    band = mask[band_top:]
    if band.size == 0 or not band.any():
        return make_unavailable(NAME, CATEGORY, WEIGHT,
                                "no content in composer band")

    # Column occupancy in the band; the send button is a tall, narrow cluster
    # on the right. Take the rightmost contiguous run of well-occupied columns.
    col_occ = band.mean(axis=0)
    occupied = col_occ > 0.15
    # rightmost run of occupied columns
    x1 = None
    for x in range(cw - 1, -1, -1):
        if occupied[x]:
            x1 = x
            break
    if x1 is None:
        # fall back to overall right-side bias of content in the band
        x1 = cw - 1
    x0 = x1
    while x0 > 0 and occupied[x0 - 1]:
        x0 -= 1

    # Vertical extent of that cluster within the band -> its center y.
    sub = band[:, x0:x1 + 1]
    rows = np.flatnonzero(sub.any(axis=1))
    if rows.size:
        cy_band = float(rows.mean())
    else:
        cy_band = band.shape[0] / 2.0
    cy_content = band_top + cy_band
    primary_y_frac_content = cy_content / ch

    # Convert to a full-screen fraction for human-readable evidence (account
    # for the trimmed status bar / home indicator).
    visible = 1.0 - STATUS_BAR_FRAC - HOME_INDICATOR_FRAC
    primary_y_frac_screen = STATUS_BAR_FRAC + primary_y_frac_content * visible

    primary_x_frac = (x0 + x1) / 2.0 / cw
    in_thumb_zone = primary_y_frac_content >= THUMB_ZONE_TOP

    # Vertical score: 100 in the comfortable zone, ramp down to 0 toward the
    # top. Below COMFORT_FLOOR scales linearly to 0 at the very top.
    if primary_y_frac_content >= THUMB_ZONE_TOP:
        vscore = 100.0
    elif primary_y_frac_content >= COMFORT_FLOOR:
        # linear from 70 (at floor) to 100 (at thumb-zone top)
        t = (primary_y_frac_content - COMFORT_FLOOR) / (THUMB_ZONE_TOP - COMFORT_FLOOR)
        vscore = 70.0 + 30.0 * t
    else:
        # primary action stranded high: 0 at top -> 70 at the comfort floor
        t = primary_y_frac_content / COMFORT_FLOOR
        vscore = 70.0 * t

    # Horizontal bonus: bottom-right is the canonical right-thumb sweet spot.
    # Small (+/-) nudge so layout that keeps send on the right edges higher.
    hbonus = 100.0 * min(1.0, primary_x_frac / 0.85)

    value = 0.85 * vscore + 0.15 * hbonus
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "primary_action_y_frac": round(primary_y_frac_screen, 4),
            "primary_action_y_frac_content": round(primary_y_frac_content, 4),
            "primary_action_x_frac": round(primary_x_frac, 4),
            "in_thumb_zone": bool(in_thumb_zone),
            "thumb_zone_top_frac": THUMB_ZONE_TOP,
            "vertical_score": round(vscore, 2),
        },
    )
