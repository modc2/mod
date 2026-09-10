"""Ed25519 verification — Solana, Sui, Aptos, NEAR, and Polkadot's ed25519 keys.

The reference algorithm on the twisted Edwards curve, with the standard
cofactorless check (`sB == R + hA`), which is what libsodium's
`crypto_sign_verify_detached` accepts and therefore what every wallet in this
list produces. Small-order R values are rejected outright rather than being
allowed through the cofactor, so a signature cannot be malleated into a second
valid form for the same statement.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

Q = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, Q - 2, Q) % Q
I = pow(2, (Q - 1) // 4, Q)

BY = 4 * pow(5, Q - 2, Q) % Q
BX = None  # filled below

Point = Tuple[int, int, int, int]  # extended coordinates


def _recover_x(y: int, sign_bit: int) -> Optional[int]:
    if y >= Q:
        return None
    xx = (y * y - 1) * pow(D * y * y + 1, Q - 2, Q) % Q
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q != 0:
        x = x * I % Q
    if (x * x - xx) % Q != 0:
        return None
    if x & 1 != sign_bit:
        x = Q - x
    return x


BX = _recover_x(BY, 0)
B_POINT: Point = (BX, BY, 1, BX * BY % Q)
ZERO: Point = (0, 1, 1, 0)


def _add(p: Point, q: Point) -> Point:
    a = (p[1] - p[0]) * (q[1] - q[0]) % Q
    b = (p[1] + p[0]) * (q[1] + q[0]) % Q
    c = 2 * p[3] * q[3] * D % Q
    d = 2 * p[2] * q[2] % Q
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _mul(point: Point, scalar: int) -> Point:
    result = ZERO
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(p: Point, q: Point) -> bool:
    if (p[0] * q[2] - q[0] * p[2]) % Q != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % Q == 0


def _decompress(data: bytes) -> Optional[Point]:
    if len(data) != 32:
        return None
    value = int.from_bytes(data, 'little')
    sign_bit = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign_bit)
    if x is None:
        return None
    return (x, y, 1, x * y % Q)


def _small_order(point: Point) -> bool:
    return _equal(_mul(point, 8), ZERO)


def verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    """True when `signature` is a valid Ed25519 signature over `message`."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    if _small_order(a) or _small_order(r):
        return False
    s = int.from_bytes(signature[32:], 'little')
    if s >= L:
        return False
    h = int.from_bytes(
        hashlib.sha512(signature[:32] + public_key + message).digest(), 'little') % L
    return _equal(_mul(B_POINT, s), _add(r, _mul(a, h)))


# ── signing, for the test suite ──────────────────────────────────────────

def _compress(point: Point) -> bytes:
    z_inv = pow(point[2], Q - 2, Q)
    x, y = point[0] * z_inv % Q, point[1] * z_inv % Q
    return int.to_bytes(y | ((x & 1) << 255), 32, 'little')


def _secret_expand(seed: bytes) -> Tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], 'little')
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed: bytes) -> bytes:
    scalar, _ = _secret_expand(seed)
    return _compress(_mul(B_POINT, scalar))


def sign(message: bytes, seed: bytes) -> bytes:
    scalar, prefix = _secret_expand(seed)
    key = _compress(_mul(B_POINT, scalar))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), 'little') % L
    point = _mul(B_POINT, r)
    encoded = _compress(point)
    h = int.from_bytes(hashlib.sha512(encoded + key + message).digest(), 'little') % L
    s = (r + h * scalar) % L
    return encoded + int.to_bytes(s, 32, 'little')
