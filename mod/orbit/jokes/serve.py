"""Server for the jokes console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

    /jokes/*            → the console (prefix kept by the gateway; stripped here)
    /jokes/api/{fn}     → the API, as the console calls it
    /api/jokes/{fn}     → the API (prefix stripped by the gateway)
    /{fn}               → the API, bare, for local curl

Reads are GET with query args. Writes (add, vote, import_pack, remove) are
POST with a JSON body. remove only answers a loopback caller: anyone you
share the console with can add and vote, only this box can delete.

    python3 serve.py [--port 51130] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/jokes'
MAX_BODY = 4 * 1024 * 1024

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import jokebook  # noqa: E402

READ_FNS = ('health', 'info', 'readme', 'get', 'search', 'random',
            'comedians', 'tags', 'share', 'export')
WRITE_FNS = ('add', 'vote', 'import_pack', 'remove')
LOCAL_ONLY = ('remove',)

_book = None


def book():
    global _book
    if _book is None:
        _book = jokebook.Jokebook()
    return _book


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def api(fn, a, host=''):
    b = book()
    if fn == 'health':
        return {'ok': True, **b.stats()}
    if fn == 'info':
        cfg = _config()
        return {'name': 'jokes', 'version': cfg.get('version'),
                'description': cfg.get('description'), 'stats': b.stats(),
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    if fn == 'get':
        return b.get(a.get('id', ''))
    if fn == 'search':
        return b.search(a.get('q', ''), a.get('comedian', ''), a.get('tag', ''),
                        a.get('sort', 'new'), a.get('limit', 50), a.get('offset', 0))
    if fn == 'random':
        return b.random(a.get('comedian', ''), a.get('tag', ''))
    if fn == 'comedians':
        return {'comedians': b.comedians()}
    if fn == 'tags':
        return {'tags': b.tags()}
    if fn == 'share':
        j = b.get(a.get('id', ''))
        if not j:
            return {'error': 'no such joke'}
        credit = j['comedian'] + (f" — {j['source']}" if j['source'] else '')
        return {'id': j['id'], 'url': f"{host}{PREFIX}/?j={j['id']}",
                'text': f"{j['text']}\n\n— {credit}",
                'pack': b.export(ids=[j['id']])}
    if fn == 'export':
        return b.export(a.get('ids'), a.get('comedian', ''), a.get('tag', ''),
                        a.get('name', ''))
    if fn == 'add':
        return b.add(a.get('text'), a.get('comedian', ''), a.get('source', ''),
                     a.get('year'), a.get('tags'), a.get('by', ''))
    if fn == 'vote':
        return b.vote(a.get('id', ''), a.get('delta', 1))
    if fn == 'import_pack':
        return b.import_pack(a.get('pack', a), a.get('by', ''))
    if fn == 'remove':
        return b.remove(a.get('id', ''))
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

    def _host(self):
        h = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or ''
        proto = self.headers.get('X-Forwarded-Proto') or 'http'
        return f'{proto}://{h}' if h else ''

    def _run(self, fn, args):
        try:
            out = api(fn, args, self._host())
        except (ValueError, KeyError) as e:
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
        # A gateway on this box also connects from loopback, so a forwarded
        # request is treated as remote whatever its socket says.
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
            args = {'pack': args}
        return self._run(fn, args)

    def do_GET(self):
        parsed = urlparse(self.path)
        raw = parsed.path
        path = raw.rstrip('/') or '/'
        fn = self._fn(path, READ_FNS)
        if fn:
            args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            return self._run(fn, args)

        # The console. Bare /jokes + relative asset paths is the gateway trap.
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
    port = int(port or _config().get('port', 51130))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'jokes: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/search')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
