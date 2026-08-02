"""
tdot.market — a place to put a layer somebody else can use.

The registry (:mod:`tdotgis.registry`) can turn anything on the city's portal
into a layer. This module covers the other half: data that is *not* on the
portal. Somebody's own survey. A licensed feed they are allowed to
redistribute. A shapefile a community group spent a winter digitising. Sold
prices from a board's data licence — which is the honest answer to "where are
the sale prices": MLS transaction data is TRREB's copyrighted database, it is
not open at any price, and no amount of scraping makes it open. What can exist
is a place for whoever *does* hold that licence to publish it, and for the map
to render it exactly like every other layer.

A listing is a small record plus its features. Two flavours:

    upload  a file somebody dropped in — GeoJSON or CSV, converted once to a
            FeatureCollection and stored beside the listing
    spec    a recipe against the city's portal (see registry.py) — no data
            stored, because it rebuilds itself from the source

Both render through the same catalogue path as the builtin layers, so a
neighbourhood association's dataset is not a second-class citizen on the map.

Publishing hands the whole listing to ``localfs`` and gets a CID back. The CID
*is* the distribution: send it to somebody and ``install(cid)`` puts the layer
on their map, no server between the two of you. Everything lives under
``~/.mod/tdot/market`` — per user, off-tree, never committed.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import registry as R
from . import sources as S

STORE = Path(os.path.expanduser('~/.mod/tdot/market'))

# A layer past this stops being a map and starts being a download.
MAX_FEATURES = 200_000
MAX_BYTES = 64 * 1024 * 1024

CATEGORIES = ['Real estate', 'Housing', 'Transit', 'Environment', 'Safety',
              'Community', 'Boundaries', 'Open data']

# Column names that mean "this row's location", in the order they are tried.
GEOMETRY_NAMES = ('geometry', 'the_geom', 'geojson', 'shape')
ADDRESS_ID_NAMES = ('geo_id', 'address_point_id')


# ─────────────────────────────────────────────────────────────────────────────
# uploads → GeoJSON
# ─────────────────────────────────────────────────────────────────────────────

def _feature(geom: dict, props: dict) -> dict:
    return {'type': 'Feature', 'properties': props, 'geometry': geom}


def _point(lng: float, lat: float) -> dict:
    return {'type': 'Point', 'coordinates': [round(lng, 5), round(lat, 5)]}


def _num(v: Any) -> Optional[float]:
    try:
        return float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def _typed(v: Any) -> Any:
    """Keep numbers numeric so a column can be sized or classed on the map."""
    s = str(v).strip() if v is not None else ''
    if not s:
        return None
    if re.fullmatch(r'-?\d{1,15}(\.\d+)?', s.replace(',', '')):
        n = float(s.replace(',', ''))
        return int(n) if n.is_integer() and abs(n) < 2 ** 53 else n
    return s


def _rows_to_features(rows: List[dict]) -> Tuple[List[dict], str]:
    """
    Place tabular rows, trying every location a Toronto table tends to carry.

    Returns the features and the mode that worked, so the uploader is told
    *how* their file was read rather than just how many rows survived.
    """
    if not rows:
        return [], 'empty'
    lower = {k.lower().strip(): k for k in rows[0] if k}

    def props(row: dict, drop: Tuple[str, ...]) -> dict:
        return {k: _typed(v) for k, v in row.items()
                if k and k not in drop and not k.startswith('_')}

    col = next((lower[n] for n in GEOMETRY_NAMES if n in lower), None)
    if col:
        feats = []
        for r in rows:
            raw = r.get(col)
            try:
                geom = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                continue
            if isinstance(geom, dict) and geom.get('type'):
                feats.append(_feature(geom, props(r, (col,))))
        if feats:
            return feats, 'geometry'

    lat_k = next((lower[n] for n in R.LAT_NAMES if n in lower), None)
    lng_k = next((lower[n] for n in R.LNG_NAMES if n in lower), None)
    if lat_k and lng_k:
        feats = []
        for r in rows:
            lat, lng = _num(r.get(lat_k)), _num(r.get(lng_k))
            if lat is None or lng is None or (lat == 0 and lng == 0):
                continue
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                feats.append(_feature(_point(lng, lat), props(r, (lat_k, lng_k))))
        if feats:
            return feats, 'latlng'

    x_k, y_k = lower.get('x'), lower.get('y')
    if x_k and y_k:
        feats = []
        for r in rows:
            x, y = _num(r.get(x_k)), _num(r.get(y_k))
            if x is None or y is None or not S.looks_like_mtm(x, y):
                continue
            lng, lat = S.mtm_to_wgs84(x, y)
            feats.append(_feature(_point(lng, lat), props(r, (x_k, y_k))))
        if feats:
            return feats, 'mtm'

    id_k = next((lower[n] for n in ADDRESS_ID_NAMES if n in lower), None)
    if id_k:
        index = S.address_index()
        feats = []
        for r in rows:
            xy = index.get(str(r.get(id_k) or '').strip())
            if xy:
                feats.append(_feature(_point(*xy), props(r, ())))
        if feats:
            return feats, 'address'

    raise ValueError(
        'no location found in this file. A CSV needs latitude/longitude '
        'columns, X/Y on the city\'s MTM grid, a GeoJSON `geometry` column, or '
        f'a `GEO_ID` address id. Columns seen: {sorted(lower.values())[:20]}')


def parse(blob: bytes, filename: str = '') -> Tuple[dict, str]:
    """Any supported upload → (FeatureCollection, how it was read)."""
    if len(blob) > MAX_BYTES:
        raise ValueError(f'file is {len(blob) // 1048576} MB; the limit is '
                         f'{MAX_BYTES // 1048576} MB')
    text = blob.decode('utf-8-sig', 'replace').strip()
    if not text:
        raise ValueError('the file is empty')

    name = filename.lower()
    looks_json = text[0] in '{[' and not name.endswith('.csv')

    if looks_json:
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ValueError(f'not valid JSON: {e}') from None
        return _from_json(doc)

    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError('the CSV has a header but no rows')
    feats, mode = _rows_to_features(rows)
    return {'type': 'FeatureCollection', 'features': feats}, mode


def _from_json(doc: Any) -> Tuple[dict, str]:
    """GeoJSON in any of the shapes people actually paste."""
    if isinstance(doc, list):
        # A plain JSON array of records is a CSV in different clothing.
        feats, mode = _rows_to_features([r for r in doc if isinstance(r, dict)])
        return {'type': 'FeatureCollection', 'features': feats}, mode
    if not isinstance(doc, dict):
        raise ValueError('expected a GeoJSON object or an array of records')

    kind = doc.get('type')
    if kind == 'FeatureCollection':
        feats = [f for f in doc.get('features') or []
                 if isinstance(f, dict) and f.get('geometry')]
        if not feats:
            raise ValueError('that FeatureCollection has no features with geometry')
        return {'type': 'FeatureCollection', 'features': feats}, 'geojson'
    if kind == 'Feature':
        return {'type': 'FeatureCollection', 'features': [doc]}, 'geojson'
    if kind in ('Point', 'MultiPoint', 'LineString', 'MultiLineString',
                'Polygon', 'MultiPolygon', 'GeometryCollection'):
        return {'type': 'FeatureCollection',
                'features': [_feature(doc, {})]}, 'geojson'
    # A CKAN-style {"records": [...]} envelope, or anything else with rows in it.
    for key in ('records', 'rows', 'data', 'features'):
        rows = doc.get(key)
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            feats, mode = _rows_to_features(rows)
            return {'type': 'FeatureCollection', 'features': feats}, mode
    raise ValueError(f'unrecognised JSON: type={kind!r}, keys={sorted(doc)[:12]}')


def _geometry_kind(fc: dict) -> str:
    t = ((fc.get('features') or [{}])[0].get('geometry') or {}).get('type', '')
    return 'polygon' if 'Polygon' in t else 'line' if 'Line' in t else 'point'


# ─────────────────────────────────────────────────────────────────────────────
# listings
# ─────────────────────────────────────────────────────────────────────────────

def _dir(listing_id: str) -> Path:
    return STORE / listing_id


def _slug(name: str, fallback: str = 'layer') -> str:
    s = re.sub(r'[^a-z0-9]+', '_', str(name).lower()).strip('_')
    return (s or fallback)[:48]


def _unique(base: str) -> str:
    """``sold_prices``, then ``sold_prices_2`` — never silently overwrite."""
    from . import layers as L
    taken = set(L.SPECS_BY_ID) | {l['id'] for l in listings()} | set(L.LOADERS)
    if base not in taken:
        return base
    for n in range(2, 100):
        if f'{base}_{n}' not in taken:
            return f'{base}_{n}'
    return f'{base}_{int(time.time())}'


def listings() -> List[dict]:
    """Every layer in this node's market, newest first."""
    if not STORE.exists():
        return []
    out = []
    for d in STORE.iterdir():
        f = d / 'listing.json'
        if not f.is_file():
            continue
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            continue
    return sorted(out, key=lambda l: l.get('created', ''), reverse=True)


def get(listing_id: str) -> Optional[dict]:
    f = _dir(str(listing_id)) / 'listing.json'
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def data(listing_id: str, refresh: bool = False) -> dict:
    """A market layer's GeoJSON — read from disk, or rebuilt from its spec."""
    listing = get(listing_id)
    if not listing:
        raise KeyError(f'unknown market layer {listing_id!r}')
    if listing.get('kind') == 'spec':
        return R.build(listing['spec'], refresh=refresh)
    f = _dir(listing_id) / 'data.geojson'
    if not f.is_file():
        raise KeyError(f'{listing_id!r} has a listing but no data on disk')
    return json.loads(f.read_text())


def _write(listing: dict, fc: Optional[dict]) -> dict:
    d = _dir(listing['id'])
    d.mkdir(parents=True, exist_ok=True)
    if fc is not None:
        (d / 'data.geojson').write_text(json.dumps(fc, separators=(',', ':')))
    (d / 'listing.json').write_text(json.dumps(listing, indent=2))
    return listing


def publish_file(blob: bytes, filename: str = '', title: Optional[str] = None,
                 description: str = '', category: str = 'Open data',
                 author: str = '', licence: str = '', source_url: str = '',
                 listing_id: Optional[str] = None) -> dict:
    """Put an uploaded GeoJSON/CSV on the map and in the market."""
    fc, mode = parse(blob, filename)
    feats = fc.get('features') or []
    if not feats:
        raise ValueError('nothing in that file could be placed on the map')
    if len(feats) > MAX_FEATURES:
        raise ValueError(f'{len(feats):,} features; the limit is {MAX_FEATURES:,}')

    name = title or re.sub(r'\.[a-z]+$', '', filename or '').replace('_', ' ').strip()
    listing = {
        'id': _unique(_slug(listing_id or name or filename)),
        'title': (name or 'Untitled layer').strip()[:120],
        'description': str(description or '').strip()[:600],
        'category': category if category in CATEGORIES else 'Open data',
        'kind': 'upload',
        'geometry': _geometry_kind(fc),
        'mode': mode,
        'features': len(feats),
        'bytes': S.geojson_bytes(fc),
        'fields': sorted({k for f in feats[:200]
                          for k in (f.get('properties') or {})})[:40],
        'author': str(author or '').strip()[:120],
        'licence': str(licence or '').strip()[:200],
        'source_url': str(source_url or '').strip()[:400],
        'filename': filename[:120],
        'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'cid': None,
    }
    return _write(listing, fc)


def publish_spec(spec: dict, description: str = '', author: str = '',
                 licence: str = '', source_url: str = '') -> dict:
    """
    List a portal recipe rather than a file.

    Nothing is copied: the listing carries the spec, and whoever installs it
    builds the layer from the city's own servers. A recipe is a few hundred
    bytes where the data is tens of megabytes, and it never goes stale.
    """
    R.validate(spec)
    listing = {
        'id': _unique(_slug(spec['id'])),
        'title': spec.get('title') or spec['id'],
        'description': (description or spec.get('description') or '')[:600],
        'category': spec.get('category', 'Open data'),
        'kind': 'spec',
        'geometry': spec.get('geometry', 'point'),
        'mode': spec['locate']['mode'],
        'features': None,
        'bytes': None,
        'fields': sorted(spec.get('props') or {})[:40],
        'author': str(author or '').strip()[:120],
        'licence': licence or 'Open Government Licence – Toronto',
        'source_url': (source_url or
                       f'https://open.toronto.ca/dataset/{spec["package"]}/'),
        'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'cid': None,
        'spec': {**spec, 'id': _slug(spec['id'])},
    }
    return _write(listing, None)


def remove(listing_id: str) -> dict:
    d = _dir(str(listing_id))
    existed = d.is_dir()
    if existed:
        for f in d.iterdir():
            f.unlink()
        d.rmdir()
    S.cache_clear(f'ds-{listing_id}')
    return {'removed': listing_id, 'existed': existed}


# ─────────────────────────────────────────────────────────────────────────────
# CIDs — the actual distribution
# ─────────────────────────────────────────────────────────────────────────────

def _fs():
    """The localfs mod, or None. A market that can't pin still works locally."""
    import mod as m
    try:
        return m.mod('localfs')()
    except Exception as e:
        print(f'tdot.market: localfs unavailable ({e}); CIDs disabled')
        return None


def bundle(listing_id: str) -> dict:
    """Everything needed to reconstruct this layer elsewhere, in one document."""
    listing = get(listing_id)
    if not listing:
        raise KeyError(f'unknown market layer {listing_id!r}')
    doc = {'tdot_market': 1, 'listing': {k: v for k, v in listing.items()
                                         if k != 'cid'}}
    if listing.get('kind') == 'upload':
        doc['data'] = data(listing_id)
    return doc


def publish(listing_id: str) -> dict:
    """Pin a listing and record the CID it can be installed by."""
    fs = _fs()
    if not fs:
        return {'ok': False, 'error': 'localfs is not available on this node'}
    listing = get(listing_id)
    if not listing:
        return {'ok': False, 'error': f'unknown market layer {listing_id!r}'}
    cid = fs.put(bundle(listing_id))
    listing['cid'] = cid
    listing['published'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    _write(listing, None)
    return {'ok': True, 'id': listing_id, 'cid': cid, 'title': listing['title']}


def install(cid: str) -> dict:
    """Put somebody else's published layer on this map."""
    fs = _fs()
    if not fs:
        return {'ok': False, 'error': 'localfs is not available on this node'}
    try:
        doc = fs.get(str(cid).strip())
    except Exception as e:
        return {'ok': False, 'error': f'could not fetch {cid}: {e}'}
    if isinstance(doc, (str, bytes)):
        try:
            doc = json.loads(doc)
        except ValueError:
            return {'ok': False, 'error': f'{cid} is not a tdot layer'}
    if not isinstance(doc, dict) or 'listing' not in doc:
        return {'ok': False, 'error': f'{cid} is not a tdot layer bundle'}

    listing = dict(doc['listing'])
    # The publisher's id may already be taken here; the CID is the identity
    # that matters, so a collision renames rather than refuses.
    listing['id'] = _unique(_slug(listing.get('id') or 'layer'))
    listing['cid'] = str(cid).strip()
    listing['installed'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    fc = doc.get('data')
    if listing.get('kind') == 'upload' and not isinstance(fc, dict):
        return {'ok': False, 'error': f'{cid} carries no data'}
    if listing.get('kind') == 'spec':
        listing['spec'] = {**listing.get('spec', {}), 'id': listing['id']}
    _write(listing, fc if listing.get('kind') == 'upload' else None)
    return {'ok': True, 'id': listing['id'], 'title': listing.get('title'),
            'kind': listing.get('kind'), 'cid': listing['cid'],
            'features': listing.get('features')}


# ─────────────────────────────────────────────────────────────────────────────
# catalogue
# ─────────────────────────────────────────────────────────────────────────────

def layer_def(listing: dict) -> dict:
    """A market listing as a catalogue entry the layer panel can draw."""
    geometry = listing.get('geometry') or 'point'
    return {
        'id': listing['id'],
        'title': listing['title'],
        'category': listing.get('category') or 'Open data',
        'kind': geometry,
        'geometry': geometry,
        'default_on': False,
        'description': listing.get('description') or '',
        'style': listing.get('style') or {},
        'endpoint': f'/layers/{listing["id"]}',
        'custom': True,
        'market': {'cid': listing.get('cid'), 'author': listing.get('author'),
                   'licence': listing.get('licence'),
                   'features': listing.get('features'),
                   'created': listing.get('created')},
        'source': {'name': listing.get('author') or 'Community upload',
                   'dataset': listing.get('filename') or listing['id'],
                   'url': listing.get('source_url') or '',
                   'portal': 'tdot market'},
    }


def layer_defs() -> List[dict]:
    return [layer_def(l) for l in listings()]
