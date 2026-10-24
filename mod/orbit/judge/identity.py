"""identity — who is calling the judge, by the mod protocol's one door.

A caller proves who they are with a mod-protocol token: the signed
`{data, time, key, signature}` envelope that `m.mod('auth').token(...)`
mints, a browser wallet produces with one `personal_sign`, and every
module in this fleet already verifies. It is checked by the auth mod
itself rather than reimplemented here, so the `m` CLI, MetaMask and a
peer module all end at the same lowercase address through the same door.
Sent as `Authorization: Bearer <token>`.

Standings, as serve.py applies them:

    box      loopback with no proxy header, or the box's write token
             (X-Judge-Token). Unchanged from v0.2: may manage any panel
             and passes `creator` through as given — the CLI path.
    signed   any caller with a valid token. Creates panels and publishes
             judges AS their address: `creator`/`author` are set to the
             signer, so update/remove/add_judge/install_judge go through
             the panel model's own creator check against that address.
    anon     reads everything, submits inputs for judgment, browses the
             market. Changes no panel.

The protocol package is imported lazily and with this directory taken off
sys.path first — every module ships a `mod.py`, and from inside this
directory `import mod` would resolve to ours. If the protocol package is
not importable at all (a bare checkout of this module), a stdlib +
eth_account fallback verifies the browser flavour (EIP-191
personal_sign over the same bytes).
"""

import base64
import contextlib
import importlib
import json
import os
import sys
import threading
import time
from pathlib import Path

TOKEN_MAX_AGE = int(os.environ.get('JUDGE_TOKEN_MAX_AGE', 7 * 86_400))
MINE = {str(Path(__file__).resolve().parent)}


class AuthError(Exception):
    """The caller is not who they say they are, or did not say."""


def strip(token):
    if not token:
        return None
    token = str(token).strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    return token or None


# ── the protocol's auth, imported without our own mod.py in the way ──

_proto = {'m': None, 'tried': False}
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
            _proto['m'] = importlib.import_module('mod')
        except Exception:  # noqa: BLE001 — fall back to the local verifier
            _proto['m'] = None
        finally:
            sys.path = saved
        return _proto['m']


def _decode(token):
    try:
        raw = base64.urlsafe_b64decode(token + '=' * (-len(token) % 4))
        env = json.loads(raw)
    except Exception:
        raise AuthError('token is not a base64url JSON envelope')
    if not isinstance(env, dict) or not env.get('key') or not env.get('signature'):
        raise AuthError('token envelope needs key and signature')
    return env


def _verify_protocol(m, token):
    # The auth mod narrates verification on stdout; keep that off the
    # server's stdout (pm2 keeps stderr anyway).
    with contextlib.redirect_stdout(sys.stderr):
        return m.mod('auth')(max_age=TOKEN_MAX_AGE).verify(token)


def _verify_local(env):
    """The browser flavour without the protocol package: an EIP-191
    personal_sign over json.dumps({data, time}, separators=(',',':'))."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        raise AuthError('no verifier available: neither the mod protocol '
                        'package nor eth_account is importable')
    msg = json.dumps({'data': env.get('data'), 'time': env.get('time')},
                     separators=(',', ':'))
    sig = str(env['signature'])
    sig_hex = sig[2:] if sig.startswith('0x') else sig
    if len(sig_hex) == 130 and int(sig_hex[128:], 16) in (0, 1):
        sig_hex = sig_hex[:128] + f'{int(sig_hex[128:], 16) + 27:02x}'
    try:
        who = Account.recover_message(encode_defunct(text=msg),
                                      signature=bytes.fromhex(sig_hex))
    except Exception as e:  # noqa: BLE001
        raise AuthError(f'signature does not recover: {e}')
    if who.lower() != str(env['key']).lower():
        raise AuthError('signature was not made by the token\'s key')
    return env


# A verified token is good until its own time runs out; re-checking the
# signature on every request is pure waste. Keyed by token, bounded.
_cache = {}
_CACHE_MAX = 512


def verify(token):
    """The lowercase address a token proves, or AuthError."""
    token = strip(token)
    if not token:
        raise AuthError('no token')
    hit = _cache.get(token)
    if hit and time.time() - hit[1] < TOKEN_MAX_AGE:
        return hit[0]
    env = _decode(token)
    try:
        t = float(env.get('time', 0))
    except (TypeError, ValueError):
        raise AuthError('token time is not a number')
    if abs(time.time() - t) > TOKEN_MAX_AGE:
        raise AuthError(f'token is stale (older than {TOKEN_MAX_AGE}s) — sign in again')
    m = protocol()
    try:
        if m is not None:
            headers = _verify_protocol(m, token)
        else:
            headers = _verify_local(env)
    except AuthError:
        raise
    except Exception as e:  # noqa: BLE001
        raise AuthError(f'token rejected: {e}')
    address = str(headers.get('key') or '').lower()
    if not address:
        raise AuthError('token carries no signer address')
    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[token] = (address, t)
    return address


def whoami(token):
    """The caller if they proved it, else None. Never raises."""
    try:
        return verify(token)
    except AuthError:
        return None


def status():
    return {'accepts': 'mod-protocol token (m.mod("auth").token, or a browser '
                       'wallet personal_sign) as Authorization: Bearer <token>',
            'token_max_age': TOKEN_MAX_AGE,
            'verifier': 'mod protocol auth' if protocol() is not None
                        else 'local eth_account (browser flavour only)'}
