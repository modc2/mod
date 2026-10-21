"""jevrelay — answer jev questions through a public Jev x402 seller.

When this node has no TypeSafe key (or the owner picks `backend: relay`), jev
still answers: the wire body jevgo would send to System One is posted to an
x402 seller that speaks the same body (`{state, questions:{name:{type,...}}}`)
and the answer is mapped back to jev's shape. Two ways the seller gets paid:

    pass-through  the caller pays the SELLER directly. jev's 402 carries the
                  seller's own offer (its payTo and amount, our resource); the
                  caller signs it as usual and retries with X-PAYMENT, and jev
                  forwards that signature (re-wrapped for the seller's x402
                  version). The seller's facilitator verifies and settles —
                  jev holds no money and no key for this path.
    node wallet   the caller is the owner (this box) or spends prepaid
                  credits: jev signs the seller's offer with its own wallet
                  (~/.mod/jev/wallet.json, `m jev/wallet create=1`, fund with
                  USDC on Base). Off until that file exists.

Sellers are tried in order (policy `upstreams`); the first that answers a
402 with a signable USDC offer is used. Stdlib + eth_account for signing.
"""

import base64
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request

import jevpay

UPSTREAMS = [
    'https://jev-x402.vercel.app/jev',          # $0.001/call, base — the busiest Jev seller
    'https://x402.agentindex.world/decide',     # $0.001/call, base, same body
]
WALLET = os.path.join(jevpay.STATE, 'wallet.json')
UA = 'mod-jev/0.3 (x402 relay)'
OFFER_TTL = 120

_lock = threading.RLock()
_offers = {}                    # upstream url -> (fetched_at, 402 doc)
_ctx = threading.local()        # how the current call pays the seller


class RelayError(Exception):
    def __init__(self, msg, code='relay', status=502, **extra):
        super().__init__(msg)
        self.code, self.status, self.extra = code, status, extra


# ── which backend answers ─────────────────────────────────────────

def key_present():
    return bool(os.environ.get('TYPESAFE_API_KEY'))


def backend(p=None):
    """'direct' (jevgo → TypeSafe with this node's key) or 'relay'."""
    p = p or jevpay.policy()
    want = str(p.get('backend') or 'auto')
    if want == 'direct':
        return 'direct'
    if want == 'relay':
        return 'relay'
    return 'direct' if key_present() else 'relay'


def upstreams(p=None):
    p = p or jevpay.policy()
    u = p.get('upstreams') or UPSTREAMS
    if isinstance(u, str):
        u = [s.strip() for s in u.split(',') if s.strip()]
    return list(u)


# ── the seller's 402 ──────────────────────────────────────────────

def _post(url, body, headers=None, timeout=60):
    """POST JSON → (status, headers{lower}, json). Monkeypatched by tests."""
    req = urllib.request.Request(url, method='POST', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Accept': 'application/json',
                                          'User-Agent': UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, jevpay._json(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, jevpay._json(e.read() or b'')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RelayError(f'{_host(url)} unreachable: {getattr(e, "reason", e)}', 'connection') from e


def _host(url):
    return url.split('//', 1)[-1].split('/', 1)[0]


def _parse_402(headers, body):
    """The seller's PaymentRequired doc, v2 (header) or v1 (body)."""
    hdr = headers.get('payment-required')
    if hdr:
        d = jevpay.decode(hdr)
        if d.get('accepts'):
            return d
    if isinstance(body, dict) and body.get('accepts'):
        return {'x402Version': int(body.get('x402Version') or 1), **body}
    return None


def _usable(a):
    """A seller offer jev can pass on: exact USDC on a chain it knows."""
    net = jevpay._network_name(a.get('network'))
    asset = jevpay.ASSETS.get(net)
    return (a.get('scheme') or 'exact') == 'exact' and asset is not None \
        and str(a.get('asset', '')).lower() == asset['asset'].lower() and str(a.get('payTo', '')).startswith('0x')


def _amount(a):
    return int(a.get('amount') or a.get('maxAmountRequired') or 0)


def offer(body, p=None, fresh=False):
    """→ (upstream url, 402 doc, chosen accepts entry) for the first seller
    that quotes. A seller's price does not depend on the body, so quotes are
    cached for OFFER_TTL seconds."""
    p = p or jevpay.policy()
    errors = []
    for url in upstreams(p):
        with _lock:
            hit = _offers.get(url)
        if hit and not fresh and time.time() - hit[0] < OFFER_TTL:
            doc = hit[1]
        else:
            try:
                status, headers, out = _post(url, body, timeout=20)
            except RelayError as e:
                errors.append(str(e))
                continue
            doc = _parse_402(headers, out) if status == 402 else None
            if not doc:
                errors.append(f'{_host(url)}: no 402 (http {status})')
                continue
            with _lock:
                _offers[url] = (time.time(), doc)
        good = sorted((a for a in doc.get('accepts') or [] if _usable(a)), key=_amount)
        if good:
            return url, doc, good[0]
        errors.append(f'{_host(url)}: no USDC offer jev can pass on')
    raise RelayError('no Jev seller is quoting right now: ' + '; '.join(errors), 'relay', 503)


def accepts(body, resource, description=None, p=None):
    """The seller's offer as a v1 `accepts` entry under OUR resource — what
    jev's 402 shows when this node relays. → (accepts list, info dict)."""
    url, doc, a = offer(body, p)
    net = jevpay._network_name(a.get('network'))
    asset = jevpay.ASSETS[net]
    entry = {
        'scheme': 'exact', 'network': net, 'maxAmountRequired': str(_amount(a)),
        'resource': resource,
        'description': description or 'jev: typed questions over the Jev model, relayed to ' + _host(url),
        'mimeType': 'application/json', 'payTo': a['payTo'],
        'maxTimeoutSeconds': int(a.get('maxTimeoutSeconds') or 120),
        'asset': a['asset'],
        'extra': {'name': (a.get('extra') or {}).get('name') or asset['name'],
                  'version': str((a.get('extra') or {}).get('version') or asset['version'])},
    }
    return [entry], {'via': _host(url), 'upstream': url, 'usd': jevpay.usd(_amount(a))}


# ── paying the seller ─────────────────────────────────────────────

def _wrap(payment, doc, a):
    """The caller's (or our) signed transfer, in the seller's x402 version → (header, value)."""
    inner = payment.get('payload') or {}
    version = int(doc.get('x402Version') or 1)
    if version >= 2:
        out = {'x402Version': version, 'accepted': a, 'payload': inner}
        if doc.get('resource') is not None:
            out['resource'] = doc['resource']
        name = 'PAYMENT-SIGNATURE'
    else:
        out = {'x402Version': 1, 'scheme': a.get('scheme') or 'exact', 'network': a.get('network'), 'payload': inner}
        name = 'X-PAYMENT'
    return name, base64.b64encode(json.dumps(out, separators=(',', ':')).encode()).decode()


def wallet(create=False):
    """The node wallet that pays sellers for the owner and for credit spends."""
    try:
        with open(WALLET) as f:
            w = json.load(f)
        return {'address': w['address'], 'file': WALLET}
    except (OSError, ValueError, KeyError):
        pass
    if not create:
        return {'address': None, 'file': WALLET,
                'note': 'no node wallet: m jev/wallet create=1, then send it USDC on Base'}
    from eth_account import Account
    acct = Account.create()
    os.makedirs(jevpay.STATE, exist_ok=True)
    fd = os.open(WALLET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'address': acct.address, 'key': acct.key.hex()}, f)
    return {'address': acct.address, 'file': WALLET, 'created': True,
            'fund': 'send USDC on Base to this address; each relayed call costs the seller\'s price'}


def _node_payment(a):
    try:
        with open(WALLET) as f:
            w = json.load(f)
    except (OSError, ValueError):
        raise RelayError('this node has no TypeSafe key and no wallet to pay a Jev seller with — '
                         'pay with x402 (X-PAYMENT), or the owner runs m jev/wallet create=1 and funds it',
                         'no_wallet', 402)
    net = jevpay._network_name(a.get('network'))
    now = int(time.time())
    auth = {'from': w['address'], 'to': a['payTo'], 'value': str(_amount(a)),
            'validAfter': str(now - 600), 'validBefore': str(now + int(a.get('maxTimeoutSeconds') or 120)),
            'nonce': '0x' + secrets.token_hex(32)}
    return {'x402Version': 1, 'scheme': 'exact', 'network': net,
            'payload': {'signature': sign(a, auth, w['key']), 'authorization': auth}}


TYPES = {
    'EIP712Domain': [{'name': 'name', 'type': 'string'}, {'name': 'version', 'type': 'string'},
                     {'name': 'chainId', 'type': 'uint256'}, {'name': 'verifyingContract', 'type': 'address'}],
    'TransferWithAuthorization': [{'name': 'from', 'type': 'address'}, {'name': 'to', 'type': 'address'},
                                  {'name': 'value', 'type': 'uint256'}, {'name': 'validAfter', 'type': 'uint256'},
                                  {'name': 'validBefore', 'type': 'uint256'}, {'name': 'nonce', 'type': 'bytes32'}],
}


def sign(a, auth, key):
    """EIP-3009 transferWithAuthorization over the offer's USDC domain."""
    from eth_account import Account
    net = jevpay._network_name(a.get('network'))
    extra = a.get('extra') or {}
    typed = {'types': TYPES, 'primaryType': 'TransferWithAuthorization',
             'domain': {'name': extra.get('name') or jevpay.ASSETS[net]['name'],
                        'version': str(extra.get('version') or jevpay.ASSETS[net]['version']),
                        'chainId': jevpay.ASSETS[net]['chain_id'], 'verifyingContract': a['asset']},
             'message': {**auth, 'value': int(auth['value']), 'validAfter': int(auth['validAfter']),
                         'validBefore': int(auth['validBefore'])}}
    return '0x' + Account.sign_typed_data(key, full_message=typed).signature.hex().removeprefix('0x')


# ── per-call payment context, set by the gate ─────────────────────

def pay_with(raw_header=None, node=False):
    _ctx.raw, _ctx.node = raw_header, node


def clear():
    _ctx.raw, _ctx.node = None, False


def _current():
    return getattr(_ctx, 'raw', None), getattr(_ctx, 'node', False)


# ── the call ──────────────────────────────────────────────────────

def _map(answer):
    """A seller's answer (System One wire shape, `type`) → jev's (`kind`)."""
    if not isinstance(answer, dict):
        return answer
    a = dict(answer)
    kind = a.pop('type', None) or a.get('kind')
    a['kind'] = kind
    if kind == 'score' and 'level' not in a and isinstance(a.get('score'), (int, float)):
        a['level'] = int(round(a['score']))
    return a


def system_one(body, p=None):
    """POST the jevgo wire body to a seller, paid as the gate decided.
    → jev's bridge-shaped dict, or {'error', 'code', ...}."""
    p = p or jevpay.policy()
    raw, node = _current()
    try:
        url, doc, a = offer(body, p)
        if raw:
            payment = jevpay.decode(raw)
            to = str(jevpay.authorization(payment).get('to', '')).lower()
            if to != a['payTo'].lower():
                # the cached quote moved on; the caller signed for whoever was quoted
                for u in upstreams(p):
                    hit = _offers.get(u)
                    cand = [x for x in (hit[1].get('accepts') if hit else []) if str(x.get('payTo', '')).lower() == to]
                    if cand:
                        url, doc, a = u, hit[1], cand[0]
                        break
            paid_by = 'caller'
        elif node:
            payment, paid_by = _node_payment(a), 'node'
        else:
            raise RelayError('payment required', 'payment', 402)
        name, value = _wrap(payment, doc, a)
        t0 = time.time()
        status, headers, out = _post(url, body, {name: value}, timeout=float(p.get('relay_timeout_s') or 90))
    except RelayError as e:
        return {'error': str(e), 'code': e.code, **e.extra}
    via = {'via': _host(url), 'upstream': url, 'paid_by': paid_by, 'usd': jevpay.usd(_amount(a)),
           'latency_ms': int((time.time() - t0) * 1000)}
    rec = jevpay.decode(headers.get('payment-response') or headers.get('x-payment-response') or '')
    if rec:
        via['receipt'] = {'success': rec.get('success'), 'tx': rec.get('transaction') or rec.get('txHash'),
                          'network': jevpay._network_name(rec.get('network')), 'payer': rec.get('payer')}
    if status == 402:
        why = _parse_402(headers, out) or {}
        reason = why.get('error') or (out or {}).get('error') or 'payment refused'
        with _lock:
            _offers.pop(url, None)
        return {'error': f'{_host(url)} refused the payment: {reason}{hint(reason)}', 'code': 'payment', 'relay': via}
    if status >= 400 or not isinstance(out, dict) or 'answers' not in out:
        msg = (out or {}).get('error') if isinstance(out, dict) else None
        return {'error': f'{_host(url)}: {msg or f"http {status}"}', 'code': 'relay',
                'relay': via, **({'detail': out} if isinstance(out, dict) else {})}
    return {'model': out.get('model') or body.get('model') or 'jev-latest',
            'answers': {k: _map(v) for k, v in (out.get('answers') or {}).items()},
            **({'usage': out['usage']} if isinstance(out.get('usage'), dict) else {}),
            'relay': via}


def credits_ok(p=None):
    """Can prepaid credits be spent? Direct: yes. Relay: only with a node wallet to pay the seller."""
    return backend(p) == 'direct' or bool(wallet().get('address'))


def hint(reason):
    """Plain words for the facilitator's most common refusal."""
    r = str(reason).lower()
    if 'revert' in r or 'insufficient' in r or 'balance' in r:
        return ' (usually: the paying wallet has no USDC on Base, or less than the price)'
    return ''


def status(p=None):
    p = p or jevpay.policy()
    out = {'backend': backend(p), 'key_present': key_present(), 'upstreams': upstreams(p),
           'wallet': wallet().get('address')}
    if out['backend'] == 'relay':
        try:
            url, _, a = offer({'state': 'ping', 'questions': {'q': {'type': 'noul', 'instructions': 'ping'}}}, p)
            out['via'] = _host(url)
            out['seller_usd'] = jevpay.usd(_amount(a))
        except RelayError as e:
            out['error'] = str(e)
    return out
