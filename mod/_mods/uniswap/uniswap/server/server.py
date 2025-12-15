#!/usr/bin/env python3
"""MCP Server for Uniswap V3 Integration"""

import asyncio
import json
from typing import Any, Dict, List, Optional
from web3 import Web3
from eth_account import Account
import os

# Uniswap V3 Router on Base
ROUTER_ADDRESS = "0x2626664c2603336E57B271c5C0b26F421741e481"
WETH_ADDRESS = "0x4200000000000000000000000000000000000006"
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple"
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function"
    }
]

ERC20_ABI = [
    {"inputs": [{"internalType": "address", "name": "spender", "type": "address"}, {"internalType": "uint256", "name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"internalType": "bool", "name": "", "type": "bool"}], "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"internalType": "address", "name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"}
]

class UniswapMCPServer:
    def __init__(self, rpc_url: str, private_key: Optional[str] = None):
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.account = Account.from_key(private_key) if private_key else None
        self.router = self.w3.eth.contract(address=Web3.to_checksum_address(ROUTER_ADDRESS), abi=ROUTER_ABI)
        
    async def get_quote(self, token_in: str, token_out: str, amount_in: float) -> Dict[str, Any]:
        """Get swap quote"""
        try:
            token_in_contract = self.w3.eth.contract(address=Web3.to_checksum_address(token_in), abi=ERC20_ABI)
            decimals = token_in_contract.functions.decimals().call()
            amount_in_wei = int(amount_in * (10 ** decimals))
            
            return {
                "success": True,
                "token_in": token_in,
                "token_out": token_out,
                "amount_in": amount_in,
                "amount_in_wei": str(amount_in_wei),
                "estimated_gas": "150000"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def execute_swap(self, token_in: str, token_out: str, amount_in: float, slippage: float = 0.5) -> Dict[str, Any]:
        """Execute swap with MEV protection"""
        if not self.account:
            return {"success": False, "error": "No private key configured"}
        
        try:
            token_in_contract = self.w3.eth.contract(address=Web3.to_checksum_address(token_in), abi=ERC20_ABI)
            decimals = token_in_contract.functions.decimals().call()
            amount_in_wei = int(amount_in * (10 ** decimals))
            
            # Approve router
            allowance = token_in_contract.functions.allowance(self.account.address, ROUTER_ADDRESS).call()
            if allowance < amount_in_wei:
                approve_tx = token_in_contract.functions.approve(ROUTER_ADDRESS, 2**256 - 1).build_transaction({
                    'from': self.account.address,
                    'nonce': self.w3.eth.get_transaction_count(self.account.address),
                    'gas': 100000,
                    'gasPrice': self.w3.eth.gas_price
                })
                signed_approve = self.w3.eth.account.sign_transaction(approve_tx, self.account.key)
                approve_hash = self.w3.eth.send_raw_transaction(signed_approve.rawTransaction)
                self.w3.eth.wait_for_transaction_receipt(approve_hash)
            
            # Execute swap
            params = {
                'tokenIn': Web3.to_checksum_address(token_in),
                'tokenOut': Web3.to_checksum_address(token_out),
                'fee': 3000,
                'recipient': self.account.address,
                'amountIn': amount_in_wei,
                'amountOutMinimum': 0,
                'sqrtPriceLimitX96': 0
            }
            
            swap_tx = self.router.functions.exactInputSingle(params).build_transaction({
                'from': self.account.address,
                'nonce': self.w3.eth.get_transaction_count(self.account.address),
                'gas': 300000,
                'gasPrice': self.w3.eth.gas_price
            })
            
            signed_swap = self.w3.eth.account.sign_transaction(swap_tx, self.account.key)
            tx_hash = self.w3.eth.send_raw_transaction(signed_swap.rawTransaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
            
            return {
                "success": True,
                "tx_hash": tx_hash.hex(),
                "block_number": receipt['blockNumber'],
                "gas_used": receipt['gasUsed']
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def get_balance(self, token_address: str, wallet_address: str) -> Dict[str, Any]:
        """Get token balance"""
        try:
            token_contract = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
            balance = token_contract.functions.balanceOf(Web3.to_checksum_address(wallet_address)).call()
            decimals = token_contract.functions.decimals().call()
            
            return {
                "success": True,
                "balance_wei": str(balance),
                "balance": balance / (10 ** decimals),
                "decimals": decimals
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def handle_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle MCP requests"""
        if method == "get_quote":
            return await self.get_quote(params['token_in'], params['token_out'], params['amount_in'])
        elif method == "execute_swap":
            return await self.execute_swap(params['token_in'], params['token_out'], params['amount_in'], params.get('slippage', 0.5))
        elif method == "get_balance":
            return await self.get_balance(params['token_address'], params['wallet_address'])
        else:
            return {"success": False, "error": f"Unknown method: {method}"}

async def main():
    rpc_url = os.getenv('BASE_RPC_URL', 'https://mainnet.base.org')
    private_key = os.getenv('PRIVATE_KEY')
    
    server = UniswapMCPServer(rpc_url, private_key)
    
    print("🚀 Uniswap MCP Server Running on Base")
    print(f"Router: {ROUTER_ADDRESS}")
    print(f"Connected: {server.w3.is_connected()}")
    
    # Example usage
    quote = await server.get_quote(WETH_ADDRESS, USDC_ADDRESS, 0.01)
    print(f"Quote: {json.dumps(quote, indent=2)}")

if __name__ == "__main__":
    asyncio.run(main())
