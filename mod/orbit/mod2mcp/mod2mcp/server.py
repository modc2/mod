"""
mod2mcp.server — the gateway: one HTTP server hosting every deployed contract.

    GET  /                     info + deployments
    GET  /health               liveness
    GET  /mods                 mods that can be compiled
    GET  /abi/<mod>            compile a mod's ABI (no deploy needed, read is free)
    GET  /deployments          the registry
    POST /deploy               {"mod": .., "fns"?: [..], "exclude"?: [..], "readonly"?: bool}
    POST /undeploy             {"mod": ..}
    POST /call/<mod>/<fn>      body = args dict (REST escape hatch)
    POST /mcp                  control-plane MCP (deploy/undeploy/abi/call)
    POST /mcp/<mod>            the deployed mod's own MCP endpoint

Run: python3 -m mod2mcp.server --port 51550
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import compiler, rpc, runtime

MODULE_DIR = Path(__file__).resolve().parents[1]


def _version() -> str:
    try:
        return json.loads((MODULE_DIR / 'config.json').read_text()).get('version', '0')
    except Exception:
        return '0'


CONTROL_INSTRUCTIONS = (
    'mod2mcp turns any mod into an MCP server the way you would program a smart '
    'contract: mod2mcp_abi compiles a mod\'s public interface into a deterministic '
    'ABI (typed functions, view/write mutability, a content-hash address); '
    'mod2mcp_deploy registers it so POST /mcp/<mod> serves it as its own MCP '
    'endpoint; mod2mcp_call executes one function on a deployed contract '
    '(write calls on a readonly deployment revert). Reads are free — '
    'mod2mcp_mods and mod2mcp_abi work without deploying anything.')

CONTROL_TOOLS = [
    {'name': 'mod2mcp_mods', 'fn': 'mods', 'description': '[view] List every mod that can be compiled.',
     'inputSchema': {'type': 'object', 'properties': {}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'mod2mcp_abi', 'fn': 'abi', 'description': '[view] Compile a mod into its ABI + MCP tool list.',
     'inputSchema': {'type': 'object', 'required': ['mod'], 'properties': {
         'mod': {'type': 'string', 'description': 'Mod name, e.g. "news"'}}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'mod2mcp_deploy', 'fn': 'deploy', 'description':
        '[write] Deploy a mod as a live MCP contract at /mcp/<mod>.',
     'inputSchema': {'type': 'object', 'required': ['mod'], 'properties': {
         'mod': {'type': 'string'},
         'fns': {'type': 'array', 'items': {'type': 'string'},
                 'description': 'Explicit interface; omit to use the mod\'s config fns'},
         'exclude': {'type': 'array', 'items': {'type': 'string'}},
         'readonly': {'type': 'boolean', 'default': False,
                      'description': 'Expose only view functions'}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'mod2mcp_undeploy', 'fn': 'undeploy', 'description': '[write] Remove a deployment.',
     'inputSchema': {'type': 'object', 'required': ['mod'], 'properties': {'mod': {'type': 'string'}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': False}},
    {'name': 'mod2mcp_deployments', 'fn': 'deployments', 'description': '[view] The deployment registry.',
     'inputSchema': {'type': 'object', 'properties': {}},
     'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'mod2mcp_call', 'fn': 'call', 'description':
        '[write] Execute one function on a deployed contract.',
     'inputSchema': {'type': 'object', 'required': ['mod', 'fn'], 'properties': {
         'mod': {'type': 'string'}, 'fn': {'type': 'string'},
         'args': {'type': 'object', 'description': 'Keyword arguments'}}},
     'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': True}},
]


def control_call(name: str, args: dict) -> Any:
    if name == 'mod2mcp_mods':
        return {'mods': runtime.mods()}
    if name == 'mod2mcp_abi':
        return runtime.contract(args['mod'])
    if name == 'mod2mcp_deploy':
        return runtime.deploy(args['mod'], fns=args.get('fns'),
                              exclude=args.get('exclude'), readonly=bool(args.get('readonly')))
    if name == 'mod2mcp_undeploy':
        return runtime.undeploy(args['mod'])
    if name == 'mod2mcp_deployments':
        return {'deployments': runtime.deployments()}
    if name == 'mod2mcp_call':
        d = runtime.deployments().get(args['mod'])
        if d is None:
            raise LookupError(f"{args['mod']} is not deployed — mod2mcp_deploy it first")
        return runtime.call(args['mod'], args['fn'], args.get('args') or {},
                            readonly=bool(d.get('readonly')))
    raise LookupError(f'unknown tool {name!r}')


CONTROL = rpc.McpServer('mod2mcp', _version(), CONTROL_INSTRUCTIONS,
                        lambda: CONTROL_TOOLS, control_call)


def mod_server(mod_name: str) -> rpc.McpServer:
    """The MCP server of one deployed contract."""
    manifest = runtime.deployed_manifest(mod_name)
    if manifest is None:
        raise LookupError(f'{mod_name} is not deployed')
    fn_by_tool = {t['name']: t['fn'] for t in manifest['tools']}

    def call(tool: str, args: dict) -> Any:
        fn = fn_by_tool.get(tool)
        if fn is None:
            raise LookupError(f'unknown tool {tool!r}')
        return runtime.call(mod_name, fn, args, readonly=manifest['readonly'])

    instructions = (f"{mod_name} as an MCP contract at {manifest['address']}: "
                    f"{len(manifest['tools'])} functions, compiled by mod2mcp. "
                    '[view] tools read, [write] tools mutate.')
    return rpc.McpServer(mod_name, _version(), instructions,
                         lambda: manifest['tools'], call)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Any:
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        return json.loads(raw) if raw else {}

    def do_OPTIONS(self):
        self._send({})

    def do_GET(self):
        path = self.path.split('?')[0].rstrip('/') or '/'
        try:
            if path in ('/', '/mod2mcp', '/health'):
                return self._send({'ok': True, 'module': 'mod2mcp', 'version': _version(),
                                   'deployments': list(runtime.deployments()),
                                   'mcp': '/mcp (control) · /mcp/<mod> (deployed contracts)'})
            if path == '/mods':
                return self._send({'mods': runtime.mods()})
            if path == '/deployments':
                return self._send({'deployments': runtime.deployments()})
            if path.startswith('/abi/'):
                return self._send(runtime.contract(path[len('/abi/'):]))
        except Exception as e:
            return self._send({'error': f'{type(e).__name__}: {e}'}, 400)
        self._send({'error': f'no route {path}'}, 404)

    def do_POST(self):
        path = self.path.split('?')[0].rstrip('/')
        try:
            body = self._body()
            if path == '/mcp':
                return self._send(CONTROL.handle(body) or {})
            if path.startswith('/mcp/'):
                return self._send(mod_server(path[len('/mcp/'):]).handle(body) or {})
            if path == '/deploy':
                return self._send(runtime.deploy(body['mod'], fns=body.get('fns'),
                                                 exclude=body.get('exclude'),
                                                 readonly=bool(body.get('readonly'))))
            if path == '/undeploy':
                return self._send(runtime.undeploy(body['mod']))
            if path.startswith('/call/'):
                _, _, rest = path.partition('/call/')
                mod_name, _, fn = rest.partition('/')
                d = runtime.deployments().get(mod_name)
                if d is None:
                    return self._send({'error': f'{mod_name} is not deployed'}, 404)
                out = runtime.call(mod_name, fn, body or {}, readonly=bool(d.get('readonly')))
                return self._send({'result': out})
        except LookupError as e:
            return self._send({'error': str(e)}, 404)
        except PermissionError as e:
            return self._send({'error': str(e)}, 403)
        except Exception as e:
            return self._send({'error': f'{type(e).__name__}: {e}'}, 400)
        self._send({'error': f'no route {path}'}, 404)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=51550)
    ap.add_argument('--host', default='127.0.0.1')
    args = ap.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
