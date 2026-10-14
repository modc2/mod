"""
gemscan.server — REST on one port, stdlib only.

    GET /            the app (app/index.html)
    GET /health
    GET /scan?asset=all|crypto|stock&k=20&narrative=&q=&min_score=&sources=a,b&hn=1&fresh=0
    GET /narratives?asset=
    GET /history?n=10
    GET /sources
    GET /cache          scan-cache state;  DELETE /cache  (or GET /cache/clear) drops it

Scans are cached in-process (SCAN_TTL, default 10 min) and a warmer refreshes
the asset=all base on boot and every TTL, so the app opens from cache in
milliseconds. ?fresh=1 forces a live rescan. ?q= is a typed theme.

Paths are also accepted under /gemfinder and /gemfinder/api (gateway convention).
Failures are 4xx with a JSON {error}: Cloudflare eats 5xx bodies.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import engine, http, narratives, sources

PREFIXES = ('/gemfinder/api', '/gemfinder', '/api')
APP = Path(__file__).resolve().parent.parent / 'app' / 'index.html'


def route(path: str) -> str:
    for p in PREFIXES:
        if path == p or path.startswith(p + '/'):
            return path[len(p):] or '/'
    return path


def _bool(v) -> bool:
    return str(v).lower() not in ('0', 'false', 'no', 'off', '')


def source_list() -> dict:
    out = {n: {'asset': s['asset'], 'docs': (s['fn'].__doc__ or '').strip()}
           for n, s in sources.CANDIDATE_SOURCES.items()}
    out.update({n: {'asset': s['asset'], 'theme': True, 'docs': (s['fn'].__doc__ or '').strip()}
                for n, s in sources.THEME_SOURCES.items()})
    return out


def warm(interval: int = None) -> threading.Thread:
    """Background: keep the asset=all scan hot so the app never waits on feeds."""
    interval = interval or max(60, engine.SCAN_TTL - 20)

    def loop():
        while True:
            try:
                engine.scan('all', k=50, save=False, fresh=True)
            except Exception:
                pass
            time.sleep(interval)
    t = threading.Thread(target=loop, name='gemfinder-warm', daemon=True)
    t.start()
    return t


class Handler(BaseHTTPRequestHandler):
    server_version = 'mod-gemfinder/0.1'

    def log_message(self, *a):
        pass

    def _send(self, code: int, body):
        data = json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.end_headers()
        self.wfile.write(data)

    def _html(self, data: bytes):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_DELETE(self):
        if route(urllib.parse.urlparse(self.path).path).rstrip('/') == '/cache':
            return self._send(200, engine.clear_scan_cache())
        self._send(404, {'error': 'no route'})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = route(u.path).rstrip('/') or '/'
        if path in ('/', '/index.html') and APP.exists():
            return self._html(APP.read_bytes())
        try:
            if path == '/health':
                body = {'ok': True, 'module': 'gemfinder', 'cache': http.stats(),
                        'narratives': narratives.ids()}
            elif path == '/scan':
                body = engine.scan(asset=q.get('asset', 'all'), k=int(q.get('k', 20)),
                                   narrative=q.get('narrative') or None, min_score=float(q.get('min_score', 0)),
                                   sources=[s for s in q.get('sources', '').split(',') if s] or None,
                                   hn=_bool(q.get('hn', '1')), save=_bool(q.get('save', '1')),
                                   q=q.get('q') or None, fresh=_bool(q.get('fresh', '0')))
            elif path == '/cache':
                body = engine.cache_stats()
            elif path == '/cache/clear':
                body = engine.clear_scan_cache()
            elif path == '/narratives':
                body = engine.narratives(asset=q.get('asset', 'all'), hn=_bool(q.get('hn', '1')))
            elif path == '/history':
                body = engine.history(int(q.get('n', 10)))
            elif path == '/sources':
                body = source_list()
            else:
                return self._send(404, {'error': f'no route {u.path}'})
            self._send(200, body)
        except (ValueError, KeyError, TypeError) as e:
            self._send(400, {'error': f'{type(e).__name__}: {e}'})
        except Exception as e:
            self._send(424, {'error': f'{type(e).__name__}: {e}'})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=51320)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--no-warm', action='store_true', help='do not keep the asset=all scan hot in the background')
    a = ap.parse_args()
    if not a.no_warm and os.environ.get('GEMFINDER_WARM', '1') != '0':
        warm()
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
