"""
Jubjub: the twisted Edwards curve Sapling is built on, in pure Python.

Everything the shielded pool needs below the protocol layer lives here --
field arithmetic, the curve group, the point encoding, the group hash that
turns a personalization string into a generator, and the Pedersen hashes used
by note commitments and nullifiers.

Jubjub is  -u^2 + v^2 = 1 + d.u^2.v^2  over F_q, where q is the scalar field
order of BLS12-381. Its group has cofactor 8 over a prime-order subgroup of
order r. Points serialize as the 32-byte little-endian v coordinate with the
low bit of u in the top bit (Zcash protocol spec section 5.4.9.3).

Correctness here is pinned by the official Zcash test vectors -- see
tests/test_shielded.py, which checks the generators, the key components, the
note commitment and the nullifier against zcash/zcash-test-vectors.
"""

import hashlib

# ── Field ───────────────────────────────────────────────────────────────────

# q = BLS12-381 scalar field order = Jubjub base field.
Q = 0x73eda753299d7d483339d80809a1d80553bda402fffe5bfeffffffff00000001
# r = order of the prime-order subgroup of Jubjub.
R = 0x0e7db4ea6533afa906673b0101343b00a6682093ccc81082d0970e5ed6f72cb7
COFACTOR = 8

# a = -1, d = -10240/10241
A = Q - 1
D = (Q - 10240) * pow(10241, Q - 2, Q) % Q

# Uniform random string, fixed by the protocol, prefixed to every group hash.
URS = b"096b36a5804bfacef1691e173c366a47ff5ba84a44f26ddd7e8d9f79d5b42df0"


def _sqrt(n: int):
    """Square root in F_q, or None. Tonelli-Shanks; q has 2-adicity 32."""
    n %= Q
    if n == 0:
        return 0
    if pow(n, (Q - 1) // 2, Q) != 1:
        return None
    # q - 1 = t * 2^s with t odd
    s, t = 0, Q - 1
    while t % 2 == 0:
        t //= 2
        s += 1
    # A quadratic non-residue; 5 is one for this q.
    z = pow(5, t, Q)
    m, c, x, b = s, z, pow(n, (t + 1) // 2, Q), pow(n, t, Q)
    while b != 1:
        i, sq = 0, b
        while sq != 1:
            sq = sq * sq % Q
            i += 1
            if i == m:
                return None
        e = pow(c, 1 << (m - i - 1), Q)
        m, c = i, e * e % Q
        x = x * e % Q
        b = b * c % Q
    return x


# ── Points ──────────────────────────────────────────────────────────────────

class Point:
    """A Jubjub point in extended twisted Edwards coordinates (X:Y:T:Z)."""

    __slots__ = ("X", "Y", "T", "Z")

    def __init__(self, X, Y, T, Z):
        self.X, self.Y, self.T, self.Z = X % Q, Y % Q, T % Q, Z % Q

    @classmethod
    def from_affine(cls, u, v):
        return cls(u, v, u * v % Q, 1)

    @classmethod
    def identity(cls):
        return cls(0, 1, 0, 1)

    def affine(self):
        zi = pow(self.Z, Q - 2, Q)
        return self.X * zi % Q, self.Y * zi % Q

    @property
    def u(self):
        return self.affine()[0]

    @property
    def v(self):
        return self.affine()[1]

    def is_identity(self) -> bool:
        return self.X % Q == 0 and self.Y == self.Z

    def __eq__(self, other):
        if not isinstance(other, Point):
            return NotImplemented
        return (self.X * other.Z - other.X * self.Z) % Q == 0 and \
               (self.Y * other.Z - other.Y * self.Z) % Q == 0

    def __add__(self, o: "Point") -> "Point":
        # add-2008-hwcd-3, for a = -1
        a = (self.Y - self.X) * (o.Y - o.X) % Q
        b = (self.Y + self.X) * (o.Y + o.X) % Q
        c = self.T * 2 * D % Q * o.T % Q
        dd = self.Z * 2 * o.Z % Q
        e, f, g, h = (b - a) % Q, (dd - c) % Q, (dd + c) % Q, (b + a) % Q
        return Point(e * f, g * h, e * h, f * g)

    def double(self) -> "Point":
        # dbl-2008-hwcd, for a = -1
        a = self.X * self.X % Q
        b = self.Y * self.Y % Q
        c = 2 * self.Z * self.Z % Q
        e = ((self.X + self.Y) ** 2 - a - b) % Q
        g = (b - a) % Q          # = -a*A + B with a = -1
        f = (g - c) % Q
        h = (-a - b) % Q         # = -a*A - B
        return Point(e * f, g * h, e * h, f * g)

    def __neg__(self) -> "Point":
        return Point(-self.X, self.Y, -self.T, self.Z)

    def __mul__(self, k: int) -> "Point":
        k %= (R * COFACTOR)
        acc, base = Point.identity(), self
        while k:
            if k & 1:
                acc = acc + base
            base = base.double()
            k >>= 1
        return acc

    __rmul__ = __mul__

    # ── Encoding ────────────────────────────────────────────────────────────

    def bytes(self) -> bytes:
        u, v = self.affine()
        out = bytearray(v.to_bytes(32, "little"))
        out[31] |= (u & 1) << 7
        return bytes(out)

    def __repr__(self):
        return f"Point({self.bytes().hex()})"


def decode_point(b: bytes):
    """32-byte encoding -> Point, or None if it is not on the curve."""
    if len(b) != 32:
        return None
    sign = b[31] >> 7
    v = int.from_bytes(b[:31] + bytes([b[31] & 0x7F]), "little")
    if v >= Q:
        return None
    v2 = v * v % Q
    denom = (1 + D * v2) % Q
    if denom == 0:
        return None
    u2 = (v2 - 1) * pow(denom, Q - 2, Q) % Q
    u = _sqrt(u2)
    if u is None:
        return None
    if u & 1 != sign:
        u = (Q - u) % Q
    if u == 0 and sign == 1:
        return None            # non-canonical encoding of (0, v)
    return Point.from_affine(u, v)


# ── Group hash ──────────────────────────────────────────────────────────────

def group_hash(personalization: bytes, m: bytes):
    """GroupHash^J_URS: personalization + message -> subgroup point, or None."""
    h = hashlib.blake2s(URS + m, digest_size=32, person=personalization).digest()
    p = decode_point(h)
    if p is None:
        return None
    p = p * COFACTOR
    return None if p.is_identity() else p


def find_group_hash(personalization: bytes, m: bytes) -> Point:
    """Group hash with a one-byte counter, as used for fixed generators."""
    for i in range(256):
        p = group_hash(personalization, m + bytes([i]))
        if p is not None:
            return p
    raise ValueError("no group hash found (should be unreachable)")


def _lazy(fn):
    cache = {}

    def get():
        if "v" not in cache:
            cache["v"] = fn()
        return cache["v"]
    return get


# The fixed bases of the Sapling circuit (protocol spec section 5.4.9.6).
SPENDING_KEY_BASE = _lazy(lambda: find_group_hash(b"Zcash_G_", b""))
PROVING_KEY_BASE = _lazy(lambda: find_group_hash(b"Zcash_H_", b""))
NULLIFIER_POSITION_BASE = _lazy(lambda: find_group_hash(b"Zcash_J_", b""))
NOTE_COMMIT_RANDOMNESS_BASE = _lazy(lambda: find_group_hash(b"Zcash_PH", b"r"))
VALUE_COMMIT_VALUE_BASE = _lazy(lambda: find_group_hash(b"Zcash_cv", b"v"))
VALUE_COMMIT_RANDOMNESS_BASE = _lazy(lambda: find_group_hash(b"Zcash_cv", b"r"))


def diversify_hash(d: bytes):
    """11-byte diversifier -> its group element g_d, or None if unusable."""
    if len(d) != 11:
        raise ValueError("a diversifier is 11 bytes")
    return group_hash(b"Zcash_gd", d)


# ── Pedersen hash ───────────────────────────────────────────────────────────

_PEDERSEN_CHUNKS_PER_SEGMENT = 63     # c, from the spec


def _pedersen_generator(i: int) -> Point:
    return find_group_hash(b"Zcash_PH", i.to_bytes(4, "little"))


_pedersen_cache = {}


def pedersen_generator(i: int) -> Point:
    if i not in _pedersen_cache:
        _pedersen_cache[i] = _pedersen_generator(i)
    return _pedersen_cache[i]


def bits_of(data: bytes) -> list:
    """Bytes -> bit list, little-endian within each byte (LEBS2OSP inverse)."""
    return [(byte >> i) & 1 for byte in data for i in range(8)]


def int_bits(value: int, length: int) -> list:
    """I2LEBSP: integer -> `length` bits, little-endian."""
    return [(value >> i) & 1 for i in range(length)]


def pedersen_hash_to_point(bits: list) -> Point:
    """PedersenHashToPoint with D = "Zcash_PH" (the only personalization used)."""
    acc = Point.identity()
    # 3 bits per chunk, `c` chunks per segment.
    per_segment = 3 * _PEDERSEN_CHUNKS_PER_SEGMENT
    for seg_index in range(0, (len(bits) + per_segment - 1) // per_segment):
        segment = bits[seg_index * per_segment:(seg_index + 1) * per_segment]
        m, shift = 0, 1
        for j in range(0, len(segment), 3):
            chunk = segment[j:j + 3] + [0] * (3 - len(segment[j:j + 3]))
            s0, s1, s2 = chunk
            enc = (1 - 2 * s2) * (1 + s0 + 2 * s1)
            m += enc * shift
            shift <<= 4
        acc = acc + pedersen_generator(seg_index) * (m % R)
    return acc


def mixing_pedersen_hash(point: Point, value: int) -> Point:
    """MixingPedersenHash: mixes a note position into its commitment."""
    return point + NULLIFIER_POSITION_BASE() * (value % R)
