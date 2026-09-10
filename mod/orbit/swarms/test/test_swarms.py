"""swarms tests — the parts that must be right before any money moves.

Most of this runs offline. The three things worth testing without a network are
the three that cost something when they are wrong: the spend guard (does a
big run actually refuse to run?), the AgentSpec validation (does a typo fail
here with a hint, or 422 at the far end?), and the MCP envelope (does a tool
failure come back as a readable isError instead of killing the session?).

The live tests are marked and skipped unless SWARMS_LIVE=1, because a test
suite that needs mainnet is a test suite nobody runs.
"""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server as api  # noqa: E402
import chain        # noqa: E402
import client as C  # noqa: E402
import mcp          # noqa: E402
from chain import ChainError            # noqa: E402
from client import Client, SwarmsError  # noqa: E402

LIVE = os.environ.get('SWARMS_LIVE') == '1'
live = pytest.mark.skipif(not LIVE, reason='set SWARMS_LIVE=1 for network tests')


# ── the module surface ──

def test_config_matches_the_code():
    with open(os.path.join(ROOT, 'config.json')) as f:
        cfg = json.load(f)
    assert cfg['name'] == 'swarms'
    assert cfg['port'] == 50690
    # A config that lists tools the server does not serve is a lie the fleet
    # reads: the MCP hub aggregates this list.
    assert set(cfg['tools']) == set(mcp.TOOLS)
    assert set(cfg['swarm_types']) == set(C.SWARM_TYPES)
    assert cfg['solana']['mint'] == chain.MINT
    assert cfg['solana']['decimals'] == chain.DECIMALS


def test_every_declared_fn_exists():
    import mod
    with open(os.path.join(ROOT, 'config.json')) as f:
        declared = json.load(f)['fns']
    m = mod.Mod()
    missing = [f for f in declared if not callable(getattr(m, f, None))]
    assert not missing, f'config.json declares fns that do not exist: {missing}'


def test_info_needs_no_network():
    out = api.info()
    assert out['name'] == 'swarms'
    assert out['token']['mint'] == chain.MINT
    assert out['token']['read_only'] is True
    assert len(out['swarm_types']) == 16
    assert out['mcp']['tools'] == len(mcp.TOOLS)


# ── the spend guard ──

def test_guard_holds_a_big_run():
    """Fifty agents times ten loops is not a call anybody makes on purpose."""
    c = Client(key='test')
    q = c.cost(agents=50, loops=10, input_tokens=4000, output_tokens=4000)
    assert q['over_guard'] is True
    held = c._guard(50, 4000, 10, confirm=False)
    assert held and held['needs_confirm'] is True
    assert 'confirm=true' in held['how']


def test_guard_lets_a_small_run_through():
    c = Client(key='test')
    assert c._guard(1, 500, 1, confirm=False) is None


def test_confirm_defeats_the_guard():
    c = Client(key='test')
    assert c._guard(50, 4000, 10, confirm=True) is None


def test_cost_is_an_upper_bound_and_says_so():
    c = Client(key='test')
    q = c.cost(agents=3, loops=2, input_tokens=1000, output_tokens=1000)
    assert q['agent_calls'] == 6
    # loops and agents multiply — the whole reason the tool exists
    single = c.cost(agents=1, loops=1, input_tokens=1000, output_tokens=1000)
    assert q['usd']['total'] == pytest.approx(single['usd']['total'] * 6, rel=1e-6)
    assert 'upper bound' in q['basis']


# ── argument validation ──

def test_bad_agent_field_fails_here_with_a_hint():
    with pytest.raises(SwarmsError) as e:
        C._agent_spec({'agent_name': 'a', 'sytem_prompt': 'typo'})
    assert 'sytem_prompt' in str(e.value)
    assert 'system_prompt' in (e.value.hint or '')


def test_a_bare_string_becomes_an_agent():
    spec = C._agent_spec('researcher')
    assert spec['agent_name'] == 'researcher' and spec['role'] == 'researcher'
    assert C._agent_list('a, b, c') == C._agent_list(['a', 'b', 'c'])


def test_unknown_swarm_type_is_refused_before_the_request():
    with pytest.raises(SwarmsError) as e:
        Client(key='test').swarm(task='x', agents=['a'], swarm_type='Sequential')
    assert 'Sequential' in str(e.value)


def test_a_roster_less_swarm_is_refused_unless_auto():
    with pytest.raises(SwarmsError) as e:
        Client(key='test').swarm(task='x', swarm_type='GroupChat')
    assert 'agents' in str(e.value)


def test_missing_key_is_a_401_with_the_fix_in_it():
    c = Client(key=None)
    saved, os.environ['SWARMS_API_KEY'] = os.environ.get('SWARMS_API_KEY'), ''
    try:
        if c.key:
            pytest.skip('a key is configured on this box')
        with pytest.raises(SwarmsError) as e:
            c.credits()
        assert e.value.status == 401
        assert 'swarms.world/platform/api-keys' in (e.value.hint or '')
    finally:
        if saved is not None:
            os.environ['SWARMS_API_KEY'] = saved
        else:
            os.environ.pop('SWARMS_API_KEY', None)


def test_a_key_is_never_echoed_back():
    c = Client(key='sk-secret-value-1234567890')
    blob = json.dumps(c.key_state())
    assert 'secret-value' not in blob
    assert c.key_state()['source'] == 'request'


# ── the chain half is read-only, and provably so ──

def test_chain_holds_no_signer():
    i = chain.info()
    assert i['read_only'] is True
    assert i['signing'].startswith('none')
    src = open(os.path.join(ROOT, 'chain.py')).read()
    for forbidden in ('sendTransaction', 'signTransaction', 'Keypair', 'private_key'):
        assert forbidden not in src, f'chain.py must not reference {forbidden}'


def test_quote_rejects_nonsense_before_the_network():
    for kwargs in ({'side': 'hodl'}, {'pay_with': 'DOGE'}, {'amount': 0},
                   {'amount': -1}, {'amount': 'lots'}):
        with pytest.raises(ChainError):
            chain.quote(**kwargs)


def test_balance_rejects_a_non_address():
    with pytest.raises(ChainError) as e:
        chain.balance('nope')
    assert e.value.status == 400


# ── MCP ──

def test_every_tool_has_a_schema_and_a_handler():
    for name, t in mcp.TOOLS.items():
        assert name.startswith('swarms_'), name
        assert t['description'] and len(t['description']) > 60, name
        assert t['inputSchema']['type'] == 'object', name
        assert callable(t['handler']), name
        for req in t['inputSchema'].get('required', []):
            assert req in t['inputSchema']['properties'], f'{name}: required {req}'


def test_initialize_and_tools_list():
    out = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                      'params': {'protocolVersion': '2025-06-18'}})
    assert out['result']['protocolVersion'] == '2025-06-18'
    assert out['result']['serverInfo']['name'] == 'swarms'
    listed = mcp.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    assert len(listed['result']['tools']) == len(mcp.TOOLS)


def test_unknown_protocol_version_falls_back_rather_than_failing():
    out = mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                      'params': {'protocolVersion': '1999-01-01'}})
    assert out['result']['protocolVersion'] == mcp.DEFAULT_PROTOCOL_VERSION


def test_notifications_get_no_response():
    assert mcp.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}) is None


def test_a_tool_failure_is_a_result_not_a_crash():
    """The MCP spec's isError, so the model reads the hint and retries."""
    out = mcp.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                      'params': {'name': 'swarms_balance',
                                 'arguments': {'owner': 'not-an-address'}}})
    assert 'error' not in out
    assert out['result']['isError'] is True
    assert 'structuredContent' in out['result']


def test_unknown_method_is_a_jsonrpc_error():
    out = mcp.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'tools/summon'})
    assert out['error']['code'] == -32601


def test_garbage_in_is_a_readable_error():
    assert mcp.handle('not a dict')['error']['code'] == -32600


def test_instructions_name_both_halves():
    i = mcp.INSTRUCTIONS
    assert 'swarms_architectures' in i and 'confirm=true' in i
    assert chain.MINT in i
    assert 'cannot sign' in i


# ── routing ──

def test_gateway_prefixes_all_resolve_to_the_same_route():
    """The console asks its own origin for /_api; the gateway mounts /api/swarms."""
    out = api.route('GET', '/health', '', {}, None)
    assert out['ok'] is True and out['mint'] == chain.MINT


def test_unknown_route_is_a_404_that_says_where_to_look():
    with pytest.raises(SwarmsError) as e:
        api.route('GET', '/nope', '', {}, None)
    assert e.value.status == 404 and 'GET /' in str(e.value)


def test_bearer_is_read_but_only_when_there_is_no_dedicated_header():
    assert api._key_from({'x-swarms-key': 'a', 'authorization': 'Bearer b'}) == 'a'
    assert api._key_from({'authorization': 'Bearer b'}) == 'b'
    assert api._key_from({}) is None


def test_mcp_config_points_at_this_deployment():
    cfg = api.mcp_config('claude', 'https://modc2.com/api/swarms')
    assert cfg['endpoint'] == 'https://modc2.com/api/swarms/mcp'
    assert cfg['http']['mcpServers']['swarms']['url'] == cfg['endpoint']
    assert 'claude mcp add' in cfg['claude_cli']


def test_console_exists_and_talks_to_its_own_origin():
    html = open(os.path.join(ROOT, 'console.html')).read()
    assert "'/_api'" in html
    for tab in ('swarm', 'agent', 'market', 'token', 'account', 'mcp'):
        assert f'data-t="{tab}"' in html


# ── live ──

@live
def test_live_the_token_is_the_token():
    t = chain.token()
    assert t['is_swarms'] is True
    assert t['price']['usd'] > 0
    assert t['supply']['decimals'] == 6
    assert t['market']['venues'] >= 1


@live
def test_live_a_quote_is_never_signed():
    q = chain.quote(side='buy', amount=0.1, pay_with='SOL')
    assert q['out'] > 0 and q['signed'] is False
    assert 'cannot sign' in q['note']


@live
def test_live_the_marketplace_is_public():
    out = Client(key=None).market(kind='prompts', limit=3)
    assert out['total'] > 0 and len(out['items']) <= 3


@live
def test_live_the_runtime_is_up():
    assert Client(key=None).health()['ok'] is True
