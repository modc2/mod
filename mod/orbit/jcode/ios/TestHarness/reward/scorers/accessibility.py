"""Scorer: D. accessibility.

STATIC scorer over the SwiftUI source (ctx.source_files). It rewards the
accessibility affordances a VoiceOver / Dynamic Type user needs and penalizes
icon-only buttons that ship no text equivalent:

  - explicit `.accessibilityLabel` / `.accessibilityHint`
  - Dynamic Type usage: semantic `.font(.body|.headline|...)` (which scales)
    instead of only fixed `Theme.mono(<size>)` points
  - `.dynamicTypeSize` clamps / ranges
  - reduce-motion awareness via `@Environment(\\.accessibilityReduceMotion)`
  - `Label(text, systemImage:)` which gives an icon a spoken text label
  - icon-only `Button { ... Image(systemName:) ... }` with NO label is a defect

If there is no source AND no AX tree, the cell is genuinely unmeasurable and we
return make_unavailable. The output is a blended 0..100 with per-signal counts
in evidence so regressions are diffable.
"""

from __future__ import annotations

import re

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "accessibility"
CATEGORY = "D"
WEIGHT = 0.08

# Semantic text styles scale with Dynamic Type; fixed Theme.mono(pt) does not.
_DYNAMIC_FONT = re.compile(
    r"\.font\(\s*\.(?:largeTitle|title3|title2|title|headline|subheadline"
    r"|body|callout|footnote|caption2|caption)\b"
)
_MONO_FONT = re.compile(r"Theme\.mono\s*\(")
_A11Y_LABEL = re.compile(r"\.accessibilityLabel\s*\(")
_A11Y_HINT = re.compile(r"\.accessibilityHint\s*\(")
_A11Y_VALUE = re.compile(r"\.accessibility(?:Value|AddTraits|Element|Identifier)\s*\(")
_DYNAMIC_TYPE_SIZE = re.compile(r"\.dynamicTypeSize\s*\(")
_REDUCE_MOTION = re.compile(r"accessibilityReduceMotion")
_LABEL_VIEW = re.compile(r"\bLabel\s*\(")
_SYS_IMAGE = re.compile(r"Image\s*\(\s*systemName\s*:")

# A Button whose closure body is essentially just an Image(systemName:) with no
# nearby text or accessibility label. We scan each `Button` occurrence's local
# window of source to decide.
_BUTTON = re.compile(r"\bButton\b")
_TEXT_VIEW = re.compile(r"\bText\s*\(|\bLabel\s*\(")


def _count(rx: re.Pattern, text: str) -> int:
    return len(rx.findall(text))


def _icononly_buttons_without_label(text: str) -> int:
    """Count buttons whose visible content is an SF Symbol with no text/label.

    Heuristic: for each `Button`, inspect a forward window up to the next
    `Button` (bounded). If it contains an Image(systemName:) but no Text(),
    Label(), or .accessibilityLabel, it is an unlabeled icon-only button.
    """
    flagged = 0
    idxs = [m.start() for m in _BUTTON.finditer(text)]
    for i, start in enumerate(idxs):
        end = idxs[i + 1] if i + 1 < len(idxs) else len(text)
        window = text[start:min(end, start + 600)]
        if not _SYS_IMAGE.search(window):
            continue
        has_text = bool(_TEXT_VIEW.search(window))
        has_label = bool(_A11Y_LABEL.search(window))
        if not has_text and not has_label:
            flagged += 1
    return flagged


def score(ctx: Context) -> CategoryScore:
    files = ctx.source_files
    if not files and ctx.ax_tree is None:
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no source or AX tree")
    if not files:
        # AX tree present but no source: we only grade source signals here.
        return make_unavailable(NAME, CATEGORY, WEIGHT, "no source to grade")

    blob = "\n".join(files.values())

    a11y_label_count = _count(_A11Y_LABEL, blob)
    a11y_hint_count = _count(_A11Y_HINT, blob)
    a11y_semantic_count = _count(_A11Y_VALUE, blob)
    dynamic_font_count = _count(_DYNAMIC_FONT, blob)
    mono_font_count = _count(_MONO_FONT, blob)
    dynamic_type_size_count = _count(_DYNAMIC_TYPE_SIZE, blob)
    reduce_motion_signals = _count(_REDUCE_MOTION, blob)
    label_view_count = _count(_LABEL_VIEW, blob)
    icononly = _icononly_buttons_without_label(blob)

    # Dynamic Type score: share of font usages that are scalable semantic styles
    # rather than fixed monospaced points. Label() also reads as text+icon.
    total_fonts = dynamic_font_count + mono_font_count
    dynamic_type_signals = dynamic_font_count + dynamic_type_size_count
    if total_fonts > 0:
        dynamic_ratio = dynamic_font_count / total_fonts
    else:
        dynamic_ratio = 0.0
    dynamic_score = 100.0 * dynamic_ratio
    if dynamic_type_size_count > 0:
        dynamic_score = min(100.0, dynamic_score + 10.0)

    # Label coverage: explicit a11y labels + Label() views that name their icon,
    # relative to the symbol images that could otherwise be unspoken. Saturates.
    sys_image_count = max(1, _count(_SYS_IMAGE, blob))
    labelled = a11y_label_count + label_view_count
    label_coverage = min(1.0, labelled / sys_image_count)
    label_score = 100.0 * label_coverage

    # Hint/semantic presence: small bonus band, present-or-not dominated.
    semantic_score = min(100.0, 100.0 * (a11y_hint_count + a11y_semantic_count) / 4.0)

    # Reduce-motion: binary-ish, any usage is most of the credit.
    reduce_score = min(100.0, 60.0 + 40.0 * reduce_motion_signals) if reduce_motion_signals else 0.0

    # Blend. Dynamic Type and labelling matter most for everyday legibility;
    # reduce-motion and hints are secondary affordances.
    value = (0.34 * dynamic_score
             + 0.34 * label_score
             + 0.16 * semantic_score
             + 0.16 * reduce_score)

    # Penalize icon-only buttons with no text equivalent: each is a VoiceOver
    # dead-end. Scale by how many such buttons relative to a small budget.
    penalty = min(40.0, icononly * 12.0)
    value = max(0.0, min(100.0, value - penalty))

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "a11y_label_count": a11y_label_count,
            "a11y_hint_count": a11y_hint_count,
            "dynamic_type_signals": dynamic_type_signals,
            "dynamic_font_count": dynamic_font_count,
            "mono_font_count": mono_font_count,
            "reduce_motion_signals": reduce_motion_signals,
            "label_view_count": label_view_count,
            "icononly_buttons_without_label": icononly,
            "dynamic_ratio": round(dynamic_ratio, 3),
            "label_coverage": round(label_coverage, 3),
        },
    )
