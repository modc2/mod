"""Feedback agent — builder ⇄ reviewer loop for N rounds (Mod._run_feedback).

The inner runs are stubbed: what is under test is the loop's shape — who runs
when, what each turn is told, and what the whole run hands back.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.agents.mod import Agents
from src.mod import Mod


def _mod(calls):
    mod = Mod.__new__(Mod)
    mod.agents = Agents()
    real_run = Mod._run

    def fake_run(self, **kw):
        agent = kw.get('agent_type')
        if not kw.get('_in_feedback'):
            return real_run(self, **kw)
        calls.append({'agent': agent, 'query': kw['query'], 'prompt': kw.get('prompt')})
        n = sum(1 for c in calls if c['agent'] == agent)
        text = f"{agent} answer {n}"
        st = {'tool': 'finish', 'params': {'summary': text}, 'result': text}
        kw['on_step'](st)
        return [st]

    mod._run = fake_run.__get__(mod)
    mod.allowed_paths_for = lambda key: None
    mod.default_agent = lambda key=None: 'default'
    return mod


def _feedback(mod, **kw):
    return Mod._run_feedback(mod, mod.agents.get('feedback'), **kw)


def test_feedback_agent_registered():
    cfg = Agents().get('feedback')
    assert cfg['loop'] == {'builder': 'builder', 'reviewer': 'reviewer'}
    assert cfg['params']['rounds'] == 3
    assert cfg['builtin'] is True


def test_default_three_rounds_review_between():
    calls, steps = [], []
    mod = _mod(calls)
    trace = _feedback(mod, query='make it fast', on_step=steps.append)
    assert [c['agent'] for c in calls] == ['builder', 'reviewer', 'builder', 'reviewer', 'builder']
    # round 2's builder sees round 1's report and review 1
    assert 'builder answer 1' in calls[2]['query'] and 'reviewer answer 1' in calls[2]['query']
    assert 'round 3 of 3' in calls[4]['query'] and 'reviewer answer 2' in calls[4]['query']
    # the reviewer is told the task and what it is reviewing
    assert 'make it fast' in calls[1]['query'] and 'builder answer 1' in calls[1]['query']
    assert trace[-1]['tool'] == 'finish'
    assert 'builder answer 3' in trace[-1]['params']['summary']
    assert Mod.graph_answer(trace).endswith('builder answer 3')
    assert steps == trace


@pytest.mark.parametrize('rounds,builds', [(1, 1), (2, 2), ('5', 5), (99, Mod.FEEDBACK_MAX_ROUNDS)])
def test_rounds_param(rounds, builds):
    calls = []
    _feedback(_mod(calls), query='x', rounds=rounds)
    assert sum(c['agent'] == 'builder' for c in calls) == builds
    assert sum(c['agent'] == 'reviewer' for c in calls) == builds - 1


def test_dispatch_through_run_and_overrides():
    calls = []
    mod = _mod(calls)
    mod._run(query='x', agent_type='feedback', rounds=2, builder='refactorer', reviewer='safety')
    assert [c['agent'] for c in calls] == ['refactorer', 'safety', 'refactorer']


def test_console_prompt_is_not_the_builders_persona():
    calls = []
    mod = _mod(calls)
    goal = mod.agents.get('feedback')['goal']
    _feedback(mod, query='x', rounds=2, prompt=f"{goal}\n\nModule note: edit orbit/foo")
    builder_prompt = calls[0]['prompt']
    assert goal not in builder_prompt and 'Module note' in builder_prompt
    assert calls[1]['prompt'] is None   # the reviewer keeps its own goal


def test_bad_agents_refused():
    mod = _mod([])
    with pytest.raises(ValueError):
        _feedback(mod, query='x', builder='no-such-agent')
    with pytest.raises(ValueError):
        _feedback(mod, query='x', reviewer='feedback')
