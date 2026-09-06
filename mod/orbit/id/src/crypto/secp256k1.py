"""secp256k1 — verification and public-key recovery, in arithmetic.

Ethereum, Bitcoin, Tron and the Cosmos chains all sign with this curve; they
differ only in what they hash and how they print the resulting public key. Two
entry points matter:

  recover()  — Ethereum and Bitcoin transmit a 65-byte signature whose extra
               byte says which of the candidate public keys to reconstruct, so
               the signer never sends a key at all. That is the whole reason an
               `ecrecover` exists: the address falls out of the signature.
  verify()   — Cosmos transmits a 64-byte signature and the key separately.

Signatures with high `s` are rejected (BIP-62 / EIP-2): every (r, s) has a
mirror (r, n-s) that is equally valid, and accepting both would let anyone
produce a second distinct signature for a statement someone else signed.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Optional, Tuple

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
A = 0
B = 7
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)

Point = Optional[Tuple[int, int]]


def _inv(value: int, modulus: int) -> int:
    return pow(value, modulus - 2, modulus)


def add(p: Point, q: Point) -> Point:
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0] and (p[1] + q[1]) % P == 0:
        return None
    if p == q:
        lam = (3 * p[0] * p[0] + A) * _inv(2 * p[1], P) % P
    else:
        lam = (q[1] - p[1]) * _inv(q[0] - p[0], P) % P
    x = (lam * lam - p[0] - q[0]) % P
    return (x, (lam * (p[0] - x) - p[1]) % P)


def mul(point: Point, scalar: int) -> Point:
    scalar %= N
    result: Point = None
    addend = point
    while scalar:
        if scalar & 1:
            result = add(result, addend)
        addend = add(addend, addend)
        scalar >>= 1
    return result


def on_curve(point: Point) -> bool:
    if point is None:
        return False
    x, y = point
    return (y * y - x * x * x - B) % P == 0


def decompress(data: bytes) -> Point:
    """Accept a 33-byte compressed, 65-byte uncompressed, or 64-byte raw key."""
    if len(data) == 65 and data[0] == 4:
        data = data[1:]
    if len(data) == 64:
        point = (int.from_bytes(data[:32], 'big'), int.from_bytes(data[32:], 'big'))
        if not on_curve(point):
            raise ValueError('public key is not on secp256k1')
        return point
    if len(data) == 33 and data[0] in (2, 3):
        x = int.from_bytes(data[1:], 'big')
        y = pow(x * x * x + B, (P + 1) // 4, P)
        if (y * y - x * x * x - B) % P:
            raise ValueError('public key x has no square root on secp256k1')
        if y % 2 != data[0] % 2:
            y = P - y
        return (x, y)
    raise ValueError(f'unrecognised secp256k1 public key ({len(data)} bytes)')


def compress(point: Point) -> bytes:
    x, y = point
    return bytes([2 + (y & 1)]) + x.to_bytes(32, 'big')


def uncompressed(point: Point) -> bytes:
    x, y = point
    return x.to_bytes(32, 'big') + y.to_bytes(32, 'big')


def split(signature: bytes) -> Tuple[int, int]:
    if len(signature) < 64:
        raise ValueError('signature shorter than 64 bytes')
    return (int.from_bytes(signature[:32], 'big'),
            int.from_bytes(signature[32:64], 'big'))


def recover(digest: bytes, r: int, s: int, recovery_id: int,
            allow_high_s: bool = False) -> Point:
    """Reconstruct the public key that produced (r, s) over `digest`."""
    if not 0 < r < N or not 0 < s < N:
        raise ValueError('signature scalar out of range')
    if not allow_high_s and s > N // 2:
        raise ValueError('signature has high s — malleable, refused (EIP-2/BIP-62)')
    if recovery_id not in (0, 1, 2, 3):
        raise ValueError(f'recovery id {recovery_id} out of range')

    x = r + (recovery_id // 2) * N
    if x >= P:
        raise ValueError('recovery id implies an x beyond the field')
    y = pow(x * x * x + B, (P + 1) // 4, P)
    if (y * y - x * x * x - B) % P:
        raise ValueError('no curve point for this r')
    if y % 2 != recovery_id % 2:
        y = P - y
    point = (x, y)

    e = int.from_bytes(digest, 'big') % N
    r_inv = _inv(r, N)
    candidate = mul(add(mul(point, s), mul(G, N - e)), r_inv)
    if candidate is None:
        raise ValueError('recovery produced the point at infinity')
    return candidate


def verify(digest: bytes, signature: bytes, public_key: bytes,
           allow_high_s: bool = True) -> bool:
    """Plain ECDSA verification, for the chains that send the key along."""
    try:
        r, s = split(signature)
        if not 0 < r < N or not 0 < s < N:
            return False
        if not allow_high_s and s > N // 2:
            return False
        point = decompress(public_key)
        e = int.from_bytes(digest, 'big') % N
        s_inv = _inv(s, N)
        candidate = add(mul(G, e * s_inv % N), mul(point, r * s_inv % N))
        return candidate is not None and candidate[0] % N == r
    except (ValueError, ZeroDivisionError):
        return False


# ── signing, for the test suite and for `m id/demo` ──────────────────────

def public_key(secret: int) -> Point:
    if not 0 < secret < N:
        raise ValueError('secret out of range')
    return mul(G, secret)


def _rfc6979_k(secret: int, digest: bytes) -> int:
    """Deterministic nonce — so a signature never leaks the key through a repeat."""
    v = b'\x01' * 32
    k = b'\x00' * 32
    key_bytes = secret.to_bytes(32, 'big')
    k = hmac.new(k, v + b'\x00' + key_bytes + digest, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b'\x01' + key_bytes + digest, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, 'big')
        if 0 < candidate < N:
            return candidate
        k = hmac.new(k, v + b'\x00', hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def sign(digest: bytes, secret: int) -> Tuple[int, int, int]:
    """Return (r, s, recovery_id) with s already normalised low."""
    e = int.from_bytes(digest, 'big') % N
    while True:
        k = _rfc6979_k(secret, digest)
        point = mul(G, k)
        r = point[0] % N
        if r == 0:
            continue
        s = _inv(k, N) * (e + r * secret) % N
        if s == 0:
            continue
        recovery_id = (point[1] & 1) | (2 if point[0] >= N else 0)
        if s > N // 2:
            s = N - s
            recovery_id ^= 1
        return r, s, recovery_id
