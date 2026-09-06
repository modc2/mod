"""
Who is calling, and what that buys them.

One door: the **mod-protocol token** — the signed `{data, time, key, signature}`
envelope `m.mod('auth').token(...)` mints and every module in this fleet
already verifies. It is checked by the auth mod itself rather than
reimplemented here, so a browser wallet, a CLI key and another module all
arrive the same way and end at one lowercase address.

That address is the unit of ownership in this module, and it matters more here
than in most of the fleet, because what it owns is **keys that move money**:

    accounts       an account lives under the address that created it.
                   ~/.mod/eth/accounts/<caller>/<name>.json, keystore v3,
                   scrypt-encrypted with a password this module never stores.
                   Nobody — including the deployment owner — reads another
                   caller's vault through this API.

    contracts      a deployment is recorded against the address that paid for
                   it. Reading an ABI is open (a verified contract's interface
                   is public); the index of *who deployed what* is not.

    owner          the first authenticated caller claims the deployment. The
                   owner may add custom networks and set the mainnet policy.
                   That is all — the owner cannot sign for anybody.

`ETH_OPEN=1` collapses every caller into one local identity. It exists for a
box with no wallet in front of it and `status()` always says when it is on.
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from protocol import auth

TOKEN_MAX_AGE = int(os.environ.get('ETH_TOKEN_MAX_AGE', 7 * 86_400))
STATE = Path(os.path.expanduser(os.environ.get('ETH_DIR', '~/.mod/eth')))
OWNER_PATH = STATE / 'owner.json'
OPEN_ADDRESS = 'open-mode'


class AuthError(Exception):
    """The caller is not who they say they are, or didn't say."""


def open_mode() -> bool:
    return os.environ.get('ETH_OPEN', '') not in ('', '0', 'false', 'False')


def strip(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    return token or None


def from_token(token: str) -> str:
    try:
        headers = auth(max_age=TOKEN_MAX_AGE).verify(token)
    except Exception as e:
        raise AuthError(f'token rejected: {e}')
    address = str(headers.get('key') or '')
    if not address:
        raise AuthError('token carries no signer address')
    return address.lower()


def whoami(token: Optional[str]) -> Optional[str]:
    """The caller if they proved it, else None. Never raises."""
    token = strip(token)
    if not token:
        return OPEN_ADDRESS if open_mode() else None
    try:
        return from_token(token)
    except AuthError:
        return None


def require(token: Optional[str]) -> str:
    token = strip(token)
    if not token:
        if open_mode():
            return OPEN_ADDRESS
        raise AuthError('sign in first — send a mod-protocol token as '
                        '`Authorization: Bearer <token>`')
    return from_token(token)


# ── the owner ────────────────────────────────────────────────────────

def owner() -> Optional[str]:
    try:
        return (json.loads(OWNER_PATH.read_text()) or {}).get('address')
    except Exception:
        return (os.environ.get('ETH_OWNER') or '').lower() or None


def claim(address: str) -> Dict[str, Any]:
    """First signed caller claims the deployment; after that it is fixed."""
    existing = owner()
    if existing and existing.lower() != (address or '').lower():
        raise AuthError(f'this deployment already belongs to {existing}')
    OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_PATH.write_text(json.dumps({'address': address,
                                      'claimed': int(time.time())}, indent=2))
    return {'owner': address, 'claimed': True}


def is_owner(address: Optional[str]) -> bool:
    current = owner()
    return bool(current and address and address.lower() == current.lower())


def require_owner(token: Optional[str]) -> str:
    address = require(token)
    if address == OPEN_ADDRESS:
        return address
    if not owner():
        return claim(address)['owner']
    if not is_owner(address):
        raise AuthError(f'only the owner ({owner()}) can do that')
    return address


def status() -> Dict[str, Any]:
    return {
        'open_mode': open_mode(),
        'owner': owner(),
        'accepts': ['mod-protocol token (m.mod("auth").token) — the same token '
                    'every module in this fleet verifies'],
        'token_max_age': TOKEN_MAX_AGE,
        'scoping': ('accounts and the deployment index are keyed by the signing '
                    'address; nobody reads another caller\'s vault'),
        'note': ('ETH_OPEN is set — every caller is one local identity'
                 if open_mode() else
                 'reads are open, anything holding a key needs a signed caller'),
    }
