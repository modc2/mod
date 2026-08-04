"""Server for the cathedral console: the static app plus a same-origin API alias.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader, and with no dependencies beyond stdlib.

It answers the app half of the protocol's URL rule:

    /cathedral/*        → the console  (gateway keeps the prefix; stripped here)
    /cathedral/_api/*   → the BYOK API (api/api.py on :50390), same origin

The `_api` alias exists so one page works in both deployments: behind the
gateway the API is at /api/cathedral, and on a bare app port it is on another
port entirely. Rather than teach the browser two shapes (and pay for CORS
preflights on every call), the console always talks to `_api` and this process
forwards it verbatim.

BYOK still holds here: the caller's `cat_sk_*` rides in the Authorization
header it is given, is forwarded to exactly one upstream, and is never read,
stored, or logged by this process. Request lines are logged; headers never are.

    python3 serve.py [--port 50391] [--host 0.0.0.0]
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WEB_DIR = os.path.dirname(os.path.abspath(__file__))
PREFIX = '/cathedral'
NAME = 'cathedral'
UPSTREAM = os.environ.get('CATHEDRAL_UPSTREAM', 'http://127.0.0.1:50390').rstrip('/')

# The only headers that cross into the upstream call. An allowlist, not a
# blocklist: nothing about this hop should depend on what a browser decides to
# send tomorrow, and the credential must be the caller's own and nothing else.
FORWARD_REQUEST = ('authorization', 'x-cathedral-key', 'content-type', 'idempotency-key')
FORWARD_RESPONSE = ('content-type',)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, fmt, *args):
        # Request line only. Never headers — one of them is somebody's key.
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _strip(self):
        """Drop the app prefix, serving the bare form as the index.

        The bare ``/cathedral`` must NOT redirect to ``/cathedral/``: the
        gateway 308s the directory form back to the bare one, so a redirect
        here is an infinite loop and the published URL never loads. index.html
        pins its own <base>, which is what keeps relative assets resolving.
        """
        if self.path == PREFIX or self.path.startswith(PREFIX + '?'):
            self.path = '/' + self.path[len(PREFIX):]
        elif self.path.startswith(PREFIX + '/'):
            self.path = self.path[len(PREFIX):] or '/'

    def _json(self, code, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    # ── /_api/* → the BYOK gateway ───────────────────────────────────────

    def _api_path(self):
        """The upstream path this request names, or None if it isn't an API call."""
        if self.path == '/_api' or self.path.startswith('/_api?'):
            return '/' + self.path[len('/_api'):].lstrip('/')
        if self.path.startswith('/_api/'):
            return self.path[len('/_api'):]
        return None

    def _proxy(self, path):
        n = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(n) if n else None
        req = urllib.request.Request(UPSTREAM + path, data=body, method=self.command)
        for h in FORWARD_REQUEST:
            v = self.headers.get(h)
            if v:
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, payload, headers = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            # The API's own 401/402/409/425 answers are the interesting ones —
            # pass them through untouched so the console can explain them.
            status, payload, headers = e.code, e.read(), e.headers
        except (urllib.error.URLError, OSError) as e:
            return self._json(502, {'error': 'cathedral api unreachable',
                                    'upstream': UPSTREAM, 'detail': str(e)})
        self.send_response(status)
        for h in FORWARD_RESPONSE:
            if headers.get(h):
                self.send_header(h, headers.get(h))
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    # ── verbs ────────────────────────────────────────────────────────────

    def do_GET(self):
        self._strip()
        api = self._api_path()
        if api:
            return self._proxy(api)
        clean = self.path.split('?')[0].rstrip('/')
        if clean in ('/health', '/healthz'):
            return self._json(200, {'ok': True, 'module': NAME, 'upstream': UPSTREAM})
        return super().do_GET()

    def do_HEAD(self):
        self._strip()
        api = self._api_path()
        return self._proxy(api) if api else super().do_HEAD()

    def do_POST(self):
        self._strip()
        api = self._api_path()
        if api:
            return self._proxy(api)
        return self._json(404, {'error': 'the console serves the app; the API lives at /cathedral/_api/'})

    def do_DELETE(self):
        self._strip()
        api = self._api_path()
        if api:
            return self._proxy(api)
        return self._json(404, {'error': 'no such path'})

    def end_headers(self):
        # One static bundle, edited in place — never let a browser pin an old copy.
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Allow', 'GET, HEAD, POST, DELETE, OPTIONS')
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 50391)))
    ap.add_argument('--host', default=os.environ.get('HOST', '0.0.0.0'))
    args = ap.parse_args()

    if not os.path.exists(os.path.join(WEB_DIR, 'index.html')):
        raise SystemExit('cathedral: app/index.html is missing')

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print('cathedral console on http://%s:%d%s  (api → %s)'
          % (args.host, args.port, PREFIX, UPSTREAM), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
