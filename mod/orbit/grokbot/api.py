#!/usr/bin/env python3
"""grokbot api — REST + MCP + the console on one port, zero dependencies.

Every route is a thin call into the same `Client` the MCP tools and the CLI
use, so an agent, a shell and a browser never get different answers.

Two headers matter and they are not the same thing:

    Authorization: Bearer <mod-protocol token>   who you are (sign-in)
    x-xai-key: xai-…                             whose Grok credits get spent

Signing in is what gives you somewhere to *keep* a key and a bot. Sending the
key per request works too, and stores nothing.

    python3 api.py [--port 50890]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import client as C     # noqa: E402
import identity        # noqa: E402
import mcp             # noqa: E402
from client import Client, GrokError    # noqa: E402

BASE = os.environ.get('BASE_PATH', '/grokbot')
PORT = int(os.environ.get('PORT', os.environ.get('GROKBOT_PORT', 50890)))


def info():
    return {
        'name': 'grokbot',
        'version': mcp.version(),
        'what': 'Grok (xAI) as a mod: sign in from the website with a wallet, '
                'save your own xAI key, keep named bots, and chat — over REST, '
                'MCP or the browser console.',
        'upstream': C.BASE,
        'auth': identity.status(),
        'byok': {'headers': ['x-xai-key: xai-…', 'authorization: Bearer xai-… '
                             '(BYOK, not sign-in)'],
                 'account': '~/.mod/grokbot/users/<address>.json (0600, off-tree)',
                 'env': 'XAI_API_KEY, GROK_API_KEY',
                 'rule': "every call spends the caller's own xAI credits — this "
                         'module holds no house key'},
        'default_model': C.DEFAULT_MODEL,
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'endpoints': {
            'GET /health': 'liveness and tool count',
            'GET /me': 'who this token is, whether a key is on file, bots',
            'POST /key': '{key: "xai-…", persist} — store your key (signed in)',
            'DELETE /key': 'forget the key on file',
            'GET /models': 'every model your key can see (refresh=1 to re-fetch)',
            'GET /model': 'id=grok-4-fast — one model',
            'GET /keyinfo': "what xAI says about the key itself",
            'POST /chat': '{prompt|messages, model, system, bot, temperature, '
                          'max_tokens, search, stream}',
            'GET /bots': 'your saved bots',
            'POST /bots': '{name, system, model, temperature, search, description}',
            'DELETE /bots': 'name=… — delete one',
            'POST /images': '{prompt, model, n}',
            'POST /raw': '{path, method, body, params} — any xAI route',
            'GET /stats': 'accounts and bots on this deployment (owner only)',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console — wallet sign-in, key, bots, chat',
        },
    }


def byok(headers):
    """The xAI key for this request, if the caller sent one.

    An `authorization` header only counts as a key when it looks like one — the
    gateway and the sign-in flow both put mod-protocol tokens there.
    """
    key = headers.get('x-xai-key') or headers.get('x-grok-key')
    if not key:
        auth = (headers.get('authorization') or '').strip()
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
        if token.startswith('xai-'):
            key = token
    return key or None


def route(method, path, query, body, token, key):
    """One request → one JSON answer. Raises GrokError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}

    def arg(name, default=None):
        return b.get(name, q.get(name, default))

    def signed():
        return identity.require(token)

    def caller():
        """The address if there is one — chat works signed-in or BYOK."""
        return identity.whoami(token)

    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'tools': len(mcp.TOOLS), 'upstream': C.BASE}
    if path == '/me':
        address = caller()
        user = C.load_user(address) if address else {}
        return {'address': address, 'role': identity.role(address),
                'signed_in': bool(address),
                'key': Client(key=key, address=address).key_state(),
                'bots': [b['name'] for b in (user.get('bots') or {}).values()],
                'auth': identity.status()}
    if path == '/key':
        address = signed()
        if method == 'DELETE':
            return C.set_user_key(address, '')
        if method == 'POST':
            return C.set_user_key(address, _need(b.get('key'), 'key'),
                                  persist=b.get('persist', True))
        return Client(key=key, address=address).key_state()
    if path == '/models':
        return Client(key=key, address=caller()).models(
            refresh=str(arg('refresh', '')) in ('1', 'true'))
    if path == '/model':
        return Client(key=key, address=caller()).model(_need(arg('id'), 'id'))
    if path == '/keyinfo':
        return Client(key=key, address=caller()).key_info()
    if path == '/chat' and method == 'POST':
        return Client(key=key, address=caller()).chat(**b)
    if path == '/bots':
        address = signed()
        if method == 'POST':
            return C.save_bot(address, _need(b.get('name'), 'name'),
                              system=b.get('system'), model=b.get('model'),
                              temperature=b.get('temperature'),
                              search=b.get('search'),
                              description=b.get('description'))
        if method == 'DELETE':
            return C.delete_bot(address, _need(arg('name'), 'name'))
        return {'bots': C.bots(address), 'address': address}
    if path == '/images' and method == 'POST':
        return Client(key=key, address=caller()).images(
            _need(b.get('prompt'), 'prompt'), model=b.get('model', 'grok-2-image'),
            n=b.get('n', 1))
    if path == '/raw' and method == 'POST':
        return Client(key=key, address=caller()).raw(
            _need(b.get('path'), 'path'), method=b.get('method') or 'GET',
            body=b.get('body'), params=b.get('params'))
    if path == '/stats':
        identity.require_owner(token)
        return stats()
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise GrokError(f'no route {method} {path} — GET / lists them', status=404)


def stats():
    """Accounts on this box. Never the keys themselves — only that one exists."""
    rows = []
    try:
        names = sorted(os.listdir(C.USERS))
    except FileNotFoundError:
        names = []
    for name in names:
        if not name.endswith('.json'):
            continue
        user = C.load_user(name[:-5])
        rows.append({'address': name[:-5], 'key': bool(user.get('key')),
                     'bots': len(user.get('bots') or {}),
                     'key_set': user.get('key_set')})
    return {'accounts': len(rows), 'users': rows, 'owner': identity.owner()}


def _need(v, name):
    if v in (None, ''):
        raise GrokError(f'{name} is required', status=400)
    return v


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works mounted at /grokbot
    # behind the gateway and served bare at :50890/ alike.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/grokbot', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'grokbot/' + mcp.version()

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self._cors()
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def _cors(self):
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods',
                             'GET,POST,PATCH,DELETE,OPTIONS')

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

        def _path(self):
            """Strip the gateway prefixes so /grokbot/_api/models == /models."""
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

        def _stream(self, body, token, key):
            """SSE passthrough, so the console renders tokens as they arrive."""
            self.close_connection = True
            started = False
            try:
                c = Client(key=key, address=identity.whoami(token))
                for chunk in c.stream(**body):
                    if not started:
                        self.send_response(200)
                        self.send_header('content-type', 'text/event-stream')
                        self.send_header('cache-control', 'no-cache')
                        self.send_header('connection', 'close')
                        self._cors()
                        self.end_headers()
                        started = True
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except GrokError as e:
                if not started:
                    return self._send(e.status if e.status in range(400, 600)
                                      else 400, e.dict())
                self.wfile.write(b'data: ' + json.dumps({'error': str(e)}).encode()
                                 + b'\n\n')
            except Exception as e:
                if not started:
                    return self._send(500, {'error': f'{type(e).__name__}: {e}'})
            if not started:                     # upstream closed without a byte
                self._send(200, b'', 'text/event-stream')

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                token = identity.strip(self.headers.get('authorization'))
                resp = mcp.handle(self._read(), token=token,
                                  key=byok(self.headers))
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, json.dumps(info(), indent=2).encode())
            body = self._read()
            token = self.headers.get('authorization')
            key = byok(self.headers)
            if p == '/chat' and self.command == 'POST' and body.get('stream'):
                return self._stream({k: v for k, v in body.items()
                                     if k != 'stream'}, token, key)
            try:
                return self._send(200, route(self.command, p, query, body,
                                             token, key))
            except identity.AuthError as e:
                return self._send(401, {'error': str(e), 'signin': 'connect a '
                                        'wallet in the console'})
            except identity.Denied as e:
                return self._send(403, {'error': str(e)})
            except GrokError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_PATCH = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'grokbot on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
