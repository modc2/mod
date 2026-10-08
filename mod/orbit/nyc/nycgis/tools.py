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

from . import catalog as CAT
from . import citydata as CD
from . import housing as HG
from . import layers as L
from . import prices as P
from . import scene as SC
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
    """
    Search the full open-data catalogue for datasets matching ``q``.

    If the full catalog has been harvested to disk (``nyc_catalog``,
    ``nycgis.catalog``), the search runs locally over the WHOLE corpus —
    fast, offline-capable, and ranked so name hits beat description hits.
    With no harvest on disk it falls back to the live Discovery API rather
    than blocking a first search on a ~35s crawl.
    """
    dom = _domain(domain)
    limit = max(1, min(int(limit), 50))

    local = CAT.search_local(q, dom, limit=limit)
    if local is not None:
        return local

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


def catalog_tool(domain: str = 'nyc', category: str = '',
                 refresh: bool = False, limit: int = 25) -> dict:
    """Catalog stats, or one category browsed; refresh re-harvests first."""
    if refresh:
        CAT.harvest(domain, refresh=True)
    if str(category or '').strip():
        return CAT.browse(domain, category, limit)
    return CAT.stats(domain)


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


# ── the owner's saved datasets ───────────────────────────────────────────────

def _data_tool(slug: str = '') -> dict:
    from . import userdata as U
    if not slug:
        out = U.list_()
        out['note'] = ('Each dataset is a map layer; toggle one with nyc_map '
                       'add=["<slug>"]. The owner saves new ones with '
                       'nyc_add_data.')
        return out
    record = U.info(slug)
    fc = U.data(slug)
    return {**record, 'features': len(fc.get('features', [])),
            'meta': fc.get('meta')}


def _add_data_tool(**kw) -> dict:
    from . import userdata as U
    record = U.add(kw)
    out = {'saved': record,
           'note': (f'"{record["title"]}" is now a layer in the rail '
                    f'(category "Your data"). Show it with nyc_map '
                    f'add=["{record["slug"]}"].')}
    if record['kind'] == 'overlay':
        meta = U.data(record['slug']).get('meta', {})
        for k in ('rows', 'plotted', 'top', 'bottom', 'unit', 'capped'):
            if meta.get(k) is not None:
                out[k] = meta[k]
    return out


def _remove_data_tool(slug: str) -> dict:
    from . import userdata as U
    return U.remove(slug)


# ─────────────────────────────────────────────────────────────────────────────
# registry
# ─────────────────────────────────────────────────────────────────────────────

# Human titles for the tool list. Most read fine derived from the name
# (`nyc_find_datasets` → "Find datasets"); these are the ones that don't.
TITLES = {
    'nyc_info': 'About this atlas',
    'nyc_where': 'Geocode a place',
    'nyc_layer': 'Read a map layer',
    'nyc_layers': 'List map layers',
    'nyc_housing': 'Housing prices by area',
    'nyc_prices': 'Citywide price summary',
    'nyc_trend': 'Price history by year',
    'nyc_sales': 'Individual recorded sales',
    'nyc_rents': 'Affordable rents',
    'nyc_homes': 'Find affordable homes',
    'nyc_lotteries': 'Open housing lotteries',
    'nyc_building_check': 'Check a building first',
    'nyc_violations': 'Housing violations',
    'nyc_nycha': 'Public housing (NYCHA)',
    'nyc_affordable': 'Affordable housing built',
    'nyc_traffic': 'Traffic speeds and when to drive',
    'nyc_crime': 'Crime and shootings',
    'nyc_collisions': 'Vehicle collisions and injuries',
    'nyc_311': '311 complaints',
    'nyc_restaurants': 'Restaurant inspections and grades',
    'nyc_trees': 'Street trees',
    'nyc_air': 'Air quality by neighborhood',
    'nyc_evictions': 'Marshal evictions',
    'nyc_permits': 'Building permits',
    'nyc_news': 'NYC news right now',
    'nyc_market': 'The listing market (asking prices and rents)',
    'nyc_catalog': 'The whole data catalog',
    'nyc_dataset': 'Describe a dataset',
    'nyc_query': 'Query any dataset (SoQL)',
    'nyc_map': 'Change the map on screen',
    'nyc_infographic': 'Pin an infographic card',
    'nyc_data': 'Your saved datasets',
    'nyc_add_data': 'Save a dataset as a layer (owner)',
    'nyc_remove_data': 'Remove a saved dataset (owner)',
}


class Tool:
    def __init__(self, name: str, description: str, group: str,
                 params: Dict[str, Dict], handler: Callable[..., Any],
                 write: bool = False, destructive: bool = False):
        self.name = name
        self.description = description
        self.group = group
        self.params = params            # name -> {type, description, default?, required?}
        self.handler = handler
        self.write = write              # persists state; owner-gated at the call site
        self.destructive = destructive

    @property
    def title(self) -> str:
        if self.name in TITLES:
            return TITLES[self.name]
        stem = self.name[4:] if self.name.startswith('nyc_') else self.name
        return stem.replace('_', ' ').capitalize()

    @property
    def annotations(self) -> Dict[str, Any]:
        """
        MCP tool annotations. Almost every tool here reads public open data
        over HTTP and writes nothing, anywhere — idempotent and open-world
        (the answer depends on what the city published today, not on anything
        this process holds). The exceptions are the owner's data tools
        (nyc_add_data / nyc_remove_data), which persist state and say so.
        """
        return {'title': self.title, 'readOnlyHint': not self.write,
                'destructiveHint': self.destructive,
                'idempotentHint': not self.write,
                'openWorldHint': True}

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
         'zones, live traffic speeds, traffic volume, traffic injuries, '
         'affordable housing, boundaries.',
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

    # ── tenants: lotteries, building checks, violations, NYCHA ──────────
    Tool('nyc_lotteries',
         'Affordable-housing lotteries on Housing Connect that are open '
         'for applications right now: deadlines (soonest first), unit mix '
         'by bedroom count, which income bands (% of AMI) qualify, '
         'senior/NYCHA/mobility set-asides, and where. Pass lottery_id to '
         'get the addresses behind one lottery. Applying is free at '
         'housingconnect.nyc.gov and timing within the window does not '
         'matter.',
         'housing',
         {'borough': _p('string', 'Borough name, e.g. "brooklyn"'),
          'status': _p('string', 'active (default), closed, tenant '
                       'selection, filled or all', 'active'),
          'lottery_id': _p('string', 'One lottery id → its buildings'),
          'limit': _p('integer', 'Max lotteries returned', 50)},
         lambda borough='', status='active', lottery_id='', limit=50:
             (HG.lottery_buildings(lottery_id) if lottery_id else
              HG.lotteries(borough=borough, status=status,
                           limit=int(limit)))),
    Tool('nyc_building_check',
         'Tenant due diligence on ONE address: open HPD housing-'
         'maintenance violations by class (C = immediately hazardous — no '
         'heat, lead, vermin), the problems tenants reported in the last '
         'two years, and HPD litigation against the landlord. Use it '
         'before signing a lease.',
         'housing',
         {'address': _p('string',
                        'House number then street, e.g. "760 Eldert Lane"',
                        required=True),
          'borough': _p('string', 'Borough (narrows same-named streets)')},
         lambda address='', borough='': HG.building(address,
                                                    borough=borough)),
    Tool('nyc_violations',
         'Housing conditions citywide or per borough: open HPD violations '
         'by severity class, the borough breakdown, and the buildings '
         'with the most open immediately-hazardous (class C) violations.',
         'housing',
         {'borough': _p('string', 'Borough name'),
          'limit': _p('integer', 'Worst buildings returned', 15)},
         lambda borough='', limit=15:
             HG.violations(borough=borough, limit=int(limit))),
    Tool('nyc_nycha',
         'Public housing: NYCHA developments, apartments, resident '
         'population and average rent by borough, plus the largest '
         'developments.',
         'housing',
         {'borough': _p('string', 'Borough name'),
          'limit': _p('integer', 'Largest developments returned', 15)},
         lambda borough='', limit=15:
             HG.nycha(borough=borough, limit=int(limit))),

    # ── people ───────────────────────────────────────────────────────────
    Tool('nyc_population',
         'Population and housing statistics for NYC per borough, neighborhood '
         '(NTA) or census tract: population, density (people/sq mi), median '
         'household income, median rent, rent burden (30%+/50%+), renter share, '
         'vacancy, poverty, median home sale, price-to-income, new homes since '
         '2020 and the construction pipeline. Census ACS 5-year + DCP + DOF. '
         'Sort by any field to rank areas.',
         'people',
         {'geography': _p('string', 'borough, nta or tract', 'borough'),
          'sort': _p('string', 'Field to rank by, descending (e.g. density, '
                     'rent_burden_pct, new_units_per_1k)'),
          'limit': _p('integer', 'Max areas returned (0 = all)', 25),
          'since': _p('string', 'Sales window start, YYYY-MM-DD', '2025-01-01')},
         lambda geography='borough', sort='', limit=25, since='2025-01-01':
             get_nyc().stats(geography=geography, since=since, sort=sort,
                             limit=limit)),

    # ── traffic ──────────────────────────────────────────────────────────
    Tool('nyc_traffic',
         'When to drive in NYC. Returns the hour-by-hour traffic profile of '
         "DOT's count locations — busiest hour, calmest hour, and the quiet "
         'hours worth leaving in — plus what the live speed sensors are '
         'reading on the highways right now. Filter by street or borough.',
         'traffic',
         {'street': _p('string', 'Street name to match, e.g. "Cross Bronx"'),
          'borough': _p('string', 'Borough name'),
          'hour': _p('integer', 'Hour 0-23 to report each location at'),
          'limit': _p('integer', 'Max count locations returned', 20)},
         lambda street='', borough='', hour=None, limit=20:
             get_nyc().traffic(street=street, borough=borough,
                               hour=hour, limit=limit)),

    # ── safety ───────────────────────────────────────────────────────────
    Tool('nyc_crime',
         'Public safety from NYPD open data: complaints and shootings this '
         'year vs the same window last year, by borough and precinct, top '
         'offense types, and a 3-year monthly trend. `part`: summary '
         '(default), boroughs, offenses, trend, or precincts (GeoJSON — '
         'large; prefer the others for answering questions). OR pass any of '
         'offense / borough / severity / since / until / group_by for a '
         'custom count instead (e.g. offense="robbery", borough="brooklyn", '
         'since="2023-01-01", group_by="borough") — the window is stitched '
         'across the historic and current-year files automatically.',
         'safety',
         {'part': _p('string', 'summary, boroughs, offenses, trend or precincts',
                     'summary'),
          'limit': _p('integer', 'Offense types / groups returned', 15),
          'offense': _p('string', 'Offense substring, e.g. "robbery", "assault"'),
          'borough': _p('string', 'Borough name, e.g. "brooklyn"'),
          'severity': _p('string', 'felony, misdemeanor or violation'),
          'since': _p('string', 'Window start, YYYY-MM-DD (custom count)'),
          'until': _p('string', 'Window end, YYYY-MM-DD (custom count)'),
          'group_by': _p('string', 'offense, borough, severity or precinct')},
         lambda part='summary', limit=15, offense='', borough='', severity='',
                since='', until='', group_by='':
             (CD.crime(offense=offense, borough=borough, severity=severity,
                       since=since, until=until,
                       group_by=group_by or 'offense', limit=limit)
              if (offense or borough or severity or since or until or group_by)
              else get_nyc().crime(part=part, limit=limit))),
    Tool('nyc_collisions',
         'Motor-vehicle crashes, injuries and deaths (NYPD collision file, '
         '~2.3M crashes since 2012, citywide ~150 reported crashes/day). Counts by '
         'borough, year, or street — group_by="street" is the worst-streets '
         'ranking, ordered by people injured. Window defaults to the last '
         '365 days. Example: borough="queens", group_by="street", limit=10.',
         'safety',
         {'borough': _p('string', 'Borough name'),
          'street': _p('string', 'Street-name substring, e.g. "atlantic av"'),
          'since': _p('string', 'Window start, YYYY-MM-DD (default 1 year ago)'),
          'until': _p('string', 'Window end, YYYY-MM-DD'),
          'group_by': _p('string', 'borough, street or year', 'borough'),
          'limit': _p('integer', 'Max groups returned', 25)},
         CD.collisions),

    # ── city life: 311, restaurants, trees, air ─────────────────────────
    Tool('nyc_311',
         '311 service requests (noise, heat, rats, parking…, ~39M rows, '
         'tens of thousands of new complaints a day). Counts grouped by '
         'complaint type, borough, ZIP, agency or status over a date window '
         '(default: last 30 days), or recent=true for the newest matching '
         'individual complaints (≤50). Examples: complaint="noise" '
         'group_by="zip"; complaint="rodent" borough="bronx" recent=true.',
         'city_life',
         {'complaint': _p('string', 'Complaint-type substring, e.g. "noise", "rodent"'),
          'borough': _p('string', 'Borough name'),
          'since': _p('string', 'Window start, YYYY-MM-DD (default 30 days ago)'),
          'until': _p('string', 'Window end, YYYY-MM-DD'),
          'group_by': _p('string', 'type, borough, zip, agency or status', 'type'),
          'limit': _p('integer', 'Max groups (or complaints if recent)', 25),
          'recent': _p('boolean', 'List newest individual complaints instead')},
         CD.complaints_311),
    Tool('nyc_restaurants',
         'DOHMH restaurant inspections. Search by name / cuisine / borough — '
         'one row per restaurant with its LATEST grade, score (lower is '
         'better: 0-13=A) and inspection date. mode="grades" returns the '
         'grade distribution (distinct restaurants) for the same filters. '
         'Example: cuisine="pizza", borough="brooklyn", grade="A".',
         'city_life',
         {'name': _p('string', 'Restaurant-name substring'),
          'cuisine': _p('string', 'Cuisine substring, e.g. "pizza", "thai"'),
          'borough': _p('string', 'Borough name'),
          'grade': _p('string', 'A, B, C, N, Z or P'),
          'mode': _p('string', '"search" (restaurants) or "grades" (distribution)',
                     'search'),
          'limit': _p('integer', 'Max restaurants returned', 20)},
         CD.restaurants),
    Tool('nyc_trees',
         'NYC street trees: species, health and borough counts from the '
         '2015 Street Tree Census (683,788 trees — the last file with '
         'common names + health + borough per tree), or source="living" '
         'for the Parks living inventory (1.1M points, updated daily, '
         'Latin names + condition only). Examples: group_by="species" '
         'borough="queens"; species="oak" group_by="health".',
         'city_life',
         {'species': _p('string', 'Species substring, e.g. "oak", "london planetree"'),
          'borough': _p('string', 'Borough name (census source only)'),
          'group_by': _p('string', 'species, health, borough or status', 'species'),
          'source': _p('string', '"census" (2015, rich) or "living" (current, coarse)',
                       'census'),
          'limit': _p('integer', 'Max groups returned', 25)},
         CD.trees),
    Tool('nyc_air',
         'DOHMH neighborhood air quality. With no arguments: every available '
         'measure (PM 2.5, ozone, NO2, benzene, asthma attributable to PM…). '
         'With measure="PM 2.5": the latest surveillance period\'s value for '
         'every neighborhood, worst first. place filters to one area.',
         'city_life',
         {'measure': _p('string', 'Measure substring, e.g. "PM 2.5", "ozone"'),
          'place': _p('string', 'Neighborhood/borough substring, e.g. "bushwick"'),
          'limit': _p('integer', 'Max places returned', 50)},
         CD.air),

    # ── news ─────────────────────────────────────────────────────────────
    Tool('nyc_news',
         'New York City news right now, from key-free newsroom feeds '
         '(Gothamist, THE CITY, NYT Metro), newest first with topic tags. '
         'Pass `topic` to filter (housing, crime, transit, government) or '
         '`q` to search wider coverage of any NYC subject (GDELT).',
         'news',
         {'topic': _p('string', 'housing, crime, transit or government'),
          'q': _p('string', 'Search query (uses GDELT instead of the feeds)'),
          'limit': _p('integer', 'Max stories', 25)},
         lambda topic='', q='', limit=25:
             get_nyc().news(topic=topic, q=q, limit=limit)),

    # ── the listing market ───────────────────────────────────────────────
    Tool('nyc_market',
         'The real-estate listing market (what is ASKED, vs nyc_prices which '
         'is what SOLD): median asking rent, asking price and rental '
         'inventory with year-over-year change for the city and each '
         'borough, the neighborhoods where rents are moving fastest, and '
         'Zillow\'s NY-metro home-value and rent indices. Pass `area` for '
         'one neighborhood\'s levels and 10-year monthly history.',
         'housing',
         {'area': _p('string', 'Neighborhood name, e.g. "Astoria"')},
         lambda area='': get_nyc().market(area=area)),

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
    Tool('nyc_evictions',
         'Evictions executed by city marshals (2017–present, ~135k), by '
         'year or borough, each row split residential vs commercial. '
         'kind="residential" narrows to homes. Example: borough="bronx" '
         'group_by="year" since="2022-01-01".',
         'housing',
         {'borough': _p('string', 'Borough name'),
          'since': _p('string', 'Window start, YYYY-MM-DD (file starts 2017)'),
          'until': _p('string', 'Window end, YYYY-MM-DD'),
          'group_by': _p('string', '"year" or "borough"', 'year'),
          'kind': _p('string', '"residential" or "commercial"')},
         CD.evictions),
    Tool('nyc_permits',
         'Construction permits from DOB NOW (the live permitting system, '
         '~2016-present, ~160k permits/year). Counts by year, borough, '
         'work type (plumbing, electrical, general construction…) or '
         'busiest street. Example: borough="manhattan" group_by="type" '
         'since="2026-01-01".',
         'housing',
         {'borough': _p('string', 'Borough name'),
          'work_type': _p('string', 'Work-type substring, e.g. "plumbing"'),
          'since': _p('string', 'Window start, YYYY-MM-DD'),
          'until': _p('string', 'Window end, YYYY-MM-DD'),
          'group_by': _p('string', 'year, borough, type or street', 'year'),
          'limit': _p('integer', 'Max groups returned', 25)},
         CD.permits),

    # ── the whole open-data portal ───────────────────────────────────────
    Tool('nyc_catalog',
         'The harvested catalog of EVERY dataset on NYC Open Data (~2,400) '
         'or NY State Open Data (~1,000): counts by category and freshness, '
         'or pass category="Health" to browse one category newest-first. '
         'refresh=true re-harvests the whole catalog from the Discovery API '
         '(~30s; otherwise cached 7 days). Once harvested, '
         'nyc_find_datasets searches this catalog offline.',
         'open_data',
         {'domain': _p('string', '"nyc" (city) or "nys" (state/MTA)', 'nyc'),
          'category': _p('string', 'Browse one category, e.g. "Transportation"'),
          'refresh': _p('boolean', 'Force a fresh harvest of the full catalog'),
          'limit': _p('integer', 'Max datasets when browsing a category', 25)},
         catalog_tool),
    Tool('nyc_find_datasets',
         'Search ALL of NYC Open Data (or NY State) for datasets on any '
         'topic — crime, 311, schools, health, budgets, permits, anything. '
         'Returns dataset ids for nyc_dataset / nyc_query. Runs offline '
         'over the harvested catalog when one exists (see nyc_catalog), '
         'ranked name-first; otherwise the live Discovery API.',
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

    # ── display: the agent drives the user's map ─────────────────────────
    Tool('nyc_map',
         'Change what the user\'s map shows, as they talk. Every argument is '
         'optional; pass only what should change. Layers: `layers` replaces the '
         'visible set, `add`/`remove` adjust it (ids from nyc_layers). Housing '
         'choropleth filters: metric, geography, since, until, property_type '
         '(turns the choropleth on). `min_value`/`max_value` dim areas outside '
         'a range of the current metric. `highlight` outlines areas by name, '
         'code or borough (e.g. ["Brooklyn"] or ["Harlem","BK0101"]; [] clears); `only` hides every area that '
         'does not match. '
         '`focus` flies the camera to a place name (or pass lat+lng+zoom). '
         '`overlay` draws ANY open dataset: {"dataset":"erm2-nwe9","where":'
         '"complaint_type=\'Rodent\' AND created_date>\'2026-01-01\'",'
         '"mode":"heat"|"points"|"areas","by":"zip"|"borough",'
         '"value":"count(*)","per_capita":true,"label":"<column>",'
         '"title":"Rat complaints 2026"} — read columns with nyc_dataset first. '
         '`reset` returns to the default map. The result says what was drawn.',
         'display',
         {'layers': _p('array', 'Replace the visible layer set with these ids'),
          'add': _p('array', 'Layer ids to switch on'),
          'remove': _p('array', 'Layer ids to switch off'),
          'basemap': _p('string', f'One of: {", ".join(SC.BASEMAPS)}. earth is the '
                                  '3-D view: satellite globe, tilted camera, extruded '
                                  'buildings — use it when the user asks for 3D, '
                                  'satellite or a Google-Earth-style look'),
          'metric': _p('string', f'Housing metric: {", ".join(P.METRICS)}'),
          'geography': _p('string', f'Housing geography: {", ".join(P.GEOGRAPHIES)}'),
          'since': _p('string', 'Housing window start, YYYY-MM-DD'),
          'until': _p('string', 'Housing window end, YYYY-MM-DD ("" = today)'),
          'property_type': _p('string', f'One of: {", ".join(P.PROPERTY_TYPES)}'),
          'min_value': _p('number', 'Dim areas below this value of the current metric'),
          'max_value': _p('number', 'Dim areas above this value of the current metric'),
          'highlight': _p('array', 'Area names / codes / boroughs to outline; [] clears'),
          'only': _p('array', 'Show ONLY areas matching these names / codes / boroughs '
                              '(e.g. ["Brooklyn"]); hides the rest; [] shows all'),
          'focus': _p('string', 'Place to fly to (geocoded), e.g. "Astoria"'),
          'lat': _p('number', 'Camera latitude (with lng)'),
          'lng': _p('number', 'Camera longitude (with lat)'),
          'zoom': _p('number', 'Camera zoom, 10 = city, 14 = neighborhood'),
          'overlay': _p('object', 'Draw an open dataset: dataset, where, mode, by, '
                                  'value, per_capita, column, lat, lng, label, limit, '
                                  'domain, title'),
          'clear_overlay': _p('boolean', 'Remove the agent overlay'),
          'reset': _p('boolean', 'Back to the default map before applying the rest'),
          'caption': _p('string', 'One line shown on the map explaining the view')},
         SC.map_directive),
    Tool('nyc_infographic',
         'Pin an infographic card on the user\'s map: headline stats, a ranked '
         'bar list, a small time series, takeaways and sources. Use it whenever '
         'an answer has numbers worth seeing — after you have looked them up. '
         'stats: [{"label","value","note"}] (value is display text, e.g. '
         '"$1.2M"); bars: {"title","unit","items":[{"label","value"}]}; '
         'series: {"title","unit","points":[{"x":"2019","y":123}]}; bullets: '
         '["..."]; sources: [{"name","url"}]. Calling it again replaces the card.',
         'display',
         {'title': _p('string', 'Card title', required=True),
          'subtitle': _p('string', 'One line under the title'),
          'stats': _p('array', 'Up to 6 headline numbers'),
          'bars': _p('object', 'Ranked bar list, up to 12 items'),
          'series': _p('object', 'Time series, up to 60 points'),
          'bullets': _p('array', 'Up to 6 short takeaways'),
          'sources': _p('array', 'Datasets the numbers came from')},
         SC.infographic),

    # ── the owner's data: saved datasets as first-class layers ───────────
    Tool('nyc_data',
         'The datasets the deployment owner has saved as map layers '
         '(category "Your data" in the rail). No slug lists them all; a slug '
         'returns that record. Toggle one on the map with nyc_map '
         'add=["<slug>"].',
         'your_data',
         {'slug': _p('string', 'One saved dataset to inspect')},
         _data_tool),
    Tool('nyc_add_data',
         'OWNER ONLY: save a dataset as a permanent map layer in the rail. '
         'Pass a title plus exactly one source: `dataset` (any Socrata id — '
         'find one with nyc_find_datasets, read columns with nyc_dataset '
         'first, then shape it with mode/where/by/value like a nyc_map '
         'overlay) or `url` (a public GeoJSON file). Fetch-backed layers '
         're-fetch themselves every few hours, so the layer stays current. '
         'Fails for non-owners; do not retry on an authorization error.',
         'your_data',
         {'title': _p('string', 'Layer title (also makes the slug)', required=True),
          'description': _p('string', 'One line shown in the layer rail'),
          'dataset': _p('string', 'Socrata dataset id, e.g. "erm2-nwe9"'),
          'url': _p('string', 'https URL of a GeoJSON FeatureCollection'),
          'geojson': _p('object', 'Inline GeoJSON FeatureCollection (small sets only)'),
          'mode': _p('string', 'points | heat | areas (Socrata datasets)', 'points'),
          'where': _p('string', 'SoQL $where to filter the dataset'),
          'by': _p('string', 'areas mode: "zip" or "borough"'),
          'column': _p('string', 'areas mode: the zip/borough column, if unusual'),
          'value': _p('string', 'areas mode: count(*) or sum/avg/min/max(<col>)'),
          'per_capita': _p('boolean', 'areas mode: per 10k residents'),
          'label': _p('string', 'points mode: column shown as the point label'),
          'lat': _p('string', 'points mode: latitude column, if unusual'),
          'lng': _p('string', 'points mode: longitude column, if unusual'),
          'limit': _p('integer', 'points mode: max points'),
          'domain': _p('string', '"nyc" or "nys"', 'nyc')},
         _add_data_tool, write=True),
    Tool('nyc_remove_data',
         'OWNER ONLY: delete one saved dataset from the layer rail.',
         'your_data',
         {'slug': _p('string', 'The saved dataset to remove', required=True)},
         _remove_data_tool, write=True, destructive=True),
]

_BY_NAME = {t.name: t for t in TOOLS}


def list_tools() -> List[Dict]:
    """MCP-shaped tool list, including titles and behaviour annotations."""
    return [{'name': t.name, 'title': t.title, 'description': t.description,
             'inputSchema': t.input_schema, 'annotations': t.annotations}
            for t in TOOLS]


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
