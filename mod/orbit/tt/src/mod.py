"""
StakeTime — Delegated staking + Consensus (pluggable) on EVM (Base Sepolia).

Primitives:
  Subnet     — ERC20 native token (each subnet IS a token)
  Staking    — Stake Subnet tokens ON validators, earn STT
  Consensus  — Mints new Subnet tokens as emissions each epoch
  Inflation  — Pluggable emission curves (halving, flat, linear, sigmoid)
  Registry   — Competitive 420-slot subnet directory

Usage:
  m.fn('staketime/status')()
  m.fn('staketime/deploy')()
  m.fn('staketime/serve')()
  m.fn('staketime/stake_on')(validator_key='val1', amount=1000, lock_blocks=43200)
  m.fn('staketime/register')(key='val1', key_type=1, commission_bps=1000)
"""

import json
import os
import signal
import subprocess
from pathlib import Path

import mod as m


DIR = Path(__file__).parent
ROOT = DIR.parent
API_PORT = 8849
APP_PORT = 8850


def _run(cmd, cwd=None, timeout=120):
    result = subprocess.run(
        cmd, shell=True, cwd=cwd or str(ROOT),
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result.stdout.strip()


class Mod:
    description = "StakeTime + Consensus — Delegated staking with pluggable consensus modules (Yuma, Linear, Staked)."

    def __init__(self, config=None, **kwargs):
        self.module_dir = ROOT
        self.api_port = API_PORT
        self.app_port = APP_PORT
        self.config = config or self._load_config()

    def _load_config(self):
        cfg_path = self.module_dir / 'config.json'
        if cfg_path.exists():
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    # ── Info ──────────────────────────────────────────────────────────

    def info(self):
        """Return module info."""
        return {
            'name': 'tt',
            'description': self.description,
            'path': str(self.module_dir),
            'fns': [
                'info', 'forward', 'status', 'compile', 'deploy', 'test',
                'serve', 'kill', 'call',
                'register', 'stake_on', 'unstake_from',
                'checkin', 'batch_checkin', 'produce_block', 'distribute',
                'claim_staker_rewards', 'claim_validator_rewards',
                'validator', 'stake_position', 'user_stakes', 'validator_stakes',
                'validator_total_stake_time',
                'consensus_state', 'leaderboard', 'staker_rewards', 'validator_balance',
                'subnets', 'subnet_info', 'weakest_subnet',
                'register_subnet', 'deregister_subnet',
            ],
        }

    # ── Default entry point ───────────────────────────────────────────

    def forward(self, **kwargs):
        return self.status()

    # ── Build / Deploy ────────────────────────────────────────────────

    def compile(self):
        _run('npx hardhat compile', cwd=str(self.module_dir))
        st_abi = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Staking.sol' / 'Staking.json'
        sub_abi = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Subnet.sol' / 'Subnet.json'
        con_abi = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'consensus' / 'yuma' / 'ConsensusYuma.sol' / 'ConsensusYuma.json'
        return {
            'compiled': True,
            'staking_abi': str(st_abi),
            'subnet_abi': str(sub_abi),
            'consensus_abi': str(con_abi),
        }

    def deploy(self, network='base_sepolia'):
        self.compile()
        output = _run(
            f'npx hardhat run scripts/deploy.js --network {network}',
            cwd=str(self.module_dir), timeout=300,
        )
        deploy_path = self.module_dir / 'config.json'
        if deploy_path.exists():
            with open(deploy_path) as f:
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

    # ── Serve ─────────────────────────────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=True):
        api_port = int(api_port or self.api_port)
        app_port = int(app_port or self.app_port)
        log_dir = Path('/tmp/staketime')
        log_dir.mkdir(parents=True, exist_ok=True)
        results = {}

        self.kill()

        api_url = f'http://localhost:{api_port}'
        app_url = f'http://localhost:{app_port}'

        api_dir = self.module_dir / 'src' / 'api'
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

        app_dir = self.module_dir / 'src' / 'app'
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

        # Save urls to config
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

    # ── Contract loaders ──────────────────────────────────────────────

    def _load_deployment(self, network=None):
        from web3 import Web3
        deploy_path = self.module_dir / 'config.json'
        if not deploy_path.exists():
            raise RuntimeError("Not deployed. Run deploy() first.")
        with open(deploy_path) as f:
            data = json.load(f)
        network = network or os.environ.get('NETWORK', 'base_sepolia')
        contracts = data.get('contracts', {})
        if network in contracts:
            deploy = contracts[network]
        else:
            # Fallback: first available network
            for key in contracts:
                if isinstance(contracts[key], dict) and ('staking' in contracts[key] or 'stakeTime' in contracts[key]):
                    deploy = contracts[key]
                    break
            else:
                raise RuntimeError(f"No contracts found for network '{network}'")
        rpc = os.environ.get('BASE_TESTNET_RPC_URL', 'https://sepolia.base.org')
        w3 = Web3(Web3.HTTPProvider(rpc))
        pk = os.environ.get('PRIVATE_KEY')
        account = w3.eth.account.from_key(pk) if pk else None
        return w3, deploy, account

    def _load_subnet(self):
        from web3 import Web3
        w3, deploy, account = self._load_deployment()
        abi_path = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Subnet.sol' / 'Subnet.json'
        with open(abi_path) as f:
            artifact = json.load(f)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(deploy['subnet']),
            abi=artifact['abi'],
        )
        return w3, contract, account

    def _load_staking(self):
        from web3 import Web3
        w3, deploy, account = self._load_deployment()
        addr = deploy.get('staking') or deploy.get('stakeTime')
        abi_path = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Staking.sol' / 'Staking.json'
        with open(abi_path) as f:
            artifact = json.load(f)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(addr),
            abi=artifact['abi'],
        )
        return w3, contract, account

    def _load_consensus(self):
        from web3 import Web3
        w3, deploy, account = self._load_deployment()
        abi_path = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'consensus' / 'yuma' / 'ConsensusYuma.sol' / 'ConsensusYuma.json'
        with open(abi_path) as f:
            artifact = json.load(f)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(deploy['consensus']),
            abi=artifact['abi'],
        )
        return w3, contract, account

    def _send_tx(self, contract_loader, fn):
        w3, contract, account = contract_loader()
        if not account:
            raise RuntimeError("PRIVATE_KEY env var required for transactions")
        tx = fn(contract).build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 500000,
        })
        signed = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        return {'success': receipt.status == 1, 'tx_hash': tx_hash.hex()}

    # ── Staking: Validator methods ───────────────────────────────────

    def register(self, key, key_type=0, commission_bps=1000):
        """Register a validator (via Staking)."""
        return self._send_tx(
            self._load_staking,
            lambda c: c.functions.registerValidatorAdmin(key, key_type, commission_bps),
        )

    # ── Staking: Staking methods ──────────────────────────────────────

    def stake_on(self, validator_key, amount, lock_blocks=0):
        """Stake Subnet tokens on a validator (via Staking)."""
        from web3 import Web3
        amount_wei = Web3.to_wei(amount, 'ether') if isinstance(amount, (int, float)) else int(amount)
        return self._send_tx(
            self._load_staking,
            lambda c: c.functions.stakeOn(validator_key, amount_wei, lock_blocks),
        )

    def unstake_from(self, stake_id):
        """Unstake a position after lock period (via Staking)."""
        return self._send_tx(
            self._load_staking,
            lambda c: c.functions.unstakeFrom(stake_id),
        )

    # ── Consensus methods ─────────────────────────────────────────────

    def checkin(self, key):
        """Validator heartbeat checkin (via Consensus)."""
        return self._send_tx(
            self._load_consensus,
            lambda c: c.functions.batchCheckin([key]),
        )

    def batch_checkin(self, keys):
        """Batch checkin for multiple validators (via Consensus)."""
        return self._send_tx(
            self._load_consensus,
            lambda c: c.functions.batchCheckin(keys),
        )

    def produce_block(self):
        """Produce the next consensus block."""
        return self._send_tx(
            self._load_consensus,
            lambda c: c.functions.produceBlock(),
        )

    def distribute(self):
        """Distribute emissions (via Consensus)."""
        return self._send_tx(
            self._load_consensus,
            lambda c: c.functions.distributeEmissions(),
        )

    # ── Reward claims ─────────────────────────────────────────────────

    def claim_staker_rewards(self):
        """Claim accumulated staker rewards."""
        return self._send_tx(
            self._load_consensus,
            lambda c: c.functions.claimStakerRewards(),
        )

    def claim_validator_rewards(self, key, to=None):
        """Claim validator commission rewards."""
        if to is None:
            _, _, account = self._load_consensus()
            to = account.address
        return self._send_tx(
            self._load_consensus,
            lambda c: c.functions.claimValidatorRewards(key, to),
        )

    # ── Staking: Views ────────────────────────────────────────────────

    def validator(self, key):
        """Get validator info from Staking."""
        _, contract, _ = self._load_staking()
        r = contract.functions.getValidator(key).call()
        return {
            'key': r[0], 'keyType': r[1],
            'registeredBlock': r[2],
            'commissionBps': r[3], 'active': r[4],
        }

    def stake_position(self, stake_id):
        _, contract, _ = self._load_staking()
        r = contract.functions.getStakePosition(stake_id).call()
        return {
            'staker': r[0], 'validatorKeyHash': r[1].hex(),
            'amount': str(r[2]), 'startBlock': r[3],
            'lockBlocks': r[4], 'stakeTimeBalance': str(r[5]),
            'blocksRemaining': r[6],
        }

    def user_stakes(self, address):
        from web3 import Web3
        _, contract, _ = self._load_staking()
        return contract.functions.getUserStakeIds(
            Web3.to_checksum_address(address)
        ).call()

    def validator_stakes(self, key):
        _, contract, _ = self._load_staking()
        return contract.functions.getValidatorStakeIds(key).call()

    def validator_total_stake_time(self, key):
        _, contract, _ = self._load_staking()
        return str(contract.functions.getValidatorTotalStakeTime(key).call())

    # ── Consensus: Views ──────────────────────────────────────────────

    def consensus_state(self):
        """Get consensus state."""
        _, contract, _ = self._load_consensus()
        r = contract.functions.getBlock().call()
        return {
            'currentBlock': r[0],
            'lastEmissionBlock': r[1],
            'totalBlocktime': r[2],
            'emissionRate': str(r[3]),
            'epochLength': r[4],
        }

    def leaderboard(self, limit=20):
        _, contract, _ = self._load_consensus()
        keys, scores = contract.functions.getLeaderboard(limit).call()
        return [{'keyHash': k.hex(), 'score': s} for k, s in zip(keys, scores)]

    def staker_rewards(self, address):
        from web3 import Web3
        _, contract, _ = self._load_consensus()
        return str(contract.functions.getStakerRewards(
            Web3.to_checksum_address(address)
        ).call())

    def validator_balance(self, key):
        _, contract, _ = self._load_consensus()
        return str(contract.functions.getValidatorBalance(key).call())

    # ── Registry ──────────────────────────────────────────────────────

    def _load_registry(self):
        from web3 import Web3
        w3, deploy, account = self._load_deployment()
        if 'registry' not in deploy:
            raise RuntimeError("Registry not deployed. Redeploy with Registry.")
        abi_path = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Registry.sol' / 'Registry.json'
        with open(abi_path) as f:
            artifact = json.load(f)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(deploy['registry']),
            abi=artifact['abi'],
        )
        return w3, contract, account

    def _load_governance_token(self):
        from web3 import Web3
        w3, deploy, account = self._load_deployment()
        abi_path = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Subnet.sol' / 'Subnet.json'
        with open(abi_path) as f:
            artifact = json.load(f)
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(deploy['governanceToken']),
            abi=artifact['abi'],
        )
        return w3, contract, account

    def register_subnet(self, name, subnet_addr, stake_time, consensus_addr):
        """Register a subnet in the Registry (locks governance token)."""
        from web3 import Web3
        w3, registry, account = self._load_registry()
        if not account:
            raise RuntimeError("PRIVATE_KEY env var required for transactions")

        # Approve governance token for registration cost
        cost = registry.functions.getRegistrationCost().call()
        if cost > 0:
            _, gov, _ = self._load_governance_token()
            approve_tx = gov.functions.approve(registry.address, cost).build_transaction({
                'from': account.address,
                'nonce': w3.eth.get_transaction_count(account.address),
                'gas': 100000,
            })
            signed = account.sign_transaction(approve_tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        return self._send_tx(
            self._load_registry,
            lambda c: c.functions.registerSubnet(
                name,
                Web3.to_checksum_address(subnet_addr),
                Web3.to_checksum_address(stake_time),
                Web3.to_checksum_address(consensus_addr),
            ),
        )

    def deregister_subnet(self, subnet_id):
        """Deregister a subnet from the Registry."""
        return self._send_tx(
            self._load_registry,
            lambda c: c.functions.deregisterSubnet(int(subnet_id)),
        )

    def subnets(self):
        """List all active subnets."""
        _, contract, _ = self._load_registry()
        raw = contract.functions.getAllSubnets().call()
        result = []
        for s in raw:
            score = contract.functions.getStakeScore(s[0]).call()
            immune = contract.functions.isImmune(s[0]).call()
            result.append({
                'id': s[0], 'owner': s[1], 'name': s[2],
                'subnet': s[3], 'stakeTime': s[4], 'consensus': s[5],
                'registeredBlock': s[6], 'active': s[7],
                'stakeScore': str(score), 'immune': immune,
            })
        return result

    def subnet_info(self, subnet_id):
        """Get a single subnet's info."""
        _, contract, _ = self._load_registry()
        r = contract.functions.getSubnet(int(subnet_id)).call()
        score = contract.functions.getStakeScore(int(subnet_id)).call()
        immune = contract.functions.isImmune(int(subnet_id)).call()
        return {
            'id': r[0], 'owner': r[1], 'name': r[2],
            'subnet': r[3], 'stakeTime': r[4], 'consensus': r[5],
            'registeredBlock': r[6], 'active': r[7],
            'stakeScore': str(score), 'immune': immune,
        }

    def weakest_subnet(self):
        """Show which subnet would be replaced at capacity."""
        _, contract, _ = self._load_registry()
        weak_id, weak_score, found = contract.functions.getWeakestSubnet().call()
        return {'id': weak_id, 'score': str(weak_score), 'found': found}

    # ── Status ────────────────────────────────────────────────────────

    def status(self):
        deploy_path = self.module_dir / 'config.json'
        if not deploy_path.exists():
            return {'deployed': False}
        with open(deploy_path) as f:
            data = json.load(f)
        network = os.environ.get('NETWORK', 'base_sepolia')
        contracts = data.get('contracts', {}).get(network, {})
        info = {
            'deployed': bool(contracts),
            'network': network,
            'urls': data.get('urls', {}),
            'contracts': contracts,
        }
        if contracts:
            info['subnet_explorer'] = f"https://sepolia.basescan.org/address/{contracts.get('subnet', '')}"
            info['consensus_explorer'] = f"https://sepolia.basescan.org/address/{contracts.get('consensus', '')}"
        try:
            info['consensus_state'] = self.consensus_state()
        except Exception:
            pass
        return info

    # ── API client ────────────────────────────────────────────────────

    def call(self, fn='health', params=None, timeout=10):
        """Call the staketime FastAPI server directly via HTTP."""
        import requests as req
        url = f'http://localhost:{self.api_port}/{fn}'
        method = 'GET' if fn == 'health' else 'POST'
        try:
            if method == 'GET':
                resp = req.get(url, timeout=timeout)
            else:
                resp = req.post(url, json=params or {}, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except req.ConnectionError:
            return {'error': f'API not running on port {self.api_port}. Run serve() first.'}
        except Exception as e:
            return {'error': str(e)}

    c = call
