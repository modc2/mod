"""
nyc.scene — what the chat agent puts on screen.

The agent answers in words; this module is how it also answers in *pictures*.
Two tools turn a model's intent into a declarative, validated directive the
browser applies as-is:

* :func:`map_directive` (``nyc_map``) — which layers are on, the housing
  choropleth's filters, a value-range filter, highlighted areas, where the
  camera goes, and an **agent overlay**: any dataset on NYC / NY State Open
  Data drawn as points, a heatmap, or a ZIP / borough choropleth.
* :func:`infographic` (``nyc_infographic``) — a card of headline numbers, a
  ranked bar list, a small time series and the sources behind them.

Both are pure: they hold no state and write nothing. The *browser* holds the
map; the directive travels back through the chat stream (the API reads it out
of the tool result) and the page applies it. Validating here rather than in the
page means a bad layer id or metric comes back to the model as an error it can
fix, instead of silently doing nothing on screen.

Overlay data is computed here (and cached) so that the tool can tell the model
what it just drew — counts, the top areas — and the browser then fetches the
same cached result from ``GET /overlay``.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from . import layers as L
from . import prices as P
from . import sources as S

BASEMAPS = ('dark', 'light', 'streets')
OVERLAY_MODES = ('points', 'heat', 'areas')
AREA_BY = ('zip', 'borough')
MAX_POINTS = 5000

DOMAINS = {'nyc': 'data.cityofnewyork.us', 'nys': 'data.ny.gov'}

# Socrata identifiers are always four-four; anything else is either a typo or
# an attempt to smuggle a path into the URL.
_DATASET_ID = re.compile(r'^[a-z0-9]{4}-[a-z0-9]{4}$')
# Column names in SoQL are lower-snake identifiers.
_COLUMN = re.compile(r'^[a-z_][a-z0-9_]*$')

ZIP_COLUMNS = ('incident_zip', 'zip_code', 'zipcode', 'zip', 'postcode',
               'postal_code', 'zip_code_1')
BOROUGH_COLUMNS = ('borough', 'boro', 'boroname', 'boro_nm', 'borough_name',
                   'arrest_boro', 'boro_name', 'city_borough')
LAT_COLUMNS = ('latitude', 'lat', 'y_latitude')
LNG_COLUMNS = ('longitude', 'lon', 'lng', 'long', 'x_longitude')

BOROUGHS = {
    # every spelling the portal uses → canonical name
    'manhattan': 'Manhattan', 'new york': 'Manhattan', 'mn': 'Manhattan',
    'm': 'Manhattan', '1': 'Manhattan',
    'bronx': 'Bronx', 'the bronx': 'Bronx', 'bx': 'Bronx', 'b': 'Bronx', '2': 'Bronx',
    'brooklyn': 'Brooklyn', 'kings': 'Brooklyn', 'bk': 'Brooklyn',
    'k': 'Brooklyn', '3': 'Brooklyn',
    'queens': 'Queens', 'qn': 'Queens', 'q': 'Queens', '4': 'Queens',
    'staten island': 'Staten Island', 'richmond': 'Staten Island',
    'si': 'Staten Island', 'r': 'Staten Island', 's': 'Staten Island',
    '5': 'Staten Island',
}
BOROUGH_POP = {'Manhattan': 1629153, 'Bronx': 1472654, 'Brooklyn': 2590516,
               'Queens': 2270976, 'Staten Island': 495747}


def _domain(name: Optional[str]) -> str:
    key = str(name or 'nyc').strip().lower()
    if key in DOMAINS:
        return DOMAINS[key]
    if key in DOMAINS.values():
        return key
    raise ValueError(f'domain must be "nyc" or "nys", got {name!r}')


def _layer_ids() -> List[str]:
    return [l['id'] for l in L.LAYERS]


def _ids(value: Any, known: List[str], what: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r'[,\s]+', value) if v]
    out = []
    for v in value:
        v = str(v).strip()
        if v not in known:
            raise ValueError(f'unknown {what} {v!r}; known: {known}')
        if v not in out:
            out.append(v)
    return out


def _list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(',')
    return [str(v).strip() for v in value if str(v).strip()][:300]


# ─────────────────────────────────────────────────────────────── columns

def _columns(dataset: str, domain: str) -> List[dict]:
    from .tools import dataset_meta
    return dataset_meta(dataset, domain=domain).get('columns', [])


def _pick(cols: List[dict], wanted: tuple, given: Optional[str]) -> Optional[str]:
    names = [c['field'] for c in cols]
    if given:
        if not _COLUMN.match(given):
            raise ValueError(f'bad column name {given!r}')
        if names and given not in names:
            raise ValueError(f'column {given!r} not in dataset; columns: {names}')
        return given
    for w in wanted:
        if w in names:
            return w
    return None


def _geo_column(cols: List[dict]) -> Optional[str]:
    for c in cols:
        if c.get('type') in ('point', 'location'):
            return c['field']
    return None


def _coords(row: dict, lat: Optional[str], lng: Optional[str],
            geo: Optional[str]) -> Optional[List[float]]:
    try:
        if lat and lng and row.get(lat) and row.get(lng):
            y, x = float(row[lat]), float(row[lng])
        elif geo and isinstance(row.get(geo), dict):
            g = row[geo]
            if 'coordinates' in g:
                x, y = float(g['coordinates'][0]), float(g['coordinates'][1])
            else:
                y, x = float(g['latitude']), float(g['longitude'])
        else:
            return None
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    # Zero-filled and out-of-region coordinates are common in city files;
    # a dot in the Atlantic is worse than no dot.
    if not (40.4 < y < 41.0 and -74.35 < x < -73.6):
        return None
    return [round(x, 6), round(y, 6)]


# ─────────────────────────────────────────────────────────────── overlay

def _overlay_key(spec: dict) -> str:
    blob = json.dumps(spec, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _quantile_stops(values: List[float], classes: int = 6) -> List[float]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return []
    stops = []
    for i in range(1, classes):
        v = vals[min(len(vals) - 1, int(len(vals) * i / classes))]
        if not stops or v > stops[-1]:
            stops.append(v)
    return stops


def normalize_overlay(spec: dict) -> dict:
    """Validate an overlay request; returns the canonical spec (no I/O beyond metadata)."""
    if not isinstance(spec, dict):
        raise ValueError('overlay must be an object')
    dataset = str(spec.get('dataset') or '').strip().lower()
    if not _DATASET_ID.match(dataset):
        raise ValueError(f'overlay.dataset must be a Socrata id like "erm2-nwe9", got {dataset!r}')
    mode = str(spec.get('mode') or 'points').lower()
    if mode not in OVERLAY_MODES:
        raise ValueError(f'overlay.mode must be one of {OVERLAY_MODES}')
    out: Dict[str, Any] = {
        'dataset': dataset,
        'domain': _domain(spec.get('domain')),
        'mode': mode,
        'where': (str(spec['where']).strip() or None) if spec.get('where') else None,
        'title': str(spec.get('title') or dataset)[:80],
    }
    if mode == 'areas':
        by = str(spec.get('by') or 'zip').lower()
        if by not in AREA_BY:
            raise ValueError(f'overlay.by must be one of {AREA_BY}')
        out['by'] = by
        out['column'] = spec.get('column') or None
        value = str(spec.get('value') or 'count(*)').strip()
        # An aggregate over one column, nothing else: this goes straight into
        # $select, so it must not be able to carry a second clause.
        if not re.match(r'^(count\(\*\)|(count|sum|avg|min|max)\([a-z_][a-z0-9_]*\))$', value):
            raise ValueError('overlay.value must be count(*) or sum/avg/min/max/count(<column>)')
        out['value'] = value
        out['per_capita'] = bool(spec.get('per_capita'))
    else:
        out['lat'] = spec.get('lat') or None
        out['lng'] = spec.get('lng') or None
        out['label'] = spec.get('label') or None
        # A heatmap only reads with density behind it; dots get unreadable first.
        out['limit'] = max(1, min(int(spec.get('limit') or (MAX_POINTS if mode == 'heat' else 2000)),
                                  MAX_POINTS))
    return out


def overlay_data(spec: dict) -> dict:
    """Run a (normalized) overlay and return GeoJSON + a summary, cached 6h."""
    spec = normalize_overlay(spec)
    key = f'overlay-{_overlay_key(spec)}'
    return S.cached(key, 6 * 3600, lambda: _build_overlay(spec))


def _build_overlay(spec: dict) -> dict:
    dom = f"https://{spec['domain']}"
    cols = _columns(spec['dataset'], spec['domain'])
    if spec['mode'] == 'areas':
        return _build_areas(spec, dom, cols)

    lat = _pick(cols, LAT_COLUMNS, spec.get('lat'))
    lng = _pick(cols, LNG_COLUMNS, spec.get('lng'))
    geo = _geo_column(cols)
    if not ((lat and lng) or geo):
        raise ValueError('dataset has no latitude/longitude or point column to plot; '
                         'try mode="areas" with by="zip" or "borough"')
    label = _pick(cols, (), spec.get('label')) if spec.get('label') else None
    select = [c for c in (lat, lng, geo, label) if c]
    where = spec['where']
    # Skip rows with no position server-side, so the limit buys real dots.
    has_pos = f'{lat} IS NOT NULL' if (lat and lng) else f'{geo} IS NOT NULL'
    where = f'({where}) AND {has_pos}' if where else has_pos
    rows = S.soql(dom, spec['dataset'], select=','.join(dict.fromkeys(select)),
                  where=where, limit=spec['limit'])
    feats = []
    for r in rows:
        c = _coords(r, lat, lng, geo)
        if not c:
            continue
        props = {'label': str(r.get(label, ''))[:120]} if label else {}
        feats.append({'type': 'Feature', 'properties': props,
                      'geometry': {'type': 'Point', 'coordinates': c}})
    return {'type': 'FeatureCollection', 'features': feats,
            'meta': {**spec, 'rows': len(rows), 'plotted': len(feats),
                     'capped': len(rows) >= spec['limit'],
                     'url': f"{dom}/d/{spec['dataset']}"}}


def _build_areas(spec: dict, dom: str, cols: List[dict]) -> dict:
    by = spec['by']
    col = _pick(cols, ZIP_COLUMNS if by == 'zip' else BOROUGH_COLUMNS, spec.get('column'))
    if not col:
        raise ValueError(f'no {by} column found; pass overlay.column '
                         f'(columns: {[c["field"] for c in cols]})')
    rows = S.soql(dom, spec['dataset'], select=f"{col} AS k, {spec['value']} AS v",
                  where=spec['where'], group=col, limit=50000)

    bounds = L.boundary(by)
    totals: Dict[str, float] = {}
    if by == 'zip':
        parent: Dict[str, str] = {}
        for f in bounds['features']:
            p = f['properties']
            for z in str(p.get('zcta', p.get('modzcta', ''))).split(','):
                if z.strip():
                    parent[z.strip()] = str(p['modzcta'])
        for r in rows:
            z = str(r.get('k') or '').strip()[:5]
            if z in parent:
                totals[parent[z]] = totals.get(parent[z], 0) + _num(r.get('v'))
        join, name_of = 'modzcta', (lambda p: p.get('label') or p.get('modzcta'))
        pop_of = lambda p: _num(p.get('pop_est'))
    else:
        for r in rows:
            b = BOROUGHS.get(str(r.get('k') or '').strip().lower())
            if b:
                totals[b] = totals.get(b, 0) + _num(r.get('v'))
        join, name_of = 'boroname', (lambda p: p.get('boroname'))
        pop_of = lambda p: BOROUGH_POP.get(p.get('boroname'), 0)

    feats, values = [], []
    for f in bounds['features']:
        p = f['properties']
        area = str(p.get(join))
        raw = totals.get(area)
        val = raw
        if raw is not None and spec['per_capita']:
            pop = pop_of(p)
            val = round(raw / pop * 10000, 2) if pop else None
        if val is not None:
            values.append(val)
        feats.append({'type': 'Feature', 'geometry': f['geometry'],
                      'properties': {'area': area, 'name': name_of(p),
                                     'value': val, 'raw': raw}})
    ranked = sorted((f['properties'] for f in feats if f['properties']['value'] is not None),
                    key=lambda p: p['value'], reverse=True)
    return {'type': 'FeatureCollection', 'features': feats,
            'breaks': {'stops': _quantile_stops(values),
                       'min': min(values) if values else None,
                       'max': max(values) if values else None},
            'meta': {**spec, 'column': col, 'areas': len(feats),
                     'areas_with_data': len(values),
                     'unit': 'per 10k residents' if spec['per_capita'] else spec['value'],
                     'top': [{'name': p['name'], 'value': p['value']} for p in ranked[:8]],
                     'bottom': [{'name': p['name'], 'value': p['value']} for p in ranked[-3:]],
                     'url': f"{dom}/d/{spec['dataset']}"}}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def overlay_url(spec: dict) -> str:
    """The query string the browser fetches the overlay with (`GET /overlay`)."""
    return '/overlay?' + urlencode({'spec': json.dumps(spec, sort_keys=True)})


# ─────────────────────────────────────────────────────────────── nyc_map

def map_directive(layers: Any = None, add: Any = None, remove: Any = None,
                  basemap: Optional[str] = None, metric: Optional[str] = None,
                  geography: Optional[str] = None, since: Optional[str] = None,
                  until: Optional[str] = None, property_type: Optional[str] = None,
                  min_value: Optional[float] = None, max_value: Optional[float] = None,
                  highlight: Any = None, only: Any = None,
                  focus: Optional[str] = None,
                  lat: Optional[float] = None, lng: Optional[float] = None,
                  zoom: Optional[float] = None, overlay: Any = None,
                  clear_overlay: bool = False, reset: bool = False,
                  caption: Optional[str] = None) -> dict:
    """Validate a map change and return the directive the browser applies."""
    known = _layer_ids()
    d: Dict[str, Any] = {'kind': 'map'}
    notes: List[str] = []
    if reset:
        d['reset'] = True
    if layers is not None:
        d['layers'] = _ids(layers, known, 'layer')
    if add is not None:
        d['add'] = _ids(add, known, 'layer')
    if remove is not None:
        d['remove'] = _ids(remove, known, 'layer')
    if basemap is not None:
        if basemap not in BASEMAPS:
            raise ValueError(f'basemap must be one of {BASEMAPS}')
        d['basemap'] = basemap

    housing: Dict[str, Any] = {}
    if metric is not None:
        if metric not in P.METRICS:
            raise ValueError(f'metric must be one of {list(P.METRICS)}')
        housing['metric'] = metric
    if geography is not None:
        if geography not in P.GEOGRAPHIES:
            raise ValueError(f'geography must be one of {list(P.GEOGRAPHIES)}')
        housing['geography'] = geography
    if property_type is not None:
        if property_type not in P.PROPERTY_TYPES:
            raise ValueError(f'property_type must be one of {list(P.PROPERTY_TYPES)}')
        housing['property_type'] = property_type
    for k, v in (('since', since), ('until', until)):
        if v is not None:
            if v and not re.match(r'^\d{4}-\d{2}-\d{2}$', str(v)):
                raise ValueError(f'{k} must be YYYY-MM-DD')
            housing[k] = v or None
    if housing:
        d['housing'] = housing
        # Filtering a choropleth that is switched off would look like a no-op.
        on = set(d.get('layers') or []) | set(d.get('add') or [])
        if 'layers' not in d and 'housing_prices' not in on:
            d.setdefault('add', []).append('housing_prices')

    if min_value is not None or max_value is not None:
        d['filter'] = {'min': None if min_value is None else float(min_value),
                       'max': None if max_value is None else float(max_value)}
    if highlight is not None:
        d['highlight'] = _list(highlight)
    if only is not None:
        d['only'] = _list(only)

    if lat is not None and lng is not None:
        d['focus'] = {'lat': float(lat), 'lng': float(lng),
                      'zoom': float(zoom or 14), 'label': focus or ''}
    elif focus:
        hits = _geocode(focus)
        if hits:
            h = hits[0]
            canon = BOROUGHS.get(focus.strip().lower())
            d['focus'] = {'lat': h['lat'], 'lng': h['lng'],
                          'zoom': float(zoom or (11 if canon else 14)),
                          'label': h['name'].split(',')[0]}
        else:
            notes.append(f'could not geocode {focus!r}; camera unchanged')

    if clear_overlay:
        d['overlay'] = None
    elif overlay is not None:
        if isinstance(overlay, str):
            overlay = json.loads(overlay)
        spec = normalize_overlay(overlay)
        data = overlay_data(spec)          # runs (and caches) the query now
        meta = data.get('meta', {})
        d['overlay'] = {'spec': spec, 'url': overlay_url(spec),
                        'title': spec['title'], 'mode': spec['mode']}
        if spec['mode'] == 'areas':
            notes.append(f"overlay: {meta.get('areas_with_data')} of {meta.get('areas')} "
                         f"{spec['by']} areas have data; values are {spec['value']}"
                         + (' per 10,000 residents' if spec['per_capita'] else '')
                         + f"; top: {meta.get('top')}; bottom: {meta.get('bottom')}")
        else:
            notes.append(f"overlay: plotted {meta.get('plotted')} of {meta.get('rows')} rows"
                         + (' (capped — narrow the where clause)' if meta.get('capped') else ''))
    if caption:
        d['caption'] = str(caption)[:200]
    if len(d) == 1:
        raise ValueError('nothing to change — pass layers/add/remove, housing filters, '
                         'highlight, only, focus, overlay or reset')
    return {'directive': d, 'notes': notes,
            'applied': 'The map in the user\'s browser now shows this.'}


def _geocode(q: str) -> List[dict]:
    from .tools import get_nyc
    try:
        return get_nyc().where(q, limit=1)
    except Exception:
        return []


# ─────────────────────────────────────────────────────────── nyc_infographic

def infographic(title: str, subtitle: Optional[str] = None, stats: Any = None,
                bars: Any = None, series: Any = None, bullets: Any = None,
                sources: Any = None) -> dict:
    """Validate an infographic card and return the directive the browser draws."""
    def parse(v):
        return json.loads(v) if isinstance(v, str) and v.strip()[:1] in '[{' else v

    stats, bars, series = parse(stats), parse(bars), parse(series)
    bullets, sources = parse(bullets), parse(sources)
    card: Dict[str, Any] = {'kind': 'infographic', 'title': str(title)[:90]}
    if subtitle:
        card['subtitle'] = str(subtitle)[:160]
    if stats:
        card['stats'] = [{'label': str(s.get('label', ''))[:40],
                          'value': str(s.get('value', ''))[:24],
                          **({'note': str(s['note'])[:60]} if s.get('note') else {})}
                         for s in list(stats)[:6] if isinstance(s, dict)]
    if bars:
        items = bars.get('items', []) if isinstance(bars, dict) else bars
        card['bars'] = {
            'title': str(bars.get('title', '')) if isinstance(bars, dict) else '',
            'unit': str(bars.get('unit', '')) if isinstance(bars, dict) else '',
            'items': [{'label': str(i.get('label', ''))[:40], 'value': _num(i.get('value'))}
                      for i in list(items)[:12] if isinstance(i, dict)],
        }
    if series:
        pts = series.get('points', []) if isinstance(series, dict) else series
        card['series'] = {
            'title': str(series.get('title', '')) if isinstance(series, dict) else '',
            'unit': str(series.get('unit', '')) if isinstance(series, dict) else '',
            'points': [{'x': str(p.get('x', '')), 'y': _num(p.get('y'))}
                       for p in list(pts)[:60] if isinstance(p, dict)],
        }
    if bullets:
        card['bullets'] = [str(b)[:200] for b in list(bullets)[:6]]
    if sources:
        card['sources'] = [{'name': str(s.get('name', ''))[:80],
                            **({'url': str(s['url'])} if str(s.get('url', '')).startswith('https://') else {})}
                           if isinstance(s, dict) else {'name': str(s)[:80]}
                           for s in list(sources)[:5]]
    return {'directive': card, 'applied': 'The infographic is now pinned on the user\'s map.'}
