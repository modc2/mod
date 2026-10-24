"""x402pay — the paying half of x402: ask, sign, retry, read the receipt.

    1. call the resource unpaid           → 402 + payment requirements
    2. pick one offer (`accepts` entry)   → what to pay, on which chain, to whom
    3. sign an EIP-3009 transferWithAuthorization for it (EIP-712 typed data)
    4. call again with the signed payload → 2xx + the facilitator's receipt

Step 3 can happen in three places, and the node prefers the first:

    own     the agent signs with its own wallet — `prepare` hands it the exact
            typed data, it returns the signature; the node never sees a key
    payload the agent brings a finished X-PAYMENT / PAYMENT-SIGNATURE value
            (any scheme, any chain — relayed untouched)
    house   the node's own wallet signs (``~/.mod/x402/wallet.json``, off by
            default, budgeted per caller by x402trust)

Only the `exact` scheme on EVM chains with an EIP-3009 token (USDC & co.) is
signed here; everything else is still reachable through `payload`. Signing
needs eth_account; the rest is stdlib.
"""

import base64
import ipaddress
import json
import os
import secrets
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

import x402src as src

STATE = os.path.expanduser(os.environ.get('X402_HOME') or '~/.mod/x402')
WALLET = os.path.join(STATE, 'wallet.json')
MAX_BODY = 2_000_000

# v1 short names → EVM chain ids (the inverse of src.NETWORKS).
CHAIN_IDS = {short: int(caip.split(':')[1]) for caip, short in src.NETWORKS.items()
             if caip.startswith('eip155:')}


class PayError(Exception):
    pass


# ── where a request may go ────────────────────────────────────────

def guard(url):
    """Public http(s) only. The node fetches on a stranger's behalf, so a URL
    that resolves into this box's own networks is refused (SSRF)."""
    p = urllib.parse.urlsplit(str(url or ''))
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise PayError(f'not an http(s) url: {url}')
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
    except socket.gaierror as e:
        raise PayError(f'cannot resolve {p.hostname}: {e}') from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if not ip.is_global:
            raise PayError(f'{p.hostname} resolves to non-public {ip}')
    return url


# ── transport ─────────────────────────────────────────────────────

def send(url, method='GET', body=None, headers=None, timeout=60):
    """Any request → (status, headers, body bytes). Never raises on a status.
    A dict/list body goes as JSON; a string as-is."""
    method = (method or 'GET').upper()
    h = {'User-Agent': src.UA, 'Accept': 'application/json, */*'}
    data = None
    if body is not None and method not in ('GET', 'HEAD'):
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        else:
            data = str(body).encode()
    elif method not in ('GET', 'HEAD', 'DELETE'):
        data, h['Content-Type'] = b'{}', 'application/json'
    h.update({str(k): str(v) for k, v in (headers or {}).items()})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(MAX_BODY)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read(MAX_BODY) or b'')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PayError(f'{url}: {getattr(e, "reason", e)}') from e


def with_query(url, query):
    if not query:
        return url
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunsplit(p._replace(query=f'{p.query}&{q}' if p.query else q))


def reply(status, headers, body, max_chars=20000):
    """A response, shaped for a model: parsed JSON, text, or a binary note."""
    ctype = next((v for k, v in headers.items() if k.lower() == 'content-type'), '')
    out = {'status': status, 'content_type': ctype, 'bytes': len(body)}
    try:
        text = body.decode()
    except UnicodeDecodeError:
        out['base64'] = base64.b64encode(body[:max_chars]).decode()
        out['truncated'] = len(body) > max_chars
        return out
    try:
        out['json'] = json.loads(text)
        return out
    except json.JSONDecodeError:
        pass
    out['text'] = text[:max_chars]
    out['truncated'] = len(text) > max_chars
    return out


# ── offers ────────────────────────────────────────────────────────

def chain_id(network):
    n = str(network or '')
    if n.startswith('eip155:'):
        try:
            return int(n.split(':', 1)[1])
        except ValueError:
            return None
    return CHAIN_IDS.get(n)


def amount(a):
    return src._int(a.get('amount') if a.get('amount') is not None else a.get('maxAmountRequired'))


def signable(a):
    """Can this node sign for this offer? exact + EVM + an EIP-3009 token."""
    return ((a.get('scheme') or 'exact') == 'exact' and chain_id(a.get('network'))
            and str(a.get('asset') or '').startswith('0x') and amount(a) is not None
            and bool(a.get('payTo')))


def offers(req):
    """Every offer in a 402, flattened, priced, and marked signable or not."""
    out = []
    for i, a in enumerate(req.get('accepts') or []):
        if isinstance(a, dict):
            o = src.offer(a)
            o.update(index=i, signable=bool(signable(a)),
                     max_timeout=a.get('maxTimeoutSeconds'))
            out.append(o)
    return out


def pick(req, index=None, network=None, max_usd=None, signable_only=True):
    """Choose the offer to pay: an explicit index, else the cheapest USD offer
    (optionally on one network) under the cap that this node can sign."""
    acc = [a for a in (req.get('accepts') or []) if isinstance(a, dict)]
    if index is not None:
        i = int(index)
        if not 0 <= i < len(acc):
            raise PayError(f'offer index {i} out of range (0..{len(acc) - 1})')
        return i, acc[i]
    best = None
    for i, a in enumerate(acc):
        o = src.offer(a)
        if signable_only and not signable(a):
            continue
        if network and o['network'] != network and a.get('network') != network:
            continue
        if o['price_usd'] is None or (max_usd is not None and o['price_usd'] > float(max_usd)):
            continue
        if best is None or o['price_usd'] < best[2]:
            best = (i, a, o['price_usd'])
    if best is None:
        raise PayError('no offer this node can pay within the limit'
                       + (f' (max ${max_usd})' if max_usd is not None else ''))
    return best[0], best[1]


# ── EIP-3009 / EIP-712 ────────────────────────────────────────────

TYPES = {
    'EIP712Domain': [
        {'name': 'name', 'type': 'string'}, {'name': 'version', 'type': 'string'},
        {'name': 'chainId', 'type': 'uint256'}, {'name': 'verifyingContract', 'type': 'address'}],
    'TransferWithAuthorization': [
        {'name': 'from', 'type': 'address'}, {'name': 'to', 'type': 'address'},
        {'name': 'value', 'type': 'uint256'}, {'name': 'validAfter', 'type': 'uint256'},
        {'name': 'validBefore', 'type': 'uint256'}, {'name': 'nonce', 'type': 'bytes32'}],
}


def authorization(a, payer, now=None):
    """The transfer the payer authorises: exactly the offer's amount, to its
    payTo, valid from 10 min ago until the offer's timeout."""
    now = int(now or time.time())
    return {'from': payer, 'to': a['payTo'], 'value': str(amount(a)),
            'validAfter': str(now - 600),
            'validBefore': str(now + int(a.get('maxTimeoutSeconds') or 300)),
            'nonce': '0x' + secrets.token_hex(32)}


def typed_data(a, auth):
    """EIP-712 typed data for eth_signTypedData_v4 — what any wallet signs."""
    extra = a.get('extra') or {}
    return {
        'types': TYPES, 'primaryType': 'TransferWithAuthorization',
        'domain': {'name': extra.get('name') or 'USD Coin', 'version': str(extra.get('version') or '2'),
                   'chainId': chain_id(a.get('network')), 'verifyingContract': a['asset']},
        'message': {**auth, 'value': int(auth['value']), 'validAfter': int(auth['validAfter']),
                    'validBefore': int(auth['validBefore'])},
    }


def sign(typed, private_key):
    try:
        from eth_account import Account
    except ImportError as e:
        raise PayError('signing needs eth_account (pip install eth-account)') from e
    signed = Account.sign_typed_data(private_key, full_message=typed)
    return '0x' + signed.signature.hex().removeprefix('0x')


def payload(req, a, signature, auth):
    """The signed payment, encoded for the retry → (header name, value).
    v2 echoes the chosen requirement back as `accepted`; v1 names the
    scheme and network at the top level."""
    version = int(req.get('x402Version') or 1)
    inner = {'signature': signature, 'authorization': auth}
    if version >= 2:
        doc = {'x402Version': version, 'accepted': a, 'payload': inner}
        if req.get('resource') is not None:
            doc['resource'] = req['resource']
        name = 'PAYMENT-SIGNATURE'
    else:
        doc = {'x402Version': 1, 'scheme': a.get('scheme') or 'exact',
               'network': a.get('network'), 'payload': inner}
        name = 'X-PAYMENT'
    return name, base64.b64encode(json.dumps(doc, separators=(',', ':')).encode()).decode()


def decode(value):
    """A base64 JSON header value → dict ({} if it is not one)."""
    try:
        d = json.loads(base64.b64decode(str(value) + '=' * (-len(str(value)) % 4)))
        return d if isinstance(d, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def header_name(req):
    return 'PAYMENT-SIGNATURE' if int(req.get('x402Version') or 1) >= 2 else 'X-PAYMENT'


def receipt(headers):
    """The facilitator's settlement receipt, if the server passed one on."""
    hdr = {k.lower(): v for k, v in (headers or {}).items()}
    enc = hdr.get('payment-response') or hdr.get('x-payment-response')
    if not enc:
        return None
    try:
        return json.loads(base64.b64decode(enc + '=' * (-len(enc) % 4)))
    except (ValueError, json.JSONDecodeError):
        return {'raw': enc}


# ── the house wallet ──────────────────────────────────────────────

def wallet(create=False):
    """The node's own payer: {address} (the key never leaves this file).
    Off until the owner creates it and turns `house` on in the policy."""
    if os.path.exists(WALLET):
        with open(WALLET) as f:
            w = json.load(f)
        return {'address': w['address'], 'file': WALLET}
    if not create:
        return None
    from eth_account import Account
    acct = Account.create()
    os.makedirs(STATE, exist_ok=True)
    fd = os.open(WALLET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'address': acct.address, 'private_key': acct.key.hex()}, f)
    return {'address': acct.address, 'file': WALLET, 'created': True}


def _house_key():
    with open(WALLET) as f:
        return json.load(f)['private_key']


def house_sign(a):
    """Sign one offer with the house wallet → (auth, signature)."""
    w = wallet()
    if not w:
        raise PayError('no house wallet on this node')
    auth = authorization(a, w['address'])
    return auth, sign(typed_data(a, auth), _house_key())
