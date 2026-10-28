"""x402 the way agenti's pay() does it: call, read the 402, sign an EIP-3009
transferWithAuthorization for USDC, retry with the payment header, read the
receipt. Every pay goes through the spend policy and lands in the ledger.

The payload shapes follow x402 v1 (X-PAYMENT, scheme/network at top level)
and v2 (PAYMENT-SIGNATURE, `accepted` echoed back) — the shapes orbit/x402
has confirmed against live services.
"""
import base64
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from eth_account import Account

from . import chains, wallets

UA = 'agenti-mod/0.2 (+x402)'
MAX_BODY = 2_000_000
_LOCK = threading.Lock()

DEFAULT_POLICY = {'max_per_call_usd': 0.25, 'max_per_day_usd': 2.0, 'allow_mainnet': True,
                  'allowed_hosts': [], 'blocked_hosts': []}

TYPES = {
    'EIP712Domain': [
        {'name': 'name', 'type': 'string'}, {'name': 'version', 'type': 'string'},
        {'name': 'chainId', 'type': 'uint256'}, {'name': 'verifyingContract', 'type': 'address'}],
    'TransferWithAuthorization': [
        {'name': 'from', 'type': 'address'}, {'name': 'to', 'type': 'address'},
        {'name': 'value', 'type': 'uint256'}, {'name': 'validAfter', 'type': 'uint256'},
        {'name': 'validBefore', 'type': 'uint256'}, {'name': 'nonce', 'type': 'bytes32'}],
}


class PayError(Exception):
    pass


# ── state: policy + ledger ────────────────────────────────────────

def _file(name):
    wallets.HOME.mkdir(parents=True, exist_ok=True)
    return wallets.HOME / name


def policy() -> dict:
    p = _file('policy.json')
    return {**DEFAULT_POLICY, **(json.loads(p.read_text()) if p.is_file() else {})}


def set_policy(**kw) -> dict:
    cur = policy()
    for k in ('max_per_call_usd', 'max_per_day_usd'):
        if kw.get(k) not in (None, ''):
            v = float(kw[k])
            if not 0 <= v <= 1000:
                raise PayError(f'{k} must be between 0 and 1000')
            cur[k] = v
    if kw.get('allow_mainnet') is not None:
        cur['allow_mainnet'] = str(kw['allow_mainnet']).lower() in ('1', 'true', 'yes', 'on')
    for k in ('allowed_hosts', 'blocked_hosts'):
        if kw.get(k) is not None:
            v = kw[k]
            items = v if isinstance(v, list) else str(v).replace('\n', ',').split(',')
            cur[k] = sorted({h.strip().lower() for h in items if h.strip()})
    _file('policy.json').write_text(json.dumps(cur, indent=2))
    return cur


def record(entry: dict) -> dict:
    entry = {'id': secrets.token_hex(5), 'ts': time.time(), **entry}
    with _LOCK, open(_file('ledger.jsonl'), 'a') as f:
        f.write(json.dumps(entry) + '\n')
    return entry


def ledger(limit: int = 200, kind: str = None) -> list:
    p = _file('ledger.jsonl')
    if not p.is_file():
        return []
    rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    if kind:
        rows = [r for r in rows if r.get('kind') == kind]
    return rows[::-1][:int(limit)]


def spent_today() -> float:
    day = time.time() - 86400
    return round(sum(r.get('usd') or 0 for r in ledger(5000, 'pay')
                     if r['ts'] > day and r.get('ok') and not r.get('testnet')), 6)


# ── transport ─────────────────────────────────────────────────────

def guard(url: str) -> str:
    p = urllib.parse.urlsplit(str(url or '').strip())
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise PayError(f'not an http(s) url: {url}')
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80))
    except socket.gaierror as e:
        raise PayError(f'cannot resolve {p.hostname}') from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split('%')[0])
        if not ip.is_global:
            raise PayError(f'{p.hostname} is not a public address')
    return url.strip()


def send(url, method='GET', body=None, headers=None, timeout=45):
    method = (method or 'GET').upper()
    h = {'User-Agent': UA, 'Accept': 'application/json, */*'}
    data = None
    if body not in (None, '') and method not in ('GET', 'HEAD'):
        if isinstance(body, (dict, list)):
            data, h['Content-Type'] = json.dumps(body).encode(), 'application/json'
        else:
            data = str(body).encode()
            h['Content-Type'] = 'application/json' if str(body).lstrip()[:1] in '{[' else 'text/plain'
    elif method in ('POST', 'PUT', 'PATCH'):
        data, h['Content-Type'] = b'{}', 'application/json'
    h.update({str(k): str(v) for k, v in (headers or {}).items()})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(MAX_BODY)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read(MAX_BODY) or b'')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PayError(f'could not reach {urllib.parse.urlsplit(url).hostname}: {getattr(e, "reason", e)}') from e


def shape(status, headers, body, max_chars=20000) -> dict:
    ctype = next((v for k, v in headers.items() if k.lower() == 'content-type'), '')
    out = {'status': status, 'content_type': ctype, 'bytes': len(body)}
    try:
        text = body.decode()
    except UnicodeDecodeError:
        out['binary'] = True
        return out
    try:
        out['json'] = json.loads(text)
    except json.JSONDecodeError:
        out['text'] = text[:max_chars]
        out['truncated'] = len(text) > max_chars
    return out


def _b64json(v):
    try:
        d = json.loads(base64.b64decode(str(v) + '=' * (-len(str(v)) % 4)))
        return d if isinstance(d, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def requirements(headers, body) -> dict:
    """v2 puts requirements in a base64 PAYMENT-REQUIRED header, v1 in the body."""
    hdr = {k.lower(): v for k, v in headers.items()}
    if hdr.get('payment-required'):
        d = _b64json(hdr['payment-required'])
        if d and d.get('accepts'):
            return d
    try:
        d = json.loads(body or b'{}')
        if isinstance(d, dict) and d.get('accepts'):
            return d
    except (ValueError, json.JSONDecodeError):
        pass
    return {}


def receipt(headers):
    hdr = {k.lower(): v for k, v in (headers or {}).items()}
    enc = hdr.get('payment-response') or hdr.get('x-payment-response')
    return (_b64json(enc) or {'raw': enc}) if enc else None


# ── offers ────────────────────────────────────────────────────────

def _amount(a):
    v = a.get('amount') if a.get('amount') is not None else a.get('maxAmountRequired')
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def offer(a: dict, i: int) -> dict:
    chain = chains.NETWORK_ALIASES.get(str(a.get('network') or '').lower()) or \
        chains.NETWORK_ALIASES.get(str(a.get('network') or ''))
    usdc = chains.usdc_of(chain) if chain else None
    raw = _amount(a)
    asset = str(a.get('asset') or '')
    is_usdc = bool(usdc) and asset.lower() == usdc.lower()
    kind = 'evm' if chain in chains.EVM else 'solana' if chain in chains.SOL else None
    payable = (kind == 'evm' and is_usdc and (a.get('scheme') or 'exact') == 'exact'
               and raw is not None and bool(a.get('payTo')))
    why = None
    if not payable:
        why = ('solana x402 needs the upstream SDK (agenti pay() on node)' if kind == 'solana'
               else 'unknown network' if not chain
               else 'only USDC "exact" offers are signed here')
    return {'index': i, 'network': a.get('network'), 'chain': chain,
            'chain_label': (chains.EVM.get(chain) or chains.SOL.get(chain) or {}).get('label', a.get('network')),
            'testnet': bool((chains.EVM.get(chain) or chains.SOL.get(chain) or {}).get('testnet')),
            'scheme': a.get('scheme') or 'exact', 'asset': asset, 'asset_name': 'USDC' if is_usdc else (a.get('extra') or {}).get('name') or asset[:10],
            'amount_raw': raw, 'usd': (raw / 1e6) if (is_usdc and raw is not None) else None,
            'pay_to': a.get('payTo'), 'description': a.get('description'),
            'payable': payable, 'why_not': why}


def offers(req: dict) -> list:
    return [offer(a, i) for i, a in enumerate(req.get('accepts') or []) if isinstance(a, dict)]


# ── probe + pay ───────────────────────────────────────────────────

def probe(url, method='GET', body=None) -> dict:
    """Call without paying. 402 → what it costs; anything else → the response."""
    url = guard(url)
    status, headers, raw = send(url, method, body)
    out = {'url': url, 'method': method.upper(), 'status': status}
    if status == 402:
        req = requirements(headers, raw)
        out.update(paywalled=True, x402_version=int(req.get('x402Version') or 1),
                   resource=req.get('resource'), offers=offers(req),
                   error=req.get('error'))
        if not req:
            out['note'] = 'got 402 but no x402 requirements in the header or body'
    else:
        out.update(paywalled=False, response=shape(status, headers, raw))
    record({'kind': 'probe', 'url': url, 'status': status, 'paywalled': status == 402})
    return out


def _check_policy(o: dict, url: str, max_usd):
    pol = policy()
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    if host in pol['blocked_hosts']:
        raise PayError(f'{host} is on your blocked list')
    if pol['allowed_hosts'] and host not in pol['allowed_hosts']:
        raise PayError(f'{host} is not on your allowed list')
    if o['testnet']:
        return
    if not pol['allow_mainnet']:
        raise PayError('mainnet payments are switched off in your limits')
    cap = pol['max_per_call_usd'] if max_usd in (None, '') else min(float(max_usd), pol['max_per_call_usd'])
    if o['usd'] > cap:
        raise PayError(f'costs ${o["usd"]:.4f}, over your ${cap:.2f} per-call limit')
    today = spent_today()
    if today + o['usd'] > pol['max_per_day_usd']:
        raise PayError(f'would pass your ${pol["max_per_day_usd"]:.2f} daily limit (spent ${today:.4f} today)')


def pay(url, method='GET', body=None, wallet_id=None, index=None, max_usd=None) -> dict:
    url = guard(url)
    w, acct = wallets.evm_account(wallet_id)
    status, headers, raw = send(url, method, body)
    if status != 402:
        return {'url': url, 'paid': False, 'status': status, 'note': 'no payment was needed',
                'response': shape(status, headers, raw)}
    req = requirements(headers, raw)
    offs = offers(req)
    if not offs:
        raise PayError('402 without x402 payment requirements')
    if index not in (None, ''):
        idx = int(index)
        if not 0 <= idx < len(offs):
            raise PayError(f'offer {idx} does not exist')
        o = offs[idx]
        if not o['payable']:
            raise PayError(o['why_not'])
    else:
        cands = sorted((x for x in offs if x['payable']), key=lambda x: (x['testnet'], x['usd']))
        if not cands:
            raise PayError(offs[0]['why_not'] or 'no offer this wallet can pay')
        o = cands[0]
    _check_policy(o, url, max_usd)
    a = req['accepts'][o['index']]
    now = int(time.time())
    auth = {'from': acct.address, 'to': a['payTo'], 'value': str(o['amount_raw']),
            'validAfter': str(now - 600), 'validBefore': str(now + int(a.get('maxTimeoutSeconds') or 300)),
            'nonce': '0x' + secrets.token_hex(32)}
    extra = a.get('extra') or {}
    typed = {'types': TYPES, 'primaryType': 'TransferWithAuthorization',
             'domain': {'name': extra.get('name') or 'USD Coin', 'version': str(extra.get('version') or '2'),
                        'chainId': chains.EVM[o['chain']]['chain_id'], 'verifyingContract': a['asset']},
             'message': {**auth, 'value': int(auth['value']), 'validAfter': int(auth['validAfter']),
                         'validBefore': int(auth['validBefore'])}}
    sig = '0x' + Account.sign_typed_data(acct.key, full_message=typed).signature.hex().removeprefix('0x')
    version = int(req.get('x402Version') or 1)
    inner = {'signature': sig, 'authorization': auth}
    if version >= 2:
        doc = {'x402Version': version, 'accepted': a, 'payload': inner}
        if req.get('resource') is not None:
            doc['resource'] = req['resource']
        hname = 'PAYMENT-SIGNATURE'
    else:
        doc = {'x402Version': 1, 'scheme': a.get('scheme') or 'exact', 'network': a.get('network'), 'payload': inner}
        hname = 'X-PAYMENT'
    value = base64.b64encode(json.dumps(doc, separators=(',', ':')).encode()).decode()
    st2, h2, raw2 = send(url, method, body, headers={hname: value, 'X-PAYMENT': value})
    rec = receipt(h2)
    ok = 200 <= st2 < 300
    err = None
    if not ok:
        r2 = requirements(h2, raw2)
        err = r2.get('error') or shape(st2, h2, raw2, 400).get('text') or (shape(st2, h2, raw2).get('json') or {}).get('error')
    entry = record({'kind': 'pay', 'url': url, 'method': method.upper(), 'wallet': w['name'], 'from': acct.address,
                    'to': a['payTo'], 'chain': o['chain'], 'testnet': o['testnet'], 'usd': o['usd'],
                    'amount_raw': o['amount_raw'], 'signed': True, 'ok': ok, 'status': st2,
                    'tx': (rec or {}).get('transaction') or (rec or {}).get('txHash'), 'error': err})
    return {'url': url, 'paid': ok, 'status': st2, 'offer': o, 'receipt': rec, 'error': err,
            'ledger_id': entry['id'], 'response': shape(st2, h2, raw2)}


def services(q: str = '', limit: int = 24) -> dict:
    """Paid APIs to try, from orbit/x402's index on this box (loopback)."""
    qs = urllib.parse.urlencode({'q': q or '', 'limit': int(limit), 'priced': 1, 'sort': 'popular'})
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:51110/x402/api/services?{qs}', timeout=20) as r:
            d = json.loads(r.read())
        return {'ok': True, 'total': d.get('total'), 'items': [
            {k: s.get(k) for k in ('url', 'host', 'name', 'description', 'method', 'networks', 'price_usd',
                                   'calls_30d', 'icon', 'tags')} for s in d.get('items', [])]}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'items': [], 'error': f'x402 index unavailable: {e}'}
