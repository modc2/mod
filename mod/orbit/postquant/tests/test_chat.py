"""The chat brain: liquidai's LFM talks, the rules brain acts.

Every test fakes lfm — conftest already disabled the real one — because what
is under test is the choreography: steps execute exactly once whatever the
model does, the model is grounded in their results, and the voice degrades
to the rules summary without re-signing anything.
"""

import urllib.error

import agent as A
import lfm
import mcp as mcpsrv


def _fake_stream(script):
    """A stand-in for lfm.stream that records what it was asked."""
    calls = []

    def stream(messages, **kw):
        calls.append(messages)
        for ev in script:
            if isinstance(ev, Exception):
                raise ev
            yield ev

    return stream, calls


def _events(query, **kw):
    return list(A.run_stream(query, **kw))


def test_disabled_lfm_means_auto_is_rules():
    assert not lfm.ready()
    out = A.run('status')
    assert out['brain'] == 'rules'


def test_chat_executes_then_voices(monkeypatch):
    stream, calls = _fake_stream([
        {'type': 'start'},
        {'type': 'token', 'text': 'The chain '},
        {'type': 'token', 'text': 'is at the tip.'},
        {'type': 'done'}])
    monkeypatch.setattr(lfm, 'stream', stream)
    evs = _events('status', brain='chat', can_write=True)
    done = evs[-1]
    assert done['type'] == 'done' and done['brain'] == 'chat'
    assert done['result'] == 'The chain is at the tip.'
    assert [s['tool'] for s in done['steps']] == ['pq_head']
    # the step ran and streamed before the model spoke
    kinds = [e['type'] for e in evs]
    assert kinds.index('step') < kinds.index('model_start')
    # the model was grounded in the executed step, not asked to pick tools
    # grounding rides in a trailing system footer, after the user turn
    footer = calls[0][-1]
    assert footer['role'] == 'system'
    assert 'pq_head' in footer['content'] and 'height' in footer['content']
    assert 'ALREADY EXECUTED' in footer['content']
    assert calls[0][-2] == {'role': 'user', 'content': 'status'}


def test_chat_token_events_stream(monkeypatch):
    stream, _ = _fake_stream([{'type': 'token', 'text': 'a'},
                              {'type': 'token', 'text': 'b'}])
    monkeypatch.setattr(lfm, 'stream', stream)
    evs = _events('hello there', brain='chat')
    tokens = [e['text'] for e in evs if e['type'] == 'token']
    assert tokens == ['a', 'b']
    assert evs[-1]['result'] == 'ab'


def test_chat_fallback_keeps_steps_and_does_not_rerun(monkeypatch):
    ran = []
    real = mcpsrv.call_tool

    def counting(tool, args):
        ran.append(tool)
        return real(tool, args)

    monkeypatch.setattr(mcpsrv, 'call_tool', counting)
    stream, _ = _fake_stream([urllib.error.URLError('down')])
    monkeypatch.setattr(lfm, 'stream', stream)
    out = A.run('status', brain='chat')
    assert 'liquidai unavailable' in out['fallback']
    assert 'height' in out['result']            # the rules summary survived
    assert ran.count('pq_head') == 1            # fallback re-voiced, not re-ran


def test_chat_pure_question_runs_no_tools(monkeypatch):
    stream, calls = _fake_stream([{'type': 'token', 'text': 'Rent is per '
                                   'byte-hour from escrow.'}])
    monkeypatch.setattr(lfm, 'stream', stream)
    out = A.run('why does every byte pay rent here?', brain='chat')
    assert out['steps'] == []
    assert 'escrow' in out['result']
    # the glossary grounded the model instead of becoming a failed tool call
    assert 'rent' in calls[0][0]['content'].lower()


def test_rules_answers_glossary_questions():
    out = A.run('why are there no curves here?', brain='rules')
    assert out['steps'] == []
    assert 'did not understand' not in out['result']
    assert 'Shor' in out['result'] or 'curve' in out['result']


def test_chat_fallback_uses_glossary(monkeypatch):
    stream, _ = _fake_stream([urllib.error.URLError('down')])
    monkeypatch.setattr(lfm, 'stream', stream)
    out = A.run('explain rent', brain='chat')
    assert 'liquidai unavailable' in out['fallback']
    assert 'rent' in out['result'].lower()
    assert 'not a chain command' not in out['result']


def test_chat_history_is_forwarded_and_clipped(monkeypatch):
    stream, calls = _fake_stream([{'type': 'token', 'text': 'ok'}])
    monkeypatch.setattr(lfm, 'stream', stream)
    history = [{'role': 'user', 'content': 'u%d' % i} for i in range(15)]
    history += [{'role': 'tool', 'content': 'dropped'},
                {'role': 'assistant', 'content': 'x' * 5000}]
    A.run('and now?', brain='chat', history=history)
    msgs = calls[0]
    roles = [m['role'] for m in msgs]
    assert roles[0] == 'system' and roles[-2:] == ['user', 'system']
    assert 'tool' not in roles                     # unknown roles dropped
    assert len(msgs) <= 13                     # system + ≤10 history + user + footer
    assert max(len(m['content']) for m in msgs[1:-2]) <= 1500


def test_chat_write_gate_dry_runs(monkeypatch):
    stream, calls = _fake_stream([{'type': 'token', 'text': 'priced it'}])
    monkeypatch.setattr(lfm, 'stream', stream)
    out = A.run('set chatkey to hello for 1 hours', brain='chat',
                can_write=False)
    step = out['steps'][0]
    assert step['tool'] == 'pq_set' and step['result']['dry_run']
    assert 'may not write' in calls[0][0]['content']


def test_brains_reports_chat():
    b = A.brains()
    assert b['chat']['provider'] == 'liquidai'
    assert b['chat']['ready'] is False             # disabled in tests
