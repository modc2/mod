"""Determinism + sanity checks for the interaction-cost engine.

The engine grounds a reward category, so it must be deterministic (same logs +
source -> same numbers) and internally consistent. Run:

    python3 -m reward.interaction.test_engine

Exits non-zero on any failure.
"""

from __future__ import annotations

import sys

from reward.interaction.cost_model import action_cost, fitts_time
from reward.interaction.engine import run_engine, reward_score
from reward.interaction.model import Action, Operators, UITarget


def main() -> None:
    failures: list[str] = []

    def check(name: str, cond: bool) -> None:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            failures.append(name)

    # 1) Fitts monotonicity (farther + smaller costs more).
    check("fitts farther/smaller costs more",
          fitts_time(300, 44) > fitts_time(50, 88))
    check("fitts non-negative", fitts_time(0, 44) >= 0.0)

    # 2) cost_model: a simple tap action ~ M + TAP + small fitts.
    tgt = {"b": UITarget(id="b", width_pt=44, height_pt=44, x_pt=200, y_pt=820)}
    bd = action_cost(Action(id="a", label="", src="s", dst="s", weight=1.0,
                            target_id="b", operators=["M", "TAP"]), tgt)
    check("tap action priced > M+TAP", bd.seconds > Operators().M + Operators().TAP)

    # 3) Engine determinism: two runs identical.
    r1 = run_engine()
    r2 = run_engine()
    check("expected cost deterministic",
          r1.expected_action_cost_s == r2.expected_action_cost_s)
    check("stationary deterministic", r1.stationary == r2.stationary)

    # 4) Engine sanity.
    check("expected cost positive + finite",
          0.0 < r1.expected_action_cost_s < 100.0)
    check("no action priced as unreachable (>1000s)",
          all(c < 1000.0 for c in r1.action_costs_s.values()))
    check("stationary sums to 1", abs(sum(r1.stationary.values()) - 1.0) < 5e-3)
    check("chat is the dominant state", r1.stationary.get("chat", 0) > 0.5)
    check("reward in [0,100]", 0.0 <= reward_score(r1) <= 100.0)

    # 5) Off-machine fallback must still work (no logs).
    rf = run_engine(log_dir="/tmp/definitely-no-logs-here")
    check("fallback runs without logs", rf.meta.get("usage_source") == "defaults")
    check("fallback reward in [0,100]", 0.0 <= reward_score(rf) <= 100.0)

    print(f"\nengine checks: {'OK' if not failures else str(len(failures)) + ' FAILURES'}")
    print(f"  expected per-action cost: {r1.expected_action_cost_s:.3f}s "
          f"-> reward {reward_score(r1):.1f}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
