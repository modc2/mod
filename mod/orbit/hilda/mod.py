"""
hilda — sixty years of global land use change, as a map, a chart and an
automaton.

HILDA+ (HIstoric Land Dynamics Assessment+) reconstructs annual land use and
land cover for the whole planet from 1960 to 2019 at 1 km, in six classes:
urban, cropland, pasture/rangeland, forest, unmanaged grass/shrubland and
sparse vegetation. It is published on PANGAEA under CC-BY-4.0 as a 4.5 GB ZIP
of 933-megapixel GeoTIFFs, which is not a thing you can put on a screen.

This module makes it one. It pulls single years out of that archive with HTTP
range requests, reduces each to class fractions on a 0.5 degree grid, and
serves the result three ways:

    longitudinal   area per class per year, for the globe, a region or a box
    spatial        the classified grid, plus full 1 km windows on demand
    modelled       a cellular automaton whose transition rates are counted
                   from the HILDA+ transition layers, hindcast-scored against
                   what actually happened

The console at /hilda draws all of it in eight-bit.

CLI:
    m hilda                                  # null call -> info()
    m hilda/ingest                           # build the cube (once, ~15 min)
    m hilda/ingest kind=transitions          # and the gross-change record
    m hilda/summary                          # what changed, 1960-2019
    m hilda/series region=amazon             # a region's curve
    m hilda/areas year=2019                  # one year's totals
    m hilda/net y0=1960 y1=2019              # net change per class
    m hilda/transitions                      # gross flows, from -> to
    m hilda/hotspots n=10                    # where the world churned most
    m hilda/cell lon=-60 lat=-3              # one cell, every year
    m hilda/ca                               # run the automaton
    m hilda/ca weight=0.6 scenario=urban=2   # ...with a thumb on the scale
    m hilda/calibrate                        # pick the neighbourhood weight
    m hilda/validate                         # hindcast scorecard
    m hilda/map year=2019 out=/tmp/map.png   # write a PNG
    m hilda/window bbox=-63,-10,-58,-5       # 1 km detail for a box
    m hilda/status                           # what is ingested
    m hilda/serve                            # API + console
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import mod as m

MODULE_DIR = Path(__file__).parent

# The orbit loader imports this file by path, so the module directory is not
# on sys.path by default. The package is called `hildaplus` rather than `src`
# on purpose: a generic `src` collides in sys.modules with every other orbit
# module that ships one.
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


class Mod:
    description = ('sixty years of global land use change from HILDA+ — '
                   'longitudinal, spatial, and modelled as a cellular '
                   'automaton, in an eight-bit console')

    def __init__(self, key='hilda', network='testnet'):
        self.key = m.key(key)
        self.network = network
        self.module_dir = MODULE_DIR
        cfg = self._config()
        self.port = int(cfg.get('port', 50550))
        self.app_port = int(cfg.get('app_port', 50551))

    def _config(self) -> dict:
        try:
            with (MODULE_DIR / 'config.json').open() as fh:
                return json.load(fh)
        except Exception:
            return {}

    # Imported lazily: `m hilda` should answer without paying for numpy, and
    # a machine with no cube should still get a useful info().
    def _lib(self):
        from hildaplus import automata, cube, raster, remote, render, series, sources
        return dict(automata=automata, cube=cube, raster=raster, remote=remote,
                    render=render, series=series, sources=sources)

    # ── ingest ───────────────────────────────────────────────────────────

    def ingest(self, years=None, kind='states', deg=None, workers=6,
               keep_tif=False, quiet=False) -> dict:
        """Fetch and reduce years into the cube.

        ``years`` takes ``1960-2019``, ``1960-2019:5``, ``1990,2000,2010`` or
        ``all``. Already-ingested years are skipped, so this is safe to re-run
        and is the way to extend a partial cube.
        """
        L = self._lib()
        deg = float(deg or L['sources'].DEFAULT_DEG)

        def progress(year, done, total):
            if not quiet:
                print(f'  [{done}/{total}] {year}', flush=True)

        if not quiet:
            print(f'hilda: ingesting {kind} at {deg} deg — '
                  f'~20 MB and ~10 s per year', flush=True)
        return L['cube'].build(years=years, deg=deg, kind=kind,
                               workers=int(workers), keep_tif=bool(keep_tif),
                               progress=progress)

    def bootstrap(self, workers=6) -> dict:
        """Ingest everything the console needs: states and transitions."""
        return {'states': self.ingest(kind='states', workers=workers),
                'transitions': self.ingest(kind='transitions', workers=workers)}

    def status(self) -> dict:
        """What is on disk: cubes, years, gaps, cached rasters."""
        return self._lib()['cube'].status()

    def clear(self, kind='', keep=0) -> dict:
        """Drop cached source rasters. The cubes are left alone."""
        return self._lib()['remote'].clear(kind, int(keep))

    # ── longitudinal ─────────────────────────────────────────────────────

    def summary(self, deg=None) -> dict:
        """The headline: what grew, what shrank, gross versus net."""
        L = self._lib()
        return L['series'].summary(float(deg or L['sources'].DEFAULT_DEG))

    def series(self, region=None, bbox=None, years=None, deg=None) -> dict:
        """Area per class per year, for a region or an arbitrary box."""
        L = self._lib()
        return L['series'].series(region, bbox, years,
                                  float(deg or L['sources'].DEFAULT_DEG))

    def areas(self, year, region=None, bbox=None, deg=None) -> dict:
        """Every class's area in one year."""
        L = self._lib()
        return L['series'].areas(year, region, bbox,
                                 float(deg or L['sources'].DEFAULT_DEG))

    def net(self, y0=None, y1=None, region=None, bbox=None, deg=None) -> dict:
        """Net change per class between two years."""
        L = self._lib()
        return L['series'].net_change(y0, y1, region, bbox,
                                      float(deg or L['sources'].DEFAULT_DEG))

    def transitions(self, y0=None, y1=None, deg=None) -> dict:
        """Gross class-to-class flows, from the HILDA+ transition layers."""
        L = self._lib()
        return L['series'].transitions(y0, y1,
                                       float(deg or L['sources'].DEFAULT_DEG))

    def hotspots(self, y0=None, y1=None, n=20, region=None, bbox=None,
                 deg=None) -> dict:
        """The cells that turned over most."""
        L = self._lib()
        return L['series'].hotspots(y0, y1, int(n), region, bbox,
                                    float(deg or L['sources'].DEFAULT_DEG))

    def cell(self, lon, lat, deg=None) -> dict:
        """One grid cell's whole history."""
        L = self._lib()
        return L['series'].cell(float(lon), float(lat),
                                float(deg or L['sources'].DEFAULT_DEG))

    # ── the automaton ────────────────────────────────────────────────────

    def ca(self, start=None, end=None, weight=None, scenario=None,
           protect=None, deg=None) -> dict:
        """Run the cellular automaton and report the resulting curves.

        ``scenario`` biases conversion toward or away from a class, as in
        ``urban=2`` or ``forest=0.5``. ``protect`` freezes a region or bbox.
        ``end`` past 2019 makes it a projection.
        """
        L = self._lib()
        A = L['automata']
        out = A.run(start, end,
                    float(A.DEFAULT_NEIGHBOURHOOD_WEIGHT if weight is None
                          else weight),
                    scenario, protect,
                    deg=float(deg or L['sources'].DEFAULT_DEG),
                    keep_frames=False)
        return out

    def rates(self, y0=None, y1=None, deg=None) -> dict:
        """The observed annual transition matrix the automaton runs on."""
        L = self._lib()
        matrix, provenance = L['automata'].rates(
            y0, y1, float(deg or L['sources'].DEFAULT_DEG))
        keys = [c['key'] for c in L['sources'].CLASSES]
        return {'classes': keys, 'source': provenance,
                'annual_probability': [[float(v) for v in row] for row in matrix]}

    def calibrate(self, start=None, end=None, deg=None) -> dict:
        """Sweep the neighbourhood weight; keep what hindcasts best."""
        L = self._lib()
        return L['automata'].calibrate(
            start, end, deg=float(deg or L['sources'].DEFAULT_DEG))

    def validate(self, start=None, end=None, weight=None, deg=None) -> dict:
        """Score a hindcast against what happened, and against doing nothing."""
        L = self._lib()
        A = L['automata']
        return A.validate(start, end,
                          float(A.DEFAULT_NEIGHBOURHOOD_WEIGHT if weight is None
                                else weight),
                          float(deg or L['sources'].DEFAULT_DEG))

    # ── pictures ─────────────────────────────────────────────────────────

    def map(self, year=2019, out=None, scale=2, deg=None) -> dict:
        """Write the classified world for one year as a PNG."""
        L = self._lib()
        deg = float(deg or L['sources'].DEFAULT_DEG)
        y = L['cube'].nearest_year(year, 'states', deg)
        png = L['render'].png_from_classified(
            L['render'].classify(L['cube'].frame(y, deg)), int(scale))
        return self._write(png, out, f'hilda_{y}.png', {'year': y})

    def window(self, bbox, year=2019, out=None, kind='states') -> dict:
        """Write a bbox at the full 1 km source resolution as a PNG."""
        L = self._lib()
        box = L['sources'].resolve_bbox(None, bbox)
        path = L['remote'].fetch_year(year, kind)
        with L['raster'].Raster(path) as src:
            arr, snapped = src.window(box)
        png = L['render'].png_from_codes(arr)
        return self._write(png, out, f'hilda_{year}_window.png',
                           {'year': int(year), 'bbox': snapped,
                            'pixels': list(arr.shape)})

    def change(self, y0=None, y1=None, out=None, scale=2, deg=None) -> dict:
        """Write the gross-turnover map for a span as a PNG."""
        L = self._lib()
        import numpy as np
        deg = float(deg or L['sources'].DEFAULT_DEG)
        ci = L['series'].change_intensity(y0, y1, deg)
        png = L['render'].png_from_intensity(
            ci, '#ffb02e', int(scale), vmax=float(np.percentile(ci, 99.5)) or 1.0)
        return self._write(png, out, 'hilda_change.png',
                           {'from': y0, 'to': y1,
                            'max_turnover': float(ci.max())})

    def _write(self, png: bytes, out, default_name: str, extra: dict) -> dict:
        path = Path(out) if out else (Path('/tmp') / default_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        return {'png': str(path), 'bytes': len(png), **extra}

    # ── module surface ───────────────────────────────────────────────────

    def classes(self) -> list:
        """The six land use classes, plus water, ice and ocean."""
        return self._lib()['render'].palette()

    def regions(self) -> list:
        """Named bounding boxes any region= argument accepts."""
        S = self._lib()['sources']
        return [{'key': k, **v} for k, v in S.REGIONS.items()]

    def forward(self, action=None, **kwargs):
        """CLI entry: ``m hilda <action> [args]``."""
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
        """What this module is, and whether it has data yet."""
        try:
            L = self._lib()
            S = L['sources']
            st = L['cube'].status()
            out = {
                'name': 'hilda',
                'description': self.description,
                'network': self.network,
                'app': f'http://localhost:{self.app_port}',
                'api': f'http://localhost:{self.port}',
                'dataset': 'HILDA+ v1.0 (vGLOB-1.0), 1 km, annual, 1960-2019',
                'classes': [c['key'] for c in S.CLASSES],
                'grid': {'deg': S.DEFAULT_DEG,
                         'shape': list(L['raster'].grid_shape()),
                         'source': [S.SRC_W, S.SRC_H]},
                'regions': list(S.REGIONS),
                'ready': st['ready'],
                'years': (f"{st['state_years'][0]}-{st['state_years'][-1]}"
                          if st['state_years'] else None),
                'cubes': st['grids'],
                'model': 'cellular automaton, rates counted from the HILDA+ '
                         'transition layers, neighbourhood-weighted',
                'attribution': S.attribution(),
            }
            if not st['ready']:
                out['next'] = 'no cube yet — run: m hilda/bootstrap'
            return out
        except Exception as e:
            return {'name': 'hilda', 'description': self.description,
                    'error': f'{type(e).__name__}: {e}',
                    'hint': 'numpy and Pillow are required'}

    def health(self) -> dict:
        try:
            st = self._lib()['cube'].status()
            return {'ok': True, 'module': 'hilda', 'ready': st['ready'],
                    'years': len(st['state_years'])}
        except Exception as e:
            return {'ok': False, 'module': 'hilda', 'error': str(e)}

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
        mod_root = str(self.module_dir.parent.parent.parent)
        env = {'PYTHONPATH': f'{mod_root}:{self.module_dir}:'
                             f'{os.environ.get("PYTHONPATH", "")}',
               'PORT': str(port)}
        cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
               '--port', str(port), '--app-dir', str(api_dir)]
        if reload:
            cmd.append('--reload')
        ok = self._pm2_start('hilda.api', cmd, env=env)
        return {'api': f'http://localhost:{port}', 'pm2': 'hilda.api',
                'docs': f'http://localhost:{port}/docs', 'ok': ok}

    def serve_app(self, app_port=None) -> dict:
        app_port = int(app_port or self.app_port)
        app_dir = self.module_dir / 'app'
        env = {'PORT': str(app_port), 'HILDA_API': f'http://localhost:{self.port}'}
        cmd = ['python3', str(app_dir / 'server.py')]
        ok = self._pm2_start('hilda.app', cmd, cwd=str(app_dir), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'hilda.app',
                'ok': ok}

    def serve(self, port=None, app_port=None) -> dict:
        """Start the API and the console under pm2, then register the route."""
        out = {'api': self.serve_api(port=port),
               'app': self.serve_app(app_port=app_port)}
        out['registration'] = self.register()
        return out

    def kill(self) -> dict:
        killed = [n for n in ('hilda.api', 'hilda.app') if self._pm2_kill(n)]
        return {'killed': killed}

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('hilda', app_url)
            ns.reg_app('hilda', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/hilda'
            print(f'hilda registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'hilda: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('hilda')
            return {'ok': True, 'deregistered': 'hilda'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
