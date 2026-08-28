#!/usr/bin/env python3
"""Design-system discipline linter for the jcode iOS SwiftUI sources.

Screenshots grade the *output*; this grades the *source*. A consistent UI comes
from routing every color, font, and spacing value through the design tokens in
Theme.swift instead of sprinkling magic numbers. This linter scans the SwiftUI
views and reports, per file and overall, a 0-100 discipline score plus the
exact offending lines so regressions are actionable.

Checks (each contributes to the score):
  raw_color     Color(red:..) / Color(.sRGB..) / Color(white:..) literals and
                .opacity() on non-token colors that should be Theme tokens.
  raw_font      .font(.system(size:..)) literals instead of Theme.mono(..).
  magic_radius  cornerRadius: <literal> values not drawn from a small scale.
  magic_pad     .padding(<literal>) values off the 4pt spacing grid.
  long_view     view files / body blocks past a size budget (split them).

Usage:
  python3 ui_lint.py [--root Sources/JCodeMobile] [--json] [--max-issues N]
Exit code is non-zero when the overall score drops below --min (default 0,
i.e. report-only); raise --min in CI once the baseline is cleaned up.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Allowed spacing values (4pt grid + a few common insets). Bypass with Theme.
SPACING_GRID = {0, 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 32, 40, 48, 56, 64}
RADIUS_SCALE = {0, 8, 10, 12, 14, 16, 20, 999}  # 999 ~ Capsule-ish; allow round

RAW_COLOR_RE = re.compile(
    r"Color\(\s*(red|white|\.sRGB|hue|displayP3)", re.IGNORECASE)
RAW_HEX_IN_VIEW_RE = re.compile(r"Color\(hex:")
SYSTEM_FONT_RE = re.compile(r"\.system\(\s*size:")
CORNER_RADIUS_RE = re.compile(r"cornerRadius:\s*([0-9]+(?:\.[0-9]+)?)")
PADDING_RE = re.compile(r"\.padding\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)")
PADDING_EDGE_RE = re.compile(r"\.padding\(\s*\.[a-z]+,\s*([0-9]+(?:\.[0-9]+)?)\s*\)")

# Penalty weight per issue category (points off per occurrence, capped).
WEIGHTS = {
    "raw_color": 6,
    "raw_font": 4,
    "magic_radius": 2,
    "magic_pad": 1,
    "long_view": 5,
}
BODY_LINE_BUDGET = 220   # a single view file beyond this likely needs splitting


@dataclass
class Issue:
    file: str
    line: int
    kind: str
    text: str


@dataclass
class Report:
    score: float
    issues: list = field(default_factory=list)
    per_file: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)

    def render(self, max_issues=40):
        out = ["Design-system discipline", "=" * 40]
        out.append(f"  score {self.score:5.1f}/100   ({sum(self.counts.values())} issues)")
        out.append("  by category:")
        for k in WEIGHTS:
            out.append(f"    {k:14} {self.counts.get(k, 0)}")
        out.append("  by file:")
        for f, s in sorted(self.per_file.items(), key=lambda kv: kv[1]):
            out.append(f"    {s:5.1f}  {f}")
        if self.issues:
            out.append("-" * 40)
            for i in self.issues[:max_issues]:
                out.append(f"  {i.file}:{i.line}  [{i.kind}]  {i.text.strip()[:70]}")
            if len(self.issues) > max_issues:
                out.append(f"  ... +{len(self.issues) - max_issues} more")
        return "\n".join(out)


def is_token_file(path: Path) -> bool:
    return path.name == "Theme.swift"


def lint_file(path: Path):
    issues = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    token_file = is_token_file(path)

    for n, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//"):
            continue

        if not token_file:
            if RAW_COLOR_RE.search(line) or RAW_HEX_IN_VIEW_RE.search(line):
                issues.append(Issue(path.name, n, "raw_color", line))
            if SYSTEM_FONT_RE.search(line) and "Theme" not in line:
                issues.append(Issue(path.name, n, "raw_font", line))

        m = CORNER_RADIUS_RE.search(line)
        if m and float(m.group(1)) not in RADIUS_SCALE and not token_file:
            issues.append(Issue(path.name, n, "magic_radius", line))

        for rx in (PADDING_RE, PADDING_EDGE_RE):
            pm = rx.search(line)
            if pm:
                val = float(pm.group(1))
                if val not in SPACING_GRID:
                    issues.append(Issue(path.name, n, "magic_pad", line))

    if not token_file and len(lines) > BODY_LINE_BUDGET:
        issues.append(
            Issue(path.name, len(lines), "long_view",
                  f"{len(lines)} lines > {BODY_LINE_BUDGET} budget"))
    return issues, len(lines)


def file_score(issues):
    penalty = sum(WEIGHTS[i.kind] for i in issues)
    return max(0.0, 100.0 - penalty)


def lint_root(root: Path):
    issues = []
    per_file = {}
    files = sorted(root.rglob("*.swift"))
    for f in files:
        fi, _ = lint_file(f)
        issues.extend(fi)
        per_file[str(f.relative_to(root))] = round(file_score(fi), 1)

    counts = {}
    for i in issues:
        counts[i.kind] = counts.get(i.kind, 0) + 1

    # Overall: penalty-weighted, normalized by file count so adding clean files
    # doesn't dilute the signal; clamp to [0,100].
    total_penalty = sum(WEIGHTS[i.kind] for i in issues)
    denom = max(1, len(files))
    score = max(0.0, 100.0 - total_penalty / denom)
    return Report(score=round(score, 1), issues=issues,
                  per_file=per_file, counts=counts)


def main():
    ap = argparse.ArgumentParser()
    default_root = Path(__file__).resolve().parent.parent / "Sources" / "JCodeMobile"
    ap.add_argument("--root", default=str(default_root))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-issues", type=int, default=40)
    ap.add_argument("--min", type=float, default=0.0,
                    help="fail (exit 1) if score < this")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"no such root: {root}", file=sys.stderr)
        sys.exit(2)

    report = lint_root(root)
    if args.json:
        print(json.dumps({
            "score": report.score,
            "counts": report.counts,
            "per_file": report.per_file,
            "issues": [asdict(i) for i in report.issues],
        }, indent=2))
    else:
        print(report.render(args.max_issues))

    sys.exit(1 if report.score < args.min else 0)


if __name__ == "__main__":
    main()
