"""
nyc.layers — the map's layer catalogue.

Every layer declares where its data comes from, how it should be drawn, and how
to load it. The frontend reads :func:`catalog` and builds its layer panel from
that, so adding a layer here makes it appear in the UI with no frontend change.

All sources are public and key-free. Attribution travels with the layer (the
``source`` block) and is shown in the UI — this is other people's open data and
it should say so on the map.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from . import prices as P
from . import rents as R
from . import sources as S
from . import traffic as TR

WEEK = 7 * S.DAY


def _src(name: str, dataset: str, domain: str = 'data.cityofnewyork.us') -> dict:
    return {'name': name, 'dataset': dataset,
            'url': f'https://{domain}/d/{dataset}', 'portal': domain}


# ─────────────────────────────────────────────────────────────────────────────
# boundary layers (also reused as the canvas for housing choropleths)
# ─────────────────────────────────────────────────────────────────────────────

def boroughs() -> dict:
    return S.cached('geo-boroughs', 30 * S.DAY, lambda: S.simplify_geojson(
        S.socrata_geojson('gthc-hcne'), tol=0.00012,
        keep=['borocode', 'boroname', 'shape_area']))


def neighborhoods() -> dict:
    """2020 Neighborhood Tabulation Areas — 262 areas, the choropleth default."""
    return S.cached('geo-nta', 30 * S.DAY, lambda: S.simplify_geojson(
        S.socrata_geojson('9nt8-h7nd'), tol=0.00008,
        keep=['nta2020', 'ntaname', 'ntaabbrev', 'boroname', 'borocode',
              'cdta2020', 'cdtaname', 'ntatype']))


def zips() -> dict:
    """Modified ZIP Code Tabulation Areas (MODZCTA)."""
    return S.cached('geo-zip', 30 * S.DAY, lambda: S.simplify_geojson(
        S.socrata_geojson('pri4-ifjk'), tol=0.00010,
        keep=['modzcta', 'label', 'zcta', 'pop_est']))


BOUNDARIES: Dict[str, Callable[[], dict]] = {
    'borough': boroughs,
    'nta': neighborhoods,
    'zip': zips,
}


def boundary(name: str) -> dict:
    loader = BOUNDARIES.get(name)
    if not loader:
        raise KeyError(f'unknown boundary {name!r}; known: {sorted(BOUNDARIES)}')
    return loader()


# ─────────────────────────────────────────────────────────────────────────────
# overlay loaders
# ─────────────────────────────────────────────────────────────────────────────

def subway_lines() -> dict:
    return S.cached('transit-subway-lines', WEEK, S.subway_lines)


def subway_stations() -> dict:
    """496 subway stations, with the routes serving each and ADA status."""
    def fetch():
        rows = S.soql_all(
            S.NYS, '39hk-dx4f', max_rows=2000,
            select=('stop_name,daytime_routes,line,borough,structure,ada,'
                    'gtfs_latitude,gtfs_longitude,division'))
        boro = {'M': 'Manhattan', 'Bk': 'Brooklyn', 'Bx': 'Bronx',
                'Q': 'Queens', 'SI': 'Staten Island'}
        for r in rows:
            r['name'] = r.pop('stop_name', '')
            r['routes'] = (r.pop('daytime_routes', '') or '').strip()
            r['borough'] = boro.get(r.get('borough', ''), r.get('borough', ''))
            r['accessible'] = str(r.get('ada', '0')) in ('1', '2')
        return S.points_from_rows(
            rows, 'gtfs_latitude', 'gtfs_longitude',
            props=['name', 'routes', 'line', 'borough', 'structure',
                   'division', 'accessible'])
    return S.cached('transit-subway-stations', WEEK, fetch)


def subway_ridership(since: str = '2025-01-01') -> dict:
    """Station complexes sized by ridership. The source table is monthly."""
    def fetch():
        rows = S.soql(
            S.NYS, 'ak4z-sape',
            select=('station_complex,borough,sum(ridership) as riders,'
                    'sum(transfers) as transfers,latitude,longitude'),
            where=f'month >= "{since}T00:00:00.000"',
            group='station_complex,borough,latitude,longitude',
            order='sum(ridership) DESC', limit=2000)
        for r in rows:
            r['riders'] = int(float(r.get('riders') or 0))
            r['transfers'] = int(float(r.get('transfers') or 0))
            r['name'] = r.pop('station_complex', '')
        return S.points_from_rows(rows, 'latitude', 'longitude',
                                  props=['name', 'borough', 'riders', 'transfers'])
    return S.cached(f'transit-subway-ridership-{since}', WEEK, fetch)


def bike_routes() -> dict:
    """
    The bike network as ~30k street segments. Properties are trimmed hard here:
    at this feature count the per-feature property bag, not the geometry, is
    what decides whether the payload is 2 MB or 10 MB.
    """
    def fetch():
        fc = S.simplify_geojson(
            S.socrata_geojson('mzxg-pwib'), tol=0.00004,
            keep=['street', 'ft_facilit', 'tf_facilit', 'facilitycl'])
        # facilitycl is the protection class: I = protected, II = lane, III = route
        cls_label = {'I': 'Protected path', 'II': 'Striped lane', 'III': 'Signed route'}
        for f in fc['features']:
            p = f['properties']
            facility = p.get('ft_facilit') or p.get('tf_facilit') or ''
            cls = (p.get('facilitycl') or '').strip().upper()
            f['properties'] = {
                'street': (p.get('street') or '').strip(),
                'facility': facility.strip(),
                'protection': cls_label.get(cls, 'Other'),
                'cls': cls,
            }
        return fc
    return S.cached('transit-bike-routes', WEEK, fetch)


def parks() -> dict:
    def fetch():
        fc = S.socrata_geojson('enfh-gkve')
        return S.simplify_geojson(
            fc, tol=0.00006,
            keep=['signname', 'name311', 'typecategory', 'acres', 'borough',
                  'address', 'zipcode', 'waterfront', 'class'])
    return S.cached('env-parks', 30 * S.DAY, fetch)


def evacuation_zones() -> dict:
    """
    Hurricane evacuation zones, 1 (evacuates first) through 6.

    The source also carries a zone ``X`` polygon meaning "no evacuation
    required" — it covers most of the city's land area and would render as a
    blanket over everything, so it is dropped.
    """
    def fetch():
        fc = S.simplify_geojson(S.socrata_geojson('epne-qv9x'), tol=0.00015,
                                keep=['hurricane_'])
        feats = []
        for f in fc['features']:
            raw = str(f['properties'].pop('hurricane_', '')).strip()
            if not raw.isdigit():
                continue
            f['properties'] = {'zone': int(raw),
                               'label': f'Zone {raw}',
                               'note': 'Zone 1 is evacuated first' if raw == '1' else ''}
            feats.append(f)
        feats.sort(key=lambda f: -f['properties']['zone'])   # draw 1 on top
        return {'type': 'FeatureCollection', 'features': feats}
    return S.cached('env-evac-zones', 30 * S.DAY, fetch)


def collisions() -> dict:
    """
    Recent crashes that injured or killed someone (Vision Zero reporting).

    Capped at 15k of the most recent qualifying crashes — enough for the
    heatmap to be representative while keeping the payload a couple of MB.
    """
    def fetch():
        rows = S.soql_all(
            S.NYC, 'h9gi-nx95', max_rows=15000,
            select=('crash_date,crash_time,borough,on_street_name,'
                    'number_of_persons_injured,number_of_persons_killed,'
                    'number_of_pedestrians_injured,number_of_cyclist_injured,'
                    'contributing_factor_vehicle_1,latitude,longitude'),
            where=('latitude IS NOT NULL and latitude > 40 and '
                   'crash_date > "2025-01-01T00:00:00.000" and '
                   '(number_of_persons_injured > 0 or number_of_persons_killed > 0)'),
            order='crash_date DESC')
        for r in rows:
            r['date'] = str(r.pop('crash_date', ''))[:10]
            r['time'] = r.pop('crash_time', '')
            r['injured'] = int(float(r.pop('number_of_persons_injured', 0) or 0))
            r['killed'] = int(float(r.pop('number_of_persons_killed', 0) or 0))
            r['peds'] = int(float(r.pop('number_of_pedestrians_injured', 0) or 0))
            r['cyclists'] = int(float(r.pop('number_of_cyclist_injured', 0) or 0))
            r['street'] = r.pop('on_street_name', '') or ''
            r['cause'] = r.pop('contributing_factor_vehicle_1', '') or ''
            r['borough'] = (r.get('borough') or '').title()
        return S.points_from_rows(
            rows, 'latitude', 'longitude',
            props=['date', 'borough', 'street', 'injured', 'killed',
                   'peds', 'cyclists', 'cause'])
    return S.cached('safety-collisions', 2 * S.DAY, fetch)


# Every income band the affordable-housing file reports, in the order HPD
# ranks them. The first five are the AMI ladder; `other` is the catch-all for
# units the programme counts but does not band.
INCOME_BANDS = [
    ('extremely_low_income_units', 'extremely_low', 'Extremely low (≤30% AMI)'),
    ('very_low_income_units', 'very_low', 'Very low (31–50% AMI)'),
    ('low_income_units', 'low', 'Low (51–80% AMI)'),
    ('moderate_income_units', 'moderate', 'Moderate (81–120% AMI)'),
    ('middle_income_units', 'middle', 'Middle (121–165% AMI)'),
    ('other_income_units', 'other', 'Other / not banded'),
]

# The bedroom mix, so a family can see whether a building has anything big
# enough for them rather than just a unit total.
BEDROOM_COLUMNS = [
    ('studio_units', 'studio'), ('_1_br_units', 'br1'), ('_2_br_units', 'br2'),
    ('_3_br_units', 'br3'), ('_4_br_units', 'br4'), ('_5_br_units', 'br5'),
    ('_6_br_units', 'br6'), ('unknown_br_units', 'br_unknown'),
]


def affordable_housing() -> dict:
    """
    Every affordable unit HPD has financed, by building.

    All six income bands are carried, not just the bottom three — a moderate-
    or middle-income unit is still an affordable unit somebody can apply for,
    and omitting those bands undercounted large buildings badly.

    About 1,900 buildings are published by HPD with the address redacted to
    ``CONFIDENTIAL`` and no coordinates (they are typically small preservation
    deals where naming the building would identify the tenants). They cannot be
    drawn, so rather than vanish they are counted in ``meta`` — the map says
    how many units it is *not* showing you.
    """
    def fetch():
        cols = [c for c, _, _ in INCOME_BANDS] + [c for c, _ in BEDROOM_COLUMNS]
        rows = S.soql_all(
            S.NYC, 'hg8x-zxpr', max_rows=20000,
            select=('project_name,house_number,street_name,borough,postcode,'
                    'project_start_date,building_completion_date,'
                    'reporting_construction_type,extended_affordability_status,'
                    'counted_rental_units,counted_homeownership_units,'
                    'all_counted_units,total_units,latitude,longitude,'
                    + ','.join(cols)),
            where='latitude IS NOT NULL')
        for r in rows:
            r['name'] = (r.pop('project_name', '') or '').title()
            r['address'] = f"{r.pop('house_number','')} {r.pop('street_name','')}".strip().title()
            r['started'] = str(r.pop('project_start_date', ''))[:10]
            r['completed'] = str(r.pop('building_completion_date', ''))[:10]
            r['construction'] = r.pop('reporting_construction_type', '')
            r['extended'] = r.pop('extended_affordability_status', '')
            r['zip'] = str(r.pop('postcode', '') or '').split('.')[0]
            for src, out in ([('all_counted_units', 'units'),
                              ('total_units', 'total'),
                              ('counted_rental_units', 'rental'),
                              ('counted_homeownership_units', 'ownership')]
                             + [(c, k) for c, k, _ in INCOME_BANDS]
                             + list(BEDROOM_COLUMNS)):
                r[out] = int(float(r.pop(src, 0) or 0))

        props = (['name', 'address', 'borough', 'zip', 'started', 'completed',
                  'construction', 'extended', 'units', 'total', 'rental',
                  'ownership']
                 + [k for _, k, _ in INCOME_BANDS]
                 + [k for _, k in BEDROOM_COLUMNS])
        fc = S.points_from_rows(rows, 'latitude', 'longitude', props=props)

        # One aggregate call, not a second download: we only need the tally of
        # what the redaction hides, not the redacted rows themselves.
        hidden = {'buildings': 0, 'units': 0}
        try:
            agg = S.soql(S.NYC, 'hg8x-zxpr',
                         select='count(1) as buildings, sum(all_counted_units) as units',
                         where='latitude IS NULL')
            if agg:
                hidden = {'buildings': int(float(agg[0].get('buildings') or 0)),
                          'units': int(float(agg[0].get('units') or 0))}
        except Exception:
            pass       # a failed tally must not cost us the whole layer

        mapped = sum(f['properties']['units'] for f in fc['features'])
        fc['meta'] = {
            'buildings': len(fc['features']),
            'units': mapped,
            'address_suppressed_buildings': hidden['buildings'],
            'address_suppressed_units': hidden['units'],
            'units_total': mapped + hidden['units'],
            'income_bands': [{'key': k, 'label': lbl} for _, k, lbl in INCOME_BANDS],
            'source': 'NYC HPD Affordable Housing Production by Building (hg8x-zxpr)',
            'note': ('HPD redacts the address of some buildings; those units '
                     'are counted here but cannot be placed on the map.'),
        }
        return fc
    return S.cached('civic-affordable-housing-v2', WEEK, fetch)


def affordable_rents() -> dict:
    """Published rents for the city's subsidised units (Local Law 44)."""
    return R.rent_points()


# ─────────────────────────────────────────────────────────────────────────────
# catalogue
# ─────────────────────────────────────────────────────────────────────────────

LAYERS: List[Dict[str, Any]] = [
    # ── Housing ──────────────────────────────────────────────────────────
    {
        'id': 'housing_prices',
        'title': 'Housing prices',
        'category': 'Housing',
        'kind': 'choropleth',
        'geometry': 'polygon',
        'default_on': True,
        'description': ('Recorded sale prices aggregated by area. Choose the metric, '
                        'geography, property type and time window.'),
        'controls': {
            'metric': list(P.METRICS.keys()),
            'geography': list(P.GEOGRAPHIES.keys()),
            'property_type': list(P.PROPERTY_TYPES.keys()),
            'window': True,
        },
        'metrics': P.METRICS,
        'geographies': P.GEOGRAPHIES,
        'property_types': P.PROPERTY_TYPES,
        'endpoint': '/layers/housing_prices',
        'source': _src('NYC DOF Citywide Rolling Sales', 'w2pb-icbu'),
    },
    {
        'id': 'sales',
        'title': 'Individual sales',
        'category': 'Housing',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': 'Every recorded sale as a point, coloured by price.',
        'style': {'color_by': 'price', 'radius': 3.5},
        'controls': {'property_type': list(P.PROPERTY_TYPES.keys()), 'window': True},
        'endpoint': '/layers/sales',
        'source': _src('NYC DOF Citywide Rolling Sales', 'w2pb-icbu'),
    },
    {
        'id': 'affordable_rents',
        'title': 'Affordable homes for rent',
        'category': 'Housing',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': ('What subsidised homes actually rent for, by building — '
                        'rent and income limit for every bedroom size.'),
        'style': {'color': '#c084fc', 'color_by': 'rent_min'},
        'endpoint': '/layers/affordable_rents',
        'source': _src('HPD Local Law 44 — Unit Income Rent', '9ay9-xkek'),
    },
    {
        'id': 'affordable_housing',
        'title': 'Affordable units built',
        'category': 'Housing',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': ('Every affordable unit HPD has financed, by building and '
                        'income band, sized by unit count.'),
        'style': {'color': '#4ade80', 'size_by': 'units'},
        'endpoint': '/layers/affordable_housing',
        'source': _src('HPD Affordable Housing Production by Building', 'hg8x-zxpr'),
    },

    # ── Transit ──────────────────────────────────────────────────────────
    {
        'id': 'subway_lines',
        'title': 'Subway lines',
        'category': 'Transit',
        'kind': 'line',
        'geometry': 'line',
        'default_on': True,
        'description': 'Every subway route, drawn in its official MTA colour.',
        'style': {'color_property': 'color', 'width': 2.2},
        'endpoint': '/layers/subway_lines',
        'source': {'name': 'MTA GTFS static feed (subway)', 'dataset': 'gtfs_subway',
                   'url': 'https://www.mta.info/developers', 'portal': 'mta.info'},
    },
    {
        'id': 'subway_stations',
        'title': 'Subway stations',
        'category': 'Transit',
        'kind': 'point',
        'geometry': 'point',
        'default_on': True,
        'description': '496 stations with the routes that serve them and ADA access.',
        'style': {'color': '#ffffff', 'stroke': '#111827', 'radius': 3.4},
        'endpoint': '/layers/subway_stations',
        'source': _src('MTA Subway Stations', '39hk-dx4f', 'data.ny.gov'),
    },
    {
        'id': 'subway_ridership',
        'title': 'Station ridership',
        'category': 'Transit',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': 'Station complexes scaled by total subway ridership this year.',
        'style': {'color': '#fbbf24', 'size_by': 'riders'},
        'endpoint': '/layers/subway_ridership',
        'source': _src('MTA Subway Hourly Ridership', 'ak4z-sape', 'data.ny.gov'),
    },
    {
        'id': 'bike_routes',
        'title': 'Bike network',
        'category': 'Transit',
        'kind': 'line',
        'geometry': 'line',
        'default_on': False,
        'description': 'Protected paths, striped lanes and signed routes citywide.',
        'style': {'color': '#22d3ee', 'width': 1.4},
        'endpoint': '/layers/bike_routes',
        'source': _src('New York City Bike Routes', 'mzxg-pwib'),
    },

    # ── Environment ──────────────────────────────────────────────────────
    {
        'id': 'parks',
        'title': 'Parks & open space',
        'category': 'Environment',
        'kind': 'polygon',
        'geometry': 'polygon',
        'default_on': False,
        'description': 'All NYC Parks properties, from pocket parks to Pelham Bay.',
        'style': {'color': '#34d399', 'opacity': 0.45},
        'endpoint': '/layers/parks',
        'source': _src('Parks Properties', 'enfh-gkve'),
    },
    {
        'id': 'evacuation_zones',
        'title': 'Hurricane evacuation zones',
        'category': 'Environment',
        'kind': 'polygon',
        'geometry': 'polygon',
        'default_on': False,
        'description': 'Zone 1 evacuates first; higher numbers are at lower risk.',
        'style': {'color_by': 'zone', 'opacity': 0.4},
        'endpoint': '/layers/evacuation_zones',
        'source': _src('Hurricane Evacuation Zones', 'epne-qv9x'),
    },

    # ── Traffic ──────────────────────────────────────────────────────────
    {
        'id': 'traffic_speeds',
        'title': 'Live traffic speeds',
        'category': 'Traffic',
        'kind': 'line',
        'geometry': 'line',
        'default_on': False,
        'live': True,
        # Refetched behind the scenes on this cadence, and the browser is told
        # to hold the response only this long — a live layer served with the
        # catalogue's hour-long cache would sit frozen on screen.
        'refresh_seconds': TR.SPEED_TTL,
        'description': ('How fast traffic is moving right now, from DOT sensors '
                        'on the highways and major arterials.'),
        'style': {'color_by': 'band', 'width': 2.6},
        'endpoint': '/layers/traffic_speeds',
        'source': _src('NYC DOT Real-Time Traffic Speeds', 'i4gi-tjb9'),
    },
    {
        'id': 'traffic_volume',
        'title': 'Traffic volume by hour',
        'category': 'Traffic',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': ('DOT count locations sized by daily traffic. Click one '
                        'for its 24-hour profile and the calmest hour to drive it.'),
        'style': {'color': '#22d3ee', 'size_by': 'daily'},
        'endpoint': '/layers/traffic_volume',
        'source': _src('NYC DOT Automated Traffic Volume Counts', '7ym2-wayt'),
    },

    # ── Safety ───────────────────────────────────────────────────────────
    {
        'id': 'collisions',
        'title': 'Traffic injuries',
        'category': 'Safety',
        'kind': 'heatmap',
        'geometry': 'point',
        'default_on': False,
        'description': 'Crashes since Jan 2025 that injured or killed someone.',
        'style': {'color': '#f87171', 'weight_by': 'injured'},
        'endpoint': '/layers/collisions',
        'source': _src('Motor Vehicle Collisions – Crashes', 'h9gi-nx95'),
    },

    # ── Boundaries ───────────────────────────────────────────────────────
    {
        'id': 'boroughs',
        'title': 'Borough boundaries',
        'category': 'Boundaries',
        'kind': 'outline',
        'geometry': 'polygon',
        'default_on': False,
        'description': 'The five boroughs.',
        'style': {'color': '#94a3b8', 'width': 1.5},
        'endpoint': '/layers/boroughs',
        'source': _src('Borough Boundaries', 'gthc-hcne'),
    },
    {
        'id': 'neighborhoods',
        'title': 'Neighborhood boundaries',
        'category': 'Boundaries',
        'kind': 'outline',
        'geometry': 'polygon',
        'default_on': False,
        'description': '2020 Neighborhood Tabulation Areas (262 neighborhoods).',
        'style': {'color': '#64748b', 'width': 0.8},
        'endpoint': '/layers/neighborhoods',
        'source': _src('2020 Neighborhood Tabulation Areas', '9nt8-h7nd'),
    },
]

# id → loader for every layer served straight from a source (housing_prices and
# sales are parameterised, so they're handled by the API rather than here).
LOADERS: Dict[str, Callable[[], dict]] = {
    'subway_lines': subway_lines,
    'subway_stations': subway_stations,
    'subway_ridership': subway_ridership,
    'bike_routes': bike_routes,
    'parks': parks,
    'evacuation_zones': evacuation_zones,
    'collisions': collisions,
    'traffic_speeds': TR.speeds,
    'traffic_volume': TR.volume,
    'affordable_housing': affordable_housing,
    'affordable_rents': affordable_rents,
    'boroughs': boroughs,
    'neighborhoods': neighborhoods,
    'zips': zips,
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
            {'name': 'NYC Open Data', 'url': 'https://opendata.cityofnewyork.us'},
            {'name': 'NY State Open Data / MTA', 'url': 'https://data.ny.gov'},
            {'name': 'OpenStreetMap contributors', 'url': 'https://www.openstreetmap.org/copyright'},
        ],
    }


def cache_control(layer_id: str, default: str) -> str:
    """
    The ``Cache-Control`` a layer's response should carry.

    Most layers move daily at most and are worth an hour in the browser. A
    layer that declares ``refresh_seconds`` is live, and gets a matching
    lifetime instead — otherwise the browser would keep showing an hour-old
    "live" reading no matter how often the server refreshed it.
    """
    for layer in LAYERS:
        if layer['id'] == layer_id and layer.get('refresh_seconds'):
            n = int(layer['refresh_seconds'])
            return f'public, max-age={n}, stale-while-revalidate={n * 4}'
    return default


def get(layer_id: str) -> dict:
    loader = LOADERS.get(layer_id)
    if not loader:
        raise KeyError(f'unknown layer {layer_id!r}; known: {sorted(LOADERS)}')
    return loader()
