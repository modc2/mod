#!/usr/bin/env python3
"""openrouter api — REST + MCP + console on one port, zero dependencies.

Every route is a thin call into the same `Client` the MCP tools use, so the
browser, the CLI and an agent all get the same answer to the same question.

The key is per request and is never stored by this server: send it as
`x-openrouter-key: sk-or-v1-…` (or `authorization: Bearer sk-or-…`). Absent
that, the client falls back to the operator's own env and off-tree keystore —
so a locally-run server is convenient, and a shared one is BYOK.

    python3 api.py [--port 50600]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import client as C   # noqa: E402
import mcp           # noqa: E402
from client import SPEND_USD, Client, ORError  # noqa: E402

BASE = os.environ.get('BASE_PATH', '/openrouter')
PORT = int(os.environ.get('PORT', 50600))


def info():
    return {
        'name': 'openrouter',
        'version': mcp.version(),
        'what': 'the whole of OpenRouter behind one mod — catalog search by price and '
                'capability, per-provider endpoint pricing and uptime, chat and '
                'completion with full provider routing, real spend per generation, '
                'key and credit state, and key provisioning',
        'upstream': C.BASE,
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'byok': {'headers': ['x-openrouter-key: sk-or-v1-…',
                             'authorization: Bearer sk-or-v1-…',
                             'x-openrouter-provisioning-key: … (only for /keys)'],
                 'keystore': f'{C.KEY_FILE} (0600, off-tree)',
                 'env': 'OPENROUTER_API_KEY, OPENROUTER_PROVISIONING_KEY',
                 'rule': "every call spends the caller's own credits — no house key"},
        'spend_guard_usd': SPEND_USD,
        'endpoints': {
            'GET /health': 'liveness, tool count, and whether a key resolved',
            'GET /models': 'q, modality, input, output, free, tools, reasoning, '
                           'structured, min_context, max_prompt_usd_m, '
                           'max_completion_usd_m, provider, sort, limit, refresh',
            'GET /model': 'id=author/slug — catalog row + every provider endpoint',
            'GET /endpoints': 'id=author/slug — price, uptime, throughput per provider',
            'GET /providers': 'the provider catalog (q= to filter)',
            'POST /chat': '{model|models, prompt|messages, system, provider, tools, '
                          'reasoning, transforms, max_tokens, stream, confirm}',
            'POST /complete': '{model, prompt, max_tokens, …} — legacy text completion',
            'GET /cost': 'prompt_tokens, completion_tokens, model? + catalog filters',
            'GET /generation': 'id=gen-… — native token counts and the real cost',
            'GET /key': 'key label, usage, limits, rate limit + credit balance',
            'GET /credits': 'purchased vs used',
            'GET /state': 'which keys this request resolved to, and the spend guard',
            'GET /keys': 'provisioned keys (needs a provisioning key)',
            'POST /keys': '{action: list|get|create|update|delete, …}',
            'POST /set_key': '{key, provisioning_key, persist}',
            'POST /raw': '{path, method, body, params} — any OpenRouter route',
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
    }


def _keys_from(headers):
    """BYOK per request. An `authorization` header only counts if it is an
    OpenRouter key — the gateway puts its own bearer tokens there."""
    key = headers.get('x-openrouter-key')
    if not key:
        auth = (headers.get('authorization') or '').strip()
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
        if token.startswith('sk-or'):
            key = token
    return {'key': key or None,
            'provisioning_key': headers.get('x-openrouter-provisioning-key') or None}


def route(method, path, query, body, keys):
    """One request → one JSON answer. Raises ORError for real failures."""
    c = Client(**keys)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}

    def arg(name, default=None):
        return b.get(name, q.get(name, default))

    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'tools': len(mcp.TOOLS), 'key': c.key_state()['key']}
    if path == '/models':
        return c.search(**{k: v for k, v in q.items() if k != 'refresh'},
                        refresh=q.get('refresh') in ('1', 'true'))
    if path == '/model':
        return c.model(_need(arg('id'), 'id'),
                       endpoints=str(arg('endpoints', '1')) not in ('0', 'false'))
    if path == '/endpoints':
        return c.endpoints(_need(arg('id'), 'id'))
    if path == '/providers':
        return c.providers(q=q.get('q'))
    if path == '/chat' and method == 'POST':
        return c.chat(**b)
    if path == '/complete' and method == 'POST':
        return c.complete(_need(b.get('model'), 'model'), _need(b.get('prompt'), 'prompt'),
                          **{k: v for k, v in b.items() if k not in ('model', 'prompt')})
    if path == '/cost':
        a = {**q, **b}
        return c.cost(prompt_tokens=a.pop('prompt_tokens', 1000),
                      completion_tokens=a.pop('completion_tokens', 1000),
                      model=a.pop('model', None), limit=a.pop('limit', 15), **a)
    if path == '/generation':
        return c.generation(_need(arg('id'), 'id'))
    if path == '/key':
        return c.key_info()
    if path == '/credits':
        return c.credits()
    if path == '/keys':
        return c.provision(**({'action': 'list', **b} if method == 'POST' else
                              {'action': q.get('action') or 'list', **q}))
    if path == '/set_key' and method == 'POST':
        if not (b.get('key') or b.get('provisioning_key')):
            raise ORError('send key and/or provisioning_key', status=400)
        return C.set_key(key=b.get('key'), provisioning_key=b.get('provisioning_key'),
                         persist=b.get('persist', True))
    if path == '/state':
        return {**c.key_state(), 'spend_guard_usd': SPEND_USD, 'upstream': C.BASE}
    if path == '/raw' and method == 'POST':
        return c.raw(_need(b.get('path'), 'path'), method=b.get('method') or 'GET',
                     body=b.get('body'), params=b.get('params'),
                     provisioning=bool(b.get('provisioning')))
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise ORError(f'no route {method} {path} — GET / lists them', status=404)


def _need(v, name):
    if v in (None, ''):
        raise ORError(f'{name} is required', status=400)
    return v


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /openrouter behind the gateway or served bare at :50600/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/openrouter', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'openrouter/' + mcp.version()

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
            self.send_header('access-control-allow-methods', 'GET,POST,PATCH,DELETE,OPTIONS')

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
            """Strip the gateway prefixes so /openrouter/_api/models == /models."""
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

        def _stream(self, body, keys):
            """SSE passthrough, so the console renders tokens as they arrive.

            No content-length is knowable up front, so this closes the connection
            at the end rather than trying to keep it alive.
            """
            self.close_connection = True
            started = False
            try:
                for chunk in Client(**keys).stream(**body):
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
            except ORError as e:
                if not started:
                    return self._send(e.status if e.status in range(400, 600) else 400,
                                      e.dict())
                self.wfile.write(b'data: ' + json.dumps({'error': str(e)}).encode() + b'\n\n')
            except Exception as e:
                if not started:
                    return self._send(500, {'error': f'{type(e).__name__}: {e}'})
            if not started:                       # upstream closed without a byte
                self._send(200, b'', 'text/event-stream')

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
                    return self._send(200, json.dumps(info(), indent=2).encode(),
                                      'application/json')
            body = self._read()
            keys = _keys_from(self.headers)
            if p == '/chat' and self.command == 'POST' and body.get('stream'):
                return self._stream({k: v for k, v in body.items() if k != 'stream'}, keys)
            try:
                return self._send(200, route(self.command, p, query, body, keys))
            except ORError as e:
                return self._send(e.status if e.status in range(400, 600) else 400, e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_PATCH = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'openrouter on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
