"""The NEAR rent rail — native NEAR in, booked once.

Offline: the indexer and RPC are answered by a stub on core.http_json, shaped
exactly like the nearblocks rows and the NEAR view_account reply captured from
testnet. Mod stores are tmp (see test_bank fixtures).
"""
import pytest

from test_bank import MAYA, JONAH, core, oh, pkg  # noqa: F401  (fixtures)

RENT = 'rent.openhouse.testnet'
Y = 10 ** 24
NS = 1_000_000_000


def row(rid, payer, near, ts=1788220800, receiver=RENT, ok=True, action='TRANSFER'):
    return {'receipt_id': rid, 'transaction_hash': 'TX' + rid, 'predecessor_account_id': payer,
            'receiver_account_id': receiver, 'receipt_kind': 'ACTION',
            'receipt_block': {'block_timestamp': ts * NS},
            'receipt_outcome': {'status': ok},
            'actions': [{'action': action, 'deposit': int(near * Y), 'method': None}]}


@pytest.fixture
def chain(monkeypatch, core):
    state = {'rows': [], 'calls': [], 'balance': 12 * Y}

    def fake(method, url, headers=None, body=None):
        state['calls'].append((method, url))
        assert (headers or {}).get('User-Agent'), 'Cloudflare 1010s the default urllib agent'
        if method == 'POST':
            return {'result': {'amount': str(state['balance'])}}
        assert '/v1/account/' + RENT + '/txns' in url
        return {'txns': state['rows'], 'cursor': ''}
    monkeypatch.setattr(core, 'http_json', fake)
    return state


def test_kind_is_registered_and_gated(oh):
    assert 'near' in [k['kind'] for k in oh.bank_kinds()]
    assert 'key=' in oh.bank_connect('near', config={'account_id': RENT})['error']
    assert 'needs: account_id' in oh.bank_connect('near', config={}, key=oh._bank_key())['error']


def test_only_real_incoming_transfers_are_credits(oh, chain):
    key = oh._bank_key()
    c = oh.bank_connect('near', config={'account_id': RENT}, key=key)
    chain['rows'] = [
        row('a', 'maya.testnet', 2.5),
        row('b', 'system', 1),                                    # protocol refund
        row('c', RENT, 1),                                        # our own receipt
        row('d', 'jonah.testnet', 1, ok=False),                   # failed
        row('e', 'jonah.testnet', 1, action='FUNCTION_CALL'),     # a call, not a transfer
        row('f', 'jonah.testnet', 0.004),                         # dust still counts, unrounded
        row('g', 'x.testnet', 1, receiver='someone.else'),
    ]
    txns = oh.bank_transactions(c['id'], key=key)
    assert sorted((t['counterparty'], t['amount']) for t in txns) == [
        ('jonah.testnet', 0.004), ('maya.testnet', 2.5)]
    assert all(t['currency'] == 'NEAR' and t['date'] == 1788220800 for t in txns)
    acct = oh.bank_accounts(c['id'], key=key)[0]
    assert acct['currency'] == 'NEAR' and acct['balance'] == 12.0


def test_rpc_down_does_not_hide_the_account(oh, chain, core, monkeypatch):
    key = oh._bank_key()
    c = oh.bank_connect('near', config={'account_id': RENT}, key=key)
    real = core.http_json

    def flaky(method, url, headers=None, body=None):
        if method == 'POST':
            raise core.BankError('bank unreachable: rpc down')
        return real(method, url, headers, body)
    monkeypatch.setattr(core, 'http_json', flaky)
    assert oh.bank_accounts(c['id'], key=key)[0]['balance'] is None


def test_linked_near_account_books_once_at_the_live_rate(oh, chain):
    key = oh._bank_key()
    oh.bank_connect('near', config={'account_id': RENT}, key=key)
    oh.bank_link(MAYA, payer_name='Maya.testnet', key=key)
    chain['rows'] = [row('a', 'maya.testnet', 490), row('b', 'stranger.testnet', 5)]
    res = oh.bank_reconcile(rate=490.0, key=key)       # 490 NEAR per Ξ
    assert [(b['matched_by'], b['units']) for b in res['booked']] == [('name', 1.0)]
    assert [t['counterparty'] for t in res['unmatched']] == ['stranger.testnet']
    again = oh.bank_reconcile(rate=490.0, key=key)
    assert again['booked'] == [] and again['already_booked'] == 1
    assert oh.rent_stats()['payments'] == 1


def test_near_converts_through_the_fx_rail(oh, monkeypatch):
    monkeypatch.setattr(oh, 'fx', lambda refresh=False: {'rates': {'near': 500.0}, 'source': 'live'})
    assert oh._to_eth(250, 'NEAR') == {'units': 0.5, 'rate': 500.0, 'fx': 'live'}


def test_near_status_degrades_to_errors_not_exceptions(oh, chain, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('rpc down')
    monkeypatch.setattr(oh, '_near_view', boom)
    monkeypatch.setattr(oh, 'fx', lambda refresh=False: {'rates': {'near': 500.0}, 'source': 'live'})
    s = oh.near_status()
    assert s['verifier']['live'] is False and 'rpc down' in s['verifier']['error']
    rail = s['rent_rail']
    assert rail['connected'] is False
    assert rail['monthly_rent_near'] == round(0.62 * 500.0, 2)
    key = oh._bank_key()
    oh.bank_connect('near', config={'account_id': RENT}, key=key)
    oh.bank_link(MAYA, payer_name='maya.testnet', key=key)
    chain['rows'] = [row('a', 'maya.testnet', 1), row('b', 'who.testnet', 2)]
    rail = oh.near_status()['rent_rail']
    assert rail['connected'] and rail['account_id'] == RENT and rail['balance'] == 12.0
    assert {t['counterparty']: (t['linked'], t['booked']) for t in rail['recent']} == {
        'maya.testnet': (True, False), 'who.testnet': (False, False)}


def test_fx_derives_near_per_eth(monkeypatch):
    import importlib.util, json, pathlib
    spec = importlib.util.spec_from_file_location('fx_t', pathlib.Path(__file__).parent.parent / 'fx.py')
    fx = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fx)

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps({'ethereum': {'usd': 2500.0, 'eur': 2300.0}, 'near': {'usd': 5.0}}).encode()
    monkeypatch.setattr(fx.urllib.request, 'urlopen', lambda *a, **k: R())
    r = fx._fetch_live()
    assert r['near'] == 500.0 and r['usd'] == 2500.0
