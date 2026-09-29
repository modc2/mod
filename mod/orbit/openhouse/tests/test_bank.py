"""
Tests for the bank rail: parsers, adapters, the key gate, reconciliation,
and the REST/MCP surfaces over it.

Offline throughout — the network adapters are driven through core.http_json
(monkeypatched) or core.INPROC (the reference mockbank), never a socket.
Every Mod gets its own store via Mod(store=tmp_path), so nothing touches
~/.openhouse or ~/.mod/openhouse.
"""
import importlib
import importlib.util
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MODULE_DIR.parent.parent.parent))     # /root/mod — `import mod`
sys.path.insert(0, str(MODULE_DIR / 'api'))

from mcp_server import TOOLS, build_router  # noqa: E402


def _load(name, path):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


openhouse_mod = _load('openhouse_under_test', MODULE_DIR / 'mod.py')
SAMPLES = MODULE_DIR / 'bank' / 'samples'
MAYA = '0x6697a6bbd2ff0d84915db3021144171761ede72f'     # demo.py cast — code OH-9B3CD4
JONAH = '0x9525893583d001c7c40e7bd4f3f7391bc6bd3cac'    # OH-E0F923
OWNER = '0x' + 'ab' * 20


@pytest.fixture
def oh(tmp_path):
    o = openhouse_mod.Mod(store=tmp_path / 'store')
    o.set_terms(model='full_credit', fee_pct=2.5, home_price=120, monthly_rent=0.62, owner=OWNER)
    return o


@pytest.fixture
def pkg(oh):
    return oh._bank_pkg()


@pytest.fixture
def core(pkg):
    return importlib.import_module(pkg.__name__ + '.core')


# ── parsing ─────────────────────────────────────────────────────

@pytest.mark.parametrize('raw,want', [
    ('1.234,56', 1234.56), ('1,234.56', 1234.56), ('-12', -12.0), ('1450,00', 1450.0),
    ('1,450', 1450.0), ('$ 99.10', 99.10), ('', 0.0), (12, 12.0),
])
def test_amounts_in_every_locale(core, raw, want):
    assert core._f(raw) == want


def test_dates_in_every_dialect(core):
    d = 1788220800                                  # 2026-09-01T00:00:00Z
    for raw in ('2026-09-01', '20260901', '260901', '2026-09-01T00:00:00Z',
                '20260901000000[-5:EST]', '01.09.2026', d, d * 1000):
        assert core.parse_date(raw) == d, raw


@pytest.mark.parametrize('fname,fmt,credits,ccy', [
    ('camt053.xml', 'camt053', 1450.0, 'EUR'),
    ('mt940.txt', 'mt940', 1450.0, 'EUR'),
    ('statement.ofx', 'ofx', 1600.0, 'USD'),
    ('statement.csv', 'csv', 1250.0, ''),
])
def test_every_statement_format(pkg, fname, fmt, credits, ccy):
    got, accts, txns = pkg.parse_statement((SAMPLES / fname).read_text())
    assert got == fmt
    maya = [t for t in txns if 'OH-9B3CD4' in t['reference'].upper()]
    assert len(maya) == 1 and maya[0]['amount'] == credits and maya[0]['currency'] == ccy
    assert maya[0]['date'] == 1788220800 or fmt == 'ofx'        # OFX carries a time
    assert any(t['amount'] < 0 for t in txns), 'debits come out negative'
    assert len({t['id'] for t in txns}) == len(txns), 'ids are unique'


def test_camt_balance_and_pending(pkg):
    _, accts, txns = pkg.parse_statement((SAMPLES / 'camt053.xml').read_text())
    assert accts[0]['balance'] == 3015.0 and accts[0]['iban'] == 'XT611234567890123456'
    assert [t['status'] for t in txns].count('pending') == 1


def test_mt940_closing_balance(pkg):
    _, accts, _ = pkg.parse_statement((SAMPLES / 'mt940.txt').read_text())
    assert accts[0]['balance'] == 3015.0


def test_csv_without_amount_is_refused(pkg):
    with pytest.raises(pkg.BankError):
        pkg.parse_statement('date,who\n2026-09-01,someone\n')


def test_pain001_is_wellformed_iso20022(pkg):
    xml = pkg.pain001('M1', 'Owner & Co', 'XT611234567890123456', [
        {'id': 'E1', 'to_name': 'A <b>', 'to_iban': 'XT15', 'amount': 10.5,
         'currency': 'EUR', 'reference': 'r'}])
    root = ET.fromstring(xml.encode())
    ns = '{urn:iso:std:iso:20022:tech:xsd:pain.001.001.03}'
    assert root.find(f'.//{ns}CtrlSum').text == '10.50'
    assert root.find(f'.//{ns}Cdtr/{ns}Nm').text == 'A <b>'


def test_reference_code_is_stable_and_case_blind(pkg):
    assert pkg.reference_code(MAYA) == pkg.reference_code(MAYA.upper()) == 'OH-9B3CD4'


# ── the sandbox bank ────────────────────────────────────────────

def test_sandbox_is_a_working_bank(oh):
    c = oh.bank_connect('sandbox', config={'currency': 'EUR', 'opening_balance': 100})
    acct = oh.bank_accounts()[0]
    assert acct['currency'] == 'EUR' and acct['balance'] == 100
    assert acct['iban'].startswith('XT') and len(acct['iban']) == 20
    # a valid mod-97 IBAN
    s = acct['iban'][4:] + acct['iban'][:4]
    assert int(''.join(str(int(ch, 36)) for ch in s)) % 97 == 1
    oh.bank_receive(50, reference='x')
    assert oh.bank_pay(120, 'XT15000', to_name='x')['balance_after'] == 30
    assert 'insufficient' in oh.bank_pay(31, 'XT15000')['error']
    assert [t['amount'] for t in oh.bank_transactions(c['id'])] == [-120, 50]


def test_receive_is_sandbox_only(oh):
    oh.bank_connect('statement', key=oh._bank_key())
    assert 'sandbox' in oh.bank_receive(10)['error']


# ── the gate ────────────────────────────────────────────────────

def test_real_banks_need_the_operator_key(oh):
    for kind in ('statement', 'openbanking', 'mcp'):
        assert 'key=' in oh.bank_connect(kind, config={})['error']
    assert 'key=' in oh.bank_connect('statement', key='wrong')['error']
    c = oh.bank_connect('statement', key=oh._bank_key())
    assert 'error' not in c
    assert 'key=' in oh.bank_transactions(c['id'])['error']
    assert 'key=' in oh.bank_reconcile(c['id'])['error']
    assert 'key=' in oh.bank_link(MAYA)['error']      # a real bank exists now
    assert isinstance(oh.bank_transactions(c['id'], key=oh._bank_key()), list)


def test_the_key_is_never_served(oh, tmp_path):
    oh._bank_key()
    p = oh.secrets_dir / 'bank_key'
    assert oct(p.stat().st_mode & 0o777) == '0o600'
    assert 'bank_key' not in json.dumps(oh.bank_status()).replace('~/.mod/openhouse/bank_key', '')


def test_credentials_are_stored_private_and_read_redacted(oh):
    k = oh._bank_key()
    oh.bank_connect('openbanking', key=k, config={'base_url': 'https://b.example',
                                                  'access_token': 'tok_supersecret_9999'})
    conns_file = oh.secrets_dir / 'connections.json'
    assert oct(conns_file.stat().st_mode & 0o777) == '0o600'
    assert 'supersecret' in conns_file.read_text()
    for read in (oh.bank_connections(key=k), oh.bank_connections(), oh.bank_status()):
        assert 'supersecret' not in json.dumps(read)
    assert oh.bank_connections(key=k)[0]['config']['access_token'] == '••••9999'
    assert 'config' not in oh.bank_connections()[0]


def test_connect_validates_fields(oh):
    k = oh._bank_key()
    assert 'needs: base_url, access_token' in oh.bank_connect('openbanking', key=k)['error']
    assert 'does not take' in oh.bank_connect('sandbox', config={'evil': 1})['error']
    assert 'unknown bank kind' in oh.bank_connect('swift', key=k)['error']


def test_links_mask_personal_data_once_a_real_bank_is_connected(oh):
    oh.bank_link(MAYA, payer_iban='XT27 9876 5432 1098 7654', payer_name='Maya Ortiz')
    assert oh.bank_links()[0]['payer_name'] == 'Maya Ortiz'       # sandbox-only node
    oh.bank_connect('statement', key=oh._bank_key())
    masked = oh.bank_links()[0]
    assert masked['payer_iban'] == '••7654' and masked['payer_name'] == 'M…'
    assert oh.bank_links(key=oh._bank_key())[0]['payer_iban'] == 'XT279876543210987654'


# ── reconciliation ──────────────────────────────────────────────

def test_reconcile_books_once_by_code_iban_and_name(oh):
    oh.bank_connect('sandbox')
    oh.bank_link(MAYA)
    oh.bank_link(JONAH, payer_iban='XT051111222233334444', payer_name='J R')
    oh.bank_receive(2500, reference='rent oh 9b3cd4')                    # code, mangled
    oh.bank_receive(1250, from_iban='XT05 1111 2222 3333 4444')          # iban only
    oh.bank_receive(1250, from_name='j r')                               # name only
    oh.bank_receive(99, reference='who is this')                         # nobody
    res = oh.bank_reconcile(rate=2500)
    assert [b['matched_by'] for b in res['booked']] == ['reference', 'iban', 'name']
    assert [b['units'] for b in res['booked']] == [1.0, 0.5, 0.5]
    assert len(res['unmatched']) == 1
    assert oh.equity(MAYA)['principal'] == 0.975
    again = oh.bank_reconcile(rate=2500)
    assert again['booked'] == [] and again['already_booked'] == 3
    assert oh.rent_stats()['payments'] == 3


def test_dry_run_writes_nothing(oh):
    oh.bank_connect('sandbox')
    oh.bank_link(MAYA)
    oh.bank_receive(2500, reference='OH-9B3CD4')
    assert len(oh.bank_reconcile(rate=2500, dry_run=True)['booked']) == 1
    assert oh.rent_stats()['payments'] == 0 and oh._bank().matched() == {}


def test_debits_and_pending_are_never_rent(oh, pkg):
    b = oh._bank()
    t = pkg.core.txn(amount=-100, reference='OH-9B3CD4')
    p = pkg.core.txn(amount=100, reference='OH-9B3CD4', status='pending')
    links = [{'address': MAYA, 'reference': 'OH-9B3CD4', 'payer_iban': '', 'payer_name': '', 'kind': 'rent'}]
    assert b.match(t, links) is None and b.match(p, links) is None


def test_civic_pause_holds_then_books(oh):
    oh.bank_connect('sandbox')
    oh.bank_link(MAYA)
    oh.civic_charter('0xc1', owner=OWNER)
    oh.civic_override('pause', '0xc1')
    oh.bank_receive(2500, reference='OH-9B3CD4')
    held = oh.bank_reconcile(rate=2500)
    assert len(held['held']) == 1 and 'Civic pause' in held['held'][0]['reason']
    oh.civic_override('unpause', '0xc1')
    assert len(oh.bank_reconcile(rate=2500)['booked']) == 1


def test_ledger_entry_carries_its_bank_source(oh):
    oh.bank_connect('sandbox', config={'currency': 'EUR'})
    oh.bank_link(MAYA, kind='option')
    oh.bank_receive(2300, reference='OH-9B3CD4')
    oh.bank_reconcile(rate=2300)
    e = oh.rent_ledger(MAYA)[0]
    assert e['kind'] == 'option' and e['source']['currency'] == 'EUR'
    assert e['source']['fiat'] == 2300 and e['source']['rate'] == 2300


def test_fx_rail_is_the_default_rate(oh, monkeypatch):
    monkeypatch.setattr(oh, 'fx', lambda refresh=False: {'rates': {'usd': 2000.0}, 'source': 'cache'})
    assert oh._to_eth(1000, 'USD') == {'units': 0.5, 'rate': 2000.0, 'fx': 'cache'}
    assert 'no ETH rate' in oh._to_eth(1000, 'XYZ')['error']
    assert oh._to_eth(1.5, 'ETH')['units'] == 1.5


def test_statement_import_then_reconcile(oh):
    k = oh._bank_key()
    oh.bank_connect('statement', config={'currency': 'USD'}, key=k)
    oh.bank_link(MAYA, key=k)
    oh.bank_link(JONAH, key=k)
    for f in ('camt053.xml', 'mt940.txt', 'statement.ofx', 'statement.csv'):
        assert oh.bank_import((SAMPLES / f).read_text(), key=k)['new'] > 0
    res = oh.bank_reconcile(rate={'EUR': 2300}.get('EUR'), key=k)
    # camt + mt940 + ofx + csv each pay both renters once
    assert len(res['booked']) == 8
    csv_rows = [b for b in res['booked'] if b['account'] == 'csv']
    assert {b['currency'] for b in csv_rows} == {'USD'}, 'CSV takes the connection currency'


# ── Open Banking (offline, through the http seam) ───────────────

def test_berlin_group_dialect(oh, core, monkeypatch):
    calls = []

    def fake(method, url, headers=None, body=None):
        calls.append((method, url, headers, body))
        if url.endswith('/v1/accounts'):
            return {'accounts': [{'resourceId': 'a1', 'iban': 'DE89370400440532013000',
                                  'currency': 'EUR', 'name': 'Rent'}]}
        if url.endswith('/balances'):
            return {'balances': [{'balanceType': 'closingBooked',
                                  'balanceAmount': {'amount': '3000.00', 'currency': 'EUR'}}]}
        if '/transactions' in url:
            return {'transactions': {'booked': [
                {'transactionId': 't1', 'bookingDate': '2026-09-01',
                 'transactionAmount': {'amount': '1450.00', 'currency': 'EUR'},
                 'debtorName': 'Maya', 'debtorAccount': {'iban': 'XT27'},
                 'remittanceInformationUnstructured': 'Miete OH-9B3CD4'},
                {'transactionId': 't2', 'bookingDate': '2026-09-02',
                 'transactionAmount': {'amount': '-20.00', 'currency': 'EUR'},
                 'creditorName': 'Utility', 'creditorAccount': {'iban': 'XT99'}}]}}
        if url.endswith('/sepa-credit-transfers'):
            return {'transactionStatus': 'RCVD', 'paymentId': 'p1',
                    '_links': {'scaRedirect': {'href': 'https://bank/sca/p1'}}}
        raise AssertionError(url)

    monkeypatch.setattr(core, 'http_json', fake)
    k = oh._bank_key()
    c = oh.bank_connect('openbanking', key=k, config={
        'base_url': 'https://psd2.bank.example/', 'access_token': 'T', 'consent_id': 'C'})
    assert oh.bank_accounts(c['id'], key=k)[0]['balance'] == 3000
    rows = oh.bank_transactions(c['id'], key=k)
    assert {r['counterparty'] for r in rows} == {'Maya', 'Utility'}
    assert next(r for r in rows if r['amount'] < 0)['counterparty_iban'] == 'XT99'
    h = calls[0][2]
    assert h['Authorization'] == 'Bearer T' and h['Consent-ID'] == 'C' and h['X-Request-ID']
    oh.bank_link(MAYA, key=k)
    assert len(oh.bank_reconcile(c['id'], rate=2300, key=k)['booked']) == 1
    p = oh.bank_pay(100, 'XT15', account='a1', connection=c['id'], key=k)
    assert p['status'] == 'RCVD' and p['approve_at'] == 'https://bank/sca/p1'
    assert calls[-1][3]['instructedAmount'] == {'currency': 'EUR', 'amount': '100.00'}


def test_obie_dialect_reads_and_refuses_to_pay(oh, core, monkeypatch):
    def fake(method, url, headers=None, body=None):
        if url.endswith('/aisp/accounts'):
            return {'Data': {'Account': [{'AccountId': 'u1', 'Currency': 'GBP', 'Nickname': 'Rent',
                                          'Account': [{'Identification': 'GB29NWBK60161331926819'}]}]}}
        if url.endswith('/balances'):
            return {'Data': {'Balance': [{'Amount': {'Amount': '10.00'}, 'CreditDebitIndicator': 'Debit'}]}}
        return {'Data': {'Transaction': [
            {'TransactionId': 'x1', 'BookingDateTime': '2026-09-01T09:00:00+00:00',
             'Amount': {'Amount': '1200.00', 'Currency': 'GBP'}, 'CreditDebitIndicator': 'Credit',
             'Status': 'Booked', 'TransactionReference': 'OH-9B3CD4'},
            {'TransactionId': 'x2', 'BookingDateTime': '2026-09-02T09:00:00+00:00',
             'Amount': {'Amount': '30.00', 'Currency': 'GBP'}, 'CreditDebitIndicator': 'Debit',
             'Status': 'Booked', 'TransactionInformation': 'fee'},
            {'TransactionId': 'x3', 'Amount': {'Amount': '1'}, 'Status': 'Pending'}]}}

    monkeypatch.setattr(core, 'http_json', fake)
    k = oh._bank_key()
    c = oh.bank_connect('openbanking', key=k, config={
        'base_url': 'https://ob.example', 'access_token': 'T', 'dialect': 'obie'})
    assert oh.bank_accounts(c['id'], key=k)[0]['balance'] == -10
    rows = oh.bank_transactions(c['id'], key=k)
    assert sorted(r['amount'] for r in rows) == [-30.0, 1200.0]
    assert 'not supported' in oh.bank_pay(5, 'XT1', connection=c['id'], key=k)['error']


# ── a bank's own MCP server ─────────────────────────────────────

def test_remote_mcp_bank(oh, pkg, core):
    mb = importlib.import_module(pkg.__name__ + '.mockbank')
    bank = mb.MockBank(token='t0k')
    bank.credit(1000, 'Maya', 'XT27', 'OH-9B3CD4')
    core.INPROC['t-mock'] = bank.inproc
    try:
        k = oh._bank_key()
        bad = oh.bank_connect('mcp', key=k, config={'url': 'inproc://t-mock/mcp', 'token': 'nope'})
        assert 'unauthorized' in oh.bank_accounts(bad['id'], key=k)['error']
        c = oh.bank_connect('mcp', key=k, config={'url': 'inproc://t-mock/mcp', 'token': 't0k'})
        assert oh.bank_accounts(c['id'], key=k)[0]['balance'] == 1000
        assert 'read-only' in oh.bank_pay(1, 'XT1', connection=c['id'], key=k)['error']
        oh.bank_link(MAYA, key=k)
        assert len(oh.bank_reconcile(c['id'], rate=1000, key=k)['booked']) == 1
        assert oh.equity(MAYA)['principal'] == 0.975
        payer = oh.bank_connect('mcp', key=k, config={
            'url': 'inproc://t-mock/mcp', 'token': 't0k', 'pay_tool': 'send_payment'})
        assert oh.bank_pay(400, 'XT15', account='chk-1', connection=payer['id'], key=k)['status'] == 'executed'
        assert 'insufficient' in oh.bank_pay(4000, 'XT15', account='chk-1',
                                             connection=payer['id'], key=k)['error']
    finally:
        core.INPROC.pop('t-mock', None)


def test_unmounted_inproc_bank_is_an_error_not_a_crash(oh):
    k = oh._bank_key()
    c = oh.bank_connect('mcp', key=k, config={'url': 'inproc://nobody/mcp'})
    assert 'no in-process bank' in oh.bank_accounts(c['id'], key=k)['error']


# ── the surfaces ────────────────────────────────────────────────

@pytest.fixture
def mcp(oh):
    app = FastAPI()
    app.include_router(build_router(lambda: oh, '9.9.9'))
    return TestClient(app)


def _call(client, tool, **arguments):
    r = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': tool, 'arguments': arguments}})
    return r.json()['result']


def test_bank_tools_are_listed_and_writes_announce_themselves():
    for name in ('openhouse_bank_connect', 'openhouse_bank_import', 'openhouse_bank_receive',
                 'openhouse_bank_link', 'openhouse_bank_reconcile', 'openhouse_bank_pay',
                 'openhouse_bank_disconnect', 'openhouse_civic_charter', 'openhouse_civic_override'):
        assert TOOLS[name]['description'].startswith('WRITES.'), name
    for name in ('openhouse_bank_kinds', 'openhouse_bank_status', 'openhouse_bank_accounts',
                 'openhouse_bank_transactions', 'openhouse_bank_reference', 'openhouse_bank_links',
                 'openhouse_examples'):
        assert not TOOLS[name]['description'].startswith('WRITES.'), name


def test_bank_over_mcp_end_to_end(mcp):
    assert not _call(mcp, 'openhouse_bank_connect', kind='sandbox')['isError']
    ref = _call(mcp, 'openhouse_bank_reference', address=MAYA)['structuredContent']['reference']
    _call(mcp, 'openhouse_bank_link', address=MAYA)
    _call(mcp, 'openhouse_bank_receive', amount=2500, reference=ref)
    res = _call(mcp, 'openhouse_bank_reconcile', rate=2500)['structuredContent']
    assert len(res['booked']) == 1
    denied = _call(mcp, 'openhouse_bank_connect', kind='openbanking',
                   config={'base_url': 'x', 'access_token': 'y'})
    assert denied['isError'] and 'key=' in denied['content'][0]['text']


def test_rest_bank_routes(oh, monkeypatch):
    api = _load('openhouse_api_under_test', MODULE_DIR / 'api' / 'api.py')
    monkeypatch.setattr(api, '_openhouse', oh)
    c = TestClient(api.app)
    assert c.post('/bank/connect', json={'kind': 'sandbox'}).status_code == 200
    assert c.post('/bank/connect', json={'kind': 'statement'}).status_code == 403
    k = oh._bank_key()
    assert c.post('/bank/connect', json={'kind': 'statement'},
                  headers={'X-Bank-Key': k}).status_code == 200
    assert c.get('/bank/reference/' + MAYA).json()['reference'] == 'OH-9B3CD4'
    assert c.post('/bank/pay', json={'amount': 1, 'to_iban': 'XT1', 'connection': 'nope'}).status_code == 400
    assert {k_['kind'] for k_ in c.get('/bank/kinds').json()} == {'sandbox', 'statement', 'openbanking', 'mcp'}
    assert c.get('/bank').json()['connections'][0]['kind'] == 'sandbox'
    # /forward can never reach the key
    assert c.post('/forward', json={'action': '_bank_key'}).status_code == 404


def test_concurrent_reconciles_book_once(oh):
    import threading
    oh.bank_connect('sandbox')
    oh.bank_link(MAYA)
    for _ in range(5):
        oh.bank_receive(250, reference='OH-9B3CD4')
    out = []
    ts = [threading.Thread(target=lambda: out.append(oh.bank_reconcile(rate=2500))) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(len(r['booked']) for r in out) == 5
    assert oh.rent_stats()['payments'] == 5
