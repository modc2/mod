"""
tdot.sources — open-data fetching, caching and geometry shrinking.

Everything the map draws comes from a public, no-key, open-data endpoint:

  * Toronto Open Data (CKAN)  https://open.toronto.ca
                              (API host: ckan0.cf.opendata.inter.prod-toronto.ca)
  * TTC GTFS static feed      via the same portal ("TTC Routes and Schedules")

Responses are cached on disk under ``~/.mod/tdot/cache`` so the map is fast and
keeps working when the upstream APIs are slow or unreachable. A stale cache
entry is preferred over an error: if a refresh fails we serve what we have.

Toronto's portal is CKAN, not Socrata, and its datastore does **not** expose
``datastore_search_sql`` — there is no server-side GROUP BY. Large tabular
datasets are therefore pulled once through the CSV dump endpoint (which *does*
support column filtering), aggregated locally, and the aggregate is what gets
cached. See :mod:`tdotgis.crime`.

Geometry from the city's portal is authoritative-precision (full shoreline
detail, 14 decimal places) which is far more than a browser map needs.
``simplify_geojson`` runs Ramer-Douglas-Peucker and rounds coordinates,
typically cutting payloads by 10-30x with no visible difference at city zoom.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import time
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import requests

CACHE_DIR = Path(os.path.expanduser('~/.mod/tdot/cache'))

# open.toronto.ca is the human portal; the API lives on the CKAN host.
CKAN = 'https://ckan0.cf.opendata.inter.prod-toronto.ca'

DAY = 86400
USER_AGENT = 'mod-tdot/1.0 (open-source Toronto GIS module)'

# CKAN caps a datastore_search page; stay under it.
CKAN_PAGE = 16_000


# ─────────────────────────────────────────────────────────────────────────────
# cache
# ─────────────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    safe = ''.join(c if (c.isalnum() or c in '-_.') else '_' for c in key)[:180]
    return CACHE_DIR / f'{safe}.json'


def cache_read(key: str, ttl: Optional[float] = None) -> Optional[Any]:
    """Return a cached value, or None. ``ttl=None`` ignores age (stale is ok)."""
    p = _cache_path(key)
    if not p.exists():
        return None
    if ttl is not None and (time.time() - p.stat().st_mtime) > ttl:
        return None
    try:
        with p.open() as fh:
            return json.load(fh)
    except Exception:
        return None


def cache_write(key: str, value: Any) -> Any:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(key)
    tmp = p.with_suffix('.tmp')
    with tmp.open('w') as fh:
        json.dump(value, fh, separators=(',', ':'))
    tmp.replace(p)          # atomic: readers never see a half-written file
    return value


def cache_age(key: str) -> Optional[float]:
    p = _cache_path(key)
    return (time.time() - p.stat().st_mtime) if p.exists() else None


def cache_stats() -> Dict[str, Any]:
    if not CACHE_DIR.exists():
        return {'dir': str(CACHE_DIR), 'entries': 0, 'bytes': 0}
    files = [f for f in CACHE_DIR.iterdir() if f.suffix == '.json']
    return {
        'dir': str(CACHE_DIR),
        'entries': len(files),
        'bytes': sum(f.stat().st_size for f in files),
        'oldest_seconds': int(time.time() - min((f.stat().st_mtime for f in files), default=time.time())),
    }


def cache_clear(prefix: str = '') -> Dict[str, Any]:
    if not CACHE_DIR.exists():
        return {'cleared': 0}
    n = 0
    for f in list(CACHE_DIR.iterdir()):
        if f.suffix in ('.json', '.tmp') and f.name.startswith(prefix):
            f.unlink()
            n += 1
    return {'cleared': n, 'prefix': prefix or '*'}


def cached(key: str, ttl: float, producer) -> Any:
    """Fetch through the cache. On upstream failure, fall back to stale data."""
    hit = cache_read(key, ttl)
    if hit is not None:
        return hit
    try:
        return cache_write(key, producer())
    except Exception:
        stale = cache_read(key, None)
        if stale is not None:
            return stale
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CKAN (Toronto Open Data)
# ─────────────────────────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 60, **params) -> requests.Response:
    r = requests.get(url, params=params or None, timeout=timeout,
                     headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    return r


def ckan_package(package: str) -> dict:
    """``package_show`` for one dataset, cached — resource ids live here."""
    def fetch():
        d = _get(f'{CKAN}/api/3/action/package_show', id=package).json()
        if not d.get('success'):
            raise ValueError(f'CKAN package_show failed for {package!r}')
        return d['result']
    return cached(f'ckan-pkg-{package}', 7 * DAY, fetch)


def ckan_resource(package: str, match: str) -> dict:
    """The first resource of ``package`` whose name contains ``match``."""
    m = match.lower()
    for res in ckan_package(package).get('resources', []):
        if m in (res.get('name') or '').lower():
            return res
    raise KeyError(f'no resource matching {match!r} in package {package!r}')


def ckan_geojson(package: str, match: str = '4326.geojson',
                 timeout: int = 300) -> dict:
    """
    Download a package's GeoJSON *file* resource (EPSG:4326).

    The portal publishes each geospatial dataset both as a datastore and as
    plain exported files; the files are the reliable path — the datastore
    returns geometry as an escaped string per record and its GeoJSON dump
    endpoint is not enabled.
    """
    res = ckan_resource(package, match)
    fc = _get(res['url'], timeout=timeout).json()
    if fc.get('type') != 'FeatureCollection':
        raise ValueError(f'{package}/{match}: expected FeatureCollection, '
                         f'got {fc.get("type")!r}')
    return fc


def ckan_records(resource_id: str, max_rows: int = 200_000,
                 timeout: int = 120) -> List[dict]:
    """Page through ``datastore_search`` until exhausted or ``max_rows``."""
    out: List[dict] = []
    while len(out) < max_rows:
        d = _get(f'{CKAN}/api/3/action/datastore_search', timeout=timeout,
                 resource_id=resource_id,
                 limit=min(CKAN_PAGE, max_rows - len(out)),
                 offset=len(out)).json()
        if not d.get('success'):
            raise ValueError(f'datastore_search failed for {resource_id}')
        page = d['result']['records']
        out.extend(page)
        if len(page) < CKAN_PAGE:
            break
    return out


def ckan_dump_rows(resource_id: str, fields: List[str],
                   timeout: int = 600) -> Iterator[dict]:
    """
    Stream a datastore's CSV dump, column-filtered server-side.

    This is the only way to get a large table out of Toronto's CKAN without
    SQL: the dump endpoint accepts ``?fields=`` (datastore_search_sql is
    blocked portal-wide), so half a million rows arrive as a ~25 MB CSV
    instead of a JSON page-walk.
    """
    r = requests.get(f'{CKAN}/datastore/dump/{resource_id}',
                     params={'fields': ','.join(fields), 'format': 'csv'},
                     timeout=timeout, stream=True,
                     headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    lines = (ln.decode('utf-8', 'replace') for ln in r.iter_lines() if ln)
    yield from csv.DictReader(lines)


def points_from_rows(rows: Iterable[dict], lat_key: str, lng_key: str,
                     props: Optional[List[str]] = None) -> dict:
    """Build a point FeatureCollection from tabular rows with lat/lng columns."""
    feats = []
    for row in rows:
        try:
            lat, lng = float(row[lat_key]), float(row[lng_key])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180) or (lat == 0 and lng == 0):
            continue
        p = {k: row.get(k) for k in props} if props else \
            {k: v for k, v in row.items() if not isinstance(v, (dict, list))}
        feats.append({'type': 'Feature',
                      'properties': p,
                      'geometry': {'type': 'Point',
                                   'coordinates': [round(lng, 5), round(lat, 5)]}})
    return {'type': 'FeatureCollection', 'features': feats}


# ─────────────────────────────────────────────────────────────────────────────
# geometry: Ramer-Douglas-Peucker + coordinate rounding
# ─────────────────────────────────────────────────────────────────────────────

def _perp_sq(pt, a, b) -> float:
    """Squared distance from ``pt`` to segment a-b, in degrees²."""
    (px, py), (ax, ay), (bx, by) = pt[:2], a[:2], b[:2]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = 0.0 if t < 0 else (1.0 if t > 1 else t)
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def rdp(points: List[list], tol: float) -> List[list]:
    """Ramer-Douglas-Peucker, iterative so deep rings can't blow the stack."""
    n = len(points)
    if n < 3:
        return list(points)
    tol2 = tol * tol
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi - lo < 2:
            continue
        worst, worst_i = -1.0, lo
        a, b = points[lo], points[hi]
        for i in range(lo + 1, hi):
            d = _perp_sq(points[i], a, b)
            if d > worst:
                worst, worst_i = d, i
        if worst > tol2:
            keep[worst_i] = True
            stack.append((lo, worst_i))
            stack.append((worst_i, hi))
    return [p for p, k in zip(points, keep) if k]


def _round_ring(ring: List[list], precision: int) -> List[list]:
    out, prev = [], None
    for p in ring:
        c = [round(p[0], precision), round(p[1], precision)]
        if c != prev:            # rounding can collapse neighbours into duplicates
            out.append(c)
            prev = c
    return out


def _simplify_ring(ring: List[list], tol: float, precision: int,
                   closed: bool) -> Optional[List[list]]:
    ring = _round_ring(rdp(ring, tol), precision)
    if closed:
        if len(ring) < 3:
            return None
        if ring[0] != ring[-1]:
            ring.append(list(ring[0]))
        if len(ring) < 4:        # a valid closed ring needs 4 positions
            return None
    elif len(ring) < 2:
        return None
    return ring


def simplify_geometry(geom: Optional[dict], tol: float = 0.0001,
                      precision: int = 5) -> Optional[dict]:
    """Simplify one GeoJSON geometry. Returns None if nothing survives."""
    if not geom or 'type' not in geom:
        return None
    t = geom['type']
    c = geom.get('coordinates')
    if c is None:
        return None

    if t == 'Point':
        return {'type': t, 'coordinates': [round(c[0], precision), round(c[1], precision)]}
    if t == 'MultiPoint':
        return {'type': t, 'coordinates': [[round(p[0], precision), round(p[1], precision)] for p in c]}
    if t == 'LineString':
        line = _simplify_ring(c, tol, precision, closed=False)
        return {'type': t, 'coordinates': line} if line else None
    if t == 'MultiLineString':
        lines = [ln for ln in (_simplify_ring(l, tol, precision, False) for l in c) if ln]
        return {'type': t, 'coordinates': lines} if lines else None
    if t == 'Polygon':
        rings = [r for r in (_simplify_ring(x, tol, precision, True) for x in c) if r]
        # if the outer ring vanished the whole polygon is below tolerance
        return {'type': t, 'coordinates': rings} if rings else None
    if t == 'MultiPolygon':
        polys = []
        for poly in c:
            rings = [r for r in (_simplify_ring(x, tol, precision, True) for x in poly) if r]
            if rings:
                polys.append(rings)
        return {'type': t, 'coordinates': polys} if polys else None
    if t == 'GeometryCollection':
        geoms = [g for g in (simplify_geometry(g, tol, precision)
                             for g in geom.get('geometries', [])) if g]
        return {'type': t, 'geometries': geoms} if geoms else None
    return geom


def simplify_geojson(fc: dict, tol: float = 0.0001, precision: int = 5,
                     keep: Optional[List[str]] = None,
                     rename: Optional[Dict[str, str]] = None) -> dict:
    """Simplify every feature, optionally trimming/renaming properties."""
    feats = []
    for f in fc.get('features', []):
        g = simplify_geometry(f.get('geometry'), tol, precision)
        if g is None:
            continue
        props = f.get('properties') or {}
        if keep is not None:
            props = {k: props.get(k) for k in keep if k in props}
        if rename:
            props = {rename.get(k, k): v for k, v in props.items()}
        feats.append({'type': 'Feature', 'properties': props, 'geometry': g})
    return {'type': 'FeatureCollection', 'features': feats}


def geojson_bytes(fc: dict) -> int:
    return len(json.dumps(fc, separators=(',', ':')))


def geometry_km2(geom: Optional[dict]) -> float:
    """Approximate area of a (Multi)Polygon in km², good to ~0.1% at city scale."""
    if not geom:
        return 0.0

    def ring_area(ring) -> float:
        R = 6371.0088
        a = 0.0
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            a += math.radians(x2 - x1) * (2 + math.sin(math.radians(y1))
                                          + math.sin(math.radians(y2)))
        return a * R * R / 2

    def poly_area(rings) -> float:
        if not rings:
            return 0.0
        # outer ring minus holes
        return abs(ring_area(rings[0])) - sum(abs(ring_area(r)) for r in rings[1:])

    t, c = geom.get('type'), geom.get('coordinates')
    if t == 'Polygon':
        return max(poly_area(c), 0.0)
    if t == 'MultiPolygon':
        return sum(max(poly_area(p), 0.0) for p in c)
    return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# TTC GTFS → rapid transit / streetcar lines + stations
# ─────────────────────────────────────────────────────────────────────────────

GTFS_PACKAGE = 'ttc-routes-and-schedules'

# The feed leaves route_color blank on a few routes; and every streetcar is
# published as the same blue, which would be indistinguishable from the
# choropleth. Official TTC line colours for the rapid network:
RAPID_FALLBACK = {'1': '#F8C300', '2': '#00923F', '3': '#0082C9',
                  '4': '#A21A68', '5': '#DF8600', '6': '#9E9E9E'}
STREETCAR_COLOR = '#DA251D'      # TTC streetcar red


def _read_gtfs(url: str, timeout: int = 300) -> Dict[str, List[dict]]:
    r = _get(url, timeout=timeout)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    tables = {}
    for name in ('routes.txt', 'trips.txt', 'shapes.txt', 'stops.txt'):
        if name not in z.namelist():
            continue
        with z.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding='utf-8-sig')
            tables[name[:-4]] = list(csv.DictReader(text))
    return tables


def _is_rapid(route: dict) -> bool:
    """Subway/metro by GTFS type, plus anything TTC brands as "Line N"."""
    if route.get('route_type') == '1':
        return True
    return (route.get('route_long_name') or '').strip().lower().startswith('line ')


def _route_color(route: dict) -> str:
    short = (route.get('route_short_name') or '').strip()
    if _is_rapid(route):
        return RAPID_FALLBACK.get(short) or \
            (f"#{route['route_color']}" if route.get('route_color') else '#8b93a7')
    return STREETCAR_COLOR


def _line_features(g: Dict[str, List[dict]], want_rapid: bool,
                   tol: float = 0.00008) -> List[dict]:
    """
    One LineString per (route, direction), longest shape wins.

    GTFS carries one shape per trip pattern — thousands of near-duplicates. The
    single longest shape per direction is the full-length service pattern
    riders think of as "the 501" or "Line 2".
    """
    routes = {r['route_id']: r for r in g.get('routes', [])
              if _is_rapid(r) == want_rapid
              and r.get('route_type') in ('0', '1')}

    shape_pts: Dict[str, List[Tuple[float, list]]] = defaultdict(list)
    for row in g.get('shapes', []):
        try:
            shape_pts[row['shape_id']].append((
                float(row['shape_pt_sequence']),
                [float(row['shape_pt_lon']), float(row['shape_pt_lat'])]))
        except (KeyError, TypeError, ValueError):
            continue

    best: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for t in g.get('trips', []):
        rid = t.get('route_id', '')
        sid = t.get('shape_id')
        if rid not in routes or not sid or sid not in shape_pts:
            continue
        k = (rid, str(t.get('direction_id', '0')))
        n = len(shape_pts[sid])
        if k not in best or n > best[k][0]:
            best[k] = (n, sid)

    feats = []
    for (route_id, direction), (_, sid) in sorted(best.items()):
        pts = [p for _, p in sorted(shape_pts[sid], key=lambda x: x[0])]
        line = _simplify_ring(pts, tol, 5, closed=False)
        if not line:
            continue
        r = routes[route_id]
        feats.append({
            'type': 'Feature',
            'properties': {
                'route': (r.get('route_short_name') or route_id).strip(),
                'name': (r.get('route_long_name') or '').strip(),
                'direction': int(direction) if str(direction).isdigit() else 0,
                'color': _route_color(r),
            },
            'geometry': {'type': 'LineString', 'coordinates': line},
        })
    return feats


def _station_features(g: Dict[str, List[dict]],
                      rapid_lines: List[dict]) -> List[dict]:
    """
    Rapid-transit stations, distilled from platform stops.

    The TTC feed publishes no parent-station records (``location_type`` is
    never 1); platforms are named "<Station> Station - <x> Platform" (subway)
    or "<Station> Station LRT Platform" (Line 5/6). Group by that prefix,
    average the platform coordinates, and tag each station with the lines
    whose track geometry passes within ~250 m of it.
    """
    import re
    groups: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    for s in g.get('stops', []):
        m = re.match(r'^(.*?) Station\b.*Platform', s.get('stop_name', ''), re.I)
        if not m:
            continue
        try:
            groups[m.group(1).strip()].append(
                (float(s['stop_lon']), float(s['stop_lat'])))
        except (KeyError, TypeError, ValueError):
            continue

    # route short-name → its shapes' coordinate lists (both directions)
    by_route: Dict[str, List[List[list]]] = defaultdict(list)
    for f in rapid_lines:
        by_route[f['properties']['route']].append(f['geometry']['coordinates'])

    # ~250 m in degrees at Toronto's latitude
    near2 = 0.0028 ** 2

    feats = []
    for name, pts in sorted(groups.items()):
        lng = sum(p[0] for p in pts) / len(pts)
        lat = sum(p[1] for p in pts) / len(pts)
        lines = []
        for route, shapes in sorted(by_route.items()):
            hit = any(
                _perp_sq((lng, lat), a, b) < near2
                for coords in shapes
                for a, b in zip(coords, coords[1:]))
            if hit:
                lines.append(route)
        feats.append({
            'type': 'Feature',
            'properties': {'name': f'{name} Station', 'lines': ' '.join(lines),
                           'platforms': len(pts)},
            'geometry': {'type': 'Point',
                         'coordinates': [round(lng, 5), round(lat, 5)]},
        })
    return feats


def gtfs_bundle() -> Dict[str, dict]:
    """
    Everything the map needs from the TTC feed, in one cached download.

    The zip is ~35 MB and three layers come out of it (rapid lines, streetcar
    lines, stations), so it is fetched once and the three FeatureCollections
    are cached together.
    """
    def build():
        url = ckan_resource(GTFS_PACKAGE, 'TTC Routes')['url']
        g = _read_gtfs(url)
        rapid = _line_features(g, want_rapid=True)
        streetcar = _line_features(g, want_rapid=False, tol=0.00012)
        stations = _station_features(g, rapid)
        return {
            'rapid': {'type': 'FeatureCollection', 'features': rapid},
            'streetcar': {'type': 'FeatureCollection', 'features': streetcar},
            'stations': {'type': 'FeatureCollection', 'features': stations},
        }
    return cached('transit-gtfs-bundle', 7 * DAY, build)
