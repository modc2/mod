"""
tdot.layers — the map's layer catalogue.

Every layer declares where its data comes from, how it should be drawn, and how
to load it. The frontend reads :func:`catalog` and builds its layer panel from
that, so adding a layer here makes it appear in the UI with no frontend change.

All sources are public and key-free. Attribution travels with the layer (the
``source`` block) and is shown in the UI — this is other people's open data and
it should say so on the map.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from . import crime as C
from . import sources as S

WEEK = 7 * S.DAY


def _src(name: str, package: str) -> dict:
    return {'name': name, 'dataset': package,
            'url': f'https://open.toronto.ca/dataset/{package}/',
            'portal': 'open.toronto.ca'}


# ─────────────────────────────────────────────────────────────────────────────
# boundary layers (also reused as the canvas for the crime choropleth)
# ─────────────────────────────────────────────────────────────────────────────

def neighbourhoods() -> dict:
    """The city's 158 social-planning neighbourhoods — the choropleth default."""
    return S.cached('geo-neighbourhoods', 30 * S.DAY, lambda: S.simplify_geojson(
        S.ckan_geojson('neighbourhoods', 'Neighbourhoods - 4326.geojson'),
        tol=0.00008,
        keep=['AREA_LONG_CODE', 'AREA_NAME', 'CLASSIFICATION']))


def neighbourhoods140() -> dict:
    """The pre-2021 140-neighbourhood model, kept for long history joins."""
    return S.cached('geo-neighbourhoods140', 30 * S.DAY, lambda: S.simplify_geojson(
        S.ckan_geojson('neighbourhoods', 'historical 140 - 4326.geojson'),
        tol=0.00008,
        keep=['AREA_LONG_CODE', 'AREA_NAME']))


def wards() -> dict:
    """The 25 city wards."""
    return S.cached('geo-wards', 30 * S.DAY, lambda: S.simplify_geojson(
        S.ckan_geojson('city-wards', 'City Wards Data - 4326.geojson'),
        tol=0.00010,
        keep=['AREA_LONG_CODE', 'AREA_SHORT_CODE', 'AREA_NAME']))


def municipalities() -> dict:
    """The six pre-amalgamation municipalities (Toronto's "boroughs")."""
    return S.cached('geo-municipalities', 30 * S.DAY, lambda: S.simplify_geojson(
        S.ckan_geojson('former-municipality-boundaries', '4326.geojson'),
        tol=0.00012,
        keep=['AREA_NAME', 'AREA_DESC']))


BOUNDARIES: Dict[str, Callable[[], dict]] = {
    'neighbourhood': neighbourhoods,
    'neighbourhood140': neighbourhoods140,
    'ward': wards,
    'municipality': municipalities,
}


def boundary(name: str) -> dict:
    loader = BOUNDARIES.get(name)
    if not loader:
        raise KeyError(f'unknown boundary {name!r}; known: {sorted(BOUNDARIES)}')
    return loader()


# ─────────────────────────────────────────────────────────────────────────────
# overlay loaders
# ─────────────────────────────────────────────────────────────────────────────

def ttc_lines() -> dict:
    """Subway / rapid-transit lines, in each line's official colour."""
    return S.gtfs_bundle()['rapid']


def streetcars() -> dict:
    """The streetcar network — the largest in North America."""
    return S.gtfs_bundle()['streetcar']


def subway_stations() -> dict:
    """Rapid-transit stations, distilled from the feed's platform stops."""
    return S.gtfs_bundle()['stations']


def cycling_network() -> dict:
    """
    The cycling network as street segments. ``INFRA_HIGHORDER`` is the city's
    facility type; it is folded into three protection classes so the map can
    weight what riders actually plan around: physical separation.
    """
    def fetch():
        fc = S.simplify_geojson(
            S.ckan_geojson('cycling-network', '4326.geojson'), tol=0.00004,
            keep=['STREET_NAME', 'INFRA_HIGHORDER', 'INSTALLED'])
        for f in fc['features']:
            p = f['properties']
            infra = (p.get('INFRA_HIGHORDER') or '').strip()
            low = infra.lower()
            if 'cycle track' in low or 'multi-use trail' in low or 'park road' in low:
                cls, label = 'I', 'Protected / off-street'
            elif 'bike lane' in low or 'contra-flow' in low:
                cls, label = 'II', 'Painted lane'
            else:
                cls, label = 'III', 'Shared / signed route'
            f['properties'] = {
                'street': (p.get('STREET_NAME') or '').strip(),
                'facility': infra,
                'protection': label,
                'cls': cls,
                'installed': p.get('INSTALLED'),
            }
        return fc
    return S.cached('transit-cycling-network', WEEK, fetch)


def green_spaces() -> dict:
    def fetch():
        fc = S.simplify_geojson(
            S.ckan_geojson('green-spaces', '4326.geojson'), tol=0.00006,
            keep=['AREA_NAME', 'AREA_CLASS'])
        for f in fc['features']:
            p = f['properties']
            f['properties'] = {
                'name': (p.get('AREA_NAME') or '').strip().title(),
                'class': (p.get('AREA_CLASS') or '').replace('_', ' ').strip().title(),
            }
        return fc
    return S.cached('env-green-spaces', 30 * S.DAY, fetch)


def collisions() -> dict:
    """
    Serious traffic collisions (killed or seriously injured, 2006–present).

    The source table is one row per *person involved*; rows are folded to one
    point per collision, keeping the counts of people killed and seriously
    injured and whether pedestrians or cyclists were among them.
    """
    def fetch():
        raw = S.ckan_geojson(
            'motor-vehicle-collisions-involving-killed-or-seriously-injured-persons',
            '4326.geojson')
        by_id: Dict[str, dict] = {}
        for f in raw.get('features', []):
            p = {str(k).lower(): v for k, v in (f.get('properties') or {}).items()}
            cid = str(p.get('collision_id') or '')
            try:
                # The exported geometry is a MultiPoint per person-row; the
                # row's own lat/lng columns are the collision location.
                lng, lat = float(p['longitude']), float(p['latitude'])
            except (KeyError, TypeError, ValueError):
                continue
            if not cid or (lat == 0 and lng == 0):
                continue
            c = by_id.get(cid)
            if c is None:
                street = ' & '.join(
                    x for x in ((p.get('stname1') or '').strip().title(),
                                (p.get('stname2') or '').strip().title()) if x)
                c = by_id[cid] = {
                    'date': str(p.get('accdate') or '')[:10],
                    'street': street,
                    'fatal': False, 'killed': 0, 'serious': 0,
                    'pedestrian': False, 'cyclist': False,
                    'road_class': (p.get('road_class') or '').strip(),
                    'light': (p.get('light') or '').strip(),
                    'neighbourhood': (p.get('neighbourhood') or '').strip(),
                    '_coords': [round(float(lng), 5), round(float(lat), 5)],
                }
            inj = (p.get('injury') or '').strip().lower()
            if inj == 'fatal':
                c['killed'] += 1
            elif inj == 'major':
                c['serious'] += 1
            if str(p.get('acclass') or '').strip().lower().startswith('fatal'):
                c['fatal'] = True
            for flag in ('pedestrian', 'cyclist'):
                if str(p.get(flag) or '').strip().lower() == 'true':
                    c[flag] = True
            # person rows are sparse; keep the first non-empty value seen
            for k in ('road_class', 'light', 'neighbourhood'):
                if not c[k] and p.get(k):
                    c[k] = str(p[k]).strip()

        feats = []
        for c in by_id.values():
            coords = c.pop('_coords')
            feats.append({'type': 'Feature', 'properties': c,
                          'geometry': {'type': 'Point', 'coordinates': coords}})
        feats.sort(key=lambda f: f['properties']['date'], reverse=True)
        return {'type': 'FeatureCollection', 'features': feats}
    return S.cached('safety-collisions', WEEK, fetch)


def apartments() -> dict:
    """
    RentSafeTO apartment-building evaluations (2017–2023 program).

    One point per registered rental building of 3+ storeys / 10+ units, with
    its most recent inspection score out of 100. The city's post-2023
    evaluation resource currently publishes only a handful of rows, so the
    completed pre-2023 program is the honest dataset to map.
    """
    def fetch():
        rid = S.ckan_resource('apartment-building-evaluation',
                              'Pre-2023 Apartment Building Evaluations')['id']
        rows = S.ckan_records(rid, max_rows=50000)
        best: Dict[str, dict] = {}
        for r in rows:
            rsn = str(r.get('RSN') or '')
            addr = str(r.get('SITE_ADDRESS') or '')
            if not rsn or 'CREATED IN ERROR' in addr.upper():
                continue
            when = str(r.get('EVALUATION_COMPLETED_ON') or '')
            if rsn not in best or when > best[rsn].get('_when', ''):
                def num(k):
                    try:
                        return int(float(r.get(k)))
                    except (TypeError, ValueError):
                        return None
                best[rsn] = {
                    '_when': when,
                    'address': addr.strip().title(),
                    'ward': (r.get('WARDNAME') or '').strip(),
                    'score': num('SCORE'),
                    'year_built': num('YEAR_BUILT'),
                    'storeys': num('CONFIRMED_STOREYS'),
                    'units': num('CONFIRMED_UNITS'),
                    'evaluated': when[:10],
                    'property_type': (r.get('PROPERTY_TYPE') or '').strip().title(),
                    'LATITUDE': r.get('LATITUDE'),
                    'LONGITUDE': r.get('LONGITUDE'),
                }
        rows = [v for v in best.values() if v.get('score') is not None]
        for v in rows:
            v.pop('_when', None)
        return S.points_from_rows(
            rows, 'LATITUDE', 'LONGITUDE',
            props=['address', 'ward', 'score', 'year_built', 'storeys',
                   'units', 'evaluated', 'property_type'])
    return S.cached('housing-apartments', 30 * S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# catalogue
# ─────────────────────────────────────────────────────────────────────────────

LAYERS: List[Dict[str, Any]] = [
    # ── Crime ────────────────────────────────────────────────────────────
    {
        'id': 'crime',
        'title': 'Major crime',
        'category': 'Crime',
        'kind': 'choropleth',
        'geometry': 'polygon',
        'default_on': True,
        'description': ('Police-reported major crime aggregated by neighbourhood. '
                        'Choose the metric, geography, crime type and time window.'),
        'controls': {
            'metric': list(C.METRICS.keys()),
            'geography': list(C.GEOGRAPHIES.keys()),
            'category': list(C.CATEGORIES.keys()),
            'window': True,
        },
        'metrics': C.METRICS,
        'geographies': C.GEOGRAPHIES,
        'categories': C.CATEGORIES,
        'endpoint': '/layers/crime',
        'source': _src('TPS Major Crime Indicators', 'major-crime-indicators'),
    },
    {
        'id': 'incidents',
        'title': 'Individual incidents',
        'category': 'Crime',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': ('Recent incidents as points, coloured by crime type. '
                        'Locations are offset to the nearest intersection by the '
                        'police service.'),
        'style': {'color_by': 'cat', 'radius': 3.2},
        'controls': {'category': list(C.CATEGORIES.keys()), 'window': True},
        'endpoint': '/layers/incidents',
        'source': _src('TPS Major Crime Indicators', 'major-crime-indicators'),
    },

    # ── Transit ──────────────────────────────────────────────────────────
    {
        'id': 'ttc_lines',
        'title': 'Subway lines',
        'category': 'Transit',
        'kind': 'line',
        'geometry': 'line',
        'default_on': True,
        'description': 'TTC rapid-transit lines, drawn in their official colours.',
        'style': {'color_property': 'color', 'width': 2.4},
        'endpoint': '/layers/ttc_lines',
        'source': _src('TTC Routes and Schedules (GTFS)', 'ttc-routes-and-schedules'),
    },
    {
        'id': 'subway_stations',
        'title': 'Subway stations',
        'category': 'Transit',
        'kind': 'point',
        'geometry': 'point',
        'default_on': True,
        'description': 'Rapid-transit stations with the lines that serve them.',
        'style': {'color': '#ffffff', 'stroke': '#111827', 'radius': 3.4},
        'endpoint': '/layers/subway_stations',
        'source': _src('TTC Routes and Schedules (GTFS)', 'ttc-routes-and-schedules'),
    },
    {
        'id': 'streetcars',
        'title': 'Streetcar network',
        'category': 'Transit',
        'kind': 'line',
        'geometry': 'line',
        'default_on': False,
        'description': 'All streetcar routes — the largest such network in North America.',
        'style': {'color': '#DA251D', 'width': 1.6},
        'endpoint': '/layers/streetcars',
        'source': _src('TTC Routes and Schedules (GTFS)', 'ttc-routes-and-schedules'),
    },
    {
        'id': 'cycling_network',
        'title': 'Cycling network',
        'category': 'Transit',
        'kind': 'line',
        'geometry': 'line',
        'default_on': False,
        'description': 'Cycle tracks, trails, painted lanes and signed routes citywide.',
        'style': {'color': '#22d3ee', 'width': 1.4},
        'endpoint': '/layers/cycling_network',
        'source': _src('Cycling Network', 'cycling-network'),
    },

    # ── Environment ──────────────────────────────────────────────────────
    {
        'id': 'green_spaces',
        'title': 'Parks & green space',
        'category': 'Environment',
        'kind': 'polygon',
        'geometry': 'polygon',
        'default_on': False,
        'description': 'Every city green space, from parkettes to Rouge Park\'s edge.',
        'style': {'color': '#34d399', 'opacity': 0.45},
        'endpoint': '/layers/green_spaces',
        'source': _src('Green Spaces', 'green-spaces'),
    },

    # ── Housing ──────────────────────────────────────────────────────────
    {
        'id': 'apartments',
        'title': 'Apartment building scores',
        'category': 'Housing',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': ('RentSafeTO evaluation scores for registered rental '
                        'buildings (3+ storeys, 10+ units), sized by unit count.'),
        'style': {'color_by': 'score', 'size_by': 'units'},
        'endpoint': '/layers/apartments',
        'source': _src('Apartment Building Evaluation', 'apartment-building-evaluation'),
    },

    # ── Safety ───────────────────────────────────────────────────────────
    {
        'id': 'collisions',
        'title': 'Serious traffic collisions',
        'category': 'Safety',
        'kind': 'heatmap',
        'geometry': 'point',
        'default_on': False,
        'description': ('Collisions since 2006 in which someone was killed or '
                        'seriously injured (Vision Zero reporting).'),
        'style': {'color': '#f87171', 'weight_by': 'serious'},
        'endpoint': '/layers/collisions',
        'source': _src('Motor Vehicle Collisions (KSI)',
                       'motor-vehicle-collisions-involving-killed-or-seriously-injured-persons'),
    },

    # ── Boundaries ───────────────────────────────────────────────────────
    {
        'id': 'municipalities',
        'title': 'Former municipalities',
        'category': 'Boundaries',
        'kind': 'outline',
        'geometry': 'polygon',
        'default_on': False,
        'description': 'The six pre-1998 municipalities: Toronto, North York, Scarborough, Etobicoke, York and East York.',
        'style': {'color': '#94a3b8', 'width': 1.5},
        'endpoint': '/layers/municipalities',
        'source': _src('Former Municipality Boundaries', 'former-municipality-boundaries'),
    },
    {
        'id': 'neighbourhoods',
        'title': 'Neighbourhood boundaries',
        'category': 'Boundaries',
        'kind': 'outline',
        'geometry': 'polygon',
        'default_on': False,
        'description': 'The 158 social-planning neighbourhoods (2021 model).',
        'style': {'color': '#64748b', 'width': 0.8},
        'endpoint': '/layers/neighbourhoods',
        'source': _src('Neighbourhoods', 'neighbourhoods'),
    },
    {
        'id': 'wards',
        'title': 'Ward boundaries',
        'category': 'Boundaries',
        'kind': 'outline',
        'geometry': 'polygon',
        'default_on': False,
        'description': 'The 25 city wards.',
        'style': {'color': '#8b7355', 'width': 1.0},
        'endpoint': '/layers/wards',
        'source': _src('City Wards', 'city-wards'),
    },
]

# id → loader for every layer served straight from a source (crime and
# incidents are parameterised, so they're handled by the API rather than here).
LOADERS: Dict[str, Callable[[], dict]] = {
    'ttc_lines': ttc_lines,
    'streetcars': streetcars,
    'subway_stations': subway_stations,
    'cycling_network': cycling_network,
    'green_spaces': green_spaces,
    'collisions': collisions,
    'apartments': apartments,
    'municipalities': municipalities,
    'neighbourhoods': neighbourhoods,
    'wards': wards,
}


def catalog() -> Dict[str, Any]:
    """The full layer catalogue, grouped by category — this drives the UI."""
    cats: Dict[str, List[dict]] = {}
    for layer in LAYERS:
        cats.setdefault(layer['category'], []).append(layer)
    return {
        'layers': LAYERS,
        'categories': [{'name': c, 'layers': [l['id'] for l in ls]}
                       for c, ls in cats.items()],
        'count': len(LAYERS),
        'attribution': [
            {'name': 'Toronto Open Data', 'url': 'https://open.toronto.ca'},
            {'name': 'Toronto Police Service open data', 'url': 'https://data.torontopolice.on.ca'},
            {'name': 'TTC', 'url': 'https://www.ttc.ca'},
            {'name': 'OpenStreetMap contributors', 'url': 'https://www.openstreetmap.org/copyright'},
        ],
    }


def get(layer_id: str) -> dict:
    loader = LOADERS.get(layer_id)
    if not loader:
        raise KeyError(f'unknown layer {layer_id!r}; known: {sorted(LOADERS)}')
    return loader()
