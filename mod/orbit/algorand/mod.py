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

Layout follows the fleet's api/app split:

    api/api.py     the API — REST + MCP (POST /mcp) on `port` (51340);
                   behind the gateway at {host}/algorand/api
    app/           the console — app/server.py serves index.html at /algorand
                   on `app_port` (51341) and proxies /algorand/_api to the API

`m algorand/serve` runs both.
"""

import importlib.util
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
    console (api/ + app/).
    """

    def __init__(self, port=None, app_port=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('ALGORAND_API_PORT') or cfg.get('port', 51340))
        self.app_port = int(app_port or os.environ.get('ALGORAND_APP_PORT')
                            or cfg.get('app_port', 51341))
        self.base = cfg.get('base_path', '/algorand')

    def _load(self, rel, name):
        # By path: a bare `import api` / `import server` can resolve to some
        # other package on sys.path (core has its own `api`).
        spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, rel))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

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
        return self._load('api/api.py', 'algorand_api').info()

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

    def serve(self, port=None, app_port=None, background=False, app=True):
        """Run the API (REST + MCP, api/api.py) and the console (app/server.py).

        Foreground: the console runs in a thread, the API in this process.
        background=True starts each as its own process. app=False: API only."""
        port, app_port = int(port or self.port), int(app_port or self.app_port)
        urls = {'api': f'http://localhost:{port}/',
                'app': f'http://localhost:{app_port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp'}
        if background:
            procs = {'api': [os.path.join(HERE, 'api', 'api.py'), '--port', str(port)]}
            if app:
                procs['app'] = [os.path.join(HERE, 'app', 'server.py'), '--port',
                                str(app_port), '--api', f'http://127.0.0.1:{port}']
            pids = {k: subprocess.Popen([sys.executable, *argv], cwd=HERE,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL).pid
                    for k, argv in procs.items()}
            return {'pids': pids, 'port': port, 'app_port': app_port, **urls}
        if app:
            import threading
            server = self._load('app/server.py', 'algorand_app')
            threading.Thread(target=server.serve, daemon=True,
                             args=(app_port, f'http://127.0.0.1:{port}')).start()
        return self._load('api/api.py', 'algorand_api').serve(port)

    def kill(self, port=None, app_port=None):
        """Stop whatever holds the API and app ports. Targets ports, never a
        name — this box runs ~100 services and a pattern kill takes the fleet down."""
        ports = [int(port or self.port), int(app_port or self.app_port)]
        killed = {}
        for p in ports:
            pids = subprocess.run(['bash', '-c', f'lsof -ti tcp:{p} -sTCP:LISTEN || true'],
                                  capture_output=True, text=True).stdout.split()
            for pid in pids:
                subprocess.run(['kill', '-9', pid], capture_output=True)
            killed[p] = pids
        return {'killed': killed}

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
