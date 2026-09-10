"""
Turning grids into things a browser can draw: dominant-class maps, PNGs and
compact binary payloads.

The console draws its own pixels on a canvas, so the useful thing to send is
not an image but the classified grid itself — one byte per cell — which the
client can recolour, toggle and animate without another round trip. Sixty
years of a 720x360 grid is 15 MB raw and about a tenth of that gzipped, so the
whole record ships once and scrubbing the timeline is instant.

PNG rendering is here too, for the API's image endpoints and for anything that
wants a picture without running JavaScript.
"""

import gzip
import io
import struct
from typing import Iterable, List, Optional

import numpy as np
from PIL import Image

from . import cube
from . import raster as R
from . import sources as S

# Sentinels in a classified grid. 0-5 are the land use classes, in the order
# of S.CLASSES.
WATER = 6
ICE = 7
OCEAN = 255


def palette() -> List[dict]:
    out = [{'index': i, **{k: c[k] for k in ('key', 'name', 'color', 'code', 'short')}}
           for i, c in enumerate(S.CLASSES)]
    out.append({'index': WATER, **{k: S.WATER[k] for k in ('key', 'name', 'color', 'code', 'short')}})
    out.append({'index': ICE, **{k: S.ICE[k] for k in ('key', 'name', 'color', 'code', 'short')}})
    out.append({'index': OCEAN, **{k: S.OCEAN[k] for k in ('key', 'name', 'color', 'code', 'short')}})
    return out


def _rgb_table() -> np.ndarray:
    table = np.zeros((256, 3), dtype=np.uint8)
    table[:] = _hex(S.OCEAN['color'])
    for i, c in enumerate(S.CLASSES):
        table[i] = _hex(c['color'])
    table[WATER] = _hex(S.WATER['color'])
    table[ICE] = _hex(S.ICE['color'])
    return table


def _hex(h: str) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ── classification ───────────────────────────────────────────────────────

def classify(frame: np.ndarray, min_fraction: float = 0.0) -> np.ndarray:
    """A (8, h, w) fraction frame as one byte per cell.

    The winner is the land use class with the largest share of the cell. Water
    and ice only win if they beat every land class, so a cell that is 60%
    ocean and 40% forest still reads as forest — at half a degree, almost
    every coastal cell is part water, and colouring them all blue would erase
    the coasts. A cell with nothing in any plane is open ocean.
    """
    f = frame.astype(np.float32)
    if f.max() > 1.5:
        f = f / 255.0
    land = f[:S.N_CLASSES]
    total = land.sum(axis=0)
    out = np.argmax(land, axis=0).astype(np.uint8)
    best = land.max(axis=0)
    zero = np.zeros_like(total)
    water = f[S.WATER_PLANE] if f.shape[0] > S.WATER_PLANE else zero
    ice = f[S.ICE_PLANE] if f.shape[0] > S.ICE_PLANE else zero
    out = np.where((water > best) & (water >= ice) & (water > 0),
                   np.uint8(WATER), out)
    out = np.where((ice > best) & (ice > water) & (ice > 0), np.uint8(ICE), out)
    out = np.where(total + water + ice <= float(min_fraction),
                   np.uint8(OCEAN), out)
    return out.astype(np.uint8)


def dominant_cube(years: Optional[Iterable[int]] = None,
                  deg: float = S.DEFAULT_DEG) -> tuple:
    """(years, (n, h, w) uint8 classified grids) for the whole record."""
    doc = cube.require('states', deg)
    want = doc['years'] if years is None else [
        y for y in years if y in doc['index']]
    stack = np.stack([classify(doc['data'][doc['index'][y]]) for y in want])
    return want, stack


def pack_grid(years: List[int], grids: np.ndarray, deg: float) -> bytes:
    """A self-describing binary blob: header, years, then the grid bytes.

        magic 'HILD' | version u16 | nyears u16 | h u16 | w u16 | deg f32
        years: nyears x i16   (offset from 1900, fits a short)
        data:  nyears * h * w u8

    Gzipped by the API. Little-endian throughout, which is what a DataView in
    the browser reads with the least ceremony.
    """
    n, h, w = grids.shape
    head = struct.pack('<4sHHHHf', b'HILD', 1, n, h, w, float(deg))
    ys = struct.pack('<' + 'h' * n, *[y - 1900 for y in years])
    return head + ys + np.ascontiguousarray(grids, dtype=np.uint8).tobytes()


def gzipped(payload: bytes, level: int = 6) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=level, mtime=0) as fh:
        fh.write(payload)
    return buf.getvalue()


# ── images ───────────────────────────────────────────────────────────────

def png_from_classified(grid: np.ndarray, scale: int = 1) -> bytes:
    rgb = _rgb_table()[grid]
    im = Image.fromarray(rgb, 'RGB')
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def png_from_intensity(values: np.ndarray, color: str = '#ff3b47',
                       scale: int = 1, vmax: Optional[float] = None,
                       background: str = '#0a0e18') -> bytes:
    """A single-class or change-intensity ramp, quantised to eight steps.

    Eight steps rather than a smooth gradient: it keeps the map readable as
    discrete blocks, which is both the house style here and, for a choropleth
    of a bounded fraction, easier to read off a legend.
    """
    v = np.asarray(values, dtype=np.float32)
    top = float(vmax if vmax else (np.nanmax(v) or 1.0))
    q = np.clip(np.ceil(v / top * 8.0), 0, 8).astype(np.int32)
    fg, bg = np.array(_hex(color), np.float32), np.array(_hex(background), np.float32)
    ramp = np.stack([bg + (fg - bg) * (i / 8.0) for i in range(9)]).astype(np.uint8)
    im = Image.fromarray(ramp[q], 'RGB')
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return buf.getvalue()


def png_from_codes(codes: np.ndarray, scale: int = 1) -> bytes:
    """Raw HILDA+ pixel codes (11, 22, ... 99) straight to colour.

    Used for full-resolution window reads, which never go through the coarse
    grid and so are not classified.
    """
    table = np.zeros((256, 3), dtype=np.uint8)
    table[:] = _hex(S.OCEAN['color'])
    for c in S.CLASSES:
        table[c['code']] = _hex(c['color'])
    table[S.WATER['code']] = _hex(S.WATER['color'])
    table[S.ICE['code']] = _hex(S.ICE['color'])
    im = Image.fromarray(table[codes], 'RGB')
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
    elif max(im.width, im.height) > 2400:
        f = 2400 / max(im.width, im.height)
        im = im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))),
                       Image.NEAREST)
    buf = io.BytesIO()
    im.save(buf, 'PNG', optimize=True)
    return buf.getvalue()
