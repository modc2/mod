"""arena — upload a class or a wasm module; agents compete at what you upload.

The backend is src/arena-rs/ (axum): an MCP server speaking JSON-RPC 2.0 over
Streamable HTTP at /mcp plus --stdio for MCP clients. Every REST route on that
server dispatches through the same MCP tool layer, so each capability is
defined exactly once — and this module is a thin client over it.

    module      anything uploaded, stored under the SHA-256 of its bytes: a
                wasm binary, a Python class, or a Rust class. The registry
                reads it rather than trusting the uploader — a wasm module's
                imports, exports and memory; a Python class's `def`s; the
                `fn`s in a Rust struct's impl block.
    role        read out of the bytes. Exporting the game ABI — or defining
                view/step/done/result — makes a game; `play` makes a player;
                `_start` makes a command; anything else is stored and still
                runs. Making a game is uploading it. There is nothing to
                register and no approval to wait for.
    class       one file holding a class, in Python or in Rust. The state is
                `self`, the methods take and return ordinary values, and there
                is no ABI to learn. Where it runs differs: a Python class is
                interpreted in a sandboxed subprocess (no filesystem, no
                network, seeded random, capped memory) — convenience, not a
                guarantee — while a Rust class is compiled to wasm on upload
                and runs in the engine sandbox, which is the hard one, in a
                browser tab as happily as in the runner.
    player      a seat-filler: a class, a wasm bot, a model on any
                OpenAI-compatible endpoint, an agent from this fleet's agent
                module, an MCP server, or an endpoint of your own.
    match       one game, N seats, every move recorded. Rated with two or more
                seats; one seat is practice.
    mcp         every stored module is also an MCP server of its own, at
                /m/<name>/mcp: a game you `open` a table at and play a turn at
                a time, an agent you hand a view and get a move back from. And
                the traffic runs both ways — a class can call out to an MCP
                server mid-move, if the match allowed it, through a door the
                arena holds rather than a socket the sandbox opens.
    nested mod  every module is also a mod, minted under orbit/arena/mods/ by
                `m arena/mint`. `m arena.nim/open`, `m arena.bot-perfect/ask`.

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
    m arena/template role=game lang=rust > game.rs  # a class to fill in
    m arena/upload path=game.rs                     # it is now playable
    m arena/abi lang=rust                           # the contract, in full
    m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'
    m arena/enter name=perfect kind=class config='{"module":"bot-perfect"}'
    m arena/play game=ttt players=opus,perfect      # play it
    m arena/leaderboard game=ttt
    m arena/run module=hello                        # run any module headlessly

    m arena/mint                                    # every module, as a mod
    m arena.nim-rs/open                             # sit down at one
    m arena.bot-perfect/ask view='Legal moves: 1, 2, 3'
    m arena/servers                                 # one MCP server per module
    m arena/mcp_servers                             # what a class may call out to
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

    def abi(self, role: str = 'game', lang: str = 'wasm'):
        """The contract a module implements to become a game or a player.

        lang=class for the Python-class form and lang=rust for the Rust one:
        the methods to define rather than the exports to compile.
        """
        return self._get('/abi', role=role, lang=lang)

    def template(self, role: str = 'game', lang: str = 'class'):
        """A class to copy, ready to fill in. Write it to a file and upload it:

            m arena/template role=game > mygame.py
            m arena/template role=game lang=rust > mygame.rs
            m arena/upload path=mygame.py

        It comes from the server, which is where the rule about what makes a
        game lives — so the starting point cannot drift from the contract.
        """
        abi = self.abi(role=role, lang=lang)
        if isinstance(abi, dict) and abi.get('template'):
            return abi['template']
        return abi

    def prelude(self):
        """What every Rust class is compiled against — Moves, Step, Outcome,
        `arena::log`, `arena::random`, `arena::mcp`. The whole file, which is
        the specification."""
        r = requests.get(f'{self.server_url}/runtime/prelude.rs', timeout=30)
        return r.text if r.ok else {'error': f'{r.status_code}: {r.text[:300]}'}

    def toolchain(self):
        """Whether this box can compile a Rust class, and where it caches."""
        return self._get('/toolchain')

    # ── modules ──────────────────────────────────────────────────

    def modules(self, role: str = None, q: str = None, tag: str = None, lang: str = None):
        """Every stored module — wasm and classes alike. role=game to see what
        can be played, lang=python or lang=rust for the classes."""
        return self._get('/modules', role=role, q=q, tag=tag, lang=lang)

    def module(self, module: str):
        """One module in full — for wasm its imports, exports and memory; for
        a class the methods it defines and its source."""
        return self._get(f'/modules/{module}')

    def classes(self, role: str = None, q: str = None):
        """The uploaded classes. Same registry as the wasm, filtered."""
        return self._get('/classes', role=role, q=q)

    def source(self, module: str):
        """Read a stored module's code back, as text — a class as itself, a
        wasm module as the source it was uploaded with (the example pack keeps
        its Rust beside each binary)."""
        card = self.module(module)
        if isinstance(card, dict) and card.get('source'):
            return card['source']
        if isinstance(card, dict) and card.get('lang') == 'wasm':
            return {'error': f"module {card.get('name', module)} is a compiled binary and came "
                             f"without its source — `m arena/module module={module}` describes "
                             f"it; upload it again with source_text to keep the code beside it"}
        return card

    code = source

    def hashes(self, module: str):
        """The two names a module's bytes have: the arena's SHA-256 (its id)
        and the store module's CID, with where each resolves."""
        card = self.module(module)
        if not isinstance(card, dict) or card.get('error'):
            return card
        return {
            'name': card.get('name'), 'size': card.get('size'),
            'sha256': card.get('sha256') or card.get('id'),
            'blob': f"{self.server_url}/blob/{card.get('id')}",
            'cid': card.get('cid'), 'store': card.get('store'),
            'src_cid': card.get('src_cid'),
        }

    # ── the store ────────────────────────────────────────────────
    # Every module is also an object in the fleet's store module: the same
    # bytes, hashed there into a CID, kept under the key arena/<sha256>, and
    # readable from the store's own page without this arena.

    def store_status(self):
        """Where the store is, whose key the copies are under, and how many
        modules have a CID yet."""
        return self._get('/store')

    def store_sync(self, force: bool = False, verify: bool = False):
        """Push every module the store has not got (force=1 for all of them);
        verify=1 reads each copy back and checks it still hashes to the id."""
        return self._post('/store/sync', {'force': bool(force), 'verify': bool(verify)}, timeout=1800)

    def compiled(self, module: str, path: str = ''):
        """The wasm a module actually runs as.

        For a wasm upload that is the bytes themselves; for a Rust class it is
        the compile, which happens once and is cached under the module's id.
        A Python class has no wasm form — it runs in the interpreter sandbox.
        """
        r = requests.get(f'{self.server_url}/wasm/{module}', timeout=300)
        if not r.ok:
            return self._read(r)
        if path:
            path = os.path.expanduser(path)
            with open(path, 'wb') as f:
                f.write(r.content)
            return {'module': module, 'bytes': len(r.content), 'path': path}
        return {'module': module, 'bytes': len(r.content),
                'note': 'pass path= to write it out'}

    def put(self, path: str, name: str = None, description: str = '',
            tags=None, author: str = ''):
        """Store a file — a .wasm module, a .py class or a .rs class. The id is
        the hash of its bytes, so this is idempotent: the same file twice
        updates the metadata, never the blob."""
        import base64
        path = os.path.expanduser(path)
        with open(path, 'rb') as f:
            raw = f.read()
        stem = os.path.basename(path)
        for suffix in ('.wasm', '.py', '.rs'):
            stem = stem.removesuffix(suffix)
        return self._post('/modules', {
            'bytes': base64.b64encode(raw).decode(),
            'name': name or stem,
            'description': description,
            'author': author,
            'tags': tags.split(',') if isinstance(tags, str) else (tags or []),
        })

    def upload(self, path: str = '', source: str = '', name: str = '',
               description: str = '', tags=None, author: str = '', lang: str = '',
               mint: bool = True):
        """Upload a class — a file, or the source inline, in Python or in Rust.

        A class defining view/step/done/result is a game; one defining `play`
        is a player. Nothing to register: the registry reads the source and
        decides, and which language it is written in is read the same way.

            m arena/upload path=mygame.py
            m arena/upload path=mygame.rs
            m arena/upload source='class Bot:
                def play(self, view, seat): return "4"'

        A Rust class is compiled on the way in, so an upload that will not
        build comes back saying so, with your own line numbers.
        """
        if path and not source:
            path = os.path.expanduser(path)
            with open(path) as f:
                source = f.read()
            stem = os.path.basename(path)
            for suffix in ('.py', '.rs'):
                stem = stem.removesuffix(suffix)
            name = name or stem
            lang = lang or {'.rs': 'rust', '.py': 'python'}.get(os.path.splitext(path)[1], '')
        if not source.strip():
            return {'error': 'upload needs `path` to a .py or .rs file, or `source` as text'}
        body = {'source': source, 'description': description, 'author': author,
                'tags': tags.split(',') if isinstance(tags, str) else (tags or [])}
        if name:
            body['name'] = name
        if lang:
            body['lang'] = lang
        stored = self._post('/classes', body)
        if not isinstance(stored, dict) or stored.get('error'):
            return stored
        # A Rust class that cannot be compiled is stored and unplayable, which
        # is worth finding out now rather than three seats into a match.
        if stored.get('lang') == 'rust' and stored.get('role') in ('game', 'player'):
            built = self.compiled(stored['id'])
            if isinstance(built, dict) and built.get('error'):
                stored['compile_error'] = built['error']
            else:
                stored['wasm_bytes'] = built.get('bytes')
        if mint and stored.get('role') in ('game', 'player', 'command'):
            try:
                from . import games as G
                stored['mod'] = 'arena.' + G.slugify(stored.get('name', ''))
                G.mint(G.card_from_module(stored, base=self.server_url))
            except Exception as e:
                stored['mod_error'] = str(e)
        return stored

    def inspect(self, path: str = '', source: str = ''):
        """Describe a file without storing it — a .wasm or a class."""
        import base64
        if source and not path:
            return self._post('/inspect', {'text': source})
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
        m arena/enter name=center kind=class config='{"module":"center"}'
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
             mcp=None, timeout: int = 900):
        """Play a match. The wasm runs in the node runner; the result is rated.

        `mcp` names the servers the classes in this match may call out to —
        `mcp=arena` lets a player consult the arena mid-move. Left out, they
        have no way out at all, which is the default and the only setting
        under which a move is a function of its view alone.
        """
        if isinstance(players, str):
            players = [p.strip() for p in players.split(',') if p.strip()]
        body = {'game': game, 'players': players, 'timeout_ms': int(timeout) * 1000}
        if seed is not None:
            body['seed'] = int(seed)
        if turns is not None:
            body['turns'] = int(turns)
        if mcp:
            body['mcp'] = [m.strip() for m in mcp.split(',')] if isinstance(mcp, str) else mcp
        return self._post('/run', body, timeout=timeout + 30)

    def run(self, module: str, entry: str = None, args: str = '', stdin: str = '',
            seed: int = 1, timeout: int = 30):
        """Run any module headlessly through the node runner — the same
        execution layer the console uses, minus the browser. Works on both
        containers: a wasm entry point, or one method of a class."""
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

    def matches(self, limit: int = 20, game: str = None, player: str = None):
        """Recent matches. Name a game, a player (id or name), or both."""
        return self._get('/matches', limit=limit, game=game, player=player)

    def match(self, id: str):
        """One match in full, including every turn."""
        return self._get(f'/matches/{id}')

    def leaderboard(self, game: str = None, limit: int = 20):
        """Ranked by Elo. Name a game for the ranking that means something."""
        return self._get('/leaderboard', game=game, limit=limit)

    # ── one MCP server per module ────────────────────────────────
    #
    # /mcp is the arena. /m/<name>/mcp is one module — a game you can open a
    # table at and play a turn at a time, an agent you can hand a view to. The
    # two halves below are the same door in opposite directions: `servers` and
    # `tool` are modules serving MCP, `mcp_servers` and `mcp_call` are modules
    # *calling* it.

    def servers(self, role: str = None):
        """Every module with the MCP endpoint and the mod name it answers to."""
        got = self._get('/servers')
        if role and isinstance(got, dict) and got.get('servers'):
            got['servers'] = [s for s in got['servers'] if s.get('role') == role]
            got['count'] = len(got['servers'])
        return got

    def tools(self, module: str = ''):
        """The MCP tools on offer — the arena's own, or one module's."""
        if not module:
            return self._get('/tools').get('tools', [])
        return self._get(f'/m/{module}/tools')

    def tool(self, module: str, tool: str, arguments=None, **kwargs):
        """Call a tool on one module's own server.

            m arena/tool module=nim-rs tool=open
            m arena/tool module=bot-perfect tool=play arguments='{"view":"…"}'
        """
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments.strip() else {}
        args = dict(arguments or {})
        args.update({k: v for k, v in kwargs.items() if not k.startswith('_')})
        return self.mcp_call('module_tool',
                             {'module': module, 'tool': tool, 'arguments': args})

    def mcp_servers(self):
        """The MCP servers a class running here may call out to.

        A class names one of these — never a URL — and the arena makes the
        call, so the sandbox never opens a socket and the credentials never
        reach the code that uses them. The list lives in
        ~/.mod/arena/mcp_servers.json.
        """
        return self._get('/mcp/servers')

    def ask_mcp(self, server: str, tool: str = '', arguments=None, **kwargs):
        """Call a tool on one of those servers — the same door a class uses.

            m arena/ask_mcp server=arena tool=leaderboard
            m arena/ask_mcp server=arena            # lists its tools
        """
        if isinstance(arguments, str):
            arguments = json.loads(arguments) if arguments.strip() else {}
        args = dict(arguments or {})
        args.update({k: v for k, v in kwargs.items() if not k.startswith('_')})
        return self._post('/mcp/call', {'server': server, 'tool': tool, 'arguments': args})

    # ── every module, as a nested mod ────────────────────────────
    #
    # orbit/arena/mods/<slug>/ — inside the arena, because a game is not a
    # peer of the arena. The fleet's tree finds them anyway: `mods` is a name
    # it drops when it turns a path into a module name, so the directory
    # orbit/arena/mods/nim is the module `arena.nim`. See src/games.py.

    def mint(self, prune: bool = True, migrate: bool = True):
        """Write a mod for every module the arena holds, and drop the stale.

            m arena/mint
            m arena.nim-rs/open
            m arena.bot-perfect/ask view='Legal moves: 1, 2, 3'

        Idempotent, and the registry is the truth: this writes what the arena
        says exists and removes what it does not.
        """
        from . import games as G
        return G.sync(base=self.server_url, prune=prune, migrate=migrate)

    def mods(self):
        """The minted mods, read from the directories themselves."""
        from . import games as G
        return {'count': len(G.minted()), 'mods': G.minted(), 'dir': G.MODS_DIR}

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
        """Build the Rust backend. examples=1 also recompiles the wasm pack.

        The class examples are not built by anything — they are .py files, and
        that is most of the argument for classes.
        """
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
        describes the example pack, wasm executes, a match between the
        reference bot and the random one goes the way it has to, and a class
        uploaded as text becomes a game that two class players can be sat at."""
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

        # The class layer: upload one as text, and it is playable — through
        # the same runner, into the same leaderboard.
        try:
            made = self.upload(source=self.template('player'), name='_test_class')
            out['class_upload'] = {'role': made.get('role'), 'lang': made.get('lang'),
                                   'id': (made.get('id') or '')[:12]}
            self.enter('_test_centre', 'class', {'module': 'center'})
            self.enter('_test_lucky', 'class', {'module': 'lucky'})
            m = self.play('connect4', ['_test_centre', '_test_lucky'], seed=5)
            scores = {s['player_name']: s['score'] for s in m.get('seats', [])}
            illegal = sum(s.get('illegal', 0) for s in m.get('seats', []))
            out['class_match'] = {'summary': m.get('summary'), 'scores': scores,
                                  'illegal': illegal, 'rated': m.get('rated')}
            out['class_ok'] = (made.get('role') == 'player'
                               and bool(m.get('rated')) and illegal == 0)
        except Exception as e:
            out['class_error'] = str(e)
            out['class_ok'] = False
        finally:
            for name in ['_test_centre', '_test_lucky']:
                try:
                    self.withdraw(name)
                except Exception:
                    pass
            try:
                self.rm('_test_class')
            except Exception:
                pass

        out['ok'] = (bool(out.get('arena_ok')) and bool(out.get('wasi', {}).get('ok'))
                     and bool(out.get('class_ok')))
        return out
