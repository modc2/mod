#!/usr/bin/env python3
"""base58, ed25519 and program-derived addresses — the parts of Solana that are
maths rather than network.

Solana speaks base58 everywhere and signs everything with ed25519, and neither
is in the standard library. Both are implemented here from scratch so the module
has no hard dependencies; when PyNaCl or `cryptography` happens to be installed
we sign with it instead, because a C signature is ~1000x faster than the
reference implementation below and the bytes are identical either way.

The keystore lives in ~/.mod/solana/keys.json, mode 0600, off the source tree —
a secret key is never written next to the code that uses it.
"""

import hashlib
import json
import os
import stat

B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
B58_INDEX = {c: i for i, c in enumerate(B58)}
KEY_DIR = os.path.expanduser(os.environ.get('SOLANA_KEY_DIR', '~/.mod/solana'))
KEY_FILE = os.path.join(KEY_DIR, 'keys.json')


class SolError(Exception):
    """A failure worth showing the caller verbatim."""

    def __init__(self, message, status=400, detail=None):
        super().__init__(message)
        self.status, self.detail = status, detail

    def dict(self):
        out = {'error': str(self)}
        if self.detail is not None:
            out['detail'] = self.detail
        return out


# ── base58 ───────────────────────────────────────────────────────

def b58encode(raw):
    n = int.from_bytes(raw, 'big')
    out = ''
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + (out or '')


def b58decode(text):
    if not isinstance(text, str):
        raise SolError(f'expected a base58 string, got {type(text).__name__}')
    if not text:
        return b''                    # the inverse of b58encode(b'')
    n = 0
    for ch in text:
        if ch not in B58_INDEX:
            raise SolError(f'{ch!r} is not a base58 character — {text[:16]}… is not an address')
        n = n * 58 + B58_INDEX[ch]
    pad = len(text) - len(text.lstrip('1'))
    body = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    return b'\0' * pad + body


def is_address(text):
    """A pubkey is 32 bytes of base58. Cheap enough to check before a round trip."""
    try:
        return len(b58decode(text)) == 32
    except Exception:
        return False


def need_address(text, what='address'):
    if not isinstance(text, str) or not is_address(text.strip()):
        raise SolError(f'{what} must be a base58 32-byte Solana address, got {text!r}')
    return text.strip()


# ── ed25519 (RFC 8032 reference, extended coordinates) ───────────

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    if (x * x - xx) % _P:
        return None
    if x == 0 and sign:
        return None
    return _P - x if x & 1 != sign else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_recover_x(_BY, 0), _BY, 1, _recover_x(_BY, 0) * _BY % _P)


def _add(p_, q_):
    x1, y1, z1, t1 = p_
    x2, y2, z2, t2 = q_
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(point, n):
    out = (0, 1, 1, 0)
    while n > 0:
        if n & 1:
            out = _add(out, point)
        point = _add(point, point)
        n >>= 1
    return out


def _compress(point):
    x, y, z, _ = point
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _clamp(h):
    a = int.from_bytes(h[:32], 'little')
    return (a & ~(2 ** 255 + 7)) | 2 ** 254


def on_curve(pubkey32):
    """True if the bytes decode to a real ed25519 point — i.e. someone could
    hold the private key. PDAs are chosen precisely because they do not."""
    if len(pubkey32) != 32:
        return False
    y = int.from_bytes(pubkey32, 'little') & (2 ** 255 - 1)
    return _recover_x(y, pubkey32[31] >> 7) is not None


def _pure_pubkey(seed):
    return _compress(_mul(_B, _clamp(hashlib.sha512(seed).digest())))


def _pure_sign(seed, message):
    h = hashlib.sha512(seed).digest()
    a = _clamp(h)
    pub = _compress(_mul(_B, a))
    r = int.from_bytes(hashlib.sha512(h[32:] + message).digest(), 'little') % _L
    rr = _compress(_mul(_B, r))
    k = int.from_bytes(hashlib.sha512(rr + pub + message).digest(), 'little') % _L
    return rr + int.to_bytes((r + k * a) % _L, 32, 'little')


def _backend():
    """Fastest available signer. The reference code above is correct but slow;
    a real signature backend turns 100ms into microseconds."""
    try:
        from nacl.signing import SigningKey            # noqa: F401
        return 'nacl'
    except Exception:
        pass
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa
        return 'cryptography'
    except Exception:
        return 'python'


BACKEND = _backend()


def pubkey_of(seed):
    """32-byte seed → 32-byte public key."""
    if BACKEND == 'nacl':
        from nacl.signing import SigningKey
        return bytes(SigningKey(seed).verify_key)
    if BACKEND == 'cryptography':
        from cryptography.hazmat.primitives import serialization as s
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
            s.Encoding.Raw, s.PublicFormat.Raw)
    return _pure_pubkey(seed)


def sign(seed, message):
    """Detached 64-byte signature over `message`."""
    if BACKEND == 'nacl':
        from nacl.signing import SigningKey
        return SigningKey(seed).sign(message).signature
    if BACKEND == 'cryptography':
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(seed).sign(message)
    return _pure_sign(seed, message)


# ── program-derived addresses ────────────────────────────────────

PDA_MARKER = b'ProgramDerivedAddress'


def find_program_address(seeds, program_id):
    """The standard bump-seed search: the first bump from 255 down whose hash
    lands *off* the curve, so no private key can exist for it."""
    prog = b58decode(program_id)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b''.join(seeds) + bytes([bump]) + prog + PDA_MARKER).digest()
        if not on_curve(h):
            return b58encode(h), bump
    raise SolError('no bump seed produced an off-curve address')


# ── keystore ─────────────────────────────────────────────────────

def _load():
    try:
        with open(KEY_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise SolError(f'{KEY_FILE} is unreadable: {e}', status=500)


def _save(data):
    os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
    tmp = KEY_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, KEY_FILE)


def parse_secret(secret):
    """Accept every shape a Solana secret key travels in and return the 32-byte
    seed: a CLI keypair array (64 or 32 numbers), base58 (64 or 32 bytes), or hex."""
    if isinstance(secret, str):
        text = secret.strip()
        if os.path.exists(os.path.expanduser(text)):
            with open(os.path.expanduser(text)) as f:
                return parse_secret(json.load(f))
        if text.startswith('['):
            return parse_secret(json.loads(text))
        try:
            raw = bytes.fromhex(text) if len(text) in (64, 128) and \
                all(c in '0123456789abcdefABCDEF' for c in text) else b58decode(text)
        except Exception:
            raw = b58decode(text)
    elif isinstance(secret, (list, tuple)):
        raw = bytes(int(x) & 0xFF for x in secret)
    elif isinstance(secret, (bytes, bytearray)):
        raw = bytes(secret)
    else:
        raise SolError('secret must be a keypair array, base58 string, hex, or a file path')
    if len(raw) == 64:
        seed, claimed = raw[:32], raw[32:]
        if pubkey_of(seed) != claimed:
            raise SolError('keypair is inconsistent — the last 32 bytes are not '
                           'the public key of the first 32')
        return seed
    if len(raw) == 32:
        return raw
    raise SolError(f'a secret key is 32 or 64 bytes, got {len(raw)}')


def wallets():
    """Every stored wallet, secrets withheld."""
    data = _load()
    out = [{'name': n, 'address': w['address'],
            'default': n == data.get('_default'),
            'created': w.get('created')}
           for n, w in sorted(data.items()) if not n.startswith('_')]
    return {'wallets': out, 'count': len(out), 'keystore': KEY_FILE,
            'default': data.get('_default'), 'signer': BACKEND}


def create(name='default', secret=None, make_default=None, overwrite=False, created=None):
    """Store a wallet. Without `secret` a fresh keypair is generated here."""
    if name.startswith('_'):
        raise SolError('wallet names cannot start with _')
    data = _load()
    if name in data and not overwrite:
        raise SolError(f'wallet {name!r} already exists — pass overwrite=true to replace '
                       f'it (address {data[name]["address"]})', status=409)
    seed = parse_secret(secret) if secret is not None else os.urandom(32)
    address = b58encode(pubkey_of(seed))
    data[name] = {'address': address, 'secret': b58encode(seed), 'created': created}
    if make_default or '_default' not in data:
        data['_default'] = name
    _save(data)
    return {'name': name, 'address': address, 'imported': secret is not None,
            'default': data.get('_default') == name, 'keystore': KEY_FILE}


def remove(name):
    data = _load()
    if name not in data:
        raise SolError(f'no wallet named {name!r}', status=404)
    gone = data.pop(name)
    if data.get('_default') == name:
        rest = [n for n in data if not n.startswith('_')]
        data['_default'] = rest[0] if rest else None
    _save(data)
    return {'removed': name, 'address': gone['address']}


def set_default(name):
    data = _load()
    if name not in data:
        raise SolError(f'no wallet named {name!r}', status=404)
    data['_default'] = name
    _save(data)
    return {'default': name, 'address': data[name]['address']}


def signer(name=None, secret=None):
    """Resolve (seed, address) for a signature.

    Order: an explicit secret, then the named wallet, then SOLANA_SECRET_KEY,
    then the keystore default. A caller can always sign with a key this box has
    never seen.
    """
    if secret:
        seed = parse_secret(secret)
        return seed, b58encode(pubkey_of(seed))
    data = _load()
    if name is None and os.environ.get('SOLANA_SECRET_KEY'):
        seed = parse_secret(os.environ['SOLANA_SECRET_KEY'])
        return seed, b58encode(pubkey_of(seed))
    pick = name or data.get('_default')
    if not pick:
        raise SolError('no wallet to sign with — create one (sol_wallet action=create), '
                       'pass secret=…, or set SOLANA_SECRET_KEY', status=404)
    if pick not in data:
        raise SolError(f'no wallet named {pick!r} — {", ".join(n for n in data if not n.startswith("_")) or "the keystore is empty"}',
                       status=404)
    seed = parse_secret(data[pick]['secret'])
    return seed, data[pick]['address']


def export(name=None):
    """The secret, in the shape the Solana CLI reads. Only ever on request."""
    seed, address = signer(name)
    return {'address': address, 'secret_base58': b58encode(seed + pubkey_of(seed)),
            'keypair_json': list(seed + pubkey_of(seed)),
            'warning': 'anyone holding this can move every lamport in the account'}
