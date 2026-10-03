#!/usr/bin/env python3
"""0xprof console — the app half of the module, on its own port.

Two jobs and nothing else:

    /0xprof/*         the files in this directory, base path kept
    /0xprof/_api/*    reverse-proxied to the API on :50610

The second is the whole reason this is a proxy and not a static file server.
The page pins `<base href="/0xprof/">` and asks its *own origin* for `_api`, so
one build works behind the gateway and on a bare port alike, with no CORS
preflight on the hot path and no per-deployment API URL baked into the console.

Zero dependencies on purpose: the console is plain ES modules and this server
is stdlib, so the app half stays up while the API half restarts — it will
render and say the API is down, which beats not answering.

    python3 server.py                            # :50611, API at :50610
    python3 server.py --port 8080 --api http://box:50610
"""
import argparse
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE = '/0xprof'
API_PREFIX = f'{BASE}/_api'
API = os.environ.get('ZKPROF_API', 'http://127.0.0.1:50610').rstrip('/')
PORT = int(os.environ.get('ZKPROF_APP_PORT', 50611))
HOST = os.environ.get('ZKPROF_APP_HOST', '0.0.0.0')

# Compiling a plonk verifier and asking mainnet to run it is measured in
# seconds, not milliseconds. This only has to outlast the slowest method so the
# proxy is never the thing that gives up first.
TIMEOUT = float(os.environ.get('ZKPROF_PROXY_TIMEOUT', 300))

MEDIA = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
         '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
         '.wasm': 'application/wasm', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
         '.txt': 'text/plain; charset=utf-8', '.map': 'application/json'}

HOP = {'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer',
       'upgrade', 'proxy-authorization', 'proxy-authenticate', 'host',
       'content-length'}


class Handler(BaseHTTPRequestHandler):
    server_version = '0xprof-console/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        if os.environ.get('ZKPROF_APP_VERBOSE'):
            sys.stderr.write(f'{self.address_string()} {fmt % args}\n')

    # ── routing ──────────────────────────────────────────────────────

    def do_GET(self):
        if self.path.startswith(API_PREFIX):
            return self.proxy('GET')
        return self.serve_file()

    def do_HEAD(self):
        return self.serve_file(head_only=True)

    def do_POST(self):
        return self.proxy('POST')

    def do_DELETE(self):
        return self.proxy('DELETE')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('access-control-allow-origin', '*')
        self.send_header('access-control-allow-headers', '*')
        self.send_header('access-control-allow-methods', 'GET,POST,DELETE,OPTIONS')
        self.send_header('content-length', '0')
        self.end_headers()

    # ── the two jobs ─────────────────────────────────────────────────

    def proxy(self, method: str):
        if not self.path.startswith(API_PREFIX):
            return self.send_error(404, 'not an API path')
        target = API + (self.path[len(API_PREFIX):] or '/')
        length = int(self.headers.get('content-length') or 0)
        body = self.rfile.read(length) if length else None
        request = urllib.request.Request(target, data=body, method=method)
        for name, value in self.headers.items():
            if name.lower() not in HOP:
                request.add_header(name, value)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                self.relay(response.status, response.headers, response.read())
        except urllib.error.HTTPError as e:
            self.relay(e.code, e.headers, e.read())
        except Exception as e:
            # The API being down is a state the console renders, not a crash.
            payload = (f'{{"error":"the API on {API} did not answer: '
                       f'{type(e).__name__}"}}').encode()
            self.relay(502, {'content-type': 'application/json'}, payload)

    def relay(self, status, headers, body: bytes):
        self.send_response(status)
        for name, value in (headers.items() if hasattr(headers, 'items') else []):
            if name.lower() not in HOP:
                self.send_header(name, value)
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, head_only: bool = False):
        path = self.path.split('?', 1)[0]
        if path in ('/', BASE, f'{BASE}/'):
            path = f'{BASE}/index.html'
        if not path.startswith(f'{BASE}/'):
            self.send_response(302)
            self.send_header('location', f'{BASE}/')
            self.send_header('content-length', '0')
            self.end_headers()
            return
        target = (APP_DIR / path[len(BASE) + 1:]).resolve()
        if not str(target).startswith(str(APP_DIR.resolve())) or not target.is_file():
            target = APP_DIR / 'index.html'          # single page, deep links work
        body = target.read_bytes()
        self.send_response(200)
        self.send_header('content-type', MEDIA.get(target.suffix, 'application/octet-stream'))
        self.send_header('content-length', str(len(body)))
        self.send_header('cache-control', 'no-cache')
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


def main():
    global API
    parser = argparse.ArgumentParser(description='0xprof console')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    # Arguments win over the environment, so the service keeps its ports across
    # a pm2 resurrect that has lost the env it was started with.
    API = args.api.rstrip('/')
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'0xprof console on http://{args.host}:{args.port}{BASE} → API {API}',
          flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
