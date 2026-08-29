#!/usr/bin/env python3
"""
The console's web server: static files plus a proxy to the hilda API.

Zero dependencies on purpose. The console is one HTML file that draws
everything on a canvas, so there is nothing to build, nothing to install and
nothing to go stale between a deploy and a page load.

The proxy exists so the page has one URL shape everywhere. Served directly the
API lives on another port; served behind the gateway it lives at /hilda/api by
fleet convention. Forwarding /api/* from here means the client can always ask
for a relative ./api/... and be right either way.
"""

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = int(os.environ.get('PORT', 50551))
API = os.environ.get('HILDA_API', 'http://localhost:50550').rstrip('/')
BASE = os.environ.get('HILDA_BASE', '/hilda').rstrip('/')

STATIC = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript',
          '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml',
          '.ico': 'image/x-icon', '.json': 'application/json'}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'hilda/1.0'

    def log_message(self, fmt, *args):        # one line, no timestamps
        sys.stderr.write(f'{self.command} {self.path} {args[1]}\n')

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        # Behind the gateway every request keeps the /hilda prefix; strip it so
        # the routes below do not have to care where they are mounted.
        for prefix in (BASE, '/hilda'):
            if prefix and path.startswith(prefix + '/'):
                path = path[len(prefix):]
            elif prefix and path == prefix:
                path = '/'
        if path.startswith('/api/') or path == '/api':
            return self._proxy(path[4:] or '/')
        if path in ('/', '/index.html', ''):
            return self._static(HERE / 'index.html')
        target = (HERE / path.lstrip('/')).resolve()
        if HERE in target.parents and target.is_file():
            return self._static(target)
        self._send(404, b'not found', 'text/plain')

    def _static(self, path: Path):
        body = path.read_bytes()
        self._send(200, body, STATIC.get(path.suffix, 'application/octet-stream'),
                   {'cache-control': 'no-cache'})

    def _proxy(self, tail: str):
        query = urllib.parse.urlsplit(self.path).query
        url = f'{API}{tail}' + (f'?{query}' if query else '')
        req = urllib.request.Request(url, headers={
            'accept-encoding': self.headers.get('accept-encoding', 'identity'),
            'user-agent': 'hilda-console'})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                body = r.read()
                extra = {'cache-control': r.headers.get('cache-control', 'no-cache')}
                # Range/encoding headers have to survive the hop or the binary
                # grid payload arrives as gibberish.
                for h in ('content-encoding', 'x-hilda-years', 'x-hilda-shape',
                          'x-hilda-bbox'):
                    if r.headers.get(h):
                        extra[h] = r.headers[h]
                self._send(r.status, body,
                           r.headers.get('content-type', 'application/json'),
                           extra)
        except urllib.error.HTTPError as e:
            body = e.read()
            self._send(e.code, body,
                       e.headers.get('content-type', 'application/json'))
        except Exception as e:
            self._send(502, f'{{"error":"api unreachable at {API}: {e}"}}'.encode(),
                       'application/json')

    def _send(self, code: int, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header('content-type', ctype)
        self.send_header('content-length', str(len(body)))
        self.send_header('access-control-allow-origin', '*')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass


if __name__ == '__main__':
    print(f'hilda console → http://0.0.0.0:{PORT}  (api {API}, base {BASE})',
          flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
