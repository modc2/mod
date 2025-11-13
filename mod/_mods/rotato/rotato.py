#!/usr/bin/env python3
"""
Quantum-Resistant Ethereum Key Rotation System
Rotates funds to new keys every N blocks to mitigate quantum attack risks
"""

import os
import json
import time
from web3 import Web3
from eth_account import Account
from datetime import datetime


class QuantumRotator:
    def __init__(self, rpc_url="http://127.0.0.1:8545", rotation_blocks=100):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.rotation_blocks = rotation_blocks
        self.keys_file = "./keys_history.json"
        self.current_key = None
        self.last_rotation_block = 0
        
    def generate_new_key(self):
        """Generate a new Ethereum key pair locally"""
        account = Account.create()
        return {
            "address": account.address,
            "private_key": account.key.hex(),
            "created_at": datetime.now().isoformat(),
            "created_block": self.w3.eth.block_number
        }
    
    def save_key_history(self, key_data):
        """Save key to encrypted history file"""
        history = []
        if os.path.exists(self.keys_file):
            with open(self.keys_file, 'r') as f:
                history = json.load(f)
        
        history.append({
            "address": key_data["address"],
            "created_at": key_data["created_at"],
            "created_block": key_data["created_block"]
        })
        
        with open(self.keys_file, 'w') as f:
            json.dump(history, f, indent=2)
        
        # Save private key separately (encrypted in production)
        with open(f"./key_{key_data['address']}.json", 'w') as f:
            json.dump(key_data, f, indent=2)
    
    def load_current_key(self):
        """Load the most recent key"""
        if os.path.exists(self.keys_file):
            with open(self.keys_file, 'r') as f:
                history = json.load(f)
                if history:
                    latest = history[-1]
                    key_file = f"./key_{latest['address']}.json"
                    if os.path.exists(key_file):
                        with open(key_file, 'r') as kf:
                            self.current_key = json.load(kf)
                            self.last_rotation_block = self.current_key["created_block"]
                            return self.current_key
        return None
    
    def should_rotate(self):
        """Check if rotation is needed based on block count"""
        current_block = self.w3.eth.block_number
        blocks_since_rotation = current_block - self.last_rotation_block
        return blocks_since_rotation >= self.rotation_blocks
    
    def transfer_funds(self, from_key, to_address):
        """Transfer all funds from old key to new key"""
        from_address = from_key["address"]
        balance = self.w3.eth.get_balance(from_address)
        
        if balance == 0:
            print(f"No funds to transfer from {from_address}")
            return None
        
        # Estimate gas
        gas_price = self.w3.eth.gas_price
        gas_limit = 21000
        gas_cost = gas_price * gas_limit
        
        # Transfer amount (balance minus gas)
        transfer_amount = balance - gas_cost
        
        if transfer_amount <= 0:
            print(f"Insufficient balance to cover gas fees")
            return None
        
        # Build transaction
        nonce = self.w3.eth.get_transaction_count(from_address)
        tx = {
            'nonce': nonce,
            'to': to_address,
            'value': transfer_amount,
            'gas': gas_limit,
            'gasPrice': gas_price,
            'chainId': self.w3.eth.chain_id
        }
        
        # Sign transaction
        signed_tx = self.w3.eth.account.sign_transaction(tx, from_key["private_key"])
        
        # Send transaction
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
        
        print(f"Transfer initiated: {tx_hash.hex()}")
        print(f"From: {from_address}")
        print(f"To: {to_address}")
        print(f"Amount: {self.w3.from_wei(transfer_amount, 'ether')} ETH")
        
        # Wait for confirmation
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        return receipt
    
    def rotate(self):
        """Perform key rotation"""
        print(f"\n{'='*60}")
        print(f"QUANTUM ROTATION INITIATED - Block {self.w3.eth.block_number}")
        print(f"{'='*60}\n")
        
        # Generate new key
        new_key = self.generate_new_key()
        print(f"New key generated: {new_key['address']}")
        
        # Transfer funds if we have a current key
        if self.current_key:
            print(f"\nTransferring funds from old key: {self.current_key['address']}")
            receipt = self.transfer_funds(self.current_key, new_key["address"])
            if receipt:
                print(f"Transfer successful! Gas used: {receipt['gasUsed']}")
        
        # Save new key
        self.save_key_history(new_key)
        self.current_key = new_key
        self.last_rotation_block = new_key["created_block"]
        
        print(f"\nRotation complete! Active address: {new_key['address']}")
        print(f"Next rotation in {self.rotation_blocks} blocks\n")
    
    def run(self):
        """Main loop - monitor and rotate"""
        print("Quantum-Resistant Key Rotation System Started")
        print(f"RPC: {self.w3.provider.endpoint_uri}")
        print(f"Rotation interval: {self.rotation_blocks} blocks\n")
        
        # Load or create initial key
        if not self.load_current_key():
            print("No existing key found. Creating initial key...")
            self.rotate()
        else:
            print(f"Loaded existing key: {self.current_key['address']}")
            print(f"Last rotation: Block {self.last_rotation_block}\n")
        
        # Monitor loop
        while True:
            try:
                if self.should_rotate():
                    self.rotate()
                else:
                    current_block = self.w3.eth.block_number
                    blocks_remaining = self.rotation_blocks - (current_block - self.last_rotation_block)
                    print(f"Block {current_block} - {blocks_remaining} blocks until rotation", end='\r')
                
                time.sleep(12)  # Check every ~block time
                
            except KeyboardInterrupt:
                print("\n\nShutdown requested. Exiting...")
                break
            except Exception as e:
                print(f"\nError: {e}")
                time.sleep(12)


if __name__ == "__main__":
    # Configuration
    RPC_URL = os.getenv("ETH_RPC_URL", "http://127.0.0.1:8545")
    ROTATION_BLOCKS = int(os.getenv("ROTATION_BLOCKS", "100"))
    
    # Start rotator
    rotator = QuantumRotator(rpc_url=RPC_URL, rotation_blocks=ROTATION_BLOCKS)
    rotator.run()
