"""
tdot.crime — the choropleth engine, fed by Toronto Police incident records.

Source: Toronto Police Service **Major Crime Indicators** (published on the
city's portal as "Community Safety Indicators") — every reported assault,
auto theft, break-and-enter, robbery and theft-over incident since 2014,
~490k rows, each carrying the occurrence date, offence, premises type,
neighbourhood (both the current 158-area and the historical 140-area model)
and an offset lat/lng. This is the police service's own reporting record —
the closest thing Toronto has to NYC's deed file: a large, dated, per-event
table that aggregates meaningfully by neighbourhood.

Toronto's CKAN datastore has no server-side GROUP BY (``datastore_search_sql``
is blocked portal-wide), so the engine works differently from a Socrata one:

1. ``warehouse()`` pulls the table **once** through the column-filtered CSV
   dump (~25 MB) and reduces it to a *month cube* — counts keyed by
   (neighbourhood, category, month) — plus a slice of recent incidents kept
   as points. The cube is ~2 MB and answers every choropleth/trend query
   locally in milliseconds; only the weekly refresh touches the network.
2. Locations are the police service's own offset geocodes (moved to the
   nearest intersection for privacy); a handful of rows carry no
   neighbourhood ("NSA") and are excluded from area aggregates.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import sources as S

RESOURCE = '11581817-b148-4dd4-99c4-679649515ccc'   # Community Safety Indicators
PACKAGE = 'major-crime-indicators'
SOURCE_URL = 'https://open.toronto.ca/dataset/major-crime-indicators/'

FIELDS = ['OCC_DATE', 'CSI_CATEGORY', 'OFFENCE', 'PREMISES_TYPE',
          'HOOD_158', 'NEIGHBOURHOOD_158', 'HOOD_140',
          'LONG_WGS84', 'LAT_WGS84']

# The record starts in 2014; occurrences reported later but dated earlier are
# folded out rather than stretching the axis back decades.
FIRST_MONTH = '2014-01'
FIRST_YEAR = 2014

# Below this many incidents in the *prior* window, a percent change is noise
# and is reported as null rather than a coloured swing.
MIN_CHANGE_SAMPLE = 10

# How far back the individual-incident point slice reaches.
POINT_MONTHS = 30

CATEGORIES: Dict[str, Dict[str, Any]] = {
    'all': {'label': 'All major crime', 'match': None},
    'assault': {'label': 'Assault', 'match': 'Assault'},
    'auto_theft': {'label': 'Auto theft', 'match': 'Auto Theft'},
    'break_enter': {'label': 'Break & enter', 'match': 'Break and Enter'},
    'robbery': {'label': 'Robbery', 'match': 'Robbery'},
    'theft_over': {'label': 'Theft over $5,000', 'match': 'Theft Over'},
}
_CAT_KEYS = [k for k in CATEGORIES if k != 'all']
_MATCH_TO_KEY = {v['match']: k for k, v in CATEGORIES.items() if v['match']}

# Geographies you can aggregate by. `cube` picks the warehouse cube;
# `join` is the matching property on the boundary layer's features.
GEOGRAPHIES: Dict[str, Dict[str, Any]] = {
    'neighbourhood': {'label': 'Neighbourhood (158)', 'cube': 'cube158',
                      'boundary': 'neighbourhood', 'join': 'AREA_LONG_CODE',
                      'name': 'AREA_NAME'},
    'neighbourhood140': {'label': 'Neighbourhood (old 140)', 'cube': 'cube140',
                         'boundary': 'neighbourhood140', 'join': 'AREA_LONG_CODE',
                         'name': 'AREA_NAME'},
}

METRICS: Dict[str, Dict[str, Any]] = {
    'incidents': {'label': 'Incidents', 'unit': 'incidents', 'format': 'count'},
    'per_km2': {'label': 'Incidents per km²', 'unit': '/km²', 'format': 'rate'},
    'per_month': {'label': 'Incidents per month', 'unit': '/month', 'format': 'rate'},
    'change': {'label': 'Change vs. prior period', 'unit': '%', 'format': 'percent'},
}


# ─────────────────────────────────────────────────────────────────────────────
# month arithmetic (the cube is month-grained)
# ─────────────────────────────────────────────────────────────────────────────

def _ym(d: str) -> str:
    return str(d)[:7]


def _month_index(ym: str) -> int:
    y, m = int(ym[:4]), int(ym[5:7])
    return y * 12 + (m - 1)


def _months_between(since: str, until: Optional[str]) -> int:
    """Inclusive month count of [since, until] at month grain, minimum 1."""
    end = _ym(until) if until else _ym(date.today().isoformat())
    return max(_month_index(end) - _month_index(_ym(since)) + 1, 1)


def _prior_window(since: str, until: Optional[str]) -> Tuple[str, str]:
    """The equally-long month window immediately before [since, until]."""
    span = _months_between(since, until)
    start = _month_index(_ym(since))
    lo, hi = start - span, start - 1
    fmt = lambda i: f'{i // 12:04d}-{i % 12 + 1:02d}'
    return f'{fmt(lo)}-01', f'{fmt(hi)}-28'


# ─────────────────────────────────────────────────────────────────────────────
# warehouse: one download → month cube + recent points
# ─────────────────────────────────────────────────────────────────────────────

def _clean_hood_name(name: str) -> str:
    """``"West Hill (136)"`` → ``"West Hill"``."""
    return re.sub(r'\s*\(\d+\)\s*$', '', name or '').strip()


def warehouse(ttl: float = 7 * S.DAY) -> Dict[str, Any]:
    def build():
        cube158: Dict[str, Dict[str, Dict[str, int]]] = {}
        cube140: Dict[str, Dict[str, Dict[str, int]]] = {}
        points: List[list] = []
        total = 0
        max_ym = FIRST_MONTH

        cutoff = (date.today().replace(day=1)
                  - timedelta(days=31 * POINT_MONTHS)).isoformat()

        for row in S.ckan_dump_rows(RESOURCE, FIELDS):
            d = (row.get('OCC_DATE') or '')[:10]
            ym = d[:7]
            cat = _MATCH_TO_KEY.get((row.get('CSI_CATEGORY') or '').strip())
            if len(ym) != 7 or ym < FIRST_MONTH or not cat:
                continue
            total += 1
            if ym > max_ym:
                max_ym = ym
            for cube, hood in ((cube158, row.get('HOOD_158')),
                               (cube140, row.get('HOOD_140'))):
                h = (hood or '').strip()
                if not h.isdigit():          # 'NSA' — no neighbourhood assigned
                    continue
                by_cat = cube.setdefault(h.zfill(3), {})
                months = by_cat.setdefault(cat, {})
                months[ym] = months.get(ym, 0) + 1
            if d >= cutoff:
                try:
                    lng, lat = float(row['LONG_WGS84']), float(row['LAT_WGS84'])
                except (KeyError, TypeError, ValueError):
                    continue
                if lat == 0 and lng == 0:
                    continue
                points.append([
                    d, cat,
                    (row.get('OFFENCE') or '').strip(),
                    (row.get('PREMISES_TYPE') or '').strip(),
                    _clean_hood_name(row.get('NEIGHBOURHOOD_158') or ''),
                    round(lng, 5), round(lat, 5),
                ])
        if not total:
            raise ValueError('CSI dump returned no usable rows')
        points.sort(key=lambda p: p[0], reverse=True)
        return {'cube158': cube158, 'cube140': cube140, 'points': points,
                'total': total, 'first_month': FIRST_MONTH, 'last_month': max_ym}

    return S.cached('csi-warehouse', ttl, build)


def coverage() -> Dict[str, Any]:
    w = warehouse()
    return {'total': w['total'], 'first_month': w['first_month'],
            'last_month': w['last_month'], 'points': len(w['points'])}


# ─────────────────────────────────────────────────────────────────────────────
# aggregation
# ─────────────────────────────────────────────────────────────────────────────

def aggregate(geography: str = 'neighbourhood',
              since: str = '2025-01-01', until: Optional[str] = None,
              category: str = 'all') -> Dict[str, Dict[str, Any]]:
    """
    Incident counts per area over a window, as
    ``{area: {incidents, assault, auto_theft, …}}``.

    ``incidents`` respects the category filter; the per-category counts are
    always the full breakdown, so the inspector can show the mix regardless
    of what the map is filtered to.
    """
    geo = GEOGRAPHIES.get(geography) or GEOGRAPHIES['neighbourhood']
    cube = warehouse()[geo['cube']]
    lo, hi = _ym(since), _ym(until) if until else '9999-12'
    wanted = None if category in ('all', '', None) else category

    out: Dict[str, Dict[str, Any]] = {}
    for hood, by_cat in cube.items():
        row = {'incidents': 0}
        for cat in _CAT_KEYS:
            n = sum(v for ym, v in by_cat.get(cat, {}).items() if lo <= ym <= hi)
            row[cat] = n
            if wanted is None or cat == wanted:
                row['incidents'] += n
        out[hood] = row
    return out


def with_change(geography: str = 'neighbourhood', since: str = '2025-01-01',
                until: Optional[str] = None,
                category: str = 'all') -> Dict[str, Dict[str, Any]]:
    """``aggregate`` plus ``change`` — % change vs the equally-long prior window."""
    current = aggregate(geography, since, until, category)
    p_since, p_until = _prior_window(since, until)
    prior = aggregate(geography, p_since, p_until, category)

    for area, stats in current.items():
        old = prior.get(area, {}).get('incidents', 0)
        new = stats['incidents']
        stats['prior_incidents'] = old
        stats['change'] = (round((new - old) / old * 100, 1)
                           if old >= MIN_CHANGE_SAMPLE else None)
    return current


# ─────────────────────────────────────────────────────────────────────────────
# choropleth
# ─────────────────────────────────────────────────────────────────────────────

def choropleth(boundaries: dict, geography: str = 'neighbourhood',
               since: str = '2025-01-01', until: Optional[str] = None,
               category: str = 'all') -> dict:
    """
    Join incident stats onto a boundary FeatureCollection.

    Areas with no recorded incidents keep their geometry and carry
    ``incidents: 0`` — on a crime map, zero is a real (and good) value, not
    missing data. Only the rate metrics go null when the area's geometry
    reports no size.
    """
    geo = GEOGRAPHIES.get(geography) or GEOGRAPHIES['neighbourhood']
    stats = with_change(geography, since, until, category)
    months = _months_between(since, until)
    join_key, name_key = geo['join'], geo['name']

    feats, matched = [], 0
    for f in boundaries.get('features', []):
        props = dict(f.get('properties') or {})
        code = str(props.get(join_key, '')).strip()
        code = code.zfill(3) if code.isdigit() else code
        s = stats.get(code)
        if s and s['incidents'] > 0:
            matched += 1
        km2 = S.geometry_km2(f.get('geometry'))
        n = (s or {}).get('incidents', 0)
        out = {
            'area': code,
            'name': _clean_hood_name(str(props.get(name_key) or code)),
            'km2': round(km2, 2),
            'incidents': n,
            'per_km2': round(n / km2, 1) if km2 > 0.01 else None,
            'per_month': round(n / months, 1),
            'change': (s or {}).get('change'),
            'prior_incidents': (s or {}).get('prior_incidents', 0),
        }
        for cat in _CAT_KEYS:
            out[cat] = (s or {}).get(cat, 0)
        feats.append({'type': 'Feature', 'properties': out, 'geometry': f['geometry']})

    return {
        'type': 'FeatureCollection',
        'features': feats,
        'meta': {
            'geography': geography, 'since': since, 'until': until,
            'category': category, 'months': months,
            'areas': len(feats), 'areas_with_data': matched,
            'source': 'TPS Major Crime Indicators (Toronto Open Data)',
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# detail views
# ─────────────────────────────────────────────────────────────────────────────

def trend(area: Optional[str] = None, geography: str = 'neighbourhood',
          category: str = 'all') -> Dict[str, Any]:
    """Yearly incident counts — city-wide, or for one area."""
    geo = GEOGRAPHIES.get(geography) or GEOGRAPHIES['neighbourhood']
    w = warehouse()
    cube = w[geo['cube']]
    wanted = None if category in ('all', '', None) else category
    last_year = int(w['last_month'][:4])

    hoods = [str(area).zfill(3)] if area else list(cube)
    by_year: Dict[int, Dict[str, int]] = {}
    for hood in hoods:
        for cat, months in (cube.get(hood) or {}).items():
            for ym, n in months.items():
                y = int(ym[:4])
                acc = by_year.setdefault(y, {'incidents': 0, **{c: 0 for c in _CAT_KEYS}})
                acc[cat] += n
                if wanted is None or cat == wanted:
                    acc['incidents'] += n

    series = [{'year': y, **by_year[y]} for y in sorted(by_year)
              if FIRST_YEAR <= y <= last_year]
    # The current year is partial; the chart labels it rather than us
    # silently projecting it.
    return {'area': str(area).zfill(3) if area else None, 'geography': geography,
            'category': category, 'series': series,
            'partial_year': last_year if w['last_month'][5:7] != '12' else None}


def incident_points(since: Optional[str] = None, until: Optional[str] = None,
                    category: str = 'all', limit: int = 15000) -> dict:
    """
    Recent individual incidents as map points, newest first.

    Only the warehouse's point slice (the last ~2.5 years) is available at
    incident grain; the cube serves anything older in aggregate.
    """
    w = warehouse()
    wanted = None if category in ('all', '', None) else category
    lo = since or '0000-00-00'
    hi = until or '9999-12-31'
    limit = max(1, min(int(limit), 50000))

    feats = []
    for d, cat, offence, premises, hood, lng, lat in w['points']:
        if len(feats) >= limit:
            break
        if not (lo <= d <= hi) or (wanted and cat != wanted):
            continue
        feats.append({
            'type': 'Feature',
            'properties': {
                'date': d,
                'category': CATEGORIES[cat]['label'],
                'cat': cat,
                'offence': offence,
                'premises': premises,
                'neighbourhood': hood,
            },
            'geometry': {'type': 'Point', 'coordinates': [lng, lat]},
        })
    oldest = w['points'][-1][0] if w['points'] else None
    return {'type': 'FeatureCollection', 'features': feats,
            'meta': {'category': category, 'since': since, 'until': until,
                     'limit': limit, 'point_record_starts': oldest}}


def summary(since: str = '2025-01-01', until: Optional[str] = None,
            category: str = 'all') -> Dict[str, Any]:
    """City-wide headline numbers plus the highest/lowest areas and movers."""
    stats = with_change('neighbourhood', since, until, category)
    ranked = [{'area': k, **v} for k, v in stats.items() if v['incidents'] > 0]
    by_count = sorted(ranked, key=lambda r: r['incidents'], reverse=True)
    movers = [r for r in ranked if r.get('change') is not None
              and r['incidents'] >= MIN_CHANGE_SAMPLE]
    by_change = sorted(movers, key=lambda r: r['change'], reverse=True)

    total = sum(v['incidents'] for v in stats.values())
    months = _months_between(since, until)
    return {
        'window': {'since': since, 'until': until, 'category': category,
                   'months': months},
        'total_incidents': total,
        'per_month': round(total / months, 1),
        'by_category': {c: sum(v.get(c, 0) for v in stats.values())
                        for c in _CAT_KEYS},
        'areas_reporting': len(ranked),
        'most_incidents': by_count[:10],
        'fewest_incidents': by_count[-10:][::-1],
        'fastest_rising': by_change[:10],
        'fastest_falling': by_change[-10:][::-1],
        'source': 'TPS Major Crime Indicators (Toronto Open Data)',
    }
