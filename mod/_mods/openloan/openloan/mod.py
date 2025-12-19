class OpenLoan:
    """OpenLoan - Decentralized Lending Protocol
    
    A DeFi lending protocol enabling trustless peer-to-peer loans
    with cryptocurrency collateral and automated risk management.
    """
    
    description = """
    OpenLoan: Revolutionary decentralized lending protocol
    - Trustless P2P lending
    - Crypto-collateralized loans
    - Real-time price oracles
    - Automated liquidation
    """
    
    def __init__(self, ltv_ratio=0.7, liquidation_threshold=0.8):
        """Initialize OpenLoan protocol.
        
        Args:
            ltv_ratio: Loan-to-value ratio (default 70%)
            liquidation_threshold: Liquidation trigger threshold (default 80%)
        """
        self.ltv_ratio = ltv_ratio
        self.liquidation_threshold = liquidation_threshold
    
    def calculate_loan(self, collateral_value, custom_ltv=None):
        """Calculate maximum loan amount based on collateral.
        
        Args:
            collateral_value: USD value of collateral
            custom_ltv: Optional custom LTV ratio
            
        Returns:
            Maximum loan amount in USD
        """
        ltv = custom_ltv if custom_ltv else self.ltv_ratio
        return self.multiply(collateral_value, ltv)
    
    def multiply(self, a, b):
        """Multiply two numbers - core calculation utility.
        
        Args:
            a: First number
            b: Second number
            
        Returns:
            Product of a and b
        """
        return a * b
    
    def get_bittenso_price(self):
        """Fetch real-time Bittenso price from oracle.
        
        Returns:
            Current USD price or error message
        """
        import requests
        try:
            response = requests.get(
                'https://api.coingecko.com/api/v3/simple/price?ids=bittenso&vs_currencies=usd',
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            price = data.get('bittenso', {}).get('usd', 'Price not available')
            return f"${price}" if isinstance(price, (int, float)) else price
        except requests.exceptions.Timeout:
            return "Error: Price oracle timeout"
        except requests.exceptions.RequestException as e:
            return f"Error fetching price: {str(e)}"
        except Exception as e:
            return f"Unexpected error: {str(e)}"
    
    def check_liquidation(self, collateral_value, loan_amount):
        """Check if position should be liquidated.
        
        Args:
            collateral_value: Current USD value of collateral
            loan_amount: Outstanding loan amount
            
        Returns:
            Boolean indicating if liquidation is needed
        """
        if loan_amount == 0:
            return False
        current_ratio = collateral_value / loan_amount
        return current_ratio < self.liquidation_threshold
    
    def calculate_interest(self, principal, rate, time_days):
        """Calculate interest on loan.
        
        Args:
            principal: Loan principal amount
            rate: Annual interest rate (decimal)
            time_days: Loan duration in days
            
        Returns:
            Total interest amount
        """
        daily_rate = rate / 365
        return self.multiply(self.multiply(principal, daily_rate), time_days)
