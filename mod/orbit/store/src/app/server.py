#!/usr/bin/env python3
"""
store console — the app half, on its own port.

    /store/            the console
    /store/v/<code>    the page a scanned QR code lands on — a local file
    /store/p/<id>      the page a public image link lands on — the same file
    /store/_api/*      reverse-proxied to the API
    /store/g/<code>    proxied — the bytes behind a code, and spending them
    /store/i/<id>      proxied — the public bytes
    /store/qr          proxied
    /store/mcp         proxied — the agent's door onto the same store
    /store/docs        proxied — the manual, as a page or as JSON

The proxying is why this is not a static file server. A share link has to be
ONE origin: the person scanning the QR code is on a phone that has never heard
of this box, and handing them a link on port A that immediately redirects to
port B is two chances to fail and a CORS problem in between. The console, the
public image, the one-time grant and the pages about them all answer on the
same host and port, and the split between app and API stays an implementation
detail.

The two page routes are served from disk here rather than proxied, because
they are one static file that says nothing about which code or id was asked
for — the page finds that out from the URL in the browser. That also means a
scan lands on something rendered by this process even while the API is
restarting, which then says so instead of showing nothing.

Stdlib, like the API, so the console still renders and reports the API down
rather than not answering at all.

    python3 server.py                     # 127.0.0.1:50671
    python3 server.py --port 8080 --api http://127.0.0.1:50670
"""
import argparse
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE = '/store'
API = os.environ.get('STORE_SHARE_API', 'http://127.0.0.1:50670').rstrip('/')
PORT = int(os.environ.get('STORE_SHARE_APP_PORT', 50671))
HOST = os.environ.get('STORE_SHARE_APP_HOST', '127.0.0.1')
TIMEOUT = float(os.environ.get('STORE_SHARE_PROXY_TIMEOUT', 60))

# Everything under these is the API's, not a file on disk.
PROXIED = (f'{BASE}/_api', f'{BASE}/g/', f'{BASE}/i/', f'{BASE}/qr',
           f'{BASE}/mcp', f'{BASE}/docs')

# ...and everything under these is one page, whatever comes after the prefix.
PAGES = (f'{BASE}/v/', f'{BASE}/p/')

TYPES = {'.html': 'text/html; charset=utf-8', '.css': 'text/css',
         '.js': 'text/javascript', '.svg': 'image/svg+xml',
         '.png': 'image/png', '.ico': 'image/x-icon'}

# Headers we do not copy back from the API: hop-by-hop, or ours to set.
SKIP = {'content-length', 'transfer-encoding', 'connection', 'server', 'date'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'store-app'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        if os.environ.get('STORE_SHARE_QUIET'):
            return
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))

    def do_GET(self):
        self._handle('GET')

    def do_HEAD(self):
        self._handle('HEAD')

    def do_POST(self):
        self._handle('POST')

    def do_DELETE(self):
        self._handle('DELETE')

    def do_OPTIONS(self):
        self._send(204, b'', None)

    def _send(self, status, body, content_type, extra=None):
        self.send_response(status)
        if content_type:
            self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body or b'')))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body and self.command != 'HEAD':
            self.wfile.write(body)

    def _handle(self, method):
        path = self.path.split('?')[0]
        if path in ('/', BASE, f'{BASE}/'):
            if path == '/':
                return self._send(302, b'', None, {'Location': f'{BASE}/'})
            return self._file('index.html')
        if any(self.path.startswith(p) for p in PROXIED):
            return self._proxy(method)
        if any(path.startswith(p) for p in PAGES):
            return self._file('view.html')
        if path.startswith(f'{BASE}/'):
            return self._file(path[len(BASE) + 1:])
        self._send(404, b'not here', 'text/plain')

    def _file(self, relative):
        # Resolve and confirm the result is still inside APP_DIR, so a path
        # like ../../../etc/passwd cannot walk out of the app directory.
        target = (APP_DIR / relative).resolve()
        if not str(target).startswith(str(APP_DIR)) or not target.is_file():
            return self._send(404, b'not here', 'text/plain')
        content_type = TYPES.get(target.suffix, 'application/octet-stream')
        self._send(200, target.read_bytes(), content_type,
                   {'Cache-Control': 'no-cache'})

    def _proxy(self, method):
        if self.path.startswith(f'{BASE}/_api'):
            tail = self.path[len(f'{BASE}/_api'):] or '/'
        else:
            tail = self.path[len(BASE):]
        url = f'{API}{tail}'

        body = None
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            body = self.rfile.read(length)

        request = urllib.request.Request(url, data=body, method=method)
        # Accept matters because /docs and /mcp both answer differently to a
        # browser and to a program, and the MCP headers matter because a
        # client that sent a session id expects to see it come back.
        for header in ('Authorization', 'Content-Type', 'Accept',
                       'Mcp-Session-Id', 'MCP-Protocol-Version'):
            if self.headers.get(header):
                request.add_header(header, self.headers[header])

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = response.read()
                extra = {k: v for k, v in response.headers.items()
                         if k.lower() not in SKIP}
                content_type = response.headers.get_content_type()
                extra.pop('Content-Type', None)
                self._send(response.status, payload, content_type, extra)
        except urllib.error.HTTPError as error:
            payload = error.read()
            self._send(error.code, payload,
                       error.headers.get_content_type() if error.headers
                       else 'application/json')
        except Exception as error:
            # The API is down or slow. Say so in the shape the console parses,
            # rather than failing to answer — a blank page explains nothing.
            message = (f'{{"error":"the store API is not answering on {API} '
                       f'({type(error).__name__})","status":502}}')
            self._send(502, message.encode('utf-8'), 'application/json')


def serve(host=HOST, port=PORT, api=API):
    global API
    API = api.rstrip('/')
    server = ThreadingHTTPServer((host, port), Handler)
    print(f'store app  http://{host}:{port}{BASE}/   -> api {API}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='store console')
    parser.add_argument('--host', default=HOST)
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--api', default=API)
    args = parser.parse_args()
    serve(args.host, args.port, args.api)
