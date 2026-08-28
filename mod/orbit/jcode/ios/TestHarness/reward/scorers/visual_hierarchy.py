"""Scorer: C. visual_hierarchy.

Grades whether the rendered UI has ONE clear focal point. A good chat screen
draws the eye to a single primary thing (the mint send button, the user
bubble, the live badge) instead of scattering equally-loud elements all over
the canvas. We build a per-pixel salience map (distance from the app
background, with a strong boost for accent/mint pixels since those are the
primary actions), pool it into a coarse block grid, then ask two questions:

  focal_count            how many distinct strong salient regions compete?
  salience_concentration what share of total salience lives in the single
                         strongest region?

Reward exactly one dominant region that holds a large share of salience;
penalize either nothing salient (no focal point) or many competing regions
(visual noise spread evenly).

Scenario-aware: the `empty` scenario is a deliberate empty state. With almost
no content, total salience is tiny, so demanding that the focal region hold
half of ALL salience is unfair: the OS status bar and header glyphs alone
dilute it. In empty mode a single focal affordance is the whole job, so the
concentration target is relaxed and the single-focal-point term dominates.
"""

from __future__ import annotations

import math

import numpy as np

from reward.context import Context, TOKENS, hex_to_rgb
from reward.types import CategoryScore, make_unavailable

NAME = "visual_hierarchy"
CATEGORY = "C"
WEIGHT = 0.04

# Scenarios that render a deliberate empty state (no transcript yet).
EMPTY_SCENARIOS = {"empty"}
# Concentration targets: a busy transcript should have a clearly dominant
# region (>=50% of salience); an empty state only needs a modestly dominant
# affordance (>=30%) because chrome glyphs dilute the tiny salience budget.
_CONC_TARGET = 0.5
_CONC_TARGET_EMPTY = 0.3

# Max possible RGB euclidean distance, used to normalize contrast salience.
_MAX_RGB_DIST = math.sqrt(3 * 255.0 * 255.0)
# Accent pixels within this RGB radius of mint are treated as primary-action
# salience and weighted far above plain text contrast.
_ACCENT_RADIUS = 120.0
_ACCENT_WEIGHT = 3.0
# Coarse pooling block size in points; large enough that neighbouring accent
# glyphs merge into one focal region instead of fragmenting.
_BLOCK_PT = 48


def _connected_components(grid: np.ndarray) -> list[list[tuple[int, int]]]:
    """4-connectivity flood fill over a 2D boolean block grid (numpy-only)."""
    rows, cols = grid.shape
    seen = np.zeros_like(grid, dtype=bool)
    comps = []
    for r in range(rows):
        for c in range(cols):
            if not grid[r, c] or seen[r, c]:
                continue
            stack = [(r, c)]
            seen[r, c] = True
            cells = []
            while stack:
                y, x = stack.pop()
                cells.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < rows and 0 <= nx < cols and grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            comps.append(cells)
    return comps


def score(ctx: Context) -> CategoryScore:
    arr = ctx.content_pixels
    if arr is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot")

    ch, cw, _ = arr.shape
    bg = hex_to_rgb(TOKENS["background"])
    mint = hex_to_rgb(TOKENS["mint"])

    # Per-pixel salience: contrast against the background (0..~1) plus a heavy
    # boost for pixels near the mint accent (the primary-action colour).
    dist_bg = np.linalg.norm(arr - bg, axis=2) / _MAX_RGB_DIST
    dist_mint = np.linalg.norm(arr - mint, axis=2)
    accent = np.clip(1.0 - dist_mint / _ACCENT_RADIUS, 0.0, 1.0)
    salience = dist_bg + _ACCENT_WEIGHT * accent

    # Pool into a coarse block grid and sum salience per block.
    block = max(1, int(_BLOCK_PT * ctx.scale))
    rows = max(1, ch // block)
    cols = max(1, cw // block)
    blocks = np.zeros((rows, cols), dtype=np.float64)
    for r in range(rows):
        y0, y1 = r * block, (r + 1) * block if r < rows - 1 else ch
        for c in range(cols):
            x0, x1 = c * block, (c + 1) * block if c < cols - 1 else cw
            blocks[r, c] = salience[y0:y1, x0:x1].sum()

    total = float(blocks.sum())
    peak = float(blocks.max())
    if total <= 1e-9 or peak <= 1e-9:
        # Nothing stands out anywhere: there is no focal point to grade.
        return CategoryScore(
            name=NAME, category=CATEGORY, weight=WEIGHT, value=20.0,
            evidence={"focal_count": 0, "salience_concentration": 0.0},
        )

    # Focal blocks are those at least half as loud as the loudest block; group
    # adjacent ones into regions so a single button isn't counted twice.
    focal = blocks >= 0.5 * peak
    comps = _connected_components(focal)
    focal_count = len(comps)

    strongest = max((blocks[tuple(np.array(cells).T.tolist())].sum() for cells in comps),
                    default=0.0)
    concentration = strongest / total

    # One region is ideal; each extra competing region decays the reward.
    focal_score = 100.0 * math.exp(-0.35 * (focal_count - 1)) if focal_count >= 1 else 0.0
    is_empty = ctx.scenario in EMPTY_SCENARIOS
    conc_target = _CONC_TARGET_EMPTY if is_empty else _CONC_TARGET
    conc_score = 100.0 * min(1.0, concentration / conc_target)

    if is_empty:
        # Empty state: "is there ONE obvious thing to do?" dominates.
        value = 0.7 * focal_score + 0.3 * conc_score
    else:
        value = 0.55 * focal_score + 0.45 * conc_score
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "mode": "empty_state" if is_empty else "transcript",
            "focal_count": int(focal_count),
            "salience_concentration": round(float(concentration), 4),
            "concentration_target": conc_target,
        },
    )
