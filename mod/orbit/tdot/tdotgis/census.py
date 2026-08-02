"""
tdot.census — what housing costs, neighbourhood by neighbourhood.

Toronto publishes the full 2021 Census profile for its 158 neighbourhoods, and
that profile is the only *open* per-neighbourhood record of what homes here are
worth: owner-estimated dwelling values, owner and tenant shelter costs, tenure,
condo share, dwelling age and the share of households priced out of their own
home. Sale-by-sale MLS prices are TRREB's copyrighted database and are not open
data at any price — see ``market.py`` for the path that exists for people who
hold a licensed feed.

The profile ships as one XLSX: census variables down column A, one column per
neighbourhood across. That shape is why this module reads it by *label* rather
than by row number — the city reissues the file and rows move, but
``  Average value of dwellings ($)`` is stable.

XLSX is a zip of XML, so it is read here directly rather than by adding an
openpyxl dependency the rest of the module would never use — the same trade
made for the MTM projection in :mod:`tdotgis.sources`. Fifty lines of
ElementTree against a documented format beats a wheel.

Every metric is joined onto the 158-neighbourhood boundary and classed the same
way the crime engine classes its own: quantiles, because dwelling values in
this city are as right-skewed as crime counts are.
"""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, List, Optional

from . import sources as S

# The 2021 profile is a census release: it will not change until 2026's lands.
PROFILE_TTL = 180 * S.DAY

PACKAGE = 'neighbourhood-profiles'
RESOURCE = 'neighbourhood-profiles-2021-158-model'

_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


# ─────────────────────────────────────────────────────────────────────────────
# XLSX (zip of XML) → rows
# ─────────────────────────────────────────────────────────────────────────────

def _col(ref: str) -> int:
    """``'AB12'`` → 27. Spreadsheet columns are base-26 with no zero."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_xlsx(blob: bytes, sheet: int = 1) -> List[List[Optional[str]]]:
    """
    One sheet of an XLSX as a dense list of rows.

    Cells carry their own address, and a run of empty cells is simply absent
    from the XML — so each row is placed by column index rather than appended,
    or a neighbourhood with one blank metric would shift every value after it
    into the wrong column.
    """
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared: List[str] = []
    if 'xl/sharedStrings.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        shared = [''.join(t.text or '' for t in si.iter(_NS + 't'))
                  for si in root.iter(_NS + 'si')]

    rows: List[List[Optional[str]]] = []
    for row in ET.fromstring(z.read(f'xl/worksheets/sheet{sheet}.xml')).iter(_NS + 'row'):
        cells: List[Optional[str]] = []
        for c in row.iter(_NS + 'c'):
            i = _col(c.get('r') or '')
            if i < 0:
                continue
            kind = c.get('t')
            if kind == 'inlineStr':
                node = c.find(_NS + 'is')
                value = ''.join(t.text or '' for t in node.iter(_NS + 't')) if node is not None else None
            else:
                v = c.find(_NS + 'v')
                value = v.text if v is not None else None
                if kind == 's' and value is not None:
                    value = shared[int(value)]
            while len(cells) <= i:
                cells.append(None)
            cells[i] = value
        rows.append(cells)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# the profile
# ─────────────────────────────────────────────────────────────────────────────

def _number(v: Any) -> Optional[float]:
    """Census cells suppress small counts as ``x``/``F``; those are not zeros."""
    if v is None:
        return None
    s = str(v).strip().replace(',', '').replace('$', '')
    if not s or s in ('x', 'F', '..', '...', '-'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def profile() -> Dict[str, Any]:
    """
    The 2021 profile as ``{label: {neighbourhood_code: value}}``.

    Cached for six months and roughly 2 MB — the whole 2,600-variable profile is
    kept rather than only today's metrics, so adding a metric below never costs
    another download.
    """
    def fetch():
        res = S.ckan_resource(PACKAGE, RESOURCE)
        rows = read_xlsx(S._get(res['url'], timeout=300).content)
        if len(rows) < 3:
            raise ValueError('census profile: sheet is empty')

        names, numbers = rows[0], rows[1]
        # Column order is the file's, and the code (the neighbourhood number) is
        # what every boundary layer joins on.
        codes: Dict[int, str] = {}
        hoods: Dict[str, str] = {}
        for i in range(1, max(len(names), len(numbers))):
            code = str(numbers[i] if i < len(numbers) else '' or '').strip()
            if not code.isdigit():
                continue
            codes[i] = str(int(code))
            hoods[codes[i]] = str(names[i] if i < len(names) else '' or '').strip()

        data: Dict[str, Dict[str, float]] = {}
        for row in rows[2:]:
            label = str(row[0] or '').rstrip()
            if not label:
                continue
            values = {}
            for i, code in codes.items():
                n = _number(row[i]) if i < len(row) else None
                if n is not None:
                    values[code] = n
            # A label repeats in the census profile (income statistics appear
            # under several universes); the first block is the household-level
            # one this module reports, so later duplicates are not allowed to
            # overwrite it.
            if values and label not in data:
                data[label] = values
        return {'neighbourhoods': hoods, 'rows': data,
                'source': res.get('name'), 'variables': len(data)}

    return S.cached('census-profile-2021', PROFILE_TTL, fetch)


def _row(prof: dict, label: str) -> Dict[str, float]:
    return prof['rows'].get(label, {})


# ─────────────────────────────────────────────────────────────────────────────
# metrics
# ─────────────────────────────────────────────────────────────────────────────

# Census labels are load-bearing here, so they are named once and referenced.
L_VALUE_AVG = '  Average value of dwellings ($)'
L_VALUE_MED = '  Median value of dwellings ($)'
L_OWNER_COST = '  Average monthly shelter costs for owned dwellings ($)'
L_RENT_AVG = '  Average monthly shelter costs for rented dwellings ($)'
L_RENT_MED = '  Median monthly shelter costs for rented dwellings ($)'
L_MORTGAGED = '  % of owner households with a mortgage'
L_OWNER_STRAIN = '  % of owner households spending 30% or more of its income on shelter costs'
L_TENANT_STRAIN = '  % of tenant households spending 30% or more of its income on shelter costs'
L_INCOME_MED = '  Median total income of household in 2020 ($)'
L_TENURE = 'Total - Private households by tenure - 25% sample data'
L_OWNERS = '  Owner'
L_RENTERS = '  Renter'
L_CONDO_TOTAL = 'Total - Occupied private dwellings by condominium status - 25% sample data'
L_CONDO = '  Condominium'
L_BUILT_TOTAL = 'Total - Occupied private dwellings by period of construction - 25% sample data'
L_BUILT_NEW = '  2016 to 2021'
L_BUILT_OLD = '  1960 or before'
L_STRUCT_TOTAL = 'Total - Occupied private dwellings by structural type of dwelling - 25% sample data'
L_HOUSES = '  Single-detached house'
L_CONDITION_TOTAL = 'Total - Occupied private dwellings by dwelling condition - 25% sample data'
L_REPAIRS = '  Major repairs needed'


def _share(prof: dict, part: str, whole: str) -> Dict[str, float]:
    """One census row as a percentage of another — the shares below."""
    num, den = _row(prof, part), _row(prof, whole)
    return {code: round(100 * v / den[code], 1)
            for code, v in num.items() if den.get(code)}


def _ratio(prof: dict, top: str, bottom: str, scale: float = 1.0) -> Dict[str, float]:
    num, den = _row(prof, top), _row(prof, bottom)
    return {code: round(scale * v / den[code], 1)
            for code, v in num.items() if den.get(code)}


# id → how to read it, what to call it and how the UI should format it.
# ``derive`` metrics are arithmetic on the profile; the rest are rows verbatim.
METRICS: Dict[str, Dict[str, Any]] = {
    'avg_value': {
        'label': 'Average dwelling value', 'format': 'money', 'unit': '$',
        'row': L_VALUE_AVG,
        'note': 'What owners estimate their home would sell for (2021 Census).',
    },
    'median_value': {
        'label': 'Median dwelling value', 'format': 'money', 'unit': '$',
        'row': L_VALUE_MED,
        'note': 'The middle home, so a few estate lots cannot move it.',
    },
    'median_rent': {
        'label': 'Median rent (all shelter costs)', 'format': 'money', 'unit': '$/mo',
        'row': L_RENT_MED,
        'note': 'Median monthly shelter cost of a tenant household — rent plus '
                'utilities, across the whole existing stock, not asking rents.',
    },
    'avg_rent': {
        'label': 'Average rent (all shelter costs)', 'format': 'money', 'unit': '$/mo',
        'row': L_RENT_AVG,
    },
    'owner_cost': {
        'label': 'Owner monthly cost', 'format': 'money', 'unit': '$/mo',
        'row': L_OWNER_COST,
        'note': 'Mortgage, taxes and utilities for owner households.',
    },
    'price_to_income': {
        'label': 'Price-to-income multiple', 'format': 'ratio', 'unit': '×',
        'derive': lambda p: _ratio(p, L_VALUE_MED, L_INCOME_MED),
        'note': 'Median home value ÷ median household income. Above 5× is '
                'conventionally "severely unaffordable".',
    },
    'rent_to_income': {
        'label': 'Rent as % of income', 'format': 'percent', 'unit': '%',
        'derive': lambda p: _ratio(p, L_RENT_MED, L_INCOME_MED, scale=1200),
        'note': 'Median rent × 12 against median household income for the whole '
                'neighbourhood, owners included.',
    },
    'median_income': {
        'label': 'Median household income', 'format': 'money', 'unit': '$',
        'row': L_INCOME_MED,
    },
    'renter_share': {
        'label': 'Renter households', 'format': 'percent', 'unit': '%',
        'derive': lambda p: _share(p, L_RENTERS, L_TENURE),
    },
    'owner_strain': {
        'label': 'Owners over 30% of income', 'format': 'percent', 'unit': '%',
        'row': L_OWNER_STRAIN,
    },
    'tenant_strain': {
        'label': 'Tenants over 30% of income', 'format': 'percent', 'unit': '%',
        'row': L_TENANT_STRAIN,
        'note': 'The standard affordability threshold. Above it, a household is '
                'considered shelter-cost burdened.',
    },
    'mortgaged': {
        'label': 'Owners with a mortgage', 'format': 'percent', 'unit': '%',
        'row': L_MORTGAGED,
    },
    'condo_share': {
        'label': 'Condominium share', 'format': 'percent', 'unit': '%',
        'derive': lambda p: _share(p, L_CONDO, L_CONDO_TOTAL),
    },
    'house_share': {
        'label': 'Detached-house share', 'format': 'percent', 'unit': '%',
        'derive': lambda p: _share(p, L_HOUSES, L_STRUCT_TOTAL),
    },
    'built_since_2016': {
        'label': 'Built 2016 or later', 'format': 'percent', 'unit': '%',
        'derive': lambda p: _share(p, L_BUILT_NEW, L_BUILT_TOTAL),
        'note': 'Where the new stock actually landed.',
    },
    'built_pre_1960': {
        'label': 'Built 1960 or before', 'format': 'percent', 'unit': '%',
        'derive': lambda p: _share(p, L_BUILT_OLD, L_BUILT_TOTAL),
    },
    'major_repairs': {
        'label': 'Needing major repairs', 'format': 'percent', 'unit': '%',
        'derive': lambda p: _share(p, L_REPAIRS, L_CONDITION_TOTAL),
    },
}

DEFAULT_METRIC = 'avg_value'

# The catalogue is JSON on the wire and METRICS holds the derive lambdas, so
# what ships is the description of each metric, not its implementation.
METRICS_PUBLIC: Dict[str, Dict[str, Any]] = {
    k: {'label': v['label'], 'format': v['format'], 'unit': v['unit'],
        'note': v.get('note', '')}
    for k, v in METRICS.items()
}


def values(metric: str) -> Dict[str, float]:
    """One metric as ``{neighbourhood_code: value}``."""
    spec = METRICS.get(metric)
    if not spec:
        raise KeyError(f'unknown housing metric {metric!r}; known: {sorted(METRICS)}')
    prof = profile()
    derive: Optional[Callable] = spec.get('derive')
    return derive(prof) if derive else dict(_row(prof, spec['row']))


def choropleth(metric: str = DEFAULT_METRIC) -> dict:
    """
    A housing-price choropleth: every metric attached to every neighbourhood.

    All of them travel on each feature, not just the one being coloured, so the
    inspector can show a neighbourhood's whole housing picture on one click and
    the client can recolour without a refetch — the same contract the crime
    choropleth keeps.
    """
    from . import layers as L                      # boundary loaders live there

    if metric not in METRICS:
        raise KeyError(f'unknown housing metric {metric!r}; known: {sorted(METRICS)}')

    prof = profile()
    columns = {mid: (spec['derive'](prof) if spec.get('derive')
                     else dict(_row(prof, spec['row'])))
               for mid, spec in METRICS.items()}

    feats = []
    for f in L.neighbourhoods()['features']:
        p = f['properties']
        code = str(p.get('AREA_LONG_CODE') or '').lstrip('0') or '0'
        props: Dict[str, Any] = {
            'area': code,
            'name': re.sub(r'\s*\(\d+\)$', '', str(p.get('AREA_NAME') or '')),
        }
        for mid, col in columns.items():
            props[mid] = col.get(code)
        feats.append({'type': 'Feature', 'geometry': f['geometry'], 'properties': props})

    fc = {'type': 'FeatureCollection', 'features': feats}
    fc['breaks'] = breaks(feats, metric)
    got = sum(1 for f in feats if f['properties'].get(metric) is not None)
    fc['meta'] = {
        'metric': metric,
        'metric_label': METRICS[metric]['label'],
        'format': METRICS[metric]['format'],
        'geography': 'neighbourhood',
        'areas': len(feats),
        'areas_with_data': got,
        'source': '2021 Census neighbourhood profiles (City of Toronto)',
    }
    return fc


def breaks(feats: List[dict], metric: str) -> dict:
    """Quantile class breaks, rounded to something a legend can print."""
    vals = sorted(v for v in (f['properties'].get(metric) for f in feats)
                  if isinstance(v, (int, float)))
    if not vals:
        return {'metric': metric, 'stops': [], 'min': None, 'max': None}
    money = METRICS[metric]['format'] == 'money'
    stops: List[float] = []
    for i in range(7):
        v = vals[min(int(i * len(vals) / 7), len(vals) - 1)]
        v = round(v / 1000) * 1000 if money and v >= 10_000 else round(v, 1)
        if not stops or v > stops[-1]:
            stops.append(v)
    return {'metric': metric, 'stops': stops, 'min': vals[0], 'max': vals[-1],
            'count': len(vals), 'diverging': False}


def options() -> dict:
    """What the housing controls are built from."""
    return {
        'metrics': {k: {'label': v['label'], 'format': v['format'],
                        'unit': v['unit'], 'note': v.get('note', '')}
                    for k, v in METRICS.items()},
        'default': DEFAULT_METRIC,
        'geography': 'neighbourhood',
        'source': {'name': '2021 Census neighbourhood profiles',
                   'dataset': PACKAGE,
                   'url': f'https://open.toronto.ca/dataset/{PACKAGE}/'},
    }


def summary(metric: str = DEFAULT_METRIC, top: int = 8) -> dict:
    """The dearest and cheapest neighbourhoods on one metric."""
    fc = choropleth(metric)
    rows = [{'area': f['properties']['area'], 'name': f['properties']['name'],
             'value': f['properties'][metric]}
            for f in fc['features'] if f['properties'].get(metric) is not None]
    rows.sort(key=lambda r: r['value'], reverse=True)
    vals = sorted(r['value'] for r in rows)
    return {
        'metric': metric,
        'label': METRICS[metric]['label'],
        'format': METRICS[metric]['format'],
        'unit': METRICS[metric]['unit'],
        'city_median': vals[len(vals) // 2] if vals else None,
        'highest': rows[:top],
        'lowest': rows[-top:][::-1],
        'areas': len(rows),
        'source': fc['meta']['source'],
    }
