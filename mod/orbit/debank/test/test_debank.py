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


# ── the bank rail (keyless) ──

def stub_rpc(monkeypatch, native_wei, token_units):
    """Answer client.rpc from fixed hex balances, and pin prices."""
    calls = []

    def fake_rpc(url, batch):
        calls.append((url, batch))
        return [hex(native_wei)] + [hex(token_units)] * (len(batch) - 1)

    monkeypatch.setattr(client, 'rpc', fake_rpc)
    monkeypatch.setattr(client, 'prices', lambda refresh=False: {
        'ethereum': 2000.0, 'binancecoin': 600.0, 'matic-network': 0.5,
        'avalanche-2': 10.0, 'xdai': 1.0, 'usd-coin': 1.0, 'tether': 1.0, 'dai': 1.0})
    return calls


def test_balances_need_no_key_and_price_native_plus_stables(monkeypatch):
    calls = stub_rpc(monkeypatch, native_wei=10 ** 18, token_units=5 * 10 ** 6)
    d = Client().balances(VITALIK, chains=['eth'])          # no key anywhere
    assert d['source'] == 'rpc' and d['errors'] is None
    assert [c['chain'] for c in d['chains']] == ['eth']
    eth = d['chains'][0]
    # 1 ETH at $2000, 5 USDC, 5 USDT, and 5e6 units of 18-decimal DAI (rounds to nothing)
    assert eth['native_amount'] == 1.0 and eth['usd'] == 2010.0
    syms = [t['symbol'] for t in d['tokens']]
    assert syms[0] == 'ETH' and set(syms) == {'ETH', 'USDC', 'USDT', 'DAI'}
    # one batched POST per chain: getBalance + one balanceOf per stablecoin
    assert len(calls) == 1 and len(calls[0][1]) == 4
    assert calls[0][1][1][0] == 'eth_call' and calls[0][1][1][1][0]['data'].startswith('0x70a08231')


def test_balances_scan_every_rail_chain_and_report_the_ones_that_fail(monkeypatch):
    def flaky(url, batch):
        if 'bsc' in url:
            raise OSError('timed out')
        return [hex(2 * 10 ** 18)] + ['0x0'] * (len(batch) - 1)
    monkeypatch.setattr(client, 'rpc', flaky)
    monkeypatch.setattr(client, 'prices', lambda refresh=False: {'ethereum': 1000.0})
    d = client.balances(VITALIK)
    assert 'bsc' in d['errors'] and 'timed out' in d['errors']['bsc']
    assert len(d['chains']) == len(client.NETWORKS) - 1
    assert d['chains'][0]['chain'] in ('eth', 'base', 'arb', 'op')   # ETH chains price highest
    assert d['coverage'].startswith('native coin + USDC/USDT/DAI on ')


def test_balances_refuse_chains_off_the_rail():
    with pytest.raises(DebankError) as e:
        client.balances(VITALIK, chains=['solana'])
    assert e.value.status == 400 and 'bank rail' in str(e.value)


def test_networks_give_a_wallet_what_it_needs():
    d = client.networks()
    assert d['count'] == len(client.NETWORKS)
    eth = next(n for n in d['networks'] if n['chain'] == 'eth')
    assert eth['chain_id'] == 1 and eth['chain_id_hex'] == '0x1'
    assert eth['rpc'].startswith('https://') and eth['explorer'].startswith('https://')
    usdc = next(t for t in eth['tokens'] if t['symbol'] == 'USDC')
    assert usdc['decimals'] == 6 and usdc['address'].startswith('0x') and len(usdc['address']) == 42
    for n in d['networks']:
        assert client.chain_id(n['chain']) == n['chain']       # rail ids are canonical DeBank ids


def test_rail_routes_answer_signed_out(monkeypatch):
    stub_rpc(monkeypatch, native_wei=0, token_units=0)
    assert api.route('GET', '/networks', '', {}, None)['count'] == len(client.NETWORKS)
    rest = api.route('GET', '/balances', f'id={VITALIK}&chains=eth,base', {}, None)
    tool = mcp.call_tool('debank_balances', {'id': VITALIK, 'chains': 'eth,base'})
    assert rest == tool and [c['chain'] for c in rest['chains']] == ['eth', 'base']
    assert 'keyless' in api.route('GET', '/health', '', {}, None)


def test_console_is_the_bank():
    html = open(os.path.join(HERE, 'console.html')).read()
    for needle in ('eth_requestAccounts', 'wallet_switchEthereumChain', 'eth_sendTransaction',
                   '0xa9059cbb', '0x095ea7b3', '/_api', 'x-debank-key'):
        assert needle in html


# ── the savings desk ──

import savings  # noqa: E402


YS = {vid: {'apy': 4.0, 'apy_30d': 5.0, 'tvl_usd': 1e8, 'apy_source': 'test'}
      for vid in savings.registry()['venues']}
LOCKED = {vid: {'locked_usd': 2e8} for vid in savings.registry()['venues']}


@pytest.fixture
def stub_savings(monkeypatch, tmp_path):
    monkeypatch.setattr(savings, 'live_yields', lambda refresh=False: YS)
    monkeypatch.setattr(savings, 'locked_onchain', lambda refresh=False: LOCKED)
    monkeypatch.setattr(savings, 'LEDGER_DIR', str(tmp_path / 'ledger'))


def selector_rpc(monkeypatch, balance_of=0, allowance=0, native=0):
    """Answer client.rpc by ABI selector, echoing convertToAssets 1:1."""
    def fake_rpc(url, batch):
        out = []
        for method, params in batch:
            if method != 'eth_call':
                out.append(hex(native))
                continue
            data = params[0]['data']
            sel = data[:10]
            if sel == savings.SEL['balance_of']:
                out.append(hex(balance_of))
            elif sel == savings.SEL['allowance']:
                out.append(hex(allowance))
            elif sel == savings.SEL['convert_to_assets']:
                out.append('0x' + data[10:74])          # 1 share = 1 asset unit
            elif sel in (savings.SEL['total_assets'], savings.SEL['total_supply']):
                out.append(hex(7 * 10 ** 12))
            else:
                out.append(hex(0))
        return out
    monkeypatch.setattr(client, 'rpc', fake_rpc)
    monkeypatch.setattr(client, 'prices', lambda refresh=False: {
        'ethereum': 2000.0, 'usd-coin': 1.0, 'tether': 1.0, 'dai': 1.0})


def test_fund_registry_is_coherent_and_on_the_rail():
    reg = savings.registry()
    for vid, v in reg['venues'].items():
        assert v['kind'] in ('erc4626', 'aave_v3', 'compound_v3')
        assert v['chain'] in client.NETWORKS
        for c in (v['address'], v['asset']['address'], v['receipt']['address']):
            assert c.startswith('0x') and len(c) == 42
        # a venue's asset must be the SAME contract the bank rail reads, so
        # "your savings" and "what a fund takes" can never disagree
        rail = client.NETWORKS[v['chain']]['tokens'].get(v['asset']['symbol'])
        assert rail and rail[0].lower() == v['asset']['address'].lower()
        assert rail[1] == v['asset']['decimals']
        assert v['exit']['kind'] and 'note' in v['exit']
    for f in reg['funds']:
        assert abs(sum(s['weight'] for s in f['sleeves']) - 1.0) < 1e-9
        for s in f['sleeves']:
            v = reg['venues'][s['venue']]
            assert v['chain'] == f['chain'] and v['asset']['symbol'] == f['asset']


def test_funds_carry_projected_roi_and_locked_liquidity(stub_savings):
    d = savings.funds(amount=10000)
    assert d['count'] == len(savings.registry()['funds'])
    f = next(x for x in d['funds'] if x['id'] == 'core-usdc-eth')
    assert f['projected_apy'] == 5.0 and f['current_apy'] == 4.0
    assert f['projected_1y_usd'] == 500.0                     # 10k at 5%
    assert f['liquidity_locked_usd'] == 3 * 2e8               # three sleeves
    assert f['exit']['kind'] == 'instant'
    for s in f['sleeves']:
        assert s['liquidity']['locked_usd'] == 2e8
        assert s['projected_1y_usd'] == round(10000 * s['weight'] * 0.05, 2)
    yp = next(x for x in d['funds'] if x['id'] == 'yield-plus-usdc-eth')
    assert yp['exit']['kind'] == 'request' and yp['exit']['delay_days'] == 1


def test_a_single_venue_is_a_fund_of_one(stub_savings):
    f = savings.fund('venue:sky-sdai-eth', amount=500)
    assert f['tier'] == 'single' and len(f['sleeves']) == 1
    assert f['sleeves'][0]['weight'] == 1.0 and f['asset'] == 'DAI'
    with pytest.raises(DebankError):
        savings.fund('venue:nope')
    with pytest.raises(DebankError):
        savings.fund('not-a-fund')


def test_plan_builds_exact_approve_then_deposit_per_sleeve(stub_savings, monkeypatch):
    selector_rpc(monkeypatch, balance_of=2000 * 10 ** 6, allowance=0)
    d = savings.plan(VITALIK, 'core-usdc-eth', 1000)
    assert d['funded'] and d['wallet_has'] == 2000.0 and d['shortfall'] is None
    assert len(d['legs']) == 3 and d['signatures'] == 6      # approve + deposit each
    for leg in d['legs']:
        v = savings.registry()['venues'][leg['venue']]
        units = int(leg['units'])
        assert units == int(1000 * leg['weight']) * 10 ** 6
        approve, deposit = leg['txs']
        assert approve['to'] == v['asset']['address']
        assert approve['data'] == savings.SEL['approve'] + \
            savings._pad(v['address']) + savings._pad(units)
        assert deposit['to'] == v['address']
        sel = {'erc4626': 'deposit_4626', 'aave_v3': 'supply_aave',
               'compound_v3': 'supply_comet'}[v['kind']]
        assert deposit['data'].startswith(savings.SEL[sel])
        if v['kind'] == 'aave_v3':                            # onBehalfOf = the owner
            assert VITALIK[2:] in deposit['data']


def test_plan_flags_a_shortfall_and_usdt_gets_its_reset_leg(stub_savings, monkeypatch):
    selector_rpc(monkeypatch, balance_of=100 * 10 ** 6, allowance=5)
    d = savings.plan(VITALIK, 'tether-usdt-eth', 900)
    assert not d['funded'] and d['shortfall'] == 800.0
    for leg in d['legs']:                                     # dirty USDT allowance
        assert [t['step'] for t in leg['txs']][0].startswith('reset approval')
        assert leg['txs'][0]['data'].endswith(savings._pad(0))
    with pytest.raises(DebankError):
        savings.plan(VITALIK, 'core-usdc-eth', 0)
    with pytest.raises(DebankError):
        savings.plan(VITALIK, 'core-usdc-eth', 'lots')


def test_holdings_and_savings_read_from_chain_keyless(stub_savings, monkeypatch):
    selector_rpc(monkeypatch, balance_of=250 * 10 ** 6, allowance=0)
    h = savings.holdings(VITALIK)
    assert h['errors'] is None and len(h['held']) == len(savings.registry()['venues'])
    usdc = h['held']['aave-v3-usdc-eth']
    assert usdc['amount'] == 250.0 and usdc['symbol'] == 'USDC'
    d = savings.savings(VITALIK)
    assert d['source'].startswith('rpc')
    assert d['placed']['usd'] > 0 and d['placed']['blended_apy'] == 5.0
    assert d['total_usd'] == round(d['idle']['usd'] + d['placed']['usd'], 2)
    assert d['ledger'] == []


def test_exit_tx_withdraws_everything(stub_savings, monkeypatch):
    selector_rpc(monkeypatch, balance_of=9 * 10 ** 18)
    e = savings.exit_tx('sky-sdai-eth', VITALIK)              # redeem all shares
    assert e['tx']['data'].startswith(savings.SEL['redeem_4626'])
    assert savings._pad(9 * 10 ** 18) in e['tx']['data']
    a = savings.exit_tx('aave-v3-usdc-eth', VITALIK)          # MAX = full balance
    assert a['tx']['data'].startswith(savings.SEL['withdraw_aave'])
    assert savings.MAX_UINT in a['tx']['data']
    c = savings.exit_tx('compound-v3-usdc-eth', VITALIK)
    assert c['tx']['data'].startswith(savings.SEL['withdraw_comet'])
    selector_rpc(monkeypatch, balance_of=0)
    with pytest.raises(DebankError):
        savings.exit_tx('sky-sdai-eth', VITALIK)


def test_ledger_records_placements_off_tree(stub_savings):
    r = savings.record(VITALIK, 'core-usdc-eth', 'aave-v3-usdc-eth', 400, '0xabc')
    assert r['count'] == 1 and r['recorded']['chain'] == 'eth'
    assert savings.LEDGER_DIR in r['stored']
    assert oct(os.stat(r['stored']).st_mode & 0o777) == '0o600'
    assert savings.ledger(VITALIK)[0]['tx'] == '0xabc'


def test_savings_routes_and_tools_answer_signed_out(stub_savings, monkeypatch):
    selector_rpc(monkeypatch, balance_of=10 ** 6)
    rest = api.route('GET', '/funds', 'amount=1000', {}, None)
    tool = mcp.call_tool('debank_funds', {'amount': 1000})
    assert rest == tool and rest['count'] >= 4
    one = api.route('GET', '/funds/core-usdc-base', '', {}, None)
    assert one['chain'] == 'base' and one['chain_name'] == 'Base'
    plan = api.route('GET', '/savings/plan',
                     f'id={VITALIK}&fund=core-usdc-base&amount=50', {}, None)
    assert plan == mcp.call_tool('debank_savings_plan',
                                 {'id': VITALIK, 'fund': 'core-usdc-base', 'amount': 50})
    assert plan['chain_id'] == 8453
    sav = api.route('GET', '/savings', f'id={VITALIK}', {}, None)
    assert sav == mcp.call_tool('debank_savings', {'id': VITALIK})


def test_console_has_a_savings_desk():
    html = open(os.path.join(HERE, 'console.html')).read()
    for needle in ('savings', 'planFund', 'placeLegs', 'withdrawVenue',
                   '/savings/plan', '/savings/record', 'Index funds'):
        assert needle in html


def test_ledger_survives_concurrent_legs(stub_savings):
    import threading
    ts = [threading.Thread(target=savings.record,
                           args=(VITALIK, 'core-usdc-base', 'spark-usdc-base', i, f'0x{i}'))
          for i in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(savings.ledger(VITALIK)) == 6


# ── the proof-of-humanity tag on the id ──

WORD_TRUE = '0x' + '0' * 63 + '1'
WORD_FALSE = '0x' + '0' * 64
WORD_UID = '0x' + 'ab' * 32


def stub_humanity_rpc(monkeypatch, answers):
    """Answer client.rpc per chain host: {host_fragment: [results after block]}."""
    def fake_rpc(url, batch):
        for key, rows in answers.items():
            if key in url:
                if isinstance(rows, Exception):
                    raise rows
                assert len(rows) == len(batch) - 1, f'{key}: wrong batch size'
                assert batch[0][0] == 'eth_blockNumber'
                return ['0x14a3bc4'] + rows
        raise AssertionError(f'unexpected rpc host: {url}')
    monkeypatch.setattr(client, 'rpc', fake_rpc)


def test_humanity_reads_the_registries_keyless(monkeypatch):
    stub_humanity_rpc(monkeypatch, {
        'ethereum-rpc': [WORD_TRUE, WORD_FALSE],   # poh-v2 yes, poh-v1 lapsed
        'gnosis-rpc': [WORD_FALSE],
        'base-rpc': [WORD_UID],                    # coinbase attestation uid
    })
    d = client.humanity(VITALIK)                   # no key anywhere
    assert d['human'] is True and d['source'] == 'rpc' and d['errors'] is None
    assert d['verified_by'] == ['Proof of Humanity v2', 'Coinbase Verified Account']
    assert len(d['sources']) == len(client.HUMANITY_REGISTRIES)
    poh2 = next(s for s in d['sources'] if s['source'] == 'poh-v2')
    assert poh2['verified'] and poh2['block'] == 0x14a3bc4 and poh2['result'] == WORD_TRUE
    lapsed = next(s for s in d['sources'] if s['source'] == 'poh-v1')
    assert not lapsed['verified'] and lapsed['result'] is None


def test_humanity_tag_is_a_recomputable_sha3_commitment(monkeypatch):
    import hashlib
    stub_humanity_rpc(monkeypatch, {
        'ethereum-rpc': [WORD_TRUE, WORD_FALSE],
        'gnosis-rpc': [WORD_FALSE], 'base-rpc': [WORD_FALSE]})
    a = client.humanity(VITALIK)['tag']
    b = client.humanity(VITALIK)['tag']
    assert a == b                                   # same chain state, same tag
    assert a['scheme'] == 'sha3-256'
    assert a['basis'].startswith('debank.humanity.v1|' + VITALIK + '|')
    # anyone can recompute the tag from the published basis — that IS the proof
    assert a['value'] == hashlib.sha3_256(a['basis'].encode()).hexdigest()
    # ... and it moves when the evidence moves
    stub_humanity_rpc(monkeypatch, {
        'ethereum-rpc': [WORD_FALSE, WORD_FALSE],
        'gnosis-rpc': [WORD_FALSE], 'base-rpc': [WORD_FALSE]})
    assert client.humanity(VITALIK)['tag']['value'] != a['value']


def test_humanity_reports_unreachable_registries_instead_of_guessing(monkeypatch):
    stub_humanity_rpc(monkeypatch, {
        'ethereum-rpc': OSError('timed out'),
        'gnosis-rpc': [WORD_FALSE], 'base-rpc': [WORD_FALSE]})
    d = client.humanity(VITALIK)
    assert d['human'] is False
    assert 'eth' in d['errors'] and 'timed out' in d['errors']['eth']
    for s in d['sources']:
        if s['chain'] == 'eth':
            assert not s['verified'] and s['block'] is None


def test_humanity_registries_sit_on_the_rail():
    for r in client.HUMANITY_REGISTRIES:
        assert r['chain'] in client.NETWORKS       # keyless by construction
        assert r['selector'].startswith('0x') and len(r['selector']) == 10
        assert r['contract'].startswith('0x') and len(r['contract']) == 42
        assert r['register'].startswith('https://')
        if r['kind'] == 'uid':
            assert len(r['suffix']) == 64


def test_humanity_answers_signed_out(monkeypatch):
    stub_humanity_rpc(monkeypatch, {
        'ethereum-rpc': [WORD_TRUE, WORD_FALSE],
        'gnosis-rpc': [WORD_FALSE], 'base-rpc': [WORD_FALSE]})
    rest = api.route('GET', '/humanity', f'id={VITALIK}', {}, None)
    tool = mcp.call_tool('debank_humanity', {'id': VITALIK})
    assert rest['human'] is True and rest['tag'] == tool['tag']
    assert '/humanity' in api.route('GET', '/health', '', {}, None)['keyless']


def test_humanity_needs_a_real_address():
    with pytest.raises(DebankError) as e:
        client.humanity('vitalik.eth')
    assert e.value.status == 400


def test_console_wears_the_humanity_tag():
    html = open(os.path.join(HERE, 'console.html')).read()
    for needle in ('/humanity', 'humanTag', 'HUMAN', 'proofofhumanity'):
        assert needle in html
