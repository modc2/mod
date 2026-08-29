"""rps — a wasm game in the arena.

Rock, paper, scissors — best of five, both seats throwing at once. The simplest game with simultaneous moves.

Nothing of it is here. The bytes live in the arena under the hash of
themselves, and this directory is a pointer: a config.json, and the methods
below, which are a thin face over two things the arena already serves —

    the REST API      http://…/api/arena
    its own MCP server  /m/rps/mcp — this module alone, as a server

`mcp_config()` prints the block that points an MCP client straight at it.
"""
import json
import os
import urllib.error
import urllib.request


class Mod:
    path = os.path.dirname(os.path.abspath(__file__))
    slug = 'rps'

    def config(self):
        with open(os.path.join(self.path, 'config.json')) as f:
            return json.load(f)

    def _base(self):
        """The arena, from wherever this is called."""
        base = os.environ.get('ARENA_BASE')
        if base:
            return base.rstrip('/')
        try:
            import mod as m
            return m.mod('arena')()._up().rstrip('/')
        except Exception:
            return 'http://127.0.0.1:50470'

    def _rpc(self, method, params=None, timeout=300):
        """One JSON-RPC call to this module's own MCP server."""
        body = json.dumps({
            'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {},
        }).encode()
        url = f'{self._base()}/m/{self.slug}/mcp'
        req = urllib.request.Request(
            url, data=body, headers={'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode())
        except urllib.error.URLError as e:
            return {'error': f'{url} is unreachable: {e} — is the arena running? '
                             '`m arena/serve`'}
        if 'error' in out:
            return {'error': out['error'].get('message', 'the call failed')}
        return out.get('result', {})

    def call(self, tool: str, **arguments):
        """Call one tool on this module's own MCP server."""
        out = self._rpc('tools/call', {'name': tool, 'arguments': arguments})
        if 'error' in out:
            return out
        if out.get('isError'):
            return {'error': (out.get('content') or [{}])[0].get('text', 'failed')}
        if 'structuredContent' in out:
            return out['structuredContent']
        return out

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        """The card: what this is, where its bytes are, what it can be asked."""
        live = self.call('about')
        return live if 'error' not in live else self.config()

    def mcp(self):
        """This module's own MCP endpoint, and what it offers there."""
        return {
            'url': f'{self._base()}/m/{self.slug}/mcp',
            'transport': 'streamable-http',
            'tools': self.tools(),
        }

    def tools(self):
        out = self._rpc('tools/list')
        return out.get('tools', out)

    def mcp_config(self):
        """A client config block pointing at this one module."""
        return {'mcpServers': {self.slug: {
            'type': 'http', 'url': f'{self._base()}/m/{self.slug}/mcp'}}}

    def source(self):
        """The module itself — the source of a class, the exports of a binary."""
        return self.call('source')

    def abi(self):
        """The contract this module implements."""
        config = self.config()
        if config.get('abi'):
            return config['abi']
        import mod as m
        lang = {'python': 'class', 'rust': 'rust'}.get(config.get('lang'), 'wasm')
        return m.mod('arena')().abi(role=config.get('role', 'game'), lang=lang)

    # ── playing it, a turn at a time ──────────────────────────────────
    #
    # A table is its seed and the moves played at it, and nothing else. Every
    # call replays it from the start, so a table survives the arena
    # restarting and the same seed and moves are the same game anywhere.

    def open(self, seats: int = None, seed: int = None):
        """Sit down. Returns a table id and the opening view for every seat."""
        args = {}
        if seats:
            args['seats'] = int(seats)
        if seed is not None:
            args['seed'] = int(seed)
        return self.call('open', **args)

    def view(self, table: str, seat: int = 0):
        """What one seat can see — the whole of what a player is entitled to."""
        return self.call('view', table=table, seat=int(seat))

    def move(self, table: str, move: str = None, seat: int = None, moves: dict = None):
        """Play one move. `moves` instead, for a game where seats move at once."""
        args = {'table': table}
        if moves:
            args['moves'] = moves
        else:
            args['move'] = move
            if seat is not None:
                args['seat'] = int(seat)
        return self.call('move', **args)

    def state(self, table: str):
        """Whose move it is, every seat's view, and the result if it is over."""
        return self.call('state', table=table)

    # ── playing it out, rated ─────────────────────────────────────────

    def play(self, players, seed: int = None, turns: int = None, mcp=None):
        """Play a full match. The arena runs it and rates the result."""
        import mod as m
        return m.mod('arena')().play(game=self.slug, players=players,
                                     seed=seed, turns=turns, mcp=mcp)

    def leaderboard(self, limit: int = 10):
        """Who is good at THIS game — never the overall number."""
        return self.call('leaderboard', limit=int(limit))
