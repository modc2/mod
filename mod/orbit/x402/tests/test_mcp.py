"""Offline tests for the agent layer: identity, the trust score, tier gates,
paying (own wallet / relayed payload / house) and the HTTP MCP framing.
No network — every outbound request goes through a stub of x402pay.send."""

import base64
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(HERE)

import x402db as store      # noqa: E402
import x402mcp as mcp       # noqa: E402
import x402pay as pay       # noqa: E402
import x402trust as trust   # noqa: E402
from eth_account import Account                    # noqa: E402
from eth_account.messages import encode_defunct     # noqa: E402

USDC = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'
OFFER = {'scheme': 'exact', 'network': 'eip155:8453', 'asset': USDC, 'amount': '1000',
         'payTo': '0x000000000000000000000000000000000000dEaD', 'maxTimeoutSeconds': 60,
         'extra': {'name': 'USD Coin', 'version': '2'}}
URL = 'https://paid.example/api'


def _b64(d):
    return base64.b64encode(json.dumps(d).encode()).decode()


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    store.reset(str(tmp_path / 'x.db'))
    trust.reset(str(tmp_path / 'agents.db'), home=str(tmp_path))
    monkeypatch.setattr(pay, 'WALLET', str(tmp_path / 'wallet.json'))
    monkeypatch.setattr(pay, 'STATE', str(tmp_path))
    monkeypatch.setattr(pay, 'guard', lambda url: url if '127.0.0.1' not in url
                        else (_ for _ in ()).throw(pay.PayError('non-public')))
    store.upsert([{'url': URL, 'host': 'paid.example', 'name': 'paid', 'networks': ['base'],
                   'price_usd': 0.001, 'offers': [], 'tags': []}], 'cdp')
    mcp._quotes.clear()
    yield


class Server:
    """A fake x402 resource: 402 until it sees a payment header, then 200
    with a v2 settlement receipt. Records what it was sent."""

    def __init__(self, version=2, settle=True, offer=OFFER):
        self.version, self.settle, self.offer, self.seen = version, settle, offer, []

    def __call__(self, url, method='GET', body=None, headers=None, timeout=60):
        self.seen.append({'url': url, 'method': method, 'body': body, 'headers': dict(headers or {})})
        h = {k.lower(): v for k, v in (headers or {}).items()}
        paid = h.get('payment-signature') or h.get('x-payment')
        if not paid:
            req = {'x402Version': self.version, 'accepts': [self.offer],
                   'resource': {'url': url}}
            if self.version >= 2:
                return 402, {'PAYMENT-REQUIRED': _b64(req)}, b'{}'
            return 402, {}, json.dumps(req).encode()
        rc = {'success': self.settle, 'transaction': '0xtx' if self.settle else None,
              'network': 'eip155:8453'}
        return 200, {'Content-Type': 'application/json', 'PAYMENT-RESPONSE': _b64(rc)}, b'{"temp": 21}'


def who(uid='local'):
    if uid == 'local':
        return trust.identify(local=True)
    kind = 'wallet' if uid.startswith('0x') else uid.split(':')[0]
    return trust._seen(uid, kind)


def run(name, args, caller):
    r = mcp.call_tool(name, args, caller)
    return r, (r.get('structuredContent') if not r['isError'] else r['content'][0]['text'])


# ── identity ──────────────────────────────────────────────────────

def _token(acct, raw=False, data='x402', t=None):
    t = str(int(t or time.time()))
    msg = json.dumps({'data': data, 'time': t}, separators=(',', ':'))
    if raw:
        from eth_keys import keys
        from eth_utils import keccak
        sig = keys.PrivateKey(acct.key).sign_msg_hash(keccak(text=msg)).to_bytes()
    else:
        sig = Account.sign_message(encode_defunct(text=msg), private_key=acct.key).signature
    claim = {'data': data, 'time': t, 'key': acct.address, 'signature': '0x' + bytes(sig).hex()}
    return base64.urlsafe_b64encode(json.dumps(claim).encode()).decode().rstrip('=')


def test_token_eip191_and_raw_and_tamper():
    a = Account.create()
    assert trust.verify_token(_token(a)) == a.address.lower()
    assert trust.verify_token(_token(a, raw=True)) == a.address.lower()
    assert trust.verify_token(_token(a, data={'mod': 'x402'})) == a.address.lower()
    assert trust.verify_token(_token(a, t=time.time() - 30 * 86400)) == ''      # stale
    claim = json.loads(base64.urlsafe_b64decode(_token(a) + '=='))
    claim['key'] = Account.create().address
    forged = base64.urlsafe_b64encode(json.dumps(claim).encode()).decode()
    assert trust.verify_token(forged) == ''
    assert trust.verify_token('garbage') == ''


def test_identify_order():
    a = Account.create()
    assert trust.identify({'token': _token(a)})['id'] == a.address.lower()
    assert trust.identify({'Authorization': 'Bearer ' + _token(a)})['kind'] == 'wallet'
    k = trust.register('bot')
    assert trust.identify({'Authorization': 'Bearer ' + k['key']})['id'] == k['id']
    trust.revoke(k['id'])
    assert trust.identify({'Authorization': 'Bearer ' + k['key']}, ip='1.2.3.4')['kind'] == 'anon'
    assert trust.identify({}, local=True)['id'] == 'local'
    assert trust.identify({}, ip='1.2.3.4', local=False)['id'].startswith('anon:')


# ── the score ─────────────────────────────────────────────────────

def test_base_scores_and_tiers():
    k = trust.register('bot')
    assert trust.trust(k['id'])['score'] == 20 and trust.trust(k['id'])['tier'] == 'basic'
    w = who('0xabc')
    assert trust.trust(w['id'])['score'] == 30
    assert trust.trust('local')['tier'] == 'owner'
    anon = who('anon:1')
    for _ in range(5):
        trust.log(anon['id'], 'call', 'settled', URL)
    assert trust.trust(anon['id'])['score'] == trust.ANON_CAP          # capped


def test_settled_lifts_and_penalties_sink():
    w = who('0xabc')['id']
    trust.log(w, 'call', 'settled', URL)
    trust.log(w, 'call', 'settled', URL)
    t = trust.trust(w)
    assert t['parts']['settled'] > 12 and t['tier'] == 'trusted'        # 30 + 12.7 + activity
    for _ in range(3):
        trust.log(w, 'x', 'denied')
    assert trust.trust(w)['score'] < t['score'] - 10


def test_reads_are_not_activity():
    w = who('0xabc')['id']
    for _ in range(10):
        trust.log(w, 'x402_search', 'read')
        trust.log(w, 'call', 'quoted', URL)
    assert trust.trust(w)['parts']['activity'] == 0


def test_owner_pin_adjust_ban_clear():
    w = who('0xabc')['id']
    assert trust.set_trust(w, pin=77)['tier'] == 'core'
    assert trust.set_trust(w, clear=True)['score'] == 30
    assert trust.set_trust(w, adjust=15)['score'] == 45
    assert trust.set_trust(w, ban=True)['score'] == 0


def test_vouch_weighted_and_loop_proof():
    a, b = who('0xaaa')['id'], who('0xbbb')['id']
    trust.vouch(a, b, 10)                    # a is 30 (< 50): does not count
    assert 'vouches' not in trust.trust(b)['parts']
    trust.vouch(b, a, 10)                    # ring: still nobody >= 50
    assert trust.trust(a)['score'] == 30
    trust.vouch('local', b, 10)             # the owner counts in full
    assert trust.trust(b)['parts']['vouches'] == 5
    with pytest.raises(ValueError):
        trust.vouch(a, a, 5)
    trust.vouch('local', b, 0)
    assert 'vouches' not in trust.trust(b)['parts']


def test_rate_limit_throttles_and_costs(monkeypatch):
    anon = who('anon:x')
    for _ in range(20):
        assert not mcp.call_tool('x402_stats', {}, anon)['isError']
    r = mcp.call_tool('x402_stats', {}, anon)
    assert r['isError'] and 'rate limit' in r['content'][0]['text']
    assert trust.trust(anon['id'])['parts']['throttled'] == -2


# ── gates ─────────────────────────────────────────────────────────

def test_anon_reads_but_cannot_quote(monkeypatch):
    monkeypatch.setattr(pay, 'send', Server())
    anon = who('anon:y')
    assert not run('x402_search', {'query': 'paid'}, anon)[0]['isError']
    r, msg = run('x402_quote', {'url': URL}, anon)
    assert r['isError'] and msg.startswith('refused')
    assert 'refused' in [e['outcome'] for e in trust.events(anon['id'])]
    assert 'denied' not in trust.trust(anon['id'])['parts'] or \
        trust.trust(anon['id'])['parts']['denied'] == 0                  # refusal is free


def test_basic_only_calls_listed_hosts_and_ssrf_is_denied(monkeypatch):
    monkeypatch.setattr(pay, 'send', Server())
    k = trust.register('bot')
    me = who(k['id'])
    r, msg = run('x402_call', {'url': 'https://unlisted.example/x', 'pay': 'none'}, me)
    assert r['isError'] and 'not in the x402 index' in msg
    r, msg = run('x402_call', {'url': 'http://127.0.0.1/admin'}, me)
    assert r['isError'] and msg.startswith('denied')
    assert trust.trust(me['id'])['parts']['denied'] == -5


def test_owner_may_reach_local_urls(monkeypatch):
    srv = Server()
    monkeypatch.setattr(pay, 'send', srv)
    r, out = run('x402_call', {'url': 'http://127.0.0.1:9/x', 'pay': 'none'}, who())
    assert not r['isError'] and out['payment_required']


# ── paying ────────────────────────────────────────────────────────

def test_pay_none_lists_offers(monkeypatch):
    monkeypatch.setattr(pay, 'send', Server())
    _, out = run('x402_call', {'url': URL, 'pay': 'none'}, who('0xabc'))
    assert out['payment_required'] and out['offers'][0]['price_usd'] == pytest.approx(0.001)
    assert out['offers'][0]['signable']


@pytest.mark.parametrize('version', [1, 2])
def test_own_wallet_quote_sign_call_settles(monkeypatch, version):
    srv = Server(version=version)
    monkeypatch.setattr(pay, 'send', srv)
    acct = Account.create()
    me = trust.identify({'token': _token(acct)})
    _, q = run('x402_quote', {'url': URL, 'method': 'POST', 'body': {'city': 'nyc'}}, me)
    assert q['payer'] == acct.address.lower() and q['typed_data']['domain']['chainId'] == 8453
    sig = pay.sign(q['typed_data'], acct.key.hex())
    _, out = run('x402_call', {'quote_id': q['quote_id'], 'signature': sig}, me)
    assert out['status'] == 200 and out['json'] == {'temp': 21}
    assert out['paid'] == {'by': 'you', 'usd': pytest.approx(0.001), 'settled': True}
    last = srv.seen[-1]
    assert last['method'] == 'POST' and last['body'] == {'city': 'nyc'}
    name = 'PAYMENT-SIGNATURE' if version == 2 else 'X-PAYMENT'
    doc = pay.decode(last['headers'][name])
    assert doc['x402Version'] == version
    assert doc['payload']['authorization']['value'] == '1000'
    assert (doc['accepted'] == OFFER) if version == 2 else (doc['network'] == 'eip155:8453')
    t = trust.trust(me['id'])
    assert t['parts']['settled'] == 8 and t['parts']['activity'] == 2.5
    # a quote is single-use and bound to its caller
    r, msg = run('x402_call', {'quote_id': q['quote_id'], 'signature': sig}, me)
    assert r['isError'] and 'expired' in msg


def test_quote_rejects_wrong_signer_and_other_callers(monkeypatch):
    monkeypatch.setattr(pay, 'send', Server())
    acct = Account.create()
    me = who('0xabc')
    _, q = run('x402_quote', {'url': URL, 'payer': acct.address}, me)
    bad = pay.sign(q['typed_data'], Account.create().key.hex())
    r, msg = run('x402_call', {'quote_id': q['quote_id'], 'signature': bad}, me)
    assert r['isError'] and 'signature is from' in msg
    _, q = run('x402_quote', {'url': URL, 'payer': acct.address}, me)
    r, msg = run('x402_call', {'quote_id': q['quote_id'], 'signature': 'x'}, who('0xother'))
    assert r['isError'] and 'another caller' in msg


def test_fake_settlement_on_unlisted_host_earns_nothing(monkeypatch):
    srv = Server()
    monkeypatch.setattr(pay, 'send', srv)
    me = who('0xabc')
    trust.set_trust(me['id'], adjust=10)                                  # 40: may call any host
    run('x402_call', {'url': 'https://my-own-fake.example/x', 'payment': _b64({'x402Version': 2})}, me)
    assert 'PAYMENT-SIGNATURE' in srv.seen[-1]['headers']
    assert trust.trust(me['id'])['parts']['settled'] == 0


def test_house_pays_within_budget(monkeypatch):
    srv = Server()
    monkeypatch.setattr(pay, 'send', srv)
    me = who('0xabc')
    trust.log(me['id'], 'call', 'settled', URL)                          # paid its own way once
    trust.set_trust(me['id'], adjust=5)
    assert trust.standing(me['id'])['tier'] == 'trusted'
    _, out = run('x402_call', {'url': URL}, me)
    assert out['payment_required'] and 'house=false' in out['note']
    trust.set_policy(house=True, house_daily_usd={'trusted': 0.0015})
    _, out = run('x402_call', {'url': URL}, me)
    assert 'no house wallet' in out['note'] or 'no wallet' in out['note']
    pay.wallet(create=True)
    _, out = run('x402_call', {'url': URL}, me)
    assert out['status'] == 200 and out['paid']['by'] == 'house'
    assert trust.spent_today(me['id']) == pytest.approx(0.001)
    _, out = run('x402_call', {'url': URL}, me)                            # 0.0005 left < 0.001
    assert out['payment_required'] and 'budget' in out['note']


def test_house_needs_a_self_paid_call_first(monkeypatch):
    monkeypatch.setattr(pay, 'send', Server())
    pay.wallet(create=True)
    trust.set_policy(house=True)
    me = who('0xabc')
    trust.set_trust(me['id'], adjust=20)                                   # 50 but never paid
    _, out = run('x402_call', {'url': URL}, me)
    assert out['payment_required'] and 'first self-paid' in out['note']


def test_house_respects_max_call(monkeypatch):
    dear = dict(OFFER, amount='100000')                                    # $0.10 > 0.05 cap
    monkeypatch.setattr(pay, 'send', Server(offer=dear))
    pay.wallet(create=True)
    trust.set_policy(house=True)
    _, out = run('x402_call', {'url': URL}, who())
    assert out['payment_required'] and trust.spent_today() == 0


def test_admin_tools_are_owner_only():
    me = who('0xabc')
    for name, args in (('x402_set_trust', {'user': me['id'], 'pin': 99}),
                       ('x402_wallet', {}), ('x402_policy', {'set': {'house': True}})):
        r, msg = run(name, args, me)
        assert r['isError'] and msg.startswith('refused'), name
    assert trust.policy()['house'] is False


def test_register_is_limited(monkeypatch):
    anon = who('anon:z')
    _, k = run('x402_register', {'label': 'bot'}, anon)
    assert k['key'].startswith('x402_') and k['standing']['score'] == 20
    r, msg = run('x402_register', {}, anon)
    assert r['isError'] and 'limited' in msg


# ── JSON-RPC + HTTP ───────────────────────────────────────────────

def test_jsonrpc_shapes():
    me = who()
    init = mcp.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                               'params': {'protocolVersion': '1999-01-01'}}, me)
    assert init['result']['protocolVersion'] == mcp.PROTOCOL_VERSION          # negotiate, never echo
    assert mcp.handle_message({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, me) is None
    tools = mcp.handle_message({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}, who('anon:q'))
    by = {t['name']: t for t in tools['result']['tools']}
    assert by['x402_search']['_meta']['allowed'] and not by['x402_call']['_meta']['allowed']
    assert by['x402_call']['_meta']['cost'] == 'spends'
    assert mcp.handle_message({'jsonrpc': '2.0', 'id': 3, 'method': 'nope'}, me)['error']['code'] == -32601


def test_http_mcp_and_rest(monkeypatch):
    monkeypatch.setenv('X402_AUTOSYNC', '0')
    spec = importlib.util.spec_from_file_location('x402_serve_mcp_t', os.path.join(HERE, 'serve.py'))
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    httpd = srv.ThreadingHTTPServer(('127.0.0.1', 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_port}'

    def post(path, body, headers=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    try:
        st, h, b = post('/x402/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
        assert st == 200 and h.get('Mcp-Session-Id', '').startswith('x402-')
        st, h, b = post('/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        assert st == 202 and b == b''
        fwd = {'X-Forwarded-For': '9.9.9.9'}
        st, _, b = post('/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                 'params': {'name': 'x402_whoami', 'arguments': {}}}, fwd)
        me = json.loads(b)['result']['structuredContent']
        assert me['kind'] == 'anon' and me['tier'] == 'restricted'
        st, _, b = post('/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                                 'params': {'name': 'x402_whoami', 'arguments': {}}})
        assert json.loads(b)['result']['structuredContent']['tier'] == 'owner'
        # REST faces of the agent tools run as the caller, never as the box
        st, _, b = post('/x402/api/set_trust', {'user': me['id'], 'pin': 99}, fwd)
        assert st == 403
        st, _, b = post('/x402/api/register', {'label': 'r'}, fwd)
        assert st == 200 and json.loads(b)['key'].startswith('x402_')
        with urllib.request.urlopen(base + '/x402/api/users') as r:
            assert json.loads(r.read())['total'] >= 2
        req = urllib.request.Request(base + '/x402/api/whoami', headers=fwd)
        with urllib.request.urlopen(req) as r:
            assert json.loads(r.read())['kind'] == 'anon'
    finally:
        httpd.shutdown()
