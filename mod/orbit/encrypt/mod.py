"""
encrypt — an encrypted message vault whose cryptography you bring yourself.

The module ships no cipher of its own. You upload a *circuit*: a python file
exposing

    encrypt(data: bytes, key: bytes, params: dict) -> bytes
    decrypt(data: bytes, key: bytes, params: dict) -> bytes

It is validated by an encrypt→decrypt roundtrip inside a sandbox (no network,
dropped privileges, cpu/memory/file limits) and then used to encrypt messages
server-side. The ciphertext is uploaded to the **store** mod under *your* wallet
identity and comes back as a CID; the metadata index here holds only the CID,
the circuit id and a label. Keys live in one request and are never persisted.

Everything you put in, you can take back and destroy: circuits and ciphertext
both download, and delete reaches through to the store object.

CLI:
    m encrypt                                   # null call → info()
    m encrypt/status                            # sandbox + store + vault state
    m encrypt/examples                          # the reference circuits shipped here
    m encrypt/add_circuit circuits/aes_gcm.py   # bring a circuit
    m encrypt/circuits                          # what I can encrypt with
    m encrypt/encrypt "meet at nine" circuit=c… key=hunter2 label=note
    m encrypt/messages                          # my ciphertext, by CID
    m encrypt/open m1234… key=hunter2           # decrypt server-side
    m encrypt/download m1234… out=./note.enc    # raw ciphertext, decrypt yourself
    m encrypt/rm m1234…                         # delete server-side (store + row)
    m encrypt/purge confirm=1                   # delete all of it
    m encrypt/serve                             # api :50380 + console :50381
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import mod as m

MODULE_DIR = Path(__file__).resolve().parent
# The orbit loader imports this file by path, so our own package needs help
# being importable. Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from encryptor import Engine  # noqa: E402


class Mod:
    description = 'Encrypted messages with bring-your-own-circuit cryptography, stored as CIDs in the store mod'

    def __init__(self, key: Optional[str] = None):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 50380))
        self.app_port = int(self.config.get('app_port', 50381))
        self.key_name = key or os.environ.get('ENCRYPT_KEY')
        self.engine = Engine(self.config)

    # ── identity ─────────────────────────────────────────────────────

    def token(self, key: Optional[str] = None) -> str:
        """Mint a protocol token for the store, signed by a local mod key.

        Auth().token(key=…) signs with one key while *declaring* another, so the
        signing key is set on the constructor instead."""
        name = key or self.key_name
        auth = m.mod('auth')(key=name, crypto_type='ecdsa') if name \
            else m.mod('auth')(crypto_type='ecdsa')
        return auth.token({'scope': 'encrypt'})

    def address(self, key: Optional[str] = None) -> str:
        name = key or self.key_name
        return (m.key(name) if name else m.key()).address.lower()

    # ── info ─────────────────────────────────────────────────────────

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {
            'name': 'encrypt',
            'description': self.description,
            'path': str(self.module_dir),
            'api': self.config['urls']['api'],
            'app': self.config['urls']['app'],
            'store': self.engine.store.url,
            'circuit_contract': 'encrypt(data: bytes, key: bytes, params: dict) -> bytes '
                                'and decrypt(data, key, params) -> bytes',
            'examples': self.examples(),
            'endpoints': sorted(self.config.get('endpoints', {})),
            'fns': self.config.get('fns', []),
        }

    def health(self) -> dict:
        return {'ok': True, 'module': 'encrypt', 'sandbox': self.engine.capabilities()}

    def status(self, key: Optional[str] = None) -> dict:
        try:
            return self.engine.status(self.token(key))
        except Exception:
            return self.engine.status()

    def examples(self) -> list:
        """The reference circuits shipped with the module — nothing is installed
        until you bring one with add_circuit."""
        d = self.module_dir / 'circuits'
        return [{'name': p.stem, 'path': str(p), 'bytes': p.stat().st_size}
                for p in sorted(d.glob('*.py'))]

    # ── circuits ─────────────────────────────────────────────────────

    def circuits(self, key: Optional[str] = None) -> list:
        return self.engine.circuits(self.address(key))

    def add_circuit(self, path: str, name: Optional[str] = None, public: bool = False,
                    pin: bool = True, key: Optional[str] = None) -> dict:
        """Bring a circuit: validate it in the sandbox, register it, pin it to
        the store so it can be shared by CID."""
        p = Path(os.path.expanduser(path))
        if not p.exists():
            p = self.module_dir / path                      # allow `circuits/aes_gcm.py`
        if not p.exists():
            return {'error': f'no such file: {path}'}
        return self.engine.add_circuit(self.token(key), p.read_bytes(),
                                       name=name or p.stem, owner=self.address(key),
                                       public=bool(public), pin=bool(pin))

    def install_circuit(self, cid: str, name: Optional[str] = None, public: bool = False,
                        key: Optional[str] = None) -> dict:
        """Install a circuit someone shared with you by store CID."""
        return self.engine.install_circuit(self.token(key), cid, owner=self.address(key),
                                           name=name, public=bool(public))

    def source(self, circuit: str, out: Optional[str] = None, key: Optional[str] = None):
        """Print (or write out) a circuit's source — take your code back."""
        src = self.engine.circuit_source(circuit, self.address(key))
        if out:
            Path(os.path.expanduser(out)).write_text(src)
            return {'circuit': circuit, 'out': out, 'bytes': len(src)}
        return src

    def rm_circuit(self, circuit: str, force: bool = False, key: Optional[str] = None) -> dict:
        return self.engine.rm_circuit(self.token(key), circuit, self.address(key),
                                      force=bool(force))

    # ── messages ─────────────────────────────────────────────────────

    def encrypt(self, text: str = None, circuit: str = None, key: str = None,
                path: Optional[str] = None, label: Optional[str] = None,
                public: bool = False, burn: bool = False, params: Optional[dict] = None,
                signer: Optional[str] = None) -> dict:
        """Encrypt text (or a file) with one of your circuits and store the
        ciphertext. `key` is the passphrase — it is never written down."""
        if not circuit:
            return {'error': 'circuit=<id> required — see m encrypt/circuits'}
        if not key:
            return {'error': 'key=<passphrase> required — nothing is stored for you'}
        if path:
            data = Path(os.path.expanduser(path)).read_bytes()
            label = label or Path(path).name
        elif text is not None:
            data = text.encode()
        else:
            return {'error': 'nothing to encrypt: pass text or path='}
        return self.engine.encrypt(self.token(signer), self.address(signer), circuit=circuit,
                                   data=data, key=key, label=label, public=bool(public),
                                   burn=bool(burn), params=params)

    def messages(self, signer: Optional[str] = None) -> list:
        return self.engine.messages(self.address(signer))

    def open(self, id: str, key: str = None, out: Optional[str] = None,
             signer: Optional[str] = None):
        """Decrypt server-side. Burn-after-read messages delete themselves here."""
        if not key:
            return {'error': 'key=<passphrase> required'}
        result = self.engine.open(self.token(signer), self.address(signer), id, key=key)
        if out:
            data = result['text'].encode() if 'text' in result \
                else base64.b64decode(result['data_b64'])
            Path(os.path.expanduser(out)).write_bytes(data)
            result['out'] = out
            result.pop('text', None)
            result.pop('data_b64', None)
        return result

    def download(self, id: str, out: Optional[str] = None, burn: bool = False,
                 signer: Optional[str] = None) -> dict:
        """Pull the raw ciphertext down so you can decrypt it yourself."""
        data, row = self.engine.ciphertext(self.token(signer), self.address(signer), id,
                                           burn=bool(burn))
        out = out or f'./{row.get("label") or id}.enc'
        Path(os.path.expanduser(out)).write_bytes(data)
        return {'id': id, 'out': out, 'bytes': len(data), 'cid': row.get('cid'),
                'circuit': row.get('circuit'),
                'deleted': bool(burn or row.get('burn'))}

    def rm(self, id: str, signer: Optional[str] = None) -> dict:
        """Delete server-side: the store object first, then the metadata row."""
        return self.engine.delete(self.token(signer), self.address(signer), id)

    def purge(self, confirm: bool = False, signer: Optional[str] = None) -> dict:
        if not confirm:
            return {'error': 'pass confirm=1 — this deletes every message you own'}
        return self.engine.purge(self.token(signer), self.address(signer))

    # ── serve / register ─────────────────────────────────────────────

    def _pm2_start(self, name, cmd, cwd=None, env=None) -> bool:
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name]
        if cwd:
            pm2_cmd += ['--cwd', cwd]
        pm2_cmd += ['--'] + list(cmd[1:])
        r = subprocess.run(pm2_cmd, capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        if r.returncode != 0:
            print(r.stderr[-800:])
        return r.returncode == 0

    def serve_api(self, port=None, reload: bool = False) -> dict:
        port = int(port or self.port)
        repo_root = str(self.module_dir.parent.parent.parent)
        env = {'PYTHONPATH': f'{repo_root}:{self.module_dir}', 'PORT': str(port)}
        cmd = ['python3', '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0',
               '--port', str(port), '--app-dir', str(self.module_dir / 'api')]
        if reload:
            cmd.append('--reload')
        # cwd = repo root on purpose: `python -m uvicorn` puts the working dir
        # at sys.path[0], and this module's own mod.py would shadow the `mod`
        # package if we started from the module directory.
        ok = self._pm2_start('encrypt.api', cmd, cwd=repo_root, env=env)
        return {'api': f'http://localhost:{port}', 'pm2': 'encrypt.api',
                'docs': f'http://localhost:{port}/docs', 'ok': ok}

    def serve_app(self, app_port=None, port=None) -> dict:
        app_port = int(app_port or self.app_port)
        env = {'PORT': str(app_port), 'API_PORT': str(int(port or self.port)),
               'BASE_PATH': '/encrypt'}
        cmd = ['node', 'server.js']
        ok = self._pm2_start('encrypt.app', cmd, cwd=str(self.module_dir / 'app'), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'encrypt.app', 'ok': ok}

    def serve(self, port=None, app_port=None) -> dict:
        """Start the API and the console under pm2, then register the route."""
        out = {'api': self.serve_api(port=port),
               'app': self.serve_app(app_port=app_port, port=port)}
        out['registration'] = self.register()
        return out

    def kill(self) -> dict:
        killed = [n for n in ('encrypt.api', 'encrypt.app')
                  if subprocess.run(['pm2', 'delete', n], capture_output=True,
                                    text=True).returncode == 0]
        return {'killed': killed}

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('encrypt', app_url)
            ns.reg_app('encrypt', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/encrypt'
            print(f'encrypt registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'encrypt: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('encrypt')
            return {'ok': True, 'deregistered': 'encrypt'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def readme(self):
        return m.get_text(str(self.module_dir / 'README.md'))
