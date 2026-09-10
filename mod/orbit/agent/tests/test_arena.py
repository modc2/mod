"""
tests for the arena — agents competing on the same tasks

covers:
    - task pool (eval suites flattened, checks -> scorers, fixtures)
    - scoring (correctness / reliability / efficiency, files on disk)
    - matches (records, scratch dirs, forfeits)
    - rating (pairwise Elo, draws, the board)
    - rounds (rotation, caps, eval subject lists)
    - qualifiers (a newcomer rated against the incumbents' records)
    - the scheduler (newcomer detection, daily period, enable switch)
    - forward() and config persistence
    - the openarena bridge (schema translation, partial credit, void on a
      judge that is down) — stubbed, never over the wire
    - tiers (the model held still, the agent moved: design spread inside one
      model, and what a design keeps when the model gets cheaper)

run:
    cd ~/mod/mod/orbit/agent && python3 -m pytest tests/test_arena.py -v
"""
import json
import os
import sys
import time
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.arena.mod import Arena, Scheduler, ELO_START
from src.arena import openarena as oa
from src.arena import models as mb
from src.arena import tiers as tb
from src.evals.scorers import run_scorer, steps_of


# ═══════════════════════════════════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def no_neighbours(monkeypatch):
    """No test in this file touches the openarena module over the wire.

    The pool pulls openarena's tasks in, so a host that happens to be running
    it would otherwise change what every task-count assertion here sees — the
    suite would pass or fail depending on what else is up on the box. The
    bridge tests below stub it deliberately instead.
    """
    down = lambda *a, **k: (_ for _ in ()).throw(
        oa.Unavailable('openarena is stubbed out in tests'))
    monkeypatch.setattr(oa, 'index', lambda ttl=0: [])
    monkeypatch.setattr(oa, 'get_task', down)
    monkeypatch.setattr(oa, 'info', down)
    monkeypatch.setattr(oa, 'grade', down)
    oa.forget()
    yield
    oa.forget()


class FakeAgents:
    """A registry with a known field, so a host's own agents can't skew a test."""

    def __init__(self, names=('alpha', 'beta', 'gamma'), harness=()):
        self._names = list(names)
        self._harness = set(harness)

    def ls(self):
        return sorted(self._names)

    def get(self, name):
        if name not in self._names:
            raise KeyError(name)
        return {'name': name, 'icon': '◆',
                'harness': 'claude' if name in self._harness else None}

    def add(self, name):
        self._names.append(name)


def finish(summary='done', tool='finish'):
    return {'tool': tool, 'params': {'summary': summary}}


def make_runner(behaviour):
    """behaviour(agent, path) -> trace. Wrapped into the runner signature."""
    def runner(prompt, agent, model, steps, free, path, provider=None):
        return behaviour(agent, path, prompt), {'cost': 0.001, 'model': 'free/model',
                                                'provider': provider}
    return runner


@pytest.fixture
def arena(tmpdir):
    """An arena with a scripted field: alpha nails it, beta talks, gamma errors."""
    def behaviour(agent, path, prompt):
        if agent == 'alpha':
            Path(path, 'count.txt').write_text('7')
            Path(path, 'port.txt').write_text('8412')
            Path(path, 'summary.txt').write_text('a ledger daemon')
            return [{'tool': 'read', 'params': {'file_path': 'notes.txt'}, 'result': 'ok'},
                    finish('3 python files, gamma.txt, relay.internal')]
        if agent == 'beta':
            return [finish('probably four')]
        return [{'tool': 'bash', 'params': {'command': 'ls'}, 'error': 'boom'}]

    a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
              root=os.path.join(tmpdir, 'arena'))
    a.set_config(tasks_per_round=2, steps=6)
    return a


# ═══════════════════════════════════════════════════════════════════════
#  TASK POOL
# ═══════════════════════════════════════════════════════════════════════

class TestTasks:

    def test_pool_flattens_every_suite(self, arena):
        keys = [t['key'] for t in arena.tasks()]
        assert 'agentic/files#0' in keys
        assert 'code/python#0' in keys
        assert len(keys) == len(set(keys))

    def test_eval_checks_become_contains_scorers(self, arena):
        task = arena.task('code/python#0')
        assert all(s['type'] == 'contains' for s in task['scorers'])
        assert {s['text'] for s in task['scorers']} >= {'Fizz', 'Buzz'}

    def test_agentic_tasks_keep_their_scorer_specs(self, arena):
        task = arena.task('agentic/files#0')
        assert {s['type'] for s in task['scorers']} == {'file_exists', 'file_regex'}
        assert task['setup']['files']['notes.txt'].count('\n') == 7

    def test_unknown_task_raises(self, arena):
        with pytest.raises(KeyError):
            arena.task('nope/nope#9')

    def test_round_tasks_rotate_by_season(self, arena):
        first = [t['key'] for t in arena.round_tasks()]
        arena._state['season'] = 1
        second = [t['key'] for t in arena.round_tasks()]
        assert first != second
        assert len(first) == len(second) == 2

    def test_suites_filter_narrows_the_pool(self, arena):
        arena.set_config(suites=['agentic/tools'])
        assert {t['suite'] for t in arena.tasks()} == {'agentic/tools'}


# ═══════════════════════════════════════════════════════════════════════
#  SUBJECTS
# ═══════════════════════════════════════════════════════════════════════

class TestSubjects:

    def test_field_is_the_registry(self, arena):
        assert arena.subjects() == ['alpha', 'beta', 'gamma']

    def test_harness_agents_sit_out_by_default(self, tmpdir):
        a = Arena(agents=FakeAgents(names=('alpha', 'cli'), harness=('cli',)),
                  root=os.path.join(tmpdir, 'arena'))
        assert a.subjects() == ['alpha']
        a.set_config(harnesses=True)
        assert a.subjects() == ['alpha', 'cli']

    def test_agents_filter_pins_the_field(self, arena):
        arena.set_config(agents=['alpha', 'beta'])
        assert arena.subjects() == ['alpha', 'beta']

    def test_newcomers_are_the_unseen(self, arena):
        assert arena.newcomers() == ['alpha', 'beta', 'gamma']
        arena._mark_seen('alpha')
        assert arena.newcomers() == ['beta', 'gamma']


# ═══════════════════════════════════════════════════════════════════════
#  SCORING
# ═══════════════════════════════════════════════════════════════════════

class TestScoring:

    def test_a_perfect_trace_scores_high(self, arena, tmpdir):
        task = arena.task('agentic/files#0')
        wd = Path(tmpdir, 'wd')
        wd.mkdir()
        (wd / 'count.txt').write_text('7')
        out = arena.score([finish('wrote it')], task, wd, limit=6)
        assert out['passed'] is True
        assert out['correct'] == 1.0
        assert out['reliable'] == 1.0
        assert out['score'] > 0.9

    def test_missing_artifact_fails_correctness_only(self, arena, tmpdir):
        task = arena.task('agentic/files#0')
        out = arena.score([finish('all done')], task, Path(tmpdir), limit=6)
        assert out['correct'] == 0.0
        assert out['reliable'] == 1.0       # it still finished, cleanly
        assert 0 < out['score'] < 0.35

    def test_an_errored_step_costs_reliability(self, arena, tmpdir):
        task = arena.task('agentic/files#0')
        trace = [{'tool': 'bash', 'error': 'boom'}, finish('gave up')]
        out = arena.score(trace, task, Path(tmpdir), limit=6)
        assert out['reliable'] == 0.5

    def test_not_finishing_zeroes_efficiency(self, arena, tmpdir):
        task = arena.task('agentic/files#0')
        out = arena.score([{'tool': 'read', 'result': 'x'}], task, Path(tmpdir), limit=6)
        assert out['efficient'] == 0.0
        assert out['reliable'] == 0.5

    def test_efficiency_rewards_unspent_budget(self, arena, tmpdir):
        task = arena.task('agentic/files#0')
        lean = arena.score([finish()], task, Path(tmpdir), limit=6)
        fat = arena.score([{'tool': 'read', 'result': 'x'}] * 5 + [finish()],
                          task, Path(tmpdir), limit=6)
        assert lean['efficient'] > fat['efficient']

    def test_file_scorers_resolve_against_the_scratch_dir(self, arena, tmpdir):
        task = arena.task('agentic/files#1')
        wd = Path(tmpdir, 'scratch')
        wd.mkdir()
        (wd / 'port.txt').write_text('8412\n')
        assert arena.score([finish()], task, wd, limit=6)['correct'] == 1.0
        # the same trace scored against an empty dir must not pass
        assert arena.score([finish()], task, Path(tmpdir, 'other'), limit=6)['correct'] == 0.0

    def test_the_finish_summary_is_part_of_the_answer(self):
        """A finish step carries no result — its summary IS what the agent said."""
        trace = [finish('the host is relay.internal')]
        assert run_scorer({'type': 'regex', 'pattern': r'relay\.internal'}, trace)['passed']

    def test_steps_of_flattens_plan_history(self):
        assert len(steps_of([[finish(), finish()], finish()])) == 3


# ═══════════════════════════════════════════════════════════════════════
#  SELF-BENCHMARKING TASKS  (the `tests` scorer, code/bench, history/sources)
# ═══════════════════════════════════════════════════════════════════════

PY_SUITE = ("def test_one():\n    from work import f\n    assert f(1) == 2\n\n"
            "def test_two():\n    from work import f\n    assert f(2) == 4\n")


class TestBenchScorer:
    """The scorer that runs the task's own tests instead of guessing at them."""

    def run(self, wd, **spec):
        return run_scorer({'type': 'tests', 'cwd': str(wd), **spec}, [finish()])

    def seed(self, tmpdir, body, suite=PY_SUITE):
        wd = Path(tmpdir, 'wd')
        wd.mkdir(exist_ok=True)
        (wd / 'work.py').write_text(body)
        if suite is not None:
            (wd / 'test_work.py').write_text(suite)
        return wd

    def test_a_passing_suite_scores_one(self, tmpdir):
        wd = self.seed(tmpdir, 'def f(x):\n    return x * 2\n')
        out = self.run(wd, cmd='python -m pytest -q')
        assert out['passed'] is True
        assert out['score'] == 1.0

    def test_a_half_right_program_gets_half_the_marks(self, tmpdir):
        # f(1) == 2 passes, f(2) == 4 does not: a near miss is not a blank page
        wd = self.seed(tmpdir, 'def f(x):\n    return x + 1\n')
        out = self.run(wd, cmd='python -m pytest -q')
        assert out['passed'] is False
        assert out['score'] == 0.5

    def test_nothing_written_scores_zero(self, tmpdir):
        wd = Path(tmpdir, 'empty')
        wd.mkdir()
        assert self.run(wd, cmd='python -m pytest -q')['score'] == 0.0

    def test_the_hidden_suite_is_the_one_that_grades(self, tmpdir):
        """The tests an agent can read are not the tests it is graded on."""
        wd = self.seed(tmpdir, 'def f(x):\n    return x * 2\n')
        hidden = PY_SUITE + "\n\ndef test_three():\n    from work import f\n    assert f(3) == 99\n"
        out = self.run(wd, cmd='python -m pytest -q', hidden={'test_work.py': hidden})
        assert out['score'] == pytest.approx(2 / 3)

    def test_deleting_the_visible_suite_does_not_help(self, tmpdir):
        """The graded copy is overlaid after the run, so tampering buys nothing."""
        wd = self.seed(tmpdir, 'def f(x):\n    return x + 1\n', suite=None)
        (wd / 'test_work.py').write_text('def test_nothing():\n    assert True\n')
        out = self.run(wd, cmd='python -m pytest -q', hidden={'test_work.py': PY_SUITE})
        assert out['score'] == 0.5

    def test_the_scratch_dir_is_never_written_to(self, tmpdir):
        """Grading runs on a copy — a test that writes leaves the match alone."""
        wd = self.seed(tmpdir, 'def f(x):\n    return x * 2\n')
        out = self.run(wd, cmd='python -m pytest -q', hidden={
            'test_work.py': PY_SUITE + "\n\ndef test_writes():\n"
                                       "    open('grader-was-here', 'w').write('x')\n"})
        assert out['score'] == 1.0
        assert not (wd / 'grader-was-here').exists()

    def test_a_missing_runner_voids_the_match(self, tmpdir):
        """A tool this host does not have graded nobody — that is not a loss."""
        wd = self.seed(tmpdir, 'def f(x):\n    return x * 2\n')
        out = self.run(wd, cmd='cargo-nextest-that-is-not-installed run')
        assert out['void'] is True
        assert out['score'] == 0.0

    def test_a_hanging_program_is_the_agents_own_fault(self, tmpdir):
        """A timeout is a real result — an infinite loop is not the judge's doing."""
        wd = self.seed(tmpdir, 'while True:\n    pass\n', suite=None)
        out = self.run(wd, cmd='python work.py', timeout=2)
        assert out['score'] == 0.0
        assert 'void' not in out
        assert 'timed out' in out['reason']

    def test_a_verifier_scores_its_own_pass_lines(self, tmpdir):
        """The historian's shape: a program that prints PASS/FAIL per case."""
        wd = Path(tmpdir, 'hist')
        wd.mkdir()
        (wd / 'answer.txt').write_text('1783-04-14')
        out = self.run(wd, cmd='python verify.py', hidden={'verify.py': (
            "from pathlib import Path\n"
            "t = Path('answer.txt').read_text()\n"
            "print('PASS dated' if '1783' in t else 'FAIL dated')\n"
            "print('FAIL cited')\n")})
        assert out['score'] == 0.5

    def test_python_means_this_python(self, tmpdir):
        """`python` is not on PATH on plenty of hosts; the runner resolves it."""
        wd = Path(tmpdir, 'p')
        wd.mkdir()
        assert self.run(wd, cmd='python -c "print(1)"')['score'] == 1.0


class TestBenchTasks:
    """The two suites written against that scorer, played end to end."""

    def test_both_suites_are_in_the_pool(self, arena):
        keys = [t['key'] for t in arena.tasks()]
        assert 'code/bench#0' in keys
        assert 'history/sources#0' in keys

    def test_the_scratch_dir_reaches_the_tests_scorer(self, arena, tmpdir):
        """A task never names the scratch dir — the arena injects it as cwd."""
        spec = arena._resolve_spec({'type': 'tests', 'cmd': 'python -m pytest -q'},
                                   Path(tmpdir, 'match'))
        assert spec['cwd'] == str(Path(tmpdir, 'match'))

    def _play(self, arena, key, files, tmpdir):
        task = arena.task(key)
        wd = Path(tmpdir, key.replace('/', '_').replace('#', '_'))
        wd.mkdir(parents=True, exist_ok=True)
        for name, body in (task.get('setup', {}).get('files') or {}).items():
            (wd / name).write_text(body)
        for name, body in files.items():
            (wd / name).write_text(body)
        return arena.score([finish('done')], task, wd, limit=task.get('steps') or 12)

    def test_a_solved_coding_task_scores_full_correctness(self, arena, tmpdir):
        solved = self._play(arena, 'code/bench#2', {'roman.py': (
            'VALS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"),\n'
            '        (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),\n'
            '        (5, "V"), (4, "IV"), (1, "I")]\n\n\n'
            'def to_roman(n):\n'
            '    if not isinstance(n, int) or n < 1 or n > 3999:\n'
            '        raise ValueError("out of range")\n'
            '    out = ""\n'
            '    for v, s in VALS:\n'
            '        while n >= v:\n'
            '            out += s\n'
            '            n -= v\n'
            '    return out\n')}, tmpdir)
        assert solved['correct'] == 1.0
        assert solved['passed'] is True

    def test_saying_it_is_done_scores_nothing(self, arena, tmpdir):
        """The whole point: the answer is run, not read."""
        assert self._play(arena, 'code/bench#2', {}, tmpdir)['correct'] == 0.0

    def test_a_no_op_fails_every_bench_task(self, arena, tmpdir):
        """A fixture handed straight back is never worth full marks here."""
        for i in range(4):
            for suite in ('code/bench', 'history/sources'):
                out = self._play(arena, f'{suite}#{i}', {}, tmpdir)
                assert out['correct'] < 0.6, f'{suite}#{i} pays a no-op {out["correct"]}'

    def test_the_historian_is_graded_on_the_documents(self, arena, tmpdir):
        right = self._play(arena, 'history/sources#2', {'answer.md':
            'Josiah Crane, master of the brig Nettle. Source: ships_log.txt\n'}, tmpdir)
        invented = self._play(arena, 'history/sources#2', {'answer.md':
            'Elias Weyland was the master of the brig Nettle.\n'}, tmpdir)
        assert right['correct'] == 1.0
        assert invented['correct'] < 0.5

    def test_an_honest_unknown_beats_a_confident_guess(self, arena, tmpdir):
        honest = self._play(arena, 'history/sources#3', {'answer.txt':
            "The sources do not say. The ledger leaves the mill's 'paid by' column "
            "empty and Anne Hale writes that nobody knows who is to pay.\n"}, tmpdir)
        guess = self._play(arena, 'history/sources#3', {'answer.txt':
            'The mill was paid for by Elias Weyland.\n'}, tmpdir)
        assert honest['correct'] == 1.0
        assert guess['correct'] < honest['correct']

    def test_a_task_written_in_the_console_cannot_run_commands(self, arena):
        """A shipped suite is written by the host; a console task is not."""
        with pytest.raises(ValueError, match='shipped suite'):
            arena.validate_task({'title': 'sneak', 'prompt': 'do the thing please',
                                 'scorers': [{'type': 'tests'}]})


# ═══════════════════════════════════════════════════════════════════════
#  MATCHES
# ═══════════════════════════════════════════════════════════════════════

class TestMatches:

    def test_a_match_is_scored_and_recorded(self, arena):
        m = arena.run_match('alpha', 'agentic/files#0')
        assert m['agent'] == 'alpha' and m['task'] == 'agentic/files#0'
        assert m['score'] > 0.9 and m['passed']
        assert arena.matches(limit=5)[0]['id'] == m['id']

    def test_the_fixture_is_seeded_for_every_agent(self, tmpdir):
        seen = {}

        def behaviour(agent, path, prompt):
            seen[agent] = Path(path, 'notes.txt').read_text()
            return [finish()]

        a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
                  root=os.path.join(tmpdir, 'arena'))
        a.run_match('alpha', 'agentic/files#0')
        a.run_match('beta', 'agentic/files#0')
        assert seen['alpha'] == seen['beta']
        assert seen['alpha'].count('\n') == 7

    def test_workdir_is_spelled_out_in_the_prompt(self, tmpdir):
        prompts = []

        def behaviour(agent, path, prompt):
            prompts.append(prompt)
            return [finish()]

        a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
                  root=os.path.join(tmpdir, 'arena'))
        a.run_match('alpha', 'agentic/files#0')
        assert '{workdir}' not in prompts[0]
        assert str(Path(tmpdir, 'arena', 'work')) in prompts[0]

    def test_the_scratch_dir_is_cleaned_up(self, arena):
        arena.run_match('alpha', 'agentic/files#0')
        assert list(arena.work.iterdir()) == []

    def test_a_runner_blowup_voids_the_match_instead_of_crashing(self, tmpdir):
        def boom(**kwargs):
            raise RuntimeError('no api key')

        a = Arena(runner=boom, agents=FakeAgents(), root=os.path.join(tmpdir, 'arena'))
        a.set_config(retries=0)
        m = a.run_match('alpha', 'agentic/files#0')
        assert m['void'] and m['score'] == 0.0
        assert 'no api key' in m['error']
        assert a._rating('alpha')['matches'] == 0     # it never competed
        assert a._rating('alpha')['voids'] == 1
        assert a.matches()[0]['id'] == m['id']        # but it is on the record

    def test_no_runner_is_reported_per_match(self, tmpdir):
        a = Arena(agents=FakeAgents(), root=os.path.join(tmpdir, 'arena'))
        a.set_config(retries=0)
        assert 'runner' in a.run_match('alpha', 'agentic/files#0')['error']

    def test_match_stats_land_on_the_rating(self, arena):
        arena.run_match('alpha', 'agentic/files#0')
        arena.run_match('alpha', 'agentic/files#1')
        r = arena._rating('alpha')
        assert r['matches'] == 2
        assert set(r['per_task']) == {'agentic/files#0', 'agentic/files#1'}
        assert r['cost_sum'] == pytest.approx(0.002)

    def test_matches_filter_by_agent_and_task(self, arena):
        arena.run_match('alpha', 'agentic/files#0')
        arena.run_match('beta', 'agentic/files#0')
        assert [m['agent'] for m in arena.matches(agent='beta')] == ['beta']
        assert len(arena.matches(task='agentic/files')) == 2   # suite name works too


class TestVoids:
    """A rate-limited free endpoint is not an agent that can't code."""

    def _flaky(self, tmpdir, fail_first=1):
        state = {'n': 0}

        def behaviour(agent, path, prompt):
            state['n'] += 1
            if state['n'] <= fail_first:
                return [{'tool': 'error', 'params': {},
                         'error': 'Upstream error: ResourceExhausted'}]
            Path(path, 'count.txt').write_text('7')
            return [finish('wrote it')]

        a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
                  root=os.path.join(tmpdir, 'arena'))
        return a, state

    def _closed_door(self, tmpdir, reason):
        calls = {'n': 0}

        def behaviour(agent, path, prompt):
            calls['n'] += 1
            return [{'tool': 'error', 'params': {}, 'error': reason}]

        a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
                  root=os.path.join(tmpdir, 'arena'))
        a.set_config(tasks_per_round=2, retries=2)
        return a, calls

    def test_a_rate_limit_ends_the_round_and_cools_the_board(self, tmpdir):
        a, calls = self._closed_door(
            tmpdir, "rate limited: openrouter's free-model quota for today is "
                    "used up — resets 2099-01-01T00:00:00Z")
        out = a.run_round(reason='daily')
        assert out['matches'] == 1 and out['capped_by'] == 'rate_limited'
        assert calls['n'] == 1                  # no replay into a closed door
        assert out['results'][0]['void']
        assert not a.due()
        st = a.status()
        assert st['cooldown_until'] > time.time() + 365 * 86400   # the named reset
        assert 'quota' in st['cooldown_reason']
        # the scheduler sits it out too — qualifiers included
        tick = Scheduler(a).tick()
        assert tick['skipped'] == 'rate_limited'
        assert a.newcomers()                    # nobody was played meanwhile

    def test_a_raw_429_cools_for_an_hour_when_no_reset_is_named(self, tmpdir):
        a, calls = self._closed_door(tmpdir, 'Error code: 429 - rate limit exceeded')
        a.run_round()
        assert calls['n'] == 1
        assert 0 < a.status()['cooldown_until'] - time.time() <= 3600
        # …and the door reopens on its own
        a._state['cooldown_until'] = time.time() - 1
        a._state['last_round'] = 0
        assert a.due()

    def test_a_flaky_endpoint_is_still_retried(self, tmpdir):
        a, state = self._flaky(tmpdir, fail_first=1)
        out = a.run_match('alpha', 'agentic/files#0')
        assert not out['void'] and state['n'] == 2

    def test_a_model_error_step_voids_the_match(self, tmpdir):
        a, _ = self._flaky(tmpdir, fail_first=99)
        a.set_config(retries=0)
        m = a.run_match('alpha', 'agentic/files#0')
        assert m['void'] and 'ResourceExhausted' in m['void_reason']
        assert a._rating('alpha')['matches'] == 0

    def test_a_voided_match_is_replayed(self, tmpdir, monkeypatch):
        monkeypatch.setattr('src.arena.mod.RETRY_PAUSE', 0)
        a, state = self._flaky(tmpdir, fail_first=1)
        m = a.run_match('alpha', 'agentic/files#0')
        assert state['n'] == 2 and m['attempt'] == 1
        assert not m['void'] and m['score'] > 0.9
        assert a._rating('alpha')['matches'] == 1

    def test_a_tool_error_still_counts_against_the_agent(self, tmpdir):
        def behaviour(agent, path, prompt):
            return [{'tool': 'bash', 'params': {}, 'error': 'command not found'},
                    finish('gave up')]

        a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
                  root=os.path.join(tmpdir, 'arena'))
        m = a.run_match('alpha', 'agentic/files#0')
        assert not m['void']                 # the model answered; the tool failed
        assert m['reliable'] == 0.5
        assert a._rating('alpha')['matches'] == 1

    def test_a_void_is_left_out_of_the_rating(self, tmpdir, monkeypatch):
        monkeypatch.setattr('src.arena.mod.RETRY_PAUSE', 0)

        def behaviour(agent, path, prompt):
            if agent == 'gamma':
                return [{'tool': 'error', 'params': {}, 'error': 'rate limited'}]
            Path(path, 'count.txt').write_text('7')
            return [finish('done')]

        a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
                  root=os.path.join(tmpdir, 'arena'))
        a.set_config(tasks_per_round=1)
        a.run_round()
        assert a._rating('gamma')['elo'] == ELO_START
        assert a._rating('gamma')['voids'] == 1
        assert {r['agent'] for r in a.leaderboard() if r['matches']} == {'alpha', 'beta'}


# ═══════════════════════════════════════════════════════════════════════
#  RATING
# ═══════════════════════════════════════════════════════════════════════

class TestRating:

    def test_a_win_moves_both_ratings_the_same_distance(self, arena):
        arena._rate('t', [('alpha', 0.9), ('beta', 0.2)])
        a, b = arena._rating('alpha'), arena._rating('beta')
        assert a['elo'] > ELO_START > b['elo']
        assert round(a['elo'] - ELO_START, 6) == round(ELO_START - b['elo'], 6)
        assert (a['wins'], b['losses']) == (1, 1)

    def test_near_identical_scores_are_a_draw(self, arena):
        arena._rate('t', [('alpha', 0.900), ('beta', 0.895)])
        assert arena._rating('alpha')['elo'] == ELO_START
        assert arena._rating('alpha')['draws'] == 1

    def test_one_entry_cannot_be_rated(self, arena):
        arena._rate('t', [('alpha', 1.0)])
        assert arena._rating('alpha')['elo'] == ELO_START
        assert arena._rating('alpha')['matches'] == 0

    def test_beating_a_stronger_agent_pays_more(self, arena):
        arena._state['ratings']['beta'] = dict(arena._rating('beta'), elo=1600.0)
        arena._rate('t', [('alpha', 0.9), ('beta', 0.1)])
        underdog_gain = arena._rating('alpha')['elo'] - ELO_START
        arena._state['ratings'] = {}
        arena._rate('t', [('alpha', 0.9), ('beta', 0.1)])
        even_gain = arena._rating('alpha')['elo'] - ELO_START
        assert underdog_gain > even_gain

    def test_the_board_ranks_by_elo(self, arena):
        arena.run_round()
        board = arena.leaderboard()
        assert [r['agent'] for r in board][0] == 'alpha'
        assert board[0]['rank'] == 1
        assert board[0]['elo'] >= board[-1]['elo']

    def test_a_deleted_agent_keeps_its_record_but_reads_retired(self, arena):
        arena.run_round()
        arena.agents._names.remove('gamma')
        row = {r['agent']: r for r in arena.leaderboard()}['gamma']
        assert row['active'] is False and row['matches'] > 0

    def test_card_carries_per_task_scores(self, arena):
        arena.run_round()
        card = arena.card('alpha')
        assert card['per_task'] and card['matches_log']
        assert all(0 <= t['last'] <= 1 for t in card['per_task'])


# ═══════════════════════════════════════════════════════════════════════
#  ROUNDS
# ═══════════════════════════════════════════════════════════════════════

class TestRounds:

    def test_a_round_is_everyone_x_the_rotation(self, arena):
        out = arena.run_round(reason='test')
        assert out['matches'] == 3 * 2          # 3 agents, 2 tasks
        assert out['reason'] == 'test'
        assert arena._state['season'] == 1

    def test_the_match_cap_holds(self, arena):
        arena.set_config(tasks_per_round=4, max_matches=5)
        out = arena.run_round()
        assert out['matches'] == 5 and out['capped']

    def test_an_eval_can_name_its_subjects(self, arena):
        # code/python lists the shipped personas — none of this test's field
        out = arena.run_round(tasks=['code/python#0'])
        assert out['matches'] == 0

    def test_a_round_can_be_narrowed_to_one_agent(self, arena):
        out = arena.run_round(agents=['alpha'], tasks=['agentic/files#0'])
        assert out['matches'] == 1
        assert out['results'][0]['agent'] == 'alpha'

    def test_a_second_round_cannot_start_mid_round(self, arena):
        arena._lock.acquire()
        try:
            assert 'already running' in arena.run_round()['error']
        finally:
            arena._lock.release()

    def test_rounds_are_logged_and_bounded(self, arena):
        for _ in range(3):
            arena.run_round()
        assert len(arena._state['rounds']) == 3
        assert arena._state['rounds'][-1]['season'] == 3

    def test_a_task_without_an_eval_suite_still_plays(self, arena, monkeypatch):
        # a hand-written task has no evals/custom/mod.py behind it — looking
        # one up used to raise out of the middle of the round
        arena.set_config(tasks_per_round=1)
        pool = arena.round_tasks()
        stray = dict(pool[0], key='custom#stray', suite='custom', custom=True)
        monkeypatch.setattr(arena, 'round_tasks', lambda n=None: pool + [stray])
        out = arena.run_round()
        assert out['matches'] == 3 * 2          # 3 agents x (1 eval task + the stray)
        assert not arena.due()

    def test_a_round_that_falls_over_is_still_stamped(self, arena, monkeypatch):
        # unstamped, the scheduler saw it as due on every tick and replayed
        # its first matches every minute — the free tier was gone by morning
        def boom(*a, **k):
            raise RuntimeError('eval not found: custom')
        monkeypatch.setattr(arena, 'run_match', boom)
        with pytest.raises(RuntimeError):
            arena.run_round(reason='daily')
        assert not arena.due()
        last = arena._state['rounds'][-1]
        assert last['error'].startswith('eval not found') and last['matches'] == 0
        assert not arena._lock.locked()


# ═══════════════════════════════════════════════════════════════════════
#  INCREMENTAL ROUNDS (unchanged task + known agent = no replay)
# ═══════════════════════════════════════════════════════════════════════

class TestIncrementalRounds:

    TASKS = ['agentic/files#0', 'agentic/files#1']

    def test_an_unchanged_round_plays_nothing(self, arena):
        first = arena.run_round(tasks=self.TASKS)
        assert first['matches'] == 6 and first['skipped'] == 0
        again = arena.run_round(tasks=self.TASKS)
        assert again['matches'] == 0 and again['skipped'] == 6

    def test_a_newcomer_plays_alone_against_standing_scores(self, arena):
        arena.run_round(tasks=self.TASKS)
        arena.agents.add('delta')
        out = arena.run_round(tasks=self.TASKS)
        assert out['matches'] == 2               # delta x 2 tasks, nobody else
        assert out['skipped'] == 6
        # rated against the field's standing scores, not into a vacuum
        assert arena._rating('delta')['wins'] + arena._rating('delta')['losses'] > 0

    def test_an_edited_task_replays_everyone(self, arena):
        spec = {'title': 'edit me', 'prompt': 'write the answer into out.txt',
                'scorers': [{'type': 'finished'}]}
        saved = arena.add_task(spec, owner='0xabc')
        key = saved['key']
        assert arena.run_round(tasks=[key])['matches'] == 3
        assert arena.run_round(tasks=[key])['matches'] == 0
        arena.add_task(dict(spec, prompt='write the other answer into out.txt'),
                       slug=saved['index'])
        replay = arena.run_round(tasks=[key])
        assert replay['matches'] == 3 and replay['skipped'] == 0

    def test_force_and_the_replay_knob_play_the_whole_field(self, arena):
        arena.run_round(tasks=self.TASKS)
        assert arena.run_round(tasks=self.TASKS, force=True)['matches'] == 6
        arena.set_config(replay=True)
        assert arena.run_round(tasks=self.TASKS)['matches'] == 6

    def test_a_voided_pair_replays_next_round(self, arena):
        arena.run_round(tasks=self.TASKS)
        # gamma's runs error out, but an error step is a played match, not a
        # void — fake a void by wiping its record for one task
        del arena._rating('gamma')['per_task'][self.TASKS[0]]
        out = arena.run_round(tasks=self.TASKS)
        assert out['matches'] == 1 and out['results'][0]['agent'] == 'gamma'

    def test_the_task_board_names_the_leader(self, arena):
        arena.run_round(tasks=self.TASKS)
        rows = {r['task']: r for r in arena.task_board()}
        row = rows[self.TASKS[0]]
        # alpha is the scripted winner, and the ranking is best-last first
        assert row['leader'] == 'alpha'
        assert [a['agent'] for a in row['agents']][0] == 'alpha'
        assert len(row['agents']) == 3
        # pool tasks nobody has played are still listed, flagged as such
        assert any(r.get('unplayed') for r in rows.values())


# ═══════════════════════════════════════════════════════════════════════
#  QUALIFIERS
# ═══════════════════════════════════════════════════════════════════════

class TestQualifier:

    def test_a_newcomer_is_rated_against_the_incumbents(self, arena):
        arena.run_round()
        arena.agents.add('delta')
        before = {r['agent']: r['elo'] for r in arena.leaderboard()}
        out = arena.qualify('delta')
        assert out['qualified'] and out['matches'] == 2
        assert arena._rating('delta')['elo'] != ELO_START
        # the incumbents' standing moves too — it is one comparison, both ways
        assert any(r['elo'] != before[r['agent']]
                   for r in arena.leaderboard() if r['agent'] in before)

    def test_the_qualifier_plays_where_the_records_are(self, arena):
        arena.run_round()                       # season 0 tasks
        played = [t['key'] for t in arena.round_tasks()]   # season 1 tasks: different
        arena.agents.add('delta')
        out = arena.qualify('delta')
        assert out['tasks'] != played
        assert set(out['tasks']) <= set(arena._rating('alpha')['per_task'])

    def test_an_empty_board_falls_back_to_the_rotation(self, arena):
        out = arena.qualify('alpha')
        assert out['tasks'] == [t['key'] for t in arena.round_tasks()]

    def test_qualifying_marks_the_agent_seen(self, arena):
        arena.qualify('alpha')
        assert 'alpha' not in arena.newcomers()


# ═══════════════════════════════════════════════════════════════════════
#  SCHEDULER
# ═══════════════════════════════════════════════════════════════════════

class TestScheduler:

    def test_a_tick_qualifies_whoever_came_online(self, arena):
        arena._state['seen'] = ['alpha', 'beta', 'gamma']
        arena._state['last_round'] = time.time()     # not due
        arena.agents.add('delta')
        out = Scheduler(arena).tick()
        assert out['actions'] == 1
        assert out['results'][0]['agent'] == 'delta'
        assert 'delta' not in arena.newcomers()

    def test_a_fresh_board_seeds_with_one_round_not_a_qualifier_each(self, arena):
        out = Scheduler(arena).tick()
        assert out['actions'] == 1                    # the round, no qualifiers
        assert out['results'][0]['reason'] == 'daily'
        assert arena.newcomers() == []                # the round marked them seen

    def test_a_tick_runs_the_round_when_the_period_has_passed(self, arena):
        arena._state['seen'] = arena.subjects()
        arena._state['last_round'] = time.time() - 25 * 3600
        out = Scheduler(arena).tick()
        assert out['results'][-1]['reason'] == 'daily'
        assert not arena.due()

    def test_a_tick_inside_the_period_does_nothing(self, arena):
        arena._state['seen'] = arena.subjects()
        arena._state['last_round'] = time.time()
        assert Scheduler(arena).tick()['actions'] == 0

    def test_the_period_is_configurable(self, arena):
        arena.set_config(period_hours=1)
        arena._state['last_round'] = time.time() - 2 * 3600
        assert arena.due()

    def test_a_disabled_board_never_runs(self, arena):
        arena.set_config(enabled=False)
        arena.agents.add('delta')
        assert Scheduler(arena).tick() == {'skipped': 'disabled'}
        assert arena.matches() == []

    def test_start_is_idempotent(self, arena):
        s = Scheduler(arena)
        try:
            first = s.start(delay=999)
            again = s.start(delay=999)
            assert first['alive'] and again['alive']
            assert s.ticks == 0                  # the delay hasn't elapsed
        finally:
            s.stop()

    def test_status_reports_the_thread(self, arena):
        s = Scheduler(arena)
        assert s.status()['alive'] is False
        assert arena.status()['scheduler']['alive'] is False


# ═══════════════════════════════════════════════════════════════════════
#  THE RUNNER (module side)
# ═══════════════════════════════════════════════════════════════════════

class TestRunner:
    """Mod.arena_run — what the arena is actually handed for each match."""

    def _mod(self):
        from src.mod import Mod
        mod = Mod.__new__(Mod)          # no __init__: only arena_run is exercised
        mod.meter = type('M', (), {'take': lambda self: {'cost': 0.5}})()
        return mod

    def test_every_executed_step_is_scored_not_just_the_last_plan(self):
        """run() returns history[-1] — the write two plans back still counts."""
        mod = self._mod()
        mod._run = lambda **kw: ([kw['on_step']({'tool': 'write'}),
                                  kw['on_step']({'tool': 'finish'})]
                                 and [{'tool': 'finish'}])
        trace, usage = mod.arena_run('do it', 'alpha', path='/tmp')
        assert [s['tool'] for s in trace] == ['write', 'finish']
        assert usage['cost'] == 0.5

    def test_a_match_is_sandboxed_to_its_scratch_dir(self):
        mod = self._mod()
        seen = {}

        def fake_run(**kw):
            seen.update(kw)
            return []

        mod._run = fake_run
        mod.arena_run('do it', 'alpha', path='/tmp/scratch', steps=4)
        assert seen['allowed_paths'] == ['/tmp/scratch']
        assert seen['key'] is None          # no caller, so nobody is billed
        assert seen['steps'] == 4 and seen['agent_type'] == 'alpha'

    def test_the_meter_is_read_even_when_the_run_blows_up(self):
        mod = self._mod()
        taken = []
        mod.meter = type('M', (), {'take': lambda self: taken.append(1) or {}})()

        def boom(**kw):
            raise RuntimeError('no key')

        mod._run = boom
        with pytest.raises(RuntimeError):
            mod.arena_run('do it', 'alpha')
        assert taken == [1]                 # an unread tally lands on the next run


# ═══════════════════════════════════════════════════════════════════════
#  PROTOCOL + STATE
# ═══════════════════════════════════════════════════════════════════════

class TestForward:

    def test_bare_forward_is_the_board(self, arena):
        out = arena.forward()
        assert 'leaderboard' in out and 'status' in out

    def test_tasks_and_matches(self, arena):
        arena.run_match('alpha', 'agentic/files#0')
        assert arena.forward('tasks')['tasks']
        assert arena.forward('matches', limit=1)['matches'][0]['agent'] == 'alpha'

    def test_run_one_match_vs_a_round(self, arena):
        one = arena.forward('run', agent='alpha', task='agentic/files#0')
        assert one['agent'] == 'alpha'
        # alpha already holds a score on that unchanged task, so the round
        # skips the pair rather than replaying it…
        rnd = arena.forward('run')
        assert rnd['matches'] == 5
        assert rnd['skipped'] == 1
        # …and force plays the whole field regardless
        assert arena.forward('run', force=True)['matches'] == 6

    def test_config_round_trips_to_disk(self, arena, tmpdir):
        arena.forward('config', period_hours=6, free=False)
        fresh = Arena(agents=FakeAgents(), root=arena.root)
        assert fresh.config()['period_hours'] == 6
        assert fresh.config()['free'] is False

    def test_config_ignores_unknown_knobs(self, arena):
        assert 'nonsense' not in arena.forward('config', nonsense=1)

    def test_unknown_action_raises(self, arena):
        with pytest.raises(KeyError):
            arena.forward('teleport')

    def test_state_survives_a_restart(self, arena):
        arena.run_round()
        fresh = Arena(agents=FakeAgents(), root=arena.root)
        assert fresh.leaderboard()[0]['agent'] == 'alpha'
        assert fresh._state['season'] == 1
        assert fresh.matches(limit=100)

    def test_status_answers_what_the_board_is_doing(self, arena):
        st = arena.status()
        assert st['enabled'] and st['due'] is True
        assert st['subjects'] == ['alpha', 'beta', 'gamma']
        assert len(st['round_tasks']) == 2


# ═══════════════════════════════════════════════════════════════════════
#  HAND-WRITTEN TASKS (the Builder's TASK mode)
# ═══════════════════════════════════════════════════════════════════════

def a_task(**over):
    """A valid hand-written spec — the shape the Builder posts."""
    spec = {
        'title': 'Sum the CSV prices',
        'description': 'reads a fixture, writes a total',
        'prompt': 'Your working directory is {workdir}. Sum items.csv and write '
                  'total.txt containing only that number. Then finish.',
        'steps': 6,
        'setup': {'files': {'items.csv': 'name,price\nbolt,3\nnut,4\nwasher,5\n'}},
        'scorers': [{'type': 'file_exists', 'path': 'total.txt'},
                    {'type': 'file_regex', 'path': 'total.txt', 'pattern': r'^\s*12\s*$'}],
    }
    spec.update(over)
    return spec


class TestCustomTasks:

    def test_a_saved_task_joins_the_pool(self, arena):
        saved = arena.add_task(a_task(), owner='0xabc')
        assert saved['key'] == 'custom#sum-the-csv-prices'
        pool = {t['key']: t for t in arena.tasks()}
        assert pool[saved['key']]['suite'] == 'custom'
        assert arena.task(saved['key'])['setup']['files']['items.csv'].startswith('name,price')

    def test_it_is_played_like_any_other_task(self, arena, tmpdir):
        arena.add_task(a_task(scorers=[{'type': 'finished'}]), owner='0xabc')
        match = arena.run_match('alpha', 'custom#sum-the-csv-prices')
        assert match['passed'] and match['suite'] == 'custom'

    def test_editing_keeps_the_key_and_the_author(self, arena):
        arena.add_task(a_task(), owner='0xabc')
        again = arena.add_task(a_task(steps=9), owner='0xsomebodyelse',
                               slug='sum-the-csv-prices')
        assert again['key'] == 'custom#sum-the-csv-prices'
        assert again['steps'] == 9
        # an edit does not transfer ownership — the API checks the caller first
        assert arena.get_custom('sum-the-csv-prices')['owner'] == '0xabc'

    def test_a_new_task_never_overwrites_another(self, arena):
        arena.add_task(a_task(), owner='0xabc')
        second = arena.add_task(a_task(), owner='0xdef')
        assert second['key'] == 'custom#sum-the-csv-prices-2'
        assert len(arena.custom()) == 2

    def test_removing_one_takes_it_out_of_the_pool(self, arena):
        arena.add_task(a_task(), owner='0xabc')
        arena.remove_task('sum-the-csv-prices')
        assert [t for t in arena.tasks() if t['suite'] == 'custom'] == []
        with pytest.raises(KeyError):
            arena.remove_task('sum-the-csv-prices')

    def test_they_survive_a_restart(self, arena):
        arena.add_task(a_task(), owner='0xabc')
        fresh = Arena(agents=FakeAgents(), root=arena.root)
        assert fresh.task('custom#sum-the-csv-prices')['title'] == 'Sum the CSV prices'

    def test_the_suites_filter_can_exclude_them(self, arena):
        arena.add_task(a_task(), owner='0xabc')
        arena.set_config(suites=['agentic/tools'])
        assert {t['suite'] for t in arena.tasks()} == {'agentic/tools'}

    @pytest.mark.parametrize('bad, why', [
        ({'title': ''}, 'title'),
        ({'prompt': 'too short'}, 'prompt'),
        ({'scorers': []}, 'check'),
        ({'scorers': [{'type': 'teleport'}]}, 'unknown check'),
        ({'scorers': [{'type': 'file_exists'}]}, 'path'),
        ({'scorers': [{'type': 'file_contains', 'path': 'a.txt'}]}, 'text'),
        ({'scorers': [{'type': 'file_exists', 'path': '/etc/passwd'}]}, 'relative'),
        ({'setup': {'files': {'../escape.txt': 'x'}}}, 'escapes'),
        ({'setup': {'files': {'big.txt': 'x' * 50_000}}}, 'too big'),
        ({'prompt': 'p' * 5000}, 'too long'),
    ])
    def test_a_task_that_could_not_be_graded_is_refused(self, arena, bad, why):
        with pytest.raises(ValueError, match=why):
            arena.validate_task(a_task(**bad))

    def test_the_step_budget_is_capped(self, arena):
        saved = arena.add_task(a_task(steps=500), owner='0xabc')
        assert saved['steps'] == 30

    def test_scorer_specs_are_stripped_to_known_fields(self, arena):
        arena.add_task(a_task(scorers=[
            {'type': 'file_exists', 'path': 'total.txt', 'rm': '-rf /'}]), owner='0xabc')
        stored = arena.get_custom('sum-the-csv-prices')['scorers'][0]
        assert stored == {'type': 'file_exists', 'path': 'total.txt'}

    def test_forward_exposes_the_store_and_the_check_types(self, arena):
        arena.forward('task_add', spec=a_task(), owner='0xabc')
        out = arena.forward('tasks')
        assert out['custom'][0]['slug'] == 'sum-the-csv-prices'
        assert 'file_not_contains' in out['scorers']
        assert arena.forward('task_rm', slug='sum-the-csv-prices')['remaining'] == 0


class TestArenaOptOut:

    def test_an_agent_can_stay_off_the_board(self, tmpdir):
        class Registry(FakeAgents):
            def get(self, name):
                info = super().get(name)
                return {**info, 'arena': name != 'scribe'}

        a = Arena(agents=Registry(names=('alpha', 'scribe')),
                  root=os.path.join(tmpdir, 'arena'))
        assert a.subjects() == ['alpha']


# ═══════════════════════════════════════════════════════════════════════
#  THE OPENARENA BRIDGE
# ═══════════════════════════════════════════════════════════════════════
#
# openarena is a neighbour, so every test here stubs it. What is under test is
# the translation and the policy: does its schema become an arena task, does a
# part-passing program get part marks, and is a judge that cannot be reached a
# void rather than a loss.

OA_FIZZ = {
    'id': 't1', 'slug': 'fizzbuzz', 'title': 'FizzBuzz', 'mode': 'io',
    'language': 'any', 'starter': '', 'statement': 'Print the FizzBuzz sequence.',
    'tags': ['warmup'], 'author': '0xabc', 'total_tests': 3, 'hidden_tests': 1,
    'tests': [
        {'name': 'n=5', 'stdin': '5\n', 'expect': '1\n2\nFizz\n4\nBuzz', 'hidden': False},
        {'name': 'n=15', 'stdin': '15\n', 'expect': 'Fizz', 'hidden': False},
        {'name': 'n=20', 'hidden': True},
    ],
}


@pytest.fixture
def bridged(monkeypatch, tmpdir):
    """An arena whose openarena neighbour holds one task and a scripted judge."""
    graded = []

    def fake_grade(slug, code, language='', agent=None):
        graded.append({'slug': slug, 'code': code, 'language': language})
        # two of three cases, one of them the hidden one, so partial credit and
        # `solved` disagree — which is the whole point of a fraction
        return {'submission_id': 's1', 'passed': 2, 'total': 3, 'score': 2 / 3,
                'solved': False, 'judge_ms': 12,
                'cases': [{'name': 'n=5', 'passed': True, 'hidden': False, 'ms': 4},
                          {'name': 'n=15', 'passed': True, 'hidden': False, 'ms': 4},
                          {'name': 'n=20', 'passed': False, 'hidden': True, 'ms': 4}]}

    monkeypatch.setattr(oa, 'index', lambda ttl=0: [dict(OA_FIZZ, tests=3)])
    monkeypatch.setattr(oa, 'get_task', lambda slug, cached=True: OA_FIZZ)
    monkeypatch.setattr(oa, 'grade', fake_grade)
    monkeypatch.setattr(oa, 'info', lambda: {'version': '0.2.0', 'tasks': 1,
                                             'agents': 0, 'matches': 0})
    monkeypatch.setattr(oa, 'entrants', lambda: [])

    def behaviour(agent, path, prompt):
        Path(path, 'solution.py').write_text('print("Fizz")')
        return [{'tool': 'write', 'params': {'path': 'solution.py'}, 'result': 'ok'},
                finish('wrote solution.py')]

    a = Arena(runner=make_runner(behaviour), agents=FakeAgents(),
              root=os.path.join(tmpdir, 'arena'))
    # retries=0: a void here is the assertion, not something to wait out
    a.set_config(steps=6, retries=0)
    return a, graded


class TestOpenArenaTasks:

    def test_a_task_over_there_is_a_task_here(self, bridged):
        arena, _ = bridged
        task = arena.task('openarena#fizzbuzz')
        assert task['suite'] == 'openarena'
        assert task['scorers'] == [{'type': 'openarena', 'task': 'fizzbuzz',
                                    'path': 'solution.py', 'language': 'any'}]
        # a program task is write-then-check, so it gets its own budget
        assert task['steps'] == oa.DEFAULT_STEPS

    def test_the_brief_shows_examples_but_never_a_hidden_case(self, bridged):
        arena, _ = bridged
        prompt = arena.task('openarena#fizzbuzz')['prompt']
        assert 'FizzBuzz sequence' in prompt
        assert 'solution.py' in prompt          # where to leave the program
        assert '1\n2\nFizz\n4\nBuzz' in prompt  # the visible case
        assert 'n=20' not in prompt             # the hidden one, by name even
        assert '1 of them hidden' in prompt     # but it is counted

    def test_they_join_the_pool_and_can_be_switched_off(self, bridged):
        arena, _ = bridged
        assert 'openarena#fizzbuzz' in [t['key'] for t in arena.tasks()]
        arena.set_config(openarena=False)
        assert 'openarena#fizzbuzz' not in [t['key'] for t in arena.tasks()]
        # naming one still plays it — a cap or a switch is about the rotation
        assert arena.task('openarena#fizzbuzz')['suite'] == 'openarena'

    def test_a_neighbour_that_is_down_is_an_empty_suite_not_an_exception(self, monkeypatch, tmpdir):
        # `no_neighbours` already has it down — this is the assertion that the
        # board carries on regardless
        a = Arena(agents=FakeAgents(), root=os.path.join(tmpdir, 'arena'))
        assert a.openarena_tasks() == []
        assert a.openarena_status()['available'] is False
        assert [t['key'] for t in a.tasks()]      # the rest of the pool is fine

    def test_the_pool_cap_takes_the_newest(self, monkeypatch, tmpdir):
        rows = [dict(OA_FIZZ, slug=f't{i}', created=i) for i in range(5)]
        monkeypatch.setattr(oa, 'index', lambda ttl=0: rows)
        monkeypatch.setattr(oa, 'get_task', lambda slug, cached=True: dict(OA_FIZZ, slug=slug))
        a = Arena(agents=FakeAgents(), root=os.path.join(tmpdir, 'arena'))
        a.set_config(openarena_tasks=2)
        assert [t['key'] for t in a.openarena_tasks()] == ['openarena#t4', 'openarena#t3']


class TestOpenArenaScoring:

    def test_part_of_the_cases_is_part_of_the_marks(self, bridged):
        arena, graded = bridged
        m = arena.run_match('alpha', 'openarena#fizzbuzz')
        assert not m['void']
        # 2 of 3 cases, weighted — not a pass, and not a zero either
        assert m['correct'] == pytest.approx(2 / 3, abs=1e-3)
        assert m['passed'] is False
        assert 0 < m['score'] < 1
        # the program that was graded is the file the agent wrote
        assert graded[0] == {'slug': 'fizzbuzz', 'code': 'print("Fizz")',
                             'language': 'python'}

    def test_the_case_list_lands_on_the_match(self, bridged):
        arena, _ = bridged
        m = arena.run_match('alpha', 'openarena#fizzbuzz')
        check = next(c for c in m['checks'] if c['type'] == 'openarena')
        assert check['score'] == pytest.approx(2 / 3, abs=1e-3)
        assert [c['name'] for c in check['cases']] == ['n=5', 'n=15', 'n=20']
        assert [c['hidden'] for c in check['cases']] == [False, False, True]

    def test_a_judge_that_is_down_voids_the_match(self, bridged, monkeypatch):
        arena, _ = bridged
        monkeypatch.setattr(oa, 'grade', lambda *a, **k: (_ for _ in ()).throw(
            oa.Unavailable('connection refused')))
        m = arena.run_match('alpha', 'openarena#fizzbuzz')
        # the agent did its part — this measured nothing, so it is not a loss
        assert m['void'] and m['score'] == 0.0
        assert 'could not grade' in m['void_reason']
        assert arena._rating('alpha')['matches'] == 0

    def test_no_program_at_all_is_a_loss_not_a_void(self, bridged, monkeypatch):
        arena, _ = bridged
        monkeypatch.setattr(oa, 'grade', lambda *a, **k: (_ for _ in ()).throw(
            AssertionError('the judge should never have been called')))

        def silent(agent, path, prompt):
            return [finish('I could not work it out')]
        arena._runner = make_runner(silent)
        m = arena.run_match('alpha', 'openarena#fizzbuzz')
        assert not m['void']
        assert m['correct'] == 0.0

    def test_a_program_shown_but_not_written_is_still_graded(self, bridged):
        arena, graded = bridged

        def in_chat(agent, path, prompt):
            return [finish('Here you go:\n```python\nprint("Buzz")\n```')]
        arena._runner = make_runner(in_chat)
        arena.run_match('alpha', 'openarena#fizzbuzz')
        assert graded[-1]['code'] == 'print("Buzz")'
        assert graded[-1]['language'] == 'python'

    def test_a_trace_scorer_still_scores_one_or_zero(self, arena, tmpdir):
        # the fraction path must not change the schema everything else uses
        out = run_scorer({'type': 'finished'}, [finish()])
        assert out['score'] == 1.0 and out['passed'] is True


class TestOpenArenaSchema:

    def test_a_valid_io_task_is_accepted(self):
        clean = oa.validate({
            'title': 'Double it', 'statement': 'Read N and print N doubled.',
            'tests': [{'name': 'a', 'stdin': '2\n', 'expect': '4'},
                      {'name': 'b', 'stdin': '9\n', 'expect': '18', 'hidden': True}]})
        assert clean['mode'] == 'io' and clean['language'] == 'any'
        assert clean['tests'][0]['compare'] == 'trim'
        assert clean['tests'][1]['hidden'] is True

    def test_a_unit_case_needs_a_grader_program(self):
        with pytest.raises(ValueError, match='program'):
            oa.validate({'title': 'Stack', 'statement': 'Define a Stack class.',
                         'mode': 'unit',
                         'tests': [{'name': 'a', 'stdin': '', 'expect': 'x'}]})

    def test_an_io_case_needs_its_expected_output(self):
        with pytest.raises(ValueError, match='expect'):
            oa.validate({'title': 'Double it', 'statement': 'Read N, print 2N.',
                         'tests': [{'name': 'a', 'stdin': '2\n'}]})

    def test_all_hidden_is_refused(self):
        # a competitor needs something to check its answer against
        with pytest.raises(ValueError, match='visible'):
            oa.validate({'title': 'Double it', 'statement': 'Read N, print 2N.',
                         'tests': [{'name': 'a', 'stdin': '2\n', 'expect': '4',
                                    'hidden': True}]})

    def test_a_task_with_no_cases_is_refused(self):
        with pytest.raises(ValueError, match='test case'):
            oa.validate({'title': 'Vibes', 'statement': 'Write something nice.',
                         'tests': []})

    def test_an_unrunnable_language_is_refused(self):
        with pytest.raises(ValueError, match='unsupported language'):
            oa.validate({'title': 'x', 'statement': 'Read N and print N.',
                         'language': 'haskell',
                         'tests': [{'name': 'a', 'stdin': '1', 'expect': '1'}]})

    def test_the_entrypoint_follows_the_language(self):
        assert oa.entrypoint('python') == 'solution.py'
        assert oa.entrypoint('javascript') == 'solution.js'
        assert oa.entrypoint('bash') == 'solution.sh'
        assert oa.entrypoint('any') == 'solution.py'

    def test_the_last_fenced_block_wins(self):
        code, lang = oa.fenced('first:\n```py\nold()\n```\nno, this:\n```python\nnew()\n```')
        assert code == 'new()' and lang == 'python'

    def test_a_fence_language_is_only_taken_if_it_can_be_run(self):
        assert oa.pick_language('rust', 'any') == 'python'
        assert oa.pick_language('', 'javascript') == 'javascript'
        assert oa.pick_language('bash', 'any') == 'bash'


class TestOpenArenaForward:

    def test_forward_reports_the_bridge(self, bridged):
        arena, _ = bridged
        out = arena.forward('openarena')
        assert out['enabled'] is True
        assert [t['slug'] for t in out['pool']] == ['fizzbuzz']

    def test_forward_writes_through_to_the_neighbour(self, bridged, monkeypatch):
        arena, _ = bridged
        seen = {}
        monkeypatch.setattr(oa, 'create_task',
                            lambda spec, author=None: seen.update(spec=spec, author=author) or {'slug': 'x'})
        arena.forward('oa_task_add', spec={
            'title': 'Double it', 'statement': 'Read N and print N doubled.',
            'tests': [{'name': 'a', 'stdin': '2\n', 'expect': '4'},
                      {'name': 'b', 'stdin': '3\n', 'expect': '6', 'hidden': True}]},
            author='0xabc')
        assert seen['author'] == '0xabc'
        assert seen['spec']['title'] == 'Double it'

    def test_bench_options_are_passed_through_and_nothing_else_is(self, bridged, monkeypatch):
        arena, _ = bridged
        seen = {}
        monkeypatch.setattr(oa, 'bench_import',
                            lambda source, **opts: seen.update(source=source, opts=opts) or {'imported': 0})
        # `key` and `author` are ours and stay here; the bench options go over
        arena.forward('oa_import', source='mbpp', limit=5, offset=20,
                      key='secret', author='0xabc')
        assert seen['source'] == 'mbpp'
        assert seen['opts'] == {'limit': 5, 'offset': 20}

    def test_the_pool_breakdown_is_on_the_status(self, bridged):
        arena, _ = bridged
        assert arena.status()['suites_count']['openarena'] == 1


# ═══════════════════════════════════════════════════════════════════════
#  THE MODEL BOARD — the same matches, keyed on what was underneath
# ═══════════════════════════════════════════════════════════════════════

def match(model, task='t#0', agent='alpha', season=0, score=0.8, seconds=10.0,
          steps=2, tokens=1000, cost=0.0, passed=True, void=False, ts=1.0):
    """One line of matches.jsonl, as the board writes it."""
    return {'id': f'{model}:{task}:{agent}:{ts}', 'ts': ts, 'season': season,
            'agent': agent, 'task': task, 'suite': task.split('#')[0],
            'title': task, 'model': model, 'seconds': seconds, 'steps': steps,
            'tokens': tokens, 'cost': cost, 'score': score, 'passed': passed,
            'void': void}


class TestModelBoard:

    def test_a_model_is_ranked_on_what_it_scored_and_what_it_burned(self):
        rows = mb.board([match('fast', score=0.9, seconds=10, steps=2, tokens=800),
                         match('fast', task='t#1', score=0.7, seconds=30, steps=4, tokens=1200)])
        assert len(rows) == 1
        row = rows[0]
        assert row['matches'] == 2
        assert row['avg_score'] == pytest.approx(0.8)
        assert row['avg_seconds'] == pytest.approx(20.0)
        # the latency that compares across tasks: 40s over 6 steps
        assert row['sec_per_step'] == pytest.approx(40 / 6, abs=0.01)
        assert row['tok_per_sec'] == pytest.approx(2000 / 40, abs=0.1)
        assert row['tasks'] == ['t#0', 't#1']

    def test_a_model_that_never_met_another_is_unrated(self):
        rows = mb.board([match('lonely'), match('lonely', task='t#1')])
        assert rows[0]['rated'] is False
        assert rows[0]['elo'] == mb.ELO_START
        assert rows[0]['h2h'] == 0

    def test_same_task_same_agent_is_a_head_to_head(self):
        rows = mb.board([match('strong', score=0.9), match('weak', score=0.2)])
        board = {r['model']: r for r in rows}
        assert board['strong']['rated'] and board['strong']['elo'] > mb.ELO_START
        assert board['weak']['elo'] < mb.ELO_START
        assert board['strong']['wins'] == 1 and board['weak']['losses'] == 1
        assert board['strong']['rank'] == 1

    def test_two_models_under_different_agents_did_not_meet(self):
        # the personas differ, so the score gap could be the agent's — this is
        # exactly the comparison the board must refuse to make
        rows = mb.board([match('strong', agent='alpha', score=0.9),
                         match('weak', agent='beta', score=0.2)])
        assert all(r['h2h'] == 0 for r in rows)
        assert all(r['elo'] == mb.ELO_START for r in rows)

    def test_scores_inside_the_draw_band_trade_nothing(self):
        rows = mb.board([match('a', score=0.80), match('b', score=0.81)])
        assert {r['elo'] for r in rows} == {mb.ELO_START}
        assert all(r['draws'] == 1 for r in rows)

    def test_a_model_playing_a_task_twice_gets_one_vote(self):
        rows = mb.board([match('a', score=0.9, ts=1), match('a', score=0.9, ts=2),
                         match('b', score=0.2, ts=3)])
        board = {r['model']: r for r in rows}
        assert board['a']['h2h'] == 1 and board['a']['matches'] == 2

    def test_voided_matches_are_counted_but_not_averaged(self):
        rows = mb.board([match('a', score=0.8),
                         match('a', task='t#1', score=0.0, void=True)])
        assert rows[0]['matches'] == 1 and rows[0]['voids'] == 1
        assert rows[0]['avg_score'] == pytest.approx(0.8)

    def test_spend_is_read_off_the_meter_not_the_model_id(self):
        rows = mb.board([match('paid:free', cost=0.02, score=0.5)])
        assert rows[0]['free'] is False
        assert rows[0]['cost_per_point'] == pytest.approx(0.04)

    def test_a_match_with_no_model_recorded_still_counts(self):
        rows = mb.board([dict(match('x'), model=None)])
        assert rows[0]['model'] == mb.UNKNOWN

    def test_the_card_carries_per_task_and_the_head_to_head_record(self):
        log = [match('a', score=0.9), match('b', score=0.2),
               match('a', task='t#1', score=0.3), match('b', task='t#1', score=0.9)]
        card = mb.card(log, 'a')
        assert {t['task'] for t in card['per_task']} == {'t#0', 't#1'}
        assert card['opponents'][0]['record'] == '1-1-0'
        assert len(card['matches_log']) == 2

    def test_the_task_board_ranks_models_inside_a_task(self):
        rows = mb.task_board([match('a', task='t#0', score=0.9),
                              match('b', task='t#0', score=0.4),
                              match('a', task='t#1', score=0.1, passed=False)])
        by_task = {r['task']: r for r in rows}
        assert by_task['t#0']['best'] == 'a'
        assert by_task['t#0']['spread'] == pytest.approx(0.5)
        # hardest first, so the task nobody scored on leads
        assert rows[0]['task'] == 't#1'
        # a task only one model played separates nobody
        assert by_task['t#1']['spread'] == 0.0

    def test_the_arena_reads_its_own_log(self, arena):
        arena.run_match('alpha', 'agentic/files#0')
        arena.run_match('beta', 'agentic/files#0')
        rows = arena.model_board()
        assert rows[0]['model'] == 'free/model' and rows[0]['matches'] == 2
        assert arena.model_card('free/model')['matches'] == 2
        assert arena.task_board()[0]['matches'] == 2
        status = arena.forward('models')
        assert status['models'][0]['model'] == 'free/model'
        assert 'agentic/files#0' in [t['key'] for t in status['tasks']]


# ═══════════════════════════════════════════════════════════════════════
#  THE GAUNTLET — one agent, one task set, N models
# ═══════════════════════════════════════════════════════════════════════

def model_runner(scores):
    """A runner whose result depends on the model it was handed."""
    def runner(prompt, agent, model, steps, free, path, provider=None):
        Path(path, 'seen.txt').write_text(f'{model}|{provider}')
        score = scores.get(model, 0.0)
        trace = [finish('done')] if score else [{'tool': 'bash', 'error': 'nope'}]
        if score:
            Path(path, 'count.txt').write_text('7')
            Path(path, 'port.txt').write_text('8412')
            Path(path, 'summary.txt').write_text('a ledger daemon')
        return trace, {'cost': 0.0, 'model': model, 'provider': provider}
    return runner


@pytest.fixture
def gauntlet_arena(tmpdir):
    a = Arena(runner=model_runner({'good': 1.0, 'bad': 0.0}), agents=FakeAgents(),
              root=os.path.join(tmpdir, 'arena'))
    a.set_config(tasks_per_round=1, steps=6, retries=0)
    return a


class TestGauntlet:

    def test_every_model_plays_every_task(self, gauntlet_arena):
        out = gauntlet_arena.run_gauntlet(['good', 'bad'], agent='alpha',
                                          tasks=['agentic/files#0', 'agentic/files#1'])
        assert out['matches'] == 4
        assert {m['model'] for m in out['results']} == {'good', 'bad'}
        assert {m['agent'] for m in out['results']} == {'alpha'}

    def test_one_model_is_not_a_comparison(self, gauntlet_arena):
        assert 'error' in gauntlet_arena.run_gauntlet(['good'], agent='alpha')

    def test_the_provider_rides_along_with_the_model_id(self, gauntlet_arena):
        out = gauntlet_arena.run_gauntlet(
            [{'model': 'good', 'provider': 'venice'}, {'model': 'bad', 'provider': 'openrouter'}],
            agent='alpha', tasks=['agentic/files#0'])
        assert {(m['model'], m['provider']) for m in out['results']} == {
            ('good', 'venice'), ('bad', 'openrouter')}

    def test_it_leaves_the_agent_board_alone(self, gauntlet_arena):
        # the agent is the constant, not the subject: a weak model in the field
        # must not move its rating or overwrite the per-task score a newcomer's
        # qualifier is measured against
        gauntlet_arena.run_match('alpha', 'agentic/files#0')
        before = json.loads(json.dumps(gauntlet_arena._rating('alpha')))
        gauntlet_arena.run_gauntlet(['good', 'bad'], agent='alpha',
                                    tasks=['agentic/files#0'])
        after = gauntlet_arena._rating('alpha')
        assert after['matches'] == before['matches']
        assert after['elo'] == before['elo']
        assert after['per_task'] == before['per_task']

    def test_the_match_cap_stops_it(self, gauntlet_arena):
        gauntlet_arena.set_config(max_matches=3)
        out = gauntlet_arena.run_gauntlet(['good', 'bad'], agent='alpha',
                                          tasks=['agentic/files#0', 'agentic/files#1'])
        assert out['matches'] == 3 and out['capped_by'] == 'max_matches'

    def test_it_produces_a_rated_model_board(self, gauntlet_arena):
        gauntlet_arena.run_gauntlet(['good', 'bad'], agent='alpha',
                                    tasks=['agentic/files#0'])
        board = {r['model']: r for r in gauntlet_arena.model_board()}
        assert board['good']['rated'] and board['good']['elo'] > board['bad']['elo']
        assert board['good']['rank'] == 1

    def test_a_gauntlet_does_not_advance_the_season(self, gauntlet_arena):
        before = gauntlet_arena._state.get('season', 0)
        gauntlet_arena.run_gauntlet(['good', 'bad'], agent='alpha',
                                    tasks=['agentic/files#0'])
        assert gauntlet_arena._state.get('season', 0) == before

    def test_forward_dispatches_it(self, gauntlet_arena):
        out = gauntlet_arena.forward('gauntlet', models=['good', 'bad'], agent='alpha',
                                     tasks=['agentic/files#0'])
        assert out['matches'] == 2 and out['agent'] == 'alpha'


# ═══════════════════════════════════════════════════════════════════════
#  TIERS — one model, every agent: what the DESIGN was worth
# ═══════════════════════════════════════════════════════════════════════

class TestTierBoard:

    def test_agents_are_rated_against_each_other_inside_one_model(self):
        out = tb.field([match('cheap', agent='alpha', score=0.9),
                        match('cheap', agent='beta', score=0.2)], 'cheap')
        board = {r['agent']: r for r in out['agents']}
        assert board['alpha']['rank'] == 1 and board['alpha']['rated']
        assert board['alpha']['elo'] > board['beta']['elo']
        assert board['alpha']['wins'] == 1 and board['beta']['losses'] == 1

    def test_two_agents_on_different_models_did_not_meet(self):
        # the same refusal the model board makes, mirrored: the gap could be
        # the model's
        out = tb.field([match('cheap', agent='alpha', score=0.9),
                        match('cheap', agent='beta', score=0.2)], 'cheap')
        other = tb.field([match('cheap', agent='alpha', score=0.9),
                          match('dear', agent='beta', score=0.2)], 'cheap')
        assert out['agents'][0]['h2h'] == 1
        assert all(r['h2h'] == 0 for r in other['agents'])

    def test_the_spread_is_what_the_design_was_worth_there(self):
        rows = tb.board([match('cheap', agent='alpha', score=0.9),
                         match('cheap', agent='beta', score=0.3)])
        assert rows[0]['model'] == 'cheap'
        assert rows[0]['spread'] == pytest.approx(0.6)
        assert rows[0]['best'] == 'alpha' and rows[0]['worst'] == 'beta'
        assert rows[0]['separates'] is True

    def test_a_tier_where_every_design_scores_the_same_ranks_nobody(self):
        # the frontier-model case: the model does the task whatever the prompt
        # says, so the board says so instead of inventing a winner
        rows = tb.board([match('dear', agent='alpha', score=0.9),
                         match('dear', agent='beta', score=0.9)])
        assert rows[0]['separates'] is False and rows[0]['spread'] == 0.0

    def test_the_widest_tier_leads_the_board(self):
        rows = tb.board([match('dear', agent='alpha', score=0.9),
                         match('dear', agent='beta', score=0.88),
                         match('cheap', agent='alpha', score=0.8),
                         match('cheap', agent='beta', score=0.1)])
        assert rows[0]['model'] == 'cheap' and rows[0]['rank'] == 1

    def test_a_model_only_one_agent_played_is_not_a_tier(self):
        assert tb.board([match('cheap', agent='alpha')]) == []

    def test_ranking_uses_only_the_tasks_the_whole_field_played(self):
        # beta drew an extra easy task; the ranking must not read that as
        # beta being better at the ones they both played
        log = [match('cheap', agent='alpha', task='t#0', score=0.4),
               match('cheap', agent='beta', task='t#0', score=0.2),
               match('cheap', agent='beta', task='t#1', score=1.0)]
        out = tb.field(log, 'cheap')
        board = {r['agent']: r for r in out['agents']}
        assert out['tasks'] == ['t#0']
        assert board['beta']['score'] == pytest.approx(0.2)
        assert board['beta']['avg_score'] == pytest.approx(0.6)
        assert board['alpha']['rank'] == 1

    def test_a_field_that_never_shared_a_task_falls_back_and_says_so(self):
        # agents drifting onto different rotations over several seasons have an
        # empty intersection; scoring everyone over it would flatten the whole
        # tier to zero, so each agent keeps its own average and the tier is
        # flagged instead
        log = [match('cheap', agent='alpha', task='t#0', score=0.9),
               match('cheap', agent='beta', task='t#1', score=0.3)]
        out = tb.field(log, 'cheap')
        board = {r['agent']: r for r in out['agents']}
        assert out['comparable'] is False and out['tasks'] == []
        assert board['alpha']['score'] == pytest.approx(0.9)
        assert board['beta']['score'] == pytest.approx(0.3)
        # ...and a gap between averages taken on different work is not a claim
        # about design
        assert out['spread'] == pytest.approx(0.6) and out['separates'] is False
        assert tb.board(log)[0]['comparable'] is False

    def test_per_task_puts_the_most_discriminating_task_first(self):
        out = tb.field([match('cheap', agent='alpha', task='t#0', score=0.9),
                        match('cheap', agent='beta', task='t#0', score=0.1),
                        match('cheap', agent='alpha', task='t#1', score=0.5),
                        match('cheap', agent='beta', task='t#1', score=0.5)], 'cheap')
        assert out['per_task'][0]['task'] == 't#0'
        assert out['per_task'][0]['best'] == 'alpha'
        assert out['per_task'][-1]['spread'] == 0.0

    def test_voids_are_counted_but_never_averaged(self):
        out = tb.field([match('cheap', agent='alpha', score=0.8),
                        match('cheap', agent='alpha', task='t#1', score=0.0, void=True),
                        match('cheap', agent='beta', score=0.4)], 'cheap')
        row = {r['agent']: r for r in out['agents']}['alpha']
        assert row['matches'] == 1 and row['voids'] == 1
        assert row['avg_score'] == pytest.approx(0.8)


class TestRetentionMatrix:

    def test_retention_is_what_a_design_keeps_on_the_cheaper_model(self):
        out = tb.matrix([match('dear', agent='alpha', score=1.0),
                         match('cheap', agent='alpha', score=0.5),
                         match('dear', agent='beta', score=1.0),
                         match('cheap', agent='beta', score=0.9)])
        assert out['ref'] == 'dear'
        rows = {r['agent']: r for r in out['rows']}
        assert rows['beta']['retention'] == pytest.approx(0.9)
        assert rows['alpha']['retention'] == pytest.approx(0.5)
        # the design that travels leads, whatever it scored on the big model
        assert out['rows'][0]['agent'] == 'beta'
        assert out['portable'] == ['beta'] and out['carried'] == ['alpha']

    def test_it_only_compares_tasks_both_cells_played(self):
        # alpha played an extra task on the dear model; retention must be read
        # off the one task both models saw, not off two different averages
        out = tb.matrix([match('dear', agent='alpha', task='t#0', score=0.8),
                         match('dear', agent='alpha', task='t#1', score=0.2),
                         match('cheap', agent='alpha', task='t#0', score=0.4)],
                        ref='dear')
        cell = {c['model']: c for c in out['rows'][0]['cells']}['cheap']
        assert cell['shared'] == 1
        assert cell['ref_score'] == pytest.approx(0.8)
        assert cell['retention'] == pytest.approx(0.5)

    def test_a_cell_with_no_shared_task_reports_nothing_rather_than_a_ratio(self):
        out = tb.matrix([match('dear', agent='alpha', task='t#0', score=0.8),
                         match('cheap', agent='alpha', task='t#1', score=0.8)],
                        ref='dear')
        cell = {c['model']: c for c in out['rows'][0]['cells']}['cheap']
        assert cell['n'] == 1 and cell['retention'] is None
        assert out['rows'][0]['retention'] is None

    def test_the_named_reference_wins_over_the_computed_one(self):
        log = [match('dear', agent='alpha', score=1.0),
               match('cheap', agent='alpha', score=0.5)]
        assert tb.matrix(log)['ref'] == 'dear'
        assert tb.matrix(log, ref='cheap')['ref'] == 'cheap'


class TestTierRound:

    def test_every_agent_plays_the_same_model(self, gauntlet_arena):
        out = gauntlet_arena.run_tier('good', tasks=['agentic/files#0'])
        assert out['matches'] == 3
        assert {m['model'] for m in out['results']} == {'good'}
        assert {m['agent'] for m in out['results']} == {'alpha', 'beta', 'gamma'}

    def test_a_tier_names_its_model(self, gauntlet_arena):
        assert 'error' in gauntlet_arena.run_tier('')

    def test_one_agent_is_not_a_comparison(self, gauntlet_arena):
        assert 'error' in gauntlet_arena.run_tier('good', agents=['alpha'])

    def test_it_leaves_the_agent_board_alone(self, gauntlet_arena):
        # the same rule the gauntlet follows, for the same reason: a score from
        # a cheap model must not read as a regression on a board built on
        # another one
        gauntlet_arena.run_match('alpha', 'agentic/files#0')
        before = json.loads(json.dumps(gauntlet_arena._rating('alpha')))
        gauntlet_arena.run_tier('bad', tasks=['agentic/files#0'])
        after = gauntlet_arena._rating('alpha')
        assert after['elo'] == before['elo'] and after['per_task'] == before['per_task']

    def test_rate_true_puts_it_on_the_record(self, gauntlet_arena):
        gauntlet_arena.run_tier('good', tasks=['agentic/files#0'], rate=True)
        assert gauntlet_arena._rating('alpha')['matches'] == 1

    def test_the_match_cap_stops_it(self, gauntlet_arena):
        gauntlet_arena.set_config(max_matches=2)
        out = gauntlet_arena.run_tier('good', tasks=['agentic/files#0',
                                                     'agentic/files#1'])
        assert out['matches'] == 2 and out['capped_by'] == 'max_matches'

    def test_it_hands_back_the_board_it_just_built(self, gauntlet_arena):
        out = gauntlet_arena.run_tier('good', tasks=['agentic/files#0'])
        assert out['tier']['model'] == 'good'
        assert len(out['tier']['agents']) == 3

    def test_a_tier_round_does_not_advance_the_season(self, gauntlet_arena):
        before = gauntlet_arena._state.get('season', 0)
        gauntlet_arena.run_tier('good', tasks=['agentic/files#0'])
        assert gauntlet_arena._state.get('season', 0) == before

    def test_forward_dispatches_it_and_the_reads(self, gauntlet_arena):
        out = gauntlet_arena.forward('tier_run', model='good',
                                     tasks=['agentic/files#0'])
        assert out['matches'] == 3 and out['model'] == 'good'
        status = gauntlet_arena.forward('tiers')
        assert status['tiers'][0]['model'] == 'good'
        assert gauntlet_arena.forward('tier', model='good')['matches'] == 3
        assert gauntlet_arena.forward('tier_matrix')['ref'] == 'good'

    def test_two_tiers_make_a_retention_row(self, gauntlet_arena):
        gauntlet_arena.run_tier('good', tasks=['agentic/files#0'])
        gauntlet_arena.run_tier('bad', tasks=['agentic/files#0'])
        matrix = gauntlet_arena.tier_matrix(ref='good')
        rows = {r['agent']: r for r in matrix['rows']}
        # the scripted runner fails outright on 'bad', so nothing survives it
        assert rows['alpha']['retention'] == 0.0
        assert 'alpha' in matrix['carried']
