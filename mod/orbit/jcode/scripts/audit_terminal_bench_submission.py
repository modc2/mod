#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DISALLOWED_NON_NULL_KEYS = {
    "override_timeout_sec",
    "max_timeout_sec",
    "override_setup_timeout_sec",
    "override_cpus",
    "override_memory_mb",
    "override_storage_mb",
    "override_gpus",
    "agent_timeout_multiplier",
    "verifier_timeout_multiplier",
    "agent_setup_timeout_multiplier",
    "environment_build_timeout_multiplier",
}
FORBIDDEN_LOG_TERMS = (
    "tbench.ai",
    "terminal-bench.org",
    "github.com/harbor-framework/terminal-bench",
    "github.com/laude-institute/terminal-bench",
    "terminal-bench leaderboard",
)


def iter_json_values(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            yield child_path, key, child
            yield from iter_json_values(child, child_path)
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            yield from iter_json_values(child, f"{path}[{idx}]")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit a Harbor Terminal-Bench 2.0 campaign for leaderboard-submission rule compatibility.")
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--min-trials", type=int, default=5)
    args = parser.parse_args()

    campaign_dir = args.campaign_dir.expanduser().resolve()
    jobs_root = campaign_dir / "harbor-jobs"
    if not jobs_root.is_dir():
        raise SystemExit(f"Missing harbor-jobs directory: {jobs_root}")

    failures: list[str] = []
    warnings: list[str] = []
    submit_ready_jobs: list[str] = []
    partial_jobs: list[str] = []

    manifest_path = campaign_dir / "campaign.json"
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        if manifest.get("timeout_multiplier") != 1.0:
            failures.append(f"campaign timeout_multiplier is {manifest.get('timeout_multiplier')!r}, expected 1.0")
        if manifest.get("attempts_per_task") and manifest.get("attempts_per_task") < args.min_trials:
            failures.append(f"campaign attempts_per_task is {manifest.get('attempts_per_task')!r}, expected >= {args.min_trials}")
    else:
        warnings.append("campaign.json not found; validating job configs only")

    task_dirs = sorted(path for path in jobs_root.iterdir() if path.is_dir())
    for task_dir in task_dirs:
        run_dirs = sorted(path for path in task_dir.iterdir() if path.is_dir())
        if not run_dirs:
            continue
        for run_dir in run_dirs:
            rel_run = run_dir.relative_to(campaign_dir)
            config_path = run_dir / "config.json"
            if not config_path.exists():
                failures.append(f"{rel_run}: missing config.json")
                continue
            config = load_json(config_path)
            if config.get("timeout_multiplier") != 1.0:
                failures.append(f"{rel_run}: timeout_multiplier is {config.get('timeout_multiplier')!r}, expected 1.0")
            for json_path, key, value in iter_json_values(config):
                if key in DISALLOWED_NON_NULL_KEYS and value is not None:
                    # suppress_override_warnings is harmless bookkeeping, not a resource override.
                    failures.append(f"{rel_run}: disallowed non-null config field {json_path}={value!r}")

            trial_results = sorted(run_dir.glob("*__/result.json")) + sorted(run_dir.glob("*__*/result.json"))
            # Glob patterns can overlap on some shells/filesystems, dedupe while preserving order.
            seen: set[Path] = set()
            trial_results = [p for p in trial_results if not (p in seen or seen.add(p))]
            if len(trial_results) < args.min_trials:
                partial_jobs.append(f"{rel_run}: only {len(trial_results)} trial result.json files, expected >= {args.min_trials}")
                continue

            invalid_trials = []
            missing_artifacts = []
            for result_path in trial_results:
                try:
                    load_json(result_path)
                except Exception as exc:  # noqa: BLE001
                    invalid_trials.append(f"{result_path.relative_to(campaign_dir)}: invalid JSON: {exc}")
                    continue
                siblings = [p for p in result_path.parent.iterdir() if p.name != "result.json"]
                if not siblings:
                    missing_artifacts.append(str(result_path.parent.relative_to(campaign_dir)))
            if invalid_trials:
                failures.extend(invalid_trials)
            if missing_artifacts:
                failures.append(f"{rel_run}: trial dirs missing non-result artifacts: {missing_artifacts[:5]}")
            if not invalid_trials and not missing_artifacts:
                submit_ready_jobs.append(str(rel_run))

    log_name_allowlist = {
        "events.ndjson",
        "stderr.txt",
        "exec_stderr.txt",
        "exec_stdout.txt",
        "instruction.txt",
        "download_error.txt",
    }
    for text_path in jobs_root.rglob("*"):
        if not text_path.is_file() or text_path.name not in log_name_allowlist or text_path.stat().st_size > 2_000_000:
            continue
        try:
            text = text_path.read_text(errors="ignore").lower()
        except Exception:
            continue
        matches = [term for term in FORBIDDEN_LOG_TERMS if term in text]
        if matches:
            warnings.append(f"possible forbidden benchmark-site/repo mention in agent log {text_path.relative_to(campaign_dir)}: {matches}")

    print(f"campaign: {campaign_dir}")
    print(f"submit-ready job runs: {len(submit_ready_jobs)}")
    for job in submit_ready_jobs:
        print(f"  OK {job}")
    print(f"partial/non-submittable job runs: {len(partial_jobs)}")
    for item in partial_jobs:
        print(f"  PARTIAL {item}")
    print(f"failures: {len(failures)}")
    for item in failures:
        print(f"  FAIL {item}")
    print(f"warnings: {len(warnings)}")
    for item in warnings[:50]:
        print(f"  WARN {item}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
