class BaseMod:
    """
    Comprehensive CoinGecko API Module - Production Ready
    
    A powerful, all-inclusive module for cryptocurrency data integration.
    Supports multiple cryptocurrencies, error handling, caching, and extensibility.
    """
    
    description = "Advanced CoinGecko cryptocurrency API integration module with multi-coin support, caching, and robust error handling"
    
    def __init__(self):
        """Initialize the module with cache support."""
        self._cache = {}
        self._cache_duration = 60  # Cache for 60 seconds
        
    def multiply(self, a, b):
        """Multiply two numbers and return the result."""
        return a * b
    
    def get_bittenso_price(self):
        """Fetch the price of Bittenso cryptocurrency with enhanced error handling."""
        return self.get_crypto_price('bittenso')
    
    def get_crypto_price(self, crypto_id='bittenso', currency='usd', use_cache=True):
        """
        Fetch cryptocurrency price with caching and comprehensive error handling.
        
        Args:
            crypto_id (str): Cryptocurrency identifier (e.g., 'bitcoin', 'ethereum', 'bittenso')
            currency (str): Target currency (default: 'usd')
            use_cache (bool): Whether to use cached data if available
            
        Returns:
            str: Formatted price string or error message
        """
        import requests
        import time
        
        # Check cache
        if use_cache:
            cache_key = f"{crypto_id}_{currency}"
            if cache_key in self._cache:
                cached_price, cached_time = self._cache[cache_key]
                if time.time() - cached_time < self._cache_duration:
                    return cached_price
        
        try:
            url = 'https://api.coingecko.com/api/v3/simple/price'
            params = {'ids': crypto_id, 'vs_currencies': currency}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            price = data.get(crypto_id, {}).get(currency)
            if price is None:
                return f"Price not available for {crypto_id}"
            
            formatted_price = f"${price}"
            
            # Update cache
            cache_key = f"{crypto_id}_{currency}"
            self._cache[cache_key] = (formatted_price, time.time())
            
            return formatted_price
            
        except requests.exceptions.Timeout:
            return f"Error: Request timeout for {crypto_id}"
        except requests.exceptions.ConnectionError:
            return f"Error: Connection failed for {crypto_id}"
        except requests.exceptions.HTTPError as e:
            return f"Error: HTTP {e.response.status_code} for {crypto_id}"
        except Exception as e:
            return f"Error fetching price for {crypto_id}: {str(e)}"
    
    def get_multiple_prices(self, crypto_ids, currency='usd'):
        """
        Fetch multiple cryptocurrency prices in a single API call.
        
        Args:
            crypto_ids (list): List of cryptocurrency IDs
            currency (str): Target currency (default: 'usd')
            
        Returns:
            dict: Dictionary mapping crypto_id to price or error
        """
        import requests
        
        try:
            url = 'https://api.coingecko.com/api/v3/simple/price'
            params = {
                'ids': ','.join(crypto_ids),
                'vs_currencies': currency
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # Format results
            results = {}
            for crypto_id in crypto_ids:
                price = data.get(crypto_id, {}).get(currency)
                results[crypto_id] = f"${price}" if price else "N/A"
            
            return results
            
        except Exception as e:
            return {"error": f"Failed to fetch prices: {str(e)}"}
    
    def get_historical_price(self, crypto_id, date, currency='usd'):
        """
        Fetch historical cryptocurrency price for a specific date.
        
        Args:
            crypto_id (str): Cryptocurrency identifier
            date (str): Date in DD-MM-YYYY format
            currency (str): Target currency (default: 'usd')
            
        Returns:
            str: Formatted historical price or error message
        """
        import requests
        
        try:
            url = f'https://api.coingecko.com/api/v3/coins/{crypto_id}/history'
            params = {'date': date}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            price = data.get('market_data', {}).get('current_price', {}).get(currency)
            return f"${price}" if price else "Historical price not available"
            
        except Exception as e:
            return f"Error fetching historical price: {str(e)}"
    
    def get_market_data(self, crypto_id):
        """
        Fetch comprehensive market data for a cryptocurrency.
        
        Args:
            crypto_id (str): Cryptocurrency identifier
            
        Returns:
            dict: Market data including price, volume, market cap, etc.
        """
        import requests
        
        try:
            url = f'https://api.coingecko.com/api/v3/coins/{crypto_id}'
            params = {
                'localization': 'false',
                'tickers': 'false',
                'community_data': 'false',
                'developer_data': 'false'
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            market_data = data.get('market_data', {})
            return {
                'name': data.get('name', 'N/A'),
                'symbol': data.get('symbol', 'N/A').upper(),
                'current_price': f"${market_data.get('current_price', {}).get('usd', 'N/A')}",
                'market_cap': f"${market_data.get('market_cap', {}).get('usd', 'N/A')}",
                'total_volume': f"${market_data.get('total_volume', {}).get('usd', 'N/A')}",
                'price_change_24h': f"{market_data.get('price_change_percentage_24h', 'N/A')}%",
                'high_24h': f"${market_data.get('high_24h', {}).get('usd', 'N/A')}",
                'low_24h': f"${market_data.get('low_24h', {}).get('usd', 'N/A')}"
            }
            
        except Exception as e:
            return {"error": f"Failed to fetch market data: {str(e)}"}
    
    def clear_cache(self):
        """Clear the price cache."""
        self._cache.clear()
        return "Cache cleared successfully"
    
    def set_cache_duration(self, seconds):
        """
        Set cache duration in seconds.
        
        Args:
            seconds (int): Cache duration in seconds
        """
        self._cache_duration = seconds
        return f"Cache duration set to {seconds} seconds"
