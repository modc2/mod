"""B. interaction_cost - expected interaction cost, in real seconds (HCI-grounded).

This replaces the old regex tap-counter with a principled model:

  * a weighted user-behavior GRAPH (Markov chain over UI states) whose edge
    weights are grounded in this user's REAL TUI usage logs (reward/interaction/
    log_mining.py), falling back to HCI-literature defaults off-machine;
  * a cost model that prices each action in SECONDS using KLM / Touch-Level-Model
    operators (M=1.35s, TAP=0.20s, H=0.40s, K) plus Fitts' law movement time over
    the actual control geometry parsed from the SwiftUI source (reward/interaction/
    ui_map.py); and
  * an engine that solves the stationary distribution and reports the expected
    seconds per user action (reward/interaction/engine.py).

The reward is `engine.reward_score()`: the expected per-action cost mapped to
0..100 (cheaper = higher). Because the geometry comes from the source, shrinking
a deep flow or enlarging/relocating a frequently-tapped control raises the score
in a way that reflects real predicted user effort, not a regex heuristic.

Deterministic: same logs + same source -> same score. If the engine cannot run
(e.g. import/parse failure), this degrades to make_unavailable so it never tanks
the overall reward.
"""

from __future__ import annotations

from reward.context import Context
from reward.types import CategoryScore, make_unavailable

NAME = "interaction_cost"
CATEGORY = "B"
WEIGHT = 0.12


def score(ctx: Context) -> CategoryScore:
    # Import lazily so a problem in the engine package can't break scorer
    # discovery for every other category.
    try:
        from reward.interaction.engine import run_engine, reward_score
    except Exception as e:  # pragma: no cover - defensive
        return make_unavailable(NAME, CATEGORY, WEIGHT, f"engine import failed: {e}")

    try:
        result = run_engine(source_root=ctx.source_root or None)
    except Exception as e:  # pragma: no cover - defensive
        return make_unavailable(NAME, CATEGORY, WEIGHT, f"engine run failed: {e}")

    value = reward_score(result)

    # Surface the most expensive frequently-used actions so the evidence points
    # straight at what to optimize next.
    ranked = sorted(
        result.action_costs_s.items(),
        key=lambda kv: kv[1] * result.action_probability.get(kv[0], 0.0),
        reverse=True,
    )
    top = [
        {"action": aid, "seconds": result.action_costs_s[aid],
         "prob": result.action_probability.get(aid, 0.0)}
        for aid, _ in ranked[:4]
    ]

    return CategoryScore(
        name=NAME, category=CATEGORY, weight=WEIGHT, value=round(value, 2),
        evidence={
            "expected_action_cost_s": result.expected_action_cost_s,
            "mean_task_time_s": result.mean_task_time_s,
            "usage_source": result.meta.get("usage_source"),
            "log_lines_scanned": result.meta.get("lines_scanned"),
            "stationary": result.stationary,
            "task_times_s": result.task_times_s,
            "top_cost_actions": top,
            "model": "KLM/TLM + Fitts over log-grounded user graph",
        },
    )
