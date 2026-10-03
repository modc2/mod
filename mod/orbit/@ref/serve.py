"""Server for the @ref gimbal console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

One process answers both halves of the protocol's URL rule:

    /@ref/*            → the console (prefix kept by the gateway; stripped here)
    /@ref/api/{fn}     → the API, as the console calls it — one relative path
                         that resolves the same locally and behind the gateway
    /api/@ref/{fn}     → the API (prefix stripped by the gateway)
    /{fn}              → the API, bare, for local curl

/file?id= streams an indexed image's bytes — only paths already in the
index are ever served, so this is not a general file server.

    python3 serve.py [--port 51040] [--host 0.0.0.0]
"""

import argparse
import json
import mimetypes
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/@ref'

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import estimate  # noqa: E402
import orientation as ori  # noqa: E402
import store  # noqa: E402


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def _one(args, key, default=None):
    return args.get(key, [default])[0]


def _num(args, key, default=0):
    try:
        return float(_one(args, key, default))
    except (TypeError, ValueError):
        return float(default)


def _tags(args):
    raw = _one(args, 'tags')
    if not raw:
        return None
    return [t for t in raw.replace(',', ' ').split() if t]


def api(fn, args):
    if fn == 'health':
        return {'ok': True, 'db': store.DB_PATH,
                'estimators': estimate.available()}
    if fn == 'info':
        cfg = _config()
        return {'name': '@ref', 'version': cfg.get('version'),
                'description': cfg.get('description'),
                'stats': store.stats(), 'endpoints': cfg.get('endpoints', {})}
    if fn == 'stats':
        return store.stats()
    if fn == 'query':
        return store.query(_num(args, 'yaw'), _num(args, 'pitch'),
                           _num(args, 'roll'),
                           tolerance=_num(args, 'tolerance', 45),
                           limit=int(_num(args, 'limit', 24)),
                           tags=_tags(args), subject=_one(args, 'subject'),
                           roll_weight=_num(args, 'roll_weight', 0.5))
    if fn == 'describe':
        y, p, r = ori.normalize(_num(args, 'yaw'), _num(args, 'pitch'),
                                _num(args, 'roll'))
        return {'yaw': y, 'pitch': p, 'roll': r, 'view': ori.describe(y, p, r)}
    if fn == 'untagged':
        return store.untagged(limit=int(_num(args, 'limit', 50)))
    if fn == 'list':
        return store.listing(limit=int(_num(args, 'limit', 100)),
                             tags=_tags(args), subject=_one(args, 'subject'))
    if fn == 'scan':
        path = _one(args, 'path')
        if not path:
            return {'error': 'path= is required'}
        est = estimate.estimate if estimate.available() else None
        return store.scan(path, tags=_tags(args),
                          subject=_one(args, 'subject', 'figure'),
                          estimator=est)
    if fn == 'set':
        id = _one(args, 'id')
        if not id or _one(args, 'yaw') is None:
            return {'error': 'id= and yaw= are required'}
        return store.set_pose(id, _num(args, 'yaw'), _num(args, 'pitch'),
                              _num(args, 'roll'), tags=_tags(args))
    if fn == 'remove':
        id = _one(args, 'id')
        return store.remove(id) if id else {'error': 'id= is required'}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    return None


API_FNS = ('health', 'info', 'stats', 'query', 'describe', 'untagged',
           'list', 'scan', 'set', 'remove', 'readme')


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

    def _file(self, args):
        ref = store.get(_one(args, 'id', ''))
        path = ref.get('path')
        if not path or not os.path.isfile(path):
            return self._json({'error': 'unknown id'}, 404)
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        with open(path, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)
        raw = parsed.path
        path = raw.rstrip('/') or '/'

        # Every spelling of the API collapses to a bare fn name.
        fn = None
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            tail = path[len(pre):] if path.startswith(pre) else None
            if tail in API_FNS or tail == 'file':
                fn = tail
                break
        if fn == 'file':
            return self._file(args)
        if fn:
            try:
                out = api(fn, args)
            except Exception as e:  # noqa: BLE001 — the caller gets the reason
                return self._json({'error': f'{type(e).__name__}: {e}'}, 500)
            return self._json(out)

        # The console. /@ref and /@ref/... map into web/; the gateway trap
        # is the bare /@ref with no slash + relative asset paths, so redirect.
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
    port = int(port or _config().get('port', 51040))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'@ref: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/query?yaw=-45')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
