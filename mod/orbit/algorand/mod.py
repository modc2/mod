"""algorand — the Algorand protocol behind one mod.

    m algorand/account algo.algo             # balance, min-balance, ASAs, apps
    m algorand/asset USDC holder=<addr>      # an ASA, and a holder's balance
    m algorand/app 1002541853 program=true   # global state, boxes, TEAL
    m algorand/simulate <app> method='get(address)uint64' args=<addr>
    m algorand/network                       # round, fee, supply, stake, price

And the write half — a keystore under ~/.mod/algorand/, never the repo:

    m algorand/wallet op=generate
    m algorand/send <addr> 1.5
    m algorand/asset_create name=Mod unit=MOD total=1000000 decimals=6
    m algorand/optin 10458941
    m algorand/app_create approval="$(cat approval.teal)" global_ints=1
    m algorand/app_call <app> method='inc()uint64'

Writes default to testnet; any other network refuses without confirm=true.
`m algorand/serve` runs the REST API, console and MCP server on one port.
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
    algorand — Algorand, whole. Reads: accounts (addresses or .algo NFD
    names) with min-balance, spendable and every ASA held; assets; smart
    contracts with decoded global state, boxes, local state and TEAL;
    keyless simulated app calls with ARC-4 encoding; history, transactions,
    blocks, network, price. Writes: payments, ASA mint/opt-in/transfer, app
    deploy/call/update, atomic groups — keys in ~/.mod/algorand/, testnet by
    default, mainnet only with confirm=true. MCP tools, a REST API and a
    console on one port.
    """

    def __init__(self, port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 51340))
        self.base = cfg.get('base_path', '/algorand')

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    def _tool(self, name, **args):
        import mcp
        return mcp.call_tool(name, {k: v for k, v in args.items() if v is not None},
                             local=True)

    # ── reads ────────────────────────────────────────────────────

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    def account(self, address, network=None):
        """Balance, min-balance, spendable, USD, ASAs, apps, rekey, NFD name."""
        return self._tool('algo_account', address=address, network=network)

    def resolve(self, name, network=None):
        """alice.algo → address, or address → name."""
        return self._tool('algo_resolve', name=name, network=network)

    def asset(self, asset, holder=None, network=None):
        return self._tool('algo_asset', asset=asset, holder=holder, network=network)

    def assets(self, q=None, creator=None, limit=20, network=None):
        return self._tool('algo_assets', q=q, creator=creator, limit=limit, network=network)

    def app(self, app_id, program=False, network=None):
        return self._tool('algo_app', app_id=int(app_id),
                          program=str(program).lower() in ('true', '1'), network=network)

    def box(self, app_id, name, network=None):
        return self._tool('algo_box', app_id=int(app_id), name=name, network=network)

    def local_state(self, address, app_id, network=None):
        return self._tool('algo_local_state', address=address, app_id=int(app_id),
                          network=network)

    def simulate(self, app_id, method=None, args=None, sender=None, network=None, **kw):
        """Call an app with no key and no fee — logs + decoded ARC-4 return."""
        return self._tool('algo_simulate', app_id=int(app_id), method=method,
                          args=args, sender=sender, network=network, **kw)

    def tx(self, txid, network=None):
        return self._tool('algo_tx', txid=txid, network=network)

    def history(self, address, limit=25, kind=None, network=None):
        return self._tool('algo_history', address=address, limit=int(limit),
                          kind=kind, network=network)

    def block(self, round=None, network=None):
        return self._tool('algo_block', round=round, network=network)

    def network(self, network=None):
        return self._tool('algo_network', network=network)

    def price(self):
        return self._tool('algo_price')

    def compile(self, source, network=None):
        return self._tool('algo_compile', source=source, network=network)

    def rpc(self, path, indexer=False, network=None, **params):
        return self._tool('algo_rpc', path=path, indexer=indexer,
                          params=params, network=network)

    # ── writes (testnet default; confirm=true elsewhere) ─────────

    def wallet(self, op='status', mnemonic=None, address=None, label=None, network=None):
        return self._tool('algo_wallet', op=op, mnemonic=mnemonic, address=address,
                          label=label, network=network)

    def export(self, address=None):
        """The selected key's 25-word mnemonic — local shell only."""
        import wallet
        return wallet.export_mnemonic(address)

    def send(self, to, amount_algo, note=None, network='testnet', confirm=False, **kw):
        return self._tool('algo_send', to=to, amount_algo=float(amount_algo), note=note,
                          network=network, confirm=confirm, **kw)

    def asset_create(self, name, unit, total, decimals=0, network='testnet',
                     confirm=False, **kw):
        return self._tool('algo_asset_create', name=name, unit=unit, total=int(total),
                          decimals=int(decimals), network=network, confirm=confirm, **kw)

    def optin(self, asset_id, network='testnet', confirm=False, **kw):
        return self._tool('algo_asset_optin', asset_id=str(asset_id), network=network,
                          confirm=confirm, **kw)

    def asset_send(self, asset_id, to, amount, network='testnet', confirm=False, **kw):
        return self._tool('algo_asset_send', asset_id=str(asset_id), to=to,
                          amount=float(amount), network=network, confirm=confirm, **kw)

    def app_create(self, network='testnet', confirm=False, **kw):
        return self._tool('algo_app_create', network=network, confirm=confirm, **kw)

    def app_call(self, app_id, method=None, args=None, network='testnet',
                 confirm=False, **kw):
        return self._tool('algo_app_call', app_id=int(app_id), method=method, args=args,
                          network=network, confirm=confirm, **kw)

    def app_update(self, app_id, network='testnet', confirm=False, **kw):
        return self._tool('algo_app_update', app_id=int(app_id), network=network,
                          confirm=confirm, **kw)

    # ── plumbing ─────────────────────────────────────────────────

    def tools(self):
        import mcp
        return {'count': len(mcp.TOOLS), 'tools': [t['name'] for t in mcp.tool_list()]}

    def mcp_config(self, url=None):
        url = url or f'http://localhost:{self.port}/mcp'
        return {'mcpServers': {'algorand': {'type': 'http', 'url': url}},
                'stdio': {'command': sys.executable,
                          'args': [os.path.join(HERE, 'mcp.py')]}}

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
        return {'pid': proc.pid, 'port': port, 'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{port}{self.base}/',
                'mcp': f'http://localhost:{port}/mcp'}

    def kill(self, port=None):
        """Stop whatever holds the port. Targets the port, never a name —
        this box runs ~100 services and a pattern kill takes the fleet down."""
        port = int(port or self.port)
        pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{port} || true'],
                              capture_output=True, text=True).stdout.split()
        for pid in pids:
            subprocess.run(['kill', '-9', pid], capture_output=True)
        return {'port': port, 'killed': pids}

    def test(self, offline=False):
        """Run the test suite. offline=1 skips everything that needs the chain."""
        env = {**os.environ, **({'ALGORAND_OFFLINE': '1'} if offline else {})}
        out = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'],
                             cwd=HERE, env=env, capture_output=True, text=True)
        return {'ok': out.returncode == 0,
                'output': out.stdout[-4000:] or out.stderr[-4000:]}

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()
