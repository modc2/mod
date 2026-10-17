"""Server for the reviews module: a tiny console + the mod protocol API.

    /reviews/*            → the console (prefix kept by the gateway; stripped here)
    /reviews/api/{fn}     → the API, as the console calls it
    /api/reviews/{fn}     → the API (prefix stripped by the gateway)
    /{fn}                 → the API, bare, for local curl

Reads are GET with query args. Writes are POST with a JSON body. Anyone may
rate (the caller names the rater; build vouches for identity when it mirrors
its reviews here) and anyone may publish a new aggregator (params only, never
code; an id cannot be taken over). Setting weights, a subject's default
aggregator, or retracting an aggregator is the operator's call: loopback only.

    python3 serve.py [--port 51440] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/reviews'
MAX_BODY = 1024 * 1024

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import reviews  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'aggregators', 'ratings', 'score',
            'subjects', 'weights', 'market')
WRITE_FNS = ('rate', 'unrate', 'aggregate', 'set_weights', 'set_aggregator',
             'publish', 'retract')
LOCAL_ONLY = ('set_weights', 'set_aggregator', 'retract')

_store = None


def store():
    global _store
    if _store is None:
        _store = reviews.Store()
    return _store


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def _json_arg(a, key):
    v = a.get(key)
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            raise ValueError(f'{key} must be JSON')
    return v


def api(fn, a):
    s = store()
    if fn == 'health':
        return {'ok': True, **s.stats()}
    if fn == 'info':
        cfg = _config()
        return {'name': 'reviews', 'version': cfg.get('version'),
                'description': cfg.get('description'), 'stats': s.stats(),
                'default_aggregator': reviews.DEFAULT_AGGREGATOR, 'scale': reviews.SCALE,
                'aggregators': reviews.aggregators(), 'published': s.published(),
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'aggregators':
        return {'default': reviews.DEFAULT_AGGREGATOR, 'scale': reviews.SCALE,
                'aggregators': reviews.aggregators(), 'published': s.published(),
                'publishable': list(reviews.PUBLISHABLE)}
    if fn == 'ratings':
        return {'subject': a.get('subject', ''), 'ratings': s.ratings(a.get('subject', ''), a.get('kind'))}
    if fn == 'score':
        return s.score(a.get('subject', ''), a.get('aggregator') or None, _json_arg(a, 'weights'))
    if fn == 'subjects':
        return {'subjects': s.subjects(a.get('prefix', ''))}
    if fn == 'weights':
        return s.weights(a.get('subject', '*'))
    if fn == 'rate':
        return s.rate(a.get('subject'), a.get('rater'), a.get('score'), a.get('kind', 'human'),
                      a.get('ref', ''), a.get('note', ''), a.get('source', ''))
    if fn == 'unrate':
        return s.unrate(a.get('subject', ''), a.get('rater', ''), a.get('ref', ''))
    if fn == 'aggregate':
        return s.aggregate(a.get('ratings') or [], a.get('aggregator') or None,
                           _json_arg(a, 'weights'), a.get('subject') or '')
    if fn == 'market':
        return s.market()
    if fn == 'publish':
        return s.publish(a.get('id'), a.get('base'), _json_arg(a, 'params'), a.get('title', ''),
                         a.get('author', ''), a.get('note', ''))
    if fn == 'retract':
        return s.retract(a.get('id', ''))
    if fn == 'set_weights':
        return s.set_weights(a.get('subject', '*'), _json_arg(a, 'weights') or {})
    if fn == 'set_aggregator':
        return s.set_aggregator(a.get('subject', ''), a.get('aggregator', ''))
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
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def _fn(self, path, allowed):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in allowed:
                return path[len(pre):]
        return None

    def _run(self, fn, args):
        try:
            out = api(fn, args)
        except (ValueError, KeyError, TypeError) as e:
            return self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001 — the caller gets the reason
            # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
            return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        return self._json(out)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/')
        fn = self._fn(path, WRITE_FNS)
        if not fn:
            return self._json({'error': 'not found'}, 404)
        local = (self.client_address[0] in ('127.0.0.1', '::1')
                 and not self.headers.get('X-Forwarded-For'))
        if fn in LOCAL_ONLY and not local:
            return self._json({'error': f'{fn} is local-only'}, 403)
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            return self._json({'error': 'body too large'}, 413)
        try:
            args = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._json({'error': 'body must be JSON'}, 400)
        if not isinstance(args, dict):
            return self._json({'error': 'body must be an object'}, 400)
        return self._run(fn, args)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'
        fn = self._fn(path, READ_FNS)
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._run(fn, args)
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
    port = int(port or _config().get('port', 51440))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'reviews: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/aggregators')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
