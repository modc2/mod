"""Graphs: states agents act on, judges that review the agent, feedback that
goes back around. Offline — no server, no model; agents here are random or
scripted, judges are the offline kinds or stubs.

    pytest src/tests/test_graph.py -q
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from graph import actors, judges, runner, states  # noqa: E402


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'STATE_DIR', str(tmp_path))


def shell_graph(rounds_of_actions, **kw):
    g = runner.template('shell')
    g['nodes'][1]['agent'] = {'kind': 'script', 'rounds': rounds_of_actions}
    g.update(kw)
    return g


def test_a_game_is_a_state_and_finishes_with_a_score():
    s = states.make({'kind': 'game', 'game': 'connect4'})
    try:
        view = s.reset(3)
        assert states.legal_moves(view)
        ep = runner.episode(s, actors.RandomActor(seed=1), steps=50, seed=3)
        assert ep['done'] and ep['state_score'] in (0.0, 0.5, 1.0)
        assert all(step['ok'] for step in ep['steps'])      # random legal moves are legal
    finally:
        s.close()


def test_an_illegal_move_is_refused_and_the_judge_says_so():
    s = states.make({'kind': 'game', 'game': 'connect4'})
    try:
        s.reset(1)
        got = s.act('banana')
        assert got['ok'] is False
    finally:
        s.close()
    v = judges.OutcomeJudge().review({'steps': [{'action': 'banana', 'ok': False}],
                                      'done': False, 'state_score': None})
    assert v['score'] == 0 and 'banana' in v['reason']


def test_feedback_reaches_the_agent_and_the_next_round_passes():
    r = runner.run(shell_graph([['echo nope > hello.txt'], ['echo hi > hello.txt']], steps=3))
    assert r['status'] == 'passed'
    assert r['scores'] == [0.0, 100.0]
    fb = r['rounds'][0]['feedback']
    assert 'outcome' in fb and 'did not finish' in fb
    assert r['rounds'][1]['episode']['feedback'] == fb


def test_an_agent_that_never_learns_runs_out_of_rounds():
    r = runner.run(shell_graph([['ls']], rounds=2, steps=2))
    assert r['status'] == 'out of rounds' and len(r['rounds']) == 2


def test_the_agent_sees_the_feedback_in_its_own_act_call():
    seen = []

    def agent(obs, tools, feedback):
        seen.append(feedback)
        return 'echo hi > hello.txt' if feedback else 'true'

    built = runner.template('shell')
    built['nodes'][1]['agent'] = actors.FnActor(fn=agent)
    built['steps'] = 1
    r = runner.run(built, save=False)
    assert r['status'] == 'passed' and seen[0] == '' and 'scored 0' in seen[1]


def test_a_judge_that_errors_abstains_and_the_bench_still_scores():
    def boom(ep):
        raise RuntimeError('judge down')

    v = judges.bench([judges.make(boom), judges.OutcomeJudge()],
                     {'steps': [], 'done': True, 'state_score': 1.0})
    assert v['score'] == 100.0
    assert v['votes'][0]['score'] is None and 'abstained' in v['votes'][0]['reason']


def test_a_feedback_agent_writes_the_advice():
    built = runner.template('shell')
    built['nodes'][1]['agent'] = actors.ScriptActor(rounds=[['true'], ['echo hi > hello.txt']])
    built['nodes'][3]['agent'] = actors.FnActor(fn=lambda prompt, *_: 'write hi into hello.txt')
    built['steps'] = 1
    r = runner.run(built, save=False)
    assert r['rounds'][0]['feedback'] == 'write hi into hello.txt'


def test_a_state_is_a_tool():
    tool = states.as_tool({'kind': 'shell', 'goal': 'g', 'check': 'test -f x'})
    tool.state.reset()
    try:
        assert 'Goal: g' in tool()['observation']
        assert tool('touch x')['ok'] and tool.state.done()
    finally:
        tool.state.close()


def test_check_catches_a_broken_graph():
    g = runner.template('game')
    g['nodes'][1]['state'] = 'nowhere'
    g['edges'].append({'from': 'review', 'to': 'end', 'when': 'score > 3'})
    errs = runner.check(g)
    assert any('must name a state' in e for e in errs)
    assert any('`when`' in e for e in errs)
    assert 'error' in runner.run(g)


def test_runs_are_saved_and_listed():
    r = runner.run(shell_graph([['echo hi > hello.txt']]))
    assert runner.runs()[0]['id'] == r['id']
    assert runner.get_run(r['id'])['status'] == 'passed'
    runner.save(dict(shell_graph([['echo hi > hello.txt']]), name='mine'))
    assert 'mine' in runner.saved() and runner.load('mine')['name'] == 'mine'


def test_protocol_answer_rule():
    assert actors.answer_of({'steps': [{'tool': 'think'}, {'tool': 'finish', 'params': {'summary': '3'}}]}) == '3'
    assert actors.answer_of({'response': 'rock'}) == 'rock'
