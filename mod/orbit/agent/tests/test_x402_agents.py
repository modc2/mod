"""x402 services as tools AND agents, every agent as a tool, all over MCP.

    x402.<slug>     a mounted paid service (src/tools/x402) — x402's own MCP
                    is stubbed, so nothing here touches the network or money
    x402-<slug>     the tool-agent mounting creates (agents/<name>, tool=…)
    agent.<name>    any agent, callable by the others (src/tools/agents)
    x402__/agent__  the same, re-served as first-class MCP tools (src/mcp.py)
"""
import json
import os
import shutil
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.mod import Agents, _check_tool
from src.mod import Mod
from src.tools.mod import Tools
from src.tools.x402.mod import X402Tools, _input_spec
from src.tools.agents.mod import AgentTools
import src.mcp as mcp

URL = 'https://wx.example.dev/forecast'
LISTING = {
    'url': URL, 'name': 'Forecast', 'description': 'Weather forecast for a point',
    'host': 'wx.example.dev', 'price_usd': 0.01, 'networks': ['base'],
    'raw': {'extensions': {'bazaar': {'schema': {'properties': {'input': {
        'properties': {
            'method': {'enum': ['GET']},
            'queryParams': {'properties': {
                'lat': {'type': 'string', 'description': 'latitude'},
                'lon': {'type': 'string'},
                'units': {'type': 'string', 'enum': ['metric', 'imperial']}},
                'required': ['lat', 'lon']}}}}}}}},
}


class FakeX402(X402Tools):
    """X402Tools with x402's MCP replaced by a table: records every call,
    answers x402_call with a 402 unless the mount pays."""

    def __init__(self, tmp, **kw):
        self.calls = []
        super().__init__(state_dir=str(tmp), url='http://unused/mcp', **kw)

    def _call(self, tool, args):
        self.calls.append((tool, args))
        if tool == 'x402_service':
            return dict(LISTING)
        if tool == 'x402_find':
            return {'mode': 'semantic', 'items': [
                {'url': URL, 'name': 'Forecast', 'price_usd': 0.01}]}
        if tool == 'x402_search':
            return {'items': [{'url': URL, 'name': 'Forecast'},
                              {'url': 'https://wx.example.dev/alerts'}]}
        if tool == 'x402_call':
            if args.get('pay') == 'none':
                return {'status': 402, 'note': 'unpaid'}
            return {'status': 200, 'json': {'temp': 21, 'q': args.get('query')},
                    'paid': {'by': 'house'}, 'receipt': {'success': True}}
        raise AssertionError(tool)


@pytest.fixture
def x(tmp_path):
    return FakeX402(tmp_path)


# ── x402.<slug>: a paid service as a tool ────────────────────────────

def test_input_spec_reads_bazaar_schema():
    spec = _input_spec(LISTING['raw'])
    assert spec['method'] == 'GET'
    assert spec['params']['lat'] == {'type': 'string', 'required': True,
                                     'where': 'query', 'hint': 'latitude'}
    assert 'metric' in spec['params']['units']['hint']


def test_mount_schema_and_persist(x, tmp_path):
    e = x.mount(URL)
    assert e['name'] == 'x402.forecast' and e['pay'] == 'none'
    sch = x.schema(['x402.forecast'])['x402.forecast']
    assert sch['params']['lat']['required'] and 'where' not in sch['params']['lat']
    assert '$0.01/call' in sch['description']
    # survives a restart, case-insensitively (the loop lowercases names)
    again = FakeX402(tmp_path)
    assert again.exists('x402.forecast') and again.exists('x402.FORECAST')
    assert not again.exists('mcp.forecast')


def test_run_unpaid_reports_the_price(x):
    x.mount(URL)
    out = x.run('x402.forecast', lat='40.7', lon=-74)
    assert out['success'] is False and out['status'] == 402
    assert 'payment required ($0.01/call)' in out['error']
    call = x.calls[-1][1]
    assert call['query'] == {'lat': '40.7', 'lon': '-74'} and call['pay'] == 'none'


def test_run_paying_mount(x):
    x.mount(URL, pay='auto', max_price_usd=0.02)
    out = x.run('x402.forecast', lat='1', lon='2')
    assert out['success'] and out['result']['temp'] == 21
    assert out['payment']['by'] == 'house'
    assert x.calls[-1][1]['max_price_usd'] == 0.02


def test_mount_server_and_unmount(x):
    res = x.mount_server('https://wx.example.dev')
    assert res['total'] == 2 and all(n.startswith('x402.wx.') for n in res['mounted'])
    assert sorted(x.unmount('server:wx')['removed']) == sorted(res['mounted'])
    assert x.ls() == []


def test_find_marks_mounted(x):
    assert x.find('weather')['items'][0]['mounted'] is False
    x.mount(URL)
    assert x.find('weather')['items'][0]['tool'] == 'x402.forecast'
    assert x.find('weather')['items'][0]['mounted'] is True


def test_bad_pay_mode_refused(x):
    with pytest.raises(ValueError):
        x.mount(URL, pay='yolo')


# ── the union registry: six kinds, one namespace ────────────────────

def _tools(x):
    t = Tools(path='/tmp/agent_x402_test_tools.json')
    t.x402 = x
    return t


def test_union_registry_knows_x402(x):
    x.mount(URL)
    t = _tools(x)
    assert t.kind('x402.forecast') == 'x402' and t.registry('x402.forecast') is x
    assert 'x402.forecast' in t.ls(mods=True)
    assert 'x402.forecast' not in t.ls()                 # potential, not loaded
    assert t.schema(['x402.forecast'])['x402.forecast']['kind'] == 'x402'


# ── tool-agents: an agent that IS one tool ───────────────────────────

def test_check_tool():
    assert _check_tool('x402.forecast') == 'x402.forecast'
    assert _check_tool('') is None
    with pytest.raises(ValueError):
        _check_tool('rm -rf /')


@pytest.fixture
def agents():
    a = Agents()
    a.identity = SimpleNamespace(
        require_signed_in=lambda key=None, operation=None: None,
        addr=lambda key=None: None, require=lambda *a, **k: None,
        owner_of=lambda o=None: {'owner': None, 'owner_source': 'host'},
        can_manage=lambda *a, **k: True)
    made = []
    yield a, made
    for n in made:
        shutil.rmtree(a._dir / n, ignore_errors=True)
        a._cache.pop(n, None)


def _mod(x, agents):
    mod = Mod.__new__(Mod)
    mod.agents = agents
    mod.tools = _tools(x)
    mod.tools.agents = AgentTools(context=mod, tools=mod.tools)
    mod.allowed_paths_for = lambda key: None
    mod.default_agent = lambda key=None: 'default'
    return mod


def test_mount_creates_tool_agent_and_unmount_removes_it(x, agents):
    reg, made = agents
    made.append('x402-forecast')
    mod = _mod(x, reg)
    res = Mod.x402_mount(mod, url=URL)
    assert res['agents'] == [{'name': 'x402-forecast', 'tool': 'x402.forecast',
                              'created': True}]
    cfg = reg.get('x402-forecast')
    assert cfg['tool'] == 'x402.forecast' and cfg['tools'] == ['x402.forecast']
    assert 'never retry a payment' in cfg['goal']
    # re-mounting refreshes the same agent instead of failing
    assert Mod.x402_mount(mod, url=URL)['agents'][0].get('updated')
    out = Mod.x402_unmount(mod, 'x402.forecast')
    assert out['agents_removed'] == ['x402-forecast']
    assert 'x402-forecast' not in reg.ls()


def test_tool_agent_json_query_is_a_direct_call(x, agents):
    reg, made = agents
    made.append('x402-forecast')
    mod = _mod(x, reg)
    Mod.x402_mount(mod, url=URL, pay='auto')
    steps = []
    trace = Mod._run(mod, query=json.dumps({'lat': '1', 'lon': '2'}),
                     agent_type='x402-forecast', on_step=steps.append)
    assert [s['tool'] for s in trace] == ['x402.forecast', 'finish']
    assert '"temp": 21' in Mod.graph_answer(trace) and steps == trace
    # unpaid mount: one error step, so the run reads as failed
    x.set_pay('x402.forecast', 'none')
    trace = Mod._run_tool_agent(mod, reg.get('x402-forecast'), '{"lat":"1","lon":"2"}')
    assert len(trace) == 1 and trace[0]['tool'] == 'error'
    assert 'payment required' in trace[0]['error']
    # prose is not arguments: the loop takes over
    assert Mod._run_tool_agent(mod, reg.get('x402-forecast'), 'weather in NYC?') is None


def test_tool_agent_sandboxed_caller_refused(x, agents):
    reg, made = agents
    made.append('x402-forecast')
    mod = _mod(x, reg)
    Mod.x402_mount(mod, url=URL, pay='auto')
    trace = Mod._run_tool_agent(mod, reg.get('x402-forecast'), '{"lat":"1"}',
                                allowed_paths=['/tmp/x'])
    assert trace[0]['tool'] == 'error' and 'host-only' in trace[0]['error']
    assert not any(c[0] == 'x402_call' for c in x.calls)


# ── agent.<name>: every agent, callable as a tool ───────────────────

def test_agent_tool_schema_carries_its_tools_params(x, agents):
    reg, made = agents
    made.append('x402-forecast')
    mod = _mod(x, reg)
    Mod.x402_mount(mod, url=URL)
    at = mod.tools.agents
    assert 'agent.x402-forecast' in at.ls() and mod.tools.kind('agent.reviewer') == 'agent'
    p = at.schema(['agent.x402-forecast'])['agent.x402-forecast']['params']
    assert p['task']['required'] is False and p['lat']['required'] is False
    assert at.schema(['agent.reviewer'])['agent.reviewer']['params']['task']['required']


def test_agent_tool_with_params_runs_the_tool(x, agents):
    reg, made = agents
    made.append('x402-forecast')
    mod = _mod(x, reg)
    Mod.x402_mount(mod, url=URL, pay='auto')
    seen = {}

    def graph_agent(agent, query, steps=None, key=None):
        seen.update(agent=agent, query=query)
        trace = Mod._run(mod, query=query, agent_type=agent)
        return {'ok': True, 'text': Mod.graph_answer(trace), 'trace': trace}

    mod._graph_agent = graph_agent
    mod._clear_run_state = lambda: None
    out = mod.tools.run('agent.x402-forecast', lat='1', lon='2')
    assert out['success'] and out['tool'] == 'x402.forecast'
    assert json.loads(seen['query']) == {'lat': '1', 'lon': '2'}
    assert '21' in out['result']
    assert mod.tools.run('agent.reviewer')['success'] is False     # needs a task


# ── MCP: mounted services and agents as first-class MCP tools ────────

def test_mcp_dynamic_tools(x, agents, monkeypatch):
    reg, made = agents
    made.append('x402-forecast')
    mod = _mod(x, reg)
    Mod.x402_mount(mod, url=URL)
    monkeypatch.setattr(mcp, '_mod', lambda: mod)
    names = {t['name']: t for t in mcp.tool_list()}
    assert 'agent_x402' in names
    t = names['x402__forecast']
    assert t['inputSchema']['required'] == ['lat', 'lon']
    # tool-agents by default; every agent only when asked for
    assert 'agent__x402-forecast' in names and 'agent__reviewer' not in names
    monkeypatch.setattr(mcp, 'DYN_AGENTS', 'all')
    assert 'agent__reviewer' in {t['name'] for t in mcp.tool_list()}
    ran = {}
    monkeypatch.setattr(mcp, '_t_tool_run', lambda a, key: ran.update(a) or {'ok': 1})
    monkeypatch.setattr(mcp, 'LOCAL', False)
    with pytest.raises(PermissionError):
        mcp.call_tool('x402__forecast', {'lat': '1'}, key=None)
    assert mcp.call_tool('x402__forecast', {'lat': '1'}, key='tok') == {'ok': 1}
    assert ran == {'name': 'x402.forecast', 'params': {'lat': '1'}}
