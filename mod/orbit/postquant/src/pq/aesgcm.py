"""AES-256-GCM (FIPS 197 + SP 800-38D), pure python, no dependencies.

Why this exists in a chain with no curves: the vault encrypts on the CLIENT,
and the browser client can only reach WebCrypto, whose one authenticated
cipher everywhere is AES-GCM. The python client (mod.py, the shell) has to
produce the exact same envelope, and stdlib has no AES — so here it is.
AES-256 under Grover keeps an effective 128-bit key, the same margin the
chain's SHA3-256 commitments keep; it is the symmetric half of a
post-quantum design, not a concession.

Speed: a few hundred KB/s. That is fine for the sizes the vault takes; a browser does
the heavy lifting through native WebCrypto.

    ct_and_tag = encrypt(key32, iv12, plaintext, aad)
    plaintext  = decrypt(key32, iv12, ct_and_tag, aad)   # ValueError on tamper
"""

import hmac

_SBOX = bytes.fromhex(
    '637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0'
    'b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275'
    '09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cf'
    'd0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2'
    'cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdb'
    'e0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08'
    'ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9e'
    'e1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16')


def _xt(b):
    return ((b << 1) ^ 0x1b) & 0xff if b & 0x80 else b << 1


# T-table for the combined SubBytes+MixColumns step, big-endian words.
_T0 = []
for _s in _SBOX:
    _T0.append((_xt(_s) << 24) | (_s << 16) | (_s << 8) | (_xt(_s) ^ _s))
_T1 = [((t >> 8) | (t << 24)) & 0xffffffff for t in _T0]
_T2 = [((t >> 16) | (t << 16)) & 0xffffffff for t in _T0]
_T3 = [((t >> 24) | (t << 8)) & 0xffffffff for t in _T0]


def _expand(key):
    if len(key) != 32:
        raise ValueError('AES-256 needs a 32-byte key')
    w = [int.from_bytes(key[i:i + 4], 'big') for i in range(0, 32, 4)]
    rcon = 1
    for i in range(8, 60):
        t = w[i - 1]
        if i % 8 == 0:
            t = ((t << 8) | (t >> 24)) & 0xffffffff
            t = (_SBOX[t >> 24] << 24 | _SBOX[(t >> 16) & 255] << 16 |
                 _SBOX[(t >> 8) & 255] << 8 | _SBOX[t & 255])
            t ^= rcon << 24
            rcon = _xt(rcon)
        elif i % 8 == 4:
            t = (_SBOX[t >> 24] << 24 | _SBOX[(t >> 16) & 255] << 16 |
                 _SBOX[(t >> 8) & 255] << 8 | _SBOX[t & 255])
        w.append(w[i - 8] ^ t)
    return w


def _block(w, b):
    s0, s1, s2, s3 = (int.from_bytes(b[i:i + 4], 'big') ^ w[i // 4]
                      for i in range(0, 16, 4))
    for r in range(1, 14):
        k = 4 * r
        t0 = (_T0[s0 >> 24] ^ _T1[(s1 >> 16) & 255] ^ _T2[(s2 >> 8) & 255] ^
              _T3[s3 & 255] ^ w[k])
        t1 = (_T0[s1 >> 24] ^ _T1[(s2 >> 16) & 255] ^ _T2[(s3 >> 8) & 255] ^
              _T3[s0 & 255] ^ w[k + 1])
        t2 = (_T0[s2 >> 24] ^ _T1[(s3 >> 16) & 255] ^ _T2[(s0 >> 8) & 255] ^
              _T3[s1 & 255] ^ w[k + 2])
        t3 = (_T0[s3 >> 24] ^ _T1[(s0 >> 16) & 255] ^ _T2[(s1 >> 8) & 255] ^
              _T3[s2 & 255] ^ w[k + 3])
        s0, s1, s2, s3 = t0, t1, t2, t3
    out = b''
    for i, (a, bb, c, d) in enumerate(((s0, s1, s2, s3), (s1, s2, s3, s0),
                                       (s2, s3, s0, s1), (s3, s0, s1, s2))):
        word = (_SBOX[a >> 24] << 24 | _SBOX[(bb >> 16) & 255] << 16 |
                _SBOX[(c >> 8) & 255] << 8 | _SBOX[d & 255]) ^ w[56 + i]
        out += word.to_bytes(4, 'big')
    return out


_R = 0xe1 << 120


def _gmul(x, y):
    z = 0
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            z ^= x
        x = (x >> 1) ^ _R if x & 1 else x >> 1
    return z


def _ghash(h, aad, ct):
    # 8-bit table for H: 16 lookups per block instead of 128 shifts.
    table = [_gmul(h, b << 120) for b in range(256)]

    def mul(x):
        z = 0
        for i in range(16):
            z = (z >> 8) ^ _REDUCE[z & 0xff] if i else z
            z ^= table[(x >> (8 * i)) & 0xff]
        return z

    y = 0
    for data in (aad, ct):
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16].ljust(16, b'\0')
            y = mul(y ^ int.from_bytes(chunk, 'big'))
    lens = (len(aad) * 8).to_bytes(8, 'big') + (len(ct) * 8).to_bytes(8, 'big')
    return mul(y ^ int.from_bytes(lens, 'big'))


# x * 2^-8 reduction: the bits shifted off the low end fold back in.
_REDUCE = []
for _b in range(256):
    _v = _b
    _acc = 0
    for _ in range(8):
        _acc = (_acc >> 1) ^ _R if _v & 1 else _acc >> 1
        _v >>= 1
    _REDUCE.append(_acc)


def _ctr(w, j0, data):
    out = bytearray(len(data))
    ctr = int.from_bytes(j0, 'big')
    for i in range(0, len(data), 16):
        ctr = (ctr & ~0xffffffff) | ((ctr + 1) & 0xffffffff)
        ks = _block(w, ctr.to_bytes(16, 'big'))
        chunk = data[i:i + 16]
        n = len(chunk)
        x = int.from_bytes(chunk, 'big') ^ int.from_bytes(ks[:n], 'big')
        out[i:i + n] = x.to_bytes(n, 'big')
    return bytes(out)


def _setup(key, iv):
    if len(iv) != 12:
        raise ValueError('GCM here takes a 12-byte iv, as WebCrypto does')
    w = _expand(key)
    h = int.from_bytes(_block(w, b'\0' * 16), 'big')
    return w, h, iv + b'\0\0\0\1'


def encrypt(key, iv, plaintext, aad=b''):
    w, h, j0 = _setup(key, iv)
    ct = _ctr(w, j0, plaintext)
    s = _ghash(h, aad, ct)
    tag = int.from_bytes(_block(w, j0), 'big') ^ s
    return ct + tag.to_bytes(16, 'big')


def decrypt(key, iv, ct_and_tag, aad=b''):
    if len(ct_and_tag) < 16:
        raise ValueError('ciphertext shorter than its tag')
    ct, tag = ct_and_tag[:-16], ct_and_tag[-16:]
    w, h, j0 = _setup(key, iv)
    s = _ghash(h, aad, ct)
    want = (int.from_bytes(_block(w, j0), 'big') ^ s).to_bytes(16, 'big')
    if not hmac.compare_digest(want, tag):
        raise ValueError('authentication failed — wrong key or tampered blob')
    return _ctr(w, j0, ct)
