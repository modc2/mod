"""
tdot.registry — turn any Toronto Open Data dataset into a map layer.

The city publishes ~500 datasets. Hand-writing a loader for each one doesn't
scale, and every loader written by hand is a layer nobody else can add. So a
layer is instead a **spec**: a small dict saying which CKAN package to read,
how to find each row's location, which columns to keep and how to draw it.

    {'id': 'development_applications',
     'title': 'Development applications',
     'category': 'Real estate',
     'package': 'development-applications',
     'resource': 'Development Applications',
     'locate': {'mode': 'mtm', 'x': 'X', 'y': 'Y'},
     'props': {'address': ['STREET_NUM', 'STREET_NAME', 'STREET_TYPE'],
               'status': 'STATUS'},
     'style': {'color_by': 'status'}}

:func:`build` turns a spec into GeoJSON, :func:`autodetect` writes one *for*
you from nothing but a package name, and :func:`save` persists it under
``~/.mod/tdot/datasets`` — off-tree, per user, never committed. Builtin specs
live in :mod:`tdotgis.layers`; user specs come from disk. Both go through the
same code path, so a dataset somebody adds from the browser is a first-class
layer, not a second-tier one.

The hard part of mapping Toronto's open data is that a table rarely says where
its rows *are*. Six location modes cover essentially all of it:

    geojson   a published EPSG:4326 GeoJSON file resource
    geometry  a datastore column holding GeoJSON geometry as a JSON string
    latlng    plain latitude/longitude columns
    mtm       X/Y on the city's MTM zone-10 grid (metres) — see sources.py
    address   a GEO_ID into the city's One Address Repository
    join      no location at all: borrow it from a sibling dataset by key
    area      no location at all: count rows per ward/neighbourhood instead
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import sources as S

# User-added datasets are per-install state, not repo content.
STORE = Path(os.path.expanduser('~/.mod/tdot/datasets'))

DAY = S.DAY

# A layer this big stops being a map and starts being a download; tabular
# sources are cut to the most recent rows past this.
MAX_FEATURES = 30_000

VALID_MODES = ('geojson', 'geometry', 'latlng', 'mtm', 'address', 'join', 'area')

# Column names that reliably mean "latitude" / "longitude" across the portal.
LAT_NAMES = ('latitude', 'lat', 'lat_wgs84', 'y_coord', 'ycoord')
LNG_NAMES = ('longitude', 'long', 'lng', 'lon', 'long_wgs84', 'x_coord', 'xcoord')

# ...and the ones that mean "which ward/neighbourhood is this row in".
AREA_COLUMNS = {
    'ward': ('ward_number', 'ward', 'wardname', 'ward_name', 'ward_no',
             'geo_id_ward'),
    'neighbourhood': ('hood_158', 'neighbourhood_158', 'neighbourhood',
                      'neighbourhood_name'),
}


# ─────────────────────────────────────────────────────────────────────────────
# spec → GeoJSON
# ─────────────────────────────────────────────────────────────────────────────

def _clean(v: Any) -> Any:
    # City tables pad columns to fixed widths; "20  Holly St" is not two words.
    return re.sub(r'\s+', ' ', v).strip() if isinstance(v, str) else v


def _as(value: Any, kind: Optional[str]) -> Any:
    """Coerce one extracted value. Unparseable numbers become None, not 0."""
    if kind in (None, 'str'):
        return _clean(value)
    if value in (None, ''):
        return None
    try:
        if kind == 'int':
            return int(float(str(value).replace(',', '')))
        if kind == 'float':
            return round(float(str(value).replace(',', '')), 4)
    except (TypeError, ValueError):
        return None
    if kind == 'title':
        return str(value).strip().title()
    if kind == 'date':
        return str(value).strip()[:10]
    return _clean(value)


def _extract(row: Dict[str, Any], rule: Any) -> Any:
    """
    Pull one output property out of a source row.

    ``rule`` is a column name, a list of columns joined with spaces (the city
    splits every address into number/name/type/direction), or a dict with
    ``from`` plus an ``as`` type.
    """
    if isinstance(rule, dict):
        value = _extract(row, rule['from'])
        if rule.get('as') == 'lookup':
            # City tables are full of two-letter codes nobody outside the
            # division reads; a spec can spell them out without custom code.
            return rule['table'].get(str(value or '').strip().upper(), value)
        value = _as(value, rule.get('as'))
        # Free-text columns (a planner's description of a proposal) run to
        # paragraphs; a map tooltip needs a sentence, and 25k paragraphs is
        # most of the payload.
        if rule.get('max') and isinstance(value, str) and len(value) > rule['max']:
            value = value[:rule['max']].rstrip(' ,;.') + '…'
        return value
    if isinstance(rule, (list, tuple)):
        parts = [str(_clean(row.get(c)) or '') for c in rule]
        return ' '.join(p for p in parts if p and p != 'None') or None
    return _clean(row.get(rule))


def _passes(row: Dict[str, Any], filt: Optional[Dict[str, Any]]) -> bool:
    for col, want in (filt or {}).items():
        got = str(_clean(row.get(col)) or '')
        if want is True:
            if not got:
                return False
        elif isinstance(want, (list, tuple)):
            if got.lower() not in {str(w).lower() for w in want}:
                return False
        elif got.lower() != str(want).lower():
            return False
    return True


def _rows(spec: dict) -> List[dict]:
    """Datastore rows for a spec, filtered, sorted and capped."""
    # Must be the queryable copy, not the identically-named .csv export beside
    # it — only the datastore answers datastore_search.
    rid = S.ckan_datastore(spec['package'], spec['resource'])['id']
    rows = [r for r in S.ckan_records(rid, max_rows=200_000)
            if _passes(r, spec.get('filter'))]
    sort = spec.get('sort')
    if sort:
        desc = sort.startswith('-')
        col = sort.lstrip('-')
        rows.sort(key=lambda r: str(r.get(col) or ''), reverse=desc)
    limit = int(spec.get('limit') or MAX_FEATURES)
    return rows[:limit]


def _properties(spec: dict, row: dict) -> dict:
    props = spec.get('props')
    if not props:
        # No prop map: keep the scalar columns, minus CKAN's own bookkeeping.
        return {k: _clean(v) for k, v in row.items()
                if not isinstance(v, (dict, list)) and k not in ('_id', 'geometry')}
    return {k: _extract(row, rule) for k, rule in props.items()}


def _locate_rows(spec: dict, rows: List[dict]) -> List[dict]:
    """Attach ``[lng, lat]`` to each row under ``_xy``; drop the unplaceable."""
    loc = spec['locate']
    mode = loc.get('mode')
    out = []

    if mode == 'latlng':
        lat_k, lng_k = loc.get('lat', 'LATITUDE'), loc.get('lng', 'LONGITUDE')
        for r in rows:
            try:
                lat, lng = float(r[lat_k]), float(r[lng_k])
            except (KeyError, TypeError, ValueError):
                continue
            if -90 <= lat <= 90 and -180 <= lng <= 180 and (lat or lng):
                out.append({**r, '_xy': [round(lng, 5), round(lat, 5)]})

    elif mode == 'mtm':
        x_k, y_k = loc.get('x', 'X'), loc.get('y', 'Y')
        for r in rows:
            try:
                x, y = float(r[x_k]), float(r[y_k])
            except (KeyError, TypeError, ValueError):
                continue
            if not S.looks_like_mtm(x, y):
                continue
            lng, lat = S.mtm_to_wgs84(x, y)
            out.append({**r, '_xy': [round(lng, 5), round(lat, 5)]})

    elif mode == 'address':
        # The row names a civic address by the city's own id; the address
        # repository knows where that is (see sources.address_index).
        key = loc.get('key', 'GEO_ID')
        index = S.address_index()
        for r in rows:
            xy = index.get(str(r.get(key) or '').strip())
            if xy:
                out.append({**r, '_xy': xy})

    elif mode == 'join':
        # The row has no location; a sibling dataset keyed the same way does.
        # The matched row is kept too, so a spec can also borrow its columns
        # with a `join.COLUMN` rule (the ward name, say, that only it carries).
        to = loc['to']
        others = _locate_rows(to, _rows(to))
        key_fn = (lambda v: re.sub(r'\s+', ' ', str(v or '').strip().upper()))
        book = {key_fn(o.get(to['key'])): o for o in others}
        for r in rows:
            other = book.get(key_fn(r.get(loc['key'])))
            if other:
                joined = {f'join.{k}': v for k, v in other.items() if k != '_xy'}
                out.append({**r, **joined, '_xy': other['_xy']})

    else:
        raise ValueError(f'{mode!r} is not a point-producing locate mode')
    return out


def _geometry_features(spec: dict) -> List[dict]:
    """Rows whose datastore carries GeoJSON geometry as a JSON string."""
    col = spec['locate'].get('column', 'geometry')
    tol = float(spec.get('simplify') or 0)
    feats = []
    for r in _rows(spec):
        raw = r.get(col)
        try:
            geom = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        geom = S.simplify_geometry(geom, tol, 5) if tol else geom
        if not geom:
            continue
        feats.append({'type': 'Feature', 'properties': _properties(spec, r),
                      'geometry': geom})
    return feats


def _area_features(spec: dict) -> dict:
    """
    Count rows per area and paint the boundary — the fallback that makes an
    address-only table (short-term rentals, licences) mappable at all.
    """
    # Imported here rather than at module scope: layers.py owns the boundary
    # loaders and imports this module for its specs.
    from . import layers as L

    loc = spec['locate']
    geography = loc.get('geography', 'ward')
    col = loc['column']
    weight = loc.get('weight')

    tally: Dict[str, float] = {}
    for r in _rows(spec):
        key = _area_key(r.get(col))
        if not key:
            continue
        if weight:
            try:
                tally[key] = tally.get(key, 0.0) + float(str(r.get(weight) or 0).replace(',', ''))
            except (TypeError, ValueError):
                continue
        else:
            tally[key] = tally.get(key, 0.0) + 1

    bounds = L.boundary(geography)
    join = 'AREA_SHORT_CODE' if geography == 'ward' else 'AREA_LONG_CODE'
    label = loc.get('label', 'count')
    feats = []
    for f in bounds['features']:
        p = f['properties']
        key = _area_key(p.get(join))
        value = tally.get(key)
        feats.append({
            'type': 'Feature',
            'geometry': f['geometry'],
            'properties': {'area': p.get(join),
                           'name': re.sub(r'\s*\(\d+\)$', '', str(p.get('AREA_NAME') or '')),
                           label: int(value) if value is not None else 0},
        })
    fc = {'type': 'FeatureCollection', 'features': feats}
    fc['breaks'] = quantile_breaks([f['properties'][label] for f in feats], label)
    fc['meta'] = {'mode': 'area', 'geography': geography, 'metric': label,
                  'total': int(sum(tally.values()))}
    return fc


def _area_key(v: Any) -> str:
    """Ward 4, '04' and 'Ward 4' are the same area."""
    s = str(v or '').strip()
    if not s:
        return ''
    m = re.match(r'^\D*(\d+)', s)
    return str(int(m.group(1))) if m else s.upper()


def quantile_breaks(values: List[Any], metric: str, classes: int = 6) -> dict:
    """Quantile class breaks — the same skew argument as the crime engine."""
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return {'metric': metric, 'stops': [], 'min': None, 'max': None}
    stops: List[float] = []
    for i in range(classes):
        s = vals[min(int(i * len(vals) / classes), len(vals) - 1)]
        if not stops or s > stops[-1]:
            stops.append(s)
    return {'metric': metric, 'stops': stops, 'min': vals[0], 'max': vals[-1],
            'count': len(vals), 'diverging': False}


def _build(spec: dict) -> dict:
    mode = spec['locate']['mode']

    if mode == 'geojson':
        fc = S.ckan_geojson(spec['package'],
                            spec['locate'].get('resource') or spec.get('resource')
                            or '4326.geojson')
        fc = S.simplify_geojson(fc, tol=float(spec.get('simplify') or 0.00006),
                                keep=spec['locate'].get('keep'))
        if spec.get('props'):
            for f in fc['features']:
                f['properties'] = _properties(spec, f['properties'])
        elif spec['locate'].get('rename'):
            ren = spec['locate']['rename']
            for f in fc['features']:
                f['properties'] = {ren.get(k, k): v for k, v in f['properties'].items()}
        return fc

    if mode == 'area':
        return _area_features(spec)

    if mode == 'geometry':
        return {'type': 'FeatureCollection', 'features': _geometry_features(spec)}

    rows = _locate_rows(spec, _rows(spec))
    return {'type': 'FeatureCollection',
            'features': [{'type': 'Feature',
                          'properties': _properties(spec, r),
                          'geometry': {'type': 'Point', 'coordinates': r['_xy']}}
                         for r in rows]}


def build(spec: dict, refresh: bool = False) -> dict:
    """A spec's GeoJSON, through the disk cache."""
    validate(spec)
    key = f'ds-{spec["id"]}'
    if refresh:
        S.cache_clear(key)
    return S.cached(key, float(spec.get('ttl_days', 7)) * DAY, lambda: _build(spec))


def validate(spec: dict) -> dict:
    """Raise unless the spec is complete enough to build. Returns the spec."""
    for field in ('id', 'title', 'package', 'locate'):
        if not spec.get(field):
            raise ValueError(f'dataset spec is missing {field!r}')
    if not re.fullmatch(r'[a-z0-9_]{2,48}', str(spec['id'])):
        raise ValueError(f'dataset id {spec["id"]!r} must be lowercase a-z0-9_')
    mode = spec['locate'].get('mode')
    if mode not in VALID_MODES:
        raise ValueError(f'unknown locate mode {mode!r}; known: {list(VALID_MODES)}')
    if mode in ('geometry', 'latlng', 'mtm', 'address', 'join', 'area') and not spec.get('resource'):
        raise ValueError(f'{mode!r} mode needs a resource name')
    return spec


# ─────────────────────────────────────────────────────────────────────────────
# catalogue entry
# ─────────────────────────────────────────────────────────────────────────────

def layer_def(spec: dict, custom: bool = False) -> dict:
    """The catalogue entry the UI's layer panel is built from."""
    mode = spec['locate']['mode']
    geometry = spec.get('geometry') or ('polygon' if mode == 'area' else 'point')
    kind = spec.get('kind') or ('area' if mode == 'area' else
                                {'point': 'point', 'line': 'line',
                                 'polygon': 'polygon'}[geometry])
    return {
        'id': spec['id'],
        'title': spec['title'],
        'category': spec.get('category', 'Open data'),
        'kind': kind,
        'geometry': geometry,
        'default_on': bool(spec.get('default_on')),
        'description': spec.get('description', ''),
        'style': spec.get('style', {}),
        'endpoint': f'/layers/{spec["id"]}',
        'custom': custom,
        'source': {'name': spec.get('source_name') or spec['title'],
                   'dataset': spec['package'],
                   'url': f'https://open.toronto.ca/dataset/{spec["package"]}/',
                   'portal': 'open.toronto.ca'},
    }


# ─────────────────────────────────────────────────────────────────────────────
# user-added datasets (off-tree, under ~/.mod/tdot/datasets)
# ─────────────────────────────────────────────────────────────────────────────

def saved() -> List[dict]:
    if not STORE.exists():
        return []
    out = []
    for f in sorted(STORE.glob('*.json')):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return out


def get_saved(dataset_id: str) -> Optional[dict]:
    return next((s for s in saved() if s.get('id') == dataset_id), None)


def save(spec: dict) -> dict:
    validate(spec)
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / f'{spec["id"]}.json').write_text(json.dumps(spec, indent=2))
    return spec


def remove(dataset_id: str) -> dict:
    p = STORE / f'{dataset_id}.json'
    existed = p.exists()
    if existed:
        p.unlink()
    S.cache_clear(f'ds-{dataset_id}')
    return {'removed': dataset_id, 'existed': existed}


# ─────────────────────────────────────────────────────────────────────────────
# discovery: search the portal, then write the spec for what you found
# ─────────────────────────────────────────────────────────────────────────────

def search(q: str, rows: int = 12) -> List[dict]:
    """Search Toronto Open Data for datasets matching ``q``."""
    def fetch():
        d = S._get(f'{S.CKAN}/api/3/action/package_search',
                   q=q, rows=int(rows)).json()
        return [{'package': r['name'],
                 'title': r.get('title') or r['name'],
                 'notes': re.sub(r'<[^>]+>', '', (r.get('notes') or ''))[:260].strip(),
                 'topics': r.get('topics') or '',
                 'refreshed': r.get('refresh_rate') or '',
                 'formats': sorted({(x.get('format') or '').upper()
                                    for x in r.get('resources', []) if x.get('format')}),
                 'url': f'https://open.toronto.ca/dataset/{r["name"]}/'}
                for r in d['result']['results']]
    return S.cached(f'ckan-search-{q.lower()}-{rows}', DAY, fetch)


def _slug(name: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')
    return (s or 'dataset')[:48]


def _pick_resource(pkg: dict) -> Optional[dict]:
    """Prefer a published 4326 GeoJSON file; fall back to the datastore table."""
    geo = [r for r in pkg.get('resources', [])
           if '4326.geojson' in (r.get('name') or '').lower()]
    if geo:
        return {'kind': 'geojson', 'resource': geo[0]}
    live = [r for r in pkg.get('resources', []) if r.get('datastore_active')]
    if live:
        return {'kind': 'datastore', 'resource': live[0]}
    return None


def autodetect(package: str, resource: Optional[str] = None,
               category: str = 'Open data', title: Optional[str] = None,
               dataset_id: Optional[str] = None) -> dict:
    """
    Write a working spec for a CKAN package by inspecting what it publishes.

    Raises with an explanation when a dataset simply cannot be placed on a map
    — better a clear "this table has no location column" than a blank layer.
    """
    pkg = S.ckan_package(package)
    spec: Dict[str, Any] = {
        'id': dataset_id or _slug(package),
        'title': title or pkg.get('title') or package,
        'category': category,
        'description': re.sub(r'<[^>]+>', '', (pkg.get('notes') or ''))[:300].strip(),
        'package': package,
        'source_name': pkg.get('title') or package,
        'ttl_days': 7,
    }

    if resource:
        res = next((r for r in pkg.get('resources', [])
                    if resource.lower() in (r.get('name') or '').lower()), None)
        if not res:
            raise KeyError(f'no resource matching {resource!r} in {package!r}')
        choice = {'kind': 'geojson' if 'geojson' in (res.get('name') or '').lower()
                  and not res.get('datastore_active') else 'datastore',
                  'resource': res}
    else:
        choice = _pick_resource(pkg)
    if not choice:
        raise ValueError(
            f'{package!r} publishes no GeoJSON export and no live datastore '
            f'table — nothing to read. Formats present: '
            f'{sorted({(r.get("format") or "?") for r in pkg.get("resources", [])})}')

    res = choice['resource']
    spec['resource'] = res['name']

    if choice['kind'] == 'geojson':
        spec['locate'] = {'mode': 'geojson', 'resource': res['name']}
        spec['geometry'] = _geojson_geometry(res['url'])
        spec['simplify'] = 0.00006
        return spec

    fields, sample = _peek(res['id'])
    lower = {f.lower(): f for f in fields}

    if 'geometry' in lower:
        spec['locate'] = {'mode': 'geometry', 'column': lower['geometry']}
        spec['geometry'] = _sample_geometry(sample, lower['geometry'])
        spec['props'] = _prop_map(fields, drop=(lower['geometry'],))
        return spec

    lat = next((lower[n] for n in LAT_NAMES if n in lower), None)
    lng = next((lower[n] for n in LNG_NAMES if n in lower), None)
    if lat and lng and _numeric(sample, lat) and abs(_numeric(sample, lat) or 0) < 90:
        spec['locate'] = {'mode': 'latlng', 'lat': lat, 'lng': lng}
        spec['geometry'] = 'point'
        spec['props'] = _prop_map(fields, drop=(lat, lng))
        return spec

    geo_id = next((lower[n] for n in ('geo_id', 'address_point_id') if n in lower), None)
    if geo_id and str(sample.get(geo_id) or '').strip().isdigit():
        spec['locate'] = {'mode': 'address', 'key': geo_id}
        spec['geometry'] = 'point'
        spec['props'] = _prop_map(fields, drop=(geo_id,))
        return spec

    x, y = lower.get('x'), lower.get('y')
    if x and y:
        xv, yv = _numeric(sample, x), _numeric(sample, y)
        if xv is not None and yv is not None and S.looks_like_mtm(xv, yv):
            spec['locate'] = {'mode': 'mtm', 'x': x, 'y': y}
            spec['geometry'] = 'point'
            spec['props'] = _prop_map(fields, drop=(x, y))
            return spec

    for geography, names in AREA_COLUMNS.items():
        col = next((lower[n] for n in names if n in lower), None)
        if col:
            spec['locate'] = {'mode': 'area', 'column': col,
                              'geography': geography, 'label': 'count'}
            spec['geometry'] = 'polygon'
            spec['kind'] = 'area'
            spec['style'] = {'color_by': 'count'}
            spec['description'] = (
                f'{spec["description"]}\n\nThis table carries no coordinates, so '
                f'rows are counted per {geography} and drawn as a density map.'
            ).strip()
            return spec

    raise ValueError(
        f'{package!r} has no coordinates, no geometry column and no ward or '
        f'neighbourhood column, so its rows cannot be placed. Columns: '
        f'{fields[:24]}')


def _peek(resource_id: str):
    d = S._get(f'{S.CKAN}/api/3/action/datastore_search',
               resource_id=resource_id, limit=3).json()['result']
    fields = [f['id'] for f in d.get('fields', []) if f['id'] != '_id']
    return fields, (d.get('records') or [{}])[0]


def _numeric(row: dict, col: str) -> Optional[float]:
    try:
        return float(str(row.get(col)).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _sample_geometry(row: dict, col: str) -> str:
    try:
        raw = row.get(col)
        t = (json.loads(raw) if isinstance(raw, str) else raw).get('type', '')
    except Exception:
        return 'point'
    return 'polygon' if 'Polygon' in t else 'line' if 'Line' in t else 'point'


def _geojson_geometry(url: str) -> str:
    """Peek at the first feature to learn what a published export draws as."""
    try:
        fc = S._get(url, timeout=300).json()
        t = (fc['features'][0].get('geometry') or {}).get('type', '')
    except Exception:
        return 'polygon'
    return 'polygon' if 'Polygon' in t else 'line' if 'Line' in t else 'point'


def _prop_map(fields: List[str], drop=(), keep: int = 12) -> Dict[str, str]:
    """A readable prop map: `SITE_ADDRESS` → `site_address`, capped in width."""
    out: Dict[str, str] = {}
    for f in fields:
        if f in drop or f.lower() in ('_id',):
            continue
        name = re.sub(r'[^a-z0-9]+', '_', f.lower()).strip('_')
        if name and name not in out:
            out[name] = f
        if len(out) >= keep:
            break
    return out
