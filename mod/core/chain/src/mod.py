"""Chain Interface - Orchestrator for contract deployment.

Provides:
- Fleet deployment straight from the compiled artifacts in artifacts/
- Deploy waves derived from the constructor dependency graph
- Parallel deployment with pre-assigned nonces (one key or several)
- Forks: the same fleet redeployed under a new owner
"""

from web3 import Web3
from typing import Dict, Any, Optional, List, Union
import json
import os
import subprocess
import threading
import time
import mod as m
import requests
from eth_account import Account
from eth_account.signers.local import LocalAccount

# Price/whitelist sentinel for native ETH, as the oracle and TokenGate use it.
ETH_SENTINEL = '0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE'
# Supply minted to the deployer for the mock stables and the native token.
MOCK_SUPPLY = 1_000_000 * 10 ** 18


class Mod:
    """Chain orchestrator - manages contract mods and parallel deployment."""

    network2url = {
        'testnet': 'https://sepolia.base.org',
        'ganache': 'http://localhost:8545',
        'mainnet': 'https://mainnet.base.org'
    }
    conns = {}

    # ── The fleet, as data ──────────────────────────────────────────────────
    # Each mod lists what it puts on chain — (config key, contract, constructor
    # args) — and the calls that wire it up once everything is standing. An
    # '@Key' arg is another contract's address, resolved from the current run
    # first and the network's config second, so the dependency graph (and the
    # deploy waves that fall out of it) is derived, never hand-kept.
    # Mirrors the constructors in src/contracts and the console's DeployGraph.
    FLEET = {
        'token': {'deploys': [
            ('USDC', 'Token', ['USD Coin', 'USDC', MOCK_SUPPLY]),
            ('USDT', 'Token', ['Tether USD', 'USDT', MOCK_SUPPLY]),
            ('DAI', 'Token', ['Dai Stablecoin', 'DAI', MOCK_SUPPLY]),
            ('NativeToken', 'Token', ['Native Token', 'NAT', MOCK_SUPPLY]),
        ]},
        'oracle': {
            'deploys': [('ManualPriceOracle', 'ManualPriceOracle', [])],
            # Stables at $1, ETH at $3000 — 8 decimals, Chainlink convention.
            'setup': [
                ('ManualPriceOracle', 'setPrice', ['@USDC', 100_000_000, 8]),
                ('ManualPriceOracle', 'setPrice', ['@USDT', 100_000_000, 8]),
                ('ManualPriceOracle', 'setPrice', ['@DAI', 100_000_000, 8]),
                ('ManualPriceOracle', 'setPrice', [ETH_SENTINEL, 300_000_000_000, 8]),
            ],
        },
        'registry': {'deploys': [('Registry', 'Registry', [])]},
        'perms': {'deploys': [('Perms', 'Perms', [])]},
        'tokengate': {
            'deploys': [('TokenGate', 'TokenGate', ['@ManualPriceOracle'])],
            'setup': [
                ('TokenGate', 'whitelistToken', ['@USDC']),
                ('TokenGate', 'whitelistToken', ['@USDT']),
                ('TokenGate', 'whitelistToken', ['@DAI']),
                ('TokenGate', 'whitelistToken', [ETH_SENTINEL]),
            ],
        },
        'bloctime': {
            'deploys': [('BlocTime', 'BlocTime',
                         ['@NativeToken', 'BlocTime Token', 'BLOC', 100_000, 5_000])],
            # (lock blocks, multiplier in basis points) — longer lock, more BLOC.
            'setup': [('BlocTime', 'setPoints',
                       [[(0, 10_000), (10_000, 15_000), (50_000, 20_000), (100_000, 30_000)]])],
        },
        'treasury': {
            # 2000 bp = 20% of fees to the owner, the rest to BLOC holders.
            'deploys': [('Treasury', 'Treasury', [2_000, '@TokenGate'])],
            'setup': [('Treasury', 'setGovernanceToken', ['@BlocTime'])],
        },
        'market': {
            'deploys': [('Market', 'Market',
                         ['BlocTime Market Token', 'BTMT', '@Treasury', '@TokenGate'])],
            # Debit deploys after Market and points back at it, so this call
            # can only happen once the whole fleet is up.
            'setup': [('Market', 'setDebitContract', ['@Debit'])],
        },
        'debit': {'deploys': [('Debit', 'Debit', ['@Market'])]},
    }

    # Tokens that already exist on a network — recorded, never redeployed.
    KNOWN_ADDRESSES = {
        'mainnet': {  # Base mainnet
            'USDC': '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
            'USDT': '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2',
            'DAI': '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb',
        },
    }

    # config.json is read-modify-written from parallel deploy threads.
    _config_lock = threading.Lock()

    # Built-in ABIs for stable core contracts. Used as a fallback when the
    # compiled artifact / pinned IPFS ABI isn't available in a checkout, so the
    # contracts can still be read/written (e.g. by the web catalog and the
    # register/mint/pool flow) without a full contract build. Keyed by the
    # `contract` name used in config deployments. Only the functions the Python
    # layer actually calls are declared.
    BUILTIN_ABIS = {
        'Registry': [
            {'inputs': [], 'name': 'nextModId',
             'outputs': [{'type': 'uint256', 'name': ''}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'}], 'name': 'getMod',
             'outputs': [{'type': 'address', 'name': 'owner'},
                         {'type': 'string', 'name': 'name'},
                         {'type': 'string', 'name': 'data'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'user'}], 'name': 'getUserMods',
             'outputs': [{'type': 'uint256[]', 'name': ''}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'creator'},
                        {'type': 'string', 'name': 'name'}], 'name': 'isNameTaken',
             'outputs': [{'type': 'bool', 'name': ''}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'string', 'name': 'name'},
                        {'type': 'string', 'name': 'data'}], 'name': 'registerMod',
             'outputs': [{'type': 'uint256', 'name': ''}],
             'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'modId'},
                        {'type': 'string', 'name': 'data'}], 'name': 'updateMod',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'modId'}], 'name': 'removeMod',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
        ],
        # Generic ERC20 — covers USDC/USDT/NativeToken (config `contract`: Token).
        'Token': [
            {'inputs': [{'type': 'address', 'name': 'a'}], 'name': 'balanceOf',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'totalSupply', 'outputs': [{'type': 'uint256'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'decimals', 'outputs': [{'type': 'uint8'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'symbol', 'outputs': [{'type': 'string'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'owner'},
                        {'type': 'address', 'name': 'spender'}], 'name': 'allowance',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'spender'},
                        {'type': 'uint256', 'name': 'amount'}], 'name': 'approve',
             'outputs': [{'type': 'bool'}], 'stateMutability': 'nonpayable', 'type': 'function'},
        ],
        'BlocTime': [
            {'inputs': [{'type': 'address', 'name': 'a'}], 'name': 'balanceOf',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'totalSupply', 'outputs': [{'type': 'uint256'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'symbol', 'outputs': [{'type': 'string'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'decimals', 'outputs': [{'type': 'uint8'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'user'}], 'name': 'getUserStakeIds',
             'outputs': [{'type': 'uint256[]'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'user'},
                        {'type': 'uint256', 'name': 'stakeId'}], 'name': 'getStakePosition',
             'outputs': [{'type': 'uint256'}, {'type': 'uint256'}, {'type': 'uint256'},
                         {'type': 'uint256'}, {'type': 'uint256'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'user'}], 'name': 'getStakeInfo',
             'outputs': [{'type': 'uint256'}, {'type': 'uint256'}, {'type': 'uint256'},
                         {'type': 'uint256'}, {'type': 'uint256'}],
             'stateMutability': 'view', 'type': 'function'},
        ],
        'TokenGate': [
            {'inputs': [], 'name': 'getTokenList', 'outputs': [{'type': 'address[]'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'token'}], 'name': 'isTokenWhitelisted',
             'outputs': [{'type': 'bool'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'token'}], 'name': 'getTokenPrice',
             'outputs': [{'type': 'uint256', 'name': 'price'}, {'type': 'uint8', 'name': 'decimals'},
                         {'type': 'uint256', 'name': 'timestamp'}],
             'stateMutability': 'view', 'type': 'function'},
        ],
        'Market': [
            {'inputs': [{'type': 'address', 'name': 'paymentToken'},
                        {'type': 'uint256', 'name': 'paymentAmount'}], 'name': 'mint',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [], 'name': 'treasury', 'outputs': [{'type': 'address'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'creditFeeBps', 'outputs': [{'type': 'uint256'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'decimals', 'outputs': [{'type': 'uint8'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'symbol', 'outputs': [{'type': 'string'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'a'}], 'name': 'balanceOf',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
        ],
        'Treasury': [
            {'inputs': [], 'name': 'governanceToken', 'outputs': [{'type': 'address'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'ownerPercentage', 'outputs': [{'type': 'uint256'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [], 'name': 'getTreasuryTokens', 'outputs': [{'type': 'address[]'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'holder'},
                        {'type': 'address', 'name': 'token'}], 'name': 'getClaimableAmount',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'token'}], 'name': 'withdrawToken',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'token'},
                        {'type': 'uint256', 'name': 'amount'}], 'name': 'fundTreasury',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
        ],
        # DeFi yield aggregator — modular multi-strategy vault.
        'YieldVault': [
            {'inputs': [], 'name': 'strategyCount', 'outputs': [{'type': 'uint256'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'}], 'name': 'strategies',
             'outputs': [{'type': 'address', 'name': 'adapter'},
                         {'type': 'address', 'name': 'asset'},
                         {'type': 'string', 'name': 'name'},
                         {'type': 'bool', 'name': 'enabled'},
                         {'type': 'uint256', 'name': 'totalShares'},
                         {'type': 'uint256', 'name': 'trackedPrincipal'},
                         {'type': 'uint256', 'name': 'accRewardPerShare'}],
             'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'address', 'name': 'asset'},
                        {'type': 'address', 'name': 'adapter'},
                        {'type': 'string', 'name': 'name'}], 'name': 'addStrategy',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'},
                        {'type': 'uint256', 'name': 'amount'}], 'name': 'deposit',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'},
                        {'type': 'uint256', 'name': 'shares'}], 'name': 'withdraw',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'}], 'name': 'harvest',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'}], 'name': 'claim',
             'outputs': [], 'stateMutability': 'nonpayable', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'},
                        {'type': 'address', 'name': 'user'}], 'name': 'pendingReward',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'}], 'name': 'pendingProfit',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
            {'inputs': [{'type': 'uint256', 'name': 'id'},
                        {'type': 'address', 'name': 'user'}], 'name': 'userShares',
             'outputs': [{'type': 'uint256'}], 'stateMutability': 'view', 'type': 'function'},
        ],
    }

    def __init__(self, network: str = 'testnet', key='test'):
        self.network = network
        self.rpc_url = self.network2url.get(network, network)

        if self.rpc_url in self.conns:
            self.w3 = self.conns[self.rpc_url]
        else:
            m.print(f'Connecting to {self.rpc_url} {network}', color='cyan')
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            self.conns[self.rpc_url] = self.w3

        self.chain_id = self.w3.eth.chain_id
        self.contracts = {}
        self.path = m.dp('chain')
        self.set_key(key)
        self.contracts_path = self.path + '/artifacts/src/contracts'
        if not os.path.exists(self.contracts_path):
            os.makedirs(self.contracts_path, exist_ok=True)
        self.config = m.config('chain')
        self.load_all_contracts()
        self._mods = {}

    # ==================== FLEET GRAPH ====================

    def list_mods(self):
        """Mods the deploy pipeline knows how to ship."""
        return list(self.FLEET)

    @classmethod
    def key_owner(cls, config_key):
        """The mod that deploys the contract stored under this config key."""
        for name, spec in cls.FLEET.items():
            if any(key.lower() == config_key.lower() for key, _c, _a in spec['deploys']):
                return name
        return None

    @classmethod
    def deps_of(cls, mod_name):
        """Mods whose addresses this one's constructors take."""
        deps = []
        for _key, _contract, args in cls.FLEET[mod_name]['deploys']:
            for arg in args:
                if not (isinstance(arg, str) and arg.startswith('@')):
                    continue
                owner = cls.key_owner(arg[1:])
                if owner and owner != mod_name and owner not in deps:
                    deps.append(owner)
        return deps

    @classmethod
    def waves(cls, mods=None):
        """Deploy waves: a mod sits one wave behind its deepest dependency, so
        everything in a wave can go out at once and waves run in order.

        Args:
            mods: Subset to deploy (default: the whole fleet). Depths are
                  measured on the full graph, so a partial deploy keeps the
                  same relative order and trusts what's already on chain.
        """
        unknown = [n for n in (mods or []) if n not in cls.FLEET]
        if unknown:
            raise ValueError(f'Unknown mod(s): {", ".join(unknown)}')

        depth = {}

        def depth_of(name):
            if name not in depth:
                depth[name] = max([depth_of(d) + 1 for d in cls.deps_of(name)] or [0])
            return depth[name]

        rows = {}
        for name in cls.FLEET:
            if mods is None or name in mods:
                rows.setdefault(depth_of(name), []).append(name)
        return [rows[d] for d in sorted(rows)]

    # ==================== DEPLOY ====================

    def deploy(self, network: str = None, keys: list = None,
               deployer_key: str = None, mods: list = None,
               setup: bool = True):
        """Deploy the fleet (or a subset), one dependency wave at a time.

        Args:
            network: Target network (testnet/ganache/mainnet). Default: self.network.
            keys: Proxy keys to spread the deploy across — each mod in a wave
                  gets one, so several deploys stay in flight. Ownership lands
                  on deployer_key afterwards.
            deployer_key: Key that owns the contracts when the dust settles.
            mods: Subset of the fleet (default: all of it).
            setup: Run the post-deploy wiring calls.

        Returns:
            {config key: address} for everything that went on chain.
        """
        network = network or self.network
        deployer_key = deployer_key or getattr(self, 'key_name', 'test')
        key_names = list(keys) if keys else [deployer_key]
        deployer = self.address_of(deployer_key)

        # Deploys read the artifacts on disk; a stale/absent toolchain
        # shouldn't sink the run, a missing artifact still errors below.
        try:
            self.compile()
        except Exception as e:
            m.print(f'compile skipped - {e}', color='yellow')

        groups = self.waves(mods)
        m.print(f'Deploying to {network} with {len(key_names)} key(s)', color='cyan')
        m.print(f'Waves: {groups}', color='cyan')
        m.print(f'Deployer (final owner): {deployer}', color='cyan')

        records = self._deploy_waves(groups, network, key_names)

        if setup:
            m.print('\n--- Wiring up ---', color='yellow')
            self._run_setup([n for g in groups for n in g], network, deployer_key, records)

        if key_names != [deployer_key]:
            m.print(f'\n--- Handing ownership to {deployer} ---', color='yellow')
            self._transfer_ownership(records, deployer, network)

        self.config = m.config('chain')
        self.load_all_contracts()
        try:
            self.sync_app()
        except Exception:
            pass

        deployed = {key: r['address'] for key, r in records.items()}
        m.print(f'\nDeployment complete - {len(deployed)} contract(s) on {network}', color='green')
        return deployed

    def deploy_mod(self, mod_name, network=None, key=None):
        """Deploy a single mod. Its dependencies must already be on chain."""
        return self.deploy(network=network, mods=[mod_name], deployer_key=key)

    def _deploy_waves(self, groups, network, key_names, record=True):
        """Run the waves, returning {config key: {address, contract, key}}.

        Within a wave each mod deploys in parallel on a pre-assigned nonce run,
        so one key can keep several transactions in flight without colliding.
        """
        w3 = self._w3(network)
        records = {}

        for index, group in enumerate(groups):
            m.print(f'\n--- Wave {index + 1}: {", ".join(group)} ---', color='yellow')

            plans, next_nonce = {}, {}
            for i, name in enumerate(group):
                plan, known = self._plan(name, network, record=record)
                records.update(known)
                key_name = key_names[i % len(key_names)]
                if key_name not in next_nonce:
                    next_nonce[key_name] = w3.eth.get_transaction_count(
                        self.address_of(key_name), 'pending')
                plans[name] = (key_name, next_nonce[key_name], plan)
                next_nonce[key_name] += len(plan)

            # Everything a wave needs was deployed by an earlier one, so each
            # mod resolves its args against the same snapshot.
            snapshot = dict(records)
            if len(group) == 1:
                name = group[0]
                key_name, nonce, plan = plans[name]
                records.update(self._deploy_plan(name, plan, network, key_name,
                                                 nonce, snapshot, record))
                continue

            futures = {}
            for name, (key_name, nonce, plan) in plans.items():
                future = m.submit(
                    self._deploy_plan,
                    dict(mod_name=name, plan=plan, network=network, key_name=key_name,
                         nonce=nonce, known=snapshot, record=record),
                    timeout=600,
                )
                futures[future] = name
            for future in m.as_completed(futures.keys()):
                name = futures[future]
                try:
                    records.update(future.result())
                except Exception as e:
                    m.print(f'{name}: FAILED -> {e}', color='red')
                    raise

        return records

    def _plan(self, mod_name, network, record=True):
        """What a mod actually has to put on chain, and what's already there."""
        known_addresses = self.KNOWN_ADDRESSES.get(network, {})
        plan, known = [], {}
        for config_key, contract, args in self.FLEET[mod_name]['deploys']:
            address = known_addresses.get(config_key)
            if not address:
                plan.append((config_key, contract, args))
                continue
            known[config_key] = {'address': address, 'contract': contract, 'key': None}
            if record:
                self._save_deployment(config_key, address, contract, network)
            m.print(f'{mod_name}: {config_key} already on {network} -> {address}', color='yellow')
        return plan, known

    def _deploy_plan(self, mod_name, plan, network, key_name, nonce, known, record=True):
        """Deploy one mod's contracts over its pre-assigned nonce run."""
        out = {}
        for config_key, contract, args in plan:
            resolved = [self._resolve(a, {**known, **out}, network, config_fallback=record)
                        for a in args]
            address = self._deploy_contract(contract, resolved, key_name, network, nonce)
            nonce += 1
            out[config_key] = {'address': address, 'contract': contract, 'key': key_name}
            m.print(f'{mod_name}: {config_key} -> {address}', color='green')
            if record:
                self._save_deployment(config_key, address, contract, network,
                                      deployer=self.address_of(key_name))
        return out

    def _deploy_contract(self, contract, args, key_name, network, nonce=None):
        """Deploy one contract from its compiled artifact."""
        artifact = self.artifact(contract)
        if not artifact.get('bytecode') or artifact['bytecode'] == '0x':
            raise ValueError(f'{contract} has no bytecode - is it an interface?')

        w3 = self._w3(network)
        account = self.account_of(key_name)
        factory = w3.eth.contract(abi=artifact['abi'], bytecode=artifact['bytecode'])
        params = {
            'from': account.address,
            'nonce': nonce if nonce is not None else w3.eth.get_transaction_count(
                account.address, 'pending'),
            # Explicit gas limit: eth_estimateGas is unreliable on load-balanced
            # public RPCs, and a wave's later nonces aren't simulatable anyway.
            # Every contract here is well under this; unused gas isn't charged.
            'gas': 6_000_000,
            'chainId': w3.eth.chain_id,
        }
        params.update(self._gas_fees(w3))
        tx = factory.constructor(*args).build_transaction(params)
        signed = w3.eth.account.sign_transaction(tx, account.key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status != 1:
            raise RuntimeError(f'{contract} deploy reverted (tx {tx_hash.hex()})')
        return receipt.contractAddress

    def _run_setup(self, names, network, key_name, records, config_fallback=True):
        """Post-deploy wiring - the calls that can only happen once the
        addresses exist. A step whose target or argument is missing is skipped
        with a warning, not fatal: a partial deploy wires what it can.

        Each call is signed by whichever key deployed the contract, since
        ownership only moves to the deployer once the wiring is done.
        """
        for name in names:
            for config_key, function, args in self.FLEET[name].get('setup', []):
                try:
                    resolved = [self._resolve(a, records, network, config_fallback)
                                for a in args]
                    contract = self._contract_at(config_key, network, records)
                    signer = (records.get(config_key) or {}).get('key') or key_name
                    self._send(contract, function, resolved, signer, network)
                    m.print(f'{name}: {config_key}.{function} ok', color='green')
                except Exception as e:
                    m.print(f'{name}: {config_key}.{function} skipped - {e}', color='yellow')

    def _transfer_ownership(self, records, new_owner, network):
        """Hand every Ownable contract we deployed to its final owner."""
        new_owner = Web3.to_checksum_address(new_owner)
        for config_key, record in records.items():
            key_name = record.get('key')
            if not key_name:
                continue  # recorded, not deployed by us
            try:
                contract = self._contract_at(config_key, network, records)
                # Ownable, specifically: Registry also has a transferOwnership,
                # but it moves a *mod's* owner and takes (modId, address).
                if not any(item.get('name') == 'transferOwnership'
                           and [i['type'] for i in item.get('inputs', [])] == ['address']
                           for item in contract.abi if item.get('type') == 'function'):
                    continue
                # Freshly deployed code can lag on a load-balanced RPC.
                owner = None
                for _attempt in range(5):
                    try:
                        owner = contract.functions.owner().call()
                        break
                    except Exception:
                        time.sleep(2)
                if owner is None:
                    m.print(f'{config_key}: could not read owner', color='red')
                    continue
                if owner.lower() == new_owner.lower():
                    continue
                self._send(contract, 'transferOwnership', [new_owner], key_name, network)
                m.print(f'{config_key}: owner -> {new_owner}', color='green')
            except Exception as e:
                m.print(f'{config_key}: ownership transfer failed - {e}', color='yellow')

    # ==================== DEPLOY PLUMBING ====================

    def _w3(self, network=None):
        """web3 client for a network (connections are shared class-wide)."""
        if network is None or network == self.network:
            return self.w3
        url = self.network2url.get(network, network)
        if url not in self.conns:
            self.conns[url] = Web3(Web3.HTTPProvider(url))
        return self.conns[url]

    def account_of(self, key_name):
        """Signing account for a key name. Never touches self.account, so
        parallel waves can sign with different keys."""
        return Account.from_key(m.key(key_name or 'test').private_key)

    def address_of(self, key_name):
        """Address behind a key name."""
        return self.account_of(key_name).address

    def artifact(self, contract):
        """Compiled hardhat artifact (abi + bytecode) for a contract name."""
        for root in (self.contracts_path, os.path.join(self.path, 'artifacts')):
            for dirpath, _dirs, files in os.walk(root):
                if f'{contract}.json' in files:
                    with open(os.path.join(dirpath, f'{contract}.json')) as f:
                        return json.load(f)
        raise ValueError(f'No compiled artifact for {contract} - run `m chain/compile`')

    def deployed_entry(self, config_key, network=None):
        """Config record for a contract key on a network (case-insensitive)."""
        contracts = (m.config('chain').get('deployments', {})
                     .get(network or self.network, {}).get('contracts', {}))
        for key, info in contracts.items():
            if key.lower() == config_key.lower():
                return info
        return None

    def deployed_address(self, config_key, network=None):
        """Address recorded for a contract key, or None."""
        return (self.deployed_entry(config_key, network) or {}).get('address')

    def _resolve(self, value, records, network, config_fallback=True):
        """'@Key' -> a deployed address; every other arg passes through."""
        if not (isinstance(value, str) and value.startswith('@')):
            return value
        config_key = value[1:]
        record = records.get(config_key)
        address = record['address'] if record else (
            self.deployed_address(config_key, network) if config_fallback else None)
        if not address:
            raise ValueError(f'{config_key} has no address on {network} - deploy it first')
        return Web3.to_checksum_address(address)

    def _contract_at(self, config_key, network, records=None):
        """A web3 contract bound to the address recorded for a contract key."""
        record = (records or {}).get(config_key) or self.deployed_entry(config_key, network)
        if not record or not record.get('address'):
            raise ValueError(f'{config_key} is not deployed on {network}')
        name = record.get('contract') or config_key
        return self._w3(network).eth.contract(
            address=Web3.to_checksum_address(record['address']),
            abi=self.artifact(name)['abi'])

    def _send(self, contract, function, args, key_name, network):
        """Sign and broadcast a contract call with an arbitrary key."""
        w3 = self._w3(network)
        account = self.account_of(key_name)
        params = {
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address, 'pending'),
            'gas': 500_000,
            'chainId': w3.eth.chain_id,
        }
        params.update(self._gas_fees(w3))
        tx = getattr(contract.functions, function)(*args).build_transaction(params)
        signed = w3.eth.account.sign_transaction(tx, account.key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        if receipt.status != 1:
            raise RuntimeError(f'{function} reverted (tx {tx_hash.hex()})')
        return receipt

    def _save_deployment(self, config_key, address, contract, network=None, deployer=None):
        """Record a deployed address in the module config."""
        network = network or self.network
        with self._config_lock:
            config = m.config('chain')
            deployment = config.setdefault('deployments', {}).setdefault(network, {})
            deployment.setdefault('chainId', str(self._w3(network).eth.chain_id))
            deployment.setdefault('url', self.network2url.get(network, network))
            if deployer:
                deployment['deployer'] = deployer
            deployment.setdefault('contracts', {})[config_key] = {
                'address': address, 'contract': contract,
            }
            m.save_config('chain', config)
            self.config = config
        return address
    # ==================== FORK ====================

    def fork(self, owner, mods=None, network=None, label=None, gas_key='test'):
        """Fork the fleet - redeploy it (or part of it) under a new owner.

        The gas_key pays for deployment; ownership of each contract transfers
        to the forker afterwards. A fork's addresses land in
        ~/mod/chain/forks/<label>.json and its contracts wire to each other -
        the module's own deployment config is never touched.

        Args:
            owner: Key name or address that will own the forked contracts.
            mods: List of mod names to fork (default: the whole fleet).
            network: Target network (default: self.network).
            label: Fork label, used in the fork key (default: owner prefix).
            gas_key: Key name that pays for gas (default: 'test').

        Returns:
            Fork metadata plus the contracts it deployed.

        CLI:
            m chain/fork owner=alice
            m chain/fork owner=alice mods='["token","registry"]'
            m chain/fork owner=0x1234... label=myproject
        """
        network = network or self.network
        w3 = self._w3(network)

        gas_address = self.address_of(gas_key)
        gas_balance = w3.eth.get_balance(gas_address)
        m.print(f'Gas sponsor: {gas_address} ({gas_balance / 1e18:.4f} ETH)', color='cyan')
        if gas_balance == 0:
            raise ValueError(f'Gas key "{gas_key}" has no ETH for deployment')

        if Web3.is_address(owner):
            owner_address = Web3.to_checksum_address(owner)
        else:
            owner_address = self.address_of(owner)
        fork_label = label or owner_address[:10].lower()

        m.print(f'Forking contracts for {owner_address}', color='cyan')
        m.print(f'Fork label: {fork_label}', color='cyan')

        try:
            self.compile()
        except Exception as e:
            m.print(f'compile skipped - {e}', color='yellow')

        groups = self.waves(mods)
        m.print(f'Waves: {groups}', color='cyan')

        # record=False keeps the fork out of the canonical config, and makes
        # its contracts resolve each other rather than the live deployment.
        records = self._deploy_waves(groups, network, [gas_key], record=False)
        self._run_setup([n for g in groups for n in g], network, gas_key, records,
                        config_fallback=False)
        m.print(f'\n--- Transferring ownership to {owner_address} ---', color='yellow')
        self._transfer_ownership(records, owner_address, network)

        fork_key = f'{network}:fork:{fork_label}'
        forks_dir = os.path.join(os.path.expanduser('~/mod/chain'), 'forks')
        os.makedirs(forks_dir, exist_ok=True)
        fork_data = {
            'owner': owner_address,
            'gas_sponsor': gas_address,
            'network': network,
            'label': fork_label,
            'contracts': {key: {'address': r['address'], 'contract': r['contract'],
                                'mod': self.key_owner(key)}
                          for key, r in records.items()},
        }
        with open(os.path.join(forks_dir, f'{fork_label}.json'), 'w') as f:
            json.dump(fork_data, f, indent=4)

        m.print(f'\nFork complete! Owner: {owner_address}', color='green')
        m.print(f'Fork key: {fork_key}', color='cyan')
        return {**fork_data, 'fork_key': fork_key}

    def forks(self, network=None):
        """List all forks from ~/mod/chain/forks/."""
        forks_dir = os.path.join(os.path.expanduser('~/mod/chain'), 'forks')
        all_forks = {}
        if not os.path.isdir(forks_dir):
            return all_forks
        for fname in os.listdir(forks_dir):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(forks_dir, fname)
            with open(fpath, 'r') as f:
                data = json.load(f)
            key = f"{data.get('network', 'unknown')}:fork:{data.get('label', fname[:-5])}"
            all_forks[key] = data
        if network:
            return {k: v for k, v in all_forks.items() if v.get('network') == network}
        return all_forks

    def get_fork(self, label=None, owner=None, network=None):
        """Get a specific fork by label or owner address."""
        network = network or self.network
        forks = self.forks(network=network)
        for key, info in forks.items():
            if label and info.get('label') == label:
                return info
            if owner:
                owner_addr = owner
                if not Web3.is_address(owner):
                    owner_addr = self.addy(owner)
                if info.get('owner', '').lower() == owner_addr.lower():
                    return info
        return None

    # ==================== BACKWARD-COMPATIBLE INTERFACE ====================
    # All original methods preserved below

    def env_dict(self) -> Dict[str, str]:
        env_path = os.path.join(self.path, '.env')
        env_example_path = os.path.join(self.path, '.env.example')
        if not os.path.exists(env_path):
            m.put_text(env_path, m.get_text(env_example_path))

        from dotenv import load_dotenv
        load_dotenv(env_path)

        env_dict = dict()
        with open(env_path, 'r') as f:
            for line in f:
                if not line.strip() or line.startswith('#'):
                    continue
                key, value = line.strip().split('=', 1)
                env_dict[key.lower()] = value

        return env_dict

    def set_key(self, key=None):
        if key:
            self.key_name = key
            self.key = m.key(key)
        else:
            self.key_name = 'env'
            self.key = m.mod('key')(self.env_dict().get('private_key'))
        self.connect(self.key.private_key)
        return self.account.address

    def owner(self):
        """Get the contract deployer of the Market contract."""
        market = self.contracts.get('market')
        if not market:
            raise ValueError('Market contract not loaded')
        return market.functions.owner().call()

    def sync_app(self):
        """Sync contract artifacts to app."""
        app_path = m.dp('app') + '/src/contracts'
        if os.path.exists(app_path):
            os.system(f'rm -rf {app_path}')
        os.system(f'mkdir -p {app_path}')
        os.system(f'cp -r {self.contracts_path}/** {app_path}')
        network = self.network
        config = m.config('chain')
        apimap = self.abimap()
        deployment = config['deployments'][network]
        for name, info in deployment['contracts'].items():
            config['deployments'][network]['contracts'][name]['abi'] = apimap[info['contract']]
        m.save_config('chain', config)
        app_config = m.config('app')
        app_config['chain'] = config['deployments']
        m.save_config('app', app_config)
        return m.files(app_path)

    def connect(self, private_key: str):
        """Connect wallet using private key."""
        self.account = self.w3.eth.account.from_key(private_key)
        return self.account.address

    def checksum(self, address: str) -> str:
        """Convert address to checksum format."""
        return Web3.to_checksum_address(address)

    def load_all_contracts(self):
        """Load all contracts at once."""
        abimap = self.abimap()
        deployments = self.config.get('deployments', {})
        if self.network not in deployments:
            return self.contracts
        contracts = deployments[self.network].get('contracts', {})
        for name, info in contracts.items():
            address = info['address']
            try:
                abi = self.ipfs.get(abimap.get(info['contract']))
                if abi is None:
                    # Fall back to a built-in ABI for stable core contracts so
                    # the contract still loads without a compiled artifact.
                    abi = self.BUILTIN_ABIS.get(info['contract'])
                    if abi is None:
                        m.print(f'ABI not found for {name} at {info.get("abi")}', color='red')
                        continue
                self.contracts[name.lower()] = self.w3.eth.contract(
                    address=self.checksum(address),
                    abi=abi
                )
            except Exception:
                continue
        return self.contracts

    # ==================== BLOCTIME FUNCTIONS ====================

    def stake(self, amount: int, lock_blocks: int) -> Dict[str, Any]:
        """Stake tokens to earn BlocTime."""
        bloctime = self.contracts.get('bloctime')
        if not bloctime:
            raise ValueError('BlocTime contract not loaded')

        native_token = self.contracts.get('native_token')
        if native_token:
            approve_tx = native_token.functions.approve(
                bloctime.address, amount
            ).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address)
            })
            signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
            self.w3.eth.send_raw_transaction(signed.raw_transaction)

        tx = bloctime.functions.stake(amount, lock_blocks).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def unstake(self, stake_id: int) -> Dict[str, Any]:
        """Unstake specific stake position."""
        bloctime = self.contracts.get('bloctime')
        if not bloctime:
            raise ValueError('BlocTime contract not loaded')

        tx = bloctime.functions.unstake(stake_id).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def get_stake_position(self, address: Optional[str] = None, stake_id: int = 0) -> Dict[str, Any]:
        """Get stake position information."""
        bloctime = self.contracts.get('bloctime')
        if not bloctime:
            raise ValueError('BlocTime contract not loaded')

        addr = address or self.account.address
        info = bloctime.functions.getStakePosition(addr, stake_id).call()
        return {
            'amount': info[0],
            'start_block': info[1],
            'lock_blocks': info[2],
            'bloctime_balance': info[3],
            'blocks_remaining': info[4]
        }

    def get_user_stake_ids(self, address: Optional[str] = None) -> List[int]:
        """Get all stake IDs for a user."""
        bloctime = self.contracts.get('bloctime')
        if not bloctime:
            raise ValueError('BlocTime contract not loaded')
        addr = address or self.account.address
        return bloctime.functions.getUserStakeIds(addr).call()

    def bloctime_balance(self, address: Optional[str] = None) -> int:
        """Get an address's aggregate BlocTime balance (raw uint).

        Sums the aggregate position from getStakeInfo with any per-stake
        positions so callers get a single number representing BlocTime held.
        """
        bloctime = self.contracts.get('bloctime')
        if not bloctime:
            raise ValueError('BlocTime contract not loaded')
        if address and not self.is_address(address):
            address = m.key(address).address
        addr = self.checksum(address or self.account.address)
        total = 0
        try:
            info = bloctime.functions.getStakeInfo(addr).call()
            total += info[3]  # blocTimeBalance
        except Exception:
            pass
        try:
            for sid in bloctime.functions.getUserStakeIds(addr).call():
                pos = bloctime.functions.getStakePosition(addr, sid).call()
                total += pos[3]  # bloctime_balance
        except Exception:
            pass
        return total

    def is_bloctime_holder(self, address: Optional[str] = None) -> bool:
        """Return True if the address holds any BlocTime (staked balance > 0)."""
        try:
            return self.bloctime_balance(address) > 0
        except Exception:
            return False

    # ==================== MARKET FUNCTIONS ====================

    def mint(self, payment_token: str = 'usdc', usd: float = 1.0) -> Dict[str, Any]:
        """Mint MOD by paying `usd` dollars of a whitelisted stablecoin, with the
        FULL payment routed into the reward pool that is distributed to BlocTime
        holders. This is the paid path to register a module when the caller holds
        no BlocTime.

        Implementation note: the deployed Market predates a payment→treasury
        `mint()` (its `credit` keeps the principal in the Market as backing), so
        to honor "the $1 is placed in the pool" we deposit the payment directly
        into the Treasury via `fundTreasury`. The Treasury's governance token is
        BlocTime, so the deposit is claimable pro-rata by BlocTime holders.

        Approves the Treasury to pull the payment (only if needed) then funds it.
        """
        treasury = self.contracts.get('treasury')
        if not treasury:
            raise ValueError('Treasury contract not loaded')
        key = payment_token.lower()
        cfg = self.contracts_config().get(key)
        if not cfg:
            raise ValueError(f'Unknown payment token: {payment_token}')
        token = self.contracts.get(key)
        if not token:
            raise ValueError(f'{payment_token} token contract not loaded')
        token_addr = self.checksum(cfg['address'])

        # Stablecoins price at $1, so $usd ≈ usd * 10**tokenDecimals smallest units.
        decimals = token.functions.decimals().call()
        amount = int(round(usd * (10 ** decimals)))
        if amount <= 0:
            raise ValueError('Payment amount too small')

        allowance = token.functions.allowance(self.account.address, treasury.address).call()
        if allowance < amount:
            self.send_tx(key, 'approve', [treasury.address, amount])
        return self.send_tx('treasury', 'fundTreasury', [token_addr, amount])

    def credit(self, stable_amount: str, payment_token: int = 'usdt') -> Dict[str, Any]:
        """Buy stable tokens with whitelisted payment token."""
        market = self.contracts.get('market')
        if not market:
            raise ValueError('Market contract not loaded')

        payment_token = self.contracts_config().get(payment_token)['address']
        print('Using payment token at address:', payment_token)
        tokengate = self.contracts.get('tokengate')

        price_info = tokengate.functions.getTokenPrice(payment_token).call()
        token_price = price_info[0]
        token_decimals = price_info[1]
        payment_amount = ((stable_amount * (10 ** token_decimals)) // token_price) ** token_decimals

        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(payment_token),
            abi=[{"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"}]
        )
        approve_tx = token.functions.approve(
            market.address, payment_amount
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f'Waiting for approval tx to be mined -> {tx_hash.hex()}')
        print(self.w3.eth.wait_for_transaction_receipt(tx_hash))

        tx = market.functions.credit(payment_token, stable_amount).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()

    # ==================== REGISTRY FUNCTIONS ====================

    def reg(self, name: str, data: str = None) -> Dict[str, Any]:
        """Register a new mod."""
        mod = m.fn('api/mod')(name)
        data = data or mod.get('cid', '')
        name = mod['name']
        if self.mod_exists(name):
            return self.update(name, data)
        return self.send_tx('registry', 'registerMod', [name, data])

    def reg_direct(self, name: str, data: str) -> Dict[str, Any]:
        """Register/update a mod by name + data WITHOUT consulting the api module.

        The on-chain Registry requires non-empty data, so callers must pass the
        module's CID/manifest reference. Used by the gated [`register`] flow so
        any catalog module can be registered straight from its name + schema CID.
        """
        if not name or not data:
            raise ValueError('register requires both name and data (e.g. the schema CID)')
        if self.mod_exists(name):
            return self.send_tx('registry', 'updateMod', [self.name2id(name), data])
        return self.send_tx('registry', 'registerMod', [name, data])

    def register(self, name: str, data: str = None, key=None,
                 pay: bool = False, payment_token: str = 'usdc') -> Dict[str, Any]:
        """Gated registration: register a module on-chain if the signer holds
        BlocTime; otherwise require a $1 MOD mint (which funds the weekly pool).

        - Holds BlocTime  → registers for free.
        - No BlocTime, pay=False → returns {status:'payment_required', ...} so the
          caller can confirm the $1 charge.
        - No BlocTime, pay=True  → mints $1 of MOD (payment → pool) then registers.

        `data` is the registry payload (the module's CID/manifest); required.
        `key` optionally switches the signing identity for this call.
        """
        if key:
            self.set_key(key)
        address = self.account.address
        bloctime = self.bloctime_balance(address)
        has_bloctime = bloctime > 0

        if not has_bloctime and not pay:
            return {
                'status': 'payment_required',
                'address': address,
                'bloctime': bloctime,
                'price_usd': 1.0,
                'payment_token': payment_token,
                'reason': 'No BlocTime held. Mint $1 of MOD to register; the $1 '
                          'funds the weekly pool paid out to BlocTime holders.',
            }

        mint_receipt = None
        if not has_bloctime:
            mint_receipt = self.mint(payment_token, 1.0)

        reg_receipt = self.reg_direct(name, data)
        return {
            'status': 'registered',
            'name': name,
            'address': address,
            'bloctime': self.bloctime_balance(address),
            'paid': not has_bloctime,
            'mint': mint_receipt,
            'register': reg_receipt,
        }

    # ==================== POOL (weekly distribution) ====================

    def pool(self) -> Dict[str, Any]:
        """Current state of the reward pool: the Treasury balance (funded by $1
        MOD mints) that is distributed to BlocTime holders, plus the governance
        token and total BlocTime outstanding. Stablecoin balances are summed as
        the pool's USD value (stables price at $1)."""
        treasury = self.contracts.get('treasury')
        bloctime = self.contracts.get('bloctime')
        if not treasury:
            raise ValueError('Treasury contract not loaded')
        gov = treasury.functions.governanceToken().call()
        owner_pct = treasury.functions.ownerPercentage().call()
        try:
            token_addrs = treasury.functions.getTreasuryTokens().call()
        except Exception:
            token_addrs = self.tokens()
        tokens = []
        pool_usd = 0.0
        for addr in token_addrs:
            tok = self._erc20(addr)
            try:
                bal = tok.functions.balanceOf(treasury.address).call()
                dec = tok.functions.decimals().call()
                sym = tok.functions.symbol().call()
            except Exception:
                continue
            human = bal / (10 ** dec)
            pool_usd += human  # stables ≈ $1
            tokens.append({'address': addr, 'symbol': sym, 'balance': bal,
                           'decimals': dec, 'human': human})
        total_bloctime = 0
        if bloctime:
            try:
                total_bloctime = bloctime.functions.totalSupply().call()
            except Exception:
                pass
        return {
            'governance_token': gov,
            'owner_percentage_bps': owner_pct,
            'distributable_bps': 10000 - owner_pct,
            'total_bloctime': total_bloctime,
            'pool_usd': pool_usd,
            'tokens': tokens,
        }

    def pool_claimable(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Per-token amount the given address (a BlocTime holder) can claim now
        from the pool, by its proportional BlocTime share."""
        treasury = self.contracts.get('treasury')
        if not treasury:
            raise ValueError('Treasury contract not loaded')
        if address and not self.is_address(address):
            address = m.key(address).address
        addr = self.checksum(address or self.account.address)
        try:
            token_addrs = treasury.functions.getTreasuryTokens().call()
        except Exception:
            token_addrs = self.tokens()
        out = []
        total_usd = 0.0
        for taddr in token_addrs:
            try:
                amount = treasury.functions.getClaimableAmount(addr, taddr).call()
            except Exception:
                continue
            tok = self._erc20(taddr)
            try:
                dec = tok.functions.decimals().call()
                sym = tok.functions.symbol().call()
            except Exception:
                dec, sym = 18, '?'
            human = amount / (10 ** dec)
            total_usd += human
            out.append({'address': taddr, 'symbol': sym, 'amount': amount,
                        'decimals': dec, 'human': human})
        return {'address': addr, 'bloctime': self.bloctime_balance(addr),
                'claimable_usd': total_usd, 'tokens': out}

    def pool_claim(self, token: str = None, key=None) -> Dict[str, Any]:
        """Claim the caller's share of the pool for one token (or every token
        with a positive claimable balance)."""
        if key:
            self.set_key(key)
        treasury = self.contracts.get('treasury')
        if not treasury:
            raise ValueError('Treasury contract not loaded')
        addr = self.account.address
        # Resolve a token symbol/name to its address if needed.
        targets = []
        if token:
            cfg = self.contracts_config().get(token.lower())
            targets = [self.checksum(cfg['address'])] if cfg else [self.checksum(token)]
        else:
            claim = self.pool_claimable(addr)
            targets = [t['address'] for t in claim['tokens'] if t['amount'] > 0]
        receipts = []
        for taddr in targets:
            try:
                receipts.append({'token': taddr,
                                 'receipt': self.send_tx('treasury', 'withdrawToken', [taddr])})
            except Exception as e:
                receipts.append({'token': taddr, 'error': str(e)})
        return {'address': addr, 'claims': receipts}

    def pool_snapshot(self) -> Dict[str, Any]:
        """Record a point-in-time snapshot of the pool for the weekly epoch log
        (off-chain, under ~/.mod/chain). Distribution itself is pull-based —
        holders claim their share — so this captures the pool size + total
        BlocTime at the moment the weekly keeper runs, for display/audit."""
        snap = self.pool()
        block = self.w3.eth.block_number
        ts = self.w3.eth.get_block(block).timestamp
        epoch = {
            'block': block,
            'timestamp': ts,
            'network': self.network,
            'pool_usd': snap['pool_usd'],
            'total_bloctime': snap['total_bloctime'],
            'governance_token': snap['governance_token'],
            'tokens': snap['tokens'],
        }
        epochs = self.pool_epochs(limit=10_000)
        epochs.append(epoch)
        epochs = epochs[-200:]  # cap history
        path = self._pool_epochs_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(epochs, f)
        return epoch

    def _pool_epochs_path(self) -> str:
        return os.path.expanduser('~/.mod/chain/pool_epochs.json')

    def pool_epochs(self, limit: int = 12) -> List[Dict[str, Any]]:
        """Return the most recent weekly pool snapshots (newest last)."""
        path = self._pool_epochs_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                epochs = json.load(f)
        except Exception:
            return []
        if not isinstance(epochs, list):
            return []
        return epochs[-int(limit):]

    # ==================== DEFI / YIELD AGGREGATOR ====================

    def _yield_vault(self):
        vault = self.contracts.get('yieldvault')
        if not vault:
            raise ValueError('YieldVault contract not loaded')
        return vault

    def _strategy_asset(self, strategy_id):
        """Resolve (asset address, asset decimals) for a strategy id."""
        s = self._yield_vault().functions.strategies(int(strategy_id)).call()
        asset = self.checksum(s[1])
        try:
            dec = self._erc20(asset).functions.decimals().call()
        except Exception:
            dec = 18
        return asset, dec

    def yield_strategies(self) -> List[Dict[str, Any]]:
        """List registered yield strategies — the modular lowfi yield options."""
        vault = self._yield_vault()
        count = vault.functions.strategyCount().call()
        out = []
        for i in range(count):
            s = vault.functions.strategies(i).call()
            asset = self.checksum(s[1])
            try:
                tok = self._erc20(asset)
                dec = tok.functions.decimals().call()
                sym = tok.functions.symbol().call()
            except Exception:
                dec, sym = 18, '?'
            try:
                profit = vault.functions.pendingProfit(i).call()
            except Exception:
                profit = 0
            out.append({
                'id': i,
                'adapter': self.checksum(s[0]),
                'asset': asset,
                'asset_symbol': sym,
                'asset_decimals': dec,
                'name': s[2],
                'enabled': s[3],
                'tvl': s[4] / (10 ** dec),
                'pending_profit': profit / (10 ** dec),
            })
        return out

    def yield_deposit(self, strategy_id: int, amount: float) -> Dict[str, Any]:
        """Deposit `amount` (human units) of a strategy's asset into the vault."""
        vault = self._yield_vault()
        asset, dec = self._strategy_asset(strategy_id)
        raw = int(round(float(amount) * (10 ** dec)))
        if raw <= 0:
            raise ValueError('Amount too small')
        token = self._erc20(asset)
        allowance = token.functions.allowance(self.account.address, vault.address).call()
        if allowance < raw:
            approve_tx = token.functions.approve(vault.address, raw).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address, 'pending'),
                'gas': 100000,
                **self._gas_fees(),
            })
            signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
            h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            self.w3.eth.wait_for_transaction_receipt(h)
        return self.send_tx('yieldvault', 'deposit', [int(strategy_id), raw])

    def yield_withdraw(self, strategy_id: int, shares: float) -> Dict[str, Any]:
        """Withdraw `shares` (human units of principal) from a strategy."""
        _, dec = self._strategy_asset(strategy_id)
        raw = int(round(float(shares) * (10 ** dec)))
        if raw <= 0:
            raise ValueError('Amount too small')
        return self.send_tx('yieldvault', 'withdraw', [int(strategy_id), raw])

    def yield_harvest(self, strategy_id: int) -> Dict[str, Any]:
        """Realize a strategy's yield → route through Market → mint native tokens
        to depositors (distributed pro-rata)."""
        return self.send_tx('yieldvault', 'harvest', [int(strategy_id)])

    def yield_claim(self, strategy_id: int) -> Dict[str, Any]:
        """Claim accrued native reward tokens for a strategy."""
        return self.send_tx('yieldvault', 'claim', [int(strategy_id)])

    def yield_position(self, strategy_id: int, address: Optional[str] = None) -> Dict[str, Any]:
        """A user's position: principal shares + claimable native reward."""
        vault = self._yield_vault()
        if address and not self.is_address(address):
            address = m.key(address).address
        addr = self.checksum(address or self.account.address)
        _, dec = self._strategy_asset(strategy_id)
        try:
            native_dec = self.decimals('market')
        except Exception:
            native_dec = 8
        shares = vault.functions.userShares(int(strategy_id), addr).call()
        pending = vault.functions.pendingReward(int(strategy_id), addr).call()
        return {
            'strategy_id': int(strategy_id),
            'address': addr,
            'shares': shares / (10 ** dec),
            'pending_reward': pending / (10 ** native_dec),
        }

    def _erc20(self, address: str):
        """A bare ERC20 handle (balanceOf/decimals/symbol/totalSupply) for any
        token address, using the built-in Token ABI."""
        return self.w3.eth.contract(address=self.checksum(address),
                                     abi=self.BUILTIN_ABIS['Token'])

    def send_tx(self, module, function, args: list) -> Dict[str, Any]:
        """Send a transaction to a contract function.

        Uses the *pending* nonce and explicit EIP-1559 gas (priority fee +
        headroom over base fee) so that sequential calls in a single flow
        (e.g. approve → fundTreasury → registerMod) don't collide and aren't
        rejected as "replacement transaction underpriced" on busy public RPCs.
        """
        contract = self.contracts.get(module)
        if not contract:
            raise ValueError(f'{module} contract not loaded')

        params = {
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address, 'pending'),
            # Explicit gas limit so build_transaction skips eth_estimateGas, which
            # is unreliable on load-balanced public RPCs (a lagging backend can
            # see a freshly-funded account as empty → "gas required exceeds
            # allowance (0)"). Our calls (approve/fundTreasury/registerMod/…) are
            # all well under this ceiling; unused gas isn't charged under 1559.
            'gas': 500000,
        }
        params.update(self._gas_fees())
        tx = getattr(contract.functions, function)(*args).build_transaction(params)
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def _gas_fees(self, w3=None) -> Dict[str, int]:
        """EIP-1559 fee fields: 2 gwei priority + 2× base-fee headroom. Falls
        back to an empty dict (node defaults) if the chain isn't 1559-capable."""
        w3 = w3 or self.w3
        try:
            base = w3.eth.get_block('latest').get('baseFeePerGas')
            if base is None:
                return {}
            priority = w3.to_wei(2, 'gwei')
            return {'maxPriorityFeePerGas': priority, 'maxFeePerGas': base * 2 + priority}
        except Exception:
            return {}

    # ==================== ADMIN / OWNER FUNCTIONS ====================
    #
    # Generic owner-operation surface used by the Owner Console (/admin in the
    # app). Supports two modes for every owner-only call:
    #   - admin_send:   sign & broadcast with the active deployer key (direct).
    #   - admin_encode: return {to, data, value} calldata WITHOUT touching the
    #                   chain, so the call can be wrapped into a Safe multisig
    #                   batch (Safe Transaction Builder JSON) and executed by the
    #                   multisig once it owns the contracts.

    ZERO_ADDRESS = '0x0000000000000000000000000000000000000000'

    def _abi_inputs(self, contract, function: str):
        """Return the ABI input descriptors for a function, or None."""
        for item in getattr(contract, 'abi', []) or []:
            if item.get('type') == 'function' and item.get('name') == function:
                return item.get('inputs', [])
        return None

    def _coerce(self, value, inp: dict):
        """Coerce a JSON-supplied value into the Solidity type from the ABI.

        Handles arrays, structs (tuple/tuple[]), uint/int (accepts strings to
        survive JS number-precision limits), address checksumming, bool, bytes.
        """
        t = inp.get('type', '')
        if t.endswith('[]'):
            base = {**inp, 'type': t[:-2]}
            return [self._coerce(v, base) for v in (value or [])]
        if t.startswith('tuple'):
            comps = inp.get('components', []) or []
            if isinstance(value, dict):
                return tuple(self._coerce(value.get(c['name']), c) for c in comps)
            return tuple(self._coerce(v, c) for v, c in zip(value, comps))
        if t.startswith(('uint', 'int')):
            return int(value)
        if t == 'address':
            return Web3.to_checksum_address(value)
        if t == 'bool':
            return value if isinstance(value, bool) else str(value).lower() in ('true', '1', 'yes')
        if t.startswith('bytes'):
            if isinstance(value, str) and value.startswith('0x'):
                return bytes.fromhex(value[2:])
            return value
        return value

    def _coerce_args(self, contract, function: str, args: list):
        inputs = self._abi_inputs(contract, function)
        args = list(args or [])
        if inputs is None or len(inputs) != len(args):
            return args
        return [self._coerce(a, i) for i, a in zip(inputs, args)]

    def admin_owner(self, module: str):
        """Read the current owner() of a module's contract (None if absent)."""
        contract = self.contracts.get(module)
        if not contract:
            return None
        try:
            return contract.functions.owner().call()
        except Exception:
            return None

    def admin_encode(self, module: str, function: str, args: list = None,
                     value=0) -> Dict[str, Any]:
        """Build calldata for an owner call without broadcasting (Safe export)."""
        contract = self.contracts.get(module)
        if not contract:
            raise ValueError(f'{module} contract not loaded')
        coerced = self._coerce_args(contract, function, args)
        fn = getattr(contract.functions, function)(*coerced)
        try:
            data = fn._encode_transaction_data()
        except Exception:
            try:
                data = contract.encode_abi(abi_element_identifier=function, args=coerced)
            except TypeError:
                data = contract.encodeABI(fn_name=function, args=coerced)
        return {'to': contract.address, 'data': data, 'value': str(int(value))}

    def admin_send(self, module: str, function: str, args: list = None,
                   value=0) -> Dict[str, Any]:
        """Sign & broadcast an owner call with the active deployer key."""
        contract = self.contracts.get(module)
        if not contract:
            raise ValueError(f'{module} contract not loaded')
        coerced = self._coerce_args(contract, function, args)
        txp = {
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address),
        }
        if int(value or 0):
            txp['value'] = int(value)
        tx = getattr(contract.functions, function)(*coerced).build_transaction(txp)
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    # ==================== CONTROL PANEL (deploy scripts / verify) ====================
    #
    # Hardhat-script-based deploys (e.g. the DeFi vault, which isn't part of the
    # Python/web3 FLEET pipeline) and Basescan/Etherscan source verification,
    # both invoked via `npx hardhat ...` subprocesses scoped to this module's directory.

    HH_NETWORK = {'testnet': 'base_sepolia', 'mainnet': 'base', 'ganache': 'ganache', 'localhost': 'hardhat'}

    def _hh_network(self, network: str) -> str:
        """Map a config network key to its Hardhat network name."""
        return self.HH_NETWORK.get(network, network)

    def verify_contract(self, network: str, name: str, args: list = None) -> Dict[str, Any]:
        """Verify a deployed contract's source on Basescan/Etherscan via hardhat-verify."""
        deployment = self.config.get('deployments', {}).get(network, {})
        contract = deployment.get('contracts', {}).get(name)
        if not contract or not contract.get('address'):
            raise ValueError(f"'{name}' is not deployed on '{network}'")
        address = contract['address']
        cmd = ['npx', 'hardhat', 'verify', '--network', self._hh_network(network),
               address, *[str(a) for a in (args or [])]]
        proc = subprocess.run(cmd, cwd=m.dp('chain'), capture_output=True, text=True, timeout=180)
        output = ((proc.stdout or '') + (proc.stderr or '')).strip()
        if 'Already Verified' in output:
            status = 'already_verified'
        elif proc.returncode == 0:
            status = 'verified'
        else:
            status = 'failed'
        return {'status': status, 'output': output, 'address': address}

    def deploy_script(self, network: str, script: str) -> Dict[str, Any]:
        """Run a whitelisted Hardhat deploy script (e.g. deploy-defi.js) against a network."""
        base = m.dp('chain')
        scripts_dir = os.path.join(base, 'scripts')
        available = {f for f in os.listdir(scripts_dir) if f.endswith('.js')} if os.path.isdir(scripts_dir) else set()
        if script not in available:
            raise ValueError(f"Unknown deploy script '{script}'")
        env = {**os.environ, 'CONFIG_NET': network}
        cmd = ['npx', 'hardhat', 'run', os.path.join('scripts', script), '--network', self._hh_network(network)]
        proc = subprocess.run(cmd, cwd=base, capture_output=True, text=True, timeout=600, env=env)
        output = ((proc.stdout or '') + (proc.stderr or '')).strip()
        if proc.returncode != 0:
            raise RuntimeError(output or f'deploy script failed (exit {proc.returncode})')
        self.config = m.config('chain')  # the script rewrites config.json directly — reload it
        return {'status': 'ok', 'output': output}

    def is_ecdsa(self, address: str) -> bool:
        """Check if address is an ECDSA address."""
        if not Web3.is_address(address):
            return False
        code = self.w3.eth.get_code(Web3.to_checksum_address(address))
        return code == b''

    def addy(self, key: str = None) -> str:
        if key is None:
            return self.account.address
        if self.is_ecdsa(key):
            return Web3.to_checksum_address(key)
        else:
            return m.key(key).address

    def abi(self, name: str = 'usdc', search=None) -> list:
        """Get contract ABI by name."""
        contract = self.ipfs.get(self.contracts_config().get(name.lower())['abi'])
        if search:
            contract = [item for item in contract if search in item.get('name', '')]
        return contract

    def contracts_config(self) -> Dict[str, Any]:
        contract_map = self.config['deployments'][self.network]['contracts']
        contract_map = {k.lower(): v for k, v in contract_map.items()}
        return contract_map

    def is_address(self, address: str) -> bool:
        """Check if string is a valid Ethereum address."""
        return Web3.is_address(address)

    def balance(self, address: str = None, token='market') -> int:
        """Get token balance."""
        if not self.is_address(address):
            address = m.key(address).address

        address = Web3.to_checksum_address(address)
        abimap = self.abimap()

        if token == 'ETH':
            addr = address or self.account.address
            print(f'Getting ETH balance for {addr}')
            balance = self.w3.eth.get_balance(addr)
        else:
            cfg = self.contracts_config()[token.lower()]
            token_contract = self.w3.eth.contract(
                address=cfg['address'],
                abi=self.ipfs.get(abimap.get(cfg['contract']))
            )
            print(f'Getting {token} balance for {address} at {cfg["address"]}')
            balance = token_contract.functions.balanceOf(address).call()

        return self.format_balance(balance, token=token.upper())

    def balances(self, address: str, tokens: list = None, timeout=30) -> dict:
        """Get balances for a single address across multiple tokens."""
        if tokens is None:
            tokens = ['ETH', 'USDC', 'USDT', 'MARKET', 'NativeToken']
        future2token = {}
        balances = {}
        for tok in tokens:
            future = m.submit(self.balance, dict(address=address, token=tok), timeout=timeout)
            future2token[future] = tok
        for future in m.as_completed(future2token.keys(), timeout=timeout):
            tok = future2token[future]
            try:
                bal = future.result()
                balances[tok] = bal
            except Exception as e:
                m.print(f'Error getting balance for {tok}: {e}', color='red')
                balances[tok] = None

        return balances

    def scan_token_holders(self, token: str = 'market', from_block: int = 0,
                           to_block: int = None, weeks: int = 2,
                           block_time: int = 2, batch_size: int = 10000) -> dict:
        """Scan blockchain for all token holders by analyzing Transfer events."""
        token_lower = token.lower()
        cfg = self.contracts_config().get(token_lower)
        if not cfg:
            raise ValueError(f'Token {token} not found in config')

        token_address = cfg['address']
        token_abi = self.ipfs.get(cfg['abi'])
        token_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=token_abi
        )

        if to_block is None:
            to_block = self.w3.eth.block_number

        if from_block == 0:
            seconds_in_period = weeks * 7 * 24 * 60 * 60
            blocks_in_period = seconds_in_period // block_time
            from_block = max(0, to_block - blocks_in_period)

        total_blocks = to_block - from_block
        m.print(f'Scanning {token.upper()} transfers from block {from_block} to {to_block} ({total_blocks:,} blocks)', color='cyan')

        all_events = []
        current_block = from_block

        while current_block <= to_block:
            batch_end = min(current_block + batch_size - 1, to_block)
            try:
                m.print(f'Fetching events from block {current_block:,} to {batch_end:,}...', color='yellow')
                transfer_filter = token_contract.events.Transfer.create_filter(
                    from_block=current_block,
                    to_block=batch_end
                )
                events = transfer_filter.get_all_entries()
                all_events.extend(events)
                m.print(f'  Found {len(events)} events in this batch', color='green')
            except Exception as e:
                m.print(f'Error fetching events for blocks {current_block}-{batch_end}: {e}', color='red')
                if batch_size > 1000:
                    m.print(f'Retrying with smaller batch size...', color='yellow')
                    return self.scan_token_holders(token, from_block, to_block, weeks, block_time, batch_size=batch_size // 2)
                raise
            current_block = batch_end + 1

        m.print(f'Total events found: {len(all_events):,}', color='green')

        balances = {}
        zero_address = '0x0000000000000000000000000000000000000000'

        for event in all_events:
            from_addr = event['args']['from']
            to_addr = event['args']['to']
            value = event['args']['value']

            if from_addr != zero_address:
                from_addr_lower = from_addr.lower()
                if from_addr_lower not in balances:
                    balances[from_addr_lower] = 0
                balances[from_addr_lower] -= value

            if to_addr != zero_address:
                to_addr_lower = to_addr.lower()
                if to_addr_lower not in balances:
                    balances[to_addr_lower] = 0
                balances[to_addr_lower] += value

        m.print(f'Fetching current balances for {len(balances):,} addresses...', color='yellow')
        final_balances = {}

        for i, addr in enumerate(balances.keys()):
            try:
                if (i + 1) % 100 == 0:
                    m.print(f'  Progress: {i + 1}/{len(balances)} addresses checked', color='cyan')
                current_balance = token_contract.functions.balanceOf(Web3.to_checksum_address(addr)).call()
                if current_balance > 0:
                    final_balances[addr] = self.format_balance(current_balance, token=token.upper())
            except Exception as e:
                m.print(f'Error getting balance for {addr}: {e}', color='red')
                continue

        m.print(f'Found {len(final_balances):,} addresses with non-zero balances', color='green')
        return final_balances

    def credits(self, address: str = None) -> int:
        """Get stable token balance."""
        return self.balance(token='MARKET', address=address)

    bal = balance

    def format_balance(self, balance: int, token='ETH') -> float:
        """Format balance from wei to human-readable."""
        decimals = self.decimals(token)
        if token != 'ETH':
            chain_config = self.contracts_config()
            token_key = token.lower()
            if token_key in chain_config:
                token_address = chain_config[token_key]['address']
                token_abi = self.name2abi(token_key)
                token_contract = self.w3.eth.contract(
                    address=Web3.to_checksum_address(token_address),
                    abi=token_abi
                )
                decimals = token_contract.functions.decimals().call()
                print(f'Token {token} has {decimals} decimals')
        return balance / (10 ** decimals)

    def regall(self, mods: List[Dict[str, str]] = None) -> List[Dict[str, Any]]:
        if mods is None:
            mods = m.fn('api/mods')()
        receipts = []
        for mod in mods:
            try:
                receipt = self.reg(mod['name'], mod['cid'])
            except Exception as e:
                print(f'Error registering mod {mod["name"]}: {e}')
                continue
            receipts.append(receipt)
        return receipts

    def name2id(self, name: str = None) -> Union[int, Dict[str, int]]:
        """Get mod ID from name."""
        mods = self.mods()
        name2id = {mod['name']: mod['id'] for mod in mods}
        return name2id.get(name, name) if name else name2id

    def rmall(self) -> List[Dict[str, Any]]:
        """Delete all mods."""
        mods = self.mods()
        receipts = []
        for mod in mods:
            try:
                receipt = self.rm(mod['name'])
            except Exception as e:
                print(f'Error removing mod {mod["name"]}: {e}')
                continue
            receipts.append(receipt)
        return receipts

    def rm(self, name: int) -> Dict[str, Any]:
        """Delete mod by name."""
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        mod_id = self.name2id(name)
        return self.send_tx('registry', 'removeMod', [mod_id])

    def update(self, name: int, data: str = None) -> Dict[str, Any]:
        """Update mod data."""
        if data is None:
            mod = m.fn('api/mod')(name)
            data = mod.get('cid', '')
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        mod_id = self.name2id(name)
        return self.send_tx('registry', 'updateMod', [mod_id, data])

    def get_mod(self, mod_id: int, block: int = None) -> Dict[str, Any]:
        """Get mod information.

        Args:
            mod_id: Mod ID in registry.
            block: Block number to query at (default: latest).
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        call_kwargs = {'block_identifier': block} if block else {}
        info = registry.functions.getMod(mod_id).call(**call_kwargs)
        return {
            'owner': info[0],
            'name': info[1],
            'data': info[2]
        }

    def name2abi(self, name: str) -> list:
        """Get ABI from contract name."""
        contract_map = self.contracts_config()
        contract_info = contract_map.get(name.lower())
        contract_name = contract_info['contract']
        abimap = self.abimap()
        abimap = {k.lower(): v for k, v in abimap.items()}
        return self.ipfs.get(abimap.get(contract_name.lower()))

    # ==================== TOKENGATE FUNCTIONS ====================

    def decimals(self, token='market') -> int:
        """Get token decimals."""
        if token == 'ETH':
            return 18
        cfg = self.contracts_config().get(token.lower())
        if not cfg:
            raise ValueError(f'Token {token} not found in config')
        token_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(cfg['address']),
            abi=self.name2abi(token.lower())
        )
        return token_contract.functions.decimals().call()

    def debit(self, client, provider, amount, deadline=0, signature=None) -> Dict[str, Any]:
        """Debit stable tokens from client to provider."""
        if amount == 0:
            return '0x0'
        market = self.contracts.get('market')
        if not market:
            raise ValueError('Market contract not loaded')
        amount = int(amount * 10 ** self.decimals('market'))
        client = Web3.to_checksum_address(client)
        provider = Web3.to_checksum_address(provider)
        if signature is None:
            signature = b''
        tx = market.functions.debit(client, provider, amount, deadline, signature).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return '0x' + tx_hash.hex()

    def transfer(self, to: str, amount: int, token='eth') -> Dict[str, Any]:
        """Transfer tokens to another address."""
        token_key = token.lower()

        if token_key in self.contracts:
            market = self.contracts.get(token)
            if not market:
                raise ValueError('Market contract not loaded')
            chain_config = self.contracts_config()
            token_key = token.lower()
            token_address = chain_config[token_key]['address']
            token_abi = self.name2abi(token_key)
            token_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=token_abi
            )
            decimals = token_contract.functions.decimals().call()
            amount = int(amount * 10 ** decimals)
            to = Web3.to_checksum_address(to)
            tx = market.functions.transfer(to, amount).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address)
            })
        else:
            amount = int(amount * 10 ** 18)
            to = Web3.to_checksum_address(to)
            tx = {
                'to': to,
                'value': amount,
                'gas': 21000,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gasPrice': self.w3.eth.gas_price
            }
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def treasury(self) -> str:
        """Get treasury address."""
        market = self.contracts.get('market')
        if not market:
            raise ValueError('Market contract not loaded')
        return market.functions.treasury().call()

    def totalTreasuryFeesAccrued(self) -> int:
        """Get total market treasury fees accrued."""
        market = self.contracts.get('market')
        if not market:
            raise ValueError('Market contract not loaded')
        value = market.functions.totalTreasuryFeesAccrued().call()
        return self.format_balance(value, token='MARKET')

    def getUnclaimedTreasuryFeesUSD(self) -> int:
        """Get total market treasury fees."""
        market = self.contracts.get('market')
        if not market:
            raise ValueError('Market contract not loaded')
        return market.functions.getUnclaimedTreasuryFeesUSD().call()

    def tokens(self) -> List[str]:
        """Get all whitelisted tokens."""
        tokengate = self.contracts.get('tokengate')
        if not tokengate:
            raise ValueError('TokenGate contract not loaded')
        return tokengate.functions.getWhitelistedTokens().call()

    def whitelist_token(self, token_address: str) -> Dict[str, Any]:
        """Whitelist a token (owner only)."""
        tokengate = self.contracts.get('tokengate')
        if not tokengate:
            raise ValueError('TokenGate contract not loaded')

        tx = tokengate.functions.whitelistToken(token_address).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def is_token_whitelisted(self, token_address: str) -> bool:
        """Check if token is whitelisted."""
        tokengate = self.contracts.get('tokengate')
        if not tokengate:
            raise ValueError('TokenGate contract not loaded')
        return tokengate.functions.isTokenWhitelisted(token_address).call()

    def get_token_price(self, token_address: str) -> Dict[str, Any]:
        """Get token price from oracle."""
        tokengate = self.contracts.get('tokengate')
        if not tokengate:
            raise ValueError('TokenGate contract not loaded')
        info = tokengate.functions.getTokenPrice(token_address).call()
        return {
            'price': info[0],
            'decimals': info[1],
            'timestamp': info[2]
        }

    # ==================== TREASURY FUNCTIONS ====================

    def fund_treasury(self, token_address: str, amount: int) -> Dict[str, Any]:
        """Fund treasury with tokens."""
        treasury = self.contracts.get('treasury')
        if not treasury:
            raise ValueError('Treasury contract not loaded')

        token = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=[{"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"}]
        )
        approve_tx = token.functions.approve(
            treasury.address, amount
        ).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
        self.w3.eth.send_raw_transaction(signed.raw_transaction)

        tx = treasury.functions.fundTreasury(token_address, amount).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def withdraw_from_treasury(self, token_address: str) -> Dict[str, Any]:
        """Withdraw proportional share from treasury."""
        treasury = self.contracts.get('treasury')
        if not treasury:
            raise ValueError('Treasury contract not loaded')

        tx = treasury.functions.withdrawToken(token_address).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def get_claimable_amount(self, holder: str, token: str) -> int:
        """Get claimable amount for holder."""
        treasury = self.contracts.get('treasury')
        if not treasury:
            raise ValueError('Treasury contract not loaded')
        return treasury.functions.getClaimableAmount(holder, token).call()

    def getMods(self):
        """Get all mods from registry."""
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        mod_count = registry.functions.mods(1).call()
        return mod_count

    def modIds(self, address=None, block: int = None):
        """Get all mods for a user from registry.

        Args:
            address: User address (default: connected account).
            block: Block number to query at (default: latest).
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        addr = address or self.account.address
        call_kwargs = {'block_identifier': block} if block else {}
        mod_ids = registry.functions.getUserMods(addr).call(**call_kwargs)
        return mod_ids

    def mod_exists(self, mod: str) -> bool:
        """Check if mod exists in registry."""
        mod_id = self.name2id(mod)
        try:
            _ = self.get_mod(mod_id)
            return True
        except Exception:
            return False

    def mods(self, address=None, keys=['id', 'data', 'name'], block: int = None):
        """Get all mods for a user from registry.

        Args:
            address: User address (default: connected account).
            keys: Fields to include in each mod dict.
            block: Block number to query at (default: latest).
        """
        mod_ids = self.modIds(address=address, block=block)
        mods = []
        for mod_id in mod_ids:
            _mod = self.get_mod(mod_id, block=block)
            _mod['id'] = mod_id
            mod_info = {k: _mod[k] for k in keys}
            mods.append(mod_info)
        return mods

    def mymods(self):
        """Get all mods for the connected user from registry."""
        return self.mods(address=self.account.address)

    def allmods(self, keys=['id', 'owner', 'name', 'data'], block: int = None):
        """Enumerate every mod in the Registry, across all owners.

        Walks ids 1..nextModId-1 and reads each one, skipping slots that have
        been removed (owner == zero address). This is the global view the
        catalog needs to know which modules are registered on-chain, regardless
        of who registered them.

        Args:
            keys: Fields to include in each mod dict.
            block: Block number to query at (default: latest).
        """
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        call_kwargs = {'block_identifier': block} if block else {}
        next_id = registry.functions.nextModId().call(**call_kwargs)
        zero = '0x0000000000000000000000000000000000000000'
        mods = []
        for mod_id in range(1, next_id):
            _mod = self.get_mod(mod_id, block=block)
            if not _mod.get('owner') or _mod['owner'].lower() == zero:
                continue  # removed slot
            _mod['id'] = mod_id
            mods.append({k: _mod[k] for k in keys})
        return mods

    # ==================== BLOCK INFO ====================

    def block(self, number=None):
        """Get block number or block info.

        Args:
            number: Block number to fetch. If None, returns latest block number.

        Returns:
            int (latest block number) or dict (block info) if number given.
        """
        if number is None:
            return self.w3.eth.block_number
        b = self.w3.eth.get_block(int(number))
        return {
            'number': b.number,
            'timestamp': b.timestamp,
            'hash': b.hash.hex(),
            'parentHash': b.parentHash.hex(),
            'gasUsed': b.gasUsed,
            'gasLimit': b.gasLimit,
            'transactions': len(b.transactions),
        }

    def timestamp(self, number=None):
        """Get the timestamp of a block.

        Args:
            number: Block number. If None, uses latest block.

        Returns:
            int: Unix timestamp of the block.
        """
        if number is None:
            number = self.w3.eth.block_number
        return self.w3.eth.get_block(int(number)).timestamp

    def utc(self, number=None):
        """Get UTC datetime for a block.

        Args:
            number: Block number or unix timestamp. If None, uses latest block.
                    Values > 1e9 are treated as unix timestamps directly.

        Returns:
            str: UTC datetime string (e.g. '2025-01-15 12:30:45 UTC').
        """
        from datetime import datetime, timezone
        if number is not None and int(number) > 1_000_000_000:
            ts = int(number)
        else:
            ts = self.timestamp(number)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')

    # ==================== UTILITY FUNCTIONS ====================

    def forward(self, x=1, y=2):
        """Default action."""
        return x + y

    def test(self, network=None):
        """Run the contract test suite (hardhat)."""
        return os.system(f'cd {self.path} && npx hardhat test')

    def compile(self):
        """Compile src/contracts into artifacts/ via hardhat."""
        proc = subprocess.run(['npx', 'hardhat', 'compile'], cwd=self.path,
                              capture_output=True, text=True, timeout=600)
        output = ((proc.stdout or '') + (proc.stderr or '')).strip()
        if proc.returncode != 0:
            raise RuntimeError(output or f'compile failed (exit {proc.returncode})')
        m.print(output.splitlines()[-1] if output else 'compiled', color='green')
        return {'status': 'ok', 'output': output}

    def abifiles(self, search=None):
        """Get ABI files."""
        files = m.files(self.contracts_path, search=search)
        avoid_terms = ['dbg']
        results = [f for f in files if all([k not in f for k in avoid_terms])]
        return results

    def abifile2name(self, path):
        """Convert ABI file path to contract name."""
        return path.split('.sol/')[-2].split('/')[-1]

    def name2abifile(self, search=None):
        """Map contract names to ABI files."""
        abifiles = self.abifiles(search=search)
        return {self.abifile2name(f): f for f in abifiles}

    def abimap(self, search=None, expand=False):
        """Map contract names to ABIs."""
        name2abifile = self.name2abifile(search=search)
        name2abi = {}
        for name, path in name2abifile.items():
            with open(path, 'r') as f:
                data = json.load(f)
                name2abi[name] = data['abi'] if expand else self.ipfs.put(data['abi'])
        return name2abi

    @property
    def ipfs(self):
        """IPFS client."""
        if not hasattr(self, '_ipfs'):
            self._ipfs = m.mod('ipfs')()
        return self._ipfs

    def name2abicid(self, search=None):
        """Map contract names to ABI IPFS CIDs."""
        name2abicid = {}
        for name, v in self.abimap(search=search).items():
            name2abicid[name] = self.ipfs.put(v)
        return name2abicid

    def abimap_cid(self):
        """Get ABI IPFS CID for contract name."""
        return self.ipfs.put(self.name2abicid())

    def ganache(self, port: int = 8545):
        """Start Ganache."""
        return os.system(f'cd {self.path} && docker-compose up -d ganache')

    # ==================== RAW TRANSACTION FUNCTIONS ====================

    def rpc_call(self, method: str, params: list = None) -> Dict[str, Any]:
        """Make a raw JSON-RPC call to the Ethereum node."""
        if params is None:
            params = []

        payload = {
            'jsonrpc': '2.0',
            'method': method,
            'params': params,
            'id': 1
        }

        response = requests.post(self.rpc_url, json=payload, headers={'Content-Type': 'application/json'})
        response.raise_for_status()
        result = response.json()

        if 'error' in result:
            raise Exception(f"RPC Error: {result['error']}")

        return result.get('result')

    def get_nonce(self, address: str) -> int:
        """Get transaction nonce for an address using raw RPC."""
        return int(self.rpc_call('eth_getTransactionCount', [address, 'latest']), 16)

    def get_gas_price(self) -> int:
        """Get current gas price using raw RPC."""
        return int(self.rpc_call('eth_gasPrice'), 16)

    def estimate_gas(self, transaction: Dict[str, Any]) -> int:
        """Estimate gas for a transaction using raw RPC."""
        return int(self.rpc_call('eth_estimateGas', [transaction]), 16)

    def build_transaction(self, to: str, data: str = '0x', value: int = 0,
                          gas: int = None, gas_price: int = None,
                          nonce: int = None, chain_id: int = None) -> Dict[str, Any]:
        """Build a raw transaction dictionary."""
        if chain_id is None:
            chain_id = int(self.rpc_call('eth_chainId'), 16)

        tx = {
            'to': Web3.to_checksum_address(to),
            'value': hex(value),
            'data': data,
            'chainId': hex(chain_id)
        }

        if nonce is None:
            nonce = self.get_nonce(self.account.address)
        tx['nonce'] = hex(nonce)

        if gas_price is None:
            gas_price = self.get_gas_price()
        tx['gasPrice'] = hex(gas_price)

        if gas is None:
            gas = self.estimate_gas(tx)
        tx['gas'] = hex(gas)

        return tx

    def sign_transaction(self, transaction: Dict[str, Any], private_key: str = None) -> str:
        """Sign a transaction with a private key."""
        if private_key is None:
            private_key = self.account.key
        else:
            if isinstance(private_key, str):
                if not private_key.startswith('0x'):
                    private_key = '0x' + private_key

        account: LocalAccount = Account.from_key(private_key)
        signed = account.sign_transaction(transaction)
        return signed.rawTransaction.hex()

    def send_raw_transaction(self, signed_tx: str) -> str:
        """Send a signed raw transaction using JSON-RPC."""
        if not signed_tx.startswith('0x'):
            signed_tx = '0x' + signed_tx
        tx_hash = self.rpc_call('eth_sendRawTransaction', [signed_tx])
        return tx_hash

    def wait_for_transaction(self, tx_hash: str, timeout: int = 120, poll_interval: int = 2) -> Dict[str, Any]:
        """Wait for a transaction to be mined using raw RPC."""
        import time

        if not tx_hash.startswith('0x'):
            tx_hash = '0x' + tx_hash

        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                receipt = self.rpc_call('eth_getTransactionReceipt', [tx_hash])
                if receipt is not None:
                    return receipt
            except Exception as e:
                m.print(f'Error getting receipt: {e}', color='red')
            time.sleep(poll_interval)

        raise TimeoutError(f'Transaction {tx_hash} not mined within {timeout} seconds')

    def raw_transfer(self, to: str, amount: float, token: str = 'market',
                     private_key: str = None, gas: int = None,
                     wait: bool = True) -> Dict[str, Any]:
        """Transfer tokens using raw transaction."""
        token_lower = token.lower()
        cfg = self.contracts_config().get(token_lower)
        if not cfg:
            raise ValueError(f'Token {token} not found in config')

        token_address = cfg['address']
        amount_wei = int(amount * 10 ** self.decimals(token))
        to_padded = to[2:].zfill(64) if to.startswith('0x') else to.zfill(64)
        amount_hex = hex(amount_wei)[2:].zfill(64)
        data = f"0xa9059cbb{to_padded}{amount_hex}"

        tx = self.build_transaction(to=token_address, data=data, value=0, gas=gas)

        m.print(f'Transferring {amount} {token.upper()} to {to}...', color='cyan')
        signed_tx = self.sign_transaction(tx, private_key)
        tx_hash = self.send_raw_transaction(signed_tx)
        m.print(f'Transaction sent: {tx_hash}', color='green')

        if wait:
            m.print('Waiting for confirmation...', color='yellow')
            receipt = self.wait_for_transaction(tx_hash)
            m.print('Transaction confirmed!', color='green')
            return {
                'hash': tx_hash,
                'receipt': receipt,
                'status': 'success' if receipt.get('status') == '0x1' else 'failed'
            }

        return {'hash': tx_hash, 'status': 'pending'}

    def raw_credit(self, stable_amount: float, payment_token: str = 'usdt',
                   private_key: str = None, wait: bool = True) -> Dict[str, Any]:
        """Buy stable tokens using raw transactions."""
        market_cfg = self.contracts_config().get('market')
        market_address = market_cfg['address']

        payment_cfg = self.contracts_config().get(payment_token.lower())
        payment_address = payment_cfg['address']

        tokengate = self.contracts.get('tokengate')
        price_info = tokengate.functions.getTokenPrice(payment_address).call()
        token_price = price_info[0]
        token_decimals = price_info[1]

        payment_amount = int((stable_amount * (10 ** token_decimals)) // token_price)

        m.print(f'Step 1: Approving {payment_amount / (10 ** token_decimals)} {payment_token.upper()}...', color='cyan')

        spender_padded = market_address[2:].zfill(64)
        amount_hex = hex(payment_amount)[2:].zfill(64)
        approve_data = f"0x095ea7b3{spender_padded}{amount_hex}"

        approve_tx = self.build_transaction(to=payment_address, data=approve_data, value=0)
        signed_approve = self.sign_transaction(approve_tx, private_key)
        approve_hash = self.send_raw_transaction(signed_approve)

        if wait:
            self.wait_for_transaction(approve_hash)
            m.print(f'Approval confirmed: {approve_hash}', color='green')

        m.print(f'Step 2: Crediting {stable_amount} stable tokens...', color='cyan')

        market = self.contracts.get('market')
        credit_call = market.functions.credit(payment_address, int(stable_amount * 10 ** self.decimals('usdc')))
        credit_data = credit_call._encode_transaction_data()

        credit_tx = self.build_transaction(to=market_address, data=credit_data, value=0)
        signed_credit = self.sign_transaction(credit_tx, private_key)
        credit_hash = self.send_raw_transaction(signed_credit)

        if wait:
            self.wait_for_transaction(credit_hash)
            m.print(f'Credit confirmed: {credit_hash}', color='green')
            return {
                'approve_hash': approve_hash,
                'credit_hash': credit_hash,
                'status': 'success'
            }

        return {
            'approve_hash': approve_hash,
            'credit_hash': credit_hash,
            'status': 'pending'
        }

    def encode_function_call(self, contract_name: str, function_name: str, args: list) -> str:
        """Encode a function call for a contract."""
        contract = self.contracts.get(contract_name.lower())
        if not contract:
            raise ValueError(f'Contract {contract_name} not found')
        func = getattr(contract.functions, function_name)
        call = func(*args)
        return call._encode_transaction_data()

    # ==================== SERVE / KILL ====================

    HUB_API_PORT = 8800
    HUB_APP_PORT = 8801
    HUB_API_PM2 = 'chain-hub-api'
    HUB_APP_PM2 = 'chain-hub-app'

    def _pm2(self):
        """Return the pm2 process manager mod."""
        return m.mod('pm.pm2')()

    def _serve_proc(self, name, script_dir, env, log_dir, log_name):
        """Start <script_dir>/start.sh under pm2, falling back to a detached
        subprocess if pm2 is unavailable. Returns a status dict."""
        import subprocess
        script = os.path.join(str(script_dir), 'start.sh')
        try:
            pm2 = self._pm2()
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            res = pm2.start_script(name=name, script_path=script,
                                   cwd=str(script_dir), interpreter='bash', env=env)
            if res.get('success'):
                return {'manager': 'pm2', 'name': name}
            raise RuntimeError(res.get('stderr') or res.get('error') or 'pm2 start failed')
        except Exception as e:
            log = open(os.path.join(str(log_dir), f'{log_name}.log'), 'w')
            proc = subprocess.Popen(['bash', script], cwd=str(script_dir),
                                    env={**os.environ, **env},
                                    stdout=log, stderr=subprocess.STDOUT)
            return {'manager': 'subprocess', 'pid': proc.pid, 'warn': str(e)}

    def serve(self, dev=True):
        """Serve the chain hub API + app.

        Args:
            dev: Use dev mode (hot reload)

        Returns:
            Dict of all running URLs
        """
        import subprocess
        import signal as sig
        from pathlib import Path

        self.kill()

        results = {}
        chain_dir = Path(self.path)

        # ── Start chain hub API ──────────────────────────────────
        api_port = self.HUB_API_PORT
        app_port = self.HUB_APP_PORT
        api_url = f'http://localhost:{api_port}'
        app_url = f'http://localhost:{app_port}'

        log_dir = Path('/tmp/chain-hub')
        log_dir.mkdir(parents=True, exist_ok=True)

        api_dir = chain_dir / 'src' / 'api'
        if not api_dir.is_dir():
            api_dir = chain_dir / 'api'
        if api_dir.is_dir() and (api_dir / 'start.sh').exists():
            api_env = {
                'PORT': str(api_port),
                'PYTHONPATH': f"{chain_dir}:{os.environ.get('PYTHONPATH', '')}",
            }
            results['hub_api'] = self._serve_proc(
                self.HUB_API_PM2, api_dir, api_env, log_dir, 'api')
            results['hub_api']['url'] = api_url
            results['hub_api']['docs'] = f'{api_url}/docs'

        # ── Start chain hub app ──────────────────────────────────
        app_dir = chain_dir / 'src' / 'app'
        if not app_dir.is_dir():
            app_dir = chain_dir / 'app'
        if app_dir.is_dir() and (app_dir / 'package.json').exists() and (app_dir / 'start.sh').exists():
            app_env = {
                'NEXT_PUBLIC_API_URL': api_url,
                'PORT': str(app_port),
                'DEV': '1' if dev else '0',
            }
            results['hub_app'] = self._serve_proc(
                self.HUB_APP_PM2, app_dir, app_env, log_dir, 'app')
            results['hub_app']['url'] = app_url

        results['hub_logs'] = str(log_dir)

        m.print(f'\nChain Hub: {api_url}/docs', color='cyan')
        m.print(f'Chain App: {app_url}', color='cyan')
        return results

    def kill(self):
        """Kill the chain hub API and app.

        Returns:
            Dict of killed processes
        """
        import subprocess
        import signal as sig

        results = {}

        # ── Kill chain hub ───────────────────────────────────────
        hub_killed = []
        # pm2-managed hub processes first
        try:
            pm2 = self._pm2()
            for name in (self.HUB_API_PM2, self.HUB_APP_PM2):
                if pm2.exists(name):
                    pm2.kill(name)
                    hub_killed.append(name)
        except Exception:
            pass
        # subprocess-started fallbacks (pgrep by port)
        for pattern in [f'uvicorn.*api:app.*{self.HUB_API_PORT}', f'next.*{self.HUB_APP_PORT}']:
            try:
                result = subprocess.run(
                    ['pgrep', '-f', pattern], capture_output=True, text=True,
                )
                for pid in result.stdout.strip().split('\n'):
                    if pid:
                        os.kill(int(pid), sig.SIGTERM)
                        hub_killed.append(pid)
            except Exception:
                pass
        results['hub'] = {'killed': hub_killed}

        return results
