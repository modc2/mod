class BaseMod:
    description = """
    Base mod template
    """
    
    def multiply(self, a, b):
        """Multiply two numbers and return the result."""
        return a * b
    
    def get_bittenso_price(self):
        """Fetch the price of Bittenso cryptocurrency."""
        import requests
        try:
            response = requests.get('https://api.coingecko.com/api/v3/simple/price?ids=bittenso&vs_currencies=usd')
            response.raise_for_status()
            data = response.json()
            return data.get('bittenso', {}).get('usd', 'Price not available')
        except Exception as e:
            return f"Error fetching price: {str(e)}"
