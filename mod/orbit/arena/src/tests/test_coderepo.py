"""test_coderepo.py — a repo of choice, harvested into a game, played in the cage.

Covers the three pieces that make a coding game possible here:

    judge()      the sandbox door that runs a player's code without handing a
                 class `exec`
    harvest      reading a repository into graded tasks, by running it
    the game     the generated class: `answer = 'code'`, many seats at once,
                 scored on vectors it never showed anyone

Nothing here needs the network or a built backend — the repo under test is
written into a temp directory.

    pytest src/tests/test_coderepo.py -q
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(SRC, 'runtime'))

import host                                        # noqa: E402  the class sandbox
from codeeval import harvest as H                  # noqa: E402


# ── the door that runs a submission ──────────────────────────────────────────

def test_judge_runs_a_submission_and_returns_one_result_per_call():
    out = host.judge('def add(a, b):\n    return a + b\n', 'add',
                     [{'args': [1, 2]}, {'args': [3, 4]}])
    assert out['ok'], out
    assert [r['value'] for r in out['results']] == [3, 7]


def test_judge_keeps_the_cage_the_class_is_in():
    blocked = host.judge('import socket\n', 'x', [])
    assert not blocked['ok']
    assert 'socket' in blocked['error']

    no_files = host.judge("def f():\n    return open('/etc/passwd')\n", 'f', [{'args': []}])
    assert no_files['ok']                       # it loaded…
    assert not no_files['results'][0]['ok']     # …and `open` is not there to call
    assert 'open' in no_files['results'][0]['error']


def test_judge_ends_a_call_that_will_not_end():
    out = host.judge('def spin(n):\n    while True:\n        pass\n', 'spin',
                     [{'args': [1], 'timeout': 1}])
    assert out['ok']
    assert not out['results'][0]['ok']
    assert 'longer than' in out['results'][0]['error']


def test_judge_seeds_randomness_so_a_stochastic_answer_replays():
    code = 'def pick(n):\n    return [random.randint(0, 99) for _ in range(n)]\n'
    once = host.judge(code, 'pick', [{'args': [3], 'seed': 7}])
    again = host.judge(code, 'pick', [{'args': [3], 'seed': 7}])
    assert once['results'][0]['value'] == again['results'][0]['value']


def test_judge_says_which_name_it_wanted():
    out = host.judge('def other():\n    return 1\n', 'wanted', [{'args': []}])
    assert not out['ok']
    assert 'wanted' in out['error']


def test_a_class_cannot_exec_but_is_handed_judge():
    """The point of the door: `exec` stays denied, `judge` is in scope."""
    session = host.Session()
    session.load({'source': textwrap.dedent('''
        class Probe:
            def play(self, view, seat):
                try:
                    exec("1 + 1")
                    return "exec worked"
                except NameError:
                    got = judge("def f():\\n    return 41 + 1\\n", "f", [{"args": []}])
                    return str(got["results"][0]["value"])
    '''), 'seed': 1, 'seats': 1})
    assert session.call({'method': 'play', 'args': ['', 0]})['value'] == '42'


# ── a repo, harvested ────────────────────────────────────────────────────────

REPO_FILE = '''
"""A small library, for the harvester to read."""

import math


def double_all(nums):
    """Every number, twice."""
    out = []
    for n in nums:
        out.append(n * 2)
    return out


def clamp(value, size):
    """Keep value inside 0..size."""
    if value < 0:
        return 0
    if value > size:
        return size
    return value


def initials(text):
    """The first letter of each word, upper case."""
    letters = []
    for word in str(text).split():
        if word:
            letters.append(word[0])
    return ''.join(letters).upper()


def hypot_to(x, y):
    """Distance from the origin, rounded."""
    total = math.sqrt(float(x) ** 2 + float(y) ** 2)
    return round(total, 3)


def shout(s):
    """Not gradeable: prints, so the harvester leaves it alone."""
    print(s)
    return s
'''


@pytest.fixture(scope='module')
def repo(tmp_path_factory):
    d = tmp_path_factory.mktemp('repo-of-choice')
    (d / 'lib.py').write_text(REPO_FILE)
    subprocess.run(['git', 'init', '-q', str(d)], check=False, capture_output=True)
    return d


@pytest.fixture(scope='module')
def built(repo):
    return H.build(str(repo), n=10, rounds=2, seed=5)


def test_a_local_path_is_a_repo(repo):
    meta = H.resolve_repo(str(repo))
    assert meta['source'] == 'local'
    assert meta['path'] == str(repo.resolve())
    assert meta['slug']


def test_a_repo_that_is_neither_a_path_nor_a_url_is_refused():
    with pytest.raises(ValueError):
        H.resolve_repo('not a repo at all')


def test_the_harvest_is_functions_with_vectors_it_ran(built):
    names = {t['name'] for t in built['tasks']}
    assert {'double_all', 'clamp', 'initials'} <= names, names
    assert 'shout' not in names, 'a function that prints is not gradeable'
    for t in built['tasks']:
        assert len(t['tests']) >= 2
        assert t['stub'].rstrip().endswith('pass')
        assert 'original' in t          # the answer key is here…


def test_the_stored_game_does_not_carry_the_answers(built):
    assert 'original' not in built['source'].split('TASKS = ')[1][:4000]
    for t in built['tasks']:
        body = t['original'].split('\n', 1)[1]
        assert body not in built['source'], f"{t['name']}'s body shipped with the exam"


# ── the game that comes out ──────────────────────────────────────────────────

@pytest.fixture(scope='module')
def game(built):
    session = host.Session()
    loaded = session.load({'source': built['source'], 'seed': 3, 'seats': 2})
    return session, loaded


def test_the_game_asks_its_seats_for_code(game):
    _, loaded = game
    assert loaded['role'] == 'game'
    assert loaded['info']['answer'] == 'code'
    assert loaded['info']['max_players'] >= 2


def test_a_round_shows_the_signature_and_holds_most_vectors_back(game, built):
    session, _ = game
    view = session.call({'method': 'view', 'args': [0]})['value']
    assert 'Reconstruct `' in view
    assert 'WRITE THIS:' in view
    name = view.split('Reconstruct `')[1].split('`')[0]
    task = next(t for t in built['tasks'] if t['name'] == name)
    shown = view.count(name + '(')
    assert shown <= 5 < len(task['tests']) + 4, 'the whole test set is on the page'


def test_the_original_scores_and_a_guess_does_not(game, built):
    session, _ = game
    answers = {t['name']: t['original'] for t in built['tasks']}
    while not session.call({'method': 'done', 'args': []})['value']:
        view = session.call({'method': 'view', 'args': [0]})['value']
        name = view.split('Reconstruct `')[1].split('`')[0]
        session.call({'method': 'step', 'args': [{
            0: '```python\n' + answers[name] + '\n```',      # the real one, fenced
            1: 'def %s(*a, **k):\n    return 42\n' % name,   # a guess
        }]})
    out = session.call({'method': 'result', 'args': []})['value']
    assert out['scores'][0] == 1.0, out['summary']
    assert out['scores'][1] < 1.0
    assert 'seat 0 takes it' in out['summary']
    assert out['card'] and out['repo']


def test_a_seat_is_scored_once_even_though_moves_arrive_twice(built):
    """The host hands a move over keyed 0 *and* keyed "0"."""
    session = host.Session()
    session.load({'source': built['source'], 'seed': 3, 'seats': 1})
    answers = {t['name']: t['original'] for t in built['tasks']}
    view = session.call({'method': 'view', 'args': [0]})['value']
    name = view.split('Reconstruct `')[1].split('`')[0]
    step = session.call({'method': 'step', 'args': [
        {0: answers[name], '0': answers[name]}]})['value']
    assert step['note'].count('seat 0') == 1, step['note']


def test_an_empty_answer_is_an_illegal_move_not_a_crash(built):
    session = host.Session()
    session.load({'source': built['source'], 'seed': 3, 'seats': 1})
    step = session.call({'method': 'step', 'args': [{0: 'I would rather not.'}]})['value']
    assert step['legal']['0'] is False
    assert session.call({'method': 'result', 'args': []})['value']['scores'][0] == 0.0


def test_a_repo_with_nothing_to_ablate_says_so(tmp_path):
    (tmp_path / 'all_io.py').write_text('def w(p):\n    print(p)\n    return p\n')
    with pytest.raises(RuntimeError) as e:
        H.build(str(tmp_path), n=5)
    assert 'no gradeable function' in str(e.value)


def test_the_game_file_is_python_a_reader_can_read(built):
    import ast
    tree = ast.parse(built['source'])
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    assert len(classes) == 1
    assert built['name'].split('-')[0] in classes[0].lower()
