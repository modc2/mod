"""
vaults - per-address key-value vaults backed by the mod store module

Each signed-in address owns any number of named vaults. A vault is a small
key-value store whose entries are either:

    public  - plaintext, readable by anyone via the public endpoint
    private - sealed with AES-256-GCM under a server-side secret before it
              ever reaches disk; listed masked, revealed only to the owner

ALL persistence goes through the mod `store` module (core/store Store class):
vault documents, and the sealing secret itself, live in a Store rooted at
~/.mod/agent/vaults (off-tree, never in the committed module dir).

Usage:
    v = Vaults()
    v.set('0xabc…', 'keys', 'OPENROUTER_API_KEY', 'sk-or-…', private=True)
    v.ls('0xabc…')                      # vault summaries
    v.get('0xabc…', 'keys')             # entries, private values masked
    v.get('0xabc…', 'keys', reveal=True)  # entries with plaintext values
    v.public('0xabc…', 'keys')          # public entries only (anyone)
"""
import os
import re
import base64
import time
from typing import Dict, List, Optional

try:
    import mod as m
except ImportError:
    m = None


NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$')
ENTRY_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$')


def _mask(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 8:
        return value[:1] + '…'
    return f'{value[:4]}…{value[-4:]}'


class Vaults:
    description = "Per-address key-value vaults (public + private entries) stored via the mod store module"

    def __init__(self, path: str = '~/.mod/agent/vaults', store=None):
        self._path = path
        self._store = store          # injected in tests
        self._secret = None

    # ── storage (the mod store module) ───────────────────────────────

    @property
    def store(self):
        """The store module's Store KV class, rooted at the vaults dir."""
        if self._store is None:
            if m is None:
                raise RuntimeError("mod framework unavailable — vaults need the store module")
            self._store = m.mod('store')(path=self._path)
        return self._store

    # ── sealing (AES-256-GCM under a server-side secret) ─────────────

    def _get_secret(self) -> bytes:
        """Random 32-byte sealing secret, persisted through the store."""
        if self._secret is not None:
            return self._secret
        p = self.store.get_path('.secret')
        if os.path.exists(p):
            self._secret = base64.b64decode(self.store.get_text(p))
        else:
            self._secret = os.urandom(32)
            self.store.ensure_path(p)
            self.store.put_text(p, base64.b64encode(self._secret).decode())
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass
        return self._secret

    def _seal(self, value: str) -> dict:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        ct = AESGCM(self._get_secret()).encrypt(nonce, value.encode(), b'agent-vaults-v1')
        return {'nonce': base64.b64encode(nonce).decode(),
                'ct': base64.b64encode(ct).decode()}

    def _open(self, sealed: dict) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(self._get_secret()).decrypt(
            base64.b64decode(sealed['nonce']), base64.b64decode(sealed['ct']),
            b'agent-vaults-v1').decode()

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _addr(address: str) -> str:
        a = (address or '').lower()
        if not (a.startswith('0x') and len(a) == 42 and re.fullmatch(r'0x[0-9a-f]{40}', a)):
            raise PermissionError("sign in to use vaults")
        return a

    @staticmethod
    def _name(name: str) -> str:
        n = (name or '').strip()
        if not NAME_RE.fullmatch(n):
            raise ValueError("vault name must be 1-64 chars: letters, digits, _ . -")
        return n

    def _key(self, address: str, name: str) -> str:
        return f'{self._addr(address)}/{self._name(name)}'

    def _load(self, address: str, name: str) -> Optional[dict]:
        return self.store.get(self._key(address, name))

    def _save(self, address: str, name: str, doc: dict):
        self.store.put(self._key(address, name), doc)

    def _entry_view(self, key: str, e: dict, reveal: bool) -> dict:
        private = bool(e.get('private'))
        out = {'key': key, 'private': private,
               'hint': e.get('hint', ''), 'updated': e.get('updated')}
        if not private:
            out['value'] = e.get('value', '')
        elif reveal:
            try:
                out['value'] = self._open(e.get('sealed') or {})
            except Exception:
                out['error'] = 'unsealing failed'
        return out

    # ── public API ───────────────────────────────────────────────────

    def ls(self, address: str) -> List[dict]:
        """Summaries of the caller's vaults, newest-updated first."""
        addr = self._addr(address)
        out = []
        for k in self.store.keys(search=addr + '/'):
            doc = self.store.get(k)
            if not isinstance(doc, dict):
                continue
            entries = doc.get('entries', {})
            n_private = sum(1 for e in entries.values() if e.get('private'))
            out.append({'name': doc.get('name', k.split('/')[-1]),
                        'entries': len(entries),
                        'private': n_private,
                        'public': len(entries) - n_private,
                        'created': doc.get('created'),
                        'updated': doc.get('updated')})
        return sorted(out, key=lambda v: v.get('updated') or 0, reverse=True)

    def create(self, address: str, name: str) -> dict:
        """Create an empty vault (no-op if it exists)."""
        addr, name = self._addr(address), self._name(name)
        doc = self._load(addr, name)
        if doc is None:
            now = time.time()
            doc = {'name': name, 'owner': addr, 'created': now,
                   'updated': now, 'entries': {}}
            self._save(addr, name, doc)
        return {'name': name, 'owner': addr, 'entries': len(doc.get('entries', {}))}

    def get(self, address: str, name: str, reveal: bool = False) -> dict:
        """A vault's entries. Private values are masked unless reveal=True."""
        addr, name = self._addr(address), self._name(name)
        doc = self._load(addr, name)
        if doc is None:
            raise KeyError(f"vault not found: {name}")
        entries = [self._entry_view(k, e, reveal)
                   for k, e in sorted(doc.get('entries', {}).items())]
        return {'name': name, 'owner': addr, 'created': doc.get('created'),
                'updated': doc.get('updated'), 'revealed': bool(reveal),
                'entries': entries}

    def set(self, address: str, name: str, key: str, value: str,
            private: bool = True) -> dict:
        """Upsert an entry. Creates the vault on first write.

        Private values are sealed before they touch the store; the document
        keeps only the ciphertext and a masked hint.
        """
        addr, name = self._addr(address), self._name(name)
        k = (key or '').strip()
        if not ENTRY_RE.fullmatch(k):
            raise ValueError("entry key must be 1-128 chars: letters, digits, _ . : / -")
        if not isinstance(value, str) or value == '':
            raise ValueError("value required")
        if len(value) > 8192:
            raise ValueError("value too large (max 8192 chars)")
        doc = self._load(addr, name)
        now = time.time()
        if doc is None:
            doc = {'name': name, 'owner': addr, 'created': now, 'entries': {}}
        if len(doc['entries']) >= 200 and k not in doc['entries']:
            raise ValueError("vault full (max 200 entries)")
        entry = {'private': bool(private), 'hint': _mask(value), 'updated': now}
        if private:
            entry['sealed'] = self._seal(value)
        else:
            entry['value'] = value
        doc['entries'][k] = entry
        doc['updated'] = now
        self._save(addr, name, doc)
        return {'name': name, 'key': k, 'private': bool(private),
                'hint': entry['hint'], 'entries': len(doc['entries'])}

    def entry_rm(self, address: str, name: str, key: str) -> dict:
        """Remove one entry from a vault."""
        addr, name = self._addr(address), self._name(name)
        doc = self._load(addr, name)
        if doc is None or key not in doc.get('entries', {}):
            raise KeyError(f"entry not found: {key}")
        del doc['entries'][key]
        doc['updated'] = time.time()
        self._save(addr, name, doc)
        return {'name': name, 'removed': key, 'entries': len(doc['entries'])}

    def rm(self, address: str, name: str) -> dict:
        """Delete a whole vault."""
        addr, name = self._addr(address), self._name(name)
        if self._load(addr, name) is None:
            raise KeyError(f"vault not found: {name}")
        self.store.rm(self._key(addr, name))
        return {'removed': name}

    def public(self, address: str, name: str) -> dict:
        """The public entries of any address's vault. No auth required."""
        addr, name = self._addr(address), self._name(name)
        doc = self._load(addr, name)
        if doc is None:
            raise KeyError(f"vault not found: {name}")
        entries = [self._entry_view(k, e, reveal=False)
                   for k, e in sorted(doc.get('entries', {}).items())
                   if not e.get('private')]
        return {'name': name, 'owner': addr, 'updated': doc.get('updated'),
                'entries': entries}
