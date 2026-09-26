"""The whole chain through its three surfaces: the tools, REST routing and MCP.

One node, one throwaway chain (see conftest), and the full life of a key:
quoted, written, proven, checked, funded, listed, bought, deleted — plus the
replay audit that re-verifies every root and every ML-DSA signature.
"""

import time

import pytest

import api
import mcp as mcpsrv
import state as S
from state import StateError, check_proof


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


@pytest.fixture(scope='session')
def alice():
    call('pq_wallet', action='create', name='alice')
    call('pq_faucet', wallet='alice', amount='500')
    return call('pq_wallet', action='show', name='alice')


@pytest.fixture(scope='session')
def bob():
    call('pq_wallet', action='create', name='bob')
    call('pq_faucet', wallet='bob', amount='500')
    return call('pq_wallet', action='show', name='bob')


def test_head_and_genesis():
    h = call('pq_head')
    assert h['chain_id'] == 'postquant-test'
    assert h['height'] >= 0
    assert h['scheme'].startswith('ML-DSA')
    g = mcpsrv.node().genesis
    assert g['rules']['hash'] == 'SHA3-256'


def test_wallet_addresses_are_pq(alice):
    assert alice['address'].startswith('pq')
    assert len(alice['address']) == 42
    assert 'seed' not in alice


def test_faucet_funded(alice):
    a = call('pq_account', wallet='alice')
    assert a['balance']['nq'] > 0


def test_quote_prices_all_three(alice):
    q = call('pq_quote', key='docs/readme', data='hello world', hours=24,
             wallet='alice')
    assert q['gas']['key'] == len('docs/readme') * S.GAS_KEY_BYTE
    assert q['value_bytes'] == 32                     # data= became a digest
    assert q['gas']['witness'] > 0                    # the witness is billed
    assert q['rent']['deposit']['nq'] > 0
    assert q['total']['nq'] > q['write_cost']['nq']


def test_set_get_check_prove(alice):
    r = call('pq_set', key='docs/readme', data='hello world', hours=24,
             wallet='alice')
    assert r['status'] == 'included'
    assert r['receipt']['ok'] is True
    assert r['witness_bytes'] >= 2420                 # sig, plus pk if first tx

    e = call('pq_get', key='docs/readme')
    assert e['owner'] == alice['address']
    assert e['value_kind'] == 'hash'
    assert not e['expired']

    c = call('pq_check', key='docs/readme', data='hello world')
    assert c['matches'] is True
    c2 = call('pq_check', key='docs/readme', data='tampered')
    assert c2['matches'] is False

    p = call('pq_prove', key='docs/readme')
    assert p['valid'] is True
    assert check_proof(p['leaf'], p['path'], p['root'])


def test_only_owner_can_overwrite(alice, bob):
    with pytest.raises(StateError) as e:
        call('pq_set', key='docs/readme', data='mine now', wallet='bob')
    assert e.value.code == 'not_owner'


def test_fund_extends_the_lease(alice, bob):
    before = call('pq_get', key='docs/readme')['expires_at']
    call('pq_fund', key='docs/readme', hours=24, wallet='bob')  # anyone may pay
    after = call('pq_get', key='docs/readme')['expires_at']
    assert after > before


def test_list_and_buy_move_ownership(alice, bob):
    call('pq_list', key='docs/readme', price='25', wallet='alice')
    assert call('pq_get', key='docs/readme')['listed'] is True
    a0 = call('pq_account', wallet='alice')['balance']['nq']
    r = call('pq_buy', key='docs/readme', wallet='bob')
    assert r['receipt']['ok'] is True
    e = call('pq_get', key='docs/readme')
    assert e['owner'] == bob['address']
    assert e['listed'] is False
    assert call('pq_account', wallet='alice')['balance']['nq'] == \
        a0 + 25 * S.PQ


def test_del_refunds_escrow_and_bond(bob):
    b0 = call('pq_account', wallet='bob')['balance']['nq']
    r = call('pq_del', key='docs/readme', wallet='bob')
    assert r['receipt']['refund']['nq'] > 0
    with pytest.raises(StateError):
        call('pq_get', key='docs/readme')
    assert call('pq_account', wallet='bob')['balance']['nq'] > b0


def test_transfer(alice, bob):
    b0 = call('pq_account', wallet='bob')['balance']['nq']
    call('pq_transfer', to='bob', amount='1.5', wallet='alice')
    assert call('pq_account', wallet='bob')['balance']['nq'] == \
        b0 + 1_500_000_000


def test_sweep_pays_the_reaper(alice, bob):
    """Expiry is enforced by the market: the sweeper collects the bond."""
    n = mcpsrv.node()
    call('pq_set', key='ephemeral', data='soon gone', seconds=3600,
         wallet='alice')
    # An hour passes: produce a block dated after the lease runs out, then
    # sweep against that chain time.
    future = int(time.time()) + 7200
    n.produce(now=future, force=True)
    import keys as K
    w = call('pq_wallet', action='show', name='bob')
    b0 = n.state.balance(w['address'])
    tx = n.make_tx(K.get('bob'), 'sweep', key='ephemeral')
    n.submit(tx, now=future)
    n.produce(now=future + 1)
    assert n.state.balance(w['address']) > b0          # the bounty landed
    assert 'ephemeral' not in n.state.store


def test_raw_values_cost_more_than_hashes():
    qh = call('pq_quote', key='k', value='00' * 32, value_kind='hash')
    qr = call('pq_quote', key='k', value='00' * 32, value_kind='raw')
    assert qr['gas']['value'] > qh['gas']['value']


def test_market_card():
    m = call('pq_market')
    assert m['gas_schedule']['key_byte'] == S.GAS_KEY_BYTE
    assert m['example']['total']['nq'] > 0
    assert isinstance(m['recent_growth'], list)


def test_verify_replays_clean():
    v = call('pq_verify', signatures=True)
    assert v['ok'], v['problems']
    assert v['matches_live_state']


def test_rest_routes_share_the_tools():
    assert api.route('GET', '/', '', {})['name'] == 'postquant'
    assert api.route('GET', '/health', '', {})['ok'] is True
    h = api.route('GET', '/head', '', {})
    assert h['height'] == call('pq_head')['height']
    ks = api.route('GET', '/keys', 'include_expired=true', {})
    assert 'keys' in ks
    with pytest.raises(api.ApiError):
        api.route('GET', '/nope', '', {})
    with pytest.raises(api.ApiError):                  # writes are POST-only
        api.route('GET', '/set', 'key=x', {})


def test_mcp_surface():
    init = mcpsrv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                          'params': {'protocolVersion': '2025-06-18'}})
    assert init['result']['serverInfo']['name'] == 'postquant'
    tools = mcpsrv.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert len(tools['result']['tools']) == len(mcpsrv.TOOLS) == 24
    got = mcpsrv.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                         'params': {'name': 'pq_head', 'arguments': {}}})
    assert got['result']['isError'] is False
    assert got['result']['structuredContent']['chain_id'] == 'postquant-test'
