"""REST + MCP HTTP server, plus the console. Errors are 4xx JSON — Cloudflare strips 5xx bodies.

One port, three jobs:
    /artlist/            the console (app/ in this module: index.html, app.css, app.js, fonts/)
    /artlist/api/<fn>    the REST API (also bare: /music, /sfx, …, /mcp)
    /artlist/stream?u=   audio proxy — Artlist's CDN 403s any Referer but artlist.io's, so a
                         browser cannot play previews directly; this relays them with Range intact.
The /artlist prefix is optional everywhere, so the same server works on a bare port and behind
the gateway, where the app route keeps the prefix and the api route strips it.
"""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import api, client, mcp_server

APP_DIR = Path(__file__).resolve().parent.parent / 'app'
MEDIA = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
         '.woff2': 'font/woff2', '.json': 'application/json', '.txt': 'text/plain; charset=utf-8'}
# Headers relayed from the CDN to the browser on /stream — the ones <audio> needs for seeking.
STREAM_HEADERS = ('content-type', 'content-length', 'content-range', 'accept-ranges', 'last-modified', 'etag')


def _routes(path: str, qs: dict, body: dict):
    q = body.get('q') or (qs.get('q') or [''])[0]
    k = int(body.get('k') or (qs.get('k') or [10])[0])
    page = int(body.get('page') or (qs.get('page') or [1])[0])
    sort = body.get('sort') or (qs.get('sort') or [''])[0]
    if path == 'health':
        return {'ok': True, 'cache': client.cache_stats()}
    if path == 'music':
        return api.music(q, k=k, page=page, sort=int(sort or 1))
    if path == 'sfx':
        return api.sfx(q, k=k, page=page, sort=sort or 'NEWEST')
    if path == 'footage':
        return api.footage(q, k=k, page=page)
    if path == 'templates':
        return api.templates(q, k=k, page=page, sort=sort or 'TOP_DOWNLOADS')
    if path == 'voices':
        return api.voices(page=page, k=k)
    if path == 'song':
        ids = body.get('ids') or [s for s in (qs.get('ids') or [''])[0].split(',') if s]
        return api.songs(ids)
    if path == 'gq':
        return client.gql(body['query'], body.get('variables'))
    raise FileNotFoundError(path)


def static_file(path: str) -> Path | None:
    """The console file for a request path, or None if it is not one. Allowlist = files under app/."""
    name = path or 'index.html'
    if name == 'index.html' or name in ('app.css', 'app.js') or name.startswith('fonts/'):
        f = (APP_DIR / name).resolve()
        if APP_DIR in f.parents and f.is_file():
            return f
    return None


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

    def _send_file(self, f: Path):
        data = f.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', MEDIA.get(f.suffix, 'application/octet-stream'))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache' if f.suffix == '.html' else 'public, max-age=3600')
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, qs: dict):
        """Relay one CDN preview with the Referer the CDN insists on, passing Range through."""
        url = (qs.get('u') or [''])[0]
        host = urllib.parse.urlparse(url).hostname or ''
        if not (url.startswith('https://') and (host == 'artlist.io' or host.endswith('.artlist.io'))):
            return self._send(400, {'error': 'stream only relays https://*.artlist.io previews'})
        headers = {'User-Agent': client.HEADERS['User-Agent'], 'Referer': 'https://artlist.io/'}
        if self.headers.get('Range'):
            headers['Range'] = self.headers['Range']
        req = urllib.request.Request(url, headers=headers)
        try:
            upstream = urllib.request.urlopen(req, timeout=60)  # follows the CDN's 302, Referer kept
        except urllib.error.HTTPError as e:
            return self._send(400, {'error': f'CDN answered {e.code}'})
        except Exception as e:
            return self._send(400, {'error': f'{type(e).__name__}: {e}'[:300]})
        with upstream:
            self.send_response(upstream.status)
            for h in STREAM_HEADERS:
                if upstream.headers.get(h):
                    self.send_header(h, upstream.headers[h])
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            try:
                while chunk := upstream.read(1 << 16):
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the listener skipped to another track

    def _handle(self, body: dict, get: bool = False):
        u = urlparse(self.path)
        # Also served under /artlist/… and /artlist/api/… behind the gateway.
        segs = [s for s in u.path.split('/') if s]
        if segs[:1] == ['artlist']:
            segs = segs[1:]
        is_api = segs[:1] == ['api']
        if is_api:
            segs = segs[1:]
        path = '/'.join(segs) or ('health' if is_api else '')
        qs = parse_qs(u.query)
        try:
            if get and path == 'stream':
                return self._stream(qs)
            if get and (f := static_file(path)):
                return self._send_file(f)
            if path == 'mcp':
                resp = mcp_server.handle_message(body)
                return self._send(200, resp if resp is not None else {})
            self._send(200, _routes(path or 'health', qs, body))
        except FileNotFoundError:
            self._send(404, {'error': f'no such endpoint: {path}'})
        except (client.ArtlistError, KeyError, ValueError) as e:
            self._send(400, {'error': str(e)[:500]})
        except Exception as e:
            self._send(400, {'error': f'{type(e).__name__}: {e}'[:500]})

    def do_GET(self):
        self._handle({}, get=True)

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
