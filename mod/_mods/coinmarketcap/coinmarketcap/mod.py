class BaseMod:
    description = """
    CoinMarketCap API Integration Module
    """
    
    def __init__(self, api_key=None):
        """Initialize with optional CoinMarketCap API key."""
        self.api_key = api_key
        self.base_url = "https://pro-api.coinmarketcap.com/v1"
    
    def multiply(self, a, b):
        """Multiply two numbers and return the result."""
        return a * b
    
    def get_crypto_price(self, symbol="BTC", convert="USD"):
        """Fetch cryptocurrency price from CoinMarketCap API."""
        import requests
        try:
            headers = {
                'X-CMC_PRO_API_KEY': self.api_key or '',
                'Accept': 'application/json'
            }
            params = {
                'symbol': symbol.upper(),
                'convert': convert.upper()
            }
            response = requests.get(
                f"{self.base_url}/cryptocurrency/quotes/latest",
                headers=headers,
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            price = data['data'][symbol.upper()]['quote'][convert.upper()]['price']
            return {"symbol": symbol, "price": price, "currency": convert}
        except Exception as e:
            return {"error": f"Error fetching price: {str(e)}"}
    
    def get_crypto_metadata(self, symbol="BTC"):
        """Fetch cryptocurrency metadata from CoinMarketCap."""
        import requests
        try:
            headers = {
                'X-CMC_PRO_API_KEY': self.api_key or '',
                'Accept': 'application/json'
            }
            params = {'symbol': symbol.upper()}
            response = requests.get(
                f"{self.base_url}/cryptocurrency/info",
                headers=headers,
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data['data'][symbol.upper()]
        except Exception as e:
            return {"error": f"Error fetching metadata: {str(e)}"}
    
    def get_top_cryptocurrencies(self, limit=10, convert="USD"):
        """Fetch top cryptocurrencies by market cap."""
        import requests
        try:
            headers = {
                'X-CMC_PRO_API_KEY': self.api_key or '',
                'Accept': 'application/json'
            }
            params = {
                'limit': limit,
                'convert': convert.upper()
            }
            response = requests.get(
                f"{self.base_url}/cryptocurrency/listings/latest",
                headers=headers,
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data['data']
        except Exception as e:
            return {"error": f"Error fetching top cryptocurrencies: {str(e)}"}
    
    def get_global_metrics(self):
        """Fetch global cryptocurrency market metrics."""
        import requests
        try:
            headers = {
                'X-CMC_PRO_API_KEY': self.api_key or '',
                'Accept': 'application/json'
            }
            response = requests.get(
                f"{self.base_url}/global-metrics/quotes/latest",
                headers=headers,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            return data['data']
        except Exception as e:
            return {"error": f"Error fetching global metrics: {str(e)}"}
