import json
import os
import glob
import time
import shutil
from typing import Optional, Union
import mod as m


class Store:

    expose = ['get', 'put', 'ls', 'serve', 'stop', 'app', 'api', 'backends']

    def __init__(self, path='~/.mod/store', password=None, filetype='json', private=False):
        self.path = self.abspath(path)
        self.set_filetype(filetype)
        self.private = private
        self.key = self.get_key(password or 'mod_default_store_password')
        if self.private:
            self.encrypt_all()

    # ── app / api launcher ───────────────────────────────────────────

    def _module_root(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def serve(self, no_app: bool = False, no_api: bool = False, prod: bool = False, **kw):
        """Launch the gateway + app under pm2 (store-api / store-app).

        Wraps serve.sh, which runs `pm2 start ecosystem.config.js`. Returns once
        pm2 has registered the processes (pm2 itself daemonizes them)."""
        import subprocess
        script = os.path.join(self._module_root(), 'serve.sh')
        if not os.path.exists(script):
            return {'error': f'serve.sh not found at {script}'}
        args = ['bash', script]
        if no_app:
            args.append('--no-app')
        if no_api:
            args.append('--no-api')
        if prod:
            args.append('--prod')
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=600)
        except Exception as e:
            return {'error': str(e)}
        return {
            'pm2': ['store-api', 'store-app'],
            'api': 'http://localhost:50150',
            'app': 'http://localhost:50151',
            'returncode': out.returncode,
            'stdout': out.stdout[-2000:],
            'stderr': out.stderr[-2000:],
        }

    def stop(self, **kw):
        """Stop the pm2-managed store processes."""
        import subprocess
        script = os.path.join(self._module_root(), 'serve.sh')
        out = subprocess.run(['bash', script, 'stop'], capture_output=True, text=True)
        return {'returncode': out.returncode, 'stdout': out.stdout, 'stderr': out.stderr}

    def app(self, **kw):
        """Launch only the Next.js app."""
        return self.serve(no_api=True)

    def api(self, **kw):
        """Launch only the FastAPI gateway."""
        return self.serve(no_app=True)

    def backends(self, **kw):
        """List available backends (local + decentralized)."""
        return {
            'localfs': 'this Store class (key-value on disk)',
            'filecoin': 'm store.filecoin/* — Lotus daemon + gateway (proxies orbit/filecoin)',
            'hippius': 'm store.hippius/* — Substrate node + S3 gateway (proxies orbit/hippius)',
            'unified': 'm dstore/* — filecoin + hippius with SIWE auth',
        }

    # ── path helpers ─────────────────────────────────────────────────

    def abspath(self, path):
        return os.path.abspath(os.path.expanduser(path))

    def get_path(self, path: str, filetype: Optional[str] = None):
        if path.startswith('~') or path.startswith('/') or path.startswith('./'):
            path = self.abspath(path)
        elif not path.startswith(self.path):
            path = f'{self.path}/{path}'
        if filetype and not path.endswith(f'.{filetype}'):
            path += f'.{filetype}'
        return path

    def ensure_path(self, path):
        d = os.path.dirname(path)
        os.makedirs(d, exist_ok=True)
        return {'path': d}

    def in_path(self, path):
        return path.startswith(self.path)

    def set_filetype(self, filetype):
        assert filetype in ['json', 'yaml'], f'File type {filetype} not supported'
        self.filetype = filetype
        return self.filetype

    # ── core I/O ─────────────────────────────────────────────────────

    def get_text(self, path: str) -> str:
        with open(path, 'r') as f:
            return f.read()

    def put_text(self, path: str, text: str) -> str:
        with open(path, 'w') as f:
            f.write(text)
        return path

    def get_json(self, path: str) -> Union[dict, list]:
        path = self.get_path(path, filetype=self.filetype)
        return json.loads(self.get_text(path), strict=False)

    def put_json(self, path: str, data: Union[dict, list] = None) -> str:
        path = self.get_path(path, filetype=self.filetype)
        self.ensure_path(path)
        with open(path, 'w') as f:
            json.dump(data, f, indent=4)
        return path

    def get(self, path, default=None, max_age=None, update=False, password=None):
        path = self.get_path(path, filetype=self.filetype)
        if not os.path.exists(path):
            return default
        if max_age is not None and self.get_age(path) > max_age:
            return default
        if update:
            return default
        data = self.validate_data(self.get_json(path))
        if self.private or password is not None:
            return self.decrypt_data(data, password=password)
        return data

    def put(self, path, data, password=None):
        self.put_json(path, data)
        if self.private or password is not None:
            self.encrypt(path, password=password)
        return {'path': path, 'encrypted': self.is_encrypted(path)}

    def rm(self, path):
        path = self.get_path(path, filetype=self.filetype)
        assert os.path.exists(path), f'Path not found: {path}'
        assert self.in_path(path), f'Path {path} outside store {self.path}'
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        return path

    def rm_all(self):
        paths = self.paths()
        for p in paths:
            self.rm(p)
        return paths

    def validate_data(self, data: Union[dict, list]) -> Union[dict, list]:
        if isinstance(data, dict) and 'data' in data and ('time' in data or 'timestamp' in data):
            return data['data']
        return data

    # ── listing / querying ───────────────────────────────────────────

    def paths(self, path=None, search=None, avoid=None, max_age=None):
        path = self.get_path(path or self.path)
        paths = [self.abspath(p) for p in glob.glob(f'{path}/**/*', recursive=True) if os.path.isfile(p)]
        if search:
            paths = [p for p in paths if search in p]
        if avoid:
            paths = [p for p in paths if avoid not in p]
        if max_age is not None:
            paths = [p for p in paths if time.time() - os.path.getmtime(p) < max_age]
        return paths

    files = paths

    def keys(self, search=None, avoid=None, max_age=None):
        return [self.shorten_item_path(p) for p in self.paths(search=search, avoid=avoid, max_age=max_age)]

    def values(self, path=None, search=None, avoid=None, max_age=None):
        out = []
        for p in self.paths(path=path, search=search, avoid=avoid, max_age=max_age):
            try:
                out.append(self.get(p))
            except Exception:
                pass
        return out

    def items(self, search=None):
        result = {}
        for k in self.keys(search=search):
            try:
                result[k] = self.get(k)
            except Exception:
                pass
        return result

    def ls(self, path=None, search=None, avoid=None):
        path = self.abspath(path or self.path)
        if not os.path.exists(path):
            return []
        return [f'{path}/{p}' for p in os.listdir(path)]

    def exists(self, path):
        return os.path.exists(self.get_path(path)) or os.path.exists(self.get_path(path, filetype=self.filetype))

    def get_age(self, path, default=None):
        path = self.get_path(path, filetype=self.filetype)
        if not os.path.exists(path):
            return default
        return time.time() - os.path.getmtime(path)

    def item2age(self):
        return {p: time.time() - os.path.getmtime(p) for p in self.paths()}

    def shorten_item_path(self, path):
        return path.replace(self.path + '/', '').replace(f'.{self.filetype}', '')

    def path2name(self, path: str) -> str:
        if path.startswith(self.path):
            path = path[len(self.path) + 1:]
        if path.endswith('.json'):
            path = path[:-5]
        return path

    def path2text(self, folder_path: str) -> dict:
        folder_path = self.abspath(folder_path)
        assert os.path.exists(folder_path), f'Folder not found: {folder_path}'
        assert os.path.isdir(folder_path), f'Not a folder: {folder_path}'
        return {
            os.path.relpath(p, folder_path): self.get_text(p)
            for p in glob.glob(f'{folder_path}/**/*', recursive=True)
            if os.path.isfile(p)
        }

    file2text = path2text

    def stats(self, path=None):
        path = self.get_path(path) if path else self.path
        return m.df([
            {'path': p.replace(path + '/', '')[:-5], 'age': self.get_age(p), 'size': os.path.getsize(p), 'encrypted': self.is_encrypted(p)}
            for p in self.paths(path)
        ])

    # ── encryption ───────────────────────────────────────────────────

    def get_key(self, password: str = None):
        if password is None:
            assert hasattr(self, 'key')
            return self.key
        return m.mod('key.aes')(password=password)

    def is_encrypted(self, path) -> bool:
        obj = self.get_json(path) if isinstance(path, str) else path
        return isinstance(obj, dict) and 'data' in obj

    def encrypt_data(self, data, password=None) -> str:
        return self.get_key(password).encrypt(data)

    def decrypt_data(self, data, password=None):
        if isinstance(data, dict) and 'data' in data:
            data = data['data']
        return self.get_key(password).decrypt(data)

    def encrypt(self, path, password=None, save=True):
        obj = self.get_json(path)
        if self.is_encrypted(path):
            return path
        result = {'data': self.encrypt_data(obj, password=password)}
        if save:
            self.put_json(path, result)
        return path

    def decrypt(self, path, password=None, save=True):
        obj = self.get_json(path)
        result = self.decrypt_data(obj, password=password)
        if save:
            self.put(path, result)
        return result

    def encrypt_all(self, password=None):
        for p in self.paths():
            if not self.is_encrypted(p):
                try:
                    self.encrypt(p, password=password)
                except Exception:
                    pass
        return self.stats()

    def decrypt_all(self, password=None):
        for p in self.paths():
            if self.is_encrypted(p):
                try:
                    self.decrypt(p, password=password)
                except Exception:
                    pass
        return self.stats()

    def encrypt_folder(self, folder_path: str, password='fam'):
        folder_path = self.abspath(folder_path)
        path2text = self.path2text(folder_path)
        encrypted = self.encrypt_data(path2text, password=password)
        self.put_text(f'{folder_path}/encrypted.txt', encrypted)
        for p in path2text:
            full = os.path.join(folder_path, p)
            if os.path.isfile(full):
                os.remove(full)
        return encrypted

    def decrypt_folder(self, folder_path, password=None):
        folder_path = self.abspath(folder_path)
        enc_path = f'{folder_path}/encrypted.txt'
        assert os.path.exists(enc_path), f'No encrypted data at {enc_path}'
        path2text = self.decrypt_data(self.get_text(enc_path), password=password)
        for rel, text in path2text.items():
            self.put_text(os.path.join(folder_path, rel), text)
        os.remove(enc_path)
        return path2text
