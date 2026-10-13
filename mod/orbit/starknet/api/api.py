"""
starknet/api/api.py — REST API + the app on one port, stdlib only.

GET  /                 info + endpoint map
GET  /health           liveness
GET  /status           network, chain id, head block, active RPC
GET  /block_number
GET  /blocks?limit=    newest blocks as one-line summaries
GET  /search?q=        what is this? block number / tx / block hash / address
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
GET  /studio/whoami    can this caller write? (owner token or loopback)
GET  /studio/<verb>    contract studio reads: status templates contracts get
                       tree read abi job jobs   (proxied to the cairo mod)
POST /studio/<verb>    owner-only writes: new write build test vibe clean
                       remove — build/test/vibe return a job to poll
GET  /stablecoins      every stablecoin on the network: supply, model,
                       Pragma oracle peg   (/stablecoins/registry: static)
GET  /agents           the agent roster — the fleet agent contract
POST /run  /run/stream ask the guide agent in plain English (JSON | SSE)
GET  /.well-known/agent.json   agent/1.0 card
POST /mcp              MCP (JSON-RPC 2.0, Streamable HTTP) — tools/list etc.
GET  /tools            the MCP tool list;  POST /tools/<name> runs one
GET  /starknet/        the app (app/dist, a static Next export; legacy.html
                       when it has not been built)

The app calls the API as /starknet/_api/<route> — the gateway routes
/starknet/api/* to the API with the prefix stripped, so /_api is the path
that reaches this server through the app route. A browser navigation
(Accept: text/html) to a path that is also an app page (/block, /tx) gets
the page; everything else gets JSON.

Every chain read takes ?network=mainnet|sepolia. Errors come back as
HTTP 400/404 with a JSON body — never 5xx (proxies strip those bodies).
"""

import json
import mimetypes
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import abi  # noqa: E402
import guide  # noqa: E402
import chain  # noqa: E402
import mcp  # noqa: E402
import stables  # noqa: E402
import strk20  # noqa: E402
import studio  # noqa: E402

ROOT = os.path.dirname(HERE)
NAME = 'starknet'
BASE = f'/{NAME}'
CONFIG = json.load(open(os.path.join(ROOT, 'config.json'))) if \
    os.path.exists(os.path.join(ROOT, 'config.json')) else {}
DIST = os.path.join(ROOT, 'app', 'dist')
LEGACY = os.path.join(ROOT, 'app', 'legacy.html')
PORT = int(os.environ.get('PORT', CONFIG.get('port', 51020)))

ENDPOINTS = {
    'info': 'GET /', 'health': 'GET /health', 'status': 'GET /status?network=',
    'block_number': 'GET /block_number', 'block': 'GET /block?id=&full=',
    'blocks': 'GET /blocks?limit=', 'search': 'GET /search?q=',
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
    'studio': 'GET /studio/{whoami,status,templates,contracts,get,tree,read,abi,job,jobs}',
    'studio_write': 'POST /studio/{new,write,build,test,vibe,clean,remove} (owner token)',
    'stablecoins': 'GET /stablecoins · /stablecoins/registry',
    'agents': 'GET /agents · /agents/<id> · /.well-known/agent.json',
    'run': 'POST /run · POST /run/stream (SSE) {query, brain=auto|facts|claude, history}',
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
                    'stdio': 'python3 api/mcp.py', 'tools': len(mcp.TOOLS)},
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
    if path == '/blocks':
        return 200, chain.blocks(int(q.get('limit') or 10), network=net)
    if path == '/search':
        return 200, chain.lookup(q['q'], network=net)
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
    if path == '/stablecoins':
        return 200, stables.stables(network=net)
    if path == '/stablecoins/registry':
        return 200, stables.registry(network=net)
    if path == '/agents':
        return 200, guide.agents()
    if path.startswith('/agents/'):
        return 200, guide.agent(path[len('/agents/'):])
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
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, Token, Mcp-Session-Id, Mcp-Protocol-Version')
        self.end_headers()
        self.wfile.write(data)

    def _page(self, path):
        """The app file for a path, or None. Pages are dist/<p>.html."""
        if not os.path.isdir(DIST):
            return LEGACY if path in ('/', '/app', '/console') else None
        p = path.strip('/') or 'index'
        if p in ('app', 'console'):
            p = 'index'
        root = os.path.realpath(DIST)
        for cand in (p, p + '.html', os.path.join(p, 'index.html')):
            f = os.path.realpath(os.path.join(root, cand))
            if f.startswith(root + os.sep) and os.path.isfile(f):
                return f
        return None

    def _static(self, f):
        ctype = mimetypes.guess_type(f)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        with open(f, 'rb') as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable'
                         if '/_next/static/' in f else 'no-cache')
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
        prefixed = path.startswith(BASE + '/')
        if prefixed:
            path = path[len(BASE):]
        if path == '/_api' or path.startswith('/_api/'):
            path = path[len('/_api'):] or '/'
        elif self.command == 'GET':
            html = 'text/html' in (self.headers.get('Accept') or '')
            if path.startswith('/_next/') or path in ('/app', '/console') or \
                    (path == '/' and (prefixed or html)) or \
                    (html and '.' not in path.rsplit('/', 1)[-1]) or \
                    path.rsplit('.', 1)[-1] in ('html', 'txt', 'ico', 'svg', 'png'):
                f = self._page(path)
                if f:
                    return self._static(f)
                if path.startswith('/_next/'):
                    return self._send(404, {'error': f'no file {path}'})
        if path == '/studio' or path.startswith('/studio/'):
            return self._studio(path[len('/studio/'):] or 'status', parsed, body)
        if path == '/.well-known/agent.json':
            host = self.headers.get('Host') or f'localhost:{PORT}'
            return self._send(200, guide.card(f'http://{host}{BASE}/_api'))
        if path in ('/run', '/run/stream'):
            return self._run(path, body)
        if path == '/mcp':
            if self.command != 'POST':
                return self._send(200, {'mcp': 'POST JSON-RPC 2.0 here',
                                        'tools': len(mcp.TOOLS)})
            if body is None or body == {}:
                return self._send(400, mcp._error(None, -32700, 'empty body'))
            resp = mcp.handle(body, writable=self._who()['writable'])
            if resp is None:
                return self._send(202, b'', 'text/plain')
            return self._send(200, resp)
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

    def _run(self, path, body):
        if self.command != 'POST':
            return self._send(200, {'run': f'POST {path} {{query}}', 'try': guide.EXAMPLES,
                                    **guide.agents()})
        body = body or {}
        kw = {'agent': body.get('agent_type') or body.get('agent'),
              'brain': body.get('brain') or 'auto', 'network': body.get('network'),
              'model': body.get('model'), 'history': body.get('history'),
              'writable': self._who()['writable']}
        query = body.get('query') or body.get('message') or ''
        if path == '/run':
            try:
                return self._send(200, guide.run(query, **kw))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {'error': str(e)})
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Accel-Buffering', 'no')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        try:
            for ev in guide.run_stream(query, **kw):
                line = ': ping\n\n' if ev['type'] == 'ping' else \
                    f'data: {json.dumps(ev, default=str)}\n\n'
                self.wfile.write(line.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def _who(self):
        hdrs = {k.lower(): v for k, v in self.headers.items()}
        return studio.whoami(self.client_address[0], hdrs)

    def _studio(self, verb, parsed, body):
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        who = self._who()
        if verb == 'whoami':
            return self._send(200, who)
        if verb in studio.WRITE and self.command != 'POST':
            return self._send(400, {'error': f'POST /studio/{verb}'})
        try:
            return self._send(200, studio.call(verb, {**q, **(body or {})},
                                               writable=who['writable']))
        except studio.Denied as e:
            return self._send(403, {'error': str(e), 'whoami': who})
        except KeyError as e:
            if verb not in studio.READ + studio.WRITE:
                return self._send(404, {'error': f'no studio verb {verb!r}',
                                        'read': studio.READ, 'write': studio.WRITE})
            return self._send(400, {'error': f'missing parameter {e}'})
        except studio.StudioError as e:
            return self._send(400, {'error': str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(400, {'error': f'{type(e).__name__}: {e}'})

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
