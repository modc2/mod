"""Who is calling — the protocol's own auth, with the shadowing trap handled.

Identity here is exactly the fleet's: a mod-protocol token
(``base64url({data,time,key,signature})``, EIP-191 personal_sign over
``JSON.stringify({data,time})``) verified by ``mod.core.server.auth`` — the
same class ``m.mod('auth')`` returns. Nothing module-specific, so a token
minted for any module signs in here too.

The import is defensive for one reason: this directory holds ``mod.py``, the
anchor, and when a script in it runs, its own directory is ``sys.path[0]`` —
so a plain ``import mod`` finds the anchor instead of the protocol package.
``_auth()`` removes this directory for the duration of that import.

Unsigned callers are not turned away. Filing a recommendation is open (they
get a stable ``anon:`` handle, like build's idea queue); deciding on one is
not — approve and reject need a signature from the module's owner.
"""

import hashlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_AGE = int(os.environ.get('ADVISE_TOKEN_MAX_AGE') or 7 * 24 * 3600)

_AUTH = None


def _auth():
    global _AUTH
    if _AUTH is not None:
        return _AUTH
    saved = list(sys.path)
    ours = sys.modules.get('mod')
    if ours is not None and os.path.realpath(getattr(ours, '__file__', '') or '') \
            == os.path.join(HERE, 'mod.py'):
        # Our anchor took the name. Drop it: from here on `mod` is the
        # protocol package, which is what every other importer expects.
        del sys.modules['mod']
    sys.path[:] = [p for p in sys.path
                   if os.path.realpath(p or os.getcwd()) != HERE]
    try:
        from mod.core.server.auth.auth.auth import Auth
        _AUTH = Auth(max_age=MAX_AGE)
    finally:
        sys.path[:] = saved
    return _AUTH


def verify(token):
    """Address behind a token, lowercased — or '' if it proves nothing.

    A bad token is not an error: an unsigned caller and a caller with an
    expired token both simply have no standing, and both may still file.
    """
    if not token or not isinstance(token, str):
        return ''
    try:
        headers = _auth().verify(token.strip())
    except Exception:
        return ''
    return str((headers or {}).get('key') or '').strip().lower()


def anon_handle(*parts):
    """A stable pen name for an unsigned caller — same client, same handle."""
    seed = '|'.join(str(p or '') for p in parts) or 'unknown'
    salt = os.environ.get('ADVISE_ANON_SALT') or 'advise'
    return 'anon:' + hashlib.sha256((salt + seed).encode()).hexdigest()[:8]


def caller(token=None, ip=None, ua=None, local=False):
    """Resolve one caller.

    ``local=True`` is the host itself — the CLI and stdio MCP run as whoever
    owns this box, the same concession build makes for local mode.
    """
    address = verify(token)
    if not address and local:
        import scan
        address = scan.deployment_owner()
    return {
        'address': address,
        'handle': address or anon_handle(ip, ua),
        'signed': bool(address),
        'local': bool(local),
        'checked_at': int(time.time()),
    }


def token_for(data='advise'):
    """Mint a token as this host, used to relay an approval into build when
    the approver did not pass theirs on.

    Minted the way a browser wallet mints one, because the token has to pass
    build's reader as well as this module's, and build is the stricter of the
    two on all three counts:

        data       a STRING — build pulls it out with `as_str()` and rebuilds
                   the signed message from it, so an object signs one message
                   and verifies another;
        time       WHOLE seconds — build parses it as an i64, while
                   ``Auth.token`` mints ``str(time.time())`` and earns an
                   "Invalid timestamp"; float or int both verify here;
        signature  EIP-191 personal_sign — build hashes the message with the
                   "\\x19Ethereum Signed Message:" prefix, and the key class's
                   own ``sign`` does not.
    """
    try:
        import json
        from eth_account import Account
        from eth_account.messages import encode_defunct
        auth = _auth()
        claim = {'data': data, 'time': str(int(time.time())),
                 'key': auth.key.address}
        message = json.dumps({'data': claim['data'], 'time': claim['time']},
                             separators=(',', ':'))
        private = auth.key.private_key
        signed = Account.sign_message(
            encode_defunct(text=message),
            private_key=private.hex() if isinstance(private, (bytes, bytearray))
            else str(private))
        claim['signature'] = '0x' + signed.signature.hex().replace('0x', '')
        return auth._base64url_encode(claim)
    except Exception:
        return ''
