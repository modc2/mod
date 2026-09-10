#!/usr/bin/env python3
"""dag api — REST, MCP and the console on one port, zero dependencies.

Every route is the same `call_tool` an MCP client reaches, so the browser, the
shell and an agent cannot be told different things about the same run.

WHY THIS BINDS LOOPBACK
    A graph step can call any tool in the fleet, and the fleet's tools include
    ones that sign transactions and move money. The MCP hub gates those on the
    caller — and a call arriving from this box is trusted. So a dag server
    reachable from the internet is a way to spend the hub's trust from outside
    it. Running a graph is therefore gated: a bearer token matching
    ~/.mod/dag/server.secret, or a caller on this box that is not being
    proxied. Reading — the tool catalogue, saved graphs, run history — is open,
    and `dry_run` is a read.

    python3 -m dagsrc.api [--port 50810]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dagsrc import mcp, refs, store, targets
    from dagsrc.graph import SpecError
else:
    from . import mcp, refs, store, targets
    from .graph import SpecError

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get('DAG_BASE_PATH', '/dag')
PORT = int(os.environ.get('DAG_PORT', os.environ.get('PORT', 50810)))
BIND = os.environ.get('DAG_BIND', '127.0.0.1')
SECRET_FILE = os.path.join(store.DIR, 'server.secret')
# Executing a graph spends the fleet's trust. Everything else is a read.
WRITE_TOOLS = {'dag_run', 'dag_save', 'dag_delete'}


class Denied(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


def secret():
    try:
        with open(SECRET_FILE) as f:
            return f.read().strip() or None
    except Exception:
        return None


def gate(client_ip, headers, tool=None, args=None):
    """Who may spend calls. A dry run may not spend any, so it is not gated."""
    if tool == 'dag_run' and (args or {}).get('dry_run'):
        return
    want = secret()
    h = headers or {}
    if want:
        auth = str(h.get('authorization') or '').strip()
        token = auth[7:].strip() if auth.lower().startswith('bearer ') else \
            str(h.get('x-dag-token') or '')
        if token != want:
            raise Denied('running or saving a graph needs Authorization: Bearer '
                         f'<the contents of {SECRET_FILE}>')
        return
    proxied = any(h.get(k) for k in ('x-forwarded-for', 'x-real-ip',
                                     'x-forwarded-host'))
    if proxied or client_ip not in ('127.0.0.1', '::1', 'localhost'):
        raise Denied('a graph may call any tool in the fleet, so running one is '
                     'restricted to this box until a secret is set — write one to '
                     f'{SECRET_FILE} and send it as a bearer token', status=403)


def info():
    d = mcp.info()
    d['endpoints'] = {
        'GET /': 'this — the spec of a graph and a worked example',
        'GET /health': 'liveness, tool count, whether the hub is answering',
        'POST /run': '{graph, inputs?, dry_run?, verbose?} — run a graph (gated)',
        'POST /plan': '{graph, inputs?} — check it and price it, calling nothing',
        'GET /tools': 'q=, server=, limit= — every tool in the fleet',
        'GET /servers': 'the MCP servers, and which are answering',
        'GET|POST /graphs': 'saved graphs; POST {name, graph} saves one (gated)',
        'GET|DELETE /graphs/<name>': 'one saved graph, with its plan',
        'GET /runs': 'graph=, status=, limit= — run history',
        'GET /runs/<id>': 'one run in full, including one still going',
        'POST /mcp': 'MCP JSON-RPC 2.0 — the same nine tools',
        f'GET {BASE}': 'browser console',
    }
    d['auth'] = ('bearer token required to run or save (%s)' % SECRET_FILE
                 if secret() else 'run and save are loopback-only — no secret set')
    d['bind'] = BIND
    return d


def route(method, path, query, body, client_ip=None, headers=None):
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}
    args = {**q, **b}

    if path in ('', '/'):
        return info()
    if path == '/health':
        try:
            n = len(targets.tool_index(timeout=8))
            hub = {'ok': True, 'tools': n}
        except targets.StepError as e:
            hub = {'ok': False, 'error': str(e)}
        return {'ok': True, 'tools': len(mcp.TOOLS), 'hub': hub,
                'graphs': len(store.graphs()), 'state': store.DIR}

    simple = {'/run': 'dag_run', '/plan': 'dag_plan', '/tools': 'dag_tools',
              '/servers': 'dag_servers'}
    if path in simple:
        tool = simple[path]
        if tool in WRITE_TOOLS:
            gate(client_ip, headers, tool, args)
        return mcp.call_tool(tool, _coerce(args))

    if path == '/graphs':
        if method == 'POST':
            gate(client_ip, headers, 'dag_save', args)
            return mcp.call_tool('dag_save', args)
        return mcp.call_tool('dag_graphs', {})
    if path.startswith('/graphs/'):
        name = urllib.parse.unquote(path[len('/graphs/'):])
        if method == 'DELETE':
            gate(client_ip, headers, 'dag_delete', args)
            return mcp.call_tool('dag_delete', {'name': name})
        if method == 'POST':
            gate(client_ip, headers, 'dag_save', args)
            return mcp.call_tool('dag_save', {'name': name, **args})
        return mcp.call_tool('dag_graphs', {'name': name})

    if path == '/runs':
        return mcp.call_tool('dag_runs', _coerce(args))
    if path.startswith('/runs/'):
        return mcp.call_tool('dag_runs', {'run': path[len('/runs/'):],
                                          'verbose': _flag(args, 'verbose', True)})
    if path == '/tools/index':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise SpecError(f'no route {method} {path} — GET / lists them')


def _coerce(args):
    """A query string is all strings; the tools want numbers and booleans."""
    out = dict(args)
    for k in ('limit', 'max_parallel', 'timeout'):
        if isinstance(out.get(k), str) and out[k]:
            try:
                out[k] = float(out[k])
            except ValueError:
                raise SpecError(f'{k} must be a number, got {out[k]!r}')
    for k in ('verbose', 'full', 'dry_run', 'check_tools'):
        if isinstance(out.get(k), str):
            out[k] = out[k].lower() not in ('0', 'false', 'no', '')
    if isinstance(out.get('graph'), str) and out['graph'].lstrip().startswith('{'):
        out['graph'] = json.loads(out['graph'])
    return out


def _flag(args, name, default=False):
    v = args.get(name)
    return default if v is None else str(v).lower() not in ('0', 'false', 'no', '')


def serve(port=PORT, base=BASE, bind=BIND):
    console = os.path.join(HERE, 'console.html')
    api_prefixes = (base.rstrip('/') + '/_api', '/api/dag', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'dag/' + mcp.version()

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods',
                             'GET,POST,DELETE,OPTIONS')
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

        def _ip(self):
            return self.client_address[0] if self.client_address else None

        def _gate_mcp(self, body):
            """The same gate on tools/call. Gating the REST routes alone would
            be no gate at all — /mcp reaches every tool by name."""
            if not isinstance(body, dict) or body.get('method') != 'tools/call':
                return None
            params = body.get('params') or {}
            if params.get('name') not in WRITE_TOOLS:
                return None
            try:
                gate(self._ip(), self.headers, params.get('name'),
                     params.get('arguments') or {})
                return None
            except Denied as e:
                return {'jsonrpc': '2.0', 'id': body.get('id'),
                        'result': {'isError': True,
                                   'content': [{'type': 'text',
                                                'text': json.dumps({'error': str(e)})}]}}

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                body = self._read()
                denied = self._gate_mcp(body)
                if denied:
                    return self._send(200, denied)
                depth = int(self.headers.get('x-dag-depth') or 0) + 1
                resp = mcp.handle(body, depth=depth)
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        return self._send(200, f.read(), 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, info())
            try:
                return self._send(200, route(self.command, p, query, self._read(),
                                             self._ip(), self.headers))
            except Denied as e:
                return self._send(e.status, {'error': str(e)})
            except (SpecError, store.StoreError, refs.RefError) as e:
                return self._send(400, {'error': str(e)})
            except targets.StepError as e:
                return self._send(502, e.dict())
            except TypeError as e:
                return self._send(400, {'error': f'bad arguments — {e}'})
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'dag on {bind}:{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools, hub {targets.HUB}', flush=True)
    ThreadingHTTPServer((bind, port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    j = argv.index('--bind') + 1 if '--bind' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT, bind=argv[j] if j > 0 else BIND)
