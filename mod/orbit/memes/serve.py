"""Server for the memes console: static bundle + the mod protocol API.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader.

One process answers both halves of the protocol's URL rule:

    /memes/*            → the console (prefix kept by the gateway; stripped here)
    /memes/api/{fn}     → the API, as the console calls it — one relative path
                          that resolves the same locally and behind the gateway
    /api/memes/{fn}     → the API (prefix stripped by the gateway, so the
                          protocol routes land at the root)
    /{fn}               → the API, bare, for local curl

    python3 serve.py [--port 50900] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/memes'

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

import sites  # noqa: E402


def _config():
    try:
        with open(os.path.join(MODULE_DIR, 'config.json')) as f:
            return json.load(f)
    except Exception:
        return {}


def _bool(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def api(fn, args):
    q = args.get('q', [''])[0]
    limit = args.get('limit', ['24'])[0]
    nsfw = _bool(args.get('nsfw', ['0'])[0])
    if fn == 'health':
        return {'ok': True, 'sources': sites.SOURCES}
    if fn == 'info':
        cfg = _config()
        return {'name': 'memes', 'version': cfg.get('version'),
                'description': cfg.get('description'),
                'endpoints': cfg.get('endpoints', {})}
    if fn == 'sources':
        return {'sources': sites.SOURCES, 'subreddits': sites.SUBS}
    if fn == 'search':
        return sites.search(q, source=args.get('source', ['all'])[0],
                            limit=limit, nsfw=nsfw)
    if fn == 'trending':
        return sites.trending(limit=limit, nsfw=nsfw)
    if fn == 'random':
        return sites.random_meme(nsfw=nsfw)
    if fn == 'templates':
        return {'templates': sites.imgflip_search(q, limit=limit or 100)}
    if fn == 'readme':
        try:
            with open(os.path.join(MODULE_DIR, 'README.md')) as f:
                return {'readme': f.read()}
        except Exception:
            return {'readme': None}
    return None


API_FNS = ('health', 'info', 'sources', 'search', 'trending', 'random',
           'templates', 'readme')


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

    def do_GET(self):
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)
        raw = parsed.path
        path = raw.rstrip('/') or '/'

        # Every spelling of the API collapses to a bare fn name.
        fn = None
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in API_FNS:
                fn = path[len(pre):]
                break
        if fn:
            try:
                out = api(fn, args)
            except Exception as e:  # noqa: BLE001 — the caller gets the reason
                return self._json({'error': f'{type(e).__name__}: {e}'}, 500)
            return self._json(out)

        # The console. /memes and /memes/... map into web/; the gateway trap
        # is the bare /memes with no slash + relative asset paths, so redirect.
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
    port = int(port or _config().get('port', 50900))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f'memes: http://localhost:{port}{PREFIX}/  api http://localhost:{port}/search?q=…')
    httpd.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=None)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
