"""arena — a wasm storage and execution layer, and an arena built on it.

The backend is src/arena-rs/ (axum): an MCP server speaking JSON-RPC 2.0 over
Streamable HTTP at /mcp plus --stdio for MCP clients. Every REST route on that
server dispatches through the same MCP tool layer, so each capability is
defined exactly once — and this module is a thin client over it.

    module      any wasm, stored under the SHA-256 of its bytes. The registry
                reads the binary rather than trusting the uploader: what it
                imports, what it exports, how much memory it wants.
    role        read out of the exports. A module exporting the game ABI is a
                game; one exporting `play` is a player; one exporting `_start`
                is a command; anything else is stored and still runs. Making a
                game is uploading a module — there is nothing to register.
    player      a seat-filler: a wasm bot, a model on any OpenAI-compatible
                endpoint, an agent from this fleet's agent module, or an
                endpoint of your own.
    match       one game, N seats, every move recorded. Rated with two or more
                seats; one seat is practice.

The server never runs wasm. Execution happens in src/runtime/, which is the
same code in both places it runs: the browser console imports it from this
server, and the node runner imports it off disk. That is what lets a match
played in a tab and a match played from the CLI share a leaderboard.

State lives off-tree in ~/.mod/arena/ — blobs, the registry, and keys.json if
you gave the arena a model key. The repo carries the example pack and nothing
a user put there.

CLI (via `m`):
    m arena/serve                                   # build + run under pm2
    m arena/modules role=game                       # what can be played
    m arena/abi                                     # how to write one
    m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'
    m arena/enter name=perfect kind=wasm config='{"module":"bot-ttt"}'
    m arena/play game=ttt players=opus,perfect      # play it
    m arena/leaderboard game=ttt
    m arena/run module=hello                        # run any module headlessly
"""

import json
import os
import subprocess

import requests

DEFAULT_PORT = 50470


class Mod:
    description = __doc__

    def __init__(self, server_url: str = None, **kwargs):
        self.dir = os.path.dirname(os.path.abspath(__file__))
        self.port = int(os.environ.get('ARENA_PORT') or self._config().get('port') or DEFAULT_PORT)
        self.server_url = (server_url or os.environ.get('ARENA_URL')
                           or f'http://127.0.0.1:{self.port}').rstrip('/')

    def _config(self):
        """The module's own config.json — one place for the port."""
        path = os.path.join(os.path.dirname(self.dir), 'config.json')
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _up(self):
        try:
            return requests.get(f'{self.server_url}/info', timeout=3).ok
        except Exception:
            return False

    def _get(self, path, **params):
        r = requests.get(f'{self.server_url}{path}',
                         params={k: v for k, v in params.items() if v is not None},
                         timeout=60)
        return self._read(r)

    def _post(self, path, body=None, timeout=900):
        r = requests.post(f'{self.server_url}{path}', json=body or {}, timeout=timeout)
        return self._read(r)

    @staticmethod
    def _read(r):
        try:
            out = r.json()
        except Exception:
            return {'error': f'{r.status_code}: {r.text[:400]}'}
        return out

    # ── mcp ──────────────────────────────────────────────────────

    def mcp_call(self, tool: str, arguments: dict = None, timeout: int = 900, **kwargs):
        """Call one MCP tool by name. Every capability is reachable this way."""
        args = dict(arguments or {})
        args.update({k: v for k, v in kwargs.items() if not k.startswith('_')})
        out = self._post('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                  'params': {'name': tool, 'arguments': args}},
                         timeout=timeout)
        result = out.get('result', out)
        if result.get('isError'):
            return {'error': result.get('content', [{}])[0].get('text', 'unknown error')}
        return result.get('structuredContent', result)

    def tools(self):
        """Every MCP tool this server exposes."""
        return self._get('/tools').get('tools', [])

    def mcp_config(self, name: str = 'arena'):
        """A ready-made MCP client entry — stdio for a local client, HTTP for the rest."""
        return {
            'mcpServers': {
                name: {'command': self.binary, 'args': ['--stdio']},
                f'{name}-http': {'type': 'http', 'url': f'{self.server_url}/mcp'},
            }
        }

    @property
    def binary(self):
        return os.path.join(self.dir, 'arena-rs', 'target', 'release', 'arena-api')

    # ── read ─────────────────────────────────────────────────────

    def forward(self, **kwargs):
        """Default entry point."""
        return self.info()

    def info(self):
        """What the arena is and what is in it."""
        return self._get('/info')

    def health(self):
        return {'up': self._up(), 'url': self.server_url}

    def readme(self):
        path = os.path.join(os.path.dirname(self.dir), 'README.md')
        return open(path).read() if os.path.exists(path) else None

    def abi(self, role: str = 'game'):
        """The contract a wasm module implements to become a game or a player."""
        return self._get('/abi', role=role)

    # ── modules ──────────────────────────────────────────────────

    def modules(self, role: str = None, q: str = None, tag: str = None):
        """Every stored module. role=game to see what can be played."""
        return self._get('/modules', role=role, q=q, tag=tag)

    def module(self, module: str):
        """One module in full — imports, exports, signatures, memory."""
        return self._get(f'/modules/{module}')

    def put(self, path: str, name: str = None, description: str = '',
            tags=None, author: str = ''):
        """Store a .wasm file. The id is the hash of its bytes, so this is
        idempotent: the same file twice updates the metadata, never the blob."""
        import base64
        with open(os.path.expanduser(path), 'rb') as f:
            raw = f.read()
        return self._post('/modules', {
            'bytes': base64.b64encode(raw).decode(),
            'name': name or os.path.basename(path).removesuffix('.wasm'),
            'description': description,
            'author': author,
            'tags': tags.split(',') if isinstance(tags, str) else (tags or []),
        })

    def inspect(self, path: str):
        """Describe a .wasm without storing it."""
        import base64
        with open(os.path.expanduser(path), 'rb') as f:
            raw = f.read()
        return self._post('/inspect', {'bytes': base64.b64encode(raw).decode()})

    def rm(self, module: str):
        """Remove a module and its bytes."""
        return self._read(requests.delete(f'{self.server_url}/modules/{module}', timeout=30))

    def examples(self):
        """Re-read the example pack from disk. Build it with src/examples/build.sh."""
        return self._post('/examples')

    # ── players ──────────────────────────────────────────────────

    def players(self, kind: str = None):
        """Everyone entered, strongest first."""
        return self._get('/players', kind=kind)

    def player(self, player: str):
        """One player, with a rating per game."""
        return self._get(f'/players/{player}')

    def enter(self, name: str, kind: str = 'model', config=None, owner: str = '',
              note: str = '', **kwargs):
        """Enter a player. Re-entering a name updates it and keeps its record.

        m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'
        m arena/enter name=perfect kind=wasm config='{"module":"bot-ttt"}'
        """
        if isinstance(config, str):
            config = json.loads(config)
        config = dict(config or {})
        # `m arena/enter name=x model=y` is what a hand reaches for; fold the
        # loose keys into the config rather than losing them.
        config.update({k: v for k, v in kwargs.items() if not k.startswith('_')})
        return self._post('/players', {'name': name, 'kind': kind, 'config': config,
                                       'owner': owner, 'note': note})

    def withdraw(self, player: str):
        """Withdraw a player. Past matches keep their record."""
        return self._read(requests.delete(f'{self.server_url}/players/{player}', timeout=30))

    def probe(self, player: str, view: str = 'Legal moves: rock, paper, scissors', seat: int = 0):
        """Ask one player for one move — how to check it answers before seating it."""
        return self._post('/play', {'player': player, 'view': view, 'seat': int(seat)})

    # ── play ─────────────────────────────────────────────────────

    def play(self, game: str, players, seed: int = None, turns: int = None,
             timeout: int = 900):
        """Play a match. The wasm runs in the node runner; the result is rated."""
        if isinstance(players, str):
            players = [p.strip() for p in players.split(',') if p.strip()]
        body = {'game': game, 'players': players, 'timeout_ms': int(timeout) * 1000}
        if seed is not None:
            body['seed'] = int(seed)
        if turns is not None:
            body['turns'] = int(turns)
        return self._post('/run', body, timeout=timeout + 30)

    def run(self, module: str, entry: str = None, args: str = '', stdin: str = '',
            seed: int = 1, timeout: int = 30):
        """Run any module headlessly through the node runner — the same
        execution layer the console uses, minus the browser."""
        cmd = ['node', os.path.join(self.dir, 'runtime', 'run.mjs'), 'run',
               '--base', self.server_url, '--module', module,
               '--seed', str(seed), '--timeout', str(int(timeout) * 1000)]
        if entry:
            cmd += ['--entry', entry]
        for a in [a for a in str(args).split(',') if a.strip()]:
            cmd += ['--arg', a.strip()]
        if stdin:
            cmd += ['--stdin', stdin]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
        if r.returncode != 0:
            return {'error': (r.stderr or r.stdout)[-2000:]}
        return json.loads(r.stdout)

    def matches(self, limit: int = 20, game: str = None):
        return self._get('/matches', limit=limit, game=game)

    def match(self, id: str):
        """One match in full, including every turn."""
        return self._get(f'/matches/{id}')

    def leaderboard(self, game: str = None, limit: int = 20):
        """Ranked by Elo. Name a game for the ranking that means something."""
        return self._get('/leaderboard', game=game, limit=limit)

    # ── uploaded games, each its own mod ─────────────────────────
    #
    # Distinct from `put`, which stores any wasm in the Rust server's own blob
    # store. This half takes a *game* specifically, keeps it in the store mod,
    # and writes games/<slug>/ — so an uploaded game is a module in the fleet
    # rather than a row in a table. See src/games.py.

    def publish(self, path: str = '', name: str = '', description: str = '',
                author: str = '', tags=None, serve: bool = True, b64: str = ''):
        """Upload a game: check the ABI, store the bytes, mint its mod.

        m arena/publish path=~/snake.wasm name=snake author=0x…

        `b64` is the same call for callers that already hold the bytes — the
        wasmland marketplace hands games over this way rather than writing
        somebody's upload back out to a temporary file first.
        """
        from . import games as G
        if b64:
            import base64
            data = base64.b64decode(b64)
        elif path:
            with open(os.path.expanduser(path), 'rb') as f:
                data = f.read()
        else:
            return {'error': 'give a path= to a .wasm, or b64= of its bytes'}
        card = G.publish(data, name=name, description=description, author=author,
                         tags=tags.split(',') if isinstance(tags, str) else tags)
        # Registering with the running server is what makes it playable *now*;
        # the game is published either way, so a stopped arena is not an error.
        if serve and self._up():
            try:
                import base64
                card['registered'] = self._post('/modules', {
                    'bytes': base64.b64encode(data).decode(),
                    'name': card['name'], 'description': card['description'],
                    'author': author, 'tags': card.get('tags') or [],
                })
            except Exception as e:
                card['registered'] = {'error': str(e)}
        return card

    def games(self, limit: int = 200):
        """Every published game — read from the store, not from disk."""
        from . import games as G
        return {'games': G.cards(limit=limit)}

    def game(self, slug: str):
        """One published game's card."""
        from . import games as G
        return G.card(slug)

    def unpublish(self, slug: str):
        """Remove a published game and its minted mod. The bytes stay."""
        from . import games as G
        return G.remove(slug)

    def register(self, slug: str):
        """(Re)register a published game with the running arena server."""
        import base64
        from . import games as G
        card = G.card(slug)
        return self._post('/modules', {
            'bytes': base64.b64encode(G.blob(slug)).decode(),
            'name': card['name'], 'description': card['description'],
            'author': card.get('author', ''), 'tags': card.get('tags') or [],
        })

    # ── build / serve / kill ─────────────────────────────────────

    def build(self, examples: bool = False, **kwargs):
        """Build the Rust backend. examples=1 also recompiles the wasm pack."""
        out = {}
        env = {**os.environ, 'PATH': os.environ['PATH'] + ':' + os.path.expanduser('~/.cargo/bin')}
        if examples:
            script = os.path.join(self.dir, 'examples', 'build.sh')
            r = subprocess.run([script], cwd=os.path.join(self.dir, 'examples'),
                               capture_output=True, text=True, env=env)
            out['examples'] = r.stdout.strip()[-1500:] if r.returncode == 0 else r.stderr[-1500:]

        rs = os.path.join(self.dir, 'arena-rs')
        r = subprocess.run(['cargo', 'build', '--release'], cwd=rs,
                           capture_output=True, text=True, env=env)
        if r.returncode != 0:
            return {**out, 'status': 'build_failed', 'stderr': r.stderr[-3000:]}
        return {**out, 'status': 'built', 'binary': self.binary}

    def serve(self, port=None, build: bool = True, **kwargs):
        """Run the arena under pm2 as arena-api (API, MCP and console).

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
        subprocess.run(['pm2', 'start', self.binary, '--name', 'arena-api'],
                       cwd=self.dir, env={**os.environ, 'PORT': str(port)},
                       capture_output=True)
        return {
            'api': f'http://localhost:{port}',
            'mcp': f'http://localhost:{port}/mcp',
            'console': f'http://localhost:{port}/arena',
            'processes': ['arena-api'],
        }

    def kill(self, **kwargs):
        """Stop the arena."""
        killed = []
        for name in ['arena-api', 'arena.api']:
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    # ── test ─────────────────────────────────────────────────────

    def test(self, **kwargs):
        """End to end, against a running server: MCP handshakes, the registry
        describes the example pack, wasm executes, and a match between the
        reference bot and the random one goes the way it has to."""
        out = {'server_url': self.server_url, 'up': self._up()}
        if not out['up']:
            out['hint'] = 'run `m arena/serve` first'
            return out

        try:
            r = self._post('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-06-18'}}, timeout=10)
            out['mcp'] = r.get('result', {}).get('serverInfo')
            out['tools'] = len(self.tools())
        except Exception as e:
            out['mcp_error'] = str(e)

        info = self.info()
        out['modules'] = info.get('modules')
        out['games'] = info.get('games')

        # The execution layer: a WASI command that was never written for this
        # arena still runs, prints, and finds no filesystem.
        try:
            hello = self.run('hello', args='test')
            out['wasi'] = {'ok': hello.get('ok'),
                           'stdout': (hello.get('stdout') or '').strip().splitlines()[:1],
                           'sandboxed': 'sandboxed' in (hello.get('stdout') or '')}
        except Exception as e:
            out['wasi_error'] = str(e)

        # The arena: minimax must not lose to random at a solved game.
        try:
            self.enter('_test_perfect', 'wasm', {'module': 'bot-ttt'})
            self.enter('_test_dice', 'wasm', {'module': 'bot-random'})
            m = self.play('ttt', ['_test_perfect', '_test_dice'], seed=42)
            scores = {s['player_name']: s['score'] for s in m.get('seats', [])}
            out['match'] = {'summary': m.get('summary'), 'scores': scores,
                            'rated': m.get('rated')}
            out['arena_ok'] = scores.get('_test_perfect', 0) >= scores.get('_test_dice', 1)
        except Exception as e:
            out['match_error'] = str(e)
            out['arena_ok'] = False
        finally:
            for name in ['_test_perfect', '_test_dice']:
                try:
                    self.withdraw(name)
                except Exception:
                    pass

        out['ok'] = bool(out.get('arena_ok')) and bool(out.get('wasi', {}).get('ok'))
        return out
