"""The agent: plain English in, the same tools out, the same write gate.

Runs against the throwaway chain conftest.py sets up; the HTTP half starts
the real server on a spare port with the block loop off.
"""

import json
import socket
import threading
import time
import urllib.request

import pytest

import agent as A
import api


# ── the rules brain ───────────────────────────────────────────────

@pytest.mark.parametrize('text,tool,args', [
    ('status', 'pq_head', {}),
    ('create wallet alice with slh-dsa-shake-128f', 'pq_wallet',
     {'action': 'create', 'name': 'alice', 'scheme': 'SLH-DSA-SHAKE-128f'}),
    ('faucet 100 to alice', 'pq_faucet', {'amount': '100', 'wallet': 'alice'}),
    ('set greeting to hello world for 2 days', 'pq_set',
     {'key': 'greeting', 'data': 'hello world', 'days': '2'}),
    ('set greeting = hello (dry run)', 'pq_set',
     {'key': 'greeting', 'data': 'hello', 'dry_run': True}),
    ('store blob as abc raw', 'pq_set',
     {'key': 'blob', 'value': 'abc', 'value_kind': 'raw'}),
    ('send 5 to bob from alice', 'pq_transfer',
     {'amount': '5', 'to': 'bob', 'wallet': 'alice'}),
    ('list greeting for 20', 'pq_list', {'key': 'greeting', 'price': '20'}),
    ('buy greeting for 25', 'pq_buy', {'key': 'greeting', 'max_price': '25'}),
    ('balance of alice', 'pq_account', {'wallet': 'alice'}),
    ('keys starting with gree', 'pq_keys', {'prefix': 'gree'}),
    ('block 3', 'pq_block', {'block': '3'}),
    ('verify signatures', 'pq_verify', {'signatures': True}),
    ('get greeting', 'pq_get', {'key': 'greeting'}),
    ('quote greeting = hi for 3 hours', 'pq_quote',
     {'key': 'greeting', 'data': 'hi', 'hours': '3'}),
    ('pq' + 'ab' * 20, 'pq_account', {'address': 'pq' + 'ab' * 20}),
])
def test_parse(text, tool, args):
    (got_tool, got_args, _), = A.parse(text)
    assert (got_tool, got_args) == (tool, args)


def test_parse_chains_and_unknowns():
    plan = A.parse('status then wallets; flibber')
    assert [p[0] for p in plan] == ['pq_head', 'pq_wallet', 'unknown']


# ── runs ──────────────────────────────────────────────────────────

def test_run_writes_when_allowed():
    out = A.run('create wallet agentw then faucet 20 to agentw then '
                'set agent-key to hi from agentw then get agent-key',
                can_write=True)
    tools = [s['tool'] for s in out['steps']]
    assert tools == ['pq_wallet', 'pq_faucet', 'pq_set', 'pq_get']
    assert not any('error' in s for s in out['steps']), out['steps']
    assert out['brain'] == 'rules' and out['free'] is True
    assert 'agent-key =' in out['result']


def test_run_without_write_dry_runs_or_refuses():
    before = A.mcpsrv.node().head()['height']
    out = A.run('set nope to x from agentw; faucet 5 to agentw',
                can_write=False)
    set_step, faucet_step = out['steps']
    assert set_step['result']['dry_run'] is True and 'note' in set_step
    assert 'may not write' in faucet_step['error']
    assert A.mcpsrv.node().head()['height'] == before


def test_reader_agent_never_writes():
    out = A.run('send 1 to agentw from validator', agent='pq-reader',
                can_write=True)
    assert out['can_write'] is False
    assert out['steps'][0]['result']['dry_run'] is True


def test_llm_brain_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(A, 'LLM_URL', 'http://127.0.0.1:1')
    out = A.run('status', brain='llm')
    assert out['brain'] == 'rules' and 'llm unavailable' in out['fallback']


def test_contract_shape():
    roster = A.agents()
    assert {a['id'] for a in roster['agents']} == {'pq', 'pq-reader'}
    assert roster['default'] == 'pq'
    events = [e['type'] for e in A.run_stream('status')]
    assert events[0] == 'model_start' and events[-1] == 'done'
    assert 'tool_start' in events and 'step' in events


# ── over HTTP ─────────────────────────────────────────────────────

def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope='module')
def base():
    port = _free_port()
    threading.Thread(target=api.serve, kwargs={
        'port': port, 'bind': '127.0.0.1', 'block_loop': False},
        daemon=True).start()
    url = f'http://127.0.0.1:{port}'
    for _ in range(50):
        try:
            urllib.request.urlopen(url + '/health', timeout=1)
            return url
        except OSError:
            time.sleep(0.1)
    raise RuntimeError('server did not start')


def _post(url, body, headers=None):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {'content-type': 'application/json',
                                  **(headers or {})})
    return urllib.request.urlopen(req, timeout=60)


def test_http_agents_probe(base):
    got = json.load(urllib.request.urlopen(base + '/agents'))
    assert isinstance(got['agents'], list) and got['agents']
    card = json.load(urllib.request.urlopen(base + '/.well-known/agent.json'))
    assert card['protocol'] == 'agent/1.0'


def test_http_run_stream(base):
    r = _post(base + '/run/stream', {'query': 'status then wallets'})
    assert r.headers['content-type'] == 'text/event-stream'
    events = [json.loads(line[6:]) for line in r.read().decode().splitlines()
              if line.startswith('data: ')]
    assert events[-1]['type'] == 'done'
    assert [s['tool'] for s in events[-1]['steps']] == ['pq_head', 'pq_wallet']


def test_http_run_respects_secret(base, monkeypatch):
    monkeypatch.setattr(api, 'secret', lambda: 's3cret')
    out = json.load(_post(base + '/run', {'query': 'set gated to x'}))
    assert out['can_write'] is False
    assert out['steps'][0]['result']['dry_run'] is True
    out = json.load(_post(base + '/run', {'query': 'status'},
                          {'authorization': 'Bearer s3cret'}))
    assert out['can_write'] is True


def test_http_run_needs_query(base):
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + '/run', {})
    assert e.value.code == 400
