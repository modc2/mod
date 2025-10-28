"""Uniswap MCP - Complete Uniswap V2/V3 Integration"""
import json
from typing import Any, Dict, List, Optional
from web3 import Web3
from eth_typing import Address


class UniswapMCP:
    """Complete Uniswap MCP class for V2 and V3 operations"""
    
    # Uniswap V2 Router Address (Ethereum Mainnet)
    UNISWAP_V2_ROUTER = "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D"
    # Uniswap V3 Router Address (Ethereum Mainnet)
    UNISWAP_V3_ROUTER = "0xE592427A0AEce92De3Edee1F18E0157C05861564"
    # Uniswap V2 Factory
    UNISWAP_V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
    # Uniswap V3 Factory
    UNISWAP_V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
    
    def __init__(self, rpc_url: str, private_key: Optional[str] = None):
        """Initialize Uniswap MCP
        
        Args:
            rpc_url: Ethereum RPC endpoint
            private_key: Private key for signing transactions (optional)
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.private_key = private_key
        self.account = self.w3.eth.account.from_key(private_key) if private_key else None
        
    def get_price(self, token_in: str, token_out: str, amount_in: int, version: str = "v2") -> Dict[str, Any]:
        """Get price quote for token swap
        
        Args:
            token_in: Input token address
            token_out: Output token address
            amount_in: Amount of input token (in wei)
            version: Uniswap version ('v2' or 'v3')
            
        Returns:
            Dict with price information
        """
        try:
            if version == "v2":
                return self._get_price_v2(token_in, token_out, amount_in)
            else:
                return self._get_price_v3(token_in, token_out, amount_in)
        except Exception as e:
            return {"error": str(e), "success": False}
    
    def _get_price_v2(self, token_in: str, token_out: str, amount_in: int) -> Dict[str, Any]:
        """Get V2 price quote"""
        router_abi = json.loads('[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"}]')
        router = self.w3.eth.contract(address=self.UNISWAP_V2_ROUTER, abi=router_abi)
        
        path = [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)]
        amounts = router.functions.getAmountsOut(amount_in, path).call()
        
        return {
            "success": True,
            "amount_in": amount_in,
            "amount_out": amounts[-1],
            "path": path,
            "version": "v2"
        }
    
    def _get_price_v3(self, token_in: str, token_out: str, amount_in: int) -> Dict[str, Any]:
        """Get V3 price quote"""
        quoter_abi = json.loads('[{"inputs":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"name":"quoteExactInputSingle","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}]')
        quoter_address = "0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6"
        quoter = self.w3.eth.contract(address=quoter_address, abi=quoter_abi)
        
        fee = 3000  # 0.3% fee tier
        amount_out = quoter.functions.quoteExactInputSingle(
            Web3.to_checksum_address(token_in),
            Web3.to_checksum_address(token_out),
            fee,
            amount_in,
            0
        ).call()
        
        return {
            "success": True,
            "amount_in": amount_in,
            "amount_out": amount_out,
            "fee": fee,
            "version": "v3"
        }
    
    def swap(self, token_in: str, token_out: str, amount_in: int, 
             min_amount_out: int, deadline: int, version: str = "v2") -> Dict[str, Any]:
        """Execute token swap
        
        Args:
            token_in: Input token address
            token_out: Output token address
            amount_in: Amount of input token
            min_amount_out: Minimum acceptable output amount (slippage protection)
            deadline: Transaction deadline timestamp
            version: Uniswap version ('v2' or 'v3')
            
        Returns:
            Transaction receipt
        """
        if not self.account:
            return {"error": "No private key provided", "success": False}
        
        try:
            if version == "v2":
                return self._swap_v2(token_in, token_out, amount_in, min_amount_out, deadline)
            else:
                return self._swap_v3(token_in, token_out, amount_in, min_amount_out, deadline)
        except Exception as e:
            return {"error": str(e), "success": False}
    
    def _swap_v2(self, token_in: str, token_out: str, amount_in: int, 
                 min_amount_out: int, deadline: int) -> Dict[str, Any]:
        """Execute V2 swap"""
        router_abi = json.loads('[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMin","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"},{"internalType":"address","name":"to","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"}],"name":"swapExactTokensForTokens","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"nonpayable","type":"function"}]')
        router = self.w3.eth.contract(address=self.UNISWAP_V2_ROUTER, abi=router_abi)
        
        path = [Web3.to_checksum_address(token_in), Web3.to_checksum_address(token_out)]
        
        txn = router.functions.swapExactTokensForTokens(
            amount_in,
            min_amount_out,
            path,
            self.account.address,
            deadline
        ).build_transaction({
            'from': self.account.address,
            'gas': 250000,
            'gasPrice': self.w3.eth.gas_price,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        
        signed_txn = self.w3.eth.account.sign_transaction(txn, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        
        return {
            "success": True,
            "tx_hash": tx_hash.hex(),
            "receipt": dict(receipt),
            "version": "v2"
        }
    
    def _swap_v3(self, token_in: str, token_out: str, amount_in: int, 
                 min_amount_out: int, deadline: int) -> Dict[str, Any]:
        """Execute V3 swap"""
        router_abi = json.loads('[{"inputs":[{"components":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"address","name":"recipient","type":"address"},{"internalType":"uint256","name":"deadline","type":"uint256"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint256","name":"amountOutMinimum","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"internalType":"struct ISwapRouter.ExactInputSingleParams","name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')
        router = self.w3.eth.contract(address=self.UNISWAP_V3_ROUTER, abi=router_abi)
        
        params = {
            'tokenIn': Web3.to_checksum_address(token_in),
            'tokenOut': Web3.to_checksum_address(token_out),
            'fee': 3000,
            'recipient': self.account.address,
            'deadline': deadline,
            'amountIn': amount_in,
            'amountOutMinimum': min_amount_out,
            'sqrtPriceLimitX96': 0
        }
        
        txn = router.functions.exactInputSingle(params).build_transaction({
            'from': self.account.address,
            'gas': 300000,
            'gasPrice': self.w3.eth.gas_price,
            'nonce': self.w3.eth.get_transaction_count(self.account.address)
        })
        
        signed_txn = self.w3.eth.account.sign_transaction(txn, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        
        return {
            "success": True,
            "tx_hash": tx_hash.hex(),
            "receipt": dict(receipt),
            "version": "v3"
        }
    
    def get_pool_info(self, token_a: str, token_b: str, version: str = "v2") -> Dict[str, Any]:
        """Get pool information
        
        Args:
            token_a: First token address
            token_b: Second token address
            version: Uniswap version
            
        Returns:
            Pool information
        """
        try:
            if version == "v2":
                return self._get_pool_info_v2(token_a, token_b)
            else:
                return self._get_pool_info_v3(token_a, token_b)
        except Exception as e:
            return {"error": str(e), "success": False}
    
    def _get_pool_info_v2(self, token_a: str, token_b: str) -> Dict[str, Any]:
        """Get V2 pool info"""
        factory_abi = json.loads('[{"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"}],"name":"getPair","outputs":[{"internalType":"address","name":"pair","type":"address"}],"stateMutability":"view","type":"function"}]')
        factory = self.w3.eth.contract(address=self.UNISWAP_V2_FACTORY, abi=factory_abi)
        
        pair_address = factory.functions.getPair(
            Web3.to_checksum_address(token_a),
            Web3.to_checksum_address(token_b)
        ).call()
        
        return {
            "success": True,
            "pair_address": pair_address,
            "token_a": token_a,
            "token_b": token_b,
            "version": "v2"
        }
    
    def _get_pool_info_v3(self, token_a: str, token_b: str, fee: int = 3000) -> Dict[str, Any]:
        """Get V3 pool info"""
        factory_abi = json.loads('[{"inputs":[{"internalType":"address","name":"tokenA","type":"address"},{"internalType":"address","name":"tokenB","type":"address"},{"internalType":"uint24","name":"fee","type":"uint24"}],"name":"getPool","outputs":[{"internalType":"address","name":"pool","type":"address"}],"stateMutability":"view","type":"function"}]')
        factory = self.w3.eth.contract(address=self.UNISWAP_V3_FACTORY, abi=factory_abi)
        
        pool_address = factory.functions.getPool(
            Web3.to_checksum_address(token_a),
            Web3.to_checksum_address(token_b),
            fee
        ).call()
        
        return {
            "success": True,
            "pool_address": pool_address,
            "token_a": token_a,
            "token_b": token_b,
            "fee": fee,
            "version": "v3"
        }
