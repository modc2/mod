"""Offline tests for the bt module: registry, MCP stdio, HTTP surfaces.

No test touches the chain — engines are only built lazily on tool *calls*
that need them, and we only call filesystem-backed tools here.
"""
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ['BT_NO_SNAPSHOT'] = '1'          # never start the indexer thread
os.environ.setdefault('BT_DATA_DIR', '/tmp/bt-test-default')

from bt import history, tools, traders  # noqa: E402


# ----------------------------------------------------------------- registry

def test_registry_shape():
    listed = tools.list_tools()
    assert len(listed) == len(tools.TOOLS) >= 20
    names = [t['name'] for t in listed]
    assert len(names) == len(set(names)), 'duplicate tool names'
    for t in listed:
        assert t['name'].startswith('bt_')
        assert t['description']
        schema = t['inputSchema']
        assert schema['type'] == 'object'
        for pname, prop in schema['properties'].items():
            assert prop['type'] in ('string', 'integer', 'number', 'boolean')
            assert prop['description']


def test_mutating_tools_flagged():
    mutating = {t.name for t in tools.TOOLS if t.mutates}
    assert mutating == {'bt_create_wallet', 'bt_transfer', 'bt_buy',
                        'bt_sell', 'bt_sell_all', 'bt_swap'}


def test_docs_grouping():
    groups = tools.docs()
    assert {g['group'] for g in groups} == {
        'Chain', 'Wallet', 'Markets', 'Trading', 'Traders', 'Network'}
    total = sum(len(g['tools']) for g in groups)
    assert total == len(tools.TOOLS)


def test_call_validation():
    with pytest.raises(ValueError, match='unknown tool'):
        tools.call_tool('bt_nope', {})
    with pytest.raises(ValueError, match='missing required'):
        tools.call_tool('bt_balance', {})
    with pytest.raises(ValueError, match='unknown argument'):
        tools.call_tool('bt_wallets', {'bogus': 1})


def test_wallets_offline():
    out = tools.call_tool('bt_wallets', {})
    assert isinstance(out, list)


def test_jsonable():
    class Obj:
        def __init__(self):
            self.a = 1
            self._hidden = 2
    assert tools._jsonable({'x': [Obj(), 3.5]}) == {'x': [{'a': 1}, 3.5]}


# ----------------------------------------------------------------- history

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('BT_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(history, '_cache', {'rows': None, 'ts': None})
    return tmp_path


def _synth_rows(mult):
    return [history.row_from_subnet({
        'netuid': u, 'subnet_name': f'net{u}', 'symbol': chr(0x3b1 + u),
        'price': 0.01 * u * mult, 'alpha_in': 100.0, 'alpha_out': 100.0,
        'tao_in': 50.0 * u, 'subnet_volume': 1000.0 * mult, 'emission': 0.1,
        'network_registered_at': 7, 'subnet_identity': {
            'github_repo': 'https://github.com/x/y',
            'subnet_url': 'https://x.y', 'discord': 'xy',
            'logo_url': None, 'description': 'a net'},
    }) for u in (1, 2, 3)]


def test_history_warming(store):
    out = history.screener()
    assert out['warming'] is True and out['rows'] == []
    assert history.stats()['warming'] is True


def test_history_changes_volume_sparks(store):
    now = int(time.time())
    history.record(_synth_rows(1.0), ts=now - 7 * 86400)
    history.record(_synth_rows(1.1), ts=now - 86400)
    history.record(_synth_rows(1.2), ts=now - 3600)
    history.record(_synth_rows(1.3), ts=now)
    out = history.screener(sort_by='market_cap')
    assert out['count'] == 3 and out.get('warming') is not True
    r = next(x for x in out['rows'] if x['netuid'] == 2)
    assert r['change_1h'] == pytest.approx(100 * (1.3 / 1.2 - 1))
    assert r['change_24h'] == pytest.approx(100 * (1.3 / 1.1 - 1))
    assert r['change_7d'] == pytest.approx(30.0)
    assert r['vol_24h'] == pytest.approx(200.0)
    assert r['github'] == 'https://github.com/x/y'
    assert len(r['spark']) >= 2
    # sorted by mcap desc: netuid 3 first
    assert [x['netuid'] for x in out['rows']] == [3, 2, 1]


def test_history_search_and_limit(store):
    history.record(_synth_rows(1.0))
    out = history.screener(search='net2')
    assert out['count'] == 1 and out['rows'][0]['netuid'] == 2
    out = history.screener(search='2')          # netuid match
    assert out['rows'][0]['netuid'] == 2
    assert history.screener(limit=2)['count'] == 2


def test_history_series_downsample(store):
    now = int(time.time())
    for i in range(50):
        history.record(_synth_rows(1 + i / 100), ts=now - (50 - i) * 60)
    s = history.series(1, hours=2, points=10)
    assert len(s) == 10
    assert s[-1]['price'] == pytest.approx(0.01 * 1.49)
    assert s[0]['t'] < s[-1]['t']


def test_history_stats_excludes_root(store):
    rows = _synth_rows(1.0) + [history.row_from_subnet(
        {'netuid': 0, 'subnet_name': 'root', 'symbol': 'Τ', 'price': 1.0,
         'alpha_in': 1e6, 'alpha_out': 1e6, 'tao_in': 1e6,
         'subnet_volume': 0.0, 'emission': 0.0})]
    history.record(rows)
    st = history.stats()
    assert st['subnets'] == 3
    assert st['total_tao_in_pools'] == pytest.approx(50.0 + 100.0 + 150.0)


def test_history_cold_start_from_disk(store):
    history.record(_synth_rows(1.0))
    history._cache = {'rows': None, 'ts': None}   # simulate restart
    out = history.screener()
    assert out.get('warming') is not True and out['count'] == 3
    assert out['rows'][0]['name'].startswith('net')


def test_screener_tool_wired(store):
    history.record(_synth_rows(1.0))
    out = tools.call_tool('bt_screener', {'sort_by': 'tao_in', 'limit': 1,
                                          'sparks': False})
    assert out['rows'][0]['netuid'] == 3
    assert 'spark' not in out['rows'][0]
    h = tools.call_tool('bt_history', {'netuid': 1})
    assert h['netuid'] == 1 and isinstance(h['series'], list)
    assert 'subnets' in tools.call_tool('bt_stats', {})


# ----------------------------------------------------------------- traders

WHALE = '5GsbTgfvgCH4xdqSkiPb7EaBBFLHjWH5vfEALhJaewSFpZX9'


@pytest.fixture
def tstore(store, monkeypatch):
    """Trader index on a temp dir, with fixed marks instead of the chain."""
    monkeypatch.setattr(traders, '_prices_now',
                        lambda: {1: 0.5, 2: 0.25, 3: 2.0})
    return store


def _pos(netuid, alpha, price, hotkey='hk1'):
    return {'netuid': netuid, 'hotkey': hotkey, 'alpha': alpha,
            'price': price, 'value_tao': alpha * price}


def _snap(ss58, ts, positions, free=1.0):
    staked = sum(p['value_tao'] for p in positions)
    return traders._record(ss58, ts, {
        'free_tao': free, 'staked_tao': staked, 'total_tao': free + staked,
        'positions': positions})


def test_track_requires_valid_ss58(tstore):
    with pytest.raises(ValueError, match='invalid ss58'):
        traders.track('not-an-address')


def test_track_untrack_roundtrip(tstore, monkeypatch):
    monkeypatch.setattr(traders, 'snapshot', lambda a, bt=None: {
        'ts': 1, 'total_tao': 0.0, 'positions': [], 'flows': []})
    out = traders.track(WHALE, label='whale')
    assert out['tracked'] is True and out['new'] is True
    assert [t['ss58'] for t in traders.watchlist()] == [WHALE]
    assert traders.track(WHALE)['new'] is False          # idempotent
    assert traders.track(WHALE, label='renamed')['tracked'] is True
    assert traders.watchlist()[0]['label'] == 'renamed'  # label updates
    assert traders.untrack(WHALE)['removed'] is True
    assert traders.watchlist() == []


def test_flows_inferred_from_position_deltas(tstore):
    now = int(time.time())
    _snap(WHALE, now - 3600, [_pos(1, 100.0, 0.5), _pos(2, 40.0, 0.25)])
    flows = _snap(WHALE, now, [
        _pos(1, 100.5, 0.5),    # +0.5% — emission drift, not a trade
        _pos(2, 20.0, 0.25),    # halved — a real sell
        _pos(3, 10.0, 2.0),     # new position — a buy
    ])
    by_uid = {f['netuid']: f for f in flows}
    assert set(by_uid) == {2, 3}
    assert by_uid[2]['side'] == 'sell'
    assert by_uid[2]['alpha'] == pytest.approx(20.0)
    assert by_uid[2]['tao_value'] == pytest.approx(5.0)
    assert by_uid[3]['side'] == 'buy'
    assert by_uid[3]['tao_value'] == pytest.approx(20.0)
    # the tape reads back with the biggest move first
    tape = traders.flows(WHALE, hours=24)['flows']
    assert [f['netuid'] for f in tape] == [3, 2] or [f['netuid'] for f in tape] == [2, 3]
    assert all(f['inferred'] for f in tape)


def test_dust_moves_are_not_trades(tstore):
    now = int(time.time())
    _snap(WHALE, now - 3600, [_pos(1, 100.0, 0.5)])
    # 10% of the position, but only 0.005 TAO — under the absolute floor
    assert _snap(WHALE, now, [_pos(1, 100.0, 0.5), _pos(2, 0.02, 0.25)]) == []


def test_first_snapshot_has_no_flows(tstore):
    assert _snap(WHALE, int(time.time()), [_pos(1, 100.0, 0.5)]) == []


def test_windows_and_equity_curve(tstore):
    now = int(time.time())
    _snap(WHALE, now - 7 * 86400, [_pos(1, 100.0, 0.25)])   # 25 + 1 free
    _snap(WHALE, now - 86400, [_pos(1, 100.0, 0.4)])        # 40 + 1
    _snap(WHALE, now, [_pos(1, 100.0, 0.5)])                # 50 + 1
    assert traders.traders()['rows'] == []  # snapshots alone don't track it
    with_meta = traders.trader(WHALE)
    assert with_meta['tracked'] is False    # the profile still works
    assert with_meta['total_tao'] == pytest.approx(51.0)
    assert with_meta['change_24h'] == pytest.approx(100 * (51 / 41 - 1))
    assert with_meta['change_7d'] == pytest.approx(100 * (51 / 26 - 1))
    assert with_meta['pnl_24h'] == pytest.approx(10.0)
    # price-only PnL on yesterday's book: 100 alpha × (0.5 − 0.4)
    assert with_meta['hold_pnl_24h'] == pytest.approx(10.0)
    assert len(with_meta['series']) == 3
    assert with_meta['positions'][0]['pct_of_total'] == pytest.approx(50 / 51 * 100)


def test_positions_at_respects_tolerance(tstore):
    now = int(time.time())
    _snap(WHALE, now - 7 * 86400, [_pos(1, 10.0, 0.5)])
    near = traders.positions_at(WHALE, ts=now - 7 * 86400 + 600,
                                tolerance_sec=3600)
    assert near['found'] is True and near['age_sec'] == 600
    far = traders.positions_at(WHALE, ts=now, tolerance_sec=3600)
    assert far['found'] is False and far['positions'] == []
    # tolerance 0 accepts whatever is nearest
    assert traders.positions_at(WHALE, ts=now)['found'] is True


def test_prices_at_reads_history(tstore):
    now = int(time.time())
    history.record(_synth_rows(1.0), ts=now - 86400)
    history.record(_synth_rows(2.0), ts=now - 60)
    out = traders.prices_at(now - 86400)
    assert out['prices']['2'] == pytest.approx(0.02)
    assert traders.prices_at()['count'] == 3      # current marks


def test_trader_tools_wired(tstore):
    now = int(time.time())
    _snap(WHALE, now - 86400, [_pos(1, 100.0, 0.4)])
    _snap(WHALE, now, [_pos(1, 50.0, 0.5)])
    assert tools.call_tool('bt_trader', {'address': WHALE})['total_tao'] \
        == pytest.approx(26.0)
    tape = tools.call_tool('bt_trader_flows', {'hours': 48})
    assert tape['count'] == 1 and tape['flows'][0]['side'] == 'sell'
    assert tools.call_tool('bt_trader_history',
                           {'address': WHALE})['series'][0]['total_tao'] \
        == pytest.approx(41.0)
    assert tools.call_tool('bt_untrack', {'address': WHALE,
                                          'purge': True})['purged'] is True
    assert traders.stats()['snapshots'] == 0


# ----------------------------------------------------------------- MCP stdio

def _mcp_roundtrip(messages):
    proc = subprocess.run(
        [sys.executable, '-m', 'bt.mcp_server'],
        input='\n'.join(json.dumps(m) for m in messages) + '\n',
        capture_output=True, text=True, cwd=ROOT, timeout=60)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_mcp_stdio_protocol():
    replies = _mcp_roundtrip([
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
         'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                    'clientInfo': {'name': 'test', 'version': '0'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
         'params': {'name': 'bt_wallets', 'arguments': {}}},
        {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
         'params': {'name': 'bt_nope', 'arguments': {}}},
        {'jsonrpc': '2.0', 'id': 5, 'method': 'no/such_method'},
    ])
    by_id = {r['id']: r for r in replies}
    init = by_id[1]['result']
    assert init['serverInfo']['name'] == 'bittensor'
    assert init['protocolVersion'] == '2025-06-18'
    assert 'tools' in init['capabilities']
    assert len(by_id[2]['result']['tools']) == len(tools.TOOLS)
    ok_call = by_id[3]['result']
    assert ok_call['isError'] is False
    assert isinstance(json.loads(ok_call['content'][0]['text']), list)
    assert by_id[4]['result']['isError'] is True
    assert by_id[5]['error']['code'] == -32601


# ----------------------------------------------------------------- HTTP

@pytest.fixture(scope='module')
def client():
    from fastapi.testclient import TestClient
    from bt.server import app
    return TestClient(app)


def test_http_info(client):
    j = client.get('/api').json()
    assert j['name'] == 'bt' and j['tools'] == len(tools.TOOLS)


def test_http_tools_and_docs(client):
    assert len(client.get('/api/tools').json()['tools']) == len(tools.TOOLS)
    assert len(client.get('/api/docs').json()['groups']) == 6


def test_http_call(client):
    j = client.post('/api/call', json={'tool': 'bt_wallets'}).json()
    assert j['ok'] is True and isinstance(j['result'], list)
    r = client.post('/api/call', json={'tool': 'bt_nope'})
    assert r.status_code == 400 and r.json()['ok'] is False


def test_http_mcp(client):
    j = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1,
                                  'method': 'tools/list'}).json()
    assert len(j['result']['tools']) == len(tools.TOOLS)
    j = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 2,
                                  'method': 'initialize',
                                  'params': {'protocolVersion': '2025-03-26'}}).json()
    assert j['result']['protocolVersion'] == '2025-03-26'


def test_http_serves_app(client):
    html = client.get('/').text
    assert 'Bittensor' in html and 'id="docs"' in html
