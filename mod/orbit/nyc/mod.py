"""
nyc — an open-source GIS map of New York City in the browser.

A full slippy-map GIS for NYC built entirely on public, key-free open data:
housing prices from recorded deeds, the subway and bike networks, parks, flood
zones, traffic injuries and administrative boundaries — all as toggleable map
layers with a choropleth engine, legends and a feature inspector.

Nothing here is proprietary. Every layer names its source dataset, geometry is
fetched from the city's own portals, the basemap is OpenStreetMap, and the
renderer is MapLibre GL (BSD-3).

Sources
    NYC Open Data (Socrata)   https://data.cityofnewyork.us
    NY State Open Data / MTA  https://data.ny.gov
    MTA GTFS static feeds     https://www.mta.info/developers
    OpenStreetMap / Nominatim https://www.openstreetmap.org

CLI:
    m nyc                              # null call → info()
    m nyc/layers                       # the layer catalogue
    m nyc/layer subway_lines           # one layer as GeoJSON
    m nyc/housing metric=median_ppsf   # a housing choropleth
    m nyc/prices                       # city-wide price summary
    m nyc/trend area=BK0101            # a neighborhood's price history
    m nyc/where "Prospect Park"        # geocode an address or place
    m nyc/warm                         # pre-fetch every layer into the cache
    m nyc/serve                        # run the API + map app
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
# The package is named `nycgis` rather than `src` on purpose: a generic `src`
# would collide in sys.modules with the other orbit modules that ship one.
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from nycgis import layers as L
from nycgis import prices as P
from nycgis import sources as S

# The five boroughs — kept from this module's original scaffold, and still the
# right thing to hand a client that just wants to know what NYC is made of.
BOROUGHS = {
    'manhattan': {'name': 'Manhattan', 'county': 'New York', 'code': '1',
                  'population': 1629153, 'area_sq_mi': 22.83,
                  'center': [-73.9712, 40.7831],
                  'fun_fact': 'The most densely populated borough and NYC’s financial and cultural core.'},
    'brooklyn': {'name': 'Brooklyn', 'county': 'Kings', 'code': '3',
                 'population': 2590516, 'area_sq_mi': 69.50,
                 'center': [-73.9442, 40.6782],
                 'fun_fact': 'The most populous borough; would be the 3rd-largest U.S. city on its own.'},
    'queens': {'name': 'Queens', 'county': 'Queens', 'code': '4',
               'population': 2270976, 'area_sq_mi': 108.53,
               'center': [-73.7949, 40.7282],
               'fun_fact': 'The most ethnically diverse urban area in the world.'},
    'bronx': {'name': 'The Bronx', 'county': 'Bronx', 'code': '2',
              'population': 1427056, 'area_sq_mi': 42.10,
              'center': [-73.8648, 40.8448],
              'fun_fact': 'The only NYC borough on the U.S. mainland; birthplace of hip-hop.'},
    'staten_island': {'name': 'Staten Island', 'county': 'Richmond', 'code': '5',
                      'population': 475596, 'area_sq_mi': 58.37,
                      'center': [-74.1502, 40.5795],
                      'fun_fact': 'The least populous borough; reached from Manhattan by the free ferry.'},
}

# Map view the app opens on: all five boroughs in frame.
DEFAULT_VIEW = {'center': [-73.9712, 40.7128], 'zoom': 10.2,
                'bounds': [[-74.30, 40.47], [-73.68, 40.93]]}


class Mod:
    description = ('an open-source browser GIS for New York City — housing prices, '
                   'transit, parks and civic data as toggleable map layers')

    def __init__(self, key='nyc', network='testnet'):
        self.key = m.key(key)
        self.network = network
        self.module_dir = MODULE_DIR
        cfg = self._config()
        self.port = int(cfg.get('port', 50310))
        self.app_port = int(cfg.get('app_port', 50311))

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

    def layer(self, id: str) -> dict:
        """One layer as a GeoJSON FeatureCollection."""
        return L.get(id)

    def boundary(self, name: str = 'nta') -> dict:
        """A boundary layer: ``borough``, ``nta`` or ``zip``."""
        return L.boundary(name)

    def view(self) -> dict:
        """The map's default camera and the borough quick-jump targets."""
        return {**DEFAULT_VIEW,
                'boroughs': [{'slug': k, 'name': v['name'], 'center': v['center']}
                             for k, v in BOROUGHS.items()]}

    # ── housing prices ───────────────────────────────────────────────────

    def housing(self, metric: str = 'median_price', geography: str = 'nta',
                since: str = '2024-01-01', until: Optional[str] = None,
                property_type: str = 'residential') -> dict:
        """
        A housing-price choropleth as GeoJSON, one feature per area with every
        metric attached (the client can recolour without a refetch).
        """
        if geography not in P.GEOGRAPHIES:
            return {'error': f'unknown geography {geography!r}',
                    'known': list(P.GEOGRAPHIES)}
        if metric not in P.METRICS:
            return {'error': f'unknown metric {metric!r}', 'known': list(P.METRICS)}
        bounds = L.boundary(P.GEOGRAPHIES[geography]['boundary'])
        fc = P.choropleth(bounds, geography=geography, since=since, until=until,
                          property_type=property_type)
        fc['meta']['metric'] = metric
        fc['meta']['metric_label'] = P.METRICS[metric]['label']
        fc['breaks'] = self._breaks(fc, metric)
        return fc

    def _breaks(self, fc: dict, metric: str) -> dict:
        """
        Quantile class breaks for the colour ramp.

        Quantiles, not equal intervals: NYC property values are extremely
        right-skewed (a handful of Manhattan areas are 20x the median), so
        equal intervals would paint the entire city one colour.
        """
        vals = sorted(v for v in (f['properties'].get(metric)
                                  for f in fc['features']) if v is not None)
        if not vals:
            return {'metric': metric, 'stops': [], 'min': None, 'max': None}

        if metric == 'price_change':
            # A diverging scale must be symmetric about zero, and its extent set
            # by the bulk of the data. One thin-volume ZIP swinging -79% would
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
                'count': len(vals),
                'diverging': metric == 'price_change'}

    def prices(self, since: str = '2024-01-01', until: Optional[str] = None,
               property_type: str = 'residential') -> dict:
        """City-wide price summary: totals, and the top/bottom neighborhoods."""
        s = P.summary(since=since, until=until, property_type=property_type)
        names = self._nta_names()
        for bucket in ('most_expensive', 'least_expensive', 'fastest_rising',
                       'fastest_falling'):
            for row in s[bucket]:
                row['name'] = names.get(row['area'], row['area'])
        return s

    def trend(self, area: Optional[str] = None, geography: str = 'nta',
              property_type: str = 'residential', start_year: int = 2016) -> dict:
        """Yearly median price and $/ft² — city-wide, or for one area."""
        t = P.trend(area=area, geography=geography,
                    property_type=property_type, start_year=start_year)
        if area and geography == 'nta':
            t['name'] = self._nta_names().get(area, area)
        return t

    def sales(self, since: str = '2025-01-01', until: Optional[str] = None,
              property_type: str = 'residential', limit: int = 12000,
              min_price: Optional[int] = None,
              max_price: Optional[int] = None) -> dict:
        """Individual recorded sales as map points."""
        return P.sales_points(since=since, until=until, property_type=property_type,
                              limit=int(limit), min_price=min_price, max_price=max_price)

    def options(self) -> dict:
        """Everything the UI needs to build its housing controls."""
        return {'metrics': P.METRICS, 'geographies': P.GEOGRAPHIES,
                'property_types': P.PROPERTY_TYPES,
                'data_range': {'start': '2016-01-01', 'note': 'DOF rolling sales'}}

    def _nta_names(self) -> Dict[str, str]:
        try:
            return {f['properties']['nta2020']: f['properties']['ntaname']
                    for f in L.neighborhoods()['features']}
        except Exception:
            return {}

    # ── search ───────────────────────────────────────────────────────────

    def where(self, q: str, limit: int = 6) -> List[dict]:
        """
        Geocode a NYC address or place via OpenStreetMap's Nominatim.

        Results are cached — Nominatim's usage policy asks for no more than one
        request per second, and the same searches repeat constantly.
        """
        import requests
        query = str(q).strip()
        if not query:
            return []

        def fetch():
            r = requests.get(
                'https://nominatim.openstreetmap.org/search',
                params={'q': query, 'format': 'jsonv2', 'limit': int(limit),
                        'countrycodes': 'us',
                        'viewbox': '-74.30,40.93,-73.68,40.47', 'bounded': 1},
                headers={'User-Agent': S.USER_AGENT}, timeout=20)
            r.raise_for_status()
            return [{'name': h.get('display_name', ''),
                     'type': h.get('type', ''),
                     'category': h.get('category', ''),
                     'lat': float(h['lat']), 'lng': float(h['lon'])}
                    for h in r.json()]

        return S.cached(f'geocode-{query.lower()}-{limit}', 30 * S.DAY, fetch)

    # ── cache ────────────────────────────────────────────────────────────

    def warm(self, include_housing: bool = True) -> dict:
        """Pre-fetch every layer so the first map load is instant."""
        results = {}
        for lid in L.LOADERS:
            try:
                fc = L.get(lid)
                results[lid] = {'features': len(fc.get('features', [])),
                                'kb': S.geojson_bytes(fc) // 1024}
            except Exception as e:
                results[lid] = {'error': f'{type(e).__name__}: {e}'}
        if include_housing:
            for geo in ('nta', 'borough', 'zip', 'community_district'):
                try:
                    P.with_change(geo, since='2024-01-01', property_type='residential')
                    results[f'housing:{geo}'] = {'ok': True}
                except Exception as e:
                    results[f'housing:{geo}'] = {'error': str(e)}
            # One query covers every area's history, so the inspector's chart
            # is instant on the first click instead of after a ~13s wait.
            try:
                series = P.trend_all('nta', 'residential')
                results['trend:nta'] = {'areas': len(series)}
            except Exception as e:
                results['trend:nta'] = {'error': str(e)}
        return {'warmed': results, 'cache': S.cache_stats()}

    def cache(self) -> dict:
        """Where the on-disk cache lives and how big it is."""
        return S.cache_stats()

    def clear_cache(self, prefix: str = '') -> dict:
        """Drop cached responses. ``prefix`` scopes it (e.g. ``geo-``)."""
        return S.cache_clear(prefix)

    # ── boroughs (from the original scaffold) ────────────────────────────

    def boroughs(self) -> list:
        """The five boroughs, as a list of summary dicts."""
        return [{'slug': slug, 'name': b['name'], 'county': b['county'],
                 'population': b['population'], 'center': b['center']}
                for slug, b in BOROUGHS.items()]

    def borough(self, name) -> dict:
        """Facts about a single borough. ``name`` is a slug or display name."""
        slug = str(name).strip().lower().replace(' ', '_').replace('the_', '')
        aliases = {'si': 'staten_island', 'bx': 'bronx', 'bk': 'brooklyn',
                   'kings': 'brooklyn', 'richmond': 'staten_island',
                   'new_york': 'manhattan'}
        slug = slug if slug in BOROUGHS else aliases.get(slug, slug)
        if slug not in BOROUGHS:
            return {'error': f'unknown borough: {name!r}', 'known': list(BOROUGHS)}
        return {'slug': slug, **BOROUGHS[slug]}

    # ── module surface ───────────────────────────────────────────────────

    def forward(self, action=None, **kwargs):
        """CLI entry: ``m nyc <action> [args]``."""
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
            'name': 'nyc',
            'description': self.description,
            'network': self.network,
            'app': f'http://localhost:{self.app_port}',
            'api': f'http://localhost:{self.port}',
            'layers': cat['count'],
            'categories': [c['name'] for c in cat['categories']],
            'housing': {'metrics': list(P.METRICS),
                        'geographies': list(P.GEOGRAPHIES),
                        'property_types': list(P.PROPERTY_TYPES),
                        'record': 'NYC DOF rolling sales, 2016–present (~845k deeds)'},
            'boroughs': list(BOROUGHS),
            'total_population': sum(b['population'] for b in BOROUGHS.values()),
            'open_data_only': True,
            'attribution': cat['attribution'],
            'cache': S.cache_stats(),
        }

    def health(self) -> dict:
        return {'ok': True, 'module': 'nyc', 'layers': len(L.LAYERS),
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
        ok = self._pm2_start('nyc.api', cmd, env=env)
        return {'api': f'http://localhost:{port}', 'pm2': 'nyc.api',
                'docs': f'http://localhost:{port}/docs', 'ok': ok}

    def serve_app(self, app_port=None, dev=False) -> dict:
        app_port = int(app_port or self.app_port)
        app_dir = self.module_dir / 'app'
        if not (app_dir / 'package.json').exists():
            return {'error': 'app/package.json not found'}
        env = {'NEXT_PUBLIC_API_URL': f'http://localhost:{self.port}',
               'PORT': str(app_port)}
        cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
        ok = self._pm2_start('nyc.app', cmd, cwd=str(app_dir), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'nyc.app',
                'dev': dev, 'ok': ok}

    def serve(self, port=None, app_port=None, dev=False) -> dict:
        """Start the API and the map app under pm2, then register the route."""
        out = {'api': self.serve_api(port=port), 'app': self.serve_app(app_port=app_port, dev=dev)}
        out['registration'] = self.register()
        return out

    def kill(self) -> dict:
        killed = [n for n in ('nyc.api', 'nyc.app') if self._pm2_kill(n)]
        return {'killed': killed}

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('nyc', app_url)
            ns.reg_app('nyc', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/nyc'
            print(f'nyc registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'nyc: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('nyc')
            return {'ok': True, 'deregistered': 'nyc'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
