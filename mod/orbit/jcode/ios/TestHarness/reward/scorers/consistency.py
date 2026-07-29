"""Scorer: C. consistency.

Grades design discipline from two angles and blends them into one 0..100:

  PIXEL palette discipline  Quantize the rendered content pixels and count how
                            many dominant colours actually appear (~4-9 is a
                            healthy, intentional palette; far fewer reads as
                            empty, far more reads as noisy). Also count distinct
                            left-margin groups from the leftmost content edge of
                            each row (1-3 is disciplined alignment).

  SOURCE token discipline   Scan ctx.source_files for raw style literals that
                            should have gone through Theme.swift: Color(red:/
                            white:/.sRGB/displayP3) and Color(hex:) used in
                            views, plus .system(size:) fonts outside Theme.swift.
                            Fewer raw hits is better.

Pixel evidence grades the output you can see; source evidence grades the habits
that keep it consistent across future screens. We blend both so neither can be
gamed alone.
"""

from __future__ import annotations

import re

import numpy as np

from reward.context import Context, TOKENS, hex_to_rgb
from reward.types import CategoryScore, make_unavailable

NAME = "consistency"
CATEGORY = "C"
WEIGHT = 0.04

# Raw style literals that bypass the design tokens (mirrors ui_lint intent).
_RAW_COLOR_RE = re.compile(r"Color\(\s*(red|white|\.sRGB|hue|displayP3)", re.IGNORECASE)
_RAW_HEX_RE = re.compile(r"Color\(hex:")
_SYSTEM_FONT_RE = re.compile(r"\.system\(\s*size:")


def _dominant_colors(content: np.ndarray, mask: np.ndarray) -> int:
    """Count quantized colour buckets covering >0.4% of content pixels."""
    px = content[mask]
    if len(px) == 0:
        return 0
    q = (px // 8).astype(np.int64)  # 5 bits/channel
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    _, counts = np.unique(keys, return_counts=True)
    return int((counts > 0.004 * len(px)).sum())


def _margin_groups(mask: np.ndarray, scale: int) -> int:
    """Distinct clusters of per-row leftmost content edges (>2% of rows each)."""
    edges = []
    for row in mask:
        idx = int(np.argmax(row))
        if row[idx]:
            edges.append(idx)
    if not edges:
        return 0
    tol = max(1, int(8 * scale))
    centers = []
    for v in sorted(edges):
        if not centers or v - centers[-1] > tol:
            centers.append(v)
    bucket: dict[int, int] = {}
    for v in edges:
        for center in centers:
            if abs(v - center) <= tol:
                bucket[center] = bucket.get(center, 0) + 1
                break
    return sum(1 for n in bucket.values() if n > 0.02 * len(edges))


def _source_hits(source_files: dict[str, str]) -> tuple[int, int]:
    """Count raw colour + raw font literals outside Theme.swift."""
    color_hits = font_hits = 0
    for path, text in source_files.items():
        is_theme = path.endswith("Theme.swift")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("//"):
                continue
            if not is_theme:
                if _RAW_COLOR_RE.search(line) or _RAW_HEX_RE.search(line):
                    color_hits += 1
                if _SYSTEM_FONT_RE.search(line) and "Theme" not in line:
                    font_hits += 1
    return color_hits, font_hits


def score(ctx: Context) -> CategoryScore:
    mask = ctx.content_mask
    have_pixels = mask is not None
    source_files = ctx.source_files
    have_source = bool(source_files)

    if not have_pixels and not have_source:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot and no source")

    parts = []
    weights = []
    evidence: dict = {}

    # --- PIXEL palette discipline -----------------------------------------
    if have_pixels:
        content = ctx.content_pixels
        dominant = _dominant_colors(content, mask)
        margins = _margin_groups(mask, ctx.scale)
        # 4-9 dominant colours is healthy; drift from 7 in either direction hurts.
        color_score = 100.0 * (1 - min(abs(dominant - 7) / 12.0, 1.0))
        # 1-3 left margins is disciplined; each extra group erodes the score.
        margin_score = 100.0 * (1 - min(max(margins - 2, 0) / 6.0, 1.0))
        pixel_score = 0.5 * color_score + 0.5 * margin_score
        parts.append(pixel_score)
        weights.append(0.6)
        evidence["dominant_colors"] = int(dominant)
        evidence["margin_groups"] = int(margins)

    # --- SOURCE token discipline ------------------------------------------
    if have_source:
        color_hits, font_hits = _source_hits(source_files)
        # Each raw colour costs ~6 pts, each raw font ~4 pts (ui_lint weights).
        penalty = 6 * color_hits + 4 * font_hits
        source_score = max(0.0, 100.0 - penalty)
        parts.append(source_score)
        weights.append(0.4)
        evidence["raw_color_hits"] = int(color_hits)
        evidence["raw_font_hits"] = int(font_hits)

    wsum = sum(weights)
    value = sum(p * w for p, w in zip(parts, weights)) / wsum if wsum > 0 else 0.0
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence=evidence,
    )
