"""swarms — the Swarms protocol, both halves of it, as one mod.

Swarms is a multi-agent runtime and a Solana token, and neither half explains
itself without the other. api.swarms.world takes a task and a roster of agents
and runs them through one of sixteen orchestration architectures; $swarms is
the SPL token the agent economy around it is priced in. This module puts both
behind the mod protocol, the REST API, a browser console and eighteen MCP
tools — one implementation, four transports:

    m swarms/architectures                  the sixteen, and what each is for
    m swarms/build "audit this contract"    task in, agent roster out
    m swarms/cost agents=5 loops=3          what that would cost, before it runs
    m swarms/run "…" agents=a,b,c type=…    run it
    m swarms/market kind=prompts q=trading  what other people have published

    m swarms/token                          $swarms: supply, price, venues, FDV
    m swarms/holders                        where the supply actually sits
    m swarms/balance owner=…                what one wallet holds
    m swarms/quote side=buy amount=1        what a position would cost

    m swarms/serve                          API + console + MCP on :50690

BYOK: every completion spends the caller's own Swarms credits. The chain half
needs no key and holds none — it reads, and `quote` prices a swap without
being able to sign one.
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
    swarms — the Swarms protocol as one mod: run multi-agent swarms on
    api.swarms.world across sixteen orchestration architectures, browse the
    swarms.world marketplace, and read the $swarms SPL token on Solana (price,
    supply, holders, wallet balances, swap quotes). Eighteen MCP tools serve
    the same code the REST API and the console do. BYOK for the runtime;
    read-only, keyless and unsigned for the chain.
    """

    def __init__(self, key=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50690))
        self.base = cfg.get('base_path', '/swarms')
        self._key = key

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
        return Client(key=self._key)

    def info(self):
        """What this module is, and every route it serves."""
        import server as api
        return api.info()

    forward = info

    # ── designing a swarm ────────────────────────────────────────

    def architectures(self, refresh=False):
        """The sixteen swarm types, with what each is best for."""
        return self.client.swarm_types(refresh=refresh)

    # `m swarms/types` reads better at a prompt than `m swarms/architectures`.
    types = architectures

    def models(self, q=None, refresh=False):
        """Models the runtime will accept as `model_name`."""
        return self.client.models(q=q, refresh=refresh)

    def tools(self, **kwargs):
        """Hosted tools an agent can be given by name."""
        return self.client.tools()

    def build(self, task, model_name=None, confirm=False):
        """Task in, agent roster out — read it before you pay to run it."""
        return self.client.auto_build(task, model_name=model_name, confirm=confirm)

    def cost(self, agents=1, loops=1, input_tokens=2000, output_tokens=2000):
        """Price a run before making it. An upper bound, not a quote."""
        return self.client.cost(agents=agents, loops=loops, input_tokens=input_tokens,
                                output_tokens=output_tokens)

    # ── running one ──────────────────────────────────────────────

    def run(self, task=None, agents=None, type='auto', swarm_type=None, name=None,
            description=None, max_loops=1, confirm=False, **spec):
        """Run a swarm. `agents` takes names or full AgentSpec objects.

        `type` is the shell-friendly spelling of `swarm_type` — both work.
        """
        return self.client.swarm(task=task, agents=agents,
                                 swarm_type=swarm_type or type, name=name,
                                 description=description, max_loops=max_loops,
                                 confirm=confirm, **spec)

    def agent(self, task=None, model_name=None, system_prompt=None, agent_name=None,
              confirm=False, **kw):
        """One agent, one task — the cheap path when no committee is needed."""
        return self.client.agent(task=task, model_name=model_name,
                                 system_prompt=system_prompt, agent_name=agent_name,
                                 confirm=confirm, **kw)

    def reasoning(self, task, **kw):
        """A reasoning agent: self-consistency, reflection and friends."""
        return self.client.reasoning(task, **kw)

    def batch(self, jobs, confirm=False):
        """Parallel fan-out: many independent {agent_config, task} jobs."""
        return self.client.agent_batch(jobs, confirm=confirm)

    def ask(self, prompt=None, model=None, system=None, confirm=False, **kw):
        """One question, one answer, through the OpenAI-shaped shim."""
        return self.client.chat(prompt=prompt, model=model, system=system,
                                confirm=confirm, **kw)

    # ── the account ──────────────────────────────────────────────

    def account(self, **kwargs):
        """Key state, credits, rate limits and live pricing in one call."""
        import mcp as _mcp
        return _mcp.call_tool('swarms_account', {'key': self._key})

    def credits(self, **kwargs):
        return self.client.credits()

    def logs(self, limit=None):
        return self.client.logs(limit=limit)

    def set_key(self, key, persist=True):
        """Store a Swarms API key off-tree at ~/.mod/swarms/key.json (0600)."""
        import client
        return client.set_key(key=key, persist=persist)

    def market(self, kind='agents', q=None, limit=25):
        """The swarms.world marketplace — public, no key needed."""
        return self.client.market(kind=kind, q=q, limit=limit)

    def raw(self, path, method='GET', body=None, params=None, market=False):
        """Escape hatch: any Swarms route, with your key attached."""
        return self.client.raw(path, method=method, body=body, params=params,
                               market=market)

    # ── the token ────────────────────────────────────────────────

    def token(self, mint=None):
        """$swarms on Solana: identity, supply, price, venues, FDV."""
        import chain
        return chain.token(mint)

    def price(self, mint=None, limit=8):
        """Spot price, and every pool trading it by liquidity."""
        import mcp as _mcp
        return _mcp.call_tool('swarms_price', {'mint': mint, 'limit': limit})

    def supply(self, mint=None):
        import chain
        return chain.supply(mint)

    def holders(self, mint=None, limit=20):
        """Largest token accounts — a concentration signal, not a rich list."""
        import chain
        return chain.holders(mint, limit=limit)

    def balance(self, owner, mint=None):
        """What one Solana wallet holds, and what it is worth."""
        import chain
        return chain.balance(owner, mint)

    def quote(self, side='buy', amount=1, pay_with='SOL', slippage_bps=100, mint=None):
        """Price a swap. A quote — this module cannot sign or send one."""
        import chain
        return chain.quote(side=side, amount=amount, pay_with=pay_with,
                           slippage_bps=slippage_bps, mint=mint)

    def chain(self, **kwargs):
        """What the chain half is and where it reads from — no network call."""
        import chain as _chain
        return _chain.info()

    # ── mcp ──────────────────────────────────────────────────────

    def mcp_tools(self):
        """The MCP tool registry this module serves."""
        import mcp as _mcp
        return {'tools': _mcp.tool_list(), 'count': len(_mcp.TOOLS)}

    def mcp_call(self, tool, arguments=None, **kwargs):
        """Call one MCP tool in-process — the same path the server takes."""
        import mcp as _mcp
        return _mcp.call_tool(tool, {**(arguments or {}), **kwargs})

    def mcp_config(self, client='json', url=None):
        """Drop-in client config for Claude Code / Desktop and friends."""
        import server as api
        return api.mcp_config(client, url)

    # ── serve ────────────────────────────────────────────────────

    def serve(self, port=None, background=True, **kwargs):
        """Run API + console + MCP on one port, under pm2 as swarms-api."""
        port = int(port or self.port)
        if not background:
            import server as api
            return api.serve(port)
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        subprocess.run(['pm2', 'start', sys.executable, '--name', 'swarms-api',
                        '--cwd', HERE, '--', os.path.join(HERE, 'api.py'),
                        '--port', str(port)],
                       cwd=HERE, env=env, capture_output=True)
        return {'api': f'http://localhost:{port}',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp',
                'process': 'swarms-api'}

    def kill(self, **kwargs):
        """Stop the server."""
        killed = []
        for name in ('swarms-api', 'swarms.api', 'swarms-app'):
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
