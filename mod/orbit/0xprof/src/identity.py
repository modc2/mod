"""
Who is calling.

Two doors, one answer — an address:

    mod-protocol token   what `m.mod('auth').token(...)` mints, and what every
                         other mod in this fleet already speaks. Verified by the
                         auth mod itself rather than reimplemented here.
    wallet signature     a browser signing a challenge with personal_sign,
                         checked locally with eth_account so the console does
                         not need the protocol installed in the tab.

Both end at a 0x address, and nothing downstream cares which door was used.
There is no password and no account to create: the address *is* the account,
and it owns the proofs it published and the credits it holds.

Open mode (ZKPROF_OPEN=1) skips the gate for local development. It is off by
default and `status()` says which it is — a module that quietly runs open is a
module that will one day be open in production.
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

CHALLENGE_TTL = 600
TOKEN_MAX_AGE = 86_400
SESSION_TTL = 7 * 86_400
STATE = Path(os.path.expanduser(os.environ.get('ZKPROF_DIR', '~/.mod/0xprof')))
SECRET_PATH = STATE / 'server.secret'
OWNER_PATH = STATE / 'owner.json'


class AuthError(Exception):
    """The caller is not who they say they are, or didn't say."""


def open_mode() -> bool:
    return os.environ.get('ZKPROF_OPEN', '') not in ('', '0', 'false')


def challenge(address: str) -> Dict[str, Any]:
    issued = int(time.time())
    return {
        'address': address,
        'issued': issued,
        'expires': issued + CHALLENGE_TTL,
        'message': (f'0xprof sign-in\naddress: {address}\nissued: {issued}\n'
                    'signing this proves you hold the key; it authorises nothing else'),
    }


def _recover(message: str, signature: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_defunct
    raw = bytes.fromhex(signature[2:] if signature.startswith('0x') else signature)
    if len(raw) == 65 and raw[64] < 27:
        raw = raw[:64] + bytes([raw[64] + 27])      # MetaMask legacy v
    return Account.recover_message(encode_defunct(text=message), signature=raw)


def from_signature(address: str, message: str, signature: str) -> str:
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


# ── signatures that are good for exactly one thing ───────────────────
#
# A session says "this address was here once". That is enough to spend your own
# credits, and it is not enough to put your name on somebody else's listing —
# which is what re-verifying does, because the run is published as *your* run.
# So a re-verification carries its own signature, over a message that names the
# proof it is for. Nothing here is a second auth system: it is the same
# personal_sign, scoped down to one action on one record.

def action_challenge(record_id: str, address: str,
                     action: str = 're-verify') -> Dict[str, Any]:
    issued = int(time.time())
    return {
        'proof': record_id,
        'address': address,
        'action': action,
        'issued': issued,
        'expires': issued + CHALLENGE_TTL,
        'message': (f'0xprof {action}\nproof: {record_id}\naddress: {address}\n'
                    f'issued: {issued}\n'
                    'this runs every verifier again and publishes the answer under '
                    'this address; it moves no credits and grants nothing'),
    }


def from_action(record_id: str, address: str, message: str, signature: str,
                action: str = 're-verify') -> Dict[str, Any]:
    """Check a signature that authorises one action on one proof.

    Three separate things are checked, because dropping any one of them turns a
    signature into a bearer token: the message says which action it is for (so a
    sign-in signature can never be replayed as a re-verification), it names the
    proof (so a signature for one record cannot be spent on another), and it is
    recent. Whether it has been *used* before is checked where the record is —
    the check log is the only place that knows.
    """
    lines = [line.strip() for line in (message or '').splitlines()]
    if not lines or lines[0] != f'0xprof {action}':
        raise AuthError(f'that is not a {action} signature — the message has to '
                        f'begin "0xprof {action}". Ask for one at '
                        f'POST /proofs/{record_id[:12]}…/verify/challenge')
    fields = {}
    for line in lines[1:]:
        if ': ' in line:
            key, value = line.split(': ', 1)
            fields.setdefault(key, value)
    if fields.get('proof') != record_id:
        raise AuthError(f'that signature is for proof {fields.get("proof", "?")[:16]}, '
                        f'not {record_id[:16]}')
    signer = fields.get('address') or address
    if address and signer.lower() != address.lower():
        raise AuthError(f'the message is signed for {signer}, not {address}')
    recovered = from_signature(signer, message, signature)
    return {'address': recovered, 'signature': signature, 'message': message,
            'issued': int(fields.get('issued') or time.time()), 'action': action}


def from_token(token: str) -> str:
    from .storage import protocol
    try:
        headers = protocol().mod('auth')(max_age=TOKEN_MAX_AGE).verify(token)
    except Exception as e:
        raise AuthError(f'token rejected: {e}')
    return headers['key']


# ── sessions ─────────────────────────────────────────────────────────

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
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    return read_session(token) or from_token(token)


def whoami(token: Optional[str] = None) -> Optional[str]:
    if not token:
        return None
    return resolve(token)


def require(token: Optional[str] = None) -> str:
    if not token:
        if open_mode():
            return 'open-mode'
        raise AuthError('sign in first — send a mod-protocol token, or sign the '
                        'challenge from /auth/challenge with your wallet')
    return resolve(token)


# ── the owner ────────────────────────────────────────────────────────
#
# One address may grant credits, and that is the only privilege there is. It
# lives in ~/.mod/0xprof/owner.json rather than config.json: who runs a
# deployment is deployment state, not something to commit to a repo.

def owner() -> Optional[str]:
    try:
        return (json.loads(OWNER_PATH.read_text()) or {}).get('address')
    except Exception:
        return os.environ.get('ZKPROF_OWNER') or None


def claim(address: str) -> Dict[str, Any]:
    """First signed caller claims the deployment; after that it is fixed."""
    existing = owner()
    if existing and existing.lower() != (address or '').lower():
        raise AuthError(f'this deployment already belongs to {existing}')
    OWNER_PATH.parent.mkdir(parents=True, exist_ok=True)
    OWNER_PATH.write_text(json.dumps({'address': address, 'claimed': time.time()}))
    return {'owner': address, 'claimed': True}


def is_owner(address: Optional[str]) -> bool:
    current = owner()
    if not current:
        return False
    return bool(address) and address.lower() == current.lower()


def require_owner(token: Optional[str] = None) -> str:
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
        'accepts': ['mod-protocol token', 'wallet signature (personal_sign)'],
        'signed_actions': {'re-verify': 'a fresh personal_sign naming the proof — a '
                                        'session token is not enough, and ZKPROF_OPEN '
                                        'does not waive it, because the run is '
                                        'published under an address and a signature '
                                        'is evidence rather than a claim'},
        'note': ('ZKPROF_OPEN is set — every caller is treated as signed in, except '
                 'for actions that are signed rather than authenticated'
                 if open_mode() else
                 'publishing, buying and posting bounties need a signed caller; '
                 'checking a loose proof at /verify needs nothing; re-verifying a '
                 'listing needs a signature for that listing'),
    }
