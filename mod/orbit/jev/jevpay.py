"""jevpay — crypto pay-per-question for jev, no account and no KYC.

Three ways a call gets through the gate, checked in this order:

    local     a request from this box itself (loopback, nothing forwarded)
              is the owner's: free, like the CLI.
    x402      the caller pays per call: the reply to an unpaid request is a
              standard HTTP 402 with `accepts` offers (USDC on Base & co.);
              the caller signs an EIP-3009 transfer for exactly the price
              and retries with `X-PAYMENT`. The facilitator verifies before
              the question runs and settles after it answered — a failed
              answer is never charged.
    credits   the caller prepaid: `POST /topup` with any X-PAYMENT of at
              least min_topup_usd credits the PAYING address (the payment
              is the proof, no sign-in). Spending needs a mod-protocol token
              (a wallet signature, `token:` header) for that address, so
              the ask itself costs one signature and no on-chain tx.

Identity is the mod protocol's: a wallet address proven by a signed token.
Nothing here holds a key: the facilitator settles, the owner's address
receives. Private state lives in ~/.mod/jev (pay.json overrides, pay.db
ledger) — never in config.json. Stdlib + eth_account (token recovery).

A `Gate` object exposes `gate_check(fn, headers)` so the protocol's own
serve/gate can mount the same rules: ``m.serve('jev', paywall=jev.paywall)``.
"""

import base64
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.expanduser(os.environ.get('JEV_HOME') or '~/.mod/jev')
POLICY = os.path.join(STATE, 'pay.json')
DB = os.path.join(STATE, 'pay.db')
TOKEN_MAX_AGE = 7 * 86400
UA = 'mod-jev/0.2 (x402 seller)'

DEFAULTS = {
    'enabled': True,            # charge callers that are not this box
    'free_local': True,         # loopback = the owner, never charged
    'pay_to': '',               # receiving EVM address (env JEV_PAY_TO wins)
    'networks': ['base'],       # chains offered in the 402
    'facilitator': 'https://facilitator.payai.network',
    'price_usd': 0.001,         # per question
    'min_topup_usd': 0.25,      # smallest credit purchase
    'max_questions': 32,        # per call
    'timeout_s': 300,           # how long a signed payment stays valid
    'description': 'jev: typed questions over the Jev model (TypeSafe System One)',
}

# USDC on each chain this node will accept. `name`/`version` are the token's
# EIP-712 domain, which the payer's wallet signs against.
ASSETS = {
    'base':         {'chain_id': 8453,  'asset': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', 'name': 'USD Coin', 'version': '2'},
    'base-sepolia': {'chain_id': 84532, 'asset': '0x036CbD53842c5426634e7929541eC2318f3dCF7e', 'name': 'USDC',     'version': '2'},
    'polygon':      {'chain_id': 137,   'asset': '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359', 'name': 'USD Coin', 'version': '2'},
    'arbitrum':     {'chain_id': 42161, 'asset': '0xaf88d065e77c8cC2239327C5EDb3A432268e5831', 'name': 'USD Coin', 'version': '2'},
    'optimism':     {'chain_id': 10,    'asset': '0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85', 'name': 'USD Coin', 'version': '2'},
    'avalanche':    {'chain_id': 43114, 'asset': '0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E', 'name': 'USD Coin', 'version': '2'},
}
DECIMALS = 6

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
    """Effective policy: defaults < config.json `pay` (public knobs) <
    ~/.mod/jev/pay.json (the owner's overrides) < env."""
    p = {**DEFAULTS, **_config_pay()}
    try:
        with open(POLICY) as f:
            p.update(json.load(f) or {})
    except Exception:                                     # noqa: BLE001
        pass
    if os.environ.get('JEV_PAY_TO'):
        p['pay_to'] = os.environ['JEV_PAY_TO']
    if os.environ.get('JEV_FREE') == '1':
        p['enabled'] = False
    if os.environ.get('JEV_FACILITATOR'):
        p['facilitator'] = os.environ['JEV_FACILITATOR']
    nets = p.get('networks') or ['base']
    if isinstance(nets, str):
        nets = [n.strip() for n in nets.split(',') if n.strip()]
    p['networks'] = [n for n in nets if n in ASSETS] or ['base']
    return p


def set_policy(**changes):
    """Owner: persist overrides. Unknown keys are refused."""
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
        if k in ('enabled', 'free_local'):
            v = str(v).lower() in ('1', 'true', 'yes', 'on') if not isinstance(v, bool) else v
        elif k in ('price_usd', 'min_topup_usd'):
            v = float(v)
        elif k in ('max_questions', 'timeout_s'):
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


def public(p=None):
    """The policy as a caller may see it: how much, where, on which chains."""
    p = p or policy()
    return {
        'enabled': bool(p['enabled']) and bool(p['pay_to']),
        'price_usd': p['price_usd'],
        'price_units': units(p['price_usd']),
        'currency': 'USDC',
        'decimals': DECIMALS,
        'networks': [{'network': n, **ASSETS[n]} for n in p['networks']],
        'pay_to': p['pay_to'],
        'facilitator': p['facilitator'],
        'min_topup_usd': p['min_topup_usd'],
        'max_questions': p['max_questions'],
        'kyc': 'none',
        'how': {
            'x402': 'call a paid route; on 402 sign the `accepts` offer (EIP-3009 USDC) and retry with X-PAYMENT',
            'credits': 'POST /topup with an X-PAYMENT of any amount >= min_topup_usd; the paying address is credited; '
                       'spend with a mod-protocol token (`token:` header) for that address',
            'local': 'calls from this box (CLI, loopback) are the owner\'s and free' if p['free_local'] else 'local calls pay too',
        },
    }


def units(usd):
    return max(0, int(round(float(usd) * 10 ** DECIMALS)))


def usd(u):
    return round(int(u) / 10 ** DECIMALS, DECIMALS)


# ── the 402 ───────────────────────────────────────────────────────

def requirement(network, amount_units, resource, description=None, p=None):
    """One x402 v1 PaymentRequirements entry."""
    p = p or policy()
    a = ASSETS[network]
    return {
        'scheme': 'exact',
        'network': network,
        'maxAmountRequired': str(int(amount_units)),
        'resource': resource,
        'description': description or p['description'],
        'mimeType': 'application/json',
        'payTo': p['pay_to'],
        'maxTimeoutSeconds': int(p['timeout_s']),
        'asset': a['asset'],
        'extra': {'name': a['name'], 'version': a['version']},
    }


def accepts(amount_units, resource, description=None, p=None):
    p = p or policy()
    return [requirement(n, amount_units, resource, description, p) for n in p['networks']]


def required(amount_units, resource, description=None, error=None, p=None, **info):
    """The body of a 402: spec `accepts` plus what this node adds (price in
    USD, the credits alternative)."""
    p = p or policy()
    return {
        'x402Version': 1,
        'error': error or 'payment required',
        'accepts': accepts(amount_units, resource, description, p),
        'price': {'usd': usd(amount_units), 'units': int(amount_units), 'currency': 'USDC',
                  'per_question_usd': p['price_usd'], **info},
        'credits': {'topup': 'POST /jev/api/topup with X-PAYMENT (>= $%s); spend with a `token:` header'
                             % p['min_topup_usd'], 'min_usd': p['min_topup_usd']},
        'kyc': 'none',
    }


def decode(value):
    """Base64 JSON header → dict ({} when it is not one)."""
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


def match(payment, offers):
    """The offer a payment is for: same scheme + network, paying our address
    at least the amount. v2 payments carry `accepted` and are matched on it."""
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


def _network_name(net):
    n = str(net or '')
    if n.startswith('eip155:'):
        for name, a in ASSETS.items():
            if a['chain_id'] == int(n.split(':')[1] or 0):
                return name
    return n


# ── facilitator ───────────────────────────────────────────────────

def facilitator_post(path, body, p=None, timeout=30):
    """POST to the facilitator → (status, json). Monkeypatched by tests."""
    p = p or policy()
    req = urllib.request.Request(
        p['facilitator'].rstrip('/') + path, method='POST',
        data=json.dumps(body).encode(),
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
    """→ receipt dict {success, transaction, network, payer, errorReason}."""
    status, r = facilitator_post('/settle', _fac_body(raw_header, payment, req), p)
    ok = bool(r.get('success')) and status < 400
    return {'success': ok,
            'transaction': r.get('transaction') or r.get('txHash') or r.get('tx') or '',
            'network': r.get('network') or req.get('network'),
            'payer': r.get('payer') or authorization(payment).get('from') or '',
            'errorReason': '' if ok else str(r.get('errorReason') or r.get('error') or f'http {status}')}


def receipt_header(receipt):
    return base64.b64encode(json.dumps(receipt, separators=(',', ':')).encode()).decode()


# ── identity: the mod protocol's token ───────────────────────────

def _b64url(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def verify_token(token, max_age=TOKEN_MAX_AGE):
    """Address a mod-protocol token proves (lowercase), or '' for no proof.
    Accepts the browser/Rust form (EIP-191 personal_sign) and the Python
    key's raw keccak signature; `data` may be a string or an object."""
    try:
        claim = json.loads(_b64url(str(token).strip()))
        data, t, key, sig = claim['data'], claim['time'], claim['key'], claim['signature']
        if abs(time.time() - float(t)) > max_age:
            return ''
        msg = json.dumps({'data': data, 'time': t}, separators=(',', ':'))
        from eth_account import Account
        from eth_account.messages import encode_defunct
        from eth_utils import keccak
        raw = bytes.fromhex(str(sig).removeprefix('0x'))
        if len(raw) != 65:
            return ''
        v = raw[64]
        rsv = raw[:64] + bytes([v + 27 if v < 27 else v])
        want = str(key).lower()
        for recover in (lambda: Account.recover_message(encode_defunct(text=msg), signature=rsv),
                        lambda: Account._recover_hash(keccak(text=msg), signature=rsv)):
            try:
                if recover().lower() == want:
                    return want
            except Exception:                             # noqa: BLE001 — try the other form
                continue
    except Exception:                                     # noqa: BLE001 — no proof is not an error
        return ''
    return ''


def identify(headers=None, local=False):
    """Request headers → {'id', 'kind'}: local | wallet | anon."""
    h = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    auth = h.get('authorization', '')
    bearer = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    for tok in (h.get('token'), h.get('x-mod-token'), bearer):
        addr = verify_token(tok) if tok else ''
        if addr:
            return {'id': addr, 'kind': 'wallet'}
    if local:
        return {'id': 'local', 'kind': 'local'}
    return {'id': 'anon', 'kind': 'anon'}


# ── ledger ────────────────────────────────────────────────────────

def db():
    c = getattr(_local, 'db', None)
    if c is None:
        os.makedirs(os.path.dirname(DB), exist_ok=True)
        c = sqlite3.connect(DB, timeout=10, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS payments (
                nonce TEXT PRIMARY KEY, payer TEXT, network TEXT, units INTEGER,
                tx TEXT, kind TEXT, resource TEXT, at REAL);
            CREATE TABLE IF NOT EXISTS credits (addr TEXT PRIMARY KEY, units INTEGER, updated REAL);
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY, at REAL, addr TEXT, delta INTEGER, kind TEXT, ref TEXT);
            CREATE INDEX IF NOT EXISTS ledger_addr ON ledger(addr, at);
        ''')
        _local.db = c
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


def record_payment(payment, req, receipt, kind, resource):
    auth = authorization(payment)
    with _lock:
        c = db()
        c.execute('INSERT OR REPLACE INTO payments VALUES (?,?,?,?,?,?,?,?)',
                  (str(auth.get('nonce') or secrets.token_hex(16)), receipt.get('payer', '').lower(),
                   req['network'], int(auth.get('value') or req['maxAmountRequired']),
                   receipt.get('transaction', ''), kind, resource, time.time()))
        c.commit()


def balance(addr):
    with _lock:
        r = db().execute('SELECT units FROM credits WHERE addr=?', (str(addr).lower(),)).fetchone()
    u = int(r['units']) if r else 0
    return {'address': str(addr).lower(), 'units': u, 'usd': usd(u), 'questions': u // max(1, units(policy()['price_usd']))}


def credit(addr, delta_units, kind, ref=''):
    addr = str(addr).lower()
    with _lock:
        c = db()
        c.execute('INSERT INTO credits (addr, units, updated) VALUES (?, ?, ?)'
                  ' ON CONFLICT(addr) DO UPDATE SET units=units+excluded.units, updated=excluded.updated',
                  (addr, int(delta_units), time.time()))
        c.execute('INSERT INTO ledger (at, addr, delta, kind, ref) VALUES (?,?,?,?,?)',
                  (time.time(), addr, int(delta_units), kind, ref))
        c.commit()
    return balance(addr)


def debit(addr, delta_units, ref=''):
    """Atomic spend; raises when the balance is short."""
    addr = str(addr).lower()
    with _lock:
        c = db()
        r = c.execute('SELECT units FROM credits WHERE addr=?', (addr,)).fetchone()
        have = int(r['units']) if r else 0
        if have < int(delta_units):
            raise PayError(f'insufficient credits: have ${usd(have)}, need ${usd(delta_units)}')
        c.execute('UPDATE credits SET units=units-?, updated=? WHERE addr=?', (int(delta_units), time.time(), addr))
        c.execute('INSERT INTO ledger (at, addr, delta, kind, ref) VALUES (?,?,?,?,?)',
                  (time.time(), addr, -int(delta_units), 'spend', ref))
        c.commit()
    return balance(addr)


def ledger(addr=None, limit=100):
    with _lock:
        q = 'SELECT at, addr, delta, kind, ref FROM ledger' + (' WHERE addr=?' if addr else '') + ' ORDER BY id DESC LIMIT ?'
        rows = db().execute(q, ((str(addr).lower(), int(limit)) if addr else (int(limit),))).fetchall()
    return [{**dict(r), 'usd': usd(r['delta'])} for r in rows]


def payments(limit=100):
    with _lock:
        rows = db().execute('SELECT * FROM payments ORDER BY at DESC LIMIT ?', (int(limit),)).fetchall()
    return [{**dict(r), 'usd': usd(r['units'])} for r in rows]


def stats():
    with _lock:
        c = db()
        paid = c.execute('SELECT COUNT(*) n, COALESCE(SUM(units),0) u FROM payments').fetchone()
        cred = c.execute('SELECT COUNT(*) n, COALESCE(SUM(units),0) u FROM credits').fetchone()
        spent = c.execute("SELECT COALESCE(-SUM(delta),0) u FROM ledger WHERE kind='spend'").fetchone()
    return {'payments': paid['n'], 'received_usd': usd(paid['u']),
            'credit_accounts': cred['n'], 'credits_outstanding_usd': usd(cred['u']),
            'credits_spent_usd': usd(spent['u'])}


# ── the gate ──────────────────────────────────────────────────────

def _pay_to_ok(p):
    return bool(p.get('pay_to')) and str(p['pay_to']).startswith('0x')


def quote(n_questions, p=None):
    p = p or policy()
    n = int(n_questions)
    if n < 1:
        raise PayError('at least one question')
    if n > int(p['max_questions']):
        raise PayError(f'at most {p["max_questions"]} questions per call')
    return n * units(p['price_usd'])


def charge(headers, n_questions, resource, run, local=False, description=None):
    """Run `run()` if the caller may: → (http_status, body, extra_headers).

    Payment outcomes are attached to the body under `payment` so a client
    (and the ledger) can see what happened: free | credits | x402."""
    p = policy()
    who = identify(headers, local=local)
    extra = {}
    try:
        cost = quote(n_questions, p)
    except PayError as e:
        return 400, {'error': str(e), 'code': 'invalid_request'}, extra

    if not p['enabled'] or not _pay_to_ok(p) or (local and p['free_local']):
        out = run()
        return _status(out), {**out, 'payment': {'mode': 'free', 'who': who['id']}}, extra

    raw = payment_header(headers)
    if raw:
        payment = decode(raw)
        offers = accepts(cost, resource, description, p)
        req = match(payment, offers)
        if not req:
            return 402, required(cost, resource, description, 'payment does not match an offer '
                                 '(scheme, network, payTo or amount)', p, questions=n_questions), extra
        nonce = authorization(payment).get('nonce')
        if nonce and seen(nonce):
            return 402, required(cost, resource, description, 'payment already used', p, questions=n_questions), extra
        try:
            ok, reason, payer = verify(raw, payment, req, p)
        except PayError as e:
            return 503, {'error': str(e), 'code': 'facilitator'}, extra
        if not ok:
            return 402, required(cost, resource, description, f'payment invalid: {reason}', p, questions=n_questions), extra
        out = run()
        if 'error' in out:
            return _status(out), {**out, 'payment': {'mode': 'x402', 'settled': False,
                                                      'note': 'not charged: the question failed'}}, extra
        try:
            rec = settle(raw, payment, req, p)
        except PayError as e:
            rec = {'success': False, 'errorReason': str(e), 'payer': payer, 'network': req['network'], 'transaction': ''}
        if rec['success']:
            record_payment(payment, req, rec, 'call', resource)
        extra['X-PAYMENT-RESPONSE'] = receipt_header(rec)
        return 200, {**out, 'payment': {'mode': 'x402', 'settled': rec['success'], 'payer': rec.get('payer'),
                                        'network': rec.get('network'), 'tx': rec.get('transaction'),
                                        'usd': usd(cost), **({'error': rec['errorReason']} if not rec['success'] else {})}}, extra

    if who['kind'] == 'wallet':
        have = balance(who['id'])['units']
        if have >= cost:
            out = run()
            if 'error' in out:
                return _status(out), {**out, 'payment': {'mode': 'credits', 'charged': False}}, extra
            left = debit(who['id'], cost, resource)
            extra['X-Jev-Credits-Left'] = str(left['usd'])
            return 200, {**out, 'payment': {'mode': 'credits', 'usd': usd(cost), 'left_usd': left['usd'],
                                            'address': who['id']}}, extra
        return 402, required(cost, resource, description,
                             f'insufficient credits for {who["id"]}: ${usd(have)} < ${usd(cost)}', p,
                             questions=n_questions, balance_usd=usd(have)), extra

    return 402, required(cost, resource, description, None, p, questions=n_questions), extra


def topup(headers, resource='topup'):
    """Credit the PAYING address with whatever an X-PAYMENT carries (>= min).
    → (status, body, extra_headers)."""
    p = policy()
    if not p['enabled'] or not _pay_to_ok(p):
        return 400, {'error': 'this node does not charge, nothing to top up', 'code': 'free'}, {}
    raw = payment_header(headers)
    floor = units(p['min_topup_usd'])
    if not raw:
        return 402, required(floor, resource, 'jev credits', 'send X-PAYMENT for the amount to credit', p,
                             note='any amount >= min_topup_usd; the paying address is credited'), {}
    payment = decode(raw)
    auth = authorization(payment)
    try:
        value = int(auth.get('value') or 0)
    except (TypeError, ValueError):
        value = 0
    if value < floor:
        return 402, required(floor, resource, 'jev credits', f'top-up below the minimum (${p["min_topup_usd"]})', p), {}
    net = _network_name((payment.get('accepted') or payment).get('network'))
    if net not in p['networks']:
        return 402, required(floor, resource, 'jev credits', f'network {net} not accepted', p), {}
    req = requirement(net, value, resource, 'jev credits', p)
    if str(auth.get('to', '')).lower() != req['payTo'].lower():
        return 402, required(floor, resource, 'jev credits', 'payment is not to this node\'s address', p), {}
    if auth.get('nonce') and seen(auth['nonce']):
        return 402, required(floor, resource, 'jev credits', 'payment already used', p), {}
    try:
        ok, reason, payer = verify(raw, payment, req, p)
        if not ok:
            return 402, required(floor, resource, 'jev credits', f'payment invalid: {reason}', p), {}
        rec = settle(raw, payment, req, p)
    except PayError as e:
        return 503, {'error': str(e), 'code': 'facilitator'}, {}
    if not rec['success']:
        return 402, required(floor, resource, 'jev credits', f'settlement failed: {rec["errorReason"]}', p), {}
    payer = (rec.get('payer') or auth.get('from') or '').lower()
    record_payment(payment, req, rec, 'topup', resource)
    bal = credit(payer, value, 'topup', rec.get('transaction', ''))
    return 200, {'credited_usd': usd(value), 'tx': rec.get('transaction'), 'network': net, **bal,
                 'spend': 'send a mod-protocol token for this address in the `token:` header'}, \
        {'X-PAYMENT-RESPONSE': receipt_header(rec)}


def _status(out):
    if 'error' not in out:
        return 200
    return {'authentication': 502, 'permission_denied': 502, 'rate_limit': 429, 'overloaded': 503,
            'timeout': 504, 'connection': 502, 'invalid_request': 400, 'invalid_config': 500,
            'bridge': 500, 'build': 500}.get(out.get('code'), 400)


class Gate:
    """`gate_check(fn, headers)` for the mod protocol's own gate/serve: None
    when the call may proceed, a 402 dict otherwise. Payment here is checked
    and settled before the function runs (the gate has no after-hook)."""

    PAID = ('ask', 'noul', 'choice', 'score')

    def __init__(self, resource='jev'):
        self.resource = resource

    def gate_check(self, fn, headers):
        name = str(fn).rsplit('/', 1)[-1]
        if name not in self.PAID:
            return None
        p = policy()
        if not p['enabled'] or not _pay_to_ok(p):
            return None
        who = identify(headers)
        cost = units(p['price_usd'])
        raw = payment_header(headers)
        if raw:
            payment = decode(raw)
            req = match(payment, accepts(cost, f'{self.resource}/{name}', None, p))
            nonce = authorization(payment).get('nonce')
            if req and not (nonce and seen(nonce)):
                try:
                    ok, _, _ = verify(raw, payment, req, p)
                    if ok:
                        rec = settle(raw, payment, req, p)
                        if rec['success']:
                            record_payment(payment, req, rec, 'call', f'{self.resource}/{name}')
                            return None
                except PayError:
                    pass
        elif who['kind'] == 'wallet' and balance(who['id'])['units'] >= cost:
            debit(who['id'], cost, f'{self.resource}/{name}')
            return None
        return {'code': 402, **required(cost, f'{self.resource}/{name}', None, None, p, questions=1)}
