"""REST + MCP HTTP server. Errors are 4xx JSON — Cloudflare strips 5xx bodies."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import api, client, mcp_server


def _routes(path: str, qs: dict, body: dict):
    q = body.get('q') or (qs.get('q') or [''])[0]
    k = int(body.get('k') or (qs.get('k') or [10])[0])
    page = int(body.get('page') or (qs.get('page') or [1])[0])
    if path == 'health':
        return {'ok': True, 'cache': client.cache_stats()}
    if path == 'music':
        return api.music(q, k=k, page=page)
    if path == 'sfx':
        return api.sfx(q, k=k, page=page)
    if path == 'footage':
        return api.footage(q, k=k, page=page)
    if path == 'templates':
        return api.templates(q, k=k, page=page)
    if path == 'voices':
        return api.voices(page=page, k=k)
    if path == 'song':
        ids = body.get('ids') or [s for s in (qs.get('ids') or [''])[0].split(',') if s]
        return api.songs(ids)
    if path == 'gq':
        return client.gql(body['query'], body.get('variables'))
    raise FileNotFoundError(path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, body: dict):
        u = urlparse(self.path)
        # Also served under /artlist/api/… behind the gateway.
        path = u.path.strip('/').removeprefix('artlist/').removeprefix('api/').strip('/')
        try:
            if path == 'mcp':
                resp = mcp_server.handle_message(body)
                return self._send(200, resp if resp is not None else {})
            self._send(200, _routes(path or 'health', parse_qs(u.query), body))
        except FileNotFoundError:
            self._send(404, {'error': f'no such endpoint: {path}'})
        except (client.ArtlistError, KeyError, ValueError) as e:
            self._send(400, {'error': str(e)[:500]})
        except Exception as e:
            self._send(400, {'error': f'{type(e).__name__}: {e}'[:500]})

    def do_GET(self):
        self._handle({})

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(n) or b'{}')
        except json.JSONDecodeError:
            return self._send(400, {'error': 'invalid JSON body'})
        self._handle(body if isinstance(body, dict) else {})


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=51190)
    p.add_argument('--host', default='127.0.0.1')
    a = p.parse_args()
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
