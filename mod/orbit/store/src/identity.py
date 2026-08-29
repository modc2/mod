"""
Who is asking.

Identity is the fleet's shared protocol auth (`m.mod('auth')`, core/server/auth):
a bearer token the caller's wallet signed, from which `verify` recovers an
address. That address is the owner of everything it uploads. There is no user
table here and no password — the same identity works in every module that
speaks the protocol.

THE ANONYMOUS CALLER IS THE BOX'S OWNER, AND ONLY OVER LOOPBACK
    On 127.0.0.1 an unauthenticated caller is treated as the local owner, so
    the CLI and a browser on the same machine do not need to sign anything to
    use their own library. That shortcut is exactly a full takeover if the port
    is ever reachable, so it is conditioned on the bind address rather than on
    a config flag someone will flip without reading this: `allow_local` is
    passed in by the server from the interface it actually bound. Off loopback,
    no token means 401 and there is no way to configure otherwise.
"""
import json
import os
from pathlib import Path

from .library import HOME, StoreError

LOOPBACK = ('127.0.0.1', '::1', 'localhost')
_AUTH = None


def _auth():
    """The shared protocol auth module, loaded once and never fatally."""
    global _AUTH
    if _AUTH is None:
        import mod as m
        ttl = int(os.environ.get('STORE_SHARE_SESSION_TTL', 7 * 86400))
        _AUTH = m.mod('auth')(crypto_type='ecdsa', max_age=ttl)
    return _AUTH


def local_owner() -> str:
    """The address this box files anonymous work under."""
    path = HOME / 'owner.json'
    if path.exists():
        try:
            data = json.loads(path.read_text())
            owner = data.get('owner') if isinstance(data, dict) else data
            if owner:
                return str(owner).lower()
        except Exception:
            pass
    return 'local'


def set_owner(address: str) -> str:
    HOME.mkdir(parents=True, exist_ok=True)
    owner = str(address).lower()
    (HOME / 'owner.json').write_text(json.dumps({'owner': owner}, indent=2))
    return owner


def bearer(authorization: str = None) -> str:
    if not authorization:
        return ''
    value = authorization.strip()
    if value.lower().startswith('bearer '):
        value = value[7:].strip()
    return value


def whoami(authorization: str = None, allow_local: bool = False) -> str:
    """The caller's address. Raises 401 rather than guessing."""
    token = bearer(authorization)
    if token:
        try:
            recovered = _auth().verify(token)
        except Exception as error:
            raise StoreError(f'bad token: {error}', 401)
        address = str((recovered or {}).get('key', '')).lower()
        if not address:
            raise StoreError('token carried no key', 401)
        return address
    if allow_local:
        return local_owner()
    raise StoreError('this needs a signed protocol auth token', 401)


def is_loopback(host: str) -> bool:
    return str(host) in LOOPBACK
