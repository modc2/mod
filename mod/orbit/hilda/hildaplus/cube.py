"""
The cube: every HILDA+ year on one coarse grid, in one file.

A year of HILDA+ is 933 million pixels. Sixty of them is not something to hold
in memory or ship to a browser, so we reduce each year once to class fractions
on a 0.5 degree grid and stack the results:

    states  (years, 8, 360, 720) uint8   fraction of each cell per class
    change  (years, 360, 720)    uint8   fraction of each cell that converted
    matrix  (years, 6, 6)        float64 gross transitions, km2

Fractions rather than a single dominant class: at 0.5 degree a cell is roughly
55 km across and almost never one land use. Keeping the mixture is what lets
the automaton move small amounts of land per step, and what makes area totals
come out right.

Ingest is the only slow thing here: roughly 20 MB of download and 10 seconds
of decode per year. Downloads run one at a time — PANGAEA is a shared public
archive and answers parallel range requests with 429 — while the decode, which
is the actual bottleneck, fans out across a process pool. Years are handled in
batches so the raw rasters on disk stay bounded.
"""

import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional

import numpy as np

from . import raster as R
from . import remote
from . import sources as S


def parse_years(spec, kind: str = 'states') -> List[int]:
    """Years from ``1960-2019``, ``1960-2019:5``, ``1960,1970,1980`` or a list.

    Years known to be unusable are dropped from ranges but honoured when named
    outright — see ``sources.EXCLUDED_STATE_YEARS``. Asking for ``1960-2019``
    should not silently hand back a corrupt year; asking for ``2015`` should
    give you what you asked for.
    """
    valid = S.STATE_YEARS if kind == 'states' else S.TRANSITION_YEARS
    explicit = isinstance(spec, (int, list, tuple)) or (
        isinstance(spec, str) and '-' not in spec.strip().lstrip('-')
        and spec.strip().lower() not in ('all', 'full', 'default', ''))
    if spec is None or spec == '' or spec == 'default':
        years = [y for y in S.DEFAULT_YEARS if y in valid]
    elif isinstance(spec, (list, tuple)):
        years = [int(y) for y in spec]
    elif isinstance(spec, int):
        years = [int(spec)]
    else:
        text = str(spec).strip().lower()
        if text in ('all', 'full'):
            years = list(valid)
        else:
            years = []
            for part in text.split(','):
                part = part.strip()
                if not part:
                    continue
                step = 1
                if ':' in part:
                    part, _, s = part.partition(':')
                    step = max(1, int(s))
                if '-' in part.lstrip('-'):
                    a, _, b = part.partition('-')
                    years += list(range(int(a), int(b) + 1, step))
                else:
                    years.append(int(part))
    out = sorted({int(y) for y in years if int(y) in valid})
    if kind == 'states' and not explicit:
        out = [y for y in out if y not in S.EXCLUDED_STATE_YEARS]
    if not out:
        raise ValueError(f'no valid {kind} years in {spec!r} '
                         f'({valid.start}-{valid.stop - 1})')
    return out


# ── one year (the reduce half runs in a worker process) ──────────────────

def _reduce(args) -> tuple:
    """Reduce one already-downloaded raster. Must be picklable, so it takes a
    path rather than a Raster and returns plain arrays."""
    year, path, kind, deg, keep_tif = args
    try:
        if kind == 'states':
            return year, (R.reduce_states(path, deg),)
        return year, R.reduce_transitions(path, deg)
    finally:
        if not keep_tif:
            try:
                os.unlink(path)
            except OSError:
                pass


# ── build ────────────────────────────────────────────────────────────────

def build(years=None, deg: float = S.DEFAULT_DEG, kind: str = 'states',
          workers: int = 4, keep_tif: bool = False, merge: bool = True,
          batch: int = 0, pause: float = 0.4, force: bool = False,
          progress=None) -> dict:
    """Reduce a set of years and write (or extend) the cube on disk.

    One batch at a time: download its years back to back, reduce them in
    parallel, drop the rasters, then move on. Peak disk is one batch of
    ~25 MB rasters instead of the whole span.
    """
    S.ensure_dirs()
    years = parse_years(years, kind)
    if merge and not force:
        have = set(load(kind, deg, quiet=True).get('years', []))
        todo = [y for y in years if y not in have]
    else:
        todo = list(years)
    started = time.time()
    done: Dict[int, tuple] = {}
    failed: Dict[int, str] = {}
    if todo:
        workers = max(1, min(int(workers), len(todo), (os.cpu_count() or 4)))
        batch = int(batch) or max(workers, 4)
        remote.index()                       # prime the member index once
        for chunk in [todo[i:i + batch] for i in range(0, len(todo), batch)]:
            jobs = []
            for y in chunk:
                try:
                    path = remote.fetch_year(y, kind)
                except Exception as e:       # a bad year must not sink the run
                    failed[y] = str(e)
                    continue
                jobs.append((y, str(path), kind, deg, keep_tif))
                if pause:
                    time.sleep(float(pause))
            if not jobs:
                continue
            if workers == 1:
                results = [_reduce(j) for j in jobs]
            else:
                with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
                    futures = {pool.submit(_reduce, j): j[0] for j in jobs}
                    results = []
                    for f in as_completed(futures):
                        try:
                            results.append(f.result())
                        except Exception as e:
                            failed[futures[f]] = f'{type(e).__name__}: {e}'
            for year, payload in results:
                done[year] = payload
                if progress:
                    progress(year, len(done), len(todo))
    if kind == 'states':
        result = _merge_states(done, deg, merge)
    else:
        result = _merge_transitions(done, deg, merge)
    result['ingested'] = sorted(done)
    result['skipped'] = sorted(set(years) - set(done))
    if failed:
        result['failed'] = {str(k): v for k, v in failed.items()}
    result['seconds'] = round(time.time() - started, 1)
    return result


def _merge_states(new: Dict[int, tuple], deg: float, merge: bool) -> dict:
    path = S.cube_path('states', deg)
    frames: Dict[int, np.ndarray] = {}
    if merge and path.exists():
        old = np.load(path)
        for i, y in enumerate(old['years'].tolist()):
            frames[int(y)] = old['data'][i]
    for y, payload in new.items():
        frames[y] = payload[0]
    years = sorted(frames)
    data = np.stack([frames[y] for y in years]) if years else np.zeros(
        (0, S.N_PLANES, *R.grid_shape(deg)), dtype=np.uint8)
    np.savez_compressed(path, years=np.array(years, dtype=np.int32), data=data,
                        deg=np.array([deg]))
    return {'cube': str(path), 'kind': 'states', 'deg': deg,
            'years': years, 'shape': list(data.shape),
            'bytes': path.stat().st_size}


def _merge_transitions(new: Dict[int, tuple], deg: float, merge: bool) -> dict:
    path = S.cube_path('transitions', deg)
    mats: Dict[int, np.ndarray] = {}
    chg: Dict[int, np.ndarray] = {}
    if merge and path.exists():
        old = np.load(path)
        for i, y in enumerate(old['years'].tolist()):
            mats[int(y)] = old['matrix'][i]
            chg[int(y)] = old['changed'][i]
    for y, (matrix, changed) in new.items():
        mats[y] = matrix
        chg[y] = changed
    years = sorted(mats)
    h, w = R.grid_shape(deg)
    matrix = (np.stack([mats[y] for y in years]) if years
              else np.zeros((0, S.N_CLASSES, S.N_CLASSES), dtype=np.float64))
    changed = (np.stack([chg[y] for y in years]) if years
               else np.zeros((0, h, w), dtype=np.uint8))
    np.savez_compressed(path, years=np.array(years, dtype=np.int32),
                        matrix=matrix, changed=changed, deg=np.array([deg]))
    return {'cube': str(path), 'kind': 'transitions', 'deg': deg,
            'years': years, 'shape': list(changed.shape),
            'bytes': path.stat().st_size}


# ── load ─────────────────────────────────────────────────────────────────

_MEM: Dict[str, dict] = {}


def load(kind: str = 'states', deg: float = S.DEFAULT_DEG,
         quiet: bool = False) -> dict:
    """The cube, memoised per process. ``{}``-ish if it has not been built."""
    path = S.cube_path(kind, deg)
    key = str(path)
    stamp = path.stat().st_mtime if path.exists() else 0
    hit = _MEM.get(key)
    if hit and hit['stamp'] == stamp:
        return hit
    if not path.exists():
        empty = {'stamp': 0, 'years': [], 'kind': kind, 'deg': deg,
                 'path': str(path), 'ready': False}
        if not quiet:
            empty['hint'] = f'run: m hilda/ingest kind={kind}'
        return empty
    z = np.load(path)
    doc = {'stamp': stamp, 'kind': kind, 'deg': float(z['deg'][0]),
           'path': str(path), 'ready': True,
           'years': [int(y) for y in z['years'].tolist()]}
    if kind == 'states':
        doc['data'] = z['data']
    else:
        doc['matrix'] = z['matrix']
        doc['changed'] = z['changed']
    doc['index'] = {y: i for i, y in enumerate(doc['years'])}
    _MEM[key] = doc
    return doc


def require(kind: str = 'states', deg: float = S.DEFAULT_DEG) -> dict:
    doc = load(kind, deg)
    if not doc.get('ready'):
        raise RuntimeError(
            f'no {kind} cube at {deg} deg yet — build one with '
            f'`m hilda/ingest kind={kind}` (about a minute per ten years)')
    return doc


def nearest_year(year, kind: str = 'states', deg: float = S.DEFAULT_DEG) -> int:
    doc = require(kind, deg)
    year = int(year)
    if year in doc['index']:
        return year
    return min(doc['years'], key=lambda y: abs(y - year))


def frame(year, deg: float = S.DEFAULT_DEG) -> np.ndarray:
    """One year as (8, h, w) uint8 fractions."""
    doc = require('states', deg)
    return doc['data'][doc['index'][nearest_year(year, 'states', deg)]]


def fractions(year, deg: float = S.DEFAULT_DEG) -> np.ndarray:
    """One year as (8, h, w) float32 fractions in 0..1."""
    return frame(year, deg).astype(np.float32) / 255.0


def land_mask(deg: float = S.DEFAULT_DEG, year: Optional[int] = None) -> np.ndarray:
    """Cells with any of the six land use classes present.

    Taken from the last available year unless one is named: the land mask is
    all but static, and using a single reference year keeps a cell that gained
    a reservoir from silently leaving the domain mid-simulation.
    """
    doc = require('states', deg)
    y = doc['years'][-1] if year is None else nearest_year(year, 'states', deg)
    f = doc['data'][doc['index'][y]][:S.N_CLASSES]
    return f.sum(axis=0) > 0


def status() -> dict:
    """What has been ingested, in a form the console can render."""
    S.ensure_dirs()
    out = {'home': str(S.CACHE), 'grids': [], 'rasters': remote.cached()}
    for path in sorted(S.CUBE_DIR.glob('cube_*.npz')):
        kind = path.stem.split('_')[1]
        try:
            z = np.load(path)
            years = [int(y) for y in z['years'].tolist()]
            deg = float(z['deg'][0])
        except Exception as e:
            out['grids'].append({'file': path.name, 'error': str(e)})
            continue
        out['grids'].append({
            'kind': kind, 'deg': deg, 'file': path.name,
            'years': len(years),
            'span': [years[0], years[-1]] if years else None,
            'gaps': _gaps(years), 'bytes': path.stat().st_size})
    states = load('states', quiet=True)
    out['ready'] = bool(states.get('ready'))
    out['state_years'] = states.get('years', [])
    # A gap in the series should explain itself rather than look like a failed
    # download someone forgot to retry.
    missing = [y for y in S.DEFAULT_YEARS if y not in set(out['state_years'])]
    out['known_gaps'] = {
        str(y): S.EXCLUDED_STATE_YEARS.get(
            y, 'not ingested — run: m hilda/ingest years=%d' % y)
        for y in missing}
    return out


def _gaps(years: List[int]) -> List[list]:
    holes, prev = [], None
    for y in years:
        if prev is not None and y != prev + 1:
            holes.append([prev + 1, y - 1])
        prev = y
    return holes
