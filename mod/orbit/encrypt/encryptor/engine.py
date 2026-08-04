"""
engine — circuits in, ciphertext out, store in the middle.

The whole module in one paragraph: a user brings a *circuit* (a python file
exposing `encrypt(data, key, params)` / `decrypt(data, key, params)`), we prove
it round-trips inside the sandbox, then we use it to encrypt messages
server-side. The ciphertext goes to the store mod under the caller's own
identity and comes back as a CID. The key exists only for the length of one
request; it is never written to disk, never logged, never put in a message row.

Everything the user gave us, the user can take back and delete: circuit sources
download, ciphertext downloads, and deletes reach through to the store.
"""
import base64
import os
import secrets
import time
from typing import Optional

from . import sandbox
from .sandbox import CircuitError
from .storeclient import Store, StoreError
from .vault import Vault, digest

SELFTEST_KEY = b'mod-encrypt-selftest-key-0123456'


class NotFound(Exception):
    pass


class AccessDenied(Exception):
    pass


class Engine:

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.config = cfg
        self.vault = Vault()
        self.store = Store(cfg.get('store_url', 'http://localhost:50152'),
                           activator=cfg.get('activator_url', 'http://localhost:9000'))
        sb = dict(cfg.get('sandbox') or {})
        self.sandbox_user = os.environ.get('ENCRYPT_SANDBOX_USER', sb.pop('user', 'nobody'))
        sb.pop('network', None)
        self.limits = {k: v for k, v in sb.items()
                       if k in ('timeout', 'cpu_seconds', 'memory_mb')}
        lim = cfg.get('limits') or {}
        self.max_circuit_bytes = int(lim.get('max_circuit_bytes', 256 * 1024))
        self.max_message_bytes = int(lim.get('max_message_bytes', 8 * 1024 * 1024))

    # ── sandbox plumbing ─────────────────────────────────────────────

    def _run(self, source: str, op: str, **kw) -> bytes:
        return sandbox.transform(source, op, limits=self.limits,
                                 user=self.sandbox_user, **kw)

    def capabilities(self) -> dict:
        return sandbox.capabilities(self.sandbox_user)

    def status(self, token: Optional[str] = None) -> dict:
        out = {
            'module': 'encrypt',
            'sandbox': self.capabilities(),
            'store': self.store.health(),
            'vault': self.vault.stats(),
            'limits': {'circuit_bytes': self.max_circuit_bytes,
                       'message_bytes': self.max_message_bytes, **self.limits},
        }
        if token:
            try:
                out['me'] = self.store.me(token)
            except StoreError as e:
                out['me'] = {'error': str(e)}
        return out

    # ── circuits ─────────────────────────────────────────────────────

    def circuits(self, caller: str) -> list:
        caller = (caller or '').lower()
        rows = [r for r in self.vault.circuits().values()
                if r.get('owner') == caller or r.get('public')]
        for r in rows:
            r['mine'] = r.get('owner') == caller
            r['messages'] = self.vault.circuit_in_use(r['id'])
        return sorted(rows, key=lambda r: r.get('created_at', 0), reverse=True)

    def circuit(self, cid: str, caller: str) -> dict:
        row = self.vault.circuit(cid)
        if not row:
            raise NotFound(f'no circuit {cid}')
        if row.get('owner') != (caller or '').lower() and not row.get('public'):
            raise AccessDenied('that circuit belongs to someone else')
        return row

    def circuit_source(self, cid: str, caller: str) -> str:
        self.circuit(cid, caller)
        source = self.vault.source(cid)
        if source is None:
            raise NotFound(f'circuit {cid} has no source on disk')
        return source

    def add_circuit(self, token: str, source: bytes, name: str, owner: str,
                    public: bool = False, pin: bool = True,
                    cid: Optional[str] = None) -> dict:
        """Validate a brought circuit and register it. The roundtrip selftest is
        the gate: code that cannot decrypt what it encrypted never gets stored,
        and neither does a pass-through 'cipher'."""
        if isinstance(source, str):
            source = source.encode()
        if not source.strip():
            raise CircuitError('empty circuit')
        if len(source) > self.max_circuit_bytes:
            raise CircuitError(f'circuit is {len(source)} bytes, limit is {self.max_circuit_bytes}')
        text = source.decode('utf-8', 'replace')
        test = sandbox.selftest(text, SELFTEST_KEY, limits=self.limits, user=self.sandbox_user)

        if pin and not cid:
            # The circuit itself becomes a shareable object: whoever holds the
            # CID can install the exact code that produced a ciphertext.
            cid = self.store.put(token, source, name=f'{name or "circuit"}.py',
                                 key=f'encrypt/circuit/{name or "circuit"}.py',
                                 public=public)['cid']
        return self.vault.add_circuit(source, name=name or 'circuit', owner=owner,
                                      public=public, cid=cid,
                                      selftest={'ok': True, 'at': int(time.time()),
                                                'ciphertext_bytes': test.get('ciphertext_bytes')})

    def install_circuit(self, token: str, cid: str, owner: str,
                        name: Optional[str] = None, public: bool = False) -> dict:
        """Install a circuit someone shared by store CID — the receiving half of
        `add_circuit`'s pin. Re-validated here; a CID is not a promise."""
        source = self.store.get(token, cid)
        return self.add_circuit(token, source, name=name or f'installed-{cid[:8]}',
                                owner=owner, public=public, pin=False, cid=cid)

    def rm_circuit(self, token: str, cid: str, caller: str, force: bool = False) -> dict:
        row = self.circuit(cid, caller)
        if row.get('owner') != (caller or '').lower():
            raise AccessDenied('only the uploader can delete a circuit')
        in_use = self.vault.circuit_in_use(cid)
        if in_use and not force:
            raise AccessDenied(
                f'{in_use} message(s) still need this circuit to decrypt — '
                'download it first, then delete with force=1')
        store_removed = None
        if row.get('cid'):
            try:
                self.store.rm(token, row['cid'])
                store_removed = True
            except StoreError as e:
                store_removed = str(e)
        self.vault.rm_circuit(cid)
        return {'deleted': cid, 'store_removed': store_removed, 'messages_orphaned': in_use}

    # ── messages ─────────────────────────────────────────────────────

    @staticmethod
    def _key_bytes(key: Optional[str] = None, key_b64: Optional[str] = None) -> bytes:
        if key_b64:
            return base64.b64decode(key_b64)
        if key:
            return key.encode()
        raise CircuitError('a key is required — this module never keeps one for you')

    def _mine(self, mid: str, caller: str) -> dict:
        row = self.vault.message(mid)
        if not row:
            raise NotFound(f'no message {mid}')
        if row.get('owner') != (caller or '').lower():
            raise AccessDenied('that message belongs to someone else')
        return row

    def messages(self, caller: str) -> list:
        return self.vault.owned(self.vault.messages(), caller)

    def message(self, mid: str, caller: str) -> dict:
        return self._mine(mid, caller)

    def encrypt(self, token: str, owner: str, circuit: str, data: bytes,
                key: Optional[str] = None, key_b64: Optional[str] = None,
                label: Optional[str] = None, public: bool = False, burn: bool = False,
                params: Optional[dict] = None) -> dict:
        """Encrypt with the caller's circuit and store the ciphertext."""
        if len(data) > self.max_message_bytes:
            raise CircuitError(f'message is {len(data)} bytes, limit is {self.max_message_bytes}')
        row = self.circuit(circuit, owner)
        source = self.circuit_source(circuit, owner)
        params = params or {}
        ciphertext = self._run(source, 'encrypt', data=data,
                               key=self._key_bytes(key, key_b64), params=params)

        mid = 'm' + secrets.token_hex(8)
        name = f'{label or mid}.enc'
        put = self.store.put(token, ciphertext, name=name, key=f'encrypt/{mid}',
                             public=public)
        return self.vault.add_message({
            'id': mid,
            'owner': (owner or '').lower(),
            'circuit': circuit,
            'circuit_name': row.get('name'),
            'circuit_sha256': row.get('sha256'),
            'cid': put['cid'],
            'bytes': len(ciphertext),
            'plaintext_bytes': len(data),
            'sha256': digest(ciphertext),
            'label': label or '',
            'params': params,
            'public': bool(public),
            'burn': bool(burn),
            'mode': 'server',
            'created_at': int(time.time()),
        })

    def attach(self, token: str, owner: str, cid: str, circuit: Optional[str] = None,
               label: Optional[str] = None, burn: bool = False,
               params: Optional[dict] = None) -> dict:
        """Register a blob the caller encrypted themselves and uploaded to the
        store. The strongest mode: the server never held the key or the
        plaintext, and still gives you the same list/download/delete surface."""
        info = self.store.object(token, cid)
        mid = 'm' + secrets.token_hex(8)
        return self.vault.add_message({
            'id': mid,
            'owner': (owner or '').lower(),
            'circuit': circuit,
            'circuit_name': (self.vault.circuit(circuit) or {}).get('name') if circuit else None,
            'circuit_sha256': (self.vault.circuit(circuit) or {}).get('sha256') if circuit else None,
            'cid': cid,
            'bytes': int(info.get('size') or 0),
            'sha256': None,
            'label': label or '',
            'params': params or {},
            'public': info.get('visibility') == 'public',
            'burn': bool(burn),
            'mode': 'client',
            'created_at': int(time.time()),
        })

    def ciphertext(self, token: str, caller: str, mid: str, burn: bool = False) -> tuple:
        """Raw ciphertext for the caller to decrypt with their own copy of the
        circuit. Returns (bytes, row)."""
        row = self._mine(mid, caller)
        data = self.store.get(token, row['cid'])
        if burn or row.get('burn'):
            self.delete(token, caller, mid)
        return data, row

    def open(self, token: str, caller: str, mid: str, key: Optional[str] = None,
             key_b64: Optional[str] = None) -> dict:
        """Fetch + decrypt server-side. Burn-after-read messages delete on the
        way out — after the plaintext is in hand, never before."""
        row = self._mine(mid, caller)
        if not row.get('circuit'):
            raise NotFound('this message was attached without a circuit — download it instead')
        source = self.circuit_source(row['circuit'], caller)
        ciphertext = self.store.get(token, row['cid'])
        plaintext = self._run(source, 'decrypt', data=ciphertext,
                              key=self._key_bytes(key, key_b64),
                              params=row.get('params') or {})
        out = {'id': mid, 'bytes': len(plaintext), 'label': row.get('label'),
               'circuit': row.get('circuit'), 'burned': False}
        try:
            out['text'] = plaintext.decode()
        except UnicodeDecodeError:
            out['data_b64'] = base64.b64encode(plaintext).decode()
        if row.get('burn'):
            self.delete(token, caller, mid)
            out['burned'] = True
        return out

    def publish(self, token: str, caller: str, mid: str, public: bool) -> dict:
        row = self._mine(mid, caller)
        self.store.publish(token, row['cid'], public)
        return self.vault.update_message(mid, public=bool(public))

    def delete(self, token: str, caller: str, mid: str) -> dict:
        """Delete server-side: the store object goes, then the metadata row.

        The result reports what the store *actually* did. As of store 1.x, /rm
        drops the index entry but the localfs backend keeps the bytes, so a CID
        someone already holds can still be fetched — we verify and say so rather
        than claim a deletion we did not get."""
        row = self._mine(mid, caller)
        store_removed = True
        try:
            self.store.rm(token, row['cid'])
        except StoreError as e:
            # A 404 means it is already gone — anything else is worth reporting,
            # but the local row still goes so the vault never lies about state.
            store_removed = 'already gone' if e.status == 404 else str(e)
        if store_removed is True:
            try:
                if self.store.readable(token, row['cid']):
                    store_removed = ('index entry removed, but the store backend still serves '
                                     'these bytes to anyone holding the CID')
            except StoreError as e:
                store_removed = f'store accepted the delete, but it could not be verified: {e}'
        self.vault.rm_message(mid)
        return {'deleted': mid, 'cid': row['cid'], 'store_removed': store_removed}

    def purge(self, token: str, caller: str) -> dict:
        results = [self.delete(token, caller, r['id']) for r in self.messages(caller)]
        return {'deleted': len(results), 'results': results}
