"""
Who is calling — the sign-in half of grokbot.

One door: the mod-protocol token. The browser mints it with a wallet signature
(`personal_sign` over `{"data":…,"time":…}`, base64url'd with the address and
signature beside it), the `m` CLI mints the same envelope from a local key, and
the auth mod verifies both. This module does not reimplement any of that — it
hands the token to `m.mod('auth')` and gets back one lowercase address.

That address is the account: it is what a stored xAI key and a saved bot hang
off. There is no password, no email, no session table.

    anon     reads the module's own description and health. Can still chat by
             sending an xAI key per request (BYOK, nothing stored).
    signed   any wallet. Owns its key and its bots, and nobody else's.
    owner    first signed caller claims the deployment; sees /stats.

`GROKBOT_OPEN=1` collapses every caller into one local identity, for a box with
no wallet in front of it.
"""
import contextlib
import json
import os
import sys
import time
from pathlib import Path

from protocol import auth

TOKEN_MAX_AGE = int(os.environ.get('GROKBOT_TOKEN_MAX_AGE', 7 * 86_400))
STATE = Path(os.path.expanduser(os.environ.get('GROKBOT_DIR', '~/.mod/grokbot')))
OWNER_PATH = STATE / 'owner.json'
OPEN_ADDRESS = 'open-mode'

ANON, SIGNED, OWNER = 'anon', 'signed', 'owner'


class AuthError(Exception):
    """The caller is not who they say they are, or did not say."""


class Denied(Exception):
    """The caller is who they say they are, and it is not enough."""


def open_mode():
    return os.environ.get('GROKBOT_OPEN', '') not in ('', '0', 'false', 'False')


def strip(token):
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    # An xAI key in the Authorization header is BYOK, not an identity.
    if token.startswith('xai-'):
        return None
    return token or None


def from_token(token):
    try:
        # The auth mod narrates verification on stdout. Harmless over HTTP,
        # fatal for MCP over stdio, where stdout IS the protocol.
        with contextlib.redirect_stdout(sys.stderr):
            headers = auth(max_age=TOKEN_MAX_AGE).verify(token)
    except Exception as e:                                   # noqa: BLE001
        raise AuthError(f'token rejected: {e}')
    address = str((headers or {}).get('key') or '')
    if not address:
        raise AuthError('token carries no signer address')
    return address.lower()


def whoami(token):
    """The caller if they proved it, else None. Never raises."""
    token = strip(token)
    if not token:
        return OPEN_ADDRESS if open_mode() else None
    try:
        return from_token(token)
    except AuthError:
        return None


def require(token):
    """A signed caller, or a clear instruction on how to become one."""
    token = strip(token)
    if not token:
        if open_mode():
            return OPEN_ADDRESS
        raise AuthError('sign in first — connect a wallet in the console, or '
                        'send a mod-protocol token as `Authorization: Bearer '
                        '<token>` (mint one with m.mod("auth")().token({}))')
    return from_token(token)


# ── the owner ────────────────────────────────────────────────────────────

def owner():
    try:
        return ((json.loads(OWNER_PATH.read_text()) or {})
                .get('address') or '').lower() or None
    except Exception:                                        # noqa: BLE001
        return (os.environ.get('GROKBOT_OWNER') or '').lower() or None


def claim(address):
    """First signed caller claims the deployment; after that it is fixed."""
    existing = owner()
    if existing and existing != (address or '').lower():
        raise Denied(f'this deployment already belongs to {existing}')
    OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_PATH.write_text(json.dumps(
        {'address': address, 'claimed': int(time.time())}, indent=2))
    return address


def is_owner(address):
    current = owner()
    return bool(current and address and address.lower() == current.lower())


def require_owner(token, what='read the deployment stats'):
    address = require(token)
    if address == OPEN_ADDRESS:
        return address
    if not owner():
        return claim(address)
    if not is_owner(address):
        raise Denied(f'only the owner ({owner()}) can {what}')
    return address


def role(address):
    if address is None:
        return ANON
    if address == OPEN_ADDRESS or is_owner(address):
        return OWNER
    return SIGNED


def status():
    return {
        'open_mode': open_mode(),
        'owner': owner(),
        'accepts': 'mod-protocol token (m.mod("auth").token) — the same token '
                   'every module in this fleet verifies, as '
                   'Authorization: Bearer <token>',
        'token_max_age': TOKEN_MAX_AGE,
        'standings': {
            'owner': 'the deployment stats; the first signed caller claims it',
            'signed': 'own xAI key, own bots, own chats',
            'anon': 'reads the module description; can chat only by sending an '
                    'xAI key per request',
        },
    }
