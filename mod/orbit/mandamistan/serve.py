"""Server for the mandamistan console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

    /mandamistan/*          → the console (prefix kept by the gateway; stripped here)
    /mandamistan/api/{fn}   → the API, as the console calls it
    /api/mandamistan/{fn}   → the API (prefix stripped by the gateway)
    /{fn}                   → the API, bare, for local curl

Everything is a read — there is no state to write. simulate() takes its
assumptions as query args.

    python3 serve.py [--port 51240] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/mandamistan'

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import housing  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'crisis', 'plan', 'pillar',
            'timeline', 'critiques', 'simulate', 'sources')


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def _num(a, key, default):
    try:
        return float(a[key]) if key in a else default
    except (TypeError, ValueError):
        return default


def api(fn, a):
    if fn == 'health':
        return {'ok': True, 'pillars': len(housing.PLAN['pillars']),
                'sources': len(housing.SOURCES)}
    if fn == 'info':
        cfg = _config()
        return {'name': 'mandamistan', 'version': cfg.get('version'),
                'description': cfg.get('description'),
                'plan': housing.PLAN['name'],
                'status': 'rent freeze in effect since 2026-10-01',
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'crisis':
        return housing.CRISIS
    if fn == 'plan':
        return housing.PLAN
    if fn == 'pillar':
        pid = a.get('id', '')
        for p in housing.PLAN['pillars']:
            if p['id'] == pid:
                return p
        return {'error': f'no pillar {pid}',
                'pillars': [p['id'] for p in housing.PLAN['pillars']]}
    if fn == 'timeline':
        return {'timeline': housing.TIMELINE}
    if fn == 'critiques':
        return {'critiques': housing.CRITIQUES}
    if fn == 'simulate':
        return housing.simulate(
            _num(a, 'new_per_year', 20000),
            _num(a, 'preserved_per_year', 20000),
            _num(a, 'freeze_years', 2),
            _num(a, 'rgb_hike', 3.0),
            _num(a, 'attrition_per_year', 10000),
            _num(a, 'median_stabilized_rent', 1500),
            _num(a, 'years', 10))
    if fn == 'sources':
        return {'sources': housing.SOURCES}
    return None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):  # quiet — pm2 keeps the logs
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _fn(self, path):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in READ_FNS:
                return path[len(pre):]
        return None

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'
        fn = self._fn(path)
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                out = api(fn, args)
            except Exception as e:  # noqa: BLE001 — the caller gets the reason
                # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
                return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
            return self._json(out)

        # The console. Bare /mandamistan + relative assets is the gateway trap.
        if raw == PREFIX:
            self.send_response(301)
            self.send_header('Location', PREFIX + '/')
            self.end_headers()
            return
        if raw.startswith(PREFIX + '/'):
            self.path = self.path[len(PREFIX):]
        if urlparse(self.path).path.rstrip('/') in ('', '/'):
            self.path = '/index.html'
        return super().do_GET()


def serve(port=None, host='0.0.0.0'):
    port = int(port or _config().get('port', 51240))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'mandamistan: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/plan')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    args = ap.parse_args()
    serve(args.port, args.host)
