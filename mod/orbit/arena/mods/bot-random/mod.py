"""bot-random — a wasm agent in the arena.

Reads the Legal moves: line and picks one at random. The floor every rating is measured against.

Nothing of it is here. The bytes live in the arena under the hash of
themselves, and this directory is a pointer: a config.json, and the methods
below, which are a thin face over two things the arena already serves —

    the REST API      http://…/api/arena
    its own MCP server  /m/bot-random/mcp — this module alone, as a server

`mcp_config()` prints the block that points an MCP client straight at it.
"""
import json
import os
import urllib.error
import urllib.request


class Mod:
    path = os.path.dirname(os.path.abspath(__file__))
    slug = 'bot-random'

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

    # ── asking it ─────────────────────────────────────────────────────

    def ask(self, view: str, seat: int = 0, seed: int = None):
        """One question: here is what a seat can see, what do you play?

        The same question the arena asks it in a rated match, so the answer
        is the answer.
        """
        args = {'view': view, 'seat': int(seat)}
        if seed is not None:
            args['seed'] = int(seed)
        return self.call('play', **args)

    def play(self, view: str, seat: int = 0):
        """`ask`, under the name the ABI uses."""
        return self.ask(view, seat)

    def record(self):
        """How it has done: Elo, win rate, illegal moves, timeouts, calls out."""
        return self.call('record')

    def enter(self, name: str = None, owner: str = ''):
        """Enter it at the arena under a name, so its matches are rated."""
        import mod as m
        return m.mod('arena')().enter(
            name=name or self.slug, kind='class', owner=owner,
            config={'module': self.config().get('artifact') or self.slug})
