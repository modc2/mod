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
from src.evals.scorers import run_scorer, steps_of


# ═══════════════════════════════════════════════════════════════════════
#  FIXTURES
# ═══════════════════════════════════════════════════════════════════════

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
    def runner(prompt, agent, model, steps, free, path):
        return behaviour(agent, path, prompt), {'cost': 0.001, 'model': 'free/model'}
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

    def test_a_runner_blowup_is_a_forfeit_not_a_crash(self, tmpdir):
        def boom(**kwargs):
            raise RuntimeError('no api key')

        a = Arena(runner=boom, agents=FakeAgents(), root=os.path.join(tmpdir, 'arena'))
        m = a.run_match('alpha', 'agentic/files#0')
        assert m['score'] == 0.0
        assert 'no api key' in m['error']

    def test_no_runner_is_reported_per_match(self, tmpdir):
        a = Arena(agents=FakeAgents(), root=os.path.join(tmpdir, 'arena'))
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
        assert arena.forward('run')['matches'] == 6

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
