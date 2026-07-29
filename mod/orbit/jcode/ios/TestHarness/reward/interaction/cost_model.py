"""Price a single user Action in SECONDS (KLM/TLM operators + Fitts' law).

This module is the `cost_model` worker described in the package docstring. It is
pure and deterministic and depends only on the shared contract in `model.py`
plus the standard library.

Pricing model
-------------
A user Action carries a list of KLM/Touch-Level-Model operator letters
(``Action.operators``) plus an optional tapped ``target_id`` and a system
``response_s`` wait. We price it as::

    seconds = sum(operator_time(letter) for letter in operators)
            + fitts_movement_time(for each TAP that acquires a target)
            + response_s
            + (unreachable penalty if the target does not exist)

Operator letters map to the literature-grounded times in ``Operators``:
``M -> ops.M``, ``TAP -> ops.TAP``, ``H -> ops.H``, ``K -> ops.K``. Any other
letter is skipped (contributes 0 s) but counted in ``detail['unknown_ops']``.

Fitts' law (touch form): ``MT = a + b * log2(D / W + 1)`` seconds, with
``a = ops.FITTS_A`` and ``b = ops.FITTS_B``.

Choice of W (effective target width along the movement axis)
------------------------------------------------------------
Fitts' W is the tolerance of the target measured *along the direction of
motion*. For an axis-aligned rectangular control the truly axis-projected width
depends on the (sometimes undefined, e.g. zero-distance) movement direction, so
we instead use ``W = min(width_pt, height_pt)`` as a deterministic, direction-
independent, conservative proxy: the narrowest dimension is the worst case the
thumb must hit, which upper-bounds difficulty and never over-credits an easy
diagonal approach. This also stays well-defined when D == 0 (a repeat tap on an
already-acquired target). See ``EFFECTIVE_WIDTH_CHOICE`` below.

Movement-time accounting
------------------------
A Fitts movement is charged once per *target acquisition*. We track a running
cursor/thumb position: it starts at ``prev_target_id``'s center (or a neutral
bottom-center thumb-reach anchor when ``prev`` is None/unknown). The first TAP
pays the full move from there to the target center; any subsequent TAP on the
same target has D == 0 and therefore pays only the Fitts intercept ``a`` (0 s by
default), matching the "movement added once per acquisition" rule.
"""

from __future__ import annotations

import math

from .model import Action, CostBreakdown, Operators, UITarget

# --- Tunables (documented, deterministic) -----------------------------------

# Neutral thumb-rest anchor used when there is no known previous target. iPhone
# portrait logical canvas ~393 x 852 pt (iPhone 14/15); a relaxed thumb sits
# near the bottom-center, so default reaches start there.
SCREEN_W_PT: float = 393.0
SCREEN_H_PT: float = 852.0
NEUTRAL_POINT: tuple[float, float] = (SCREEN_W_PT / 2.0, SCREEN_H_PT * 0.92)

# Cost charged when an action targets a control with exists=False. Chosen large
# enough to dominate any realistic flow so optimizers strongly avoid it.
UNREACHABLE_PENALTY_S: float = 1_000_000.0

# Human-readable note of the W convention (see module docstring).
EFFECTIVE_WIDTH_CHOICE: str = "min(width_pt, height_pt)  # conservative, direction-independent"

# Operator-letter -> attribute on Operators. Letters absent here are "unknown".
_OPERATOR_ATTR: dict[str, str] = {"M": "M", "TAP": "TAP", "H": "H", "K": "K"}


def fitts_time(distance_pt: float, target_w_pt: float, ops: Operators = Operators()) -> float:
    """Fitts' law touch movement time in seconds: ``a + b * log2(D/W + 1)``.

    ``distance_pt`` is the travel distance D (pt); ``target_w_pt`` is the
    effective target width W (pt). W is guarded to be > 0; non-positive widths
    are clamped to a tiny epsilon so the log stays finite (treated as a very hard
    target). Negative distances are clamped to 0.
    """
    d = max(0.0, float(distance_pt))
    w = float(target_w_pt)
    if w <= 0.0:
        w = 1e-9  # guard W > 0; degenerate target -> maximally hard
    return ops.FITTS_A + ops.FITTS_B * math.log2(d / w + 1.0)


def _effective_width(target: UITarget) -> float:
    """W along the movement axis (see module docstring: conservative proxy)."""
    return min(target.width_pt, target.height_pt)


def _euclidean(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def action_cost(
    action: Action,
    targets: dict[str, UITarget],
    prev_target_id: str | None = None,
    ops: Operators = Operators(),
) -> CostBreakdown:
    """Price one ``Action`` in seconds with a per-operator + Fitts + response breakdown.

    Sums operator times for ``action.operators``, adds a Fitts movement time for
    each TAP that acquires ``action.target_id`` (from the previous thumb position),
    adds ``action.response_s``, and applies a large penalty if the action's target
    has ``exists=False``. Returns a ``CostBreakdown`` whose ``detail`` aggregates
    per-operator seconds plus ``'fitts'`` and ``'response'`` (and ``'unknown_ops'``
    / ``'unreachable'`` when relevant).
    """
    detail: dict[str, float] = {}
    seconds = 0.0

    cur_target: UITarget | None = (
        targets.get(action.target_id) if action.target_id is not None else None
    )
    target_unreachable = cur_target is not None and not cur_target.exists

    # Running thumb position: start at the previous target's center, else neutral.
    prev_target = targets.get(prev_target_id) if prev_target_id is not None else None
    cursor: tuple[float, float] = (
        (prev_target.x_pt, prev_target.y_pt) if prev_target is not None else NEUTRAL_POINT
    )

    # 1) KLM/TLM operator times + Fitts movement charged per TAP acquisition.
    fitts_total = 0.0
    unknown_ops = 0.0
    for letter in action.operators:
        attr = _OPERATOR_ATTR.get(letter)
        if attr is None:
            unknown_ops += 1.0
            continue
        op_time = float(getattr(ops, attr))
        seconds += op_time
        detail[letter] = detail.get(letter, 0.0) + op_time

        if letter == "TAP" and cur_target is not None and not target_unreachable:
            w = _effective_width(cur_target)
            target_center = (cur_target.x_pt, cur_target.y_pt)
            d = _euclidean(cursor, target_center)
            mt = fitts_time(d, w, ops)
            fitts_total += mt
            seconds += mt
            cursor = target_center  # target now acquired; repeat taps cost only `a`

    detail["fitts"] = fitts_total
    if unknown_ops:
        detail["unknown_ops"] = unknown_ops

    # 2) System / network response wait.
    detail["response"] = float(action.response_s)
    seconds += float(action.response_s)

    # 3) Reachability penalty (control absent in this build).
    if target_unreachable:
        detail["unreachable"] = UNREACHABLE_PENALTY_S
        seconds += UNREACHABLE_PENALTY_S

    return CostBreakdown(action_id=action.id, seconds=seconds, detail=detail)


# --- Literature self-test ----------------------------------------------------

if __name__ == "__main__":
    ops = Operators()

    def show(label: str, bd: CostBreakdown) -> None:
        parts = ", ".join(f"{k}={v:.3f}" for k, v in bd.detail.items())
        print(f"  {label}: {bd.seconds:.3f} s  [{parts}]")

    print("Operators (literature defaults):", ops)
    print(f"Effective W choice: {EFFECTIVE_WIDTH_CHOICE}")
    print(f"Neutral thumb anchor: {NEUTRAL_POINT}")

    # --- Fitts monotonicity sanity ------------------------------------------
    far_small = fitts_time(300, 44, ops)   # far + small target
    near_big = fitts_time(50, 88, ops)     # near + big target
    print("\nFitts' law sanity:")
    print(f"  fitts_time(300, 44) = {far_small:.3f} s")
    print(f"  fitts_time(50, 88)  = {near_big:.3f} s")
    assert far_small > near_big, "farther+smaller must cost more than near+big"
    # D == W => log2(2) = 1 bit => MT == a + b.
    one_bit = fitts_time(44, 44, ops)
    assert math.isclose(one_bit, ops.FITTS_A + ops.FITTS_B, rel_tol=1e-9), one_bit
    print(f"  fitts_time(W, W) = a + b = {one_bit:.3f} s  (1 bit of difficulty)")

    # --- Wikipedia KLM example sanity ---------------------------------------
    # Classic KLM "point to a button and press it" reduces, in the touch model,
    # to a mental decision M, the discrete TAP, and the Fitts point. We verify a
    # one-tap button-press flow equals M + TAP + fitts (+ response).
    send_btn = UITarget(id="send", width_pt=44, height_pt=44, x_pt=360, y_pt=300)
    targets = {send_btn.id: send_btn}
    tap_send = Action(
        id="tap_send", label="Tap Send", src="chat", dst="chat",
        weight=1.0, target_id="send", operators=["M", "TAP"], response_s=0.0,
    )
    bd = action_cost(tap_send, targets, prev_target_id=None, ops=ops)
    expected = ops.M + ops.TAP + bd.detail["fitts"]
    assert math.isclose(bd.seconds, expected, rel_tol=1e-9), (bd.seconds, expected)
    assert math.isclose(bd.detail["M"], 1.35) and math.isclose(bd.detail["TAP"], 0.20)
    print("\nKLM 1-tap button-press flow == M + TAP + fitts (+response):")
    show("tap send", bd)
    print(f"    check: M(1.35)+TAP(0.20)+fitts({bd.detail['fitts']:.3f}) "
          f"= {expected:.3f} s")

    # --- A couple more priced sample actions --------------------------------
    print("\nSample priced actions:")

    # Type a short message: homing to keyboard + 5 keystrokes, no Fitts target.
    type_msg = Action(
        id="type_hi", label="Type 'hello'", src="chat", dst="chat",
        weight=1.0, target_id=None, operators=["H", "M", "K", "K", "K", "K", "K"],
        response_s=0.0,
    )
    show("type 'hello'", action_cost(type_msg, targets, ops=ops))

    # Tap send right after typing (thumb already near keyboard area): moving from
    # a known previous target shortens the Fitts move vs. the neutral reach.
    kbd_key = UITarget(id="kbd_o", width_pt=30, height_pt=42, x_pt=300, y_pt=720)
    targets2 = {**targets, kbd_key.id: kbd_key}
    show("tap send (prev=kbd_o)",
         action_cost(tap_send, targets2, prev_target_id="kbd_o", ops=ops))

    # Open a sheet that must present (network/animation response) with M+TAP.
    settings_btn = UITarget(id="settings", width_pt=44, height_pt=44, x_pt=30, y_pt=60)
    open_settings = Action(
        id="open_settings", label="Open Settings", src="chat", dst="settings_sheet",
        weight=1.0, target_id="settings", operators=["M", "TAP"], response_s=0.35,
    )
    show("open settings (+0.35 s present)",
         action_cost(open_settings, {**targets, settings_btn.id: settings_btn}, ops=ops))

    # Unreachable control (exists=False) => dominated by the penalty + noted.
    ghost = UITarget(id="ghost", width_pt=44, height_pt=44, x_pt=200, y_pt=200, exists=False)
    tap_ghost = Action(
        id="tap_ghost", label="Tap missing control", src="chat", dst="chat",
        weight=1.0, target_id="ghost", operators=["M", "TAP"], response_s=0.0,
    )
    gbd = action_cost(tap_ghost, {ghost.id: ghost}, ops=ops)
    assert gbd.seconds >= UNREACHABLE_PENALTY_S and "unreachable" in gbd.detail
    show("tap missing control (exists=False)", gbd)

    # Unknown operator letters are skipped but counted.
    weird = Action(
        id="weird", label="Unknown ops", src="chat", dst="chat",
        weight=1.0, target_id=None, operators=["M", "Z", "TAP", "Q"], response_s=0.0,
    )
    wbd = action_cost(weird, targets, ops=ops)
    assert wbd.detail.get("unknown_ops") == 2.0
    show("unknown ops [M,Z,TAP,Q]", wbd)

    print("\nAll self-test assertions passed.")
