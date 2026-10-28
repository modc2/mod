"""States — the world an agent acts on.

A state is anything an agent can look at, act on, and be scored by. A game in
this arena is one; a scratch computer is one (the build mod's whole job is a
computer an agent edits); a mod with functions is one. The interface is small
on purpose, so anything can be wrapped:

    reset(seed)   -> observation      start over
    observe()     -> str              what the agent sees now
    tools()       -> [{name, description}]   what it may do
    act(action)   -> {ok, observation, note?}   do one thing
    done()        -> bool
    score()       -> float 0..1 | None          how well it went, if the state knows

And because an agent's tool is just "a thing you call that changes the world
and tells you what happened", every state is also a tool: `as_tool(state)`.
"""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
CLASSES = os.path.join(SRC, 'examples', 'classes')
HOST = os.path.join(SRC, 'runtime', 'host.py')


def protocol():
    """The mod protocol package — never arena's own mod.py, which shadows it
    whenever this module's dir is on sys.path (pytest, the server)."""
    mine = {os.path.realpath(p) for p in (SRC, os.path.dirname(SRC), HERE)}
    saved, cached = sys.path[:], sys.modules.pop('mod', None)
    try:
        sys.path[:] = [p for p in sys.path if os.path.realpath(p or '.') not in mine]
        import mod as m
        if not hasattr(m, 'mod'):
            raise ImportError('mod protocol package not found')
        return m
    finally:
        sys.path[:] = saved
        if cached is not None and not hasattr(cached, 'mod'):
            sys.modules['mod'] = cached


def legal_moves(view):
    """Arena games end their view with `Legal moves: a, b, c`."""
    m = re.search(r'Legal moves:\s*(.+)', view or '')
    return [x.strip() for x in m.group(1).split(',') if x.strip()] if m else []


class State:
    kind = 'state'
    goal = ''

    def reset(self, seed=1):
        return self.observe()

    def observe(self):
        return ''

    def tools(self):
        return []

    def act(self, action):
        raise NotImplementedError

    def done(self):
        return False

    def score(self):
        return None

    def close(self):
        pass

    def card(self):
        return {'kind': self.kind, 'goal': self.goal, 'tools': self.tools()}


# ── a game ───────────────────────────────────────────────────────────────────

class GameState(State):
    """An arena class game, played from one seat. The other seats are filled
    by an opponent that picks a random legal move, so a single agent can be
    dropped into any multi-seat game. Runs in the same sandboxed host the
    arena's matches use (runtime/host.py), not in this process."""

    kind = 'game'

    def __init__(self, game='connect4', seat=0, seats=None, source='', **_):
        self.name = game
        self.seat = int(seat)
        self.source = source or self._source(game)
        self.seats_wanted = seats
        self.proc = None
        self.turns = 0
        self.last = ''
        self.goal = f'win the game `{game}` playing seat {self.seat}'

    @staticmethod
    def _source(game):
        path = game if game.endswith('.py') else os.path.join(CLASSES, f'{game}.py')
        if not os.path.exists(path):
            have = sorted(f[:-3] for f in os.listdir(CLASSES) if f.endswith('.py'))
            raise ValueError(f'no class game `{game}` — try one of {have}')
        with open(path) as f:
            return f.read()

    def _rpc(self, msg):
        self.proc.stdin.write(json.dumps(msg) + '\n')
        self.proc.stdin.flush()
        got = json.loads(self.proc.stdout.readline() or '{"ok": false, "error": "host died"}')
        if not got.get('ok'):
            raise RuntimeError(got.get('error'))
        return got

    def _call(self, method, *args):
        return self._rpc({'op': 'call', 'method': method, 'args': list(args)}).get('value')

    def reset(self, seed=1):
        self.close()
        self.rng = random.Random(seed)
        self.proc = subprocess.Popen([sys.executable, HOST], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
        json.loads(self.proc.stdout.readline())             # hello
        guess = int(self.seats_wanted or 2)
        info = self._rpc({'op': 'load', 'source': self.source, 'seed': seed,
                          'seats': guess}).get('info') or {}
        players = info.get('players', 2)
        self.seats = int(self.seats_wanted or (players[0] if isinstance(players, list) else players))
        if self.seats != guess:                             # built for the wrong table
            self._rpc({'op': 'load', 'source': self.source, 'seed': seed, 'seats': self.seats})
        self.max_turns = int(info.get('max_turns') or 200)
        self.turns, self.last = 0, ''
        self._others_until_mine()
        return self.observe()

    def _movers(self):
        return self._call('turn', self.seats, self.turns) or []

    def _others_until_mine(self):
        """Let the opponent move until it is this seat's turn (or the end)."""
        while not self.done():
            movers = self._movers()
            if self.seat in movers:
                return
            self._step({s: self._pick(s) for s in movers})

    def _pick(self, seat):
        moves = legal_moves(self._call('view', seat))
        return self.rng.choice(moves) if moves else ''

    def _step(self, moves):
        got = self._call('step', {str(k): v for k, v in moves.items()}) or {}
        self.turns += 1
        return got

    def observe(self):
        return self._call('view', self.seat) if self.proc else ''

    def tools(self):
        return [{'name': 'move', 'description': 'play one move; the view lists the legal ones'}]

    def act(self, action):
        if self.done():
            return {'ok': False, 'observation': self.observe(), 'note': 'game over'}
        movers = self._movers()
        moves = {s: self._pick(s) for s in movers if s != self.seat}
        moves[self.seat] = str(action)
        got = self._step(moves)
        legal = (got.get('legal') or {}).get(str(self.seat), True)
        self._others_until_mine()
        return {'ok': bool(legal), 'observation': self.observe(),
                'note': got.get('note', '')}

    def done(self):
        return (not self.proc) or self.turns >= self.max_turns or bool(self._call('done'))

    def result(self):
        return self._call('result')

    def score(self):
        if not self.proc or not self.done():
            return None
        scores = (self.result() or {}).get('scores') or []
        if not scores:
            return None
        lo, hi = min(scores), max(scores)
        return 1.0 if hi == lo else (scores[self.seat] - lo) / (hi - lo)

    def close(self):
        if self.proc:
            try:
                self.proc.stdin.write(json.dumps({'op': 'close'}) + '\n')
                self.proc.stdin.flush()
            except Exception:
                pass
            self.proc.kill()
            self.proc = None


# ── a computer ───────────────────────────────────────────────────────────────

class ShellState(State):
    """A scratch computer: a fresh directory and a shell. This is the build
    mod's world in miniature — the agent runs commands, files change, and a
    `check` command (exit 0 = done) says whether the goal is met.

    Not a sandbox: commands run as this user with cwd in a temp dir. Point it
    at the docker sandbox when the agent is not yours."""

    kind = 'shell'

    def __init__(self, goal='create hello.txt containing hi', check='grep -qx hi hello.txt',
                 files=None, timeout=20, **_):
        self.goal, self.check, self.files, self.timeout = goal, check, files or {}, int(timeout)
        self.dir = None
        self.last = ''

    def reset(self, seed=1):
        self.close()
        self.dir = tempfile.mkdtemp(prefix='arena-shell-')
        for name, body in self.files.items():
            path = os.path.join(self.dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(body)
        self.last = ''
        return self.observe()

    def _sh(self, cmd):
        try:
            r = subprocess.run(cmd, shell=True, cwd=self.dir, capture_output=True,
                               text=True, timeout=self.timeout)
            return r.returncode, (r.stdout + r.stderr)[-4000:]
        except subprocess.TimeoutExpired:
            return 124, f'timed out after {self.timeout}s'

    def observe(self):
        files = sorted(os.path.relpath(os.path.join(d, f), self.dir)
                       for d, _, fs in os.walk(self.dir or '.') for f in fs) if self.dir else []
        return (f'Goal: {self.goal}\nFiles: {", ".join(files) or "(none)"}\n'
                f'Last output:\n{self.last or "(nothing yet)"}\n'
                f'Reply with one shell command.')

    def tools(self):
        return [{'name': 'sh', 'description': 'run one shell command in the working directory'}]

    def act(self, action):
        code, out = self._sh(str(action))
        self.last = f'$ {action}\n[exit {code}]\n{out}'
        return {'ok': code == 0, 'observation': self.observe()}

    def done(self):
        return bool(self.dir) and self._sh(self.check)[0] == 0

    def score(self):
        return 1.0 if self.done() else 0.0

    def close(self):
        if self.dir:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None


# ── a mod ────────────────────────────────────────────────────────────────────

class ModState(State):
    """Any mod in the fleet, its functions as the action space — superhood,
    a chain, a market. An action is `fn {json kwargs}`. There's no `done` or
    `score` the mod could know about, so a judge decides."""

    kind = 'mod'

    def __init__(self, mod='superhood', fns=None, goal='', **_):
        self.mod, self.fns, self.goal = mod, list(fns or []), goal or f'use the `{mod}` mod'
        self.obj = None
        self.last = ''

    def reset(self, seed=1):
        m = protocol()
        self.obj = m.mod(self.mod)()
        if not self.fns:
            self.fns = [f for f in (m.fns(self.mod) if hasattr(m, 'fns') else dir(self.obj))
                        if not f.startswith('_')][:40]
        self.last = ''
        return self.observe()

    def observe(self):
        return (f'Goal: {self.goal}\nYou can call: {", ".join(self.fns)}\n'
                f'Last result:\n{self.last or "(nothing yet)"}\n'
                'Reply with `fn {"kwarg": value}`.')

    def tools(self):
        return [{'name': f, 'description': f'{self.mod}/{f}'} for f in self.fns]

    def act(self, action):
        fn, _, rest = str(action).strip().partition(' ')
        if fn not in self.fns:
            self.last = f'no fn `{fn}`'
            return {'ok': False, 'observation': self.observe()}
        try:
            kwargs = json.loads(rest) if rest.strip() else {}
            self.last = json.dumps(getattr(self.obj, fn)(**kwargs), default=str)[:4000]
            ok = True
        except Exception as e:                              # noqa: BLE001 — the agent reads it
            self.last, ok = f'{type(e).__name__}: {e}', False
        return {'ok': ok, 'observation': self.observe()}


KINDS = {'game': GameState, 'shell': ShellState, 'mod': ModState}


def make(spec):
    """{kind: game|shell|mod, ...config} -> a State."""
    if isinstance(spec, State):
        return spec
    spec = dict(spec or {})
    kind = spec.pop('kind', 'game')
    if kind not in KINDS:
        raise ValueError(f'no state kind `{kind}` — kinds: {sorted(KINDS)}')
    return KINDS[kind](**spec)


def as_tool(state):
    """A state is a tool: call it with an action, get what happened back."""
    state = make(state)

    def tool(action: str = ''):
        if not action:
            return {'observation': state.observe(), 'tools': state.tools()}
        return state.act(action)

    tool.__name__ = f'{state.kind}_state'
    tool.__doc__ = f'act on a {state.kind} state. goal: {state.goal}'
    tool.state = state
    return tool


def kinds():
    return {k: (c.__doc__ or '').strip().split('\n\n')[0].replace('\n', ' ') for k, c in KINDS.items()}
