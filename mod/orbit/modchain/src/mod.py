"""
modchain — proxy-router namespace prioritised by staketime.

A trimmed-down 'chain': one contract, NamespaceRegistry, that
records which mod owns which name in the router. Highest STT
stake wins. The Python side bridges on-chain claims into the
local routy proxy.

Usage:
  m.fn('modchain/status')()
  m.fn('modchain/deploy')()
  m.fn('modchain/claim')(name='polymarket', stake=10, target_url='http://localhost:3000', kind='app')
  m.fn('modchain/topup')(name='polymarket', amount=20)
  m.fn('modchain/release')(name='polymarket')
  m.fn('modchain/claims')()
  m.fn('modchain/sync')()              # push on-chain claims to routy
"""

import json
import os
import subprocess
from pathlib import Path

import mod as m


DIR = Path(__file__).parent
ROOT = DIR.parent


def _run(cmd, cwd=None, timeout=120):
    result = subprocess.run(
        cmd, shell=True, cwd=cwd or str(ROOT),
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result.stdout.strip()


class Mod:
    description = (
        "modchain — namespace registry for the proxy router; "
        "highest staketime wins the name."
    )

    def __init__(self, config=None, **kwargs):
        self.module_dir = ROOT
        self.config = config or self._load_config()

    def _load_config(self):
        p = self.module_dir / 'config.json'
        if p.exists():
            with open(p) as f:
                return json.load(f)
        return {}

    def forward(self, **kwargs):
        return self.status()

    # ── Build / Deploy ───────────────────────────────────────────────

    def compile(self):
        _run('npx hardhat compile', cwd=str(self.module_dir))
        return {'compiled': True}

    def deploy(self, network='base_sepolia'):
        self.compile()
        out = _run(
            f'npx hardhat run scripts/deploy.js --network {network}',
            cwd=str(self.module_dir), timeout=300,
        )
        self.config = self._load_config()
        return {
            'network': network,
            'contracts': self.config.get('contracts', {}).get(network, {}),
            'output': out,
        }

    def test(self):
        out = _run('npx hardhat test', cwd=str(self.module_dir), timeout=300)
        return {'output': out}

    # ── Loaders ──────────────────────────────────────────────────────

    def _network(self):
        return os.environ.get('NETWORK', 'base_sepolia')

    def _rpc(self):
        return os.environ.get(
            'BASE_TESTNET_RPC_URL', 'https://sepolia.base.org'
        )

    def _load(self):
        from web3 import Web3
        net = self._network()
        contracts = self.config.get('contracts', {}).get(net)
        if not contracts:
            raise RuntimeError(f"Not deployed on {net}. Run deploy() first.")

        abi_path = (
            self.module_dir / 'artifacts' / 'src' / 'contracts'
            / 'NamespaceRegistry.sol' / 'NamespaceRegistry.json'
        )
        if not abi_path.exists():
            raise RuntimeError(f"ABI missing at {abi_path}. Run compile().")
        with open(abi_path) as f:
            artifact = json.load(f)

        w3 = Web3(Web3.HTTPProvider(self._rpc()))
        registry = w3.eth.contract(
            address=Web3.to_checksum_address(contracts['namespaceRegistry']),
            abi=artifact['abi'],
        )
        pk = os.environ.get('PRIVATE_KEY')
        account = w3.eth.account.from_key(pk) if pk else None
        return w3, registry, account, contracts

    def _load_stt(self):
        from web3 import Web3
        w3, _, account, contracts = self._load()
        # Minimal ERC20 ABI for approve.
        erc20 = [
            {"constant": False, "inputs": [
                {"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ], "name": "approve", "outputs": [
                {"name": "", "type": "bool"}
            ], "type": "function"},
            {"constant": True, "inputs": [
                {"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"},
            ], "name": "allowance", "outputs": [
                {"name": "", "type": "uint256"}
            ], "type": "function"},
        ]
        stt = w3.eth.contract(
            address=Web3.to_checksum_address(contracts['stt']),
            abi=erc20,
        )
        return w3, stt, account

    def _send(self, w3, account, fn, gas=400_000):
        if not account:
            raise RuntimeError("PRIVATE_KEY env required for transactions")
        tx = fn.build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': gas,
        })
        signed = account.sign_transaction(tx)
        h = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(h, timeout=120)
        return {'success': receipt.status == 1, 'tx_hash': h.hex()}

    def _approve_if_needed(self, amount_wei):
        w3, stt, account = self._load_stt()
        _, registry, _, _ = self._load()
        cur = stt.functions.allowance(account.address, registry.address).call()
        if cur >= amount_wei:
            return None
        return self._send(
            w3, account,
            stt.functions.approve(registry.address, 2 ** 255),
            gas=80_000,
        )

    @staticmethod
    def _to_wei(amount):
        from web3 import Web3
        if isinstance(amount, (int, float)):
            return Web3.to_wei(amount, 'ether')
        return int(amount)

    # ── Mutations ────────────────────────────────────────────────────

    def claim(self, name, stake, target_url, kind='app'):
        """Claim a name (or outbid the current holder)."""
        amt = self._to_wei(stake)
        self._approve_if_needed(amt)
        w3, registry, account, _ = self._load()
        return self._send(
            w3, account,
            registry.functions.claim(name, amt, target_url, kind),
        )

    def topup(self, name, amount):
        """Add stake to your existing claim."""
        amt = self._to_wei(amount)
        self._approve_if_needed(amt)
        w3, registry, account, _ = self._load()
        return self._send(
            w3, account,
            registry.functions.topUp(name, amt),
        )

    def set_target(self, name, target_url, kind='app'):
        w3, registry, account, _ = self._load()
        return self._send(
            w3, account,
            registry.functions.setTarget(name, target_url, kind),
        )

    def release(self, name):
        w3, registry, account, _ = self._load()
        return self._send(
            w3, account,
            registry.functions.release(name),
        )

    def withdraw(self):
        """Withdraw STT credited from being outbid."""
        w3, registry, account, _ = self._load()
        return self._send(
            w3, account,
            registry.functions.withdraw(),
        )

    # ── Views ────────────────────────────────────────────────────────

    def claims(self):
        """All active claims."""
        _, registry, _, _ = self._load()
        raw = registry.functions.getActiveClaims().call()
        return [self._claim_to_dict(c) for c in raw]

    def get(self, name):
        _, registry, _, _ = self._load()
        c = registry.functions.getClaim(name).call()
        return self._claim_to_dict(c)

    def threshold(self, name):
        """STT amount needed to outbid this name."""
        _, registry, _, _ = self._load()
        return str(registry.functions.outbidThreshold(name).call())

    @staticmethod
    def _claim_to_dict(c):
        # Tuple order matches the Claim struct.
        return {
            'owner':     c[0],
            'name':      c[1],
            'targetUrl': c[2],
            'kind':      c[3],
            'stake':     str(c[4]),
            'claimedAt': c[5],
            'active':    c[6],
        }

    # ── Router sync ──────────────────────────────────────────────────

    def sync(self, router_url=None):
        """
        Push active on-chain claims to the routy proxy.

        For each claim, POST /_api/sync with apps & apis lists. Highest
        stake already wins on chain — sync just mirrors that ordering.
        """
        import requests
        url = (router_url
               or self.config.get('router', {}).get('url', 'http://localhost:8080'))

        active = self.claims()
        # Sort by stake DESC so the router sees highest-priority entries first.
        active.sort(key=lambda c: int(c['stake']), reverse=True)

        apps, apis = [], []
        for c in active:
            entry = {
                'name': c['name'],
                'target_url': c['targetUrl'],
                'description': f"staked {c['stake']}",
                'website_type': c['kind'],
            }
            (apis if c['kind'] == 'api' else apps).append(entry)

        try:
            resp = requests.post(
                f'{url}/_api/sync',
                json={'apps': apps, 'apis': apis},
                timeout=10,
            )
            resp.raise_for_status()
            return {
                'synced': resp.json(),
                'apps': len(apps),
                'apis': len(apis),
                'router': url,
            }
        except requests.ConnectionError:
            return {'error': f'Router not running at {url}'}
        except Exception as e:
            return {'error': str(e)}

    # ── Status ───────────────────────────────────────────────────────

    def status(self):
        net = self._network()
        contracts = self.config.get('contracts', {}).get(net, {})
        info = {
            'deployed': bool(contracts),
            'network': net,
            'contracts': contracts,
            'router': self.config.get('router', {}).get('url'),
        }
        if contracts:
            try:
                info['active_claims'] = len(self.claims())
            except Exception as e:
                info['claims_error'] = str(e)
        return info
