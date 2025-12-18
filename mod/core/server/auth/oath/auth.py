import base64
import hmac
import json
import time
from typing import Dict, Optional, Any
import mod as m
import hashlib

class Auth:
    """Advanced authentication system with cryptographic signing and verification."""

    separators=(',', ':')

    def __init__(self, 
                key=None, 
                crypto_type='sr25519', 
                hash_type='sha256',    
                max_age=60, 
                signature_keys = ['data', 'time', 'cost']):
        
        """
        Initialize the Auth class with cryptographic signing capabilities.
        
        Args:
            key: Cryptographic key for signing (auto-generated if None)
            crypto_type: Cryptographic algorithm ('sr25519', 'ed25519', etc.)
            hash_type: Hash algorithm for data integrity ('sha256', 'identity')
            max_age: Maximum token age in seconds before expiration
            signature_keys: Keys to include in signature payload
        """
        self.signature_keys = signature_keys
        self.crypto_type = crypto_type
        self.key = m.get_key(key=key, crypto_type=crypto_type)
        self.hash_type = hash_type
        self.auth_features = signature_keys + ['key', 'signature']
        self.max_age = max_age

    def forward(self, data: Any, key=None, cost=0) -> dict:
        """
        Generate authenticated headers with cryptographic signature.
        
        Args:
            data: Data payload to authenticate
            key: Optional key override for signing
            cost: Computational cost associated with request
            
        Returns:
            Dictionary containing data hash, timestamp, cost, key address, and signature
        """
        key = self.get_key(key)
        result = {
            'data': self.hash(data),
            'time': str(time.time()),
            'cost': str(cost),
            'key': key.address,
        }
        result['signature'] = key.sign(self.get(result), mode='str')
        return result

    headers = generate = forward

    def verify(self, headers: dict, data: Optional[Any]=None, max_age: Optional[int]=None) -> bool:
        """
        Verify cryptographic signature and validate token freshness.
        
        Args:
            headers: Authentication headers containing signature and metadata
            data: Optional data to verify against hash in headers
            max_age: Override default maximum token age
            
        Returns:
            True if signature is valid and token is fresh
            
        Raises:
            AssertionError: If token is stale, signature invalid, or data mismatch
        """
        # Validate token freshness
        age = abs(time.time() - float(headers['time']))
        max_age = max_age or self.max_age
        assert age < max_age, f'Token expired: age={age:.2f}s exceeds max_age={max_age}s'
        
        # Verify cryptographic signature
        verified = self.key.verify(
            self.get(headers), 
            signature=headers['signature'], 
            address=headers['key']
        )
        assert verified, f'Signature verification failed for key={headers["key"]}'
        
        # Optionally verify data integrity
        if data is not None:
            data_hash = self.hash(data)
            assert headers['data'] == data_hash, f'Data integrity check failed: expected={data_hash}, got={headers["data"]}'
        
        return verified

    def get_key(self, key=None):
        """
        Retrieve or validate cryptographic key.
        
        Args:
            key: Key identifier or object (uses default if None)
            
        Returns:
            Key object with signing capabilities
        """
        if key is None:
            key = self.key
        else:
            key = m.get_key(key, crypto_type=self.crypto_type)
        assert hasattr(key, 'address'), f'Invalid key: missing address attribute'
        return key

    verify_headers = verify

    def _is_identity_hash_type(self):
        """Check if hash type is identity (no hashing)."""
        return self.hash_type in ['identity', None, 'none']

    def hash(self, data: Any) -> str:
        """
        Generate cryptographic hash of data.
        
        Args:
            data: Data to hash (will be JSON serialized)
            
        Returns:
            Hexadecimal hash string or identity string
        """
        data_str = json.dumps(data, separators=self.separators)
        if self.hash_type == 'sha256':
            return hashlib.sha256(data_str.encode()).hexdigest()
        elif self._is_identity_hash_type():
            return data_str
        else: 
            raise ValueError(f'Unsupported hash type: {self.hash_type}')

    def get(self, headers: Dict[str, str]) -> str:
        """
        Extract signature payload from headers.
        
        Args:
            headers: Authentication headers
            
        Returns:
            JSON string of signature keys
        """
        assert all(k in headers for k in self.signature_keys), f'Missing required keys: {set(self.signature_keys) - set(headers.keys())}'
        return json.dumps({k: headers[k] for k in self.signature_keys}, separators=self.separators)

    def test(self, key='test.auth', crypto_type='sr25519'):
        """
        Run comprehensive authentication test.
        
        Args:
            key: Test key identifier
            crypto_type: Cryptographic algorithm to test
            
        Returns:
            Test results with status and generated headers
        """
        data = {'fn': 'test', 'params': {'a': 1, 'b': 2}}
        auth = Auth(key=key, crypto_type=crypto_type)
        headers = auth.forward(data, key=key)
        assert auth.verify(headers, data=data)
        return {'test_passed': True, 'headers': headers, 'message': 'All authentication tests passed successfully'}
