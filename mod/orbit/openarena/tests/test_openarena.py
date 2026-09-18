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
        assert len(mod.tools()) == 16
        assert mod.tasks()['count'] >= 6

        report = mod.test()
        assert report['judge_ok'] is True, report
    finally:
        sys.path.remove(MOD)


# ── benchmarks off the web ───────────────────────────────────────────────
#
# The importer is a network client, so these tests give it a network: a
# throwaway http server on localhost serving a benchmark shaped like MBPP and a
# problem page shaped like a judge's. Nothing here reaches the real internet —
# what is worth testing is the conversion and the fence around it, and both are
# ours.

SAMPLE_BENCH = [
    {
        'task_id': 1,
        'prompt': 'Write a function add(a, b) that returns their sum.',
        'test_imports': [],
        'test_list': [
            'assert add(1, 2) == 3',
            'assert add(-4, 9) == 5',
            'assert add(0, 0) == 0',
        ],
    },
    {
        'task_id': 2,
        'prompt': 'Write a function double(x) that returns twice its argument.',
        'test_imports': [],
        'test_list': ['assert double(2) == 4', 'assert double(-3) == -6'],
    },
]

SAMPLE_PAGE = """<html><head><title>Echo Sum &ndash; Test Judge</title></head><body>
<nav><a href="/">home</a> <a href="/x">problems</a></nav>
<h1>Echo Sum</h1>
<p>The first line of stdin holds two integers separated by a space. Print their
sum on one line, and nothing else, because that is all the judge compares.</p>
<h3>Sample Input 1</h3><pre>2 3</pre>
<h3>Sample Output 1</h3><pre>5</pre>
<h3>Sample Input 2</h3><pre>-4 9</pre>
<h3>Sample Output 2</h3><pre>5</pre>
</body></html>"""


@pytest.fixture(scope='module')
def bench_site():
    """A tiny web to import from."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body, kind = (json.dumps(SAMPLE_BENCH), 'application/json')
            if self.path.startswith('/problem'):
                body, kind = SAMPLE_PAGE, 'text/html'
            raw = body.encode()
            self.send_response(200)
            self.send_header('content-type', kind)
            self.send_header('content-length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_port}'
    srv.shutdown()


@pytest.fixture(scope='module')
def open_arena():
    """A backend allowed to fetch from this machine — the default refuses."""
    if not os.path.exists(BINARY):
        pytest.skip(f'{BINARY} not built — run `m openarena/build`')
    state = tempfile.mkdtemp(prefix='openarena-bench-')
    port = free_port()
    proc = subprocess.Popen(
        [BINARY],
        env={**os.environ, 'PORT': str(port), 'OPENARENA_STATE': state,
             'OPENARENA_BENCH_LOCAL': '1'},
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


def test_bench_catalog_lists_the_known_benchmarks(arena):
    c = requests.get(f'{arena}/bench/sources', timeout=10).json()
    ids = {s['id'] for s in c['sources']}
    assert {'humaneval', 'mbpp', 'code_contests', 'hf', 'json', 'html'} <= ids
    assert c['enabled'] is True
    assert set(c['styles']) == {'humaneval', 'asserts', 'io', 'html'}
    # Every source says where it came from and under what terms.
    for s in c['sources']:
        assert s['license'], s['id']


def test_bench_unknown_source_is_refused(arena):
    r = post(arena, '/bench/preview', {'source': 'not-a-benchmark'})
    assert r.status_code == 400
    assert 'unknown source' in r.json()['error']


def test_bench_refuses_this_network_by_default(arena, bench_site):
    """The importer is not a way to ask the arena's host what it can reach."""
    r = post(arena, '/bench/preview', {'source': 'json', 'url': bench_site})
    assert r.status_code == 400
    assert 'inside this network' in r.json()['error']


def test_bench_refuses_a_non_http_url(arena):
    r = post(arena, '/bench/preview', {'source': 'json', 'url': 'file:///etc/passwd'})
    assert r.status_code == 400
    assert 'not an http(s) url' in r.json()['error']


def test_bench_preview_writes_nothing(open_arena, bench_site):
    before = requests.get(f'{open_arena}/tasks', timeout=10).json()['count']
    r = post(open_arena, '/bench/preview',
             {'source': 'json', 'url': bench_site, 'style': 'asserts', 'limit': 2}).json()
    assert r['count'] == 2
    assert r['sample']['mode'] == 'unit'
    assert requests.get(f'{open_arena}/tasks', timeout=10).json()['count'] == before


def test_bench_import_grades_like_any_other_task(open_arena, bench_site):
    r = post(open_arena, '/bench/import',
             {'source': 'json', 'url': bench_site, 'style': 'asserts',
              'limit': 2, 'slug_prefix': 'demo'}).json()
    assert r['imported'] == 2
    assert r['next_offset'] == 2

    t = requests.get(f'{open_arena}/tasks/demo-1', timeout=10).json()
    assert t['mode'] == 'unit' and t['language'] == 'python'
    # One case per assertion, the first of them visible so the entrant learns
    # the function's name.
    assert t['total_tests'] == 3 and t['hidden_tests'] == 2
    assert 'benchmark' in t['tags']

    good = post(open_arena, '/submit',
                {'task': 'demo-1', 'code': 'def add(a, b):\n    return a + b\n'}).json()
    assert good['score'] == 1.0 and good['solved'] is True

    # Answering only the visible case is exactly what hidden cases are for.
    cheat = post(open_arena, '/submit',
                 {'task': 'demo-1', 'code': 'def add(a, b):\n    return 3\n'}).json()
    assert 0 < cheat['score'] < 1 and cheat['solved'] is False


def test_bench_reimport_skips_rather_than_fails(open_arena, bench_site):
    body = {'source': 'json', 'url': bench_site, 'style': 'asserts',
            'limit': 2, 'slug_prefix': 'twice'}
    assert post(open_arena, '/bench/import', body).json()['imported'] == 2
    again = post(open_arena, '/bench/import', body).json()
    assert again['imported'] == 0
    assert len(again['skipped']) == 2
    assert not again['failed']


def test_bench_hide_after_overrides_the_split(open_arena, bench_site):
    r = post(open_arena, '/bench/import',
             {'source': 'json', 'url': bench_site, 'style': 'asserts',
              'limit': 1, 'hide_after': 0, 'slug_prefix': 'blind'}).json()
    assert r['tasks'][0]['cases'] == r['tasks'][0]['hidden'] == 3


def test_bench_scrapes_a_problem_page_into_an_io_task(open_arena, bench_site):
    r = post(open_arena, '/bench/import',
             {'source': 'html', 'url': f'{bench_site}/problem', 'slug_prefix': 'page'}).json()
    assert r['imported'] == 1, r
    slug = r['tasks'][0]['slug']

    t = requests.get(f'{open_arena}/tasks/{slug}?reveal=1', timeout=10).json()
    assert t['mode'] == 'io'
    # Both samples, paired by their Input/Output labels and not by luck.
    assert [(c['stdin'], c['expect']) for c in t['tests']] == [('2 3', '5'), ('-4 9', '5')]
    assert 'sum' in t['statement'].lower()
    assert 'home' not in t['statement'], 'the site navigation is not the statement'

    ok = post(open_arena, '/submit', {
        'task': slug, 'language': 'python',
        'code': 'import sys\nprint(sum(int(x) for x in sys.stdin.read().split()))\n',
    }).json()
    assert ok['score'] == 1.0


def test_bench_can_be_switched_off():
    """OPENARENA_BENCH=0 means the arena does not fetch, and says so."""
    if not os.path.exists(BINARY):
        pytest.skip('not built')
    state = tempfile.mkdtemp(prefix='openarena-nobench-')
    port = free_port()
    proc = subprocess.Popen(
        [BINARY],
        env={**os.environ, 'PORT': str(port), 'OPENARENA_STATE': state,
             'OPENARENA_BENCH': '0'},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(50):
            try:
                if requests.get(f'{url}/health', timeout=1).ok:
                    break
            except requests.RequestException:
                time.sleep(0.1)
        assert requests.get(f'{url}/health', timeout=5).json()['bench_fetch'] is False
        assert requests.get(f'{url}/bench/sources', timeout=5).json()['enabled'] is False
        r = post(url, '/bench/preview', {'source': 'mbpp', 'limit': 1})
        assert r.status_code == 400
        assert 'off' in r.json()['error']
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        shutil.rmtree(state, ignore_errors=True)


def test_bench_next_offset_counts_records_not_keeps(open_arena, bench_site):
    """Re-importing a page must not advance past a record it only skipped."""
    body = {'source': 'json', 'url': bench_site, 'style': 'asserts',
            'limit': 2, 'slug_prefix': 'paging'}
    first = post(open_arena, '/bench/import', body).json()
    second = post(open_arena, '/bench/import', body).json()
    assert first['next_offset'] == second['next_offset'] == 2
