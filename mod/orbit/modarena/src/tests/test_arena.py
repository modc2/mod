"""End-to-end tests against a real arena server on a throwaway state directory.

Everything here goes through the same doors a user does — HTTP, MCP, and the
node runner — because the thing worth testing is not that the Rust compiles
(it does, or nothing runs) but that the three surfaces agree with each other.

    pytest src/tests -q

Needs the backend built (`m modarena/build`) and node on PATH.
"""

import base64
import json
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
BINARY = os.path.join(SRC, 'modarena-rs', 'target', 'release', 'modarena-api')
RUNNER = os.path.join(SRC, 'runtime', 'run.mjs')
EXAMPLES = os.path.join(SRC, 'examples', 'wasm')
CLASSES = os.path.join(SRC, 'examples', 'classes')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def arena():
    """A server of our own, on its own port, with its own state."""
    if not os.path.exists(BINARY):
        pytest.skip(f'no backend at {BINARY} — run `m modarena/build`')

    state = tempfile.mkdtemp(prefix='arena-test-')
    port = free_port()
    proc = subprocess.Popen(
        [BINARY],
        env={**os.environ, 'PORT': str(port), 'MODARENA_STATE': state},
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


def mcp(base, tool, args=None):
    _, out = post(base, '/mcp', {
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': tool, 'arguments': args or {}},
    })
    result = out['result']
    if result.get('isError'):
        raise AssertionError(result['content'][0]['text'])
    return result['structuredContent']


# ── the registry ─────────────────────────────────────────────────────────

def test_the_example_pack_is_planted_and_typed_by_its_exports(arena):
    _, out = get(arena, '/modules')
    roles = {m['name']: m['role'] for m in out['modules']}
    assert roles.get('ttt') == 'game', roles
    assert roles.get('rps') == 'game'
    assert roles.get('bot-ttt') == 'player'
    assert roles.get('hello') == 'command'
    # A model is not a special case; it is a module with no arena exports.
    assert roles.get('mlp') == 'wasm'


def test_the_parser_reports_what_a_module_needs_from_the_host(arena):
    _, hello = get(arena, '/modules/hello')
    assert [h['namespace'] for h in hello['host_needs']] == ['wasi_snapshot_preview1']

    _, bot = get(arena, '/modules/bot-random')
    assert [h['namespace'] for h in bot['host_needs']] == ['arena']

    # Signatures come out of the binary, not out of a manifest.
    play = next(e for e in bot['info']['exports'] if e['name'] == 'play')
    assert play['signature'] == '(i32, i32, i32) -> i64'


def test_the_id_is_the_content_so_storing_twice_is_idempotent(arena):
    raw = open(os.path.join(EXAMPLES, 'nim.wasm'), 'rb').read()
    body = {'bytes': base64.b64encode(raw).decode(), 'name': 'nim-again'}
    _, first = post(arena, '/modules', body)
    _, again = post(arena, '/modules', body)
    assert first['id'] == again['id']
    assert len(first['id']) == 64

    _, listing = get(arena, '/modules')
    assert sum(1 for m in listing['modules'] if m['id'] == first['id']) == 1

    # And the name it already had stands — otherwise anyone could rename a game
    # out from under the players entered at it by uploading a copy of it.
    assert first['name'] == 'nim'
    assert 'already stored' in first['note']
    assert get(arena, '/modules/nim')[0] == 200


def test_bytes_that_are_not_a_module_are_refused_rather_than_stored(arena):
    code, out = post(arena, '/modules', {'bytes': base64.b64encode(b'(module)').decode()})
    assert code == 400
    # Two readers, and neither of them recognised it — the error says both.
    assert 'wasm module' in out['error'] and 'Python source' in out['error']


def test_a_module_resolves_by_name_full_id_or_unambiguous_prefix(arena):
    _, by_name = get(arena, '/modules/ttt')
    _, by_id = get(arena, '/modules/' + by_name['id'])
    _, by_prefix = get(arena, '/modules/' + by_name['id'][:12])
    assert by_name['id'] == by_id['id'] == by_prefix['id']

    code, _ = get(arena, '/modules/no-such-module')
    assert code == 404


def test_the_blob_endpoint_serves_the_exact_bytes(arena):
    _, m = get(arena, '/modules/mlp')
    r = requests.get(f'{arena}/blob/{m["id"]}', timeout=30)
    assert r.headers['content-type'] == 'application/wasm'
    assert r.content[:4] == b'\0asm'
    assert len(r.content) == m['size']
    # Content-addressed, so it can be cached forever.
    assert 'immutable' in r.headers.get('cache-control', '')


def test_inspect_describes_without_storing(arena):
    raw = open(os.path.join(EXAMPLES, 'rps.wasm'), 'rb').read()
    _, out = post(arena, '/inspect', {'bytes': base64.b64encode(raw).decode()})
    assert out['role'] == 'game'
    assert out['stored'] is True   # the pack is already in


# ── players ──────────────────────────────────────────────────────────────

def test_entering_a_player_validates_the_driver_up_front(arena):
    code, out = post(arena, '/players', {'name': 'bad', 'kind': 'wasm', 'config': {}})
    assert code == 400 and 'config.module' in out['error']

    # A game module is not a player module, and saying so now beats failing
    # three turns into a match.
    code, out = post(arena, '/players', {'name': 'bad', 'kind': 'wasm',
                                         'config': {'module': 'ttt'}})
    assert code == 400 and 'not a player' in out['error']

    code, out = post(arena, '/players', {'name': 'bad', 'kind': 'model', 'config': {}})
    assert code == 400 and 'config.model' in out['error']


def test_a_player_card_never_carries_the_key(arena):
    post(arena, '/players', {'name': 'keyed', 'kind': 'model',
                             'config': {'model': 'a/b', 'key': 'sk-secret',
                                        'headers': {'x': 'y'}}})
    _, card = get(arena, '/players/keyed')
    assert card['config']['model'] == 'a/b'
    assert card['config']['key'] == '···'
    assert card['config']['headers'] == '···'
    assert 'sk-secret' not in json.dumps(card)


def test_re_entering_a_name_keeps_the_record(arena):
    post(arena, '/players', {'name': 'steady', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})
    _, before = get(arena, '/players/steady')
    post(arena, '/players', {'name': 'steady', 'kind': 'wasm',
                             'config': {'module': 'bot-ttt'}, 'note': 'switched'})
    _, after = get(arena, '/players/steady')
    assert after['id'] == before['id']
    assert after['note'] == 'switched'


# ── execution ────────────────────────────────────────────────────────────

def run_module(base, module, *args, entry=None, stdin=''):
    cmd = ['node', RUNNER, 'run', '--base', base, '--module', module]
    if entry:
        cmd += ['--entry', entry]
    for a in args:
        cmd += ['--arg', str(a)]
    if stdin:
        cmd += ['--stdin', stdin]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_wasi_command_written_for_nobody_here_still_runs(arena):
    """The point of "anything wasm": hello.wasm knows nothing about the arena."""
    out = run_module(arena, 'hello', 'friend', stdin='a\nb')
    assert out['ok'], out
    assert out['stdout'].startswith('hello, friend')
    assert '2 line(s)' in out['stdout']
    assert out['stderr'].strip() == 'done'


def test_the_sandbox_gives_a_module_no_filesystem(arena):
    out = run_module(arena, 'hello')
    assert 'sandboxed' in out['stdout']
    assert 'readable' not in out['stdout']


def test_a_model_module_runs_and_gets_its_answers_right(arena):
    out = run_module(arena, 'mlp', entry='evaluate')
    report = json.loads(out['text'])
    assert report['task'] == 'xor'
    assert report['correct'] == report['of'] == 4

    one = run_module(arena, 'mlp', 1, 0, entry='predict')
    assert one['value'] > 0.9


def test_a_packed_return_is_read_back_as_text(arena):
    out = run_module(arena, 'markov', 11, 120, entry='generate')
    assert len(out['text']) >= 120
    # Seeded, so the same seed is the same text on any engine.
    assert run_module(arena, 'markov', 11, 120, entry='generate')['text'] == out['text']


# ── matches ──────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def bots(arena):
    post(arena, '/players', {'name': 'perfect', 'kind': 'wasm',
                             'config': {'module': 'bot-ttt'}})
    post(arena, '/players', {'name': 'dice', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})
    return ['perfect', 'dice']


def test_minimax_does_not_lose_a_solved_game_to_random(arena, bots):
    played = mcp(arena, 'run_match', {'game': 'ttt', 'players': bots, 'seed': 42})
    scores = {s['player_name']: s['score'] for s in played['seats']}
    assert scores['perfect'] >= scores['dice'], played['summary']
    assert played['rated'] is True


def test_a_match_moves_elo_and_the_move_is_zero_sum(arena, bots):
    played = mcp(arena, 'run_match', {'game': 'nim', 'players': bots, 'seed': 7})
    deltas = [s['delta'] for s in played['seats']]
    assert abs(sum(deltas)) < 0.01, deltas
    assert any(d != 0 for d in deltas)


def test_a_simultaneous_game_asks_both_seats_every_turn(arena, bots):
    played = mcp(arena, 'run_match', {'game': 'rps', 'players': bots, 'seed': 3})
    _, full = get(arena, f'/matches/{played["id"]}')
    by_turn = {}
    for t in full['turns']:
        by_turn.setdefault(t['turn'], set()).add(t['seat'])
    assert all(seats == {0, 1} for seats in by_turn.values()), by_turn


def test_the_transcript_records_what_was_seen_and_what_was_said(arena, bots):
    played = mcp(arena, 'run_match', {'game': 'ttt', 'players': bots, 'seed': 1})
    _, full = get(arena, f'/matches/{played["id"]}')
    first = full['turns'][0]
    assert 'Tic-tac-toe' in first['view']
    assert first['mv']
    assert first['legal'] is True
    # A match is its seed and its moves, which is what makes it replayable.
    assert full['seed'] == 1


def test_one_seat_is_practice_and_moves_nothing(arena, bots):
    before = get(arena, '/players/perfect')[1]['elo']
    played = mcp(arena, 'record_match', {
        'game': 'ttt', 'runtime': 'node',
        'seats': [{'player_id': 'perfect', 'score': 1.0, 'moves': 3}],
    })
    assert played['rated'] is False
    assert get(arena, '/players/perfect')[1]['elo'] == before


def test_a_refused_move_is_counted_against_the_player(arena):
    """The number that separates a model that can play from one that talks."""
    post(arena, '/players', {'name': 'waffler', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})
    mcp(arena, 'record_match', {
        'game': 'ttt', 'runtime': 'node',
        'seats': [
            {'player_id': 'waffler', 'score': 0.0, 'moves': 4, 'illegal': 3},
            {'player_id': 'perfect', 'score': 1.0, 'moves': 4, 'illegal': 0},
        ],
    })
    _, card = get(arena, '/players/waffler')
    assert card['illegal'] == 3
    assert card['illegal_rate'] == 0.75


def test_skill_is_kept_per_game_as_well_as_overall(arena, bots):
    _, card = get(arena, '/players/perfect')
    games = {g['game_name']: g['elo'] for g in card['by_game']}
    assert len(games) >= 2, games
    # The overall number is not any single game's number.
    assert card['games_played'] == len(games)

    _, board = get(arena, '/leaderboard', game='ttt')
    assert board['scope'] == 'ttt'
    assert board['players'][0]['elo'] >= board['players'][-1]['elo']


# ── classes ──────────────────────────────────────────────────────────────
# The second container. Everything below is the same registry, the same match
# loop and the same leaderboard as the wasm above — which is the claim worth
# testing, because if it were a second arena bolted on, the ratings would mean
# two different things.

A_GAME = """
class Countdown:
    \"\"\"Say a number lower than the last. Whoever cannot, loses.\"\"\"

    name = "countdown"
    players = 2

    def __init__(self, seed):
        self.at = 10 + seed % 3
        self.loser = None

    def view(self, seat):
        return f"The number is {self.at}. Legal moves: any integer below it."

    def step(self, moves):
        seat = 0 if 0 in moves and moves.get(0) else 1
        raw = str(moves.get(seat, "")).strip()
        try:
            said = int(raw)
        except ValueError:
            self.loser = seat
            return {seat: False, "note": f"seat {seat} said {raw!r}"}
        if said >= self.at:
            self.loser = seat
            return {seat: False}
        self.at = said
        if self.at <= 0:
            self.loser = 1 - seat
        return {seat: True}

    def done(self):
        return self.loser is not None

    def result(self):
        scores = [0, 0]
        scores[1 - self.loser] = 1
        return {"scores": scores, "summary": f"seat {self.loser} could not go lower"}
"""

A_PLAYER = """
class Decrement:
    \"\"\"Always says one less than whatever it was shown.\"\"\"

    name = "minus-one"

    def play(self, view, seat):
        digits = [int(w) for w in view.replace(".", " ").split() if w.isdigit()]
        return str(digits[0] - 1) if digits else "0"
"""


def test_the_class_pack_is_planted_and_typed_by_what_it_defines(arena):
    _, out = get(arena, '/classes')
    by_name = {m['name']: m for m in out['modules']}
    assert by_name['connect4']['role'] == 'game'
    assert by_name['blotto']['role'] == 'game'
    assert by_name['lucky']['role'] == 'player'
    # The role came out of the source, and the container is recorded.
    assert by_name['connect4']['lang'] == 'python'
    assert by_name['connect4']['class'] == 'ConnectFour'


def test_uploading_a_class_as_text_is_the_whole_act_of_making_a_game(arena):
    made = mcp(arena, 'put_class', {'source': A_GAME, 'name': 'countdown'})
    assert made['role'] == 'game'
    assert made['lang'] == 'python'
    # Read back with its source, because for a class the source is the card.
    _, card = get(arena, '/modules/countdown')
    assert 'class Countdown' in card['source']
    assert [e['name'] for e in card['info']['exports']] == \
        ['__init__', 'view', 'step', 'done', 'result']


def test_a_class_and_a_wasm_module_share_one_registry_and_one_id_rule(arena):
    once = mcp(arena, 'put_class', {'source': A_PLAYER, 'name': 'minus'})
    twice = mcp(arena, 'put_class', {'source': A_PLAYER, 'name': 'something-else'})
    assert once['id'] == twice['id']          # the id is the content
    assert twice['name'] == 'minus'           # so a re-upload cannot rename it
    assert 'the id is the content' in twice.get('note', '')


def test_a_class_that_is_neither_is_stored_and_told_what_it_lacks(arena):
    made = mcp(arena, 'put_class', {'source': 'class Half:\n    def view(self, seat):\n        return ""\n'})
    assert made['role'] == 'class'
    assert 'play' in made['note'] or 'step' in made['note']


def test_something_that_is_neither_wasm_nor_a_class_is_refused(arena):
    _, out = post(arena, '/classes', {'source': 'just some prose, no code in it at all'})
    assert 'class' in out['error']


def test_a_class_plays_a_class_and_the_leaderboard_does_not_care(arena):
    post(arena, '/players', {'name': 'centre', 'kind': 'class', 'config': {'module': 'center'}})
    post(arena, '/players', {'name': 'chance', 'kind': 'class', 'config': {'module': 'lucky'}})
    played = mcp(arena, 'run_match', {'game': 'connect4', 'players': ['centre', 'chance'],
                                      'seed': 4})
    assert played['rated'] is True
    scores = {s['player_name']: s['score'] for s in played['seats']}
    # One move of lookahead beats no lookahead at Connect Four.
    assert scores['centre'] > scores['chance'], played['summary']
    assert all(s['illegal'] == 0 for s in played['seats']), played


def test_a_class_player_sits_at_a_wasm_game(arena):
    """The point of one registry: the container of the game and the container
    of the player have nothing to do with each other."""
    post(arena, '/players', {'name': 'chance', 'kind': 'class', 'config': {'module': 'lucky'}})
    post(arena, '/players', {'name': 'perfect', 'kind': 'wasm', 'config': {'module': 'bot-ttt'}})
    played = mcp(arena, 'run_match', {'game': 'ttt', 'players': ['chance', 'perfect'],
                                      'seed': 8})
    assert played['rated'] is True
    by_name = {s['player_name']: s for s in played['seats']}
    # It read the wasm game's view well enough to answer legally every time.
    assert by_name['chance']['illegal'] == 0, played
    assert by_name['chance']['moves'] > 0


def test_entering_a_class_says_which_container_it_really_is(arena):
    """Typed `wasm` by hand, but the module is a class — the module wins."""
    _, card = post(arena, '/players', {'name': 'mislabelled', 'kind': 'wasm',
                                       'config': {'module': 'lucky'}})
    assert card['kind'] == 'class'

    _, refused = post(arena, '/players', {'name': 'nope', 'kind': 'class',
                                          'config': {'module': 'connect4'}})
    assert 'not a player' in refused['error']


def test_a_class_is_asked_the_same_question_a_model_would_be(arena):
    post(arena, '/players', {'name': 'chance', 'kind': 'class', 'config': {'module': 'lucky'}})
    post(arena, '/players', {'name': 'centre', 'kind': 'class', 'config': {'module': 'center'}})
    played = mcp(arena, 'run_match', {'game': 'connect4', 'players': ['chance', 'centre'],
                                      'seed': 2})
    _, full = get(arena, f'/matches/{played["id"]}')
    first = full['turns'][0]
    assert 'Connect Four' in first['view']
    assert 'Legal moves:' in first['view']
    assert first['mv'] in [str(c) for c in range(7)]


def test_a_class_match_replays_from_its_seed(arena):
    post(arena, '/players', {'name': 'chance', 'kind': 'class', 'config': {'module': 'lucky'}})
    post(arena, '/players', {'name': 'chance2', 'kind': 'class', 'config': {'module': 'lucky'}})
    args = {'game': 'connect4', 'players': ['chance', 'chance2'], 'seed': 99}
    first = mcp(arena, 'run_match', args)
    second = mcp(arena, 'run_match', args)
    moves = []
    for played in (first, second):
        _, full = get(arena, f'/matches/{played["id"]}')
        moves.append([t['mv'] for t in full['turns']])
    # `random` is seeded from the match seed, so two runs are one computation.
    assert moves[0] == moves[1], moves


def test_the_sandbox_gives_a_class_no_filesystem_and_no_network(arena):
    reader = 'class Peeker:\n    def play(self, view, seat):\n        return open("/etc/passwd").read()\n'
    mcp(arena, 'put_class', {'source': reader, 'name': 'peeker'})
    out = run_module(arena, 'peeker', 'a view', 0, entry='play')
    assert out['ok'] is False
    assert 'open' in out['error']

    dialer = 'import socket\nclass Dialer:\n    def play(self, view, seat):\n        return ""\n'
    mcp(arena, 'put_class', {'source': dialer, 'name': 'dialer'})
    out = run_module(arena, 'dialer', entry='play')
    assert out['ok'] is False
    assert 'socket' in out['error']


def test_a_class_that_never_returns_is_killed_rather_than_hanging_the_match(arena):
    spinner = 'class Spinner:\n    def play(self, view, seat):\n        while True:\n            pass\n'
    mcp(arena, 'put_class', {'source': spinner, 'name': 'spinner'})
    post(arena, '/players', {'name': 'spinner', 'kind': 'class', 'config': {'module': 'spinner'}})
    post(arena, '/players', {'name': 'centre', 'kind': 'class', 'config': {'module': 'center'}})
    played = mcp(arena, 'run_match', {'game': 'connect4', 'players': ['centre', 'spinner'],
                                      'seed': 6, 'turns': 4})
    stuck = [s for s in played['seats'] if s['player_name'] == 'spinner'][0]
    assert stuck['timeouts'] > 0 or stuck['error'], played


def test_the_template_the_arena_hands_out_is_itself_a_game(arena):
    """The starting point we print has to be one that works, or the first
    thing a newcomer does is debug our documentation."""
    for role in ('game', 'player'):
        abi = mcp(arena, 'game_abi', {'role': role, 'lang': 'class'})
        made = mcp(arena, 'put_class', {'source': abi['template'],
                                        'name': f'template-{role}'})
        assert made['role'] == role, made


# ── the MCP surface ──────────────────────────────────────────────────────

def test_mcp_handshakes_and_lists_its_tools(arena):
    _, out = post(arena, '/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                  'params': {'protocolVersion': '2025-06-18'}})
    assert out['result']['serverInfo']['name'] == 'arena'

    _, out = post(arena, '/mcp', {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
    names = {t['name'] for t in out['result']['tools']}
    assert {'put_module', 'run_match', 'leaderboard', 'game_abi'} <= names


def test_rest_and_mcp_are_the_same_capability(arena):
    _, rest = get(arena, '/modules', role='game')
    tool = mcp(arena, 'list_modules', {'role': 'game'})
    assert rest == tool


def test_an_unknown_tool_says_what_the_arena_actually_does(arena):
    _, out = post(arena, '/mcp', {
        'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
        'params': {'name': 'no_such_tool', 'arguments': {}},
    })
    assert out['result']['isError'] is True
    assert 'game_init' in out['result']['content'][0]['text']


def test_the_abi_is_documented_at_runtime(arena):
    """An agent that wants to write a game can read the contract from the
    server rather than from this repository."""
    abi = mcp(arena, 'game_abi', {'role': 'game'})
    assert set(abi['required_exports']) == {
        'game_init(seed: i32) -> i64',
        'game_view(state_ptr, state_len, seat: i32) -> i64',
        'game_step(state_ptr, state_len, moves_ptr, moves_len) -> i64',
        'game_done(state_ptr, state_len) -> i32',
        'game_result(state_ptr, state_len) -> i64',
    }
    assert 'play' in json.dumps(mcp(arena, 'game_abi', {'role': 'player'}))


def test_deleting_a_module_someone_plays_with_is_refused(arena, bots):
    r = requests.delete(f'{arena}/modules/bot-ttt', timeout=30)
    assert r.status_code == 400
    assert 'perfect' in r.json()['error']
    assert get(arena, '/modules/bot-ttt')[0] == 200
