"""agenti app tests: derivation vectors, wallet store, x402 pay against a local
mock paywall (signature recovered and checked), spend limits, invoices, HTTP
owner gate and MCP. No network: chains and the SSRF guard are stubbed."""
import base64
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))

from eth_account import Account  # noqa: E402

from agentiapp import chains, invoices, pay, server, toolkit, wallets  # noqa: E402

ABANDON = 'abandon ' * 11 + 'about'
USDC_BASE = chains.EVM['base']['usdc']
PAY_TO = '0x44adc7d3199486730d6346f2dc2424de97124700'


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(wallets, 'HOME', tmp_path)
    monkeypatch.setattr(pay, 'guard', lambda u: u)
    return tmp_path


def test_derivation_matches_metamask_and_phantom():
    k = wallets.derive(ABANDON)
    assert k['evm']['address'] == '0x9858EfFD232B4033E47d90003D41EC34EcaEda94'
    assert k['solana']['address'] == 'HAgk14JpMQLgt6rVgv7cBQFJWFto5Dqxi472uT3DKpqk'


def test_wallet_store_is_private_and_public_view_has_no_secrets(home):
    w = wallets.create('bot')
    assert oct(os.stat(home / 'wallets.json').st_mode)[-3:] == '600'
    assert 'secret' not in json.dumps(wallets.list_wallets()) and 'mnemonic' not in json.dumps(w)
    assert w['default'] and len(wallets.reveal(w['id'])['mnemonic'].split()) == 12
    w2 = wallets.create('bot')
    assert w2['name'] == 'bot-2'
    wallets.delete(w['id'])
    assert wallets.get()['id'] == w2['id'] and (home / 'deleted' / f"{w['id']}.json").is_file()


def test_import_by_phrase_and_keys():
    a = wallets.import_wallet('p', mnemonic=ABANDON)
    assert a['evm'] == '0x9858EfFD232B4033E47d90003D41EC34EcaEda94'
    sol_secret = wallets.reveal(a['id'])['solana_secret_key']
    b = wallets.import_wallet('k', evm_key=wallets.reveal(a['id'])['evm_private_key'], solana_key=sol_secret)
    assert (b['evm'], b['solana']) == (a['evm'], a['solana'])
    with pytest.raises(ValueError):
        wallets.import_wallet('x')


# ── mock x402 paywall ─────────────────────────────────────────────

class Paywall(BaseHTTPRequestHandler):
    price = 10000
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        sig = self.headers.get('PAYMENT-SIGNATURE')
        if not sig:
            req = {'x402Version': 2, 'resource': {'url': 'http://mock/x'}, 'accepts': [
                {'scheme': 'exact', 'network': 'eip155:8453', 'amount': str(self.price), 'asset': USDC_BASE,
                 'payTo': PAY_TO, 'maxTimeoutSeconds': 60, 'extra': {'name': 'USD Coin', 'version': '2'}},
                {'scheme': 'exact', 'network': 'solana', 'amount': '5000', 'asset': 'x', 'payTo': 'y'}]}
            body = b'{}'
            self.send_response(402)
            self.send_header('PAYMENT-REQUIRED', base64.b64encode(json.dumps(req).encode()).decode())
        else:
            doc = json.loads(base64.b64decode(sig))
            Paywall.seen.append(doc)
            body = json.dumps({'weather': 'sunny'}).encode()
            self.send_response(200)
            self.send_header('PAYMENT-RESPONSE', base64.b64encode(json.dumps(
                {'success': True, 'transaction': '0xabc'}).encode()).decode())
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def paywall():
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Paywall)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    Paywall.seen.clear()
    yield f'http://127.0.0.1:{srv.server_port}/x'
    srv.shutdown()


def test_probe_lists_offers(paywall):
    r = pay.probe(paywall)
    assert r['paywalled'] and r['x402_version'] == 2
    o = r['offers']
    assert o[0]['payable'] and o[0]['usd'] == 0.01 and o[0]['chain'] == 'base'
    assert not o[1]['payable'] and 'solana' in o[1]['why_not']


def test_pay_signs_valid_eip3009_and_records(paywall):
    w = wallets.import_wallet('p', mnemonic=ABANDON)
    r = pay.pay(paywall)
    assert r['paid'] and r['receipt']['transaction'] == '0xabc' and r['response']['json'] == {'weather': 'sunny'}
    doc = Paywall.seen[0]
    assert doc['x402Version'] == 2 and doc['accepted']['payTo'] == PAY_TO
    auth, sig = doc['payload']['authorization'], doc['payload']['signature']
    assert auth['value'] == '10000' and auth['from'] == w['evm']
    typed = {'types': pay.TYPES, 'primaryType': 'TransferWithAuthorization',
             'domain': {'name': 'USD Coin', 'version': '2', 'chainId': 8453, 'verifyingContract': USDC_BASE},
             'message': {**auth, 'value': int(auth['value']), 'validAfter': int(auth['validAfter']),
                         'validBefore': int(auth['validBefore'])}}
    from eth_account.messages import encode_typed_data
    assert Account.recover_message(encode_typed_data(full_message=typed), signature=sig) == w['evm']
    assert pay.spent_today() == 0.01 and pay.ledger()[0]['ok']


def test_limits_refuse_before_signing(paywall):
    wallets.create('p')
    pay.set_policy(max_per_call_usd=0.005)
    with pytest.raises(pay.PayError, match='per-call'):
        pay.pay(paywall)
    pay.set_policy(max_per_call_usd=1, max_per_day_usd=0.015)
    pay.pay(paywall)
    with pytest.raises(pay.PayError, match='daily'):
        pay.pay(paywall)
    pay.set_policy(blocked_hosts='127.0.0.1')
    with pytest.raises(pay.PayError, match='blocked'):
        pay.pay(paywall)
    assert len(Paywall.seen) == 1


def test_invoice_paid_when_balance_rises(monkeypatch):
    wallets.create('p')
    bal = {'v': 1_000_000}
    monkeypatch.setattr(chains, 'balance', lambda c, a, fresh=False: {'ok': True, 'usdc_raw': bal['v']})
    inv = invoices.create(2.5, 'base', memo='hi')
    assert inv['uri'].startswith(f'ethereum:{USDC_BASE}@8453/transfer?') and inv['uri'].endswith('uint256=2500000')
    assert invoices.check(inv['id'])['status'] == 'open'
    bal['v'] += 2_500_000
    assert invoices.check(inv['id'])['status'] == 'paid'
    assert '<svg' in invoices.qr_svg(inv['uri'])
    sol = invoices.create(1, 'solana')
    assert sol['uri'].startswith('solana:') and 'spl-token=' in sol['uri']


def test_toolkit_reads_upstream():
    s = toolkit.summary()
    assert s['packages'] >= 10 and s['mcp_tools'] >= 50 and s['examples'] >= 20
    names = {t['name'] for t in toolkit.mcp_tools()}
    assert {'pay', 'create_wallet', 'jupiter_swap'} <= names
    assert toolkit.example(toolkit.examples()[0]['file'])['source']


# ── HTTP ──────────────────────────────────────────────────────────

@pytest.fixture
def app():
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_port}'
    srv.shutdown()


def _req(url, body=None, headers=None):
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                               headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_http_owner_gate(app):
    assert _req(app + '/agenti/api/whoami')[1]['owner'] is True
    code, w = _req(app + '/agenti/api/create_wallet', {'name': 'x'})
    assert code == 200 and w['name'] == 'x'
    proxied = {'X-Forwarded-For': '8.8.8.8'}
    assert _req(app + '/agenti/api/wallets', headers=proxied)[0] == 403
    assert _req(app + '/agenti/api/reveal', {'id': w['id']}, proxied)[0] == 403
    assert _req(app + '/agenti/api/toolkit', headers=proxied)[0] == 200
    assert _req(app + '/agenti/api/create_wallet')[0] == 405  # writes are POST
    with urllib.request.urlopen(urllib.request.Request(app + '/agenti/', headers={'Accept': 'text/html'})) as r:
        assert b'agenti' in r.read()


def test_mcp(app):
    _, d = _req(app + '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
    assert 'agenti_pay' in {t['name'] for t in d['result']['tools']}
    _, d = _req(app + '/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                               'params': {'name': 'agenti_policy', 'arguments': {}}})
    assert d['result']['structuredContent']['max_per_call_usd'] == 0.25
    _, d = _req(app + '/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                               'params': {'name': 'agenti_wallets', 'arguments': {}}},
                {'X-Forwarded-For': '8.8.8.8'})
    assert d['result']['isError']
