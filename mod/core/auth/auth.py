import base64
import hmac
import json
import time
from typing import Dict, Optional, Any
import mod as m
import hashlib

class Auth:

    separators=(',', ':')

    def __init__(self, 
                key=None, 
                crypto_type='sr25519', 
                hash_type='sha256',    
                max_age=60, 
                signature_keys = ['data', 'time', 'cost']):
        
        """

        Initialize the Auth class
        :param key: the key to use for signing
        :param crypto_type: the crypto type to use for signing
        :param hash_type: the hash type to use for signing
        :param signature_keys: the keys to use for signing 
        """
        self.signature_keys = signature_keys
        self.crypto_type = crypto_type
        self.key = m.get_key(key=key, crypto_type=crypto_type)
        self.hash_type = hash_type
        self.auth_features = signature_keys + ['key', 'signature']
        self.max_age = max_age

    def forward(self,  data: Any,  key=None, cost=0) -> dict:
        """
        Generate the headers with the JWT token
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

    def verify(self, headers: str, data:Optional[Any]=None, max_age=10) -> bool:
        """
        Verify and decode a JWT token
        provide the data if you want to verify the data hash
        """
        # check age 
        crypto_type = headers.get('crypto_type', self.crypto_type)
        age = abs(time.time() - float(headers['time']))
        max_age = max_age or self.max_age
        assert age < max_age, f'Token is stale {age} > {max_age}'
        verified = self.key.verify(self.get(headers), signature=headers['signature'], address=headers['key'])
        assert verified, f'Invalid signature {headers}'
        if data != None:
            assert headers['data'] == self.hash(data), f'Invalid data {data}'
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
        Hash the data using sha256
        """
        data = json.dumps(data, separators=self.separators)
        if self.hash_type == 'sha256':
            return hashlib.sha256(data.encode()).hexdigest()
        elif self._is_identity_hash_type():
            return data
        else: 
            raise ValueError(f'Invalid hash type {self.hash_type}')

    def get(self, headers: Dict[str, str]) -> str:
        assert all(k in headers for k in self.signature_keys), f'Missing keys in headers {headers}'
        return json.dumps({k: headers[k] for k in self.signature_keys}, separators=self.separators)

    def test(self, key='test.auth', crypto_type='sr25519'):
        data = {'fn': 'test', 'params': {'a': 1, 'b': 2}}
        auth = Auth(key=key, crypto_type=crypto_type)
        headers = auth.forward(data, key=key)
        assert auth.verify(headers)
        return {'test_passed': True, 'headers': headers}

