"""
tdot API — FastAPI wrapper over the tdot mod.

Serves the layer catalogue and every map layer as GeoJSON. Launched/killed via
``mod.py serve_api() / kill()``.

GeoJSON is verbose and highly repetitive, so responses are gzipped — the
cycling network drops from several MB to well under 1 MB on the wire. Layer
responses are also given a long browser cache lifetime, since the underlying
open data updates weekly at most.
"""

import json
import os
import sys
import threading
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

import mod as m
from tdotgis import agent as A
from tdotgis import registry as R
from tdotgis import tools as T

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


@app.on_event('startup')
def warm_score_model() -> None:
    """
    Fit the score model in the background as the API comes up.

    The layer, the report and the outlier list all come off disk, but a
    per-building explanation needs the fold estimators in memory, and fitting
    them takes ~25 seconds. Doing it at startup means the first person to click
    a building waits for a round trip rather than for a gradient booster.
    """
    def fit():
        try:
            from tdotgis import score as SC
            SC.model()
        except Exception as e:                      # a warm-up is never fatal
            print(f'tdot: score model not warmed ({type(e).__name__}: {e})')

    threading.Thread(target=fit, name='tdot-score-warm', daemon=True).start()


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
    """
    The layer catalogue that drives the UI's layer panel.

    Deliberately uncacheable: adding a dataset changes it, and a browser
    holding a five-minute-old copy would show the person a map missing the
    layer they just added. It is a few KB — correctness is worth more.
    """
    return geo(tdot().layers(), cache='no-cache')


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
def layer(layer_id: str, refresh: bool = Query(False)):
    """Any catalogue layer as a GeoJSON FeatureCollection."""
    try:
        return geo(tdot().layer(layer_id, refresh=refresh))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f'upstream source failed: {type(e).__name__}: {e}')


# ── open-data registry ──────────────────────────────────────────────────────
# Adding a dataset is a first-class operation, not an admin chore: the same
# calls back the CLI, the browser's "Add open data" panel and the chat agent.

@app.get('/datasets')
def datasets():
    """Every spec-driven layer — the builtin ones and the ones you added."""
    return tdot().datasets()


@app.get('/datasets/search')
def datasets_search(q: str = Query(..., min_length=2), rows: int = Query(10, ge=1, le=40)):
    """Search Toronto Open Data for something to put on the map."""
    try:
        return {'results': tdot().find_data(q, rows=rows), 'query': q}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'portal search failed: {e}')


@app.post('/datasets')
def datasets_add(body: dict = Body(...)):
    """Add a CKAN package as a map layer. The spec is worked out from the data."""
    package = str(body.get('package') or '').strip()
    if not package:
        raise HTTPException(status_code=400, detail='package is required')
    out = tdot().add_data(package,
                          id=body.get('id'), title=body.get('title'),
                          category=body.get('category') or 'Open data',
                          resource=body.get('resource'))
    if not out.get('ok'):
        raise HTTPException(status_code=422, detail=out.get('error'))
    return out


@app.get('/datasets/{dataset_id}')
def dataset(dataset_id: str):
    """One dataset's spec — the recipe its layer is built from."""
    out = tdot().dataset(dataset_id)
    if out.get('error'):
        raise HTTPException(status_code=404, detail=out['error'])
    return out


@app.delete('/datasets/{dataset_id}')
def dataset_remove(dataset_id: str):
    out = tdot().remove_data(dataset_id)
    if not out.get('ok'):
        raise HTTPException(status_code=400, detail=out.get('error'))
    return out


# ── housing ─────────────────────────────────────────────────────────────────

@app.get('/housing')
def housing(role: Optional[str] = Query(None),
            live: bool = Query(True)):
    """
    Every open dataset the city publishes about housing, and what tdot does
    with each: drawn as a layer, fed to the score model, open-but-unmappable,
    or not open at all.
    """
    try:
        return geo(tdot().housing(role=role, live=live),
                   cache='public, max-age=3600')
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get('/housing/search')
def housing_search(q: str = Query('housing', min_length=2),
                   rows: int = Query(20, ge=1, le=50)):
    """Housing datasets on the portal that the catalogue does not yet cover."""
    try:
        return {'query': q, 'results': tdot().housing_search(q, rows=rows)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'portal search failed: {e}')


# ── the score model ─────────────────────────────────────────────────────────

@app.get('/score/model')
def score_model(refresh: bool = Query(False)):
    """
    How well open data predicts a building's RentSafeTO score: out-of-fold
    accuracy against a mean baseline, what drives it, and what it cannot see.
    """
    try:
        return geo(tdot().score(refresh=refresh), cache='public, max-age=3600')
    except RuntimeError as e:                       # scikit-learn missing
        raise HTTPException(status_code=501, detail=str(e))


@app.get('/score/outliers')
def score_outliers(direction: str = Query('under', pattern='^(under|over)$'),
                   limit: int = Query(25, ge=1, le=200)):
    """The buildings furthest from what comparable buildings score."""
    try:
        return geo(tdot().outliers(direction=direction, limit=limit),
                   cache='public, max-age=3600')
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get('/score/building/{rsn}')
def score_building(rsn: str):
    """One building: its filing, its prediction, and what is driving it."""
    try:
        return geo(tdot().building(rsn), cache='public, max-age=3600')
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))


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


# ── agent ───────────────────────────────────────────────────────────────────

@app.get('/agent/card')
@app.get('/.well-known/agent.json')
def agent_card():
    """Who the agent is and what it can do — the discovery document."""
    return A.card()


@app.get('/agent/status')
def agent_status():
    return A.status()


@app.get('/agent/tools')
def agent_tools():
    """The tools the agent plays, grouped — this is also the console's help."""
    return {'groups': T.docs(), 'count': len(T.TOOLS)}


@app.post('/agent/chat')
def agent_chat(body: dict = Body(...)):
    """
    Ask the map a question. Streams SSE events; ``map`` events carry an action
    the browser applies to the live map.
    """
    question = str(body.get('message') or body.get('question') or '').strip()
    if not question:
        raise HTTPException(status_code=400, detail='message is required')
    session = body.get('session') or None

    def stream():
        try:
            for ev in A.chat(question, session=session):
                yield f'data: {json.dumps(ev, default=str)}\n\n'
        except Exception as e:                      # never hang an open stream
            yield f'data: {json.dumps({"type": "error", "error": str(e)})}\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',                  # let the gateway pass it through
    })


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 50320)))
