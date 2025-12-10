import requests
import hmac
import hashlib
import json
from typing import Dict, Any, Optional, List
import time

class HyperliquidMod:
    """Hyperliquid API Module - Complete interface for Hyperliquid DEX"""
    
    description = "Hyperliquid API Module - Fetch market data and execute trades on Hyperliquid DEX"
    
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None, testnet: bool = False):
        """Initialize Hyperliquid API client
        
        Args:
            api_key: API key for authenticated requests
            api_secret: API secret for signing requests
            testnet: Use testnet endpoints if True
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.hyperliquid-testnet.xyz" if testnet else "https://api.hyperliquid.xyz"
        self.info_url = f"{self.base_url}/info"
        self.exchange_url = f"{self.base_url}/exchange"
    
    def _sign_request(self, data: Dict[str, Any]) -> str:
        """Sign request with API secret for authentication"""
        if not self.api_secret:
            raise ValueError("API secret required for authenticated requests")
        message = json.dumps(data, separators=(',', ':'))
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    # FETCH METHODS
    def fetch_market_data(self, symbol: str) -> Dict[str, Any]:
        """Fetch market metadata and asset contexts for a symbol"""
        payload = {"type": "metaAndAssetCtxs"}
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def fetch_orderbook(self, symbol: str) -> Dict[str, Any]:
        """Fetch L2 orderbook data for a symbol"""
        payload = {"type": "l2Book", "coin": symbol}
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def fetch_user_state(self, address: str) -> Dict[str, Any]:
        """Fetch user state including positions, balances, and margin info"""
        payload = {"type": "clearinghouseState", "user": address}
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def fetch_candles(self, symbol: str, interval: str = "1h", start_time: Optional[int] = None, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch OHLCV candlestick data
        
        Args:
            symbol: Trading pair symbol
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            start_time: Start timestamp in milliseconds
            end_time: End timestamp in milliseconds
        """
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol,
                "interval": interval,
                "startTime": start_time or int(time.time() * 1000) - 86400000,
                "endTime": end_time or int(time.time() * 1000)
            }
        }
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def fetch_all_mids(self) -> Dict[str, str]:
        """Fetch mid prices for all assets"""
        payload = {"type": "allMids"}
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def fetch_user_funding(self, address: str, start_time: Optional[int] = None, end_time: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch user funding history"""
        payload = {
            "type": "userFunding",
            "user": address,
            "startTime": start_time,
            "endTime": end_time
        }
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    # TRADING METHODS
    def place_order(self, symbol: str, is_buy: bool, size: float, price: float, order_type: str = "limit", reduce_only: bool = False, post_only: bool = False, ioc: bool = False) -> Dict[str, Any]:
        """Place a limit order on Hyperliquid
        
        Args:
            symbol: Trading pair symbol
            is_buy: True for buy, False for sell
            size: Order size
            price: Limit price
            order_type: Order type (limit/market)
            reduce_only: Only reduce existing position
            post_only: Post-only order (maker only)
            ioc: Immediate or cancel
        """
        if not self.api_key:
            raise ValueError("API key required for trading")
        
        tif = "Ioc" if ioc else ("Alo" if post_only else "Gtc")
        
        order = {
            "coin": symbol,
            "is_buy": is_buy,
            "sz": size,
            "limit_px": price,
            "order_type": {"limit": {"tif": tif}},
            "reduce_only": reduce_only
        }
        
        payload = {
            "type": "order",
            "orders": [order],
            "grouping": "na"
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        }
        
        response = requests.post(self.exchange_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    
    def market_order(self, symbol: str, is_buy: bool, size: float, slippage: float = 0.05) -> Dict[str, Any]:
        """Place a market order with slippage protection
        
        Args:
            symbol: Trading pair symbol
            is_buy: True for buy, False for sell
            size: Order size
            slippage: Maximum slippage tolerance (default 5%)
        """
        # Get current mid price
        mids = self.fetch_all_mids()
        mid_price = float(mids.get(symbol, 0))
        
        if mid_price == 0:
            # Fallback to aggressive limit
            price = 1000000 if is_buy else 0.01
        else:
            # Apply slippage
            price = mid_price * (1 + slippage) if is_buy else mid_price * (1 - slippage)
        
        return self.place_order(symbol, is_buy, size, price, ioc=True)
    
    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        """Cancel an existing order
        
        Args:
            symbol: Trading pair symbol
            order_id: Order ID to cancel
        """
        if not self.api_key:
            raise ValueError("API key required for trading")
        
        payload = {
            "type": "cancel",
            "cancels": [{"coin": symbol, "o": order_id}]
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        }
        
        response = requests.post(self.exchange_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    
    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancel all open orders, optionally filtered by symbol"""
        if not self.api_key:
            raise ValueError("API key required for trading")
        
        payload = {"type": "cancelAll"}
        if symbol:
            payload["coin"] = symbol
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        }
        
        response = requests.post(self.exchange_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    
    def modify_order(self, symbol: str, order_id: int, new_price: float, new_size: float) -> Dict[str, Any]:
        """Modify an existing order"""
        if not self.api_key:
            raise ValueError("API key required for trading")
        
        payload = {
            "type": "modify",
            "modifies": [{
                "oid": order_id,
                "coin": symbol,
                "limit_px": new_price,
                "sz": new_size
            }]
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key
        }
        
        response = requests.post(self.exchange_url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    
    # ACCOUNT METHODS
    def get_open_orders(self, address: str) -> List[Dict[str, Any]]:
        """Get all open orders for an address"""
        payload = {"type": "openOrders", "user": address}
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def get_user_fills(self, address: str) -> List[Dict[str, Any]]:
        """Get user trade fills history"""
        payload = {"type": "userFills", "user": address}
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    def get_user_rate_limit(self, address: str) -> Dict[str, Any]:
        """Get user rate limit info"""
        payload = {"type": "userRateLimit", "user": address}
        response = requests.post(self.info_url, json=payload)
        response.raise_for_status()
        return response.json()
    
    # CONVENIENCE METHODS
    def get_position(self, address: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get specific position for a symbol"""
        state = self.fetch_user_state(address)
        positions = state.get("assetPositions", [])
        for pos in positions:
            if pos.get("position", {}).get("coin") == symbol:
                return pos
        return None
    
    def get_balance(self, address: str) -> Dict[str, Any]:
        """Get account balance info"""
        state = self.fetch_user_state(address)
        return {
            "marginSummary": state.get("marginSummary", {}),
            "crossMarginSummary": state.get("crossMarginSummary", {})
        }
    
    def close_position(self, symbol: str, address: str) -> Dict[str, Any]:
        """Close an entire position using market order"""
        position = self.get_position(address, symbol)
        if not position:
            return {"error": "No position found"}
        
        pos_data = position.get("position", {})
        size = abs(float(pos_data.get("szi", 0)))
        is_long = float(pos_data.get("szi", 0)) > 0
        
        if size == 0:
            return {"error": "Position size is zero"}
        
        # Close by selling if long, buying if short
        return self.market_order(symbol, not is_long, size)
