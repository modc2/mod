import base64
import hmac
import json
import time
import threading
import uuid
from typing import Dict, Optional, Any
import mod as m
import hashlib

class Auth:

    features = ['data', 'time', 'nonce', 'key', 'signature']
    sig_features = ['data', 'time', 'nonce']

    def __init__(self,
                key=None,
                crypto_type='ecdsa',
                max_age=86_400 ):

        """

        Initialize the Auth class
        :param key: the key to use for signing
        :param crypto_type: the crypto type to use for signing
        :param signature_keys: the keys to use for signing
        """
        self.set_key(key=key, crypto_type=crypto_type)
        self.max_age = max_age
        # Replay protection: track used nonces with expiry
        self._used_nonces: Dict[str, float] = {}
        self._nonce_lock = threading.Lock()
        self._last_nonce_cleanup = time.time()


    def infer_crypto_type(self, key):
        """
        Infer the crypto type from an address/key string.
        Supports: ecdsa (0x), sr25519 (SS58), solana (base58 32-byte), ed25519 (fallback)
        """
        from mod.core.key.key.utils import is_ethereum_address, is_substrate_ss58_address
        if is_ethereum_address(key):
            return 'ecdsa'
        if is_substrate_ss58_address(key):
            return 'sr25519'
        try:
            import base58
            from scalecodec.utils.ss58 import is_valid_ss58_address
            decoded = base58.b58decode(key)
            if len(decoded) == 32 and not is_valid_ss58_address(key):
                return 'solana'
        except Exception:
            pass
        return 'ed25519'

    def set_key(self, key, crypto_type=None):
        """
        Set the key to use for signing
        """
        self.key = m.key(key=key, crypto_type=crypto_type)
        self.crypto_type = crypto_type or self.key.crypto_type_name


    def key_address(self, key=None) -> str:
        """
        Get the address of the key
        """
    
        return self.get_key(key).address

    def token_data(self, data, key=None) -> dict:
        """
        Generate the token data without encoding
        """
        result = {
            'data': data,
            'time': str(time.time()),
            'nonce': uuid.uuid4().hex,
            'key': key.address if key else self.key.address,
        }

        return result

    def token(self,  data: dict = {},  key=None, mod='str') -> dict:
        """
        Generate the headers with the JWT token
        """
        key = self.get_key(key)
        result = self.token_data(data)
        result['signature'] = key.sign(self.sig_data(result), mode='str')

        if mod == 'dict':
            return result
        elif mod == 'str':
            return self._base64url_encode(result)
        else:
            raise ValueError(f'Invalid mod {mod}')
        

    def headers(self, data: dict, key=None) -> dict:
        return {'token': self.token(data=data, key=key)}

    generate = forward = headers


    def set_crypto_type(self, crypto_type):
        self.crypto_type = crypto_type
        self.key = m.key(key=self.key, crypto_type=crypto_type)

    def verify(self, headers: str, crypto_type=None) -> dict:
        self.crypto_type = crypto_type or self.crypto_type
        if isinstance(headers, str):
            headers = json.loads(self._base64url_decode(headers))
        if 'Token' in headers:
            headers['token'] = headers.pop('Token')
        if 'token' in headers:
            token = headers['token']
            if token:
                decoded = json.loads(self._base64url_decode(token))
                if decoded and 'key' in decoded:
                    headers = decoded

        if 'key' not in headers:
            raise Exception('No authentication key provided')

        crypto_type = self.infer_crypto_type(headers['key'])
        # ────────────────────────────────────────────────
        # FIX: Normalize MetaMask legacy v=27/28 → v=0/1
        # ────────────────────────────────────────────────
        sig = headers['signature']
        if sig.startswith('0x'):
            sig_hex = sig[2:]
        else:
            sig_hex = sig

        if len(sig_hex) == 130:  # 65 bytes = 130 hex chars
            r = sig_hex[:64]
            s = sig_hex[64:128]
            v_hex = sig_hex[128:130]  # last 2 hex chars = 1 byte
            v = int(v_hex, 16)
            if v in (27, 28):
                normalized_v = v - 27   # 27→0, 28→1
                headers['signature'] = '0x' + r + s + f'{normalized_v:02x}'
                print(f"Normalized legacy v={v} → {normalized_v}")

        print('Verifying signature with headers:', headers)

        sig_data = self.sig_data(headers)   
        print('Hashing sig_data for verification:', m.hash(sig_data))
        # Now verify with (possibly normalized) signature

        age = abs(time.time() - float(headers['time']))
        assert age < self.max_age, f'Token is stale {age} > {self.max_age}'

        assert self.key.verify(
            sig_data,
            signature=headers['signature'],
            address=headers['key'],
            crypto_type=crypto_type
        ), f'Invalid signature'

        # ── Replay protection: reject reused nonces ──
        nonce = headers.get('nonce', '')
        if nonce:
            now = time.time()
            with self._nonce_lock:
                # Periodic cleanup of expired nonces
                if now - self._last_nonce_cleanup > 300:
                    cutoff = now - self.max_age
                    self._used_nonces = {k: v for k, v in self._used_nonces.items() if v > cutoff}
                    self._last_nonce_cleanup = now
                assert nonce not in self._used_nonces, 'Replay detected: nonce already used'
                self._used_nonces[nonce] = now

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
        if isinstance(data, dict):
            data = json.dumps(data, separators=(',', ':'))
        if isinstance(data, str):
            data = data.encode('utf-8')

        return hashlib.sha256(data).hexdigest() 

    def sig_data(self, headers: Dict[str, str]) -> str:
        """
        get the signature data from the headers
        """
        return json.dumps({k: headers[k] for k in self.sig_features}, separators=(',', ':'))

    def test(self, key='test.auth', crypto_type='ecdsa'):
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


