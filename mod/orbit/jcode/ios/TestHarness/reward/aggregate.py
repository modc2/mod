"""Scorer discovery + reward aggregation.

Finds every module under reward/scorers/ that exposes the scorer contract
(NAME, CATEGORY, WEIGHT, score(ctx) -> CategoryScore), runs them over a matrix
of (device x scenario) cells, and produces a single hill-climbable reward.

Per cell: weighted mean of *available* category scores (weights renormalized so
unavailable scorers never tank the reward). Overall: mean across cells, plus
the worst cell and worst category so you know exactly what to fix next.

Usage:
  python3 -m reward.aggregate --matrix-json matrix.json            # score cells
  python3 -m reward.aggregate --shot a.png --device "iPhone 17" --scenario short
  python3 -m reward.aggregate --baseline before.json --candidate after.json

`--matrix-json` consumes the output of ui_matrix.py --json (which lists shots
per device/scenario); without it, score a single screenshot.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import pkgutil
import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
IOS = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))  # so `import reward...` works

from reward.context import Context  # noqa: E402
from reward.types import CategoryScore  # noqa: E402

DEFAULT_SOURCE_ROOT = str(IOS / "Sources" / "JCodeMobile")


def discover_scorers():
    """Import every scorer module and return [(module)] that satisfy the contract."""
    scorers_pkg = HERE / "scorers"
    found = []
    for info in pkgutil.iter_modules([str(scorers_pkg)]):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"reward.scorers.{info.name}")
        if all(hasattr(mod, a) for a in ("NAME", "CATEGORY", "WEIGHT", "score")):
            found.append(mod)
        else:
            print(f"warning: {info.name} missing contract attrs, skipped", file=sys.stderr)
    return sorted(found, key=lambda m: (m.CATEGORY, m.NAME))


def score_cell(ctx: Context, scorers) -> dict:
    cats = []
    for mod in scorers:
        try:
            cs = mod.score(ctx)
        except Exception as e:  # a broken scorer must not kill the run
            cs = CategoryScore(mod.NAME, mod.CATEGORY, mod.WEIGHT,
                               value=0.0, evidence={"error": str(e)}, available=False)
        cats.append(cs)

    available = [c for c in cats if c.available]
    wsum = sum(c.weight for c in available)
    if wsum > 0:
        cell_reward = sum(c.clamped() * c.weight for c in available) / wsum
    else:
        cell_reward = 0.0

    return {
        "device": ctx.device,
        "scenario": ctx.scenario,
        "content_size": ctx.meta.get("content_size", "large"),
        "shot": ctx.screenshot,
        "reward": round(cell_reward, 2),
        "categories": [asdict(c) for c in cats],
    }


def aggregate(cells: list[dict]) -> dict:
    if not cells:
        return {"reward": 0.0, "cells": [], "by_category": {}, "worst_cell": None}

    overall = sum(c["reward"] for c in cells) / len(cells)

    # Mean per category id across cells (available only).
    by_cat: dict[str, list[float]] = {}
    cat_meta: dict[str, tuple[str, float]] = {}
    for cell in cells:
        for c in cell["categories"]:
            if c["available"]:
                by_cat.setdefault(c["name"], []).append(c["value"])
                cat_meta[c["name"]] = (c["category"], c["weight"])
    cat_means = {
        name: {
            "category": cat_meta[name][0],
            "weight": cat_meta[name][1],
            "mean": round(sum(v) / len(v), 2),
            "n": len(v),
        }
        for name, v in by_cat.items()
    }

    worst_cell = min(cells, key=lambda c: c["reward"])
    worst_cat = min(cat_means.items(), key=lambda kv: kv[1]["mean"]) if cat_means else None

    return {
        "reward": round(overall, 2),
        "cells": cells,
        "by_category": cat_means,
        "worst_cell": {"device": worst_cell["device"],
                       "scenario": worst_cell["scenario"],
                       "content_size": worst_cell.get("content_size", "large"),
                       "reward": worst_cell["reward"]},
        "worst_category": ({"name": worst_cat[0], **worst_cat[1]} if worst_cat else None),
    }


def render(report: dict) -> str:
    out = ["UX reward", "=" * 52]
    out.append(f"  OVERALL  {report['reward']:5.1f}/100   "
               f"({len(report['cells'])} cells)")
    out.append("")
    out.append("  by category (mean across cells):")
    for name, d in sorted(report["by_category"].items(),
                          key=lambda kv: (kv[1]["category"], kv[0])):
        out.append(f"    [{d['category']}] {name:20} {d['mean']:5.1f}  (w={d['weight']:.2f})")
    out.append("")
    out.append("  per cell:")
    out.append(f"    {'device':22} {'size':14} {'scenario':9} {'reward':>6}")
    for c in report["cells"]:
        size = c.get("content_size", "large").replace("accessibility", "a11y")
        out.append(f"    {c['device'][:22]:22} {size[:14]:14} "
                   f"{c['scenario']:9} {c['reward']:6.1f}")
    out.append("-" * 52)
    if report.get("worst_cell"):
        w = report["worst_cell"]
        out.append(f"  worst cell:     {w['reward']:.1f}  "
                   f"({w['device']} / {w.get('content_size', 'large')} / "
                   f"{w['scenario']})")
    if report.get("worst_category"):
        w = report["worst_category"]
        out.append(f"  worst category: {w['mean']:.1f}  ({w['name']})")
    return "\n".join(out)


def build_cells_from_matrix(matrix_json: str, source_root: str) -> list[Context]:
    data = json.loads(Path(matrix_json).read_text())
    ctxs = []
    for row in data:
        runtime = row.get("runtime")
        if not isinstance(runtime, dict) or not runtime:
            runtime = None
        meta = {}
        if row.get("content_size"):
            meta["content_size"] = row["content_size"]
        ctxs.append(Context(
            screenshot=row.get("shot"),
            device=row.get("device", "iPhone 17"),
            scenario=row.get("scenario", "short"),
            scale=int(row.get("scale", 3)),
            source_root=source_root,
            runtime=runtime,
            meta=meta,
        ))
    return ctxs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix-json", help="output of ui_matrix.py --json")
    ap.add_argument("--shot", help="single screenshot path")
    ap.add_argument("--device", default="iPhone 17")
    ap.add_argument("--scenario", default="short")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out-json", help="write the full report JSON here")
    ap.add_argument("--baseline", help="baseline report JSON for regression gate")
    ap.add_argument("--candidate", help="candidate report JSON for regression gate")
    ap.add_argument("--min", type=float, default=0.0, help="fail if overall < this")
    args = ap.parse_args()

    if args.baseline and args.candidate:
        a = json.loads(Path(args.baseline).read_text())
        b = json.loads(Path(args.candidate).read_text())
        delta = b["reward"] - a["reward"]
        print(f"baseline  {a['reward']:5.1f}")
        print(f"candidate {b['reward']:5.1f}")
        print(f"delta     {'+' if delta >= 0 else ''}{delta:.1f}")
        sys.exit(0 if delta >= -0.5 else 1)

    scorers = discover_scorers()
    if not scorers:
        print("no scorers found under reward/scorers/", file=sys.stderr)
        sys.exit(2)

    if args.matrix_json:
        ctxs = build_cells_from_matrix(args.matrix_json, args.source_root)
    elif args.shot:
        ctxs = [Context(screenshot=args.shot, device=args.device,
                        scenario=args.scenario, scale=args.scale,
                        source_root=args.source_root)]
    else:
        ap.error("provide --matrix-json or --shot")

    cells = [score_cell(ctx, scorers) for ctx in ctxs]
    report = aggregate(cells)

    if args.out_json:
        Path(args.out_json).write_text(json.dumps(report, indent=2))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))

    sys.exit(1 if report["reward"] < args.min else 0)


if __name__ == "__main__":
    main()
