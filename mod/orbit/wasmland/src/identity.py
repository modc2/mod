"""
Who is calling.

Two doors, one answer — an address:

    mod-protocol token   what `m.mod('auth').token(...)` mints, and what every
                         other mod in this fleet already speaks. Verified by
                         the auth mod itself rather than reimplemented here.
    wallet signature     a browser signing a challenge string with personal_sign.
                         Verified locally with eth_account, because the console
                         should not need the protocol installed in the tab.

Both end at a checksummed 0x address, and nothing downstream cares which door
was used. There is no password, no session table, and no account to create:
the address *is* the account, and it owns whatever it published.

Open mode (WASMLAND_OPEN=1) skips the gate for local development. It is off by
default, and `status()` says which it is — a module that quietly runs open is
a module that will one day be open in production.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Optional

CHALLENGE_TTL = 600          # seconds a sign-in challenge stays good
TOKEN_MAX_AGE = 86_400
SESSION_TTL = 7 * 86_400
SECRET_PATH = Path(os.path.expanduser('~/.mod/wasmland/server.secret'))


class AuthError(Exception):
    """The caller is not who they say they are, or didn't say."""


def open_mode() -> bool:
    return os.environ.get('WASMLAND_OPEN', '') not in ('', '0', 'false')


def challenge(address: str) -> Dict[str, Any]:
    """The exact text a wallet must sign. Includes the clock so it expires."""
    issued = int(time.time())
    return {
        'address': address,
        'issued': issued,
        'expires': issued + CHALLENGE_TTL,
        'message': (f'wasmland sign-in\naddress: {address}\nissued: {issued}\n'
                    'signing this proves you hold the key; it authorises nothing else'),
    }


def _recover(message: str, signature: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    raw = bytes.fromhex(signature[2:] if signature.startswith('0x') else signature)
    if len(raw) == 65 and raw[64] < 27:
        raw = raw[:64] + bytes([raw[64] + 27])     # MetaMask legacy v
    return Account.recover_message(encode_defunct(text=message), signature=raw)


def from_signature(address: str, message: str, signature: str) -> str:
    """Verify a wallet signature over a challenge this box issued."""
    try:
        recovered = _recover(message, signature)
    except Exception as e:
        raise AuthError(f'signature does not parse: {e}')
    if recovered.lower() != (address or '').lower():
        raise AuthError(f'signature is by {recovered}, not {address}')
    for line in message.splitlines():
        if line.startswith('issued: '):
            age = time.time() - int(line.split(': ', 1)[1])
            if age > CHALLENGE_TTL:
                raise AuthError(f'challenge expired {int(age - CHALLENGE_TTL)}s ago')
    return recovered


def from_token(token: str) -> str:
    """Verify a mod-protocol token by asking the auth mod, not by guessing."""
    from .storage import protocol
    try:
        headers = protocol().mod('auth')(max_age=TOKEN_MAX_AGE).verify(token)
    except Exception as e:
        raise AuthError(f'token rejected: {e}')
    return headers['key']


# ── sessions ─────────────────────────────────────────────────────────
#
# A wallet signature proves who you are once. A session is what the console
# holds afterwards, so the tab isn't asking for a signature on every click.
# It is an HMAC over (address, expiry) with a secret that never leaves this
# box — there is no session table, so there is nothing to leak and nothing to
# clean up.

def _secret() -> bytes:
    try:
        data = SECRET_PATH.read_bytes().strip()
        if data:
            return data
    except OSError:
        pass
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = secrets.token_hex(32).encode()
    SECRET_PATH.write_bytes(data)
    SECRET_PATH.chmod(0o600)
    return data


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def mint_session(address: str, ttl: int = SESSION_TTL) -> Dict[str, Any]:
    body = {'address': address, 'exp': int(time.time() + ttl)}
    raw = json.dumps(body, sort_keys=True, separators=(',', ':')).encode()
    sig = hmac.new(_secret(), raw, hashlib.sha256).digest()
    return {'token': f'{_b64(raw)}.{_b64(sig)}', **body}


def read_session(token: str) -> Optional[str]:
    """The address a session names, or None if it isn't one of ours."""
    try:
        body_b64, sig_b64 = token.split('.', 1)
        raw = _unb64(body_b64)
        expected = hmac.new(_secret(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            return None
        body = json.loads(raw)
    except Exception:
        return None
    if body.get('exp', 0) < time.time():
        return None
    return body.get('address')


def resolve(token: Optional[str]) -> Optional[str]:
    """Whatever kind of credential this is, the address behind it.

    A session first because it is the common case and costs one HMAC; the
    protocol token second because verifying it means loading the auth mod.
    """
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    return read_session(token) or from_token(token)


def whoami(token: Optional[str] = None) -> Optional[str]:
    """The caller's address, or None. Raises only if a credential was offered
    and turned out to be bad — an absent credential is anonymous, not wrong."""
    if not token:
        return None
    return resolve(token)


def require(token: Optional[str] = None) -> str:
    """The caller's address, or a refusal."""
    if not token:
        if open_mode():
            return 'open-mode'
        raise AuthError('sign in first — send a mod-protocol token, or sign the '
                        'challenge from /auth/challenge with your wallet')
    return resolve(token)


def status() -> Dict[str, Any]:
    return {
        'open_mode': open_mode(),
        'accepts': ['mod-protocol token', 'wallet signature (personal_sign)'],
        'note': ('WASMLAND_OPEN is set — every caller is treated as signed in'
                 if open_mode() else
                 'publishing, spending and deleting need a signed caller'),
    }
