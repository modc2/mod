#!/usr/bin/env python3
"""
Bitchain - A blockchain state machine stored in Bitcoin testnet via OP_RETURN metadata
"""

import os
import json
import time
import hashlib
from typing import Dict, List, Any, Optional
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException


class BitchainState:
    """Represents a state in the bitchain"""
    
    def __init__(self, state_id: str, data: Dict[str, Any], prev_txid: Optional[str] = None):
        self.state_id = state_id
        self.data = data
        self.prev_txid = prev_txid
        self.timestamp = int(time.time())
    
    def to_metadata(self) -> str:
        """Convert state to metadata string for OP_RETURN"""
        payload = {
            'id': self.state_id,
            'data': self.data,
            'prev': self.prev_txid,
            'ts': self.timestamp
        }
        return json.dumps(payload)
    
    @classmethod
    def from_metadata(cls, metadata: str) -> 'BitchainState':
        """Parse state from metadata string"""
        payload = json.loads(metadata)
        state = cls(
            state_id=payload['id'],
            data=payload['data'],
            prev_txid=payload.get('prev')
        )
        state.timestamp = payload['ts']
        return state
    
    def hash(self) -> str:
        """Calculate hash of the state"""
        content = f"{self.state_id}{json.dumps(self.data)}{self.prev_txid}{self.timestamp}"
        return hashlib.sha256(content.encode()).hexdigest()


class Bitchain:
    """Main Bitchain implementation - stores state chain in Bitcoin testnet"""
    
    def __init__(self, rpc_user: str, rpc_password: str, rpc_host: str = 'localhost', rpc_port: int = 18332):
        self.rpc_connection = AuthServiceProxy(
            f"http://{rpc_user}:{rpc_password}@{rpc_host}:{rpc_port}"
        )
        self.states: List[BitchainState] = []
        self.last_txid: Optional[str] = None
    
    def wait_for_node(self, max_attempts: int = 30):
        """Wait for Bitcoin node to be ready"""
        print("Waiting for Bitcoin node to be ready...")
        for i in range(max_attempts):
            try:
                self.rpc_connection.getblockchaininfo()
                print("Bitcoin node is ready!")
                return True
            except Exception as e:
                print(f"Attempt {i+1}/{max_attempts}: Node not ready yet...")
                time.sleep(2)
        raise Exception("Bitcoin node failed to start")
    
    def create_op_return_tx(self, metadata: str) -> str:
        """Create a transaction with OP_RETURN output containing metadata"""
        # Ensure metadata fits in OP_RETURN (80 bytes max)
        metadata_hex = metadata.encode('utf-8').hex()
        if len(metadata_hex) > 160:  # 80 bytes = 160 hex chars
            raise ValueError(f"Metadata too large: {len(metadata_hex)/2} bytes (max 80)")
        
        # Get a new address for change
        change_address = self.rpc_connection.getnewaddress()
        
        # Get unspent outputs
        unspent = self.rpc_connection.listunspent(1, 9999999)
        if not unspent:
            raise Exception("No unspent outputs available. Mine some blocks first!")
        
        # Use first unspent output
        utxo = unspent[0]
        
        # Create raw transaction with OP_RETURN
        inputs = [{'txid': utxo['txid'], 'vout': utxo['vout']}]
        outputs = {
            'data': metadata_hex,
            change_address: float(utxo['amount']) - 0.0001  # Small fee
        }
        
        raw_tx = self.rpc_connection.createrawtransaction(inputs, outputs)
        signed_tx = self.rpc_connection.signrawtransactionwithwallet(raw_tx)
        
        if not signed_tx['complete']:
            raise Exception("Failed to sign transaction")
        
        # Send transaction
        txid = self.rpc_connection.sendrawtransaction(signed_tx['hex'])
        print(f"Transaction sent: {txid}")
        
        return txid
    
    def add_state(self, state_id: str, data: Dict[str, Any]) -> str:
        """Add a new state to the bitchain"""
        state = BitchainState(state_id, data, self.last_txid)
        metadata = state.to_metadata()
        
        print(f"\nAdding state: {state_id}")
        print(f"Data: {data}")
        print(f"Previous TX: {self.last_txid}")
        
        txid = self.create_op_return_tx(metadata)
        self.last_txid = txid
        self.states.append(state)
        
        return txid
    
    def get_state_from_tx(self, txid: str) -> Optional[BitchainState]:
        """Retrieve state from a transaction"""
        try:
            raw_tx = self.rpc_connection.getrawtransaction(txid, True)
            
            # Find OP_RETURN output
            for vout in raw_tx['vout']:
                if vout['scriptPubKey']['type'] == 'nulldata':
                    hex_data = vout['scriptPubKey']['asm'].split(' ')[1]
                    metadata = bytes.fromhex(hex_data).decode('utf-8')
                    return BitchainState.from_metadata(metadata)
            
            return None
        except Exception as e:
            print(f"Error retrieving state: {e}")
            return None
    
    def get_chain(self) -> List[BitchainState]:
        """Get the full state chain"""
        return self.states
    
    def verify_chain(self) -> bool:
        """Verify the integrity of the state chain"""
        for i in range(1, len(self.states)):
            if self.states[i].prev_txid != self.last_txid:
                return False
        return True
    
    def mine_blocks(self, num_blocks: int = 1):
        """Mine blocks on testnet (for testing)"""
        try:
            address = self.rpc_connection.getnewaddress()
            self.rpc_connection.generatetoaddress(num_blocks, address)
            print(f"Mined {num_blocks} blocks")
        except Exception as e:
            print(f"Mining error: {e}")


class BaseMod:
    description = """
    Bitchain - A blockchain state machine implementation that stores states in Bitcoin testnet
    via OP_RETURN metadata. Each state is linked to the previous one, forming a verifiable chain.
    """


def demo():
    """Demo the bitchain functionality"""
    print("=" * 60)
    print("BITCHAIN - State Chain on Bitcoin Testnet")
    print("=" * 60)
    
    # Initialize bitchain
    rpc_user = os.getenv('BITCOIN_RPC_USER', 'bitcoinrpc')
    rpc_password = os.getenv('BITCOIN_RPC_PASSWORD', 'bitcoinrpcpassword')
    rpc_host = os.getenv('BITCOIN_RPC_HOST', 'localhost')
    rpc_port = int(os.getenv('BITCOIN_RPC_PORT', '18332'))
    
    bitchain = Bitchain(rpc_user, rpc_password, rpc_host, rpc_port)
    
    # Wait for node
    bitchain.wait_for_node()
    
    # Mine initial blocks to get some coins
    print("\nMining initial blocks...")
    bitchain.mine_blocks(101)  # Need 101 blocks for coinbase maturity
    
    # Add states to the chain
    print("\n" + "=" * 60)
    print("Adding states to bitchain...")
    print("=" * 60)
    
    tx1 = bitchain.add_state('state_001', {'action': 'init', 'value': 100})
    bitchain.mine_blocks(1)
    
    tx2 = bitchain.add_state('state_002', {'action': 'update', 'value': 150})
    bitchain.mine_blocks(1)
    
    tx3 = bitchain.add_state('state_003', {'action': 'transfer', 'from': 'A', 'to': 'B', 'amount': 50})
    bitchain.mine_blocks(1)
    
    # Retrieve and verify states
    print("\n" + "=" * 60)
    print("Retrieving states from blockchain...")
    print("=" * 60)
    
    for i, txid in enumerate([tx1, tx2, tx3], 1):
        state = bitchain.get_state_from_tx(txid)
        if state:
            print(f"\nState {i}:")
            print(f"  ID: {state.state_id}")
            print(f"  Data: {state.data}")
            print(f"  Previous TX: {state.prev_txid}")
            print(f"  Timestamp: {state.timestamp}")
            print(f"  Hash: {state.hash()}")
    
    # Verify chain integrity
    print("\n" + "=" * 60)
    print(f"Chain verification: {'VALID' if bitchain.verify_chain() else 'INVALID'}")
    print("=" * 60)
    
    print("\nBitchain demo completed successfully!")


if __name__ == '__main__':
    demo()
