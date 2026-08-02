"""
tdot — an open-source GIS map of Toronto in the browser.

A full slippy-map GIS for Toronto built entirely on public, key-free open data:
police-reported crime by neighbourhood, the TTC subway and streetcar networks,
the cycling network, green spaces, serious traffic collisions, RentSafeTO
apartment scores and administrative boundaries — all as toggleable map layers
with a choropleth engine, legends and a feature inspector.

Nothing here is proprietary. Every layer names its source dataset, geometry is
fetched from the city's own portal, the basemap is OpenStreetMap, and the
renderer is MapLibre GL (BSD-3).

Sources
    Toronto Open Data (CKAN)  https://open.toronto.ca
    TTC GTFS static feed      via the same portal
    OpenStreetMap / Nominatim https://www.openstreetmap.org

CLI:
    m tdot                              # null call → info()
    m tdot/layers                       # the layer catalogue
    m tdot/layer ttc_lines              # one layer as GeoJSON
    m tdot/crime metric=per_km2         # a crime choropleth
    m tdot/summary                      # city-wide crime summary
    m tdot/trend area=174               # a neighbourhood's yearly history
    m tdot/where "High Park"            # geocode an address or place
    m tdot/warm                         # pre-fetch every layer into the cache
    m tdot/serve                        # run the API + map app
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import mod as m

MODULE_DIR = Path(__file__).parent

# The orbit loader imports this file by path, so the module directory is not
# necessarily importable. Put it on the path before reaching for our package.
# The package is named `tdotgis` rather than `src` on purpose: a generic `src`
# would collide in sys.modules with the other orbit modules that ship one.
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from tdotgis import crime as C
from tdotgis import layers as L
from tdotgis import registry as R
from tdotgis import sources as S

# The six municipalities amalgamated into the City of Toronto in 1998 — still
# how residents carve up the city, and the closest thing to NYC's boroughs.
DISTRICTS = {
    'toronto': {'name': 'Old Toronto', 'code': 'TO',
                'population': 797642, 'area_sq_km': 97.15,
                'center': [-79.3832, 43.6532],
                'fun_fact': 'The pre-1998 City of Toronto — downtown, the waterfront and the streetcar grid.'},
    'north_york': {'name': 'North York', 'code': 'NY',
                   'population': 869401, 'area_sq_km': 176.87,
                   'center': [-79.4085, 43.7708],
                   'fun_fact': 'The most populous district; its centre grew a second downtown on the Yonge subway.'},
    'scarborough': {'name': 'Scarborough', 'code': 'SC',
                    'population': 632098, 'area_sq_km': 187.70,
                    'center': [-79.2500, 43.7764],
                    'fun_fact': 'Home to the Scarborough Bluffs and some of the most diverse neighbourhoods in Canada.'},
    'etobicoke': {'name': 'Etobicoke', 'code': 'ET',
                  'population': 365143, 'area_sq_km': 123.93,
                  'center': [-79.5500, 43.6500],
                  'fun_fact': 'The city\'s western flank, from the Humber River to the airport.'},
    'york': {'name': 'York', 'code': 'YK',
             'population': 201762, 'area_sq_km': 23.06,
             'center': [-79.4700, 43.6900],
             'fun_fact': 'Once its own city; densely built along Eglinton and the new Line 5.'},
    'east_york': {'name': 'East York', 'code': 'EY',
                  'population': 118071, 'area_sq_km': 21.26,
                  'center': [-79.3300, 43.7000],
                  'fun_fact': 'Canada\'s only borough until 1998 — and proud of it.'},
}

# Map view the app opens on: the whole city in frame.
DEFAULT_VIEW = {'center': [-79.3832, 43.7000], 'zoom': 10.6,
                'bounds': [[-79.65, 43.56], [-79.10, 43.87]]}


class Mod:
    description = ('an open-source browser GIS for Toronto — crime by '
                   'neighbourhood, TTC transit, cycling, parks and civic data '
                   'as toggleable map layers')

    def __init__(self, key='tdot', network='testnet'):
        self.key = m.key(key)
        self.network = network
        self.module_dir = MODULE_DIR
        cfg = self._config()
        self.port = int(cfg.get('port', 50320))
        self.app_port = int(cfg.get('app_port', 50321))

    def _config(self) -> dict:
        try:
            import json
            with (MODULE_DIR / 'config.json').open() as fh:
                return json.load(fh)
        except Exception:
            return {}

    # ── map + catalogue ──────────────────────────────────────────────────

    def layers(self) -> dict:
        """The layer catalogue: every layer, its category, style and source."""
        return L.catalog()

    def layer(self, id: str, refresh: bool = False) -> dict:
        """One layer as a GeoJSON FeatureCollection."""
        return L.get(id, refresh=bool(refresh))

    def boundary(self, name: str = 'neighbourhood') -> dict:
        """A boundary layer: ``neighbourhood``, ``neighbourhood140``, ``ward`` or ``municipality``."""
        return L.boundary(name)

    def view(self) -> dict:
        """The map's default camera and the district quick-jump targets."""
        return {**DEFAULT_VIEW,
                'districts': [{'slug': k, 'name': v['name'], 'center': v['center']}
                              for k, v in DISTRICTS.items()]}

    # ── crime choropleth ─────────────────────────────────────────────────

    def crime(self, metric: str = 'incidents', geography: str = 'neighbourhood',
              since: str = '2025-01-01', until: Optional[str] = None,
              category: str = 'all') -> dict:
        """
        A crime choropleth as GeoJSON, one feature per area with every metric
        attached (the client can recolour without a refetch).
        """
        if geography not in C.GEOGRAPHIES:
            return {'error': f'unknown geography {geography!r}',
                    'known': list(C.GEOGRAPHIES)}
        if metric not in C.METRICS:
            return {'error': f'unknown metric {metric!r}', 'known': list(C.METRICS)}
        if category not in C.CATEGORIES:
            return {'error': f'unknown category {category!r}',
                    'known': list(C.CATEGORIES)}
        bounds = L.boundary(C.GEOGRAPHIES[geography]['boundary'])
        fc = C.choropleth(bounds, geography=geography, since=since, until=until,
                          category=category)
        fc['meta']['metric'] = metric
        fc['meta']['metric_label'] = C.METRICS[metric]['label']
        fc['breaks'] = self._breaks(fc, metric)
        return fc

    def _breaks(self, fc: dict, metric: str) -> dict:
        """
        Quantile class breaks for the colour ramp.

        Quantiles, not equal intervals: incident counts are heavily
        right-skewed (a handful of downtown areas are 20x the median), so
        equal intervals would paint the entire city one colour.
        """
        vals = sorted(v for v in (f['properties'].get(metric)
                                  for f in fc['features']) if v is not None)
        if not vals:
            return {'metric': metric, 'stops': [], 'min': None, 'max': None}

        if metric == 'change':
            # A diverging scale must be symmetric about zero, and its extent set
            # by the bulk of the data. One thin-volume area swinging -80% would
            # otherwise stretch the domain until every other area reads neutral.
            def pct(p):
                return vals[min(int(p * (len(vals) - 1)), len(vals) - 1)]
            extent = max(abs(pct(0.05)), abs(pct(0.95)), 1.0)
            return {'metric': metric, 'stops': [round(-extent, 1), round(extent, 1)],
                    'min': round(-extent, 1), 'max': round(extent, 1),
                    'true_min': vals[0], 'true_max': vals[-1],
                    'count': len(vals), 'diverging': True}

        n_classes = 7
        stops = [vals[min(int(i * len(vals) / n_classes), len(vals) - 1)]
                 for i in range(n_classes)]
        # collapse duplicate stops (happens when many areas share a value)
        dedup = []
        for s in stops:
            if not dedup or s > dedup[-1]:
                dedup.append(s)
        return {'metric': metric, 'stops': dedup, 'min': vals[0], 'max': vals[-1],
                'count': len(vals), 'diverging': False}

    def summary(self, since: str = '2025-01-01', until: Optional[str] = None,
                category: str = 'all') -> dict:
        """City-wide crime summary: totals, and the top/bottom neighbourhoods."""
        s = C.summary(since=since, until=until, category=category)
        names = self._hood_names()
        for bucket in ('most_incidents', 'fewest_incidents', 'fastest_rising',
                       'fastest_falling'):
            for row in s[bucket]:
                row['name'] = names.get(row['area'], row['area'])
        return s

    def trend(self, area: Optional[str] = None, geography: str = 'neighbourhood',
              category: str = 'all') -> dict:
        """Yearly incident counts — city-wide, or for one area."""
        t = C.trend(area=area, geography=geography, category=category)
        if area and geography == 'neighbourhood':
            t['name'] = self._hood_names().get(t['area'], t['area'])
        return t

    def incidents(self, since: Optional[str] = None, until: Optional[str] = None,
                  category: str = 'all', limit: int = 15000) -> dict:
        """Recent individual incidents as map points."""
        return C.incident_points(since=since, until=until, category=category,
                                 limit=int(limit))

    def options(self) -> dict:
        """Everything the UI needs to build its crime controls."""
        return {'metrics': C.METRICS, 'geographies': C.GEOGRAPHIES,
                'categories': C.CATEGORIES,
                'data_range': {'start': f'{C.FIRST_YEAR}-01-01',
                               'note': 'TPS Major Crime Indicators'}}

    def _hood_names(self) -> Dict[str, str]:
        try:
            return {f['properties']['AREA_LONG_CODE']:
                    C._clean_hood_name(f['properties']['AREA_NAME'])
                    for f in L.neighbourhoods()['features']}
        except Exception:
            return {}

    # ── open-data registry ───────────────────────────────────────────────
    #
    # A layer is a spec, not a loader (see tdotgis/registry.py), so adding a
    # dataset to the map is a data operation anyone can do — from the CLI, the
    # API, the browser or the chat agent — with no code change and no redeploy.

    def datasets(self) -> dict:
        """Every spec-driven layer: the builtin ones and the ones you added."""
        builtin = set(L.SPECS_BY_ID)
        out = []
        for spec in L.specs().values():
            cached = S.cache_age(f'ds-{spec["id"]}')
            out.append({'id': spec['id'], 'title': spec['title'],
                        'category': spec.get('category', 'Open data'),
                        'package': spec['package'],
                        'mode': spec['locate']['mode'],
                        'custom': spec['id'] not in builtin,
                        'cached_seconds': int(cached) if cached else None})
        return {'datasets': sorted(out, key=lambda d: (d['category'], d['title'])),
                'count': len(out), 'store': str(R.STORE)}

    def find_data(self, q: str, rows: int = 12) -> list:
        """Search Toronto Open Data for datasets you could put on the map."""
        return R.search(str(q), rows=int(rows))

    def add_data(self, package: str, id: Optional[str] = None,
                 title: Optional[str] = None, category: str = 'Open data',
                 resource: Optional[str] = None, build: bool = True) -> dict:
        """
        Add a Toronto Open Data package as a map layer.

        The spec is written by inspecting what the package publishes — a
        GeoJSON export, a geometry column, lat/lng columns, the city's MTM
        grid, or failing all of those, a per-ward count. It is saved under
        ``~/.mod/tdot/datasets`` and appears in the catalogue immediately.
        """
        try:
            spec = R.autodetect(str(package), resource=resource,
                                category=category, title=title, dataset_id=id)
        except Exception as e:
            return {'ok': False, 'error': str(e), 'package': package}
        R.save(spec)
        out = {'ok': True, 'dataset': spec['id'], 'title': spec['title'],
               'mode': spec['locate']['mode'], 'spec': spec}
        if build:
            try:
                fc = R.build(spec)
                out['features'] = len(fc.get('features', []))
                out['kb'] = S.geojson_bytes(fc) // 1024
            except Exception as e:
                # The spec is saved either way — a source that is merely slow
                # or briefly down shouldn't lose the user's dataset.
                out['ok'] = False
                out['error'] = f'saved, but the first build failed: {e}'
        return out

    def remove_data(self, id: str) -> dict:
        """Remove a dataset you added (builtin layers can't be removed)."""
        if id in L.SPECS_BY_ID:
            return {'ok': False, 'error': f'{id!r} is a builtin layer'}
        return {'ok': True, **R.remove(str(id))}

    def dataset(self, id: str) -> dict:
        """One dataset's spec — the recipe the layer is built from."""
        spec = L.specs().get(str(id))
        if not spec:
            return {'error': f'unknown dataset {id!r}', 'known': sorted(L.specs())}
        return {'spec': spec, 'custom': id not in L.SPECS_BY_ID,
                'layer': R.layer_def(spec, custom=id not in L.SPECS_BY_ID)}

    # ── search ───────────────────────────────────────────────────────────

    def where(self, q: str, limit: int = 6) -> List[dict]:
        """Geocode a Toronto address or place (OpenStreetMap Nominatim)."""
        return S.geocode(q, limit=int(limit))

    # ── housing ──────────────────────────────────────────────────────────

    def housing(self, role: Optional[str] = None, live: bool = True) -> dict:
        """
        Every open dataset Toronto publishes about housing, and its role here.

        ``role`` filters: ``layer`` (on the map), ``feature`` (feeding the
        score model), ``table`` (open but with nothing to draw) or ``closed``
        (what people ask for that is not open data at all).
        """
        from tdotgis import housing as H
        return H.inventory(role=role, live=bool(live))

    def housing_search(self, q: str = 'housing', rows: int = 20) -> list:
        """Housing datasets on the portal that the catalogue does not cover."""
        from tdotgis import housing as H
        return H.search(str(q), rows=int(rows))

    # ── the score model ──────────────────────────────────────────────────

    def score(self, refresh: bool = False) -> dict:
        """
        How well open data predicts a building's RentSafeTO evaluation score.

        Out-of-fold accuracy against a mean baseline, the inputs that drive it,
        and what the model cannot see. Fitting happens on the first call after
        a refresh; everything else is served from the cache.
        """
        from tdotgis import score as SC
        return SC.report(refresh=bool(refresh))

    def outliers(self, direction: str = 'under', limit: int = 25) -> dict:
        """
        Buildings scoring furthest from what comparable buildings score.

        ``under`` is the shortlist — same age, size, systems and neighbourhood,
        markedly worse inspection. ``over`` is the tail that beats its stock.
        """
        from tdotgis import score as SC
        return SC.outliers(limit=int(limit), direction=str(direction))

    def building(self, rsn: str) -> dict:
        """One rental building: its filing, its prediction and what drives it."""
        from tdotgis import score as SC
        return SC.building(str(rsn))

    # ── agent ────────────────────────────────────────────────────────────

    def ask(self, question: str, session: Optional[str] = None) -> dict:
        """
        Ask the map a question in plain language.

        The agent answers from this module's own tools — the catalogue, layer
        queries, the crime record, the city's portal — and can turn layers on,
        move the camera or add an entirely new open dataset while it does.
        """
        from tdotgis import agent as A
        return A.ask(str(question), session=session)

    def agent(self) -> dict:
        """The agent's card: its skills, its tools and whether auth is set up."""
        from tdotgis import agent as A
        return A.card()

    def tools(self) -> list:
        """The tool registry the agent plays, grouped."""
        from tdotgis import tools as T
        return T.docs()

    # ── cache ────────────────────────────────────────────────────────────

    def warm(self, include_crime: bool = True) -> dict:
        """Pre-fetch every layer so the first map load is instant."""
        results = {}
        for lid in L.LOADERS:
            try:
                fc = L.get(lid)
                results[lid] = {'features': len(fc.get('features', [])),
                                'kb': S.geojson_bytes(fc) // 1024}
            except Exception as e:
                results[lid] = {'error': f'{type(e).__name__}: {e}'}
        # The score model's own caches: the feature table (four datasets, one of
        # them 125k rows) and the fitted report. Warming these is what keeps the
        # housing panel from making its first visitor wait on a model fit.
        try:
            from tdotgis import housing as H
            from tdotgis import score as SC
            report = SC.report()
            results['score:model'] = {'r2': report['accuracy']['model']['r2'],
                                      'mae': report['accuracy']['model']['mae'],
                                      'buildings': report['target']['buildings_scored']}
            results['housing:inventory'] = {'datasets': H.inventory()['count']}
        except Exception as e:
            results['score:model'] = {'error': f'{type(e).__name__}: {e}'}

        if include_crime:
            # One dump download builds the whole warehouse; everything after
            # is served from the local month cube.
            try:
                results['crime:warehouse'] = C.coverage()
            except Exception as e:
                results['crime:warehouse'] = {'error': str(e)}
            for geo in C.GEOGRAPHIES:
                try:
                    C.with_change(geo, since='2025-01-01')
                    results[f'crime:{geo}'] = {'ok': True}
                except Exception as e:
                    results[f'crime:{geo}'] = {'error': str(e)}
        return {'warmed': results, 'cache': S.cache_stats()}

    def cache(self) -> dict:
        """Where the on-disk cache lives and how big it is."""
        return S.cache_stats()

    def clear_cache(self, prefix: str = '') -> dict:
        """Drop cached responses. ``prefix`` scopes it (e.g. ``geo-``)."""
        return S.cache_clear(prefix)

    # ── districts (the former municipalities) ────────────────────────────

    def districts(self) -> list:
        """The six former municipalities, as a list of summary dicts."""
        return [{'slug': slug, 'name': d['name'], 'code': d['code'],
                 'population': d['population'], 'center': d['center']}
                for slug, d in DISTRICTS.items()]

    def district(self, name) -> dict:
        """Facts about one former municipality. ``name`` is a slug or display name."""
        slug = str(name).strip().lower().replace(' ', '_').replace('the_', '')
        aliases = {'old_toronto': 'toronto', 'downtown': 'toronto',
                   'ny': 'north_york', 'sc': 'scarborough',
                   'et': 'etobicoke', 'ey': 'east_york', 'yk': 'york'}
        slug = slug if slug in DISTRICTS else aliases.get(slug, slug)
        if slug not in DISTRICTS:
            return {'error': f'unknown district: {name!r}', 'known': list(DISTRICTS)}
        return {'slug': slug, **DISTRICTS[slug]}

    # ── module surface ───────────────────────────────────────────────────

    def forward(self, action=None, **kwargs):
        """CLI entry: ``m tdot <action> [args]``."""
        if action is None:
            return self.info()
        fn = getattr(self, str(action), None)
        if not callable(fn) or str(action).startswith('_'):
            return {'error': f'unknown action {action!r}', 'fns': self._fns()}
        return fn(**kwargs)

    def _fns(self) -> List[str]:
        return [k for k in dir(self)
                if not k.startswith('_') and callable(getattr(self, k))]

    def info(self) -> dict:
        cat = L.catalog()
        return {
            'name': 'tdot',
            'description': self.description,
            'network': self.network,
            'app': f'http://localhost:{self.app_port}',
            'api': f'http://localhost:{self.port}',
            'layers': cat['count'],
            'categories': [c['name'] for c in cat['categories']],
            'crime': {'metrics': list(C.METRICS),
                      'geographies': list(C.GEOGRAPHIES),
                      'categories': list(C.CATEGORIES),
                      'record': f'TPS Major Crime Indicators, {C.FIRST_YEAR}–present (~490k incidents)'},
            'districts': list(DISTRICTS),
            'total_population': sum(d['population'] for d in DISTRICTS.values()),
            'open_data_only': True,
            'attribution': cat['attribution'],
            'cache': S.cache_stats(),
        }

    def health(self) -> dict:
        return {'ok': True, 'module': 'tdot', 'layers': len(L.LAYERS),
                'cache': S.cache_stats()}

    # ── serve / register ─────────────────────────────────────────────────

    def _pm2_start(self, name, cmd, cwd=None, env=None) -> bool:
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name]
        if cwd:
            pm2_cmd += ['--cwd', cwd]
        pm2_cmd += ['--'] + list(cmd[1:])
        r = subprocess.run(pm2_cmd, capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        if r.returncode != 0:
            print(r.stderr[-800:])
        return r.returncode == 0

    def _pm2_kill(self, name) -> bool:
        return subprocess.run(['pm2', 'delete', name],
                              capture_output=True, text=True).returncode == 0

    def serve_api(self, port=None, reload=False) -> dict:
        port = int(port or self.port)
        api_dir = self.module_dir / 'api'
        if not (api_dir / 'api.py').exists():
            return {'error': 'api/api.py not found'}
        mod_root = str(self.module_dir.parent.parent.parent)
        env = {'PYTHONPATH': f'{mod_root}:{self.module_dir}:{os.environ.get("PYTHONPATH", "")}',
               'PORT': str(port)}
        cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
               '--port', str(port), '--app-dir', str(api_dir)]
        if reload:
            cmd.append('--reload')
        ok = self._pm2_start('tdot.api', cmd, env=env)
        return {'api': f'http://localhost:{port}', 'pm2': 'tdot.api',
                'docs': f'http://localhost:{port}/docs', 'ok': ok}

    def serve_app(self, app_port=None, dev=False) -> dict:
        app_port = int(app_port or self.app_port)
        app_dir = self.module_dir / 'app'
        if not (app_dir / 'package.json').exists():
            return {'error': 'app/package.json not found'}
        env = {'NEXT_PUBLIC_API_URL': f'http://localhost:{self.port}',
               'PORT': str(app_port)}
        cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
        ok = self._pm2_start('tdot.app', cmd, cwd=str(app_dir), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'tdot.app',
                'dev': dev, 'ok': ok}

    def serve(self, port=None, app_port=None, dev=False) -> dict:
        """Start the API and the map app under pm2, then register the route."""
        out = {'api': self.serve_api(port=port), 'app': self.serve_app(app_port=app_port, dev=dev)}
        out['registration'] = self.register()
        return out

    def kill(self) -> dict:
        killed = [n for n in ('tdot.api', 'tdot.app') if self._pm2_kill(n)]
        return {'killed': killed}

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('tdot', app_url)
            ns.reg_app('tdot', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/tdot'
            print(f'tdot registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'tdot: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('tdot')
            return {'ok': True, 'deregistered': 'tdot'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
