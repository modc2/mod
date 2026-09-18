#!/usr/bin/env python3
"""polaris api — REST + MCP + console on one port, zero dependencies.

Every route is a thin call into the same `Polaris` client the MCP tools use, so
the browser, the CLI and an agent get the same answer to the same question.

The key is per request and never stored by this server: send it as
`x-polaris-key: pi_sk_…`. Absent that, the client falls back to the operator's
own environment and off-tree keystore — so a locally-run server is convenient
and a published one is BYOK.

    python3 api.py [--port 50870]
"""

import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import auth                                                   # noqa: E402
import mcp                                                    # noqa: E402
from client import BASE as UPSTREAM                           # noqa: E402
from client import CONFIRM_USD, KEY_FILE, Polaris, PolarisError, ssh_key  # noqa: E402

BASE = os.environ.get('BASE_PATH', '/polaris')
PORT = int(os.environ.get('PORT', 50870))


def info():
    return {
        'name': 'polaris',
        'version': mcp.version(),
        'what': 'the Polaris GPU cloud (polaris.computer) as a mod protocol '
                'module — GPUs and CPU boxes billed by the second, sixteen '
                'one-click template deployments, a 400-model inference catalog, '
                'and the billing behind all of it',
        'upstream': UPSTREAM,
        'mcp': {'endpoint': 'POST /mcp', 'transport': 'Streamable HTTP (JSON-RPC 2.0)',
                'stdio': 'python3 mcp.py', 'tools': len(mcp.TOOLS)},
        'byok': {'header': 'x-polaris-key: pi_sk_…',
                 'env': 'POLARIS_KEY',
                 'keystore': f'{KEY_FILE} (0600, off-tree)',
                 'rule': "every call spends the caller's own credits — no house key"},
        'spend_guard_usd': CONFIRM_USD,
        'endpoints': {
            'GET /gpus': 'the catalog — gpu, min_vram_gb, max_usd_hr, kind, '
                         'available_only, sort, limit. Public.',
            'GET /pricing': 'the billing table behind the catalog. Public.',
            'GET /templates': 'one-click images — category, q. Public.',
            'GET /template': 'template_id=… — one in full, with its parameters.',
            'GET /models': 'the inference catalog — q, provider, max_prompt_price, '
                           'min_context, sort, limit.',
            'GET /quote': 'gpu_type=&hours=&spot=&quantity= — cost before you commit.',
            'POST /rent': '{gpu_type, name, hours, confirm, spot, quantity, '
                          'ssh_public_key} — spends credits.',
            'GET /instances': 'everything running, with the combined burn rate.',
            'GET /instance': 'instance_id=… — one, by id or name.',
            'GET /ssh': 'instance_id=… — how to get a shell.',
            'POST /stop': '{instance_id} — ends the billing.',
            'GET /deployments': 'template deployments — state, kind.',
            'GET /deployment': 'deployment_id=… — one in full.',
            'GET /logs': 'deployment_id=&tail= — provisioning and runtime logs.',
            'GET /activity': 'limit=… — the account event tape.',
            'GET /account': 'who the key belongs to, and its quota.',
            'GET /credits': 'the prepaid balance and what it permits.',
            'GET /history': 'limit=&offset= — the credit ledger.',
            'GET /packs': 'top-up sizes. Public.',
            'GET /usage': 'billing=1 — API requests and compute seconds.',
            'GET /stats': 'deployment and spend counters.',
            'GET /keys': 'your Polaris keys as metadata — never the secrets.',
            'GET /status': 'money, boxes, deployments and hours of runway.',
            'POST /set_key': '{key, persist} — store a key 0600, off-tree.',
            'GET /identity': 'the SSH public key this module rents with.',
            'POST /raw': "{path, method, body, params} — Polaris's own API.",
            'GET /tools': 'the MCP tool registry.',
            'POST /mcp': 'MCP JSON-RPC 2.0.',
            f'GET {BASE}': 'browser console.',
        },
        'auth': {
            'open': 'gpus, pricing, templates, models, quote, packs, tools — '
                    'reading a catalog spends nothing',
            'byok': 'every account route, when the caller sends x-polaris-key',
            'owner': 'rent, stop, raw, set_key, token',
            'proof': 'Authorization: Bearer `m polaris/token`, or a request from '
                     'localhost that did not pass through the gateway',
        },
    }


def route(method, path, query, body, key=None, owner=False):
    """One request → one JSON answer. Raises PolarisError for real failures."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    b = body if isinstance(body, dict) else {}
    auth.guard(path, key=key, owner=owner)
    c = Polaris(key=key)

    def arg(name, default=None):
        return b.get(name, q.get(name, default))

    def flag(name, default=False):
        v = arg(name, default)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() not in ('', '0', 'false', 'no', 'none')

    if path in ('', '/'):
        return info()
    if path == '/health':
        return {'ok': True, 'upstream': UPSTREAM, 'tools': len(mcp.TOOLS),
                'key': 'set' if c.has_key() else 'missing'}

    # ── catalog ──
    if path == '/gpus':
        return c.gpus(gpu=arg('gpu'), min_vram_gb=arg('min_vram_gb'),
                      max_usd_hr=arg('max_usd_hr'), kind=arg('kind'),
                      available_only=flag('available_only', True),
                      sort=arg('sort') or 'price', limit=arg('limit'))
    if path == '/pricing':
        return c.pricing()
    if path == '/templates':
        return c.templates(category=arg('category'), q=arg('q'))
    if path == '/template':
        return c.template(_need(arg('template_id') or arg('id'), 'template_id'))
    if path == '/models':
        return c.models(q=arg('q'), provider=arg('provider'),
                        max_prompt_price=arg('max_prompt_price'),
                        min_context=arg('min_context'), sort=arg('sort') or 'name',
                        limit=arg('limit') or 60)
    if path == '/quote':
        return c.quote(_need(arg('gpu_type'), 'gpu_type'), hours=arg('hours') or 1,
                       spot=flag('spot', True), quantity=arg('quantity') or 1)

    # ── renting ──
    if path == '/rent' and method == 'POST':
        extra = {k: v for k, v in b.items()
                 if k in ('image', 'region', 'disk_gb', 'template_id')}
        return c.rent(_need(b.get('gpu_type'), 'gpu_type'), name=b.get('name') or 'mod',
                      hours=b.get('hours') or 1, confirm=b.get('confirm', False),
                      ssh_public_key=b.get('ssh_public_key'),
                      spot=b.get('spot', True), quantity=b.get('quantity') or 1,
                      **extra)
    if path == '/instances':
        return c.instances(state=arg('state'))
    if path == '/instance':
        return c.instance(_need(arg('instance_id') or arg('id'), 'instance_id'))
    if path == '/ssh':
        return c.ssh(arg('instance_id') or arg('id'))
    if path == '/stop' and method in ('POST', 'DELETE'):
        return c.stop(_need(arg('instance_id') or arg('id'), 'instance_id'))

    # ── deployments ──
    if path == '/deployments':
        return c.deployments(state=arg('state'), kind=arg('kind'))
    if path == '/deployment':
        return c.deployment(_need(arg('deployment_id') or arg('id'), 'deployment_id'))
    if path == '/logs':
        return c.deployment_logs(_need(arg('deployment_id') or arg('id'), 'deployment_id'),
                                 tail=arg('tail') or 200)
    if path == '/activity':
        return c.activity(limit=arg('limit') or 25)

    # ── account and money ──
    if path == '/account':
        return c.account()
    if path in ('/credits', '/balance'):
        return c.credits()
    if path == '/history':
        return c.history(limit=arg('limit') or 20, offset=arg('offset') or 0)
    if path == '/packs':
        return c.packs()
    if path == '/usage':
        return c.usage(billing=flag('billing', True))
    if path == '/stats':
        return c.stats()
    if path == '/keys' and method != 'POST':
        return c.api_keys()
    if path == '/status':
        return c.status()

    # ── credentials and escape hatch ──
    if path in ('/set_key', '/keys') and method == 'POST':
        return Polaris.set_key(_need(b.get('key'), 'key'), persist=b.get('persist', True))
    if path == '/identity':
        return {'ssh_public_key': ssh_key(),
                'polaris_key': 'set' if c.has_key() else 'missing',
                'key_file': KEY_FILE, **auth.state()}
    if path == '/raw' and method in ('POST', 'GET'):
        return c.raw(_need(arg('path'), 'path'), method=arg('method') or 'GET',
                     body=b.get('body'), params=b.get('params'),
                     auth=flag('auth', True))
    if path == '/tools':
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}
    raise PolarisError(f'no route {method} {path} — GET / lists them', status=404)


def _need(v, name):
    if v in (None, ''):
        raise PolarisError(f'{name} is required')
    return v


def serve(port=PORT, base=BASE):
    console = os.path.join(HERE, 'console.html')
    # The console calls `<its own path>/_api`, so it works whether the module is
    # mounted at /polaris behind the gateway or served bare at :50870/.
    api_prefixes = (base.rstrip('/') + '/_api', '/api/polaris', '/_api')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'polaris/' + mcp.version()

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
            """Strip the gateway prefixes so /polaris/_api/gpus == /gpus."""
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

        def _dispatch(self):
            p, query = self._path()
            p = p.rstrip('/') or '/'
            owner = auth.authed(self.headers,
                                self.client_address[0] if self.client_address else None)
            if p == '/mcp':
                if self.command != 'POST':
                    return self._send(405, b'POST JSON-RPC 2.0 here', 'text/plain')
                resp = mcp.handle(self._read(), owner=owner)
                return self._send(202 if resp is None else 200, resp or b'',
                                  'application/json' if resp else 'text/plain')
            if p in ('/console', '/index.html') and self.command == 'GET':
                try:
                    with open(console, 'rb') as f:
                        page = f.read()
                    if owner:
                        # The operator's own browser should not have to paste a
                        # token at itself. A proxied request never gets this.
                        page = page.replace(b'</head>', b'<script>window.__POLARIS_TOKEN='
                                            + json.dumps(auth.secret()).encode()
                                            + b'</script></head>', 1)
                    return self._send(200, page, 'text/html; charset=utf-8')
                except FileNotFoundError:
                    return self._send(200, json.dumps(info(), indent=2).encode())
            try:
                return self._send(200, route(self.command, p, query, self._read(),
                                             key=auth.caller_key(self.headers),
                                             owner=owner))
            except PolarisError as e:
                return self._send(e.status if e.status in range(400, 600) else 400,
                                  e.dict())
            except Exception as e:
                return self._send(500, {'error': f'{type(e).__name__}: {e}'})

        do_GET = do_POST = do_DELETE = _dispatch

        def log_message(self, *a):
            pass

    print(f'polaris on :{port} — api /, console {base}, mcp POST /mcp, '
          f'{len(mcp.TOOLS)} tools, upstream {UPSTREAM}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    i = argv.index('--port') + 1 if '--port' in argv else -1
    serve(int(argv[i]) if i > 0 else PORT)
