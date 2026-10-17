"""Server for saygm: console + mod protocol API + a /v1 passthrough.

Standalone so pm2 can run it directly (``python3 serve.py``).

    /saygm/*            → the console (prefix kept by the gateway; stripped here)
    /saygm/api/{fn}     → the API, as the console calls it
    /api/saygm/{fn}     → the API (legacy gateway alias)
    /{fn}               → the API, bare, for local curl
    /v1/*, /v1beta/*    → raw passthrough to api.saygm.com (also under /saygm/)

Whose key pays. The stored house key (~/.mod/saygm/key.json) only ever
pays for loopback callers. Anyone arriving through the gateway brings their
own: ``x-saygm-key`` on the API, or the usual ``Authorization: Bearer`` /
``x-api-key`` on /v1. set_key is loopback-only.

    python3 serve.py [--port 51270] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/saygm'
MAX_BODY = 8 * 1024 * 1024

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import gm  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'models', 'model', 'labs', 'quote', 'credits')
WRITE_FNS = ('chat', 'set_key', 'refresh')
LOCAL_ONLY = ('set_key', 'refresh')
# Passthrough: only the headers an upstream SDK call needs travel.
PASS_HEADERS = ('content-type', 'accept', 'anthropic-version', 'anthropic-beta',
                'x-goog-api-client')


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def api(fn, a, key=None):
    """``key`` is the caller's key, or None to fall back to the house key."""
    if fn == 'health':
        return {'ok': True, 'key_set': bool(gm.load_key())}
    if fn == 'info':
        cfg = _config()
        return {'name': 'saygm', 'version': cfg.get('version'),
                'description': cfg.get('description'), 'upstream': gm.API,
                'key': gm.mask(gm.load_key()), 'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'models':
        rows = gm.models(a.get('q', ''), a.get('tier', ''), a.get('lab', ''),
                         a.get('shape', ''), a.get('confidential'),
                         a.get('available', True), a.get('sort', 'id'), a.get('limit', 200))
        return {'count': len(rows), 'models': rows}
    if fn == 'model':
        return gm.model(a.get('id', ''))
    if fn == 'labs':
        return {'labs': gm.labs()}
    if fn == 'quote':
        return gm.quote(a.get('model', ''), a.get('input_tokens', 1000),
                        a.get('output_tokens', 1000))
    if fn == 'credits':
        return gm.credits(key)
    if fn == 'chat':
        return gm.chat(a.get('model', ''), a.get('prompt', ''), a.get('messages'),
                       a.get('system', ''), a.get('max_tokens', 1024),
                       a.get('temperature'), a.get('confirm', False), key)
    if fn == 'set_key':
        return gm.save_key(a.get('key', ''))
    if fn == 'refresh':
        return {'count': len(gm.fetch_models(refresh=True))}
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
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, Authorization, x-api-key, x-saygm-key, '
                         'anthropic-version, anthropic-beta, x-goog-api-key')
        self.end_headers()

    def _local(self):
        # A gateway on this box also connects from loopback, so a forwarded
        # request is treated as remote whatever its socket says.
        return (self.client_address[0] in ('127.0.0.1', '::1')
                and not self.headers.get('X-Forwarded-For'))

    def _caller_key(self):
        """The caller's own SayGM key, or None for 'use the house key'.
        Raises PermissionError for a remote caller with no key."""
        k = self.headers.get('x-saygm-key')
        if k:
            return k
        if self._local():
            return None
        raise PermissionError('bring your own SayGM key in the x-saygm-key header')

    def _fn(self, path, allowed):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in allowed:
                return path[len(pre):]
        return None

    def _run(self, fn, args):
        try:
            key = self._caller_key() if fn in ('chat', 'credits') else None
            out = api(fn, args, key)
        except PermissionError as e:
            return self._json({'error': str(e)}, 401)
        except gm.GMError as e:
            # 4xx, not 5xx: Cloudflare swaps 5xx bodies for its own page.
            return self._json({'error': str(e)}, e.status if 400 <= e.status < 500 else 424)
        except (ValueError, KeyError, TypeError) as e:
            return self._json({'error': str(e)}, 400)
        except Exception as e:  # noqa: BLE001 — the caller gets the reason
            return self._json({'error': f'{type(e).__name__}: {e}'}, 422)
        return self._json(out)

    # ── /v1 passthrough ──────────────────────────────────────────

    def _passthrough_path(self, raw):
        for pre in (PREFIX, f'/api{PREFIX}', ''):
            rest = raw[len(pre):] if raw.startswith(pre) else None
            if rest is not None and (rest.startswith('/v1/') or rest.startswith('/v1beta/')):
                return rest
        return None

    def _passthrough(self, method, upath):
        q = urlparse(self.path).query
        hdr = {'User-Agent': gm.UA}
        for h in PASS_HEADERS:
            if self.headers.get(h):
                hdr[h] = self.headers[h]
        auth = self.headers.get('Authorization')
        xkey = self.headers.get('x-api-key') or self.headers.get('x-goog-api-key')
        if auth or xkey:
            if auth:
                hdr['Authorization'] = auth
            if xkey:
                hdr['x-api-key'] = xkey
        elif upath != '/v1/models':
            if not self._local():
                return self._json({'error': {'message': 'send your SayGM key '
                                             '(Authorization: Bearer or x-api-key)'}}, 401)
            house = gm.load_key()
            if not house:
                return self._json({'error': {'message': 'no house key — m saygm/set_key'}}, 401)
            hdr['Authorization'] = f'Bearer {house}'
            hdr['x-api-key'] = house
        data = None
        if method == 'POST':
            n = int(self.headers.get('Content-Length') or 0)
            if n > MAX_BODY:
                return self._json({'error': 'body too large'}, 413)
            data = self.rfile.read(n)
        url = gm.API + upath + (f'?{q}' if q else '')
        req = urllib.request.Request(url, data=data, method=method, headers=hdr)
        try:
            r = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            r = e
        except urllib.error.URLError as e:
            return self._json({'error': {'message': f'saygm unreachable: {e.reason}'}}, 424)
        status = r.status if hasattr(r, 'status') else r.code
        self.send_response(424 if status >= 500 else status)
        self.send_header('Content-Type', r.headers.get('Content-Type', 'application/json'))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        # Close-delimited body (HTTP/1.0 handler) so SSE streams through as-is.
        while True:
            chunk = r.read1(8192) if hasattr(r, 'read1') else r.read(8192)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()

    # ── verbs ────────────────────────────────────────────────────

    def do_POST(self):
        raw = urlparse(self.path).path
        up = self._passthrough_path(raw)
        if up:
            return self._passthrough('POST', up)
        fn = self._fn(raw.rstrip('/'), WRITE_FNS + ('credits',))
        if not fn:
            return self._json({'error': 'not found'}, 404)
        if fn in LOCAL_ONLY and not self._local():
            return self._json({'error': f'{fn} is local-only'}, 403)
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            return self._json({'error': 'body too large'}, 413)
        try:
            args = json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return self._json({'error': 'body must be JSON'}, 400)
        if not isinstance(args, dict):
            return self._json({'error': 'body must be a JSON object'}, 400)
        return self._run(fn, args)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        up = self._passthrough_path(raw)
        if up:
            return self._passthrough('GET', up)
        path = raw.rstrip('/') or '/'
        fn = self._fn(path, READ_FNS)
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._run(fn, args)

        # The console. Bare /saygm + relative asset paths is the gateway trap.
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
    port = int(port or _config().get('port', 51270))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'saygm: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/models  '
          f'sdk base_url http://localhost:{port}/v1')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
