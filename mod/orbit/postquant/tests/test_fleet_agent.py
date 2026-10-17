"""The agent brain: orbit/agent runs the loop, postquant gates and relays.

orbit/agent is faked at fleet.stream — these tests pin what postquant does
with its events, not what a 1.2B model decides.
"""

import urllib.error

import agent
import fleet
import mcp as mcpsrv


def _head_result():
    return {'server': 'postquant', 'tool': 'pq_head', 'success': True,
            'result': {'height': 42, 'chain_id': 'postquant-test'}}


def _fake(events, seen=None):
    def stream(query, tool_names, prompt, model=None, steps=None):
        if seen is not None:
            seen.update(query=query, tools=tool_names, prompt=prompt)
        for ev in events:
            if isinstance(ev, Exception):
                raise ev
            yield ev
    return stream


def _run(monkeypatch, events, can_write=False, seen=None, **kw):
    monkeypatch.setattr(fleet, 'stream', _fake(events, seen))
    return list(agent.run_stream('what is the height?', brain='agent',
                                 can_write=can_write, **kw))


def test_relays_steps_with_local_names_and_unwrapped_results(monkeypatch):
    evs = _run(monkeypatch, [
        {'type': 'model_start', 'step': 0, 'model': 'LFM'},
        {'type': 'token', 'text': '<STEP>{"tool": "mcp.postquant.pq_head"}</STEP>'},
        {'type': 'tool_start', 'tool': 'mcp.postquant.pq_head', 'params': {}},
        {'type': 'step', 'step': {'tool': 'mcp.postquant.pq_head', 'params': {},
                                  'result': _head_result()}},
        {'type': 'step', 'step': {'tool': 'response', 'params': {}, 'result':
                                  'Answer what you asked: the height is 42.\n\n'
                                  'finish(summary: height 42)'}},
        {'type': 'done', 'result': []},
    ])
    starts = [e for e in evs if e['type'] == 'tool_start']
    assert starts and starts[0]['tool'] == 'pq_head'
    steps = [e['step'] for e in evs if e['type'] == 'step'
             and e['step']['tool'] == 'pq_head']
    assert steps[0]['result'] == {'height': 42, 'chain_id': 'postquant-test'}
    # the loop's raw plan tokens never reach the console; the clean answer does
    tokens = [e['text'] for e in evs if e['type'] == 'token']
    assert tokens == ['the height is 42.']
    done = evs[-1]
    assert done['type'] == 'done' and done['brain'] == 'agent'
    assert done['result'] == 'the height is 42.' and done['fallback'] is None
    assert any(e.get('brain') == 'agent' for e in evs if e['type'] == 'model_start')


def test_finish_summary_wins(monkeypatch):
    evs = _run(monkeypatch, [
        {'type': 'step', 'step': {'tool': 'finish',
                                  'params': {'summary': 'Height is 42.'}}},
        {'type': 'done', 'result': []},
    ])
    assert evs[-1]['result'] == 'Height is 42.'


def test_a_reader_is_never_offered_a_write_tool(monkeypatch):
    seen = {}
    monkeypatch.setattr(fleet, 'stream', _fake(
        [{'type': 'step', 'step': {'tool': 'finish', 'params': {'summary': 'ok'}}}],
        seen))
    list(agent.run_stream('send 5 to bob then set greeting to hi, check market '
                          'and wallet', brain='agent', can_write=False))
    local = [fleet.local_tool(t) for t in seen['tools']]
    assert local and local[0] == 'pq_head'
    assert not set(local) & mcpsrv.WRITE_TOOLS
    assert 'may not write' in seen['prompt']


def test_a_writer_gets_the_write_tools_the_message_names(monkeypatch):
    seen = {}
    monkeypatch.setattr(fleet, 'stream', _fake(
        [{'type': 'step', 'step': {'tool': 'finish', 'params': {'summary': 'ok'}}}],
        seen))
    list(agent.run_stream('send 5 to bob', brain='agent', can_write=True))
    assert 'mcp.postquant.pq_transfer' in seen['tools']
    assert len(seen['tools']) <= fleet.MAX_TOOLS


def test_pq_reader_agent_never_writes_even_for_a_writer(monkeypatch):
    seen = {}
    monkeypatch.setattr(fleet, 'stream', _fake(
        [{'type': 'step', 'step': {'tool': 'finish', 'params': {'summary': 'ok'}}}],
        seen))
    list(agent.run_stream('send 5 to bob', agent='pq-reader', brain='agent',
                          can_write=True))
    assert 'mcp.postquant.pq_transfer' not in seen['tools']


def test_unreachable_before_any_step_falls_back_to_chat(monkeypatch):
    evs = _run(monkeypatch, [urllib.error.URLError('connection refused')])
    done = evs[-1]
    assert done['type'] == 'done' and done['brain'] == 'agent'
    assert 'orbit/agent unavailable' in done['fallback']
    assert done['result']                     # chat (and its own fallback) answered


def test_failure_after_a_step_never_reruns_it(monkeypatch):
    ran = []
    monkeypatch.setattr(agent, '_run_chat', lambda *a, **k: ran.append(1))
    evs = _run(monkeypatch, [
        {'type': 'step', 'step': {'tool': 'mcp.postquant.pq_head', 'params': {},
                                  'result': _head_result()}},
        {'type': 'error', 'error': 'model fell over'},
    ])
    assert not ran
    done = evs[-1]
    assert 'model fell over' in done['fallback']
    assert done['steps'] and done['steps'][0]['tool'] == 'pq_head'


def test_auto_prefers_the_agent_when_it_is_up(monkeypatch):
    monkeypatch.setattr(fleet, 'ready', lambda: True)
    monkeypatch.setattr(fleet, 'stream', _fake(
        [{'type': 'step', 'step': {'tool': 'finish', 'params': {'summary': 'hi'}}}]))
    evs = list(agent.run_stream('hello'))
    assert evs[-1]['brain'] == 'agent'


def test_auto_without_orbit_agent_is_unchanged():
    # conftest disables both neighbours: auto must still land on rules
    assert not fleet.enabled()
    evs = list(agent.run_stream('status'))
    assert evs[-1]['brain'] == 'rules'


def test_brains_lists_the_agent():
    b = agent.brains()['agent']
    assert b['provider'] == fleet.PROVIDER and b['ready'] is False


def test_clean_strips_loop_anchors():
    assert fleet.clean('<STEP>{"tool":"x"}</STEP>\nThe height is 7.') == 'The height is 7.'
    assert fleet.clean('Height 7.\n\nfinish(summary: 7)') == 'Height 7.'


def test_tool_routing_is_capped_and_head_first():
    names = fleet.tools('market algos quote block tx history mempool verify',
                        set(mcpsrv.TOOLS))
    assert names[0] == 'mcp.postquant.pq_head'
    assert len(names) == fleet.MAX_TOOLS
