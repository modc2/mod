"""
nyc.rents — what an affordable home actually costs to rent.

The sales side of this module (:mod:`nyc.prices`) is built on recorded deeds,
so it can only ever answer "what did this sell for". It says nothing about
rent, which is how two thirds of New York households actually pay for housing.

This module fills that in from the **Local Law 44** disclosures — the rent roll
the city publishes for every building it subsidises. Three tables are joined:

  * ``hu6m-9cfi``  LL44 Building        — 3.2k buildings, with coordinates
  * ``9ay9-xkek``  LL44 Unit Income Rent — 20k rows: rent by bedroom count and
                                          the income you may not exceed
  * ``ucdy-byxd``  LL44 Projects        — project names and programmes

The result is a map point per building carrying its whole rent table: for each
bedroom size and AMI band, what the units rent for and who qualifies.

Four quirks in the raw tables drive most of the code here:

1. ``medianactualrent`` is **0** for a unit that is vacant or unreported. Zero
   is not a rent. Left in, it drags every median toward nothing — the same trap
   ``prices.MIN_SALE_PRICE`` guards on the sales side. ``_rent`` maps it to None.
2. ``bedroomsize`` is free text with several spellings of the same thing —
   ``STUDIO`` and ``Studio``, ``4-BR`` and ``4BR``, plus ``Unknown``, ``NULL``
   and blanks. Grouping on the raw string splits one bedroom size into several.
3. ``maxallowableincome`` buckets AMI inconsistently: mostly decades
   (``41%-50%``) but sometimes fifths (``61%-65%``, ``66%-70%``). Only the
   band's upper bound is comparable across rows, so that is what we sort and
   group on.
4. Only about 8.5k of the 20k rent rows report a rent at all. The rest are
   unit counts with no price. We keep them for the unit tallies and report the
   coverage rather than implying the city published more than it did.
"""

from __future__ import annotations

import json
import statistics
from typing import Any, Dict, List, Optional

from . import sources as S

BUILDINGS = 'hu6m-9cfi'
UNIT_RENTS = '9ay9-xkek'
PROJECTS = 'ucdy-byxd'

WEEK = 7 * S.DAY

# A rent below this is a reporting artefact (a $1 super's unit, a placeholder),
# not a price anybody pays. Above it, we trust the city's own number.
MIN_RENT = 100
MAX_RENT = 20_000

# Bedroom sizes in the order a renter scans them, not alphabetical order.
BEDROOM_ORDER = ['Studio', '1-BR', '2-BR', '3-BR', '4-BR', '5-BR', '6-BR+',
                 'SRO', 'Unknown']

# AMI bands, keyed by the upper bound of the reported range. HPD's own naming;
# the cut-points are the ones used in every Housing Connect listing.
AMI_BANDS = [
    (30, 'Extremely low income', '0–30% AMI'),
    (50, 'Very low income', '31–50% AMI'),
    (80, 'Low income', '51–80% AMI'),
    (120, 'Moderate income', '81–120% AMI'),
    (165, 'Middle income', '121–165% AMI'),
]
OTHER_BAND = ('Other / not reported', 'Not reported')


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _rent(v: Any) -> Optional[int]:
    """A reported rent, or None. Zero means 'not reported', never 'free'."""
    f = _num(v)
    if f is None or not (MIN_RENT <= f <= MAX_RENT):
        return None
    return int(round(f))


def bedroom(raw: Any) -> str:
    """Normalise the free-text bedroom size onto :data:`BEDROOM_ORDER`."""
    s = str(raw or '').strip().upper().replace(' ', '')
    if not s or s in ('NULL', 'UNKNOWN', 'N/A'):
        return 'Unknown'
    if s == 'SRO':
        return 'SRO'
    if s.startswith('STUDIO') or s == '0-BR' or s == '0BR':
        return 'Studio'
    # "4-BR", "4BR", "4 BR", "6-BR+" all reduce to their leading digit.
    digits = ''.join(c for c in s if c.isdigit())
    if not digits:
        return 'Unknown'
    n = int(digits)
    if n <= 0:
        return 'Studio'
    return '6-BR+' if n >= 6 else f'{n}-BR'


def ami(raw: Any) -> Dict[str, Any]:
    """
    Parse ``"41%-50%"`` into a comparable band.

    Returns the reported range verbatim (renters recognise it), plus the upper
    bound — the only figure that sorts correctly across the source's mix of
    decade and half-decade buckets — and HPD's name for the band it falls in.
    """
    s = str(raw or '').strip()
    nums = []
    cur = ''
    for c in s:
        if c.isdigit():
            cur += c
        elif cur:
            nums.append(int(cur))
            cur = ''
    if cur:
        nums.append(int(cur))

    if not nums:
        return {'range': s or 'Not reported', 'max_ami': None,
                'band': OTHER_BAND[0], 'band_label': OTHER_BAND[1]}
    top = max(nums)
    for cap, name, label in AMI_BANDS:
        if top <= cap:
            return {'range': s, 'max_ami': top, 'band': name, 'band_label': label}
    return {'range': s, 'max_ami': top, 'band': 'Middle income',
            'band_label': 'Above 165% AMI'}


def _median(vals: List[int]) -> Optional[int]:
    vals = [v for v in vals if v is not None]
    return int(round(statistics.median(vals))) if vals else None


# ─────────────────────────────────────────────────────────────────────────────
# source tables
# ─────────────────────────────────────────────────────────────────────────────

def buildings(ttl: float = WEEK) -> Dict[str, dict]:
    """LL44 buildings keyed by ``buildingid``, with coordinates and address."""
    def fetch():
        rows = S.soql_all(S.NYC, BUILDINGS, max_rows=20000,
                          select=('projectid,buildingid,housenumber,streetname,'
                                  'boroid,postcode,nta,bbl,latitude,longitude,'
                                  'countedrentalunits,allcountedunits,'
                                  'totalbuildingunits,stories,'
                                  'reportingconstructiontype'))
        out: Dict[str, dict] = {}
        for r in rows:
            bid = str(r.get('buildingid') or '').strip()
            if not bid:
                continue
            addr = f"{r.get('housenumber','')} {r.get('streetname','')}".strip()
            out[bid] = {
                'building_id': bid,
                'project_id': str(r.get('projectid') or '').strip(),
                'address': addr.title(),
                'borough': (r.get('boroid') or '').strip(),
                'zip': str(r.get('postcode') or '').strip(),
                'nta': (r.get('nta') or '').strip(),
                'bbl': str(r.get('bbl') or '').strip(),
                'lat': _num(r.get('latitude')),
                'lng': _num(r.get('longitude')),
                'rental_units': int(_num(r.get('countedrentalunits')) or 0),
                'affordable_units': int(_num(r.get('allcountedunits')) or 0),
                'total_units': int(_num(r.get('totalbuildingunits')) or 0),
                'construction': (r.get('reportingconstructiontype') or '').strip(),
            }
        return out
    return S.cached('rents-ll44-buildings', ttl, fetch)


def projects(ttl: float = WEEK) -> Dict[str, dict]:
    """LL44 projects keyed by ``projectid`` — names the buildings inherit."""
    def fetch():
        rows = S.soql_all(S.NYC, PROJECTS, max_rows=5000,
                          select='projectid,projectname,programname,startdate')
        return {str(r.get('projectid') or '').strip(): {
            'name': (r.get('projectname') or '').strip().title(),
            'program': (r.get('programname') or '').strip(),
            'started': str(r.get('startdate') or '')[:10],
        } for r in rows if r.get('projectid')}
    return S.cached('rents-ll44-projects', ttl, fetch)


def rent_rows(ttl: float = WEEK) -> Dict[str, List[dict]]:
    """
    The rent roll grouped by ``buildingid``.

    Each row is one (bedroom size, AMI band) offering in that building: how
    many units, what they rent for now, and what the legal rent was set at.
    """
    def fetch():
        rows = S.soql_all(S.NYC, UNIT_RENTS, max_rows=40000,
                          select=('projectid,buildingid,bedroomsize,'
                                  'maxallowableincome,totalunits,'
                                  'medianactualrent,lowactualrent,highactualrent,'
                                  'medianinitiallegalrent,lowinitiallegalrent,'
                                  'highinitiallegalrent'))
        out: Dict[str, List[dict]] = {}
        for r in rows:
            bid = str(r.get('buildingid') or '').strip()
            if not bid:
                continue
            band = ami(r.get('maxallowableincome'))
            out.setdefault(bid, []).append({
                'bedrooms': bedroom(r.get('bedroomsize')),
                'units': int(_num(r.get('totalunits')) or 0),
                'ami_range': band['range'],
                'max_ami': band['max_ami'],
                'band': band['band'],
                'rent': _rent(r.get('medianactualrent')),
                'rent_low': _rent(r.get('lowactualrent')),
                'rent_high': _rent(r.get('highactualrent')),
                'legal_rent': _rent(r.get('medianinitiallegalrent')),
                'legal_low': _rent(r.get('lowinitiallegalrent')),
                'legal_high': _rent(r.get('highinitiallegalrent')),
            })
        # Cheapest first within a building — a renter reads the list top-down.
        order = {b: i for i, b in enumerate(BEDROOM_ORDER)}
        for rows_ in out.values():
            rows_.sort(key=lambda r: (order.get(r['bedrooms'], 99),
                                      r['max_ami'] if r['max_ami'] is not None else 999))
        return out
    return S.cached('rents-ll44-unit-rents', ttl, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# map layer
# ─────────────────────────────────────────────────────────────────────────────

def _asking(row: dict) -> Optional[int]:
    """What this offering costs a tenant: the actual rent, else the legal one."""
    return row['rent'] if row['rent'] is not None else row['legal_rent']


def rent_points(ttl: float = WEEK) -> dict:
    """
    Every LL44 building with a published rent, as map points.

    The whole per-building rent table rides along in ``rents`` as a JSON
    string. MapLibre flattens nested properties on the way through
    ``queryRenderedFeatures``, so encoding it explicitly is what lets the
    inspector show the table rather than ``[object Object]``.
    """
    def fetch():
        blds, projs, rents = buildings(), projects(), rent_rows()
        feats, no_geom, no_rent = [], 0, 0

        for bid, rows in rents.items():
            b = blds.get(bid)
            if not b:
                continue
            priced = [r for r in rows if _asking(r) is not None]
            if not priced:
                no_rent += 1
                continue
            if b['lat'] is None or b['lng'] is None:
                no_geom += 1
                continue

            asks = [_asking(r) for r in priced]
            proj = projs.get(b['project_id'], {})
            beds = [x for x in BEDROOM_ORDER
                    if any(r['bedrooms'] == x for r in priced)]
            feats.append({
                'type': 'Feature',
                'properties': {
                    'name': proj.get('name') or b['address'],
                    'address': b['address'],
                    'borough': b['borough'],
                    'zip': b['zip'],
                    'program': proj.get('program', ''),
                    'construction': b['construction'],
                    'affordable_units': b['affordable_units'],
                    'total_units': b['total_units'],
                    # Units the rent table actually prices, which is what the
                    # listed range describes — not the building's whole count.
                    'priced_units': sum(r['units'] for r in priced),
                    'rent_min': min(asks),
                    'rent_max': max(asks),
                    'rent_median': _median(asks),
                    'min_ami': min((r['max_ami'] for r in priced
                                    if r['max_ami'] is not None), default=None),
                    'bedrooms': ', '.join(beds),
                    'rents': json.dumps(priced, separators=(',', ':')),
                },
                'geometry': {'type': 'Point',
                             'coordinates': [round(b['lng'], 5), round(b['lat'], 5)]},
            })

        feats.sort(key=lambda f: f['properties']['rent_min'])
        return {
            'type': 'FeatureCollection',
            'features': feats,
            'meta': {
                'buildings': len(feats),
                'units_priced': sum(f['properties']['priced_units'] for f in feats),
                # Disclosed, not hidden: a building we cannot draw is still a
                # building somebody could rent in.
                'buildings_without_coordinates': no_geom,
                'buildings_without_published_rent': no_rent,
                'source': 'NYC HPD Local Law 44 (hu6m-9cfi + 9ay9-xkek)',
                'note': ('Rents are what the city published for subsidised '
                         'units — the actual median rent where reported, '
                         'otherwise the initial legal rent.'),
            },
        }
    return S.cached('rents-points', ttl, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# tables
# ─────────────────────────────────────────────────────────────────────────────

def summary(ttl: float = WEEK) -> Dict[str, Any]:
    """
    City-wide affordable rents: the median by bedroom size and by AMI band.

    This is the answer to "what does an affordable 2-bedroom actually cost",
    which no single building can give you.
    """
    def fetch():
        blds, rents = buildings(), rent_rows()
        by_bed: Dict[str, List[int]] = {}
        by_band: Dict[str, List[int]] = {}
        by_boro: Dict[str, List[int]] = {}
        units_by_bed: Dict[str, int] = {}
        total_units = priced_rows = 0

        for bid, rows in rents.items():
            boro = (blds.get(bid) or {}).get('borough', '') or 'Unknown'
            for r in rows:
                total_units += r['units']
                units_by_bed[r['bedrooms']] = units_by_bed.get(r['bedrooms'], 0) + r['units']
                ask = _asking(r)
                if ask is None:
                    continue
                priced_rows += 1
                by_bed.setdefault(r['bedrooms'], []).append(ask)
                by_band.setdefault(r['band'], []).append(ask)
                by_boro.setdefault(boro, []).append(ask)

        def table(d: Dict[str, List[int]], keys=None) -> List[dict]:
            items = keys or sorted(d, key=lambda k: -len(d[k]))
            return [{'key': k, 'offerings': len(d[k]),
                     'median_rent': _median(d[k]),
                     'min_rent': min(d[k]), 'max_rent': max(d[k])}
                    for k in items if d.get(k)]

        band_order = [n for _, n, _ in AMI_BANDS] + [OTHER_BAND[0]]
        for row in (bed := table(by_bed, BEDROOM_ORDER)):
            row['units'] = units_by_bed.get(row['key'], 0)

        return {
            'by_bedrooms': bed,
            'by_income_band': table(by_band, band_order),
            'by_borough': table(by_boro),
            'buildings': len({b for b in rents if b in blds}),
            'total_units': total_units,
            'offerings_with_rent': priced_rows,
            'offerings_total': sum(len(v) for v in rents.values()),
            'source': 'NYC HPD Local Law 44 (9ay9-xkek)',
        }
    return S.cached('rents-summary', ttl, fetch)


def listings(max_rent: Optional[int] = None, bedrooms: str = '',
             borough: str = '', search: str = '', limit: int = 100) -> Dict[str, Any]:
    """
    Affordable rentals as a flat, filterable list — one row per offering.

    This is the "find me somewhere I can afford" view: every priced unit in the
    city, narrowed by what you can pay and how many bedrooms you need.
    """
    fc = rent_points()
    want_bed = bedroom(bedrooms) if str(bedrooms).strip() else ''
    boro = str(borough or '').strip().lower()
    needle = str(search or '').strip().lower()
    cap = int(max_rent) if max_rent else None

    rows: List[dict] = []
    for f in fc['features']:
        p = f['properties']
        if boro and boro not in p['borough'].lower():
            continue
        if needle and needle not in f"{p['name']} {p['address']} {p['zip']}".lower():
            continue
        lng, lat = f['geometry']['coordinates']
        for r in json.loads(p['rents']):
            ask = _asking(r)
            if ask is None:
                continue
            if want_bed and r['bedrooms'] != want_bed:
                continue
            if cap and ask > cap:
                continue
            rows.append({
                'name': p['name'], 'address': p['address'],
                'borough': p['borough'], 'zip': p['zip'],
                'bedrooms': r['bedrooms'], 'units': r['units'],
                'rent': ask,
                'rent_is_legal_max': r['rent'] is None,
                'ami_range': r['ami_range'], 'band': r['band'],
                'lat': lat, 'lng': lng,
            })

    rows.sort(key=lambda r: r['rent'])
    n = max(1, int(limit))
    return {
        'matched': len(rows),
        'returned': min(len(rows), n),
        'filters': {'max_rent': cap, 'bedrooms': want_bed or None,
                    'borough': borough or None, 'search': search or None},
        'listings': rows[:n],
        'source': 'NYC HPD Local Law 44 (hu6m-9cfi + 9ay9-xkek)',
    }
