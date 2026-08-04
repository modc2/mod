"""openarena tests.

Every test drives a real backend process against a scratch state directory —
the judge runs real interpreters in real sandboxes, so the things worth testing
(does a correct program pass, does a wrong one fail, does an infinite loop get
killed) can only be tested for real.

    pytest orbit/openarena/tests -q
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import pytest
import requests

MOD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARY = os.path.join(MOD, 'openarena-rs', 'target', 'release', 'openarena-api')

REFERENCE = """import sys
n = int(sys.stdin.read().split()[0])
for i in range(1, n + 1):
    s = ('Fizz' if i % 3 == 0 else '') + ('Buzz' if i % 5 == 0 else '')
    print(s or i)
"""

STACK = """class Stack:
    def __init__(self):
        self._items = []
    def push(self, x):
        self._items.append(x)
    def pop(self):
        if not self._items:
            raise IndexError('pop from empty stack')
        return self._items.pop()
    def peek(self):
        if not self._items:
            raise IndexError('peek at empty stack')
        return self._items[-1]
    def size(self):
        return len(self._items)
    def is_empty(self):
        return not self._items
"""


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def arena():
    """A backend on its own port with its own empty state directory."""
    if not os.path.exists(BINARY):
        pytest.skip(f'{BINARY} not built — run `m openarena/build`')
    state = tempfile.mkdtemp(prefix='openarena-test-')
    port = free_port()
    proc = subprocess.Popen(
        [BINARY],
        env={**os.environ, 'PORT': str(port), 'OPENARENA_STATE': state},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f'http://127.0.0.1:{port}'
    for _ in range(50):
        try:
            if requests.get(f'{url}/health', timeout=1).ok:
                break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail('backend never came up')
    yield url
    proc.terminate()
    proc.wait(timeout=10)
    shutil.rmtree(state, ignore_errors=True)


def post(url, path, body, timeout=120):
    return requests.post(f'{url}{path}', json=body, timeout=timeout)


def enter_static(url, name, code, language='python'):
    return post(url, '/agents', {
        'name': name, 'kind': 'static',
        'config': {'code': code, 'language': language},
    }).json()


# ── the arena comes up seeded ────────────────────────────────────────────

def test_seed_pack_is_planted(arena):
    tasks = requests.get(f'{arena}/tasks', timeout=10).json()
    assert tasks['count'] >= 6
    slugs = {t['slug'] for t in tasks['tasks']}
    assert {'fizzbuzz', 'two-sum', 'stack-class'} <= slugs


def test_hidden_cases_are_not_leaked(arena):
    t = requests.get(f'{arena}/tasks/fizzbuzz', timeout=10).json()
    assert t['hidden_tests'] >= 1
    hidden = [c for c in t['tests'] if c.get('hidden')]
    assert hidden, 'the hidden case should still be listed'
    for c in hidden:
        assert not c.get('expect'), 'a hidden case must not ship its answer'
        assert not c.get('stdin'), 'a hidden case must not ship its input'
    # The author can still see them.
    full = requests.get(f'{arena}/tasks/fizzbuzz?reveal=1', timeout=10).json()
    assert any(c.get('expect') for c in full['tests'] if c.get('hidden'))


# ── the judge ────────────────────────────────────────────────────────────

def test_correct_program_scores_one(arena):
    r = post(arena, '/submit', {'task': 'fizzbuzz', 'code': REFERENCE, 'language': 'python'}).json()
    assert r['score'] == 1.0
    assert r['solved'] is True
    assert r['passed'] == r['total'] >= 3


def test_wrong_program_scores_zero(arena):
    r = post(arena, '/submit', {'task': 'fizzbuzz', 'code': 'print("nope")', 'language': 'python'}).json()
    assert r['score'] == 0.0
    assert str(r['score'])[0] != '-', 'a zero score must not read as -0.0'
    assert r['solved'] is False


def test_partial_credit(arena):
    """Memorising the visible example passes that case and nothing else."""
    r = post(arena, '/submit', {
        'task': 'fizzbuzz', 'code': "print('1\\n2\\nFizz\\n4\\nBuzz')", 'language': 'python',
    }).json()
    assert 0 < r['score'] < 1
    assert r['passed'] == 1


def test_io_task_is_language_agnostic(arena):
    js = """const d=require('fs').readFileSync(0,'utf8').trim().split(/\\s+/);
const n=parseInt(d[0]); const out=[];
for(let i=1;i<=n;i++){const s=(i%3?'':'Fizz')+(i%5?'':'Buzz');out.push(s||i);}
console.log(out.join('\\n'));
"""
    r = post(arena, '/submit', {'task': 'fizzbuzz', 'code': js, 'language': 'javascript'}).json()
    assert r['score'] == 1.0
    assert r['language'] == 'javascript'


def test_unit_mode_grader_imports_the_submission(arena):
    r = post(arena, '/submit', {'task': 'stack-class', 'code': STACK}).json()
    assert r['score'] == 1.0, r['cases']


def test_unit_mode_catches_a_missing_contract(arena):
    """A Stack that returns None on empty instead of raising IndexError passes
    the cases that only push and pop, and fails the one that checks the raise."""
    sloppy = STACK.replace("        if not self._items:\n"
                           "            raise IndexError('pop from empty stack')\n"
                           "        return self._items.pop()",
                           "        return self._items.pop() if self._items else None")
    assert sloppy != STACK, 'the test patch stopped matching the fixture'
    r = post(arena, '/submit', {'task': 'stack-class', 'code': sloppy}).json()
    assert r['score'] < 1.0
    failed = [c['name'] for c in r['cases'] if not c['passed']]
    assert failed == ['empty raises IndexError'], failed


def test_unsupported_language_is_refused(arena):
    r = post(arena, '/submit', {'task': 'fizzbuzz', 'code': 'int main(){}', 'language': 'c++'})
    assert r.status_code == 400
    assert 'unsupported language' in r.json()['error']


# ── the sandbox ──────────────────────────────────────────────────────────

def test_infinite_loop_is_killed(arena):
    r = post(arena, '/submit', {
        'task': 'fizzbuzz', 'code': 'while True: pass', 'language': 'python',
    }, timeout=180).json()
    assert r['score'] == 0.0
    assert r['cases'][0]['timed_out'] is True


def test_submission_has_no_network(arena):
    code = ('import urllib.request\n'
            'print(urllib.request.urlopen("http://1.1.1.1", timeout=3).status)\n')
    r = post(arena, '/submit', {'task': 'fizzbuzz', 'code': code, 'language': 'python'},
             timeout=180).json()
    assert r['score'] == 0.0
    # Either the namespace blocked it or the host has no unshare; only the
    # first is a pass, and a host without unshare should say so loudly.
    assert 'unreachable' in r['cases'][0]['stderr'].lower() or \
           'network' in r['cases'][0]['stderr'].lower()


# ── competitors and matches ──────────────────────────────────────────────

def test_entering_a_competitor_validates_its_driver(arena):
    assert post(arena, '/agents', {'name': 'no-url', 'kind': 'http'}).status_code == 400
    assert post(arena, '/agents', {'name': 'no-code', 'kind': 'static'}).status_code == 400
    assert post(arena, '/agents', {'name': 'bogus', 'kind': 'telepathy'}).status_code == 400


def test_duplicate_names_are_refused(arena):
    enter_static(arena, 'dupe', REFERENCE)
    r = post(arena, '/agents', {'name': 'dupe', 'kind': 'static', 'config': {'code': 'x'}})
    assert r.status_code == 400
    assert 'already in the arena' in r.json()['error']


def test_rated_match_orders_and_moves_elo(arena):
    enter_static(arena, 'good', REFERENCE)
    enter_static(arena, 'bad', 'print("nope")')
    m = post(arena, '/matches', {'task': 'fizzbuzz', 'agents': ['good', 'bad']}, timeout=300).json()

    assert m['rated'] is True
    assert [r['agent_name'] for r in m['results']] == ['good', 'bad'], 'best score first'
    good, bad = m['results']
    assert good['score'] == 1.0 and bad['score'] == 0.0
    assert good['elo_after'] > good['elo_before']
    assert bad['elo_after'] < bad['elo_before']
    # Elo is zero-sum within a match.
    moved = sum(r['elo_after'] - r['elo_before'] for r in m['results'])
    assert abs(moved) < 1e-9


def test_solo_match_is_practice(arena):
    enter_static(arena, 'solo', REFERENCE)
    m = post(arena, '/matches', {'task': 'fizzbuzz', 'agents': ['solo']}, timeout=300).json()
    assert m['rated'] is False
    assert m['results'][0]['elo_after'] == m['results'][0]['elo_before']


def test_a_broken_driver_loses_rather_than_breaking_the_match(arena):
    """One entrant being unreachable must not deny everyone else a result."""
    post(arena, '/agents', {'name': 'ghost', 'kind': 'http',
                            'config': {'url': 'http://127.0.0.1:1/nope'}})
    enter_static(arena, 'present', REFERENCE)
    m = post(arena, '/matches', {'task': 'fizzbuzz', 'agents': ['present', 'ghost']},
             timeout=300).json()
    by_name = {r['agent_name']: r for r in m['results']}
    assert by_name['present']['score'] == 1.0
    assert by_name['ghost']['score'] == 0.0
    assert by_name['ghost']['error'], 'the failure should be recorded, not swallowed'


def test_match_record_keeps_the_programs(arena):
    enter_static(arena, 'archivist', REFERENCE)
    m = post(arena, '/matches', {'task': 'fizzbuzz', 'agents': ['archivist']}, timeout=300).json()
    full = requests.get(f"{arena}/matches/{m['id']}", timeout=10).json()
    assert full['results'][0]['code'].strip() == REFERENCE.strip()


def test_leaderboard_ranks_by_elo(arena):
    b = requests.get(f'{arena}/leaderboard', timeout=10).json()
    elos = [a['elo'] for a in b['leaderboard']]
    assert elos == sorted(elos, reverse=True)
    assert all(a['attempts'] > 0 for a in b['leaderboard']), 'unplayed entrants are unranked'


# ── uploading a task ─────────────────────────────────────────────────────

def test_upload_and_play_a_new_task(arena):
    spec = {
        'title': 'Sum Two Numbers',
        'statement': 'Read two integers on one line and print their sum.',
        'tests': [
            {'name': 'visible', 'stdin': '2 3\n', 'expect': '5'},
            {'name': 'hidden', 'stdin': '-4 9\n', 'expect': '5', 'hidden': True},
        ],
    }
    t = post(arena, '/tasks', spec).json()
    assert t['slug'] == 'sum-two-numbers'
    assert t['mode'] == 'io' and t['language'] == 'any'

    code = 'import sys\na, b = map(int, sys.stdin.read().split())\nprint(a + b)\n'
    r = post(arena, '/submit', {'task': 'sum-two-numbers', 'code': code}).json()
    assert r['score'] == 1.0

    assert requests.delete(f"{arena}/tasks/{t['id']}", timeout=10).ok
    assert requests.get(f'{arena}/tasks/sum-two-numbers', timeout=10).status_code == 404


def test_a_task_without_cases_is_refused(arena):
    r = post(arena, '/tasks', {'title': 'Vibes Only', 'tests': []})
    assert r.status_code == 400
    assert 'at least one test case' in r.json()['error']


# ── MCP ──────────────────────────────────────────────────────────────────

def test_mcp_handshake_and_tools(arena):
    r = post(arena, '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                             'params': {'protocolVersion': '2025-06-18'}}).json()
    assert r['result']['serverInfo']['name'] == 'openarena'

    r = post(arena, '/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}).json()
    names = {t['name'] for t in r['result']['tools']}
    assert {'list_tasks', 'enter_agent', 'run_match', 'submit', 'leaderboard'} <= names
    for t in r['result']['tools']:
        assert t['description'] and t['inputSchema']['type'] == 'object'


def test_mcp_tool_call_matches_rest(arena):
    r = post(arena, '/mcp', {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                             'params': {'name': 'list_tasks', 'arguments': {}}}).json()
    assert r['result']['isError'] is False
    assert r['result']['structuredContent'] == requests.get(f'{arena}/tasks', timeout=10).json()


def test_mcp_reports_tool_errors_in_band(arena):
    """A failed tool is an MCP result with isError, not a JSON-RPC error."""
    r = post(arena, '/mcp', {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                             'params': {'name': 'get_task', 'arguments': {'task': 'nope'}}}).json()
    assert 'error' not in r
    assert r['result']['isError'] is True


def test_mcp_notification_gets_no_reply(arena):
    r = post(arena, '/mcp', {'jsonrpc': '2.0', 'method': 'notifications/initialized'})
    assert r.status_code == 202


def test_mcp_stdio_transport():
    if not os.path.exists(BINARY):
        pytest.skip('not built')
    state = tempfile.mkdtemp(prefix='openarena-stdio-')
    try:
        msgs = '\n'.join([
            json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}),
            json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                        'params': {'name': 'arena_info', 'arguments': {}}}),
        ]) + '\n'
        out = subprocess.run([BINARY, '--stdio'], input=msgs, capture_output=True,
                             text=True, timeout=60,
                             env={**os.environ, 'OPENARENA_STATE': state}).stdout
        lines = [json.loads(l) for l in out.strip().splitlines()]
        assert lines[0]['result']['serverInfo']['name'] == 'openarena'
        assert lines[1]['result']['structuredContent']['tasks'] >= 6
    finally:
        shutil.rmtree(state, ignore_errors=True)


# ── the python client ────────────────────────────────────────────────────

def test_mod_client_round_trip(arena):
    sys.path.insert(0, MOD)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('openarena_mod', os.path.join(MOD, 'mod.py'))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        mod = m.Mod(server_url=arena)

        assert mod.health()['up'] is True
        assert mod.info()['protocol'] == 'arena/1.0'
        assert len(mod.tools()) == 13
        assert mod.tasks()['count'] >= 6

        report = mod.test()
        assert report['judge_ok'] is True, report
    finally:
        sys.path.remove(MOD)
