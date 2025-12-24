#!/usr/bin/env python3
"""
Deploy StringDelegate contract to Ganache or Base network
"""
from web3 import Web3
from solcx import compile_standard, install_solc
import json
import os

# Install solc
install_solc('0.8.19')

def deploy_contract(network='ganache', private_key=None):
    """Deploy the StringDelegate contract."""
    
    NETWORKS = {
        "ganache": "http://127.0.0.1:8545",
        "base_mainnet": "https://mainnet.base.org",
        "base_sepolia": "https://sepolia.base.org"
    }
    
    w3 = Web3(Web3.HTTPProvider(NETWORKS.get(network, network)))
    
    if not w3.is_connected():
        raise Exception(f"Failed to connect to {network}")
    
    # Read contract source
    contract_path = os.path.join(os.path.dirname(__file__), 'contracts', 'StringDelegate.sol')
    with open(contract_path, 'r') as f:
        contract_source = f.read()
    
    # Compile contract
    compiled = compile_standard({
        "language": "Solidity",
        "sources": {"StringDelegate.sol": {"content": contract_source}},
        "settings": {
            "outputSelection": {
                "*": {"*": ["abi", "metadata", "evm.bytecode", "evm.sourceMap"]}
            }
        }
    }, solc_version='0.8.19')
    
    abi = compiled['contracts']['StringDelegate.sol']['StringDelegate']['abi']
    bytecode = compiled['contracts']['StringDelegate.sol']['StringDelegate']['evm']['bytecode']['object']
    
    # Deploy
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    
    if network == 'ganache':
        account = w3.eth.accounts[0]
        tx_hash = Contract.constructor().transact({'from': account})
    else:
        if not private_key:
            raise ValueError("Private key required for non-Ganache networks")
        account = w3.eth.account.from_key(private_key)
        tx = Contract.constructor().build_transaction({
            'from': account.address,
            'nonce': w3.eth.get_transaction_count(account.address),
            'gas': 2000000,
            'gasPrice': w3.eth.gas_price
        })
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
    
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    
    print(f"Contract deployed at: {receipt.contractAddress}")
    print(f"Transaction hash: {tx_hash.hex()}")
    
    return receipt.contractAddress, abi

if __name__ == '__main__':
    import sys
    network = sys.argv[1] if len(sys.argv) > 1 else 'ganache'
    private_key = sys.argv[2] if len(sys.argv) > 2 else None
    deploy_contract(network, private_key)
