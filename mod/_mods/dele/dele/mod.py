from web3 import Web3
import json
import os

class BaseMod:
    description = """
    Base mod template with StringDelegate smart contract integration
    Compatible with Base and Ganache networks
    """
    
    # Contract ABI for StringDelegate
    CONTRACT_ABI = [
        {"inputs": [{"name": "_to", "type": "address"}, {"name": "_value", "type": "string"}], "name": "delegateString", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
        {"inputs": [{"name": "_address", "type": "address"}], "name": "getDelegatedStrings", "outputs": [{"name": "", "type": "string[]"}], "stateMutability": "view", "type": "function"},
        {"inputs": [{"name": "_to", "type": "address"}], "name": "getMyDelegationsTo", "outputs": [{"name": "", "type": "string[]"}], "stateMutability": "view", "type": "function"},
        {"inputs": [{"name": "_address", "type": "address"}], "name": "getDelegatedCount", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
        {"inputs": [{"name": "_address", "type": "address"}, {"name": "_index", "type": "uint256"}], "name": "getDelegatedStringAt", "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
        {"anonymous": False, "inputs": [{"indexed": True, "name": "from", "type": "address"}, {"indexed": True, "name": "to", "type": "address"}, {"indexed": False, "name": "value", "type": "string"}], "name": "StringDelegated", "type": "event"}
    ]
    
    # Network configurations
    NETWORKS = {
        "ganache": "http://127.0.0.1:8545",
        "base_mainnet": "https://mainnet.base.org",
        "base_sepolia": "https://sepolia.base.org"
    }
    
    def __init__(self, network="ganache", contract_address=None, private_key=None):
        """Initialize the mod with network and contract settings."""
        self.network = network
        self.rpc_url = self.NETWORKS.get(network, network)
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.contract_address = contract_address
        self.private_key = private_key
        self.contract = None
        
        if contract_address:
            self.contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(contract_address),
                abi=self.CONTRACT_ABI
            )
    
    def set_contract(self, contract_address):
        """Set the contract address after deployment."""
        self.contract_address = contract_address
        self.contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(contract_address),
            abi=self.CONTRACT_ABI
        )
    
    def delegate_string(self, to_address, string_value):
        """Delegate a string to a specific address."""
        if not self.contract:
            raise ValueError("Contract not set. Call set_contract() first.")
        if not self.private_key:
            raise ValueError("Private key required for transactions.")
        
        account = self.w3.eth.account.from_key(self.private_key)
        to_addr = Web3.to_checksum_address(to_address)
        
        tx = self.contract.functions.delegateString(to_addr, string_value).build_transaction({
            'from': account.address,
            'nonce': self.w3.eth.get_transaction_count(account.address),
            'gas': 200000,
            'gasPrice': self.w3.eth.gas_price
        })
        
        signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        
        return {'tx_hash': tx_hash.hex(), 'status': receipt['status']}
    
    def get_delegated_strings(self, address):
        """Get all strings delegated to an address."""
        if not self.contract:
            raise ValueError("Contract not set. Call set_contract() first.")
        
        addr = Web3.to_checksum_address(address)
        return self.contract.functions.getDelegatedStrings(addr).call()
    
    def get_delegated_count(self, address):
        """Get count of delegated strings for an address."""
        if not self.contract:
            raise ValueError("Contract not set. Call set_contract() first.")
        
        addr = Web3.to_checksum_address(address)
        return self.contract.functions.getDelegatedCount(addr).call()
    
    def get_delegated_string_at(self, address, index):
        """Get a specific delegated string by index."""
        if not self.contract:
            raise ValueError("Contract not set. Call set_contract() first.")
        
        addr = Web3.to_checksum_address(address)
        return self.contract.functions.getDelegatedStringAt(addr, index).call()
    
    def is_connected(self):
        """Check if connected to the network."""
        return self.w3.is_connected()
    
    def get_balance(self, address):
        """Get ETH balance of an address."""
        addr = Web3.to_checksum_address(address)
        return self.w3.eth.get_balance(addr)
    
    def multiply(self, a, b):
        """Multiply two numbers and return the result."""
        return a * b
    
    def get_bittenso_price(self):
        """Fetch the price of Bittenso cryptocurrency."""
        import requests
        try:
            response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=bittenso&vs_currencies=usd')
            response.raise_for_status()
            data = response.json()
            return data.get('bittenso', {}).get('usd', 'Price not available')
        except Exception as e:
            return f"Error fetching price: {str(e)}"
