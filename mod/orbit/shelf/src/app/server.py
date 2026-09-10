#!/usr/bin/env python3
"""
shelf console — the app half, on its own port.

Two jobs, and nothing else:

    /shelf/*        the files in this directory, base path kept
    /shelf/_api/*   reverse-proxied to the API on :50570

The proxy is the reason this is not a static file server. The page pins
`<base href="/shelf/">` and asks its *own* origin for `_api`, so one build
works behind a router and on a bare port alike — no CORS preflight on the hot
path, no API URL baked in at deploy time. Whether the API is one hop away
(here) or one route away (a gateway) is a detail the console never learns.

Stdlib, like the API, so the app half stays up while the API half restarts: it
will render and say the API is down, which is more useful than not answering.

    python3 server.py                          # 127.0.0.1:50571
    python3 server.py --port 8080 --api http://127.0.0.1:50570

Loopback by default for the same reason the API is: this renders every
module's private state, and the redaction that makes it safe lives on the API's
read path, not in the page.
"""
import argparse
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE = '/shelf'
API_PREFIX = f'{BASE}/_api'
API = os.environ.get('SHELF_API', 'http://127.0.0.1:50570').rstrip('/')
PORT = int(os.environ.get('SHELF_APP_PORT', 50571))
HOST = os.environ.get('SHELF_APP_HOST', '127.0.0.1')
TIMEOUT = float(os.environ.get('SHELF_PROXY_TIMEOUT', 120))

MEDIA = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
         '.css': 'text/css', '.json': 'application/json',
         '.svg': 'image/svg+xml', '.ico': 'image/x-icon'}

HOP = {'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer',
       'upgrade', 'proxy-authorization', 'proxy-authenticate', 'host',
       'content-length'}


class Console(BaseHTTPRequestHandler):
    server_version = 'shelf-app'
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def do_OPTIONS(self):
        self._send(204, b'', 'text/plain')

    def _route(self, method):
        path = self.path.split('?')[0]
        if self.path.startswith(API_PREFIX):
            return self._proxy(method)
        if method != 'GET':
            return self._send(405, b'{"error":"method not allowed"}', 'application/json')
        if path in ('/', BASE, BASE + '/'):
            return self._file('index.html')
        if path.startswith(BASE + '/'):
            return self._file(path[len(BASE) + 1:])
        # Bare paths work too, so hitting the port directly is not a 404 maze.
        return self._file(path.lstrip('/') or 'index.html')

    # ── static ───────────────────────────────────────────────────

    def _file(self, rel):
        rel = rel.split('?')[0].strip('/') or 'index.html'
        target = (APP_DIR / rel).resolve()
        # Serve only from this directory: the path comes off the wire.
        if not str(target).startswith(str(APP_DIR)) or not target.is_file():
            return self._send(404, b'not found', 'text/plain')
        body = target.read_bytes()
        self._send(200, body, MEDIA.get(target.suffix, 'application/octet-stream'))

    # ── proxy ────────────────────────────────────────────────────

    def _proxy(self, method):
        tail = self.path[len(API_PREFIX):] or '/'
        url = API + (tail if tail.startswith('/') else '/' + tail)
        body = None
        if method == 'POST':
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = 0
            body = self.rfile.read(length) if length else b'{}'

        request = urllib.request.Request(url, data=body, method=method)
        for name, value in self.headers.items():
            if name.lower() not in HOP:
                request.add_header(name, value)

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = response.read()
                ctype = response.headers.get('Content-Type', 'application/json')
                self._send(response.status, payload, ctype)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, exc.read() or b'{}',
                       exc.headers.get('Content-Type', 'application/json'))
        except Exception as exc:                       # noqa: BLE001
            # The API being down is a normal state for a console to render.
            import json
            self._send(502, json.dumps(
                {'error': 'API unreachable', 'api': API, 'detail': str(exc)}).encode(),
                'application/json')

    # ── plumbing ─────────────────────────────────────────────────

    def _send(self, status, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write(f'[shelf-app] {fmt % args}\n')


def main():
    parser = argparse.ArgumentParser(description='shelf console')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    globals()['API'] = args.api.rstrip('/')

    server = ThreadingHTTPServer((args.host, args.port), Console)
    print(f'[shelf-app] http://{args.host}:{args.port}{BASE} → API {API}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == '__main__':
    main()
