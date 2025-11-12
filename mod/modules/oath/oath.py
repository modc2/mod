import base64
import hmac
import json
import time
from typing import Dict, Optional, Any
import mod as m
import hashlib

class Auth:
    """Enhanced authentication system with improved security and flexibility"""

    separators=(',', ':')

    def __init__(self, 
                key=None, 
                crypto_type='sr25519', 
                hash_type='sha256',    
                max_age=60, 
                signature_keys=['data', 'time', 'cost'],
                enable_nonce=True,
                enable_expiry=True):
        """
        Initialize the Auth class with enhanced security features
        :param key: the key to use for signing
        :param crypto_type: the crypto type to use for signing
        :param hash_type: the hash type to use for signing
        :param signature_keys: the keys to use for signing
        :param enable_nonce: enable nonce for replay attack prevention
        :param enable_expiry: enable explicit expiry timestamps
        """
        self.signature_keys = signature_keys
        self.crypto_type = crypto_type
        self.key = m.get_key(key=key, crypto_type=crypto_type)
        self.hash_type = hash_type
        self.auth_features = signature_keys + ['key', 'signature']
        self.max_age = max_age
        self.enable_nonce = enable_nonce
        self.enable_expiry = enable_expiry
        self._used_nonces = set()  # Track used nonces for replay protection

    def forward(self, data: Any, key=None, cost=0, ttl=None) -> dict:
        """
        Generate authentication headers with JWT-like token
        :param data: data to authenticate
        :param key: optional key override
        :param cost: computational cost
        :param ttl: time-to-live in seconds (overrides max_age)
        """
        key = self.get_key(key)
        current_time = time.time()
        
        result = {
            'data': self.hash(data),
            'time': str(current_time),
            'cost': str(cost),
            'key': key.address,
        }
        
        # Add nonce for replay attack prevention
        if self.enable_nonce:
            nonce = hashlib.sha256(f"{current_time}{key.address}{data}".encode()).hexdigest()[:16]
            result['nonce'] = nonce
            if 'nonce' not in self.signature_keys:
                self.signature_keys.append('nonce')
        
        # Add explicit expiry
        if self.enable_expiry:
            expiry = current_time + (ttl if ttl else self.max_age)
            result['expiry'] = str(expiry)
            if 'expiry' not in self.signature_keys:
                self.signature_keys.append('expiry')
        
        result['signature'] = key.sign(self.get(result), mode='str')
        return result

    headers = generate = forward

    def verify(self, headers: dict, data: Optional[Any]=None, max_age=None, check_nonce=True) -> bool:
        """
        Verify and decode authentication headers with enhanced security
        :param headers: authentication headers to verify
        :param data: optional data to verify hash against
        :param max_age: maximum age override
        :param check_nonce: whether to check nonce for replay attacks
        """
        crypto_type = headers.get('crypto_type', self.crypto_type)
        current_time = time.time()
        
        # Check expiry if present
        if 'expiry' in headers:
            expiry = float(headers['expiry'])
            assert current_time < expiry, f'Token expired at {expiry}, current time {current_time}'
        
        # Check age
        age = abs(current_time - float(headers['time']))
        max_age = max_age if max_age is not None else self.max_age
        assert age < max_age, f'Token is stale {age} > {max_age}'
        
        # Check nonce for replay attacks
        if check_nonce and self.enable_nonce and 'nonce' in headers:
            nonce = headers['nonce']
            assert nonce not in self._used_nonces, 'Nonce already used - possible replay attack'
            self._used_nonces.add(nonce)
            # Clean old nonces periodically
            if len(self._used_nonces) > 10000:
                self._used_nonces.clear()
        
        # Verify signature
        verified = self.key.verify(self.get(headers), signature=headers['signature'], address=headers['key'])
        assert verified, f'Invalid signature {headers}'
        
        # Verify data hash if provided
        if data is not None:
            assert headers['data'] == self.hash(data), f'Invalid data hash'
        
        return verified

    def get_key(self, key=None):
        """
        Get the key to use for signing
        """
        if key is None:
            key = self.key
        else:
            key = m.get_key(key, crypto_type=self.crypto_type)
        assert hasattr(key, 'address'), f'Invalid key {key}'
        return key

    verify_headers = verify

    def _is_identity_hash_type(self):
        return self.hash_type in ['identity', None, 'none']

    def hash(self, data: Any) -> str:
        """
        Hash the data using specified hash algorithm
        """
        data = json.dumps(data, separators=self.separators, sort_keys=True)  # sort_keys for consistency
        if self.hash_type == 'sha256':
            return hashlib.sha256(data.encode()).hexdigest()
        elif self.hash_type == 'sha512':
            return hashlib.sha512(data.encode()).hexdigest()
        elif self._is_identity_hash_type():
            return data
        else: 
            raise ValueError(f'Invalid hash type {self.hash_type}')

    def get(self, headers: Dict[str, str]) -> str:
        """Extract signature payload from headers"""
        assert all(k in headers for k in self.signature_keys), f'Missing keys in headers {headers}'
        return json.dumps({k: headers[k] for k in self.signature_keys}, separators=self.separators, sort_keys=True)

    def revoke_nonce(self, nonce: str):
        """Manually revoke a nonce"""
        self._used_nonces.add(nonce)

    def clear_nonces(self):
        """Clear all tracked nonces"""
        self._used_nonces.clear()

    def test(self, key='test.auth', crypto_type='sr25519'):
        """Comprehensive test suite"""
        data = {'fn': 'test', 'params': {'a': 1, 'b': 2}}
        auth = Auth(key=key, crypto_type=crypto_type)
        
        # Test basic auth
        headers = auth.forward(data, key=key)
        assert auth.verify(headers, data=data)
        
        # Test with TTL
        headers_ttl = auth.forward(data, key=key, ttl=120)
        assert auth.verify(headers_ttl, data=data)
        
        return {'test_passed': True, 'headers': headers, 'features': ['nonce', 'expiry', 'enhanced_hashing']}
