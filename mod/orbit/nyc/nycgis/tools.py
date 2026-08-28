"""
nyc.tools — the single tool registry over NYC open data.

Every surface (MCP stdio server, MCP-over-HTTP, JSON API, the in-app chat
agent, docs) is generated from this one table, so a tool added here appears
everywhere at once.

Two kinds of tool live side by side:

* curated tools over this module's own engine — the housing-price choropleth,
  price trends, individual sales, the map layers, geocoding, borough facts;
* portal-wide tools (`nyc_find_datasets`, `nyc_dataset`, `nyc_query`) that
  reach *every* dataset on NYC Open Data and NY State Open Data through
  Socrata's public Discovery and SoQL APIs — thousands of datasets, not just
  the twelve the map draws. No API keys anywhere, same as the rest of the
  module.

Results are shaped for a language model, not a map: tables of properties
rather than GeoJSON, ranked and capped, with the source dataset named so an
answer can cite it.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import layers as L
from . import prices as P
from . import sources as S

DISCOVERY = 'https://api.us.socrata.com/api/catalog/v1'
DOMAINS = {'nyc': 'data.cityofnewyork.us', 'nys': 'data.ny.gov'}

_lock = threading.Lock()
_nyc = None


def _protocol():
    """
    The mod protocol package. When this runs from inside the module directory
    (the stdio MCP server does), ``import mod`` finds our own anchor mod.py
    instead of the protocol package — so drop the shadowing paths and retry.
    """
    m = sys.modules.get('mod')
    if m is not None and hasattr(m, 'mod'):
        return m
    import importlib
    here = str(Path(__file__).resolve().parent.parent)
    sys.path[:] = [p for p in sys.path if str(Path(p or '.').resolve()) != here]
    sys.modules.pop('mod', None)
    return importlib.import_module('mod')


def get_nyc():
    """Cached instance of the nyc Mod (the module's own engine)."""
    global _nyc
    with _lock:
        if _nyc is None:
            _nyc = _protocol().mod('nyc')()
        return _nyc


def _domain(name: str) -> str:
    key = str(name or 'nyc').strip().lower()
    return DOMAINS.get(key, key or DOMAINS['nyc'])


# ─────────────────────────────────────────────────────────────────────────────
# result shaping — tool output goes into a model's context, so it must be
# compact, ranked and self-describing rather than a raw FeatureCollection
# ─────────────────────────────────────────────────────────────────────────────

def _props_table(fc: dict, limit: int, search: str = '') -> dict:
    """A FeatureCollection as a table of properties (+ point coords)."""
    rows = []
    needle = str(search or '').strip().lower()
    for f in fc.get('features', []):
        p = dict(f.get('properties') or {})
        if needle and needle not in str(p).lower():
            continue
        g = f.get('geometry') or {}
        if g.get('type') == 'Point':
            p['lng'], p['lat'] = g.get('coordinates', [None, None])[:2]
        rows.append(p)
        if len(rows) >= max(1, int(limit)):
            break
    return {'total_features': len(fc.get('features', [])),
            'returned': len(rows),
            'rows': rows}


def _housing_table(metric: str = 'median_price', geography: str = 'nta',
                   since: str = '2024-01-01', until: Optional[str] = None,
                   property_type: str = 'residential', top: int = 15,
                   bottom: int = 5) -> dict:
    fc = get_nyc().housing(metric=metric, geography=geography, since=since,
                           until=until, property_type=property_type)
    if fc.get('error'):
        return fc
    cols = ['area', 'name', 'borough', 'sales', 'median_price', 'median_ppsf',
            'avg_price', 'total_value', 'price_change']
    rows = [{k: f['properties'].get(k) for k in cols}
            for f in fc['features'] if f['properties'].get(metric) is not None]
    rows.sort(key=lambda r: r[metric], reverse=True)
    top, bottom = max(0, int(top)), max(0, int(bottom))
    out = {'meta': fc['meta'], 'metric': metric,
           'metric_label': P.METRICS[metric]['label'],
           'areas_ranked': len(rows), 'top': rows[:top]}
    if bottom and len(rows) > top:
        out['bottom'] = rows[-bottom:]
    return out


def _layer_table(id: str, limit: int = 25, search: str = '') -> dict:
    fc = get_nyc().layer(id)
    meta = next((l for l in L.LAYERS if l['id'] == id), {})
    out = _props_table(fc, limit, search)
    out['layer'] = {'id': id, 'title': meta.get('title', id),
                    'description': meta.get('description', ''),
                    'source': meta.get('source', {})}
    return out


def _layers_compact() -> dict:
    cat = L.catalog()
    return {'count': cat['count'],
            'layers': [{'id': l['id'], 'title': l['title'],
                        'category': l['category'], 'kind': l['kind'],
                        'description': l['description'],
                        'source': f"{l['source']['name']} ({l['source']['dataset']})"}
                       for l in cat['layers']],
            'attribution': cat['attribution']}


def _sales_table(since: str = '2025-01-01', until: Optional[str] = None,
                 property_type: str = 'residential', limit: int = 50,
                 min_price: Optional[int] = None,
                 max_price: Optional[int] = None, search: str = '') -> dict:
    # Fetch more than we return so a `search` filter has something to bite on.
    fc = get_nyc().sales(since=since, until=until, property_type=property_type,
                         limit=max(2000, int(limit)), min_price=min_price,
                         max_price=max_price)
    out = _props_table(fc, limit, search)
    out['source'] = 'NYC DOF Citywide Rolling Sales (w2pb-icbu)'
    return out


# ─────────────────────────────────────────────────────────────────────────────
# the whole portal: Socrata Discovery + metadata + SoQL
# ─────────────────────────────────────────────────────────────────────────────

def find_datasets(q: str, domain: str = 'nyc', limit: int = 15) -> dict:
    """Search the full open-data catalogue for datasets matching ``q``."""
    dom = _domain(domain)
    limit = max(1, min(int(limit), 50))

    def fetch():
        import requests
        r = requests.get(DISCOVERY, params={
            'domains': dom, 'search_context': dom, 'q': str(q),
            'only': 'dataset', 'limit': limit},
            headers={'User-Agent': S.USER_AGENT}, timeout=30)
        r.raise_for_status()
        hits = []
        for h in r.json().get('results', []):
            res = h.get('resource') or {}
            cls = h.get('classification') or {}
            hits.append({
                'id': res.get('id'),
                'name': res.get('name'),
                'description': (res.get('description') or '')[:400],
                'updated': str(res.get('data_updated_at') or '')[:10],
                'rows': res.get('rows_size') or None,
                'category': cls.get('domain_category') or '',
                'url': f'https://{dom}/d/{res.get("id")}',
            })
        return {'domain': dom, 'query': str(q), 'count': len(hits),
                'datasets': hits}

    return S.cached(f'catalog-{dom}-{str(q).lower()}-{limit}', S.DAY, fetch)


def dataset_meta(id: str, domain: str = 'nyc') -> dict:
    """Metadata + columns for one dataset, so a SoQL query can be written."""
    dom = _domain(domain)

    def fetch():
        import requests
        r = requests.get(f'https://{dom}/api/views/{id}.json',
                         headers={'User-Agent': S.USER_AGENT}, timeout=30)
        r.raise_for_status()
        v = r.json()
        return {
            'id': id, 'domain': dom, 'name': v.get('name'),
            'description': (v.get('description') or '')[:600],
            'rows': (v.get('columns') or [{}])[0].get('cachedContents', {}).get('count'),
            'category': v.get('category') or '',
            'url': f'https://{dom}/d/{id}',
            'columns': [{'field': c.get('fieldName'), 'name': c.get('name'),
                         'type': c.get('dataTypeName')}
                        for c in v.get('columns', [])
                        if not str(c.get('fieldName', '')).startswith(':')],
        }

    return S.cached(f'catalog-meta-{dom}-{id}', 7 * S.DAY, fetch)


def query_dataset(id: str, select: Optional[str] = None,
                  where: Optional[str] = None, group: Optional[str] = None,
                  order: Optional[str] = None, limit: int = 100,
                  domain: str = 'nyc') -> dict:
    """Run a SoQL query against any dataset on the portal."""
    dom = _domain(domain)
    limit = max(1, min(int(limit), 1000))
    rows = S.soql(f'https://{dom}', str(id), select=select, where=where,
                  group=group, order=order, limit=limit)
    return {'dataset': id, 'domain': dom, 'returned': len(rows),
            'limit': limit, 'url': f'https://{dom}/d/{id}', 'rows': rows}


# ─────────────────────────────────────────────────────────────────────────────
# registry
# ─────────────────────────────────────────────────────────────────────────────

class Tool:
    def __init__(self, name: str, description: str, group: str,
                 params: Dict[str, Dict], handler: Callable[..., Any]):
        self.name = name
        self.description = description
        self.group = group
        self.params = params            # name -> {type, description, default?, required?}
        self.handler = handler

    @property
    def input_schema(self) -> Dict:
        props, required = {}, []
        for pname, p in self.params.items():
            prop = {'type': p['type'], 'description': p['description']}
            if 'default' in p:
                prop['default'] = p['default']
            props[pname] = prop
            if p.get('required'):
                required.append(pname)
        schema: Dict[str, Any] = {'type': 'object', 'properties': props}
        if required:
            schema['required'] = required
        return schema

    def call(self, args: Optional[Dict] = None) -> Any:
        args = dict(args or {})
        known = set(self.params)
        unknown = set(args) - known
        if unknown:
            raise ValueError(f'unknown argument(s) {sorted(unknown)}; '
                             f'known: {sorted(known)}')
        missing = [p for p, spec in self.params.items()
                   if spec.get('required') and p not in args]
        if missing:
            raise ValueError(f'missing required argument(s): {missing}')
        return self.handler(**args)


def _p(type_: str, desc: str, default: Any = None, required: bool = False) -> dict:
    out: Dict[str, Any] = {'type': type_, 'description': desc}
    if required:
        out['required'] = True
    elif default is not None:
        out['default'] = default
    return out


_WINDOW = {
    'since': _p('string', 'Start date, YYYY-MM-DD', '2024-01-01'),
    'until': _p('string', 'End date, YYYY-MM-DD (default: today)'),
    'property_type': _p('string',
                        f'One of: {", ".join(P.PROPERTY_TYPES)}', 'residential'),
}

TOOLS: List[Tool] = [
    # ── the city ─────────────────────────────────────────────────────────
    Tool('nyc_info',
         'Overview of the NYC atlas: layers, housing metrics, data sources.',
         'city', {}, lambda: get_nyc().info()),
    Tool('nyc_boroughs', 'The five boroughs with population and area.',
         'city', {}, lambda: get_nyc().boroughs()),
    Tool('nyc_borough', 'Facts about one borough.',
         'city', {'name': _p('string', 'Borough name or slug', required=True)},
         lambda name: get_nyc().borough(name)),
    Tool('nyc_where',
         'Geocode an NYC address or place name to coordinates (Nominatim).',
         'city', {'q': _p('string', 'Address or place', required=True),
                  'limit': _p('integer', 'Max results', 6)},
         lambda q, limit=6: get_nyc().where(q, limit=limit)),

    # ── map layers ───────────────────────────────────────────────────────
    Tool('nyc_layers',
         'The map layer catalogue: subway, bike network, parks, evacuation '
         'zones, traffic injuries, affordable housing, boundaries.',
         'layers', {}, _layers_compact),
    Tool('nyc_layer',
         'Rows from one map layer (feature properties, plus lat/lng for '
         'points). Use `search` to filter, e.g. a street or park name.',
         'layers',
         {'id': _p('string', 'Layer id from nyc_layers', required=True),
          'limit': _p('integer', 'Max rows returned', 25),
          'search': _p('string', 'Case-insensitive substring filter')},
         _layer_table),

    # ── housing prices ───────────────────────────────────────────────────
    Tool('nyc_housing',
         'Housing prices ranked by area from ~845k recorded deeds '
         '(2016–present). Returns the top and bottom areas for a metric.',
         'housing',
         {'metric': _p('string', f'One of: {", ".join(P.METRICS)}', 'median_price'),
          'geography': _p('string', f'One of: {", ".join(P.GEOGRAPHIES)}', 'nta'),
          **_WINDOW,
          'top': _p('integer', 'How many top areas', 15),
          'bottom': _p('integer', 'How many bottom areas', 5)},
         _housing_table),
    Tool('nyc_prices',
         'City-wide price summary: totals, most/least expensive '
         'neighborhoods, fastest rising and falling.',
         'housing', dict(_WINDOW),
         lambda since='2024-01-01', until=None, property_type='residential':
             get_nyc().prices(since=since, until=until, property_type=property_type)),
    Tool('nyc_trend',
         'Yearly median price and $/ft² since 2016 — city-wide or one area.',
         'housing',
         {'area': _p('string', 'Area code (e.g. NTA "BK0101") — omit for city-wide'),
          'geography': _p('string', f'One of: {", ".join(P.GEOGRAPHIES)}', 'nta'),
          'property_type': _p('string', f'One of: {", ".join(P.PROPERTY_TYPES)}',
                              'residential')},
         lambda area=None, geography='nta', property_type='residential':
             get_nyc().trend(area=area, geography=geography,
                             property_type=property_type)),
    Tool('nyc_sales',
         'Individual recorded sales (address, price, date, building class).',
         'housing',
         {**_WINDOW,
          'since': _p('string', 'Start date, YYYY-MM-DD', '2025-01-01'),
          'limit': _p('integer', 'Max rows returned', 50),
          'min_price': _p('integer', 'Minimum sale price'),
          'max_price': _p('integer', 'Maximum sale price'),
          'search': _p('string', 'Filter by address/neighborhood substring')},
         _sales_table),

    # ── affordable homes ─────────────────────────────────────────────────
    Tool('nyc_rents',
         'What affordable housing costs to rent in NYC: median, lowest and '
         'highest rent by bedroom size, income band and borough, from the '
         "city's Local Law 44 rent roll.",
         'housing', {}, lambda: get_nyc().rents()),
    Tool('nyc_homes',
         'Affordable rentals somebody could apply for, cheapest first — '
         'address, rent, bedroom size and the income limit to qualify. '
         'Filter by budget, bedrooms, borough or name.',
         'housing',
         {'max_rent': _p('integer', 'Most you can pay per month'),
          'bedrooms': _p('string', 'Bedrooms needed: "studio", "1", "2", "3"…'),
          'borough': _p('string', 'Borough name'),
          'search': _p('string', 'Filter by building name, address or ZIP'),
          'limit': _p('integer', 'Max rows returned', 100)},
         lambda max_rent=None, bedrooms='', borough='', search='', limit=100:
             get_nyc().homes(max_rent=max_rent, bedrooms=bedrooms,
                             borough=borough, search=search, limit=limit)),
    Tool('nyc_affordable',
         'How many affordable units NYC has financed, by income band and '
         'borough — including the units HPD publishes with the address '
         'redacted, which the map cannot draw.',
         'housing', {}, lambda: get_nyc().affordable()),

    # ── the whole open-data portal ───────────────────────────────────────
    Tool('nyc_find_datasets',
         'Search ALL of NYC Open Data (or NY State) for datasets on any '
         'topic — crime, 311, schools, health, budgets, permits, anything. '
         'Returns dataset ids for nyc_dataset / nyc_query.',
         'open_data',
         {'q': _p('string', 'Topic to search for', required=True),
          'domain': _p('string', '"nyc" (city) or "nys" (state/MTA)', 'nyc'),
          'limit': _p('integer', 'Max datasets', 15)},
         find_datasets),
    Tool('nyc_dataset',
         'Columns and metadata for one dataset — read this before nyc_query.',
         'open_data',
         {'id': _p('string', 'Dataset id, e.g. "erm2-nwe9"', required=True),
          'domain': _p('string', '"nyc" or "nys"', 'nyc')},
         dataset_meta),
    Tool('nyc_query',
         'Run a SoQL query against ANY dataset on the portal (SELECT with '
         'aggregates, WHERE, GROUP BY, ORDER BY). This reaches every row of '
         'every public dataset.',
         'open_data',
         {'id': _p('string', 'Dataset id', required=True),
          'select': _p('string', 'SoQL $select, e.g. "borough, count(*)"'),
          'where': _p('string', 'SoQL $where, e.g. "created_date > \'2026-01-01\'"'),
          'group': _p('string', 'SoQL $group'),
          'order': _p('string', 'SoQL $order, e.g. "count DESC"'),
          'limit': _p('integer', 'Max rows (≤1000)', 100),
          'domain': _p('string', '"nyc" or "nys"', 'nyc')},
         query_dataset),
]

_BY_NAME = {t.name: t for t in TOOLS}


def list_tools() -> List[Dict]:
    """MCP-shaped tool list."""
    return [{'name': t.name, 'description': t.description,
             'inputSchema': t.input_schema} for t in TOOLS]


def call_tool(name: str, args: Optional[Dict] = None) -> Any:
    tool = _BY_NAME.get(str(name))
    if not tool:
        raise KeyError(f'unknown tool {name!r}; known: {sorted(_BY_NAME)}')
    return tool.call(args)


def groups() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for t in TOOLS:
        out.setdefault(t.group, []).append(t.name)
    return out
