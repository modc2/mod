"""
nyc API — FastAPI wrapper over the nyc mod.

Serves the layer catalogue and every map layer as GeoJSON. Launched/killed via
``mod.py serve_api() / kill()``.

GeoJSON is verbose and highly repetitive, so responses are gzipped — the bike
network drops from ~6 MB to well under 1 MB on the wire. Layer responses are
also given a long browser cache lifetime, since the underlying open data
updates daily at most.
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

_nyc = None


def nyc():
    global _nyc
    if _nyc is None:
        _nyc = m.mod('nyc')()
    return _nyc


app = FastAPI(
    title='NYC GIS API',
    description=('Open-source GIS for New York City — housing prices, transit, '
                 'parks and civic data as GeoJSON layers. All sources are public '
                 'open data; no API keys anywhere.'),
    version='1.0.0')

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True,
                   allow_methods=['*'], allow_headers=['*'])

# Layer geometry changes daily at most; let the browser hold onto it.
LAYER_CACHE = 'public, max-age=3600, stale-while-revalidate=86400'


def geo(payload: dict, cache: str = LAYER_CACHE) -> JSONResponse:
    return JSONResponse(payload, headers={'Cache-Control': cache})


@app.get('/')
def root():
    return nyc().info()


@app.get('/health')
def health():
    return nyc().health()


@app.get('/view')
def view():
    """Default map camera + borough quick-jump targets."""
    return nyc().view()


@app.get('/layers')
def layers():
    """The layer catalogue that drives the UI's layer panel."""
    return geo(nyc().layers(), cache='public, max-age=300')


@app.get('/options')
def options():
    """Metrics, geographies and property types for the housing controls."""
    return geo(nyc().options(), cache='public, max-age=3600')


@app.get('/layers/housing_prices')
def housing_prices(
    metric: str = Query('median_price'),
    geography: str = Query('nta'),
    since: str = Query('2024-01-01'),
    until: Optional[str] = Query(None),
    property_type: str = Query('residential'),
):
    """A housing-price choropleth as GeoJSON, with quantile class breaks."""
    out = nyc().housing(metric=metric, geography=geography, since=since,
                        until=until, property_type=property_type)
    if isinstance(out, dict) and out.get('error'):
        raise HTTPException(status_code=400, detail=out)
    return geo(out)


@app.get('/layers/sales')
def sales(
    since: str = Query('2025-01-01'),
    until: Optional[str] = Query(None),
    property_type: str = Query('residential'),
    limit: int = Query(12000, ge=1, le=50000),
    min_price: Optional[int] = Query(None),
    max_price: Optional[int] = Query(None),
):
    """Individual recorded sales as points."""
    return geo(nyc().sales(since=since, until=until, property_type=property_type,
                           limit=limit, min_price=min_price, max_price=max_price))


@app.get('/layers/{layer_id}')
def layer(layer_id: str):
    """Any catalogue layer as a GeoJSON FeatureCollection."""
    try:
        return geo(nyc().layer(layer_id))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f'upstream source failed: {type(e).__name__}: {e}')


@app.get('/boundary/{name}')
def boundary(name: str):
    try:
        return geo(nyc().boundary(name))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get('/prices')
def prices(since: str = Query('2024-01-01'), until: Optional[str] = Query(None),
           property_type: str = Query('residential')):
    """City-wide price summary: totals, most/least expensive, biggest movers."""
    return geo(nyc().prices(since=since, until=until, property_type=property_type),
               cache='public, max-age=3600')


@app.get('/trend')
def trend(area: Optional[str] = Query(None), geography: str = Query('nta'),
          property_type: str = Query('residential'),
          start_year: int = Query(2016, ge=2016, le=2100)):
    """Yearly median price / $ per ft² — city-wide, or for one area."""
    return geo(nyc().trend(area=area, geography=geography,
                           property_type=property_type, start_year=start_year),
               cache='public, max-age=3600')


@app.get('/where')
def where(q: str = Query(..., min_length=1), limit: int = Query(6, ge=1, le=20)):
    """Geocode an address or place within NYC (OpenStreetMap Nominatim)."""
    try:
        return nyc().where(q, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'geocoder failed: {e}')


@app.get('/cache')
def cache():
    return nyc().cache()


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 50310)))
