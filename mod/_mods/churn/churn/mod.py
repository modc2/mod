class Mod:
    """Enhanced module for mathematical operations."""
    
    def forward(self, x=1, y=2):
        """Add two numbers together.
        
        Args:
            x: First number (default: 1)
            y: Second number (default: 2)
            
        Returns:
            Sum of x and y
        """
        return x + y
    
    def multiply(self, x, y):
        """Multiply two numbers.
        
        Args:
            x: First number
            y: Second number
            
        Returns:
            Product of x and y
        """
        return x * y
    
    def power(self, base, exponent):
        """Raise base to the power of exponent.
        
        Args:
            base: The base number
            exponent: The exponent
            
        Returns:
            base raised to exponent
        """
        return base ** exponent
