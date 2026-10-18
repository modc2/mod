"""agenti server: the web app, a JSON API and MCP on one port.

    /agenti/               the app (web/index.html)
    /agenti/api/<fn>       API  (also /api/agenti/<fn>, and /api/<fn> on the bare port)
    /agenti/mcp, /mcp      MCP (stateless JSON-RPC over HTTP)

Who is the owner: a loopback caller with no proxy headers, or a request whose
Bearer token build's whoami calls the owner. Everything that touches a wallet
or spends is owner-only; lookups by address, the toolkit and discovery are open.

    python3 -m agentiapp.server [--port 50129] [--host 0.0.0.0]
"""
import argparse
import hashlib
import json
import sys
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import chains, invoices, pay, toolkit, wallets

MODULE_DIR = Path(__file__).resolve().parent.parent
WEB = MODULE_DIR / 'web' / 'index.html'
PREFIX = '/agenti'
MAX_BODY = 1_000_000
STARTED = time.time()
VERSION = json.loads((MODULE_DIR / 'config.json').read_text()).get('version', '0')


# ── API functions: name -> (callable(args, who), owner_only, write) ──

def _wallet_balances(a, who):
    w = wallets.get(a.get('wallet') or a.get('id'))
    return {'wallet': w, **chains.balances(w['evm'], w['solana'], testnets=_flag(a, 'testnets', True),
                                           fresh=_flag(a, 'fresh'))}


def _lookup(a, who):
    addr = (a.get('address') or '').strip()
    if not addr:
        raise ValueError('address required')
    if addr.startswith('0x') and len(addr) == 42:
        return chains.balances(evm=addr, fresh=_flag(a, 'fresh'))
    return chains.balances(solana=addr, fresh=_flag(a, 'fresh'))


def _overview(a, who):
    out = {'version': VERSION, 'owner': who['owner'], 'summary': toolkit.summary()}
    if who['owner']:
        ws = wallets.list_wallets()
        out.update(wallets=ws, policy=pay.policy(), spent_today=pay.spent_today(),
                   open_invoices=sum(1 for i in invoices.list_invoices() if i['status'] == 'open'),
                   recent=pay.ledger(8))
    return out


def _flag(a, k, default=False):
    v = a.get(k)
    if v is None or v == '':
        return default
    return v is True or str(v).lower() in ('1', 'true', 'yes', 'on')


FNS = {
    # open
    'health':     (lambda a, w: {'ok': True, 'version': VERSION, 'uptime': int(time.time() - STARTED)}, False, False),
    'whoami':     (lambda a, w: w, False, False),
    'overview':   (_overview, False, False),
    'chains':     (lambda a, w: chains.chains(), False, False),
    'lookup':     (_lookup, False, False),
    'services':   (lambda a, w: pay.services(a.get('q', ''), int(a.get('limit') or 24)), False, False),
    'toolkit':    (lambda a, w: {'summary': toolkit.summary(), 'packages': toolkit.packages(),
                                 'tools': toolkit.mcp_tools(), 'examples': toolkit.examples()}, False, False),
    'example':    (lambda a, w: toolkit.example(a.get('file', '')), False, False),
    'readme':     (lambda a, w: toolkit.readme(a.get('package') or None), False, False),
    # owner: wallets
    'wallets':    (lambda a, w: wallets.list_wallets(), True, False),
    'balances':   (_wallet_balances, True, False),
    'create_wallet': (lambda a, w: wallets.create(a.get('name') or 'agent', int(a.get('words') or 12)), True, True),
    'import_wallet': (lambda a, w: wallets.import_wallet(a.get('name') or 'imported', a.get('mnemonic'),
                                                         a.get('evm_key'), a.get('solana_key'),
                                                         int(a.get('index') or 0)), True, True),
    'rename_wallet': (lambda a, w: wallets.rename(a['id'], a.get('name')), True, True),
    'default_wallet': (lambda a, w: wallets.set_default(a['id']), True, True),
    'delete_wallet': (lambda a, w: wallets.delete(a['id']), True, True),
    'reveal':     (lambda a, w: wallets.reveal(a['id']), True, True),
    'sign':       (lambda a, w: wallets.sign_message(a.get('message', ''), a.get('wallet') or None), True, True),
    # owner: pay
    'probe':      (lambda a, w: pay.probe(a.get('url'), a.get('method') or 'GET', a.get('body')), True, True),
    'pay':        (lambda a, w: pay.pay(a.get('url'), a.get('method') or 'GET', a.get('body'),
                                        a.get('wallet') or None, a.get('index'), a.get('max_usd')), True, True),
    'policy':     (lambda a, w: {**pay.policy(), 'spent_today': pay.spent_today()}, True, False),
    'set_policy': (lambda a, w: pay.set_policy(**{k: a.get(k) for k in pay.DEFAULT_POLICY}), True, True),
    'ledger':     (lambda a, w: pay.ledger(int(a.get('limit') or 200), a.get('kind') or None), True, False),
    # owner: receive
    'invoices':   (lambda a, w: invoices.list_invoices(), True, False),
    'invoice':    (lambda a, w: invoices.get(a['id']), True, False),
    'create_invoice': (lambda a, w: invoices.create(a.get('amount'), a.get('chain') or 'base',
                                                    a.get('wallet') or None, a.get('memo') or ''), True, True),
    'check_invoice': (lambda a, w: invoices.check(a['id']), True, True),
    'cancel_invoice': (lambda a, w: invoices.cancel(a['id']), True, True),
}


def call(fn, args, who):
    f, owner_only, _ = FNS[fn]
    if owner_only and not who['owner']:
        raise PermissionError('owner only — sign in as the owner of this box')
    return f(args, who)


# ── MCP ───────────────────────────────────────────────────────────

def _s(props, req=()):
    return {'type': 'object', 'properties': props, 'required': list(req)}


STR = {'type': 'string'}
TOOLS = [
    ('agenti_overview', 'What this agent wallet node holds: wallets, spend limits, today\'s spend, recent activity.', 'overview', _s({})),
    ('agenti_wallets', 'List agent wallets (EVM + Solana addresses). Owner only.', 'wallets', _s({})),
    ('agenti_create_wallet', 'Create a new agent wallet (fresh 12-word phrase → EVM + Solana). Owner only.', 'create_wallet',
     _s({'name': STR})),
    ('agenti_balances', 'Native + USDC balances of a wallet on Base, Ethereum, Arbitrum, Polygon, Solana (+testnets).', 'balances',
     _s({'wallet': {'type': 'string', 'description': 'wallet id or name; default wallet if omitted'}})),
    ('agenti_lookup', 'Balances of any EVM (0x…) or Solana address. Open to everyone.', 'lookup', _s({'address': STR}, ['address'])),
    ('agenti_services', 'Find paid x402 APIs to call (from the x402 index on this node).', 'services',
     _s({'q': STR, 'limit': {'type': 'integer'}})),
    ('agenti_probe', 'Call a URL without paying. If it answers 402, returns its price offers.', 'probe',
     _s({'url': STR, 'method': STR, 'body': STR}, ['url'])),
    ('agenti_pay', 'Call a URL and pay its x402 price in USDC from an agent wallet, within the spend limits. Spends money.', 'pay',
     _s({'url': STR, 'method': STR, 'body': STR, 'wallet': STR, 'index': {'type': 'integer'},
         'max_usd': {'type': 'number', 'description': 'cap for this call (never above the policy cap)'}}, ['url'])),
    ('agenti_policy', 'Spend limits (per call, per day) and how much was spent today.', 'policy', _s({})),
    ('agenti_create_invoice', 'Ask to be paid: an amount of USDC on a chain to one of your wallets. Returns a pay link.', 'create_invoice',
     _s({'amount': {'type': 'number'}, 'chain': STR, 'wallet': STR, 'memo': STR}, ['amount'])),
    ('agenti_check_invoice', 'Has an invoice been paid? Reads the chain.', 'check_invoice', _s({'id': STR}, ['id'])),
    ('agenti_ledger', 'Payments and probes this node made.', 'ledger', _s({'limit': {'type': 'integer'}, 'kind': STR})),
    ('agenti_sign_message', 'Sign a text message (EIP-191) with an agent wallet.', 'sign', _s({'message': STR, 'wallet': STR}, ['message'])),
]
TOOL_FN = {t[0]: t[2] for t in TOOLS}


def mcp(msg, who):
    mid, method, params = msg.get('id'), msg.get('method'), msg.get('params') or {}
    if method == 'initialize':
        res = {'protocolVersion': params.get('protocolVersion') or '2025-06-18',
               'capabilities': {'tools': {}}, 'serverInfo': {'name': 'agenti', 'version': VERSION},
               'instructions': 'Agent wallets that pay x402 APIs in USDC. agenti_overview first; agenti_probe a URL '
                               'to see its price; agenti_pay to call it paid (owner only, inside spend limits).'}
    elif method == 'tools/list':
        res = {'tools': [{'name': n, 'description': d, 'inputSchema': s} for n, d, _, s in TOOLS]}
    elif method == 'tools/call':
        name = params.get('name')
        if name not in TOOL_FN:
            return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32602, 'message': f'unknown tool {name}'}}
        try:
            out = call(TOOL_FN[name], params.get('arguments') or {}, who)
            sc = out if isinstance(out, dict) else {'items': out}
            res = {'content': [{'type': 'text', 'text': json.dumps(out, default=str)[:60000]}], 'structuredContent': sc}
        except Exception as e:  # noqa: BLE001
            res = {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
    elif method in ('ping',) or (method or '').startswith('notifications/'):
        res = {}
    else:
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'no method {method}'}}
    return None if mid is None else {'jsonrpc': '2.0', 'id': mid, 'result': res}


# ── owner check ───────────────────────────────────────────────────

_TOK = {}


def _build_owner(token: str) -> dict:
    h = hashlib.sha256(token.encode()).hexdigest()
    hit = _TOK.get(h)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    who = {'owner': False, 'address': None}
    try:
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': 'whoami', 'arguments': {}}}).encode()
        req = urllib.request.Request('http://127.0.0.1:8890/mcp', data=body, headers={
            'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream',
            'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = r.read().decode()
        if raw.lstrip().startswith('data:') or '\ndata:' in raw:
            raw = next(l[5:] for l in raw.splitlines() if l.startswith('data:'))
        sc = (json.loads(raw).get('result') or {}).get('structuredContent') or {}
        who = {'owner': bool(sc.get('is_owner')), 'address': sc.get('address')}
    except Exception:  # noqa: BLE001
        pass
    _TOK[h] = (time.time(), who)
    return who


# ── HTTP ──────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    server_version = 'agenti'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        if '/health' not in (self.path or ''):
            sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _who(self):
        h = {k.lower(): v for k, v in self.headers.items()}
        proxied = any(k in h for k in ('x-forwarded-for', 'cf-connecting-ip', 'x-real-ip', 'forwarded'))
        if self.client_address[0] in ('127.0.0.1', '::1') and not proxied:
            return {'owner': True, 'via': 'local', 'address': None}
        auth = h.get('authorization', '')
        if auth.lower().startswith('bearer ') and len(auth) > 12:
            w = _build_owner(auth[7:].strip())
            return {**w, 'via': 'token'}
        return {'owner': False, 'via': 'anon', 'address': None}

    def _send(self, code, body, ctype='application/json'):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _route(self, path):
        for pre in (f'{PREFIX}/api/', f'/api{PREFIX}/', '/api/'):
            if path.startswith(pre):
                return path[len(pre):].strip('/')
        return None

    def _args(self):
        q = {k: v[-1] for k, v in parse_qs(urlparse(self.path).query).items()}
        if self.command in ('POST', 'PUT'):
            n = int(self.headers.get('Content-Length') or 0)
            if n > MAX_BODY:
                raise ValueError('body too large')
            raw = self.rfile.read(n) if n else b''
            if raw:
                d = json.loads(raw)
                if isinstance(d, dict):
                    q.update(d)
        return q

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ('/', PREFIX, PREFIX + '/', '/app', PREFIX + '/index.html'):
            if path == PREFIX:
                self.send_response(308)
                self.send_header('Location', PREFIX + '/')
                self.send_header('Content-Length', '0')
                return self.end_headers()
            accept = self.headers.get('Accept', '')
            if path == '/' and 'text/html' not in accept:
                return self._json({'ok': True, 'name': 'agenti', 'version': VERSION, 'app': PREFIX + '/'})
            return self._send(200, WEB.read_bytes(), 'text/html; charset=utf-8')
        if path in ('/health', PREFIX + '/health'):
            return self._json(FNS['health'][0]({}, None))
        qr = f'{PREFIX}/qr/'
        if path.startswith(qr) or path.startswith('/qr/'):
            who = self._who()
            if not who['owner']:
                return self._json({'error': 'owner only'}, 403)
            try:
                inv = invoices.get(path.rsplit('/', 1)[-1].removesuffix('.svg'))
                return self._send(200, invoices.qr_svg(inv['uri']), 'image/svg+xml')
            except KeyError:
                return self._json({'error': 'no such invoice'}, 404)
        self._api(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ('/mcp', f'{PREFIX}/mcp', f'/api{PREFIX}/mcp'):
            try:
                msg = self._args()
            except Exception as e:  # noqa: BLE001
                return self._json({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': str(e)}}, 400)
            who = self._who()
            if isinstance(msg, list):
                out = [r for r in (mcp(m, who) for m in msg) if r]
            else:
                out = mcp(msg, who)
            if out is None:
                self.send_response(202)
                self.send_header('Content-Length', '0')
                return self.end_headers()
            return self._json(out)
        self._api(path)

    def _api(self, path):
        fn = self._route(path)
        if fn is None or fn not in FNS:
            return self._json({'error': f'no route {path}', 'fns': sorted(FNS)}, 404)
        _, owner_only, write = FNS[fn]
        if write and self.command != 'POST':
            return self._json({'error': f'{fn} is POST'}, 405)
        who = self._who()
        try:
            args = self._args()
            return self._json(call(fn, args, who))
        except PermissionError as e:
            return self._json({'error': str(e), 'owner_only': True}, 403)
        except (KeyError, ValueError, pay.PayError) as e:
            msg = str(e).strip("'\"")
            return self._json({'error': msg}, 404 if isinstance(e, KeyError) else 400)
        except Exception as e:  # noqa: BLE001 — keep 4xx, CF strips 5xx bodies
            traceback.print_exc()
            return self._json({'error': f'{type(e).__name__}: {e}'}, 400)


def serve(port=50129, host='0.0.0.0'):
    srv = ThreadingHTTPServer((host, int(port)), Handler)
    srv.daemon_threads = True
    print(f'agenti on http://{host}:{port}{PREFIX}/', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return {'ok': True}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=50129)
    ap.add_argument('--host', default='0.0.0.0')
    a = ap.parse_args()
    serve(a.port, a.host)
