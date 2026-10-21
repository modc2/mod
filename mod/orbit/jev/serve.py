"""Server for jev: the console, the protocol API and MCP on one port, with
crypto pay-per-question (x402) and prepaid credits at the door.

Kept as a standalone script so pm2 can run it directly (``python3 serve.py``)
without importing the orbit loader; ``m.serve('jev')`` hands off to it through
``Mod.serve``. Every route dispatches into ``Mod`` so ``m jev/noul ...`` and
``POST /jev/api/noul`` are the same call — the only difference is who pays.

    /jev/                 → the console (web/index.html)
    /jev/api/{fn}         → the API (canonical mod-protocol shape)
    /api/jev/{fn}         → the API (legacy alias)
    /{fn}                 → the API, bare, for local curl
    /jev/mcp, /mcp, /api/jev/mcp
                          → MCP (streamable HTTP, stateless JSON-RPC)

Paid routes (ask, noul, choice, score) answer 402 with x402 `accepts` unless
the caller is this box, pays with X-PAYMENT, or holds credits under a
mod-protocol token. See jevpay.

    python3 serve.py [--port 50127] [--host 0.0.0.0]
"""

import argparse
import importlib.util
import json
import os
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(MODULE_DIR, 'web')
PREFIX = '/jev'
MAX_BODY = 1_000_000

if MODULE_DIR not in sys.path:
    sys.path.append(MODULE_DIR)

# Loaded by path under its own name — `mod` belongs to the protocol package.
_spec = importlib.util.spec_from_file_location('jev_anchor', os.path.join(MODULE_DIR, 'mod.py'))
_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_anchor)
MOD = _anchor.Mod()

import jevpay as pay            # noqa: E402 — the door
from jevrelay import credits_ok as pay_credits_ok   # noqa: E402

FREE_FNS = ('info', 'health', 'status', 'models', 'price', 'pay', 'whoami', 'balance',
            'readme', 'encode', 'version', 'tools', 'stats', 'relay')
PAID_FNS = ('ask', 'noul', 'choice', 'score')
TOPUP_FNS = ('topup',)
OWNER_FNS = ('grant', 'ledger', 'payments', 'pricing', 'wallet')        # this box only
API_FNS = FREE_FNS + PAID_FNS + TOPUP_FNS + OWNER_FNS
MCP_PATHS = ('/mcp', f'{PREFIX}/mcp', f'/api{PREFIX}/mcp')
STARTED = time.time()


# ── MCP tools ─────────────────────────────────────────────────────

def _schema(props, required):
    return {'type': 'object', 'properties': props, 'required': required}


_STATE = {'type': ['string', 'object', 'array'], 'description': 'the content every question refers to'}
_MODEL = {'type': 'string', 'description': 'model override (default jev-latest)'}

TOOLS = [
    {'name': 'jev_noul', 'description': 'Yes/no: the probability (0-1) that `instructions` holds for `state`. Paid (x402 or credits).',
     'inputSchema': _schema({'state': _STATE, 'instructions': {'type': 'string'},
                             'criteria': {'type': 'object', 'description': 'optional {true, false} descriptions'},
                             'model': _MODEL}, ['state', 'instructions'])},
    {'name': 'jev_choice', 'description': 'Pick one of `options`: choice, confidence, per-option probabilities. Paid.',
     'inputSchema': _schema({'state': _STATE, 'instructions': {'type': 'string'},
                             'options': {'type': ['array', 'object', 'string'], 'description': 'list, "a,b,c", or {option: description}'},
                             'model': _MODEL}, ['state', 'instructions', 'options'])},
    {'name': 'jev_score', 'description': 'Rate `state` against ordered `levels` (low→high): score, level, confidence, distribution. Paid.',
     'inputSchema': _schema({'state': _STATE, 'instructions': {'type': 'string'},
                             'levels': {'type': ['array', 'string'], 'description': 'ordered levels, list or "a,b,c"'},
                             'model': _MODEL}, ['state', 'instructions', 'levels'])},
    {'name': 'jev_ask', 'description': 'Full System One: many named typed questions in one call. '
                                        'questions = {name: {kind: noul|choice|score|raw, instructions, criteria}}. Paid per question.',
     'inputSchema': _schema({'state': _STATE, 'questions': {'type': 'object'}, 'model': _MODEL,
                             'timeout_s': {'type': 'number'}, 'extra': {'type': 'object'}}, ['state', 'questions'])},
    {'name': 'jev_models', 'description': 'Every model the TypeSafe API offers. Free.', 'inputSchema': _schema({}, [])},
    {'name': 'jev_price', 'description': 'What a question costs here, on which chains, paid to whom, and how credits work. Free.',
     'inputSchema': _schema({}, [])},
    {'name': 'jev_balance', 'description': 'Prepaid credits of an address (yours when you send a mod-protocol token). Free.',
     'inputSchema': _schema({'address': {'type': 'string'}}, [])},
    {'name': 'jev_encode', 'description': 'Validate a request offline and return the exact wire body jevgo would send. Free.',
     'inputSchema': _schema({'state': _STATE, 'questions': {'type': 'object'}, 'model': _MODEL}, ['state', 'questions'])},
]
TOOL_ANNOTATIONS = {t['name']: {'readOnlyHint': True, 'openWorldHint': t['name'] not in ('jev_price', 'jev_balance', 'jev_encode'),
                                '_meta': {'cost': 'spends' if t['name'] in ('jev_noul', 'jev_choice', 'jev_score', 'jev_ask') else 'free'}}
                    for t in TOOLS}


def _n_questions(fn, a):
    if fn != 'ask':
        return 1
    q = a.get('questions')
    if isinstance(q, str):
        try:
            q = json.loads(q)
        except json.JSONDecodeError:
            return 1
    return len(q) if isinstance(q, dict) else 1


def _coerce(args):
    out = {}
    for k, v in args.items():
        val = v[0] if isinstance(v, list) else v
        if val != '':
            out[k] = val
    return out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB_DIR, **kw)

    def log_message(self, *a):          # quiet — pm2 keeps the logs
        pass

    # ── plumbing ──
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'Content-Type, Authorization, token, X-Mod-Token, X-PAYMENT, PAYMENT-SIGNATURE, Mcp-Session-Id')
        self.send_header('Access-Control-Expose-Headers', 'X-PAYMENT-RESPONSE, X-Jev-Credits-Left')

    def _json(self, obj, status=200, extra=None):
        body = json.dumps(obj, default=str).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        # Owner standing is this box only: never a request the gateway forwarded.
        fwd = self.headers.get('X-Forwarded-For') or self.headers.get('CF-Connecting-IP')
        return not fwd and self.client_address[0] in ('127.0.0.1', '::1')

    def _headers(self):
        return dict(self.headers.items())

    def _resource(self, fn):
        proto = self.headers.get('X-Forwarded-Proto') or 'http'
        host = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or f'localhost:{self.server.server_port}'
        return f'{proto}://{host}{PREFIX}/api/{fn}'

    def _fn(self, path):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/'):
            if path.startswith(pre) and path[len(pre):] in API_FNS:
                return path[len(pre):]
        return None

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            return None
        raw = self.rfile.read(n) if n else b''
        if not raw:
            return {}
        ctype = (self.headers.get('Content-Type') or '').lower()
        if 'json' in ctype or raw.lstrip()[:1] in (b'{', b'['):
            try:
                d = json.loads(raw)
                return d if isinstance(d, dict) else {'body': d}
            except json.JSONDecodeError:
                return None
        return _coerce(parse_qs(raw.decode('utf-8', 'replace')))

    # ── dispatch ──
    def _dispatch(self, fn, args):
        local = self._local()
        if fn in OWNER_FNS:
            if not local:
                return self._json({'error': f'{fn} is owner-only (this box)', 'code': 'forbidden'}, 403)
            return self._json(self._owner(fn, args))
        if fn in TOPUP_FNS:
            status, body, extra = pay.topup(self._headers(), self._resource('topup'))
            return self._json(body, status, extra)
        if fn in PAID_FNS:
            n = _n_questions(fn, args)
            status, body, extra = pay.charge(self._headers(), n, self._resource(fn),
                                             lambda: self._run(fn, args), local=local)
            return self._json(body, status, extra)
        return self._json(self._free(fn, args, local))

    def _run(self, fn, args):
        try:
            return getattr(MOD, fn)(**args)
        except TypeError as e:
            return {'error': f'{fn}: {e}', 'code': 'invalid_request'}
        except Exception as e:                                # noqa: BLE001
            return {'error': f'{fn}: {e}', 'code': 'internal'}

    def _free(self, fn, args, local):
        if fn == 'health':
            return {'ok': True, 'module': 'jev', 'uptime_s': int(time.time() - STARTED),
                    'pay': pay.public()['enabled'], 'built': os.path.exists(_anchor.BIN)}
        if fn == 'whoami':
            who = pay.identify(self._headers(), local=local)
            out = {**who, 'pays': not (local and pay.policy()['free_local']) and pay.public()['enabled']}
            if who['kind'] == 'wallet':
                out['credits'] = pay.balance(who['id'])
            return out
        if fn == 'balance':
            addr = args.get('address') or pay.identify(self._headers(), local=local).get('id')
            if not str(addr).startswith('0x'):
                return {'error': 'send a mod-protocol token or address=0x…', 'code': 'invalid_request'}
            return pay.balance(addr)
        if fn in ('price', 'pay'):
            pub, rel = pay.public(), MOD.relay()
            relayed = rel.get('backend') == 'relay'
            # one line a console or an agent can read: who answers, what one ask costs
            answers = {'backend': rel.get('backend'), 'live': not relayed or 'via' in rel,
                       'via': rel.get('via') if relayed else 'TypeSafe (direct)',
                       'per': 'call' if relayed else 'question',
                       'usd': rel.get('seller_usd') if relayed else pub['price_usd'],
                       'paid_to': 'the Jev seller' if relayed else 'this node'}
            return {**pub, 'relay': rel, 'answers': answers, 'credits_available': pay_credits_ok()}
        if fn == 'tools':
            return {'tools': TOOLS}
        if fn == 'stats':
            return pay.stats()
        if fn == 'readme':
            return {'readme': MOD.readme()}
        return self._run(fn, args)

    def _owner(self, fn, args):
        try:
            if fn == 'grant':
                return MOD.grant(**args)
            if fn == 'ledger':
                return MOD.ledger(**args)
            if fn == 'payments':
                return MOD.payments(**args)
            if fn == 'pricing':
                return MOD.pricing(**args)
            if fn == 'wallet':
                return MOD.wallet(**args)
        except (pay.PayError, TypeError, ValueError) as e:
            return {'error': str(e), 'code': 'invalid_request'}
        return {'error': 'unknown', 'code': 'not_found'}

    # ── MCP (stateless streamable HTTP) ──
    def _mcp(self, msg):
        mid = msg.get('id')
        method = msg.get('method')
        if method == 'initialize':
            return {'jsonrpc': '2.0', 'id': mid, 'result': {
                'protocolVersion': '2025-03-26',
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': 'jev', 'version': MOD.version().get('module', '0.2.0')},
                'instructions': ('Typed questions over the Jev model (TypeSafe System One). jev_noul / jev_choice / '
                                 'jev_score / jev_ask cost ' + str(pay.policy()['price_usd']) + ' USD per question, '
                                 'paid in USDC with no KYC: send X-PAYMENT (x402) or a mod-protocol `token:` header '
                                 'with prepaid credits. jev_price explains; jev_models, jev_balance, jev_encode are free.')}}
        if method in ('notifications/initialized', 'ping'):
            return {'jsonrpc': '2.0', 'id': mid, 'result': {}} if mid is not None else None
        if method == 'tools/list':
            return {'jsonrpc': '2.0', 'id': mid, 'result': {
                'tools': [{**t, 'annotations': TOOL_ANNOTATIONS[t['name']]} for t in TOOLS]}}
        if method == 'tools/call':
            params = msg.get('params') or {}
            name, a = params.get('name'), params.get('arguments') or {}
            fn = str(name or '').removeprefix('jev_')
            if fn not in ('noul', 'choice', 'score', 'ask', 'models', 'price', 'balance', 'encode'):
                return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32602, 'message': f'unknown tool {name}'}}
            local = self._local()
            if fn in PAID_FNS:
                status, body, _ = pay.charge(self._headers(), _n_questions(fn, a), self._resource(fn),
                                             lambda: self._run(fn, a), local=local)
            else:
                status, body = 200, self._free(fn, a, local)
                if 'error' in body:
                    status = 400
            is_err = status >= 400
            return {'jsonrpc': '2.0', 'id': mid, 'result': {
                'content': [{'type': 'text', 'text': json.dumps(body, default=str)}],
                'structuredContent': body, 'isError': is_err,
                **({'_meta': {'http_status': status}} if is_err else {})}}
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'method {method} not found'}}

    # ── verbs ──
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip('/') or '/'
        if path in MCP_PATHS:
            return self._json({'error': 'POST JSON-RPC here', 'tools': [t['name'] for t in TOOLS]}, 405)
        fn = self._fn(path)
        if fn:
            return self._dispatch(fn, _coerce(parse_qs(u.query)))
        if path.startswith((f'{PREFIX}/api/', f'/api{PREFIX}/')):
            return self._json({'error': 'not found', 'api': [f'{PREFIX}/api/{f}' for f in API_FNS]}, 404)
        if path in ('/', PREFIX) or path.startswith(PREFIX + '/'):
            rel = path[len(PREFIX):] if path.startswith(PREFIX) else path
            if rel in ('', '/'):
                self.path = '/index.html'
            else:
                self.path = rel
            return super().do_GET()
        return self._json({'error': 'not found', 'api': [f'{PREFIX}/api/{f}' for f in API_FNS]}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip('/') or '/'
        body = self._body()
        if body is None:
            return self._json({'error': 'body must be JSON (or too large)', 'code': 'invalid_request'}, 400)
        if path in MCP_PATHS:
            msgs = body.get('body') if 'body' in body and isinstance(body.get('body'), list) else [body]
            replies = [r for r in (self._mcp(m) for m in msgs if isinstance(m, dict)) if r is not None]
            if not replies:
                self.send_response(202)
                self._cors()
                self.end_headers()
                return
            return self._json(replies[0] if len(replies) == 1 else replies)
        fn = self._fn(path)
        if not fn:
            return self._json({'error': 'not found', 'api': [f'{PREFIX}/api/{f}' for f in API_FNS]}, 404)
        args = {**_coerce(parse_qs(u.query)), **body}
        return self._dispatch(fn, args)


def serve(port=50127, host='0.0.0.0'):
    srv = ThreadingHTTPServer((host, int(port)), Handler)
    srv.daemon_threads = True
    pub = pay.public()
    print(f'jev: http://localhost:{port}{PREFIX}/  api http://localhost:{port}{PREFIX}/api/noul  '
          f'mcp http://localhost:{port}{PREFIX}/mcp  pay={"on" if pub["enabled"] else "off"} '
          f'${pub["price_usd"]}/question -> {pub["pay_to"] or "(no pay_to)"}', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=int(os.environ.get('JEV_PORT') or 50127))
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
