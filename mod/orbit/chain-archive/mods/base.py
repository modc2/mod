"""Base class for all chain contract modules.

Provides standardized deploy, send_tx, call, ownership transfer,
and config management for each contract module.
"""

from web3 import Web3
from typing import Dict, Any, List, Optional
import json
import os
import mod as m


class m.mod('chain.contracts')ule:
    """Base class for chain contract modules.

    Subclasses set:
        name: str           - module name (e.g. 'market')
        contracts: list     - solidity contract names (e.g. ['Market'])
        dependencies: list  - module names that must deploy first
    """

    name = ''
    contracts = []
    dependencies = []

    network2url = {
        'testnet': 'https://sepolia.base.org',
        'ganache': 'http://localhost:8545',
        'mainnet': 'https://mainnet.base.org',
    }
    _conns = {}

    def __init__(self, network='testnet', key='test'):
        self.network = network
        self.rpc_url = self.network2url.get(network, network)

        if self.rpc_url in self._conns:
            self.w3 = self._conns[self.rpc_url]
        else:
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            self._conns[self.rpc_url] = self.w3

        self.chain_id = self.w3.eth.chain_id
        self.chain_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # Module path: directory containing this subclass's mod.py
        self.module_path = self._find_module_path()
        # Artifacts: check local module first, then chain-level
        self.local_artifacts = os.path.join(self.module_path, 'artifacts', 'contracts')
        self.chain_artifacts = os.path.join(self.chain_path, 'artifacts', 'contracts')
        self._contract_instances = {}
        self.set_key(key)
        self._load_contracts()

    def _find_module_path(self):
        """Find the directory of the subclass's mod.py (the module root)."""
        import inspect
        # Walk up the MRO to find the subclass file
        for cls in type(self).__mro__:
            if cls is m.mod('chain.contracts')ule:
                continue
            src = inspect.getfile(cls)
            return os.path.dirname(src)
        return os.path.dirname(os.path.abspath(__file__))

    def set_key(self, key):
        """Set the signing key."""
        if key is None:
            key = 'test'
        self.key_name = key
        key_obj = m.key(key)
        self.account = self.w3.eth.account.from_key(key_obj.private_key)
        return self.account.address

    # ==================== CONFIG ====================

    def config(self):
        """Load chain config.json."""
        config_path = os.path.join(self.chain_path, 'config.json')
        if os.path.exists(config_path):
            return m.get_json(config_path)
        return {'deployments': {}}

    def save_config(self, config):
        """Save chain config.json."""
        config_path = os.path.join(self.chain_path, 'config.json')
        m.put_json(config_path, config)

    def get_deployment(self, network=None):
        """Get deployment info for this network."""
        network = network or self.network
        config = self.config()
        return config.get('deployments', {}).get(network, {})

    def get_contract_config(self, contract_key=None):
        """Get contract address/info from config."""
        deployment = self.get_deployment()
        contracts = deployment.get('contracts', {})
        if contract_key:
            # case-insensitive lookup
            for k, v in contracts.items():
                if k.lower() == contract_key.lower():
                    return v
            return None
        return contracts

    def save_deployment(self, contract_key, address, contract_name):
        """Save a deployed contract address to config."""
        config = self.config()
        if self.network not in config['deployments']:
            config['deployments'][self.network] = {
                'chainId': str(self.chain_id),
                'deployer': self.account.address,
                'url': self.rpc_url,
                'contracts': {},
            }
        deployment = config['deployments'][self.network]
        deployment['contracts'][contract_key] = {
            'address': address,
            'contract': contract_name,
        }
        self.save_config(config)
        return address

    # ==================== ABI ====================

    @property
    def ipfs(self):
        if not hasattr(self, '_ipfs'):
            self._ipfs = m.mod('ipfs')()
        return self._ipfs

    def _search_artifacts(self, contract_name, field):
        """Search for contract artifact in local then chain-level artifacts."""
        for artifacts_dir in [self.local_artifacts, self.chain_artifacts]:
            if not os.path.exists(artifacts_dir):
                continue
            for root, _dirs, files in os.walk(artifacts_dir):
                for f in files:
                    if f == f'{contract_name}.json' and '.dbg.' not in f:
                        path = os.path.join(root, f)
                        with open(path) as fh:
                            data = json.load(fh)
                        return data.get(field, [] if field == 'abi' else '')
        return [] if field == 'abi' else ''

    def get_abi(self, contract_name):
        """Get ABI from hardhat artifacts (local module first, then chain-level)."""
        return self._search_artifacts(contract_name, 'abi')

    def get_bytecode(self, contract_name):
        """Get bytecode from hardhat artifacts (local module first, then chain-level)."""
        return self._search_artifacts(contract_name, 'bytecode')

    # ==================== CONTRACT LOADING ====================

    def _load_contracts(self):
        """Load deployed contract instances from config."""
        deployment = self.get_deployment()
        contracts = deployment.get('contracts', {})
        for key, info in contracts.items():
            contract_name = info.get('contract', '')
            address = info.get('address', '')
            if not address:
                continue
            # Only load contracts this module manages
            if contract_name in self.contracts or not self.contracts:
                try:
                    abi = self.get_abi(contract_name)
                    if abi:
                        self._contract_instances[key.lower()] = self.w3.eth.contract(
                            address=Web3.to_checksum_address(address),
                            abi=abi,
                        )
                except Exception:
                    continue

    def contract(self, name=None):
        """Get a loaded contract instance."""
        if name is None:
            name = self.name
        return self._contract_instances.get(name.lower())

    # ==================== DEPLOY ====================

    def compile(self):
        """Compile this module's contracts via hardhat.

        Uses the module's local contracts/ dir with a symlinked node_modules
        from the chain directory, or falls back to chain-level compilation.
        """
        local_contracts = os.path.join(self.module_path, 'contracts')
        modules_dir = os.path.dirname(self.module_path)
        shared_config = os.path.join(modules_dir, 'hardhat.config.js')
        chain_node_modules = os.path.join(self.chain_path, 'node_modules')
        local_node_modules = os.path.join(self.module_path, 'node_modules')

        if os.path.isdir(local_contracts) and os.path.exists(shared_config):
            # Ensure node_modules symlink exists
            if not os.path.exists(local_node_modules) and os.path.exists(chain_node_modules):
                os.symlink(chain_node_modules, local_node_modules)

            # Copy shared config to module dir for hardhat
            local_config = os.path.join(self.module_path, 'hardhat.config.js')
            if not os.path.exists(local_config):
                import shutil
                shutil.copy2(shared_config, local_config)

            m.print(f'Compiling {self.name} contracts...', color='yellow')
            os.system(f'cd {self.module_path} && npx hardhat compile')
        else:
            m.print(f'Compiling all contracts at chain level...', color='yellow')
            os.system(f'cd {self.chain_path} && npx hardhat compile')

    def deploy_contract(self, contract_name, constructor_args=None, key=None, network=None, contract_key=None):
        """Deploy a single contract.

        Args:
            contract_name: Solidity contract name (e.g. 'Market')
            constructor_args: List of constructor arguments
            key: Key name for signing (uses self.account if None)
            network: Network to deploy to
            contract_key: Config key to save under (defaults to contract_name)

        Returns:
            Deployed contract address
        """
        if network:
            self.network = network
            self.rpc_url = self.network2url.get(network, network)
            self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
            self.chain_id = self.w3.eth.chain_id

        if key:
            self.set_key(key)

        abi = self.get_abi(contract_name)
        bytecode = self.get_bytecode(contract_name)
        if not abi or not bytecode:
            m.print(f'Compiling contracts...', color='yellow')
            self.compile()
            abi = self.get_abi(contract_name)
            bytecode = self.get_bytecode(contract_name)

        if not abi or not bytecode:
            raise ValueError(f'Could not find artifacts for {contract_name}')

        contract = self.w3.eth.contract(abi=abi, bytecode=bytecode)

        args = constructor_args or []
        tx = contract.constructor(*args).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address, 'pending'),
            'gasPrice': self.w3.eth.gas_price,
            'chainId': self.chain_id,
        })

        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        m.print(f'Deploying {contract_name}... tx: {tx_hash.hex()}', color='cyan')

        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        address = receipt.contractAddress
        m.print(f'{contract_name} deployed at {address}', color='green')

        # Save to config
        save_key = contract_key or contract_name
        self.save_deployment(save_key, address, contract_name)

        # Load the instance
        self._contract_instances[save_key.lower()] = self.w3.eth.contract(
            address=Web3.to_checksum_address(address),
            abi=abi,
        )

        return address

    # ==================== TRANSACTIONS ====================

    def send_tx(self, function_name, args=None, contract_key=None, value=0):
        """Send a transaction to a contract function.

        Args:
            function_name: Contract function name
            args: List of arguments
            contract_key: Contract key (defaults to self.name)
            value: ETH value in wei

        Returns:
            Transaction receipt
        """
        c = self.contract(contract_key)
        if not c:
            raise ValueError(f'Contract {contract_key or self.name} not loaded')

        args = args or []
        tx = getattr(c.functions, function_name)(*args).build_transaction({
            'from': self.account.address,
            'nonce': self.w3.eth.get_transaction_count(self.account.address, 'pending'),
            'value': value,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.account.key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        m.print(f'{self.name}.{function_name}() tx: {tx_hash.hex()}', color='cyan')
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt

    def call(self, function_name, args=None, contract_key=None):
        """Call a read-only contract function.

        Args:
            function_name: Contract function name
            args: List of arguments
            contract_key: Contract key (defaults to self.name)

        Returns:
            Function return value
        """
        c = self.contract(contract_key)
        if not c:
            raise ValueError(f'Contract {contract_key or self.name} not loaded')
        args = args or []
        return getattr(c.functions, function_name)(*args).call()

    # ==================== OWNERSHIP ====================

    def owner(self, contract_key=None):
        """Get contract owner."""
        return self.call('owner', contract_key=contract_key)

    def transfer_ownership(self, new_owner, contract_key=None):
        """Transfer contract ownership (Ownable pattern)."""
        new_owner = Web3.to_checksum_address(new_owner)
        current = self.owner(contract_key=contract_key)
        if current.lower() == new_owner.lower():
            m.print(f'{self.name}: already owned by {new_owner}', color='yellow')
            return None
        m.print(f'{self.name}: transferring ownership to {new_owner}', color='cyan')
        return self.send_tx('transferOwnership', [new_owner], contract_key=contract_key)

    # ==================== DEPLOY INTERFACE ====================

    def deploy(self, network='testnet', key=None, **deps):
        """Deploy this module's contracts. Override in subclass."""
        raise NotImplementedError(f'{self.name}.deploy() not implemented')

    def setup(self, network='testnet', **deps):
        """Post-deploy configuration. Override in subclass if needed."""
        pass

    def test(self):
        """Run this mod's hardhat tests.

        Compiles contracts, ensures node_modules and hardhat config
        are symlinked, then runs `npx hardhat test` in the mod directory.
        """
        self.compile()
        test_dir = os.path.join(self.module_path, 'test')
        if not os.path.isdir(test_dir):
            m.print(f'{self.name}: no test/ directory found', color='yellow')
            return 1
        m.print(f'Running {self.name} tests...', color='cyan')
        return os.system(f'cd {self.module_path} && npx hardhat test')

    def forward(self):
        """Default CLI action."""
        return {
            'name': self.name,
            'contracts': self.contracts,
            'dependencies': self.dependencies,
            'network': self.network,
            'account': self.account.address,
        }
