#!/usr/bin/env python3
"""wasmland console — the app half of the module, on its own port.

The fleet's convention is two processes per module: the API on `port` and the
console on `app_port`, so the router can send `/api/wasmland` to one and
`/wasmland` to the other. This is the second of those. It does two jobs and
nothing else:

    /wasmland/*        the files in this directory, base path kept
    /wasmland/_api/*   reverse-proxied to the API on :50480

The second one is the whole reason this is a proxy rather than a static file
server. The page pins `<base href="/wasmland/">` and asks its *own origin* for
`_api`, so one build works behind the gateway and on a bare port alike, with no
CORS preflight on the hot path and no per-deployment API URL baked into the
console. Whether the API is one hop away (here) or one route away (the gateway)
is a deployment detail the page never has to know.

Zero dependencies on purpose: the console is plain ES modules and this server
is stdlib, so the app half stays up even when the API half is being restarted —
it will render and say the API is down, which is better than not answering.

    python3 server.py                             # :50481, API at :50480
    python3 server.py --port 8080 --api http://box:50480
    WASMLAND_APP_PORT=8080 python3 server.py      # env works too

Arguments win over the environment, and are what `m wasmland/serve` passes, so
the service keeps its ports across a pm2 resurrect that has lost the env it was
started with.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE = '/wasmland'
API_PREFIX = f'{BASE}/_api'
API = os.environ.get('WASMLAND_API', 'http://127.0.0.1:50480').rstrip('/')
PORT = int(os.environ.get('WASMLAND_APP_PORT', 50481))
HOST = os.environ.get('WASMLAND_APP_HOST', '0.0.0.0')

# A run in the server venue is wall-clock bounded by the sandbox, not by us;
# this only has to be longer than that so the proxy is never the thing that
# gives up first.
TIMEOUT = float(os.environ.get('WASMLAND_PROXY_TIMEOUT', 180))

MEDIA = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
         '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
         '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
         '.wasm': 'application/wasm', '.woff2': 'font/woff2', '.map': 'application/json'}

# Hop-by-hop headers are per-connection and must not be forwarded either way.
HOP = {'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer',
       'upgrade', 'proxy-authorization', 'proxy-authenticate', 'host',
       'content-length'}


class Console(BaseHTTPRequestHandler):
    server_version = 'wasmland-app'
    protocol_version = 'HTTP/1.1'

    # ── routing ──────────────────────────────────────────────────

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route(head=True)

    def do_POST(self):
        self._route()

    def do_PUT(self):
        self._route()

    def do_PATCH(self):
        self._route()

    def do_DELETE(self):
        self._route()

    def do_OPTIONS(self):
        # Same-origin in the browser, so this only fires for other callers.
        self.send_response(204)
        self.send_header('access-control-allow-origin', '*')
        self.send_header('access-control-allow-methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS')
        self.send_header('access-control-allow-headers', 'authorization, content-type')
        self.send_header('content-length', '0')
        self.end_headers()

    def _route(self, head=False):
        path = self.path.split('?', 1)[0]
        if path.startswith(API_PREFIX):
            return self._proxy()
        if path == '/health':
            return self._health()
        if path in ('/', '', BASE):
            return self._redirect(f'{BASE}/')
        if path.startswith(f'{BASE}/'):
            return self._file(path[len(BASE) + 1:], head=head)
        # Anything else is somebody hitting the app port without the base path.
        # Send them to the console rather than 404ing on a bare `/app.css`.
        return self._redirect(f'{BASE}/')

    # ── the API, one hop away ────────────────────────────────────

    def _proxy(self):
        """Forward /wasmland/_api/* to the API, verbatim, both directions.

        Status codes are forwarded rather than normalised: the console reads
        402 as 'you have not bought this' and 401 as 'sign in', and both carry
        a `detail` the page shows. Swallowing them here would turn the market's
        refusals into generic failures.
        """
        target = API + (self.path[len(API_PREFIX):] or '/')
        length = int(self.headers.get('content-length') or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(target, data=body, method=self.command)
        for key, value in self.headers.items():
            if key.lower() not in HOP:
                req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                self._relay(res.status, res.headers, res.read())
        except urllib.error.HTTPError as e:
            self._relay(e.code, e.headers, e.read())
        except (urllib.error.URLError, OSError) as e:
            # The API half is down or restarting. Say which half, so the tab
            # doesn't report a broken console.
            self._json(502, {'detail': f'the wasmland API at {API} is not answering ({e})'})

    def _relay(self, status, headers, body):
        self.send_response(status)
        sent_type = False
        for key, value in (headers or {}).items():
            if key.lower() in HOP:
                continue
            if key.lower() == 'content-type':
                sent_type = True
            self.send_header(key, value)
        if not sent_type:
            self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    # ── files ────────────────────────────────────────────────────

    def _file(self, rel, head=False):
        target = (APP_DIR / (rel or 'index.html')).resolve()
        if not str(target).startswith(str(APP_DIR)) or not target.is_file():
            # Unknown paths under the base fall back to the console shell, so
            # a deep link survives a reload.
            target = APP_DIR / 'index.html'
            if not target.is_file():
                return self._json(404, {'detail': 'console not installed'})
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('content-type', MEDIA.get(target.suffix, 'application/octet-stream'))
        self.send_header('content-length', str(len(data)))
        if target.suffix in ('.js', '.mjs', '.css'):
            self.send_header('cache-control', 'no-cache')
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def _health(self):
        try:
            with urllib.request.urlopen(f'{API}/health', timeout=5) as res:
                api_ok = res.status == 200
        except Exception:
            api_ok = False
        self._json(200, {'ok': True, 'app': 'wasmland', 'port': PORT,
                         'base_path': BASE, 'api': API, 'api_ok': api_ok})

    # ── plumbing ─────────────────────────────────────────────────

    def _redirect(self, where):
        self.send_response(302)
        self.send_header('location', where)
        self.send_header('content-length', '0')
        self.end_headers()

    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write(f'{self.address_string()} {fmt % args}\n')


def main():
    global API, PORT, HOST
    parser = argparse.ArgumentParser(description='the wasmland console service')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--api', default=API, help='where the API answers')
    args = parser.parse_args()
    API, PORT, HOST = args.api.rstrip('/'), args.port, args.host

    server = ThreadingHTTPServer((HOST, PORT), Console)
    server.daemon_threads = True
    print(f'wasmland console → http://localhost:{PORT}{BASE}  (api {API})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
