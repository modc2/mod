"""
hilda API — HILDA+ land use change, served.

Read-only and public: every byte behind this API is CC-BY-4.0 open data from
PANGAEA, so there is nothing here to gate. The only endpoints that reach the
network are /window (fetches one year's raster on demand) and /ingest, which
is deliberately not exposed — building the cube is a CLI job, not a request.

Routes fall into four groups:

    catalogue   /info /classes /regions /status
    spatial     /grid.bin /grid.png /layer.png /change.png /window.png /cell
    temporal    /series /areas /net /transitions /hotspots /summary
    model       /ca/run /ca/validate /ca/calibrate /ca/frame.png
"""

import io
import os
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

MODULE_DIR = Path(__file__).resolve().parent.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from hildaplus import automata as A            # noqa: E402
from hildaplus import cube as C                # noqa: E402
from hildaplus import raster as R              # noqa: E402
from hildaplus import remote                   # noqa: E402
from hildaplus import render as D              # noqa: E402
from hildaplus import series as T              # noqa: E402
from hildaplus import sources as S             # noqa: E402

app = FastAPI(title='hilda',
              description='HILDA+ global land use change, 1960-2019 — '
                          'longitudinal, spatial, and as a cellular automaton',
              version='1.0.0')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])

STARTED = time.time()


@app.exception_handler(ValueError)
async def _value_error(request: Request, exc: ValueError):
    return JSONResponse({'error': str(exc)}, status_code=400)


@app.exception_handler(RuntimeError)
async def _runtime_error(request: Request, exc: RuntimeError):
    # The commonest RuntimeError by far is "the cube has not been built yet",
    # which is a state problem the caller can fix, not a server fault.
    return JSONResponse({'error': str(exc), 'ready': False}, status_code=409)


def _png(data: bytes, max_age: int = 3600) -> Response:
    return Response(content=data, media_type='image/png',
                    headers={'cache-control': f'public, max-age={max_age}'})


# ── catalogue ────────────────────────────────────────────────────────────

@app.get('/')
@app.get('/info')
def info():
    st = C.status()
    return {
        'module': 'hilda', 'version': '1.0.0',
        'dataset': 'HILDA+ (HIstoric Land Dynamics Assessment+) v1.0',
        'what': 'annual global land use / land cover, 1 km, 1960-2019, '
                'reduced to a 0.5 degree cube and stepped as a cellular '
                'automaton',
        'source': S.attribution(),
        'grid': {'deg': S.DEFAULT_DEG, 'shape': list(R.grid_shape()),
                 'source_grid': [S.SRC_W, S.SRC_H], 'source_deg': S.SRC_DEG,
                 'crs': 'EPSG:4326'},
        'classes': D.palette(),
        'regions': [{'key': k, **v} for k, v in S.REGIONS.items()],
        'ready': st['ready'],
        'years': st['state_years'],
        'cache': st,
        'uptime_s': round(time.time() - STARTED, 1),
    }


@app.get('/health')
def health():
    st = C.status()
    return {'ok': True, 'ready': st['ready'], 'years': len(st['state_years'])}


@app.get('/classes')
def classes():
    return {'classes': D.palette(),
            'note': 'index is the byte used in classified grids; code is the '
                    'raw HILDA+ pixel value'}


@app.get('/regions')
def regions():
    return {'regions': [{'key': k, **v} for k, v in S.REGIONS.items()],
            'note': 'bounding boxes, west/south/east/north — any endpoint that '
                    'takes region also takes an explicit bbox'}


@app.get('/status')
def status():
    return C.status()


# ── spatial ──────────────────────────────────────────────────────────────

@app.get('/grid.bin')
def grid_bin(request: Request, years: Optional[str] = None,
             deg: float = S.DEFAULT_DEG):
    """The whole classified record as one binary blob — see render.pack_grid."""
    want = C.parse_years(years) if years else None
    ys, grids = D.dominant_cube(want, deg)
    payload = D.pack_grid(ys, grids, deg)
    headers = {'cache-control': 'public, max-age=600',
               'x-hilda-years': f'{ys[0]}-{ys[-1]}' if ys else 'none',
               'x-hilda-shape': ','.join(str(x) for x in grids.shape)}
    if 'gzip' in (request.headers.get('accept-encoding') or ''):
        return Response(D.gzipped(payload), media_type='application/octet-stream',
                        headers={**headers, 'content-encoding': 'gzip'})
    return Response(payload, media_type='application/octet-stream', headers=headers)


@app.get('/grid.png')
def grid_png(year: int = Query(..., description='year to draw'),
             scale: int = 2, deg: float = S.DEFAULT_DEG):
    """The dominant land use class per cell, as an image."""
    y = C.nearest_year(year, 'states', deg)
    return _png(D.png_from_classified(D.classify(C.frame(y, deg)),
                                      max(1, min(int(scale), 8))))


@app.get('/layer.png')
def layer_png(year: int, cls: str = Query(..., alias='class'),
              scale: int = 2, deg: float = S.DEFAULT_DEG):
    """One class's fractional cover, as an eight-step ramp."""
    i = S.resolve_class(cls)
    y = C.nearest_year(year, 'states', deg)
    f = C.fractions(y, deg)[i]
    return _png(D.png_from_intensity(f, S.CLASSES[i]['color'],
                                     max(1, min(int(scale), 8)), vmax=1.0))


@app.get('/change.png')
def change_png(y0: Optional[int] = None, y1: Optional[int] = None,
               scale: int = 2, deg: float = S.DEFAULT_DEG):
    """Gross turnover per cell over a span — where the world churned."""
    ci = T.change_intensity(y0, y1, deg)
    return _png(D.png_from_intensity(ci, '#ffb02e', max(1, min(int(scale), 8)),
                                     vmax=float(np.percentile(ci, 99.5)) or 1.0))


@app.get('/window.png')
def window_png(bbox: str, year: int = 2019, kind: str = 'states'):
    """A bbox at the full 1 km source resolution.

    Reads only the rows it needs out of the year's GeoTIFF, fetching that
    raster from PANGAEA first if it is not cached. Bounded to a sane area so a
    stray request cannot ask for the globe at 1 km.
    """
    box = S.resolve_bbox(None, bbox)
    span = abs(box[2] - box[0]) * abs(box[3] - box[1])
    if span > 2500:
        raise HTTPException(413, f'window covers {span:.0f} square degrees; '
                                 'the 1 km reader is capped at 2500 '
                                 '(about 50x50 degrees)')
    path = remote.fetch_year(year, kind)
    with R.Raster(path) as src:
        arr, snapped = src.window(box)
    remote.clear(kind, keep=4)              # keep the cache from creeping
    return Response(D.png_from_codes(arr), media_type='image/png',
                    headers={'cache-control': 'public, max-age=3600',
                             'x-hilda-bbox': ','.join(f'{v:g}' for v in snapped),
                             'x-hilda-shape': f'{arr.shape[0]},{arr.shape[1]}'})


@app.get('/cell')
def cell(lon: float, lat: float, deg: float = S.DEFAULT_DEG):
    """Every year of one grid cell — the inspector's payload."""
    return T.cell(lon, lat, deg)


# ── temporal ─────────────────────────────────────────────────────────────

@app.get('/series')
def series(region: Optional[str] = None, bbox: Optional[str] = None,
           years: Optional[str] = None, deg: float = S.DEFAULT_DEG):
    return T.series(region, bbox, years, deg)


@app.get('/areas')
def areas(year: int, region: Optional[str] = None, bbox: Optional[str] = None,
          deg: float = S.DEFAULT_DEG):
    return T.areas(year, region, bbox, deg)


@app.get('/net')
def net(y0: Optional[int] = None, y1: Optional[int] = None,
        region: Optional[str] = None, bbox: Optional[str] = None,
        deg: float = S.DEFAULT_DEG):
    return T.net_change(y0, y1, region, bbox, deg)


@app.get('/transitions')
def transitions(y0: Optional[int] = None, y1: Optional[int] = None,
                deg: float = S.DEFAULT_DEG):
    return T.transitions(y0, y1, deg)


@app.get('/hotspots')
def hotspots(y0: Optional[int] = None, y1: Optional[int] = None, n: int = 20,
             region: Optional[str] = None, bbox: Optional[str] = None,
             deg: float = S.DEFAULT_DEG):
    return T.hotspots(y0, y1, n, region, bbox, deg)


@app.get('/summary')
def summary(deg: float = S.DEFAULT_DEG):
    return T.summary(deg)


# ── the automaton ────────────────────────────────────────────────────────

@app.get('/ca/rates')
def ca_rates(y0: Optional[int] = None, y1: Optional[int] = None,
             deg: float = S.DEFAULT_DEG):
    """The observed annual transition matrix the automaton runs on."""
    matrix, provenance = A.rates(y0, y1, deg)
    keys = [c['key'] for c in S.CLASSES]
    return {'classes': keys, 'source': provenance,
            'annual_probability': [[float(v) for v in row] for row in matrix],
            'note': 'row i, column j is the share of class i that became '
                    'class j in an average year; the diagonal is persistence'}


@app.get('/ca/run')
def ca_run(start: Optional[int] = None, end: Optional[int] = None,
           weight: float = A.DEFAULT_NEIGHBOURHOOD_WEIGHT,
           scenario: Optional[str] = None, protect: Optional[str] = None,
           frames: bool = False, deg: float = S.DEFAULT_DEG):
    """Run the automaton. ``scenario`` is e.g. ``urban=2,forest=0.5``."""
    r = A.run(start, end, weight, scenario, protect, deg=deg,
              keep_frames=bool(frames))
    packed = r.pop('_frames', None)
    if frames and packed:
        ys = sorted(packed)
        grids = np.stack([D.classify(packed[y]) for y in ys])
        r['frames'] = {'years': ys, 'shape': list(grids.shape),
                       'grid_b64': _b64(D.gzipped(D.pack_grid(ys, grids, deg))),
                       'encoding': 'gzip(pack_grid) base64'}
    return r


@app.get('/ca/validate')
def ca_validate(start: Optional[int] = None, end: Optional[int] = None,
                weight: float = A.DEFAULT_NEIGHBOURHOOD_WEIGHT,
                deg: float = S.DEFAULT_DEG):
    return A.validate(start, end, weight, deg)


@app.get('/ca/calibrate')
def ca_calibrate(start: Optional[int] = None, end: Optional[int] = None,
                 deg: float = S.DEFAULT_DEG):
    return A.calibrate(start, end, deg=deg)


@app.get('/ca/frame.png')
def ca_frame_png(year: int, weight: float = A.DEFAULT_NEIGHBOURHOOD_WEIGHT,
                 start: Optional[int] = None, which: str = 'sim',
                 scale: int = 2, deg: float = S.DEFAULT_DEG):
    """One simulated year as an image; ``which=obs`` draws the truth instead."""
    r = A.compare_frame(year, weight, start, deg)
    grid = D.classify(r['obs'] if which == 'obs' else r['sim'])
    return _png(D.png_from_classified(grid, max(1, min(int(scale), 8))))


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 50550)))
