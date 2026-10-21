"""Offline tests for jev: the pay gate (facilitator mocked), mod-protocol
identity, credits, and the server's routing. No network, no TypeSafe key."""

import base64
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import jevpay as pay  # noqa: E402

PAY_TO = '0x' + 'ab' * 20


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    pay.reset(home=str(tmp_path))
    monkeypatch.delenv('JEV_FREE', raising=False)
    monkeypatch.delenv('JEV_PAY_TO', raising=False)
    pay.set_policy(pay_to=PAY_TO, price_usd=0.001, networks='base,base-sepolia', min_topup_usd=0.25,
                   backend='direct')      # relay has its own tests below
    import jevrelay
    jevrelay.WALLET = os.path.join(str(tmp_path), 'wallet.json')
    jevrelay._offers.clear()
    yield tmp_path
    pay.reset(home=str(tmp_path))


class Fac:
    """A facilitator that says yes (or not) and counts calls."""

    def __init__(self, valid=True, settle_ok=True):
        self.valid, self.settle_ok, self.calls = valid, settle_ok, []

    def __call__(self, path, body, p=None, timeout=30):
        self.calls.append((path, body))
        if path == '/verify':
            return 200, {'isValid': self.valid, 'invalidReason': None if self.valid else 'bad sig',
                         'payer': body['paymentPayload']['payload']['authorization']['from']}
        return 200, {'success': self.settle_ok, 'transaction': '0x' + 'cd' * 32, 'network': body['paymentRequirements']['network'],
                     'payer': body['paymentPayload']['payload']['authorization']['from'],
                     'errorReason': None if self.settle_ok else 'nope'}


def x_payment(value, to=PAY_TO, network='base', payer=None, nonce=None):
    now = int(time.time())
    doc = {'x402Version': 1, 'scheme': 'exact', 'network': network,
           'payload': {'signature': '0x' + 'ab' * 65,
                       'authorization': {'from': payer or '0x' + 'ef' * 20, 'to': to, 'value': str(value),
                                         'validAfter': str(now - 600), 'validBefore': str(now + 300),
                                         'nonce': nonce or '0x' + secrets.token_hex(32)}}}
    return base64.b64encode(json.dumps(doc).encode()).decode()


def mod_token(acct=None, raw=False):
    from eth_account import Account
    from eth_account.messages import encode_defunct
    from eth_utils import keccak
    acct = acct or Account.create()
    t = str(int(time.time()))
    msg = json.dumps({'data': 'jev', 'time': t}, separators=(',', ':'))
    if raw:
        sig = Account._sign_hash(keccak(text=msg), private_key=acct.key).signature.hex()
    else:
        sig = Account.sign_message(encode_defunct(text=msg), private_key=acct.key).signature.hex()
    claim = {'data': 'jev', 'time': t, 'key': acct.address, 'signature': '0x' + sig.removeprefix('0x')}
    return acct, base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip('=')


def run_ok():
    return {'noul': 0.9, 'model': 'jev-latest', 'usage': {'input_tokens': 1, 'output_tokens': 1}}


# ── units, policy, 402 ────────────────────────────────────────────

def test_units_and_quote():
    assert pay.units(0.001) == 1000
    assert pay.usd(1000) == 0.001
    assert pay.quote(3) == 3000
    with pytest.raises(pay.PayError):
        pay.quote(0)
    with pytest.raises(pay.PayError):
        pay.quote(999)


def test_policy_layers(monkeypatch):
    p = pay.policy()
    assert p['pay_to'] == PAY_TO and p['networks'] == ['base', 'base-sepolia']
    monkeypatch.setenv('JEV_PAY_TO', '0x' + '11' * 20)
    assert pay.policy()['pay_to'] == '0x' + '11' * 20
    monkeypatch.setenv('JEV_FREE', '1')
    assert pay.policy()['enabled'] is False
    with pytest.raises(pay.PayError):
        pay.set_policy(bogus=1)
    with pytest.raises(pay.PayError):
        pay.set_policy(pay_to='nope')


def test_402_shape():
    status, body, extra = pay.charge({}, 2, 'http://x/jev/api/ask', run_ok, local=False)
    assert status == 402 and body['x402Version'] == 1 and extra == {}
    nets = [a['network'] for a in body['accepts']]
    assert nets == ['base', 'base-sepolia']
    a = body['accepts'][0]
    assert a['maxAmountRequired'] == '2000' and a['payTo'] == PAY_TO and a['scheme'] == 'exact'
    assert a['asset'].lower() == pay.ASSETS['base']['asset'].lower() and a['extra'] == {'name': 'USD Coin', 'version': '2'}
    assert body['price'] == {'usd': 0.002, 'units': 2000, 'currency': 'USDC', 'per_question_usd': 0.001, 'questions': 2}
    assert body['kyc'] == 'none' and 'credits' in body


def test_local_is_free():
    calls = []
    status, body, _ = pay.charge({}, 1, 'r', lambda: (calls.append(1), run_ok())[1], local=True)
    assert status == 200 and body['payment'] == {'mode': 'free', 'who': 'local'} and calls == [1]
    pay.set_policy(free_local=False)
    assert pay.charge({}, 1, 'r', run_ok, local=True)[0] == 402


def test_disabled_or_no_pay_to_is_free():
    pay.set_policy(enabled=False)
    assert pay.charge({}, 1, 'r', run_ok)[0] == 200
    pay.set_policy(enabled=True, pay_to=None)
    pay.set_policy(pay_to=None)
    assert pay.policy()['pay_to'] == '' or pay.charge({}, 1, 'r', run_ok)[0] == 200


# ── x402 ──────────────────────────────────────────────────────────

def test_x402_pays_then_settles_and_blocks_replay(monkeypatch):
    fac = Fac()
    monkeypatch.setattr(pay, 'facilitator_post', fac)
    nonce = '0x' + secrets.token_hex(32)
    hdr = {'X-PAYMENT': x_payment(1000, nonce=nonce)}
    ran = []
    status, body, extra = pay.charge(hdr, 1, 'r', lambda: (ran.append(1), run_ok())[1])
    assert status == 200 and ran == [1]
    assert body['payment']['mode'] == 'x402' and body['payment']['settled'] is True
    assert [c[0] for c in fac.calls] == ['/verify', '/settle']
    assert fac.calls[0][1]['x402Version'] == 1 and 'paymentHeader' in fac.calls[0][1]
    rec = json.loads(base64.b64decode(extra['X-PAYMENT-RESPONSE']))
    assert rec['success'] and rec['transaction'].startswith('0x')
    assert pay.payments()[0]['nonce'] == nonce and pay.stats()['received_usd'] == 0.001
    # the same signed payment again: refused before the facilitator is asked
    n = len(fac.calls)
    status, body, _ = pay.charge(hdr, 1, 'r', run_ok)
    assert status == 402 and 'already used' in body['error'] and len(fac.calls) == n


def test_x402_underpaid_or_wrong_recipient():
    assert pay.charge({'X-PAYMENT': x_payment(999)}, 1, 'r', run_ok)[0] == 402
    assert pay.charge({'X-PAYMENT': x_payment(1000, to='0x' + '00' * 20)}, 1, 'r', run_ok)[0] == 402
    assert pay.charge({'X-PAYMENT': x_payment(1000, network='polygon')}, 1, 'r', run_ok)[0] == 402
    assert pay.charge({'X-PAYMENT': 'not base64 json'}, 1, 'r', run_ok)[0] == 402


def test_x402_invalid_signature(monkeypatch):
    fac = Fac(valid=False)
    monkeypatch.setattr(pay, 'facilitator_post', fac)
    ran = []
    status, body, _ = pay.charge({'X-PAYMENT': x_payment(1000)}, 1, 'r', lambda: (ran.append(1), run_ok())[1])
    assert status == 402 and 'bad sig' in body['error'] and ran == []


def test_x402_failed_answer_is_not_charged(monkeypatch):
    fac = Fac()
    monkeypatch.setattr(pay, 'facilitator_post', fac)
    status, body, _ = pay.charge({'X-PAYMENT': x_payment(1000)}, 1, 'r',
                                 lambda: {'error': 'jev: overloaded', 'code': 'overloaded'})
    assert status == 503 and body['payment']['settled'] is False
    assert [c[0] for c in fac.calls] == ['/verify'] and pay.payments() == []


def test_x402_facilitator_down(monkeypatch):
    def down(*a, **k):
        raise pay.PayError('facilitator unreachable: boom')
    monkeypatch.setattr(pay, 'facilitator_post', down)
    status, body, _ = pay.charge({'X-PAYMENT': x_payment(1000)}, 1, 'r', run_ok)
    assert status == 503 and body['code'] == 'facilitator'


def test_v2_payment_matches_on_accepted(monkeypatch):
    fac = Fac()
    monkeypatch.setattr(pay, 'facilitator_post', fac)
    req = pay.requirement('base', 1000, 'r')
    now = int(time.time())
    doc = {'x402Version': 2, 'accepted': {**req, 'network': 'eip155:8453'},
           'payload': {'signature': '0x' + 'ab' * 65,
                       'authorization': {'from': '0x' + 'ef' * 20, 'to': PAY_TO, 'value': '1000',
                                         'validAfter': str(now - 1), 'validBefore': str(now + 100),
                                         'nonce': '0x' + secrets.token_hex(32)}}}
    hdr = {'PAYMENT-SIGNATURE': base64.b64encode(json.dumps(doc).encode()).decode()}
    status, body, _ = pay.charge(hdr, 1, 'r', run_ok)
    assert status == 200 and fac.calls[0][1]['x402Version'] == 2 and 'paymentHeader' not in fac.calls[0][1]


# ── identity + credits ────────────────────────────────────────────

def test_mod_token_identity():
    acct, tok = mod_token()
    assert pay.verify_token(tok) == acct.address.lower()
    acct2, tok2 = mod_token(raw=True)
    assert pay.verify_token(tok2) == acct2.address.lower()
    assert pay.verify_token('garbage') == ''
    assert pay.identify({'token': tok}) == {'id': acct.address.lower(), 'kind': 'wallet'}
    assert pay.identify({'Authorization': 'Bearer ' + tok})['kind'] == 'wallet'
    assert pay.identify({'X-Mod-Token': tok})['kind'] == 'wallet'
    assert pay.identify({}, local=True) == {'id': 'local', 'kind': 'local'}
    assert pay.identify({'token': 'bad'}) == {'id': 'anon', 'kind': 'anon'}


def test_credits_spend_and_run_out():
    acct, tok = mod_token()
    addr = acct.address.lower()
    assert pay.credit(addr, 2500, 'grant')['usd'] == 0.0025
    status, body, extra = pay.charge({'token': tok}, 2, 'r', run_ok)
    assert status == 200 and body['payment'] == {'mode': 'credits', 'usd': 0.002, 'left_usd': 0.0005, 'address': addr}
    assert extra['X-Jev-Credits-Left'] == '0.0005'
    status, body, _ = pay.charge({'token': tok}, 1, 'r', run_ok)
    assert status == 402 and body['price']['balance_usd'] == 0.0005 and 'insufficient' in body['error']
    assert pay.balance(addr)['questions'] == 0
    led = pay.ledger(addr)
    assert [r['kind'] for r in led] == ['spend', 'grant'] and led[0]['delta'] == -2000
    with pytest.raises(pay.PayError):
        pay.debit(addr, 10 ** 9)


def test_credits_failed_answer_not_debited():
    acct, tok = mod_token()
    pay.credit(acct.address, 1000, 'grant')
    status, body, _ = pay.charge({'token': tok}, 1, 'r', lambda: {'error': 'x', 'code': 'timeout'})
    assert status == 504 and body['payment']['charged'] is False
    assert pay.balance(acct.address)['units'] == 1000


def test_topup_credits_the_payer(monkeypatch):
    fac = Fac()
    monkeypatch.setattr(pay, 'facilitator_post', fac)
    payer = '0x' + '12' * 20
    status, body, _ = pay.topup({}, 'topup')
    assert status == 402 and body['accepts'][0]['maxAmountRequired'] == '250000'
    status, body, _ = pay.topup({'X-PAYMENT': x_payment(100, payer=payer)}, 'topup')
    assert status == 402 and 'minimum' in body['error']
    status, body, extra = pay.topup({'X-PAYMENT': x_payment(500000, payer=payer)}, 'topup')
    assert status == 200 and body['credited_usd'] == 0.5 and body['questions'] == 500
    assert pay.balance(payer)['units'] == 500000 and 'X-PAYMENT-RESPONSE' in extra
    assert fac.calls[0][1]['paymentRequirements']['maxAmountRequired'] == '500000'
    assert pay.payments()[0]['kind'] == 'topup'


def test_gate_adapter_for_core_serve(monkeypatch):
    fac = Fac()
    monkeypatch.setattr(pay, 'facilitator_post', fac)
    g = pay.Gate('jev')
    assert g.gate_check('models', {}) is None
    r = g.gate_check('jev/noul', {})
    assert r['code'] == 402 and r['accepts'][0]['resource'] == 'jev/noul'
    assert g.gate_check('noul', {'X-PAYMENT': x_payment(1000)}) is None
    assert [c[0] for c in fac.calls] == ['/verify', '/settle']
    acct, tok = mod_token()
    pay.credit(acct.address, 1000, 'grant')
    assert g.gate_check('score', {'token': tok}) is None
    assert g.gate_check('score', {'token': tok})['code'] == 402


# ── the server ────────────────────────────────────────────────────

@pytest.fixture
def server(monkeypatch):
    import serve as srv
    monkeypatch.setattr(srv.MOD, '_bridge', lambda op, payload=None: {
        'model': 'jev-latest', 'answers': {'q': {'kind': 'noul', 'noul': 0.42}},
        'usage': {'input_tokens': 3, 'output_tokens': 1}, 'request_id': 'r1', 'attempts': 1}
        if op == 'system_one' else {'models': [{'name': 'jev-latest'}]} if op == 'models'
        else {'client': 'github.com/fgn/jevgo', 'base_url': 'x', 'version': '0.3.0'})
    s = ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f'http://127.0.0.1:{s.server_port}'
    s.shutdown()
    s.server_close()


def http(url, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ('POST' if data else 'GET'),
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b'{}'), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'{}'), dict(e.headers)


def test_server_routes(server, monkeypatch):
    s, body, _ = http(server + '/jev/api/health')
    assert s == 200 and body['ok'] and body['pay'] is True
    s, body, _ = http(server + '/api/jev/price')
    assert s == 200 and body['price_usd'] == 0.001 and body['kyc'] == 'none'
    # local = owner = free
    s, body, _ = http(server + '/jev/api/noul', {'state': 'hi', 'instructions': 'greeting?'})
    assert s == 200 and body['noul'] == 0.42 and body['payment']['mode'] == 'free'
    # the gateway forwarded it: a stranger, so 402
    s, body, _ = http(server + '/jev/api/noul', {'state': 'hi', 'instructions': 'greeting?'},
                      {'X-Forwarded-For': '203.0.113.9', 'X-Forwarded-Proto': 'https', 'X-Forwarded-Host': 'modc2.com'})
    assert s == 402 and body['accepts'][0]['resource'] == 'https://modc2.com/jev/api/noul'
    # pay and it answers
    fac = Fac()
    monkeypatch.setattr(pay, 'facilitator_post', fac)
    s, body, h = http(server + '/noul', {'state': 'hi', 'instructions': 'greeting?'},
                      {'X-Forwarded-For': '203.0.113.9', 'X-PAYMENT': x_payment(1000)})
    assert s == 200 and body['noul'] == 0.42 and body['payment']['settled'] and 'X-PAYMENT-RESPONSE' in h
    # ask prices per question
    s, body, _ = http(server + '/jev/api/ask', {'state': 'hi', 'questions': {'a': {'kind': 'noul', 'instructions': '?'},
                                                                              'b': {'kind': 'noul', 'instructions': '?'}}},
                      {'X-Forwarded-For': '203.0.113.9'})
    assert s == 402 and body['price']['questions'] == 2 and body['accepts'][0]['maxAmountRequired'] == '2000'
    # credits via token
    acct, tok = mod_token()
    s, body, _ = http(server + '/jev/api/whoami', headers={'token': tok, 'X-Forwarded-For': '203.0.113.9'})
    assert body['kind'] == 'wallet' and body['pays'] is True and body['credits']['units'] == 0
    s, body, _ = http(server + '/jev/api/grant', {'address': acct.address, 'usd': 0.01})
    assert s == 200 and body['units'] == 10000
    s, body, _ = http(server + '/jev/api/grant', {'address': acct.address, 'usd': 1}, {'X-Forwarded-For': '203.0.113.9'})
    assert s == 403
    s, body, h = http(server + '/jev/api/noul', {'state': 'hi', 'instructions': '?'},
                      {'token': tok, 'X-Forwarded-For': '203.0.113.9'})
    assert s == 200 and body['payment']['mode'] == 'credits' and h['X-Jev-Credits-Left'] == '0.009'
    # topup
    s, body, _ = http(server + '/jev/api/topup', {}, {'X-Forwarded-For': '1.1.1.1', 'X-PAYMENT': x_payment(300000, payer=acct.address)})
    assert s == 200 and body['credited_usd'] == 0.3
    assert pay.balance(acct.address)['units'] == 309000
    # console + 404
    req = urllib.request.Request(server + '/jev/')
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 200 and b'<html' in r.read(400).lower()
    assert http(server + '/jev/api/nope')[0] == 404


def test_server_mcp(server, monkeypatch):
    s, body, _ = http(server + '/jev/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    names = [t['name'] for t in body['result']['tools']]
    assert 'jev_noul' in names and 'jev_price' in names and 'jev_encode' in names
    s, body, _ = http(server + '/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                        'params': {'name': 'jev_price', 'arguments': {}}})
    assert body['result']['structuredContent']['currency'] == 'USDC' and not body['result']['isError']
    s, body, _ = http(server + '/api/jev/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                                'params': {'name': 'jev_noul', 'arguments': {'state': 'x', 'instructions': '?'}}},
                      {'X-Forwarded-For': '203.0.113.9'})
    assert body['result']['isError'] and body['result']['structuredContent']['x402Version'] == 1
    assert body['result']['_meta']['http_status'] == 402
    s, body, _ = http(server + '/jev/mcp', {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                                            'params': {'name': 'jev_noul', 'arguments': {'state': 'x', 'instructions': '?'}}})
    assert body['result']['structuredContent']['noul'] == 0.42
    s, body, _ = http(server + '/jev/mcp', {'jsonrpc': '2.0', 'id': 5, 'method': 'initialize', 'params': {}})
    assert body['result']['serverInfo']['name'] == 'jev'


# ── relay: no TypeSafe key, a Jev x402 seller answers ─────────────

SELLER = '0x' + '5e' * 20


class Seller:
    """A v2 Jev x402 seller: 402 with PAYMENT-REQUIRED unpaid, answers when a
    PAYMENT-SIGNATURE for its payTo arrives (or refuses with a reason)."""

    def __init__(self, refuse=None):
        self.refuse, self.calls = refuse, []

    def doc(self):
        return {'x402Version': 2, 'error': 'Payment required', 'resource': {'url': 'https://seller/jev'},
                'accepts': [{'scheme': 'exact', 'network': 'eip155:8453', 'amount': '1000',
                             'asset': pay.ASSETS['base']['asset'], 'payTo': SELLER, 'maxTimeoutSeconds': 120,
                             'extra': {'name': 'USD Coin', 'version': '2'}}]}

    def __call__(self, url, body, headers=None, timeout=60):
        self.calls.append((url, body, dict(headers or {})))
        sig = (headers or {}).get('PAYMENT-SIGNATURE')
        if not sig:
            return 402, {'payment-required': base64.b64encode(json.dumps(self.doc()).encode()).decode()}, {}
        if self.refuse:
            d = {**self.doc(), 'error': self.refuse}
            return 402, {'payment-required': base64.b64encode(json.dumps(d).encode()).decode()}, {}
        answers = {}
        for name, q in body['questions'].items():
            answers[name] = {'noul': {'type': 'noul', 'noul': 0.8},
                             'choice': {'type': 'choice', 'choice': 'billing', 'confidence': 0.9,
                                        'probabilities': {'billing': 0.95, 'other': 0.05}},
                             'score': {'type': 'score', 'score': 1.7, 'confidence': 0.6,
                                       'legend': {'0': 'a', '1': 'b', '2': 'c'},
                                       'probabilities': {'0': 0.1, '1': 0.1, '2': 0.8}}}[q['type']]
        rec = base64.b64encode(json.dumps({'success': True, 'transaction': '0x' + '77' * 32,
                                           'network': 'eip155:8453', 'payer': '0x' + 'ef' * 20}).encode()).decode()
        return 200, {'payment-response': rec}, {'model': 'jev-1.13.0', 'answers': answers}


@pytest.fixture
def relay(monkeypatch):
    import jevrelay
    s = Seller()
    monkeypatch.setattr(jevrelay, '_post', s)
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    pay.set_policy(backend='auto', upstreams='https://seller/jev')
    return s


def anchor():
    import importlib.util
    spec = importlib.util.spec_from_file_location('jev_anchor_t', os.path.join(ROOT, 'mod.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.Mod()


def test_relay_backend_auto(monkeypatch, relay):
    import jevrelay
    assert jevrelay.backend() == 'relay'
    monkeypatch.setenv('TYPESAFE_API_KEY', 'k')
    assert jevrelay.backend() == 'direct'
    pay.set_policy(backend='relay')
    assert jevrelay.backend() == 'relay'


def test_relay_402_carries_the_sellers_offer(relay):
    status, body, _ = pay.charge({}, 3, 'http://x/jev/api/ask', run_ok)
    assert status == 402
    a = body['accepts'][0]
    assert a['payTo'] == SELLER and a['network'] == 'base' and a['maxAmountRequired'] == '1000'
    assert a['resource'] == 'http://x/jev/api/ask'          # ours, not the seller's
    assert body['relay']['via'] == 'seller' and body['price']['usd'] == 0.001


def test_relay_forwards_caller_payment_as_v2(relay):
    m = anchor()
    raw = x_payment(1000, to=SELLER)
    status, body, extra = pay.charge({'X-PAYMENT': raw}, 1, 'http://x/jev/api/noul',
                                     lambda: m.noul('charged twice', 'angry?'))
    assert status == 200, body
    assert body['noul'] == 0.8 and body['relay']['paid_by'] == 'caller'
    assert body['payment']['mode'] == 'x402' and body['payment']['settled'] and body['payment']['via'] == 'seller'
    fwd = pay.decode(relay.calls[-1][2]['PAYMENT-SIGNATURE'])
    assert fwd['x402Version'] == 2 and fwd['accepted']['payTo'] == SELLER
    assert fwd['payload'] == pay.decode(raw)['payload']    # the caller's signature, untouched
    assert relay.calls[-1][1]['questions']['q']['type'] == 'noul'   # jevgo's wire body
    assert 'X-PAYMENT-RESPONSE' in extra
    # replay refused, and relayed money is not counted as the owner's income
    status, body, _ = pay.charge({'X-PAYMENT': raw}, 1, 'http://x/jev/api/noul', lambda: m.noul('x', 'y'))
    assert status == 402 and 'already used' in body['error']
    st = pay.stats()
    assert st['relayed_calls'] == 1 and st['payments'] == 0


def test_relay_maps_choice_and_score(relay):
    m = anchor()
    pay.set_policy(enabled=False)
    import jevrelay
    jevrelay.wallet(create=True)
    jevrelay.pay_with(node=True)
    try:
        c = m.choice('charged twice', 'team?', 'billing,other')
        s = m.score('charged twice', 'how bad?', 'a,b,c')
    finally:
        jevrelay.clear()
    assert c['choice'] == 'billing' and c['probabilities']['billing'] == 0.95
    assert s['score'] == 1.7 and s['level'] == 2
    fwd = pay.decode(relay.calls[-1][2]['PAYMENT-SIGNATURE'])
    assert fwd['payload']['authorization']['to'] == SELLER and fwd['payload']['signature'].startswith('0x')


def test_relay_wrong_payee_is_refused_before_forwarding(relay):
    status, body, _ = pay.charge({'X-PAYMENT': x_payment(1000)}, 1, 'http://x/jev/api/noul', run_ok)
    assert status == 402 and 'does not match' in body['error']
    assert all('PAYMENT-SIGNATURE' not in c[2] for c in relay.calls)


def test_relay_seller_refusal_is_a_402(relay):
    relay.refuse = 'insufficient_funds'
    m = anchor()
    status, body, _ = pay.charge({'X-PAYMENT': x_payment(1000, to=SELLER)}, 1, 'http://x/jev/api/noul',
                                 lambda: m.noul('x', 'y'))
    assert status == 402 and 'insufficient_funds' in body['error'] and body['accepts'][0]['payTo'] == SELLER
    assert pay.stats()['relayed_calls'] == 0


def test_relay_local_without_wallet_explains(relay):
    m = anchor()
    status, body, _ = pay.charge({}, 1, 'http://x/jev/api/noul', lambda: m.noul('x', 'y'), local=True)
    assert status == 402 and body['code'] == 'no_wallet'


def test_relay_credits_use_node_wallet(relay):
    import jevrelay
    jevrelay.wallet(create=True)
    m = anchor()
    acct, tok = mod_token()
    pay.credit(acct.address, 5000, 'grant')
    status, body, extra = pay.charge({'token': tok}, 1, 'http://x/jev/api/noul', lambda: m.noul('x', 'y'))
    assert status == 200 and body['payment']['mode'] == 'credits' and body['relay']['paid_by'] == 'node'
    assert extra['X-Jev-Credits-Left'] == '0.004'


def test_relay_refusal_says_why_in_plain_words(relay):
    relay.refuse = 'invalid_payload: contract call failed: execution reverted'
    m = anchor()
    status, body, _ = pay.charge({'X-PAYMENT': x_payment(1000, to=SELLER)}, 1, 'http://x/jev/api/noul',
                                 lambda: m.noul('x', 'y'))
    assert status == 402 and 'no USDC on Base' in body['error']


def test_relay_topup_off_without_node_wallet(relay):
    # credits paid to the owner could never be spent: the node has nothing to pay the seller with
    status, body, _ = pay.topup({'X-PAYMENT': x_payment(500000)})
    assert status == 409 and body['code'] == 'credits_off'
    import jevrelay
    jevrelay.wallet(create=True)
    assert jevrelay.credits_ok()


def test_server_price_says_who_answers(server, relay):
    s, body, _ = http(server + '/jev/api/price')
    assert s == 200
    assert body['answers'] == {'backend': 'relay', 'live': True, 'via': 'seller', 'per': 'call', 'usd': 0.001,
                               'paid_to': 'the Jev seller'}
    assert body['credits_available'] is False
