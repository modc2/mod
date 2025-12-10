from Crypto import Random
import hashlib
from Crypto.Cipher import AES
import copy
import base64
import json
from typing import *

class AesKey:
    """
    AES encryption and decryption class.
    """

    def __init__(self, password='fam'):
        self.password = self.get_password(password)

    def encrypt(self, data, password=None, verify_decryption=False):
        password = self.get_password(password or self.password)  
        data = copy.deepcopy(data)
        data = json.dumps(data)
        if not isinstance(data, str):
            data = str(data)
        data = data + (AES.block_size - len(data) % AES.block_size) * chr(AES.block_size - len(data) % AES.block_size)
        iv = Random.new().read(AES.block_size)
        cipher = AES.new(password, AES.MODE_CBC, iv)
        encrypted_bytes = base64.b64encode(iv + cipher.encrypt(data.encode()))
        # test decryption 
        if verify_decryption:
            decrypted_data = self.decrypt(encrypted_bytes, password)
            assert decrypted_data == data, "Decryption verification failed {}, original data: {}".format(decrypted_data, data)
        
        return encrypted_bytes.decode() 

    def decrypt(self, data, password:str=None):  
        password = self.get_password(password or self.password)  
        data = base64.b64decode(data)
        iv = data[:AES.block_size]
        cipher = AES.new(password, AES.MODE_CBC, iv)
        data =  cipher.decrypt(data[AES.block_size:])
        data = data[:-ord(data[len(data)-1:])].decode('utf-8')
        data = json.loads(data)
        return data

    def test(self,  values = ['10', 'fam', 'hello world'], password='1234'):
        import mod as m
        key= m.key('fam')
        for value in values:
            value = str(value)
            enc = key.encrypt(value, password)
            dec = key.decrypt(enc, password)
            assert dec == value, f'encryption failed, {dec} != {value}'
        return {'encrypted':enc, 'decrypted': dec, 'crypto_type':key.crypto_type}

    def get_password(self, password:str):
        assert password != None, "Password cannot be None"
        if isinstance(password, str):
            password = password.encode()
        return hashlib.sha256(password).digest()
