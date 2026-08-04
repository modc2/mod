"""lium tests — shape and wiring, with the live subnet as the fixture.

Public reads (nodes, templates, subnet stats) need no key, so these run
anywhere with network. Anything that spends money or needs an account is
asserted on its refusal, not its success.
"""

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mod import Mod  # noqa: E402


@pytest.fixture(scope='module')
def m():
    return Mod()


@pytest.fixture(scope='module')
def server(m):
    """The Rust MCP backend, if it is running — most tests want it."""
    if not m._server_up():
        pytest.skip('lium-api not running (m lium/serve)')
    return m.server_url


def test_config_is_wired(m):
    cfg = m._load_config()
    assert cfg['name'] == 'lium'
    assert cfg['netuid'] == 51
    assert cfg['port'] == m.port
    # Every advertised fn exists on the module.
    for fn in cfg['fns']:
        assert callable(getattr(m, fn)), fn


def test_binary_built(m):
    assert os.path.exists(m.binary), 'run m lium/build'


def test_mcp_handshake(m, server):
    r = requests.post(f'{server}/mcp', timeout=10, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18'}}).json()
    assert r['result']['serverInfo']['name'] == 'lium'
    assert r['result']['protocolVersion'] == '2025-06-18'


def test_tools_match_config(m, server):
    tools = [t['name'] for t in m.tools()['tools']]
    assert tools == m._load_config()['tools']
    # Tool schemas are what an agent reads; they must be complete.
    for t in m.tools()['tools']:
        assert t['description'] and t['inputSchema']['type'] == 'object'


def test_notification_gets_no_reply(m, server):
    """JSON-RPC notifications carry no id and must not be answered."""
    r = requests.post(f'{server}/mcp', timeout=10,
                      json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    assert r.status_code == 202


def test_executors_are_shaped_and_sorted(m):
    r = m.executors(limit=10)
    assert r['count'] <= 10 and r['count'] <= r['listed']
    prices = [e['price_per_gpu_hr'] for e in r['executors']]
    assert prices == sorted(prices), 'default sort is cheapest first'
    for e in r['executors']:
        assert e['id'] and e['gpu']
        assert e['gpu_count'] >= 1
        # The Bittensor side of every node comes through.
        assert e['miner_hotkey'] and e['validator_hotkey']
        assert e['price_per_hr'] == pytest.approx(e['price_per_gpu_hr'] * e['gpu_count'], abs=0.01)


def test_filters_narrow(m):
    r = m.executors(gpu_type='4090', available_only=True, limit=5)
    for e in r['executors']:
        assert '4090' in e['gpu']
        assert e['available_gpu_count'] > 0


def test_executor_resolves_by_prefix(m):
    first = m.executors(limit=1)['executors'][0]
    got = m.executor(first['id'][:8])
    assert got['id'] == first['id']


def test_unknown_node_is_404(m, server):
    r = requests.get(f'{server}/executors/not-a-node', timeout=30)
    assert r.status_code == 404
    assert not r.json()['upstream'], 'we refuse this one ourselves'


def test_subnet_state(m):
    s = m.subnet()
    assert s['netuid'] == 51 and s['chain'] == 'bittensor'
    mk = s['marketplace']
    assert mk['gpus'] >= mk['gpus_available'] >= 0
    assert mk['providers'] >= 1 and mk['nodes_total'] >= mk['nodes_rentable']
    w = s['weights']
    assert w['netuid'] == 51
    shares = [u['share'] for u in w['top_uids']]
    assert shares == sorted(shares, reverse=True)
    assert sum(shares) <= 100.01


def test_gpu_types_and_capacity(m):
    stats = m.gpu_types()['stats']
    assert any(s['all_count'] >= s['rented_count'] for s in stats)
    assert m.capacity()['capacity'][0]['base_model']


def test_endpoints_come_from_the_live_spec(m):
    e = m.endpoints(q='pods')
    assert e['count'] >= 1
    paths = {row['path'] for row in e['endpoints']}
    assert '/pods' in paths
    assert all('pods' in r['path'].lower() or 'pods' in r['summary'].lower()
               for r in e['endpoints'])


def test_api_passthrough(m):
    v = m.api('/version')
    assert 'uptime_seconds' in v


def test_api_rejects_relative_paths(m, server):
    r = requests.post(f'{server}/api', json={'path': 'executors'}, timeout=15)
    assert r.status_code == 400
    assert 'must start with /' in r.json()['error']


@pytest.mark.parametrize('path', ['/pods', '/me', '/ssh-keys', '/volumes'])
def test_account_routes_need_a_key(m, server, path):
    if m.api_key:
        pytest.skip('a key is loaded — these would really answer')
    r = requests.get(f'{server}{path}', timeout=15)
    assert r.status_code == 401
    assert 'API key' in r.json()['error']


def test_console_and_gateway_aliases(server):
    for path in ['/lium', '/lium/']:
        assert '<!doctype html>' in requests.get(f'{server}{path}', timeout=10).text[:40]
    # Same API behind the fleet router prefix and the app alias.
    for prefix in ['', '/api/lium', '/lium/_api']:
        assert requests.get(f'{server}{prefix}/health', timeout=10).json()['name'] == 'lium'


def test_info_reports_upstream(m, server):
    i = m.info()
    assert i['netuid'] == 51 and i['chain'] == 'bittensor'
    assert i['upstream_up'] is True
    assert i['tools'] == len(m._load_config()['tools'])
