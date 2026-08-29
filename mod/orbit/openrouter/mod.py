"""openrouter — one key, every model.

OpenRouter fronts 400+ models from 100+ providers behind an OpenAI-shaped API.
This module puts all of it — the catalog, the routing, the money — behind the
mod protocol:

    m openrouter/models tools=1 max_prompt_usd_m=1 sort=price   # what's cheap and capable
    m openrouter/endpoints id=moonshotai/kimi-k2                # who serves it, how fast
    m openrouter/cost prompt_tokens=8000 completion_tokens=2000 # what a call would cost
    m openrouter/ask "explain the mod protocol" model=…         # make the call
    m openrouter/generation id=gen-…                            # what it really cost
    m openrouter/key                                            # usage, limit, balance

The same code answers the REST API, the browser console and twelve MCP tools,
so an agent, a shell and a human never see different answers.

`m openrouter/serve` runs the API, the console and the MCP server on one port.
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
    openrouter — the whole OpenRouter API as one mod: search 400+ models by
    price, context, modality and capability; compare the providers serving a
    model by price, uptime and throughput; run chat or completion with full
    provider routing and a spend guard; and read back what it really cost. BYOK
    — every call spends the caller's own credits.
    """

    def __init__(self, key=None, provisioning_key=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50600))
        self.base = cfg.get('base_path', '/openrouter')
        self._key, self._prov = key, provisioning_key

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
        return Client(key=self._key, provisioning_key=self._prov)

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    # ── catalog ──────────────────────────────────────────────────

    def models(self, q=None, tools=None, reasoning=None, structured=None, free=None,
               modality=None, input=None, output=None, min_context=None,
               max_prompt_usd_m=None, max_completion_usd_m=None, provider=None,
               sort='price', limit=40, refresh=False):
        """Search the catalog. Prices are USD per MILLION tokens."""
        return self.client.search(
            q=q, tools=tools, reasoning=reasoning, structured=structured, free=free,
            modality=modality, input=input, output=output, min_context=min_context,
            max_prompt_usd_m=max_prompt_usd_m, max_completion_usd_m=max_completion_usd_m,
            provider=provider, sort=sort, limit=limit, refresh=refresh)

    ls = models

    def model(self, id, endpoints=True):
        """One model in full: prices, limits, parameters, and who serves it."""
        return self.client.model(id, endpoints=endpoints)

    def endpoints(self, id):
        """Every provider serving a model — price, quantization, uptime, throughput."""
        return self.client.endpoints(id)

    def providers(self, q=None):
        """The provider catalog: who they are, where they are, what they promise."""
        return self.client.providers(q=q)

    # ── inference ────────────────────────────────────────────────

    def chat(self, model=None, prompt=None, system=None, messages=None, models=None,
             temperature=None, max_tokens=None, provider=None, reasoning=None,
             tools=None, response_format=None, transforms=None, confirm=False, **opts):
        """One chat completion. Spends your credits; guarded above the ceiling."""
        return self.client.chat(
            model=model, prompt=prompt, system=system, messages=messages, models=models,
            temperature=temperature, max_tokens=max_tokens, provider=provider,
            reasoning=reasoning, tools=tools, response_format=response_format,
            transforms=transforms, confirm=confirm, **opts)

    def ask(self, prompt, model='openrouter/auto', **opts):
        """The one-liner: `m openrouter/ask "…" model=…` → just the text."""
        out = self.chat(model=model, prompt=prompt, **opts)
        return out.get('text', out) if isinstance(out, dict) else out

    def complete(self, model, prompt, **opts):
        """The legacy text-completion route, for base models."""
        return self.client.complete(model, prompt, **opts)

    # ── money ────────────────────────────────────────────────────

    def cost(self, prompt_tokens=1000, completion_tokens=1000, model=None, limit=15,
             **filters):
        """Price a call — a quote for one model, or the catalog ranked by it."""
        return self.client.cost(prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                model=model, limit=limit, **filters)

    def cheapest(self, prompt_tokens=1000, completion_tokens=1000, limit=10, **filters):
        """The cheapest models that still match the filters, priced for this call."""
        return self.cost(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                         limit=limit, **filters)

    def generation(self, id):
        """What a finished generation really cost, in native provider tokens."""
        return self.client.generation(id)

    def key(self):
        """Key label, usage, limits, rate limit, and the credit balance."""
        return self.client.key_info()

    def credits(self):
        """Credits purchased vs used."""
        return self.client.credits()

    # ── keys ─────────────────────────────────────────────────────

    def set_key(self, key=None, provisioning_key=None, persist=True):
        """Store your key in ~/.mod/openrouter/key.json (0600, off-tree)."""
        import client
        return client.set_key(key=key, provisioning_key=provisioning_key, persist=persist)

    def provision(self, action='list', **opts):
        """List / create / update / delete inference keys (provisioning key only)."""
        return self.client.provision(action=action, **opts)

    def raw(self, path, method='GET', body=None, params=None, provisioning=False):
        """Escape hatch: any OpenRouter route, with your key attached."""
        return self.client.raw(path, method=method, body=body, params=params,
                               provisioning=provisioning)

    # ── mcp ──────────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry this module serves."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS)}

    def mcp_call(self, tool, arguments=None, **kwargs):
        """Call one MCP tool in-process — the same path the server takes."""
        import mcp
        return mcp.call_tool(tool, {**(arguments or {}), **kwargs})

    def mcp_config(self):
        """Drop-in client config for Claude Code / Desktop and friends."""
        return {'mcpServers': {
            'openrouter': {'command': 'python3', 'args': [os.path.join(HERE, 'mcp.py')]},
            'openrouter-http': {'type': 'http',
                                'url': f'http://localhost:{self.port}/mcp'},
        }}

    # ── serve ────────────────────────────────────────────────────

    def serve(self, port=None, background=True, **kwargs):
        """Run API + console + MCP on one port, under pm2 as openrouter-api."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        subprocess.run(['pm2', 'start', sys.executable, '--name', 'openrouter-api',
                        '--cwd', HERE, '--', os.path.join(HERE, 'api.py'),
                        '--port', str(port)],
                       cwd=HERE, env=env, capture_output=True)
        return {'api': f'http://localhost:{port}',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp',
                'process': 'openrouter-api'}

    def kill(self, **kwargs):
        """Stop the server."""
        killed = []
        for name in ('openrouter-api', 'openrouter.api', 'openrouter-app'):
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    def test(self, **kwargs):
        """Run the module's tests."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                            os.path.join(HERE, 'test')],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()
