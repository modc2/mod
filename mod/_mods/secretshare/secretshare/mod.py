import secrets
import hashlib
from typing import List, Tuple

class BaseMod:
    description = """
    Secret sharing implementation using Shamir's Secret Sharing Scheme
    Split secrets into N shares where M shares are needed to reconstruct
    Perfect for end-to-end encryption and secure distributed storage
    """
    
    def __init__(self):
        # Use a large prime for finite field arithmetic
        self.PRIME = 2**127 - 1
    
    def _eval_poly(self, coeffs: List[int], x: int) -> int:
        """Evaluate polynomial at point x in finite field"""
        result = 0
        for coeff in reversed(coeffs):
            result = (result * x + coeff) % self.PRIME
        return result
    
    def _lagrange_interpolate(self, shares: List[Tuple[int, int]], x: int = 0) -> int:
        """Lagrange interpolation to reconstruct secret at x=0"""
        result = 0
        for i, (xi, yi) in enumerate(shares):
            numerator = denominator = 1
            for j, (xj, _) in enumerate(shares):
                if i != j:
                    numerator = (numerator * (x - xj)) % self.PRIME
                    denominator = (denominator * (xi - xj)) % self.PRIME
            # Modular multiplicative inverse
            inv = pow(denominator, self.PRIME - 2, self.PRIME)
            result = (result + yi * numerator * inv) % self.PRIME
        return result
    
    def _bytes_to_int(self, data: bytes) -> int:
        """Convert bytes to integer"""
        return int.from_bytes(data, byteorder='big')
    
    def _int_to_bytes(self, num: int, length: int) -> bytes:
        """Convert integer to bytes with fixed length"""
        return num.to_bytes(length, byteorder='big')
    
    def secretshare(self, message: str, n: int, m: int) -> List[str]:
        """
        Split a message into N shares where M shares are needed to reconstruct.
        Perfect for distributing encrypted data across multiple devices.
        
        Args:
            message: The secret message to split
            n: Total number of shares to create
            m: Minimum number of shares needed to reconstruct (threshold)
            
        Returns:
            List of N share strings (encoded as hex)
            
        Example:
            >>> mod = BaseMod()
            >>> shares = mod.secretshare("my secret data", n=5, m=3)
            >>> # Send shares[0] to device 1, shares[1] to device 2, etc.
            >>> # Any 3 shares can reconstruct the original message
            >>> reconstructed = mod.reconstruct([shares[0], shares[2], shares[4]])
        """
        if m > n:
            raise ValueError("Threshold m cannot be greater than total shares n")
        if m < 2:
            raise ValueError("Threshold m must be at least 2")
        if n < 2:
            raise ValueError("Must create at least 2 shares")
        
        # Convert message to bytes and then to integer
        message_bytes = message.encode('utf-8')
        secret = self._bytes_to_int(message_bytes)
        
        if secret >= self.PRIME:
            raise ValueError("Message too large for current prime field")
        
        # Generate random coefficients for polynomial of degree m-1
        # f(x) = secret + a1*x + a2*x^2 + ... + a(m-1)*x^(m-1)
        coeffs = [secret] + [secrets.randbelow(self.PRIME) for _ in range(m - 1)]
        
        # Generate n shares by evaluating polynomial at x = 1, 2, ..., n
        shares = []
        for x in range(1, n + 1):
            y = self._eval_poly(coeffs, x)
            # Encode share as "x:y:length" where length is original message length
            share_str = f"{x}:{y}:{len(message_bytes)}"
            shares.append(share_str)
        
        return shares
    
    def reconstruct(self, shares: List[str]) -> str:
        """
        Reconstruct the original message from M or more shares.
        
        Args:
            shares: List of share strings (at least M shares)
            
        Returns:
            The original secret message
            
        Example:
            >>> mod = BaseMod()
            >>> shares = mod.secretshare("my secret data", n=5, m=3)
            >>> # Collect shares from different devices
            >>> reconstructed = mod.reconstruct([shares[0], shares[2], shares[4]])
            >>> assert reconstructed == "my secret data"
        """
        if len(shares) < 2:
            raise ValueError("Need at least 2 shares to reconstruct")
        
        # Parse shares
        parsed_shares = []
        msg_length = None
        for share in shares:
            parts = share.split(':')
            if len(parts) != 3:
                raise ValueError(f"Invalid share format: {share}")
            x, y, length = int(parts[0]), int(parts[1]), int(parts[2])
            parsed_shares.append((x, y))
            msg_length = length
        
        # Reconstruct secret using Lagrange interpolation at x=0
        secret = self._lagrange_interpolate(parsed_shares, 0)
        
        # Convert integer back to bytes and then to string
        secret_bytes = self._int_to_bytes(secret, msg_length)
        return secret_bytes.decode('utf-8')
