"""
3w — 3world: free 3D models of any city, and the seed of an open 3D-asset marketplace.

Any place on Earth becomes a 3D model for free: geocode it (Nominatim), pull
building footprints + heights from OpenStreetMap (Overpass), extrude them into
an OBJ you can open in Blender/three.js/anything. NYC and Toronto additionally
have official, city-published 3D massing models — `sources` points at them.

The marketplace half (3world) starts as a browser over Poly Haven's CC0 model
catalog and is meant to grow into a free exchange of 3D assets.

Everything is keyless public data. Nothing here is proprietary.

CLI:
    m 3w                               # null call → status + what this is
    m 3w/cities                        # supported city shortcuts + their official datasets
    m 3w/where "Lower Manhattan"       # geocode a place → bbox
    m 3w/buildings "Kensington Market, Toronto"   # footprints+heights as GeoJSON
    m 3w/model "Financial District, NYC"          # extruded OBJ → ~/.mod/3w/models/
    m 3w/model bbox=-74.016,40.702,-74.004,40.712
    m 3w/sources nyc                   # the official free 3D downloads for a city
    m 3w/assets "medieval chair"       # CC0 models from Poly Haven (3world seed)
    m 3w/asset ArmChair_01             # one asset's files/download links
"""
import json
import math
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path.home() / '.mod' / '3w'
MODELS_DIR = STATE_DIR / 'models'
CACHE_DIR = STATE_DIR / 'cache'

UA = '3w/0.1 (mod protocol; free 3D city models)'
OVERPASS = 'https://overpass-api.de/api/interpreter'
NOMINATIM = 'https://nominatim.openstreetmap.org/search'
POLYHAVEN = 'https://api.polyhaven.com'

# City shortcuts: a sensible default area, plus the official city-published
# 3D dataset so people can grab the real thing when OSM extrusion isn't enough.
CITIES = {
    'nyc': {
        'name': 'New York City',
        'center': [-74.0060, 40.7128],
        'default_area': 'Financial District, Manhattan, New York',
        'official': {
            'title': 'NYC 3D Building Massing Model (DCP)',
            'formats': ['CityGML', 'Esri Multipatch'],
            'license': 'NYC Open Data (free)',
            'url': 'https://www.nyc.gov/site/planning/data-maps/open-data/dwn-nyc-3d-model-download.page',
        },
        'see_also': 'orbit/nyc — full NYC GIS module (layers, census, prices)',
    },
    'toronto': {
        'name': 'Toronto',
        'center': [-79.3832, 43.6532],
        'default_area': 'Financial District, Toronto',
        'official': {
            'title': 'City of Toronto 3D Massing',
            'formats': ['OBJ', 'FBX', 'CityGML', 'SketchUp', '3DS', 'Shapefile'],
            'license': 'Open Government Licence – Toronto (free)',
            'url': 'https://open.toronto.ca/dataset/3d-massing/',
        },
        'see_also': 'orbit/tdot — full Toronto GIS module',
    },
}

ANY_CITY = {
    'openstreetmap': 'Overpass building footprints + height/levels tags (what model() uses)',
    'overture': 'https://overturemaps.org — global buildings w/ heights, GeoParquet, free',
}


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _post(url: str, data: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, data=data.encode(), headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _slug(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')[:60] or 'model'


def _height(tags: dict) -> float:
    """Building height in meters: height tag, else levels * 3m, else 8m."""
    h = tags.get('height', '')
    m = re.match(r'^([\d.]+)', str(h))
    if m:
        try:
            return max(2.0, float(m.group(1)))
        except ValueError:
            pass
    try:
        return max(2.0, float(tags.get('building:levels')) * 3.0)
    except (TypeError, ValueError):
        return 8.0


def _ear_clip(ring):
    """Triangulate a simple polygon ring [(x,y),...] by ear clipping.
    Good enough for building roofs; degenerate rings fall back to a fan."""
    pts = list(ring)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts.pop()
    n = len(pts)
    if n < 3:
        return []
    area2 = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    if area2 < 0:
        pts.reverse()
    idx = list(range(len(pts)))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        ear_found = False
        for k in range(len(idx)):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            ax, ay = pts[a]; bx, by = pts[b]; cx, cy = pts[c]
            if (bx - ax) * (cy - ay) - (cx - ax) * (by - ay) <= 0:
                continue  # reflex
            def inside(px, py):
                d1 = (bx - ax) * (py - ay) - (px - ax) * (by - ay)
                d2 = (cx - bx) * (py - by) - (px - bx) * (cy - by)
                d3 = (ax - cx) * (py - cy) - (px - cx) * (ay - cy)
                return d1 >= 0 and d2 >= 0 and d3 >= 0
            if any(inside(*pts[j]) for j in idx if j not in (a, b, c)):
                continue
            tris.append((a, b, c))
            idx.pop(k)
            ear_found = True
            break
        if not ear_found:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    elif len(idx) > 3:
        tris.extend((idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1))
    return tris


class Mod:
    description = 'Free 3D city models (OSM-extruded OBJ, NYC/Toronto official datasets) + CC0 asset catalog'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        for d in (MODELS_DIR, CACHE_DIR):
            d.mkdir(parents=True, exist_ok=True)

    def forward(self, query: str = '', **kwargs):
        return self.model(query, **kwargs) if query else self.status()

    def cities(self) -> dict:
        """City shortcuts with official free 3D datasets, plus the any-city path."""
        return {'cities': CITIES, 'any_city': ANY_CITY,
                'hint': "m 3w/model '<any place on earth>'"}

    def sources(self, city: str = '') -> dict:
        """The official free 3D downloads for a city (or every known source)."""
        c = CITIES.get(str(city).lower().strip())
        if c:
            return {'city': c['name'], 'official': c['official'], 'see_also': c.get('see_also'),
                    'fallback': ANY_CITY}
        return {'known_cities': {k: v['official'] for k, v in CITIES.items()}, 'any_city': ANY_CITY}

    def where(self, place: str) -> dict:
        """Geocode a place name → {name, bbox:[w,s,e,n], center} via Nominatim."""
        q = urllib.parse.urlencode({'q': place, 'format': 'json', 'limit': 1})
        rows = json.loads(_get(f'{NOMINATIM}?{q}', timeout=30))
        if not rows:
            return {'ok': False, 'error': f'no match for {place!r}'}
        r = rows[0]
        s, n, w, e = (float(x) for x in r['boundingbox'])
        return {'ok': True, 'name': r.get('display_name'), 'bbox': [w, s, e, n],
                'center': [float(r['lon']), float(r['lat'])]}

    def _resolve_bbox(self, place: str, bbox, cap_km: float) -> dict:
        """place or bbox → {'bbox':[w,s,e,n], 'name':...}, clamped to cap_km a side."""
        name = None
        if bbox is not None:
            if isinstance(bbox, str):
                bbox = [float(x) for x in bbox.split(',')]
            w, s, e, n = (float(x) for x in bbox)
        else:
            if not place:
                return {'ok': False, 'error': 'pass a place name or bbox=[w,s,e,n]'}
            g = self.where(place)
            if not g.get('ok'):
                return g
            w, s, e, n = g['bbox']
            name = g['name']
        # Clamp around the center: a city-name bbox can span a whole metro.
        cap = float(cap_km)
        clat, clon = (s + n) / 2, (w + e) / 2
        half_lat = min((n - s) / 2, cap / 2 / 110.54)
        half_lon = min((e - w) / 2, cap / 2 / (111.32 * max(0.1, math.cos(math.radians(clat)))))
        return {'ok': True, 'name': name,
                'bbox': [clon - half_lon, clat - half_lat, clon + half_lon, clat + half_lat]}

    def buildings(self, place: str = '', bbox=None, cap_km: float = 2.0) -> dict:
        """Building footprints + heights inside a place/bbox, as GeoJSON.
        bbox is [w,s,e,n]; a named place is geocoded first. The box is clamped
        to cap_km on a side so a city-name query doesn't pull a whole metro."""
        box = self._resolve_bbox(place, bbox, cap_km)
        if 'error' in box:
            return box
        w, s, e, n = box['bbox']
        query = (f'[out:json][timeout:90];'
                 f'(way["building"]({s},{w},{n},{e});'
                 f'relation["building"]({s},{w},{n},{e}););'
                 f'out tags geom;')
        cache = CACHE_DIR / f'op_{_slug(f"{w}_{s}_{e}_{n}")}.json'
        if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
            data = json.loads(cache.read_text())
        else:
            data = json.loads(_post(OVERPASS, 'data=' + urllib.parse.quote(query)))
            cache.write_text(json.dumps(data))
        feats = []
        for el in data.get('elements', []):
            geom = el.get('geometry') or (el.get('members') or [{}])[0].get('geometry')
            if not geom or len(geom) < 4:
                continue
            tags = el.get('tags', {})
            ring = [[p['lon'], p['lat']] for p in geom]
            feats.append({'type': 'Feature',
                          'properties': {'height_m': _height(tags),
                                         'name': tags.get('name'),
                                         'building': tags.get('building')},
                          'geometry': {'type': 'Polygon', 'coordinates': [ring]}})
        return {'ok': True, 'area': box.get('name') or box['bbox'], 'bbox': box['bbox'],
                'count': len(feats),
                'geojson': {'type': 'FeatureCollection', 'features': feats}}

    def model(self, place: str = '', bbox=None, out: Optional[str] = None,
              cap_km: float = 2.0) -> dict:
        """Extrude an area's buildings into an OBJ file under ~/.mod/3w/models/.
        Coordinates are local meters (ENU-ish), Z up, ground at 0."""
        b = self.buildings(place, bbox=bbox, cap_km=cap_km)
        if not b.get('ok'):
            return b
        feats = b['geojson']['features']
        if not feats:
            return {'ok': False, 'error': 'no buildings in this area', 'bbox': b['bbox']}
        w, s, e, n = b['bbox']
        lat0, lon0 = (s + n) / 2, (w + e) / 2
        mx = 111320.0 * math.cos(math.radians(lat0))
        my = 110540.0
        verts, faces = [], []
        for f in feats:
            ring = f['geometry']['coordinates'][0]
            h = f['properties']['height_m']
            pts = [((lon - lon0) * mx, (lat - lat0) * my) for lon, lat in ring]
            if pts[0] == pts[-1]:
                pts.pop()
            if len(pts) < 3:
                continue
            base = len(verts)
            verts += [(x, y, 0.0) for x, y in pts] + [(x, y, h) for x, y in pts]
            k = len(pts)
            for i in range(k):  # walls
                j = (i + 1) % k
                faces.append((base + i, base + j, base + k + j, base + k + i))
            for a, bb, c in _ear_clip(pts):  # roof
                faces.append((base + k + a, base + k + bb, base + k + c))
        name = _slug(place or f'{w}_{s}')
        dest = Path(out) if out else MODELS_DIR / f'{name}.obj'
        with open(dest, 'w') as fh:
            fh.write(f'# 3w — {b["area"]} — {len(feats)} buildings — OSM © contributors, ODbL\n')
            for v in verts:
                fh.write('v %.2f %.2f %.2f\n' % v)
            for face in faces:
                fh.write('f ' + ' '.join(str(i + 1) for i in face) + '\n')
        return {'ok': True, 'path': str(dest), 'buildings': len(feats),
                'vertices': len(verts), 'faces': len(faces), 'area': b['area'],
                'units': 'meters, Z-up', 'attribution': 'OpenStreetMap contributors (ODbL)'}

    def assets(self, query: str = '', k: int = 20) -> dict:
        """3world marketplace seed: search Poly Haven's CC0 model catalog."""
        raw = json.loads(_get(f'{POLYHAVEN}/assets?type=models', timeout=30))
        q = str(query).lower()
        hits = []
        for aid, a in raw.items():
            hay = ' '.join([aid, a.get('name', ''), *a.get('tags', []), *a.get('categories', [])]).lower()
            if not q or q in hay:
                hits.append({'id': aid, 'name': a.get('name'), 'categories': a.get('categories'),
                             'tags': a.get('tags', [])[:8], 'license': 'CC0',
                             'page': f'https://polyhaven.com/a/{aid}'})
            if len(hits) >= int(k):
                break
        return {'ok': True, 'source': 'polyhaven (CC0)', 'count': len(hits), 'results': hits}

    def asset(self, asset_id: str) -> dict:
        """One Poly Haven asset's downloadable files (glTF/blend/fbx per resolution)."""
        files = json.loads(_get(f'{POLYHAVEN}/files/{asset_id}', timeout=30))
        return {'ok': True, 'id': asset_id, 'license': 'CC0',
                'page': f'https://polyhaven.com/a/{asset_id}', 'files': files}

    def status(self) -> dict:
        models = sorted(MODELS_DIR.glob('*.obj'), key=lambda p: p.stat().st_mtime, reverse=True)
        return {'module': '3w (3world)', 'what': Mod.description,
                'cities': list(CITIES), 'any_city': True,
                'models_on_disk': [p.name for p in models[:10]], 'models_dir': str(MODELS_DIR),
                'try': "m 3w/model 'Financial District, Toronto'"}

    def test(self) -> dict:
        """Offline self-test: triangulation + extrusion geometry, no network."""
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        tris = _ear_clip(sq)
        ok_tri = len(tris) == 2
        ok_h = _height({'height': '21.5 m'}) == 21.5 and _height({'building:levels': '4'}) == 12.0 \
            and _height({}) == 8.0
        return {'ok': ok_tri and ok_h, 'ear_clip_square': tris, 'heights_ok': ok_h}
