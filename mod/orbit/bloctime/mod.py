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

    def distribute(self):
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
