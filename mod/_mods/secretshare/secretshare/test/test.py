import pytest
from secretshare.mod import BaseMod


class TestBaseMod:
    """Test suite for SecretShare BaseMod"""
    
    def __init__(self):
        """Setup test fixtures"""
        self.mod = BaseMod()
        self.test_message = "my secret data"
        
    def test_basic_secret_sharing(self):
        """Test basic secret sharing and reconstruction"""
        shares = self.mod.secretshare(self.test_message, n=5, m=3)
        assert len(shares) == 5
        
        # Reconstruct with exactly m shares
        reconstructed = self.mod.reconstruct([shares[0], shares[2], shares[4]])
        assert reconstructed == self.test_message
        
    def test_minimum_threshold(self):
        """Test that minimum threshold works"""
        shares = self.mod.secretshare(self.test_message, n=5, m=2)
        
        # Should work with 2 shares
        reconstructed = self.mod.reconstruct([shares[0], shares[1]])
        assert reconstructed == self.test_message
        
    def test_all_shares_reconstruction(self):
        """Test reconstruction with all shares"""
        shares = self.mod.secretshare(self.test_message, n=4, m=3)
        
        # Use all shares
        reconstructed = self.mod.reconstruct(shares)
        assert reconstructed == self.test_message
        
    def test_different_share_combinations(self):
        """Test that different combinations of shares work"""
        shares = self.mod.secretshare(self.test_message, n=6, m=3)
        
        # Test multiple combinations
        combinations = [
            [shares[0], shares[1], shares[2]],
            [shares[1], shares[3], shares[5]],
            [shares[0], shares[2], shares[4]],
        ]
        
        for combo in combinations:
            reconstructed = self.mod.reconstruct(combo)
            assert reconstructed == self.test_message
            
    def test_empty_message(self):
        """Test with empty message"""
        empty_msg = ""
        shares = self.mod.secretshare(empty_msg, n=3, m=2)
        reconstructed = self.mod.reconstruct([shares[0], shares[1]])
        assert reconstructed == empty_msg
        
    def test_long_message(self):
        """Test with longer message"""
        long_msg = "This is a much longer secret message that contains multiple words and sentences. It should still work perfectly with the secret sharing scheme!"
        shares = self.mod.secretshare(long_msg, n=5, m=3)
        reconstructed = self.mod.reconstruct([shares[1], shares[2], shares[4]])
        assert reconstructed == long_msg
        
    def test_special_characters(self):
        """Test with special characters"""
        special_msg = "Secret! @#$%^&*() 123 ñ é ü"
        shares = self.mod.secretshare(special_msg, n=4, m=2)
        reconstructed = self.mod.reconstruct([shares[0], shares[3]])
        assert reconstructed == special_msg
        
    def test_invalid_threshold_too_high(self):
        """Test that m > n raises error"""
        with pytest.raises(ValueError, match="Threshold m cannot be greater than total shares n"):
            self.mod.secretshare(self.test_message, n=3, m=5)
            
    def test_invalid_threshold_too_low(self):
        """Test that m < 2 raises error"""
        with pytest.raises(ValueError, match="Threshold m must be at least 2"):
            self.mod.secretshare(self.test_message, n=5, m=1)
            
    def test_invalid_shares_count(self):
        """Test that n < 2 raises error"""
        with pytest.raises(ValueError, match="Must create at least 2 shares"):
            self.mod.secretshare(self.test_message, n=1, m=1)
            
    def test_insufficient_shares_reconstruction(self):
        """Test that reconstruction with too few shares fails gracefully"""
        shares = self.mod.secretshare(self.test_message, n=5, m=4)
        
        # Try with only 3 shares when 4 are needed
        # This should produce incorrect result (not enough shares)
        reconstructed = self.mod.reconstruct([shares[0], shares[1], shares[2]])
        # The reconstruction will complete but produce wrong result
        assert reconstructed != self.test_message
        
    def test_invalid_share_format(self):
        """Test that invalid share format raises error"""
        with pytest.raises(ValueError, match="Invalid share format"):
            self.mod.reconstruct(["invalid:share", "1:2:3"])
            
    def test_bytes_to_int_conversion(self):
        """Test internal byte conversion methods"""
        test_bytes = b"test"
        num = self.mod._bytes_to_int(test_bytes)
        converted_back = self.mod._int_to_bytes(num, len(test_bytes))
        assert converted_back == test_bytes
        
    def test_polynomial_evaluation(self):
        """Test polynomial evaluation"""
        coeffs = [1, 2, 3]  # f(x) = 1 + 2x + 3x^2
        # f(2) = 1 + 2*2 + 3*4 = 1 + 4 + 12 = 17
        result = self.mod._eval_poly(coeffs, 2)
        assert result == 17
        
    def test_share_independence(self):
        """Test that shares are independent (different runs produce different shares)"""
        shares1 = self.mod.secretshare(self.test_message, n=3, m=2)
        shares2 = self.mod.secretshare(self.test_message, n=3, m=2)
        
        # Shares should be different (due to random coefficients)
        assert shares1 != shares2
        
        # But both should reconstruct to same message
        reconstructed1 = self.mod.reconstruct([shares1[0], shares1[1]])
        reconstructed2 = self.mod.reconstruct([shares2[0], shares2[1]])
        assert reconstructed1 == reconstructed2 == self.test_message
