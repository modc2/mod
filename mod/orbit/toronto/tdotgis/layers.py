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

from . import census as N
from . import crime as C
from . import market as M
from . import registry as R
from . import sources as S

WEEK = 7 * S.DAY


def _src(name: str, package: str) -> dict:
    return {'name': name, 'dataset': package,
            'url': f'https://open.toronto.ca/dataset/{package}/',
            'portal': 'open.toronto.ca'}


def _score():
    """The score model, imported late — it needs the boundary loaders here."""
    from . import score
    return score


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
# real estate & housing — declared as specs, built by tdotgis.registry
#
# These are the same shape as anything a user adds from the browser: no loader
# function, just a description of where the data is and how to place it. If the
# spec format can carry the city's real-estate datasets it can carry the rest.
# ─────────────────────────────────────────────────────────────────────────────

# Planning-application codes as filed. Nobody outside the division reads "OZ".
APPLICATION_TYPES = {
    'OZ': 'Official Plan / Rezoning',
    'SA': 'Site plan approval',
    'CD': 'Condominium',
    'SB': 'Subdivision',
    'RH': 'Rental housing demolition',
    'MV': 'Minor variance',
    'CO': 'Consent / severance',
    'PLN': 'Planning study',
    'TLAB': 'Appeal (TLAB)',
}

# Where an application stands, as the city files it, folded onto what that
# *means*: live, cleared, contested, refused, done. A spec never names a colour
# — the map surface picks the hue (see app/src/lib/palette.ts), so the same
# spec reads correctly on the dark and light basemaps.
APPLICATION_STATUS = {
    'Under Review': 'live', 'Application Received': 'live',
    'Approved': 'good', 'Council Approved': 'good', 'OMB Approved': 'good',
    'Draft Plan Approved': 'good', 'Final Approval Completed': 'good',
    'NOAC Issued': 'good',
    'OMB Appeal': 'warn', 'Appeal Received': 'warn',
    'Refused': 'bad',
    'Closed': 'muted',
}

SPECS: List[Dict[str, Any]] = [
    # ── Real estate ──────────────────────────────────────────────────────
    {
        'id': 'development_applications',
        'title': 'Development applications',
        'category': 'Real estate',
        'description': ('Every rezoning, site-plan, condo and subdivision '
                        'application filed with City Planning, coloured by where '
                        'it stands. Click one for the file number and the city\'s '
                        'own application page.'),
        'package': 'development-applications',
        'resource': 'Development Applications',
        'source_name': 'Development Applications',
        # Filed on the city's MTM grid in metres, not lat/lng.
        'locate': {'mode': 'mtm', 'x': 'X', 'y': 'Y'},
        'props': {
            'address': {'from': ['STREET_NUM', 'STREET_NAME', 'STREET_TYPE',
                                 'STREET_DIRECTION'], 'as': 'title'},
            'type': {'from': 'APPLICATION_TYPE', 'as': 'lookup',
                     'table': APPLICATION_TYPES},
            'status': 'STATUS',
            'submitted': {'from': 'DATE_SUBMITTED', 'as': 'date'},
            'ward': 'WARD_NAME',
            'file': 'APPLICATION#',
            'description': {'from': 'DESCRIPTION', 'max': 200},
            'url': 'APPLICATION_URL',
        },
        'sort': '-DATE_SUBMITTED',
        'style': {'color_by': 'status', 'classes': APPLICATION_STATUS,
                  'radius': 3.2},
        'ttl_days': 1,
    },
    {
        'id': 'development_pipeline',
        'title': 'Development pipeline (units proposed)',
        'category': 'Real estate',
        'default_on': True,
        'description': ('Active applications with the residential units and floor '
                        'area they propose — the housing supply actually in the '
                        'queue. Circles are sized by units proposed.'),
        'package': 'development-pipeline',
        'resource': 'Development Pipeline',
        'source_name': 'Development Pipeline',
        # The pipeline table is address-only; its application numbers are the
        # same ones the applications dataset publishes coordinates for.
        'locate': {
            'mode': 'join',
            'key': 'Application Number',
            'to': {'package': 'development-applications',
                   'resource': 'Development Applications',
                   'key': 'APPLICATION#',
                   'locate': {'mode': 'mtm', 'x': 'X', 'y': 'Y'}},
        },
        'props': {
            'address': {'from': 'Address', 'as': 'title'},
            'status': 'Pipeline Status',
            'units': {'from': 'Proposed Residential Units', 'as': 'int'},
            'residential_m2': {'from': 'Proposed Residential Gross Floor Area',
                               'as': 'int'},
            'other_m2': {'from': 'Proposed Non-Residential Gross Floor Area',
                         'as': 'int'},
            'received': {'from': 'Date Received', 'as': 'date'},
            # The pipeline table numbers its wards; the applications table it
            # borrows coordinates from also names them.
            'ward': 'join.WARD_NAME',
            'file': 'Application Number',
            'description': {'from': 'Description', 'max': 200},
            'url': 'Application Information Centre Link',
        },
        'style': {'color_by': 'status',
                  'classes': {'Under Review': 'live', 'Active': 'warn',
                              'Built': 'good'},
                  'size_by': 'units', 'size_norm': 200},
        'ttl_days': 7,
    },
    {
        'id': 'affordable_housing',
        'title': 'Affordable housing pipeline',
        'category': 'Real estate',
        'description': ('Affordable and rent-geared-to-income homes approved, '
                        'under way or just completed, sized by homes approved.'),
        'package': 'upcoming-and-recently-completed-affordable-housing-units',
        'resource': 'Affordable Rental Housing Pipeline',
        'source_name': 'Affordable Rental Housing Pipeline',
        'locate': {'mode': 'geometry', 'column': 'geometry'},
        'geometry': 'point',
        'props': {
            'address': {'from': 'Anchor Address', 'as': 'title'},
            'project': 'Addresses',
            'status': 'Status',
            'affordable_homes': {'from': 'Affordable Homes Approved', 'as': 'int'},
            'rgi_homes': {'from': 'RGI Homes Approved', 'as': 'int'},
            'market_homes': {'from': 'Rent-Controlled Market Units Approved',
                             'as': 'int'},
            'ward': 'Ward Name',
        },
        'style': {'size_by': 'affordable_homes', 'size_norm': 120},
        'ttl_days': 14,
    },
    {
        'id': 'city_realty',
        'title': 'City-owned buildings',
        'category': 'Real estate',
        'description': ('The city\'s own real-estate portfolio — every building it '
                        'owns or manages, with floor area, use and year built.'),
        'package': 'real-estate-asset-inventory',
        'resource': 'building-asset-inventory',
        'source_name': 'Real Estate Asset Inventory',
        'locate': {'mode': 'geometry', 'column': 'geometry'},
        'geometry': 'point',
        'props': {
            'name': {'from': 'Building Description', 'as': 'title'},
            'address': {'from': 'Address', 'as': 'title'},
            'building_type': 'Building Type',
            'status': 'Building Status',
            'owner': 'Owner',
            'floor_area_m2': {'from': 'Gross Floor Area (M2)', 'as': 'int'},
            'year_built': {'from': 'Year Built', 'as': 'int'},
            'ward': 'Ward Name',
        },
        'style': {'size_by': 'floor_area_m2', 'size_norm': 5000},
        'ttl_days': 14,
    },
    {
        'id': 'building_permits',
        'title': 'Building permits (construction value)',
        'category': 'Real estate',
        'description': ('Every open building permit, at the property it was '
                        'issued for, sized by the estimated cost of the work. '
                        'The closest thing to a per-property money trail that '
                        'open data carries: what is being built, torn down, '
                        'split into units or renovated, by whom, and for how '
                        'much.'),
        'package': 'building-permits-active-permits',
        'resource': 'building-permits-active-permits',
        # The permit table's GEO_ID is an address-repository id, so each permit
        # lands on its own lot rather than somewhere in its ward.
        'locate': {'mode': 'address', 'key': 'GEO_ID'},
        'props': {
            'address': {'from': ['STREET_NUM', 'STREET_NAME', 'STREET_TYPE',
                                 'STREET_DIRECTION'], 'as': 'title'},
            'permit_type': 'PERMIT_TYPE',
            'structure': 'STRUCTURE_TYPE',
            'status': 'STATUS',
            'cost': {'from': 'EST_CONST_COST', 'as': 'int'},
            'units_created': {'from': 'DWELLING_UNITS_CREATED', 'as': 'int'},
            'units_lost': {'from': 'DWELLING_UNITS_LOST', 'as': 'int'},
            'applied': {'from': 'APPLICATION_DATE', 'as': 'date'},
            'issued': {'from': 'ISSUED_DATE', 'as': 'date'},
            'proposed_use': {'from': 'PROPOSED_USE', 'as': 'title'},
            'builder': {'from': 'BUILDER_NAME', 'as': 'title'},
            'description': {'from': 'DESCRIPTION', 'max': 180},
            'permit': 'PERMIT_NUM',
        },
        'sort': '-ISSUED_DATE',
        'limit': 25000,
        # A quarter-million-dollar job is the typical mark; a tower reads big.
        'style': {'size_by': 'cost', 'size_norm': 250000,
                  'color_by': 'status',
                  'classes': {'Permit Issued': 'live',
                              'Inspection': 'good',
                              'Ready for Issuance': 'warn',
                              'Application Received': 'muted',
                              'Not Started': 'muted'}},
        'ttl_days': 1,
    },
    {
        'id': 'short_term_rentals',
        'title': 'Short-term rentals per ward',
        'category': 'Real estate',
        'description': ('Registered short-term rental operators — the housing '
                        'stock let by the night. Addresses are withheld for '
                        'privacy, so registrations are counted per ward.'),
        'package': 'short-term-rentals-registration',
        'resource': 'short-term-rental-registrations-data',
        'source_name': 'Short-Term Rental Registrations',
        'locate': {'mode': 'area', 'column': 'ward_number', 'geography': 'ward',
                   'label': 'registrations'},
        'style': {'color_by': 'registrations'},
        'ttl_days': 7,
    },

    # ── Housing ──────────────────────────────────────────────────────────
    {
        'id': 'rental_buildings',
        'title': 'Registered rental buildings',
        'category': 'Housing',
        'description': ('Every apartment building registered with RentSafeTO '
                        '(3+ storeys, 10+ units) — size, age, who manages it and '
                        'what it offers. Circles are sized by number of units.'),
        'package': 'apartment-building-registration',
        'resource': 'Apartment Building Registration Data',
        'source_name': 'Apartment Building Registration',
        # The registration table carries no coordinates; the evaluation table
        # geocodes the same buildings and both are keyed by RSN.
        'locate': {
            'mode': 'join',
            'key': 'RSN',
            'to': {'package': 'apartment-building-evaluation',
                   'resource': 'Pre-2023 Apartment Building Evaluations',
                   'key': 'RSN',
                   'locate': {'mode': 'latlng', 'lat': 'LATITUDE',
                              'lng': 'LONGITUDE'}},
        },
        'props': {
            'address': {'from': 'SITE_ADDRESS', 'as': 'title'},
            'units': {'from': 'CONFIRMED_UNITS', 'as': 'int'},
            'storeys': {'from': 'CONFIRMED_STOREYS', 'as': 'int'},
            'year_built': {'from': 'YEAR_BUILT', 'as': 'int'},
            'property_type': {'from': 'PROPERTY_TYPE', 'as': 'title'},
            'managed_by': {'from': 'PROP_MANAGEMENT_COMPANY_NAME', 'as': 'title'},
            'heating': {'from': 'HEATING_TYPE', 'as': 'title'},
            'air_conditioning': {'from': 'AIR_CONDITIONING_TYPE', 'as': 'title'},
            'pets_allowed': 'PETS_ALLOWED',
            'barrier_free_entry': 'BARRIER_FREE_ACCESSIBILTY_ENTR',
            'bike_parking': 'BIKE_PARKING',
            'ward': 'WARD',
        },
        # Size carries how much housing a building is; age and condition are in
        # the inspector. One data channel per layer keeps the map readable when
        # three of them are switched on at once.
        'style': {'size_by': 'units', 'size_norm': 100},
        'ttl_days': 30,
    },
    {
        'id': 'multi_tenant_houses',
        'title': 'Rooming houses per ward',
        'category': 'Housing',
        'description': ('Licensed multi-tenant ("rooming") houses — the cheapest '
                        'legal rental floor in the city, counted per ward.'),
        'package': 'multi-tenant-house-licences',
        'resource': 'Multi Tenant House Licences',
        'source_name': 'Multi-Tenant House Licences',
        'filter': {'Status': ['Active']},
        'locate': {'mode': 'area', 'column': 'Ward', 'geography': 'ward',
                   'label': 'licences'},
        'style': {'color_by': 'licences'},
        'ttl_days': 7,
    },
    {
        'id': 'community_housing',
        'title': 'Toronto Community Housing',
        'category': 'Housing',
        'description': ('Every TCHC building, sized by units and coloured by how '
                        'much of it is rent-geared-to-income. The city is its own '
                        'largest landlord and this is the portfolio.'),
        'package': 'toronto-community-housing-data',
        'resource': 'Community Housing Data',
        'source_name': 'Toronto Community Housing Data',
        'locate': {'mode': 'geometry', 'column': 'geometry'},
        'geometry': 'point',
        'props': {
            'development': {'from': 'DEV_NAME', 'as': 'title'},
            'description': {'from': 'BLD_DESC', 'max': 160},
            'units': {'from': 'TTL_RES_UNIT', 'as': 'int'},
            'rgi_units': {'from': 'RGI_UNITS', 'as': 'int'},
            'market_units': {'from': 'MRKT_UNIT', 'as': 'int'},
            'year_built': {'from': 'YR_BUILT', 'as': 'int'},
            'storeys': {'from': 'FLR_ABV_GR', 'as': 'int'},
            'building_type': {'from': 'BLD_TYPO', 'as': 'title'},
            'form': {'from': 'BLD_FORM', 'as': 'title'},
            'postal_code': 'PSTL_CODE',
        },
        'style': {'size_by': 'units', 'size_norm': 150},
        'ttl_days': 30,
    },
    {
        'id': 'subsidized_housing',
        'title': 'Subsidized housing',
        'category': 'Housing',
        'description': ('Buildings on the centralized waiting list, with their '
                        'unit mix, provider and accessibility. Where an '
                        'affordable home is actually applied for.'),
        'package': 'subsidized-housing-listings',
        'resource': 'Subsidized Housing',
        'source_name': 'Subsidized Housing Listings',
        'locate': {'mode': 'latlng', 'lat': 'Latitude', 'lng': 'Longitude'},
        'props': {
            'name': {'from': 'Building Complex Name', 'as': 'title'},
            'address': {'from': 'Building Address', 'as': 'title'},
            'provider': {'from': 'Provider Name', 'as': 'title'},
            'provider_type': 'Provider Type',
            'building_type': 'Building Type',
            'seniors': 'Senior Building',
            'bachelor': {'from': 'Bachelor', 'as': 'int'},
            'one_bedroom': {'from': 'One Bedroom', 'as': 'int'},
            'two_bedroom': {'from': 'Two Bedroom', 'as': 'int'},
            'three_bedroom': {'from': 'Three Bedroom', 'as': 'int'},
            'elevators': 'Elevators',
            'air_conditioning': 'Air Conditioning',
            'ward': 'Ward',
            'website': 'Provider Website',
        },
        'style': {'color_by': 'provider_type', 'radius': 3.6},
        'ttl_days': 14,
    },
    {
        'id': 'health_hazards',
        'title': 'Highrise health hazards',
        'category': 'Housing',
        'description': ('Health-hazard investigations in highrise residential '
                        'buildings — pests, mould, indoor sanitation — coloured '
                        'by where the case stands.'),
        'package': 'residential-health-hazards',
        'resource': 'Residential Health Inspections',
        'source_name': 'Highrise Residential Health Hazards',
        'locate': {'mode': 'latlng', 'lat': 'lat', 'lng': 'lon'},
        'props': {
            'address': {'from': 'address', 'as': 'title'},
            'hazard': {'from': 'hazard_type', 'as': 'title'},
            'violation': 'violation',
            'investigation': 'investigation_type',
            'status': 'status_desc',
            'opened': {'from': 'investigation_date', 'as': 'date'},
            'updated': {'from': 'last_updated_date', 'as': 'date'},
            'case': 'case_id',
        },
        'sort': '-investigation_date',
        'style': {'color_by': 'status',
                  'classes': {'Under Investigation': 'live',
                              'Closed': 'muted', 'Resolved': 'good',
                              'Referred': 'warn'},
                  'radius': 3.2},
        'ttl_days': 7,
    },
    {
        'id': 'fire_violations',
        'title': 'Fire-code violations',
        'category': 'Housing',
        'description': ('Violations found during residential fire inspections, '
                        'with the code section and whether the city took it to '
                        'enforcement. The most recent 25,000.'),
        'package': 'residential-fire-inspection-results',
        'resource': 'inspection-results',
        'source_name': 'Residential Fire Inspection Results',
        'locate': {'mode': 'geometry', 'column': 'geometry'},
        'geometry': 'point',
        'props': {
            'address': {'from': 'PropertyAddress', 'as': 'title'},
            'property_type': 'PropertyType',
            'violation': {'from': 'VIOLATION_DESCRIPTION', 'max': 160},
            'fire_code': 'VIOLATION_FIRE_CODE',
            'enforcement': 'Enforcement_Proceedings',
            'opened': {'from': 'INSPECTIONS_OPENDATE', 'as': 'date'},
            'closed': {'from': 'INSPECTIONS_CLOSEDDATE', 'as': 'date'},
            'ward': 'propertyWard',
        },
        'sort': '-INSPECTIONS_OPENDATE',
        'limit': 25000,
        'style': {'color_by': 'enforcement',
                  'classes': {'Yes': 'bad', 'No': 'muted'},
                  'radius': 2.6},
        'ttl_days': 7,
    },
    {
        'id': 'rental_demolitions',
        'title': 'Rental homes demolished per ward',
        'category': 'Housing',
        'description': ('Rental homes the city approved for demolition. Under '
                        'the rental-replacement rules most are rebuilt, but the '
                        'tenants are moved and the rent resets.'),
        'package': 'demolition-and-replacement-of-rental-housing-units',
        'resource': 'Rental Demolition and Replacement',
        'source_name': 'Demolition and Replacement of Rental Housing Units',
        'locate': {'mode': 'area', 'column': '(Post 2018) Ward',
                   'geography': 'ward', 'label': 'homes_demolished',
                   'weight': 'Total Rental Homes for Demolition'},
        'style': {'color_by': 'homes_demolished'},
        'ttl_days': 14,
    },
    {
        'id': 'retirement_homes',
        'title': 'Retirement homes',
        'category': 'Housing',
        'description': 'Licensed retirement homes, sized by resident capacity.',
        'package': 'retirement-homes',
        'resource': 'Retirement Homes',
        'source_name': 'Retirement Homes',
        # The table has LATITUDE/LONGITUDE columns and they are all null; the
        # geometry column is the one that is actually populated.
        'locate': {'mode': 'geometry', 'column': 'geometry'},
        'geometry': 'point',
        'props': {
            'name': {'from': 'NAME', 'as': 'title'},
            'address': {'from': 'ADDRESS_FULL', 'as': 'title'},
            'capacity': {'from': 'CAPACITY', 'as': 'int'},
            'municipality': {'from': 'MUNICIPALITY', 'as': 'title'},
            'postal_code': 'POSTAL_CODE',
        },
        'style': {'size_by': 'capacity', 'size_norm': 200},
        'ttl_days': 30,
    },
]

SPECS_BY_ID: Dict[str, dict] = {s['id']: s for s in SPECS}


def specs() -> Dict[str, dict]:
    """Builtin specs plus anything the user added, keyed by id (user wins)."""
    out = dict(SPECS_BY_ID)
    for s in R.saved():
        if s.get('id'):
            out[s['id']] = s
    return out


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
        'id': 'prices',
        'title': 'What homes cost',
        'category': 'Housing',
        'kind': 'area',
        'geometry': 'polygon',
        'default_on': False,
        'description': ('Dwelling values, rents, the price-to-income multiple '
                        'and who is priced out — per neighbourhood, from the '
                        '2021 Census. Pick the metric in the panel above.'),
        'controls': {'metric': list(N.METRICS.keys())},
        'metrics': N.METRICS_PUBLIC,
        'style': {'color_by': N.DEFAULT_METRIC},
        'endpoint': '/layers/prices',
        'source': _src('Neighbourhood Profiles (2021 Census)', 'neighbourhood-profiles'),
    },
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
    {
        'id': 'housing_lots',
        'title': 'Lots for affordable housing',
        'category': 'Housing',
        'kind': 'polygon',
        'geometry': 'polygon',
        'default_on': False,
        'description': ('City-owned land affordable housing could actually be '
                        'built on — vacant lots, surface parking and land '
                        'declared surplus, at least 450 m², never parks. Each '
                        'full lot boundary is highlighted and scored on size, '
                        'transit and how ready the city is to part with it. '
                        'In 3-D the lots rise to the scale of housing they '
                        'could carry.'),
        # `highlight` asks the map for the full-lot glow treatment;
        # `extrude_by` is the massing height the 3-D view uses.
        'style': {'color_by': 'tier',
                  'classes': {'PRIME': 'good', 'STRONG': 'live',
                              'POSSIBLE': 'muted'},
                  'highlight': True, 'extrude_by': 'massing_m'},
        'endpoint': '/layers/housing_lots',
        'source': _src('Real Estate Asset Inventory (land)',
                       'real-estate-asset-inventory'),
    },
    {
        'id': 'predicted_scores',
        'title': 'Predicted vs actual score',
        'category': 'Housing',
        'kind': 'point',
        'geometry': 'point',
        'default_on': False,
        'description': ('What every rental building\'s inspection score should '
                        'be, given its age, size, systems, management and '
                        'neighbourhood — against what it actually scored. Red '
                        'is scoring worse than comparable buildings.'),
        # `down_bad`: a negative residual is the building scoring below its
        # peers, and that is the end the warning colour belongs on.
        'style': {'color_by': 'residual', 'diverging': 'down_bad',
                  'size_by': 'units', 'size_norm': 100},
        'endpoint': '/layers/predicted_scores',
        'model': '/score/model',
        'source': _src('Apartment Building Evaluation + Registration',
                       'apartment-building-evaluation'),
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
def _lots():
    """Late import like the score model — nothing else needs it at boot."""
    from . import lots
    return lots.candidates()


LOADERS: Dict[str, Callable[[], dict]] = {
    'prices': N.choropleth,
    'predicted_scores': lambda: _score().predictions(),
    'housing_lots': _lots,
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


# Categories keep a stable order in the panel no matter what gets added; an
# unlisted category (anything a user adds) sorts to the end alphabetically.
CATEGORY_ORDER = ['Crime', 'Real estate', 'Housing', 'Transit', 'Environment',
                  'Safety', 'Boundaries']


def all_layers() -> List[Dict[str, Any]]:
    """Hand-written layers, every spec-driven one, and the market's."""
    builtin_ids = set(SPECS_BY_ID)
    out = list(LAYERS)
    for spec in specs().values():
        out.append(R.layer_def(spec, custom=spec['id'] not in builtin_ids))
    out.extend(M.layer_defs())
    order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    out.sort(key=lambda l: (order.get(l['category'], len(order)), l['category']))
    return out


def catalog() -> Dict[str, Any]:
    """The full layer catalogue, grouped by category — this drives the UI."""
    layers = all_layers()
    cats: Dict[str, List[dict]] = {}
    for layer in layers:
        cats.setdefault(layer['category'], []).append(layer)
    return {
        'layers': layers,
        'categories': [{'name': c, 'layers': [l['id'] for l in ls]}
                       for c, ls in cats.items()],
        'count': len(layers),
        'attribution': [
            {'name': 'Toronto Open Data', 'url': 'https://open.toronto.ca'},
            {'name': 'Toronto Police Service open data', 'url': 'https://data.torontopolice.on.ca'},
            {'name': 'TTC', 'url': 'https://www.ttc.ca'},
            {'name': 'OpenStreetMap contributors', 'url': 'https://www.openstreetmap.org/copyright'},
        ],
    }


def get(layer_id: str, refresh: bool = False) -> dict:
    loader = LOADERS.get(layer_id)
    if loader:
        return loader()
    spec = specs().get(layer_id)
    if spec:
        return R.build(spec, refresh=refresh)
    known = sorted(set(LOADERS) | set(specs()))
    raise KeyError(f'unknown layer {layer_id!r}; known: {known}')
