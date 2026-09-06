#!/usr/bin/env python3
"""wingman api — REST, MCP and the console on one port, standard library only.

Every route is a thin call into the same `engine` the MCP tools use, so a
shell, an agent and the browser cannot be told different things about the
same photo.

Two kinds of route are not JSON: `POST /photos` accepts multipart uploads
straight from a file picker, and `GET /img/…` and `GET /download/…` hand back
bytes (thumbnails, renders, the zip). Everything else is JSON in, JSON out.

    python3 api.py [--port 50830]
"""

import json
import os
import sys
import urllib.parse
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import engine as E                                          # noqa: E402
import mcp                                                  # noqa: E402
from engine import WingmanError                             # noqa: E402

BASE = os.environ.get('BASE_PATH', '/wingman')
PORT = int(os.environ.get('PORT', 50830))
BIND = os.environ.get('WINGMAN_BIND', '0.0.0.0')


def info():
    return {
        'name': 'wingman',
        'version': mcp.version(),
        'what': 'a set of photos in, a dating-app-ready lineup out — measured, '
                'face-aware crops to each app\'s card ratio, metadata stripped, '
                'nothing retouched and nothing uploaded anywhere',
        'presets': E.PRESETS,
        'store': E.SETS_DIR,
        'detector': E.detector(),
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'endpoints': {
            'GET /health': 'runtime, detector, state dir',
            'GET /guide': 'what makes a profile photo work, and how the score is built',
            'GET /presets': 'the app card ratios',
            'GET /sets': 'every set (loopback or x-wingman-token only)',
            'POST /sets': '{name?} — a new empty set',
            'GET /sets/<id>': 'one set, its photos',
            'DELETE /sets/<id>': 'delete it',
            'POST /sets/<id>/name': '{name}',
            'POST /photos': 'multipart files[] (set= in the query or form) or JSON '
                            '{set?, data|files|path|dir|url}',
            'DELETE /photos/<set>/<photo>': 'drop one photo',
            'GET /audit': 'set=, photo=, force= — measurements, issues, score, verdict',
            'GET /faces': 'set=, photo=, threshold= — face boxes',
            'GET /lineup': 'set=, n=, min_score=, allow_group= — the best N, in order',
            'POST /render': '{set, photo?, preset?, ratio?, zoom?, polish?, only_lineup?}',
            'POST /export': '{set, preset?, n?} — lineup + renders + zip',
            'GET /img/<set>/<photo>': 'w= — JPEG thumbnail of the source',
            'GET /img/<set>/<photo>/<preset>': 'the rendered JPEG',
            'GET /download/<set>/<preset>.zip': 'the export',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
        'privacy': E.health()['privacy'],
    }


def _token():
    """An owner token for listing sets from outside loopback. Minted once."""
    path = os.path.join(E.STATE_DIR, 'token')
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        import secrets
        os.makedirs(E.STATE_DIR, exist_ok=True)
        t = secrets.token_hex(16)
        with open(path, 'w') as f:
            f.write(t)
        os.chmod(path, 0o600)
        return t


def route(method, path, query, body, trusted=False):
    """One request → one JSON answer. Raises WingmanError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}
    args = {**q, **b}

    def arg(name, default=None):
        return args.get(name, default)

    def num(name, default=None):
        v = args.get(name)
        if v is None:
            return default
        try:
            return type(default)(v) if default is not None else float(v)
        except (TypeError, ValueError):
            raise WingmanError(f'{name}= should be a number, got {v!r}')

    def flag(name, default=False):
        v = args.get(name)
        return default if v is None else str(v).lower() not in ('0', 'false', 'no', '')

    def set_arg():
        s = arg('set') or arg('id')
        if not s:
            raise WingmanError('which set? pass set=<id|name>')
        return s

    parts = [p for p in path.split('/') if p]
    if path in ('', '/'):
        return info()
    if path == '/health':
        return E.health()
    if path == '/guide':
        return mcp.call_tool('wingman_guide', {})
    if path == '/presets':
        return {'presets': E.PRESETS, 'default': E.DEFAULT_PRESET}
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    if path == '/sets':
        if method == 'POST':
            return E.new_set(arg('name'))
        if not trusted:
            raise WingmanError('listing sets needs loopback or x-wingman-token; a set '
                               'you already hold is at /sets/<id>', status=403)
        return E.sets(limit=num('limit', 100))
    if parts[:1] == ['sets'] and len(parts) >= 2:
        sid = parts[1]
        if len(parts) == 3 and parts[2] == 'name' and method == 'POST':
            return E.rename(sid, arg('name'))
        if method == 'DELETE':
            return E.delete_set(sid)
        meta = E.get_set(sid)
        audits = E._read_json(os.path.join(E._set_dir(meta['id']), 'audit.json'), {})
        for p in meta['photos']:
            a = audits.get(p['id'])
            p['audit'] = {k: a[k] for k in ('score', 'role', 'verdict', 'lead_ok',
                                             'face_count', 'faces', 'issues')} \
                if a and a.get('v') == E.AUDIT_VERSION else None
        return meta
    if path == '/photos' and method == 'POST':
        return E.add(set_ref=arg('set'), data=arg('data'), files=arg('files'),
                     path=arg('path'), dir=arg('dir'), url=arg('url'),
                     name=arg('name'), set_name=arg('set_name') or arg('name'))
    if parts[:1] == ['photos'] and len(parts) == 3 and method == 'DELETE':
        return E.remove(parts[1], parts[2])
    if path == '/audit':
        return E.audit(set_arg(), photo=arg('photo'), force=flag('force'))
    if path == '/faces':
        if not arg('photo'):
            raise WingmanError('faces needs photo=')
        return E.faces(set_arg(), arg('photo'), threshold=num('threshold', E.FACE_THRESHOLD))
    if path == '/lineup':
        return E.lineup(set_arg(), n=num('n', 6), min_score=num('min_score', 35),
                        allow_group=flag('allow_group', True), force=flag('force'))
    if path == '/render':
        return E.render(set_arg(), photo=arg('photo'), preset=arg('preset'),
                        ratio=arg('ratio'), size=arg('size'), zoom=arg('zoom') or 'auto',
                        polish_mode=arg('polish') or 'auto', quality=num('quality', 90),
                        force=flag('force'), only_lineup=flag('only_lineup'), n=num('n', 6))
    if path == '/export':
        return E.export(set_arg(), preset=arg('preset'), n=num('n', 6),
                        zoom=arg('zoom') or 'auto', polish_mode=arg('polish') or 'auto',
                        quality=num('quality', 90), force=flag('force'))
    raise WingmanError(f'no route {path} — GET / lists them', 404)


def _multipart(ctype, data):
    """Files out of a multipart/form-data body, via the email parser."""
    head = (f'content-type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n').encode()
    msg = BytesParser(policy=HTTP).parsebytes(head + data)
    files, fields = [], {}
    for part in msg.iter_parts():
        name = part.get_param('name', header='content-disposition')
        fn = part.get_filename()
        payload = part.get_payload(decode=True)
        if fn:
            files.append({'name': fn, 'data': payload})
        elif name:
            fields[name] = (payload or b'').decode('utf-8', 'replace')
    return files, fields


def serve(port=PORT):
    console = os.path.join(HERE, 'console.html')
    base = BASE if BASE.startswith('/') else '/' + BASE
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /wingman behind the gateway or served bare at :50830/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/wingman', '/_api')
    token = _token()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'wingman/' + mcp.version()

        def _send(self, code, payload, ctype='application/json', extra=None):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self._cors()
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def _cors(self):
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods', 'GET,POST,DELETE,OPTIONS')

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def _trusted(self):
            if self.headers.get('x-wingman-token') == token:
                return True
            fwd = self.headers.get('x-forwarded-for') or self.headers.get('x-real-ip')
            return (not fwd) and self.client_address[0] in ('127.0.0.1', '::1')

        def _body(self):
            n = int(self.headers.get('content-length') or 0)
            if not n:
                return {}
            raw = self.rfile.read(n)
            ctype = self.headers.get('content-type') or ''
            if ctype.startswith('multipart/form-data'):
                files, fields = _multipart(ctype, raw)
                return {**fields, 'files': files}
            try:
                return json.loads(raw or b'{}')
            except Exception:
                return {}

        def _path(self):
            raw = urllib.parse.urlparse(self.path)
            p, query = raw.path, raw.query
            for prefix in api_prefixes:
                if p == prefix or p.startswith(prefix + '/'):
                    return p[len(prefix):] or '/', query
            if p in (base, base + '/'):
                return '/console', query
            if p.startswith(base + '/'):
                return p[len(base):], query
            return p, query

        def _bytes(self, path, ctype, name=None):
            with open(path, 'rb') as f:
                data = f.read()
            extra = {'cache-control': 'private, max-age=60'}
            if name:
                extra['content-disposition'] = f'attachment; filename="{name}"'
            self._send(200, data, ctype, extra)

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
            try:
                if p == '/mcp':
                    if self.command != 'POST':
                        return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                    resp = mcp.handle(self._body())
                    return self._send(202 if resp is None else 200, resp or b'',
                                      'application/json' if resp else 'text/plain')
                if p in ('/console', '/index.html') and self.command == 'GET':
                    try:
                        with open(console, 'rb') as f:
                            return self._send(200, f.read(), 'text/html; charset=utf-8')
                    except FileNotFoundError:
                        return self._send(200, json.dumps(info(), indent=2).encode())
                parts = [x for x in p.split('/') if x]
                if parts[:1] == ['img'] and len(parts) in (3, 4) and self.command in ('GET', 'HEAD'):
                    if len(parts) == 4:
                        return self._bytes(E.rendered_path(parts[1], parts[2], parts[3]),
                                           'image/jpeg')
                    data = E.thumb(parts[1], parts[2], w=q.get('w', 320))
                    return self._send(200, data, 'image/jpeg',
                                      {'cache-control': 'private, max-age=3600'})
                if parts[:1] == ['download'] and len(parts) == 3 and self.command in ('GET', 'HEAD'):
                    preset = parts[2][:-4] if parts[2].endswith('.zip') else parts[2]
                    return self._bytes(E.zip_path(parts[1], preset), 'application/zip',
                                       f'wingman-{preset}.zip')
                if p == '/token' and self.command == 'GET':
                    if not self._trusted():
                        raise WingmanError('the token is only readable from loopback', 403)
                    return self._send(200, {'token': token})
                return self._send(200, route(self.command, p, query, self._body(),
                                             trusted=self._trusted()))
            except WingmanError as e:
                return self._send(e.status if e.status in range(400, 600) else 400, e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = do_HEAD = _dispatch

        def log_message(self, *a):
            pass

    print(f'wingman on {BIND}:{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools, sets {E.SETS_DIR}, detector '
          f'{E.detector()["name"]}', flush=True)
    ThreadingHTTPServer((BIND, port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
