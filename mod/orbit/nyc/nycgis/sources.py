"""
nyc.sources — open-data fetching, caching and geometry shrinking.

Everything the map draws comes from a public, no-key, open-data endpoint:

  * NYC Open Data (Socrata)   https://data.cityofnewyork.us
  * NY State Open Data        https://data.ny.gov          (MTA tables)
  * MTA GTFS static feeds     https://rrgtfsfeeds.s3.amazonaws.com

Responses are cached on disk under ``~/.mod/nyc/cache`` so the map is fast and
keeps working when the upstream APIs are slow or unreachable. A stale cache
entry is preferred over an error: if a refresh fails we serve what we have.

Geometry from the city's portal is authoritative-precision (full coastline
detail, 14 decimal places) which is far more than a browser map needs — a
single borough file is ~3 MB. ``simplify_geojson`` runs Ramer-Douglas-Peucker
and rounds coordinates, typically cutting payloads by 10-30x with no visible
difference at city zoom levels.
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
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import requests

CACHE_DIR = Path(os.path.expanduser('~/.mod/nyc/cache'))
NYC = 'https://data.cityofnewyork.us'
NYS = 'https://data.ny.gov'
GTFS_SUBWAY = 'https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip'

DAY = 86400
USER_AGENT = 'mod-nyc/1.0 (open-source NYC GIS module)'

# Socrata caps an unauthenticated page at 50k rows.
SOCRATA_PAGE = 50_000


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
# Socrata
# ─────────────────────────────────────────────────────────────────────────────

def soql(domain: str, dataset: str, timeout: int = 90, **params) -> List[dict]:
    """One SoQL query. Keys are passed as ``$select``, ``$where``, … ."""
    q = {(k if k.startswith('$') else '$' + k): v for k, v in params.items() if v is not None}
    url = f'{domain}/resource/{dataset}.json'
    r = requests.get(url, params=q, timeout=timeout, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    return r.json()


def soql_all(domain: str, dataset: str, max_rows: int = 200_000, **params) -> List[dict]:
    """Page through a SoQL query until exhausted or ``max_rows`` is reached."""
    out: List[dict] = []
    while len(out) < max_rows:
        page = soql(domain, dataset,
                    limit=min(SOCRATA_PAGE, max_rows - len(out)),
                    offset=len(out), **params)
        out.extend(page)
        if len(page) < SOCRATA_PAGE:
            break
    return out


def socrata_geojson(dataset: str, domain: str = NYC, timeout: int = 180) -> dict:
    """Download a Socrata geospatial dataset as a GeoJSON FeatureCollection."""
    url = f'{domain}/api/geospatial/{dataset}?method=export&format=GeoJSON'
    r = requests.get(url, timeout=timeout, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    fc = r.json()
    if fc.get('type') != 'FeatureCollection':
        raise ValueError(f'{dataset}: expected FeatureCollection, got {fc.get("type")!r}')
    return fc


# ─────────────────────────────────────────────────────────────────────────────
# NY State Plane → WGS84
#
# DOT's traffic-count file publishes location as WKT in EPSG:2263 (NAD83 / New
# York Long Island, US survey feet) rather than lat/lng, so it has to be
# unprojected before anything can be drawn. That is a Lambert Conformal Conic
# inverse, done here in the stdlib: pulling in pyproj (and its PROJ database)
# to convert a few hundred points would be the largest dependency in the
# module by an order of magnitude.
#
# The datum shift from NAD83 to WGS84 is under a metre in NYC and is ignored —
# it is far below the precision the counts themselves are located to.
# ─────────────────────────────────────────────────────────────────────────────

_SP_A = 6378137.0                       # GRS80 semi-major axis, metres
_SP_E = math.sqrt(2 / 298.257222101 - (1 / 298.257222101) ** 2)
_SP_FT = 1200.0 / 3937.0                # US survey foot, exactly
_SP_LAT1 = math.radians(40 + 40 / 60)          # standard parallels
_SP_LAT2 = math.radians(41 + 2 / 60 + 20 / 3600)
_SP_LAT0 = math.radians(40 + 10 / 60)          # latitude of origin
_SP_LON0 = math.radians(-74.0)                 # central meridian
_SP_FE = 984250.0 * _SP_FT                     # false easting, metres


def _sp_m(phi: float) -> float:
    return math.cos(phi) / math.sqrt(1 - _SP_E ** 2 * math.sin(phi) ** 2)


def _sp_t(phi: float) -> float:
    s = _SP_E * math.sin(phi)
    return math.tan(math.pi / 4 - phi / 2) / ((1 - s) / (1 + s)) ** (_SP_E / 2)


_SP_N = (math.log(_sp_m(_SP_LAT1) / _sp_m(_SP_LAT2))
         / math.log(_sp_t(_SP_LAT1) / _sp_t(_SP_LAT2)))
_SP_F = _sp_m(_SP_LAT1) / (_SP_N * _sp_t(_SP_LAT1) ** _SP_N)
_SP_RHO0 = _SP_A * _SP_F * _sp_t(_SP_LAT0) ** _SP_N


def state_plane_to_wgs84(x_ft: float, y_ft: float) -> Tuple[float, float]:
    """EPSG:2263 easting/northing in US survey feet → ``(lng, lat)`` degrees."""
    x = x_ft * _SP_FT - _SP_FE
    y = y_ft * _SP_FT
    rho = math.copysign(math.hypot(x, _SP_RHO0 - y), _SP_N)
    t = (rho / (_SP_A * _SP_F)) ** (1 / _SP_N)
    lon = math.atan2(x, _SP_RHO0 - y) / _SP_N + _SP_LON0
    # Latitude has no closed form; the series converges in a handful of passes.
    phi = math.pi / 2 - 2 * math.atan(t)
    for _ in range(12):
        s = _SP_E * math.sin(phi)
        nxt = math.pi / 2 - 2 * math.atan(t * ((1 - s) / (1 + s)) ** (_SP_E / 2))
        if abs(nxt - phi) < 1e-12:
            phi = nxt
            break
        phi = nxt
    return math.degrees(lon), math.degrees(phi)


def wkt_point_to_lnglat(wkt: str) -> Optional[Tuple[float, float]]:
    """Unproject a ``POINT (x y)`` in state-plane feet. None if unparseable."""
    if not wkt or 'POINT' not in wkt.upper():
        return None
    try:
        inner = wkt[wkt.index('(') + 1:wkt.index(')')]
        x_str, y_str = inner.replace(',', ' ').split()[:2]
        lng, lat = state_plane_to_wgs84(float(x_str), float(y_str))
    except (ValueError, IndexError, ZeroDivisionError, OverflowError):
        return None
    # A count location outside the city's envelope means the row was projected
    # from something other than state-plane feet; drop it rather than draw it.
    if not (-74.30 <= lng <= -73.68) or not (40.47 <= lat <= 40.93):
        return None
    return round(lng, 5), round(lat, 5)


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


# ─────────────────────────────────────────────────────────────────────────────
# MTA GTFS → subway lines + stations
# ─────────────────────────────────────────────────────────────────────────────

def _read_gtfs(url: str, timeout: int = 180) -> Dict[str, List[dict]]:
    r = requests.get(url, timeout=timeout, headers={'User-Agent': USER_AGENT})
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    tables = {}
    for name in ('routes.txt', 'trips.txt', 'shapes.txt', 'stops.txt'):
        if name not in z.namelist():
            continue
        with z.open(name) as fh:
            text = io.TextIOWrapper(fh, encoding='utf-8-sig')
            tables[name[:-4]] = list(csv.DictReader(text))
    return tables


def subway_lines(url: str = GTFS_SUBWAY, tol: float = 0.00008) -> dict:
    """
    Subway route geometry as a LineString FeatureCollection, one feature per
    route direction, coloured with the MTA's official ``route_color``.

    GTFS carries one shape per trip pattern — thousands of near-duplicates. We
    keep the single longest shape per (route, direction), which is the full-
    length service pattern riders think of as "the 4 train".
    """
    g = _read_gtfs(url)
    routes = {r['route_id']: r for r in g.get('routes', [])}

    shape_pts: Dict[str, List[Tuple[float, list]]] = defaultdict(list)
    for row in g.get('shapes', []):
        try:
            shape_pts[row['shape_id']].append((
                float(row['shape_pt_sequence']),
                [float(row['shape_pt_lon']), float(row['shape_pt_lat'])]))
        except (KeyError, TypeError, ValueError):
            continue

    # longest shape per (route_id, direction_id)
    best: Dict[Tuple[str, str], Tuple[int, str]] = {}
    for t in g.get('trips', []):
        sid = t.get('shape_id')
        if not sid or sid not in shape_pts:
            continue
        k = (t.get('route_id', ''), str(t.get('direction_id', '0')))
        n = len(shape_pts[sid])
        if k not in best or n > best[k][0]:
            best[k] = (n, sid)

    feats = []
    for (route_id, direction), (_, sid) in sorted(best.items()):
        pts = [p for _, p in sorted(shape_pts[sid], key=lambda x: x[0])]
        line = _simplify_ring(pts, tol, 5, closed=False)
        if not line:
            continue
        r = routes.get(route_id, {})
        color = (r.get('route_color') or '').strip()
        feats.append({
            'type': 'Feature',
            'properties': {
                'route': r.get('route_short_name') or route_id,
                'route_id': route_id,
                'direction': int(direction) if str(direction).isdigit() else 0,
                'name': r.get('route_long_name') or '',
                'desc': (r.get('route_desc') or '').strip(),
                'color': f'#{color}' if color else '#8b93a7',
            },
            'geometry': {'type': 'LineString', 'coordinates': line},
        })
    return {'type': 'FeatureCollection', 'features': feats}


def subway_route_colors(url: str = GTFS_SUBWAY) -> Dict[str, str]:
    """``{'A': '#2850ad', ...}`` — the official colour of every subway route."""
    g = _read_gtfs(url)
    out = {}
    for r in g.get('routes', []):
        short = (r.get('route_short_name') or r.get('route_id') or '').strip()
        color = (r.get('route_color') or '').strip()
        if short and color:
            out[short] = f'#{color}'
    return out
