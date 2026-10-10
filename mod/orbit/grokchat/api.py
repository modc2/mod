#!/usr/bin/env python3
"""grokchat-api — a thin Python API in front of orbit/grokbot.

grokbot (:50890) already owns the hard parts: xAI upstream, wallet identity,
per-address keys, saved bots. This module is the pairing for a dedicated
Next.js chat app: it proxies the handful of routes the app needs, forwards
the caller's Authorization / x-xai-key headers untouched, passes SSE chat
streams through byte-for-byte, and knocks the activator once if grokbot is
asleep. Stdlib only — no dependencies.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get('PORT', 50930))
GROKBOT = os.environ.get('GROKBOT_URL', 'http://localhost:50890').rstrip('/')
ACTIVATOR = os.environ.get('ACTIVATOR_URL', 'http://localhost:9000').rstrip('/')
CHAT_TIMEOUT = int(os.environ.get('GROKCHAT_TIMEOUT', 300))
FORWARD = ('authorization', 'x-xai-key')


def info():
    return {
        'name': 'grokchat',
        'description': 'Next.js chat app + Python API over orbit/grokbot',
        'grokbot': GROKBOT,
        'endpoints': {
            'GET /': 'this',
            'GET /health': 'own health + grokbot reachability',
            'GET /models': 'proxy → grokbot /models (?refresh=1)',
            'GET /me': 'proxy → grokbot /me (needs Authorization)',
            'GET /bots': 'proxy → grokbot /bots (needs Authorization)',
            'POST /chat': 'proxy → grokbot /chat; stream:true is SSE passthrough',
        },
        'headers': 'Authorization: Bearer <mod token> · x-xai-key: xai-… (BYOK)',
    }


def _wake():
    """grokbot may sleep behind the activator; one knock on :9000 wakes it."""
    try:
        urllib.request.urlopen(ACTIVATOR + '/api/grokbot', timeout=10).read()
        return True
    except Exception:
        return False


def _refused(err):
    reason = getattr(err, 'reason', None)
    return isinstance(reason, ConnectionRefusedError) or 'refused' in str(reason).lower()


def _open(method, path, body=None, headers=None, timeout=30):
    """One request to grokbot; a refused connection gets one wake + retry."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(GROKBOT + path, data=data, method=method)
    req.add_header('content-type', 'application/json')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as e:
        if not isinstance(e, urllib.error.HTTPError) and _refused(e) and _wake():
            time.sleep(2)
            return urllib.request.urlopen(req, timeout=timeout)
        raise


class Handler(BaseHTTPRequestHandler):
    server_version = 'grokchat/0.1'

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.command or '-', self.path))

    # ── plumbing ─────────────────────────────────────────────────

    def _cors(self):
        self.send_header('access-control-allow-origin', '*')
        self.send_header('access-control-allow-headers',
                         'content-type, authorization, x-xai-key')
        self.send_header('access-control-allow-methods', 'GET, POST, OPTIONS')

    def _send(self, code, payload, ctype='application/json'):
        self.send_response(code)
        self._cors()
        self.send_header('content-type', ctype)
        self.send_header('content-length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def _fwd(self):
        return {k: v for k in FORWARD if (v := self.headers.get(k))}

    def _body(self):
        n = int(self.headers.get('content-length') or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except Exception:
            return {}

    def _proxy(self, method, path, body=None, timeout=30):
        """Relay one grokbot response — status, body and all — to the caller."""
        try:
            with _open(method, path, body=body, headers=self._fwd(),
                       timeout=timeout) as r:
                self._send(r.status, r.read(),
                           r.headers.get('content-type', 'application/json'))
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read() or json.dumps(
                {'error': f'grokbot → {e.code}'}).encode())
        except Exception as e:
            self._json(502, {'error': f'grokbot unreachable: '
                                      f'{type(e).__name__}: {e}',
                             'grokbot': GROKBOT})

    # ── routes ───────────────────────────────────────────────────

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path, _, query = self.path.partition('?')
        if path == '/':
            return self._json(200, info())
        if path == '/health':
            up = None
            try:
                with _open('GET', '/health', timeout=8) as r:
                    up = json.loads(r.read() or b'{}')
            except Exception as e:
                up = {'error': f'{type(e).__name__}: {e}'}
            return self._json(200, {'ok': True, 'grokbot': up,
                                    'grokbot_ok': bool(up and up.get('ok'))})
        if path in ('/models', '/me', '/bots'):
            return self._proxy('GET', path + (f'?{query}' if query else ''))
        self._json(404, {'error': f'no route {path}'})

    def do_POST(self):
        path = self.path.partition('?')[0]
        if path != '/chat':
            return self._json(404, {'error': f'no route {path}'})
        body = self._body()
        if body.get('stream'):
            return self._stream(body)
        self._proxy('POST', '/chat', body=body, timeout=CHAT_TIMEOUT)

    def _stream(self, body):
        """SSE passthrough — grokbot's frames go to the browser unchanged."""
        try:
            r = _open('POST', '/chat', body=body, headers=self._fwd(),
                      timeout=CHAT_TIMEOUT)
        except urllib.error.HTTPError as e:
            return self._send(e.code, e.read() or b'{"error":"chat failed"}')
        except Exception as e:
            return self._json(502, {'error': f'grokbot unreachable: {e}'})
        self.send_response(200)
        self._cors()
        self.send_header('content-type', 'text/event-stream')
        self.send_header('cache-control', 'no-cache')
        self.send_header('connection', 'close')
        self.end_headers()
        try:
            with r:
                for line in r:
                    self.wfile.write(line)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve(port=PORT):
    httpd = ThreadingHTTPServer(('0.0.0.0', int(port)), Handler)
    sys.stderr.write(f'grokchat-api on :{port} → {GROKBOT}\n')
    httpd.serve_forever()


if __name__ == '__main__':
    args = sys.argv[1:]
    port = PORT
    if '--port' in args:
        port = int(args[args.index('--port') + 1])
    serve(port)
