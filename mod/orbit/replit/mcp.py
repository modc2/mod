#!/usr/bin/env python3
"""replit mcp — Model Context Protocol server for the mod ⇄ Repl bridge.

This is the half of the module an *agent* uses. The console is for hands; this
is for a model that wants to reach the mods running on Replit.

Two layers of tools:

  the bridge itself   replit_catalog / replit_repl / replit_call / replit_link,
                      plus bundle, db and import — everything the console does.
  the mods on Replit  every function of every linked Repl, lifted into its own
                      tool: `repl_{remote}_{fn}`, typed from the signatures the
                      Repl reports on its null call. So a model does not "use
                      the replit module to call a mod" — it just calls the mod.

The dynamic half is rebuilt on every tools/list, so linking a Repl adds tools
without a restart (clients that honour notifications/tools/list_changed pick it
up immediately; the rest see them on their next list).

Transports:
    python3 mcp.py                     # stdio — one JSON-RPC message per line
    python3 mcp.py --http [--port N]   # Streamable HTTP — POST /mcp (:50531)

The console at :50530 also answers POST /mcp with this same dispatcher, so
https://modc2.com/replit/mcp is a live MCP endpoint with no second process.

Writes (link, bundle, import, db writes, and any call that reaches out to a
Repl) follow the module's rule: allowed over stdio and from loopback, refused
for a gateway-proxied caller, so the public endpoint is a read-only catalog.
"""
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'The Replit bridge for the mod protocol. Start with replit_catalog: it '
    'lists every mod deployed on Replit that this bridge knows about, with the '
    'functions each one exposes. Those functions are also lifted into their '
    'own tools, named repl_{remote}_{fn} — prefer those, they are typed. '
    'replit_call is the escape hatch for a function the catalog has not seen '
    'yet (pass fn=""/omit it for the null call, which returns the mod\'s info). '
    'Repls sleep: the first call after idle can take seconds and a timeout is '
    'usually a wake, not a failure — replit_ping, then retry. Going the other '
    'way, replit_bundle turns any fleet module into a Replit-runnable project '
    'and replit_run_url gives the one-click import link.'
)

_API = None


def bind(instance):
    """Let an embedding process (the console server) inject its own Mod."""
    global _API
    _API = instance
    return _API


def api():
    """The module instance — injected, or loaded from mod.py by path.

    Loading by path (not `import mod`) matters twice over: `mod` is the fleet
    SDK's own package name, and mod.py imports this file back for its /mcp
    route, so a plain import would be circular.
    """
    global _API
    if _API is None:
        spec = importlib.util.spec_from_file_location('replit_anchor',
                                                      os.path.join(HERE, 'mod.py'))
        anchor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(anchor)
        _API = anchor.Mod()
    return _API


def _version() -> str:
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except (OSError, ValueError):
        return '0.0.0'


# ── static tools ────────────────────────────────────────────────────────────
#
# 'write' marks a tool that changes local state, spends disk, or reaches out to
# a Repl. Those are refused for a proxied caller (see handle(local=…)).

def _obj(props=None, required=None, extra=False):
    s = {'type': 'object', 'properties': props or {}}
    if required:
        s['required'] = required
    if extra:
        s['additionalProperties'] = True
    return s


_STR = {'type': 'string'}


TOOLS = {
    'replit_catalog': {
        'description': 'Every mod deployed on Replit that this bridge is linked to, with the '
                       'functions each exposes, health and last-seen time. The index the other '
                       'tools address. refresh=true re-discovers over the network first (slower, '
                       'wakes sleeping Repls).',
        'inputSchema': _obj({'refresh': {'type': 'boolean',
                                         'description': 're-null-call every Repl first (default false)'}}),
        'handler': lambda a: api().catalog(refresh=bool(a.get('refresh'))),
    },
    'replit_repl': {
        'description': "One linked Repl in full: URL, health, the mod it serves and every "
                       "function with its parameters. Use before calling something unfamiliar.",
        'inputSchema': _obj({'name': dict(_STR, description='the remote name, or a Repl URL'),
                             'refresh': {'type': 'boolean',
                                         'description': 're-discover over the network (default false)'}},
                            ['name']),
        'handler': lambda a: api().repl(a['name'], refresh=bool(a.get('refresh'))),
    },
    'replit_ping': {
        'description': 'Health-check a linked Repl and report the round trip. A slow first hit '
                       'is a sleeping Repl waking up, not an error.',
        'inputSchema': _obj({'name': _STR}, ['name']),
        'handler': lambda a: api().ping(a['name'], timeout=int(a.get('timeout') or 20)),
    },
    'replit_discover': {
        'description': 'Null-call a linked Repl and cache what it exposes, so its functions '
                       'appear as repl_{remote}_{fn} tools. Run after linking.',
        'inputSchema': _obj({'name': _STR}, ['name']),
        'write': True,
        'handler': lambda a: api().discover(a['name'], timeout=int(a.get('timeout') or 25)),
    },
    'replit_call': {
        'description': 'Call any function on a linked Repl: POST {url}/{fn} with args as the '
                       'JSON body. Omit fn for the null call, which returns the mod\'s info. '
                       'The escape hatch — a function the catalog already knows has its own tool.',
        'inputSchema': _obj({
            'name': dict(_STR, description='remote name, or a full Repl URL'),
            'fn': dict(_STR, description='function name; omit for the null call (info)'),
            'args': dict(_obj(extra=True), description='keyword arguments for the function'),
            'timeout': {'type': 'integer', 'description': 'seconds (default 60)'},
        }, ['name']),
        'write': True,
        'handler': lambda a: api().call(a['name'], a.get('fn'),
                                        args=a.get('args') or {},
                                        timeout=int(a.get('timeout') or 60)),
    },
    'replit_link': {
        'description': 'Register a deployed Repl as a callable remote and discover its '
                       'functions in one step. The URL is the deployment (…replit.app) or dev '
                       '(…replit.dev) address, not the replit.com editor page.',
        'inputSchema': _obj({'name': dict(_STR, description='[A-Za-z0-9._-]+'),
                             'url': _STR, 'note': _STR}, ['name', 'url']),
        'write': True,
        'handler': lambda a: api().link(a['name'], a['url'], note=a.get('note')),
    },
    'replit_unlink': {
        'description': 'Forget a linked Repl.',
        'inputSchema': _obj({'name': _STR}, ['name']),
        'write': True,
        'handler': lambda a: api().unlink(a['name']),
    },
    'replit_status': {
        'description': 'Bridge status: how many bundles and remotes exist, whether Replit DB is '
                       'connected, and which Replit endpoints are actually reachable vs blocked.',
        'inputSchema': _obj(),
        'handler': lambda a: api().status(),
    },
    'replit_modules': {
        'description': 'Fleet modules on this box that could be bundled for Replit.',
        'inputSchema': _obj({'search': dict(_STR, description='substring filter')}),
        'handler': lambda a: api().modules(search=a.get('search')),
    },
    'replit_bundle': {
        'description': 'Package a fleet module as a Replit-runnable project: its own files plus '
                       '.replit, replit.nix, requirements.txt and a stdlib main.py that serves '
                       'it the way the protocol does. Read the returned warnings — a module that '
                       'imports the fleet SDK will not boot on Replit untouched.',
        'inputSchema': _obj({'module': dict(_STR, description="'git' or 'orbit/git'"),
                             'name': dict(_STR, description='bundle name (default: the module name)'),
                             'tests': {'type': 'boolean', 'description': 'include tests/ (default false)'}},
                            ['module']),
        'write': True,
        'handler': lambda a: api().bundle(a['module'], name=a.get('name'),
                                          tests=bool(a.get('tests'))),
    },
    'replit_bundles': {
        'description': 'Bundles built so far, with their manifests.',
        'inputSchema': _obj(),
        'handler': lambda a: api().bundles(),
    },
    'replit_bundle_files': {
        'description': 'The file tree of one bundle.',
        'inputSchema': _obj({'name': _STR}, ['name']),
        'handler': lambda a: api().bundle_files(a['name']),
    },
    'replit_bundle_file': {
        'description': 'Read one file out of a bundle — e.g. the generated main.py or .replit.',
        'inputSchema': _obj({'name': _STR, 'file': _STR}, ['name', 'file']),
        'handler': lambda a: api().bundle_file(a['name'], a['file']),
    },
    'replit_zip': {
        'description': 'Zip a bundle for upload into a Repl. Returns the path and size on this box.',
        'inputSchema': _obj({'name': _STR,
                             'module': dict(_STR, description='build this module first (optional)')},
                            ['name']),
        'write': True,
        'handler': lambda a: api().zip(a['name'], module=a.get('module')),
    },
    'replit_run_url': {
        'description': "The one-click https://replit.com/github/{owner}/{repo} import URL. This "
                       'is the only import path Replit honours for anonymous callers.',
        'inputSchema': _obj({'repo': dict(_STR, description="'owner/name' or a github.com URL")},
                            ['repo']),
        'handler': lambda a: api().run_url(a['repo']),
    },
    'replit_import': {
        'description': 'Clone the GitHub repo behind a Repl into the fleet as a module, '
                       'scaffolding config.json and an anchor if it has none. Replit blocks '
                       'anonymous Repl export (403), so import goes through the repo.',
        'inputSchema': _obj({'repo': _STR, 'name': _STR,
                             'orbit': dict(_STR, description="core|orbit|mods|local (default orbit)")},
                            ['repo']),
        'write': True,
        'handler': lambda a: api().import_repl(a['repo'], name=a.get('name'),
                                               orbit=a.get('orbit') or 'orbit'),
    },
    'replit_db_keys': {
        'description': "Keys in the attached Replit key-value store (the one documented Replit "
                       'API that is actually reachable). Needs a REPLIT_DB_URL — see '
                       'replit_status.account.',
        'inputSchema': _obj({'prefix': _STR}),
        'handler': lambda a: api().db_keys(prefix=a.get('prefix') or ''),
    },
    'replit_db_get': {
        'description': 'Read one key from the attached Replit DB.',
        'inputSchema': _obj({'key': _STR}, ['key']),
        'handler': lambda a: api().db_get(a['key']),
    },
    'replit_db_set': {
        'description': 'Write one key to the attached Replit DB.',
        'inputSchema': _obj({'key': _STR, 'value': {'description': 'string, or any JSON value'}},
                            ['key']),
        'write': True,
        'handler': lambda a: api().db_set(a['key'], a.get('value')),
    },
    'replit_db_del': {
        'description': 'Delete one key from the attached Replit DB.',
        'inputSchema': _obj({'key': _STR}, ['key']),
        'write': True,
        'handler': lambda a: api().db_del(a['key']),
    },
}


# ── dynamic tools: every fn of every linked Repl ────────────────────────────

DYN_PREFIX = 'repl_'
_JSON_TYPE = {bool: 'boolean', int: 'integer', float: 'number', str: 'string',
              list: 'array', dict: 'object'}


def _safe(part: str) -> str:
    """MCP tool names are [A-Za-z0-9_-]; remote names may carry dots."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', part or '')


def _fn_schema(params) -> dict:
    """A Repl reports each fn's parameters on its null call (name, required,
    default). Turn that into an input schema; a Repl too old to report them
    gets a free-form object so the fn is still callable."""
    if not isinstance(params, list):
        return _obj(extra=True)
    props, required = {}, []
    for p in params:
        if not isinstance(p, dict) or not p.get('name'):
            continue
        s = {}
        d = p.get('default')
        t = _JSON_TYPE.get(type(d)) if d is not None else None
        if t:
            s['type'] = t
            s['description'] = f'default: {json.dumps(d)}'
        if p.get('doc'):
            s['description'] = p['doc']
        props[p['name']] = s or {}
        if p.get('required'):
            required.append(p['name'])
    return _obj(props, required, extra=True)


def dynamic_tools() -> dict:
    """Rebuilt per tools/list — from the cache, never the network."""
    out = {}
    try:
        cat = api().catalog()
    except Exception:                                # noqa: BLE001 — no catalog, no tools
        return out
    for repl in cat.get('mods', []):
        remote = repl.get('name')
        params = repl.get('params') or {}
        docs = repl.get('docs') or {}
        for fn in repl.get('fns') or []:
            name = f'{DYN_PREFIX}{_safe(remote)}_{_safe(fn)}'[:64]
            if name in TOOLS or name in out:
                continue
            where = repl.get('mod') or remote
            doc = (docs.get(fn) or '').strip()
            out[name] = {
                'description': (f'{doc} — ' if doc else '') +
                               f'{fn}() on the Repl-hosted mod "{where}" ({repl.get("url")}), '
                               f'called through the bridge.',
                'inputSchema': _fn_schema(params.get(fn)),
                'write': True,          # it leaves this box
                # args as a dict, never **a: the remote is entitled to a
                # parameter called `name` or `timeout`.
                'handler': (lambda r, f: lambda a: api().call(r, f, args=a))(remote, fn),
            }
    return out


def all_tools() -> dict:
    t = dict(TOOLS)
    t.update(dynamic_tools())
    return t


# ── JSON-RPC 2.0 ────────────────────────────────────────────────────────────

def _result(id_, result: dict) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code: int, message: str) -> dict:
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _tool_error(id_, text: str) -> dict:
    # A failing tool is a *successful* JSON-RPC response carrying isError, per
    # the spec — the model reads the message and adjusts.
    return _result(id_, {'content': [{'type': 'text', 'text': text}], 'isError': True})


def _call_tool(id_, params: dict, local: bool) -> dict:
    name = str(params.get('name') or '')
    tool = all_tools().get(name)
    if not tool:
        return _error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    if tool.get('write') and not local:
        return _tool_error(id_, f'{name} is refused here: this endpoint is proxied, and every '
                                'tool that writes local state or reaches a Repl is loopback-only. '
                                'Read-only tools (replit_catalog, replit_status, replit_repl) work.')
    try:
        result = tool['handler'](args)
    except KeyError as e:
        return _tool_error(id_, f'{name} needs argument {e}')
    except Exception as e:                           # noqa: BLE001 — reported to the model
        return _tool_error(id_, f'{name} failed: {type(e).__name__}: {e}')
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body, local: bool = True):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object with a method')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        client = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': client if client in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {'listChanged': True}},
            'serverInfo': {'name': 'replit', 'version': _version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': [
            {'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in all_tools().items()]})
    if method == 'tools/call':
        return _call_tool(id_, params, local)
    if method == 'resources/list':
        return _result(id_, {'resources': []})
    if method == 'prompts/list':
        return _result(id_, {'prompts': []})
    return _error(id_, -32601, f'method not found: {method}')


# ── transports ──────────────────────────────────────────────────────────────

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except ValueError:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body, local=True)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


def serve_http(port: int):
    """Streamable HTTP without SSE: one JSON-RPC message per POST /mcp."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    paths = ('/mcp', '/replit/mcp', '/api/replit/mcp')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):
            pass

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def do_GET(self):
            if self.path.rstrip('/').endswith('/health'):
                return self._send(200, {'ok': True, 'mcp': 'replit',
                                        'tools': len(all_tools())})
            self._send(405, b'POST JSON-RPC 2.0 messages to /mcp', 'text/plain')

        def do_POST(self):
            if self.path.split('?')[0].rstrip('/') not in paths:
                return self._send(404, b'not found', 'text/plain')
            n = int(self.headers.get('content-length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'')
            except ValueError:
                return self._send(400, _error(None, -32700, 'parse error: body is not valid JSON'))
            local = (not self.headers.get('X-Forwarded-For')
                     and self.client_address[0] in ('127.0.0.1', '::1', 'localhost'))
            resp = handle(body, local=local)
            if resp is None:
                return self._send(202, b'', 'text/plain')
            self._send(200, resp)

    print(f'replit mcp on :{port} — POST /mcp ({len(all_tools())} tools)', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--tools' in argv:
        print(json.dumps({'n': len(all_tools()),
                          'tools': [{'name': n, 'description': t['description']}
                                    for n, t in all_tools().items()]}, indent=2))
    elif '--http' in argv:
        i = argv.index('--port') + 1 if '--port' in argv else -1
        serve_http(int(argv[i] if i > 0 else os.environ.get('MCP_PORT', 50531)))
    else:
        serve_stdio()
