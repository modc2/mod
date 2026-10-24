"""ytmod.identity — who is calling, by the mod protocol's one door.

A caller proves who they are with a mod-protocol token: the signed
base64url `{data, time, key, signature}` envelope that
`m.mod('auth').token(...)` mints, a browser wallet produces with one
EIP-191 `personal_sign` over `JSON.stringify({data, time})`, and every
module in this fleet already verifies. Sent as
`Authorization: Bearer <token>`.

Two ways in, same door:

    browser wallet   the app signs in the page (MetaMask et al.) — no
                     server involvement, the token just verifies here.
    local key        GET /auth/local from the box itself (loopback, no
                     proxy header) mints a token with the box's own mod
                     key. That is how the machine's operator signs in
                     without a wallet extension.

Identity is authorization here: the Google accounts this module holds
can act as the user on YouTube, so everything that reads or uses them
(the account list, OAuth sign-in, every write, the MCP tools that act
as an account) asks for the box itself or its owner. Public, keyless
reads (resolving a URL to an embed, RSS of a channel) stay open.

The protocol package is imported lazily with this module's directory
taken off sys.path first — every module ships a `mod.py`, and from this
cwd `import mod` would resolve to ours. Without the protocol package
(a bare checkout), eth_account verifies the browser flavour and local
sign-in reports itself unavailable.
"""
from __future__ import annotations

import base64
import contextlib
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

TOKEN_MAX_AGE = int(os.environ.get('YOUTUBE_TOKEN_MAX_AGE', 7 * 86_400))
MINE = {str(Path(__file__).resolve().parent.parent)}


class AuthError(Exception):
    """The caller is not who they say they are, or did not say."""


def strip(bearer) -> str | None:
    if not bearer:
        return None
    bearer = str(bearer).strip()
    if bearer.lower().startswith('bearer '):
        bearer = bearer[7:].strip()
    return bearer or None


# ── the protocol's auth, imported without our own mod.py in the way ──

_proto = {'m': None, 'tried': False, 'box': None}
_lock = threading.Lock()


def protocol():
    """`import mod` — the protocol package, not this module's mod.py."""
    with _lock:
        if _proto['m'] is not None or _proto['tried']:
            return _proto['m']
        _proto['tried'] = True
        got = sys.modules.get('mod')
        if got is not None and hasattr(got, 'mod'):
            _proto['m'] = got
            return got
        saved = list(sys.path)
        sys.modules.pop('mod', None)
        try:
            sys.path = [p for p in sys.path
                        if p and str(Path(p).resolve()) not in MINE]
            cand = importlib.import_module('mod')
            if hasattr(cand, 'mod'):
                _proto['m'] = cand
        except Exception:  # noqa: BLE001 — fall back to the local verifier
            _proto['m'] = None
        finally:
            sys.path = saved
        return _proto['m']


def box_address() -> str | None:
    """The box's own key address (lowercase), or None without the protocol."""
    m = protocol()
    if m is None:
        return None
    if _proto['box'] is None:
        with contextlib.redirect_stdout(sys.stderr):
            _proto['box'] = str(m.key().address).lower()
    return _proto['box']


def local_token() -> str:
    """Mint a protocol token with the box's own mod key (loopback sign-in)."""
    m = protocol()
    if m is None:
        raise AuthError('the mod protocol package is not importable here; '
                        'sign in with a browser wallet instead')
    with contextlib.redirect_stdout(sys.stderr):
        return m.mod('auth')().token({'mod': 'youtube'})


# ── verification ──

def _decode(token: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        env = json.loads(raw)
    except Exception:
        raise AuthError('token is not a base64url JSON envelope')
    if not isinstance(env, dict) or not env.get('key') or not env.get('signature'):
        raise AuthError('token envelope needs key and signature')
    return env


def _verify_fallback(env: dict) -> str:
    """The browser flavour without the protocol package: EIP-191
    personal_sign over json.dumps({data, time}, separators=(',',':'))."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        raise AuthError('no verifier available: neither the mod protocol '
                        'package nor eth_account is importable')
    try:
        age = time.time() - float(env.get('time', 0))
    except (TypeError, ValueError):
        raise AuthError('token time is not a number')
    if not -60 <= age <= TOKEN_MAX_AGE:
        raise AuthError('token expired: sign in again')
    msg = json.dumps({'data': env.get('data'), 'time': env.get('time')},
                     separators=(',', ':'))
    sig = str(env['signature'])
    sig_hex = sig[2:] if sig.startswith('0x') else sig
    if len(sig_hex) == 130 and int(sig_hex[128:], 16) in (0, 1):
        sig_hex = sig_hex[:128] + f'{int(sig_hex[128:], 16) + 27:02x}'
    try:
        recovered = Account.recover_message(encode_defunct(text=msg),
                                            signature=bytes.fromhex(sig_hex))
    except Exception as e:
        raise AuthError(f'signature does not verify: {e}')
    if recovered.lower() != str(env['key']).lower():
        raise AuthError('signature does not match the token key')
    return recovered.lower()


def whoami(bearer) -> str | None:
    """Lowercase signer address, None when no token was sent.
    Raises AuthError when a token was sent and is bad."""
    token = strip(bearer)
    if not token:
        return None
    m = protocol()
    if m is not None:
        try:
            # The auth mod narrates on stdout; keep that off the server's.
            with contextlib.redirect_stdout(sys.stderr):
                headers = m.mod('auth')(max_age=TOKEN_MAX_AGE).verify(token)
            return str(headers['key']).lower()
        except Exception as e:
            raise AuthError(f'invalid token: {e}')
    return _verify_fallback(_decode(token))


def status() -> dict:
    return {'scheme': 'mod-protocol token as Authorization: Bearer',
            'token_max_age': TOKEN_MAX_AGE,
            'protocol': protocol() is not None,
            'box': box_address()}
