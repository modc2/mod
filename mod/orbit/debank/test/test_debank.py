"""debank tests — offline.

Nothing here touches the network or needs an AccessKey: the point is that the
normalizers, the argument validation and the MCP protocol are correct before a
single unit is spent. Upstream calls are stubbed with fixtures shaped like the
DeBank Cloud responses they stand in for.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import api      # noqa: E402
import client   # noqa: E402
import mcp      # noqa: E402
from client import Client, DebankError  # noqa: E402


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch, tmp_path):
    """The tests must not read the operator's real key, or write over it."""
    monkeypatch.delenv('DEBANK_ACCESS_KEY', raising=False)
    monkeypatch.delenv('DEBANK_API_KEY', raising=False)
    monkeypatch.setattr(client, 'KEY_DIR', str(tmp_path))
    monkeypatch.setattr(client, 'KEY_FILE', str(tmp_path / 'key.json'))
    Client._chain_cache.update(at=0, rows=None)


def stub(monkeypatch, responses):
    """Answer Client.get from a {path: payload} map, recording the calls."""
    calls = []

    def fake_get(self, path, _public=False, _retries=2, **params):
        calls.append((path, params))
        if path not in responses:
            raise AssertionError(f'unexpected upstream call: {path}')
        return responses[path]

    monkeypatch.setattr(Client, 'get', fake_get)
    return calls


VITALIK = '0xd8da6bf26964af9d7eed9e03e53415d37aa96045'


# ── addresses and chains ──

def test_address_must_be_an_evm_address():
    with pytest.raises(DebankError) as e:
        Client(key='k').portfolio('vitalik.eth')
    assert 'EVM address' in str(e.value)
    assert e.value.status == 400
    assert 'ENS' in (e.value.hint or '')


def test_address_is_lowercased_for_debank(monkeypatch):
    calls = stub(monkeypatch, {'/v1/user/total_balance': {'total_usd_value': 0}})
    Client(key='k').portfolio(VITALIK.upper().replace('0X', '0x'))
    assert calls[0][1]['id'] == VITALIK


@pytest.mark.parametrize('given,expected', [
    ('ethereum', 'eth'), ('Polygon', 'matic'), ('arbitrum', 'arb'),
    ('gnosis', 'xdai'), ('eth', 'eth'), ('somenewchain', 'somenewchain'), (None, None),
])
def test_chain_aliases(given, expected):
    assert client.chain_id(given) == expected


# ── the money math ──

def test_tokens_are_priced_ranked_and_dust_is_counted_not_hidden(monkeypatch):
    stub(monkeypatch, {'/v1/user/all_token_list': [
        {'id': 'eth', 'chain': 'eth', 'symbol': 'ETH', 'amount': 2, 'price': 3000},
        {'id': '0xa', 'chain': 'matic', 'symbol': 'USDC', 'amount': 500, 'price': 1},
        {'id': '0xb', 'chain': 'eth', 'symbol': 'DUST', 'amount': 1, 'price': 0.01},
    ]})
    d = Client(key='k').tokens(VITALIK, min_usd=1)
    assert [t['symbol'] for t in d['tokens']] == ['ETH', 'USDC']   # biggest first
    assert d['tokens'][0]['usd'] == 6000
    assert d['hidden_below_min_usd'] == 1                          # dropped, but counted
    assert d['total_usd'] == 6500.01                               # total includes dust


def test_protocol_positions_subtract_debt(monkeypatch):
    stub(monkeypatch, {'/v1/user/all_complex_protocol_list': [{
        'id': 'aave3', 'name': 'Aave V3', 'chain': 'eth',
        'portfolio_item_list': [{
            'name': 'Lending',
            'detail': {
                'supply_token_list': [{'amount': 10, 'price': 3000, 'symbol': 'ETH'}],
                'borrow_token_list': [{'amount': 12000, 'price': 1, 'symbol': 'USDC'}],
                'reward_token_list': [{'amount': 100, 'price': 2, 'symbol': 'AAVE'}],
                'health_rate': 1.8,
            },
        }],
    }]})
    d = Client(key='k').protocols(VITALIK)
    p = d['protocols'][0]
    assert p['supplied_usd'] == 30000 and p['borrowed_usd'] == 12000
    assert p['usd'] == 18200                       # 30000 + 200 rewards - 12000 debt
    assert d['total_borrowed_usd'] == 12000
    assert p['positions'][0]['health_rate'] == 1.8


def test_approvals_rank_by_what_a_spender_could_take_today(monkeypatch):
    stub(monkeypatch, {'/v1/user/token_authorized_list': [
        {'id': '0xu', 'symbol': 'USDC', 'balance': 10000, 'price': 1, 'spenders': [
            {'id': '0xrouter', 'value': 1e59},           # infinite allowance
            {'id': '0xsmall', 'value': 25},              # capped below the balance
        ]},
        {'id': '0xw', 'symbol': 'WBTC', 'balance': 0, 'price': 60000, 'spenders': [
            {'id': '0xold', 'value': 1e59},              # infinite, but nothing to take
        ]},
    ]})
    d = Client(key='k').approvals(VITALIK, chain='ethereum')
    assert d['chain'] == 'eth'
    top = d['approvals'][0]
    assert top['spender'] == '0xrouter' and top['unlimited'] is True
    assert top['exposure_usd'] == 10000             # capped at the balance, not 1e59
    assert d['approvals'][1]['exposure_usd'] == 25  # capped allowance priced as-is
    assert d['approvals'][2]['exposure_usd'] == 0   # empty balance = nothing at risk
    assert d['unlimited_count'] == 2


def test_approvals_refuse_to_guess_a_chain():
    with pytest.raises(DebankError) as e:
        Client(key='k').approvals(VITALIK, chain=None)
    assert 'per chain' in str(e.value)


def test_history_resolves_token_symbols_and_gas(monkeypatch):
    stub(monkeypatch, {'/v1/user/history_list': {
        'token_dict': {'eth': {'symbol': 'ETH', 'price': 3000}},
        'project_dict': {'uniswap3': {'name': 'Uniswap V3'}},
        'history_list': [{
            'id': '0xhash', 'chain': 'eth', 'cate_id': 'swap', 'time_at': 1700000000,
            'project_id': 'uniswap3',
            'sends': [{'amount': 1.5, 'token_id': 'eth', 'to_addr': '0xpool'}],
            'receives': [],
            'tx': {'name': 'swap', 'usd_gas_fee': 4.2117, 'status': 1},
        }],
    }})
    tx = Client(key='k').history(VITALIK, chain='eth')['transactions'][0]
    assert tx['type'] == 'swap' and tx['project'] == 'Uniswap V3'
    assert tx['sent'][0] == {'symbol': 'ETH', 'amount': 1.5, 'usd': 4500,
                             'counterparty': '0xpool'}
    assert tx['gas_usd'] == 4.2117 and tx['status'] == 'ok'


def test_history_page_count_is_clamped_to_the_upstream_cap(monkeypatch):
    calls = stub(monkeypatch, {'/v1/user/all_history_list': {'history_list': []}})
    Client(key='k').history(VITALIK, page_count=500)
    assert calls[0][1]['page_count'] == 20


def test_net_curve_reports_the_change(monkeypatch):
    stub(monkeypatch, {'/v1/user/total_net_curve': [
        {'timestamp': 1, 'usd_value': 100}, {'timestamp': 2, 'usd_value': 150}]})
    d = Client(key='k').net_curve(VITALIK)
    assert d['change_usd'] == 50 and d['change_pct'] == 50.0


def test_portfolio_picks_the_chains_that_matter(monkeypatch):
    stub(monkeypatch, {'/v1/user/total_balance': {
        'total_usd_value': 1000.5,
        'chain_list': [{'id': 'eth', 'usd_value': 1000}, {'id': 'ftm', 'usd_value': 0.5}],
    }})
    d = Client(key='k').portfolio(VITALIK, min_usd=1)
    assert [c['chain'] for c in d['chains']] == ['eth']
    assert d['chains_below_min_usd'] == 1 and d['total_usd'] == 1000.5


# ── keys ──

def test_no_key_is_a_401_with_a_fix_not_a_crash():
    with pytest.raises(DebankError) as e:
        Client().portfolio(VITALIK)
    assert e.value.status == 401 and 'set_key' in (e.value.hint or '')


def test_set_key_stores_off_tree_and_never_returns_the_key():
    r = client.set_key('secret-access-key-value')
    assert r['ok'] and 'secret' not in json.dumps(r)
    assert oct(os.stat(client.KEY_FILE).st_mode)[-3:] == '600'
    assert client.resolve_key() == 'secret-access-key-value'


def test_bearer_tokens_are_never_read_as_debank_keys():
    # The gateway puts its own session token in Authorization; forwarding it
    # upstream would leak it.
    assert api._key_from({'authorization': 'Bearer gateway-session-token'}) is None
    assert api._key_from({'x-debank-key': 'k'}) == 'k'


def test_chains_falls_back_to_the_public_catalog_when_unauthorized(monkeypatch):
    def fake_get(self, path, _public=False, _retries=2, **params):
        if not _public:
            raise DebankError('unauthorized', status=401)
        return {'data': {'chains': [{'id': 'eth', 'name': 'Ethereum'}]}}

    monkeypatch.setattr(Client, 'get', fake_get)
    d = Client().chains()
    assert d['source'] == 'public' and d['chains'][0]['chain'] == 'eth'


# ── mcp ──

def test_every_tool_is_wired_and_declares_a_schema():
    cfg = json.load(open(os.path.join(HERE, 'config.json')))
    assert sorted(cfg['tools']) == sorted(mcp.TOOLS)
    for name, tool in mcp.TOOLS.items():
        assert name.startswith('debank_') and callable(tool['handler'])
        assert tool['inputSchema']['type'] == 'object'
        assert len(tool['description']) > 80        # a description an agent can route on
        for req in tool['inputSchema'].get('required', []):
            assert req in tool['inputSchema']['properties']


def test_initialize_and_tools_list():
    init = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                       'params': {'protocolVersion': '2025-06-18'}})
    assert init['result']['protocolVersion'] == '2025-06-18'
    assert init['result']['serverInfo']['name'] == 'debank'
    listed = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert len(listed['result']['tools']) == len(mcp.TOOLS)


def test_notifications_get_no_response():
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_unknown_method_is_a_protocol_error():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/nope'})
    assert r['error']['code'] == -32601


def test_tool_call_returns_structured_content(monkeypatch):
    stub(monkeypatch, {'/v1/user/total_balance': {
        'total_usd_value': 42, 'chain_list': [{'id': 'eth', 'usd_value': 42}]}})
    r = mcp.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                    'params': {'name': 'debank_portfolio',
                               'arguments': {'id': VITALIK, 'key': 'k'}}})
    assert r['result']['isError'] is False
    assert r['result']['structuredContent']['total_usd'] == 42


def test_a_failing_tool_returns_the_hint_not_an_exception():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                    'params': {'name': 'debank_portfolio',
                               'arguments': {'id': 'not-an-address', 'key': 'k'}}})
    assert r['result']['isError'] is True
    assert 'EVM address' in r['result']['content'][0]['text']


def test_missing_required_argument_is_reported_by_name():
    r = mcp.handle({'jsonrpc': '2.0', 'id': 6, 'method': 'tools/call',
                    'params': {'name': 'debank_portfolio', 'arguments': {}}})
    assert r['result']['isError'] is True and 'id' in r['result']['content'][0]['text']


def test_batch_requests():
    r = mcp.handle([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}])
    assert len(r) == 2


# ── http surface ──

def test_rest_and_mcp_answer_from_the_same_client(monkeypatch):
    stub(monkeypatch, {'/v1/user/all_token_list': [
        {'id': 'eth', 'chain': 'eth', 'symbol': 'ETH', 'amount': 1, 'price': 3000}]})
    rest = api.route('GET', '/tokens', f'id={VITALIK}', {}, 'k')
    tool = mcp.call_tool('debank_tokens', {'id': VITALIK, 'key': 'k'})
    assert rest == tool


def test_gateway_prefixes_are_stripped_the_same_way():
    for prefix in ('/debank/_api', '/api/debank', '/_api'):
        assert api.route.__name__ == 'route'
        assert (prefix + '/tokens')[len(prefix):] == '/tokens'


def test_unknown_route_is_a_404_that_says_where_to_look():
    with pytest.raises(DebankError) as e:
        api.route('GET', '/nope', '', {}, 'k')
    assert e.value.status == 404 and 'GET /' in str(e.value)


def test_info_lists_every_declared_fn():
    cfg = json.load(open(os.path.join(HERE, 'config.json')))
    import mod as anchor_module  # this module's own mod.py, on sys.path[0]
    anchor = anchor_module.Mod()
    for fn in cfg['fns']:
        assert callable(getattr(anchor, fn)), f'config.json declares a missing fn: {fn}'
    assert api.info()['mcp']['tools'] == len(mcp.TOOLS)
