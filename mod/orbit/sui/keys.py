#!/usr/bin/env python3
"""base58, bech32, ed25519 and Sui addresses — the parts of Sui that are maths
rather than network.

A Sui address is not a public key. It is `blake2b256(scheme_flag || pubkey)`,
which means you cannot read a key out of an address and you cannot tell an
address apart from an object ID by looking at it — both are 32 bytes of hex.
That derivation is implemented here and pinned in the tests against real
signatures pulled off mainnet.

Secrets travel in four shapes and all four are accepted: the CLI's
`suiprivkey1…` bech32 string, the base64 `flag || seed` used inside
`~/.sui/sui_config/sui.keystore`, raw hex, and base58. Export gives back the
bech32 form, because that is the one the Sui CLI reads.

ed25519 is implemented from scratch so the module has no hard dependencies;
when PyNaCl or `cryptography` is installed we sign with that instead, because a
C signature is ~1000x faster and the bytes are identical either way.

The keystore lives in ~/.mod/sui/keys.json, mode 0600, off the source tree — a
secret key is never written next to the code that uses it.
"""

import hashlib
import json
import os
import stat

KEY_DIR = os.path.expanduser(os.environ.get('SUI_KEY_DIR', '~/.mod/sui'))
KEY_FILE = os.path.join(KEY_DIR, 'keys.json')

# Signature scheme flags. Only ed25519 signs here; the others are recognised so
# that an address derived from someone else's key still identifies correctly.
FLAG_ED25519 = 0x00
FLAG_SECP256K1 = 0x01
FLAG_SECP256R1 = 0x02
FLAG_MULTISIG = 0x03
FLAG_ZKLOGIN = 0x05
FLAG_PASSKEY = 0x06
SCHEMES = {FLAG_ED25519: 'ed25519', FLAG_SECP256K1: 'secp256k1',
           FLAG_SECP256R1: 'secp256r1', FLAG_MULTISIG: 'multisig',
           FLAG_ZKLOGIN: 'zklogin', FLAG_PASSKEY: 'passkey'}


class SuiError(Exception):
    """A failure worth showing the caller verbatim."""

    def __init__(self, message, status=400, detail=None):
        super().__init__(message)
        self.status, self.detail = status, detail

    def dict(self):
        out = {'error': str(self)}
        if self.detail is not None:
            out['detail'] = self.detail
        return out


# ── base58 (object digests and transaction digests) ──────────────

B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
B58_INDEX = {c: i for i, c in enumerate(B58)}


def b58encode(raw):
    n = int.from_bytes(raw, 'big')
    out = ''
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return '1' * (len(raw) - len(raw.lstrip(b'\0'))) + (out or '')


def b58decode(text):
    if not isinstance(text, str):
        raise SuiError(f'expected a base58 string, got {type(text).__name__}')
    if not text:
        return b''
    n = 0
    for ch in text:
        if ch not in B58_INDEX:
            raise SuiError(f'{ch!r} is not a base58 character — {text[:16]}… is not '
                           'a digest')
        n = n * 58 + B58_INDEX[ch]
    pad = len(text) - len(text.lstrip('1'))
    body = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    return b'\0' * pad + body


def is_digest(text):
    """A transaction or object digest is 32 bytes of base58 — never 0x-prefixed.
    That is the one shape on Sui that is unambiguous."""
    try:
        return isinstance(text, str) and not text.startswith('0x') and \
            len(b58decode(text.strip())) == 32
    except Exception:
        return False


# ── bech32 (the suiprivkey format, BIP-173) ──────────────────────

BECH32_CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
PRIVKEY_HRP = 'suiprivkey'


def _bech32_polymod(values):
    gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _convertbits(data, frm, to, pad=True):
    acc = bits = 0
    out = []
    maxv = (1 << to) - 1
    for value in data:
        if value < 0 or value >> frm:
            return None
        acc = (acc << frm) | value
        bits += frm
        while bits >= to:
            bits -= to
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (to - bits)) & maxv)
    elif bits >= frm or ((acc << (to - bits)) & maxv):
        return None
    return out


def bech32_encode(hrp, raw):
    data = _convertbits(raw, 8, 5)
    checksum = _bech32_polymod(_hrp_expand(hrp) + data + [0] * 6) ^ 1
    full = data + [(checksum >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + '1' + ''.join(BECH32_CHARSET[d] for d in full)


def bech32_decode(text):
    text = text.strip()
    if text.lower() != text and text.upper() != text:
        raise SuiError('a bech32 string is all lower or all upper case, not mixed')
    text = text.lower()
    pos = text.rfind('1')
    if pos < 1 or pos + 7 > len(text):
        raise SuiError(f'{text[:20]}… is not a bech32 string')
    hrp, body = text[:pos], text[pos + 1:]
    if any(c not in BECH32_CHARSET for c in body):
        raise SuiError('bech32 body contains a character outside the charset')
    data = [BECH32_CHARSET.index(c) for c in body]
    if _bech32_polymod(_hrp_expand(hrp) + data) != 1:
        raise SuiError('bech32 checksum failed — the key string is mistyped or '
                       'truncated')
    raw = _convertbits(data[:-6], 5, 8, pad=False)
    if raw is None:
        raise SuiError('bech32 payload is not a whole number of bytes')
    return hrp, bytes(raw)


# ── addresses ────────────────────────────────────────────────────

def normalize(address, what='address'):
    """Sui prints addresses short (`0x2`) and stores them long. One form here:
    0x + 64 hex, so that string comparison means what it looks like it means."""
    if not isinstance(address, str):
        raise SuiError(f'{what} must be a string, got {type(address).__name__}')
    text = address.strip().lower()
    if text.startswith('0x'):
        text = text[2:]
    if not text or len(text) > 64 or any(c not in '0123456789abcdef' for c in text):
        raise SuiError(f'{what} must be hex, up to 32 bytes — got {address!r}')
    return '0x' + text.rjust(64, '0')


def is_address(text):
    try:
        normalize(text)
        return True
    except Exception:
        return False


def need_address(text, what='address'):
    return normalize(text, what)


def short(address, keep=6):
    """0x1234…cdef — for tables, never for comparison."""
    a = normalize(address)
    return f'{a[:2 + keep]}…{a[-4:]}'


def address_of(pubkey, flag=FLAG_ED25519):
    """The derivation that makes an address unreadable: blake2b256(flag||pubkey).

    Pinned in the tests against signatures taken off mainnet — get this wrong
    and every balance lookup silently answers about the wrong account.
    """
    return '0x' + hashlib.blake2b(bytes([flag]) + bytes(pubkey),
                                  digest_size=32).hexdigest()


def blake2b(data):
    return hashlib.blake2b(data, digest_size=32).digest()


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


# ── secrets ──────────────────────────────────────────────────────

def parse_secret(secret):
    """Every shape a Sui secret travels in → a 32-byte ed25519 seed.

    `suiprivkey1…` (the CLI's export format), base64 of `flag || seed` (what
    sits in sui.keystore), raw hex, base58, or a path to a keystore file. A
    keystore file holds a JSON array; the first entry is used.
    """
    if isinstance(secret, (bytes, bytearray)):
        raw = bytes(secret)
    elif isinstance(secret, (list, tuple)):
        if secret and isinstance(secret[0], str):
            return parse_secret(secret[0])          # a sui.keystore array
        raw = bytes(int(x) & 0xFF for x in secret)
    elif isinstance(secret, str):
        text = secret.strip()
        if not text:
            raise SuiError('secret is empty')
        path = os.path.expanduser(text)
        if os.path.sep in text and os.path.exists(path):
            with open(path) as f:
                return parse_secret(json.load(f))
        if text.lower().startswith(PRIVKEY_HRP):
            hrp, raw = bech32_decode(text)
            if hrp != PRIVKEY_HRP:
                raise SuiError(f'expected a {PRIVKEY_HRP}1… string, got {hrp}1…')
        elif all(c in '0123456789abcdefABCDEF' for c in text.removeprefix('0x')) \
                and len(text.removeprefix('0x')) in (64, 66):
            raw = bytes.fromhex(text.removeprefix('0x'))
        else:
            import base64
            try:
                raw = base64.b64decode(text, validate=True)
            except Exception:
                raw = b58decode(text)
    else:
        raise SuiError('secret must be a suiprivkey string, base64, hex, or a path')

    if len(raw) == 33:
        flag, seed = raw[0], raw[1:]
        if flag != FLAG_ED25519:
            raise SuiError(
                f'that is a {SCHEMES.get(flag, "scheme " + hex(flag))} key. This '
                'module signs with ed25519 only — it can still READ any address, '
                'but it cannot sign for that one')
        return seed
    if len(raw) == 32:
        return raw                                   # a bare seed, flag implied
    if len(raw) == 64:
        seed, claimed = raw[:32], raw[32:]
        if pubkey_of(seed) != claimed:
            raise SuiError('64-byte keypair is inconsistent — the last 32 bytes are '
                           'not the public key of the first 32')
        return seed
    raise SuiError(f'a Sui secret key is 32 bytes (or 33 with a scheme flag), '
                   f'got {len(raw)}')


def to_suiprivkey(seed):
    """The form the Sui CLI reads back: bech32 over flag || seed."""
    return bech32_encode(PRIVKEY_HRP, bytes([FLAG_ED25519]) + seed)


# ── keystore ─────────────────────────────────────────────────────

def _load():
    try:
        with open(KEY_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        raise SuiError(f'{KEY_FILE} is unreadable: {e}', status=500)


def _save(data):
    os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
    tmp = KEY_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, KEY_FILE)


def wallets():
    """Every stored wallet, secrets withheld."""
    data = _load()
    out = [{'name': n, 'address': w['address'],
            'default': n == data.get('_default'), 'created': w.get('created')}
           for n, w in sorted(data.items()) if not n.startswith('_')]
    return {'wallets': out, 'count': len(out), 'keystore': KEY_FILE,
            'default': data.get('_default'), 'signer': BACKEND,
            'scheme': 'ed25519'}


def create(name='default', secret=None, make_default=None, overwrite=False,
           created=None):
    """Store a wallet. Without `secret` a fresh keypair is generated here."""
    if name.startswith('_'):
        raise SuiError('wallet names cannot start with _')
    data = _load()
    if name in data and not overwrite:
        raise SuiError(f'wallet {name!r} already exists — pass overwrite=true to '
                       f'replace it (address {data[name]["address"]})', status=409)
    seed = parse_secret(secret) if secret is not None else os.urandom(32)
    address = address_of(pubkey_of(seed))
    data[name] = {'address': address, 'secret': to_suiprivkey(seed),
                  'created': created}
    if make_default or '_default' not in data:
        data['_default'] = name
    _save(data)
    return {'name': name, 'address': address, 'imported': secret is not None,
            'default': data.get('_default') == name, 'keystore': KEY_FILE}


def remove(name):
    data = _load()
    if name not in data:
        raise SuiError(f'no wallet named {name!r}', status=404)
    gone = data.pop(name)
    if data.get('_default') == name:
        rest = [n for n in data if not n.startswith('_')]
        data['_default'] = rest[0] if rest else None
    _save(data)
    return {'removed': name, 'address': gone['address']}


def set_default(name):
    data = _load()
    if name not in data:
        raise SuiError(f'no wallet named {name!r}', status=404)
    data['_default'] = name
    _save(data)
    return {'default': name, 'address': data[name]['address']}


def signer(name=None, secret=None):
    """Resolve (seed, address) for a signature.

    Order: an explicit secret, then the named wallet, then SUI_SECRET_KEY, then
    the keystore default. A caller can always sign with a key this box has
    never seen.
    """
    if secret:
        seed = parse_secret(secret)
        return seed, address_of(pubkey_of(seed))
    data = _load()
    if name is None and os.environ.get('SUI_SECRET_KEY'):
        seed = parse_secret(os.environ['SUI_SECRET_KEY'])
        return seed, address_of(pubkey_of(seed))
    pick = name or data.get('_default')
    if not pick:
        raise SuiError('no wallet to sign with — create one (sui_wallet '
                       'action=create), pass secret=…, or set SUI_SECRET_KEY',
                       status=404)
    if pick not in data:
        known = ', '.join(n for n in data if not n.startswith('_'))
        raise SuiError(f'no wallet named {pick!r} — '
                       f'{known or "the keystore is empty"}', status=404)
    return parse_secret(data[pick]['secret']), data[pick]['address']


def export(name=None):
    """The secret, in the form the Sui CLI reads. Only ever on request."""
    seed, address = signer(name)
    return {'address': address, 'suiprivkey': to_suiprivkey(seed),
            'scheme': 'ed25519',
            'warning': 'anyone holding this can move every object in the account'}
