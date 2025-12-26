import base64
import hmac
import json
import time
from typing import Dict, Optional, Any
import mod as m
import hashlib

class Auth:

    features = ['data', 'time', 'cost', 'key', 'signature']
    sig_features = ['data', 'time', 'cost']

    def __init__(self, 
                key=None, 
                crypto_type='sr25519', 
                max_age=60 ):
        
        """

        Initialize the Auth class
        :param key: the key to use for signing
        :param crypto_type: the crypto type to use for signing
        :param signature_keys: the keys to use for signing 
        """
        self.set_key(key=key, crypto_type=crypto_type)
        self.max_age = max_age

    def set_key(self, key, crypto_type=None):
        """
        Set the key to use for signing
        """
        self.key = m.key(key=key, crypto_type=crypto_type)
        self.crypto_type = crypto_type or self.key.crypto_type_name

    def token(self,  data: dict,  key=None, cost=0) -> dict:
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
        result['signature'] = key.sign(self.sig_data(result), mode='str')
        token = self._base64url_encode(result)
        return token

    def headers(self, data: dict, key=None, cost=0) -> dict:
        return {'token': self.token(data=data, key=key, cost=cost)}

    generate = forward = headers

    def verify(self, headers: str) -> dict:
        """
        Verify and decode a JWT token
        provide the data if you want to verify the data hash
        """
        if 'token' in headers:
            token = headers['token']
            headers = json.loads(self._base64url_decode(token))
        age = abs(time.time() - float(headers['time']))
        assert age < self.max_age, f'Token is stale {age} > {self.max_age}'
        verified = self.key.verify(self.sig_data(headers), signature=headers['signature'], address=headers['key'])
        assert verified
        return headers

    def get_key(self, key=None):
        """
        Get the key to use for signing
        """
        if key is None:
            key = self.key
        else:
            key = m.key(key, crypto_type=self.crypto_type)
        assert hasattr(key, 'address'), f'Invalid key {key}'
        return key

    def hash(self, data: Any) -> str:
        """
        Hash the data using sha256
        """
        if isinstance(data, str):
            data = data.encode('utf-8')
        elif isinstance(data, dict):
            data = json.dumps(data)
        return m.hash(data)

    def sig_data(self, headers: Dict[str, str]) -> str:
        """
        get the signature data from the headers
        """
        return json.dumps({k: headers[k] for k in self.sig_features}, separators=(',', ':'))

    def test(self, key='test.auth', crypto_type='sr25519'):
        data = {'fn': 'test', 'params': {'a': 1, 'b': 2}}
        auth = Auth(key=key, crypto_type=crypto_type)
        headers = auth.generate(data, key=key)
        assert auth.verify(headers), 'Auth test failed'
        return {'test_passed': True, 'headers': headers,  'data': data, 'verify': self.verify(headers)}


    def _base64url_encode(self, data):
        """Encode data in base64url format"""
        if isinstance(data, str):
            data = data.encode('utf-8')
        elif isinstance(data, dict):
            data = json.dumps(data, separators=(',', ':')).encode('utf-8')
        encoded = base64.urlsafe_b64encode(data).rstrip(b'=')
        return encoded.decode('utf-8')
    
    def _base64url_decode(self, data):
        """Decode base64url data"""
        padding = b'=' * (4 - (len(data) % 4))
        return base64.urlsafe_b64decode(data.encode('utf-8') + padding)