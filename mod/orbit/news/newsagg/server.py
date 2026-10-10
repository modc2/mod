"""
newsagg.server — REST + MCP on one port, stdlib only.

    GET  /health
    GET  /search?q=&k=&hours=&sources=gdelt,hn
    GET  /read?url=&max_chars=
    GET  /sources
    GET  /feeds          POST /feeds {url,name}     DELETE /feeds?url=
    POST /mcp            (MCP streamable HTTP, JSON responses)

Paths are also accepted under /news and /news/api (gateway convention).
Failures are 4xx with a JSON {error}: Cloudflare eats 5xx bodies.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import cache, engine, sources, store
from .mcp_server import SERVER_INFO, handle_message

PREFIXES = ('/news/api', '/news', '/api')


def route(path: str) -> str:
    for p in PREFIXES:
        if path == p or path.startswith(p + '/'):
            return path[len(p):] or '/'
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = 'mod-news/0.1'

    def log_message(self, *a):
        pass

    def _send(self, code: int, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}') if n else {}

    def _run(self, fn):
        try:
            self._send(200, fn())
        except (ValueError, KeyError, TypeError) as e:
            self._send(400, {'error': f'{type(e).__name__}: {e}'})
        except Exception as e:
            self._send(424, {'error': f'{type(e).__name__}: {e}'})   # upstream failed; not 5xx

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = route(u.path)
        if path in ('/', '/health'):
            return self._send(200, {'ok': True, **SERVER_INFO, 'cache': cache.stats()})
        if path == '/search':
            srcs = [s for s in q.get('sources', '').split(',') if s] or None
            return self._run(lambda: engine.search(q.get('q') or q.get('query', ''), k=int(q.get('k', 20)),
                                                   sources=srcs, hours=float(q.get('hours', 72))))
        if path == '/read':
            return self._run(lambda: engine.read(q['url'], int(q.get('max_chars', 8000))))
        if path == '/sources':
            return self._send(200, {n: {'default': s['default'], 'docs': s['docs']}
                                    for n, s in sources.SOURCES.items()})
        if path == '/feeds':
            return self._send(200, {'feeds': store.feeds()})
        if path == '/mcp':
            return self._send(405, {'error': 'POST JSON-RPC to /mcp; no SSE stream is offered'})
        self._send(404, {'error': f'no route {path}'})

    def do_POST(self):
        path = route(urllib.parse.urlsplit(self.path).path)
        try:
            body = self._body()
        except json.JSONDecodeError:
            return self._send(400, {'jsonrpc': '2.0', 'id': None,
                                    'error': {'code': -32700, 'message': 'parse error'}})
        if path == '/mcp':
            resp = handle_message(body)
            return self._send(202, b'') if resp is None else self._send(200, resp)
        if path == '/search':
            return self._run(lambda: engine.search(body.get('query', ''), k=int(body.get('k', 20)),
                                                   sources=body.get('sources'), hours=float(body.get('hours', 72))))
        if path == '/feeds':
            return self._run(lambda: store.add(body['url'], body.get('name', '')))
        self._send(404, {'error': f'no route {path}'})

    def do_DELETE(self):
        u = urllib.parse.urlsplit(self.path)
        if route(u.path) == '/feeds':
            url = urllib.parse.parse_qs(u.query).get('url', [''])[-1]
            return self._run(lambda: store.remove(url))
        self._send(404, {'error': 'no route'})


def serve(port: int = 51120, host: str = '127.0.0.1') -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'news on http://{host}:{port}  (mcp: /mcp)', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=51120)
    ap.add_argument('--host', default='127.0.0.1')
    a = ap.parse_args()
    serve(a.port, a.host)
