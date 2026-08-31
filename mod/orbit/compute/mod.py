"""compute — one interface to every compute market.

Targon and Lium (Bittensor SN4/SN51), Akash, Vast.ai, Clore, Nosana, Aleph
Cloud, Cathedral (confidential TDX), Prime Intellect, Polaris, Hyperbolic,
RunPod, Fluence, Shadeform, and your own docker hosts, behind one vocabulary:

    m compute/search gpu=H100 max_usd_hr=3      # every market at once, cheapest first
    m compute/quote id=lium:b7095b41 hours=4    # cost + what it costs elsewhere
    m compute/rent id=lium:b7095b41 confirm=1   # spends YOUR credits
    m compute/instances                         # everything running, with burn rate
    m compute/stop id=lium:pod-…                # ends the billing

Same surface as MCP tools (`compute_search`, `compute_rent`, …) at POST /mcp,
so an agent and a human drive the identical code. Keys are per provider and
per caller: env, ~/.mod/compute/keys.json, or the sibling module's key file —
this module never holds a house key.

`m compute/serve` runs the API, the console and the MCP server on one port.
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
    compute — aggregate every crypto-friendly compute market behind one set of
    verbs (search / quote / rent / instances / logs / stop / balance) and one
    MCP server. No-KYC markets first: Bittensor (Targon SN4, Lium SN51), Akash,
    Vast.ai, Nosana, plus confidential compute (Cathedral) and your own hosts.
    """

    def __init__(self, keys=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50510))
        self.base = cfg.get('base_path', '/compute')
        self._keys = keys or {}

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    @property
    def hub(self):
        from hub import Hub
        return Hub(keys=self._keys)

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    # ── the eight verbs ──────────────────────────────────────────

    def providers(self, kyc=None, cap=None, provider=None):
        """Every market: capabilities, KYC level, payment rails, key state."""
        return self.hub.providers(kyc=kyc, cap=cap, names=provider)

    def mods(self, mod=None, compare=True, sample=6, gpu=None, max_usd_hr=None,
             min_gpus=None, min_vram_gb=None, kind=None):
        """The markets that also run as their own modules here (targon, lium,
        cathedral), read through those modules and set against the direct lane."""
        import mods as lanes
        return lanes.lane(names=mod, keys=self._keys, compare=compare, sample=sample,
                          gpu=gpu, max_usd_hr=max_usd_hr, min_gpus=min_gpus,
                          min_vram_gb=min_vram_gb, kind=kind)

    def search(self, gpu=None, min_gpus=None, min_vram_gb=None, max_usd_hr=None,
               region=None, provider=None, kyc=None, kind=None, sort='price',
               limit=40, available_only=True):
        """Search every market at once, cheapest first, in one shape."""
        return self.hub.search(gpu=gpu, min_gpus=min_gpus, min_vram_gb=min_vram_gb,
                               max_usd_hr=max_usd_hr, region=region, provider=provider,
                               kyc=kyc, kind=kind, sort=sort, limit=limit,
                               available_only=available_only)

    ls = search

    def offer(self, id):
        """Re-read one offer from its provider (id = provider:ref)."""
        return self.hub.offer(id)

    def quote(self, id, hours=1):
        """Cost for N hours, the spend-guard verdict, and cheaper alternatives."""
        return self.hub.quote(id, hours=hours)

    def rent(self, id, hours=1, confirm=False, **opts):
        """Rent an offer. Spends your credits until `stop`. Guarded — see quote."""
        return self.hub.rent(id, hours=hours, confirm=confirm, **opts)

    up = rent

    def instances(self, provider=None):
        """Everything running across every market, with the combined burn rate."""
        return self.hub.instances(provider=provider)

    ps = instances

    def status(self, id):
        """One rental: state, price, ssh line."""
        return self.hub.status(id)

    def logs(self, id, tail=200):
        """A rental's logs."""
        return self.hub.logs(id, tail=tail)

    def exec(self, id, cmd):
        """Run a command inside a rental, where the provider supports it."""
        return self.hub.exec(id, cmd)

    def stop(self, id):
        """Stop a rental — this is what ends the billing."""
        return self.hub.stop(id)

    down = rm = stop

    def balance(self, provider=None):
        """Prepaid balance on every provider account you hold a key for."""
        return self.hub.balance(provider=provider)

    # ── nodes: the rented box, running mod ───────────────────────

    def deploy(self, id=None, ssh=None, instance=None, docker=None, name=None,
               hours=1, confirm=False, image=None, profile='lite', ports=None,
               gpus=None, wait=True, **kwargs):
        """Rent (or adopt) a box and install the mod protocol on it.

            m compute/deploy docker=1                    # a container, right here
            m compute/deploy ssh=root@1.2.3.4            # a box you already have
            m compute/deploy id=vast:46240433 confirm=1  # rent one, then install
        """
        import node
        return node.deploy(id=id, ssh=ssh, instance=instance, docker=docker,
                           name=name, hours=hours, confirm=confirm, image=image,
                           profile=profile, ports=ports, gpus=gpus, wait=wait,
                           keys=self._keys)

    def nodes(self, state=None):
        """Every node, with what it is running and what it is costing."""
        import node
        return node.nodes(state=state)

    def node(self, id, probe=True):
        """One node, re-probed over its own transport."""
        import node as N
        return N.probe(id) if probe else N._public(N.get(id))

    def node_sh(self, id, cmd, timeout=120):
        """Run a command on a node."""
        import node
        return node.sh(id, cmd, timeout=timeout)

    def node_mods(self, id, mod=None):
        """The modules on a node — or one module's fns."""
        import node
        return node.ctl(id, 'fns', mod=mod) if mod else node.ctl(id, 'mods')

    def node_call(self, id, mod, fn='forward', args=None, kwargs=None, init=None,
                  **extra):
        """Call a module function on a node and bring the answer back."""
        import node
        return node.call(id, mod, fn=fn, args=args, kwargs={**(kwargs or {}), **extra},
                         init=init)

    def node_push(self, id, mod, restart=False):
        """Send a local module to a node, exactly as it is on this disk."""
        import node
        return node.push(id, mod, restart=restart)

    def node_bootstrap(self, id, profile='lite', force=False, wait=True):
        """(Re)install mod on a node. Idempotent."""
        import node
        return node.bootstrap(id, profile=profile, force=force, wait=wait)

    def node_sync(self, id):
        """Re-ask the market for a young rental's SSH line, then probe."""
        import node
        return node.sync(id, keys=self._keys)

    def node_tunnel(self, id, port, local_port=None):
        """Bring a port on a node back to localhost."""
        import node
        return node.tunnel(id, port, local_port=local_port)

    def node_rm(self, id, release=False):
        """Tear a node down. release=1 also stops the rental — that ends billing."""
        import node
        return node.destroy(id, release=release, keys=self._keys)

    def node_forget(self, id):
        """Drop a node from the registry without touching the box."""
        import node
        return node.forget(id)

    # ── keys ─────────────────────────────────────────────────────

    def token(self):
        """The bearer token for this server's owner-only routes."""
        import auth
        return {'token': auth.secret(), 'file': auth.SECRET_FILE,
                'use': 'Authorization: Bearer <token> — or call from localhost, '
                       'where the console is handed it automatically'}

    def identity(self):
        """The SSH public key this module hands to markets when it rents."""
        import node
        return node.keypair()

    def set_key(self, provider, key, persist=True):
        """Store one provider's key in ~/.mod/compute/keys.json (0600, off-tree)."""
        return self.hub.set_key(provider, key, persist=persist)

    def keys(self):
        """Which providers have a key — never the keys themselves."""
        import providers as P
        return {n: cls(key=self._keys.get(n)).key_state()
                for n, cls in P.REGISTRY.items()}

    def raw(self, provider, path, method='GET', body=None, params=None):
        """Escape hatch: call a provider's own API with that provider's key."""
        return self.hub.raw(provider, path, method=method, body=body, params=params)

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
            'compute': {'command': 'python3', 'args': [os.path.join(HERE, 'mcp.py')]},
            'compute-http': {'type': 'http', 'url': f'http://localhost:{self.port}/mcp'},
        }}

    # ── serve ────────────────────────────────────────────────────

    def serve(self, port=None, background=True, **kwargs):
        """Run API + console + MCP on one port, under pm2 as compute-api."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        subprocess.run(['pm2', 'start', sys.executable, '--name', 'compute-api',
                        '--cwd', HERE, '--', os.path.join(HERE, 'api.py'),
                        '--port', str(port)],
                       cwd=HERE, env=env, capture_output=True)
        return {'api': f'http://localhost:{port}',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp',
                'process': 'compute-api'}

    def kill(self, **kwargs):
        """Stop the server."""
        killed = []
        for name in ('compute-api', 'compute.api', 'compute-app'):
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
