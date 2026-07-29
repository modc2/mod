#!/usr/bin/env python3
"""Golden-image regression checks for Jcode Desktop gallery captures.

Renders every gallery fixture state headlessly via
`jcode-desktop --capture-gallery-screens` and compares the PNGs against
checked-in baselines.

Usage:
  scripts/desktop_gallery_golden.py check            # compare against baselines
  scripts/desktop_gallery_golden.py update           # rewrite baselines
  scripts/desktop_gallery_golden.py check --size 640x400

Exit codes: 0 ok, 1 regressions found, 2 setup/usage error.

A state regresses when more than --threshold fraction of pixels differ
by more than --tolerance per channel (defaults: 0.5% of pixels, 8/255).
Diff images for failing states are written next to the captures.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image, ImageChops
except ImportError:
    print("error: pillow is required (pip install pillow)", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BIN = ROOT / "target" / "debug" / "jcode-desktop"
DEFAULT_BASELINE_DIR = ROOT / "tests" / "desktop-gallery-golden"


def capture(binary: Path, out_dir: Path, size: str | None) -> list[dict]:
    cmd = [str(binary), "--capture-gallery-screens", str(out_dir)]
    if size:
        cmd += ["--capture-size", size]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        print(f"error: capture failed: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    manifest = json.loads(result.stdout)
    return manifest["screens"]


def compare(
    baseline_path: Path, candidate_path: Path, tolerance: int, threshold: float
) -> tuple[bool, float, Image.Image | None]:
    baseline = Image.open(baseline_path).convert("RGB")
    candidate = Image.open(candidate_path).convert("RGB")
    if baseline.size != candidate.size:
        return False, 1.0, None
    diff = ImageChops.difference(baseline, candidate)
    # Count pixels where any channel differs by more than `tolerance`.
    mask = diff.convert("L").point(lambda value: 255 if value > tolerance else 0)
    histogram = mask.histogram()
    differing = sum(histogram[1:])
    total = baseline.size[0] * baseline.size[1]
    fraction = differing / total
    return fraction <= threshold, fraction, diff if fraction > threshold else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "update"])
    parser.add_argument("--binary", type=Path, default=DEFAULT_BIN)
    parser.add_argument("--baseline-dir", type=Path, default=DEFAULT_BASELINE_DIR)
    parser.add_argument("--size", default=None, help="WxH render size override")
    parser.add_argument("--tolerance", type=int, default=8, help="per-channel delta ignored")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.005,
        help="max fraction of differing pixels before failing",
    )
    args = parser.parse_args()

    if not args.binary.exists():
        print(
            f"error: desktop binary not found at {args.binary}; build with "
            "`cargo build -p jcode-desktop --bin jcode-desktop`",
            file=sys.stderr,
        )
        return 2

    baseline_dir = args.baseline_dir
    if args.size:
        baseline_dir = baseline_dir / args.size

    with tempfile.TemporaryDirectory(prefix="jcode-gallery-golden-") as tmp:
        out_dir = Path(tmp)
        screens = capture(args.binary, out_dir, args.size)

        if args.mode == "update":
            baseline_dir.mkdir(parents=True, exist_ok=True)
            for screen in screens:
                source = out_dir / screen["file"]
                target = baseline_dir / screen["file"]
                target.write_bytes(source.read_bytes())
            print(f"updated {len(screens)} baselines in {baseline_dir}")
            return 0

        if not baseline_dir.exists():
            print(
                f"error: no baselines at {baseline_dir}; run "
                f"`{sys.argv[0]} update` first",
                file=sys.stderr,
            )
            return 2

        failures = []
        for screen in screens:
            name = screen["file"]
            baseline_path = baseline_dir / name
            candidate_path = out_dir / name
            if not baseline_path.exists():
                failures.append((name, "missing baseline", None))
                continue
            ok, fraction, diff = compare(
                baseline_path, candidate_path, args.tolerance, args.threshold
            )
            if ok:
                print(f"ok    {name}  diff={fraction:.4%}")
            else:
                diff_dir = ROOT / "target" / "gallery-golden-diffs"
                diff_dir.mkdir(parents=True, exist_ok=True)
                candidate_copy = diff_dir / name
                candidate_copy.write_bytes(candidate_path.read_bytes())
                diff_path = None
                if diff is not None:
                    diff_path = diff_dir / f"diff-{name}"
                    diff.save(diff_path)
                failures.append((name, f"{fraction:.4%} pixels differ", diff_path))
                print(f"FAIL  {name}  diff={fraction:.4%}")

        if failures:
            print(f"\n{len(failures)} state(s) regressed:")
            for name, reason, diff_path in failures:
                suffix = f" (diff: {diff_path})" if diff_path else ""
                print(f"  {name}: {reason}{suffix}")
            print(
                "\nIf the change is intentional, refresh with: "
                f"{sys.argv[0]} update"
                + (f" --size {args.size}" if args.size else "")
            )
            return 1

        print(f"\nall {len(screens)} gallery states match baselines")
        return 0


if __name__ == "__main__":
    sys.exit(main())
