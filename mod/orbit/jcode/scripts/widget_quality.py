#!/usr/bin/env python3
"""Quantitative quality metrics for Jcode Desktop inline widgets.

Captures widget states headlessly and scores each card from its PNG plus the
geometry in the capture manifest:

  bottom_clip   ink fraction in the last few px above the card's bottom
                border. Sliced glyphs leave ink touching the boundary;
                clean cards end with padding. Lower is better.
  right_clip    same for the right border (text running off the card).
  row_fit       |content_height mod line_height| in px, normalized to
                [0, 0.5*lh]. Non-zero means the card cannot end on a whole
                text row.
  edge_margin   min distance in px from any ink to the card border
                (should be >= ~4px padding).

Usage:
  scripts/widget_quality.py [--size WxH] [--states a,b,c] [--json]

Exit code 1 if any state fails a budget.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("error: pillow required", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BIN = ROOT / "target" / "debug" / "jcode-desktop"
WIDGET_STATES = [
    "hotkey-help",
    "model-picker",
    "session-info",
    "session-switcher",
    "slash-suggestions",
]

# Budgets: a card fails when any metric exceeds these.
BOTTOM_CLIP_BUDGET = 0.02   # <=2% ink density in the clip band
RIGHT_CLIP_BUDGET = 0.02
EDGE_MARGIN_BUDGET = 3.0    # ink should keep >=3px from the border
ROW_CUT_BUDGET = 0.15       # clip window must end within 0.15 rows of a whole row
CLIP_BAND_PX = 4            # band width inspected inside each border


def luminance_ink(img: Image.Image, background_estimate: int = 16) -> Image.Image:
    """Binary ink mask: pixels notably darker than their card background."""
    gray = img.convert("L")
    # Card backgrounds are near-white (>=230); text is dark (<=120).
    return gray.point(lambda v: 255 if v < 180 else 0)


def analyze(image_path: Path, geometry: dict) -> dict:
    img = Image.open(image_path)
    card = geometry["card"]
    line_height = geometry["line_height"]
    text_top = geometry["text_top"]
    clip_bottom = geometry["visible_text_bottom"]
    clip_right = geometry.get("visible_text_right")
    x0 = max(int(card["x"]), 0)
    y0 = max(int(card["y"]), 0)
    x1 = min(int(card["x"] + card["width"]), img.width)
    y1 = min(int(card["y"] + card["height"]), img.height)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return {"error": "card rect out of bounds"}

    ink = luminance_ink(img.crop((x0, y0, x1, y1)))
    width, height = ink.size
    pixels = ink.load()

    def band_density(band) -> float:
        bx0, by0, bx1, by1 = band
        bx0 = max(bx0, 0); by0 = max(by0, 0)
        bx1 = min(bx1, width); by1 = min(by1, height)
        total = max((bx1 - bx0) * (by1 - by0), 1)
        hits = sum(
            1
            for yy in range(by0, by1)
            for xx in range(bx0, bx1)
            if pixels[xx, yy]
        )
        return hits / total

    # Glyphs sliced by the text clip line leave ink right at the boundary.
    clip_y = int(clip_bottom - card["y"])
    bottom_clip = band_density((0, clip_y - CLIP_BAND_PX, width, clip_y))
    if clip_right is not None:
        clip_x = int(clip_right - card["x"])
        right_clip = band_density((clip_x - CLIP_BAND_PX, 0, clip_x, height))
    else:
        right_clip = band_density((width - 4 - CLIP_BAND_PX, 0, width - 4, height))

    # Fraction of a text row that the visible clip window cuts through.
    visible_rows = (clip_bottom - text_top) / line_height
    row_cut = visible_rows - int(visible_rows)
    row_cut = min(row_cut, 1.0 - row_cut)  # distance to nearest whole row

    # Min ink margin to the card border itself.
    bbox = ink.getbbox()
    if bbox:
        _, _, ink_right, ink_bottom = bbox
        edge_margin = min(height - ink_bottom, width - ink_right)
    else:
        edge_margin = min(width, height)

    failures = []
    if bottom_clip > BOTTOM_CLIP_BUDGET:
        failures.append(f"bottom_clip {bottom_clip:.3f} > {BOTTOM_CLIP_BUDGET} (glyphs sliced at clip line)")
    if right_clip > RIGHT_CLIP_BUDGET:
        failures.append(f"right_clip {right_clip:.3f} > {RIGHT_CLIP_BUDGET} (text cut at right edge)")
    if row_cut > ROW_CUT_BUDGET:
        failures.append(f"row_cut {row_cut:.2f} rows > {ROW_CUT_BUDGET} (clip not row-aligned)")
    if edge_margin < EDGE_MARGIN_BUDGET:
        failures.append(f"edge_margin {edge_margin}px < {EDGE_MARGIN_BUDGET}px")

    return {
        "bottom_clip": round(bottom_clip, 4),
        "right_clip": round(right_clip, 4),
        "row_cut": round(row_cut, 3),
        "edge_margin_px": edge_margin,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BIN)
    parser.add_argument("--size", default=None)
    parser.add_argument("--states", default=",".join(WIDGET_STATES))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    states = [s for s in args.states.split(",") if s]
    results = {}
    failed = False
    with tempfile.TemporaryDirectory(prefix="jcode-widget-quality-") as tmp:
        out = Path(tmp)
        for state in states:
            cmd = [
                str(args.binary),
                "--capture-gallery-screens", str(out),
                "--gallery-state", state,
            ]
            if args.size:
                cmd += ["--capture-size", args.size]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                results[state] = {"error": proc.stderr.strip()[:200]}
                failed = True
                continue
            manifest = json.loads(proc.stdout)
            screen = manifest["screens"][0]
            geometry = screen.get("inline_widget")
            if not geometry:
                results[state] = {"error": "no inline widget geometry"}
                failed = True
                continue
            metrics = analyze(out / screen["file"], geometry)
            results[state] = metrics
            if metrics.get("failures") or metrics.get("error"):
                failed = True

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for state, metrics in results.items():
            if "error" in metrics:
                print(f"FAIL  {state:20s} {metrics['error']}")
                continue
            status = "FAIL" if metrics["failures"] else "ok  "
            print(
                f"{status}  {state:20s} bottom_clip={metrics['bottom_clip']:.3f} "
                f"right_clip={metrics['right_clip']:.3f} "
                f"row_cut={metrics['row_cut']:.2f} "
                f"edge_margin={metrics['edge_margin_px']}px"
            )
            for failure in metrics["failures"]:
                print(f"        - {failure}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
