#!/usr/bin/env python3
"""compute-fork api — REST + MCP + console on one port, zero dependencies.

Every route is a thin call into the same `Hub` the MCP tools use, so the
browser, the CLI and an agent all get the same answer to the same question.

Keys are per request and never stored by this server: send them as
`x-<provider>-key: …` headers, or one `x-computefork-keys: {"lium":"…"}` header.
Absent that, the Hub falls back to the operator's own environment and off-tree
keystore — so a locally-run server is convenient, and a shared one is BYOK.

    python3 api.py [--port 50511]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import auth                               # noqa: E402
import mcp                                # noqa: E402
import node as N                          # noqa: E402
from hub import CONFIRM_USD, Hub          # noqa: E402
from providers import REGISTRY            # noqa: E402
from providers.base import ProviderError  # noqa: E402

BASE = os.environ.get('BASE_PATH', '/compute-fork')
PORT = int(os.environ.get('PORT', 50511))


def info():
    return {
        'name': 'compute-fork',
        'version': mcp.version(),
        'what': 'one interface to every compute market — search, quote, rent, '
                'stop, across Bittensor (Targon, Lium), Akash, Vast.ai, Nosana, '
                'Cathedral, Prime Intellect, Polaris, Shadeform and your own hosts',
        'providers': list(REGISTRY),
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'byok': {'headers': ['x-<provider>-key: <key>',
                             'x-computefork-keys: {"lium":"…","vast":"…"}'],
                 'keystore': '~/.mod/compute-fork/keys.json (0600, off-tree)',
                 'rule': "every call spends the caller's own credits — no house key"},
        'spend_guard_usd': CONFIRM_USD,
        'endpoints': {
            'GET /providers': 'markets, capabilities, KYC, payment rails, key state',
            'GET /mods': 'the same markets read through the sibling modules that '
                         'front them (targon, lium, cathedral), each against the '
                         'direct lane — mod=&compare=&sample=',
            'GET /search': 'gpu, min_gpus, min_vram_gb, max_usd_hr, region, provider, '
                           'kyc, sort, limit',
            'GET /offer': 'id=provider:ref',
            'GET /quote': 'id=provider:ref&hours=N — cost + cheaper alternatives',
            'POST /rent': '{id, hours, confirm, name, image, ssh_key, …}',
            'GET /instances': 'everything running, with the combined burn rate',
            'GET /status': 'id=provider:ref',
            'GET /logs': 'id=provider:ref&tail=N',
            'POST /exec': '{id, cmd}',
            'POST /stop': '{id} — ends the billing',
            'GET /balance': 'per-provider prepaid balance',
            'POST /keys': '{provider, key, persist}',
            'POST /raw': "{provider, path, method, body} — a provider's own API",
            'GET /tools': 'the MCP tool registry',
            'POST /mcp': 'MCP JSON-RPC 2.0',
            f'GET {BASE}': 'browser console',
        },
        'nodes': {
            'what': 'a rented box, bootstrapped into a mod protocol container '
                    'and driven from here — no port has to be open on it',
            'POST /nodes/deploy': '{id|instance|ssh|docker, name, hours, confirm, '
                                  'image, profile, ports} — rent/adopt, then install',
            'GET /nodes': 'every node, with the combined burn rate',
            'GET /node': 'node=ID&probe=1 — one node, live',
            'POST /node/sh': '{node, cmd} — a shell on it',
            'GET /node/mods': 'node=ID — the modules that node is running',
            'POST /node/call': '{node, mod, fn, args, kwargs} — call one of them',
            'POST /node/push': '{node, mod} — send a local module to it',
            'POST /node/tunnel': '{node, port} — bring a port back to this browser',
            'POST /node/rm': '{node, release} — tear it down; release=1 also '
                             'stops the rental, which is what ends the billing',
            'auth': 'owner-only: Authorization: Bearer `m compute-fork/token`, or '
                    'call from localhost',
        },
    }


def _keys_from(headers):
    """BYOK per request: one JSON header, or one header per provider."""
    keys = {}
    blob = headers.get('x-computefork-keys')
    if blob:
        try:
            got = json.loads(blob)
            if isinstance(got, dict):
                keys.update({k.lower(): v for k, v in got.items() if isinstance(v, str)})
        except Exception:
            pass
    for name in REGISTRY:
        v = headers.get(f'x-{name}-key')
        if v:
            keys[name] = v
    return keys


def route(method, path, query, body, keys, owner=False):
    """One request → one JSON answer. Raises ProviderError for real failures."""
    h = Hub(keys=keys)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}
    auth.guard(path, keys=keys, owner=owner)

    def arg(name, default=None):
        v = b.get(name, q.get(name, default))
        return v

    def flag(name, default=False):
        v = arg(name, default)
        return str(v).lower() not in ('', '0', 'false', 'none') if v is not True else True

    if path.startswith('/node'):
        return node_route(method, path, q, b, keys, arg, flag)
    if path in ('', '/'):
        return info()
    if path == '/identity':
        return {'ssh_public_key': N.keypair()['public'], **auth.state()}
    if path == '/health':
        return {'ok': True, 'providers': len(REGISTRY), 'tools': len(mcp.TOOLS)}
    if path == '/providers':
        return h.providers(kyc=q.get('kyc'), cap=q.get('cap'), names=q.get('provider'))
    if path == '/mods':
        import mods
        return mods.lane(names=q.get('mod') or q.get('provider'), keys=keys,
                         compare=q.get('compare', '1') not in ('0', 'false'),
                         sample=int(q.get('sample') or 6),
                         gpu=q.get('gpu'), max_usd_hr=q.get('max_usd_hr'),
                         min_gpus=q.get('min_gpus'), min_vram_gb=q.get('min_vram_gb'),
                         kind=q.get('kind'),
                         available_only=q.get('available_only', '1') not in ('0', 'false'))
    if path == '/search':
        return h.search(provider=q.get('provider'), kyc=q.get('kyc'),
                        sort=q.get('sort') or 'price', gpu=q.get('gpu'),
                        min_gpus=q.get('min_gpus'), min_vram_gb=q.get('min_vram_gb'),
                        max_usd_hr=q.get('max_usd_hr'), region=q.get('region'),
                        kind=q.get('kind'),
                        available_only=q.get('available_only', '1') not in ('0', 'false'),
                        limit=q.get('limit') or 40)
    if path == '/offer':
        return h.offer(_need(arg('id'), 'id'))
    if path == '/quote':
        return h.quote(_need(arg('id'), 'id'), hours=arg('hours') or 1)
    if path == '/rent' and method == 'POST':
        opts = {k: v for k, v in b.items()
                if k in ('name', 'image', 'cmd', 'ssh_key', 'disk_gb', 'gpus',
                         'template_id', 'region', 'host', 'gpu_count')}
        return h.rent(_need(b.get('id'), 'id'), hours=b.get('hours') or 1,
                      confirm=bool(b.get('confirm')), **opts)
    if path == '/instances':
        return h.instances(provider=q.get('provider'))
    if path == '/status':
        return h.status(_need(arg('id'), 'id'))
    if path == '/logs':
        return h.logs(_need(arg('id'), 'id'), tail=arg('tail') or 200)
    if path == '/exec' and method == 'POST':
        return h.exec(_need(b.get('id'), 'id'), _need(b.get('cmd'), 'cmd'))
    if path == '/stop' and method in ('POST', 'DELETE'):
        return h.stop(_need(arg('id'), 'id'))
    if path == '/balance':
        return h.balance(provider=q.get('provider'))
    if path == '/keys' and method == 'POST':
        return h.set_key(_need(b.get('provider'), 'provider'), _need(b.get('key'), 'key'),
                         persist=b.get('persist', True))
    if path == '/keys':
        return {'keys': {n: ('set' if REGISTRY[n](key=keys.get(n)).has_key() else 'missing')
                         for n in REGISTRY}}
    if path == '/raw' and method == 'POST':
        return h.raw(_need(b.get('provider'), 'provider'), _need(b.get('path'), 'path'),
                     method=b.get('method') or 'GET', body=b.get('body'),
                     params=b.get('params'))
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise ProviderError(f'no route {method} {path} — GET / lists them', status=404)


def node_route(method, path, q, b, keys, arg, flag):
    """Everything under /node*: deploy a box, then drive what is on it.

    Guarded upstream in `route` — nothing here is reachable without the token.
    """
    nid = arg('node') or arg('id')

    if path in ('/nodes', '/node/list'):
        return N.nodes(state=q.get('state'))
    if path in ('/nodes/deploy', '/node/deploy') and method == 'POST':
        return N.deploy(id=b.get('id'), ssh=b.get('ssh'), instance=b.get('instance'),
                        docker=b.get('docker'), name=b.get('name'),
                        hours=b.get('hours') or 1, confirm=bool(b.get('confirm')),
                        image=b.get('image'), profile=b.get('profile') or 'lite',
                        ports=b.get('ports'), gpus=b.get('gpus'),
                        wait=bool(b.get('wait')), keys=keys)
    if path == '/node':
        return N.probe(_need(nid, 'node')) if flag('probe', True) \
            else N._public(N.get(_need(nid, 'node')))
    if path == '/node/log':
        return {'node': nid, 'log': N.get(_need(nid, 'node')).get('log') or []}
    if path == '/node/bootstrap' and method == 'POST':
        return N.bootstrap(_need(nid, 'node'), profile=b.get('profile') or 'lite',
                           force=bool(b.get('force')), wait=bool(b.get('wait')))
    if path == '/node/sh' and method == 'POST':
        return N.sh(_need(nid, 'node'), _need(b.get('cmd'), 'cmd'),
                    timeout=float(b.get('timeout') or 120))
    if path == '/node/mods':
        return N.ctl(_need(nid, 'node'), 'mods')
    if path == '/node/fns':
        return N.ctl(_need(nid, 'node'), 'fns', mod=_need(arg('mod'), 'mod'))
    if path == '/node/call' and method == 'POST':
        return N.call(_need(nid, 'node'), _need(b.get('mod'), 'mod'),
                      fn=b.get('fn') or 'forward', args=b.get('args'),
                      kwargs=b.get('kwargs'), init=b.get('init'),
                      timeout=float(b.get('timeout') or 300))
    if path == '/node/ctl':
        return N.ctl(_need(nid, 'node'), arg('op') or 'info')
    if path == '/node/push' and method == 'POST':
        return N.push(_need(nid, 'node'), _need(b.get('mod'), 'mod'),
                      restart=bool(b.get('restart')))
    if path == '/node/sync' and method == 'POST':
        return N.sync(_need(nid, 'node'), keys=keys)
    if path == '/node/tunnel' and method == 'POST':
        return N.tunnel(_need(nid, 'node'), _need(b.get('port'), 'port'),
                        local_port=b.get('local_port'))
    if path in ('/node/rm', '/node/destroy') and method in ('POST', 'DELETE'):
        return N.destroy(_need(nid, 'node'), release=bool(b.get('release')), keys=keys)
    if path == '/node/forget' and method in ('POST', 'DELETE'):
        return N.forget(_need(nid, 'node'))
    raise ProviderError(f'no route {method} {path} — GET / lists them', status=404)


def _need(v, name):
    if v in (None, ''):
        raise ProviderError(f'{name} is required')
    return v


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /compute-fork behind the gateway or served bare at :50511/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/compute-fork', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'compute-fork/' + mcp.version()

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else \
                json.dumps(payload, default=str, indent=2).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.send_header('access-control-allow-methods', 'GET,POST,DELETE,OPTIONS')
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
            """Strip the gateway prefixes so /compute-fork/_api/search == /search."""
            raw = urllib.parse.urlparse(self.path)
            p, query = raw.path, raw.query
            for prefix in api_prefixes:
                if p == prefix or p.startswith(prefix + '/'):
                    return p[len(prefix):] or '/', query, True
            if p == base or p == base + '/':
                return '/console', query, False
            if p.startswith(base + '/'):
                return p[len(base):], query, False
            return p, query, False

        def _owner(self):
            return auth.authed(self.headers, self.client_address[0]
                               if self.client_address else None)

        def _dispatch(self):
            p, query, _ = self._path()
            p = p.rstrip('/') or '/'
            owner = self._owner()
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcp.handle(self._read(), owner=owner)
                return self._send(202 if resp is None else 200, resp or b'', 'application/json'
                                  if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        page = f.read()
                    if owner:
                        # The operator's own browser should not have to paste a
                        # token at itself. A proxied request never gets this.
                        page = page.replace(b'</head>', b'<script>window.__COMPUTE_TOKEN='
                                            + json.dumps(auth.secret()).encode()
                                            + b'</script></head>', 1)
                    return self._send(200, page, 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, json.dumps(info(), indent=2).encode(),
                                      'application/json')
            try:
                return self._send(200, route(self.command, p, query, self._read(),
                                             _keys_from(self.headers), owner=owner))
            except ProviderError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'compute-fork on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(REGISTRY)} providers, {len(mcp.TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
