#!/usr/bin/env python3
"""eth console — the app half of the module, on its own port.

Two jobs and nothing else:

    /eth/*         the files in this directory, base path kept
    /ethdesk/_api/*    reverse-proxied to the API on :50750

The second is the whole reason this is a proxy rather than a static file
server. The page pins `<base href="/eth/">` and asks its *own origin* for
`_api`, so one build works behind the gateway (modc2.com/eth) and on a bare
port alike — no CORS preflight on the hot path, no per-deployment API url baked
into the console, and the wallet token never crosses an origin.

Zero dependencies on purpose: the console is plain ES modules and this server is
stdlib, so the app half stays up while the API half restarts — it will render
and say the API is down, which beats not answering.

    python3 server.py                          # :50751, API at :50750
    python3 server.py --port 8080 --api http://box:50750
"""
import argparse
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE = '/ethdesk'
API_PREFIX = f'{BASE}/_api'
API = os.environ.get('ETH_API', 'http://127.0.0.1:50750').rstrip('/')
PORT = int(os.environ.get('ETH_APP_PORT', 50751))
HOST = os.environ.get('ETH_APP_HOST', '0.0.0.0')

# A deploy crosses this proxy and then waits for a block. On a slow public
# testnet that is minutes; the timeout only has to outlast the slowest hop so
# the proxy is never the thing that gives up first.
TIMEOUT = float(os.environ.get('ETH_PROXY_TIMEOUT', 600))

MEDIA = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
         '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
         '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8',
         '.sol': 'text/plain; charset=utf-8', '.map': 'application/json'}

HOP = {'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer',
       'upgrade', 'proxy-authorization', 'proxy-authenticate', 'host',
       'content-length'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'eth-console/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        if os.environ.get('ETH_APP_VERBOSE'):
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

    def do_PUT(self):
        return self.proxy('PUT')

    def do_DELETE(self):
        return self.proxy('DELETE')

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('access-control-allow-origin', '*')
        self.send_header('access-control-allow-headers', '*')
        self.send_header('access-control-allow-methods',
                         'GET,POST,PUT,DELETE,OPTIONS')
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
            payload = (f'{{"detail":"the API on {API} did not answer: '
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
        self.send_header('content-type',
                         MEDIA.get(target.suffix, 'application/octet-stream'))
        self.send_header('content-length', str(len(body)))
        self.send_header('cache-control', 'no-cache')
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


def main():
    global API
    parser = argparse.ArgumentParser(description='eth console')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    # Arguments win over the environment, so the service keeps its ports across
    # a pm2 resurrect that has lost the env it was started with.
    API = args.api.rstrip('/')
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'eth console on http://{args.host}:{args.port}{BASE} → API {API}',
          flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
