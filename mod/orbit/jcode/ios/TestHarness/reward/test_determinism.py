"""Determinism + contract checks for reward scorers.

Every scorer must be a pure function: same Context in -> same CategoryScore out,
and must declare the contract attributes. This guards the reward signal from
flaky scorers that would make the hill un-climbable.

Run:  python3 -m reward.test_determinism
Exit non-zero on any failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from reward.aggregate import discover_scorers  # noqa: E402
from reward.context import Context  # noqa: E402

IOS = HERE.parent.parent
SOURCE_ROOT = str(IOS / "Sources" / "JCodeMobile")


def find_a_screenshot() -> str | None:
    import glob
    import os
    candidates = []
    for base in (os.environ.get("TMPDIR", "/tmp"), "/tmp"):
        candidates += glob.glob(os.path.join(base, "jcode-ui-matrix", "*.png"))
        candidates += glob.glob(os.path.join(base, "jcode_ios_*.png"))
    return candidates[0] if candidates else None


def main():
    scorers = discover_scorers()
    if not scorers:
        print("FAIL: no scorers discovered", file=sys.stderr)
        sys.exit(1)

    shot = find_a_screenshot()
    failures = []

    # Weights must sum to 1.0 (spec invariant), so the reward stays comparable
    # across revisions of the framework.
    wsum = sum(getattr(m, "WEIGHT", 0.0) for m in scorers)
    if abs(wsum - 1.0) > 1e-6:
        failures.append(f"scorer WEIGHTs sum to {wsum:.6f}, expected 1.0")

    for mod in scorers:
        # contract
        for attr in ("NAME", "CATEGORY", "WEIGHT", "score"):
            if not hasattr(mod, attr):
                failures.append(f"{mod.__name__}: missing {attr}")
        if not (0.0 <= getattr(mod, "WEIGHT", -1) <= 1.0):
            failures.append(f"{mod.__name__}: WEIGHT out of [0,1]")

        # determinism: run twice per scenario, compare. "empty" exercises the
        # scenario-aware branches (empty-state grading) as well.
        for scenario in ("short", "empty"):
            try:
                a = mod.score(Context(screenshot=shot, device="iPhone 17",
                                      scenario=scenario, scale=3,
                                      source_root=SOURCE_ROOT))
                b = mod.score(Context(screenshot=shot, device="iPhone 17",
                                      scenario=scenario, scale=3,
                                      source_root=SOURCE_ROOT))
            except Exception as e:
                failures.append(f"{mod.NAME} [{scenario}]: raised {e!r}")
                continue
            if a.value != b.value or a.available != b.available:
                failures.append(f"{mod.NAME} [{scenario}]: non-deterministic "
                                f"({a.value}/{a.available} vs {b.value}/{b.available})")
            if a.available and not (0.0 <= a.value <= 100.0):
                failures.append(f"{mod.NAME} [{scenario}]: value {a.value} out of [0,100]")

    print(f"checked {len(scorers)} scorers; "
          f"{'OK' if not failures else str(len(failures)) + ' FAILURES'}")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
