
import json
import os
import time
import shutil
from typing import Optional, Union
import mod as m

class Store:
    endpoints=['get', 'put', 'ls']

    def __init__(self, path='~/.mod',  file_type='json',  password = None ):

        """
        Store class to manage the storage of data in files

        path: str: the path of the path where the data is stored
        file_type: str: the file_type of the files (json, txt, etc)
        """
        self.path = self.abspath(path)
        self.file_type = file_type
        self.private = False
        if password:
            self.private = True
            self.key = self.get_key(password)
            self.encrypt_all()
        # print(f'Store private={self.private} path={self.path} file_type={self.file_type}')
        
    def put(self, path, data, password=None):
        path = self.get_path(path, file_type=self.file_type)
        self.ensure_path(path)
        with open(path, 'w') as f:
            json.dump(data, f)
        if self.private or password != None:
            self.encrypt(path, password=password)
        return {'path': path, 'encrypted': self.is_encrypted(path)}
    
    def shorten_item_path(self, path):
        return path.replace(self.path+'/', '').replace(f'.{self.file_type}', '')

    def ensure_path(self, path):
        """
        Ensure that the directory exists
        """
        path = os.path.dirname(path)
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        return {'path': path}

    def get(self, path, default=None, max_age=None, update=False, password=None, verbose=False):
        """
        Get the data from the file
        params
            path: str: the path of the file (relative to the self.path)
            default: any: the default value to return if the file does not exist
            max_age: int: the maximum age of the file in seconds (update if too old)
            update: bool: if True, update the file if it is too old

        """
        path = self.get_path(path, file_type=self.file_type)
        if not os.path.exists(path):
            return default
        data = self.get_json(path)
        data = self.validate_data(data)
        update =  update or  bool(max_age != None and self.get_age(path) > max_age)
        if update:
            return default
        if self.private or password != None:
            return self.decrypt_data(data, password=password)
        return data

    def validate_data(self, data: Union[dict, list]) -> Union[dict, list]:
        """
        
        """
        if isinstance(data, dict) and 'data' in data and ('time' in data or 'timestamp' in data):
            data = data['data']
        return data

    def get_age(self, path, default=None):
        """
        Get the age of the file
        params
        """
        path = self.get_path(path, file_type=self.file_type)
        if not os.path.exists(path):
            return default
        return time.time() - os.path.getmtime(path)

    def get_path(self, path:str, file_type:Optional[str]=None):
        """
        Get the path of the file
        params
            path: str: the path of the file
            file_type: str: the file_type of the file (json, txt, etc)
        return: str: the path of the file
        """
        if  path.startswith('~') or path.startswith('/') or path.startswith('./'):
            path = self.abspath(path)
        elif not path.startswith(self.path):
            path = f'{self.path}/{path}'
        if file_type != None:
            file_type = f'.{file_type}'
            if not path.endswith(file_type):
                path += file_type
        return path

    def in_path(self, path):
        return path.startswith(self.path)

    def rm(self, path):
        path = self.get_path(path, file_type=self.file_type)
        assert os.path.exists(path), f'Failed to find path {path}'
        assert self.in_path(path), f'Path {path} is not in path {self.path}'
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return path

    def rm_all(self):
        """
        Remove all items in the storage
        """
        paths = self.paths()
        for p in paths:
            self.rm(p)
        return paths

    def values(self, path=None, search=None, avoid=None, max_age=None, verbose=False):
        values = []
        for p in self.paths(path=None, search=search, avoid=avoid, max_age=max_age):
            try:
                values.append( self.get(p))
            except Exception as e:
                if verbose:
                    print(f'Failed to get {p} error={e}')
        return values
        return [self.get(p) for p in self.paths(path=None, search=search, avoid=avoid, max_age=max_age)]
    
    def keys(self, search=None, avoid=None, max_age=None):
        """
        Get the keys in the storage
        """
        paths = self.paths(search=search, avoid=avoid, max_age=max_age)
        keys = [self.shorten_item_path(p) for p in paths]
        return keys

    def items(self, search=None):
        """
        Get the items in the storage
        """
        keys = self.keys(search=search)
        data = []
        path2data = {}
        for p in keys:
            try:
                path2data[p] = self.get(p)
            except Exception as e:
                print(f'Failed to get {p} error={e}')
        return path2data
        
    def ls(self, path=None, search=None, avoid=None):
        path = path or self.path
        path = self.get_path(path)
        if not os.path.exists(path):
            return []
        path = self.abspath(path)
        paths = os.listdir(path)
        paths = [f'{path}/{p}' for p in paths]
        return paths

    def lsdir(self, path='./', search=None, avoid=None):
        path = self.get_path(path)
        return os.listdir(path)

    def paths(self, path=None, search=None, avoid=None, max_age=None):
        import glob
        path = self.get_path(path or self.path)
        paths = glob.glob(f'{path}/**/*', recursive=True)
        paths = [self.abspath(p) for p in paths if os.path.isfile(p)]
        if search != None:
            paths = [p for p in paths if search in p]
        if avoid != None:
            paths = [p for p in paths if avoid not in p]
        if max_age != None:
            paths = [p for p in paths if time.time() - os.path.getmtime(p) < max_age]
        return paths

    def files(self, path=None, search=None, avoid=None):
        return self.paths(path=path,search=search, avoid=avoid)

    def exists(self, path):
        path = self.get_path(path)
        exists =  os.path.exists(path)
        if not exists:
            item_path = self.get_path(path, file_type=self.file_type)
            exists =  os.path.exists(item_path)
        return exists
    def item2age(self):
        """
        returns the age of the item in seconds
        """
        paths = self.paths()
        ages = {}
        for p in paths:
            ages[p] = time.time() - os.path.getmtime(p)
        return ages
        
    def n(self):
        paths = self.items()
        return len(paths)

    def _rm_all(self):
        """
        removes all items in the storage
        """
        paths = self.paths()
        for p in paths:
            os.remove(p)
        return paths

    def abspath(self, path):
        return os.path.abspath(os.path.expanduser(path))

    def get_text(self, path) -> str:
        with open(path, 'r') as f:
            result =  f.read()
        return result

    def get_json(self, path: str= 'test/a')-> Union[dict, list]:
        path = self.get_path(path, file_type=self.file_type)
        data = self.get_text(path)
        data = json.loads(data)
        return data 

    def put_json(self, path: str= 'test/a', data: Union[dict, list]=None) -> str:
        json_data = json.dumps(data, indent=4)
        path = self.get_path(path, file_type=self.file_type)
        self.ensure_path(path)
        with open(path, 'w') as f:
            f.write(json_data)
        return path

    # Encryption methods
    def encrypt_data(self, data, password=None) -> str:
        """
        Encrypt data using the given key
        """
        key = self.get_key(password)
        encrypted_data = key.encrypt(data)
        return encrypted_data

    def encrypt(self, path: str= 'test/a', password=None, save=True) -> str:
        """
        Encrypt a file using the given key
        """
        obj = self.get_json(path)
        if self.is_encrypted(path): 
            return {'msg': 'aready encrytped'}
        result = {'encrypted_data': self.encrypt_data(obj, password=password)}
        assert self.is_encrypted(result), f'Failed to encrypt {result}'
        if save:
            self.put_json(path, result)
            assert self.is_encrypted(path), f'Failed to encrypt {path}'

        return path
    
    # Decryption methods
    def decrypt_data(self, data, password=None) -> str:
        """
        Decrypt data using the given key
        """
        if 'encrypted_data' not in data:
            return data
        data = data['encrypted_data']
        key = self.get_key(password)
        decrypted_data = key.decrypt(data)
        return decrypted_data

    def decrypt(self, path: str= 'test/a', password=None, save=True) -> str:
        """
        Decrypt a file using the given key
        """
        obj = self.get_json(path)
        result = self.decrypt_data(obj, password=password)
        assert not self.is_encrypted(result), f'Failed to decrypt {path}'
        if save:
            self.put(path, result)
        return result

    def is_encrypted(self, path: str= 'test/a') -> bool:
        """
        Check if the file is encrypted using the given key
        """
        if isinstance(path, str):
            obj = self.get_json(path) 
        else:
            obj = path
  
        return bool(isinstance(obj, dict) and 'encrypted_data' in obj)

    def is_private(self, path=None) -> bool:
        """
        Check if the file is private
        """
        return all([self.is_encrypted(p) for p in  self.paths(path=path)])

    def encrypted_paths(self, path=None) -> list:
        """
        Get the paths of the encrypted files
        """
        paths = self.paths(path=path)
        encrypted_paths = []
        for p in paths:
            if self.is_encrypted(p):
                encrypted_paths.append(p)
        return encrypted_paths
    
    def unencrypted_paths(self, path=None) -> list: 
        """
        Get the paths of the unencrypted files
        """
        paths = self.paths(path=path)
        unencrypted_paths = []
        for p in paths:
            if not self.is_encrypted(p):
                unencrypted_paths.append(p)
        return unencrypted_paths

    def path2name(self, path: str) -> str:
        if path.startswith(self.path):
            path = path[len(self.path)+1:]
        if path.endswith('.json'):
            path = path[:-len('.json')]
        return path

    def get_key(self, password: str=None) -> str:
        if password is None:
            return self.key
        return m.mod('key.aes')(password=password or self.password)

    def encrypt_all(self, password=None) -> list:
        """
        Encrypt all files in the given path
        """
        encrypted_paths = []
        for p in self.paths():
            if not self.is_encrypted(p):
                try:
                    encrypted_paths.append(self.encrypt(p, password=password))
                except Exception as e: 
                    print(p)
        assert all([self.is_encrypted(p) for p in encrypted_paths]), f'Failed to encrypt all paths {encrypted_paths}'
        return self.stats()

    def decrypt_all(self, password=None) -> list:
        """
        Decrypt all files in the given path
        """
        decrypted_paths = []
        for p in self.paths():
            if self.is_encrypted(p):
                try:
                    self.decrypt(p, password=password)
                    decrypted_paths.append(p)
                except Exception as e:
                    print(f'Failed to decrypt {p} error={e}')
        assert all([not self.is_encrypted(p) for p in decrypted_paths]), f'Failed to decrypt all paths {decrypted_paths}'
        return self.stats()

    def stats(self, path = None)-> 'df':
        """
        Get the overview of the storage
        """
        path = self.get_path(path) if path else self.path
        paths = self.paths(path)
        data = []
        for p in paths:
            data.append({'path': p.replace(path+'/', '')[:-len('.json')], 'age': self.get_age(p), 'size': os.path.getsize(p), 'encrypted': self.is_encrypted(p)})
        return m.df(data)
