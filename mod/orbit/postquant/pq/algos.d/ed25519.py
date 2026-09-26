"""ed25519 — the worked example of adding a key type, and a cautionary one.

This is RFC 8032 Ed25519, pure Python, registered through the same four-
function dynamic as ML-DSA and SLH-DSA: keygen from a 32-byte seed, sign,
verify, sizes. It exists to show that ANY public/private/data/signature
scheme — ecdsa, sr25519, whatever comes next — drops into this chain as one
file in a plugin directory.

It also exists to be refused. It declares quantum_safe=False, because a
64-byte curve signature is exactly what Shor's algorithm takes apart, so the
chain lists it in pq_algos and turns its witnesses away at the mempool. Set
POSTQUANT_ALLOW_CLASSICAL=1 on a throwaway devnet to watch it get in — and
notice, while it is in, that its witness costs 96 bytes of gas against
ML-DSA's 3732. Cheap is what vulnerable looks like.

The ctx wrapper mirrors the FIPS one (a length-prefixed context ahead of the
message) so domain separation works the same across every key type here; that
framing is this module's convention, not RFC 8032's.
"""

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
I = pow(2, (P - 1) // 4, P)


def _sha512(*parts):
    h = hashlib.sha512()
    for part in parts:
        h.update(part)
    return h.digest()


# Points in extended homogeneous coordinates (x, y, z, t), t = xy/z.

def _add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = 2 * t1 * t2 * D % P
    d = 2 * z1 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _recover_x(y, sign):
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * I % P
    if (x * x - x2) % P:
        return None
    if x == 0 and sign:
        return None
    if x & 1 != sign:
        x = P - x
    return x


_BY = 4 * pow(5, P - 2, P) % P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY, 1, _BX * _BY % P)


def _compress(p):
    x, y, z, _ = p
    zi = pow(z, P - 2, P)
    x, y = x * zi % P, y * zi % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(b):
    y = int.from_bytes(b, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _clamp(h32):
    a = int.from_bytes(h32, "little")
    return (a & ~7 & ~(1 << 255) & ((1 << 254) - 1)) | (1 << 254)


def _wrap(msg, ctx):
    if len(ctx) > 255:
        raise ValueError("context must be at most 255 bytes")
    return b"\x00" + bytes([len(ctx)]) + ctx + msg


def keygen(seed):
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    a = _clamp(_sha512(seed)[:32])
    pk = _compress(_mul(a, _B))
    return pk, seed + pk


def sign(sk, msg, ctx=b""):
    seed, pk = sk[:32], sk[32:]
    m = _wrap(msg, ctx)
    h = _sha512(seed)
    a, prefix = _clamp(h[:32]), h[32:]
    r = int.from_bytes(_sha512(prefix, m), "little") % L
    rb = _compress(_mul(r, _B))
    k = int.from_bytes(_sha512(rb, pk, m), "little") % L
    s = (r + k * a) % L
    return rb + s.to_bytes(32, "little")


def verify(pk, msg, sig, ctx=b""):
    try:
        if len(sig) != 64 or len(pk) != 32:
            return False
        a = _decompress(pk)
        r = _decompress(sig[:32])
        s = int.from_bytes(sig[32:], "little")
        if a is None or r is None or s >= L:
            return False
        m = _wrap(msg, ctx)
        k = int.from_bytes(_sha512(sig[:32], pk, m), "little") % L
        left = _mul(s, _B)
        right = _add(r, _mul(k, a))
        # cross-multiplied projective equality
        return (left[0] * right[2] - right[0] * left[2]) % P == 0 and \
               (left[1] * right[2] - right[1] * left[2]) % P == 0
    except Exception:
        return False


def register(algos):
    algos.register(algos.SigAlgo(
        "ed25519", keygen, sign, verify,
        sizes={"pk": 32, "sig": 64, "seed": 32},
        family="EdDSA", standard="RFC 8032",
        basis="curve25519 discrete log — Shor breaks this",
        quantum_safe=False,
        note="the worked example: registers fine, witnesses refused unless "
             "POSTQUANT_ALLOW_CLASSICAL=1"))
