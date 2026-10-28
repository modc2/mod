"""lhpay — x402 pay-per-upload for lighthouse. No account and no KYC.

An upload spends the deployment's Lighthouse key, so a stranger pays for it
in USDC with the x402 flow:

    1. POST /put (or GET /price?size=N first) → HTTP 402 with `accepts`:
       USDC `exact` offers on the chains this node takes, priced by size.
    2. The caller signs an EIP-3009 transferWithAuthorization for that amount
       (no gas; any EVM wallet) and retries with `X-PAYMENT: <base64 json>`.
    3. The facilitator VERIFIES before the bytes go to Lighthouse and SETTLES
       after the CID came back — a failed upload is never charged. The receipt
       rides back in `X-PAYMENT-RESPONSE`.

Who does not pay:
    local     a request from this box (loopback, nothing forwarded) — the owner
    owner     a mod-protocol token for the owner address
    byok      a caller spending their own Lighthouse key (x-lh-key)

Nothing here holds a key: the facilitator settles, `pay_to` receives. Owner
overrides live off-chain in ~/.mod/lighthouse/pay.json, the ledger in pay.db.
Adapted from orbit/jev/jevpay.py (same wire format, same facilitator).
"""

import base64
import json
import math
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.expanduser(os.environ.get('LIGHTHOUSE_DIR') or '~/.mod/lighthouse')
POLICY = os.path.join(STATE, 'pay.json')
DB = os.path.join(STATE, 'pay.db')
UA = 'mod-lighthouse/1.2 (x402 seller)'
MIB = 1 << 20

DEFAULTS = {
    'enabled': True,              # charge callers that are not exempt
    'pay_to': '',                 # receiving EVM address (env LIGHTHOUSE_PAY_TO wins)
    'networks': ['base'],         # chains offered in the 402
    'facilitator': 'https://facilitator.payai.network',
    'price_usd': 0.001,           # per upload
    'price_per_mib_usd': 0.001,   # plus this per started MiB
    'max_mb': 100,                # largest paid upload
    'timeout_s': 300,             # how long a signed payment stays valid
    'description': 'lighthouse: store a file forever on IPFS + Filecoin',
}

# USDC per chain; `name`/`version` are the token's EIP-712 domain.
ASSETS = {
    'base':         {'chain_id': 8453,  'asset': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', 'name': 'USD Coin', 'version': '2'},
    'base-sepolia': {'chain_id': 84532, 'asset': '0x036CbD53842c5426634e7929541eC2318f3dCF7e', 'name': 'USDC',     'version': '2'},
    'polygon':      {'chain_id': 137,   'asset': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359', 'name': 'USD Coin', 'version': '2'},
    'arbitrum':     {'chain_id': 42161, 'asset': '0xaf88d065e77c8cC2239327C5EDb3A432268e5831', 'name': 'USD Coin', 'version': '2'},
    'optimism':     {'chain_id': 10,    'asset': '0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85', 'name': 'USD Coin', 'version': '2'},
    'avalanche':    {'chain_id': 43114, 'asset': '0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E', 'name': 'USD Coin', 'version': '2'},
}
DECIMALS = 6
LOOPBACK = ('127.0.0.1', '::1', 'localhost', 'testclient')

_lock = threading.RLock()
_local = threading.local()


class PayError(Exception):
    pass


# ── policy ────────────────────────────────────────────────────────

def _config_pay():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('pay') or {}
    except Exception:                                     # noqa: BLE001
        return {}


def policy():
    """defaults < config.json `pay` < ~/.mod/lighthouse/pay.json < env."""
    p = {**DEFAULTS, **{k: v for k, v in _config_pay().items() if k in DEFAULTS}}
    try:
        with open(POLICY) as f:
            p.update({k: v for k, v in (json.load(f) or {}).items() if k in DEFAULTS})
    except Exception:                                     # noqa: BLE001
        pass
    if os.environ.get('LIGHTHOUSE_PAY_TO'):
        p['pay_to'] = os.environ['LIGHTHOUSE_PAY_TO']
    if os.environ.get('LIGHTHOUSE_FREE') == '1':
        p['enabled'] = False
    if os.environ.get('LIGHTHOUSE_FACILITATOR'):
        p['facilitator'] = os.environ['LIGHTHOUSE_FACILITATOR']
    nets = p.get('networks') or ['base']
    if isinstance(nets, str):
        nets = [n.strip() for n in nets.split(',') if n.strip()]
    p['networks'] = [n for n in nets if n in ASSETS] or ['base']
    return p


def set_policy(**changes):
    """Owner: persist overrides off-chain. Unknown keys are refused; None clears."""
    bad = [k for k in changes if k not in DEFAULTS]
    if bad:
        raise PayError(f'unknown policy keys: {", ".join(bad)} (have {", ".join(DEFAULTS)})')
    cur = {}
    try:
        with open(POLICY) as f:
            cur = json.load(f) or {}
    except Exception:                                     # noqa: BLE001
        pass
    for k, v in changes.items():
        if v is None:
            cur.pop(k, None)
            continue
        if k == 'enabled':
            v = v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'yes', 'on')
        elif k in ('price_usd', 'price_per_mib_usd'):
            v = float(v)
        elif k in ('max_mb', 'timeout_s'):
            v = int(v)
        elif k == 'networks' and isinstance(v, str):
            v = [n.strip() for n in v.split(',') if n.strip()]
        elif k == 'pay_to':
            v = str(v)
            if not (v.startswith('0x') and len(v) == 42):
                raise PayError('pay_to must be a 0x EVM address')
        cur[k] = v
    os.makedirs(STATE, exist_ok=True)
    tmp = POLICY + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cur, f, indent=2)
    os.replace(tmp, POLICY)
    return policy()


def active(p=None):
    p = p or policy()
    return bool(p['enabled']) and str(p.get('pay_to') or '').startswith('0x')


def units(usd_):
    return max(0, int(round(float(usd_) * 10 ** DECIMALS)))


def usd(u):
    return round(int(u) / 10 ** DECIMALS, DECIMALS)


def cost(size, p=None):
    """USDC units for an upload of `size` bytes: base + per started MiB."""
    p = p or policy()
    mib = max(1, math.ceil(max(0, int(size)) / MIB))
    return units(p['price_usd']) + mib * units(p['price_per_mib_usd'])


def public(p=None):
    """The policy as a caller may see it."""
    p = p or policy()
    return {
        'enabled': active(p),
        'protocol': 'x402',
        'x402Version': 1,
        'scheme': 'exact',
        'currency': 'USDC',
        'decimals': DECIMALS,
        'price_usd': p['price_usd'],
        'price_per_mib_usd': p['price_per_mib_usd'],
        'max_mb': p['max_mb'],
        'networks': [{'network': n, **ASSETS[n]} for n in p['networks']],
        'pay_to': p['pay_to'],
        'facilitator': p['facilitator'],
        'kyc': 'none',
        'free_for': ['this box (loopback)', 'the owner token',
                     'a caller spending their own Lighthouse key (x-lh-key)'],
        'how': 'POST /put (or GET /price?size=N) → 402 `accepts`; sign the EIP-3009 '
               'USDC transfer it describes; retry with X-PAYMENT. Verified before the '
               'upload, settled after it — a failed upload is never charged.',
    }


# ── the 402 ───────────────────────────────────────────────────────

def requirement(network, amount_units, resource, p=None):
    p = p or policy()
    a = ASSETS[network]
    return {
        'scheme': 'exact',
        'network': network,
        'maxAmountRequired': str(int(amount_units)),
        'resource': resource,
        'description': p['description'],
        'mimeType': 'application/json',
        'payTo': p['pay_to'],
        'maxTimeoutSeconds': int(p['timeout_s']),
        'asset': a['asset'],
        'extra': {'name': a['name'], 'version': a['version']},
    }


def accepts(amount_units, resource, p=None):
    p = p or policy()
    return [requirement(n, amount_units, resource, p) for n in p['networks']]


def required(amount_units, resource, error=None, p=None, **info):
    """The body of a 402: spec `accepts` plus the price in plain dollars."""
    p = p or policy()
    return {
        'x402Version': 1,
        'error': error or 'payment required',
        'accepts': accepts(amount_units, resource, p),
        'price': {'usd': usd(amount_units), 'units': int(amount_units), 'currency': 'USDC',
                  'per_upload_usd': p['price_usd'], 'per_mib_usd': p['price_per_mib_usd'], **info},
        'kyc': 'none',
    }


def decode(value):
    try:
        s = str(value or '').strip()
        d = json.loads(base64.b64decode(s + '=' * (-len(s) % 4)))
        return d if isinstance(d, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def payment_header(headers):
    h = {str(k).lower(): v for k, v in (headers or {}).items()}
    return h.get('x-payment') or h.get('payment-signature') or ''


def authorization(payment):
    return ((payment.get('payload') or {}).get('authorization') or {}) if isinstance(payment, dict) else {}


def _network_name(net):
    n = str(net or '')
    if n.startswith('eip155:'):
        for name, a in ASSETS.items():
            if a['chain_id'] == int(n.split(':')[1] or 0):
                return name
    return n


def match(payment, offers):
    """The offer a payment is for: same scheme + network, to our address, >= amount."""
    if not payment:
        return None
    acc = payment.get('accepted') if isinstance(payment.get('accepted'), dict) else None
    net = (acc or payment).get('network')
    scheme = (acc or payment).get('scheme') or 'exact'
    auth = authorization(payment)
    for o in offers:
        if o['scheme'] != scheme or o['network'] != _network_name(net):
            continue
        if str(auth.get('to', '')).lower() != o['payTo'].lower():
            continue
        try:
            if int(auth.get('value', 0)) < int(o['maxAmountRequired']):
                continue
        except (TypeError, ValueError):
            continue
        return o
    return None


# ── facilitator ───────────────────────────────────────────────────

def facilitator_post(path, body, p=None, timeout=30):
    """POST to the facilitator → (status, json). Monkeypatched by tests."""
    p = p or policy()
    req = urllib.request.Request(
        p['facilitator'].rstrip('/') + path, method='POST', data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, _json(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read() or b'')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PayError(f'facilitator unreachable: {getattr(e, "reason", e)}') from e


def _json(b):
    try:
        return json.loads(b or b'{}')
    except json.JSONDecodeError:
        return {'raw': b.decode('utf-8', 'replace')[:400]}


def _fac_body(raw_header, payment, req):
    body = {'x402Version': int(payment.get('x402Version') or 1),
            'paymentPayload': payment, 'paymentRequirements': req}
    if body['x402Version'] == 1:
        body['paymentHeader'] = raw_header
    return body


def verify(raw_header, payment, req, p=None):
    """→ (ok, reason, payer)."""
    status, r = facilitator_post('/verify', _fac_body(raw_header, payment, req), p)
    ok = bool(r.get('isValid', r.get('valid')))
    reason = r.get('invalidReason') or r.get('error') or r.get('reason') or ('' if ok else f'http {status}')
    payer = r.get('payer') or authorization(payment).get('from') or ''
    return ok and status < 400, str(reason), str(payer)


def settle(raw_header, payment, req, p=None):
    status, r = facilitator_post('/settle', _fac_body(raw_header, payment, req), p)
    ok = bool(r.get('success')) and status < 400
    return {'success': ok,
            'transaction': r.get('transaction') or r.get('txHash') or r.get('tx') or '',
            'network': r.get('network') or req.get('network'),
            'payer': r.get('payer') or authorization(payment).get('from') or '',
            'errorReason': '' if ok else str(r.get('errorReason') or r.get('error') or f'http {status}')}


def receipt_header(receipt):
    return base64.b64encode(json.dumps(receipt, separators=(',', ':')).encode()).decode()


# ── ledger ────────────────────────────────────────────────────────

def db():
    c = getattr(_local, 'db', None)
    if c is None or getattr(_local, 'path', None) != DB:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
        c = sqlite3.connect(DB, timeout=10, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS payments (
                nonce TEXT PRIMARY KEY, payer TEXT, network TEXT, units INTEGER,
                tx TEXT, cid TEXT, size INTEGER, at REAL);
        ''')
        _local.db, _local.path = c, DB
    return c


def reset(home=None):
    """Point state at another directory (tests)."""
    global STATE, POLICY, DB
    if home:
        STATE = os.path.expanduser(home)
        POLICY = os.path.join(STATE, 'pay.json')
        DB = os.path.join(STATE, 'pay.db')
    _local.db = None


def seen(nonce):
    with _lock:
        return bool(db().execute('SELECT 1 FROM payments WHERE nonce=?', (str(nonce),)).fetchone())


def record(payment, req, receipt, cid='', size=0):
    auth = authorization(payment)
    with _lock:
        c = db()
        c.execute('INSERT OR REPLACE INTO payments VALUES (?,?,?,?,?,?,?,?)',
                  (str(auth.get('nonce') or secrets.token_hex(16)), str(receipt.get('payer', '')).lower(),
                   req['network'], int(auth.get('value') or req['maxAmountRequired']),
                   receipt.get('transaction', ''), cid, int(size or 0), time.time()))
        c.commit()


def payments(limit=100):
    with _lock:
        rows = db().execute('SELECT * FROM payments ORDER BY at DESC LIMIT ?', (int(limit),)).fetchall()
    return [{**dict(r), 'usd': usd(r['units'])} for r in rows]


def stats():
    with _lock:
        r = db().execute('SELECT COUNT(*) n, COALESCE(SUM(units),0) u, COALESCE(SUM(size),0) b '
                         'FROM payments').fetchone()
    return {'payments': r['n'], 'received_usd': usd(r['u']), 'paid_bytes': r['b']}


# ── who pays ──────────────────────────────────────────────────────

def is_local(client_host, headers):
    """Loopback with nothing forwarded from further away. The console's app
    server and caddy both add X-Forwarded-For, so a visitor arriving through
    either is never mistaken for the box."""
    if str(client_host or '') not in LOOPBACK:
        return False
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if h.get('cf-connecting-ip') or h.get('x-real-ip'):
        return False
    fwd = [x.strip() for x in h.get('x-forwarded-for', '').split(',') if x.strip()]
    return all(x in LOOPBACK for x in fwd)


# ── the gate ──────────────────────────────────────────────────────

class Charge:
    """One upload's payment, split around the upload itself:

        c = Charge(headers, size, resource, exempt=...)
        if c.refusal: return 402/503 with c.refusal      # before any bytes move
        ... upload ...
        info = c.settle(cid)                             # after the CID came back

    `exempt` is the caller's reason not to pay (local/owner/byok) or ''.
    """

    def __init__(self, headers, size, resource, exempt=''):
        self.p = policy()
        self.size = int(size)
        self.resource = resource
        self.units = cost(self.size, self.p)
        self.raw = payment_header(headers)
        self.payment = None
        self.req = None
        self.payer = ''
        self.refusal = None        # (status, body)
        self.mode = 'free'
        self.exempt = exempt
        if not active(self.p) or exempt:
            return
        self.mode = 'x402'
        limit = int(self.p['max_mb']) * MIB
        if self.size > limit:
            self.refusal = (413, {'error': f'too large: {self.size} bytes > {self.p["max_mb"]} MiB per paid upload',
                                  'code': 'too_large'})
            return
        info = {'size': self.size}
        if not self.raw:
            self.refusal = (402, required(self.units, resource, None, self.p, **info))
            return
        self.payment = decode(self.raw)
        self.req = match(self.payment, accepts(self.units, resource, self.p))
        if not self.req:
            self.refusal = (402, required(self.units, resource, 'payment does not match an offer '
                                          '(scheme, network, payTo or amount)', self.p, **info))
            return
        nonce = authorization(self.payment).get('nonce')
        if nonce and seen(nonce):
            self.refusal = (402, required(self.units, resource, 'payment already used', self.p, **info))
            return
        try:
            ok, reason, self.payer = verify(self.raw, self.payment, self.req, self.p)
        except PayError as e:
            self.refusal = (503, {'error': str(e), 'code': 'facilitator'})
            return
        if not ok:
            self.refusal = (402, required(self.units, resource, f'payment invalid: {reason}', self.p, **info))

    def settle(self, cid=''):
        """→ (payment info for the result, extra response headers)."""
        if self.mode == 'free':
            return {'mode': 'free', **({'why': self.exempt} if self.exempt else {})}, {}
        try:
            rec = settle(self.raw, self.payment, self.req, self.p)
        except PayError as e:
            rec = {'success': False, 'errorReason': str(e), 'payer': self.payer,
                   'network': self.req['network'], 'transaction': ''}
        if rec['success']:
            record(self.payment, self.req, rec, cid, self.size)
        info = {'mode': 'x402', 'settled': rec['success'], 'usd': usd(self.units),
                'payer': rec.get('payer'), 'network': rec.get('network'), 'tx': rec.get('transaction')}
        if not rec['success']:
            info['error'] = rec['errorReason']
        return info, {'X-PAYMENT-RESPONSE': receipt_header(rec)}

    def abandon(self):
        """The upload failed: nothing is settled, so nothing is charged."""
        return {'mode': self.mode, 'settled': False, 'note': 'not charged: the upload failed'}
