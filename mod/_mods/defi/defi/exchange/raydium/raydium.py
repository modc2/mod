from typing import Optional, Dict, Any, List
from datetime import datetime
import requests
from .exchange import BaseExchange


class RaydiumExchange(BaseExchange):
    """Raydium DEX implementation conforming to BaseExchange interface"""
    
    def __init__(self, rpc_url: Optional[str] = None):
        super().__init__()
        self.rpc_url = rpc_url or "https://api.mainnet-beta.solana.com"
        self.api_base = "https://api.raydium.io/v2"
        
    def swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        slippage: float = 0.01
    ) -> Dict[str, Any]:
        """Execute swap on Raydium"""
        try:
            # Get pool info for the pair
            pools = self._get_pools(from_token, to_token)
            if not pools:
                return {
                    "success": False,
                    "error": f"No pool found for {from_token}/{to_token}",
                    "amount_out": 0,
                    "tx_hash": "",
                    "price": 0
                }
            
            pool = pools[0]
            
            # Calculate expected output with slippage
            price = self.get_price(from_token, to_token)
            expected_out = amount * price
            min_out = expected_out * (1 - slippage)
            
            # In production, this would execute the actual swap transaction
            # For now, return simulated result
            return {
                "success": True,
                "amount_out": expected_out,
                "min_amount_out": min_out,
                "tx_hash": "simulated_tx_hash",
                "price": price,
                "pool_id": pool.get("id", "")
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "amount_out": 0,
                "tx_hash": "",
                "price": 0
            }
    
    def get_price(
        self,
        base_token: Optional[str] = None,
        quote_token: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ) -> float:
        """Get current or historical price from Raydium"""
        base = base_token or self.default_base_token
        quote = quote_token or self.default_quote_token
        
        try:
            if timestamp:
                # Historical price lookup
                return self._get_historical_price(base, quote, timestamp)
            else:
                # Current price
                return self._get_current_price(base, quote)
                
        except Exception as e:
            print(f"Error getting price: {e}")
            return 0.0
    
    def _get_current_price(self, base_token: str, quote_token: str) -> float:
        """Get current price from Raydium API"""
        try:
            # Get pool info
            pools = self._get_pools(base_token, quote_token)
            if not pools:
                return 0.0
            
            pool = pools[0]
            # Extract price from pool data
            price = float(pool.get("price", 0))
            return price
            
        except Exception as e:
            print(f"Error fetching current price: {e}")
            return 0.0
    
    def _get_historical_price(
        self,
        base_token: str,
        quote_token: str,
        timestamp: datetime
    ) -> float:
        """Get historical price from Raydium"""
        try:
            # Convert timestamp to unix time
            unix_time = int(timestamp.timestamp())
            
            # Fetch historical data
            url = f"{self.api_base}/main/price-history"
            params = {
                "base": base_token,
                "quote": quote_token,
                "timestamp": unix_time
            }
            
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return float(data.get("price", 0))
            
            return 0.0
            
        except Exception as e:
            print(f"Error fetching historical price: {e}")
            return 0.0
    
    def search_tokens(
        self,
        query: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for tokens on Raydium"""
        try:
            url = f"{self.api_base}/main/token-list"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return []
            
            tokens = response.json().get("tokens", [])
            
            # Filter tokens by query
            query_lower = query.lower()
            filtered = [
                token for token in tokens
                if query_lower in token.get("symbol", "").lower()
                or query_lower in token.get("name", "").lower()
            ]
            
            # Format results
            results = []
            for token in filtered[:limit]:
                results.append({
                    "symbol": token.get("symbol", ""),
                    "name": token.get("name", ""),
                    "address": token.get("address", ""),
                    "decimals": token.get("decimals", 9)
                })
            
            return results
            
        except Exception as e:
            print(f"Error searching tokens: {e}")
            return []
    
    def _get_pools(
        self,
        token_a: str,
        token_b: str
    ) -> List[Dict[str, Any]]:
        """Get available pools for token pair"""
        try:
            url = f"{self.api_base}/main/pairs"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return []
            
            pairs = response.json().get("pairs", [])
            
            # Filter for matching pairs
            matching = [
                pair for pair in pairs
                if (pair.get("base_symbol") == token_a and pair.get("quote_symbol") == token_b)
                or (pair.get("base_symbol") == token_b and pair.get("quote_symbol") == token_a)
            ]
            
            return matching
            
        except Exception as e:
            print(f"Error fetching pools: {e}")
            return []
