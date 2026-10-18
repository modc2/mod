"""Agent wallets: one BIP39 phrase -> an EVM key (m/44'/60'/0'/0/i) and a
Solana key (m/44'/501'/i'/0'), the same paths upstream's derive_wallet uses.

Secrets live in ~/.mod/agenti/wallets.json (0600) and never leave this box
except through reveal(), which the server only lets the owner call.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import unicodedata
from pathlib import Path

import base58
from eth_account import Account
from eth_account.hdaccount import Language, generate_mnemonic, seed_from_mnemonic
from eth_account.messages import encode_defunct
from nacl.signing import SigningKey

Account.enable_unaudited_hdwallet_features()

HOME = Path(os.environ.get('AGENTI_HOME', Path.home() / '.mod' / 'agenti'))
_LOCK = threading.Lock()


def _path() -> Path:
    return HOME / 'wallets.json'


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {'wallets': [], 'default': None}
    return json.loads(p.read_text())


def _save(data: dict):
    HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME, 0o700)
    tmp = _path().with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _path())


# ── derivation ────────────────────────────────────────────────────

def _slip10_ed25519(seed: bytes, path: list) -> bytes:
    """SLIP-0010 ed25519 derivation (hardened only) -> 32-byte private seed."""
    digest = hmac.new(b'ed25519 seed', seed, hashlib.sha512).digest()
    key, chain = digest[:32], digest[32:]
    for idx in path:
        data = b'\x00' + key + (idx | 0x80000000).to_bytes(4, 'big')
        digest = hmac.new(chain, data, hashlib.sha512).digest()
        key, chain = digest[:32], digest[32:]
    return key


def _solana_from_seed32(seed32: bytes) -> dict:
    sk = SigningKey(seed32)
    pub = bytes(sk.verify_key)
    return {'address': base58.b58encode(pub).decode(),
            'secret': base58.b58encode(seed32 + pub).decode()}


def derive(mnemonic: str, index: int = 0) -> dict:
    mnemonic = unicodedata.normalize('NFKD', ' '.join(mnemonic.strip().lower().split()))
    evm = Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{index}")
    seed = seed_from_mnemonic(mnemonic, '')
    sol = _solana_from_seed32(_slip10_ed25519(seed, [44, 501, index, 0]))
    return {'evm': {'address': evm.address, 'secret': evm.key.hex() if evm.key.hex().startswith('0x') else '0x' + evm.key.hex()},
            'solana': sol}


# ── public view ───────────────────────────────────────────────────

def _public(w: dict, default_id) -> dict:
    return {'id': w['id'], 'name': w['name'], 'created': w['created'], 'source': w['source'],
            'evm': w.get('evm', {}).get('address'), 'solana': w.get('solana', {}).get('address'),
            'default': w['id'] == default_id}


def list_wallets() -> list:
    d = _load()
    return [_public(w, d.get('default')) for w in d['wallets']]


def get(wallet_id: str = None, secret: bool = False) -> dict:
    d = _load()
    wid = wallet_id or d.get('default')
    for w in d['wallets']:
        if w['id'] == wid or w['name'] == wid:
            return w if secret else _public(w, d.get('default'))
    raise KeyError(f'no wallet {wid!r}' if wid else 'no wallet yet — create one first')


def _add(name: str, source: str, evm: dict = None, solana: dict = None, mnemonic: str = None) -> dict:
    name = (name or '').strip()[:40] or 'agent'
    with _LOCK:
        d = _load()
        taken = {w['name'] for w in d['wallets']}
        base, n = name, 2
        while name in taken:
            name, n = f'{base}-{n}', n + 1
        w = {'id': secrets.token_hex(6), 'name': name, 'created': int(time.time()), 'source': source}
        if evm:
            w['evm'] = evm
        if solana:
            w['solana'] = solana
        if mnemonic:
            w['mnemonic'] = mnemonic
        d['wallets'].append(w)
        if not d.get('default'):
            d['default'] = w['id']
        _save(d)
        return _public(w, d['default'])


def create(name: str = 'agent', words: int = 12) -> dict:
    strength = {12: 12, 24: 24}.get(int(words), 12)
    m = generate_mnemonic(num_words=strength, lang=Language.ENGLISH)
    keys = derive(m, 0)
    return _add(name, 'generated', keys['evm'], keys['solana'], m)


def import_wallet(name: str = 'imported', mnemonic: str = None, evm_key: str = None,
                  solana_key: str = None, index: int = 0) -> dict:
    if mnemonic:
        m = ' '.join(mnemonic.strip().lower().split())
        if len(m.split()) not in (12, 15, 18, 21, 24):
            raise ValueError('a recovery phrase has 12 or 24 words')
        keys = derive(m, int(index))
        return _add(name, 'phrase', keys['evm'], keys['solana'], m)
    evm = sol = None
    if evm_key:
        k = evm_key.strip()
        k = k if k.startswith('0x') else '0x' + k
        evm = {'address': Account.from_key(k).address, 'secret': k}
    if solana_key:
        raw = base58.b58decode(solana_key.strip())
        if len(raw) not in (32, 64):
            raise ValueError('solana key must be a base58 32- or 64-byte secret')
        sol = _solana_from_seed32(raw[:32])
    if not (evm or sol):
        raise ValueError('give a recovery phrase, an EVM private key or a Solana secret key')
    return _add(name, 'key', evm, sol)


def rename(wallet_id: str, name: str) -> dict:
    with _LOCK:
        d = _load()
        for w in d['wallets']:
            if w['id'] == wallet_id:
                w['name'] = (name or w['name']).strip()[:40]
                _save(d)
                return _public(w, d['default'])
    raise KeyError(wallet_id)


def set_default(wallet_id: str) -> dict:
    with _LOCK:
        d = _load()
        if not any(w['id'] == wallet_id for w in d['wallets']):
            raise KeyError(wallet_id)
        d['default'] = wallet_id
        _save(d)
    return get(wallet_id)


def delete(wallet_id: str) -> dict:
    with _LOCK:
        d = _load()
        keep = [w for w in d['wallets'] if w['id'] != wallet_id]
        if len(keep) == len(d['wallets']):
            raise KeyError(wallet_id)
        gone = [w for w in d['wallets'] if w['id'] == wallet_id][0]
        # Keep a tombstone copy so a mis-click is recoverable by hand.
        trash = HOME / 'deleted'
        trash.mkdir(parents=True, exist_ok=True)
        fd = os.open(trash / f"{gone['id']}.json", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(gone, f)
        d['wallets'] = keep
        if d.get('default') == wallet_id:
            d['default'] = keep[0]['id'] if keep else None
        _save(d)
    return {'ok': True, 'deleted': wallet_id}


def reveal(wallet_id: str) -> dict:
    w = get(wallet_id, secret=True)
    return {'id': w['id'], 'name': w['name'], 'mnemonic': w.get('mnemonic'),
            'evm_private_key': w.get('evm', {}).get('secret'),
            'solana_secret_key': w.get('solana', {}).get('secret')}


def sign_message(message: str, wallet_id: str = None) -> dict:
    w = get(wallet_id, secret=True)
    if 'evm' not in w:
        raise ValueError('this wallet has no EVM key')
    acct = Account.from_key(w['evm']['secret'])
    sig = acct.sign_message(encode_defunct(text=message))
    return {'address': acct.address, 'message': message, 'signature': '0x' + sig.signature.hex().removeprefix('0x')}


def evm_account(wallet_id: str = None):
    w = get(wallet_id, secret=True)
    if 'evm' not in w:
        raise ValueError('this wallet has no EVM key')
    return w, Account.from_key(w['evm']['secret'])
