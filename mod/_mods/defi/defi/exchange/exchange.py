from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, List
from datetime import datetime


class BaseExchange(ABC):
    """Base class for all exchange implementations"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
    
    @abstractmethod
    def swap(
        self,
        token_in: str,
        token_out: str,
        amount_in: float,
        slippage: float = 0.01,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Execute a token swap
        
        Args:
            token_in: Input token address/symbol
            token_out: Output token address/symbol
            amount_in: Amount of input token
            slippage: Maximum slippage tolerance (default 1%)
            **kwargs: Additional exchange-specific parameters
            
        Returns:
            Dict containing:
                - success: bool
                - amount_out: float
                - tx_hash: str
                - price: float
                - gas_used: Optional[float]
        """
        pass
    
    @abstractmethod
    def get_price(
        self,
        token_in: str,
        token_out: str,
        amount_in: float = 1.0,
        timestamp: Optional[datetime] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Get historical or current price for a token pair
        
        Args:
            token_in: Input token address/symbol
            token_out: Output token address/symbol
            amount_in: Amount of input token (default 1.0)
            timestamp: Historical timestamp (None for current price)
            **kwargs: Additional exchange-specific parameters
            
        Returns:
            Dict containing:
                - price: float (token_out per token_in)
                - amount_out: float
                - timestamp: datetime
                - liquidity: Optional[float]
                - volume_24h: Optional[float]
        """
        pass
    
    def get_historical_prices(
        self,
        token_in: str,
        token_out: str,
        start_time: datetime,
        end_time: datetime,
        interval: str = '1h',
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Get historical price data for a token pair
        
        Args:
            token_in: Input token address/symbol
            token_out: Output token address/symbol
            start_time: Start of time range
            end_time: End of time range
            interval: Time interval (e.g., '1m', '5m', '1h', '1d')
            **kwargs: Additional exchange-specific parameters
            
        Returns:
            List of price data dictionaries
        """
        # Default implementation - can be overridden
        return []
