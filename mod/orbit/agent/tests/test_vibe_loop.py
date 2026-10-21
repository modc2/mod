"""Vibecoded loop agents — an agent built ON a code-edit bot, looping n rounds.

agent_vibe(base=, rounds=) drafts an agent that takes the bot's harness/tools
and saves `loop = {"builder": "self", "reviewer": …}`; Mod._run_feedback
resolves `self` to the agent being run. Model runs are stubbed throughout.
"""

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.mod import Agents, _check_loop
from src.mod import Mod

SRC = Path(__file__).resolve().parents[1] / 'src' / 'agents'
KEEP = ('reviewer', 'refactorer', 'claude-code', 'builder', 'feedback', 'vibe-builder')


@pytest.fixture
def agents(tmp_path):
    for n in KEEP:
        shutil.copytree(SRC / n, tmp_path / n, ignore=shutil.ignore_patterns('__pycache__'))
    a = Agents()
    a._dir = tmp_path
    a._autopin = lambda name: None
    return a


def _mod(agents, calls, trusted=True):
    mod = Mod.__new__(Mod)
    mod.agents = agents
    mod.identity = agents.identity
    mod.require_allowed = lambda key, what: None
    mod._harness_trusted = lambda harness, key=None: trusted
    mod._vibe_catalog = lambda key=None: {'tools': [{'name': n} for n in ('read', 'edit', 'think', 'finish')],
                                          'toolboxes': [], 'via': 'local'}
    real_run = Mod._run

    def fake_run(self, **kw):
        if not kw.get('_in_feedback'):
            return real_run(self, **kw)
        agent = kw.get('agent_type')
        calls.append({'agent': agent, 'query': kw['query'], 'prompt': kw.get('prompt')})
        text = f"{agent} answer {sum(c['agent'] == agent for c in calls)}"
        st = {'tool': 'finish', 'params': {'summary': text}, 'result': text}
        kw['on_step'](st)
        return [st]

    mod._run = fake_run.__get__(mod)
    mod.allowed_paths_for = lambda key: None
    mod.default_agent = lambda key=None: 'default'
    return mod


def _spec(query_seen):
    def complete(query, **kw):
        query_seen.append(query)
        return '```json\n' + json.dumps({
            'names': ['loop-smith'], 'icon': '⟳', 'description': 'tightens code',
            'prompt': 'You are Loop Smith. Tighten the code.', 'tools': ['read', 'bogus']}) + '\n```'
    return complete


def test_check_loop():
    assert _check_loop(None) is None and _check_loop({}) is None
    assert _check_loop({'builder': 'self', 'reviewer': 'Reviewer'}) == {'builder': 'self', 'reviewer': 'reviewer'}
    with pytest.raises(ValueError):
        _check_loop({'builder': 'x', 'judge': 'y'})
    with pytest.raises(ValueError):
        _check_loop({'builder': 'a"; import os'})
    with pytest.raises(ValueError):
        _check_loop(['builder'])


def test_create_and_update_round_trip_loop(agents):
    agents.create('my-bot', goal='You code.', loop={'builder': 'self', 'reviewer': 'reviewer'},
                  params={'rounds': 4})
    cfg = agents.get('my-bot')
    assert cfg['loop'] == {'builder': 'self', 'reviewer': 'reviewer'}
    assert cfg['params'] == {'rounds': 4}
    agents.update('my-bot', description='changed')
    assert agents.get('my-bot')['loop'] == {'builder': 'self', 'reviewer': 'reviewer'}
    agents.update('my-bot', loop=None)
    assert agents.get('my-bot')['loop'] is None


def test_self_loop_runs_the_agent_itself(agents):
    agents.create('my-bot', goal='You are My Bot.', loop={'builder': 'self', 'reviewer': 'reviewer'},
                  params={'rounds': 3})
    calls = []
    mod = _mod(agents, calls)
    trace = mod._run(query='make it fast', agent_type='my-bot', prompt='You are My Bot.')
    assert [c['agent'] for c in calls] == ['my-bot', 'reviewer', 'my-bot', 'reviewer', 'my-bot']
    assert 'reviewer answer 2' in calls[4]['query']
    assert 'my-bot answer 3' in trace[-1]['params']['summary']


def test_vibe_on_a_native_bot_with_rounds(agents):
    seen = []
    mod = _mod(agents, [])
    mod._scout_complete = _spec(seen)
    out = mod.agent_vibe('an agent that tightens my code a little each pass',
                         base='refactorer', rounds=4, direct=True, save=True)
    d = out['draft']
    assert d['base'] == 'refactorer' and d['harness'] is None
    assert d['tools'] == agents.get('refactorer')['tools']    # the bot's own loadout
    assert d['loop'] == {'builder': 'self', 'reviewer': 'reviewer'}
    assert d['params']['rounds'] == 4 and d['params']['agent'] == {'rounds': 4}
    assert 'BUILT ON: refactorer' in seen[0] and '4 passes' in seen[0]
    saved = agents.get(d['name'])
    assert saved['loop'] == d['loop'] and saved['params']['rounds'] == 4


def test_vibe_on_a_cli_bot(agents):
    mod = _mod(agents, [])
    mod._scout_complete = _spec([])
    d = mod.agent_vibe('claude code that reviews itself three times', base='claude-code',
                       rounds=3, direct=True)['draft']
    assert d['harness'] == 'claude' and d['tools'] is None
    # the bot's standing instructions stay under the new persona
    assert d['goal'].startswith('You are Loop Smith.')
    assert agents.get('claude-code')['goal'].strip() in d['goal']


def test_vibe_without_rounds_is_a_plain_agent(agents):
    mod = _mod(agents, [])
    mod._scout_complete = _spec([])
    d = mod.agent_vibe('a plain one-shot coder agent', base='refactorer', direct=True)['draft']
    assert 'loop' not in d and 'params' not in d


def test_vibe_refusals(agents):
    mod = _mod(agents, [], trusted=False)
    mod._scout_complete = _spec([])
    with pytest.raises(PermissionError):
        mod.agent_vibe('claude code agent for me please', base='claude-code', direct=True)
    with pytest.raises(ValueError):
        mod.agent_vibe('an agent built on a loop agent', base='feedback', direct=True)
    with pytest.raises(ValueError):
        mod.agent_vibe('an agent with a missing reviewer', rounds=3, reviewer='nobody', direct=True)
    bases = {b['name']: b for b in mod.vibe_bases()}
    assert 'feedback' not in bases and 'vibe-builder' not in bases
    assert bases['claude-code']['allowed'] is False and bases['refactorer']['allowed'] is True
