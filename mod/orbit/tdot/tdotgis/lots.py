"""
tdot.lots — where affordable housing could actually go.

The city's Real Estate Asset Inventory publishes every parcel of land the city
holds, *with its full lot polygon*. Most of it is parks, road allowances and
walkways — but buried in it are the two kinds of land affordable housing
actually gets built on in Toronto: vacant city land and city surface parking
(the Housing Now program is largely Green P lots), plus anything the city has
already declared surplus.

This module filters the inventory down to buildable candidates and scores each
lot 0–100 on the things that decide whether a site is worth pursuing:

    size      a bigger lot carries more homes and amortises the fixed costs
    transit   affordable housing policy (and funding) favours station-adjacent
              sites; distance to the nearest rapid-transit station
    use       surface parking is the easiest political and physical lift —
              flat, serviced, already paved; vacant land is next
    status    land the city has *declared surplus* is land it already wants
              to part with

Deliberately excluded, whatever their status: parks and recreation land
(building on parks is a different fight), and linear scraps — walkways, road
allowances, reserve strips, watercourses — which cannot carry a building.

The output is a polygon FeatureCollection: the **full lot boundary**, not a
point, so the map can highlight exactly the land being talked about. Each lot
carries a rough massing estimate (storeys, homes) sized to its area, which the
3-D view extrudes.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from . import sources as S

INVENTORY_PACKAGE = 'real-estate-asset-inventory'
LAND_RESOURCE = 'land-asset-inventory - 4326.geojson'

# A small infill apartment building needs roughly this much land.
MIN_AREA_M2 = 450.0

# Owners whose land the city (or its housing arm) can actually direct to
# housing. Third parties, the province, school boards and Metrolinx cannot be
# volunteered by a map.
CITY_OWNERS = {'City of Toronto', 'Toronto Community Housing Corporation'}

# The land uses a building can land on. Everything else qualifies only by
# being declared surplus — and then only if it isn't a linear scrap or a park.
BUILDABLE_TYPES = {'Vacant Land', 'Parking Facility'}
NEVER_TYPES = {'Park/Rec/Open Space', 'Walkway', 'Road Allowance',
               'Reserve Strip', 'Watercourse', 'Water Management',
               'Railway Land'}
SURPLUS_STATUSES = {'Declared Surplus', 'Declared Surplus on Market',
                    'Potentially Surplus'}
# Land already on its way out of city hands is not an opportunity.
LEAVING_STATUSES = {'Sell Abutting Owner', 'Sale In Progress',
                    'Sale (Lease >20 Years)', 'JT in Progress'}

# ~1° of longitude at Toronto's latitude, in km.
_KM_LNG = 111.0 * math.cos(math.radians(43.7))
_KM_LAT = 111.0


def _centroid(geom: dict) -> Optional[Tuple[float, float]]:
    """Bounding-box centre of a (Multi)Polygon — plenty for a distance score."""
    t, coords = geom.get('type'), geom.get('coordinates') or []
    polys = [coords] if t == 'Polygon' else coords if t == 'MultiPolygon' else []
    pts = [p for rings in polys for p in (rings[0] if rings else [])]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _station_index() -> List[Tuple[str, float, float]]:
    return [(f['properties']['name'], *f['geometry']['coordinates'][:2])
            for f in S.gtfs_bundle()['stations']['features']]


def _nearest_station(lng: float, lat: float,
                     stations: List[Tuple[str, float, float]]
                     ) -> Tuple[Optional[str], Optional[int]]:
    best, best_d2 = None, None
    for name, slng, slat in stations:
        d2 = ((lng - slng) * _KM_LNG) ** 2 + ((lat - slat) * _KM_LAT) ** 2
        if best_d2 is None or d2 < best_d2:
            best, best_d2 = name, d2
    if best is None:
        return None, None
    return best, int(math.sqrt(best_d2) * 1000)


def _massing(area_m2: float) -> Tuple[int, int, float]:
    """
    (storeys, homes, height_m) a lot of this size could plausibly carry.

    Missing-middle assumptions, not a pro forma: 45% lot coverage, 90 m² gross
    per home, storeys stepped by lot size (small infill → midrise → the scale
    Housing Now actually files at). The 3-D view extrudes ``height_m``.
    """
    storeys = 4 if area_m2 < 1500 else 8 if area_m2 < 4000 else 12
    homes = int(area_m2 * 0.45 * storeys / 90)
    return storeys, homes, round(storeys * 3.2, 1)


def score_lot(area_m2: float, station_m: Optional[int],
              use: str, status: str) -> int:
    """0–100. Documented weights: 35 size, 30 transit, 20 use, 15 status."""
    pts = 0.0
    # Size: log-scaled so 450 m² → ~0 and ~29,000 m² tops out.
    if area_m2 > MIN_AREA_M2:
        pts += min(35.0, 5.83 * math.log2(area_m2 / MIN_AREA_M2))
    # Transit: full marks within 400 m of a station, fading to zero at 2.4 km.
    if station_m is not None:
        pts += 30.0 * max(0.0, min(1.0, 1 - (station_m - 400) / 2000))
    # Use: paved and serviced beats overgrown beats everything else.
    pts += 20 if use == 'Parking Facility' else 15 if use == 'Vacant Land' else 5
    # Status: the city has already said it doesn't need this land.
    if status in ('Declared Surplus', 'Declared Surplus on Market'):
        pts += 15
    elif status == 'Potentially Surplus':
        pts += 10
    return min(100, round(pts))


def tier_of(score: int) -> str:
    return 'PRIME' if score >= 65 else 'STRONG' if score >= 45 else 'POSSIBLE'


def _why(use: str, status: str, area_m2: float, station: Optional[str],
         station_m: Optional[int]) -> str:
    bits = []
    what = {'Parking Facility': 'city surface parking',
            'Vacant Land': 'vacant city land'}.get(use, f'city {use.lower()}')
    bits.append(f'{area_m2:,.0f} m² of {what}')
    if station and station_m is not None and station_m <= 2400:
        bits.append(f'{station_m} m from {station}')
    if status in SURPLUS_STATUSES:
        bits.append(status.lower())
    return ', '.join(bits)


def candidates(refresh: bool = False) -> dict:
    """Every scored candidate lot, as full lot polygons. Cached two weeks."""
    if refresh:
        S.cache_clear('housing-lots')
    return S.cached('housing-lots', 14 * S.DAY, build)


def _merge_polys(geoms: List[dict]) -> Optional[dict]:
    """Fold parcel (Multi)Polygons into one MultiPolygon."""
    polys: List[list] = []
    for g in geoms:
        t, c = g.get('type'), g.get('coordinates') or []
        if t == 'Polygon':
            polys.append(c)
        elif t == 'MultiPolygon':
            polys.extend(c)
    return {'type': 'MultiPolygon', 'coordinates': polys} if polys else None


def build() -> dict:
    raw = S.ckan_geojson(INVENTORY_PACKAGE, LAND_RESOURCE)
    stations = _station_index()
    feats: List[dict] = []
    total = 0

    # The inventory files one row per *parcel*, with the property-level fields
    # (address, filed area) repeated on each — 777 Victoria Park Ave is six
    # rows. FLOC_ID is "<property>-P<parcel>", so parcels fold back into one
    # lot per property here.
    grouped: Dict[str, dict] = {}
    for f in raw.get('features', []):
        total += 1
        p = f.get('properties') or {}
        use = str(p.get('Property Type') or '').strip()
        status = str(p.get('Property Status') or '').strip()
        owner = str(p.get('Owner') or '').strip()

        if owner not in CITY_OWNERS or status in LEAVING_STATUSES:
            continue
        if use in NEVER_TYPES:
            continue
        if use not in BUILDABLE_TYPES and status not in SURPLUS_STATUSES:
            continue
        geom = f.get('geometry') or {}
        if not geom.get('coordinates'):
            continue

        floc = str(p.get('FLOC_ID') or '').split('-')[0] \
            or f"addr:{p.get('Address')}"
        slot = grouped.setdefault(floc, {'props': p, 'geoms': [],
                                         'filed_m2': 0.0, 'poly_m2': 0.0})
        slot['geoms'].append(geom)
        slot['poly_m2'] += S.geometry_km2(geom) * 1e6
        try:
            # Repeated per parcel, so the property total is the max, not a sum.
            slot['filed_m2'] = max(slot['filed_m2'],
                                   float(p.get('Area (M2)') or 0))
        except (TypeError, ValueError):
            pass

    for slot in grouped.values():
        p = slot['props']
        use = str(p.get('Property Type') or '').strip()
        status = str(p.get('Property Status') or '').strip()
        area_m2 = slot['filed_m2'] if slot['filed_m2'] > 0 else slot['poly_m2']
        if area_m2 < MIN_AREA_M2:
            continue

        merged = _merge_polys(slot['geoms'])
        if merged is None:
            continue
        centre = _centroid(merged)
        if centre is None:
            continue
        station, station_m = _nearest_station(*centre, stations)

        # A tight tolerance: these are individual lots, and the boundary being
        # highlighted has to be the boundary on file, not a caricature of it.
        g = S.simplify_geometry(merged, tol=0.000015, precision=6)
        if g is None:
            continue

        storeys, homes, height_m = _massing(area_m2)
        sc = score_lot(area_m2, station_m, use, status)
        feats.append({
            'type': 'Feature',
            'properties': {
                'name': str(p.get('Property Description') or '').strip().title()
                        or 'City land',
                'address': str(p.get('Address') or '').strip().title(),
                'use': use,
                'status': status,
                'owner': str(p.get('Owner') or '').strip(),
                'managed_by': str(p.get('Management') or '').strip(),
                'ward': str(p.get('Ward Name') or '').strip(),
                'area_m2': int(round(area_m2)),
                'score': sc,
                'tier': tier_of(sc),
                'est_storeys': storeys,
                'est_homes': homes,
                'massing_m': height_m,
                'station': station,
                'station_m': station_m,
                'why': _why(use, status, area_m2, station, station_m),
            },
            'geometry': g,
        })

    feats.sort(key=lambda f: -f['properties']['score'])
    tiers: Dict[str, int] = {}
    for f in feats:
        tiers[f['properties']['tier']] = tiers.get(f['properties']['tier'], 0) + 1
    return {
        'type': 'FeatureCollection',
        'features': feats,
        'meta': {
            'parcels_screened': total,
            'candidates': len(feats),
            'by_tier': tiers,
            'potential_homes': sum(f['properties']['est_homes'] for f in feats),
            'criteria': ('City-owned vacant land, surface parking, or land '
                         f'declared surplus; at least {MIN_AREA_M2:.0f} m²; '
                         'never parks or linear scraps. Scored on size, '
                         'rapid-transit distance, current use and surplus '
                         'status.'),
        },
    }


def shortlist(top: int = 12, min_score: int = 0,
              ward: Optional[str] = None) -> Dict[str, Any]:
    """The best lots as rows an agent can read — never raw geometry."""
    fc = candidates()
    rows = []
    for f in fc['features']:
        p = f['properties']
        if p['score'] < min_score:
            continue
        if ward and ward.lower() not in p['ward'].lower():
            continue
        c = _centroid(f['geometry'])
        rows.append({k: p[k] for k in
                     ('name', 'address', 'ward', 'use', 'status', 'area_m2',
                      'score', 'tier', 'est_homes', 'station', 'station_m',
                      'why')}
                    | ({'lng': round(c[0], 5), 'lat': round(c[1], 5)} if c else {}))
        if len(rows) >= top:
            break
    return {'lots': rows, 'total_candidates': len(fc['features']),
            'meta': fc.get('meta', {})}
