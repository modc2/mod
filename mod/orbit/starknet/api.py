"""
starknet/api.py — REST API + browser console on one port, stdlib only.

GET  /                 info + endpoint map
GET  /health           liveness
GET  /status           network, chain id, head block, active RPC
GET  /block_number
GET  /block?id=latest|<n>|<hash>&full=1
GET  /tx?hash=         /receipt?hash=
GET  /account?address= /balance?address=&token=eth|strk|usdc|0x..
GET  /nonce?address=   /class_hash?address=  /storage?address=&key=
GET  /selector?name=   starknet_keccak of an entry-point name (offline)
GET|POST /call         {contract, entrypoint|selector, calldata[]}
POST /rpc              {method, params} — raw JSON-RPC escape hatch
GET  /contract?address=            ABI-derived interface of any contract
GET|POST /read         {contract, function, args} — typed call, decoded
GET|POST /encode       {contract, function, args} — calldata only
GET  /events?address=&name=&limit= decoded events, newest first
GET  /strk20/pool | /activity?event= | /user?address= | /note?id=
     /nullifier?value= | /helpers | /helper?address= | /docs?page=&q=
POST /strk20/invoke_action {helper, args} — InvokeExternal client action
POST /mcp              MCP (JSON-RPC 2.0, Streamable HTTP) — tools/list etc.
GET  /tools            the MCP tool list;  POST /tools/<name> runs one
GET  /starknet/        the app (console.html)

Every chain read takes ?network=mainnet|sepolia. Errors come back as
HTTP 400/404 with a JSON body — never 5xx (proxies strip those bodies).
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi  # noqa: E402
import chain  # noqa: E402
import mcp  # noqa: E402
import strk20  # noqa: E402

NAME = 'starknet'
BASE = f'/{NAME}'
CONFIG = json.load(open(os.path.join(HERE, 'config.json'))) if \
    os.path.exists(os.path.join(HERE, 'config.json')) else {}
PORT = int(os.environ.get('PORT', CONFIG.get('port', 51020)))

ENDPOINTS = {
    'info': 'GET /', 'health': 'GET /health', 'status': 'GET /status?network=',
    'block_number': 'GET /block_number', 'block': 'GET /block?id=&full=',
    'tx': 'GET /tx?hash=', 'receipt': 'GET /receipt?hash=',
    'account': 'GET /account?address=', 'balance': 'GET /balance?address=&token=',
    'nonce': 'GET /nonce?address=', 'class_hash': 'GET /class_hash?address=',
    'storage': 'GET /storage?address=&key=', 'selector': 'GET /selector?name=',
    'call': 'GET|POST /call {contract, entrypoint|selector, calldata}',
    'rpc': 'POST /rpc {method, params}',
    'contract': 'GET /contract?address=',
    'read': 'GET|POST /read {contract, function, args}',
    'encode': 'GET|POST /encode {contract, function, args}',
    'events': 'GET /events?address=&name=&limit=',
    'strk20': 'GET /strk20/{pool,activity,user,note,nullifier,helpers,helper,docs}',
    'strk20_invoke_action': 'POST /strk20/invoke_action {helper, args}',
    'mcp': 'POST /mcp (JSON-RPC 2.0)', 'tools': 'GET /tools · POST /tools/<name>',
    'app': f'GET {BASE}/',
}


def _info():
    return {'name': NAME, 'description': CONFIG.get('description', NAME),
            'port': PORT, 'networks': list(chain.NETWORKS),
            'default_network': chain.DEFAULT_NETWORK,
            'tokens': {k: v['address'] for k, v in chain.TOKENS.items()},
            'strk20_pool': strk20.POOLS.get(chain.DEFAULT_NETWORK),
            'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                    'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
            'endpoints': ENDPOINTS}


def _strk20(sub, p, net):
    if sub == 'pool':
        return strk20.state(network=net)
    if sub == 'activity':
        return strk20.activity(p.get('event'), limit=min(int(p.get('limit') or 20), 200),
                               network=net)
    if sub == 'user':
        return strk20.user(p['address'], limit=int(p.get('limit') or 10), network=net)
    if sub == 'note':
        return strk20.note(p.get('id') or p['note_id'], network=net)
    if sub == 'nullifier':
        return strk20.nullifier(p.get('value') or p['nullifier'], network=net)
    if sub == 'helpers':
        return strk20.helpers(limit=int(p.get('limit') or 200), network=net)
    if sub == 'helper':
        return strk20.helper(p['address'], network=net)
    if sub == 'invoke_action':
        return strk20.invoke_action(p['helper'], p.get('args'), network=net)
    if sub == 'docs':
        return strk20.docs(p.get('page'), p.get('q'))
    raise KeyError(f'strk20/{sub}')


def route(path, q, body):
    """Dispatch one request. Returns (status, payload)."""
    net = q.get('network')
    if path in ('', '/'):
        return 200, _info()
    if path == '/health':
        return 200, {'ok': True}
    if path == '/status':
        return 200, chain.status(network=net)
    if path == '/block_number':
        return 200, {'block_number': chain.block_number(network=net)}
    if path == '/block':
        return 200, chain.block(q.get('id', 'latest'),
                                full=q.get('full') in ('1', 'true'), network=net)
    if path == '/tx':
        return 200, chain.tx(q['hash'], network=net)
    if path == '/receipt':
        return 200, chain.receipt(q['hash'], network=net)
    if path == '/account':
        return 200, chain.account(q['address'], network=net)
    if path == '/balance':
        return 200, chain.balance(q['address'], q.get('token', 'eth'), network=net)
    if path == '/nonce':
        return 200, {'nonce': chain.nonce(q['address'], network=net)}
    if path == '/class_hash':
        return 200, {'class_hash': chain.class_hash(q['address'], network=net)}
    if path == '/storage':
        return 200, {'value': chain.storage(q['address'], q['key'], network=net)}
    if path == '/selector':
        return 200, {'name': q['name'], 'selector': chain.selector(q['name'])}
    if path == '/call':
        p = {**q, **(body or {})}
        calldata = p.get('calldata') or []
        if isinstance(calldata, str):
            calldata = [c for c in calldata.replace(',', ' ').split() if c]
        result = chain.call(p['contract'], p.get('entrypoint'),
                            calldata, entry_selector=p.get('selector'),
                            block_id=p.get('block_id', 'latest'),
                            network=p.get('network') or net)
        return 200, {'result': result}
    if path == '/rpc':
        p = body or {}
        return 200, {'result': chain.rpc(p['method'], p.get('params', []),
                                         network=p.get('network') or net)}
    if path in ('/contract', '/read', '/encode', '/events') or \
            path.startswith('/strk20/'):
        p = {**q, **(body or {})}
        net = p.get('network') or net
        if path == '/contract':
            return 200, abi.iface(p['address'], network=net)
        if path in ('/read', '/encode'):
            fn = abi.read if path == '/read' else abi.encode
            return 200, fn(p['contract'], p['function'], p.get('args'), network=net)
        if path == '/events':
            return 200, abi.events(p['address'], p.get('name'),
                                   limit=min(int(p.get('limit') or 20), 200),
                                   network=net)
        return 200, _strk20(path[len('/strk20/'):], p, net)
    if path == '/tools':
        return 200, {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                     'instructions': mcp.INSTRUCTIONS}
    if path.startswith('/tools/'):
        return 200, mcp.call_tool(path[len('/tools/'):], {**q, **(body or {})})
    if path == '/test':
        return 200, {**chain.selftest(), **abi.selftest()}
    return 404, {'error': f'no route {path}', 'endpoints': ENDPOINTS}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, payload, ctype='application/json'):
        data = payload if isinstance(payload, bytes) else \
            json.dumps(payload, indent=2).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Mcp-Session-Id, Mcp-Protocol-Version')
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, body=None):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        # The gateway routes /starknet/* here with the prefix kept.
        if path == BASE:  # bare /starknet: redirect so relative URLs resolve
            self.send_response(302)
            self.send_header('Location', BASE + '/')
            self.end_headers()
            return
        if path.startswith(BASE + '/'):
            path = path[len(BASE):]
            if path == '/':
                path = '/app'
        if path == '/mcp':
            if self.command != 'POST':
                return self._send(200, {'mcp': 'POST JSON-RPC 2.0 here',
                                        'tools': len(mcp.TOOLS)})
            if body is None or body == {}:
                return self._send(400, mcp._error(None, -32700, 'empty body'))
            resp = mcp.handle(body)
            if resp is None:
                return self._send(202, b'', 'text/plain')
            return self._send(200, resp)
        if path in ('/app', '/console'):
            with open(os.path.join(HERE, 'console.html'), 'rb') as f:
                return self._send(200, f.read(), 'text/html; charset=utf-8')
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            code, payload = route(path, q, body)
        except KeyError as e:
            code, payload = 400, {'error': f'missing parameter {e}'}
        except chain.RpcError as e:
            code, payload = 400, {'error': str(e), 'code': e.code,
                                  'data': e.data, 'rpc': e.url}
        except (abi.AbiError, mcp.ToolError) as e:
            code, payload = 400, {'error': str(e)}
        except (ConnectionError, Exception) as e:
            code, payload = 400, {'error': f'{type(e).__name__}: {e}'}
        self._send(code, payload)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        try:
            body = json.loads(raw.decode()) if raw else {}
        except ValueError:
            return self._send(400, {'error': 'body must be JSON'})
        self._handle(body)

    def do_OPTIONS(self):
        self._send(200, {'ok': True})


def serve(port=PORT):
    print(f'{NAME}: api http://localhost:{port}/ · app http://localhost:{port}{BASE}/ · '
          f'mcp POST /mcp ({len(mcp.TOOLS)} tools)', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    port = PORT
    if '--port' in sys.argv:
        port = int(sys.argv[sys.argv.index('--port') + 1])
    serve(port)
