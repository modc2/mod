#!/usr/bin/env python3
"""Generate the theme token blocks in src/app/app/globals.css.

Every colour utility in the app (emerald/gray/amber/sky/violet/red/white and
the three surface levels) is re-pointed at an rgb-triple CSS variable in
tailwind.config.ts, so a theme is nothing but one block of variables. Writing
those ramps by hand across nine themes invites drift, so they're derived here
from one base colour per role and pasted between the CSS markers.

    python3 scripts/gen_theme_tokens.py        # rewrites the marked block
"""
from __future__ import annotations

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parent.parent / "src/app/app/globals.css"
BEGIN = "/* >>> generated theme tokens — scripts/gen_theme_tokens.py */"
END = "/* <<< generated theme tokens */"


def hexrgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def mix(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def fmt(c) -> str:
    return " ".join(str(max(0, min(255, round(x)))) for x in c)


WHITE, BLACK = (255.0, 255.0, 255.0), (0.0, 0.0, 0.0)


def ramp(base: str, base_is_light: bool, stops: list[int]) -> dict[int, str]:
    """Five-stop accent ramp keyed by Tailwind shade.

    Dark themes read low shades as *bright* (text on near-black), so 200 mixes
    toward white. Light themes need the same utilities to stay legible on a
    pale surface, so the ramp flips and mixes toward black instead.
    """
    c = hexrgb(base)
    if base_is_light:  # light-base theme: low shades are the darkest ink
        steps = {200: -0.52, 300: -0.36, 400: -0.18, 500: 0.0, 600: -0.30}
    else:
        steps = {200: 0.58, 300: 0.38, 400: 0.17, 500: 0.0, 600: -0.20}
    out = {}
    for s in stops:
        t = steps[s]
        out[s] = fmt(mix(c, WHITE if t > 0 else BLACK, abs(t)))
    return out


# grey ramp endpoints: (brightest, dimmest) — 100 is the loudest text, 800 the
# faintest hairline. Light themes invert both ends.
def grays(ink: str, dim: str) -> dict[int, str]:
    a, b = hexrgb(ink), hexrgb(dim)
    ts = {100: 0.0, 200: 0.12, 300: 0.26, 400: 0.46, 500: 0.64, 600: 0.80, 700: 0.92, 800: 1.0}
    return {k: fmt(mix(a, b, t)) for k, t in ts.items()}


THEMES = [
    # id, base, skin, surfaces (page, panel, raised), ink, grey ends, role bases
    dict(id="arcade", base="dark", skin="pixel",
         s=("#0a0a0a", "#0c0c0d", "#141416"), body="#060909", ink="#ffffff",
         gray=("#f4f6f5", "#3a3f3d"),
         acc="#10b981", warn="#f59e0b", info="#0ea5e9", alt="#8b5cf6", dang="#ef4444",
         glow="52 211 153"),
    dict(id="matrix", base="dark", skin="pixel",
         s=("#020604", "#03100a", "#062015"), body="#010502", ink="#ddffec",
         gray=("#c6ffdd", "#2c5a45"),
         acc="#00e56f", warn="#a3e635", info="#2dd4bf", alt="#4ade80", dang="#ff5f52",
         glow="0 229 111"),
    dict(id="midnight", base="dark", skin="soft",
         s=("#0b0b13", "#12121d", "#1b1b2b"), body="#08080f", ink="#ffffff",
         gray=("#eef0f8", "#414463"),
         acc="#6366f1", warn="#f59e0b", info="#0ea5e9", alt="#a855f7", dang="#f43f5e",
         glow="129 140 248"),
    dict(id="ember", base="dark", skin="soft",
         s=("#100a06", "#18100a", "#241710"), body="#0c0603", ink="#fff6ee",
         gray=("#ffeede", "#5c463a"),
         acc="#f97316", warn="#facc15", info="#38bdf8", alt="#e879f9", dang="#ef4444",
         glow="249 115 22"),
    dict(id="abyss", base="dark", skin="soft",
         s=("#040d1a", "#071528", "#0c2038"), body="#020814", ink="#eaf6ff",
         gray=("#dceeff", "#2f4f6d"),
         acc="#0ea5e9", warn="#fbbf24", info="#22d3ee", alt="#818cf8", dang="#fb7185",
         glow="14 165 233"),
    dict(id="neon", base="dark", skin="soft",
         s=("#0c0518", "#150925", "#1f1036"), body="#08030f", ink="#ffeaff",
         gray=("#ffdcfb", "#553a6b"),
         acc="#ec4899", warn="#fde047", info="#22d3ee", alt="#a855f7", dang="#ff4d6d",
         glow="236 72 153"),
    dict(id="paper", base="light", skin="soft",
         s=("#f7f2e8", "#fdfaf3", "#fffdf8"), body="#f3ece0", ink="#241d13",
         gray=("#241d13", "#8f8271"),
         acc="#15803d", warn="#b45309", info="#1d4ed8", alt="#7c3aed", dang="#b91c1c",
         glow="21 128 61"),
    dict(id="daylight", base="light", skin="soft",
         s=("#f6f7fa", "#ffffff", "#ffffff"), body="#eef1f6", ink="#101319",
         gray=("#101319", "#7f8794"),
         acc="#0d9488", warn="#b45309", info="#2563eb", alt="#7c3aed", dang="#dc2626",
         glow="13 148 136"),
    dict(id="win95", base="light", skin="pixel",
         s=("#c0c0c0", "#d4d0c8", "#e6e3dc"), body="#008080", ink="#000000",
         gray=("#000000", "#6e6e6e"),
         acc="#000080", warn="#8a6d00", info="#0f4c81", alt="#5b2d8e", dang="#a00000",
         glow="0 0 128"),
]

ROLE_STOPS = {
    "acc": [200, 300, 400, 500, 600],   # emerald  → accent
    "warn": [200, 300, 400, 500],       # amber    → warn / coin
    "info": [200, 300, 400, 500, 600],  # sky      → info
    "alt": [200, 300, 400, 500],        # violet   → alternate
    "dang": [200, 300, 400, 500],       # red      → danger
}
PREFIX = {"acc": "a", "warn": "w", "info": "i", "alt": "v", "dang": "d"}


def block(t: dict) -> str:
    light = t["base"] == "light"
    lines = [f':root[data-theme="{t["id"]}"] {{']
    lines.append(f'  --ink: {fmt(hexrgb(t["ink"]))};')
    for i, s in enumerate(t["s"]):
        lines.append(f"  --s-{i}: {fmt(hexrgb(s))};")
    lines.append(f'  --body: {fmt(hexrgb(t["body"]))};')
    g = grays(*t["gray"])
    lines.append("  " + " ".join(f"--g-{k}: {v};" for k, v in sorted(g.items())))
    for role, stops in ROLE_STOPS.items():
        r = ramp(t[role], light, stops)
        lines.append("  " + " ".join(f"--{PREFIX[role]}-{k}: {v};" for k, v in sorted(r.items())))
    lines.append(f'  --glow: {t["glow"]};')
    lines.append("}")
    return "\n".join(lines)


def main() -> None:
    css = CSS.read_text()
    body = "\n\n".join(block(t) for t in THEMES)
    new = f"{BEGIN}\n{body}\n{END}"
    if BEGIN not in css:
        raise SystemExit(f"marker {BEGIN!r} missing from {CSS}")
    css = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: new, css, flags=re.S)
    CSS.write_text(css)
    print(f"wrote {len(THEMES)} theme blocks into {CSS}")


if __name__ == "__main__":
    main()
