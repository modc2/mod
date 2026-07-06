"""EVM registry backend — interfaces with Registry.sol on Base via web3.py."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'registry'))
from base import RegistryBackend

# Registry.sol ABI (minimal — matches deployed contract)
REGISTRY_ABI = [
    {"inputs": [{"name": "name", "type": "string"}, {"name": "data", "type": "string"}],
     "name": "registerMod", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "modId", "type": "uint256"}, {"name": "data", "type": "string"}],
     "name": "updateMod", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "modId", "type": "uint256"}],
     "name": "removeMod", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "modId", "type": "uint256"}, {"name": "newOwner", "type": "address"}],
     "name": "transferOwnership", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "id", "type": "uint256"}],
     "name": "getMod", "outputs": [
         {"name": "owner", "type": "address"}, {"name": "name", "type": "string"}, {"name": "data", "type": "string"}
     ], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "user", "type": "address"}],
     "name": "getUserMods", "outputs": [{"name": "", "type": "uint256[]"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "creator", "type": "address"}, {"name": "name", "type": "string"}],
     "name": "isNameTaken", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "nextModId", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]


class EVMRegistry(RegistryBackend):
    """Base/EVM registry backend using web3.py to call Registry.sol."""

    name = 'evm'

    def __init__(self, rpc_url: str = None, chain_id: int = None,
                 registry_address: str = None, network: str = 'testnet',
                 private_key: str = None, abi: list = None, **kw):
        self.rpc_url = rpc_url or 'https://sepolia.base.org'
        self.chain_id = chain_id or 84532
        self.registry_address = registry_address
        self.network = network
        self._private_key = private_key
        self._abi = abi or self._load_abi()
        self._w3 = None
        self._contract = None
        self._account = None

    def _load_abi(self):
        """Try loading ABI from chain artifacts, fall back to inline."""
        artifact = os.path.join(
            os.path.dirname(__file__), '..', '..', '..', '..', '..', 'core', 'chain',
            'artifacts', 'contracts', 'registry', 'Registry.sol', 'Registry.json'
        )
        if os.path.exists(artifact):
            with open(artifact, 'r') as f:
                return json.load(f).get('abi', REGISTRY_ABI)
        return REGISTRY_ABI

    def _connect(self):
        if self._w3 is not None:
            return
        from web3 import Web3
        self._w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        if not self._w3.is_connected():
            raise ConnectionError(f'Cannot connect to {self.rpc_url}')
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(self.registry_address),
            abi=self._abi,
        )

    def _get_account(self):
        if self._account:
            return self._account
        if self._private_key:
            self._account = self._w3.eth.account.from_key(self._private_key)
        else:
            # Try loading from mod framework key module
            try:
                import mod as m
                key = m.mod('key')(crypto_type='ecdsa')
                self._private_key = key.private_key.hex() if isinstance(key.private_key, bytes) else key.private_key
                self._account = self._w3.eth.account.from_key(self._private_key)
            except Exception:
                raise ValueError('No private key configured. Pass private_key= or configure mod key module.')
        return self._account

    def _send_tx(self, fn):
        """Build, sign, and send a contract transaction."""
        self._connect()
        account = self._get_account()
        tx = fn.build_transaction({
            'from': account.address,
            'nonce': self._w3.eth.get_transaction_count(account.address),
            'chainId': self.chain_id,
            'gas': 500_000,
            'gasPrice': self._w3.eth.gas_price,
        })
        signed = account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt

    def register(self, name: str, data: str, owner: str = None, **kw) -> str:
        self._connect()
        fn = self._contract.functions.registerMod(name, data)
        receipt = self._send_tx(fn)
        # Parse ModRegistered event to get mod ID
        logs = self._contract.events.ModRegistered().process_receipt(receipt)
        if logs:
            return str(logs[0]['args']['modId'])
        # Fallback: read nextModId - 1
        next_id = self._contract.functions.nextModId().call()
        return str(next_id - 1)

    def update(self, mod_id: str, data: str, owner: str = None, **kw) -> bool:
        self._connect()
        fn = self._contract.functions.updateMod(int(mod_id), data)
        self._send_tx(fn)
        return True

    def remove(self, mod_id: str, owner: str = None, **kw) -> bool:
        self._connect()
        fn = self._contract.functions.removeMod(int(mod_id))
        self._send_tx(fn)
        return True

    def get(self, mod_id: str, **kw) -> dict:
        self._connect()
        result = self._contract.functions.getMod(int(mod_id)).call()
        owner, name, data = result
        if owner == '0x' + '0' * 40:
            return None
        return {'id': str(mod_id), 'owner': owner, 'name': name, 'data': data}

    def get_user_mods(self, owner: str, **kw) -> list:
        self._connect()
        from web3 import Web3
        mod_ids = self._contract.functions.getUserMods(
            Web3.to_checksum_address(owner)
        ).call()
        mods = []
        for mid in mod_ids:
            mod = self.get(str(mid))
            if mod:
                mods.append(mod)
        return mods

    def is_name_taken(self, owner: str, name: str, **kw) -> bool:
        self._connect()
        from web3 import Web3
        return self._contract.functions.isNameTaken(
            Web3.to_checksum_address(owner), name
        ).call()

    def transfer(self, mod_id: str, new_owner: str, owner: str = None, **kw) -> bool:
        self._connect()
        from web3 import Web3
        fn = self._contract.functions.transferOwnership(
            int(mod_id), Web3.to_checksum_address(new_owner)
        )
        self._send_tx(fn)
        return True

    def list_all(self, **kw) -> list:
        self._connect()
        next_id = self._contract.functions.nextModId().call()
        mods = []
        for i in range(1, next_id):
            mod = self.get(str(i))
            if mod:
                mods.append(mod)
        return mods
