#!/usr/bin/env python3
"""Aggregate a jcode Terminal-Bench Harbor job dir and compare against the
Claude Code + Opus 4.8 baseline.

Usage:
  python scripts/tb_compare.py <jobs-dir> [baseline.tsv]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_baseline(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        task, _trials, _resolved, rate = parts[:4]
        out[task] = float(rate)
    return out


def collect_results(jobs_dir: Path) -> dict[str, list[float]]:
    """Map task_name -> list of per-trial rewards across all result.json files."""
    results: dict[str, list[float]] = {}
    for result_json in jobs_dir.rglob("result.json"):
        try:
            data = json.loads(result_json.read_text())
        except Exception:
            continue
        stats = data.get("stats") or {}
        evals = stats.get("evals") or {}
        for _eval_name, ev in evals.items():
            reward_stats = (ev.get("reward_stats") or {}).get("reward") or {}
            for reward_str, trial_ids in reward_stats.items():
                try:
                    reward = float(reward_str)
                except ValueError:
                    continue
                for trial_id in trial_ids:
                    # trial_id like "regex-log__abc123"
                    task = trial_id.split("__", 1)[0]
                    results.setdefault(task, []).append(reward)
    return results


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    jobs_dir = Path(sys.argv[1]).expanduser()
    baseline_path = Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 else (
        Path(__file__).parent / "tb_baseline_cc_opus48.tsv"
    )
    baseline = load_baseline(baseline_path)
    results = collect_results(jobs_dir)

    rows = []
    jcode_resolved = jcode_trials = 0
    regressions = []
    for task in sorted(set(baseline) | set(results)):
        rewards = results.get(task, [])
        n = len(rewards)
        passed = sum(1 for r in rewards if r >= 1.0)
        rate = (100.0 * passed / n) if n else None
        base = baseline.get(task)
        jcode_resolved += passed
        jcode_trials += n
        flag = ""
        if rate is not None and base is not None:
            if rate < base:
                flag = "REGRESSION"
                regressions.append((task, base, rate))
            elif rate > base:
                flag = "gain"
        rows.append((task, base, rate, passed, n, flag))

    print(f"{'task':38} {'base%':>7} {'jcode%':>7} {'pass':>6} {'flag'}")
    print("-" * 75)
    for task, base, rate, passed, n, flag in rows:
        base_s = f"{base:.1f}" if base is not None else "-"
        rate_s = f"{rate:.1f}" if rate is not None else "n/a"
        pass_s = f"{passed}/{n}" if n else "-"
        print(f"{task:38} {base_s:>7} {rate_s:>7} {pass_s:>6} {flag}")

    print("-" * 75)
    if jcode_trials:
        print(f"jcode micro-avg: {jcode_resolved}/{jcode_trials} = {100*jcode_resolved/jcode_trials:.1f}%")
    base_resolved = sum(baseline.values())
    print(f"baseline macro-avg: {base_resolved/len(baseline):.1f}% (CC+Opus4.8)")
    if regressions:
        print(f"\n{len(regressions)} REGRESSIONS vs baseline:")
        for task, base, rate in regressions:
            print(f"  {task}: {base:.1f}% -> {rate:.1f}%")
    else:
        print("\nNo per-task regressions detected (for tasks with results).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
