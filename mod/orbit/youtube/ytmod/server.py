"""
ytmod.server — the app, REST and MCP on one port, stdlib only.

    GET  /                 the web app (web/index.html) for browsers, /health JSON for the rest
    GET  /health
    GET  /open?target=     resolve any YouTube url/id/@handle -> {kind,id,embed,watch}
    GET  /go?target=       302 to the framable embed of that target
    GET  /tools            every MCP tool with its ring and schema
    GET|POST /tools/<name> call one tool (query string or JSON body = arguments)
    POST /mcp              MCP streamable HTTP (JSON responses, no SSE)

    GET  /whoami           who the Authorization: Bearer <mod-protocol token> is
    GET  /auth/local       loopback only: a token minted with the box's own key
    GET  /auth/start       owner: {url} to Google's consent screen (?go=1 redirects there)
    GET  /auth/callback    Google sends the browser back here with ?code=&state=
    GET  /client           owner: OAuth client / api key status
    POST /client           owner: {client_id, client_secret, api_key, redirect_uri}

Rings: `open` tools run for anyone (keyless or api key). `user` and `creator`
tools act as a Google account, so they run only for the box (loopback, no
proxy header) or its owner (bearer token whose address is the box key).
Paths are also accepted under /youtube and /youtube/api (gateway convention).
Failures are 4xx JSON: Cloudflare eats 5xx bodies.
"""
from __future__ import annotations

import argparse
import html
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import api, auth, identity, store
from .mcp_server import SERVER_INFO, TOOLS, call_tool, handle_message, tool_list

PREFIXES = ('/youtube/api', '/youtube', '/api')
WEB = Path(__file__).resolve().parents[1] / 'web' / 'index.html'

# friendly REST names -> tools
ALIASES = {
    '/search': 'yt_search', '/video': 'yt_video', '/channel': 'yt_channel', '/playlist': 'yt_playlist',
    '/trending': 'yt_trending', '/rss': 'yt_rss', '/home': 'yt_home', '/comments': 'yt_comments',
    '/status': 'yt_status', '/accounts': 'yt_accounts', '/accounts/use': 'yt_use_account',
    '/accounts/remove': 'yt_remove_account', '/subscriptions': 'yt_subscriptions', '/feed': 'yt_feed',
    '/playlists': 'yt_my_playlists', '/playlists/create': 'yt_create_playlist',
    '/playlists/add': 'yt_playlist_add', '/playlists/remove': 'yt_playlist_remove',
    '/rate': 'yt_rate', '/rating': 'yt_rating', '/subscribe': 'yt_subscribe', '/unsubscribe': 'yt_unsubscribe',
    '/comment': 'yt_comment', '/reply': 'yt_reply', '/me': 'yt_me', '/my/videos': 'yt_my_videos',
    '/upload': 'yt_upload', '/video/update': 'yt_update_video', '/video/thumbnail': 'yt_set_thumbnail',
    '/video/delete': 'yt_delete_video', '/analytics': 'yt_analytics', '/my/comments': 'yt_my_comments',
    '/moderate': 'yt_moderate',
}

CALLBACK_HTML = """<!doctype html><meta charset="utf-8"><title>youtube · signed in</title>
<body style="font-family:system-ui;background:#0f0f0f;color:#f1f1f1;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><div style="font-size:22px;margin-bottom:8px">{msg}</div>
<div style="color:#aaa">{sub}</div></div>
<script>try{{if(window.opener){{window.opener.postMessage({{youtube:'signed-in',ok:{ok}}},'*');setTimeout(()=>window.close(),800)}}
else{{setTimeout(()=>location.replace({back}),900)}}}}catch(e){{}}</script>"""


def route(path: str) -> str:
    for p in PREFIXES:
        if path == p or path.startswith(p + '/'):
            return path[len(p):] or '/'
    return path


def _coerce(q: dict) -> dict:
    """Query strings are strings; tools want ints/bools/lists where the schema says so."""
    out = {}
    for k, v in q.items():
        if k in ('n', 'start_s'):
            try:
                out[k] = int(v)
                continue
            except ValueError:
                pass
        if k in ('tags',):
            out[k] = [s for s in v.split(',') if s]
            continue
        if v in ('true', 'false') and k in ('ban', 'made_for_kids'):
            out[k] = v == 'true'
            continue
        out[k] = v
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = 'mod-youtube/0.1'

    def log_message(self, *a):
        pass

    # ── plumbing ──
    def _send(self, code: int, body, ctype='application/json', extra: dict | None = None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _html(self, text: str, code=200):
        self._send(code, text.encode(), 'text/html; charset=utf-8', {'Cache-Control': 'no-cache'})

    def _body(self) -> dict:
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        # urllib and HTML forms both label bodies x-www-form-urlencoded; trust the bytes, not the label
        if raw.lstrip()[:1] in (b'{', b'['):
            return json.loads(raw)
        return {k: v[-1] for k, v in urllib.parse.parse_qs(raw.decode()).items()}

    def _forwarded(self) -> bool:
        return bool(self.headers.get('X-Forwarded-For') or self.headers.get('X-Real-IP')
                    or self.headers.get('X-Forwarded-Host'))

    def _is_box(self) -> bool:
        return self.client_address[0] in ('127.0.0.1', '::1') and not self._forwarded()

    def _caller(self):
        """(address|None, owner: bool). Raises identity.AuthError on a bad token."""
        addr = identity.whoami(self.headers.get('Authorization', ''))
        box = identity.box_address()
        owner = self._is_box() or bool(addr and box and addr == box)
        return addr, owner

    def _ctx(self) -> dict:
        try:
            addr, owner = self._caller()
        except identity.AuthError:
            addr, owner = None, False
        return {'owner': owner, 'address': addr}

    def _redirect_uri(self) -> str:
        c = store.client().get('redirect_uri')
        if c:
            return c
        proto = self.headers.get('X-Forwarded-Proto') or 'http'
        host = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or f'localhost:{self.server.server_port}'
        prefix = '/youtube/api' if self.headers.get('X-Forwarded-Host') else ''
        return f'{proto}://{host}{prefix}/auth/callback'

    def _app_url(self) -> str:
        proto = self.headers.get('X-Forwarded-Proto') or 'http'
        host = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or 'localhost'
        return f'{proto}://{host}' + ('/youtube/' if self.headers.get('X-Forwarded-Host') else '/')

    def _tool(self, name: str, args: dict):
        res = call_tool(name, args, self._ctx())
        if res.get('isError'):
            msg = res['content'][0]['text']
            code = 401 if ('owner' in msg or 'sign in' in msg.lower() or 'signed in' in msg) else \
                400 if msg.startswith('missing argument') else 424
            return self._send(code, {'error': msg, 'tool': name})
        return self._send(200, res.get('structuredContent', {}))

    # ── verbs ──
    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = route(u.path).rstrip('/') or '/'
        if path == '/app' or (path == '/' and 'text/html' in self.headers.get('Accept', '')):
            try:
                return self._html(WEB.read_text())
            except OSError:
                return self._send(404, {'error': 'web/index.html is missing'})
        if path in ('/', '/health'):
            act = store.get()
            return self._send(200, {'ok': True, **SERVER_INFO, 'client': store.client_status(),
                                    'accounts': len(store.accounts()),
                                    'active': {k: act.get(k) for k in ('id', 'title', 'handle')} if act else None,
                                    'tools': len(TOOLS), 'mcp': '/mcp'})
        if path == '/open':
            try:
                return self._send(200, api.resolve(q.get('target') or q.get('url') or q.get('q', '')))
            except api.ApiError as e:
                return self._send(e.status if e.status < 500 else 424, {'error': str(e)})
        if path == '/go':
            try:
                r = api.resolve(q.get('target') or q.get('url', ''))
            except api.ApiError as e:
                return self._send(400, {'error': str(e)})
            if r.get('embed'):
                return self._send(302, b'', extra={'Location': r['embed']})
            return self._send(400, {'error': f"nothing to frame for kind {r.get('kind')}"})
        if path == '/tools':
            return self._send(200, {'tools': tool_list(), 'rest': ALIASES, 'mcp': '/mcp'})
        if path == '/whoami':
            try:
                addr, owner = self._caller()
            except identity.AuthError as e:
                return self._send(401, {'error': str(e)})
            return self._send(200, {'address': addr, 'signed': bool(addr), 'owner': owner,
                                    'box': self._is_box(), 'auth': identity.status()})
        if path == '/auth/local':
            if not self._is_box():
                return self._send(403, {'error': 'local sign-in is for the box itself; use a browser wallet'})
            try:
                return self._send(200, {'token': identity.local_token(), 'address': identity.box_address(),
                                        'owner': True})
            except identity.AuthError as e:
                return self._send(424, {'error': str(e)})
        if path == '/auth/start':
            if not self._ctx()['owner']:
                return self._send(401, {'error': 'adding a Google account is for the box or its owner: sign in first'})
            scopes = [s for s in q.get('scopes', '').split(' ') if s] or None
            try:
                st = auth.start(self._redirect_uri(), scopes, q.get('hint', ''))
            except auth.AuthError as e:
                return self._send(424, {'error': str(e), 'client': store.client_status()})
            if q.get('go'):
                return self._send(302, b'', extra={'Location': st['url']})
            return self._send(200, st)
        if path == '/auth/callback':
            back = json.dumps(self._app_url() + '#/accounts')
            if q.get('error'):
                return self._html(CALLBACK_HTML.format(msg='Google said no', sub=html.escape(q['error']),
                                                       ok='false', back=back), 400)
            try:
                acc = auth.finish(q.get('code', ''), q.get('state', ''))
            except auth.AuthError as e:
                return self._html(CALLBACK_HTML.format(msg='Sign-in failed', sub=html.escape(str(e)),
                                                       ok='false', back=back), 400)
            return self._html(CALLBACK_HTML.format(
                msg=f"Signed in as {html.escape(acc.get('title') or acc.get('email') or acc['id'])}",
                sub=html.escape(acc.get('email') or ''), ok='true', back=back))
        if path == '/client':
            if not self._ctx()['owner']:
                return self._send(401, {'error': 'owner only'})
            return self._send(200, store.client_status())
        if path.startswith('/tools/'):
            return self._tool(path[len('/tools/'):], _coerce(q))
        if path in ALIASES:
            return self._tool(ALIASES[path], _coerce(q))
        if path == '/mcp':
            return self._send(405, {'error': 'POST JSON-RPC to /mcp; no SSE stream is offered'})
        self._send(404, {'error': f'no route {path}'})

    def do_POST(self):
        path = route(urllib.parse.urlsplit(self.path).path).rstrip('/') or '/'
        try:
            body = self._body()
        except (json.JSONDecodeError, ValueError):
            return self._send(400, {'jsonrpc': '2.0', 'id': None,
                                    'error': {'code': -32700, 'message': 'parse error'}})
        if path == '/mcp':
            resp = handle_message(body, self._ctx())
            return self._send(202, b'') if resp is None else self._send(200, resp)
        if path == '/client':
            if not self._ctx()['owner']:
                return self._send(401, {'error': 'owner only'})
            return self._send(200, store.set_client(body.get('client_id', ''), body.get('client_secret', ''),
                                                    body.get('api_key', ''), body.get('redirect_uri', '')))
        if path.startswith('/tools/'):
            return self._tool(path[len('/tools/'):], body if isinstance(body, dict) else {})
        if path in ALIASES:
            return self._tool(ALIASES[path], body if isinstance(body, dict) else {})
        self._send(404, {'error': f'no route {path}'})

    def do_DELETE(self):
        u = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        path = route(u.path).rstrip('/')
        if path == '/accounts':
            return self._tool('yt_remove_account', q)
        if path == '/video':
            return self._tool('yt_delete_video', q)
        self._send(404, {'error': 'no route'})


def serve(port: int = 51400, host: str = '127.0.0.1') -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print(f'youtube on http://{host}:{port}  (app: / · mcp: /mcp · tools: /tools)', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=51400)
    ap.add_argument('--host', default='127.0.0.1')
    a = ap.parse_args()
    serve(a.port, a.host)
