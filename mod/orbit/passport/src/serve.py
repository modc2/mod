"""passport service: the protocol over HTTP, plus the browser SDK.

    GET  /health                    {ok, fingerprint, version}
    GET  /protocol                  protocol.json as live on disk + fingerprint
    GET  /verify?token=…            {address, scheme, time} | 401 {error}
    GET  /role?token=…&mod=agent    {signed_in, address, is_owner, owner, …}
    GET  /passport.js               the browser SDK (shared sign-in, build adoption)

The token may also come as `Authorization: Bearer …` or a `token:` header.
Paths work bare, under /passport/ (gateway keeps the prefix) and under
/api/passport/. Nothing is cached across restarts and nothing needs one:
the protocol files are re-read when they change.

    python3 serve.py [--port 51390] [--host 0.0.0.0]
"""
import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, 'web')
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from passport import Passport  # noqa: E402

P = Passport()
PREFIXES = ('/api/passport', '/passport')


def _token(h, q) -> str:
    t = (q.get('token') or q.get('key') or [''])[0]
    if t:
        return t
    a = h.headers.get('Authorization') or ''
    if a.lower().startswith('bearer '):
        return a[7:].strip()
    return (h.headers.get('token') or '').strip()


class Handler(BaseHTTPRequestHandler):
    server_version = 'passport/0.1'

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json', cache='no-store'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', cache)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, token, Content-Type')
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(min(n, 65536)) or b'{}')
        except Exception:
            body = {}
        self._route({k: [str(v)] for k, v in body.items() if v is not None})

    def do_GET(self):
        self._route({})

    def _route(self, extra):
        u = urlparse(self.path)
        path = u.path
        for p in PREFIXES:
            if path == p or path.startswith(p + '/'):
                path = path[len(p):] or '/'
                break
        q = {**parse_qs(u.query), **extra}
        try:
            if path in ('/', '/index.html'):
                with open(os.path.join(WEB, 'index.html'), 'rb') as f:
                    return self._send(200, f.read(), 'text/html; charset=utf-8')
            if path == '/passport.js':
                with open(os.path.join(WEB, 'passport.js'), 'rb') as f:
                    # no-cache, not max-age: a protocol change must reach every
                    # console on its next load, without a rebuild
                    return self._send(200, f.read(), 'application/javascript; charset=utf-8',
                                      'no-cache')
            if path == '/health':
                return self._send(200, {'ok': True, 'fingerprint': P.fingerprint(),
                                        'version': P.protocol().get('version')})
            if path == '/protocol':
                return self._send(200, {**P.protocol(), 'fingerprint': P.fingerprint()})
            if path == '/verify':
                try:
                    return self._send(200, P.verify(_token(self, q)))
                except ValueError as e:
                    return self._send(401, {'error': str(e)})
            if path == '/role':
                mod = (q.get('mod') or [''])[0] or None
                r = P.role(_token(self, q), mod)
                return self._send(200 if r.get('signed_in') else 401, r)
            return self._send(404, {'error': f'no route {path}'})
        except Exception as e:
            return self._send(400, {'error': str(e)[:200]})


def serve(port=51390, host='0.0.0.0'):
    srv = ThreadingHTTPServer((host, int(port)), Handler)
    print(f'passport on :{port} (protocol {P.fingerprint()})', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=51390)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
