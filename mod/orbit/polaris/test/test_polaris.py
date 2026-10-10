"""polaris tests — the shapes this module promises, and the refusals it owes.

Nothing here rents anything. The only write path exercised is `rent` blocked by
the spend guard, which is the behaviour worth pinning: a test that actually
provisioned a box would bill the operator for as long as the test suite is
forgotten about.

    python3 -m pytest test/ -q
    POLARIS_TEST_LIVE=0 python3 -m pytest test/ -q   # skip the live catalog
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import api                                      # noqa: E402
import auth                                     # noqa: E402
import mcp                                      # noqa: E402
from client import Polaris, PolarisError        # noqa: E402
from mod import Mod                             # noqa: E402

LIVE = os.environ.get('POLARIS_TEST_LIVE', '1') not in ('0', 'false')
live = pytest.mark.skipif(not LIVE, reason='POLARIS_TEST_LIVE=0')


@pytest.fixture(scope='module')
def m():
    return Mod()


# ── the module declares what it does ──

def test_config_matches_code(m):
    cfg = m.config()
    assert cfg['name'] == 'polaris'
    assert cfg['port'] == 50870 and cfg['base_path'] == '/polaris'
    # Every declared fn exists, and every declared tool is registered. These
    # drift silently otherwise, and the config is what the fleet reads.
    for fn in cfg['fns']:
        assert callable(getattr(m, fn, None)), f'config declares missing fn {fn}'
    assert sorted(cfg['tools']) == sorted(mcp.TOOLS)


def test_no_secret_in_the_tree():
    """A key in the repo is a key in git history — this module shipped one for
    months. Match the format, not a fixed prefix, so the guard itself is not a
    fragment of a real key."""
    import re
    live = re.compile(r'pi_sk_[A-Za-z0-9_-]{20,}')
    for name in ('config.json', 'skill.md', 'mod.py', 'api.py', 'mcp.py',
                 'client.py', 'console.html', 'test/test_polaris.py'):
        with open(os.path.join(ROOT, name)) as f:
            body = f.read()
        assert not live.search(body), f'{name} carries a real API key'


def test_info_lists_its_routes():
    info = api.info()
    assert info['name'] == 'polaris'
    assert info['mcp']['tools'] == len(mcp.TOOLS)
    assert '/gpus' in ' '.join(info['endpoints'])


# ── auth tiers ──

def test_catalog_is_open_and_account_is_not():
    auth.guard('/gpus')                       # public: must not raise
    auth.guard('/quote')
    with pytest.raises(PolarisError) as e:
        auth.guard('/instances')
    assert e.value.status == 401
    auth.guard('/instances', key='pi_sk_theirs')   # BYOK: their account
    with pytest.raises(PolarisError):
        auth.guard('/rent', key='pi_sk_theirs')    # spending is never BYOK
    auth.guard('/rent', owner=True)


def test_proxied_request_never_looks_local():
    assert auth.is_local('127.0.0.1', {})
    assert not auth.is_local('127.0.0.1', {'x-forwarded-for': '8.8.8.8'})
    assert not auth.is_local('8.8.8.8', {})


def test_owner_only_tools_refused_without_a_key():
    with pytest.raises(PolarisError):
        auth.guard_tool('polaris_rent', {})
    auth.guard_tool('polaris_gpus', {})
    auth.guard_tool('polaris_credits', {'key': 'pi_sk_theirs'})


# ── mcp protocol ──

def test_initialize_and_tools_list():
    got = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                      'params': {'protocolVersion': '2025-06-18'}})
    assert got['result']['serverInfo']['name'] == 'polaris'
    assert got['result']['protocolVersion'] == '2025-06-18'
    tools = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})['result']['tools']
    assert len(tools) == len(mcp.TOOLS)
    for t in tools:
        assert t['description'] and t['inputSchema']['type'] == 'object'


def test_notifications_get_no_response():
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_tool_failure_is_a_result_not_an_error():
    """MCP says a failed tool is a successful response carrying isError, so the
    model reads the hint instead of the transport blowing up."""
    got = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                      'params': {'name': 'nope', 'arguments': {}}}, owner=True)
    assert 'result' in got and got['result']['isError'] is True


# ── normalization, without the network ──

def test_available_count_null_still_counts_as_available(monkeypatch):
    """The upstream sends null when it knows a type is up but not how many are
    free. Reading that as zero would hide most of the catalog."""
    rows = {'gpus': [
        {'name': 'A', 'display_name': 'A', 'vram_gb': 80, 'spot_price': 1.0,
         'on_demand_price': 2.0, 'available': True, 'available_count': None},
        {'name': 'B', 'display_name': 'B', 'vram_gb': 80, 'spot_price': 0.5,
         'on_demand_price': 1.0, 'available': True, 'available_count': 0},
    ]}
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'get', lambda *a, **k: rows)
    got = c.gpus()
    assert [g['name'] for g in got['gpus']] == ['A']


def test_instances_unwraps_and_sums_the_burn(monkeypatch):
    """The upstream wraps the list in {"instances": …}; treating that dict as a
    list is the bug this module was rewritten to kill."""
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'get', lambda *a, **k: {'instances': [
        {'id': 'i1', 'name': 'one', 'status': 'running', 'ip': '203.0.113.1',
         'hourly_cost': 1.5},
        {'id': 'i2', 'name': 'two', 'status': 'terminated', 'hourly_cost': 9.0},
    ]})
    got = c.instances()
    assert got['count'] == 2
    assert got['burn_usd_hr'] == 1.5          # a terminated box bills nothing
    assert got['instances'][0]['ssh'] == 'ssh root@203.0.113.1'
    assert c.instance('one')['id'] == 'i1'    # by name, not just by id


def test_ssh_says_provisioning_rather_than_lying(monkeypatch):
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'get', lambda *a, **k: {'instances': [
        {'id': 'i1', 'name': 'one', 'status': 'provisioning'}]})
    with pytest.raises(PolarisError) as e:
        c.ssh()
    assert e.value.status == 409 and 'provisioning' in (e.value.hint or '')


def test_router_price_sentinel_is_not_a_price(monkeypatch):
    """The catalog carries OpenRouter rows, where -1 means "priced when the
    request is routed". Passed through it reads as a $1M-per-million-token
    credit and sorts to the top of every cheapest-first list."""
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'get', lambda *a, **k: {'models': [
        {'id': 'openrouter/auto', 'name': 'Auto Router', 'provider': 'openrouter',
         'context_length': 2000000, 'prompt_price': '-1', 'completion_price': '-1'},
        {'id': 'real/model', 'name': 'Real', 'provider': 'real',
         'context_length': 128000, 'prompt_price': '0.000001',
         'completion_price': '0.000002'},
    ]})
    got = c.models(sort='price')
    router = next(m for m in got['models'] if m['id'] == 'openrouter/auto')
    assert router['usd_per_token_in'] is None and router['pricing'] == 'routed'
    assert got['models'][0]['id'] == 'real/model', 'a routed model must not sort as free'
    # And it cannot pass a price ceiling it has no price for.
    assert [m['id'] for m in c.models(max_prompt_price=0.01)['models']] == ['real/model']


def test_dollar_strings_parse():
    c = Polaris(key='x')
    c.get = lambda *a, **k: {'balance_usd': '$25.00', 'pending_usd': '$0.00',
                             'account_status': 'active'}
    assert c.credits()['balance_usd'] == 25.0


# ── the spend guard ──

def test_guard_refuses_and_hands_back_the_quote(monkeypatch):
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'gpus', lambda **k: {'gpus': [
        {'id': 'h100', 'gpu_type': 'H100', 'name': 'H100', 'vram_gb': 80,
         'is_cpu': False, 'usd_hr': 3.0, 'usd_hr_spot': 3.0,
         'usd_hr_on_demand': 4.0, 'available': True}]})
    posted = []
    monkeypatch.setattr(c, 'post', lambda *a, **k: posted.append(a) or {})
    with pytest.raises(PolarisError) as e:
        c.rent('H100', hours=1)
    assert e.value.status == 402
    assert e.value.upstream['estimate_usd'] == 3.0
    assert not posted, 'the guard must refuse before the money moves'


def test_guard_lets_a_cheap_confirmed_rental_through(monkeypatch):
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'gpus', lambda **k: {'gpus': [
        {'id': 'cpu', 'gpu_type': 'CPU Small', 'name': 'CPU Small', 'vram_gb': 0,
         'is_cpu': True, 'usd_hr': 0.06, 'usd_hr_spot': 0.06,
         'usd_hr_on_demand': 0.06, 'available': True}]})
    monkeypatch.setattr(c, 'post', lambda p, b: {'success': True, 'instances': [
        {'id': 'i9', 'name': b['name'], 'status': 'provisioning'}]})
    got = c.rent('CPU Small', ssh_public_key='ssh-ed25519 AAAA test')
    assert got['ok'] and got['instances'][0]['id'] == 'i9'


def test_unavailable_is_refused_before_the_post(monkeypatch):
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'gpus', lambda **k: {'gpus': [
        {'id': 'b200', 'gpu_type': 'B200', 'name': 'B200', 'vram_gb': 180,
         'is_cpu': False, 'usd_hr': 6.0, 'usd_hr_spot': 6.0,
         'usd_hr_on_demand': 7.0, 'available': False}]})
    with pytest.raises(PolarisError) as e:
        c.rent('B200', confirm=True, ssh_public_key='k')
    assert e.value.status == 409


def test_gpu_type_resolves_by_name_or_slug(monkeypatch):
    """The catalog gives both; a rent call upstream only accepts the name, so
    the slug has to be normalized here rather than rejected."""
    c = Polaris(key='x')
    monkeypatch.setattr(c, 'gpus', lambda **k: {'gpus': [
        {'id': 'h100-sxm5-80gb', 'gpu_type': 'H100 SXM5 80GB',
         'name': 'H100 SXM5 80GB', 'vram_gb': 80, 'is_cpu': False, 'usd_hr': 3.0,
         'usd_hr_spot': 3.0, 'usd_hr_on_demand': 4.0, 'available': True}]})
    assert c.quote('h100-sxm5-80gb')['gpu_type'] == 'H100 SXM5 80GB'
    assert c.quote('H100 SXM5 80GB')['gpu_type'] == 'H100 SXM5 80GB'
    # A near miss names the real ones rather than failing blankly.
    with pytest.raises(PolarisError) as e:
        c.quote('H100')
    assert 'H100 SXM5 80GB' in (e.value.hint or '')


# ── keys stay off the tree ──

def test_set_key_writes_0600_off_tree(tmp_path, monkeypatch):
    import client
    target = tmp_path / 'api_key'
    monkeypatch.setattr(client, 'KEY_FILE', str(target))
    monkeypatch.setattr(client, 'STATE', str(tmp_path))
    monkeypatch.delenv('POLARIS_KEY', raising=False)
    monkeypatch.delenv('POLARIS_API_KEY', raising=False)
    got = client.Polaris.set_key('pi_sk_testkey_not_real')
    assert got['persisted'] and oct(target.stat().st_mode)[-3:] == '600'
    assert client.Polaris().key() == 'pi_sk_testkey_not_real'
    assert str(target) not in ROOT


def test_missing_key_says_how_to_set_one(monkeypatch, tmp_path):
    import client
    monkeypatch.setattr(client, 'KEY_FILE', str(tmp_path / 'nope'))
    monkeypatch.delenv('POLARIS_KEY', raising=False)
    monkeypatch.delenv('POLARIS_API_KEY', raising=False)
    with pytest.raises(PolarisError) as e:
        client.Polaris().key()
    assert e.value.status == 401 and 'set_key' in (e.value.hint or '')


# ── routing ──

def test_route_rejects_unknown_paths():
    with pytest.raises(PolarisError) as e:
        api.route('GET', '/nonsense', '', {}, owner=True)
    assert e.value.status == 404


def test_route_requires_its_arguments():
    with pytest.raises(PolarisError) as e:
        api.route('GET', '/quote', '', {}, owner=True)
    assert 'gpu_type is required' in e.value.message


# ── live, against the real API ──

@live
def test_catalog_is_public_and_priced(m):
    got = m.gpus(available_only=False)
    assert got['count'] > 5
    for row in got['gpus']:
        assert row['gpu_type'] and isinstance(row['is_cpu'], bool)
        assert row['usd_hr'] is None or row['usd_hr'] > 0
    # The catalog is reachable with no key at all — that is the point of it.
    assert Polaris(key=None).gpus()['count'] >= 0


@live
def test_models_and_templates_load(m):
    models = m.models(limit=5)
    assert models['total'] > 100 and len(models['models']) == 5
    tpl = m.templates()
    assert tpl['count'] and 'ai_ml' in tpl['categories']


@live
@pytest.mark.skipif(not Polaris().has_key(), reason='no Polaris key set')
def test_account_routes_answer(m):
    st = m.status()
    assert 'credits' in st and 'instances' in st
    assert isinstance(st['burn_usd_hr'], (int, float))
    assert m.account()['id']


if __name__ == '__main__':
    sys.exit(pytest.main([HERE, '-q']))
