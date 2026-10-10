"""
x402 client — pay for an HTTP request with a signed USDC authorization.

Flow: request -> 402 + payment requirements -> sign EIP-3009
transferWithAuthorization (gasless: the wallet needs USDC, not ETH) -> retry
with the signed payload in a header -> 200 (+ settlement receipt header).

Speaks both wire versions:
  v1  requirements in the 402 JSON body, payment in `X-PAYMENT`
  v2  requirements in the `PAYMENT-REQUIRED` header (base64 JSON),
      payment in `PAYMENT-SIGNATURE`, CAIP-2 networks (eip155:8453)
Only the `exact` scheme on EVM networks is implemented.
"""
import base64
import json
import os
import time
from typing import Optional

import requests
from eth_account import Account

CHAIN_IDS = {'base': 8453, 'eip155:8453': 8453,
             'base-sepolia': 84532, 'eip155:84532': 84532}

TRANSFER_TYPES = {
    'TransferWithAuthorization': [
        {'name': 'from', 'type': 'address'},
        {'name': 'to', 'type': 'address'},
        {'name': 'value', 'type': 'uint256'},
        {'name': 'validAfter', 'type': 'uint256'},
        {'name': 'validBefore', 'type': 'uint256'},
        {'name': 'nonce', 'type': 'bytes32'},
    ]
}


class PaymentError(Exception):
    pass


def _b64json(s: str) -> dict:
    return json.loads(base64.b64decode(s + '=' * (-len(s) % 4)))


def requirements(resp: requests.Response) -> dict:
    """The 402 challenge as {'version', 'accepts': [...]}, whichever wire version sent it."""
    hdr = resp.headers.get('PAYMENT-REQUIRED') or resp.headers.get('Payment-Required')
    if hdr:
        body = _b64json(hdr)
    else:
        try:
            body = resp.json()
        except ValueError:
            raise PaymentError(f'402 without readable requirements: {resp.text[:200]}')
    accepts = body.get('accepts') or []
    if not accepts:
        raise PaymentError(f'402 lists no payment options: {str(body)[:200]}')
    return {'version': int(body.get('x402Version', 2 if hdr else 1)), 'accepts': accepts, 'raw': body}


def amount(req: dict) -> int:
    """Atomic units asked for (v1 maxAmountRequired, v2 amount)."""
    return int(req.get('amount') or req.get('maxAmountRequired') or 0)


def usd(req: dict) -> float:
    decimals = int((req.get('extra') or {}).get('decimals', 6))
    return amount(req) / 10 ** decimals


def pick(accepts: list, networks: Optional[list] = None) -> dict:
    """Cheapest `exact` EVM option on a network we can sign for."""
    ok = [a for a in accepts if a.get('scheme') == 'exact'
          and a.get('network') in CHAIN_IDS
          and (not networks or a.get('network') in networks)]
    if not ok:
        nets = sorted({f"{a.get('scheme')}@{a.get('network')}" for a in accepts})
        raise PaymentError(f'no payable option (offered: {", ".join(nets)})')
    return min(ok, key=amount)


def sign(req: dict, key: str, version: int = 1, now: Optional[int] = None) -> str:
    """Base64 payment header for one requirement, signed with `key`."""
    acct = Account.from_key(key)
    now = int(now or time.time())
    extra = req.get('extra') or {}
    auth = {
        'from': acct.address,
        'to': req['payTo'],
        'value': str(amount(req)),
        'validAfter': str(now - 60),
        'validBefore': str(now + int(req.get('maxTimeoutSeconds') or 600)),
        'nonce': '0x' + os.urandom(32).hex(),
    }
    typed = {
        'types': {'EIP712Domain': [
            {'name': 'name', 'type': 'string'}, {'name': 'version', 'type': 'string'},
            {'name': 'chainId', 'type': 'uint256'}, {'name': 'verifyingContract', 'type': 'address'}],
            **TRANSFER_TYPES},
        'primaryType': 'TransferWithAuthorization',
        'domain': {'name': extra.get('name', 'USD Coin'), 'version': extra.get('version', '2'),
                   'chainId': CHAIN_IDS[req['network']], 'verifyingContract': req['asset']},
        'message': {**auth, 'value': int(auth['value']), 'validAfter': int(auth['validAfter']),
                    'validBefore': int(auth['validBefore']), 'nonce': bytes.fromhex(auth['nonce'][2:])},
    }
    sig = acct.sign_typed_data(full_message=typed).signature.hex()
    payload = {'signature': sig if sig.startswith('0x') else '0x' + sig, 'authorization': auth}
    msg = {'x402Version': version, 'scheme': 'exact', 'network': req['network'], 'payload': payload}
    if version >= 2:
        msg['accepted'] = req
    return base64.b64encode(json.dumps(msg, separators=(',', ':')).encode()).decode()


def recover(header: str, req: dict) -> str:
    """Signer address of a payment header (used by tests and any local seller)."""
    msg = _b64json(header)
    a = msg['payload']['authorization']
    extra = req.get('extra') or {}
    typed = {
        'types': {'EIP712Domain': [
            {'name': 'name', 'type': 'string'}, {'name': 'version', 'type': 'string'},
            {'name': 'chainId', 'type': 'uint256'}, {'name': 'verifyingContract', 'type': 'address'}],
            **TRANSFER_TYPES},
        'primaryType': 'TransferWithAuthorization',
        'domain': {'name': extra.get('name', 'USD Coin'), 'version': extra.get('version', '2'),
                   'chainId': CHAIN_IDS[req['network']], 'verifyingContract': req['asset']},
        'message': {**a, 'value': int(a['value']), 'validAfter': int(a['validAfter']),
                    'validBefore': int(a['validBefore']), 'nonce': bytes.fromhex(a['nonce'][2:])},
    }
    from eth_account.messages import encode_typed_data
    return Account.recover_message(encode_typed_data(full_message=typed), signature=msg['payload']['signature'])


def quote(url: str, body: dict, timeout: int = 30, networks: Optional[list] = None) -> dict:
    """Ask the price without paying: send the request, read the 402."""
    r = requests.post(url, json=body, timeout=timeout)
    if r.status_code != 402:
        return {'free': r.ok, 'status': r.status_code, 'body': r.text[:300]}
    rq = requirements(r)
    best = pick(rq['accepts'], networks)
    return {'usd': usd(best), 'atomic': amount(best), 'network': best['network'],
            'pay_to': best['payTo'], 'asset': best['asset'], 'version': rq['version'], 'requirement': best}


def pay(url: str, body: dict, key: str, max_usd: float, timeout: int = 120,
        networks: Optional[list] = None) -> dict:
    """POST, settle the 402 if one comes back and it is within max_usd, return the paid response."""
    r = requests.post(url, json=body, timeout=timeout)
    if r.status_code != 402:
        return {'response': r, 'paid_usd': 0.0}
    rq = requirements(r)
    best = pick(rq['accepts'], networks)
    price = usd(best)
    if price > float(max_usd):
        raise PaymentError(f'price ${price:.4f} is over max_usd=${float(max_usd):.4f}')
    header = sign(best, key, version=rq['version'])
    r2 = requests.post(url, json=body, timeout=timeout,
                       headers={'X-PAYMENT': header, 'PAYMENT-SIGNATURE': header})
    if r2.status_code == 402:
        raise PaymentError(f'payment refused: {r2.text[:300]}')
    receipt = r2.headers.get('PAYMENT-RESPONSE') or r2.headers.get('X-PAYMENT-RESPONSE')
    try:
        receipt = _b64json(receipt) if receipt else None
    except Exception:
        pass
    return {'response': r2, 'paid_usd': price, 'network': best['network'], 'receipt': receipt}
