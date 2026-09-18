"""raydium — Solana's biggest AMM, read properly.

Raydium's API is good and reading it well is work: the pool book is paged and
every row is eight nested token objects, quotes come back in base units, and
the question an LP actually has — "what is my position worth" — is not in the
API at all, because a concentrated position lives in an account derived from an
NFT that nobody indexes.

    m raydium/overview                      # TVL, volume, RAY, the fee to pay
    m raydium/pools sort=volume limit=10     # the book, ranked honestly
    m raydium/pair SOL USDC                  # every pool, and their disagreement
    m raydium/token BONK                     # is it verified, and can you sell it
    m raydium/quote SOL USDC 10              # what the swap really gets you
    m raydium/depth <pool>                   # the money within 1% of the price
    m raydium/wallet <address>               # LP tokens AND CLMM positions
    m raydium/position <nft mint>            # one position, in range or not
    m raydium/swap_tx <wallet> SOL USDC 1    # unsigned — sign it elsewhere
    m raydium/serve

Seventeen MCP tools, a REST API and a browser console run the same code on one
port, so an agent, a shell and a human never see different answers. No keys
live here: swaps are built and handed to a signer (`orbit/solana` has one).
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
    raydium — the AMM as data. Rank every pool by volume, TVL, fees or APR;
    see every pool that trades a pair and how far apart they price it; quote a
    swap in whole tokens with the route hop by hop; measure the liquidity that
    is actually within a few percent of the price instead of the TVL headline;
    and read a wallet's concentrated positions off chain, which no portfolio
    API shows. Seventeen MCP tools, a REST API and a console on one port.
    Signs nothing: swap transactions come back unsigned.
    """

    def __init__(self, port=None, rpc=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50790))
        self.base = cfg.get('base_path', '/raydium')
        if rpc:
            os.environ['RAYDIUM_RPC'] = rpc

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    forward = info

    def _tool(self, name, **args):
        import mcp
        return mcp.call_tool(name, args)

    # ── the book ─────────────────────────────────────────────────

    def overview(self):
        """TVL, 24h volume, the turnover between them, RAY and SOL, the fee."""
        return self._tool('ray_overview')

    def pools(self, type='all', sort='volume24h', order='desc', limit=20, page=1,
              min_tvl=None, min_volume=None, search=None, full=False):
        """The pool book, ranked. Sort by volume — sorting by TVL floats pools
        whose reserves are a worthless token."""
        return self._tool('ray_pools', type=type, sort=sort, order=order,
                          limit=limit, page=page, min_tvl=min_tvl,
                          min_volume=min_volume, search=search, full=full)

    def pool(self, pool, keys=False):
        """One pool in full, by address or by LP mint."""
        return self._tool('ray_pool', pool=pool, keys=keys)

    def pair(self, token_a, token_b=None, sort='liquidity', limit=10, type='all'):
        """Every pool that trades a pair, the deepest, the busiest, the spread."""
        return self._tool('ray_pair', token_a=token_a, token_b=token_b, sort=sort,
                          limit=limit, type=type)

    def token(self, token, limit=5):
        """A token: price, whether Raydium vouches for it, where it trades."""
        return self._tool('ray_token', token=token, limit=limit)

    def price(self, tokens):
        """USD prices by mint or symbol, from Raydium's own pools."""
        return self._tool('ray_price', tokens=tokens)

    def search(self, query, limit=10):
        """Find a token by symbol or name, labelled with how much to trust it."""
        return self._tool('ray_search', query=query, limit=limit)

    def mints(self, search=None, limit=50, page=1):
        """Raydium's verified mint list."""
        return self._tool('ray_mints', search=search, limit=limit, page=page)

    # ── trading ──────────────────────────────────────────────────

    def quote(self, input, output, amount, slippage_bps=50, mode='in'):
        """What a swap actually gets you, in whole tokens, route hop by hop."""
        return self._tool('ray_quote', input=input, output=output, amount=amount,
                          slippage_bps=slippage_bps, mode=mode)

    def swap_tx(self, wallet, input, output, amount, slippage_bps=50, mode='in',
                priority='h'):
        """Build the swap and hand it back UNSIGNED. Nothing here holds a key."""
        return self._tool('ray_swap_tx', wallet=wallet, input=input, output=output,
                          amount=amount, slippage_bps=slippage_bps, mode=mode,
                          priority=priority)

    # ── liquidity ────────────────────────────────────────────────

    def depth(self, pool, bands=None, points=48):
        """The money within 0.5-10% of the price — the number TVL hides."""
        return self._tool('ray_depth', pool=pool, bands=bands, points=points)

    def keys(self, pool):
        """The on-chain accounts behind a pool: vaults, authority, lookup table."""
        return self._tool('ray_keys', pool=pool)

    def farms(self, pool=None, ids=None, limit=20):
        """Emission farms, their reward tokens and when they stop."""
        return self._tool('ray_farms', pool=pool, ids=ids, limit=limit)

    def stake(self):
        """Single-sided RAY staking."""
        return self._tool('ray_stake')

    def wallet(self, wallet, min_usd=0.01, limit=50):
        """A wallet's Raydium exposure — LP tokens and concentrated positions."""
        return self._tool('ray_wallet', wallet=wallet, min_usd=min_usd, limit=limit)

    def position(self, nft_mint):
        """One concentrated position by its NFT, decoded off chain."""
        return self._tool('ray_position', nft_mint=nft_mint)

    def api(self, path, **params):
        """Any Raydium v3 path, unwrapped — the escape hatch."""
        return self._tool('ray_api', path=path, params=params)

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
        return {'mcpServers': {'raydium': {
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
        out = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            subprocess.run(['kill', pid], capture_output=True)
        return {'port': port, 'killed': out}

    def test(self, offline=False):
        """Run the module's tests. offline=1 skips everything needing a network."""
        env = {**os.environ, **({'RAYDIUM_OFFLINE': '1'} if offline else {})}
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'],
                           cwd=HERE, capture_output=True, text=True, env=env)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        """The project README."""
        for name in ('README.md', 'skill.md'):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                with open(p) as f:
                    return f.read()
        return None
