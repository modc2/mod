"""
Who is calling.

One door: the **mod-protocol token** — the signed, time-bounded
`{data, time, key, signature}` envelope that `m.mod('auth').token(...)` mints
and that every other module in this fleet already speaks. It is verified by the
auth mod itself rather than reimplemented here, so a browser wallet, a CLI key
and another module all arrive the same way and end at one address.

That is deliberately the *same* token the store module verifies. The console
signs once, and this module can hand that very token to the store on the
caller's behalf (see store_link.py) — no second login, and no credential of
ours standing in for a user.

Two privileges exist, and they are small:

    owner    may persist this deployment's Lighthouse API key. The first
             authenticated caller claims the deployment; after that it is
             fixed. Lives in ~/.mod/lighthouse/owner.json — who runs a
             deployment is deployment state, not something to commit.

    anyone   may upload with their OWN key (the `x-lh-key` header, never
             stored) and may read anything they have the CID for, because
             IPFS content is public by construction and pretending otherwise
             would be theatre.
"""
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from protocol import auth

TOKEN_MAX_AGE = int(os.environ.get('LIGHTHOUSE_TOKEN_MAX_AGE', 7 * 86_400))
STATE = Path(os.path.expanduser(os.environ.get('LIGHTHOUSE_DIR', '~/.mod/lighthouse')))
OWNER_PATH = STATE / 'owner.json'


class AuthError(Exception):
    """The caller is not who they say they are, or didn't say."""


def open_mode() -> bool:
    """Local development only — and `status()` always says which mode it is in."""
    return os.environ.get('LIGHTHOUSE_OPEN', '') not in ('', '0', 'false')


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
    token = strip(token)
    if not token:
        return None
    try:
        return from_token(token)
    except AuthError:
        return None


def require(token: Optional[str]) -> str:
    token = strip(token)
    if not token:
        if open_mode():
            return 'open-mode'
        raise AuthError('sign in first — send a mod-protocol token as '
                        '`Authorization: Bearer <token>`')
    return from_token(token)


# ── the owner ────────────────────────────────────────────────────────

def owner() -> Optional[str]:
    try:
        return (json.loads(OWNER_PATH.read_text()) or {}).get('address')
    except Exception:
        return os.environ.get('LIGHTHOUSE_OWNER') or None


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
    if address == 'open-mode':
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
                    'the store module verifies'],
        'token_max_age': TOKEN_MAX_AGE,
        'note': ('LIGHTHOUSE_OPEN is set — every caller is treated as signed in'
                 if open_mode() else
                 'uploading and the store bridge need a signed caller; reading a '
                 'CID from the gateway needs nothing'),
    }
