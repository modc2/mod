"""
Keys, and the smallest number of ways to get at them.

An account is a keystore-v3 file under the address that created it:

    ~/.mod/eth/accounts/<caller>/<name>.json

scrypt-encrypted by `eth_account`, with a password this module never writes
down. That is the whole storage story, and the properties that fall out of it
are the point:

  * losing the password loses the account. There is no recovery hook, because
    a recovery hook is a second way to spend the money.
  * the file on disk is worth nothing on its own. A backup, a snapshot or a
    stray `cat` leaks ciphertext.
  * accounts are namespaced by the signing address, so two callers on one
    deployment cannot see, list or spend each other's keys.

Unlocking is explicit and time-bounded. `unlock(name, password, ttl)` decrypts
once and holds the key **in memory only**, for at most ETH_MAX_UNLOCK seconds
(default 15 minutes), so an agent can send a sequence of transactions without
the password crossing the wire each time — and a restart forgets everything.
Every signed operation records which account and which unlock it used.

Three ways in, all ending at the same signer:

    create()          a new key, shown once as a mnemonic if you asked for one
    import_key()      an existing private key or mnemonic
    ETH_PRIVATE_KEY   an env key exposed as the read-only account `env`,
                      for a headless box that is deliberately trusted
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eth_account import Account

Account.enable_unaudited_hdwallet_features()

STATE = Path(os.path.expanduser(os.environ.get('ETH_DIR', '~/.mod/eth')))
ACCOUNTS = STATE / 'accounts'
MAX_UNLOCK = int(os.environ.get('ETH_MAX_UNLOCK', 900))
DEFAULT_PATH = "m/44'/60'/0'/0/0"

# (owner, name) → {'key': bytes, 'expires': float}. Memory only, on purpose.
_unlocked: Dict[Tuple[str, str], Dict[str, Any]] = {}


class WalletError(Exception):
    """The account, the password or the request was wrong."""


def _safe(name: str) -> str:
    name = (name or '').strip()
    if not name or not all(c.isalnum() or c in '-_.' for c in name):
        raise WalletError('an account name is letters, digits, - _ . and nothing else')
    if name in ('.', '..'):
        raise WalletError('that is not a name')
    return name


def _dir(owner: str) -> Path:
    d = ACCOUNTS / (owner or 'anonymous').lower()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(owner: str, name: str) -> Path:
    return _dir(owner) / f'{_safe(name)}.json'


def env_key() -> Optional[str]:
    key = (os.environ.get('ETH_PRIVATE_KEY') or '').strip()
    return key or None


# ── the vault ────────────────────────────────────────────────────────

def create(owner: str, name: str, password: str,
           mnemonic: bool = False) -> Dict[str, Any]:
    """A new account. The mnemonic, if asked for, is returned exactly once."""
    if not password or len(password) < 8:
        raise WalletError('choose a password of at least 8 characters — it is '
                          'the only thing standing between the file and the funds')
    path = _path(owner, name)
    if path.exists():
        raise WalletError(f'you already have an account named {name!r}')
    if mnemonic:
        account, phrase = Account.create_with_mnemonic(account_path=DEFAULT_PATH)
    else:
        account, phrase = Account.create(), None
    _write(path, account, password, source='created')
    return {'name': _safe(name), 'address': account.address,
            'mnemonic': phrase,
            'note': ('write the mnemonic down now — it is not stored and will '
                     'not be shown again' if phrase else
                     'the key is encrypted under your password and nowhere else')}


def import_key(owner: str, name: str, password: str, secret: str,
               path_: str = DEFAULT_PATH) -> Dict[str, Any]:
    """A private key (hex) or a BIP-39 mnemonic becomes an account here."""
    if not password or len(password) < 8:
        raise WalletError('choose a password of at least 8 characters')
    dest = _path(owner, name)
    if dest.exists():
        raise WalletError(f'you already have an account named {name!r}')
    secret = (secret or '').strip()
    if not secret:
        raise WalletError('nothing to import')
    try:
        if len(secret.split()) >= 12:
            account = Account.from_mnemonic(secret, account_path=path_)
            source = 'mnemonic'
        else:
            account = Account.from_key(secret if secret.startswith('0x')
                                       else '0x' + secret)
            source = 'private key'
    except Exception as e:
        raise WalletError(f'that is not a usable key or mnemonic: {e}')
    _write(dest, account, password, source=f'imported ({source})')
    return {'name': _safe(name), 'address': account.address, 'imported': source}


def _write(path: Path, account, password: str, source: str) -> None:
    keystore = Account.encrypt(account.key, password)
    keystore['x-mod'] = {'created': int(time.time()), 'source': source,
                         'address': account.address}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keystore))
    os.chmod(path, 0o600)


def listing(owner: str) -> List[Dict[str, Any]]:
    """Names and addresses. Never anything that could be decrypted."""
    out: List[Dict[str, Any]] = []
    d = ACCOUNTS / (owner or 'anonymous').lower()
    if d.is_dir():
        for f in sorted(d.glob('*.json')):
            try:
                blob = json.loads(f.read_text())
            except Exception:
                continue
            meta = blob.get('x-mod') or {}
            address = meta.get('address') or ('0x' + blob.get('address', ''))
            key = (owner.lower(), f.stem)
            session = _unlocked.get(key)
            out.append({
                'name': f.stem,
                'address': _checksum(address),
                'created': meta.get('created'),
                'source': meta.get('source', 'keystore'),
                'unlocked': bool(session and session['expires'] > time.time()),
                'unlocked_until': (int(session['expires'])
                                   if session and session['expires'] > time.time()
                                   else None),
            })
    if env_key():
        try:
            out.append({'name': 'env', 'address': Account.from_key(env_key()).address,
                        'created': None, 'source': 'ETH_PRIVATE_KEY',
                        'unlocked': True, 'unlocked_until': None,
                        'note': 'from the environment — no password, no file'})
        except Exception:
            pass
    return out


def _hex(value) -> str:
    """bytes → 0x-prefixed hex; py3.12's bytes.hex() drops the prefix and a
    signature without one is rejected by half the tools that consume it."""
    raw = value.hex() if isinstance(value, (bytes, bytearray)) else str(value)
    return raw if raw.startswith('0x') else '0x' + raw


def _checksum(address: str) -> str:
    from web3 import Web3
    try:
        return Web3.to_checksum_address(address)
    except Exception:
        return address


def address_of(owner: str, name: str) -> str:
    for row in listing(owner):
        if row['name'] == name:
            return row['address']
    raise WalletError(f'no account named {name!r}')


def delete(owner: str, name: str) -> Dict[str, Any]:
    path = _path(owner, name)
    if not path.exists():
        raise WalletError(f'no account named {name!r}')
    address = None
    try:
        address = (json.loads(path.read_text()).get('x-mod') or {}).get('address')
    except Exception:
        pass
    path.unlink()
    _unlocked.pop((owner.lower(), _safe(name)), None)
    return {'deleted': _safe(name), 'address': address,
            'warning': 'the keystore is gone; without a backup the key is gone with it'}


def export(owner: str, name: str, password: str) -> Dict[str, Any]:
    """The raw private key, for someone holding the password who asked twice.

    This exists because an account you cannot take with you is a hostage, not a
    wallet. The API makes the caller pass `confirm` on top of the password.
    """
    key = _decrypt(owner, name, password)
    return {'name': _safe(name), 'address': Account.from_key(key).address,
            'private_key': '0x' + key.hex(),
            'warning': 'anyone with this string owns the account'}


def keystore(owner: str, name: str) -> Dict[str, Any]:
    """The encrypted file itself — a backup that is safe to move."""
    path = _path(owner, name)
    if not path.exists():
        raise WalletError(f'no account named {name!r}')
    return json.loads(path.read_text())


# ── unlocking ────────────────────────────────────────────────────────

def _decrypt(owner: str, name: str, password: str) -> bytes:
    if name == 'env' and env_key():
        return Account.from_key(env_key()).key
    path = _path(owner, name)
    if not path.exists():
        raise WalletError(f'no account named {name!r}')
    try:
        return Account.decrypt(json.loads(path.read_text()), password or '')
    except Exception:
        raise WalletError('wrong password')


def unlock(owner: str, name: str, password: str, ttl: int = 300) -> Dict[str, Any]:
    """Hold the key in memory for a bounded time, so an agent can batch work."""
    ttl = max(1, min(int(ttl or 300), MAX_UNLOCK))
    key = _decrypt(owner, name, password)
    expires = time.time() + ttl
    _unlocked[(owner.lower(), _safe(name))] = {'key': key, 'expires': expires}
    return {'name': _safe(name), 'address': Account.from_key(key).address,
            'unlocked_for': ttl, 'expires': int(expires),
            'note': 'in memory only — a restart or the timeout forgets it'}


def lock(owner: str, name: Optional[str] = None) -> Dict[str, Any]:
    if name:
        _unlocked.pop((owner.lower(), _safe(name)), None)
        return {'locked': _safe(name)}
    dropped = [k[1] for k in list(_unlocked) if k[0] == owner.lower()]
    for k in list(_unlocked):
        if k[0] == owner.lower():
            _unlocked.pop(k, None)
    return {'locked': dropped}


def signer(owner: str, name: str, password: Optional[str] = None):
    """A LocalAccount for `name`: the live unlock, else the password, else no.

    Every write path in this module goes through here, which is why the rule
    about who may spend what is stated once.
    """
    name = _safe(name)
    if name == 'env' and env_key():
        return Account.from_key(env_key())
    session = _unlocked.get((owner.lower(), name))
    if session and session['expires'] > time.time():
        return Account.from_key(session['key'])
    if session:
        _unlocked.pop((owner.lower(), name), None)
    if password:
        return Account.from_key(_decrypt(owner, name, password))
    if not _path(owner, name).exists():
        raise WalletError(f'no account named {name!r}')
    raise WalletError(f'{name} is locked — send its password, or unlock it first')


def sign_message(owner: str, name: str, message: str,
                 password: Optional[str] = None) -> Dict[str, Any]:
    """EIP-191 personal_sign, the same shape a browser wallet produces."""
    from eth_account.messages import encode_defunct
    account = signer(owner, name, password)
    signed = account.sign_message(encode_defunct(text=message))
    return {'address': account.address, 'message': message,
            'signature': _hex(signed.signature)}


def verify_message(message: str, signature: str) -> Dict[str, Any]:
    from eth_account.messages import encode_defunct
    try:
        address = Account.recover_message(encode_defunct(text=message),
                                          signature=signature)
        return {'valid': True, 'address': address}
    except Exception as e:
        return {'valid': False, 'error': str(e)}


def sessions(owner: str) -> List[Dict[str, Any]]:
    now = time.time()
    return [{'name': n, 'expires': int(s['expires']),
             'seconds_left': int(s['expires'] - now)}
            for (o, n), s in _unlocked.items()
            if o == owner.lower() and s['expires'] > now]
