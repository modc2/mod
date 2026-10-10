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
            value = self.open_db()[key]
            return {'ok': True, 'key': key, 'value': value}
        except KeyError:
            return {'ok': False, 'key': key, 'error': 'key not found'}

    def delete(self, key: str):
        db = self.open_db()
        try:
            db[key]  # raises KeyError if absent
        except KeyError:
            return {'ok': False, 'key': key, 'error': 'key not found'}
        del db[key]
        return {'ok': True, 'key': key}

    def list_keys(self, prefix: str = "", limit: int = None):
        try:
            db = self.open_db()
            it = db.iter()
            if prefix:
                it.seek(prefix)
            else:
                it.seek_to_first()
            keys = []
            while it.valid():
                if limit is not None and len(keys) >= limit:
                    break
                k = it.key()
                if prefix and not k.startswith(prefix):
                    break
                keys.append(k)
                it.next()
            return {'ok': True, 'keys': keys, 'count': len(keys)}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def scan(self, prefix: str = "", limit: int = None):
        try:
            db = self.open_db()
            it = db.iter()
            if prefix:
                it.seek(prefix)
            else:
                it.seek_to_first()
            result = {}
            while it.valid():
                if limit is not None and len(result) >= limit:
                    break
                k = it.key()
                if prefix and not k.startswith(prefix):
                    break
                result[k] = it.value()
                it.next()
            return {'ok': True, 'items': result, 'count': len(result)}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def batch_put(self, items: dict):
        db = self.open_db()
        batch = db.write_batch()
        for k, v in items.items():
            batch.put(k, v)
        batch.write()
        return {'ok': True, 'count': len(items)}

    def batch_delete(self, keys: list):
        db = self.open_db()
        batch = db.write_batch()
        for k in keys:
            batch.delete(k)
        batch.write()
        return {'ok': True, 'count': len(keys)}

    KNOWN_ACTIONS = {'put', 'get', 'delete', 'list_keys', 'scan', 'batch_put', 'batch_delete'}

    def forward(self, **kwargs):
        action = kwargs.get('action')
        if action not in self.KNOWN_ACTIONS:
            if action is None:
                return self.info()
            return {'ok': False, 'error': f'unknown action: {action}'}
        try:
            if action == 'put':
                return self.put(kwargs['key'], kwargs['value'])
            if action == 'get':
                return self.get(kwargs['key'])
            if action == 'delete':
                return self.delete(kwargs['key'])
            if action == 'list_keys':
                return self.list_keys(kwargs.get('prefix', ''), kwargs.get('limit'))
            if action == 'scan':
                return self.scan(kwargs.get('prefix', ''), kwargs.get('limit'))
            if action == 'batch_put':
                return self.batch_put(kwargs['items'])
            if action == 'batch_delete':
                return self.batch_delete(kwargs['keys'])
        except KeyError as e:
            return {'ok': False, 'error': f'missing required parameter: {e.args[0]}'}

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
