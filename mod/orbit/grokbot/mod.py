"""grokbot — Grok, with your key and your bots.

xAI's API is one endpoint and a bearer token; the interesting question is
*whose* token. This module answers it the way the rest of the fleet does: you
sign in from the website with a wallet, that mints a mod-protocol token, and
the address inside it is the account your xAI key and your saved bots hang off.
Nothing is stored in this repo — keys live at ~/.mod/grokbot/users/<address>.json,
0600, off-tree.

    m grokbot/serve                                   # api + console + mcp
    m grokbot/set_key key=xai-…                       # the operator's own key
    m grokbot/models                                  # what your key can see
    m grokbot/ask "what happened on X today" search=auto
    m grokbot/save_bot name=skeptic system="…" model=grok-4-fast
    m grokbot/ask "is this true?" bot=skeptic

The same code answers the REST API, the browser console and ten MCP tools, so
an agent, a shell and a human never see different answers.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    grokbot — the Grok (xAI) API as one mod: sign in from the website with a
    wallet, save your own xAI key, keep named bots (a model + a system prompt),
    and chat with live search over X and the web. BYOK — every call spends the
    caller's own xAI credits.
    """

    def __init__(self, key=None, token=None, address=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT')
                        or os.environ.get('GROKBOT_PORT') or cfg.get('port', 50890))
        self.base = cfg.get('base_path', '/grokbot')
        self._key, self._token, self._address = key, token, address

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    @property
    def client(self):
        from client import Client
        return Client(key=self._key, address=self._who())

    def _who(self):
        """The address this call speaks for — a token if given, else local."""
        import identity
        if self._address:
            return self._address.lower()
        if self._token:
            return identity.require(self._token)
        return identity.whoami(None) or identity.owner()

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    def me(self):
        """Who you are here, whether a key resolved, and from where."""
        import identity
        address = self._who()
        return {'address': address, 'role': identity.role(address),
                'key': self.client.key_state(),
                'bots': [b['name'] for b in self.bots()] if address else [],
                'auth': identity.status()}

    # ── catalog ──────────────────────────────────────────────────

    def models(self, refresh=False):
        """Every model your key can see. xAI needs a key even to list them."""
        return self.client.models(refresh=refresh)

    ls = models

    def model(self, id):
        """One model: modalities and USD-per-million prices."""
        return self.client.model(id)

    def key_info(self):
        """What xAI says about the key itself — name, blocked, permissions."""
        return self.client.key_info()

    # ── inference ────────────────────────────────────────────────

    def chat(self, prompt=None, messages=None, model=None, system=None, bot=None,
             temperature=None, max_tokens=None, search=None, **opts):
        """One completion, with the full result: text, usage, citations."""
        return self.client.chat(prompt=prompt, messages=messages, model=model,
                                system=system, bot=bot, temperature=temperature,
                                max_tokens=max_tokens, search=search, **opts)

    def ask(self, prompt, **opts):
        """The one-liner: `m grokbot/ask "…" search=auto` → just the text."""
        out = self.chat(prompt=prompt, **opts)
        return out.get('text', out) if isinstance(out, dict) else out

    def image(self, prompt, model='grok-2-image', n=1, **opts):
        """Grok's image generation."""
        return self.client.images(prompt, model=model, n=n, **opts)

    # ── bots ─────────────────────────────────────────────────────

    def bots(self):
        """The bots saved against your address."""
        import client
        return client.bots(self._who())

    def bot(self, name):
        """One saved bot."""
        import client
        return client.get_bot(self._who(), name)

    def save_bot(self, name, system=None, model=None, temperature=None,
                 search=None, description=None):
        """Create or update a bot — a name, a model, a system prompt."""
        import client
        return client.save_bot(self._who(), name, system=system, model=model,
                               temperature=temperature, search=search,
                               description=description)

    def delete_bot(self, name):
        """Delete one of your bots."""
        import client
        return client.delete_bot(self._who(), name)

    # ── keys ─────────────────────────────────────────────────────

    def set_key(self, key, persist=True, mine=False):
        """Store an xAI key — the operator's fallback, or `mine=1` for yours."""
        import client
        if mine:
            return client.set_user_key(self._who(), key, persist=persist)
        return client.set_key(key, persist=persist)

    def raw(self, path, method='GET', body=None, params=None):
        """Escape hatch: any xAI route, with the resolved key attached."""
        return self.client.raw(path, method=method, body=body, params=params)

    # ── mcp ──────────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry this module serves."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS)}

    def mcp_call(self, tool, arguments=None, **kwargs):
        """Call one MCP tool in-process — the same path the server takes."""
        import mcp
        return mcp.call_tool(tool, {**(arguments or {}), **kwargs},
                             token=self._token, key=self._key)

    def mcp_config(self):
        """Drop-in client config for Claude Code / Desktop and friends."""
        return {'mcpServers': {
            'grokbot': {'command': 'python3', 'args': [os.path.join(HERE, 'mcp.py')]},
            'grokbot-http': {'type': 'http',
                             'url': f'http://localhost:{self.port}/mcp'},
        }}

    # ── serve ────────────────────────────────────────────────────

    def serve(self, port=None, background=True, **kwargs):
        """Run API + console + MCP on one port, under pm2 as grokbot-api."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        subprocess.run(['pm2', 'start', sys.executable, '--name', 'grokbot-api',
                        '--cwd', HERE, '--', os.path.join(HERE, 'api.py'),
                        '--port', str(port)],
                       cwd=HERE, env=env, capture_output=True)
        return {'api': f'http://localhost:{port}',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp',
                'process': 'grokbot-api'}

    def kill(self, **kwargs):
        """Stop the server."""
        killed = []
        for name in ('grokbot-api', 'grokbot.api', 'grokbot-app'):
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    def test(self, **kwargs):
        """Run the module's tests."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                            os.path.join(HERE, 'tests')],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()
