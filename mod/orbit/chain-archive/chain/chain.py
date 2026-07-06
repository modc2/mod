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
        self.contracts_path = self.path + '/artifacts/contracts'
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
            mod_path = f'mod.core.chain.mods.{name}.mod'
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

            if len(proxy_key_names) > 1 and len(group) > 1:
                # Parallel deployment with proxy keys
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
                # Sequential deployment
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

    def _deploy_mod(self, mod_name, network, key, deps):
        """Internal: deploy a single mod."""
        mod_instance = self.mod(mod_name)
        mod_instance.set_key(key)
        return mod_instance.deploy(network=network, key=key, **deps)

    def _resolve_deps(self, mod_name, deployed):
        """Resolve dependency addresses from already-deployed mods."""
        mod_instance = self.mod(mod_name)
        deps = {}
        for dep_name in mod_instance.dependencies:
            if dep_name in deployed:
                deps[dep_name] = deployed[dep_name]
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
        contracts = self.config['deployments'][self.network]['contracts']
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

    def get_mod(self, mod_id: int) -> Dict[str, Any]:
        """Get mod information."""
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        info = registry.functions.getMod(mod_id).call()
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

    def modIds(self, address=None):
        """Get all mods for a user from registry."""
        registry = self.contracts.get('registry')
        if not registry:
            raise ValueError('Registry contract not loaded')
        addr = address or self.account.address
        mod_ids = registry.functions.getUserMods(addr).call()
        return mod_ids

    def mod_exists(self, mod: str) -> bool:
        """Check if mod exists in registry."""
        mod_id = self.name2id(mod)
        try:
            _ = self.get_mod(mod_id)
            return True
        except Exception:
            return False

    def mods(self, address=None, keys=['id', 'data', 'name']):
        """Get all mods for a user from registry."""
        mod_ids = self.modIds(address=address)
        mods = []
        for mod_id in mod_ids:
            _mod = self.get_mod(mod_id)
            _mod['id'] = mod_id
            mod_info = {k: _mod[k] for k in keys}
            mods.append(mod_info)
        return mods

    def mymods(self):
        """Get all mods for the connected user from registry."""
        return self.mods(address=self.account.address)

    # ==================== UTILITY FUNCTIONS ====================

    def forward(self, x=1, y=2):
        """Default action."""
        return x + y

    def test(self):
        """Run contract tests."""
        dp = m.dp('chain')
        return os.system(f'cd {dp} && npm test')

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
            mods_dir = os.path.join(self.path, 'mods')
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
