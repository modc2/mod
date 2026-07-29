"""Scorer: C. rhythm.

Grades whether vertical spacing snaps to an 8pt grid (the iOS default rhythm).
From the content mask we find content bands (contiguous runs of occupied rows)
and measure the gaps between consecutive bands. Gaps whose size in points lands
near a multiple of 8 read as deliberate, rhythmic spacing; off-grid gaps read
as ad-hoc and jittery. As a source cross-check we scan ctx.source_files for
spacing/padding literals and count how many fall off the {4,8,12,16,...} grid.

  gap_count            number of inter-band vertical gaps measured (pixels)
  mean_grid_snap       0..1, how close gaps sit to the nearest 8pt multiple
  offgrid_padding_hits source spacing/padding literals not on the grid
"""

from __future__ import annotations

import re

import numpy as np

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "rhythm"
CATEGORY = "C"
WEIGHT = 0.04

_GRID_PT = 8
# Accepted spacing values: the 8pt grid plus the common 4pt half-steps it is
# built from. Anything else is an off-grid literal.
_SPACING_GRID = {0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48, 56, 64}
# Pull the numeric argument out of spacing:/padding literals.
_SPACING_RE = re.compile(r"\bspacing:\s*([0-9]+(?:\.[0-9]+)?)")
_PADDING_RE = re.compile(r"\.padding\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)")
_PADDING_EDGE_RE = re.compile(r"\.padding\(\s*\.[a-zA-Z]+,\s*([0-9]+(?:\.[0-9]+)?)\s*\)")


def _content_bands(row_occ: np.ndarray, thresh: float = 0.01) -> list[tuple[int, int]]:
    bands = []
    start = None
    for i, v in enumerate(row_occ):
        if v >= thresh and start is None:
            start = i
        elif v < thresh and start is not None:
            bands.append((start, i))
            start = None
    if start is not None:
        bands.append((start, len(row_occ)))
    # Drop sub-pixel specks so anti-aliasing noise isn't read as a band.
    return [b for b in bands if b[1] - b[0] > 3]


def _offgrid_padding_hits(source_files: dict[str, str]) -> int:
    hits = 0
    for path, text in source_files.items():
        if path.endswith("Theme.swift"):
            continue
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("//"):
                continue
            for rx in (_SPACING_RE, _PADDING_RE, _PADDING_EDGE_RE):
                for m in rx.finditer(line):
                    val = float(m.group(1))
                    if val not in _SPACING_GRID:
                        hits += 1
    return hits


def score(ctx: Context) -> CategoryScore:
    mask = ctx.content_mask
    source_files = ctx.source_files
    have_pixels = mask is not None
    have_source = bool(source_files)

    if not have_pixels and not have_source:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot and no source")

    parts = []
    weights = []
    evidence: dict = {}

    # --- PIXEL rhythm: inter-band gaps vs the 8pt grid --------------------
    if have_pixels:
        row_occ = mask.mean(axis=1)
        bands = _content_bands(row_occ)
        grid = max(1, int(_GRID_PT * ctx.scale))
        gaps = [bands[i + 1][0] - bands[i][1] for i in range(len(bands) - 1)]
        gaps = [g for g in gaps if g > 0]
        if gaps:
            # Snap = 1 when a gap lands exactly on a grid line, 0 at the worst
            # (half a cell) offset; average across all gaps.
            snaps = []
            for g in gaps:
                off = g % grid
                snaps.append(1.0 - min(off, grid - off) / (grid / 2.0))
            mean_snap = float(max(0.0, np.mean(snaps)))
        else:
            mean_snap = 0.0
        parts.append(100.0 * mean_snap)
        weights.append(0.7)
        evidence["gap_count"] = int(len(gaps))
        evidence["mean_grid_snap"] = round(mean_snap, 4)

    # --- SOURCE cross-check: off-grid padding/spacing literals ------------
    if have_source:
        offgrid = _offgrid_padding_hits(source_files)
        # Each off-grid literal costs 8 pts off a perfect 100.
        source_score = max(0.0, 100.0 - 8.0 * offgrid)
        parts.append(source_score)
        weights.append(0.3)
        evidence["offgrid_padding_hits"] = int(offgrid)

    wsum = sum(weights)
    value = sum(p * w for p, w in zip(parts, weights)) / wsum if wsum > 0 else 0.0
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence=evidence,
    )
