"""B. touch_targets - interactive controls >= 44x44pt with adequate spacing.

Apple's HIG asks for >= 44x44pt tap targets spaced so adjacent controls don't
collide. This scorer measures that two ways and combines them:

  1. PIXEL evidence (source of truth): detect compact, non-background blobs in
     the header band and the composer band of the rendered screenshot. Those
     blobs are the real tappable controls (send/interrupt button, the header
     "more" button, the status pill). Each blob's pixel size is converted to
     points via ctx.scale and graded against the 44pt minimum; bbox gaps are
     graded against the 8pt spacing minimum.

  2. SOURCE evidence (corroboration): parse the SwiftUI source for explicit
     `.frame(width:height:)` / `.frame(width:)` / `.frame(height:)` modifiers
     applied to icon Buttons and flag any whose binding dimension is < 44pt.

The two are blended so the value tracks the real layout but is robustly
hill-climbable: bumping a 40pt button to 44pt raises both the pixel and source
components. Everything here is deterministic and pure.
"""

from __future__ import annotations

import re

import numpy as np

from reward.context import TOKENS, hex_to_rgb, Context
from reward.types import CategoryScore, make_unavailable

NAME = "touch_targets"
CATEGORY = "B"
WEIGHT = 0.1

# Apple HIG ergonomics constants.
MIN_TARGET_PT = 44.0
MIN_SPACING_PT = 8.0

# Vertical bands (fractions of the full screenshot height) where the chrome
# controls live. The header sits just below the OS status bar; the composer
# hugs the bottom above the home indicator. Bounds are intentionally generous.
HEADER_BAND = (0.070, 0.145)
COMPOSER_BAND = (0.875, 0.965)

# A "compact control" blob is neither a hairline glyph nor a full-width field.
# In points: drop sub-12pt letter fragments and >80pt-wide regions (text field).
MIN_BLOB_PT = 12.0
MAX_BLOB_PT = 80.0

# Background-difference threshold, matched to Context.content_mask so a button
# filled with Theme.surface/surfaceElevated still reads as foreground.
BG_DELTA = 18.0


def _blobs(mask: np.ndarray):
    """4-connected components of a boolean mask via scanline run union-find.

    Returns [(x0, y0, x1, y1, area)] in mask coordinates. Deterministic: the
    output is sorted by (y0, x0). Fast enough for the thin bands we scan.
    """
    h, w = mask.shape
    runs: list[tuple[int, int, int]] = []   # (row, start_col, end_col_inclusive)
    row_runs: list[list[int]] = []          # per row: indices into `runs`
    for y in range(h):
        idx = np.flatnonzero(mask[y])
        these: list[int] = []
        if idx.size:
            breaks = np.flatnonzero(np.diff(idx) > 1)
            starts = np.concatenate(([0], breaks + 1))
            ends = np.concatenate((breaks, [idx.size - 1]))
            for s, e in zip(starts, ends):
                these.append(len(runs))
                runs.append((y, int(idx[s]), int(idx[e])))
        row_runs.append(these)

    parent = list(range(len(runs)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for y in range(1, h):
        for ri in row_runs[y]:
            _, s, e = runs[ri]
            for pj in row_runs[y - 1]:
                _, ps, pe = runs[pj]
                if s <= pe and ps <= e:   # column overlap -> vertically connected
                    union(ri, pj)

    boxes: dict[int, list[int]] = {}
    for ri, (y, s, e) in enumerate(runs):
        r = find(ri)
        if r not in boxes:
            boxes[r] = [s, y, e, y, e - s + 1]
        else:
            b = boxes[r]
            b[0] = min(b[0], s)
            b[1] = min(b[1], y)
            b[2] = max(b[2], e)
            b[3] = max(b[3], y)
            b[4] += e - s + 1
    out = [tuple(v) for v in boxes.values()]
    out.sort(key=lambda b: (b[1], b[0]))
    return out


def _band_controls(mask: np.ndarray, band, scale: float):
    """Detect compact control candidates in one full-image band.

    Returns list of dicts with full-image pixel bbox + point size. y0/y1 are in
    full-image coordinates (band offset added) so spacing can be measured
    across bands.
    """
    h = mask.shape[0]
    y0 = int(h * band[0])
    y1 = int(h * band[1])
    sub = mask[y0:y1]
    controls = []
    for (bx0, by0, bx1, by1, area) in _blobs(sub):
        w_pt = (bx1 - bx0 + 1) / scale
        h_pt = (by1 - by0 + 1) / scale
        min_pt = min(w_pt, h_pt)
        max_pt = max(w_pt, h_pt)
        # Keep compact, roughly button/pill-sized blobs; drop glyph fragments
        # and the full-width text field.
        if min_pt < MIN_BLOB_PT or max_pt > MAX_BLOB_PT:
            continue
        controls.append({
            "x0": bx0, "y0": by0 + y0, "x1": bx1, "y1": by1 + y0,
            "w_pt": round(w_pt, 1), "h_pt": round(h_pt, 1),
            "min_pt": round(min_pt, 1),
        })
    return controls


def _rect_gap_pt(a: dict, b: dict, scale: float) -> float:
    """Minimum edge-to-edge gap between two bboxes, in points (0 if touching)."""
    dx = max(a["x0"] - b["x1"], b["x0"] - a["x1"], 0)
    dy = max(a["y0"] - b["y1"], b["y0"] - a["y1"], 0)
    return float(np.hypot(dx, dy)) / scale


_FRAME_WH = re.compile(r"\.frame\(\s*width:\s*(\d+)\s*,\s*height:\s*(\d+)")
_FRAME_W = re.compile(r"\.frame\(\s*width:\s*(\d+)\s*\)")
_FRAME_H = re.compile(r"\.frame\(\s*height:\s*(\d+)\s*\)")


def _interactive_frame_dims(files: dict[str, str]) -> list[dict]:
    """Find frame sizes applied to icon Buttons (Image(systemName:) controls).

    A frame is "interactive" if, scanning a few lines up the modifier chain, we
    hit an `Image(systemName:` before a decorative `Circle(`/`Text(`. This keeps
    the 40x40 send/interrupt icons and rejects the 8x8 status dots that happen
    to live inside a Button row.
    """
    out = []
    for path, text in sorted(files.items()):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = _FRAME_WH.search(line)
            if m:
                dims = (int(m.group(1)), int(m.group(2)))
            else:
                mw = _FRAME_W.search(line)
                mh = _FRAME_H.search(line)
                if mw:
                    dims = (int(mw.group(1)),)
                elif mh:
                    dims = (int(mh.group(1)),)
                else:
                    continue
            # Walk back up the chain to classify the leaf view.
            interactive = False
            for j in range(i, max(-1, i - 8), -1):
                up = lines[j]
                if "Image(systemName" in up:
                    interactive = True
                    break
                if "Circle(" in up or "Text(" in up or "Rectangle(" in up:
                    break
            if not interactive:
                continue
            binding = min(dims)  # the dimension that constrains the tap target
            out.append({"file": path, "line": i + 1,
                        "dims_pt": list(dims), "binding_pt": binding})
    return out


def score(ctx: Context) -> CategoryScore:
    scale = float(max(1, ctx.scale))
    components: list[tuple[float, float]] = []   # (score, weight)
    evidence: dict = {}

    # --- pixel evidence ---------------------------------------------------
    arr = ctx.pixels
    pixel_controls: list[dict] = []
    if arr is not None:
        bg = hex_to_rgb(TOKENS["background"])
        mask = np.linalg.norm(arr - bg, axis=2) > BG_DELTA
        pixel_controls = (_band_controls(mask, HEADER_BAND, scale)
                          + _band_controls(mask, COMPOSER_BAND, scale))

    if pixel_controls:
        # Graded size: full credit only at >= 44pt, linear below (climbable).
        size_quality = float(np.mean([min(1.0, c["min_pt"] / MIN_TARGET_PT)
                                      for c in pixel_controls]))
        n_ok = sum(1 for c in pixel_controls if c["min_pt"] >= MIN_TARGET_PT)
        compliance = n_ok / len(pixel_controls)
        pixel_score = 100.0 * (0.6 * size_quality + 0.4 * compliance)
        components.append((pixel_score, 0.45))
        evidence["pixel_controls"] = len(pixel_controls)
        evidence["pixel_controls_ge_44"] = n_ok
        evidence["pixel_min_pt"] = round(min(c["min_pt"] for c in pixel_controls), 1)
        evidence["pixel_size_quality"] = round(size_quality, 3)
        evidence["pixel_sizes_pt"] = [[c["w_pt"], c["h_pt"]] for c in pixel_controls]

        # Spacing: only adjacent pairs (gap below one target width) can collide.
        violations = 0
        pairs = 0
        worst = None
        n = len(pixel_controls)
        for i in range(n):
            for k in range(i + 1, n):
                gap = _rect_gap_pt(pixel_controls[i], pixel_controls[k], scale)
                if gap < MIN_TARGET_PT:          # neighbours, not far-apart bands
                    pairs += 1
                    worst = gap if worst is None else min(worst, gap)
                    if 0.0 < gap < MIN_SPACING_PT:
                        violations += 1
        if pairs:
            spacing_score = 100.0 * (1.0 - violations / pairs)
            components.append((spacing_score, 0.25))
            evidence["adjacent_pairs"] = pairs
            evidence["spacing_violations"] = violations
            evidence["min_gap_pt"] = round(worst, 1) if worst is not None else None

    # --- source evidence --------------------------------------------------
    frames = _interactive_frame_dims(ctx.source_files)
    if frames:
        bindings = [f["binding_pt"] for f in frames]
        src_quality = float(np.mean([min(1.0, b / MIN_TARGET_PT) for b in bindings]))
        n_ok = sum(1 for b in bindings if b >= MIN_TARGET_PT)
        compliance = n_ok / len(bindings)
        src_score = 100.0 * (0.6 * src_quality + 0.4 * compliance)
        components.append((src_score, 0.30))
        evidence["source_icon_frames"] = len(frames)
        evidence["source_frames_ge_44"] = n_ok
        evidence["source_undersized"] = [
            {"file": f["file"], "line": f["line"], "binding_pt": f["binding_pt"]}
            for f in frames if f["binding_pt"] < MIN_TARGET_PT
        ]

    if not components:
        return make_unavailable(NAME, CATEGORY, WEIGHT,
                                "no detectable controls in pixels or source")

    wsum = sum(w for _, w in components)
    value = sum(s * w for s, w in components) / wsum
    value = max(0.0, min(100.0, value))
    evidence["min_target_pt"] = MIN_TARGET_PT
    evidence["min_spacing_pt"] = MIN_SPACING_PT

    return CategoryScore(name=NAME, category=CATEGORY, weight=WEIGHT,
                         value=round(value, 2), evidence=evidence)
