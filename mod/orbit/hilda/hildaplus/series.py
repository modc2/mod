"""
The longitudinal half: HILDA+ as time series rather than maps.

Everything here reduces the cube over space to get a curve over time, for the
globe, a named region or an arbitrary bbox. Two things are worth stating
plainly because they are the easiest ways to get this wrong:

*Area weighting.* The grid is plate carree, so a 0.5 degree cell at 60N is
half the area of one at the equator. Summing fractions without weighting by
``cell_area_km2`` overstates high latitudes badly — it would put most of the
world's forest in Siberia.

*Net is not gross.* The state layers give net change: if a cell loses 100 km2
of forest and gains 100 km2 elsewhere in the same cell, the net is zero. The
transition layers give gross change, and HILDA+'s headline finding is that
gross change is roughly four times net. Both are exposed, and which one you
are looking at is always labelled.
"""

from typing import Dict, List, Optional

import numpy as np

from . import cube
from . import raster as R
from . import sources as S


def _weights(bbox=None, deg: float = S.DEFAULT_DEG):
    """(row slice, col slice, per-cell km2 for that window)."""
    rs, cs = (R.bbox_slice(bbox, deg) if bbox is not None
              else (slice(None), slice(None)))
    area = R.cell_area_km2(deg)[rs][:, None]
    return rs, cs, area


def areas(year, region=None, bbox=None, deg: float = S.DEFAULT_DEG) -> dict:
    """Area in km2 of every class in one year, over a region."""
    box = S.resolve_bbox(region, bbox)
    y = cube.nearest_year(year, 'states', deg)
    f = cube.fractions(y, deg)
    rs, cs, area = _weights(box, deg)
    out = {}
    for i, key in enumerate(S.PLANE_KEYS):
        out[key] = float((f[i, rs, cs] * area).sum())
    land = sum(out[c['key']] for c in S.CLASSES)
    return {'year': y, 'region': region or 'custom', 'bbox': box,
            'km2': out, 'land_km2': land,
            'share': {k: (v / land if land else 0.0) for k, v in out.items()}}


def series(region=None, bbox=None, years=None,
           deg: float = S.DEFAULT_DEG) -> dict:
    """Class area per year: the core longitudinal view.

    Returns parallel arrays — years plus one km2 series per class — which is
    what a chart wants and what keeps the payload small enough to send whole.
    """
    doc = cube.require('states', deg)
    box = S.resolve_bbox(region, bbox)
    want = doc['years'] if years is None else [
        y for y in cube.parse_years(years) if y in doc['index']]
    if not want:
        raise ValueError('no requested year is in the cube')
    rs, cs, area = _weights(box, deg)
    idx = [doc['index'][y] for y in want]
    block = doc['data'][idx][:, :, rs, cs].astype(np.float32) / 255.0
    km2 = (block * area[None, None, :, :]).sum(axis=(2, 3))    # (years, planes)
    out = {k: [float(v) for v in km2[:, i]] for i, k in enumerate(S.PLANE_KEYS)}
    land = km2[:, :S.N_CLASSES].sum(axis=1)
    first, last = km2[0], km2[-1]
    return {
        'region': region or ('global' if bbox is None else 'custom'),
        'bbox': box, 'years': want, 'deg': deg,
        'unit': 'km2', 'measure': 'net state area',
        'km2': out,
        'land_km2': [float(v) for v in land],
        'change': {k: {'first': float(first[i]), 'last': float(last[i]),
                       'delta': float(last[i] - first[i]),
                       'pct': (float((last[i] - first[i]) / first[i] * 100)
                               if first[i] else None)}
                   for i, k in enumerate(S.PLANE_KEYS)},
        'span': [want[0], want[-1]],
    }


def net_change(y0=None, y1=None, region=None, bbox=None,
               deg: float = S.DEFAULT_DEG) -> dict:
    """Net area change per class between two years."""
    doc = cube.require('states', deg)
    y0 = doc['years'][0] if y0 is None else cube.nearest_year(y0, 'states', deg)
    y1 = doc['years'][-1] if y1 is None else cube.nearest_year(y1, 'states', deg)
    a, b = areas(y0, region, bbox, deg), areas(y1, region, bbox, deg)
    delta = {k: b['km2'][k] - a['km2'][k] for k in S.PLANE_KEYS}
    return {'from': y0, 'to': y1, 'region': a['region'], 'bbox': a['bbox'],
            'measure': 'net', 'km2': delta,
            'gained': max(delta, key=delta.get),
            'lost': min(delta, key=delta.get),
            'total_abs_km2': float(sum(abs(v) for v in delta.values())) / 2}


def transitions(y0=None, y1=None, deg: float = S.DEFAULT_DEG) -> dict:
    """Gross transitions from the HILDA+ transition layers, summed over a span.

    Global only: the transition cube stores the 6x6 matrix globally plus a
    per-cell change intensity, which keeps it small. For a regional gross
    matrix use ``transitions_window``, which reads the source rasters.
    """
    doc = cube.require('transitions', deg)
    years = doc['years']
    y0 = years[0] if y0 is None else int(y0)
    y1 = years[-1] if y1 is None else int(y1)
    picked = [y for y in years if y0 <= y <= y1]
    if not picked:
        raise ValueError(f'no transition years between {y0} and {y1}; '
                         f'have {years[0]}-{years[-1]}')
    idx = [doc['index'][y] for y in picked]
    m = doc['matrix'][idx].sum(axis=0).astype(np.float64)   # km2
    keys = [c['key'] for c in S.CLASSES]
    moved = m.copy()
    np.fill_diagonal(moved, 0.0)
    gross = float(moved.sum())
    # Net is what a state-to-state comparison would have seen: the part of the
    # gross flow that did not cancel against a flow the other way.
    net = float(np.abs(moved.sum(axis=1) - moved.sum(axis=0)).sum() / 2)
    return {
        'from': picked[0] - 1, 'to': picked[-1], 'years': len(picked),
        'measure': 'gross transitions (HILDA+ transition layers), km2',
        'classes': keys,
        'matrix_km2': [[float(v) for v in row] for row in moved],
        'stable_km2': {k: float(m[i, i]) for i, k in enumerate(keys)},
        'loss_km2': {k: float(moved[i].sum()) for i, k in enumerate(keys)},
        'gain_km2': {k: float(moved[:, i].sum()) for i, k in enumerate(keys)},
        'gross_km2': gross, 'net_km2': net,
        'gross_over_net': (gross / net) if net else None,
        'top_flows': _top_flows(moved, keys),
    }


def _top_flows(m, keys, n: int = 8) -> List[dict]:
    flows = [{'from': keys[i], 'to': keys[j], 'km2': float(m[i, j])}
             for i in range(len(keys)) for j in range(len(keys)) if i != j]
    return sorted(flows, key=lambda f: -f['km2'])[:n]


def change_intensity(y0=None, y1=None, deg: float = S.DEFAULT_DEG) -> np.ndarray:
    """Per-cell converted fraction, summed over a span. Values may exceed 1:
    a cell that turned over twice changed twice."""
    doc = cube.require('transitions', deg)
    years = doc['years']
    y0 = years[0] if y0 is None else int(y0)
    y1 = years[-1] if y1 is None else int(y1)
    idx = [doc['index'][y] for y in years if y0 <= y <= y1]
    if not idx:
        raise ValueError('no transition years in that span')
    return doc['changed'][idx].astype(np.float32).sum(axis=0) / 255.0


def hotspots(y0=None, y1=None, n: int = 20, region=None, bbox=None,
             deg: float = S.DEFAULT_DEG) -> dict:
    """The cells that churned most over a span."""
    box = S.resolve_bbox(region, bbox)
    ci = change_intensity(y0, y1, deg)
    rs, cs = R.bbox_slice(box, deg)
    sub = ci[rs, cs]
    flat = np.argsort(sub, axis=None)[::-1][:int(n)]
    rows, cols = np.unravel_index(flat, sub.shape)
    area = R.cell_area_km2(deg)
    out = []
    for r, c in zip(rows.tolist(), cols.tolist()):
        gr, gc = r + rs.start, c + cs.start
        out.append({'row': gr, 'col': gc,
                    'bbox': R.cell_bounds(gr, gc, deg),
                    'lon': -180.0 + (gc + 0.5) * deg,
                    'lat': 90.0 - (gr + 0.5) * deg,
                    'turnover': float(sub[r, c]),
                    'churned_km2': float(sub[r, c] * area[gr])})
    return {'from': y0, 'to': y1, 'region': region or 'custom', 'bbox': box,
            'measure': 'gross turnover, cell fractions summed over the span',
            'hotspots': out}


def cell(lon: float, lat: float, deg: float = S.DEFAULT_DEG) -> dict:
    """Everything we know about one grid cell, as a time series."""
    doc = cube.require('states', deg)
    row, col = R.lonlat_to_cell(lon, lat, deg)
    stack = doc['data'][:, :, row, col].astype(np.float32) / 255.0
    area = float(R.cell_area_km2(deg)[row])
    out = {'lon': float(lon), 'lat': float(lat), 'row': row, 'col': col,
           'bbox': R.cell_bounds(row, col, deg), 'cell_km2': area,
           'years': doc['years'],
           'fraction': {k: [float(v) for v in stack[:, i]]
                        for i, k in enumerate(S.PLANE_KEYS)}}
    last = stack[-1][:S.N_CLASSES]
    out['dominant'] = (S.CLASSES[int(np.argmax(last))]['key']
                       if last.sum() > 0 else 'ocean')
    out['land_fraction'] = float(last.sum())
    tdoc = cube.load('transitions', deg, quiet=True)
    if tdoc.get('ready'):
        out['turnover'] = {'years': tdoc['years'],
                           'fraction': [float(v) / 255.0
                                        for v in tdoc['changed'][:, row, col]]}
    return out


def summary(deg: float = S.DEFAULT_DEG) -> dict:
    """The numbers the console shows on load."""
    doc = cube.require('states', deg)
    y0, y1 = doc['years'][0], doc['years'][-1]
    s = series(years=f'{y0}-{y1}', deg=deg)
    out = {'span': [y0, y1], 'years': len(doc['years']), 'deg': deg,
           'grid': list(R.grid_shape(deg)),
           'land_km2': s['land_km2'][-1],
           'change': s['change'],
           'headline': []}
    for c in S.CLASSES:
        ch = s['change'][c['key']]
        out['headline'].append({'class': c['key'], 'name': c['name'],
                                'delta_km2': ch['delta'], 'pct': ch['pct']})
    out['headline'].sort(key=lambda h: -abs(h['delta_km2']))
    tdoc = cube.load('transitions', deg, quiet=True)
    if tdoc.get('ready'):
        t = transitions(deg=deg)
        out['gross_km2'] = t['gross_km2']
        out['net_km2'] = t['net_km2']
        out['gross_over_net'] = t['gross_over_net']
        out['top_flows'] = t['top_flows'][:5]
    return out
