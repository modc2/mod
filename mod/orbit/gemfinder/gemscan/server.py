"""
gemscan.server — REST on one port, stdlib only.

    GET /health
    GET /scan?asset=all|crypto|stock&k=20&narrative=&min_score=&sources=a,b&hn=1
    GET /narratives?asset=
    GET /history?n=10
    GET /sources

Paths are also accepted under /gemfinder and /gemfinder/api (gateway convention).
Failures are 4xx with a JSON {error}: Cloudflare eats 5xx bodies.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import engine, http, narratives, sources

PREFIXES = ('/gemfinder/api', '/gemfinder', '/api')


def route(path: str) -> str:
    for p in PREFIXES:
        if path == p or path.startswith(p + '/'):
            return path[len(p):] or '/'
    return path


def _bool(v) -> bool:
    return str(v).lower() not in ('0', 'false', 'no', 'off', '')


def source_list() -> dict:
    return {n: {'asset': s['asset'], 'docs': (s['fn'].__doc__ or '').strip()}
            for n, s in sources.CANDIDATE_SOURCES.items()}


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

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = route(u.path).rstrip('/') or '/'
        try:
            if path in ('/', '/health'):
                body = {'ok': True, 'module': 'gemfinder', 'cache': http.stats(),
                        'narratives': narratives.ids()}
            elif path == '/scan':
                body = engine.scan(asset=q.get('asset', 'all'), k=int(q.get('k', 20)),
                                   narrative=q.get('narrative') or None, min_score=float(q.get('min_score', 0)),
                                   sources=[s for s in q.get('sources', '').split(',') if s] or None,
                                   hn=_bool(q.get('hn', '1')))
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
    a = ap.parse_args()
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
