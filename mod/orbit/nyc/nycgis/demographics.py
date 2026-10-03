"""
nyc.demographics — who lives where, how densely, and what housing costs them.

Three public, key-free sources joined on the 2020 census tract:

  * US Census ACS 5-year **bulk table files** (www2.census.gov). The Census
    *API* now redirects keyless callers to a "missing key" page, but the
    table-based summary files are plain pipe-delimited downloads anyone can
    fetch. Each file covers every geography in the country (~18-46 MB); we
    stream it once, keep the ~2,300 NYC tract rows, and cache the result.
  * DCP **2020 Census Tracts** (``63ge-mke6``) — the polygons, already
    clipped to the shoreline, each tagged with its NTA and CDTA. That tag is
    what lets tract data roll up to neighbourhoods with no spatial join.
  * DCP **Housing Database by tract** (``nahe-je7c``) — net units completed
    each year and the permitted/filed pipeline: where the city is actually
    adding homes.

Plus the DOF deed records already behind the housing-price layer, for what
homes *sell* for next to what residents *earn*.

Rolling tract medians up to a neighbourhood is not exact — a median of
medians is not a median. We report the household-weighted average of tract
medians and mark it ``approx`` rather than pretend otherwise.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

from . import prices as P
from . import sources as S

ACS_YEARS = (2024, 2023)        # newest first; fall back if a year is missing
ACS_URL = ('https://www2.census.gov/programs-surveys/acs/summary_file/{y}/'
           'table-based-SF/data/5YRData/acsdt5y{y}-{t}.dat')
NYC_COUNTIES = {'36005': 'Bronx', '36047': 'Brooklyn', '36061': 'Manhattan',
                '36081': 'Queens', '36085': 'Staten Island'}
TRACT_PREFIXES = tuple(f'1400000US{c}' for c in NYC_COUNTIES)
# The same files carry each borough (county) and the city (place 3651000).
# Their medians are exact, where a roll-up of tract medians is not — for the
# city the household-weighted tract average overstates median income by ~14%.
WHOLE_AREAS = {f'0500000US{c}': name for c, name in NYC_COUNTIES.items()}
WHOLE_AREAS['1600000US3651000'] = 'NYC'
KEEP_PREFIXES = TRACT_PREFIXES + tuple(WHOLE_AREAS)
SQFT_PER_SQMI = 27_878_400
TTL = 30 * S.DAY

# table → the estimate columns we keep, renamed. Medians are flagged so the
# Census's negative sentinels (-666666666 = "not computable") become None.
ACS_TABLES: Dict[str, Dict[str, str]] = {
    'b01003': {'B01003_E001': 'population'},
    'b19013': {'B19013_E001': 'median_income'},
    'b25064': {'B25064_E001': 'median_rent'},
    'b25077': {'B25077_E001': 'median_value'},
    'b25071': {'B25071_E001': 'rent_pct_income'},
    'b25010': {'B25010_E001': 'household_size'},
    'b25001': {'B25001_E001': 'housing_units'},
    'b25002': {'B25002_E003': 'vacant_units'},
    'b25003': {'B25003_E001': 'households', 'B25003_E002': 'owner_households',
               'B25003_E003': 'renter_households'},
    # gross rent as a share of income: E007-E010 are 30%+, E010 is 50%+,
    # E011 is "not computed" and leaves the denominator.
    'b25070': {'B25070_E001': 'rb_total', 'B25070_E007': 'rb30a', 'B25070_E008': 'rb30b',
               'B25070_E009': 'rb30c', 'B25070_E010': 'rb50', 'B25070_E011': 'rb_nc'},
    # ratio of income to poverty level: E002 <0.5, E003 0.5-0.99
    'c17002': {'C17002_E001': 'pov_total', 'C17002_E002': 'pov_a', 'C17002_E003': 'pov_b'},
}
MEDIANS = {'median_income', 'median_rent', 'median_value', 'rent_pct_income',
           'household_size'}


# ─────────────────────────────────────────────────────────────────────────────
# Census ACS — streamed bulk files
# ─────────────────────────────────────────────────────────────────────────────

def _acs_table(year: int, table: str, cols: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """
    ``{geoid: {name: value}}`` for NYC tracts from one bulk table file, plus
    the five boroughs and the city keyed by name (``'Bronx'`` … ``'NYC'``).
    """
    url = ACS_URL.format(y=year, t=table)
    with requests.get(url, stream=True, timeout=300,
                      headers={'User-Agent': S.USER_AGENT}) as r:
        r.raise_for_status()
        # No charset on the response, so iter_lines yields bytes; the files
        # are plain ASCII.
        lines = (b.decode('latin-1') for b in r.iter_lines(chunk_size=1 << 16))
        header = next(lines).split('|')
        idx = {header.index(c): name for c, name in cols.items() if c in header}
        if len(idx) != len(cols):
            raise ValueError(f'{table} {year}: missing columns '
                             f'{set(cols) - set(header)}')
        out = {}
        for line in lines:
            if not line or not line.startswith(KEEP_PREFIXES):
                continue
            parts = line.split('|')
            row = {}
            for i, name in idx.items():
                try:
                    v = float(parts[i])
                except (IndexError, ValueError):
                    v = None
                if v is not None and v < 0:      # Census sentinel
                    v = None
                row[name] = v
            gid = parts[0]
            out[WHOLE_AREAS.get(gid) or gid[9:]] = row   # '1400000US36061000100' → geoid
        return out


def acs_tracts() -> Dict[str, Any]:
    """Every ACS column we use, per NYC tract, for the newest 5-year release."""
    def fetch():
        last: Optional[Exception] = None
        for year in ACS_YEARS:
            try:
                merged: Dict[str, Dict[str, Any]] = {}
                for table, cols in ACS_TABLES.items():
                    for geoid, row in _acs_table(year, table, cols).items():
                        merged.setdefault(geoid, {}).update(row)
                whole = {k: merged.pop(k) for k in list(merged) if not k.isdigit()}
                return {'year': year, 'vintage': f'{year - 4}-{year}',
                        'tracts': merged, 'whole': whole}
            except requests.HTTPError as e:      # year not published yet
                last = e
        raise last or RuntimeError('no ACS year available')
    return S.cached('demo-acs-tracts-v3', TTL, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# DCP — tract polygons and the housing database
# ─────────────────────────────────────────────────────────────────────────────

def tracts_geo() -> dict:
    """2020 census tracts, shoreline-clipped, tagged with NTA/CDTA."""
    def fetch():
        fc = S.simplify_geojson(
            S.socrata_geojson('63ge-mke6'), tol=0.00006,
            keep=['geoid', 'boroct2020', 'boroname', 'nta2020', 'ntaname',
                  'cdta2020', 'cdtaname', 'shape_area', 'ctlabel'])
        for f in fc['features']:
            p = f['properties']
            p['land_sqmi'] = round(float(p.pop('shape_area', 0) or 0) / SQFT_PER_SQMI, 5)
        return fc
    return S.cached('demo-geo-tracts-v1', TTL, fetch)


COMPLETION_YEARS = list(range(2020, 2027))


def housing_db() -> Dict[str, Dict[str, Any]]:
    """Net new units per tract since 2020, plus the pipeline, keyed by boroct."""
    def fetch():
        cols = ','.join(f'comp{y}' for y in COMPLETION_YEARS)
        rows = S.soql_all(S.NYC, 'nahe-je7c', max_rows=5000,
                          select=f'bct2010,cenunits20,filed,approved,permitted,{cols}')
        out = {}
        for r in rows:
            f = lambda k: float(r.get(k) or 0)
            out[str(r.get('bct2010'))] = {
                'census_units_2020': int(f('cenunits20')),
                'new_units_since_2020': int(sum(f(f'comp{y}') for y in COMPLETION_YEARS)),
                'by_year': {y: int(f(f'comp{y}')) for y in COMPLETION_YEARS},
                'pipeline_units': int(f('filed') + f('approved') + f('permitted')),
                'permitted_units': int(f('permitted')),
            }
        return out
    return S.cached('demo-housingdb-v1', 7 * S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# joining and rolling up
# ─────────────────────────────────────────────────────────────────────────────

# Counts sum; medians average, weighted by the households they describe.
SUMS = ['population', 'housing_units', 'vacant_units', 'households',
        'owner_households', 'renter_households', 'rb_total', 'rb30a', 'rb30b',
        'rb30c', 'rb50', 'rb_nc', 'pov_total', 'pov_a', 'pov_b', 'land_sqmi',
        'new_units_since_2020', 'pipeline_units', 'permitted_units',
        'census_units_2020']
WEIGHTED = {'median_income': 'households', 'median_rent': 'renter_households',
            'median_value': 'owner_households', 'rent_pct_income': 'renter_households',
            'household_size': 'households'}


def _derive(s: Dict[str, Any]) -> Dict[str, Any]:
    """Rates and ratios from the summed counts. Mutates and returns ``s``."""
    def ratio(a, b, scale=100.0, nd=1):
        a, b = s.get(a), s.get(b)
        return round(a / b * scale, nd) if a is not None and b else None

    pop, land = s.get('population') or 0, s.get('land_sqmi') or 0
    s['density'] = round(pop / land) if land > 0.005 else None
    s['vacancy_pct'] = ratio('vacant_units', 'housing_units')
    s['renter_pct'] = ratio('renter_households', 'households')
    denom = (s.get('rb_total') or 0) - (s.get('rb_nc') or 0)
    burden30 = sum(s.get(k) or 0 for k in ('rb30a', 'rb30b', 'rb30c', 'rb50'))
    s['rent_burden_pct'] = round(burden30 / denom * 100, 1) if denom > 0 else None
    s['severe_burden_pct'] = round((s.get('rb50') or 0) / denom * 100, 1) if denom > 0 else None
    s['poverty_pct'] = (round(((s.get('pov_a') or 0) + (s.get('pov_b') or 0))
                              / s['pov_total'] * 100, 1) if s.get('pov_total') else None)
    s['new_units_per_1k'] = ratio('new_units_since_2020', 'population', 1000)
    s['pipeline_per_1k'] = ratio('pipeline_units', 'population', 1000)
    s['units_growth_pct'] = ratio('new_units_since_2020', 'census_units_2020')
    inc = s.get('median_income')
    if inc and s.get('median_sale_price'):
        s['price_to_income'] = round(s['median_sale_price'] / inc, 1)
    if inc and s.get('median_value'):
        s['value_to_income'] = round(s['median_value'] / inc, 1)
    for k in ('rb30a', 'rb30b', 'rb30c', 'rb50', 'rb_nc', 'rb_total',
              'pov_a', 'pov_b', 'pov_total'):
        s.pop(k, None)                    # intermediate counts, not stats
    return s


def tract_table() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """One joined row per tract (no geometry) and the ACS metadata."""
    acs = acs_tracts()
    hdb = housing_db()
    rows = []
    for f in tracts_geo()['features']:
        p = f['properties']
        r = dict(p)
        r.update(acs['tracts'].get(str(p.get('geoid')), {}))
        r.update({k: v for k, v in hdb.get(str(p.get('boroct2020')), {}).items()
                  if k != 'by_year'})
        rows.append(r)
    return rows, {'acs_year': acs['year'], 'acs_vintage': acs['vintage'],
                  'whole': acs.get('whole', {})}


def _rollup(rows: List[Dict[str, Any]], key: Callable[[dict], Optional[str]]
            ) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    wsum: Dict[str, Dict[str, float]] = {}
    for r in rows:
        k = key(r)
        if not k:
            continue
        g = groups.setdefault(k, {k2: 0 for k2 in SUMS})
        w = wsum.setdefault(k, {})
        for c in SUMS:
            g[c] += r.get(c) or 0
        for m, wc in WEIGHTED.items():
            v, wt = r.get(m), r.get(wc) or 0
            if v is not None and wt > 0:
                w[m] = w.get(m, 0) + v * wt
                w[m + '#'] = w.get(m + '#', 0) + wt
    for k, g in groups.items():
        for m in WEIGHTED:
            n = wsum[k].get(m + '#')
            g[m] = (round(wsum[k][m] / n, 2 if m == 'household_size' else
                          (1 if m == 'rent_pct_income' else 0)) if n else None)
        g['land_sqmi'] = round(g['land_sqmi'], 3)
    return groups


GEOS = {
    'tract': {'label': 'Census tract', 'join': 'geoid', 'name': 'ctlabel'},
    'nta': {'label': 'Neighborhood (NTA)', 'join': 'nta2020', 'name': 'ntaname'},
    'borough': {'label': 'Borough', 'join': 'boroname', 'name': 'boroname'},
}


# Homes people buy to live in — houses, condos, co-ops. Whole rental-building
# sales are excluded: one $40M walk-up portfolio deal makes a poor
# neighbourhood look like Tribeca next to its residents' incomes.
SALE_TYPE = 'homes'


def _exact(whole: Dict[str, Any], area: Dict[str, Any], name: str) -> None:
    """Replace rolled-up medians with the Census's exact ones for ``name``."""
    for m in WEIGHTED:
        v = (whole.get(name) or {}).get(m)
        if v is not None:
            area[m] = v
    if name in whole:
        area['medians'] = 'exact'


def _sales(geography: str, since: str) -> Dict[str, Dict[str, Any]]:
    """DOF median home sale price per area — best-effort."""
    try:
        if geography == 'nta':
            return P.aggregate('nta', since=since, property_type=SALE_TYPE)
        if geography in ('borough', 'city'):
            stats = P.aggregate('borough', since=since, property_type=SALE_TYPE)
            return {P.BOROUGH_CODES.get(k, k): v for k, v in stats.items()}
    except Exception:
        pass        # prices are a garnish here; demographics must still load
    return {}


def stats(geography: str = 'nta', since: str = '2025-01-01') -> Dict[str, Any]:
    """Every statistic for every area at ``geography``, plus a citywide row."""
    if geography not in GEOS:
        raise KeyError(f'unknown geography {geography!r}; known: {sorted(GEOS)}')

    def fetch():
        rows, meta = tract_table()
        if geography == 'tract':
            areas = {}
            for r in rows:
                a = {k: r.get(k) for k in SUMS + list(WEIGHTED)}
                a.update(name=f"Tract {r.get('ctlabel')}, {r.get('boroname')}",
                         nta=r.get('ntaname'), borough=r.get('boroname'))
                areas[str(r['geoid'])] = _derive(a)
        else:
            j = GEOS[geography]['join']
            areas = _rollup(rows, lambda r: r.get(j))
            names = {r.get(j): r for r in rows}
            sales = _sales(geography, since)
            for k, a in areas.items():
                src = names.get(k, {})
                a['name'] = src.get(GEOS[geography]['name']) or k
                a['borough'] = src.get('boroname')
                s = sales.get(k) or {}
                a['median_sale_price'] = s.get('median_price')
                a['sales'] = s.get('sales', 0)
                a['medians'] = 'approx'
                if geography == 'borough':
                    _exact(meta['whole'], a, k)
                _derive(a)
        city = _rollup(rows, lambda r: 'NYC')['NYC']
        city.update(name='New York City', medians='approx')
        _exact(meta['whole'], city, 'NYC')
        city['sales'] = sum(v.get('sales', 0) for v in _sales('city', since).values())
        try:     # one more grouped call; a citywide median, not a mean of five
            row = S.soql(P.DOMAIN, P.DATASET, select='median(sale_price) as m',
                         where=P._where('borough', since, None, SALE_TYPE))
            city['median_sale_price'] = P._num(row[0].get('m')) if row else None
        except Exception:
            pass
        _derive(city)
        meta.pop('whole', None)
        return {'geography': geography, 'since': since, 'areas': areas,
                'city': city, **meta, 'sources': SOURCES,
                'notes': NOTES}
    return S.cached(f'demo-stats-{geography}-{since}-v3', S.DAY, fetch)


# ─────────────────────────────────────────────────────────────────────────────
# the map layer
# ─────────────────────────────────────────────────────────────────────────────

METRICS: Dict[str, Dict[str, Any]] = {
    'density': {'label': 'People per sq mi', 'format': 'int'},
    'population': {'label': 'Population', 'format': 'int'},
    'median_income': {'label': 'Median household income', 'format': 'usd'},
    'median_rent': {'label': 'Median gross rent', 'format': 'usd'},
    'median_value': {'label': 'Median home value (owner est.)', 'format': 'usd'},
    'rent_burden_pct': {'label': 'Renters paying 30%+ of income', 'format': 'pct'},
    'severe_burden_pct': {'label': 'Renters paying 50%+ of income', 'format': 'pct'},
    'renter_pct': {'label': 'Households that rent', 'format': 'pct'},
    'vacancy_pct': {'label': 'Vacant homes', 'format': 'pct'},
    'poverty_pct': {'label': 'Below poverty line', 'format': 'pct'},
    'new_units_per_1k': {'label': 'New homes since 2020 per 1k people', 'format': 'num'},
    'pipeline_per_1k': {'label': 'Homes in pipeline per 1k people', 'format': 'num'},
    'price_to_income': {'label': 'Sale price / income (years)', 'format': 'num'},
}

# Feature properties kept on the map layer — every metric plus labels.
_PROPS = ['name', 'borough', 'nta', 'households', 'housing_units', 'land_sqmi',
          'median_sale_price', 'sales', 'new_units_since_2020', 'pipeline_units',
          'household_size', 'rent_pct_income'] + list(METRICS)


def quantile_breaks(values: List[float], n: int = 7) -> Dict[str, Any]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {'stops': [], 'min': None, 'max': None}
    stops = []
    for i in range(1, n):
        v = vals[min(len(vals) - 1, int(len(vals) * i / n))]
        if not stops or v > stops[-1]:
            stops.append(v)
    return {'stops': stops, 'min': vals[0], 'max': vals[-1]}


def choropleth(metric: str = 'density', geography: str = 'tract') -> dict:
    """Polygons for ``geography`` carrying every stat, with breaks for ``metric``."""
    if metric not in METRICS:
        raise KeyError(f'unknown metric {metric!r}; known: {sorted(METRICS)}')
    st = stats(geography)
    if geography == 'tract':
        geo, join = tracts_geo(), 'geoid'
    elif geography == 'nta':
        from . import layers as L
        geo, join = L.neighborhoods(), 'nta2020'
    else:
        from . import layers as L
        geo, join = L.boroughs(), 'boroname'
    feats = []
    for f in geo['features']:
        k = str(f['properties'].get(join))
        a = st['areas'].get(k)
        if a is None:
            continue
        props = {p: a.get(p) for p in _PROPS}
        props['key'] = k
        feats.append({'type': 'Feature', 'geometry': f['geometry'], 'properties': props})
    values = [f['properties'][metric] for f in feats]
    # Tracts that are a park or an airport hold no people; leave them out of
    # the breaks or they flatten the bottom of the scale.
    if metric in ('density', 'population'):
        values = [v for v in values if v]
    return {'type': 'FeatureCollection', 'features': feats,
            'breaks': quantile_breaks(values),
            'meta': {'metric': metric, 'geography': geography,
                     'label': METRICS[metric]['label'],
                     'format': METRICS[metric]['format'],
                     'areas_with_data': sum(1 for v in values if v is not None),
                     'acs_vintage': st['acs_vintage'], 'city': st['city']}}


SOURCES = [
    {'name': 'US Census Bureau, American Community Survey 5-year (table-based summary file)',
     'url': 'https://www2.census.gov/programs-surveys/acs/summary_file/'},
    {'name': 'NYC DCP 2020 Census Tracts', 'url': 'https://data.cityofnewyork.us/d/63ge-mke6'},
    {'name': 'NYC DCP Housing Database by 2020 Census Tract',
     'url': 'https://data.cityofnewyork.us/d/nahe-je7c'},
    {'name': 'NYC DOF Citywide Rolling Sales', 'url': 'https://data.cityofnewyork.us/d/w2pb-icbu'},
]

NOTES = [
    'Population, income, rent, home value, vacancy, rent burden and poverty are '
    'ACS 5-year survey estimates with margins of error; small tracts are noisy.',
    'Citywide and borough medians are the Census\'s own. Neighborhood medians '
    'are household-weighted averages of tract medians (an approximation).',
    'Density is residents per square mile of land (tracts are clipped to the '
    'shoreline); parks, cemeteries and airports pull neighborhood averages down.',
    'New homes = net units completed 2020 to date (DCP Housing Database); '
    'pipeline = units filed, approved or permitted but not finished.',
    'Sale price = median recorded sale of a house, condo or co-op (DOF), $50k+ '
    'only; whole rental-building sales are excluded.',
]
