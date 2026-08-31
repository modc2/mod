"""debank — what an address actually owns.

DeBank indexes every EVM chain and answers the one question a block explorer
cannot: not "what transactions happened" but "what does this wallet own right
now" — tokens, LP positions, staked and locked balances, debt, NFTs, and the
approvals that let someone else move it.

    m debank/portfolio 0xd8da…6045              # net worth, and which chains hold it
    m debank/tokens 0xd8da…6045 chain=eth       # priced balances, biggest first
    m debank/protocols 0xd8da…6045              # DeFi positions, net of borrowing
    m debank/approvals 0xd8da…6045 chain=eth    # who can still take it
    m debank/history 0xd8da…6045 chain=eth      # decoded transactions
    m debank/balances 0xd8da…6045             # keyless: native + stables, 8 chains
    m debank/funds amount=10000                 # savings index funds: ROI + liquidity
    m debank/savings 0xd8da…6045              # idle vs placed, read from chain
    m debank/tools                              # the twenty-four MCP tools

The same code answers the REST API, the browser console and the MCP tools, so
an agent, a shell and a human never see different answers.

BYOK: every call spends the caller's own DeBank units. `m debank/set_key <key>`
stores one at ~/.mod/debank/key.json (0600, off-tree). `m debank/chains` works
without one.
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
    debank — the DeBank Cloud API as one mod: net worth across every EVM chain,
    token balances priced and ranked, DeFi positions net of debt, NFTs, decoded
    transaction history, and live token approvals ranked by what a spender could
    take today. A bank console that connects to the browser wallet — send,
    receive, revoke — and a savings desk that places the account's stablecoins
    into curated index funds of yield venues, each with live projected ROI and
    the liquidity locked in the protocol. Twenty-four MCP tools over the same
    code. BYOK, with a keyless floor: native + stablecoin balances on 8 chains
    via public RPCs.
    """

    def __init__(self, key=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50720))
        self.base = cfg.get('base_path', '/debank')
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
        import api
        return api.info()

    forward = info

    # ── portfolio ────────────────────────────────────────────────

    def portfolio(self, id, min_usd=1.0):
        """Net worth across every chain, and which chains carry it."""
        return self.client.portfolio(id, min_usd=min_usd)

    def tokens(self, id, chain=None, min_usd=1.0, limit=100, all_tokens=False):
        """Wallet token balances, priced, biggest first."""
        return self.client.tokens(id, chain=chain, min_usd=min_usd, limit=limit,
                                  all_tokens=all_tokens)

    def protocols(self, id, chain=None, min_usd=1.0, limit=50, detail=False):
        """Open DeFi positions — supplied plus rewards, minus borrowed."""
        return self.client.protocols(id, chain=chain, min_usd=min_usd, limit=limit,
                                     detail=detail)

    def nfts(self, id, chain=None, limit=50, all_nfts=False):
        """NFTs held, at floor price where DeBank has one."""
        return self.client.nfts(id, chain=chain, limit=limit, all_nfts=all_nfts)

    def net_curve(self, id, chain=None):
        """Net worth over time, and the change across the window."""
        return self.client.net_curve(id, chain=chain)

    def chain_balance(self, id, chain):
        """Net worth on one chain."""
        return self.client.chain_balance(id, chain)

    def chains_used(self, id):
        """Which chains this address has ever touched."""
        return self.client.chains_used(id)

    # ── activity & risk ──────────────────────────────────────────

    def history(self, id, chain=None, start_time=None, page_count=20, token_id=None):
        """Recent transactions, decoded — what moved, through what, at what gas."""
        return self.client.history(id, chain=chain, start_time=start_time,
                                   page_count=page_count, token_id=token_id)

    def approvals(self, id, chain, min_usd=0.0, limit=100):
        """Standing token approvals, ranked by what the spender could take today."""
        return self.client.approvals(id, chain=chain, min_usd=min_usd, limit=limit)

    def nft_approvals(self, id, chain):
        """NFT approvals, contract and per-token."""
        return self.client.nft_approvals(id, chain)

    # ── catalog ──────────────────────────────────────────────────

    def position(self, id, protocol):
        """One address's full position in one protocol, unsummarized."""
        return self.client.protocol_position(id, protocol)

    def protocol(self, protocol=None, chain=None, limit=100):
        """A protocol by id, or every protocol on a chain ranked by TVL."""
        return self.client.protocol(id=protocol, chain=chain, limit=limit)

    def token(self, chain, token):
        """Token metadata and current USD price."""
        return self.client.token(chain, token)

    def token_price(self, chain, token, date=None):
        """A token's closing price on a past date (YYYY-MM-DD, UTC)."""
        return self.client.token_price_history(chain, token, date_at=date)

    def holders(self, protocol=None, chain=None, token=None, start=0, limit=20):
        """Biggest holders of a token, or biggest depositors in a protocol."""
        if protocol:
            return self.client.protocol_holders(protocol, start=start, limit=limit)
        return self.client.token_holders(chain, token, start=start, limit=limit)

    def gas(self, chain):
        """The current gas market on a chain."""
        return self.client.gas(chain)

    def chains(self, q=None, refresh=False):
        """Every chain DeBank indexes. Works without a key."""
        return self.client.chains(q=q, refresh=refresh)

    # ── the bank rail (keyless) ─────────────────────────────────

    def balances(self, id, chains=None, min_usd=0.0):
        """Native + USDC/USDT/DAI on 8 chains via public RPCs. Needs no key."""
        if isinstance(chains, str):
            chains = [x for x in chains.split(',') if x.strip()]
        return self.client.balances(id, chains=chains, min_usd=min_usd)

    def networks(self):
        """Chain ids, RPCs, explorers and stablecoin contracts a wallet needs."""
        return self.client.networks()

    # ── the savings desk (keyless) ──────────────────────────────

    def funds(self, amount=None, refresh=False):
        """The savings index funds: projected ROI + locked liquidity, live."""
        import savings
        return savings.funds(amount=amount, refresh=refresh)

    def fund(self, fund, amount=None, refresh=False):
        """One fund in full; venue:<id> is a fund of one."""
        import savings
        return savings.fund(fund, amount=amount, refresh=refresh)

    def savings(self, id):
        """Idle stablecoins vs money placed in each venue, read from chain."""
        import savings
        return savings.savings(id)

    def savings_plan(self, id, fund, amount):
        """The approve+deposit transactions the owner's wallet must sign."""
        import savings
        return savings.plan(id, fund, amount)

    # ── keys ─────────────────────────────────────────────────────

    def account(self):
        """Does the key work, where did it come from, what is left on it."""
        return self.client.account()

    def set_key(self, key, persist=True):
        """Store an AccessKey at ~/.mod/debank/key.json (0600, off-tree)."""
        import client
        return client.set_key(key, persist=persist)

    def raw(self, path, params=None, public=False):
        """Escape hatch: any DeBank Cloud route, with your key attached."""
        return self.client.raw(path, params=params, public=public)

    # ── mcp ──────────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry this module serves."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS),
                'instructions': mcp.INSTRUCTIONS}

    def mcp_call(self, tool, arguments=None, **kwargs):
        """Call one MCP tool in-process — the same path the server takes."""
        import mcp
        return mcp.call_tool(tool, {**(arguments or {}), **kwargs})

    def mcp_config(self):
        """Drop-in client config for Claude Code / Desktop and friends."""
        return {'mcpServers': {
            'debank': {'command': 'python3', 'args': [os.path.join(HERE, 'mcp.py')]},
            'debank-http': {'type': 'http', 'url': f'http://localhost:{self.port}/mcp'},
        }}

    # ── serve ────────────────────────────────────────────────────

    def serve(self, port=None, background=True, **kwargs):
        """Run API + console + MCP on one port, under pm2 as debank-api."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        subprocess.run(['pm2', 'start', sys.executable, '--name', 'debank-api',
                        '--cwd', HERE, '--', os.path.join(HERE, 'api.py'),
                        '--port', str(port)],
                       cwd=HERE, env=env, capture_output=True)
        return {'api': f'http://localhost:{port}',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp',
                'process': 'debank-api'}

    def kill(self, **kwargs):
        """Stop the server."""
        killed = []
        for name in ('debank-api', 'debank.api', 'debank-app'):
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    def status(self, **kwargs):
        """Is it up, and does the key work."""
        import urllib.error
        import urllib.request
        out = {'port': self.port, 'up': False}
        try:
            with urllib.request.urlopen(f'http://localhost:{self.port}/health',
                                        timeout=5) as r:
                out.update(up=True, **json.loads(r.read() or b'{}'))
        except Exception as e:
            out['error'] = f'{type(e).__name__}: {e}'
        return out

    def test(self, **kwargs):
        """Run the module's tests."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                            os.path.join(HERE, 'test')],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()
