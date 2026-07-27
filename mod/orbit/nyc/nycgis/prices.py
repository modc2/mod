"""
nyc.prices — housing prices from the city's own deed records.

Source: NYC Department of Finance **Citywide Annualized Calendar Year Rolling
Sales** (Socrata ``w2pb-icbu``) — every recorded property sale in the five
boroughs, ~845k rows covering 2016 to the present. This is the primary
transaction record, not an estimate or a listings scrape.

Everything is aggregated server-side with SoQL ``$group``, so a choropleth for
262 neighbourhoods costs one HTTP request instead of downloading a million
rows. Results are cached; see :mod:`nyc.sources`.

Two data quirks in the raw table drive most of the code here:

1. ``gross_square_feet`` is stored as *text with thousands separators*
   ("1,430"), so it must be de-comma'd before casting. Hence ``SQFT_EXPR``.
2. A large share of rows are $0 or nominal-price deed transfers (family
   transfers, LLC restructurings, estate filings). They are not market sales
   and would drag every median to zero, so ``MIN_SALE_PRICE`` excludes them.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

from . import sources as S

DATASET = 'w2pb-icbu'
DOMAIN = S.NYC

# gross_square_feet arrives as text like "1,430" — strip separators, then cast.
SQFT_EXPR = 'replace(gross_square_feet,",","")::number'

# Below this, a "sale" is almost always a non-arms-length transfer, not a price.
MIN_SALE_PRICE = 50_000
MIN_SQFT = 200          # guards against typo'd square footage inflating $/ft²

# $/ft² plausibility band, applied per row before the median is taken.
#
# This is not cosmetic. For condo and co-op units the DOF file frequently
# reports `gross_square_feet` for the *entire building* rather than the unit,
# so a $1M apartment in a 250,000 ft² tower computes to $4/ft². Left in, those
# rows dragged Financial District-Battery Park City to a $4/ft² median across
# 163 "samples" — a number the map would have drawn as if it were real.
# Filtering per row (rather than clamping the result) removes the bad rows and
# leaves the genuine ones in the median.
MIN_PPSF = 50
MAX_PPSF = 5_000
MIN_PPSF_SAMPLE = 5     # below this the median is noise, so report no value

BOROUGH_CODES = {'1': 'Manhattan', '2': 'Bronx', '3': 'Brooklyn',
                 '4': 'Queens', '5': 'Staten Island'}

# Building-class categories are prefixed with a 2-digit code. Matching on the
# prefix avoids the dataset's inconsistent spacing ("01 ONE" vs "01  ONE").
PROPERTY_TYPES: Dict[str, Dict[str, Any]] = {
    'all': {'label': 'All property types', 'prefixes': None},
    'residential': {'label': 'All residential',
                    'prefixes': ['01', '02', '03', '04', '07', '08', '09', '10',
                                 '11', '12', '13', '14', '15', '17']},
    'houses': {'label': 'Houses (1–3 family)', 'prefixes': ['01', '02', '03']},
    'one_family': {'label': 'One-family houses', 'prefixes': ['01']},
    'condo': {'label': 'Condos', 'prefixes': ['04', '12', '13', '15']},
    'coop': {'label': 'Co-ops', 'prefixes': ['09', '10', '17']},
    'rental': {'label': 'Rental buildings', 'prefixes': ['07', '08', '11', '14']},
    'commercial': {'label': 'Commercial & mixed use',
                   'prefixes': ['21', '22', '25', '26', '27', '28', '29', '30',
                                '31', '32', '33', '34', '35', '36', '37', '38']},
}

# Geographies you can aggregate by. `column` is the sales-table column;
# `join` is the matching property on the boundary layer's features.
GEOGRAPHIES: Dict[str, Dict[str, Any]] = {
    'nta': {'label': 'Neighborhood (NTA)', 'column': 'nta',
            'boundary': 'nta', 'join': 'nta2020', 'name': 'ntaname'},
    'borough': {'label': 'Borough', 'column': 'borough',
                'boundary': 'borough', 'join': 'borocode', 'name': 'boroname'},
    'zip': {'label': 'ZIP code', 'column': 'zip_code',
            'boundary': 'zip', 'join': 'modzcta', 'name': 'label'},
    'community_district': {'label': 'Community district', 'column': 'community_board',
                           'boundary': 'nta', 'join': 'cdta2020', 'name': 'cdtaname',
                           'key': 'community_board_to_cdta'},
}

# CDTA codes prefix the borough by initials; the sales table encodes the same
# thing as a leading digit ("101" = Manhattan CD 1 = "MN01").
_CDTA_PREFIX = {'1': 'MN', '2': 'BX', '3': 'BK', '4': 'QN', '5': 'SI'}


def community_board_to_cdta(board: str) -> Optional[str]:
    """``"101"`` → ``"MN01"``. Returns None for the "unknown board" codes."""
    b = str(board).strip()
    if len(b) != 3 or not b.isdigit():
        return None
    prefix = _CDTA_PREFIX.get(b[0])
    district = int(b[1:])
    # Boards 64/80/82/83/84/95 are parks, airports and other non-residential
    # catch-alls with no CDTA polygon to join to.
    if not prefix or not 1 <= district <= 18:
        return None
    return f'{prefix}{district:02d}'


KEY_MAPS = {'community_board_to_cdta': community_board_to_cdta}


def remap_keys(stats: Dict[str, dict], geography: str) -> Dict[str, dict]:
    """Translate aggregation keys into the boundary layer's join keys."""
    geo = GEOGRAPHIES.get(geography) or {}
    fn = KEY_MAPS.get(geo.get('key', ''))
    if not fn:
        return stats
    out: Dict[str, dict] = {}
    for k, v in stats.items():
        mapped = fn(k)
        if not mapped:
            continue
        if mapped in out:
            out[mapped] = _merge(out[mapped], v)
        else:
            out[mapped] = dict(v)
    return out


def _merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Combine two areas' stats, weighting medians by sale count."""
    na, nb = a.get('sales', 0), b.get('sales', 0)
    total = na + nb or 1
    out = dict(a)
    for k in ('median_price', 'median_ppsf', 'avg_price'):
        va, vb = a.get(k), b.get(k)
        if va is not None and vb is not None:
            out[k] = round((va * na + vb * nb) / total)
        elif vb is not None:
            out[k] = vb
    out['sales'] = total
    out['total_value'] = (a.get('total_value') or 0) + (b.get('total_value') or 0)
    return out

METRICS: Dict[str, Dict[str, Any]] = {
    'median_price': {'label': 'Median sale price', 'unit': '$', 'format': 'usd'},
    'median_ppsf': {'label': 'Median price per ft²', 'unit': '$/ft²', 'format': 'usd'},
    'avg_price': {'label': 'Average sale price', 'unit': '$', 'format': 'usd'},
    'sales': {'label': 'Number of sales', 'unit': 'sales', 'format': 'count'},
    'total_value': {'label': 'Total sales value', 'unit': '$', 'format': 'usd_compact'},
    'price_change': {'label': 'Price change vs. prior period', 'unit': '%', 'format': 'percent'},
}


# ─────────────────────────────────────────────────────────────────────────────
# query building
# ─────────────────────────────────────────────────────────────────────────────

def _type_clause(property_type: str) -> Optional[str]:
    prefixes = PROPERTY_TYPES.get(property_type, PROPERTY_TYPES['all'])['prefixes']
    if not prefixes:
        return None
    return '(' + ' or '.join(
        f'starts_with(building_class_category,"{p}")' for p in prefixes) + ')'


def ppsf_clause() -> str:
    """Row filter keeping only sales whose implied $/ft² is plausible."""
    return (f'{SQFT_EXPR} > {MIN_SQFT} and {SQFT_EXPR} < 1000000 and '
            f'sale_price/({SQFT_EXPR}) >= {MIN_PPSF} and '
            f'sale_price/({SQFT_EXPR}) <= {MAX_PPSF}')


def _where(column: str, since: Optional[str], until: Optional[str],
           property_type: str, extra: Optional[str] = None) -> str:
    parts = [
        f'sale_price > {MIN_SALE_PRICE}',
        f'{column} IS NOT NULL',
    ]
    if since:
        parts.append(f'sale_date >= "{since}T00:00:00.000"')
    if until:
        parts.append(f'sale_date <= "{until}T23:59:59.999"')
    tc = _type_clause(property_type)
    if tc:
        parts.append(tc)
    if extra:
        parts.append(extra)
    return ' and '.join(parts)


def _num(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # drop NaN


# ─────────────────────────────────────────────────────────────────────────────
# aggregation
# ─────────────────────────────────────────────────────────────────────────────

def aggregate(geography: str = 'nta', since: Optional[str] = '2024-01-01',
              until: Optional[str] = None, property_type: str = 'residential',
              ttl: float = 12 * S.DAY) -> Dict[str, Dict[str, Any]]:
    """
    Price statistics per area, as ``{area_key: {sales, median_price, …}}``.

    Two SoQL round-trips: one for price stats over all qualifying sales, one
    for $/ft² restricted to rows that actually report square footage (co-ops
    almost never do, so mixing them would bias the count).
    """
    geo = GEOGRAPHIES.get(geography) or GEOGRAPHIES['nta']
    col = geo['column']
    key = f'agg-{geography}-{since}-{until}-{property_type}'

    def fetch():
        base = _where(col, since, until, property_type)
        rows = S.soql(
            DOMAIN, DATASET,
            select=(f'{col} as area, count(1) as sales, median(sale_price) as median_price, '
                    f'avg(sale_price) as avg_price, sum(sale_price) as total_value, '
                    f'min(sale_price) as min_price, max(sale_price) as max_price'),
            where=base, group=col, limit=5000)

        ppsf_rows = S.soql(
            DOMAIN, DATASET,
            select=(f'{col} as area, median(sale_price/({SQFT_EXPR})) as median_ppsf, '
                    f'count(1) as ppsf_sales'),
            where=_where(col, since, until, property_type, extra=ppsf_clause()),
            group=col, limit=5000)
        ppsf = {r['area']: r for r in ppsf_rows}

        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            area = str(r['area']).strip()
            if not area:
                continue
            p = ppsf.get(r['area'], {})
            ppsf_val = _num(p.get('median_ppsf'))
            ppsf_n = int(_num(p.get('ppsf_sales')) or 0)
            if ppsf_val is None or ppsf_n < MIN_PPSF_SAMPLE:
                ppsf_val = None
            out[area] = {
                'sales': int(_num(r.get('sales')) or 0),
                'median_price': _num(r.get('median_price')),
                'avg_price': round(_num(r.get('avg_price')) or 0),
                'total_value': _num(r.get('total_value')),
                'min_price': _num(r.get('min_price')),
                'max_price': _num(r.get('max_price')),
                'median_ppsf': round(ppsf_val) if ppsf_val is not None else None,
                'ppsf_sales': ppsf_n,
            }
        return out

    return S.cached(key, ttl, fetch)


def with_change(geography: str = 'nta', since: str = '2024-01-01',
                until: Optional[str] = None, property_type: str = 'residential',
                prior_since: Optional[str] = None,
                prior_until: Optional[str] = None,
                ttl: float = 12 * S.DAY) -> Dict[str, Dict[str, Any]]:
    """``aggregate`` plus ``price_change`` — % change in median vs a prior window."""
    current = aggregate(geography, since, until, property_type, ttl)
    if not prior_since:
        prior_since, prior_until = _prior_window(since, until)
    prior = aggregate(geography, prior_since, prior_until, property_type, ttl)

    merged: Dict[str, Dict[str, Any]] = {}
    for area, stats in current.items():
        s = dict(stats)
        old = prior.get(area, {})
        old_med, new_med = old.get('median_price'), s.get('median_price')
        # Require a real sample on both sides — a 3-sale neighbourhood produces
        # meaningless swings that would dominate the colour scale.
        if old_med and new_med and old.get('sales', 0) >= 5 and s.get('sales', 0) >= 5:
            s['price_change'] = round((new_med - old_med) / old_med * 100, 1)
            s['prior_median_price'] = old_med
        else:
            s['price_change'] = None
            s['prior_median_price'] = old_med
        s['prior_sales'] = old.get('sales', 0)
        merged[area] = s
    return merged


def _prior_window(since: str, until: Optional[str]) -> tuple:
    """The equally-long window immediately before [since, until]."""
    from datetime import date, timedelta
    start = date.fromisoformat(since)
    end = date.fromisoformat(until) if until else date.today()
    span = max((end - start).days, 1)
    return (start - timedelta(days=span)).isoformat(), (start - timedelta(days=1)).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# choropleth
# ─────────────────────────────────────────────────────────────────────────────

def choropleth(boundaries: dict, geography: str = 'nta',
               since: str = '2024-01-01', until: Optional[str] = None,
               property_type: str = 'residential') -> dict:
    """
    Join price stats onto a boundary FeatureCollection.

    Areas with no qualifying sales keep their geometry but carry ``sales: 0``
    and null metrics, so the map can grey them out honestly rather than
    dropping them (a missing polygon reads as "no data", a hole reads as "bug").
    """
    geo = GEOGRAPHIES.get(geography) or GEOGRAPHIES['nta']
    stats = remap_keys(with_change(geography, since, until, property_type), geography)
    join_key, name_key = geo['join'], geo['name']

    # ZIP boundaries are *modified* ZCTAs: one polygon can cover several ZIPs
    # (e.g. 10001 also carries 10118/10119). Roll member ZIPs into their parent.
    if geography == 'zip':
        stats = _roll_up_zips(boundaries, stats)

    feats, matched = [], 0
    for f in boundaries.get('features', []):
        props = dict(f.get('properties') or {})
        area = str(props.get(join_key, '')).strip()
        s = stats.get(area)
        if s:
            matched += 1
        out = {
            'area': area,
            'name': props.get(name_key) or area,
            'borough': props.get('boroname') or BOROUGH_CODES.get(props.get('borocode', '')) or '',
        }
        for k in ('sales', 'median_price', 'median_ppsf', 'avg_price',
                  'total_value', 'price_change', 'prior_median_price', 'ppsf_sales'):
            out[k] = (s or {}).get(k, 0 if k == 'sales' else None)
        feats.append({'type': 'Feature', 'properties': out, 'geometry': f['geometry']})

    return {
        'type': 'FeatureCollection',
        'features': feats,
        'meta': {
            'geography': geography, 'since': since, 'until': until,
            'property_type': property_type,
            'areas': len(feats), 'areas_with_data': matched,
            'source': 'NYC DOF Citywide Rolling Sales (w2pb-icbu)',
        },
    }


def _roll_up_zips(boundaries: dict, stats: Dict[str, dict]) -> Dict[str, dict]:
    """Map each member ZIP onto its MODZCTA parent, summing sales."""
    parent: Dict[str, str] = {}
    for f in boundaries.get('features', []):
        p = f.get('properties') or {}
        modz = str(p.get('modzcta', '')).strip()
        if not modz:
            continue
        for z in str(p.get('zcta', modz)).split(','):
            z = z.strip()
            if z:
                parent[z] = modz

    rolled: Dict[str, dict] = {}
    for zip_code, s in stats.items():
        key = parent.get(zip_code, zip_code)
        # Medians can't be summed, so _merge weights them by sale count.
        rolled[key] = _merge(rolled[key], s) if key in rolled else dict(s)
    return rolled


# ─────────────────────────────────────────────────────────────────────────────
# detail views
# ─────────────────────────────────────────────────────────────────────────────

def trend_all(geography: str = 'nta', property_type: str = 'residential',
              start_year: int = 2016, ttl: float = 12 * S.DAY) -> Dict[str, list]:
    """
    Yearly price history for *every* area at once, as ``{area: [year rows]}``.

    Deliberately one pair of queries for the whole city rather than a pair per
    area. Grouping by (area, year) returns ~2,600 rows for the 262 NTAs — well
    inside a single page — so the first click pays ~20s once and every
    subsequent area is served from cache instantly. Per-area queries made each
    first click on a neighbourhood take that 20s on its own.
    """
    geo = GEOGRAPHIES.get(geography) or GEOGRAPHIES['nta']
    col = geo['column']
    key = f'trendall-{geography}-{property_type}-{start_year}'

    def fetch():
        since = f'{start_year}-01-01'
        rows = S.soql(
            DOMAIN, DATASET,
            select=(f'{col} as area, date_extract_y(sale_date) as year, count(1) as sales, '
                    'median(sale_price) as median_price, sum(sale_price) as total_value'),
            where=_where(col, since, None, property_type),
            group=f'{col}, date_extract_y(sale_date)', limit=40000)
        ppsf_rows = S.soql(
            DOMAIN, DATASET,
            select=(f'{col} as area, date_extract_y(sale_date) as year, '
                    f'median(sale_price/({SQFT_EXPR})) as median_ppsf, count(1) as n'),
            where=_where(col, since, None, property_type, extra=ppsf_clause()),
            group=f'{col}, date_extract_y(sale_date)', limit=40000)

        ppsf = {}
        for r in ppsf_rows:
            v = _num(r.get('median_ppsf'))
            n = int(_num(r.get('n')) or 0)
            if v is not None and MIN_PPSF <= v <= MAX_PPSF and n >= MIN_PPSF_SAMPLE:
                ppsf[(str(r.get('area')), str(r.get('year')))] = round(v)

        out: Dict[str, list] = {}
        for r in rows:
            area = str(r.get('area', '')).strip()
            if not area:
                continue
            year = str(r.get('year'))
            out.setdefault(area, []).append({
                'year': int(_num(year) or 0),
                'sales': int(_num(r.get('sales')) or 0),
                'median_price': _num(r.get('median_price')),
                'median_ppsf': ppsf.get((str(r.get('area')), year)),
                'total_value': _num(r.get('total_value')),
            })
        for series in out.values():
            series.sort(key=lambda s: s['year'])
        return out

    return S.cached(key, ttl, fetch)


def trend(area: Optional[str] = None, geography: str = 'nta',
          property_type: str = 'residential', start_year: int = 2016,
          ttl: float = 12 * S.DAY) -> Dict[str, Any]:
    """Yearly median price / $/ft² / volume — city-wide, or for one area."""
    everything = trend_all(geography, property_type, start_year, ttl)

    if area:
        key = area
        # Community districts aggregate several board codes into one polygon.
        if GEOGRAPHIES.get(geography, {}).get('key') == 'community_board_to_cdta':
            series = _sum_years([v for k, v in everything.items()
                                 if community_board_to_cdta(k) == area])
        else:
            series = everything.get(key, [])
    else:
        series = _sum_years(list(everything.values()))

    return {'area': area, 'geography': geography,
            'property_type': property_type, 'series': series}


def _sum_years(groups: List[list]) -> list:
    """
    Combine several areas' yearly series into one.

    Medians are weighted by sale count rather than averaged — a 30-sale year
    and a 3,000-sale year should not count equally. This is an approximation of
    the true pooled median, which the grouped query can't give us directly.
    """
    by_year: Dict[int, Dict[str, Any]] = {}
    for series in groups:
        for row in series:
            y = row['year']
            acc = by_year.setdefault(y, {'year': y, 'sales': 0, 'total_value': 0.0,
                                         '_p': 0.0, '_pn': 0, '_s': 0.0, '_sn': 0})
            n = row.get('sales') or 0
            acc['sales'] += n
            acc['total_value'] += row.get('total_value') or 0
            if row.get('median_price') is not None:
                acc['_p'] += row['median_price'] * n
                acc['_pn'] += n
            if row.get('median_ppsf') is not None:
                acc['_s'] += row['median_ppsf'] * n
                acc['_sn'] += n

    out = []
    for y in sorted(by_year):
        a = by_year[y]
        out.append({
            'year': y,
            'sales': a['sales'],
            'median_price': round(a['_p'] / a['_pn']) if a['_pn'] else None,
            'median_ppsf': round(a['_s'] / a['_sn']) if a['_sn'] else None,
            'total_value': a['total_value'],
        })
    return out


def sales_points(since: str = '2025-01-01', until: Optional[str] = None,
                 property_type: str = 'residential', limit: int = 12000,
                 min_price: Optional[int] = None, max_price: Optional[int] = None,
                 ttl: float = 2 * S.DAY) -> dict:
    """Individual recorded sales as map points, newest first."""
    key = f'points-{since}-{until}-{property_type}-{limit}-{min_price}-{max_price}'

    def fetch():
        extra = 'latitude IS NOT NULL and longitude IS NOT NULL'
        if min_price:
            extra += f' and sale_price >= {int(min_price)}'
        if max_price:
            extra += f' and sale_price <= {int(max_price)}'
        rows = S.soql(
            DOMAIN, DATASET,
            select=('address,neighborhood,borough,zip_code,sale_price,sale_date,'
                    'building_class_category,gross_square_feet,year_built,'
                    'residential_units,total_units,latitude,longitude'),
            where=_where('latitude', since, until, property_type, extra),
            order='sale_date DESC', limit=min(int(limit), 50000))

        feats = []
        for r in rows:
            lat, lng = _num(r.get('latitude')), _num(r.get('longitude'))
            price = _num(r.get('sale_price'))
            if lat is None or lng is None or not price:
                continue
            sqft = _num(str(r.get('gross_square_feet', '')).replace(',', ''))
            ppsf = round(price / sqft) if sqft and sqft > MIN_SQFT else None
            feats.append({
                'type': 'Feature',
                'properties': {
                    'address': r.get('address', '').strip(),
                    'neighborhood': r.get('neighborhood', '').strip(),
                    'borough': BOROUGH_CODES.get(str(r.get('borough', '')).strip(), ''),
                    'zip': r.get('zip_code'),
                    'price': int(price),
                    'date': str(r.get('sale_date', ''))[:10],
                    'type': r.get('building_class_category', '').strip(),
                    'sqft': int(sqft) if sqft else None,
                    'ppsf': ppsf if (ppsf and MIN_PPSF <= ppsf <= MAX_PPSF) else None,
                    'year_built': int(_num(r.get('year_built')) or 0) or None,
                    'units': int(_num(r.get('total_units')) or 0) or None,
                },
                'geometry': {'type': 'Point', 'coordinates': [round(lng, 5), round(lat, 5)]},
            })
        return {'type': 'FeatureCollection', 'features': feats}

    return S.cached(key, ttl, fetch)


def summary(since: str = '2024-01-01', until: Optional[str] = None,
            property_type: str = 'residential') -> Dict[str, Any]:
    """City-wide headline numbers plus the most and least expensive areas."""
    stats = with_change('nta', since, until, property_type)
    ranked = [{'area': k, **v} for k, v in stats.items()
              if v.get('median_price') and v.get('sales', 0) >= 20]
    by_price = sorted(ranked, key=lambda r: r['median_price'], reverse=True)
    movers = [r for r in ranked if r.get('price_change') is not None]
    by_change = sorted(movers, key=lambda r: r['price_change'], reverse=True)
    medians = [r['median_price'] for r in ranked]

    return {
        'window': {'since': since, 'until': until, 'property_type': property_type},
        'total_sales': sum(v.get('sales', 0) for v in stats.values()),
        'total_value': sum(v.get('total_value') or 0 for v in stats.values()),
        'areas_reporting': len(ranked),
        'citywide_median_of_neighborhood_medians': round(statistics.median(medians)) if medians else None,
        'most_expensive': by_price[:10],
        'least_expensive': by_price[-10:][::-1],
        'fastest_rising': by_change[:10],
        'fastest_falling': by_change[-10:][::-1],
        'source': 'NYC DOF Citywide Rolling Sales (w2pb-icbu)',
    }
