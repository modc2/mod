#!/usr/bin/env python3
"""
shelf API — the module's functions over HTTP, on 127.0.0.1 only.

Stdlib, with no framework under it. That is not minimalism for its own sake:
this is the tool you reach for when the box is misbehaving, and a diagnostic
that cannot start because the thing it diagnoses is broken has failed at the
one moment it existed for. It imports the same `src/` the CLI does, so there is
one implementation of every answer and the console cannot drift from `m shelf`.

    python3 api.py                    # 127.0.0.1:50570
    python3 api.py --port 8080
    SHELF_HOST=0.0.0.0 python3 api.py # only if you have read the warning

BOUND TO LOOPBACK, DELIBERATELY
    This reads every module's private state. `config.json` sets `route: false`
    so the gateway will not publish it, and the default bind is 127.0.0.1 so
    nothing reaches it without already being on the box. The host is settable
    because someone will have a reason, and a tool that cannot be moved gets
    replaced by one with no redaction at all — but it is not the default, and
    `/` says which way it is running.

WRITES ARE DRY UNLESS ASKED TWICE
    `gc`, `restore` and `rm` are GET-safe: a GET plans and returns what it
    would do, a POST with `confirm: true` performs it. Nothing that deletes
    is reachable by following a link.
"""
import argparse
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MODULE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MODULE_DIR))

from src import blobs, keys, snapshot, space  # noqa: E402

PORT = int(os.environ.get('SHELF_PORT', 50570))
HOST = os.environ.get('SHELF_HOST', '127.0.0.1')


def _int(params, name, default):
    try:
        return int(params.get(name, [default])[0])
    except (TypeError, ValueError):
        return default


def _str(params, name, default=''):
    value = params.get(name, [default])[0]
    return value if value not in (None, '') else default


def _flag(params, name):
    return _str(params, name).lower() in ('1', 'true', 'yes')


class Handler(BaseHTTPRequestHandler):
    server_version = 'shelf-api'
    protocol_version = 'HTTP/1.1'

    # ── routing ──────────────────────────────────────────────────

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')

    def do_OPTIONS(self):
        self._send(204, None)

    def _handle(self, method):
        parsed = urlparse(self.path)
        route = parsed.path.rstrip('/') or '/'
        # The console proxies under /shelf/_api; accept both so the API is the
        # same whether it is called directly or through the app server.
        for prefix in ('/shelf/_api', '/api/shelf', '/_api'):
            if route.startswith(prefix):
                route = route[len(prefix):] or '/'
                break
        params = parse_qs(parsed.query)
        body = self._body() if method == 'POST' else {}

        try:
            status, payload = self._route(method, route, params, body)
        except Exception as exc:                      # noqa: BLE001 — the API is the last line
            traceback.print_exc()
            status, payload = 500, {'error': str(exc), 'route': route}
        self._send(status, payload)

    def _route(self, method, route, params, body):
        root = _str(params, 'root') or body.get('root')
        parts = [p for p in route.split('/') if p]
        head = parts[0] if parts else ''
        rest = parts[1] if len(parts) > 1 else ''

        if route == '/':
            return 200, self._info()
        if head == 'health':
            return 200, {'ok': blobs.verify(root)['healthy']}
        if head == 'routes':
            return 200, {'routes': ROUTES}

        # ── space ────────────────────────────────────────────────
        if head == 'space':
            return 200, space.scan(limit=_int(params, 'limit', 0))
        if head == 'usage':
            module = rest or _str(params, 'module')
            if not module:
                return 400, {'error': 'usage needs a module'}
            return 200, space.usage(module, depth=_int(params, 'depth', 1),
                                    limit=_int(params, 'limit', 40))
        if head == 'big':
            return 200, space.big(limit=_int(params, 'limit', 25),
                                  module=_str(params, 'module') or None)

        # ── keys ─────────────────────────────────────────────────
        if head == 'roots':
            return 200, {'roots': keys.roots()}
        if head == 'prefixes':
            return 200, keys.prefixes(root)
        if head == 'keys':
            return 200, keys.keys(root=root, prefix=_str(params, 'prefix'),
                                  search=_str(params, 'search'),
                                  limit=_int(params, 'limit', 200),
                                  offset=_int(params, 'offset', 0))
        if head == 'read':
            key = _str(params, 'key') or rest
            if not key:
                return 400, {'error': 'read needs a key'}
            return 200, keys.read(key, root=root)
        if head == 'grep':
            query = _str(params, 'q') or _str(params, 'text')
            if not query:
                return 400, {'error': 'grep needs q'}
            return 200, keys.grep(query, root=root, prefix=_str(params, 'prefix'),
                                  limit=_int(params, 'limit', 50))

        # ── integrity ────────────────────────────────────────────
        if head == 'verify':
            return 200, blobs.verify(root, limit=_int(params, 'limit', 0))
        if head == 'orphans':
            return 200, blobs.orphans(root)
        if head == 'strays':
            return 200, blobs.strays(root)

        # ── writes: plan on GET, act on POST+confirm ─────────────
        if head == 'gc':
            confirm = method == 'POST' and bool(body.get('confirm'))
            return 200, blobs.gc(root, confirm=confirm,
                                 min_age_days=float(body.get('min_age_days', 1.0)))
        if head == 'rm':
            key = body.get('key') or _str(params, 'key')
            if not key:
                return 400, {'error': 'rm needs a key'}
            return 200, self._rm(key, root, method == 'POST' and bool(body.get('confirm')))
        if head == 'snapshot':
            if rest:
                return 200, snapshot.inspect(rest)
            if method != 'POST':
                return 405, {'error': 'snapshot is POST — it writes a pin'}
            return 200, snapshot.create(root=root, pin=bool(body.get('pin', True)))
        if head == 'restore':
            if method != 'POST':
                return 405, {'error': 'restore is POST'}
            cid = body.get('cid')
            if not cid:
                return 400, {'error': 'restore needs a cid'}
            return 200, snapshot.restore(cid, root=root,
                                         confirm=bool(body.get('confirm')),
                                         overwrite=bool(body.get('overwrite')))

        return 404, {'error': f'no route {route}', 'routes': ROUTES}

    @staticmethod
    def _rm(key, root, confirm):
        from src import redact
        root_path = keys._resolve(root)
        path = keys.key2path(root_path, key)
        if not path:
            return {'key': key, 'found': False, 'deleted': False}
        if redact.sensitive_file(path):
            return {'key': key, 'deleted': False,
                    'error': 'refusing to delete a secret file from a browser'}
        size = os.path.getsize(path)
        if not confirm:
            return {'key': key, 'bytes': size, 'deleted': False,
                    'note': 'dry run — POST with confirm:true to delete'}
        os.remove(path)
        return {'key': key, 'bytes': size, 'deleted': True}

    @staticmethod
    def _info():
        report = space.scan(limit=5)
        store = keys.prefixes()
        return {
            'name': 'shelf', 'reads': space.MOD_HOME,
            'bound': f'{HOST}:{PORT}',
            'public': HOST not in ('127.0.0.1', 'localhost'),
            'total': report['total'],
            'biggest': [{'module': r['module'], 'size': r['size']}
                        for r in report['modules']],
            'store': {'root': store['root'], 'keys': store.get('keys', 0)},
            'routes': ROUTES,
        }

    # ── plumbing ─────────────────────────────────────────────────

    def _body(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length) or b'{}')
        except (ValueError, TypeError):
            return {}

    def _send(self, status, payload):
        blob = b'' if payload is None else json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(blob)))
        # Same-origin in practice: the console proxies through its own server,
        # so this only has to be permissive enough for a direct curl.
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()
        if blob:
            self.wfile.write(blob)

    def log_message(self, fmt, *args):
        sys.stderr.write(f'[shelf-api] {fmt % args}\n')


ROUTES = {
    'GET /': 'what is on the box',
    'GET /health': 'is the store internally consistent',
    'GET /space': "every module's state, largest first",
    'GET /usage/{module}': 'one module, broken down',
    'GET /big': 'the largest individual files',
    'GET /roots': 'directories this can read',
    'GET /prefixes': 'namespaces in a root',
    'GET /keys?prefix=': 'keys under a prefix',
    'GET /read?key=': 'one value, redacted',
    'GET /grep?q=': 'which record mentions this',
    'GET /verify': 'do the blobs hash to their names',
    'GET /orphans': 'blobs nothing refers to',
    'GET /strays': 'the same bytes filed twice',
    'GET|POST /gc': 'plan, then confirm:true to delete',
    'POST /rm': 'delete one key, confirm:true',
    'POST /snapshot': 'freeze a root under a CID',
    'GET /snapshot/{cid}': "what is in one",
    'POST /restore': 'unpack one back, confirm:true',
}


def main():
    parser = argparse.ArgumentParser(description='shelf API')
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--host', default=HOST)
    args = parser.parse_args()

    globals()['PORT'], globals()['HOST'] = args.port, args.host
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    where = 'local only' if args.host in ('127.0.0.1', 'localhost') else 'REACHABLE OFF-BOX'
    print(f'[shelf-api] http://{args.host}:{args.port} — {where}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == '__main__':
    main()
