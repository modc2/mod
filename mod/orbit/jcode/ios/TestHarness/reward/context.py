"""Context passed to every reward scorer.

A scorer reads only what it needs from this object. The aggregator builds one
Context per matrix cell (device x scenario) and reuses it across all scorers,
so expensive work (decoding the screenshot, parsing the source tree) happens
once.

Heavy fields are lazy: the numpy image and source-file map are only loaded on
first access. Optional fields (ax_tree, runtime) are None when not collected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

# iPhone OS chrome fractions (status bar / home indicator). Scorers that grade
# the app's own content area should trim these.
STATUS_BAR_FRAC = 0.055
HOME_INDICATOR_FRAC = 0.025

# Design tokens, mirrored from Sources/JCodeMobile/Theme.swift. Shared so every
# scorer agrees on "the background" / "the accent".
TOKENS = {
    "background": 0x0F0F14,
    "surface": 0x1A1A1F,
    "surfaceElevated": 0x242429,
    "mint": 0x4DD9A6,
    "warning": 0xF59E0B,
    "error": 0xD94D59,
}


def hex_to_rgb(h: int) -> np.ndarray:
    return np.array([(h >> 16) & 0xFF, (h >> 8) & 0xFF, h & 0xFF], dtype=np.float64)


@dataclass
class Context:
    screenshot: Optional[str] = None      # path to a PNG
    device: str = "iPhone 17"
    scenario: str = "short"
    scale: int = 3                        # device px per point
    source_root: Optional[str] = None     # e.g. Sources/JCodeMobile
    ax_tree_path: Optional[str] = None     # optional accessibility tree JSON
    runtime: Optional[dict] = None        # optional perf metrics dict
    meta: dict[str, Any] = field(default_factory=dict)

    # --- lazy, cached derivations ----------------------------------------
    @cached_property
    def image(self) -> Optional[Image.Image]:
        if not self.screenshot:
            return None
        return Image.open(self.screenshot).convert("RGB")

    @cached_property
    def pixels(self) -> Optional[np.ndarray]:
        img = self.image
        return None if img is None else np.asarray(img, dtype=np.float64)

    @cached_property
    def content_pixels(self) -> Optional[np.ndarray]:
        """The app content region with OS chrome trimmed top/bottom."""
        arr = self.pixels
        if arr is None:
            return None
        h = arr.shape[0]
        top = int(h * STATUS_BAR_FRAC)
        bot = int(h * (1 - HOME_INDICATOR_FRAC))
        return arr[top:bot]

    @cached_property
    def content_mask(self) -> Optional[np.ndarray]:
        """Boolean mask of pixels that differ from the app background."""
        arr = self.content_pixels
        if arr is None:
            return None
        bg = hex_to_rgb(TOKENS["background"])
        dist = np.linalg.norm(arr - bg, axis=2)
        return dist > 18.0

    @cached_property
    def source_files(self) -> dict[str, str]:
        """Map of relative path -> file text for every .swift under source_root."""
        if not self.source_root:
            return {}
        root = Path(self.source_root)
        if not root.exists():
            return {}
        out = {}
        for f in sorted(root.rglob("*.swift")):
            out[str(f.relative_to(root))] = f.read_text(encoding="utf-8", errors="replace")
        return out

    @cached_property
    def ax_tree(self) -> Optional[dict]:
        if not self.ax_tree_path or not Path(self.ax_tree_path).exists():
            return None
        try:
            return json.loads(Path(self.ax_tree_path).read_text())
        except Exception:
            return None
