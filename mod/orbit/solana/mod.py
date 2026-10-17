"""solana — the whole chain behind one mod.

An address on Solana is 32 opaque bytes that will not tell you what it is. That
is the problem this module is shaped around: ask it once, and it says whether
you are holding a wallet, a token mint, a token account, a stake account or a
program — and then answers the question you actually had.

    m solana/account 9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM   # what is this
    m solana/portfolio <wallet>                        # everything it holds, in USD
    m solana/history <wallet> detail=1                 # what it has been doing
    m solana/tx <signature>                            # what one transaction moved
    m solana/token <mint>                              # supply, authorities, risk
    m solana/quote SOL USDC 10                         # what a swap really gets you
    m solana/tokens sort=liquidity                     # every token, deepest first
    m solana/liquidity BONK                            # what could ACTUALLY be sold
    m solana/venues                                    # where the chain's depth is
    m solana/network                                   # slot, epoch, TPS, price
    m solana/wallet create name=hot                    # a key, stored off-tree
    m solana/send <to> 0.01 network=devnet             # signed here, sent there
    m solana/program <address>                         # what is deployed there
    m solana/deploy clone=memo network=devnet          # put a program on chain
    m solana/invoke <program> data=text:hi             # call one, simulated first
    m solana/swap SOL USDC 0.5 confirm=1               # traded on the DEXes

The same code answers the REST API, the browser console and twenty-six MCP
tools, so an agent, a shell and a human never see different answers.

`m solana/serve` runs all three on one port.
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
    solana — read the chain and move value on it. Identify any address, price a
    wallet across SOL and every SPL token, rank every routable token by the
    liquidity behind it and measure what one could really be sold for, decode a
    transaction into what actually moved per owner, quote a swap, read the
    validator set and its Nakamoto coefficient, and sign transfers with a key
    that never leaves this box. Load a deployed program — loader, upgrade authority,
    syscalls, IDL — call one with its arguments simulated before they are
    signed, and deploy or upgrade one from a file, from base64, or cloned off
    another cluster. Twenty-six MCP tools, a REST API and a console on one port.
    """

    def __init__(self, network=None, rpc=None, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50710))
        self.base = cfg.get('base_path', '/solana')
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

    def account(self, address, network=None, rpc=None):
        """What an address IS — wallet, mint, token account, stake or program."""
        return self.client(network, rpc).account(address)

    what = account

    def balance(self, address, network=None, rpc=None):
        """SOL for one address or several, comma-separated, priced in USD."""
        return self.client(network, rpc).balance(address)

    def portfolio(self, address, min_usd=0.01, include_dust=False, limit=200,
                  network=None, rpc=None):
        """SOL plus every SPL position, merged per mint and sorted by value."""
        return self.client(network, rpc).portfolio(
            address, min_usd=min_usd, include_dust=include_dust, limit=limit)

    holdings = portfolio

    def token(self, mint, network=None, rpc=None):
        """A mint in full: supply, authorities, liquidity, holders, risk."""
        return self.client(network, rpc).token(mint)

    def price(self, ids, network=None, rpc=None):
        """USD price by mint or symbol. Symbols resolve to deepest liquidity."""
        return self.client(network, rpc).price(ids)

    def tokens(self, list='verified', sort='liquidity', limit=50, offset=0,
               query=None, tag=None, min_liquidity=None, max_liquidity=None,
               safe_only=False, ascending=False, exclude=None, network=None,
               rpc=None):
        """Every routable token on Solana, ranked by the liquidity behind it."""
        import mcp
        return mcp.call_tool('sol_tokens', {
            'list': list, 'sort': sort, 'limit': limit, 'offset': offset,
            'query': query, 'tag': tag, 'min_liquidity': min_liquidity,
            'max_liquidity': max_liquidity, 'safe_only': safe_only,
            'ascending': ascending, 'exclude': exclude,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    def liquidity(self, mint, depth=True, sizes=None, cost_limit_pct=1.0,
                  pool_limit=50, network=None, rpc=None):
        """One token's liquidity three ways — including the sell size this
        module measured by quoting real routes, which is usually a fraction of
        the number the indexes print."""
        import mcp
        return mcp.call_tool('sol_liquidity', {
            'mint': mint, 'depth': depth, 'sizes': sizes,
            'cost_limit_pct': cost_limit_pct, 'pool_limit': pool_limit,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    def pools(self, mint, limit=50, network=None, rpc=None):
        """Every pool holding a token, deduped across indexes, deepest first."""
        import mcp
        return mcp.call_tool('sol_pools', {
            'mint': mint, 'limit': limit,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    def venues(self, tokens=10, pages=1, network=None, rpc=None):
        """Where the chain's liquidity sits, by DEX — measured from pools."""
        import mcp
        return mcp.call_tool('sol_venues', {
            'tokens': tokens, 'pages': pages,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    def history(self, address, limit=20, before=None, until=None, detail=False,
                network=None, rpc=None):
        """Recent transactions. detail=1 summarises each one for this address."""
        return self.client(network, rpc).history(
            address, limit=limit, before=before, until=until, detail=detail)

    def tx(self, signature, logs=False, network=None, rpc=None):
        """One transaction, decoded into what actually moved and who ran what."""
        return self.client(network, rpc).tx(signature, logs=logs)

    def quote(self, input, output, amount, slippage_bps=50, network=None, rpc=None):
        """What a swap would really get you, through Jupiter's best route."""
        return self.client(network, rpc).quote(input, output, amount,
                                               slippage_bps=slippage_bps)

    def swap(self, input, output, amount, slippage_bps=50, wallet=None, secret=None,
             confirm=False, dry_run=False, network=None, rpc=None):
        """Trade one token for another on the DEXes, signed here.

        Jupiter prices the route and builds the transaction; this signs it with
        a keystore key and sends it. Over the USD guard it returns needs_confirm
        instead of trading.
        """
        return self.client(network, rpc).swap(
            input, output, amount, slippage_bps=slippage_bps, wallet=wallet,
            secret=secret, confirm=confirm, dry_run=dry_run)

    # ── programs ─────────────────────────────────────────────────

    def program(self, program, code=False, strings=True, idl=True, accounts=False,
                account_type=None, limit=25, network=None, rpc=None):
        """What is deployed at an address: loader, upgrade authority, code,
        syscalls, IDL — and optionally the accounts it owns."""
        import mcp
        return mcp.call_tool('sol_program', {
            'program': program, 'code': code, 'strings': strings, 'idl': idl,
            'accounts': accounts, 'account_type': account_type, 'limit': limit,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    def idl(self, program, action='get', idl=None, full=False, network=None,
            rpc=None):
        """A program's interface. action=set teaches this module one it never
        published, and every call afterwards can use instruction names."""
        import mcp
        return mcp.call_tool('sol_idl', {
            'program': program, 'action': action, 'idl': idl, 'full': full,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    def deploy(self, path=None, data=None, clone=None, clone_network='mainnet',
               program=None, buffer=None, wallet=None, max_data_len=None,
               name=None, confirm=False, wait=25, action='deploy', job=None,
               network=None, rpc=None):
        """Put an ELF on chain, or upgrade one that is there. Returns a job."""
        import mcp
        return mcp.call_tool('sol_deploy', {
            'action': action, 'job': job, 'path': path, 'data': data,
            'clone': clone, 'clone_network': clone_network, 'program': program,
            'buffer': buffer, 'wallet': wallet, 'max_data_len': max_data_len,
            'name': name, 'confirm': confirm, 'wait': wait,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    def invoke(self, program, ix=None, args=None, accounts=None, data=None,
               wallet=None, payer=None, send=False, force=False, idl=None,
               network=None, rpc=None):
        """Call a program. Simulates by default — send=1 signs it for real."""
        import mcp
        return mcp.call_tool('sol_invoke', {
            'program': program, 'ix': ix, 'args': args, 'accounts': accounts,
            'data': data, 'wallet': wallet, 'payer': payer, 'send': send,
            'force': force, 'idl': idl,
            'network': network or self._network, 'rpc': rpc or self._rpc})

    call = invoke

    def pda(self, program, seeds):
        """Derive a program address from seeds, and say how each was read."""
        import mcp
        return mcp.call_tool('sol_pda', {'program': program, 'seeds': seeds})

    def authority(self, action, account, new_authority=None, recipient=None,
                  wallet=None, payer_wallet=None, confirm=False, network=None,
                  rpc=None):
        """set, revoke or close — who may replace a program's code."""
        import mcp
        return mcp.call_tool('sol_authority', {
            'action': action, 'account': account, 'new_authority': new_authority,
            'recipient': recipient, 'wallet': wallet, 'payer_wallet': payer_wallet,
            'confirm': confirm, 'network': network or self._network,
            'rpc': rpc or self._rpc})

    def network(self, network=None, rpc=None):
        """Slot, epoch, TPS, supply, inflation, price — the state of the chain."""
        return self.client(network, rpc).status()

    status = network

    def validators(self, limit=20, sort='stake', delinquent=None, network=None, rpc=None):
        """The validator set by stake, with the Nakamoto coefficient."""
        return self.client(network, rpc).validators(limit=limit, sort=sort,
                                                    delinquent=delinquent)

    def stake(self, address, network=None, rpc=None):
        """Stake accounts a wallet can withdraw from, and their state."""
        return self.client(network, rpc).stakes(address)

    def rpc(self, method, params=None, network=None, rpc=None):
        """Any Solana JSON-RPC method, raw — the escape hatch."""
        c = self.client(network, rpc)
        if isinstance(params, str):
            params = json.loads(params)
        return {'network': c.network, 'method': method,
                'result': c.call(method, params or [])}

    # ── keys and money ───────────────────────────────────────────

    def wallet(self, action='list', name=None, secret=None, default=None,
               overwrite=False, network=None, rpc=None):
        """The off-tree keystore: list, create, import, remove, default, export."""
        import mcp
        return mcp.call_tool('sol_wallet', {
            'action': action, 'name': name, 'secret': secret, 'default': default,
            'overwrite': overwrite, 'network': network or self._network,
            'rpc': rpc or self._rpc})

    def send(self, to, amount, mint=None, wallet=None, secret=None, memo=None,
             confirm=False, wait=True, network=None, rpc=None):
        """Send SOL, or an SPL token with mint=. Guarded above the value ceiling."""
        return self.client(network, rpc).transfer(
            to, amount, mint=mint, wallet=wallet, secret=secret, memo=memo,
            confirm=confirm, wait=wait)

    transfer = send

    def airdrop(self, address=None, sol=1, wallet=None, network='devnet', rpc=None):
        """Test SOL from the devnet or testnet faucet. There is no mainnet one."""
        return self.client(network, rpc).airdrop(address, sol_amount=sol, wallet=wallet)

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
        return {'mcpServers': {'solana': {
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
        env = {**os.environ, **({'SOLANA_OFFLINE': '1'} if offline else {})}
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
