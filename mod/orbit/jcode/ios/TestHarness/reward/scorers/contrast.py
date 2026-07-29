"""Scorer: D. contrast.

Grades WCAG text/background contrast on the *rendered* content. We isolate the
text-ish foreground (pixels whose contrast vs the app background already exceeds
a weak 2:1, which excludes the near-background card surfaces and anti-aliased
fuzz) and read two bands: the brightest decile (primary copy / accents) and a
dim lower-mid band (the secondary / tertiary label tier). Both are scored on the
WCAG ramp (1:1 -> 0, >=7:1 -> 100 AAA) and a `below_AA` flag fires when a major
text band drops under 4.5:1. relative_luminance + contrast_ratio are implemented
here (sRGB linearization) so the scorer never trusts a view model.
"""

from __future__ import annotations

import numpy as np

from reward.context import Context, TOKENS, hex_to_rgb
from reward.types import CategoryScore, make_unavailable

NAME = "contrast"
CATEGORY = "D"
WEIGHT = 0.14

# WCAG thresholds.
AA = 4.5
AAA = 7.0
# Pixels at least this far above background contrast count as "text-ish"; this
# discards the surface/elevated card fills (~1.2:1) and pure background noise.
TEXT_MIN_RATIO = 2.0
MIN_TEXT_PX = 200  # below this there is no measurable text on the cell


def _relative_luminance(rgb: np.ndarray) -> np.ndarray:
    """sRGB relative luminance per WCAG 2.x. Accepts (...,3) in 0..255."""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def _contrast_ratio(l1: float, l2: float) -> float:
    """WCAG contrast ratio between two relative luminances."""
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _ramp(ratio: float) -> float:
    """Map a contrast ratio onto 0..100: 1:1 -> 0, >=7:1 (AAA) -> 100."""
    return max(0.0, min(100.0, (ratio - 1.0) / (AAA - 1.0) * 100.0))


def score(ctx: Context) -> CategoryScore:
    arr = ctx.content_pixels
    if arr is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot")

    flat = arr.reshape(-1, 3)
    bg = hex_to_rgb(TOKENS["background"])
    bg_lum = float(_relative_luminance(bg))

    lum = _relative_luminance(flat)
    ratio = (np.maximum(lum, bg_lum) + 0.05) / (np.minimum(lum, bg_lum) + 0.05)

    fg = ratio >= TEXT_MIN_RATIO
    n_text = int(fg.sum())
    if n_text < MIN_TEXT_PX:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no measurable text")

    fg_px = flat[fg]
    fg_lum = lum[fg]
    fg_ratio = ratio[fg]

    # Primary band: brightest decile of text-ish pixels (titles, body, accents).
    p90 = np.percentile(fg_lum, 90)
    primary_rgb = fg_px[fg_lum >= p90].mean(axis=0)
    primary_contrast = _contrast_ratio(float(_relative_luminance(primary_rgb)), bg_lum)

    # Secondary band: the dim lower-mid slice of text-ish pixels, i.e. the
    # secondary/tertiary label tier when it is distinct from the bright primary.
    lo, hi = np.percentile(fg_lum, 15), np.percentile(fg_lum, 50)
    band = fg_px[(fg_lum >= lo) & (fg_lum <= hi)]
    secondary_detected = band.shape[0] >= MIN_TEXT_PX // 2
    if secondary_detected:
        secondary_contrast = _contrast_ratio(float(_relative_luminance(band.mean(axis=0))), bg_lum)
    else:
        secondary_contrast = primary_contrast

    # A "major text region" below AA is the secondary band; flag it.
    below_AA = bool(secondary_contrast < AA)

    primary_score = _ramp(primary_contrast)
    secondary_score = _ramp(secondary_contrast)
    value = 0.6 * primary_score + 0.4 * secondary_score
    if below_AA:
        value -= 8.0  # dim copy that fails AA is a real legibility cost
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "primary_contrast": round(primary_contrast, 2),
            "est_secondary_contrast": round(secondary_contrast, 2),
            "below_AA": below_AA,
            "secondary_detected": secondary_detected,
            "text_px": n_text,
        },
    )
