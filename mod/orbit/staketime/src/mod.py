"""
StakeTime — Delegated staking + Consensus (pluggable) on EVM (Base Sepolia).

Primitives:
  Mod        — ERC20 token (zero initial supply, minter decided by consensus)
  Staking    — Stake Mod tokens ON validators, earn STT
  Consensus  — Mints new Mod tokens as emissions each epoch
  Inflation  — Pluggable emission curves (halving, flat, linear, sigmoid)
  Registry   — Competitive 420-slot subnet directory

Backend: Rust (alloy + PyO3) — mod.py interfaces via staketime_rs native module.

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
import sys
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


def _load_engine():
    """Load the Rust StakeTimeEngine via PyO3."""
    try:
        from staketime_rs import StakeTimeEngine
    except ImportError:
        # Add the src directory to path for the .so
        rs_parent = str(DIR)
        if rs_parent not in sys.path:
            sys.path.insert(0, rs_parent)
        try:
            from staketime_rs import StakeTimeEngine
        except ImportError:
            raise ImportError(
                "staketime_rs not built. Run: cd src/rs && ./build.sh"
            )
    rpc = os.environ.get('BASE_TESTNET_RPC_URL', 'https://sepolia.base.org')
    pk = os.environ.get('PRIVATE_KEY')
    network = os.environ.get('NETWORK', 'base_sepolia')
    engine = StakeTimeEngine(str(ROOT), rpc, pk, network)
    engine.init()
    return engine


class Mod:
    description = "StakeTime + Consensus — Delegated staking with pluggable consensus modules (Yuma, Linear, Staked). Rust backend via PyO3."

    def __init__(self, config=None, **kwargs):
        self.module_dir = ROOT
        self.api_port = API_PORT
        self.app_port = APP_PORT
        self.config = config or self._load_config()
        self._engine = None

    def _load_config(self):
        cfg_path = self.module_dir / 'config.json'
        if cfg_path.exists():
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    @property
    def engine(self):
        """Lazy-init the Rust engine."""
        if self._engine is None:
            self._engine = _load_engine()
        return self._engine

    # ── Default entry point ───────────────────────────────────────────

    def forward(self, **kwargs):
        return self.status()

    # ── Build / Deploy ────────────────────────────────────────────────

    def compile(self):
        _run('npx hardhat compile', cwd=str(self.module_dir))
        st_abi = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Staking.sol' / 'Staking.json'
        sub_abi = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'Mod.sol' / 'Mod.json'
        con_abi = self.module_dir / 'artifacts' / 'src' / 'contracts' / 'consensus' / 'yuma' / 'ConsensusYuma.sol' / 'ConsensusYuma.json'
        return {
            'compiled': True,
            'staking_abi': str(st_abi),
            'mod_abi': str(sub_abi),
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
            self._engine = None  # Reset engine to pick up new addresses
            return {
                'contracts': data.get('contracts', {}).get(network, {}),
                'network': network,
                'output': output,
            }
        return {'output': output}

    def test(self):
        output = _run('npx hardhat test', cwd=str(self.module_dir), timeout=300)
        return {'output': output}

    def build_rs(self):
        """Build the Rust PyO3 module."""
        output = _run('./build.sh', cwd=str(self.module_dir / 'src' / 'rs'), timeout=300)
        return {'output': output, 'built': True}

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
        env['PYTHONPATH'] = f"{mod_root}:{self.module_dir}:{DIR}:{env.get('PYTHONPATH', '')}"
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

    # ── Staking: Validator methods (Rust backend) ─────────────────────

    def register(self, key, key_type=0, commission_bps=1000):
        """Register a validator (via Rust engine)."""
        result = self.engine.register(key, int(key_type), int(commission_bps))
        return json.loads(result)

    # ── Staking: Staking methods (Rust backend) ──────────────────────

    def stake_on(self, validator_key, amount, lock_blocks=0):
        """Stake Mod tokens on a validator (via Rust engine)."""
        if isinstance(amount, (int, float)) and amount < 1e15:
            amount_wei = str(int(amount * 10**18))
        else:
            amount_wei = str(int(amount))
        result = self.engine.stake_on(validator_key, amount_wei, int(lock_blocks))
        return json.loads(result)

    def unstake_from(self, stake_id):
        """Unstake a position after lock period (via Rust engine)."""
        result = self.engine.unstake_from(int(stake_id))
        return json.loads(result)

    # ── Consensus methods (Rust backend) ──────────────────────────────

    def checkin(self, key):
        """Validator heartbeat checkin (via Rust engine)."""
        result = self.engine.checkin(key)
        return json.loads(result)

    def batch_checkin(self, keys):
        """Batch checkin for multiple validators (via Rust engine)."""
        result = self.engine.batch_checkin(keys)
        return json.loads(result)

    def produce_block(self):
        """Produce the next consensus block (via Rust engine)."""
        result = self.engine.produce_block()
        return json.loads(result)

    def distribute(self):
        """Distribute emissions (via Rust engine)."""
        result = self.engine.distribute()
        return json.loads(result)

    # ── Reward claims (Rust backend) ──────────────────────────────────

    def claim_staker_rewards(self):
        """Claim accumulated staker rewards (via Rust engine)."""
        result = self.engine.claim_staker_rewards()
        return json.loads(result)

    def claim_validator_rewards(self, key, to=None):
        """Claim validator commission rewards (via Rust engine)."""
        result = self.engine.claim_validator_rewards(key, to)
        return json.loads(result)

    # ── Staking: Views (Rust backend) ─────────────────────────────────

    def validator(self, key):
        """Get validator info from Staking (via Rust engine)."""
        result = self.engine.get_validator(key)
        return json.loads(result)

    def stake_position(self, stake_id):
        """Get a stake position."""
        result = self.engine.get_stake_position(int(stake_id))
        return json.loads(result)

    def user_stakes(self, address):
        """Get all stake positions for an address."""
        result = self.engine.get_user_stakes(address)
        return json.loads(result)

    def validator_stakes(self, key):
        """Get validator info including stakes."""
        return self.validator(key)

    def validator_total_stake_time(self, key):
        """Get total STT for a validator."""
        info = self.validator(key)
        return info.get('totalSTT', '0')

    # ── Consensus: Views (Rust backend) ───────────────────────────────

    def consensus_state(self):
        """Get consensus state (via Rust engine)."""
        result = self.engine.get_consensus()
        return json.loads(result)

    def leaderboard(self, limit=20):
        """Get validators sorted by score."""
        validators = json.loads(self.engine.get_validators())
        validators.sort(key=lambda v: int(v.get('blocktimeScore', 0)), reverse=True)
        return validators[:int(limit)]

    def staker_rewards(self, address):
        """Get staker rewards (via Rust engine)."""
        return self.engine.get_staker_rewards(address)

    def validator_balance(self, key):
        """Get validator balance."""
        info = self.validator(key)
        return info.get('balance', '0')

    # ── Registry (Rust backend) ───────────────────────────────────────

    def register_subnet(self, name, subnet_addr, stake_time, consensus_addr):
        """Register a subnet in the Registry (via Rust engine)."""
        result = self.engine.register_subnet(name, subnet_addr, stake_time, consensus_addr)
        return json.loads(result)

    def deregister_subnet(self, subnet_id):
        """Deregister a subnet from the Registry (via Rust engine)."""
        result = self.engine.deregister_subnet(int(subnet_id))
        return json.loads(result)

    def subnets(self):
        """List all active subnets (via Rust engine)."""
        result = self.engine.get_subnets()
        return json.loads(result)

    def subnet_info(self, subnet_id):
        """Get a single subnet's info (via Rust engine)."""
        result = self.engine.get_subnet(int(subnet_id))
        return json.loads(result)

    def weakest_subnet(self):
        """Show which subnet would be replaced at capacity (via Rust engine)."""
        result = self.engine.get_weakest_subnet()
        return json.loads(result)

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
            'backend': 'rust+pyo3',
        }
        if contracts:
            info['mod_explorer'] = f"https://sepolia.basescan.org/address/{contracts.get('mod', contracts.get('subnet', ''))}"
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
