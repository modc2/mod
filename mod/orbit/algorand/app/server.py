#!/usr/bin/env python3
"""algorand console — the app half of the module, on its own port.

Two jobs and nothing else:

    /algorand/*         the files in this directory (index.html is the console)
    /algorand/_api/*    reverse-proxied to the API (api/api.py, :51340)

The console asks its *own origin* for `_api`, so one page works behind the
gateway (modc2.com/algorand) and on the bare port alike, with no CORS on the
hot path and no API url baked in. Stdlib only, so the console stays up and says
the API is down while the API restarts.

    python3 app/server.py                              # :51341, API at :51340
    python3 app/server.py --port 8080 --api http://box:51340
"""
import argparse
import mimetypes
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE = os.environ.get('BASE_PATH', '/algorand').rstrip('/')
API_PREFIX = BASE + '/_api'
API = os.environ.get('ALGORAND_API', 'http://127.0.0.1:51340').rstrip('/')
PORT = int(os.environ.get('ALGORAND_APP_PORT', 51341))
TIMEOUT = float(os.environ.get('ALGORAND_PROXY_TIMEOUT', 120))
# Never served even though they sit in this directory.
HIDDEN = {'server.py'}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'algorand-app'

    def _send(self, code, data, ctype, extra=()):
        self.send_response(code)
        self.send_header('content-type', ctype)
        self.send_header('content-length', str(len(data)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _proxy(self, rest, query):
        n = int(self.headers.get('content-length') or 0)
        body = self.rfile.read(n) if n else None
        url = API + (rest or '/') + (('?' + query) if query else '')
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() in ('content-type', 'accept', 'authorization')}
        req = urllib.request.Request(url, data=body, method=self.command, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                code, data, ctype = r.status, r.read(), r.headers.get('content-type', 'application/json')
        except urllib.error.HTTPError as e:
            code, data, ctype = e.code, e.read(), e.headers.get('content-type', 'application/json')
        except Exception as e:
            # 4xx, not 5xx: Cloudflare replaces 5xx bodies with its own page.
            code, ctype = 424, 'application/json'
            data = ('{"error": "algorand api unreachable at %s: %s"}'
                    % (API, str(e).replace('"', "'"))).encode()
        self._send(code, data, ctype)

    def _static(self, rel):
        rel = rel.lstrip('/') or 'index.html'
        target = (APP_DIR / rel).resolve()
        if APP_DIR not in target.parents or target.name in HIDDEN or not target.is_file():
            # Unknown paths fall back to the console (single page).
            target = APP_DIR / 'index.html'
        ctype = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        self._send(200, target.read_bytes(), ctype, [('cache-control', 'no-cache')])

    def _dispatch(self):
        path, _, query = self.path.partition('?')
        # `/_api` too: the page served at the bare port's `/` derives that.
        for prefix in (API_PREFIX, '/_api'):
            if path == prefix or path.startswith(prefix + '/'):
                return self._proxy(path[len(prefix):], query)
        if self.command not in ('GET', 'HEAD'):
            return self._send(405, b'the console only serves GET; the API is under _api',
                              'text/plain')
        # Bare /algorand and /algorand/ both serve the page — never redirect
        # between them (the gateway 308s /algorand/ -> /algorand).
        if path == BASE or path.startswith(BASE + '/'):
            return self._static(path[len(BASE):])
        return self._static(path)

    do_GET = do_HEAD = do_POST = do_OPTIONS = _dispatch

    def log_message(self, *a):
        pass


def serve(port=PORT, api=API):
    global API
    API = api.rstrip('/')
    print(f'algorand app on :{port}{BASE} — {API_PREFIX} -> {API}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--api', default=API)
    a = ap.parse_args()
    serve(a.port, a.api)
