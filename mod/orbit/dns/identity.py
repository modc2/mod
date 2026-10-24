"""
Who is calling, and what that lets them change.

One door: the mod-protocol token — the signed `{data, time, key, signature}`
envelope `m.mod('auth').token(...)` mints and every module in this fleet
already verifies. It is checked by the auth mod itself rather than
reimplemented here, so a browser wallet, the `m` CLI and another module all
arrive the same way and end at one lowercase address.

DNS is a place where the difference between "my names" and "the system's
names" has teeth, so there are exactly three standings:

    owner        the deployment owner. Claims the box on first signed call.
                 Owns the SYSTEM zone (the protocol host — modc2.com here),
                 the settings that decide what the protocol's names mean, the
                 DNS listener, and the router sync. Nobody else touches those.

    holder       any signed caller. May register their OWN host and is the
                 owner of that zone: its records, its target, its deletion.
                 A holder cannot touch the system zone, cannot repoint the
                 protocol host, and cannot take a name under somebody else's
                 zone. This is the "run the protocol on your own domain"
                 path — it needs no permission from the deployment owner.

    anon         unauthenticated. Reads everything: zones, records, the
                 resolver, the operation log. Changes nothing.

Reads are open on purpose. A zone is public data — it is answered to the whole
internet over UDP a few milliseconds later — so hiding it behind a token would
be theatre. What is gated is *change*.

`DNS_OPEN=1` collapses every caller into one local identity, for a box with no
wallet in front of it. `status()` always says when it is on.
"""
import contextlib
import json
import os
import sys
import time
from pathlib import Path

from protocol import auth

TOKEN_MAX_AGE = int(os.environ.get('DNS_TOKEN_MAX_AGE', 7 * 86_400))
STATE = Path(os.path.expanduser(os.environ.get('DNS_DIR', '~/.mod/dns')))
OWNER_PATH = STATE / 'owner.json'
OPEN_ADDRESS = 'open-mode'

ANON, SIGNED, OWNER = 'anon', 'holder', 'owner'


class AuthError(Exception):
    """The caller is not who they say they are, or did not say."""


class Denied(Exception):
    """The caller is who they say they are, and it is not enough."""


def open_mode():
    return os.environ.get('DNS_OPEN', '') not in ('', '0', 'false', 'False')


def strip(token):
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    return token or None


def from_token(token):
    try:
        # The auth mod narrates verification on stdout. Harmless over HTTP,
        # fatal for MCP over stdio, where stdout IS the protocol.
        with contextlib.redirect_stdout(sys.stderr):
            headers = auth(max_age=TOKEN_MAX_AGE).verify(token)
    except Exception as e:                                   # noqa: BLE001
        raise AuthError(f'token rejected: {e}')
    address = str(headers.get('key') or '')
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
        raise AuthError('sign in first — send a mod-protocol token as '
                        '`Authorization: Bearer <token>`')
    return from_token(token)


# ── the owner ────────────────────────────────────────────────────────────

def owner():
    try:
        return ((json.loads(OWNER_PATH.read_text()) or {}).get('address') or '').lower() or None
    except Exception:                                        # noqa: BLE001
        return (os.environ.get('DNS_OWNER') or '').lower() or None


def claim(address):
    """First signed caller claims the deployment; after that it is fixed."""
    existing = owner()
    if existing and existing != (address or '').lower():
        raise Denied(f'this deployment already belongs to {existing}')
    OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_PATH.write_text(json.dumps(
        {'address': address, 'claimed': int(time.time())}, indent=2))
    return {'owner': address, 'claimed': True}


def is_owner(address):
    current = owner()
    return bool(current and address and address.lower() == current.lower())


def require_owner(token, what='change the system configuration'):
    """The gate on every key system change."""
    address = require(token)
    if address == OPEN_ADDRESS:
        return address
    if not owner():
        return claim(address)['owner']
    if not is_owner(address):
        raise Denied(
            f'only the owner ({owner()}) can {what}. You can still run the '
            f'protocol on your own host: register a zone you control with '
            f'`zone_register` and you own everything in it.')
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
            'owner': 'the system zone, the protocol host, the listener, the '
                     'router sync',
            'holder': 'any signed caller — registers and fully owns their own '
                      'host and its records',
            'anon': 'reads everything, changes nothing',
        },
        'note': ('DNS_OPEN is set — every caller is one local identity'
                 if open_mode() else
                 'reads are open; every change is attributed to a signed address'),
    }
