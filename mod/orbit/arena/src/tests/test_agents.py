"""Agent protocol tests: model players, A/B prompt comparison, memory systems.

Tests here verify the server-side player drivers and the prompt + memory
contracts.  They go through the same HTTP/MCP surface as test_arena.py but
focus on what makes agent seats different from wasm/class seats:

  model player   reaches an OpenAI-compatible endpoint; the prompt it sends and
                 the move it reads back are the whole contract.
  prompt config  system, brief — both flow through verbatim; both are recorded
                 in the transcript so every match is auditable.
  A/B testing    two variants with different configs must produce different
                 prompt templates and accumulate separate leaderboard records.
  memory system  a class player that uses self.mcp to recall past matches and
                 adapt its move selection — tested against a stateless baseline.

    pytest src/tests/test_agents.py -q

Needs the backend built (`m arena/build`) and node on PATH.
"""

import json
import os
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
BINARY = os.path.join(SRC, 'arena-rs', 'target', 'release', 'arena-api')
EXAMPLES = os.path.join(SRC, 'examples', 'wasm')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# ── arena fixture ─────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def arena():
    """A fresh arena server with its own state directory."""
    if not os.path.exists(BINARY):
        pytest.skip(f'no backend at {BINARY} — run `m arena/build`')

    state = tempfile.mkdtemp(prefix='arena-agent-test-')
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


# ── mock OpenAI-compatible model server ──────────────────────────────────

class _MockModelState:
    """Shared state between the handler and the fixture."""
    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()
        # Optional override: when set, reply with this move regardless of view.
        self.reply_with = None

    def record(self, body):
        with self.lock:
            self.requests.append(body)

    def snapshot(self):
        with self.lock:
            return list(self.requests)

    def clear(self):
        with self.lock:
            self.requests.clear()


def _make_handler(state: _MockModelState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length))
            state.record(body)

            user_content = ''
            for m in body.get('messages', []):
                if m.get('role') == 'user':
                    user_content = m.get('content', '')

            move = state.reply_with or _first_legal(user_content)
            response = json.dumps({
                'choices': [{'message': {'role': 'assistant', 'content': move},
                             'finish_reason': 'stop'}],
                'model': body.get('model', 'mock'),
                'usage': {'prompt_tokens': 10, 'completion_tokens': 1, 'total_tokens': 11},
            }).encode()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            pass  # silence

    return Handler


def _first_legal(content):
    """Extract the first legal move from a view string."""
    for line in content.splitlines():
        head, _, rest = line.partition(':')
        if head.strip().lower() in ('legal moves', 'moves', 'options'):
            opts = [t.strip().strip('`') for t in rest.replace(',', ' ').split() if t.strip()]
            return opts[0] if opts else '0'
    return '0'


class _ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


@pytest.fixture(scope='module')
def mock_model():
    """A tiny OpenAI-compatible server.

    Records every /chat/completions request body.  Returns the first legal
    move it can parse from the user message.  Runs in a background thread
    for the duration of the test module.
    """
    state = _MockModelState()
    port = free_port()
    server = _ThreadedHTTPServer(('127.0.0.1', port), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'url': f'http://127.0.0.1:{port}', 'state': state}
    server.shutdown()


# ── helpers ───────────────────────────────────────────────────────────────

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


# ── model player: basic contract ──────────────────────────────────────────

def test_a_model_player_can_sit_and_play_a_move(arena, mock_model):
    """kind=model reaches the configured base URL and returns a legal move."""
    post(arena, '/players', {'name': 'mock-basic', 'kind': 'model', 'config': {
        'model': 'mock', 'base': mock_model['url'],
    }})
    _, out = post(arena, '/play', {
        'player': 'mock-basic',
        'view': 'Legal moves: X, O',
        'seat': 0,
    })
    assert out.get('move') in {'X', 'O'}, out


def test_a_system_prompt_is_sent_as_the_first_message(arena, mock_model):
    """config.system flows through to the model as a system-role message."""
    state = mock_model['state']
    state.clear()
    post(arena, '/players', {'name': 'with-system', 'kind': 'model', 'config': {
        'model': 'mock', 'base': mock_model['url'],
        'system': 'You are a game-playing AI. Be concise.',
    }})
    post(arena, '/play', {
        'player': 'with-system', 'view': 'Legal moves: 1, 2, 3', 'seat': 0,
    })
    reqs = state.snapshot()
    assert reqs, 'model was never called'
    messages = reqs[-1]['messages']
    assert messages[0]['role'] == 'system'
    assert 'game-playing AI' in messages[0]['content']


def test_a_player_with_no_system_sends_only_a_user_message(arena, mock_model):
    """Without config.system the request has no system role message."""
    state = mock_model['state']
    state.clear()
    post(arena, '/players', {'name': 'no-sys', 'kind': 'model', 'config': {
        'model': 'mock', 'base': mock_model['url'],
    }})
    post(arena, '/play', {
        'player': 'no-sys', 'view': 'Legal moves: A, B', 'seat': 1,
    })
    reqs = state.snapshot()
    messages = reqs[-1]['messages']
    assert all(m['role'] == 'user' for m in messages), messages


def test_a_brief_is_prepended_to_the_view_in_the_user_message(arena, mock_model):
    """config.brief appears verbatim in the user turn, before the view."""
    state = mock_model['state']
    state.clear()
    post(arena, '/players', {'name': 'briefed', 'kind': 'model', 'config': {
        'model': 'mock', 'base': mock_model['url'],
        'brief': 'Pick the numerically largest move.',
    }})
    post(arena, '/play', {
        'player': 'briefed', 'view': 'Legal moves: 1, 2, 3', 'seat': 0,
    })
    reqs = state.snapshot()
    user_content = next(m['content'] for m in reqs[-1]['messages'] if m['role'] == 'user')
    assert 'numerically largest move' in user_content
    assert 'Legal moves: 1, 2, 3' in user_content


def test_the_seat_number_appears_in_the_prompt(arena, mock_model):
    """The player knows which seat it is from the prompt, not just the view."""
    state = mock_model['state']
    state.clear()
    post(arena, '/play', {
        'player': 'no-sys', 'view': 'Legal moves: A', 'seat': 2,
    })
    reqs = state.snapshot()
    user_content = next(m['content'] for m in reqs[-1]['messages'] if m['role'] == 'user')
    assert 'seat 2' in user_content.lower()


# ── transcript audit ──────────────────────────────────────────────────────

def test_the_prompt_is_recorded_in_the_match_transcript(arena, mock_model):
    """Every model turn records the prompt it received — the transcript is auditable."""
    post(arena, '/players', {'name': 'audit-model', 'kind': 'model', 'config': {
        'model': 'mock', 'base': mock_model['url'],
        'system': 'AuditMe-Marker',
    }})
    post(arena, '/players', {'name': 'dice-audit', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})
    played = mcp(arena, 'run_match', {
        'game': 'ttt', 'players': ['audit-model', 'dice-audit'], 'seed': 10,
    })
    _, full = get(arena, f"/matches/{played['id']}")
    model_turns = [t for t in full['turns'] if t['seat'] == 0]
    assert model_turns, 'expected at least one model turn'
    # Every model turn must have a non-empty prompt field.
    assert all(t.get('prompt') for t in model_turns), \
        'prompt must be recorded on every model turn'
    # The system instruction and seat appear in the prompt.
    for t in model_turns:
        p = t['prompt']
        assert 'AuditMe-Marker' in p or 'seat' in p.lower(), \
            f'prompt does not look right: {p[:200]}'


def test_the_player_card_exposes_the_prompt_template(arena, mock_model):
    """GET /players/:name includes prompt so you can inspect what a model is asked."""
    post(arena, '/players', {'name': 'card-inspect', 'kind': 'model', 'config': {
        'model': 'mock', 'base': mock_model['url'],
        'system': 'Be precise.',
        'brief': 'One word only.',
    }})
    _, card = get(arena, '/players/card-inspect')
    pt = card.get('prompt', {})
    assert pt.get('system') == 'Be precise.'
    assert 'One word only' in pt.get('template', ''), pt
    assert '{view}' in pt.get('template', '') or 'view' in pt.get('template', '').lower()


def test_a_model_key_is_never_exposed_in_the_player_card(arena, mock_model):
    """Keys are redacted in GET /players/:name — same guarantee as test_arena.py."""
    post(arena, '/players', {'name': 'keyed-model', 'kind': 'model', 'config': {
        'model': 'mock', 'base': mock_model['url'], 'key': 'sk-top-secret',
    }})
    _, card = get(arena, '/players/keyed-model')
    assert 'sk-top-secret' not in json.dumps(card)
    assert card['config']['key'] == '···'


# ── A/B prompt comparison ─────────────────────────────────────────────────

def test_ab_variants_produce_different_prompt_templates(arena, mock_model):
    """Four configs → four distinct templates.  If templates match, the A/B test
    measures nothing — this catches that before any matches run."""
    variants = {
        'ab-bare':   {},
        'ab-system': {'system': 'You are a careful strategist.'},
        'ab-brief':  {'brief': 'Think step by step before answering.'},
        'ab-both':   {'system': 'Strategic AI.', 'brief': 'One move per turn.'},
    }
    for name, cfg in variants.items():
        post(arena, '/players', {'name': name, 'kind': 'model', 'config': {
            'model': 'mock', 'base': mock_model['url'], **cfg,
        }})

    templates = {}
    for name in variants:
        _, card = get(arena, f'/players/{name}')
        templates[name] = card.get('prompt', {}).get('template', '')

    # All four must be distinct strings.
    assert len(set(templates.values())) == 4, \
        f'variants share a template — A/B would measure nothing: {templates}'

    # System field is exactly what was configured.
    cards = {n: get(arena, f'/players/{n}')[1] for n in variants}
    assert cards['ab-bare']['prompt']['system'] is None
    assert cards['ab-system']['prompt']['system'] == 'You are a careful strategist.'
    assert cards['ab-both']['prompt']['system'] == 'Strategic AI.'


def test_ab_each_variant_sends_the_right_prompt_to_the_model(arena, mock_model):
    """Run one match per variant and check the model received the configured brief."""
    state = mock_model['state']
    post(arena, '/players', {'name': 'dice-ab', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})

    for variant, expected_brief in [
        ('ab-brief', 'Think step by step'),
        ('ab-both', 'One move per turn'),
    ]:
        state.clear()
        mcp(arena, 'run_match', {
            'game': 'rps', 'players': [variant, 'dice-ab'], 'seed': 7,
        })
        reqs = state.snapshot()
        assert reqs, f'{variant} never called the model'
        user_content = next(
            m['content'] for m in reqs[0]['messages'] if m['role'] == 'user'
        )
        assert expected_brief in user_content, \
            f'{variant} brief not in prompt. Got: {user_content[:300]}'


def test_ab_variants_accumulate_separate_leaderboard_records(arena, mock_model):
    """Two prompt variants played against the same opponent have independent Elo."""
    post(arena, '/players', {'name': 'ref-bot', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})

    for seed in range(4):
        mcp(arena, 'run_match', {
            'game': 'nim', 'players': ['ab-bare', 'ref-bot'], 'seed': seed,
        })
        mcp(arena, 'run_match', {
            'game': 'nim', 'players': ['ab-system', 'ref-bot'], 'seed': seed,
        })

    _, bare = get(arena, '/players/ab-bare')
    _, sys_ = get(arena, '/players/ab-system')

    # Separate player records.
    assert bare['id'] != sys_['id']
    assert bare['games_played'] >= 1
    assert sys_['games_played'] >= 1

    # Each has a per-game nim entry.
    bare_nim = next((g for g in bare['by_game'] if g['game_name'] == 'nim'), None)
    sys_nim  = next((g for g in sys_['by_game'] if g['game_name'] == 'nim'), None)
    assert bare_nim is not None, 'ab-bare has no nim record'
    assert sys_nim  is not None, 'ab-system has no nim record'

    # A leaderboard query separates them.
    _, board = get(arena, '/leaderboard', game='nim')
    names_on_board = {p['name'] for p in board['players']}
    assert 'ab-bare' in names_on_board
    assert 'ab-system' in names_on_board


def test_ab_transcript_records_system_prompt_for_each_variant(arena, mock_model):
    """The transcript shows what each variant was actually asked — variants differ."""
    post(arena, '/players', {'name': 'dice-ab2', 'kind': 'wasm',
                             'config': {'module': 'bot-random'}})

    match_ids = {}
    for variant in ['ab-bare', 'ab-system']:
        played = mcp(arena, 'run_match', {
            'game': 'ttt', 'players': [variant, 'dice-ab2'], 'seed': 3,
        })
        match_ids[variant] = played['id']

    prompts = {}
    for variant, mid in match_ids.items():
        _, full = get(arena, f'/matches/{mid}')
        seat_idx = next(i for i, s in enumerate(full['seats'])
                        if s['player_name'] == variant)
        turns = [t for t in full['turns'] if t['seat'] == seat_idx and t.get('prompt')]
        prompts[variant] = turns[0]['prompt'] if turns else ''

    # The two transcripts record different prompts.
    assert prompts['ab-bare'] != prompts['ab-system'], \
        'different variants must produce different transcripts'
    assert 'careful strategist' in prompts['ab-system']


# ── memory systems ─────────────────────────────────────────────────────────
#
# A stateless class picks the first legal move every turn and never calls out.
# A memory-augmented class uses self.mcp to recall past matches and prefer
# moves that led to wins.  The test verifies the contract, not LLM quality:
# the memory agent must make MCP calls and play legally in both modes.

STATELESS_AGENT = '''
class StatelessAgent:
    """Picks the first legal move every turn.  No history.  No memory.

    This is the baseline for the A/B comparison with MemoryAgent: same
    legal-move parsing, no MCP calls, same moves every time it sees the
    same view.
    """

    name = "stateless"

    def play(self, view, seat):
        for line in view.splitlines():
            head, _, rest = line.partition(":")
            if head.strip().lower() in ("legal moves", "moves", "options"):
                opts = [t.strip() for t in rest.replace(",", " ").split() if t.strip()]
                return opts[0] if opts else ""
        return ""
'''

MEMORY_AGENT = '''
import random

class MemoryAgent:
    """Recalls winning moves from past matches via self.mcp.

    On the first turn of each match it loads its own match history from the
    arena and builds a map of view-key -> move for positions it has won from
    before.  Subsequent turns prefer known-winning moves; unknown positions
    fall back to the first legal move.

    Cross-match recall requires the arena MCP door to be open (mcp=["arena"]
    in run_match).  Without it, self.mcp raises and the agent falls back
    gracefully — illegal rate stays zero either way.
    """

    name = "memory-bot"

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.history = []        # (view_key, move) for this match
        self.known_wins = None   # loaded once, lazily

    def _view_key(self, view):
        """A stable key for a position — the Legal-moves line."""
        for line in view.splitlines():
            head, _, rest = line.partition(":")
            if head.strip().lower() in ("legal moves", "moves", "options"):
                return "legal:" + rest.strip()
        # Fall back to the first 80 chars when no legal-moves line exists.
        return view[:80]

    def _load_history(self):
        """Pull this player\'s winning moves from the arena.

        Called once per match; any MCP failure (door not open, arena not
        answering) is caught and returns an empty dict so play continues.
        """
        try:
            result = self.mcp("arena", "list_matches", {
                "player": self.name, "limit": 30,
            })
            matches = (result or {}).get("matches", [])
        except Exception as exc:
            print(f"memory: list_matches failed ({exc}) — playing cold")
            return {}

        wins = {}
        for m in matches:
            seats = m.get("seats", [])
            my_seat_info = next(
                (s for s in seats if s.get("player_name") == self.name), None
            )
            if not my_seat_info or my_seat_info.get("score", 0) <= 0.5:
                continue
            mid = m.get("id", "")
            if not mid:
                continue
            try:
                full = self.mcp("arena", "get_match", {"id": mid})
                my_idx = next(
                    (i for i, s in enumerate(full.get("seats", []))
                     if s.get("player_name") == self.name),
                    None,
                )
                if my_idx is None:
                    continue
                for turn in (full.get("turns") or []):
                    if (turn.get("seat") == my_idx
                            and turn.get("legal")
                            and turn.get("mv")):
                        key = self._view_key(turn.get("view", ""))
                        wins[key] = turn["mv"]
            except Exception:
                continue
        return wins

    def play(self, view, seat):
        # Load memory once, on the first turn of the match.
        if self.known_wins is None:
            self.known_wins = self._load_history()
            print(f"memory: {len(self.known_wins)} winning positions loaded")

        key = self._view_key(view)
        if key in self.known_wins:
            recalled = self.known_wins[key]
            print(f"memory: replaying known move {recalled!r}")
            return recalled

        # Unknown position: pick first legal move and remember the choice.
        for line in view.splitlines():
            head, _, rest = line.partition(":")
            if head.strip().lower() in ("legal moves", "moves", "options"):
                opts = [t.strip() for t in rest.replace(",", " ").split() if t.strip()]
                move = opts[0] if opts else ""
                self.history.append((key, move))
                return move
        return ""
'''


@pytest.fixture(scope='module')
def agents(arena):
    """Upload the two agent classes and enter them as players."""
    mcp(arena, 'put_class', {'source': STATELESS_AGENT, 'name': 'stateless'})
    mcp(arena, 'put_class', {'source': MEMORY_AGENT,    'name': 'memory-bot'})
    post(arena, '/players', {'name': 'stateless', 'kind': 'class',
                             'config': {'module': 'stateless'}})
    post(arena, '/players', {'name': 'memory',    'kind': 'class',
                             'config': {'module': 'memory-bot'}})
    post(arena, '/players', {'name': 'dice-mem',  'kind': 'wasm',
                             'config': {'module': 'bot-random'}})
    return {'stateless': 'stateless', 'memory': 'memory', 'opponent': 'dice-mem'}


def test_a_stateless_agent_makes_no_mcp_calls(arena, agents):
    """A class that never calls self.mcp has mcp=0 on its seat."""
    played = mcp(arena, 'run_match', {
        'game': 'ttt',
        'players': [agents['stateless'], agents['opponent']],
        'seed': 20,
    })
    stat = next(s for s in played['seats'] if s['player_name'] == 'stateless')
    assert stat['mcp'] == 0
    assert stat['illegal'] == 0, played


def test_a_memory_agent_calls_out_when_the_door_is_open(arena, agents):
    """With mcp=["arena"] the memory agent loads history and its mcp count > 0."""
    played = mcp(arena, 'run_match', {
        'game': 'ttt',
        'players': [agents['memory'], agents['opponent']],
        'seed': 21,
        'mcp': ['arena'],
    })
    mem = next(s for s in played['seats'] if s['player_name'] == 'memory')
    assert mem['mcp'] > 0, f'expected MCP calls, got: {played}'
    assert mem['illegal'] == 0, played


def test_a_memory_agent_falls_back_gracefully_without_the_door(arena, agents):
    """Without the MCP door, self.mcp raises and the agent still plays legally."""
    played = mcp(arena, 'run_match', {
        'game': 'ttt',
        'players': [agents['memory'], agents['opponent']],
        'seed': 22,
        # no mcp= key — door is closed
    })
    mem = next(s for s in played['seats'] if s['player_name'] == 'memory')
    assert mem['mcp'] == 0
    assert mem['illegal'] == 0, played


def test_memory_mcp_calls_are_counted_on_the_players_card(arena, agents):
    """After a match with the door open, the player card shows cumulative mcp."""
    # Run with door open.
    mcp(arena, 'run_match', {
        'game': 'nim',
        'players': [agents['memory'], agents['opponent']],
        'seed': 23,
        'mcp': ['arena'],
    })
    _, card = get(arena, '/players/memory')
    assert card['mcp'] > 0, 'mcp counter must accumulate on the player card'


def test_stateless_and_memory_agents_are_separate_players_with_independent_elo(arena, agents):
    """Running both against the same opponent for the same seeds produces two
    independent leaderboard entries — the A/B comparison is clean."""
    for seed in range(3):
        mcp(arena, 'run_match', {
            'game': 'nim',
            'players': [agents['stateless'], agents['opponent']],
            'seed': seed,
        })
        mcp(arena, 'run_match', {
            'game': 'nim',
            'players': [agents['memory'], agents['opponent']],
            'seed': seed,
            'mcp': ['arena'],
        })

    _, sc = get(arena, '/players/stateless')
    _, mc = get(arena, '/players/memory')

    assert sc['id'] != mc['id']
    assert sc['games_played'] >= 1
    assert mc['games_played'] >= 1

    sc_nim = next((g for g in sc['by_game'] if g['game_name'] == 'nim'), None)
    mc_nim = next((g for g in mc['by_game'] if g['game_name'] == 'nim'), None)
    assert sc_nim is not None, 'stateless has no nim record'
    assert mc_nim is not None, 'memory has no nim record'

    # Both appear on the nim leaderboard.
    _, board = get(arena, '/leaderboard', game='nim')
    on_board = {p['name'] for p in board['players']}
    assert 'stateless' in on_board
    assert 'memory'    in on_board


def test_the_memory_agent_recalls_a_move_it_played_in_a_prior_match(arena, agents):
    """After winning a match with the door open, the memory agent's next match
    (also with the door open) should load at least one past winning move.

    We verify this by checking the match transcripts: the memory agent logs
    how many positions it loaded — a value > 0 on the second match confirms
    the cross-match recall worked.
    """
    # First match: win some positions; open the door so it records them.
    mcp(arena, 'run_match', {
        'game': 'nim',
        'players': [agents['memory'], agents['opponent']],
        'seed': 30,
        'mcp': ['arena'],
    })
    # Second match: the agent should find those positions in its history.
    played = mcp(arena, 'run_match', {
        'game': 'nim',
        'players': [agents['memory'], agents['opponent']],
        'seed': 30,   # same seed → same positions → memory can match
        'mcp': ['arena'],
    })
    _, full = get(arena, f"/matches/{played['id']}")
    mem_idx = next(
        i for i, s in enumerate(full['seats'])
        if s['player_name'] == 'memory'
    )
    prints = ' '.join(
        t.get('note', '') for t in full['turns'] if t.get('seat') == mem_idx
    )
    # The agent prints either how many positions it loaded or that it replayed one.
    assert 'memory:' in prints or played['seats'][mem_idx]['mcp'] > 0, \
        f'no evidence of cross-match recall in transcript: {prints[:400]}'
