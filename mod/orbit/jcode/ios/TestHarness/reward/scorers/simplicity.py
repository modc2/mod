"""Scorer: F. simplicity.

Grades anti-complexity: a crafted screen is shallow, focused, and calm, while
AI-generated UI piles on structure for its own sake. Per
`reward/AI_SLOP_RESEARCH.md`, two documented slop tells are "card nesting (cards
inside cards inside cards; everything wrapped in a container regardless of
need)" and "too many primitives" with "no real hierarchy". Higher score here =
simpler / cleaner.

We blend SOURCE structure (what the SwiftUI tree actually is) with a PIXEL
proxy (how cluttered the rendered content reads). Source is the honest, primary
signal because nesting/primitive count are properties of the design itself and
are independent of how much conversation happens to be on screen; the pixel pass
is a lighter corroboration so neither can be gamed alone.

SOURCE signals (all measured on the parsed View structs):
  max_nesting_depth   Deepest chain of layout containers (VStack/HStack/ZStack/
                      ScrollView/List/...) anywhere in the source. Deep trees are
                      the "wrapped in a container regardless of need" tell;
                      shallow trees use spacing + type as structure. Reward
                      shallow, penalize deep.
  cardincard_count    Container shapes (Card { } / .clipShape(RoundedRectangle))
                      nested inside another card region. This is the canonical
                      "card-in-card" slop tell -> pure penalty.
  distinct_primitives Number of DISTINCT SwiftUI primitive types used in the
                      heaviest (densest) view. A focused screen reaches for a
                      few primitives; a slop screen sprays many. Reward few.
  avg_modifier_chain  Average chained-modifier count per view element. Very long
                      fiddly chains signal incidental complexity. Reward calm.

PIXEL signal (content_mask only, numpy-only, deterministic):
  pixel_region_count  Distinct content "blobs" found by row/column banding of the
                      content mask. We don't punish a chat for having many
                      message rows; we punish many SMALL competing regions
                      (scattered chips/dots/badges), which is the visual form of
                      "too many primitives". Reward fewer small competing blobs.
"""

from __future__ import annotations

import re

import numpy as np

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "simplicity"
CATEGORY = "F"
WEIGHT = 0.04

# Layout containers that genuinely create UI nesting depth. Buttons/Menus also
# take trailing closures but wrap a single label, so counting them would inflate
# depth without reflecting real structural complexity; we keep this to the
# stacking/scrolling/list primitives the spec names.
_CONTAINERS = {
    "VStack", "HStack", "ZStack", "ScrollView", "ScrollViewReader",
    "List", "LazyVStack", "LazyHStack", "LazyVGrid", "LazyHGrid", "Grid",
    "Form", "Section", "Group", "NavigationStack", "NavigationView", "ForEach",
}

# Leaf primitives. Distinct-primitive variety counts containers + leaves: a
# focused screen draws from a small vocabulary.
_LEAVES = {
    "Text", "Image", "Button", "TextField", "SecureField", "Toggle", "Label",
    "Spacer", "Divider", "ProgressView", "Circle", "Rectangle",
    "RoundedRectangle", "Capsule", "Link", "Menu", "Picker", "Stepper",
    "Slider", "Color", "Gauge",
}
_PRIMITIVES = _CONTAINERS | _LEAVES

# A standalone token (not a property access like Theme.Text or .Color).
_TOKEN_RE = {p: re.compile(rf"(?<![\w.]){p}\b") for p in _PRIMITIVES}
# A modifier call on its own line: `.font(...)`, `.padding(10)`, `.italic()`.
_MODIFIER_RE = re.compile(r"^\s*\.[A-Za-z_]\w*\s*\(")
# A clipped rounded surface = a "card". Capsule/Circle pills are intentional
# status chrome, not cards, so they are excluded.
_CARD_CLIP_RE = re.compile(r"\.clipShape\(\s*RoundedRectangle")
_STRUCT_RE = re.compile(r"struct\s+(\w+)\s*:\s*[^{]*\bView\b[^{]*\{")
_WORD_OR_BRACE_RE = re.compile(r"[A-Za-z_]\w*|[{}]")


def _matching_brace(text: str, open_idx: int) -> int:
    """Index of the brace matching the `{` at open_idx (or len(text))."""
    depth = 0
    for i in range(open_idx, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _view_bodies(source_files: dict[str, str]) -> list[tuple[str, str]]:
    """(struct_name, struct_body_text) for every `struct X: View`, deterministic."""
    out: list[tuple[str, str]] = []
    for _path, text in sorted(source_files.items()):
        for m in _STRUCT_RE.finditer(text):
            brace = text.index("{", m.start())
            end = _matching_brace(text, brace)
            out.append((m.group(1), text[brace:end + 1]))
    return out


def _container_depth(body: str) -> int:
    """Max simultaneously-open layout containers (approx UI nesting depth).

    Token scan: a container keyword arms `pending`; the next `{` opens a
    container frame. Other `{` (closures like Button { } / .onChange { }) open
    non-container frames so they never inflate structural depth.
    """
    stack: list[bool] = []
    pending = False
    best = 0
    for tok in _WORD_OR_BRACE_RE.findall(body):
        if tok == "{":
            stack.append(pending)
            pending = False
            depth = sum(stack)
            if depth > best:
                best = depth
        elif tok == "}":
            if stack:
                stack.pop()
        elif tok in _CONTAINERS:
            pending = True
    return best


def _cardincard(body: str) -> int:
    """Count card regions nested inside another card region.

    A frame is a "card" if its `{` was a `Card {` opener, or if the current
    scope carries a `.clipShape(RoundedRectangle)` surface modifier. Opening a
    second card while an ancestor card frame is still open is the card-in-card
    slop tell. This is lexical (an approximation): it fires on real nesting like
    `Card { ... Card { ... } }` and stays at 0 for a flat, disciplined app.
    """
    count = 0
    stack: list[bool] = []  # is_card per brace frame
    pending_card = False
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "{":
            is_card = pending_card
            if is_card and any(stack):  # an ancestor frame is already a card
                count += 1
            stack.append(is_card)
            pending_card = False
            i += 1
            continue
        if c == "}":
            if stack:
                stack.pop()
            i += 1
            continue
        if body.startswith("Card", i) and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] in "_.")):
            # `Card` container constructor -> next `{` is a card frame.
            pending_card = True
            i += 4
            continue
        if c == "." and _CARD_CLIP_RE.match(body, i):
            # A clipped rounded surface in the current scope: if an ancestor is
            # already a card, that is card-in-card; otherwise the scope becomes a
            # card (so a later inner clip would count).
            if stack:
                if not stack[-1] and any(stack[:-1]):
                    count += 1
                stack[-1] = True
            i += 1
            continue
        i += 1
    return count


def _distinct_primitives(body: str) -> int:
    return sum(1 for p, rx in _TOKEN_RE.items() if rx.search(body))


def _primitive_total(body: str) -> int:
    return sum(len(rx.findall(body)) for rx in _TOKEN_RE.values())


def _modifier_density(bodies: list[tuple[str, str]]) -> float:
    """Average chained modifiers per element across all views.

    Modifiers in this codebase sit one-per-line, so counting `^\\s*.\\w+(` lines
    is a faithful chain length; dividing by element count normalizes for screen
    size. Long chains per element read as fiddly, incidental complexity.
    """
    modifiers = elements = 0
    for _name, body in bodies:
        for line in body.splitlines():
            if _MODIFIER_RE.match(line):
                modifiers += 1
        elements += _primitive_total(body)
    return modifiers / elements if elements else 0.0


def _pixel_regions(mask: np.ndarray, scale: int) -> tuple[int, int]:
    """(total_regions, small_competing_regions) via coarse connected components.

    Counting raw connected pixels would split every glyph into its own region;
    that measures text, not layout. Instead we downsample the content mask onto
    an 8pt grid (one coarse cell per `8*scale` px), mark a cell ON when it is
    meaningfully covered, then label 4-connected blobs deterministically. Each
    blob is a visual "block" (a message bubble, a card, the composer). Large,
    well-separated blocks read as simple; many TINY competing blobs (status
    dots, scattered chips/badges) are the visual form of "too many primitives",
    so we report them separately as the thing to penalize.
    """
    block = max(1, int(8 * scale))
    h, w = mask.shape
    gh, gw = h // block, w // block
    if gh == 0 or gw == 0:
        return 0, 0
    cell_cov = (mask[:gh * block, :gw * block]
                .reshape(gh, block, gw, block).mean(axis=(1, 3)))
    grid = cell_cov > 0.12  # a cell is "content" when >12% of it is covered

    labels = np.zeros((gh, gw), dtype=np.int32)
    total = small = 0
    for si in range(gh):
        for sj in range(gw):
            if not grid[si, sj] or labels[si, sj]:
                continue
            total += 1
            size = 0
            stack = [(si, sj)]
            labels[si, sj] = total
            while stack:
                y, x = stack.pop()
                size += 1
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < gh and 0 <= nx < gw and grid[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = total
                        stack.append((ny, nx))
            # <=2 coarse cells ~ a region under ~16x16pt: a dot/chip, not a block.
            if size <= 2:
                small += 1
    return total, small


def score(ctx: Context) -> CategoryScore:
    source_files = ctx.source_files
    have_source = bool(source_files)
    mask = ctx.content_mask
    have_pixels = mask is not None

    if not have_source and not have_pixels:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no screenshot and no source")

    parts: list[float] = []
    weights: list[float] = []
    evidence: dict = {}

    # --- SOURCE structure (primary) ---------------------------------------
    if have_source:
        bodies = _view_bodies(source_files)
        max_depth = max((_container_depth(b) for _n, b in bodies), default=0)
        cardincard = sum(_cardincard(b) for _n, b in bodies)
        # Heaviest = densest view (most primitive instances) per the spec.
        heaviest = max(bodies, key=lambda nb: _primitive_total(nb[1]), default=("", ""))
        distinct = _distinct_primitives(heaviest[1])
        avg_mods = _modifier_density(bodies)

        # Shallow trees are simple. A clean SwiftUI screen nests ~4 containers;
        # each extra level past that erodes the score, zeroing by ~12 deep.
        depth_score = 100.0 * (1 - min(max(max_depth - 4, 0) / 8.0, 1.0))
        # Card-in-card is a hard slop tell: each instance is a steep penalty.
        cardincard_score = max(0.0, 100.0 - 22.0 * cardincard)
        # A focused screen uses ~6 primitive types; sprawl past that is penalized.
        distinct_score = 100.0 * (1 - min(max(distinct - 6, 0) / 10.0, 1.0))
        # ~3 modifiers/element is calm; long fiddly chains (>=9) zero it out.
        modifier_score = 100.0 * (1 - min(max(avg_mods - 3.0, 0.0) / 6.0, 1.0))

        source_score = (0.30 * depth_score
                        + 0.30 * cardincard_score
                        + 0.20 * distinct_score
                        + 0.20 * modifier_score)
        parts.append(source_score)
        weights.append(0.70)
        evidence["max_nesting_depth"] = int(max_depth)
        evidence["cardincard_count"] = int(cardincard)
        evidence["distinct_primitives"] = int(distinct)
        evidence["heaviest_view"] = heaviest[0]
        evidence["avg_modifier_chain"] = round(avg_mods, 3)

    # --- PIXEL clutter proxy (corroboration) ------------------------------
    if have_pixels:
        regions, small = _pixel_regions(mask, ctx.scale)
        # Many small competing blobs read as cluttered; large well-separated
        # blocks do not. Penalize small competing regions (the visual "too many
        # primitives" tell) primarily, with a gentle nudge against very high
        # total block counts. Raw content volume (more messages) is NOT punished.
        small_score = 100.0 * (1 - min(small / 8.0, 1.0))
        total_score = 100.0 * (1 - min(max(regions - 12, 0) / 24.0, 1.0))
        pixel_score = 0.7 * small_score + 0.3 * total_score
        parts.append(pixel_score)
        weights.append(0.30)
        evidence["pixel_region_count"] = int(regions)
        evidence["pixel_small_regions"] = int(small)

    wsum = sum(weights)
    value = sum(p * w for p, w in zip(parts, weights)) / wsum if wsum > 0 else 0.0
    value = max(0.0, min(100.0, value))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence=evidence,
    )
