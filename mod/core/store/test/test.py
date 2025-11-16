
import mod as m
import time
import json

class TestStore:
    def __init__(self, mod='store',  path='~/.commune/store/test', **kwargs):
        self.store = m.mod(mod)(path, **kwargs)

    def test_encrypt(self, path: str= 'test/a',  key: str='fam') -> str:
            """
            Test the encryption and decryption of a file
            """


            store = m.mod('store')(path='~/.commune/store/test_encrypt', password=key)
            if store.exists(path):
                store.rm(path)
            assert not store.exists(path), f'Failed to delete {path}'
            
            value = {'test': 'test', 'fam': {'test': 'test'}}
            store.put(path, value)
            obj = store.get(path)
            assert store.exists(path), f'Failed to find {path}'
            key = m.key(key)
            store.encrypt(path)
            assert store.is_encrypted(path), f'Failed to encrypt {path}'
            result = store.decrypt(path)
            assert not store.is_encrypted(result), f'Failed to decrypt {result}'
            # delete the file
            store.rm(path)
            assert not store.exists(path), f'Failed to delete {path}'
            return {'success': True, 'msg': 'Passed all tests'}

    def test_basics(self, path='test.json', data={'test': 'test', 'fam': {'test': 'test'}}):
        t0 = time.time()
        if self.store.exists(path):
            self.store.rm(path)
        assert not self.store.exists(path), f'Failed to delete'
        self.store.put(path, {'test': 'test'})
        assert self.store.exists(path), f'Failed to find {path}'
        data = self.store.get(path)
        self.store.rm(path)
        assert not self.store.exists(path), f'Failed to delete {path}'
        assert data == {'test': 'test'}, f'Failed test data={data}'
        t1 = time.time()
        print(f'Passed all tests in {t1 - t0} seconds')
        return {'success': True, 'msg': 'Passed all tests'}


    def test_private(self, data={'test': "test", 'fam': 1, "bro": [1,"fam"] }, key='test_key'):
        """
        Test the private store
        """
        store = m.mod('store')(path='~/.commune/store/test_private', password=key)
        store.rm_all()
        for path, value in data.items():
            print(f'Putting {path} with value {value}')
            store.put(path, value)
        # assert all([store.is_encrypted(path) for path in data.keys()]), f'Failed to encrypt all {data.keys()}'
        for path, value in data.items():
            new_value = store.get(path)
            assert  new_value == value, f'Failed to get {path}, {new_value} ({type(new_value)}) != {value} ({type(value)})'
        store.rm_all()
        return {'success': True, 'msg': 'Passed all tests in private store'}        

    def test_encrypt_all(self, data={'test': "test", 'fam': 1, "bro": [1,"fam"] }, key='test_key'):
        """
        Test the encrypt all function
        """
        store = m.mod('store')(path='~/.commune/store/test_encrypt_all')
        store.rm_all()
        for path, value in data.items():
            print(f'Putting {path} with value {value}')
            store.put(path, value)
        store.encrypt_all(password=key)
        store.decrypt_all(password=key)
        
        store.rm_all()
        return {'success': True, 'msg': 'Passed all tests in encrypt all'}

    
    def test_encrypt_folder(self,  mod='mod_test' , password='fam') -> bool:
        """
        Test encrypting and decrypting a folder
        """
        store = self.store
        if m.mod_exists(mod):
            m.rmmod(mod)
        m.addmod(mod)
        folder_path = m.dp(mod)
        store.encrypt_folder(folder_path, password=password)
        path2text =store.decrypt_folder(folder_path, password=password)
        og_path2text = store.path2text(folder_path)
        for k in og_path2text:
            assert k in path2text, f'Key {k} not found in decrypted folder'
            assert og_path2text[k] == path2text[k], f'Value for key {k} does not match original'
        m.rmmod(mod)
        assert m.mod_exists(mod) == False, f'Failed to remove mod {mod}'
        return {'msg': 'Encryption and decryption successful'}
