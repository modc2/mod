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
import chain  # noqa: E402

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
    'rpc': 'POST /rpc {method, params}', 'app': f'GET {BASE}/',
}


def _info():
    return {'name': NAME, 'description': CONFIG.get('description', NAME),
            'port': PORT, 'networks': list(chain.NETWORKS),
            'default_network': chain.DEFAULT_NETWORK,
            'tokens': {k: v['address'] for k, v in chain.TOKENS.items()},
            'endpoints': ENDPOINTS}


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
    if path == '/test':
        return 200, chain.selftest()
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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
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
    print(f'{NAME}: api http://localhost:{port}/ · app http://localhost:{port}{BASE}/')
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    port = PORT
    if '--port' in sys.argv:
        port = int(sys.argv[sys.argv.index('--port') + 1])
    serve(port)
