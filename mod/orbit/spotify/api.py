#!/usr/bin/env python3
"""spotify api — REST + MCP + the OAuth redirect on one port, zero dependencies.

Every route is a thin call into the same `Spotify` adapter the MCP tools and
the CLI use. The server also serves the OAuth redirect at `/callback`, so
pointing the app's redirect URI at `http://127.0.0.1:<port>/callback` lets the
browser finish the login without a second process.

    python3 api.py [--port 50610]

BYOK: send `authorization: Bearer <spotify access token>` and that token is
used for the request and never stored. Without it, the module falls back to
the operator's own ~/.mod/spotify tokens — convenient locally, so bind this to
loopback if you ever expose the box.
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import mcp                              # noqa: E402
from spotify import Spotify, SpotifyError  # noqa: E402

BASE = os.environ.get('BASE_PATH', '/spotify')
PORT = int(os.environ.get('PORT', 50610))


def info():
    return {
        'name': 'spotify',
        'version': mcp.version(),
        'what': 'MCP server + adapter for the Spotify Web API — playback, queue, '
                'search, library and playlists on the caller\'s own account',
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': f'python3 {os.path.join(HERE, "mcp.py")}',
                'tools': len(mcp.TOOLS)},
        'auth': {'flow': 'OAuth 2.0 authorization code + PKCE',
                 'keystore': '~/.mod/spotify/keys.json (0600, off-tree)',
                 'tokens': '~/.mod/spotify/auth.json (0600, off-tree)',
                 'byok': 'authorization: Bearer <spotify token> per request',
                 'start': 'GET /login → open the url → redirect lands on /callback'},
        'endpoints': {
            'GET /': 'this', 'GET /health': 'liveness',
            'GET /status': 'auth + player state',
            'GET /login': 'authorize url (PKCE)', 'GET /callback': 'OAuth redirect',
            'GET /search': 'q, type, limit, market',
            'GET /now': 'what is playing', 'GET /devices': 'reachable devices',
            'POST /play': '{query|uri, device, position_ms, shuffle}',
            'POST /pause': '{device}', 'POST /next': '{device}',
            'POST /previous': '{device}', 'POST /seek': '{position_ms}',
            'POST /volume': '{percent}', 'POST /shuffle': '{state}',
            'POST /repeat': '{state}', 'POST /transfer': '{device, play}',
            'POST /queue': '{query|uri}', 'GET /queue': 'what is up next',
            'GET /recent': 'limit', 'GET /top': 'type, time_range, limit',
            'GET /saved': 'limit', 'POST /save': '{query|uri, remove}',
            'GET /playlists': 'limit, offset', 'GET /playlist': 'id, limit',
            'POST /playlist': '{name, public, description, tracks}',
            'POST /playlist/tracks': '{id, tracks, remove, position}',
            'GET /lookup': 'uri', 'POST /raw': '{path, method, params, body}',
            'GET /tools': 'the MCP tool registry', 'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def _need(v, name):
    if v in (None, ''):
        raise SpotifyError(f'{name} is required')
    return v


def route(method, path, query, body, token=None):
    """One request → one JSON answer. Raises SpotifyError for real failures."""
    sp = Spotify(token=token)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}

    def arg(name, *alts, default=None):
        for n in (name, *alts):
            if b.get(n) not in (None, ''):
                return b[n]
            if q.get(n) not in (None, ''):
                return q[n]
        return default

    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'tools': len(mcp.TOOLS)}
    if path == '/status':
        return mcp.call_tool('spotify_status', {})
    if path == '/login':
        return sp.authorize_url(scopes=q.get('scopes'),
                                redirect_uri=q.get('redirect_uri'))
    if path == '/callback':
        if q.get('error'):
            raise SpotifyError(f"spotify denied the request: {q['error']}", 400)
        return sp.exchange(_need(q.get('code'), 'code'), q.get('state'))
    if path == '/logout' and method == 'POST':
        return sp.logout()
    if path == '/keys' and method == 'POST':
        return sp.set_key(b.get('client_id'), b.get('client_secret'),
                          b.get('redirect_uri'))

    if path == '/search':
        return sp.search(_need(arg('q', 'query'), 'q'), type=arg('type', default='track'),
                         limit=arg('limit', default=10), market=arg('market'))
    if path == '/lookup':
        return sp.lookup(_need(arg('uri', 'url', 'id'), 'uri'))
    if path == '/now':
        return sp.now_playing()
    if path == '/devices':
        return sp.devices()
    if path == '/queue' and method == 'GET':
        return sp.up_next(limit=arg('limit', default=10))
    if path == '/recent':
        return sp.recent(limit=arg('limit', default=20))
    if path == '/top':
        return sp.top(type=arg('type', default='tracks'),
                      time_range=arg('time_range', default='medium_term'),
                      limit=arg('limit', default=20))
    if path == '/saved' and method == 'GET':
        return sp.saved(limit=arg('limit', default=20), offset=arg('offset', default=0))
    if path == '/playlists':
        return sp.playlists(limit=arg('limit', default=50), offset=arg('offset', default=0))
    if path == '/playlist' and method == 'GET':
        return sp.playlist(_need(arg('id', 'uri'), 'id'), limit=arg('limit', default=100))

    if method in ('POST', 'PUT'):
        if path == '/play':
            return sp.play(query=arg('query', 'q'), uri=arg('uri'), device=arg('device'),
                           position_ms=arg('position_ms'), shuffle=arg('shuffle'))
        if path == '/pause':
            return sp.pause(device=arg('device'))
        if path == '/next':
            return sp.next(device=arg('device'))
        if path == '/previous':
            return sp.previous(device=arg('device'))
        if path == '/seek':
            return sp.seek(_need(arg('position_ms'), 'position_ms'), device=arg('device'))
        if path == '/volume':
            return sp.volume(_need(arg('percent', 'volume_percent'), 'percent'),
                             device=arg('device'))
        if path == '/shuffle':
            return sp.shuffle(arg('state', default=True), device=arg('device'))
        if path == '/repeat':
            return sp.repeat(arg('state', default='context'), device=arg('device'))
        if path == '/transfer':
            return sp.transfer(_need(arg('device'), 'device'), play=arg('play', default=True))
        if path == '/queue':
            return sp.queue(query=arg('query', 'q'), uri=arg('uri'), device=arg('device'))
        if path == '/save':
            return sp.save(query=arg('query', 'uri'), remove=bool(arg('remove')))
        if path == '/playlist':
            return sp.playlist_create(_need(arg('name'), 'name'),
                                      public=bool(arg('public')),
                                      description=arg('description'),
                                      uris=arg('tracks', 'uris'))
        if path == '/playlist/tracks':
            pid, tracks = _need(arg('id'), 'id'), _need(arg('tracks', 'uris'), 'tracks')
            if arg('remove'):
                return sp.playlist_remove(pid, tracks)
            return sp.playlist_add(pid, tracks, position=arg('position'))
        if path == '/raw':
            return sp.raw(_need(arg('path'), 'path'), method=arg('method', default='GET'),
                          body=b.get('body'), params=b.get('params'))
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise SpotifyError(f'no route {method} {path} — GET / lists them', 404)


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works mounted at /spotify
    # behind the gateway or served bare at :50610/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/spotify', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'spotify/' + mcp.version()

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods', 'GET,POST,PUT,DELETE,OPTIONS')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def _read(self):
            n = int(self.headers.get('content-length') or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b'{}')
            except Exception:
                return {}

        def _token(self):
            auth = self.headers.get('authorization') or ''
            return auth[7:].strip() if auth[:7].lower() == 'bearer ' else None

        def _path(self):
            """Strip the gateway prefixes so /spotify/_api/now == /now."""
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

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcp.handle(self._read())
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, info())
            try:
                out = route(self.command, p, query, self._read(), self._token())
            except SpotifyError as e:
                return self._send(e.status if 400 <= e.status < 600 else 400, e.dict())
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})
            # The browser lands on /callback itself — answer it in HTML.
            if p == '/callback' and self.command == 'GET':
                who = (out.get('user') or {}).get('name') or 'your account'
                html = (f'<body style="font:15px ui-monospace;background:#0b0f0c;'
                        f'color:#1db954;padding:3rem"><h2>spotify: connected</h2>'
                        f'<p>{who} — you can close this tab.</p></body>').encode()
                return self._send(200, html, 'text/html; charset=utf-8')
            return self._send(200, out)

        do_GET = do_POST = do_PUT = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'spotify on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
