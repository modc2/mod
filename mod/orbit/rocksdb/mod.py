import os
import mod as m
from rocksdict import Rdict

class Mod:
    description = """RocksDB key-value store"""
    path = r'/root/mod/mod/orbit/rocksdb'

    def __init__(self):
        self._db = None

    def open_db(self, db_path=None):
        if self._db is None:
            path = db_path or os.path.join(self.path, 'data.db')
            self._db = Rdict(path)
        return self._db

    def put(self, key: str, value: str):
        self.open_db()[key] = value
        return {'ok': True, 'key': key}

    def get(self, key: str):
        try:
            return self.open_db()[key]
        except KeyError:
            return None

    def delete(self, key: str):
        db = self.open_db()
        try:
            db[key]  # raises KeyError if absent
        except KeyError:
            return {'ok': False, 'key': key, 'error': 'key not found'}
        del db[key]
        return {'ok': True, 'key': key}

    def list_keys(self, prefix: str = ""):
        db = self.open_db()
        it = db.iter()
        it.seek_to_first()
        keys = []
        while it.valid():
            k = it.key()
            if not prefix or k.startswith(prefix):
                keys.append(k)
            it.next()
        return keys

    def forward(self, **kwargs):
        action = kwargs.get('action')
        if action == 'put':
            return self.put(kwargs['key'], kwargs['value'])
        if action == 'get':
            return self.get(kwargs['key'])
        if action == 'delete':
            return self.delete(kwargs['key'])
        if action == 'list_keys':
            return self.list_keys(kwargs.get('prefix', ''))
        return self.info()

    def info(self):
        return {
            'name': 'rocksdb',
            'description': self.description,
            'path': self.path,
            'files': os.listdir(self.path),
        }

    def readme(self):
        for name in ['README.md', 'readme.md', 'README.rst', 'README']:
            p = os.path.join(self.path, name)
            if os.path.exists(p):
                return m.get_text(p)
        return None
