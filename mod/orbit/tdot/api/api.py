"""
tdot API — FastAPI wrapper over the tdot mod.

Serves the layer catalogue and every map layer as GeoJSON. Launched/killed via
``mod.py serve_api() / kill()``.

GeoJSON is verbose and highly repetitive, so responses are gzipped — the
cycling network drops from several MB to well under 1 MB on the wire. Layer
responses are also given a long browser cache lifetime, since the underlying
open data updates weekly at most.
"""

import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

import mod as m

_tdot = None


def tdot():
    global _tdot
    if _tdot is None:
        _tdot = m.mod('tdot')()
    return _tdot


app = FastAPI(
    title='Toronto GIS API',
    description=('Open-source GIS for Toronto — police-reported crime, TTC '
                 'transit, cycling, parks and civic data as GeoJSON layers. '
                 'All sources are public open data; no API keys anywhere.'),
    version='2.0.0')

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True,
                   allow_methods=['*'], allow_headers=['*'])

# Layer geometry changes weekly at most; let the browser hold onto it.
LAYER_CACHE = 'public, max-age=3600, stale-while-revalidate=86400'


def geo(payload: dict, cache: str = LAYER_CACHE) -> JSONResponse:
    return JSONResponse(payload, headers={'Cache-Control': cache})


@app.get('/')
def root():
    return tdot().info()


@app.get('/health')
def health():
    return tdot().health()


@app.get('/view')
def view():
    """Default map camera + district quick-jump targets."""
    return tdot().view()


@app.get('/layers')
def layers():
    """The layer catalogue that drives the UI's layer panel."""
    return geo(tdot().layers(), cache='public, max-age=300')


@app.get('/options')
def options():
    """Metrics, geographies and crime categories for the choropleth controls."""
    return geo(tdot().options(), cache='public, max-age=3600')


@app.get('/layers/crime')
def crime(
    metric: str = Query('incidents'),
    geography: str = Query('neighbourhood'),
    since: str = Query('2025-01-01'),
    until: Optional[str] = Query(None),
    category: str = Query('all'),
):
    """A crime choropleth as GeoJSON, with quantile class breaks."""
    out = tdot().crime(metric=metric, geography=geography, since=since,
                       until=until, category=category)
    if isinstance(out, dict) and out.get('error'):
        raise HTTPException(status_code=400, detail=out)
    return geo(out)


@app.get('/layers/incidents')
def incidents(
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    category: str = Query('all'),
    limit: int = Query(15000, ge=1, le=50000),
):
    """Recent individual incidents as points."""
    return geo(tdot().incidents(since=since, until=until, category=category,
                                limit=limit))


@app.get('/layers/{layer_id}')
def layer(layer_id: str):
    """Any catalogue layer as a GeoJSON FeatureCollection."""
    try:
        return geo(tdot().layer(layer_id))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f'upstream source failed: {type(e).__name__}: {e}')


@app.get('/boundary/{name}')
def boundary(name: str):
    try:
        return geo(tdot().boundary(name))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get('/summary')
def summary(since: str = Query('2025-01-01'), until: Optional[str] = Query(None),
            category: str = Query('all')):
    """City-wide crime summary: totals, highest/lowest areas, biggest movers."""
    return geo(tdot().summary(since=since, until=until, category=category),
               cache='public, max-age=3600')


@app.get('/trend')
def trend(area: Optional[str] = Query(None),
          geography: str = Query('neighbourhood'),
          category: str = Query('all')):
    """Yearly incident counts — city-wide, or for one area."""
    return geo(tdot().trend(area=area, geography=geography, category=category),
               cache='public, max-age=3600')


@app.get('/where')
def where(q: str = Query(..., min_length=1), limit: int = Query(6, ge=1, le=20)):
    """Geocode an address or place within Toronto (OpenStreetMap Nominatim)."""
    try:
        return tdot().where(q, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'geocoder failed: {e}')


@app.get('/cache')
def cache():
    return tdot().cache()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 50320)))
