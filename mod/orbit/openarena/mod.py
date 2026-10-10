"""openarena — an arena where agents compete on uploaded tasks.

The backend is openarena-rs/ (axum): an MCP server speaking JSON-RPC 2.0 over
Streamable HTTP at /mcp plus --stdio for MCP clients. Every REST route on that
server dispatches through the same MCP tool layer, so each arena capability is
defined exactly once — and this module is a thin client over it.

    task        a coding challenge: a statement plus graded cases. `io` cases
                feed stdin and compare stdout; `unit` cases run a grader that
                imports the submission. Hidden cases are graded, never shown.
    competitor  something that turns a task into a program — an agent in this
                fleet's `agent` module, an HTTP endpoint, an Agent Protocol v1
                server, or a fixed program used as a baseline.
    match       one task, N competitors, all briefed at the same moment and
                graded on the same cases. Two or more entrants makes it rated
                and moves Elo; one entrant is practice.

Arena state lives off-tree in ~/.mod/openarena/ — the repo carries the seed
task pack and nothing else.

CLI (via `m`):
    m openarena/serve                                   # build + run under pm2
    m openarena/tasks                                   # what is on offer
    m openarena/task task=fizzbuzz                      # one task in full
    m openarena/enter name=opus kind=agent_mod config='{"model":"anthropic/claude-opus-5"}'
    m openarena/seed_agents free=1                      # a competitor per model
    m openarena/run_match task=fizzbuzz agents=opus,gpt # race them
    m openarena/leaderboard
"""

import itertools
import json
import os
import subprocess

import requests

DEFAULT_PORT = 50400
AGENT_MOD_API = 'http://127.0.0.1:50117'   # the fleet's agent module

# A sensible opening field for `seed_agents` — override with models=...
SEED_MODELS = [
    'anthropic/claude-opus-5',
    'openai/gpt-5.2',
    'qwen/qwen3-coder',
]


class Mod:
    description = """
    An arena of competing agents. Upload a coding task with its graded tests,
    enter competitors (agents from the agent module, HTTP endpoints, Agent
    Protocol v1 servers, or fixed baselines), and race them: every entrant gets
    the same brief at the same moment, every program is graded in a sandbox
    against the same cases, and the scores move an Elo rating. Rust backend,
    MCP over Streamable HTTP and stdio.
    """
    path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, server_url: str = None, **kwargs):
        self.dir = self.path
        cfg = self._config()
        self.port = int(cfg.get('port', DEFAULT_PORT))
        self.server_url = (server_url or f'http://127.0.0.1:{self.port}').rstrip('/')
        self._rpc_ids = itertools.count(1)

    def _config(self):
        try:
            with open(os.path.join(self.path, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    # ── MCP client (the backend) ─────────────────────────────────

    def _up(self):
        try:
            return requests.get(f'{self.server_url}/health', timeout=2).ok
        except Exception:
            return False

    def mcp_call(self, tool: str, arguments: dict = None, timeout: int = 900, **kwargs):
        """Call an MCP tool on the Rust backend (JSON-RPC tools/call)."""
        args = {k: v for k, v in dict(arguments or {}, **kwargs).items() if v is not None}
        try:
            resp = requests.post(
                f'{self.server_url}/mcp',
                json={'jsonrpc': '2.0', 'id': next(self._rpc_ids),
                      'method': 'tools/call',
                      'params': {'name': tool, 'arguments': args}},
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise RuntimeError(
                f'openarena backend unreachable at {self.server_url} '
                f'— run `m openarena/serve` ({e})')
        body = resp.json()
        if 'error' in body:
            raise RuntimeError(body['error'].get('message', str(body['error'])))
        result = body.get('result', {})
        if result.get('isError'):
            raise RuntimeError((result.get('content') or [{}])[0].get('text', 'unknown error'))
        return result.get('structuredContent', result)

    def tools(self):
        """The MCP tool list the backend exposes."""
        return requests.get(f'{self.server_url}/tools', timeout=10).json()['tools']

    @property
    def binary(self):
        return os.path.join(self.dir, 'openarena-rs', 'target', 'release', 'openarena-api')

    def mcp_config(self):
        """How to point an MCP client at this arena."""
        return {
            'stdio': {'command': self.binary, 'args': ['--stdio']},
            'http': {'url': f'{self.server_url}/mcp'},
            'claude_code': f'claude mcp add openarena -- {self.binary} --stdio',
        }

    # ── the arena (one thin wrapper per MCP tool) ────────────────

    def forward(self, **kwargs):
        """Default entry — what this arena is and what is in it."""
        return self.info()

    def info(self):
        return self.mcp_call('arena_info', timeout=15)

    def health(self):
        return {'up': self._up(), 'url': self.server_url,
                'binary_built': os.path.exists(self.binary)}

    def readme(self):
        with open(os.path.join(self.path, 'README.md')) as f:
            return f.read()

    def tasks(self, q: str = None, tag: str = None, kind: str = None):
        """Every task in the arena."""
        return self.mcp_call('list_tasks', q=q, tag=tag, kind=kind, timeout=30)

    def task(self, task: str, reveal: bool = False):
        """One task in full. reveal=True includes the hidden cases — for the
        author, not for entrants."""
        return self.mcp_call('get_task', task=task, reveal=bool(reveal), timeout=30)

    def create_task(self, title: str, tests, statement: str = '', language: str = 'any',
                    mode: str = 'io', slug: str = None, starter: str = None,
                    tags=None, author: str = None, timeout_ms: int = None):
        """Upload a task. `tests` is the grading contract: io cases carry
        {stdin, expect}, unit cases carry {program}; mark any of them hidden."""
        if isinstance(tests, str):
            tests = json.loads(tests)
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(',') if t.strip()]
        return self.mcp_call('create_task', title=title, tests=tests, statement=statement,
                             language=language, mode=mode, slug=slug, starter=starter,
                             tags=tags, author=author, timeout_ms=timeout_ms, timeout=30)

    def delete_task(self, task: str):
        """Remove a task. Past matches keep their record."""
        return self.mcp_call('delete_task', task=task, timeout=30)

    def agents(self):
        """Every competitor entered, strongest first."""
        return self.mcp_call('list_agents', timeout=30)

    def enter(self, name: str, kind: str = 'agent_mod', config=None,
              owner: str = None, note: str = None):
        """Enter a competitor.

        agent_mod  config: {base?, agent?, model?, prompt?, toolbox?, steps?, free?, key?}
        http       config: {url, headers?, field?}
        ap         config: {base, steps?, headers?}   — Agent Protocol v1
        static     config: {code, language?}          — a fixed baseline
        """
        if isinstance(config, str):
            config = json.loads(config)
        return self.mcp_call('enter_agent', name=name, kind=kind, config=config or {},
                             owner=owner, note=note, timeout=30)

    def remove_agent(self, agent: str):
        """Withdraw a competitor. Past matches keep their record."""
        return self.mcp_call('remove_agent', agent=agent, timeout=30)

    def run_match(self, task: str, agents, timeout: int = 1800):
        """Put competitors on a task. They answer concurrently and every answer
        is graded on the same cases — so this blocks for as long as the slowest
        entrant takes to think."""
        if isinstance(agents, str):
            agents = [a.strip() for a in agents.split(',') if a.strip()]
        return self.mcp_call('run_match', task=task, agents=list(agents), timeout=timeout)

    def submit(self, task: str, code: str, language: str = None, agent: str = None):
        """Grade a program without a match — how a human or an outside agent
        plays. Records the result; never moves Elo, since nobody was raced."""
        return self.mcp_call('submit', task=task, code=code, language=language,
                             agent=agent, timeout=600)

    def matches(self, limit: int = 20):
        """Recent matches with the scoreboard of each."""
        return self.mcp_call('list_matches', limit=int(limit), timeout=30)

    def match(self, id: str):
        """One match in full — every case result and every submitted program."""
        return self.mcp_call('get_match', id=id, timeout=30)

    def leaderboard(self, limit: int = 20):
        """Competitors ranked by Elo, with pass rate, mean score and mean time."""
        return self.mcp_call('leaderboard', limit=int(limit), timeout=30)

    # ── standardized benchmarks off the web ──────────────────────

    def bench_sources(self):
        """The benchmarks this arena can pull off the web, and whether
        fetching is switched on at all."""
        return self.mcp_call('bench_sources', timeout=15)

    def bench_preview(self, source: str = 'humaneval', limit: int = 5, offset: int = 0,
                      url: str = None, dataset: str = None, config: str = None,
                      split: str = None, style: str = None, map=None,
                      hide_after: int = None, max_cases: int = None,
                      language: str = None, tags=None, slug_prefix: str = None,
                      split_asserts: bool = True, refresh: bool = False):
        """Fetch a benchmark and show the tasks it would become. Imports
        nothing — read it before you keep it."""
        return self.mcp_call('bench_preview', timeout=300, **self._bench_args(locals()))

    def bench_import(self, source: str = 'humaneval', limit: int = 5, offset: int = 0,
                     url: str = None, dataset: str = None, config: str = None,
                     split: str = None, style: str = None, map=None,
                     hide_after: int = None, max_cases: int = None,
                     language: str = None, tags=None, slug_prefix: str = None,
                     split_asserts: bool = True, refresh: bool = False,
                     dry_run: bool = False):
        """Convert a benchmark into arena tasks and keep them. A slug already
        in the arena is skipped, so paging with offset= is safe to repeat.

            m openarena/bench_import source=humaneval limit=20
            m openarena/bench_import source=mbpp limit=10 offset=10
            m openarena/bench_import source=html url=https://example.org/problem
        """
        return self.mcp_call('bench_import', timeout=600, **self._bench_args(locals()))

    @staticmethod
    def _bench_args(local_vars: dict):
        """The CLI hands everything over as strings; the tool wants types."""
        args = {k: v for k, v in local_vars.items() if k != 'self' and v is not None}
        for key in ('limit', 'offset', 'hide_after', 'max_cases'):
            if key in args:
                args[key] = int(args[key])
        for key in ('split_asserts', 'refresh', 'dry_run'):
            if key in args:
                args[key] = str(args[key]).lower() not in ('0', 'false', 'no', '')
        if isinstance(args.get('map'), str):
            args['map'] = json.loads(args['map'])
        if isinstance(args.get('tags'), str):
            args['tags'] = [t.strip() for t in args['tags'].split(',') if t.strip()]
        return args

    # ── the agent module ─────────────────────────────────────────

    def seed_agents(self, models=None, base: str = AGENT_MOD_API, agent: str = None,
                    free: bool = False, steps: int = 4, prefix: str = ''):
        """Enter one competitor per model, all of them running on the fleet's
        agent module — the quickest way to get a field on the board.

            m openarena/seed_agents free=1
            m openarena/seed_agents models=anthropic/claude-opus-5,openai/gpt-5.2
        """
        if isinstance(models, str):
            models = [x.strip() for x in models.split(',') if x.strip()]
        models = models or SEED_MODELS
        entered, skipped = [], []
        for model in models:
            name = prefix + model.split('/')[-1]
            cfg = {'base': base, 'model': model, 'steps': int(steps)}
            if agent:
                cfg['agent'] = agent
            if free:
                cfg['free'] = True
            try:
                entered.append(self.enter(name, 'agent_mod', cfg, owner='seed',
                                          note=f'agent module · {model}'))
            except RuntimeError as e:
                # Already entered is the common case here, and not a failure.
                skipped.append({'name': name, 'why': str(e)[:120]})
        return {'entered': [a['name'] for a in entered], 'skipped': skipped,
                'agent_module': base}

    # ── build / serve / kill ─────────────────────────────────────

    def build(self, **kwargs):
        """Build the Rust backend (cargo build --release)."""
        rs = os.path.join(self.dir, 'openarena-rs')
        r = subprocess.run(
            ['cargo', 'build', '--release'], cwd=rs, capture_output=True, text=True,
            env={**os.environ,
                 'PATH': os.environ['PATH'] + ':' + os.path.expanduser('~/.cargo/bin')},
        )
        if r.returncode != 0:
            return {'status': 'build_failed', 'stderr': r.stderr[-3000:]}
        return {'status': 'built', 'binary': self.binary}

    def serve(self, port=None, build=True, **kwargs):
        """Run the arena under pm2 as openarena-api (API, MCP and console).

        Builds first by default — cargo is incremental, so an unchanged tree
        costs a moment, and skipping it is how you deploy a stale binary and
        spend an hour wondering why your edit did nothing. build=0 to skip.
        """
        port = int(port or self.port)
        if build or not os.path.exists(self.binary):
            built = self.build()
            if built.get('status') != 'built':
                return built
        self.kill()
        subprocess.run(['pm2', 'start', self.binary, '--name', 'openarena-api'],
                       cwd=self.dir, env={**os.environ, 'PORT': str(port)},
                       capture_output=True)
        return {
            'api': f'http://localhost:{port}',
            'mcp': f'http://localhost:{port}/mcp',
            'console': f'http://localhost:{port}/openarena',
            'processes': ['openarena-api'],
        }

    def kill(self, **kwargs):
        """Stop the arena."""
        killed = []
        for name in ['openarena-api', 'openarena.api']:
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    # ── test ─────────────────────────────────────────────────────

    # A program that really solves the seeded FizzBuzz — the judge is only
    # worth trusting if a correct answer passes and a wrong one does not.
    REFERENCE = (
        "import sys\n"
        "n = int(sys.stdin.read().split()[0])\n"
        "for i in range(1, n + 1):\n"
        "    s = ('Fizz' if i % 3 == 0 else '') + ('Buzz' if i % 5 == 0 else '')\n"
        "    print(s or i)\n"
    )

    def test(self, **kwargs):
        """End to end: the backend answers, MCP handshakes, and the judge scores
        a correct program 1.0 and a wrong one 0.0."""
        out = {'server_url': self.server_url, 'up': self._up()}
        if not out['up']:
            out['hint'] = 'run `m openarena/serve` first'
            return out
        try:
            r = requests.post(f'{self.server_url}/mcp',
                              json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-06-18'}},
                              timeout=10).json()
            out['mcp'] = r.get('result', {}).get('serverInfo')
            out['tools'] = len(self.tools())
        except Exception as e:
            out['mcp_error'] = str(e)

        out['info'] = self.info()
        try:
            good = self.submit('fizzbuzz', self.REFERENCE, 'python')
            bad = self.submit('fizzbuzz', 'print("nope")', 'python')
            out['judge'] = {'correct_scores': good['score'], 'wrong_scores': bad['score'],
                            'cases': good['total']}
            out['judge_ok'] = good['score'] == 1.0 and bad['score'] == 0.0
        except Exception as e:
            out['judge_error'] = str(e)
            out['judge_ok'] = False
        return out
