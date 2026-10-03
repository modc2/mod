"""
Read HILDA+ GeoTIFFs — whole, windowed, or reduced to a coarse grid.

Each HILDA+ raster is 36000x18000 uint8, LZW compressed, EPSG:4326 on a 0.01
degree grid with the origin at (-180, 90). That is 933 million pixels; a full
decode needs about 900 MB of array and 2.5 seconds, which is fine for a batch
ingest and much too heavy for a map pan.

The saving grace is the file layout: RowsPerStrip is 1, so every one of the
18000 rows is an independently compressed LZW strip with its own offset. To
read a latitude band we assemble a *new, small* TIFF in memory that points at
just those strips and hand it to Pillow, which decodes them in C. A 200-row
window costs about 90 milliseconds and is byte-identical to the same slice of
a full decode.

Pillow is the only non-stdlib dependency beyond numpy. No GDAL, no rasterio.
"""

import io
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from . import sources as S

# These rasters are far above Pillow's decompression-bomb threshold, which is
# a sensible default for untrusted uploads and simply wrong for a 1 km global
# grid we fetched ourselves from PANGAEA.
Image.MAX_IMAGE_PIXELS = None

TAG_WIDTH, TAG_LENGTH, TAG_BITS = 256, 257, 258
TAG_COMPRESSION, TAG_PHOTOMETRIC = 259, 262
TAG_STRIP_OFFSETS, TAG_SAMPLES, TAG_ROWS_PER_STRIP = 273, 277, 278
TAG_STRIP_BYTES, TAG_PLANAR, TAG_PREDICTOR = 279, 284, 317


class TiffError(RuntimeError):
    pass


class Raster:
    """A parsed HILDA+ GeoTIFF header, kept open for windowed reads."""

    def __init__(self, path):
        self.path = Path(path)
        self._fh = open(self.path, 'rb')
        head = self._fh.read(8)
        if head[:2] not in (b'II', b'MM'):
            raise TiffError(f'{self.path.name}: not a TIFF')
        self.bo = '<' if head[:2] == b'II' else '>'
        if struct.unpack(self.bo + 'H', head[2:4])[0] != 42:
            raise TiffError(f'{self.path.name}: only classic TIFF is supported')
        self.tags = self._read_ifd(struct.unpack(self.bo + 'I', head[4:8])[0])
        self.width = self._scalar(TAG_WIDTH)
        self.height = self._scalar(TAG_LENGTH)
        self.rows_per_strip = self._scalar(TAG_ROWS_PER_STRIP, 1)
        self.compression = self._scalar(TAG_COMPRESSION, 1)
        self.predictor = self._scalar(TAG_PREDICTOR, 1)
        if self._scalar(TAG_BITS, 8) != 8 or self._scalar(TAG_SAMPLES, 1) != 1:
            raise TiffError(f'{self.path.name}: expected single-band 8-bit')
        if self.rows_per_strip != 1:
            raise TiffError(f'{self.path.name}: expected one row per strip, '
                            f'got {self.rows_per_strip}')
        self.strip_offsets = self._array(TAG_STRIP_OFFSETS)
        self.strip_bytes = self._array(TAG_STRIP_BYTES)

    # -- IFD plumbing ----------------------------------------------------

    _SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 12: 8, 16: 8}

    def _read_ifd(self, offset: int) -> Dict[int, tuple]:
        self._fh.seek(offset)
        n = struct.unpack(self.bo + 'H', self._fh.read(2))[0]
        raw = self._fh.read(12 * n)
        tags = {}
        for i in range(n):
            e = raw[12 * i:12 * i + 12]
            tag, typ = struct.unpack(self.bo + 'HH', e[:4])
            cnt = struct.unpack(self.bo + 'I', e[4:8])[0]
            tags[tag] = (typ, cnt, e[8:12])
        return tags

    def _values(self, tag: int) -> Optional[List[int]]:
        if tag not in self.tags:
            return None
        typ, cnt, val = self.tags[tag]
        size = self._SIZES.get(typ, 1)
        fmt = {1: 'B', 3: 'H', 4: 'I', 16: 'Q'}.get(typ)
        if fmt is None:
            return None
        if size * cnt <= 4:
            buf = val[:size * cnt]
        else:
            ptr = struct.unpack(self.bo + 'I', val)[0]
            here = self._fh.tell()
            self._fh.seek(ptr)
            buf = self._fh.read(size * cnt)
            self._fh.seek(here)
        return list(struct.unpack(self.bo + fmt * cnt, buf))

    def _scalar(self, tag: int, default=None):
        v = self._values(tag)
        return default if not v else v[0]

    def _array(self, tag: int) -> List[int]:
        v = self._values(tag)
        if v is None:
            raise TiffError(f'{self.path.name}: missing tag {tag}')
        return v

    # -- geometry --------------------------------------------------------

    def rows_for(self, north: float, south: float) -> Tuple[int, int]:
        """Row range [r0, r1) covering a latitude span, clamped to the raster."""
        r0 = int(np.floor((S.ORIGIN[1] - float(north)) / S.SRC_DEG))
        r1 = int(np.ceil((S.ORIGIN[1] - float(south)) / S.SRC_DEG))
        return max(0, min(r0, self.height)), max(0, min(max(r1, r0 + 1), self.height))

    def cols_for(self, west: float, east: float) -> Tuple[int, int]:
        c0 = int(np.floor((float(west) - S.ORIGIN[0]) / S.SRC_DEG))
        c1 = int(np.ceil((float(east) - S.ORIGIN[0]) / S.SRC_DEG))
        return max(0, min(c0, self.width)), max(0, min(max(c1, c0 + 1), self.width))

    # -- reads -----------------------------------------------------------

    def rows(self, r0: int, r1: int) -> np.ndarray:
        """Rows [r0, r1) as a (r1-r0, width) uint8 array.

        Builds a minimal one-strip-per-row TIFF around the strips we want and
        lets Pillow's LZW decoder do the work. Valid only because the source
        uses no predictor: each row decodes without reference to its
        neighbours.
        """
        r0, r1 = int(r0), int(r1)
        if not 0 <= r0 < r1 <= self.height:
            raise ValueError(f'row range {r0}:{r1} outside 0:{self.height}')
        if self.predictor != 1:
            raise TiffError('windowed reads assume no predictor')
        # libtiff refuses a one-strip image built this way — it wants an EOI
        # the source strips do not carry. Give it the *same* strip twice and
        # keep the first copy: two strips satisfy the decoder, and pairing a
        # row with itself rather than its neighbour means a damaged strip
        # cannot condemn the intact row next to it.
        n = r1 - r0
        if n == 1:
            rows = list(range(r0, r1)) * 2
            n = 2
        else:
            rows = list(range(r0, r1))
        counts = [self.strip_bytes[i] for i in rows]
        entries = [(TAG_WIDTH, 3, 1, self.width), (TAG_LENGTH, 3, 1, n),
                   (TAG_BITS, 3, 1, 8), (TAG_COMPRESSION, 3, 1, self.compression),
                   (TAG_PHOTOMETRIC, 3, 1, 1), (TAG_SAMPLES, 3, 1, 1),
                   (TAG_ROWS_PER_STRIP, 3, 1, 1), (TAG_PLANAR, 3, 1, 1),
                   (TAG_PREDICTOR, 3, 1, 1)]
        ntags = len(entries) + 2                      # + strip offsets/counts
        table = 8 + 2 + 12 * ntags + 4                # after header + IFD
        off_tbl, cnt_tbl = table, table + 4 * n
        first = cnt_tbl + 4 * n
        new_offsets, cur = [], first
        for c in counts:
            new_offsets.append(cur)
            cur += c
        if cur > 0xFFFFFFFF:
            raise TiffError('window too large for a classic-TIFF wrapper')
        body = bytearray(struct.pack('<H', ntags))
        for tag, typ, cnt, val in sorted(entries + [(TAG_STRIP_OFFSETS, 4, n, off_tbl),
                                                    (TAG_STRIP_BYTES, 4, n, cnt_tbl)]):
            body += (struct.pack('<HHIHH', tag, typ, cnt, val, 0) if typ == 3
                     else struct.pack('<HHII', tag, typ, cnt, val))
        body += struct.pack('<I', 0)                  # no next IFD
        body += struct.pack('<' + 'I' * n, *new_offsets)
        body += struct.pack('<' + 'I' * n, *counts)
        for i in rows:
            self._fh.seek(self.strip_offsets[i])
            body += self._fh.read(self.strip_bytes[i])
        buf = b'II*\x00' + struct.pack('<I', 8) + bytes(body)
        out = np.asarray(Image.open(io.BytesIO(buf)))
        return out[:1] if r1 - r0 == 1 else out

    def rows_tolerant(self, r0: int, r1: int) -> Tuple[np.ndarray, List[int]]:
        """Rows [r0, r1), surviving a corrupt strip.

        One member of the published archive — the 2015-2014 transition layer —
        has a damaged LZW stream at row 10: it decodes to 374 of the expected
        36000 bytes, and libtiff rejects the whole file because of it. The row
        sits at 89.9N, which is Arctic ocean and entirely nodata, so losing it
        costs nothing; refusing to read the year because of it costs a year.

        Decode the range in one go when it works, and only on failure fall
        back to row-at-a-time, zero-filling whatever genuinely will not decode
        and reporting which rows those were.
        """
        try:
            return self.rows(r0, r1), []
        except Exception:
            pass
        out = np.zeros((r1 - r0, self.width), dtype=np.uint8)
        bad = []
        for i in range(r0, r1):
            try:
                out[i - r0] = self.rows(i, i + 1)[0]
            except Exception:
                bad.append(i)
        return out, bad

    def window(self, bbox) -> Tuple[np.ndarray, list]:
        """A bbox as (array, snapped [w, s, e, n]). Full 1 km resolution."""
        w, s, e, n = [float(x) for x in bbox]
        r0, r1 = self.rows_for(n, s)
        c0, c1 = self.cols_for(w, e)
        arr = self.rows_tolerant(r0, r1)[0][:, c0:c1]
        snapped = [S.ORIGIN[0] + c0 * S.SRC_DEG, S.ORIGIN[1] - r1 * S.SRC_DEG,
                   S.ORIGIN[0] + c1 * S.SRC_DEG, S.ORIGIN[1] - r0 * S.SRC_DEG]
        return arr, snapped

    def full(self) -> np.ndarray:
        """The whole raster. ~900 MB — prefer ``rows`` or ``window``."""
        return np.asarray(Image.open(self.path))

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ── aggregation ──────────────────────────────────────────────────────────

def block_size(deg: float = S.DEFAULT_DEG) -> int:
    k = float(deg) / S.SRC_DEG
    if abs(k - round(k)) > 1e-9:
        raise ValueError(f'{deg} deg is not a whole multiple of the '
                         f'{S.SRC_DEG} deg source grid')
    k = int(round(k))
    if S.SRC_H % k or S.SRC_W % k:
        raise ValueError(f'{deg} deg does not divide the source grid evenly')
    return k


def grid_shape(deg: float = S.DEFAULT_DEG) -> Tuple[int, int]:
    k = block_size(deg)
    return S.SRC_H // k, S.SRC_W // k


def reduce_states(path, deg: float = S.DEFAULT_DEG,
                  chunk_rows: int = 1000) -> np.ndarray:
    """A state raster as (8, h, w) uint8 class fractions, 255 == whole cell.

    Read in latitude chunks so peak memory stays near 100 MB rather than the
    ~3 GB a full decode plus reshape would take. The counts are exact; only
    the final scaling to a byte is lossy, at 1/255 of a cell.
    """
    k = block_size(deg)
    h, w = grid_shape(deg)
    counts = np.zeros((S.N_PLANES, h, w), dtype=np.int32)
    chunk_rows = max(k, (chunk_rows // k) * k)
    damaged: List[int] = []
    with Raster(path) as r:
        if (r.width, r.height) != (S.SRC_W, S.SRC_H):
            raise TiffError(f'{Path(path).name}: unexpected size '
                            f'{r.width}x{r.height}')
        for r0 in range(0, S.SRC_H, chunk_rows):
            r1 = min(r0 + chunk_rows, S.SRC_H)
            block, bad = r.rows_tolerant(r0, r1)
            damaged += bad
            rows = (r1 - r0) // k
            for i, code in enumerate(S.PLANES):
                counts[i, r0 // k:r0 // k + rows] = (
                    (block == code).reshape(rows, k, w, k).sum(axis=(1, 3)))
    if damaged:
        print(f'{Path(path).name}: {len(damaged)} unreadable source rows '
              f'treated as nodata (first at {damaged[0]})')
    return np.rint(counts * (255.0 / (k * k))).astype(np.uint8)


def reduce_transitions(path, deg: float = S.DEFAULT_DEG,
                       chunk_rows: int = 1000):
    """A transition raster as (matrix, changed) where

        matrix  — (6, 6) float64 km2 that moved, from-class to to-class
        changed — (h, w) uint8 fraction of each cell's pixels that converted

    HILDA+ encodes a transition as ``from * 10 + to`` using the same digits as
    the state codes, so 43 is forest to pasture and 44 is forest that stayed
    forest. This is the dataset's *gross* change signal: the thing the paper
    is about, and what the automaton is calibrated against.

    The matrix is accumulated in km2 rather than pixels, one output row of
    latitude at a time. Counting pixels would quietly weight a hectare in
    Siberia twice a hectare in Brazil, which on this dataset means boreal
    forest dominating a matrix that is supposed to describe the world.
    """
    k = block_size(deg)
    h, w = grid_shape(deg)
    matrix = np.zeros((S.N_CLASSES, S.N_CLASSES), dtype=np.float64)
    changed = np.zeros((h, w), dtype=np.int32)
    codes = [c['code'] // 10 for c in S.CLASSES]      # 1..6
    src_px_km2 = cell_area_km2(S.SRC_DEG)             # per source row
    chunk_rows = max(k, (chunk_rows // k) * k)
    damaged: List[int] = []
    with Raster(path) as r:
        for r0 in range(0, S.SRC_H, chunk_rows):
            r1 = min(r0 + chunk_rows, S.SRC_H)
            block, bad = r.rows_tolerant(r0, r1)
            damaged += bad
            rows = (r1 - r0) // k
            for b in range(rows):
                band = block[b * k:(b + 1) * k]
                hist = np.bincount(band.ravel(), minlength=256)
                px = float(src_px_km2[r0 + b * k:r0 + (b + 1) * k].mean())
                for i, fi in enumerate(codes):
                    for j, tj in enumerate(codes):
                        matrix[i, j] += hist[fi * 10 + tj] * px
            # A pixel converted iff its code is a two-digit from/to pair with
            # differing digits: 11..66 excluding the multiples of 11, which
            # are exactly the stayed-put codes. Cheaper than testing all 30
            # off-diagonal values, and 0/77/99 fall outside the range.
            moved = (block >= 11) & (block <= 66) & (block % 11 != 0)
            changed[r0 // k:r0 // k + rows] = moved.reshape(
                rows, k, w, k).sum(axis=(1, 3))
    if damaged:
        print(f'{Path(path).name}: {len(damaged)} unreadable source rows '
              f'treated as nodata (first at {damaged[0]})')
    return matrix, np.rint(changed * (255.0 / (k * k))).astype(np.uint8)


# ── coordinate helpers on the coarse grid ────────────────────────────────

def cell_area_km2(deg: float = S.DEFAULT_DEG) -> np.ndarray:
    """Area of one grid cell per row, in km2.

    On a plate carree grid the cells are not equal-area: a 0.5 degree cell at
    60N covers half what it does at the equator. Every area figure this module
    reports is weighted by this, which is the difference between a believable
    forest total and a badly wrong one.
    """
    h, _ = grid_shape(deg)
    d = np.deg2rad(float(deg))
    lat_n = np.deg2rad(90.0 - np.arange(h) * float(deg))
    lat_s = np.deg2rad(90.0 - (np.arange(h) + 1) * float(deg))
    return (S.EARTH_RADIUS_KM ** 2) * d * (np.sin(lat_n) - np.sin(lat_s))


def bbox_slice(bbox, deg: float = S.DEFAULT_DEG) -> Tuple[slice, slice]:
    """Grid row/column slices covering a bbox."""
    h, w = grid_shape(deg)
    west, south, east, north = [float(x) for x in bbox]
    r0 = int(np.clip(np.floor((90.0 - north) / deg), 0, h - 1))
    r1 = int(np.clip(np.ceil((90.0 - south) / deg), r0 + 1, h))
    c0 = int(np.clip(np.floor((west + 180.0) / deg), 0, w - 1))
    c1 = int(np.clip(np.ceil((east + 180.0) / deg), c0 + 1, w))
    return slice(r0, r1), slice(c0, c1)


def cell_bounds(row: int, col: int, deg: float = S.DEFAULT_DEG) -> list:
    return [-180.0 + col * deg, 90.0 - (row + 1) * deg,
            -180.0 + (col + 1) * deg, 90.0 - row * deg]


def lonlat_to_cell(lon: float, lat: float, deg: float = S.DEFAULT_DEG) -> Tuple[int, int]:
    h, w = grid_shape(deg)
    row = int(np.clip((90.0 - float(lat)) / deg, 0, h - 1))
    col = int(np.clip((float(lon) + 180.0) / deg, 0, w - 1))
    return row, col
