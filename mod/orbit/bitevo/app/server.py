"""
Bitevo app server — zero-dependency static frontend + API proxy.

Serves app/index.html and WHITEPAPER.md, and proxies /api/* to the
FastAPI backend on API_PORT so the frontend works same-origin both
locally (:50121) and behind a gateway prefix (/bitevo/...).
"""

import http.server
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MODULE_DIR = APP_DIR.parent
API_PORT = 50120
PROXY_TIMEOUT = 900  # epochs shell out to LLMs and can take minutes


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body: bytes, ctype='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._route('GET')

    def do_POST(self):
        self._route('POST')

    def _route(self, method):
        path, _, query = self.path.partition('?')
        # tolerate a gateway prefix (/bitevo/...)
        if path == '/bitevo':
            path = '/'
        elif path.startswith('/bitevo/'):
            path = path[len('/bitevo'):]

        if path.startswith('/api/') or path == '/api':
            return self._proxy(method, path[len('/api'):] or '/', query)

        if method != 'GET':
            return self._send(405, b'{"error":"method not allowed"}')

        if path in ('/', '/index.html'):
            return self._file(APP_DIR / 'index.html', 'text/html; charset=utf-8')
        if path == '/whitepaper.md':
            return self._file(MODULE_DIR / 'WHITEPAPER.md', 'text/markdown; charset=utf-8')
        if path == '/health':
            return self._send(200, b'{"status":"ok","app":"bitevo"}')
        return self._send(404, b'{"error":"not found"}')

    def _file(self, fpath: Path, ctype: str):
        try:
            self._send(200, fpath.read_bytes(), ctype)
        except OSError:
            self._send(404, b'{"error":"missing file"}')

    def _proxy(self, method, path, query):
        url = f'http://127.0.0.1:{API_PORT}{path}' + (f'?{query}' if query else '')
        body = None
        if method == 'POST':
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length else b'{}'
        req = urllib.request.Request(
            url, data=body, method=method,
            headers={'Content-Type': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=PROXY_TIMEOUT) as resp:
                self._send(resp.status, resp.read(),
                           resp.headers.get('Content-Type', 'application/json'))
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read() or json.dumps({'error': str(e)}).encode())
        except Exception as e:
            self._send(502, json.dumps({'error': f'api unreachable: {e}'}).encode())


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 50121
    server = http.server.ThreadingHTTPServer(('0.0.0.0', port), Handler)
    print(f'bitevo app on http://localhost:{port} (api proxy → :{API_PORT})')
    server.serve_forever()


if __name__ == '__main__':
    main()
