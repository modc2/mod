"""Scorer: F. ai_patterns - anti "AI-slop" (higher score = LESS slop).

This is the design-authenticity scorer that penalizes the documented, repeatable
tells of AI-generated UI catalogued in `reward/AI_SLOP_RESEARCH.md`. It starts at
100 (a perfectly un-slopped UI) and subtracts a bounded penalty per tell, so a
crafted app keeps its score and a generated-looking one bleeds points.

Tells detected (each cites AI_SLOP_RESEARCH.md "The AI-slop tells"):

SOURCE (primary; scans every .swift under ctx.source_root incl. Theme.swift):
  * slop palette hexes - purple/indigo (#667eea #764ba2 #8b5cf6 #A855F7 #6366F1
    #7C3AED #818CF8) and neon cyan-on-dark (#38BDF8 #22D3EE). Detected from
    Color(hex: 0x..) AND Color(red:green:blue:) by classifying hue+saturation,
    so a renamed copy is still caught. (research: "Color / theme")
  * gradients - LinearGradient/RadialGradient/AngularGradient/.gradient, and
    ESPECIALLY a gradient applied to text (foregroundStyle(...Gradient) or
    .overlay(Gradient).mask(Text)), the "gradient text for impact" tell.
  * glassmorphism/blur overuse - .ultraThinMaterial/.regularMaterial/
    .thinMaterial/.blur(...) sprinkled on many surfaces. ("Material / depth")
  * generic fonts - "Inter"/"Roboto"/"Arial"/"Open Sans"/"Lato"/"Space Grotesk"
    inside .font(.custom(...)). ("Typography")
  * emoji-as-UI - emoji codepoints inside SwiftUI Text("...") literals.
    ("Content: Emoji used as UI iconography")
  * uniform 0.1-opacity shadows applied broadly + oversized uniform corner
    radius (cornerRadius >= 24 on everything). ("Material / depth")
  * decorative badge proliferation - many "Live"/"New"/"Beta" pills. A SINGLE
    small functional status pill (our app's one "live" connection pill) is
    acceptable and is NOT penalized; only proliferation is.

PIXEL (corroboration; ctx.content_pixels): fraction of content pixels whose hue
  falls in the purple/indigo or neon-cyan range, plus the presence of large
  smooth multi-stop color ramps (gradient regions). Small penalty.

FAIRNESS: our app uses mint #4DD9A6 (hue ~158, a green-teal, NOT neon cyan and
NOT purple), solid tokenized dark surfaces, mono fonts, SF Symbols (not emoji),
and exactly one functional status pill -> every counter is 0 -> score 100.
"""

from __future__ import annotations

import re

import numpy as np

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "ai_patterns"
CATEGORY = "F"
WEIGHT = 0.04

# --- slop palette ----------------------------------------------------------
# Canonical AI-slop hexes from AI_SLOP_RESEARCH.md (Tailwind indigo-500 family
# and the neon cyan-on-dark accents). Matched literally as a backstop, in
# addition to the hue classifier below.
_SLOP_HEXES = {
    "667eea", "764ba2", "8b5cf6", "a855f7", "6366f1", "7c3aed", "818cf8",  # purple/indigo
    "38bdf8", "22d3ee",                                                    # neon cyan
}
# Hue bands (degrees) for the hue-based classifier. Purple/indigo spans the
# canonical hexes (~229-271deg); neon cyan spans ~185-205deg. Mint #4DD9A6 sits
# at ~158deg (green-teal) and is deliberately OUTSIDE both bands.
_PURPLE_BAND = (225.0, 295.0)
_CYAN_BAND = (180.0, 205.0)
# A slop accent is vivid: a near-neutral dark surface (e.g. #0F0F14, which has a
# nominal blue hue) must NOT count, so require real saturation + brightness.
_SRC_MIN_SAT = 0.40
_SRC_MIN_VAL = 0.30

# Generic "AI escape-hatch" font families (AI_SLOP_RESEARCH.md "Typography").
_GENERIC_FONTS = {"inter", "roboto", "arial", "open sans", "lato", "space grotesk"}

# Decorative badge words. "live" is intentionally included because our app uses
# a single functional "live" pill; one is allowed (see _BADGE_FREE) and only
# proliferation is penalized.
_BADGE_WORDS = {"live", "new", "beta", "pro", "hot", "sale", "free", "premium", "trending"}

# --- regexes (deterministic, compiled once) --------------------------------
_HEX_RE = re.compile(r"(?:0x|#)([0-9A-Fa-f]{6})\b")
_RGB_RE = re.compile(
    r"Color\(\s*(?:\.sRGB[^,]*,\s*)?red:\s*([0-9.]+)\s*,\s*green:\s*([0-9.]+)\s*,\s*blue:\s*([0-9.]+)"
)
_GRADIENT_RE = re.compile(r"\b(?:Linear|Radial|Angular|Mesh|Elliptical)Gradient\b|\.gradient\b")
_MATERIAL_RE = re.compile(
    r"\.(?:ultraThin|thin|regular|thick|ultraThick)Material\b|\.blur\s*\(",
)
_FONT_CUSTOM_RE = re.compile(r"\.custom\(\s*\"([^\"]+)\"")
_TEXT_LITERAL_RE = re.compile(r"\bText\(\s*\"((?:[^\"\\]|\\.)*)\"")
_SHADOW_RE = re.compile(r"\.shadow\s*\(")
_CORNER_RE = re.compile(r"cornerRadius:\s*([0-9]+(?:\.[0-9]+)?)")
_GRADIENT_TOKEN_RE = re.compile(r"Gradient")
# Emoji codepoints (pictographs, symbols, dingbats, flags, variation selectors).
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U00002300-\U000023FF]"
)

# --- penalty schedule (per-tell weight, free allowance, hard cap) ----------
# Purple/cyan palette and gradient-on-text are the loudest slop signals, so they
# carry the heaviest per-hit weights and caps.
P_HEX = (12.0, 0, 40.0)          # purple/indigo or neon-cyan accent
P_GRAD = (8.0, 0, 24.0)          # any gradient
P_GRAD_TEXT = (20.0, 0, 40.0)    # gradient applied to text (heaviest)
P_MATERIAL = (6.0, 2, 24.0)      # blur/material: a couple is fine, many = slop
P_FONT = (10.0, 0, 30.0)         # generic .custom() font
P_EMOJI = (8.0, 0, 24.0)         # emoji-as-UI
P_SHADOW = (5.0, 1, 15.0)        # uniform ~0.1-opacity shadows applied broadly
P_RADIUS = (4.0, 2, 16.0)        # oversized uniform corner radius (>= 24)
P_BADGE = (6.0, 1, 18.0)         # decorative badge proliferation (1 pill is ok)
P_PIXEL_CAP = 12.0               # corroborating pixel evidence cap


def _penalty(count: int, schedule: tuple[float, int, float]) -> float:
    """count over the free allowance, times per-hit weight, clamped to the cap."""
    per, free, cap = schedule
    return min(cap, max(0, count - free) * per)


def _rgb_to_hsv(r: float, g: float, b: float) -> tuple[float, float, float]:
    """RGB in 0..255 -> (hue degrees, saturation 0..1, value 0..1)."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    delta = mx - mn
    if delta < 1e-9:
        hue = 0.0
    elif mx == rf:
        hue = 60.0 * (((gf - bf) / delta) % 6.0)
    elif mx == gf:
        hue = 60.0 * (((bf - rf) / delta) + 2.0)
    else:
        hue = 60.0 * (((rf - gf) / delta) + 4.0)
    sat = 0.0 if mx <= 1e-9 else delta / mx
    return hue % 360.0, sat, mx


def _is_slop_color(r: float, g: float, b: float) -> bool:
    """A vivid purple/indigo or neon-cyan accent (mint #4DD9A6 fails this)."""
    hue, sat, val = _rgb_to_hsv(r, g, b)
    if sat < _SRC_MIN_SAT or val < _SRC_MIN_VAL:
        return False  # near-neutral dark surface, not an accent
    if _PURPLE_BAND[0] <= hue <= _PURPLE_BAND[1]:
        return True
    if _CYAN_BAND[0] <= hue <= _CYAN_BAND[1]:
        return True
    return False


# --- source scanning -------------------------------------------------------
def _scan_source(files: dict[str, str]) -> dict[str, int]:
    slop_hex = grad = grad_text = material = font = emoji = shadow = radius = badge = 0

    for text in files.values():
        # slop palette: explicit hex literals.
        for m in _HEX_RE.finditer(text):
            h = m.group(1).lower()
            if h in _SLOP_HEXES:
                slop_hex += 1
                continue
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            if _is_slop_color(r, g, b):
                slop_hex += 1
        # slop palette: Color(red:green:blue:) component literals.
        for m in _RGB_RE.finditer(text):
            vals = [float(x) for x in m.groups()]
            if all(v <= 1.0 for v in vals):  # 0..1 fractions
                vals = [v * 255.0 for v in vals]
            if _is_slop_color(*vals):
                slop_hex += 1

        # gradients (any) and the gradient-on-text special case.
        grad += len(_GRADIENT_RE.findall(text))
        for m in _GRADIENT_TOKEN_RE.finditer(text):
            window = text[max(0, m.start() - 220): m.end() + 220]
            if ("foregroundStyle" in window or "foregroundColor" in window
                    or ".mask(" in window or ".mask {" in window):
                grad_text += 1

        # glassmorphism / blur overuse.
        material += len(_MATERIAL_RE.findall(text))

        # generic fonts inside .font(.custom("...")).
        for m in _FONT_CUSTOM_RE.finditer(text):
            if m.group(1).strip().lower() in _GENERIC_FONTS:
                font += 1

        # emoji inside Text("...") literals only (not comments / symbols).
        for m in _TEXT_LITERAL_RE.finditer(text):
            if _EMOJI_RE.search(m.group(1)):
                emoji += 1

        # uniform ~0.1-opacity shadows applied broadly.
        for m in _SHADOW_RE.finditer(text):
            window = text[m.start(): m.end() + 120]
            if re.search(r"0\.0?[5-9]\d*|0\.1\d*|0\.2\b", window):
                shadow += 1

        # oversized uniform corner radius (>= 24).
        for m in _CORNER_RE.finditer(text):
            if float(m.group(1)) >= 24.0:
                radius += 1

        # decorative badge words used as Text("...") pills.
        for m in _TEXT_LITERAL_RE.finditer(text):
            if m.group(1).strip().lower() in _BADGE_WORDS:
                badge += 1

    return {
        "slop_hex_hits": slop_hex,
        "gradient_hits": grad,
        "gradient_on_text_hits": grad_text,
        "material_blur_hits": material,
        "generic_font_hits": font,
        "emoji_hits": emoji,
        "uniform_shadow_hits": shadow,
        "oversized_radius_hits": radius,
        "badge_count": badge,
    }


# --- pixel corroboration ---------------------------------------------------
def _purple_pixel_frac(content: np.ndarray, mask: np.ndarray) -> float:
    """Fraction of vivid content pixels in the purple/indigo or neon-cyan band."""
    px = content[mask]
    if px.shape[0] == 0:
        return 0.0
    r, g, b = px[:, 0] / 255.0, px[:, 1] / 255.0, px[:, 2] / 255.0
    mx = np.maximum.reduce([r, g, b])
    mn = np.minimum.reduce([r, g, b])
    delta = mx - mn
    nz = delta > 1e-6
    hue = np.zeros_like(mx)
    rmax = (mx == r) & nz
    gmax = (mx == g) & nz & ~rmax
    bmax = (mx == b) & nz & ~rmax & ~gmax
    hue[rmax] = ((g[rmax] - b[rmax]) / delta[rmax]) % 6.0
    hue[gmax] = ((b[gmax] - r[gmax]) / delta[gmax]) + 2.0
    hue[bmax] = ((r[bmax] - g[bmax]) / delta[bmax]) + 4.0
    hue *= 60.0
    safe_mx = np.where(mx > 1e-9, mx, 1.0)
    sat = np.where(mx > 1e-9, delta / safe_mx, 0.0)
    vivid = (sat >= 0.40) & (mx >= 0.30)
    in_purple = (hue >= _PURPLE_BAND[0]) & (hue <= _PURPLE_BAND[1])
    in_cyan = (hue >= _CYAN_BAND[0]) & (hue <= _CYAN_BAND[1])
    return float((vivid & (in_purple | in_cyan)).mean())


def _gradient_region_frac(content: np.ndarray) -> float:
    """Fraction of a coarse block grid covered by smooth multi-stop color ramps.

    A gradient region is a run of adjacent blocks whose mean color changes by a
    small, steady step in a consistent direction across many blocks (a smooth
    ramp), accumulating a large total color distance. Solid tokenized surfaces
    (zero step) and hard edges (large jumps) do not qualify, so our flat app
    reads ~0.
    """
    h, w, _ = content.shape
    bs = max(16, min(h, w) // 24)  # block size
    gh, gw = h // bs, w // bs
    if gh < 6 or gw < 6:
        return 0.0
    grid = content[: gh * bs, : gw * bs].reshape(gh, bs, gw, bs, 3).mean(axis=(1, 3))

    covered = np.zeros((gh, gw), dtype=bool)

    def mark_runs(line: np.ndarray, paint) -> None:
        """Flag maximal runs of >=6 blocks of small, steady step that together
        span a large total color distance (a smooth multi-stop ramp).

        Decorative AI-slop gradients are *colorful* (purple->blue); a grayscale
        brightness ramp (e.g. anti-aliased mono text over a dark panel) is not a
        slop gradient, so require real chroma somewhere along the run."""
        n = len(line)
        chroma = line.max(axis=1) - line.min(axis=1)  # per-block max-min
        run_start = 0
        for i in range(1, n + 1):
            smooth = (i < n) and (2.0 < float(np.linalg.norm(line[i] - line[i - 1])) < 30.0)
            if not smooth:  # run ends at i-1
                a, b = run_start, i - 1
                if (b - a >= 5
                        and float(np.linalg.norm(line[b] - line[a])) >= 40.0
                        and float(chroma[a:b + 1].max()) >= 30.0):
                    paint(a, b)
                run_start = i

    for y in range(gh):
        mark_runs(grid[y], lambda a, b, y=y: covered.__setitem__((y, slice(a, b + 1)), True))
    for x in range(gw):
        mark_runs(grid[:, x], lambda a, b, x=x: covered.__setitem__((slice(a, b + 1), x), True))

    return float(covered.mean())


def score(ctx: Context) -> CategoryScore:
    files = ctx.source_files
    content = ctx.content_pixels
    have_source = bool(files)
    have_pixels = content is not None

    # make_unavailable only if BOTH source and screenshot are unavailable.
    if not have_source and not have_pixels:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no source and no screenshot")

    ev: dict = {}

    # --- source tells (primary) ---
    if have_source:
        src = _scan_source(files)
    else:
        src = {
            "slop_hex_hits": 0, "gradient_hits": 0, "gradient_on_text_hits": 0,
            "material_blur_hits": 0, "generic_font_hits": 0, "emoji_hits": 0,
            "uniform_shadow_hits": 0, "oversized_radius_hits": 0, "badge_count": 0,
        }
    ev.update(src)

    source_penalty = (
        _penalty(src["slop_hex_hits"], P_HEX)
        + _penalty(src["gradient_hits"], P_GRAD)
        + _penalty(src["gradient_on_text_hits"], P_GRAD_TEXT)
        + _penalty(src["material_blur_hits"], P_MATERIAL)
        + _penalty(src["generic_font_hits"], P_FONT)
        + _penalty(src["emoji_hits"], P_EMOJI)
        + _penalty(src["uniform_shadow_hits"], P_SHADOW)
        + _penalty(src["oversized_radius_hits"], P_RADIUS)
        + _penalty(src["badge_count"], P_BADGE)
    )

    # --- pixel corroboration (secondary, small) ---
    purple_frac = 0.0
    grad_frac = 0.0
    if have_pixels:
        mask = ctx.content_mask
        if mask is not None:
            purple_frac = _purple_pixel_frac(content, mask)
        grad_frac = _gradient_region_frac(content)
    ev["purple_pixel_frac"] = round(purple_frac, 5)
    ev["gradient_region_frac"] = round(grad_frac, 5)

    # >2% vivid purple/cyan content saturates the 8pt purple penalty; a sizeable
    # smooth ramp saturates the 4pt gradient-region penalty.
    pixel_penalty = min(P_PIXEL_CAP, min(8.0, purple_frac / 0.02 * 8.0)
                        + min(4.0, grad_frac / 0.08 * 4.0))

    value = max(0.0, min(100.0, 100.0 - source_penalty - pixel_penalty))

    ev["source_penalty"] = round(source_penalty, 2)
    ev["pixel_penalty"] = round(pixel_penalty, 2)
    ev["source_available"] = have_source
    ev["pixel_available"] = have_pixels

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT,
        value=round(value, 2), evidence=ev,
    )
