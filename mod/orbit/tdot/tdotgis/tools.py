"""
tdot.tools — the tool registry the map agent plays.

One table, three surfaces: the MCP stdio server (:mod:`tdotgis.mcp_server`),
the chat endpoint (:mod:`tdotgis.agent`) and the console's tool docs are all
generated from ``TOOLS``. Adding a capability here gives the agent that
capability everywhere.

Two kinds of tool live here:

    read    answer a question from the data — the catalogue, a layer's shape,
            a grouped count, the crime record, a geocode
    drive   change what the person is looking at — turn layers on, move the
            camera, retune the choropleth, add a whole new open dataset

A *drive* tool returns its result with a ``__map__`` key. The chat stream
relays that to the browser, which applies it to the live map. That is what
makes the chat a map control and not just a chatbot bolted to the side: you
ask for something and the map you are already looking at changes.

Nothing here mutates city data — the writes are all local (a saved dataset
spec, the disk cache). The agent cannot spend, sign or publish anything.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from . import crime as C
from . import layers as L
from . import registry as R
from . import sources as S


class Tool:
    def __init__(self, name: str, description: str, group: str,
                 params: Dict[str, Dict], handler: Callable[..., Any],
                 drives_map: bool = False):
        self.name = name
        self.description = description
        self.group = group
        self.params = params          # name -> {type, description, default?, required?}
        self.handler = handler
        self.drives_map = drives_map

    @property
    def input_schema(self) -> Dict:
        props, required = {}, []
        for pname, p in self.params.items():
            prop = {'type': p['type'], 'description': p['description']}
            if 'default' in p:
                prop['default'] = p['default']
            if 'items' in p:
                prop['items'] = p['items']
            props[pname] = prop
            if p.get('required'):
                required.append(pname)
        schema: Dict[str, Any] = {'type': 'object', 'properties': props}
        if required:
            schema['required'] = required
        return schema

    def call(self, args: Optional[Dict] = None) -> Any:
        args = dict(args or {})
        missing = [k for k, p in self.params.items()
                   if p.get('required') and k not in args]
        if missing:
            raise ValueError(f'missing required argument(s): {", ".join(missing)}')
        unknown = set(args) - set(self.params)
        if unknown:
            raise ValueError(f'unknown argument(s): {", ".join(sorted(unknown))}')
        return self.handler(**args)


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _as_list(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [x.strip() for x in v.split(',') if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _known_layers() -> Dict[str, dict]:
    return {l['id']: l for l in L.all_layers()}


def _resolve(ids: Any) -> List[str]:
    """Accept layer ids or titles; raise naming the real ones if nothing matches."""
    known = _known_layers()
    out = []
    for want in _as_list(ids):
        if want in known:
            out.append(want)
            continue
        slug = re.sub(r'[^a-z0-9]+', '', want.lower())
        hit = next((i for i, l in known.items()
                    if re.sub(r'[^a-z0-9]+', '', l['title'].lower()) == slug
                    or slug in re.sub(r'[^a-z0-9]+', '', l['title'].lower())), None)
        if not hit:
            raise ValueError(f'no layer {want!r}. Available: {sorted(known)}')
        out.append(hit)
    return out


def _numeric(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ─────────────────────────────────────────────────────────────────────────────
# read tools
# ─────────────────────────────────────────────────────────────────────────────

def _list_layers(category: Optional[str] = None) -> dict:
    cat = (category or '').strip().lower()
    rows = [{'id': l['id'], 'title': l['title'], 'category': l['category'],
             'kind': l['kind'], 'description': l['description'][:200],
             'source': l['source']['dataset']}
            for l in L.all_layers()
            if not cat or l['category'].lower() == cat]
    return {'layers': rows, 'count': len(rows),
            'categories': sorted({l['category'] for l in L.all_layers()})}


def _layer_summary(layer: str) -> dict:
    """What a layer actually contains: how many features and which fields."""
    lid = _resolve(layer)[0]
    fc = L.get(lid)
    feats = fc.get('features', [])
    fields: Dict[str, dict] = {}
    for f in feats:
        for k, v in (f.get('properties') or {}).items():
            slot = fields.setdefault(k, {'nulls': 0, 'nums': [], 'vals': {}})
            if v in (None, ''):
                slot['nulls'] += 1
                continue
            n = _numeric(v)
            if n is not None:
                slot['nums'].append(n)
            elif len(slot['vals']) < 400:
                slot['vals'][str(v)[:60]] = slot['vals'].get(str(v)[:60], 0) + 1

    out = {}
    for k, slot in fields.items():
        if slot['nums']:
            ns = sorted(slot['nums'])
            out[k] = {'type': 'number', 'min': ns[0], 'max': ns[-1],
                      'median': ns[len(ns) // 2],
                      'sum': round(sum(ns), 2), 'nulls': slot['nulls']}
        else:
            top = sorted(slot['vals'].items(), key=lambda kv: -kv[1])[:8]
            out[k] = {'type': 'text', 'distinct': len(slot['vals']),
                      'top': [{'value': v, 'n': n} for v, n in top],
                      'nulls': slot['nulls']}
    return {'layer': lid, 'features': len(feats), 'fields': out,
            'meta': fc.get('meta', {})}


def _layer_query(layer: str, where: Optional[dict] = None,
                 group_by: Optional[str] = None, sum_of: Optional[str] = None,
                 near: Optional[str] = None, radius_km: float = 1.0,
                 top: int = 12, list_features: bool = False) -> dict:
    """
    Filter, group and total a layer's features — the analytic workhorse.

    Never returns raw GeoJSON: a 25,000-feature layer would swamp the agent's
    context and tell it nothing. It returns counts, sums and at most ``top``
    named rows.
    """
    lid = _resolve(layer)[0]
    fc = L.get(lid)
    feats = fc.get('features', [])

    for key, want in (where or {}).items():
        needle = str(want).lower()
        feats = [f for f in feats
                 if needle in str((f.get('properties') or {}).get(key, '')).lower()]

    centre = None
    if near:
        hits = S.geocode(near, limit=1)
        if not hits:
            return {'error': f'could not find {near!r} in Toronto'}
        centre = (hits[0]['lng'], hits[0]['lat'], hits[0]['name'])
        # ~1 degree of latitude is 111 km; longitude is scaled by cos(43.7°).
        dlat = radius_km / 111.0
        dlng = radius_km / (111.0 * 0.723)
        kept = []
        for f in feats:
            g = f.get('geometry') or {}
            if g.get('type') != 'Point':
                continue
            lng, lat = g['coordinates'][:2]
            if abs(lat - centre[1]) <= dlat and abs(lng - centre[0]) <= dlng:
                kept.append(f)
        feats = kept

    result: Dict[str, Any] = {'layer': lid, 'matched': len(feats)}
    if centre:
        result['near'] = {'place': centre[2], 'lng': centre[0], 'lat': centre[1],
                          'radius_km': radius_km}

    if sum_of:
        total = sum(_numeric((f['properties'] or {}).get(sum_of)) or 0 for f in feats)
        result['total'] = {'field': sum_of, 'value': round(total, 2)}

    if group_by:
        groups: Dict[str, dict] = {}
        for f in feats:
            p = f.get('properties') or {}
            key = str(p.get(group_by) or '—')[:60]
            g = groups.setdefault(key, {'n': 0, 'sum': 0.0})
            g['n'] += 1
            if sum_of:
                g['sum'] += _numeric(p.get(sum_of)) or 0
        rows = sorted(groups.items(),
                      key=lambda kv: -(kv[1]['sum'] if sum_of else kv[1]['n']))
        result['groups'] = [
            {group_by: k, 'count': v['n'],
             **({sum_of: round(v['sum'], 2)} if sum_of else {})}
            for k, v in rows[:int(top)]]
        result['group_count'] = len(groups)

    if list_features or (not group_by and not sum_of):
        rows = []
        for f in feats[:int(top)]:
            p = dict(f.get('properties') or {})
            for k, v in list(p.items()):
                if isinstance(v, str) and len(v) > 160:
                    p[k] = v[:160] + '…'
            g = f.get('geometry') or {}
            if g.get('type') == 'Point':
                p['_lng'], p['_lat'] = g['coordinates'][:2]
            rows.append(p)
        result['features'] = rows
    return result


def _crime_summary(since: str = '2025-01-01', until: Optional[str] = None,
                   category: str = 'all') -> dict:
    s = C.summary(since=since, until=until, category=category)
    names = {f['properties']['AREA_LONG_CODE']:
             C._clean_hood_name(f['properties']['AREA_NAME'])
             for f in L.neighbourhoods()['features']}
    for bucket in ('most_incidents', 'fewest_incidents', 'fastest_rising',
                   'fastest_falling'):
        for row in s.get(bucket, []):
            row['name'] = names.get(row['area'], row['area'])
    return s


def _crime_trend(area: Optional[str] = None, category: str = 'all') -> dict:
    return C.trend(area=area, geography='neighbourhood', category=category)


def _find_place(query: str, limit: int = 5) -> dict:
    return {'results': S.geocode(query, limit=int(limit))}


# ─────────────────────────────────────────────────────────────────────────────
# drive tools — these change what the person is looking at
# ─────────────────────────────────────────────────────────────────────────────

def _show_layers(layers: Any, only: bool = False) -> dict:
    ids = _resolve(layers)
    known = _known_layers()
    return {'shown': ids,
            'titles': [known[i]['title'] for i in ids],
            '__map__': {'show': ids, 'only': bool(only)}}


def _hide_layers(layers: Any) -> dict:
    ids = _resolve(layers)
    return {'hidden': ids, '__map__': {'hide': ids}}


def _fly_to(place: str, zoom: float = 14) -> dict:
    hits = S.geocode(place, limit=1)
    if not hits:
        return {'error': f'could not find {place!r} in Toronto'}
    h = hits[0]
    return {'moved_to': h['name'], 'lng': h['lng'], 'lat': h['lat'],
            '__map__': {'fly_to': {'lng': h['lng'], 'lat': h['lat'],
                                   'zoom': float(zoom)}}}


def _set_crime_view(metric: str = 'incidents', geography: str = 'neighbourhood',
                    category: str = 'all', since: str = '2025-01-01',
                    until: Optional[str] = None) -> dict:
    for name, value, table in (('metric', metric, C.METRICS),
                               ('geography', geography, C.GEOGRAPHIES),
                               ('category', category, C.CATEGORIES)):
        if value not in table:
            raise ValueError(f'unknown {name} {value!r}; known: {list(table)}')
    query = {'metric': metric, 'geography': geography, 'category': category,
             'since': since}
    if until:
        query['until'] = until
    return {'crime_view': query,
            '__map__': {'show': ['crime'], 'crime': query}}


def _search_open_data(query: str, rows: int = 10) -> dict:
    return {'results': R.search(str(query), rows=int(rows)),
            'note': ('Add one with add_open_data(package=...). The package is '
                     'the "package" field of a result.')}


def _add_open_data(package: str, title: Optional[str] = None,
                   category: str = 'Open data',
                   resource: Optional[str] = None) -> dict:
    """Add a dataset to the map and switch it on in one step."""
    existing = next((s for s in L.specs().values()
                     if s['package'] == package), None)
    if existing:
        return {'already_present': existing['id'], 'title': existing['title'],
                '__map__': {'show': [existing['id']]}}
    spec = R.autodetect(package, resource=resource, category=category,
                        title=title)
    R.save(spec)
    try:
        fc = R.build(spec)
    except Exception as e:
        R.remove(spec['id'])
        raise ValueError(f'{package!r} could not be built into a layer: {e}')
    n = len(fc.get('features', []))
    if not n:
        R.remove(spec['id'])
        raise ValueError(f'{package!r} produced no mappable features')
    return {'added': spec['id'], 'title': spec['title'],
            'category': spec['category'], 'features': n,
            'located_by': spec['locate']['mode'],
            '__map__': {'show': [spec['id']], 'catalog_changed': True}}


def _remove_open_data(dataset: str) -> dict:
    if dataset in L.SPECS_BY_ID:
        raise ValueError(f'{dataset!r} is a builtin layer and cannot be removed')
    out = R.remove(dataset)
    return {**out, '__map__': {'hide': [dataset], 'catalog_changed': True}}


# ─────────────────────────────────────────────────────────────────────────────
# housing & the score model
# ─────────────────────────────────────────────────────────────────────────────

def _housing_data(role: Optional[str] = None) -> dict:
    from . import housing as H
    inv = H.inventory(role=role)
    # The agent gets the catalogue without the portal-status bookkeeping; it
    # needs to know what exists and what tdot does with it, not each package's
    # resource count.
    return {
        'datasets': [{k: v for k, v in d.items() if k != 'portal_status'}
                     for d in inv['datasets']],
        'count': inv['count'], 'by_role': inv['by_role'], 'roles': inv['roles'],
    }


def _score_model() -> dict:
    from . import score as SC
    return SC.report()


def _score_outliers(direction: str = 'under', limit: int = 15) -> dict:
    from . import score as SC
    out = SC.outliers(limit=int(limit), direction=str(direction))
    return {**out, '__map__': {'show': ['predicted_scores']}}


def _score_building(rsn: str) -> dict:
    from . import score as SC
    return SC.building(str(rsn))


# ─────────────────────────────────────────────────────────────────────────────
# registry
# ─────────────────────────────────────────────────────────────────────────────

TOOLS: List[Tool] = [
    Tool('tdot_list_layers',
         'List every map layer available: id, title, category and what it '
         'shows. Call this first — layer ids come from here.',
         'Map',
         dict(category={'type': 'string',
                        'description': 'Only this category (Crime, Real estate, '
                                       'Housing, Transit, Environment, Safety, '
                                       'Boundaries, Open data)'}),
         _list_layers),

    Tool('tdot_layer_summary',
         'Describe one layer: how many features it has and, for every field, '
         'its range (numbers) or commonest values (text). Use it to learn what '
         'you can filter or group by before calling tdot_layer_query.',
         'Map',
         dict(layer={'type': 'string', 'description': 'Layer id or title',
                     'required': True}),
         _layer_summary),

    Tool('tdot_layer_query',
         'Filter, group, total and list a layer\'s features. This is how you '
         'answer questions with numbers — e.g. group development_pipeline by '
         'ward summing units, or list rental_buildings within 1 km of a '
         'station. Returns counts and at most `top` rows, never raw geometry.',
         'Map',
         dict(layer={'type': 'string', 'description': 'Layer id or title',
                     'required': True},
              where={'type': 'object',
                     'description': 'Field → substring to match, '
                                    'case-insensitive. e.g. {"status": "under review"}'},
              group_by={'type': 'string', 'description': 'Field to group by'},
              sum_of={'type': 'string',
                      'description': 'Numeric field to total (e.g. "units")'},
              near={'type': 'string',
                    'description': 'Only features near this Toronto address or place'},
              radius_km={'type': 'number', 'description': 'Radius for `near`',
                         'default': 1.0},
              top={'type': 'integer', 'description': 'Rows to return', 'default': 12},
              list_features={'type': 'boolean',
                             'description': 'Also list matching features',
                             'default': False}),
         _layer_query),

    Tool('tdot_crime_summary',
         'City-wide crime totals for a window, with the highest and lowest '
         'neighbourhoods and the biggest movers.',
         'Crime',
         dict(since={'type': 'string', 'description': 'ISO start date',
                     'default': '2025-01-01'},
              until={'type': 'string', 'description': 'ISO end date'},
              category={'type': 'string',
                        'description': 'all, assault, auto_theft, break_enter, '
                                       'robbery, theft_over',
                        'default': 'all'}),
         _crime_summary),

    Tool('tdot_crime_trend',
         'Yearly incident counts since 2014 — city-wide, or for one '
         'neighbourhood by its code.',
         'Crime',
         dict(area={'type': 'string',
                    'description': 'Neighbourhood code, e.g. "170". Omit for city-wide'},
              category={'type': 'string', 'description': 'Crime category',
                        'default': 'all'}),
         _crime_trend),

    Tool('tdot_find_place',
         'Geocode a Toronto address, intersection or place name.',
         'Map',
         dict(query={'type': 'string', 'description': 'Address or place',
                     'required': True},
              limit={'type': 'integer', 'description': 'Max results', 'default': 5}),
         _find_place),

    Tool('tdot_show_layers',
         'Turn layers ON on the map the person is looking at. Use this whenever '
         'someone asks to see something.',
         'View',
         dict(layers={'type': 'array', 'items': {'type': 'string'},
                      'description': 'Layer ids to show', 'required': True},
              only={'type': 'boolean',
                    'description': 'Also hide every other layer, for a clean view',
                    'default': False}),
         _show_layers, drives_map=True),

    Tool('tdot_hide_layers',
         'Turn layers OFF on the map.',
         'View',
         dict(layers={'type': 'array', 'items': {'type': 'string'},
                      'description': 'Layer ids to hide', 'required': True}),
         _hide_layers, drives_map=True),

    Tool('tdot_fly_to',
         'Move the map to an address, neighbourhood or landmark.',
         'View',
         dict(place={'type': 'string', 'description': 'Where to go',
                     'required': True},
              zoom={'type': 'number', 'description': 'Zoom level, 10 city to 17 street',
                    'default': 14}),
         _fly_to, drives_map=True),

    Tool('tdot_set_crime_view',
         'Retune the crime choropleth: which metric it colours by, which '
         'geography it aggregates to, which crime type and which window.',
         'View',
         dict(metric={'type': 'string',
                      'description': 'incidents, per_km2, per_month or change',
                      'default': 'incidents'},
              geography={'type': 'string',
                         'description': 'neighbourhood or neighbourhood140',
                         'default': 'neighbourhood'},
              category={'type': 'string', 'description': 'Crime category',
                        'default': 'all'},
              since={'type': 'string', 'description': 'ISO start date',
                     'default': '2025-01-01'},
              until={'type': 'string', 'description': 'ISO end date'}),
         _set_crime_view, drives_map=True),

    Tool('tdot_search_open_data',
         'Search Toronto Open Data for datasets that are not on the map yet. '
         'Use it when someone asks for something no current layer covers.',
         'Open data',
         dict(query={'type': 'string', 'description': 'What to look for',
                     'required': True},
              rows={'type': 'integer', 'description': 'Max results', 'default': 10}),
         _search_open_data),

    Tool('tdot_add_open_data',
         'Add a Toronto Open Data package to the map as a new layer and show '
         'it. The location method is worked out automatically; a table with no '
         'coordinates becomes a per-ward density map. Fails clearly if the '
         'dataset cannot be mapped.',
         'Open data',
         dict(package={'type': 'string',
                       'description': 'CKAN package name from tdot_search_open_data',
                       'required': True},
              title={'type': 'string', 'description': 'Override the layer title'},
              category={'type': 'string', 'description': 'Panel category',
                        'default': 'Open data'},
              resource={'type': 'string',
                        'description': 'Pick a specific resource by name substring'}),
         _add_open_data, drives_map=True),

    Tool('tdot_remove_open_data',
         'Remove a layer somebody added earlier. Builtin layers cannot be removed.',
         'Open data',
         dict(dataset={'type': 'string', 'description': 'Dataset id',
                       'required': True}),
         _remove_open_data, drives_map=True),

    Tool('tdot_housing_data',
         'Every open dataset the city publishes about housing and what this map '
         'does with each — drawn as a layer, feeding the score model, open but '
         'with no geography to draw, or not open data at all. Answer "what '
         'housing data is there" from this rather than guessing.',
         'Housing',
         dict(role={'type': 'string',
                    'description': 'Only one role: layer, feature, table or closed'}),
         _housing_data),

    Tool('tdot_score_model',
         'How well open data predicts a building\'s RentSafeTO inspection '
         'score: out-of-fold accuracy against a mean baseline, what drives it, '
         'and what it cannot see. Quote the error, not just the accuracy.',
         'Housing',
         dict(),
         _score_model),

    Tool('tdot_score_outliers',
         'Rental buildings scoring furthest from what buildings of the same '
         'age, size, systems and neighbourhood score. `under` is the shortlist '
         'of buildings doing worse than their peers; `over` is the other tail.',
         'Housing',
         dict(direction={'type': 'string', 'description': 'under or over',
                         'default': 'under'},
              limit={'type': 'integer', 'description': 'How many', 'default': 15}),
         _score_outliers),

    Tool('tdot_score_building',
         'One rental building against the model: what the city has on file, '
         'its predicted score, what it actually scored, and which inputs move '
         'the prediction. Takes the RSN from tdot_score_outliers or the '
         'predicted_scores layer.',
         'Housing',
         dict(rsn={'type': 'string', 'description': 'Building registration number',
                   'required': True}),
         _score_building),
]

TOOL_MAP: Dict[str, Tool] = {t.name: t for t in TOOLS}


def list_tools() -> List[Dict]:
    """MCP-shaped tool listing."""
    return [{'name': t.name, 'description': t.description,
             'inputSchema': t.input_schema} for t in TOOLS]


def call_tool(name: str, args: Optional[Dict] = None) -> Any:
    tool = TOOL_MAP.get(name)
    if tool is None:
        raise ValueError(f'unknown tool: {name}')
    return tool.call(args)


def docs() -> List[Dict]:
    """Grouped tool docs — what the console shows people the agent can do."""
    groups: Dict[str, List[Dict]] = {}
    for t in TOOLS:
        groups.setdefault(t.group, []).append({
            'name': t.name, 'description': t.description,
            'drives_map': t.drives_map,
            'params': [{'name': k, **v} for k, v in t.params.items()]})
    return [{'group': g, 'tools': ts} for g, ts in groups.items()]
