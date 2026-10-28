"""lsag.py — the signer half of the scheme in pure Python, a line-for-line
port of networks/starknet/contract/src/lsag.cairo (sign, verify, key
image, ring sort, scopes). Same inputs give the same felts as the Cairo
prover — tests/test_core.py pins that — but in milliseconds instead of a
1-4 s `scarb cairo-run` per signature, which was most of an epoch's wall
time. The Cairo prover stays the reference (`ZTAO_SIGNER=cairo`).
"""
from .stark import (G, P, TAG_CHAL, TAG_H2P, TAG_KEY, TAG_MSG, BETA, JINF,
                    h, jadd_aff, jdbl, sqrt, to_aff)
from .util import ORDER

_H2P = {}


def short(s):
    return int.from_bytes(s.encode(), 'big')


def scalar(v):
    return int(v) % ORDER


def _jadd(p, q):
    """Jacobian + Jacobian."""
    if p[2] == 0:
        return q
    if q[2] == 0:
        return p
    X1, Y1, Z1 = p
    X2, Y2, Z2 = q
    Z1Z1, Z2Z2 = Z1 * Z1 % P, Z2 * Z2 % P
    U1, U2 = X1 * Z2Z2 % P, X2 * Z1Z1 % P
    S1, S2 = Y1 * Z2 * Z2Z2 % P, Y2 * Z1 * Z1Z1 % P
    H, r = (U2 - U1) % P, (S2 - S1) % P
    if H == 0:
        return jdbl(p) if r == 0 else JINF
    HH = H * H % P
    HHH = H * HH % P
    V = U1 * HH % P
    X3 = (r * r - HHH - 2 * V) % P
    return (X3, (r * (V - X3) - S1 * HHH) % P, Z1 * Z2 * H % P)


def _table(A):
    """[0..15]*A in Jacobian (no inversions)."""
    t = [JINF, (A[0], A[1], 1)]
    for _ in range(14):
        t.append(jadd_aff(t[-1], A))
    return t


def _window(pairs):
    """sum k_i * A_i (Straus, 4-bit windows) -> affine or None."""
    tabs = [(k % ORDER, _table(A)) for k, A in pairs if A is not None]
    acc = JINF
    for i in range(63, -1, -1):
        for _ in range(4):
            acc = jdbl(acc)
        for k, t in tabs:
            d = (k >> (4 * i)) & 15
            if d:
                acc = _jadd(acc, t[d])
    return to_aff(acc)


def mul(k, A):
    return _window([(k, A)])


def mul2(a, A, b, B):
    return _window([(a, A), (b, B)])


def xy(p):
    return p if p is not None else (0, 0)


def public_key(secret):
    return mul(scalar(secret), G)


def key_id(p):
    return h(TAG_KEY, p[0], p[1])


def hash_to_point(scope):
    """Cairo's EcPointTrait::new_from_x keeps the smaller root."""
    scope = int(scope) % P
    hp = _H2P.get(scope)
    if hp is None:
        ctr = 0
        while True:
            x = h(TAG_H2P, scope, ctr)
            r = sqrt((x ** 3 + x + BETA) % P)
            if r is not None:
                hp = (x, min(r, P - r))
                break
            ctr += 1
        if len(_H2P) > 4096:
            _H2P.clear()
        _H2P[scope] = hp
    return hp


def key_image(secret, scope):
    return mul(scalar(secret), hash_to_point(scope))


def scope(kind, subnet, epoch, tag=0):
    if kind == 1:
        return h(short('zuma-mine'), subnet, epoch)
    return h(short('zuma-score'), subnet, epoch, tag)


def sort_ring(ring):
    return sorted(ring, key=key_id)


def digest(ring, scope, msg, image):
    xs = [TAG_MSG, scope, msg, image[0], image[1], len(ring)]
    for p in ring:
        xs += [p[0], p[1]]
    return h(*xs)


def challenge(d, l, r):
    return h(TAG_CHAL, d, *xy(l), *xy(r))


def sign(ring, idx, secret, scope, msg, alpha, fill):
    """ring sorted; returns (c0, s[], image). Mirrors lsag::sign."""
    n = len(ring)
    x, a = scalar(secret), scalar(alpha)
    assert x and ring[idx] == public_key(x), 'secret not ring[idx]'
    hp = hash_to_point(scope)
    image = mul(x, hp)
    d = digest(ring, scope, msg, image)
    cs, ss = {}, {}
    j = (idx + 1) % n
    cs[j] = challenge(d, mul(a, G), mul(a, hp))
    while j != idx:
        s = scalar(fill[j])
        ss[j] = s
        c = cs[j]
        l = mul2(s, G, c, ring[j])
        r = mul2(s, hp, c, image)
        j = (j + 1) % n
        cs[j] = challenge(d, l, r)
    ss[idx] = (a - (cs[idx] % ORDER) * x) % ORDER
    return cs[0], [ss[k] for k in range(n)], image


def on_curve(p):
    return p is not None and p[1] * p[1] % P == (p[0] ** 3 + p[0] + BETA) % P


def verify(ring, scope, msg, c0, s, image):
    n = len(ring)
    if not n or len(s) != n or not on_curve(image):
        return False
    hp = hash_to_point(scope)
    d = digest(ring, scope, msg, image)
    c = c0
    for i in range(n):
        if not on_curve(ring[i]):
            return False
        c = challenge(d, mul2(s[i], G, c, ring[i]), mul2(s[i], hp, c, image))
    return c == c0
