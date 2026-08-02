#!/usr/bin/env python3
"""docs mcp — Model Context Protocol server for the mod protocol documentation.

Turns the `docs` module into a set of tools any MCP client (Claude Code /
Desktop, IDEs, agent frameworks) can call: read the protocol doc pages in
either flavor (ENGINEER / HUMAN), the whitepaper, the module catalog, and
search across both. Same content the web app renders at /docs — no scraping.

Self-contained by design: the JSON-RPC 2.0 handling is hand-rolled on the
stdlib (no `mcp` package, no fastapi) and every tool is a thin wrap of a
`m.mod('docs')` function, so the CLI, the app and the tools can never drift.

Transports:
    python3 api/mcp.py                    # stdio — one JSON-RPC msg per line
    python3 api/mcp.py --http [--port N]  # Streamable HTTP — POST /mcp (:50192)

The docs app also proxies POST /docs/mcp here, so the same server is reachable
on the public :50191 route without a second hop for clients.

Read-only and unauthenticated: the docs are public.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
# The module ships its own mod.py — keep its directory off sys.path or
# `import mod` grabs docs/mod.py instead of the framework.
sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') != MODULE]
try:
    import mod as m
except ModuleNotFoundError:
    # Launched outside the repo's python env (nix image, an agent client
    # spawning us with a bare environment) — `mod` is the repo package itself.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(MODULE))))
    import mod as m  # noqa: E402

# Echo the client's protocol version when we know it; otherwise pin the oldest
# revision whose feature set (plain-JSON Streamable HTTP, tools) we implement.
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Documentation for the mod protocol: a toolkit for building apps out of '
    'small composable modules. Use docs_pages to see what exists, docs_page '
    'to read one (simple=true gives the plain-language HUMAN twin, default is '
    'the technical ENGINEER text), docs_search to find the right page, '
    'docs_modules for the catalog of shipped modules and docs_module_doc for '
    "one module's README + skill. Everything is public — no auth."
)


def _docs():
    """Resolved lazily so the server starts even if a dep is briefly down."""
    return m.mod('docs')()


def _req(args: dict, key: str) -> str:
    v = str(args.get(key) or '').strip()
    if not v:
        raise ValueError(f'{key} required')
    return v


# ── tool handlers (thin wrappers over mod.py functions) ──

def _t_overview(args):
    return _docs().overview()


def _t_pages(args):
    d = _docs()
    simple = set(d.simple_pages())
    return {'pages': [{'name': p, 'simple': p in simple} for p in d.pages()]}


def _t_page(args):
    d, name = _docs(), _req(args, 'name')
    simple = bool(args.get('simple'))
    return {'page': name,
            'variant': 'simple' if simple and name in d.simple_pages() else 'tech',
            'text': d.page(name, simple=simple)}


def _t_search(args):
    return _docs().search(_req(args, 'query'))


def _t_whitepaper(args):
    fmt = str(args.get('fmt') or 'md')
    # mod.py falls back to markdown for anything it doesn't know; say so
    # instead, so a typo'd fmt doesn't look like a successful answer.
    if fmt not in ('md', 'simple', 'tex'):
        raise ValueError(f"unknown fmt '{fmt}' — use md, simple or tex")
    text = _docs().whitepaper(fmt)
    if text is None:
        raise ValueError(f"no whitepaper in format '{fmt}'")
    return {'fmt': fmt, 'text': text}


def _t_modules(args):
    return {'modules': _docs().modules(str(args.get('group') or 'all'))}


def _t_module_doc(args):
    return _docs().doc(_req(args, 'module'))


TOOLS = {
    'docs_overview': {
        'description': 'Front-door overview of the mod protocol: what it is, '
                       'what it is made of, where to go next. Start here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_overview,
    },
    'docs_pages': {
        'description': 'List the protocol doc pages (getting-started, cli, api, '
                       'protocol, modules, orbit, servers, storage, keys, '
                       'skills, contracts, frontend, utils, whitepaper …). '
                       '`simple: true` means the page has a plain-language twin.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_pages,
    },
    'docs_page': {
        'description': 'Read one doc page as markdown. Default is the technical '
                       '(ENGINEER) text; simple=true returns the plain-language '
                       '(HUMAN) twin when one exists, else falls back to the '
                       'technical page — the returned `variant` says which.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': 'page name from docs_pages, e.g. "cli"'},
            'simple': {'type': 'boolean', 'description': 'true = plain-language twin (default false)'},
        }, 'required': ['name']},
        'handler': _t_page,
    },
    'docs_search': {
        'description': 'Search the docs: returns the doc pages whose name or '
                       'body contains the query, plus the modules whose name or '
                       'description matches. Use it to find the page to read.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': 'text to look for (case-insensitive)'},
        }, 'required': ['query']},
        'handler': _t_search,
    },
    'docs_whitepaper': {
        'description': 'The mod protocol whitepaper. fmt=md (default), '
                       'simple (plain language) or tex (LaTeX source).',
        'inputSchema': {'type': 'object', 'properties': {
            'fmt': {'type': 'string', 'enum': ['md', 'simple', 'tex'],
                    'description': 'output flavor (default md)'},
        }},
        'handler': _t_whitepaper,
    },
    'docs_modules': {
        'description': 'The module catalog: every module in the orbit with its '
                       'group, description and which docs it ships. '
                       'group=orbit|core|all (default all).',
        'inputSchema': {'type': 'object', 'properties': {
            'group': {'type': 'string', 'enum': ['orbit', 'core', 'all'],
                      'description': 'which half of the repo (default all)'},
        }},
        'handler': _t_modules,
    },
    'docs_module_doc': {
        'description': "One module's shipped documentation: description, "
                       'README.md and skill.md. Use after docs_modules or '
                       'docs_search to learn how a specific module works.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': {'type': 'string', 'description': 'module name, e.g. "chain"'},
        }, 'required': ['module']},
        'handler': _t_module_doc,
    },
}


# ── JSON-RPC 2.0 ──

def _result(id_, result: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _call_tool(id_, params: dict) -> dict:
    name = str(params.get('name') or '')
    tool = TOOLS.get(name)
    if not tool:
        return _error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = tool['handler'](args)
    except Exception as e:
        # Tool failures are *successful* JSON-RPC responses carrying isError —
        # per MCP spec — so the calling model reads the message and retries.
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name} failed: {type(e).__name__}: {e}'}],
                             'isError': True})
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body) -> dict:
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 '
                                   'object with a method')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        client_ver = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'docs', 'version': _version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': [
            {'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]})
    if method == 'tools/call':
        return _call_tool(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def _version() -> str:
    p = os.path.join(MODULE, 'config.json')
    try:
        return json.loads(open(p).read()).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


# ── transports ──

def serve_stdio():
    """One JSON message per line on stdin → one response line on stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()


def serve_http(port: int, base: str = '/docs'):
    """Streamable HTTP without SSE: one JSON-RPC message per POST /mcp."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    paths = ('/mcp', base.rstrip('/') + '/mcp')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def do_GET(self):
            if self.path.rstrip('/').endswith('/health'):
                return self._send(200, b'ok', 'text/plain')
            self._send(405, b'POST JSON-RPC 2.0 messages to this endpoint',
                       'text/plain')

        def do_POST(self):
            if self.path.split('?')[0].rstrip('/') not in paths:
                return self._send(404, b'not found', 'text/plain')
            n = int(self.headers.get('content-length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'')
            except Exception:
                return self._send(400, _error(None, -32700,
                                              'parse error: body is not valid JSON'))
            resp = handle(body)
            if resp is None:  # notification — nothing to answer
                return self._send(202, b'', 'text/plain')
            self._send(200, resp)

        def log_message(self, *a):  # quiet: pm2 logs are for real events
            pass

    print(f'docs mcp on :{port} — POST {paths[1]}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        i = argv.index('--port') + 1 if '--port' in argv else -1
        port = int(argv[i] if i > 0 else os.environ.get('MCP_PORT', 50192))
        serve_http(port, os.environ.get('BASE_PATH', '/docs'))
    else:
        serve_stdio()
