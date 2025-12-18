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

    def headers(self,  data: dict,  key=None, cost=0) -> dict:
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
        return result

    generate = forward = headers

    def verify(self, headers: str, data:dict, max_age=10) -> bool:
        """
        Verify and decode a JWT token
        provide the data if you want to verify the data hash
        """
        # check age 
        age = abs(time.time() - float(headers['time']))
        max_age = max_age or self.max_age
        assert age < max_age, f'Token is stale {age} > {max_age}'
        sig_data = self.sig_data(headers)
        verified = self.key.verify(sig_data, signature=headers['signature'], address=headers['key'])
        assert verified, f'Invalid signature {sig_data}'
        # if data != None:
        #     assert headers['data'] == self.hash(data), f'Invalid data {data}'
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
        return json.dumps({k: headers[k] for k in self.sig_features}, separators=(',', ':'))

    def test(self, key='test.auth', crypto_type='sr25519'):
        data = {'fn': 'test', 'params': {'a': 1, 'b': 2}}
        auth = Auth(key=key, crypto_type=crypto_type)
        headers = auth.generate(data, key=key)
        assert auth.verify(headers, data), 'Auth test failed'
        return {'test_passed': True, 'headers': headers}

