"""
BlocTime — Time-weighted staking with delegation, daily rewards, and Bitcoin-style inflation.

Contracts:
  NativeToken — ERC20 token staked into the protocol
  BlocTime   — Stake NativeToken for BLOC tokens, delegate voting power, earn inflation rewards

Usage:
  m.fn('bloctime/status')()
  m.fn('bloctime/deploy')()
  m.fn('bloctime/serve')()
  m.fn('bloctime/stake')(amount=100, lock_blocks=10000)
  m.fn('bloctime/delegate')(to='0x...')
  m.fn('bloctime/distribute')()
  m.fn('bloctime/claim_rewards')()
  m.fn('bloctime/fork')(name='mybloctime')       # your own copy of the module
  m.fn('bloctime/compile_contract')(path='contracts/MyToken.sol')
  m.fn('bloctime/deploy_contract')(path='contracts/MyToken.sol', args=[1000])
  m.fn('bloctime/deployments')()                 # what you have deployed here
  m.fn('bloctime/market')()                      # browse deployed instances
  m.fn('bloctime/register_instance')(name='x', rpc='...', bloctime='0x...')
  m.fn('bloctime/bridge')(fn='in_snapshot', address='...')
"""

import json
import os
import signal
import subprocess
from pathlib import Path

import mod as m


DIR = Path(__file__).parent
API_PORT = 8851
APP_PORT = 8852


def _run(cmd, cwd=None, timeout=120):
    result = subprocess.run(
        cmd, shell=True, cwd=cwd or str(DIR),
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result.stdout.strip()


class Mod:
    description = "BlocTime — Time-weighted staking with delegation, Bitcoin-style inflation, and daily reward distribution."

    def __init__(self, config=None, **kwargs):
        self.module_dir = DIR
        self.api_port = API_PORT
        self.app_port = APP_PORT
        self.config = config or self._load_config()

    def _load_config(self):
        cfg_path = self.module_dir / 'config.json'
        if cfg_path.exists():
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    def forward(self, **kwargs):
        return self.status()

    # ── Build / Deploy ────────────────────────────────────────────

    def compile(self):
        _run('npx hardhat compile', cwd=str(self.module_dir))
        abi = self.module_dir / 'artifacts' / 'contracts' / 'BlocTime.sol' / 'BlocTime.json'
        return {'compiled': True, 'abi': str(abi)}

    def deploy(self, network='base_sepolia'):
        self.compile()
        output = _run(
            f'npx hardhat run scripts/deploy.js --network {network}',
            cwd=str(self.module_dir), timeout=300,
        )
        cfg_path = self.module_dir / 'config.json'
        if cfg_path.exists():
            with open(cfg_path) as f:
                data = json.load(f)
            self.config = data
            return {
                'contracts': data.get('contracts', {}).get(network, {}),
                'network': network,
                'output': output,
            }
        return {'output': output}

    def test(self):
        output = _run('npx hardhat test', cwd=str(self.module_dir), timeout=300)
        return {'output': output}

    # ── Deploy any contract ───────────────────────────────────────

    def _contracts_store(self):
        """api/bt_contracts.py — solc compile + the deployment store."""
        import sys
        api_dir = str(self.module_dir / 'api')
        if api_dir not in sys.path:
            sys.path.insert(0, api_dir)
        import bt_contracts
        return bt_contracts

    def _source(self, source=None, path=None):
        if path:
            p = Path(path)
            if not p.is_absolute():
                p = self.module_dir / p
            if not p.exists():
                raise FileNotFoundError(f"No such file: {p}")
            return p.read_text(), p.name
        if not source:
            raise ValueError("Pass path='MyToken.sol' or source='<solidity>'")
        return source, 'Contract.sol'

    def _rpc(self, rpc=None):
        if rpc:
            return rpc
        network = self.config.get('network', 'testnet')
        return self.config.get('contracts', {}).get(network, {}).get('url')

    def compile_contract(self, source=None, path=None, optimize=True, runs=200):
        """Compile any Solidity file or string. Imports resolve like hardhat's."""
        src, filename = self._source(source, path)
        out = self._contracts_store().compile_source(
            src, filename=filename, optimize=optimize, runs=runs,
        )
        return {
            'solc': out['solc'],
            'filename': out['filename'],
            'warnings': out['warnings'],
            'contracts': [{
                'name': c['name'],
                'constructor': [f"{i['type']} {i.get('name', '')}".strip() for i in c['constructor']],
                'bytes': max(0, len(c['bytecode']) // 2 - 1),
                'deployable': c['deployable'],
            } for c in out['contracts']],
        }

    def deploy_contract(self, source=None, path=None, contract=None, args=None,
                        rpc=None, name=None, record=True):
        """Compile and deploy any contract with the server signer (PRIVATE_KEY).

        The app's DEPLOY tab does the same thing from your own browser wallet.
        Deployed contracts are remembered and show up in the CONTRACTS tab.
        """
        bt = self._contracts_store()
        src, filename = self._source(source, path)
        compiled = bt.compile_source(src, filename=filename)
        picks = [c for c in compiled['contracts'] if c['deployable']]
        if contract:
            picks = [c for c in picks if c['name'] == contract]
        if not picks:
            wanted = f" '{contract}'" if contract else ''
            raise ValueError(f"No deployable contract{wanted} in {filename}")
        chosen = picks[-1]

        ctor = next((e for e in chosen['abi'] if e.get('type') == 'constructor'), None)
        ctor_args = bt.coerce_args(ctor.get('inputs', []) if ctor else [], args or [])
        facts = bt.deploy(chosen['abi'], chosen['bytecode'], args=ctor_args, rpc=self._rpc(rpc))

        result = {'name': name or chosen['name'], **facts}
        if record:
            result['entry'] = bt.add_deployment(
                name=result['name'], address=facts['address'], abi=chosen['abi'],
                rpc=facts['rpc'], chain_id=facts['chainId'], deployer=facts['deployer'],
                tx_hash=facts['txHash'], source=src, filename=filename,
                solc=compiled['solc'], verify=False,
            )
        return result

    def deployments(self):
        """Every contract deployed through this module (app or CLI)."""
        entries = self._contracts_store().list_deployments()
        return {'count': len(entries), 'deployments': entries}

    def forget_deployment(self, id):
        """Drop a deployment record (local trusted path — no signature needed)."""
        return self._contracts_store().remove_deployment(id)

    # ── Serve ─────────────────────────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=True):
        api_port = int(api_port or self.api_port)
        app_port = int(app_port or self.app_port)
        log_dir = Path('/tmp/bloctime')
        log_dir.mkdir(parents=True, exist_ok=True)
        results = {}

        self.kill()

        api_url = f'http://localhost:{api_port}'
        app_url = f'http://localhost:{app_port}'

        api_dir = self.module_dir / 'api'
        mod_root = str(self.module_dir.parent.parent)
        env = os.environ.copy()
        env['PYTHONPATH'] = f"{mod_root}:{self.module_dir}:{env.get('PYTHONPATH', '')}"
        env['PORT'] = str(api_port)

        api_log = open(log_dir / 'api.log', 'w')
        api_cmd = [
            'python3', '-m', 'uvicorn', 'api:app',
            '--host', '0.0.0.0', '--port', str(api_port),
            '--app-dir', str(api_dir),
        ]
        if dev:
            api_cmd.append('--reload')
        subprocess.Popen(api_cmd, env=env, stdout=api_log, stderr=subprocess.STDOUT)
        results['api'] = api_url
        results['api_docs'] = f'{api_url}/docs'
        results['api_log'] = str(log_dir / 'api.log')

        app_dir = self.module_dir / 'app'
        if (app_dir / 'package.json').exists():
            app_env = os.environ.copy()
            app_env['NEXT_PUBLIC_API_URL'] = api_url
            app_env['PORT'] = str(app_port)
            app_log = open(log_dir / 'app.log', 'w')
            app_cmd = ['npx', 'next', 'dev' if dev else 'start', '-p', str(app_port)]
            subprocess.Popen(
                app_cmd, cwd=str(app_dir), env=app_env,
                stdout=app_log, stderr=subprocess.STDOUT,
            )
            results['app'] = app_url
            results['app_log'] = str(log_dir / 'app.log')

        self._save_urls(api_url, app_url)
        results['dev'] = dev
        results['logs'] = str(log_dir)
        return results

    def _save_urls(self, api_url, app_url):
        cfg_path = self.module_dir / 'config.json'
        cfg = {}
        if cfg_path.exists():
            with open(cfg_path) as f:
                cfg = json.load(f)
        cfg['urls'] = {'api': api_url, 'app': app_url}
        with open(cfg_path, 'w') as f:
            json.dump(cfg, f, indent=2)
        self.config = cfg

    def kill(self):
        killed = []
        for pattern in [f'uvicorn.*api:app.*{self.api_port}', f'next.*{self.app_port}']:
            try:
                result = subprocess.run(
                    ['pgrep', '-f', pattern], capture_output=True, text=True,
                )
                for pid in result.stdout.strip().split('\n'):
                    if pid:
                        os.kill(int(pid), signal.SIGTERM)
                        killed.append(pid)
            except Exception:
                pass
        return {'killed': killed}

    # ── Contract loaders ──────────────────────────────────────────

    def _load_deployment(self, network=None):
        from web3 import Web3
        cfg_path = self.module_dir / 'config.json'
        if not cfg_path.exists():
            raise RuntimeError("Not deployed. Run deploy() first.")
        with open(cfg_path) as f:
            data = json.load(f)
        network = network or data.get('network', 'testnet')
        contracts = data.get('contracts', {}).get(network, {})
        if not contracts.get('bloctime'):
            raise RuntimeError(f"No contracts found for network '{network}'")
        rpc = contracts.get('url') or os.environ.get('BASE_TESTNET_RPC_URL', 'https://sepolia.base.org')
        w3 = Web3(Web3.HTTPProvider(rpc))
        pk = os.environ.get('PRIVATE_KEY')
        account = w3.eth.account.from_key(pk) if pk else None
        return w3, contracts, account

    def _load_bloctime(self):
        from web3 import Web3
        w3, contracts, account = self._load_deployment()
        abi_path = self.module_dir / 'artifacts' / 'contracts' / 'BlocTime.sol' / 'BlocTime.json'
        with open(abi_path) as f:
            artifact = json.load(f)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(contracts['bloctime']),
            abi=artifact['abi'],
        )
        return w3, contract, account

    def _send_tx(self, fn):
        w3, contract, account = self._load_bloctime()
        if not account:
            raise RuntimeError("PRIVATE_KEY env var required")
        tx = fn(contract).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 500000,
        })
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return {'success': receipt.status == 1, 'tx_hash': tx_hash.hex()}

    # ── Staking ───────────────────────────────────────────────────

    def stake(self, amount, lock_blocks=0):
        from web3 import Web3
        amount_wei = Web3.to_wei(amount, 'ether') if isinstance(amount, (int, float)) else int(amount)
        return self._send_tx(lambda c: c.functions.stake(amount_wei, int(lock_blocks)))

    def unstake(self, stake_id):
        return self._send_tx(lambda c: c.functions.unstake(int(stake_id)))

    # ── Delegation ────────────────────────────────────────────────

    def delegate(self, to):
        from web3 import Web3
        return self._send_tx(lambda c: c.functions.delegate(Web3.to_checksum_address(to)))

    def undelegate(self):
        return self._send_tx(lambda c: c.functions.undelegate())

    # ── Rewards ───────────────────────────────────────────────────

    def pot(self):
        """Reward pot and the weekly Friday 12:00 EST distribution schedule."""
        import time
        _, contract, _ = self._load_bloctime()
        pot, pending, eligible, next_time, last_time, due = contract.functions.getPotInfo().call()
        return {
            'pot': str(pot),
            'pendingInflation': str(pending),
            'projected': str(pot + pending),
            'eligibleSupply': str(eligible),
            'nextDistribution': time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(next_time)),
            'lastDistribution': (
                time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(last_time)) if last_time else ''
            ),
            'secondsRemaining': 0 if due else max(0, next_time - int(time.time())),
            'due': due,
            'schedule': 'Weekly, Friday 12:00 EST (17:00 UTC)',
        }

    def fund_pot(self, amount):
        """Add BLOC to the pot — paid out at the next weekly distribution."""
        from web3 import Web3
        amount_wei = Web3.to_wei(amount, 'ether') if isinstance(amount, (int, float)) else int(amount)
        return self._send_tx(lambda c: c.functions.fundPot(amount_wei))

    def distribute(self, force=False):
        """Sweep the pot to BLOC holders. No-ops politely outside the weekly
        window, so a keeper can call it on any cadence (`m bloctime/distribute`)."""
        if not force:
            info = self.pot()
            if not info['due']:
                return {'distributed': False, **info}
        return self._send_tx(lambda c: c.functions.distributeRewards())

    def claim_rewards(self):
        return self._send_tx(lambda c: c.functions.claimRewards())

    # ── Views ─────────────────────────────────────────────────────

    def overview(self, address=None):
        from web3 import Web3
        w3, contract, account = self._load_bloctime()
        addr = Web3.to_checksum_address(address or account.address)
        ids = contract.functions.getUserStakeIds(addr).call()
        positions = []
        for sid in ids:
            pos = contract.functions.getStakePosition(addr, sid).call()
            positions.append({
                'stakeId': sid, 'amount': str(pos[0]), 'startBlock': pos[1],
                'lockBlocks': pos[2], 'blocTimeBalance': str(pos[3]), 'blocksRemaining': pos[4],
            })
        pending = contract.functions.earned(addr).call()
        vp = contract.functions.getVotingPower(addr).call()
        deleg = contract.functions.delegates(addr).call()
        return {
            'address': addr,
            'stakeCount': len(positions),
            'totalStaked': str(sum(int(p['amount']) for p in positions)),
            'totalBlocTime': str(sum(int(p['blocTimeBalance']) for p in positions)),
            'pendingRewards': str(pending),
            'blocBalance': str(contract.functions.balanceOf(addr).call()),
            'votingPower': str(vp),
            'delegate': deleg if deleg != '0x0000000000000000000000000000000000000000' else '',
            'positions': positions,
        }

    def status(self):
        cfg_path = self.module_dir / 'config.json'
        if not cfg_path.exists():
            return {'deployed': False}
        with open(cfg_path) as f:
            data = json.load(f)
        network = data.get('network', 'testnet')
        contracts = data.get('contracts', {}).get(network, {})
        return {
            'deployed': bool(contracts),
            'network': network,
            'urls': data.get('urls', {}),
            'contracts': contracts,
            'explorer': f"https://sepolia.basescan.org/address/{contracts.get('bloctime', '')}",
        }

    # ── Fork / Marketplace / Bridge ───────────────────────────────

    def _registry(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('bt_registry', DIR / 'api' / 'bt_registry.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fork(self, name, port=None, app_port=None):
        """Copy this module into orbit/<name> so anyone can run their own BlocTime.

        Rewrites the fork's identity (config name, API/app ports, basePath,
        gateway route) and leaves it pointing at the same contracts until the
        new owner deploys their own via deploy() or the app's DEPLOY tab.
        """
        import shutil
        import socket

        name = str(name).lower()
        if not name.replace('-', '').replace('_', '').isalnum():
            raise ValueError("Fork name must be alphanumeric (dashes/underscores ok)")
        dest = DIR.parent / name
        if dest.exists():
            raise ValueError(f"Module '{name}' already exists at {dest}")

        def _free(start):
            p = start
            while p < start + 200:
                with socket.socket() as s:
                    if s.connect_ex(('127.0.0.1', p)) != 0:
                        return p
                p += 1
            raise RuntimeError("No free port found")

        port = int(port) if port else _free(50260)
        app_port = int(app_port) if app_port else _free(port + 1)

        shutil.copytree(DIR, dest, ignore=shutil.ignore_patterns(
            'node_modules', '.next', '__pycache__', 'cache', 'build-info',
            '.git', '*.tsbuildinfo', 'deployment.json',
        ))

        # Rewire identity: config, ports, basePath, gateway route, log dirs.
        cfg_path = dest / 'config.json'
        with open(cfg_path) as f:
            cfg = json.load(f)
        cfg['name'] = name
        cfg['port'] = port
        cfg['app_port'] = app_port
        cfg['forked_from'] = 'bloctime'
        cfg['urls'] = {'api': f'http://localhost:{port}', 'app': f'http://localhost:{app_port}'}
        cfg.pop('schema', None)
        with open(cfg_path, 'w') as f:
            json.dump(cfg, f, indent=2)

        replacements = {
            dest / 'mod.py': [
                ('API_PORT = 8851', f'API_PORT = {port}'),
                ('APP_PORT = 8852', f'APP_PORT = {app_port}'),
                ("/tmp/bloctime", f"/tmp/{name}"),
                ("'bloctime.app'", f"'{name}.app'"),
            ],
            dest / 'app' / 'mod.py': [
                ('APP_PORT = 8852', f'APP_PORT = {app_port}'),
                ('API_PORT = 8851', f'API_PORT = {port}'),
                ("'/bloctime'", f"'/{name}'"),
                ("/tmp/bloctime", f"/tmp/{name}"),
            ],
            dest / 'app' / 'next.config.js': [
                ("'/bloctime'", f"'/{name}'"),
            ],
            dest / 'app' / 'src' / 'app' / 'page.tsx': [
                ('/api/bloctime', f'/api/{name}'),
                ('http://localhost:8851', f'http://localhost:{port}'),
            ],
        }
        for path, subs in replacements.items():
            if not path.exists():
                continue
            text = path.read_text()
            for old, new in subs:
                text = text.replace(old, new)
            path.write_text(text)

        return {
            'module': name,
            'path': str(dest),
            'api_port': port,
            'app_port': app_port,
            'next_steps': [
                f"m {name}/serve                      # run your fork",
                f"m {name}/deploy network=base_sepolia # deploy YOUR contracts (needs PRIVATE_KEY)",
                "  ...or use the app's DEPLOY tab to deploy from your wallet",
                f"m bloctime/register_instance name={name} rpc=<rpc> bloctime=<0x...>  # list it on the market",
            ],
        }

    def market(self, stats=False):
        """Browse the marketplace: every registered BlocTime instance."""
        reg = self._registry()
        instances = reg.list_instances()
        if stats:
            for e in instances:
                e['stats'] = reg.instance_stats(e)
        return {'count': len(instances), 'instances': instances}

    def register_instance(self, name, rpc, bloctime, native_token=None, description=''):
        """Verify a deployed BlocTime on-chain and list it on the marketplace."""
        return self._registry().add_instance(
            name=name, rpc=rpc, bloctime=bloctime,
            native_token=native_token, description=description,
        )

    def unregister_instance(self, id):
        """Remove a marketplace entry (local trusted path — no signature needed)."""
        return self._registry().remove_instance(id)

    def bridge(self, fn='health', **kwargs):
        """Call into the bridge module (Substrate/Solana → EVM snapshot claims)."""
        import requests as req
        base = os.environ.get('BRIDGE_API_URL', 'http://localhost:8840')
        try:
            if fn in ('in_snapshot', 'has_claimed', 'unclaimed', 'commitment'):
                resp = req.get(f"{base}/{fn}/{kwargs.get('address', '')}", timeout=15)
            else:
                resp = req.post(f"{base}/{fn}", json=kwargs or {}, timeout=15)
                if resp.status_code in (404, 405):
                    resp = req.get(f"{base}/{fn}", params=kwargs or {}, timeout=15)
            return resp.json()
        except Exception as e:
            return {'error': f'bridge module unreachable: {e}'}

    def app(self, fn='info', **kwargs):
        """The BlocTime app submodule — see app/mod.py (m bloctime.app)."""
        app_mod = m.mod('bloctime.app')()
        return getattr(app_mod, fn)(**kwargs)

    def call(self, fn='health', params=None, timeout=10):
        import requests as req
        url = f'http://localhost:{self.api_port}/{fn}'
        method = 'GET' if fn in ('health', 'stats', 'params', 'points') else 'POST'
        try:
            if method == 'GET':
                resp = req.get(url, timeout=timeout)
            else:
                resp = req.post(url, json=params or {}, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {'error': str(e)}

    c = call
