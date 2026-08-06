"""targon tests — shape and wiring, with the live subnet as the fixture.

Inventory, version and health need no key, so those run anywhere with
network. Anything that spends money or needs an account is asserted on its
refusal, not its success.
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
        pytest.skip('targon-api not running (m targon/serve)')
    return m.server_url


def test_config_is_wired(m):
    cfg = m._load_config()
    assert cfg['name'] == 'targon'
    assert cfg['port'] == m.port
    # The console is served on the API port, so the gateway routes both to it.
    assert cfg['app_port'] == cfg['port']
    assert cfg['urls']['app'].endswith('/targon')
    for fn in cfg['fns']:
        assert callable(getattr(m, fn)), fn


def test_binary_built(m):
    assert os.path.exists(m.binary), 'run m targon/build'


def test_mcp_handshake(m, server):
    r = requests.post(f'{server}/mcp', timeout=10, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18'}}).json()
    assert r['result']['serverInfo']['name'] == 'targon'
    assert r['result']['protocolVersion'] == '2025-06-18'


def test_notification_gets_no_reply(server):
    """JSON-RPC notifications carry no id and must not be answered."""
    r = requests.post(f'{server}/mcp', timeout=10,
                      json={'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    assert r.status_code == 202


def test_every_tool_is_agent_readable(m, server):
    tools = requests.get(f'{server}/tools', timeout=10).json()['tools']
    assert len(tools) >= 49
    names = {t['name'] for t in tools}
    # The composed tools are the reason this module exists rather than curl.
    assert {'cheapest', 'rent', 'workload_exec'} <= names
    for t in tools:
        assert t['description'], t['name']
        assert t['inputSchema']['type'] == 'object'


def test_inventory_is_priced_and_live(m):
    tiers = m.inventory()
    assert len(tiers) > 5
    for t in tiers:
        assert t['name'] and t['cost_per_hour'] >= 0
        assert t['available'] >= 0


def test_cheapest_picks_an_available_tier(m, server):
    c = m.cheapest()
    assert c['available'] > 0
    assert c['spec']['gpu_count'] >= 1
    # It is the floor: nothing in the alternatives undercuts it.
    assert all(a['cost_per_hour'] >= c['cost_per_hour'] for a in c['alternatives'])


def test_cheapest_honours_the_filter(m, server):
    c = m.cheapest(gpu_type='H200')
    assert 'h200' in c['resource_name'].lower()


def test_cheapest_says_so_when_nothing_matches(m, server):
    with pytest.raises(Exception) as e:
        m.cheapest(max_cost_per_hour=0.0001)
    assert 'no available tier' in str(e.value)


@pytest.mark.parametrize('path', ['/workloads', '/volumes', '/ssh-keys', '/credits'])
def test_account_routes_need_a_key(m, server, path):
    if m.api_key:
        pytest.skip('a key is loaded — these would really answer')
    r = requests.get(f'{server}{path}', timeout=20)
    assert r.status_code == 400
    assert 'API key' in r.json()['error']


def test_forward_dispatches_any_tool(server):
    r = requests.post(f'{server}/forward', timeout=20,
                      json={'action': 'cheapest', 'gpu': True}).json()
    assert r['resource_name']


def test_console_and_gateway_aliases(server):
    for path in ['/targon', '/targon/']:
        assert '<!doctype html>' in requests.get(f'{server}{path}', timeout=10).text[:40]
    # Same API at the root, behind the fleet router prefix, and at the app alias.
    for prefix in ['', '/api/targon', '/targon/_api']:
        assert requests.get(f'{server}{prefix}/health', timeout=10).json()['name'] == 'targon'


def test_console_ships_every_theme(m):
    """The theme picker is the console's one piece of persisted state; each id
    needs a palette block, a menu entry and swatches, or switching half-works."""
    html = open(os.path.join(m.dir, 'targon-rs', 'src', 'console.html')).read()
    ids = ['dmg', 'pocket', 'light', 'vboy', 'sgb', 'micro', 'crt', 'paper']
    for tid in ids:
        assert f'data-theme="{tid}"' in html, tid
        assert f"['{tid}'," in html, f'{tid} missing from the picker'
    assert html.count('--plate:') == len(ids)
    assert "localStorage.getItem('targon.theme')" in html


def test_info_reports_the_surface(m, server):
    i = requests.get(f'{server}/info', timeout=10).json()
    assert i['name'] == 'targon' and i['status'] == 'ok'
    assert i['tools'] == len(requests.get(f'{server}/tools', timeout=10).json()['tools'])
    assert i['upstream'].startswith('https://')
