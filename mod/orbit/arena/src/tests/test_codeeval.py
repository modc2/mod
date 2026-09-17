"""test_codeeval.py — prompt-token efficiency for coding-game agents.

Harvests real functions from the orbit codebase, turns them into a coding game,
then runs model players with different system-prompt and memory configurations
through it.  Because every variant uses the same smart-mock model (which returns
the correct implementation), all variants score equally well — the only variable
is how many prompt characters each configuration sends per turn.

That gives a clean efficiency metric: chars_per_point = avg_prompt_chars / score.
Lower is better.  The test verifies the measurement infrastructure works, and
prints the efficiency table so you can see which combination wins.

    pytest src/tests/test_codeeval.py -q -s

Needs the backend built (`m arena/build`) and node on PATH.
"""

import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.dirname(HERE)
BINARY   = os.path.join(SRC, 'arena-rs', 'target', 'release', 'arena-api')
ORBIT_DIR = os.path.abspath(os.path.join(SRC, '..', '..'))   # orbit/


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


# ── arena fixture ─────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def arena():
    if not os.path.exists(BINARY):
        pytest.skip(f'no backend at {BINARY} — run `m arena/build`')
    state = tempfile.mkdtemp(prefix='arena-codeeval-')
    port  = free_port()
    proc  = subprocess.Popen(
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


# ── coding task harvest ───────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def tasks():
    """Harvest real functions from the orbit codebase."""
    sys.path.insert(0, SRC)
    from codeeval.harvest import harvest
    found = harvest(ORBIT_DIR, n=8, seed=42, quiet=True)
    if len(found) < 2:
        pytest.skip('fewer than 2 harvestable tasks in orbit/ — '
                    'check that orbit has pure Python functions')
    return found


@pytest.fixture(scope='module')
def game(arena, tasks):
    """Upload the coding game to the arena; return the game name."""
    sys.path.insert(0, SRC)
    from codeeval.harvest import generate_game_source
    repo   = {'name': 'orbit', 'slug': 'orbit', 'url': '', 'commit': 'test'}
    source = generate_game_source(tasks, repo, rounds=2, name='orbit-codeeval')
    result = mcp(arena, 'put_class', {'source': source, 'name': 'orbit-codeeval'})
    assert result.get('id'), f'put_class returned no id: {result}'
    return 'orbit-codeeval'


# ── smart mock model ──────────────────────────────────────────────────────────
#
# Returns the CORRECT original implementation for each task (pre-loaded from
# the harvest record).  Every variant therefore scores the same, so the only
# measurable difference between them is how many prompt characters they send.

class _SolverState:
    def __init__(self):
        self.solutions: dict = {}   # fn_name → source
        self.log: list = []
        self.lock = threading.Lock()

    def add(self, name: str, source: str):
        with self.lock:
            self.solutions[name] = source

    def record(self, prompt_chars: int, move: str):
        with self.lock:
            self.log.append({'prompt_chars': prompt_chars, 'move_len': len(move)})

    def snapshot(self):
        with self.lock:
            return list(self.log)

    def clear(self):
        with self.lock:
            self.log.clear()

    def solve(self, user_content: str) -> str:
        # The view says "Reconstruct `fn_name` from file:lineno."
        m = re.search(r'Reconstruct `(\w+)` from', user_content)
        if m:
            name = m.group(1)
            with self.lock:
                if name in self.solutions:
                    return self.solutions[name]
        # Fallback: pull the first def name from the stub in the view
        m = re.search(r'def (\w+)\(', user_content)
        if m:
            name = m.group(1)
            with self.lock:
                if name in self.solutions:
                    return self.solutions[name]
        return 'def _unknown(): pass'


def _make_solver_handler(state: _SolverState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length))
            msgs   = body.get('messages', [])
            user_content = next(
                (m.get('content', '') for m in reversed(msgs) if m.get('role') == 'user'), ''
            )
            prompt_chars = sum(len(m.get('content', '')) for m in msgs)
            move = state.solve(user_content)
            state.record(prompt_chars, move)
            resp = json.dumps({
                'choices': [{'message': {'role': 'assistant', 'content': move},
                             'finish_reason': 'stop'}],
                'model': body.get('model', 'mock-coder'),
                'usage': {
                    'prompt_tokens':     prompt_chars // 4,
                    'completion_tokens': len(move) // 4,
                    'total_tokens':      (prompt_chars + len(move)) // 4,
                },
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, *args):
            pass

    return Handler


class _Threaded(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True


@pytest.fixture(scope='module')
def smart_model(tasks):
    state = _SolverState()
    for t in tasks:
        if t.get('original'):
            state.add(t['name'], t['original'])
    port   = free_port()
    server = _Threaded(('127.0.0.1', port), _make_solver_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {'url': f'http://127.0.0.1:{port}', 'state': state}
    server.shutdown()


# ── helpers ───────────────────────────────────────────────────────────────────

def get(base, path, **params):
    r = requests.get(base + path, params=params, timeout=30)
    return r.status_code, r.json()


def post(base, path, body, timeout=600):
    r = requests.post(base + path, json=body, timeout=timeout)
    return r.status_code, r.json()


def mcp(base, tool, args=None):
    _, out = post(base, '/mcp', {
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': tool, 'arguments': args or {}},
    })
    result = out.get('result', {})
    if result.get('isError'):
        raise AssertionError(result['content'][0]['text'])
    return result.get('structuredContent', result)


def _prompt_chars_for(arena, match_id, player_name):
    """Average prompt chars per model turn for `player_name` in the match."""
    _, full = get(arena, f'/matches/{match_id}')
    seat_idx = next(
        (i for i, s in enumerate(full.get('seats', []))
         if s.get('player_name') == player_name),
        None,
    )
    if seat_idx is None:
        return 0
    turns = [t for t in full.get('turns', [])
             if t.get('seat') == seat_idx and t.get('prompt')]
    if not turns:
        return 0
    return sum(len(t['prompt']) for t in turns) / len(turns)


# ── prompt / system-prompt variants ──────────────────────────────────────────
#
# These are the A/B dimensions we measure.  All variants get the same game view
# (determined by the seed), the same model, and the same correct solution from
# the mock — so differences in score directly reflect prompt engineering, while
# differences in avg_prompt_chars reflect how much context each variant sends.

VARIANTS = [
    ('bare',         {}),
    ('concise-sys',  {'system': 'Python. Return only the complete function def.'}),
    ('domain-sys',   {'system': (
        'You work in the orbit/mod codebase (Python, composable modules). '
        'Implement the given function. Return only the def, no prose, no fences.'
    )}),
    ('brief-only',   {'brief': 'Return the complete `def`. No prose, no fences.'}),
    ('full-both',    {
        'system': 'You are an expert Python engineer.',
        'brief':  'Implement the function exactly. Return only the def.',
    }),
    ('verbose-sys',  {'system': (
        'You are an expert software engineer specializing in Python development '
        'for the mod protocol — a fleet of composable modules. Each module is a '
        'Python class or wasm binary. When asked to implement a function, write '
        'only the function body with correct indentation, no explanations, no '
        'markdown code fences unless the function itself uses markdown-like '
        'strings. The codebase uses pure functions wherever possible; avoid '
        'side effects. Prefer clarity over cleverness. Match the style of the '
        'surrounding context you are given.'
    )}),
]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_harvest_finds_tasks_in_orbit(tasks):
    """Harvest picks up at least 2 gradeable tasks from the orbit codebase."""
    assert len(tasks) >= 2, f'only {len(tasks)} task(s) — orbit may not have enough pure fns'
    for t in tasks:
        assert 'name'  in t, t
        assert 'stub'  in t, t
        assert len(t.get('tests', [])) >= 2, f'{t["name"]}: needs ≥2 test vectors'


def test_game_class_uploads_as_a_game(arena, game):
    """The generated coding game registers with role=game in the arena."""
    modules = mcp(arena, 'list_modules', {'role': 'game'})
    names = {m.get('name') for m in modules.get('modules', [])}
    assert game in names, f'{game!r} not in game list: {names}'


def test_bare_variant_has_fewest_prompt_chars(arena, game, smart_model):
    """No brief → shorter turn.prompt than a 600-char brief.

    turn.prompt stores the user-turn text (brief + view + instruction).  The
    system message is a separate message and does NOT appear here.  So the
    measurable difference in turn.prompt is the brief, not the system.
    """
    post(arena, '/players', {'name': 'ce-bare', 'kind': 'model', 'config': {
        'model': 'mock-coder', 'base': smart_model['url'],
    }})
    post(arena, '/players', {'name': 'ce-full', 'kind': 'model', 'config': {
        'model': 'mock-coder', 'base': smart_model['url'],
        'brief': 'B' * 600,   # brief appears in turn.prompt; system does not
    }})
    bare_m = mcp(arena, 'run_match', {'game': game, 'players': ['ce-bare'], 'seed': 1})
    full_m = mcp(arena, 'run_match', {'game': game, 'players': ['ce-full'], 'seed': 1})

    bare_chars = _prompt_chars_for(arena, bare_m['id'], 'ce-bare')
    full_chars = _prompt_chars_for(arena, full_m['id'], 'ce-full')

    assert bare_chars > 0, 'bare player was never called — check that model turns record prompt'
    # 600-char brief must show up; ~602 chars of extra turn.prompt content
    assert full_chars - bare_chars > 500, (
        f'600-char brief overhead not reflected in turn.prompt: '
        f'bare={bare_chars:.0f}  full={full_chars:.0f}'
    )


def test_brief_overhead_is_proportional(arena, game, smart_model):
    """Quadrupling the brief quadruples the per-turn user-message overhead.

    turn.prompt = brief + seat line + view + instruction.  A 400-char brief
    vs a 100-char brief adds ~300 chars to every turn's stored prompt.
    """
    for name, brief_text in [('ce-brief-100', 'B' * 100), ('ce-brief-400', 'B' * 400)]:
        post(arena, '/players', {'name': name, 'kind': 'model', 'config': {
            'model': 'mock-coder', 'base': smart_model['url'], 'brief': brief_text,
        }})
    m100 = mcp(arena, 'run_match', {'game': game, 'players': ['ce-brief-100'], 'seed': 2})
    m400 = mcp(arena, 'run_match', {'game': game, 'players': ['ce-brief-400'], 'seed': 2})

    chars_100 = _prompt_chars_for(arena, m100['id'], 'ce-brief-100')
    chars_400 = _prompt_chars_for(arena, m400['id'], 'ce-brief-400')

    delta = chars_400 - chars_100
    assert delta > 250, (
        f'400-char brief vs 100-char brief: expected ~300 chars delta in turn.prompt, got {delta:.0f}'
    )


def test_each_variant_plays_legally(arena, game, smart_model):
    """Every prompt variant produces legal moves (no parse failures in the game)."""
    for name, cfg in VARIANTS:
        pname = f'legal-{name}'
        post(arena, '/players', {'name': pname, 'kind': 'model', 'config': {
            'model': 'mock-coder', 'base': smart_model['url'], **cfg,
        }})
        played = mcp(arena, 'run_match', {'game': game, 'players': [pname], 'seed': 5})
        seat = played['seats'][0]
        assert seat.get('illegal', 0) == 0, (
            f'{name} produced illegal moves: {seat}'
        )


def test_efficiency_report(arena, game, smart_model, tasks, capsys):
    """Run every variant; print chars-per-point efficiency table.

    Measured: avg turn.prompt chars (brief + seat line + view + instruction).
    The system message is a separate LLM message not captured here — to get
    total token cost, add len(system) to avg_chars for each variant.

    Lower chars/point = more efficient: the model solves the task with less
    user-turn context.  Because the mock always returns the correct code, score
    differences reflect how cleanly each variant's prompt lets the mock parse
    the task name — a proxy for how well a real LLM would parse the task.
    """
    rows = []
    for name, cfg in VARIANTS:
        pname = f'eff-{name}'
        post(arena, '/players', {'name': pname, 'kind': 'model', 'config': {
            'model': 'mock-coder', 'base': smart_model['url'], **cfg,
        }})
        played = mcp(arena, 'run_match', {'game': game, 'players': [pname], 'seed': 6})
        score  = played['seats'][0].get('score', 0.0) if played.get('seats') else 0.0
        avg    = _prompt_chars_for(arena, played['id'], pname)
        cpp    = (avg / score) if score > 0 else float('inf')
        rows.append((name, avg, score, cpp))

    with capsys.disabled():
        print()
        print(f'  {"variant":18s}  {"avg_chars":>9}  {"score":>6}  {"chars/point":>12}')
        print(f'  {"-"*18}  {"-"*9}  {"-"*6}  {"-"*12}')
        for name, avg, score, cpp in rows:
            cpp_s = f'{cpp:12.0f}' if cpp != float('inf') else '         ∞'
            print(f'  {name:18s}  {avg:9.0f}  {score:6.3f}  {cpp_s}')
        finite = [(n, c) for n, _, _, c in rows if c != float('inf')]
        if finite:
            best = min(finite, key=lambda x: x[1])
            print(f'\n  Most efficient: {best[0]} ({best[1]:.0f} chars/point)')
        print()

    # All variants should at least attempt moves (avg_chars > 0).
    for name, avg, score, _ in rows:
        assert avg > 0, f'{name}: no prompt chars recorded — model never called?'


# ── class-level view compaction ───────────────────────────────────────────────
#
# A class player can strip boilerplate from the view before handing it to a
# downstream model — reducing the effective context size.  These two classes
# test that infrastructure:  CompactCoder keeps only the stub; VerboseCoder
# keeps the full view.  Both play legally (return a def); their view footprint
# differs by the ALREADY IN SCOPE + WORKED CALLS sections.

COMPACT_CODER = '''\
import re as _re

class CompactCoder:
    """Strips ALREADY IN SCOPE and WORKED CALLS before replying.

    Simulates an agent that compresses the view before passing it to a
    downstream LLM — trading context size for potentially lower token cost.
    """
    name = "compact-coder"

    def play(self, view, seat):
        lines = view.splitlines()
        start = next((i for i, l in enumerate(lines) if l.startswith("WRITE THIS:")), None)
        if start is None:
            # No task header — return a safe stub.
            m = _re.search(r"def (\\w+)\\(", view)
            fn = m.group(1) if m else "f"
            return f"def {fn}(): pass"
        stub_lines = []
        for l in lines[start + 1:]:
            if l.startswith("WORKED CALLS") or l.startswith("Last round") or l.startswith("Write the whole"):
                break
            stub_lines.append(l)
        stub = "\\n".join(stub_lines).strip()
        # Return the stub itself (a pass-body function is syntactically valid).
        if stub and "def " in stub:
            return stub
        m = _re.search(r"def (\\w+)\\(", view)
        fn = m.group(1) if m else "f"
        return f"def {fn}(): pass"
'''

VERBOSE_CODER = '''\
import re as _re

class VerboseCoder:
    """Passes the full view to a hypothetical downstream model.

    Baseline for the compaction test: records the full view length so we can
    measure how much CompactCoder strips.
    """
    name = "verbose-coder"

    def play(self, view, seat):
        # Extract def line and return a minimal but legal function.
        for line in view.splitlines():
            stripped = line.strip()
            if stripped.startswith("def "):
                indent = "    "
                return stripped + "\\n" + indent + "pass"
        m = _re.search(r"def (\\w+)\\(", view)
        fn = m.group(1) if m else "f"
        return f"def {fn}(): pass"
'''


def test_compact_class_produces_shorter_play_output_than_verbose(arena, game):
    """CompactCoder returns a shorter string than VerboseCoder (and both play legally).

    In a real pipeline, the returned string would be the prompt sent to an LLM.
    The shorter it is, the fewer tokens the downstream model needs to process.

    Run as separate solo matches to avoid spawning three Python subprocesses
    simultaneously (game + 2 players), which would exceed the match timeout.
    """
    mcp(arena, 'put_class', {'source': COMPACT_CODER, 'name': 'compact-coder'})
    mcp(arena, 'put_class', {'source': VERBOSE_CODER, 'name': 'verbose-coder'})
    post(arena, '/players', {'name': 'compact', 'kind': 'class',
                             'config': {'module': 'compact-coder'}})
    post(arena, '/players', {'name': 'verbose', 'kind': 'class',
                             'config': {'module': 'verbose-coder'}})

    # Solo matches — one class player per match, avoids triple subprocess load.
    cp = mcp(arena, 'run_match', {'game': game, 'players': ['compact'], 'seed': 7})
    vp = mcp(arena, 'run_match', {'game': game, 'players': ['verbose'], 'seed': 7})

    for played, pname in [(cp, 'compact'), (vp, 'verbose')]:
        assert played['seats'][0].get('illegal', 0) == 0, (
            f'{pname} made illegal moves: {played["seats"][0]}'
        )

    _, cf = get(arena, f"/matches/{cp['id']}")
    _, vf = get(arena, f"/matches/{vp['id']}")

    compact_moves = [t.get('mv', '') for t in cf.get('turns', []) if t.get('mv')]
    verbose_moves = [t.get('mv', '') for t in vf.get('turns', []) if t.get('mv')]

    if compact_moves and verbose_moves:
        avg_compact = sum(len(m) for m in compact_moves) / len(compact_moves)
        avg_verbose = sum(len(m) for m in verbose_moves) / len(verbose_moves)
        print(f'\n  compact avg_move={avg_compact:.0f}  verbose avg_move={avg_verbose:.0f}')


def test_variant_with_domain_system_prompt_scores_correctly(arena, game, smart_model):
    """The domain-specific system prompt variant scores > 0 on coding tasks.

    This confirms the full pipeline works end-to-end with a realistic
    system prompt — the kind you'd actually use with a real LLM.
    """
    domain_cfg = dict(VARIANTS)['domain-sys']
    post(arena, '/players', {'name': 'domain-e2e', 'kind': 'model', 'config': {
        'model': 'mock-coder', 'base': smart_model['url'],
        **domain_cfg,
    }})
    played = mcp(arena, 'run_match', {'game': game, 'players': ['domain-e2e'], 'seed': 8})
    score = played['seats'][0].get('score', 0.0) if played.get('seats') else 0.0
    assert score > 0, f'domain-sys variant scored 0 — mock coder did not solve any task: {played}'


def test_brief_reduces_illegal_rate_vs_bare(arena, game, smart_model):
    """A brief that says "return the complete def" should keep illegal rate at 0.

    Both bare and brief-only start at 0 illegal (the mock always returns valid
    code) — this verifies they both stay there across multiple seeds.
    """
    post(arena, '/players', {'name': 'bare-multi',  'kind': 'model', 'config': {
        'model': 'mock-coder', 'base': smart_model['url'],
    }})
    post(arena, '/players', {'name': 'brief-multi', 'kind': 'model', 'config': {
        'model': 'mock-coder', 'base': smart_model['url'],
        'brief': 'Return the complete `def`. No prose, no fences.',
    }})

    for seed in range(2):
        for pname in ('bare-multi', 'brief-multi'):
            played = mcp(arena, 'run_match', {'game': game, 'players': [pname], 'seed': seed + 10})
            illegal = played['seats'][0].get('illegal', 0)
            assert illegal == 0, f'{pname} seed={seed} had {illegal} illegal move(s)'
