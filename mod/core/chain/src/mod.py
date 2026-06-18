"""Chain Interface - Orchestrator for modular contract deployment.

Provides:
- Modular deployment via per-contract mods (mods/)
- Parallel proxy deployment with multiple keys
- Backward-compatible interaction with all contracts
- Dependency graph resolution for deploy ordering
"""

from web3 import Web3
from typing import Dict, Any, Optional, List, Union
import json
import os
import mod as m
import requests
from eth_account import Account
from eth_account.signers.local import LocalAccount


class Mod:
    """Chain orchestrator - manages contract mods and parallel deployment."""

    network2url = {
        'testnet': 'https://sepolia.base.org',
        'ganache': 'http://localhost:8545',
        'mainnet': 'https://mainnet.base.org'
    }
    conns = {}

    # Mod deploy order (dependency graph as ordered groups)
    # Each group can deploy in parallel; groups are sequential
    DEPLOY_GROUPS = [
        ['token', 'oracle', 'registry', 'perms'],   # no dependencies
        ['tokengate', 'bloctime'],                    # depend on group 1
        ['treasury'],                                 # depends on tokengate + bloctime
        ['market'],                                   # depends on treasury + tokengate
        ['debit'],                                    # depends on market
    ]

    MOD_NAMES = ['token', 'oracle', 'tokengate', 'bloctime',
                 'registry', 'treasury', 'market', 'debit', 'perms',
                 'safe', 'bridge']

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

    # ==================== MOD SYSTEM ====================

    def mod(self, name):
        """Get a contract mod instance.

        Args:
            name: Mod name (e.g. 'market', 'token', 'oracle')

        Returns:
            m.mod('chain.contracts')ule subclass instance
        """
        if name not in self._mods:
            from importlib import import_module
            mod_path = f'mod.core.chain.contracts.{name}.mod'
            mod_module = import_module(mod_path)
            self._mods[name] = mod_module.Mod(
                network=self.network,
                key=self.key_name if hasattr(self, 'key_name') else 'test',
            )
        return self._mods[name]

    def list_mods(self):
        """List available contract mods."""
        return self.MOD_NAMES

    # ==================== PARALLEL PROXY DEPLOYMENT ====================

    def deploy(self, network: str = None, keys: list = None,
               deployer_key: str = None, mods: list = None,
               setup: bool = True):
        """Deploy all contracts with optional parallel proxy deployment.

        Args:
            network: Target network (testnet/ganache/mainnet)
            keys: List of proxy key names for parallel deployment.
                  If None, uses single key (deployer_key or self key).
            deployer_key: Key that will own all contracts after deployment.
                          Defaults to self key.
            mods: Specific mods to deploy (default: all core mods)
            setup: Run post-deploy setup for each mod

        Returns:
            Dict of module_name -> deployed addresses
        """
        if network is None:
            network = self.network

        # Resolve keys
        deployer_key_name = deployer_key or (self.key_name if hasattr(self, 'key_name') else 'test')
        deployer = m.key(deployer_key_name)
        deployer_address = self.w3.eth.account.from_key(deployer.private_key).address

        if keys:
            proxy_key_names = keys
        else:
            proxy_key_names = [deployer_key_name]

        # Compile first
        self.compile()

        # Filter deploy groups to requested mods
        requested = set(mods) if mods else None
        deploy_groups = []
        for group in self.DEPLOY_GROUPS:
            filtered = [name for name in group if requested is None or name in requested]
            if filtered:
                deploy_groups.append(filtered)

        m.print(f'Deploying to {network} with {len(proxy_key_names)} key(s)', color='cyan')
        m.print(f'Deploy groups: {deploy_groups}', color='cyan')
        m.print(f'Deployer (final owner): {deployer_address}', color='cyan')

        deployed = {}  # module_name -> address or dict of addresses

        for group_idx, group in enumerate(deploy_groups):
            m.print(f'\n--- Group {group_idx + 1}: {group} ---', color='yellow')

            if len(group) > 1:
                if len(proxy_key_names) > 1:
                    # Parallel deployment with multiple proxy keys
                    futures = {}
                    for i, mod_name in enumerate(group):
                        key_name = proxy_key_names[i % len(proxy_key_names)]
                        deps = self._resolve_deps(mod_name, deployed)
                        future = m.submit(
                            self._deploy_mod,
                            dict(mod_name=mod_name, network=network,
                                 key=key_name, deps=deps),
                        )
                        futures[future] = mod_name

                    for future in m.as_completed(futures.keys()):
                        name = futures[future]
                        try:
                            result = future.result()
                            deployed[name] = result
                            m.print(f'{name}: deployed -> {result}', color='green')
                        except Exception as e:
                            m.print(f'{name}: FAILED -> {e}', color='red')
                            raise
                else:
                    # Parallel deployment with single key via nonce pre-assignment
                    results = self._deploy_group_parallel(
                        group, network, proxy_key_names[0], deployed)
                    deployed.update(results)
            else:
                # Sequential deployment (single mod in group)
                for mod_name in group:
                    key_name = proxy_key_names[0]
                    deps = self._resolve_deps(mod_name, deployed)
                    result = self._deploy_mod(mod_name, network, key_name, deps)
                    deployed[mod_name] = result
                    m.print(f'{mod_name}: deployed -> {result}', color='green')

        # Post-deploy setup
        if setup:
            m.print(f'\n--- Running setup ---', color='yellow')
            for group in deploy_groups:
                for mod_name in group:
                    try:
                        mod_instance = self.mod(mod_name)
                        all_deps = self._resolve_all_deps(deployed)
                        mod_instance.setup(network=network, **all_deps)
                    except Exception as e:
                        m.print(f'{mod_name} setup: {e}', color='yellow')

        # Transfer ownership from proxy keys to deployer
        if keys and len(keys) > 1:
            m.print(f'\n--- Transferring ownership to {deployer_address} ---', color='yellow')
            for mod_name in deployed:
                try:
                    mod_instance = self.mod(mod_name)
                    mod_instance.transfer_ownership(deployer_address)
                except Exception as e:
                    m.print(f'{mod_name} ownership transfer: {e}', color='yellow')

        # Reload contracts
        self.config = m.config('chain')
        self.load_all_contracts()

        # Sync to app
        try:
            self.sync_app()
        except Exception:
            pass

        m.print(f'\nDeployment complete!', color='green')
        return deployed

    def deploy_mod(self, mod_name, network=None, key=None, **deps):
        """Deploy a single mod.

        Args:
            mod_name: Mod name (e.g. 'market')
            network: Target network
            key: Key name for signing
            **deps: Dependency addresses
        """
        network = network or self.network
        key = key or (self.key_name if hasattr(self, 'key_name') else 'test')
        self.compile()
        result = self._deploy_mod(mod_name, network, key, deps)
        self.config = m.config('chain')
        self.load_all_contracts()
        return result

    def _deploy_mod(self, mod_name, network, key, deps, nonce=None):
        """Internal: deploy a single mod."""
        mod_instance = self.mod(mod_name)
        mod_instance.set_key(key)
        return mod_instance.deploy(network=network, key=key, nonce=nonce, **deps)

    def _deploy_group_parallel(self, group, network, key_name, deployed):
        """Deploy a group of mods in parallel using nonce pre-assignment.

        Pre-assigns nonces from a single wallet so all deploys can fire
        concurrently without nonce conflicts.
        """
        # Get base nonce
        deployer = m.key(key_name)
        deployer_address = self.w3.eth.account.from_key(deployer.private_key).address
        base_nonce = self.w3.eth.get_transaction_count(deployer_address, 'pending')

        # Assign nonce ranges
        nonce_assignments = {}
        current_nonce = base_nonce
        for mod_name in group:
            mod_instance = self.mod(mod_name)
            count = getattr(mod_instance, 'deploy_count', 1)
            nonce_assignments[mod_name] = current_nonce
            current_nonce += count

        m.print(f'Nonce pre-assignment: base={base_nonce}, '
                f'assignments={nonce_assignments}', color='cyan')

        # Fire all deploys in parallel
        futures = {}
        for mod_name in group:
            deps = self._resolve_deps(mod_name, deployed)
            nonce = nonce_assignments[mod_name]
            future = m.submit(
                self._deploy_mod,
                dict(mod_name=mod_name, network=network,
                     key=key_name, deps=deps, nonce=nonce),
            )
            futures[future] = mod_name

        # Collect results
        results = {}
        for future in m.as_completed(futures.keys()):
            name = futures[future]
            try:
                result = future.result()
                results[name] = result
                m.print(f'{name}: deployed -> {result}', color='green')
            except Exception as e:
                m.print(f'{name}: FAILED -> {e}', color='red')
                raise

        return results

    def _resolve_deps(self, mod_name, deployed):
        """Resolve dependency addresses from already-deployed mods."""
        mod_instance = self.mod(mod_name)
        deps = {}
        for dep_name in mod_instance.dependencies:
            if dep_name in deployed:
                val = deployed[dep_name]
                if isinstance(val, str):
                    deps[dep_name] = val
                elif isinstance(val, dict) and 'address' in val:
                    deps[dep_name] = val['address']
                elif isinstance(val, dict):
                    # Multi-address result (e.g. token returns {USDC: addr, ...})
                    deps[dep_name] = val
                else:
                    raise ValueError(f'Invalid deployment result for {dep_name}: {val}')

            else:
                # Try to load from config
                deployment = self.config.get('deployments', {}).get(self.network, {})
                contracts = deployment.get('contracts', {})
                # Map mod name to config key
                key_map = {
                    'oracle': 'ManualPriceOracle',
                    'tokengate': 'TokenGate',
                    'bloctime': 'BlocTime',
                    'market': 'Market',
                    'treasury': 'Treasury',
                    'debit': 'Debit',
                    'registry': 'Registry',
                    'perms': 'Perms',
                    'token': 'NativeToken',
                }
                config_key = key_map.get(dep_name, dep_name)
                if config_key in contracts:
                    deps[dep_name] = contracts[config_key].get('address')
        return deps

    def _resolve_all_deps(self, deployed):
        """Build a flat dict of all deployed addresses for setup."""
        deps = {}
        for name, result in deployed.items():
            if isinstance(result, dict):
                deps.update(result)
                deps[name] = result
            else:
                deps[name] = result
        # Also pull from config
        deployment = self.config.get('deployments', {}).get(self.network, {})
        contracts = deployment.get('contracts', {})
        for key, info in contracts.items():
            if key.lower() not in deps:
                deps[key.lower()] = info.get('address')
            deps[key] = info.get('address')
        return deps

    # ==================== FORK ====================

    def fork(self, owner, mods=None, network=None, label=None, gas_key='test'):
        """Fork contracts - redeploy all (or selected) contracts under a new owner.

        The gas_key (default: test) pays for deployment gas. After deployment,
        ownership of each contract transfers to the forker's address.

        Uses nonce pre-assignment for parallel deployment within each group.

        Args:
            owner: Key name or address that will own the forked contracts.
            mods: List of mod names to fork (default: all in DEPLOY_GROUPS).
            network: Target network (default: self.network).
            label: Optional label for the fork (used in config namespace).
                   Defaults to the owner's address.
            gas_key: Key name that pays for gas (default: 'test').

        Returns:
            Dict with deployed addresses and fork metadata.

        CLI:
            m chain/fork owner=alice
            m chain/fork owner=alice mods='["token","registry"]'
            m chain/fork owner=0x1234... label=myproject
        """
        network = network or self.network
        w3 = self.w3
        chain_id = self.chain_id

        # Resolve gas sponsor
        gas_account = w3.eth.account.from_key(m.key(gas_key).private_key)
        gas_balance = w3.eth.get_balance(gas_account.address)
        m.print(f'Gas sponsor: {gas_account.address} ({gas_balance / 1e18:.4f} ETH)', color='cyan')
        if gas_balance == 0:
            raise ValueError(f'Gas key "{gas_key}" has no ETH for deployment')

        # Resolve fork owner
        if Web3.is_address(owner):
            owner_address = Web3.to_checksum_address(owner)
        else:
            owner_address = w3.eth.account.from_key(m.key(owner).private_key).address
        fork_label = label or owner_address[:10].lower()

        m.print(f'Forking contracts for {owner_address}', color='cyan')
        m.print(f'Fork label: {fork_label}', color='cyan')

        # Compile first
        self.compile()

        # Filter deploy groups
        requested = set(mods) if mods else None
        deploy_groups = []
        for group in self.DEPLOY_GROUPS:
            filtered = [name for name in group if requested is None or name in requested]
            if filtered:
                deploy_groups.append(filtered)

        m.print(f'Deploy groups: {deploy_groups}', color='cyan')

        deployed = {}  # mod_name -> address or dict

        for group_idx, group in enumerate(deploy_groups):
            m.print(f'\n--- Fork group {group_idx + 1}: {group} ---', color='yellow')

            if len(group) > 1:
                # Parallel deploy with nonce pre-assignment
                base_nonce = w3.eth.get_transaction_count(gas_account.address, 'pending')
                nonce_map = {}
                current_nonce = base_nonce

                # Pre-count how many txs each mod needs
                deploy_counts = {
                    'token': 4,  # USDC, USDT, DAI, NativeToken
                }
                for mod_name in group:
                    count = deploy_counts.get(mod_name, getattr(self.mod(mod_name), 'deploy_count', 1))
                    nonce_map[mod_name] = current_nonce
                    current_nonce += count

                m.print(f'Nonce pre-assignment: base={base_nonce}, map={nonce_map}', color='cyan')

                futures = {}
                for mod_name in group:
                    deps = self._resolve_deps(mod_name, deployed)
                    start_nonce = nonce_map[mod_name]
                    future = m.submit(
                        self._fork_deploy_mod,
                        dict(mod_name=mod_name, network=network,
                             gas_key=gas_key, deps=deps, nonce=start_nonce),
                    )
                    futures[future] = mod_name

                for future in m.as_completed(futures.keys()):
                    name = futures[future]
                    try:
                        result = future.result()
                        deployed[name] = result
                        m.print(f'{name}: forked -> {result}', color='green')
                    except Exception as e:
                        m.print(f'{name}: FAILED -> {e}', color='red')
                        raise
            else:
                for mod_name in group:
                    deps = self._resolve_deps(mod_name, deployed)
                    result = self._fork_deploy_mod(mod_name, network, gas_key, deps)
                    deployed[mod_name] = result
                    m.print(f'{mod_name}: forked -> {result}', color='green')

        # Transfer ownership to fork owner
        import time
        m.print(f'\n--- Transferring ownership to {owner_address} ---', color='yellow')
        for mod_name in deployed:
            try:
                mod_instance = self.mod(mod_name)
                mod_instance.set_key(gas_key)
                mod_w3 = mod_instance.w3
                result = deployed[mod_name]
                if isinstance(result, dict):
                    m.print(f'{mod_name}: multi-contract, skipping ownership transfer', color='yellow')
                    continue
                address = result
                abi = mod_instance.get_abi(mod_instance.contracts[0])
                has_ownership = any(
                    item.get('name') == 'transferOwnership'
                    for item in abi if item.get('type') == 'function'
                )
                if not has_ownership:
                    m.print(f'{mod_name}: no transferOwnership function', color='yellow')
                    continue
                contract = mod_w3.eth.contract(
                    address=Web3.to_checksum_address(address), abi=abi)
                # Retry owner() call with backoff (RPC state sync delay)
                current_owner = None
                for attempt in range(5):
                    try:
                        current_owner = contract.functions.owner().call()
                        break
                    except Exception:
                        time.sleep(2)
                if current_owner is None:
                    m.print(f'{mod_name}: could not read owner after retries', color='red')
                    continue
                if current_owner.lower() != owner_address.lower():
                    tx = contract.functions.transferOwnership(
                        Web3.to_checksum_address(owner_address)
                    ).build_transaction({
                        'from': mod_instance.account.address,
                        'nonce': mod_w3.eth.get_transaction_count(
                            mod_instance.account.address, 'pending'),
                        'gasPrice': mod_w3.eth.gas_price,
                        'chainId': mod_instance.chain_id,
                    })
                    signed = mod_w3.eth.account.sign_transaction(
                        tx, mod_instance.account.key)
                    tx_hash = mod_w3.eth.send_raw_transaction(signed.raw_transaction)
                    mod_w3.eth.wait_for_transaction_receipt(tx_hash)
                    m.print(f'{mod_name}: ownership -> {owner_address}', color='green')
                else:
                    m.print(f'{mod_name}: already owned by {owner_address}', color='yellow')
            except Exception as e:
                m.print(f'{mod_name}: ownership transfer failed - {e}', color='red')

        # Save fork deployment to ~/mod/chain/forks/
        fork_key = f'{network}:fork:{fork_label}'
        forks_dir = os.path.join(os.path.expanduser('~/mod/chain'), 'forks')
        os.makedirs(forks_dir, exist_ok=True)
        fork_data = {
            'owner': owner_address,
            'gas_sponsor': gas_account.address,
            'network': network,
            'label': fork_label,
            'contracts': {},
        }
        for mod_name, result in deployed.items():
            if isinstance(result, dict):
                for k, v in result.items():
                    fork_data['contracts'][k] = {
                        'address': v, 'mod': mod_name,
                    }
            else:
                fork_data['contracts'][mod_name] = {
                    'address': result, 'mod': mod_name,
                }
        fork_file = os.path.join(forks_dir, f'{fork_label}.json')
        with open(fork_file, 'w') as f:
            json.dump(fork_data, f, indent=4)

        m.print(f'\nFork complete! Owner: {owner_address}', color='green')
        m.print(f'Fork key: {fork_key}', color='cyan')
        return {
            'fork_key': fork_key,
            'owner': owner_address,
            'gas_sponsor': gas_account.address,
            'deployed': deployed,
        }

    def _fork_deploy_mod(self, mod_name, network, gas_key, deps, nonce=None):
        """Internal: deploy a single mod using gas_key as sponsor.

        Sets _next_nonce on the mod instance so deploy_contract auto-increments
        through pre-assigned nonces for parallel deployment.
        """
        mod_instance = self.mod(mod_name)
        mod_instance.set_key(gas_key)
        if nonce is not None:
            mod_instance._next_nonce = nonce
        result = mod_instance.deploy(network=network, key=gas_key, **deps)
        mod_instance._next_nonce = None  # reset
        return result

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
                    m.print(f'ABI not found for {name} at {info.get("abi")}', color='red')
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

    def send_tx(self, module, function, args: list) -> Dict[str, Any]:
        """Send a transaction to a contract function."""
        contract = self.contracts.get(module)
        if not contract:
            raise ValueError(f'{module} contract not loaded')

        tx = getattr(contract.functions, function)(*args).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

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

    def test(self, mod_name=None, network=None):
        """Run contract tests.

        Args:
            mod_name: Specific mod to test (e.g. 'market', 'token').
                      If None, runs all tests.
            network: Network for test context (default: testnet).
                     Used for ganache/fork tests.
        """
        network = network or self.network
        if mod_name:
            mod_instance = self.mod(mod_name)
            if hasattr(mod_instance, 'test'):
                return mod_instance.test()
            raise ValueError(f'{mod_name} has no test() method')
        # Run all tests
        dp = m.dp('chain')
        return os.system(f'cd {dp} && npx hardhat test')

    def compile(self, mod_name=None):
        """Compile contracts.

        Args:
            mod_name: If provided, compile only that mod's contracts.
                      If None, compile all mods.
        """
        if mod_name:
            mod_instance = self.mod(mod_name)
            mod_instance.compile()
        else:
            # Compile each mod that has local contracts
            mods_dir = os.path.join(self.path, 'chain', 'mods')
            for name in self.MOD_NAMES:
                mod_contracts = os.path.join(mods_dir, name, 'contracts')
                if os.path.isdir(mod_contracts):
                    try:
                        mod_instance = self.mod(name)
                        mod_instance.compile()
                    except Exception as e:
                        m.print(f'Error compiling {name}: {e}', color='yellow')
            # Also compile chain-level contracts as fallback
            dp = m.dp('chain')
            os.system(f'cd {dp} && npx hardhat compile')

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

    def serve(self, mods=None, dev=True):
        """Serve chain hub API + app, plus all (or selected) module APIs + apps.

        Args:
            mods: List of mod names to serve, or None for all
            dev: Use dev mode (hot reload)

        Returns:
            Dict of all running URLs
        """
        import subprocess
        import signal as sig
        from pathlib import Path

        self.kill(mods=mods)

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

        # ── Start module APIs + apps ─────────────────────────────
        mod_names = mods if mods else self.MOD_NAMES
        results['modules'] = {}

        for mod_name in mod_names:
            try:
                mod_instance = self.mod(mod_name)
                mod_result = mod_instance.serve(dev=dev)
                results['modules'][mod_name] = mod_result
                m.print(f'{mod_name}: serving', color='green')
            except Exception as e:
                results['modules'][mod_name] = {'error': str(e)}
                m.print(f'{mod_name}: serve failed - {e}', color='red')

        m.print(f'\nChain Hub: {api_url}/docs', color='cyan')
        m.print(f'Chain App: {app_url}', color='cyan')
        return results

    def kill(self, mods=None):
        """Kill chain hub and all (or selected) module processes.

        Args:
            mods: List of mod names to kill, or None for all

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

        # ── Kill modules ─────────────────────────────────────────
        mod_names = mods if mods else self.MOD_NAMES
        results['modules'] = {}

        for mod_name in mod_names:
            try:
                mod_instance = self.mod(mod_name)
                mod_result = mod_instance.kill()
                results['modules'][mod_name] = mod_result
            except Exception as e:
                results['modules'][mod_name] = {'error': str(e)}

        return results
