"""x402: strangers pay per upload in USDC; the box, the owner and BYOK do not.

The facilitator and Lighthouse are stubbed — no network, no real payment. A
"remote" caller is simulated the way caddy and the console's app server make
one: an X-Forwarded-For that is not loopback.
"""
import base64
import json
import secrets

import pytest

import lhpay

PAY_TO = '0x' + '11' * 20
PAYER = '0x' + '22' * 20
REMOTE = {'X-Forwarded-For': '203.0.113.7'}


@pytest.fixture
def paywall(state_dir, monkeypatch):
    """Payment on, a fake facilitator, a fake Lighthouse; yields the call log."""
    lhpay.reset(home=str(state_dir / 'x402'))
    lhpay.set_policy(pay_to=PAY_TO, enabled=True, networks='base',
                     price_usd=0.001, price_per_mib_usd=0.001)
    calls = {'verify': 0, 'settle': 0, 'put': 0, 'fail_put': False, 'valid': True}

    def fac(path, body, p=None, timeout=30):
        assert body['paymentRequirements']['payTo'] == PAY_TO
        if path == '/verify':
            calls['verify'] += 1
            return 200, {'isValid': calls['valid'], 'payer': PAYER,
                         **({} if calls['valid'] else {'invalidReason': 'bad_sig'})}
        calls['settle'] += 1
        return 200, {'success': True, 'transaction': '0xabc', 'network': 'base', 'payer': PAYER}
    monkeypatch.setattr(lhpay, 'facilitator_post', fac)

    import api.api as api_mod

    class FakeLH:
        api_key = 'k'
        gateway = 'https://gateway.lighthouse.storage'

        def put(self, path, owner=None, key=None):
            if calls['fail_put']:
                raise RuntimeError('lighthouse down')
            calls['put'] += 1
            return {'cid': 'bafytest' + secrets.token_hex(4), 'key': key, 'size': 5,
                    'url': 'https://gateway.lighthouse.storage/ipfs/x', 'owner': owner}

        def anon_check(self, size):
            pass

        def status(self):
            return {'name': 'lighthouse', 'configured': True}
    monkeypatch.setattr(api_mod, 'lighthouse', lambda byok=None: FakeLH())
    yield calls
    lhpay.set_policy(pay_to=None, enabled=None, networks=None,
                     price_usd=None, price_per_mib_usd=None)


def _pay(offer, value=None, nonce=None):
    doc = {'x402Version': 1, 'scheme': offer['scheme'], 'network': offer['network'],
           'payload': {'signature': '0x' + '00' * 65, 'authorization': {
               'from': PAYER, 'to': offer['payTo'], 'value': str(value or offer['maxAmountRequired']),
               'validAfter': '0', 'validBefore': '9999999999',
               'nonce': nonce or '0x' + secrets.token_hex(32)}}}
    return base64.b64encode(json.dumps(doc).encode()).decode()


def test_price_is_by_size():
    p = {**lhpay.DEFAULTS, 'price_usd': 0.001, 'price_per_mib_usd': 0.002}
    assert lhpay.cost(1, p) == 1000 + 2000
    assert lhpay.cost((1 << 20) + 1, p) == 1000 + 2 * 2000


def test_unpaid_remote_upload_is_a_spec_402(client, paywall):
    r = client.post('/put/text', json={'text': 'hello', 'register': False}, headers=REMOTE)
    assert r.status_code == 402
    body = r.json()
    assert body['x402Version'] == 1
    offer = body['accepts'][0]
    assert offer['scheme'] == 'exact' and offer['network'] == 'base'
    assert offer['payTo'] == PAY_TO
    assert offer['asset'] == lhpay.ASSETS['base']['asset']
    assert offer['extra'] == {'name': 'USD Coin', 'version': '2'}
    assert int(offer['maxAmountRequired']) == lhpay.cost(5)
    assert paywall['put'] == 0 and paywall['verify'] == 0


def test_price_route_hands_out_the_same_402(client, paywall):
    q = client.get('/price?size=5', headers=REMOTE).json()
    assert q['due'] is True and q['x402']['accepts'][0]['payTo'] == PAY_TO
    assert q['units'] == lhpay.cost(5)


def test_paid_upload_verifies_uploads_then_settles(client, paywall):
    offer = client.get('/price?size=5', headers=REMOTE).json()['x402']['accepts'][0]
    pay = _pay(offer)
    r = client.post('/put/text', json={'text': 'hello', 'register': False},
                    headers={**REMOTE, 'X-PAYMENT': pay})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out['payment']['mode'] == 'x402' and out['payment']['settled'] is True
    assert out['payment']['tx'] == '0xabc'
    receipt = json.loads(base64.b64decode(r.headers['x-payment-response']))
    assert receipt['success'] is True
    assert (paywall['verify'], paywall['put'], paywall['settle']) == (1, 1, 1)
    # the same signed payment cannot buy a second upload
    again = client.post('/put/text', json={'text': 'hello', 'register': False},
                        headers={**REMOTE, 'X-PAYMENT': pay})
    assert again.status_code == 402 and 'already used' in again.json()['error']
    assert lhpay.stats()['payments'] >= 1


def test_multipart_put_is_gated_too(client, paywall):
    r = client.post('/put', files={'file': ('a.txt', b'hello')}, data={'register': 'false'},
                    headers=REMOTE)
    assert r.status_code == 402
    offer = r.json()['accepts'][0]
    r = client.post('/put', files={'file': ('a.txt', b'hello')}, data={'register': 'false'},
                    headers={**REMOTE, 'X-PAYMENT': _pay(offer)})
    assert r.status_code == 200 and r.json()['payment']['settled'] is True


def test_underpayment_and_wrong_payee_are_refused(client, paywall):
    offer = client.get('/price?size=5', headers=REMOTE).json()['x402']['accepts'][0]
    short = client.post('/put/text', json={'text': 'hello', 'register': False},
                        headers={**REMOTE, 'X-PAYMENT': _pay(offer, value=1)})
    assert short.status_code == 402 and 'does not match' in short.json()['error']
    elsewhere = _pay({**offer, 'payTo': '0x' + '33' * 20})
    r = client.post('/put/text', json={'text': 'hello', 'register': False},
                    headers={**REMOTE, 'X-PAYMENT': elsewhere})
    assert r.status_code == 402
    assert paywall['verify'] == 0 and paywall['put'] == 0


def test_an_invalid_signature_never_uploads(client, paywall):
    paywall['valid'] = False
    offer = client.get('/price?size=5', headers=REMOTE).json()['x402']['accepts'][0]
    r = client.post('/put/text', json={'text': 'hello', 'register': False},
                    headers={**REMOTE, 'X-PAYMENT': _pay(offer)})
    assert r.status_code == 402 and 'bad_sig' in r.json()['error']
    assert paywall['put'] == 0 and paywall['settle'] == 0


def test_a_failed_upload_is_never_charged(client, paywall):
    paywall['fail_put'] = True
    offer = client.get('/price?size=5', headers=REMOTE).json()['x402']['accepts'][0]
    r = client.post('/put/text', json={'text': 'hello', 'register': False},
                    headers={**REMOTE, 'X-PAYMENT': _pay(offer)})
    assert r.status_code == 502 and 'not charged' in r.json()['detail']
    assert paywall['verify'] == 1 and paywall['settle'] == 0


def test_the_box_and_byok_upload_free(client, paywall):
    # TestClient with nothing forwarded is the box itself
    r = client.post('/put/text', json={'text': 'hello', 'register': False})
    assert r.status_code == 200 and r.json()['payment'] == {'mode': 'free', 'why': 'local'}
    r = client.post('/put/text', json={'text': 'hello', 'register': False},
                    headers={**REMOTE, 'x-lh-key': 'mine'})
    assert r.status_code == 200 and r.json()['payment']['why'] == 'byok'
    assert client.get('/price?size=5', headers=REMOTE).json()['due'] is True
    assert client.get('/price?size=5').json()['due'] is False
    assert paywall['settle'] == 0


def test_a_forwarded_loopback_chain_is_still_remote():
    assert lhpay.is_local('127.0.0.1', {}) is True
    assert lhpay.is_local('127.0.0.1', {'X-Forwarded-For': '127.0.0.1'}) is True
    assert lhpay.is_local('127.0.0.1', {'X-Forwarded-For': '203.0.113.7, 127.0.0.1'}) is False
    assert lhpay.is_local('127.0.0.1', {'CF-Connecting-IP': '203.0.113.7'}) is False
    assert lhpay.is_local('10.0.0.5', {}) is False


def test_mcp_put_answers_the_402_then_takes_payment(paywall, monkeypatch):
    import mcp
    monkeypatch.setattr(mcp.Ctx, 'paying_key', lambda self: _FakeMcpLH())
    ctx = mcp.Ctx(local=False, exempt='')
    with pytest.raises(mcp.Refused) as e:
        mcp.call_tool('lighthouse_put', {'text': 'hello', 'register': False}, ctx)
    body = json.loads(str(e.value).split('\n', 1)[1])
    assert body['x402Version'] == 1 and body['accepts'][0]['payTo'] == PAY_TO
    out = mcp.call_tool('lighthouse_put', {'text': 'hello', 'register': False,
                                           'payment': _pay(body['accepts'][0])}, ctx)
    assert out['payment']['mode'] == 'x402' and out['payment']['settled'] is True
    # stdio is the box: free
    out = mcp.call_tool('lighthouse_put', {'text': 'hi', 'register': False},
                        mcp.Ctx(local=True))
    assert out['payment']['mode'] == 'free'


class _FakeMcpLH:
    api_key = 'k'

    def put(self, path, owner=None, key=None):
        return {'cid': 'bafymcp' + secrets.token_hex(4), 'key': key, 'size': 5}

    def anon_check(self, size):
        pass


def test_status_advertises_x402(client, paywall):
    pay = client.get('/status').json()['pay']
    assert pay['enabled'] is True and pay['protocol'] == 'x402' and pay['pay_to'] == PAY_TO


def test_an_empty_post_is_how_crawlers_ask_the_price(client, paywall):
    r = client.post('/put/text', headers=REMOTE)
    assert r.status_code == 402 and r.json()['accepts'][0]['payTo'] == PAY_TO
    assert client.post('/put/text', json={}, headers=REMOTE).status_code == 402
    assert client.post('/put', headers=REMOTE).status_code == 402
    assert client.post('/put/text').status_code == 422      # the box need not pay
