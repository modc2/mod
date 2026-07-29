#!/usr/bin/env python3
"""Visual reporting tool for jcode-desktop debug frames.

Composes gallery frame captures into filmstrips, animated GIFs, and
before/after pixel-diff reports. Requires python3 + Pillow only.

Frame naming convention: sequences look like ``gallery-<state>-f000.png``,
``gallery-<state>-f001.png``, ... Single captures without the ``-fNNN``
suffix (e.g. ``gallery-empty.png``) are treated as one-frame sequences.

Usage examples:

    # One filmstrip PNG: each state is a row of horizontally-composed frames.
    scripts/desktop_visual_report.py filmstrip \
        --frames-dir /tmp/desktop-vis/baseline --out /tmp/filmstrip.png

    # Animated GIF for a single state's frame sequence.
    scripts/desktop_visual_report.py gif \
        --frames-dir /tmp/desktop-vis/frames --state streaming \
        --out /tmp/streaming.gif --duration-ms 90

    # Pixel diff every PNG shared by two directories.
    scripts/desktop_visual_report.py diff \
        --before /tmp/desktop-vis/baseline --after /tmp/desktop-vis/candidate \
        --out-dir /tmp/desktop-vis/diff --threshold 8
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from PIL import Image, ImageChops, ImageDraw, ImageOps
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("error: Pillow is required (pip install Pillow)\n")
    sys.exit(2)

FRAME_RE = re.compile(r"^(?P<state>.+)-f(?P<idx>\d+)$")

# Percent of changed pixels above which a side-by-side composite is emitted.
COMPOSITE_PERCENT_THRESHOLD = 0.01


def collect_sequences(frames_dir: Path) -> dict[str, list[Path]]:
    """Group PNGs in ``frames_dir`` into named frame sequences.

    Files matching ``<state>-fNNN.png`` are grouped under ``<state>`` and
    ordered by frame index. Any other PNG becomes a single-frame sequence
    keyed by its stem. Returns an insertion-ordered dict sorted by state name.
    """
    sequences: dict[str, list[tuple[int, Path]]] = {}
    for path in sorted(frames_dir.glob("*.png")):
        match = FRAME_RE.match(path.stem)
        if match:
            state = match.group("state")
            idx = int(match.group("idx"))
        else:
            state = path.stem
            idx = 0
        sequences.setdefault(state, []).append((idx, path))
    return {
        state: [path for _, path in sorted(frames)]
        for state, frames in sorted(sequences.items())
    }


def match_state(sequences: dict[str, list[Path]], state: str) -> list[Path] | None:
    """Find a sequence by exact state name, with a ``gallery-`` prefix fallback."""
    for candidate in (state, f"gallery-{state}"):
        if candidate in sequences:
            return sequences[candidate]
    return None


# ---------------------------------------------------------------------------
# filmstrip
# ---------------------------------------------------------------------------

def cmd_filmstrip(args: argparse.Namespace) -> int:
    frames_dir = Path(args.frames_dir)
    sequences = collect_sequences(frames_dir)
    if not sequences:
        sys.stderr.write(f"error: no PNG files found in {frames_dir}\n")
        return 1

    max_width = args.max_width
    label_height = 18
    pad = 4
    rows: list[tuple[str, list[Image.Image]]] = []

    for state, paths in sequences.items():
        frames = [Image.open(p).convert("RGB") for p in paths]
        total_w = sum(im.width for im in frames) + pad * (len(frames) - 1)
        scale = min(1.0, (max_width - 2 * pad) / total_w) if total_w > 0 else 1.0
        if scale < 1.0:
            frames = [
                im.resize(
                    (max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                    Image.LANCZOS,
                )
                for im in frames
            ]
        rows.append((state, frames))

    strip_width = max(
        sum(im.width for im in frames) + pad * (len(frames) - 1) + 2 * pad
        for _, frames in rows
    )
    strip_height = sum(
        label_height + max(im.height for im in frames) + 2 * pad for _, frames in rows
    )

    canvas = Image.new("RGB", (strip_width, strip_height), (24, 24, 28))
    draw = ImageDraw.Draw(canvas)
    y = 0
    for state, frames in rows:
        frame_note = f"  ({len(frames)} frames)" if len(frames) > 1 else ""
        draw.text((pad, y + 2), state + frame_note, fill=(220, 220, 220))
        y += label_height
        x = pad
        row_h = max(im.height for im in frames)
        for im in frames:
            canvas.paste(im, (x, y + pad))
            x += im.width + pad
        y += row_h + 2 * pad

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(
        f"filmstrip: {len(rows)} state row(s), "
        f"{sum(len(f) for _, f in rows)} frame(s) -> {out} "
        f"({canvas.width}x{canvas.height})"
    )
    return 0


# ---------------------------------------------------------------------------
# gif
# ---------------------------------------------------------------------------

def cmd_gif(args: argparse.Namespace) -> int:
    frames_dir = Path(args.frames_dir)
    sequences = collect_sequences(frames_dir)
    paths = match_state(sequences, args.state)
    if paths is None:
        available = ", ".join(sequences) or "<none>"
        sys.stderr.write(
            f"error: no frames for state '{args.state}' in {frames_dir}\n"
            f"available states: {available}\n"
        )
        return 1

    frames = [Image.open(p).convert("RGB") for p in paths]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=args.duration_ms,
        loop=0,
        optimize=False,
    )
    print(
        f"gif: state '{args.state}' {len(frames)} frame(s) "
        f"@ {args.duration_ms}ms -> {out}"
    )
    return 0


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

def max_channel_diff(a: Image.Image, b: Image.Image) -> Image.Image:
    """Per-pixel max absolute channel difference of two RGB images (mode L)."""
    diff = ImageChops.difference(a, b)
    r, g, bl = diff.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), bl)


def cmd_diff(args: argparse.Namespace) -> int:
    before_dir = Path(args.before)
    after_dir = Path(args.after)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    threshold = args.threshold

    before_names = {p.name for p in before_dir.glob("*.png")}
    after_names = {p.name for p in after_dir.glob("*.png")}
    shared = sorted(before_names & after_names)
    if not shared:
        sys.stderr.write("error: no PNG filenames shared between the two dirs\n")
        return 1

    rows: list[str] = []
    composites = 0
    for name in shared:
        img_a = Image.open(before_dir / name).convert("RGB")
        img_b = Image.open(after_dir / name).convert("RGB")
        if img_a.size != img_b.size:
            rows.append(
                f"| {name} | - | - | size mismatch "
                f"{img_a.width}x{img_a.height} vs {img_b.width}x{img_b.height}, "
                f"pixel diff skipped |"
            )
            continue

        gray = max_channel_diff(img_a, img_b)
        mask = gray.point(lambda p: 255 if p > threshold else 0)
        changed = mask.histogram()[255]
        total = img_a.width * img_a.height
        percent = 100.0 * changed / total if total else 0.0
        note = ""
        if percent > COMPOSITE_PERCENT_THRESHOLD:
            composite_path = out_dir / f"{Path(name).stem}-diff.png"
            heat = ImageOps.colorize(
                ImageOps.autocontrast(gray), black=(0, 0, 0), white=(255, 32, 32)
            )
            gap = 8
            composite = Image.new(
                "RGB",
                (img_a.width * 3 + gap * 2, img_a.height),
                (24, 24, 28),
            )
            composite.paste(img_a, (0, 0))
            composite.paste(img_b, (img_a.width + gap, 0))
            composite.paste(heat, ((img_a.width + gap) * 2, 0))
            composite.save(composite_path)
            composites += 1
            note = f"composite: {composite_path.name}"
        rows.append(f"| {name} | {changed} | {percent:.4f}% | {note} |")

    only_before = sorted(before_names - after_names)
    only_after = sorted(after_names - before_names)

    report = out_dir / "diff-report.md"
    lines = [
        "# Visual diff report",
        "",
        f"- before: `{before_dir}`",
        f"- after: `{after_dir}`",
        f"- per-channel threshold: {threshold}",
        f"- composite emitted when changed pixels > {COMPOSITE_PERCENT_THRESHOLD}%",
        "",
        "| file | changed pixels | changed % | notes |",
        "| --- | ---: | ---: | --- |",
        *rows,
    ]
    if only_before:
        lines += ["", "Only in before: " + ", ".join(only_before)]
    if only_after:
        lines += ["", "Only in after: " + ", ".join(only_after)]
    lines.append("")
    report.write_text("\n".join(lines))
    print(
        f"diff: {len(shared)} file(s) compared, {composites} composite(s) "
        f"-> {report}"
    )
    return 0


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Filmstrip/GIF/diff reporting for jcode-desktop frame captures."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_strip = sub.add_parser("filmstrip", help="compose frames into one filmstrip PNG")
    p_strip.add_argument("--frames-dir", required=True)
    p_strip.add_argument("--out", required=True)
    p_strip.add_argument("--max-width", type=int, default=1600)
    p_strip.set_defaults(func=cmd_filmstrip)

    p_gif = sub.add_parser("gif", help="assemble an animated GIF for one state")
    p_gif.add_argument("--frames-dir", required=True)
    p_gif.add_argument("--state", required=True)
    p_gif.add_argument("--out", required=True)
    p_gif.add_argument("--duration-ms", type=int, default=90)
    p_gif.set_defaults(func=cmd_gif)

    p_diff = sub.add_parser("diff", help="pixel-diff PNGs shared by two directories")
    p_diff.add_argument("--before", required=True)
    p_diff.add_argument("--after", required=True)
    p_diff.add_argument("--out-dir", required=True)
    p_diff.add_argument("--threshold", type=int, default=8)
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
