from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime


class BaseExchange(ABC):
    """Base exchange class that all exchanges must conform to"""
    
    def __init__(self):
        self.default_base_token = "ETH"  # ethereum
        self.default_quote_token = "USDT"
    
    @abstractmethod
    def swap(
        self,
        from_token: str,
        to_token: str,
        amount: float,
        slippage: float = 0.01
    ) -> Dict[str, Any]:
        """Execute a token swap
        
        Args:
            from_token: Token to swap from
            to_token: Token to swap to
            amount: Amount to swap
            slippage: Acceptable slippage percentage
            
        Returns:
            Dict containing swap results with keys:
            - success: bool
            - amount_out: float
            - tx_hash: str
            - price: float
        """
        pass
    
    @abstractmethod
    def get_price(
        self,
        base_token: Optional[str] = None,
        quote_token: Optional[str] = None,
        timestamp: Optional[datetime] = None
    ) -> float:
        """Get current or historical price for a token pair
        
        Args:
            base_token: Base token symbol (defaults to ETH)
            quote_token: Quote token symbol (defaults to USDT)
            timestamp: Optional timestamp for historical price
            
        Returns:
            Price as float
        """
        pass
    
    @abstractmethod
    def search_tokens(
        self,
        query: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search for tokens by name or symbol
        
        Args:
            query: Search query string
            limit: Maximum number of results
            
        Returns:
            List of token info dicts with keys:
            - symbol: str
            - name: str
            - address: str
            - decimals: int
        """
        pass
    
    def get_default_price(self, timestamp: Optional[datetime] = None) -> float:
        """Get ETH/USDT price (default pair)"""
        return self.get_price(
            base_token=self.default_base_token,
            quote_token=self.default_quote_token,
            timestamp=timestamp
        )
