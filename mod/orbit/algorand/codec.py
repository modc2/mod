"""algorand codec — everything Algorand puts on the wire, in-tree.

Algorand signs canonical msgpack: maps with sorted keys, zero values omitted,
integers in their smallest encoding. Addresses are base32 of the 32-byte
ed25519 public key plus a 4-byte SHA-512/256 checksum. A transaction id is
base32(SHA-512/256("TX" || msgpack(txn))). Secret keys travel as 25-word
mnemonics: the 32-byte seed in 11-bit chunks over the BIP-39 English list,
plus one checksum word. ARC-4 is the ABI app calls speak.

No dependencies: ed25519 uses PyNaCl when present and a pure-Python RFC 8032
implementation otherwise; both are tested against each other.
"""

import base64
import hashlib
import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
ZERO_ADDRESS = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAY5HFKQ'


class CodecError(ValueError):
    pass


# ── hashing, base32, addresses ───────────────────────────────────

def sha512_256(data):
    return hashlib.new('sha512_256', data).digest()


def b32(data):
    return base64.b32encode(data).decode().rstrip('=')


def unb32(s):
    s = s.strip().upper()
    return base64.b32decode(s + '=' * (-len(s) % 8))


def encode_address(pk):
    if len(pk) != 32:
        raise CodecError('an address is a 32-byte public key')
    return b32(pk + sha512_256(pk)[-4:])


def decode_address(addr):
    """Address → 32-byte public key; raises on a bad checksum."""
    if not isinstance(addr, str) or len(addr.strip()) != 58:
        raise CodecError(f'not an Algorand address: {addr!r} (58 base32 chars)')
    try:
        raw = unb32(addr)
    except Exception:
        raise CodecError(f'not an Algorand address: {addr!r} (bad base32)')
    pk, check = raw[:32], raw[32:]
    if sha512_256(pk)[-4:] != check:
        raise CodecError(f'not an Algorand address: {addr!r} (checksum mismatch)')
    return pk


def is_address(s):
    try:
        decode_address(s)
        return True
    except CodecError:
        return False


def app_address(app_id):
    """Every application owns an account: sha512_256("appID" || uint64)."""
    return encode_address(sha512_256(b'appID' + struct.pack('>Q', int(app_id))))


def logic_address(program):
    """A logic signature's escrow account: sha512_256("Program" || bytecode)."""
    return encode_address(sha512_256(b'Program' + program))


# ── canonical msgpack ────────────────────────────────────────────

def _empty(v):
    return v is None or v is False or v == 0 and not isinstance(v, bool) \
        or (isinstance(v, (bytes, str, list, dict, tuple)) and len(v) == 0)


def msgpack(obj):
    """Canonical msgpack as Algorand hashes it: sorted map keys, empty map
    values omitted (recursively, maps only — list members are kept)."""
    out = bytearray()
    _pack(obj, out)
    return bytes(out)


def _pack(o, out):
    if o is None:
        out.append(0xc0)
    elif o is True:
        out.append(0xc3)
    elif o is False:
        out.append(0xc2)
    elif isinstance(o, int):
        if 0 <= o < 0x80:
            out.append(o)
        elif 0 <= o <= 0xff:
            out += b'\xcc' + struct.pack('>B', o)
        elif 0 <= o <= 0xffff:
            out += b'\xcd' + struct.pack('>H', o)
        elif 0 <= o <= 0xffffffff:
            out += b'\xce' + struct.pack('>I', o)
        elif 0 <= o < 1 << 64:
            out += b'\xcf' + struct.pack('>Q', o)
        elif -32 <= o < 0:
            out += struct.pack('>b', o)
        elif -128 <= o < 0:
            out += b'\xd0' + struct.pack('>b', o)
        elif -32768 <= o < 0:
            out += b'\xd1' + struct.pack('>h', o)
        elif -(1 << 31) <= o < 0:
            out += b'\xd2' + struct.pack('>i', o)
        elif -(1 << 63) <= o < 0:
            out += b'\xd3' + struct.pack('>q', o)
        else:
            raise CodecError(f'integer out of range: {o}')
    elif isinstance(o, str):
        b = o.encode()
        n = len(b)
        if n < 32:
            out.append(0xa0 | n)
        elif n <= 0xff:
            out += b'\xd9' + struct.pack('>B', n)
        elif n <= 0xffff:
            out += b'\xda' + struct.pack('>H', n)
        else:
            out += b'\xdb' + struct.pack('>I', n)
        out += b
    elif isinstance(o, (bytes, bytearray)):
        n = len(o)
        if n <= 0xff:
            out += b'\xc4' + struct.pack('>B', n)
        elif n <= 0xffff:
            out += b'\xc5' + struct.pack('>H', n)
        else:
            out += b'\xc6' + struct.pack('>I', n)
        out += bytes(o)
    elif isinstance(o, (list, tuple)):
        n = len(o)
        if n < 16:
            out.append(0x90 | n)
        elif n <= 0xffff:
            out += b'\xdc' + struct.pack('>H', n)
        else:
            out += b'\xdd' + struct.pack('>I', n)
        for v in o:
            _pack(v, out)
    elif isinstance(o, dict):
        items = sorted((k, v) for k, v in o.items() if not _empty(v))
        n = len(items)
        if n < 16:
            out.append(0x80 | n)
        elif n <= 0xffff:
            out += b'\xde' + struct.pack('>H', n)
        else:
            out += b'\xdf' + struct.pack('>I', n)
        for k, v in items:
            _pack(k, out)
            _pack(v, out)
    else:
        raise CodecError(f'cannot msgpack {type(o).__name__}')


def unmsgpack(data):
    obj, i = _unpack(memoryview(bytes(data)), 0)
    if i != len(data):
        raise CodecError(f'{len(data) - i} trailing bytes after msgpack value')
    return obj


def _unpack(b, i):
    t = b[i]
    i += 1
    if t < 0x80:
        return t, i
    if t >= 0xe0:
        return t - 0x100, i
    if 0x80 <= t <= 0x8f:
        return _map(b, i, t & 0x0f)
    if 0x90 <= t <= 0x9f:
        return _arr(b, i, t & 0x0f)
    if 0xa0 <= t <= 0xbf:
        n = t & 0x1f
        return bytes(b[i:i + n]).decode(), i + n
    fixed = {0xc0: None, 0xc2: False, 0xc3: True}
    if t in fixed:
        return fixed[t], i
    sizes = {0xcc: '>B', 0xcd: '>H', 0xce: '>I', 0xcf: '>Q',
             0xd0: '>b', 0xd1: '>h', 0xd2: '>i', 0xd3: '>q'}
    if t in sizes:
        fmt = sizes[t]
        n = struct.calcsize(fmt)
        return struct.unpack(fmt, b[i:i + n])[0], i + n
    lens = {0xc4: ('>B', 'bin'), 0xc5: ('>H', 'bin'), 0xc6: ('>I', 'bin'),
            0xd9: ('>B', 'str'), 0xda: ('>H', 'str'), 0xdb: ('>I', 'str'),
            0xdc: ('>H', 'arr'), 0xdd: ('>I', 'arr'),
            0xde: ('>H', 'map'), 0xdf: ('>I', 'map')}
    if t in lens:
        fmt, kind = lens[t]
        w = struct.calcsize(fmt)
        n = struct.unpack(fmt, b[i:i + w])[0]
        i += w
        if kind == 'bin':
            return bytes(b[i:i + n]), i + n
        if kind == 'str':
            return bytes(b[i:i + n]).decode(), i + n
        return (_arr if kind == 'arr' else _map)(b, i, n)
    raise CodecError(f'unsupported msgpack type byte 0x{t:02x}')


def _arr(b, i, n):
    out = []
    for _ in range(n):
        v, i = _unpack(b, i)
        out.append(v)
    return out, i


def _map(b, i, n):
    out = {}
    for _ in range(n):
        k, i = _unpack(b, i)
        v, i = _unpack(b, i)
        out[k] = v
    return out, i


# ── transactions ─────────────────────────────────────────────────

def txid(txn):
    return b32(sha512_256(b'TX' + msgpack(txn)))


def group_id(txns):
    """The group hash every member of an atomic group carries in `grp`."""
    hashes = [sha512_256(b'TX' + msgpack({k: v for k, v in t.items() if k != 'grp'}))
              for t in txns]
    return sha512_256(b'TG' + msgpack({'txlist': hashes}))


# ── ed25519 ──────────────────────────────────────────────────────

try:
    import nacl.signing as _nacl
except Exception:            # pragma: no cover — exercised when PyNaCl is absent
    _nacl = None

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _xrecover(y):
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    return _P - x if x % 2 else x


_BY = 4 * pow(5, _P - 2, _P) % _P
_B = (_xrecover(_BY), _BY, 1, _xrecover(_BY) * _BY % _P)


def _add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = t1 * 2 * _D * t2 % _P
    d = z1 * 2 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return e * f % _P, g * h % _P, f * g % _P, e * h % _P


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _enc(p):
    x, y, z, _ = p
    zi = pow(z, _P - 2, _P)
    x, y = x * zi % _P, y * zi % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _expand(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def _py_public(seed):
    return _enc(_mul(_expand(seed)[0], _B))


def _py_sign(seed, msg):
    a, prefix = _expand(seed)
    pk = _enc(_mul(a, _B))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), 'little') % _L
    R = _enc(_mul(r, _B))
    k = int.from_bytes(hashlib.sha512(R + pk + msg).digest(), 'little') % _L
    return R + int.to_bytes((r + k * a) % _L, 32, 'little')


def public_key(seed):
    if _nacl:
        return bytes(_nacl.SigningKey(seed).verify_key)
    return _py_public(seed)


def sign(seed, msg):
    if _nacl:
        return _nacl.SigningKey(seed).sign(msg).signature
    return _py_sign(seed, msg)


def verify(pk, msg, sig):
    if _nacl:
        try:
            _nacl.VerifyKey(pk).verify(msg, sig)
            return True
        except Exception:
            return False
    raise CodecError('signature verification needs PyNaCl')


def sign_txn(seed, txn):
    """{'sig', 'txn'} as algod wants it, plus the id. If the account is
    rekeyed the caller sets `sgnr` on the envelope itself."""
    return {'sig': sign(seed, b'TX' + msgpack(txn)), 'txn': txn}


# ── mnemonics ────────────────────────────────────────────────────

_WORDS = None


def wordlist():
    global _WORDS
    if _WORDS is None:
        with open(os.path.join(HERE, 'wordlist.txt')) as f:
            _WORDS = [w.strip() for w in f if w.strip()]
        assert len(_WORDS) == 2048
    return _WORDS


def _to11(data):
    buf, bits, out = 0, 0, []
    for byte in data:
        buf |= byte << bits
        bits += 8
        if bits >= 11:
            out.append(buf & 0x7ff)
            buf >>= 11
            bits -= 11
    if bits:
        out.append(buf & 0x7ff)
    return out


def _from11(nums):
    buf, bits, out = 0, 0, bytearray()
    for n in nums:
        buf |= n << bits
        bits += 11
        while bits >= 8:
            out.append(buf & 0xff)
            buf >>= 8
            bits -= 8
    if bits:
        out.append(buf & 0xff)
    return bytes(out)


def to_mnemonic(seed):
    words = wordlist()
    nums = _to11(seed)
    check = _to11(sha512_256(seed)[:2])[0]
    return ' '.join(words[n] for n in nums + [check])


def from_mnemonic(phrase):
    words = wordlist()
    index = {w: i for i, w in enumerate(words)}
    parts = phrase.lower().split()
    if len(parts) != 25:
        raise CodecError(f'an Algorand mnemonic is 25 words, got {len(parts)}')
    # BIP-39 words are unique in their first four letters; accept prefixes.
    nums = []
    for w in parts:
        if w in index:
            nums.append(index[w])
            continue
        hits = [i for x, i in index.items() if x.startswith(w[:4])]
        if len(hits) != 1:
            raise CodecError(f'{w!r} is not a mnemonic word')
        nums.append(hits[0])
    raw = _from11(nums[:24])
    if len(raw) != 33 or raw[32] != 0:
        raise CodecError('mnemonic does not decode to a 32-byte key')
    seed = raw[:32]
    if _to11(sha512_256(seed)[:2])[0] != nums[24]:
        raise CodecError('mnemonic checksum word does not match')
    return seed


# ── ARC-4 ABI (the scalar and dynamic types most methods use) ────

ABI_RETURN_PREFIX = bytes.fromhex('151f7c75')


def method_selector(signature):
    """'add(uint64,uint64)uint64' → 4-byte selector."""
    return sha512_256(signature.encode())[:4]


def parse_signature(signature):
    """'name(t1,t2)ret' → (name, [t1, t2], ret). Tuple types are not
    supported here — top-level comma split on depth 0 handles nesting."""
    sig = signature.strip()
    if '(' not in sig or ')' not in sig:
        raise CodecError(f'not an ARC-4 method signature: {signature!r}')
    name, rest = sig.split('(', 1)
    depth, cur, args, i = 0, '', [], 0
    while i < len(rest):
        c = rest[i]
        if c == '(':
            depth += 1
        elif c == ')':
            if depth == 0:
                break
            depth -= 1
        if c == ',' and depth == 0:
            args.append(cur)
            cur = ''
        else:
            cur += c
        i += 1
    if cur:
        args.append(cur)
    return name, args, rest[i + 1:] or 'void'


def abi_encode(t, v):
    if t.startswith('uint') or t.startswith('ufixed'):
        bits = int(t[4:].split('x')[0]) if t.startswith('uint') else \
            int(t[6:].split('x')[0])
        return int(v).to_bytes(bits // 8, 'big')
    if t == 'byte':
        return bytes([int(v)])
    if t == 'bool':
        return b'\x80' if str(v).lower() in ('true', '1') or v is True else b'\x00'
    if t == 'address':
        return decode_address(v)
    if t == 'string':
        b = str(v).encode()
        return struct.pack('>H', len(b)) + b
    if t == 'byte[]':
        b = _as_bytes(v)
        return struct.pack('>H', len(b)) + b
    if t.startswith('byte[') and t.endswith(']'):
        return _as_bytes(v)
    raise CodecError(f'ABI type {t!r} is not supported here — pass raw args instead')


def abi_decode(t, b):
    if t == 'void':
        return None
    if t.startswith('uint'):
        return int.from_bytes(b[:int(t[4:]) // 8], 'big')
    if t == 'byte':
        return b[0]
    if t == 'bool':
        return bool(b[0] & 0x80)
    if t == 'address':
        return encode_address(b[:32])
    if t == 'string':
        n = struct.unpack('>H', b[:2])[0]
        return b[2:2 + n].decode(errors='replace')
    if t == 'byte[]':
        n = struct.unpack('>H', b[:2])[0]
        return b[2:2 + n].hex()
    return b.hex()


def _as_bytes(v):
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    s = str(v)
    if s.startswith('0x'):
        return bytes.fromhex(s[2:])
    if s.startswith('b64:'):
        return base64.b64decode(s[4:])
    return s.encode()


def app_arg(v):
    """One raw application arg: int → uint64 big-endian, 'b64:'/'0x' → bytes,
    a valid address → its public key, 'str:' or anything else → utf-8."""
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return struct.pack('>Q', v)
    s = str(v)
    if s.startswith('int:'):
        return struct.pack('>Q', int(s[4:]))
    if s.startswith('str:'):
        return s[4:].encode()
    if s.startswith('addr:'):
        return decode_address(s[5:])
    return _as_bytes(s)


def b64(data):
    return base64.b64encode(data).decode()


def unb64(s):
    return base64.b64decode(s or '')
