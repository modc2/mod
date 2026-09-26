"""sui — the whole chain behind one mod.

Sui has an identification problem that Solana only hints at: an account address
and an object ID are both 32 bytes of hex, and nothing distinguishes them. A
`0x…` string might be somebody's wallet, an NFT, a coin, a shared object or a
published package, and each of those wants a different question asked of it.

So this module starts by asking the chain rather than guessing from shape.

    m sui/what 0x2                        # a package, as it happens
    m sui/what bob.sui                    # SuiNS, then whatever it points at
    m sui/portfolio <address>             # everything held, staked SUI included
    m sui/tx <digest>                     # what one transaction actually moved
    m sui/package 0x2 module=coin         # what a contract can be asked to do
    m sui/network                         # epoch, checkpoint, TPS, gas price
    m sui/wallet create name=hot          # a key, stored off-tree
    m sui/send <to> 0.01 dry_run=1        # simulated, signed nothing

The same code answers the REST API, the browser console and seventeen MCP
tools, so an agent, a shell and a human never see different answers.

`m sui/serve` runs all three on one port.
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
    sui — read the chain and move value on it. Identify any 0x string (an
    address and an object ID look identical), price everything an address holds
    including staked SUI, decode a transaction into what moved per owner, read
    a Move package's callable functions before you call them, and sign
    transfers with a key that never leaves this box. Seventeen MCP tools, a
    REST API and a console on one port.
    """

    def __init__(self, network=None, rpc=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50740))
        self.base = cfg.get('base_path', '/sui')
        self._network, self._rpc = network, rpc

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def client(self, network=None, rpc=None):
        from chain import Client
        return Client(network=network or self._network, rpc=rpc or self._rpc)

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    # ── reading ──────────────────────────────────────────────────

    def what(self, query, network=None, rpc=None):
        """What a string IS — address, object, coin, package, digest or name."""
        return self.client(network, rpc).what(query)

    identify = what

    def balance(self, address, coin_type='0x2::sui::SUI', network=None, rpc=None):
        """One coin type for one address or several, comma-separated, in USD."""
        return self.client(network, rpc).balance(address, coin_type)

    def portfolio(self, address, min_usd=0.01, include_dust=False, limit=100,
                  network=None, rpc=None):
        """Every coin an address holds, priced, plus staked SUI and objects."""
        return self.client(network, rpc).portfolio(
            address, min_usd=min_usd, include_dust=include_dust, limit=limit)

    holdings = portfolio

    def objects(self, address, type=None, limit=50, cursor=None, network=None, rpc=None):
        """Objects an address owns — NFTs, coins, capabilities, receipts."""
        return self.client(network, rpc).objects(address, type=type, limit=limit,
                                                 cursor=cursor)

    def object(self, object_id, network=None, rpc=None):
        """One object in full, and — the part that matters — how it is owned."""
        return self.client(network, rpc).object(object_id)

    def coin(self, coin_type, network=None, rpc=None):
        """A coin type: decimals, supply, market cap, liquidity, price."""
        return self.client(network, rpc).coin(coin_type)

    def price(self, ids, network=None, rpc=None):
        """USD price by coin type or symbol. Symbols resolve by liquidity."""
        return self.client(network, rpc).price(ids)

    def history(self, address, limit=20, cursor=None, direction='both', detail=False,
                network=None, rpc=None):
        """Recent transactions, newest first, with the net change for this address."""
        return self.client(network, rpc).history(
            address, limit=limit, cursor=cursor, direction=direction, detail=detail)

    def tx(self, digest, events=False, network=None, rpc=None):
        """One transaction, decoded into what moved and which commands ran."""
        return self.client(network, rpc).tx(digest, events=events)

    def network(self, network=None, rpc=None):
        """Epoch, checkpoint, TPS, gas price, stake — the state of the chain."""
        return self.client(network, rpc).status()

    status = network

    def validators(self, limit=20, sort='stake', network=None, rpc=None):
        """The validator set by stake, with APY and the Nakamoto coefficient."""
        return self.client(network, rpc).validators(limit=limit, sort=sort)

    def stake(self, address, network=None, rpc=None):
        """Delegated SUI — the half of a balance that no balance call shows."""
        return self.client(network, rpc).stakes(address)

    def package(self, package, module=None, limit=40, network=None, rpc=None):
        """What a published package can DO. Move keeps its interface on chain."""
        return self.client(network, rpc).package(package, module=module, limit=limit)

    def rpc(self, method, params=None, network=None, rpc=None):
        """Any Sui JSON-RPC method, raw — the escape hatch."""
        return self.client(network, rpc).rpc(method, params)

    # ── keys and money ───────────────────────────────────────────

    def wallet(self, action='list', name=None, secret=None, default=None,
               overwrite=False):
        """The off-tree keystore: list, create, import, remove, default, export."""
        import mcp
        return mcp.call_tool('sui_wallet', {
            'action': action, 'name': name, 'secret': secret, 'default': default,
            'overwrite': overwrite})

    def send(self, to, amount, coin_type='0x2::sui::SUI', wallet=None, secret=None,
             dry_run=False, confirm=False, network=None, rpc=None):
        """Send SUI or any coin. Simulated first, always; guarded by value."""
        return self.client(network, rpc).transfer(
            to, amount, coin_type=coin_type, wallet=wallet, secret=secret,
            dry_run=dry_run, confirm=confirm)

    transfer = send

    def faucet(self, address=None, wallet=None, network='testnet', rpc=None):
        """Test SUI from the testnet or devnet faucet. There is no mainnet one."""
        return self.client(network, rpc).faucet(address, wallet=wallet)

    # ── surfaces ─────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry, as an agent sees it."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}

    def mcp_call(self, tool, **args):
        """Invoke one MCP tool directly, without a transport in the way."""
        import mcp
        return mcp.call_tool(tool, args)

    def mcp_config(self, url=None):
        """Drop-in client config for anything that speaks MCP over HTTP."""
        return {'mcpServers': {'sui': {
            'type': 'http', 'url': url or f'http://localhost:{self.port}/mcp'}}}

    def serve(self, port=None, background=False):
        """Run the REST API, the console and the MCP server on one port."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, 'api.py'),
                                 '--port', str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                cwd=HERE)
        return {'pid': proc.pid, 'port': port,
                'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp'}

    def kill(self, port=None):
        """Stop whatever is holding the port. Targets the port, never a name —
        this box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', '-9', pid], capture_output=True)
        return {'port': port, 'killed': pids}

    def test(self, offline=False):
        """Run the test suite. offline=1 skips everything that needs the chain."""
        env = {**os.environ, **({'SUI_OFFLINE': '1'} if offline else {})}
        out = subprocess.run([sys.executable, '-m', 'pytest', '-q'],
                             cwd=HERE, env=env, capture_output=True, text=True)
        return {'ok': out.returncode == 0, 'output': out.stdout[-4000:] or out.stderr[-4000:]}

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()
