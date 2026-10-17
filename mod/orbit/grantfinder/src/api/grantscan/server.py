"""grantscan.server — REST + the web app on one port, stdlib only.

    GET  /                web app (Accept: text/html) else /health      GET /app
    GET  /health
    GET  /grants?q=&tags=a,b&status=&sort=deadline|amount|new|name&k=
    GET  /grants/<id>     DELETE /grants/<id>
    GET  /tags            tag counts, grouped (category, ecosystem, topic, ...)
    GET  /lanes           what a sweep covers
    GET  /runs?sweep=     agent runs, newest first
    GET  /sweep           the sweep in flight, if any
    POST /sweep {lanes?, category?, model?, budget?, concurrency?}
    POST /verify {id}
    GET  /export.csv

Writes (POST/DELETE) start agent runs on the host's Claude account, so they
are loopback-only: refused when the request came through a proxy.
Paths are also accepted under /grantfinder and /grantfinder/api.
Failures are 4xx with {error}: Cloudflare eats 5xx bodies.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import agent, lanes as L, store, sweep

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / 'src' / 'app' / 'index.html'
PREFIXES = ('/grantfinder/api', '/grantfinder', '/api')


def version() -> str:
    try:
        return json.loads((ROOT / 'config.json').read_text()).get('version', '')
    except Exception:
        return ''


def route(path: str) -> str:
    for p in PREFIXES:
        if path == p or path.startswith(p + '/'):
            return path[len(p):] or '/'
    return path


def health() -> dict:
    return {'ok': True, 'name': 'grantfinder', 'version': version(), **store.stats(),
            'agent': agent.available(), 'sweep': sweep.current()['sweep']}


def csv_export() -> bytes:
    cols = ['id', 'name', 'funder', 'status', 'deadline', 'amount_text', 'amount_max_usd',
            'ecosystem', 'url', 'apply_url', 'tags', 'summary']
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for g in store.grants():
        w.writerow([' '.join(g.get(c) or []) if c == 'tags' else g.get(c, '') for c in cols])
    return buf.getvalue().encode()


class Handler(BaseHTTPRequestHandler):
    server_version = 'mod-grantfinder/0.1'

    def log_message(self, *a):
        pass

    def _send(self, code: int, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get('Content-Length') or 0)
        try:
            b = json.loads(self.rfile.read(n) or b'{}') if n else {}
        except json.JSONDecodeError:
            b = {}
        return b if isinstance(b, dict) else {}

    def _local(self) -> bool:
        proxied = any(self.headers.get(h) for h in ('X-Forwarded-For', 'X-Real-IP', 'Forwarded'))
        return self.client_address[0] in ('127.0.0.1', '::1') and not proxied

    def _run(self, fn):
        try:
            self._send(200, fn())
        except KeyError as e:
            self._send(404, {'error': str(e).strip("'")})
        except (ValueError, TypeError) as e:
            self._send(400, {'error': str(e)})
        except Exception as e:
            self._send(400, {'error': f'{type(e).__name__}: {e}'})

    def _split(self):
        u = urllib.parse.urlsplit(self.path)
        return route(u.path), {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        path, q = self._split()
        wants_html = 'text/html' in (self.headers.get('Accept') or '')
        if path == '/app' or (path == '/' and wants_html):
            return self._send(200, WEB.read_bytes(), 'text/html; charset=utf-8')
        if path in ('/', '/health'):
            return self._run(health)
        if path == '/grants':
            return self._run(lambda: (lambda r: {'n': r[1], 'grants': r[0]})(store.grants(
                q.get('q', ''), q.get('tags'), q.get('status', ''), q.get('sort', 'deadline'),
                int(q.get('k') or 0), total=True)))
        if path.startswith('/grants/'):
            return self._run(lambda: store.get(path.split('/')[2]))
        if path == '/tags':
            return self._run(store.tag_counts)
        if path == '/lanes':
            return self._run(lambda: {'lanes': L.LANES, 'categories': L.CATEGORIES})
        if path == '/runs':
            return self._run(lambda: {'runs': store.runs(q.get('sweep', ''), int(q.get('k') or 50))})
        if path == '/sweep':
            return self._run(sweep.current)
        if path == '/export.csv':
            return self._send(200, csv_export(), 'text/csv; charset=utf-8')
        self._send(404, {'error': f'no route {path}'})

    def do_POST(self):
        path, _ = self._split()
        if not self._local():
            return self._send(403, {'error': 'sweeps run on the host\'s Claude account — loopback only'})
        b = self._body()
        if path == '/sweep':
            return self._run(lambda: sweep.sweep(
                b.get('lanes'), b.get('category'), b.get('model') or 'sonnet',
                float(b.get('budget') or 1.5), int(b.get('concurrency') or 2), background=True))
        if path == '/verify':
            return self._run(lambda: sweep.verify(str(b.get('id') or ''), b.get('model') or 'sonnet'))
        self._send(404, {'error': f'no route {path}'})

    def do_DELETE(self):
        path, _ = self._split()
        if not self._local():
            return self._send(403, {'error': 'loopback only'})
        if path.startswith('/grants/'):
            return self._run(lambda: store.delete(path.split('/')[2]))
        self._send(404, {'error': f'no route {path}'})


def serve(port: int = 51380, host: str = '127.0.0.1') -> None:
    store.mark_orphans()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f'grantfinder on http://{host}:{port}  (app: /app)', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=51380)
    ap.add_argument('--host', default='127.0.0.1')
    a = ap.parse_args()
    serve(a.port, a.host)
