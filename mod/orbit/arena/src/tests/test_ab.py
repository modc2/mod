"""A/B experiments, end to end: two entered players, the same game, seats
swapped, one report — against a real server on a throwaway state directory,
exactly like test_arena.py.

    pytest src/tests/test_ab.py -q
"""

import os
import shutil
import socket
import subprocess
import tempfile
import time

import pytest
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
BINARY = os.path.join(SRC, 'arena-rs', 'target', 'release', 'arena-api')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def arena():
    if not os.path.exists(BINARY):
        pytest.skip(f'no backend at {BINARY} — run `m arena/build`')
    state = tempfile.mkdtemp(prefix='arena-ab-test-')
    port = free_port()
    proc = subprocess.Popen(
        [BINARY],
        env={**os.environ, 'PORT': str(port), 'ARENA_STATE': state,
             'ARENA_STORE_URL': 'off'},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            if requests.get(f'{base}/info', timeout=1).ok:
                break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail(f'server never came up: {proc.stdout.read()[-2000:]}')
    yield base
    proc.terminate()
    proc.wait(timeout=10)
    shutil.rmtree(state, ignore_errors=True)


def get(base, path, **params):
    r = requests.get(base + path, params=params, timeout=30)
    return r.status_code, r.json()


def post(base, path, body, timeout=120):
    r = requests.post(base + path, json=body, timeout=timeout)
    return r.status_code, r.json()


@pytest.fixture(scope='module')
def bots(arena):
    post(arena, '/players', {'name': 'perfect', 'kind': 'wasm',
                             'config': {'module': 'bot-ttt'}})
    post(arena, '/players', {'name': 'dice', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})
    return ['perfect', 'dice']


def finished(base, exp_id, budget=180):
    """Poll one experiment until it leaves `running`."""
    deadline = time.time() + budget
    while time.time() < deadline:
        code, rep = get(base, f'/ab/{exp_id}')
        assert code == 200, rep
        if rep['status'] != 'running':
            return rep
        time.sleep(1)
    pytest.fail(f'experiment {exp_id} still running after {budget}s')


def test_an_experiment_plays_seat_swapped_and_reports_the_difference(arena, bots):
    code, rep = post(arena, '/ab', {'a': 'perfect', 'b': 'dice',
                                    'games': ['ttt'], 'count': 2, 'seed': 11})
    assert code == 200, rep
    assert rep['status'] == 'running'
    rep = finished(arena, rep['id'])

    assert rep['status'] == 'done', rep
    assert rep['played'] == 2 and rep['voids'] == 0, rep
    ta, tb = rep['a']['total'], rep['b']['total']
    # Every finished match is exactly one result on each side.
    assert ta['wins'] + ta['draws'] + ta['losses'] == 2
    assert ta['wins'] == tb['losses'] and tb['wins'] == ta['losses']
    assert rep['verdict'], rep
    # A solved-game bot does not lose to dice.
    assert ta['losses'] == 0, rep['verdict']

    # Seats actually swapped: match 0 seats a first, match 1 seats b first.
    firsts = []
    for mid in rep['matches']:
        _, m = get(arena, f'/matches/{mid}')
        firsts.append(m['seats'][0]['player_name'])
    assert firsts == ['perfect', 'dice'], firsts

    # The matches were rated as usual — they are on the record, not beside it.
    _, m = get(arena, f'/matches/{rep["matches"][0]}')
    assert m['rated'] is True


def test_the_report_is_listed_and_can_be_forgotten(arena, bots):
    _, listed = get(arena, '/ab')
    assert listed['count'] >= 1
    brief = listed['experiments'][0]
    assert brief['a'] == 'perfect' and brief['b'] == 'dice'
    assert brief['verdict']

    assert requests.delete(f"{arena}/ab/{brief['id']}", timeout=10).status_code == 200
    code, _ = get(arena, f"/ab/{brief['id']}")
    assert code == 404


def test_both_sides_must_exist_and_differ(arena, bots):
    code, out = post(arena, '/ab', {'a': 'perfect', 'b': 'perfect'})
    assert code == 400 and 'different' in out['error']
    code, out = post(arena, '/ab', {'a': 'perfect', 'b': 'nobody-here'})
    assert code == 400 or code == 404
    code, out = post(arena, '/ab', {'a': 'perfect'})
    assert code == 400
