"""
Pallas: the curve Orchard is built on, in pure Python.

Sapling stands on Jubjub and Pedersen hashes; Orchard stands on Pallas and
Sinsemilla. This file is the Orchard twin of `jubjub.py` -- field arithmetic,
the curve group, the point encoding, the hash that turns a personalization
string into a generator, and the Sinsemilla hash and commitment that Orchard
uses where Sapling used Pedersen.

Pallas is  y^2 = x^3 + 5  over F_p, and its group order q is the base field of
its partner curve Vesta. The group is prime order -- there is no cofactor and
no subgroup check, which is why an Orchard key agreement is one plain scalar
multiplication. Points serialize as the 32-byte little-endian x coordinate
with the low bit of y in the top bit; the all-zero encoding is the identity.

Hashing into the group is the interesting part. GroupHash^P is the
"simplified SWU" map of the IRTF hash-to-curve draft: BLAKE2b expands the
message into two field elements, each is mapped onto a curve 3-isogenous to
Pallas, the two are added, and the sum is carried back with the isogeny map.
Sinsemilla then chains 1024 of those generators -- one per 10-bit chunk of the
message -- into a hash that a Halo 2 circuit can verify cheaply.

Correctness here is pinned by the official Zcash test vectors: see
tests/test_orchard.py, which checks the map, the group hash, the generators
and Sinsemilla against zcash/zcash-test-vectors.
"""

import functools
import hashlib

# ── Fields ──────────────────────────────────────────────────────────────────

# p = Pallas base field = Vesta group order.
P = 0x40000000000000000000000000000000224698fc094cf91b992d30ed00000001
# q = Pallas group order = Vesta base field. Prime: cofactor 1.
Q = 0x40000000000000000000000000000000224698fc0994a8dd8c46eb2100000001

# p - 1 = T * 2^S with T odd.
S = 32
T = 0x40000000000000000000000000000000224698fc094cf91b992d30ed
# 5^T mod p: a generator of the 2^S-torsion, and the fixed non-residue the
# square-root algorithm multiplies by when its input is not a square.
ROOT_OF_UNITY = 0x2bce74deac30ebda362120830561f81aea322bf2b7bb7584bdad6fabd87ea32f

B = 5                                   # Pallas: y^2 = x^3 + 5

# The 3-isogenous curve the SWU map actually lands on.
ISO_A = 0x18354a2eb0ea8c9c49be2d7258370742b74134581a27a59f92bb4b0b657a014b
ISO_B = 1265
# Z = -13, the non-square the map is parameterized by.
Z = P - 13

# Constants of the degree-3 isogeny from the SWU curve back to Pallas.
ISO_MAP = (
    0x0e38e38e38e38e38e38e38e38e38e38e4081775473d8375b775f6034aaaaaaab,
    0x3509afd51872d88e267c7ffa51cf412a0f93b82ee4b994958cf863b02814fb76,
    0x17329b9ec525375398c7d7ac3d98fd13380af066cfeb6d690eb64faef37ea4f7,
    0x1c71c71c71c71c71c71c71c71c71c71c8102eea8e7b06eb6eebec06955555580,
    0x1d572e7ddc099cff5a607fcce0494a799c434ac1c96b6980c47f2ab668bcd71f,
    0x325669becaecd5d11d13bf2a7f22b105b4abf9fb9a1fc81c2aa3af1eae5b6604,
    0x1a12f684bda12f684bda12f684bda12f7642b01ad461bad25ad985b5e38e38e4,
    0x1a84d7ea8c396c47133e3ffd28e7a09507c9dc17725cca4ac67c31d8140a7dbb,
    0x3fb98ff0d2ddcadd303216cce1db9ff11765e924f745937802e2be87d225b234,
    0x025ed097b425ed097b425ed097b425ed0ac03e8e134eb3e493e53ab371c71c4f,
    0x0c02c5bcca0e6b7f0790bfb3506defb65941a3a4a97aa1b35a28279b1d1b42ae,
    0x17033d3c60c68173573b3d7f7d681310d976bbfabbc5661d4d90ab820b12320a,
    0x40000000000000000000000000000000224698fc094cf91b992d30ecfffffde5,
)


def _inv(x: int) -> int:
    """1/x in F_p -- and 0 for x = 0, the `inv0` of the hash-to-curve draft."""
    return pow(x % P, P - 2, P)


def sqrt(n: int):
    """A square root in F_p, or None if there is none. Tonelli-Shanks."""
    n %= P
    if n == 0:
        return 0
    if pow(n, (P - 1) // 2, P) != 1:
        return None
    m, c = S, ROOT_OF_UNITY
    x, b = pow(n, (T + 1) // 2, P), pow(n, T, P)
    while b != 1:
        i, sq = 0, b
        while sq != 1:
            sq = sq * sq % P
            i += 1
            if i == m:
                return None
        e = pow(c, 1 << (m - i - 1), P)
        m, c = i, e * e % P
        x = x * e % P
        b = b * c % P
    return x


def to_base(buf: bytes) -> int:
    """A 64-byte PRF output, reduced into the base field."""
    return int.from_bytes(buf, "little") % P


def to_scalar(buf: bytes) -> int:
    """A 64-byte PRF output, reduced into the scalar field."""
    return int.from_bytes(buf, "little") % Q


# ── Points ──────────────────────────────────────────────────────────────────

class Point:
    """An affine Pallas point, or the identity.

    Affine rather than projective because Orchard's Sinsemilla is defined in
    terms of *incomplete* addition -- the formula that is only correct when
    the two points differ -- and the protocol relies on the exceptional cases
    being unreachable. Keeping the coordinates affine keeps that visible.
    Scalar multiplication drops into Jacobian coordinates internally, where
    it costs one field inversion instead of one per bit.
    """

    __slots__ = ("x", "y", "infinity")

    def __init__(self, x: int, y: int, infinity: bool = False):
        self.x, self.y = x % P, y % P
        self.infinity = infinity

    @classmethod
    def identity(cls) -> "Point":
        return cls(0, 0, True)

    def is_identity(self) -> bool:
        return self.infinity

    def on_curve(self) -> bool:
        if self.infinity:
            return True
        return self.y * self.y % P == (pow(self.x, 3, P) + B) % P

    def __eq__(self, other) -> bool:
        if not isinstance(other, Point):
            return NotImplemented
        if self.infinity or other.infinity:
            return self.infinity == other.infinity
        return self.x == other.x and self.y == other.y

    def __hash__(self):
        return hash((self.x, self.y, self.infinity))

    def __neg__(self) -> "Point":
        if self.infinity:
            return self
        return Point(self.x, -self.y)

    def __add__(self, other: "Point") -> "Point":
        if self.infinity:
            return other
        if other.infinity:
            return self
        if self.x == other.x:
            if (self.y + other.y) % P == 0:
                return Point.identity()
            return self.double()
        lam = (self.y - other.y) * _inv(self.x - other.x) % P
        x = (lam * lam - self.x - other.x) % P
        return Point(x, lam * (self.x - x) - self.y)

    def __sub__(self, other: "Point") -> "Point":
        return self + (-other)

    def double(self) -> "Point":
        if self.infinity or self.y == 0:
            return Point.identity()
        lam = 3 * self.x * self.x % P * _inv(2 * self.y) % P
        x = (lam * lam - 2 * self.x) % P
        return Point(x, lam * (self.x - x) - self.y)

    def incomplete_add(self, other: "Point") -> "Point":
        """Sinsemilla's addition: the exceptional cases are a hard error.

        Sinsemilla is only a hash because the chained additions never hit a
        doubling or a cancellation; a message that did would break the
        circuit's soundness. Reaching one here means the caller fed in
        something the protocol says cannot happen, so it raises instead of
        silently returning a different group element than the circuit would.
        """
        if self.infinity or other.infinity:
            raise ValueError("incomplete addition met the identity")
        if self.x == other.x:
            raise ValueError("incomplete addition met equal x coordinates")
        return self + other

    def __mul__(self, scalar: int) -> "Point":
        """[scalar] self, computed in Jacobian coordinates."""
        k = scalar % Q
        if self.infinity or k == 0:
            return Point.identity()
        x1, y1, z1 = self.x, self.y, 1
        rx = ry = rz = 0                    # Jacobian identity: z = 0
        for bit in bin(k)[2:]:
            rx, ry, rz = _jac_double(rx, ry, rz)
            if bit == "1":
                rx, ry, rz = _jac_add(rx, ry, rz, x1, y1, z1)
        if rz == 0:
            return Point.identity()
        zi = _inv(rz)
        zi2 = zi * zi % P
        return Point(rx * zi2 % P, ry * zi2 % P * zi % P)

    __rmul__ = __mul__

    def extract(self) -> int:
        """ExtractP: the x coordinate, and 0 for the identity."""
        return 0 if self.infinity else self.x

    def to_bytes(self) -> bytes:
        if self.infinity:
            return bytes(32)
        buf = bytearray(self.x.to_bytes(32, "little"))
        buf[31] |= (self.y & 1) << 7
        return bytes(buf)

    __bytes__ = to_bytes

    def __repr__(self):
        if self.infinity:
            return "Point(identity)"
        return f"Point({self.x:#x}, {self.y:#x})"


def _jac_double(x, y, z):
    if z == 0 or y == 0:
        return 0, 0, 0
    a = x * x % P
    b = y * y % P
    c = b * b % P
    d = 2 * ((x + b) * (x + b) - a - c) % P
    e = 3 * a % P
    f = e * e % P
    x3 = (f - 2 * d) % P
    y3 = (e * (d - x3) - 8 * c) % P
    z3 = 2 * y * z % P
    return x3, y3, z3


def _jac_add(x1, y1, z1, x2, y2, z2):
    if z1 == 0:
        return x2, y2, z2
    if z2 == 0:
        return x1, y1, z1
    z1z1 = z1 * z1 % P
    z2z2 = z2 * z2 % P
    u1 = x1 * z2z2 % P
    u2 = x2 * z1z1 % P
    s1 = y1 * z2 % P * z2z2 % P
    s2 = y2 * z1 % P * z1z1 % P
    h = (u2 - u1) % P
    r = 2 * (s2 - s1) % P
    if h == 0:
        if r == 0:
            return _jac_double(x1, y1, z1)
        return 0, 0, 0
    i = (2 * h) * (2 * h) % P
    j = h * i % P
    v = u1 * i % P
    x3 = (r * r - j - 2 * v) % P
    y3 = (r * (v - x3) - 2 * s1 * j) % P
    z3 = ((z1 + z2) * (z1 + z2) - z1z1 - z2z2) * h % P
    return x3, y3, z3


def decode_point(buf: bytes):
    """A 32-byte encoding back to a point, or None if it is not one."""
    if len(buf) != 32:
        return None
    if buf == bytes(32):
        return Point.identity()
    sign = buf[31] >> 7
    x = int.from_bytes(buf[:31] + bytes([buf[31] & 0x7F]), "little")
    if x >= P:
        return None
    y = sqrt((pow(x, 3, P) + B) % P)
    if y is None:
        return None
    if y & 1 != sign:
        y = P - y
    return Point(x, y)


GENERATOR = Point(P - 1, 2)


# ── Bit strings ─────────────────────────────────────────────────────────────

def i2lebsp(length: int, value: int) -> list:
    """`length` bits of `value`, least significant first."""
    return [(value >> i) & 1 for i in range(length)]


def leos2bsp(data: bytes) -> list:
    """A byte string as its little-endian bit string."""
    return [(byte >> i) & 1 for byte in data for i in range(8)]


def lebs2ip(bits) -> int:
    return sum(int(b) << i for i, b in enumerate(bits))


# ── GroupHash^P ─────────────────────────────────────────────────────────────

def expand_message_xmd(msg: bytes, dst: bytes, length: int) -> bytes:
    """expand_message_xmd with BLAKE2b, per the hash-to-curve draft.

    BLAKE2b is used unkeyed with an all-zero personalization, so this is the
    plain draft construction rather than one of Zcash's personalized hashes.
    """
    if len(dst) > 255:
        raise ValueError("hash-to-curve domain separation tag is too long")
    b_in_bytes, r_in_bytes = 64, 128
    ell = -(-length // b_in_bytes)
    if ell > 255:
        raise ValueError("expand_message_xmd asked for too many bytes")
    dst_prime = dst + bytes([len(dst)])
    person = bytes(16)

    def h(data: bytes) -> bytes:
        return hashlib.blake2b(data, digest_size=b_in_bytes,
                               person=person).digest()

    b0 = h(bytes(r_in_bytes) + msg + length.to_bytes(2, "big") + b"\x00" + dst_prime)
    blocks = [h(b0 + b"\x01" + dst_prime)]
    for i in range(2, ell + 1):
        prev = bytes(a ^ b for a, b in zip(b0, blocks[-1]))
        blocks.append(h(prev + bytes([i]) + dst_prime))
    return b"".join(blocks)[:length]


def hash_to_field(msg: bytes, dst: bytes) -> tuple:
    """Two base field elements, uniformly distributed, from a message."""
    uniform = expand_message_xmd(msg, dst, 128)
    return (int.from_bytes(uniform[:64], "big") % P,
            int.from_bytes(uniform[64:], "big") % P)


def map_to_curve_simple_swu(u: int):
    """The simplified SWU map onto the curve 3-isogenous to Pallas.

    Returns affine (x, y) on y^2 = x^3 + ISO_A.x + ISO_B. Follows appendix
    F.2 of the hash-to-curve draft, with `inv0` in place of the branch on
    zero so that the same code path runs for every input.
    """
    c1 = (P - ISO_B) * _inv(ISO_A) % P
    c2 = (P - 1) * _inv(Z) % P

    tv1 = Z * u % P * u % P
    tv2 = tv1 * tv1 % P
    x1 = _inv((tv1 + tv2) % P)
    x1 = c2 if x1 == 0 else (x1 + 1) % P
    x1 = x1 * c1 % P
    gx1 = ((x1 * x1 + ISO_A) * x1 + ISO_B) % P
    x2 = tv1 * x1 % P
    gx2 = gx1 * (tv1 * tv2) % P

    y = sqrt(gx1)
    if y is not None:
        x = x1
    else:
        x, y = x2, sqrt(gx2)
        if y is None:                       # one of gx1, gx2 is always square
            raise ValueError("simplified SWU found no square; field is wrong")
    if (u & 1) != (y & 1):
        y = P - y
    return x, y


def iso_map(x: int, y: int) -> Point:
    """Carry a point on the isogenous curve back to Pallas."""
    c = ISO_MAP
    x2 = x * x % P
    x3 = x2 * x % P
    num_x = (c[0] * x3 + c[1] * x2 + c[2] * x + c[3]) % P
    div_x = (x2 + c[4] * x + c[5]) % P
    num_y = (c[6] * x3 + c[7] * x2 + c[8] * x + c[9]) % P * y % P
    div_y = (x3 + c[10] * x2 + c[11] * x + c[12]) % P
    if div_x == 0 or div_y == 0:
        return Point.identity()
    return Point(num_x * _inv(div_x) % P, num_y * _inv(div_y) % P)


@functools.lru_cache(maxsize=4096)
def group_hash(d: bytes, m: bytes) -> Point:
    """GroupHash^P(D, M): a generator nobody knows the discrete log of.

    Cached because Sinsemilla asks for the same 1024 generators over and over
    -- one per possible 10-bit chunk -- and each one costs two square roots
    and a handful of inversions.
    """
    dst = d + b"-pallas_XMD:BLAKE2b_SSWU_RO_"
    u0, u1 = hash_to_field(m, dst)
    q0 = iso_map(*map_to_curve_simple_swu(u0))
    q1 = iso_map(*map_to_curve_simple_swu(u1))
    return q0 + q1


# ── Sinsemilla ──────────────────────────────────────────────────────────────

SINSEMILLA_K = 10                       # bits per chunk
SINSEMILLA_C = 253                      # chunks a message may have


def sinsemilla_hash_to_point(d: bytes, bits) -> Point:
    """SinsemillaHashToPoint(D, M) over a message given as a list of bits."""
    bits = list(bits)
    # A caller that hands over a string of "0"/"1" -- or bytes -- would
    # otherwise get a hash of nonsense out, silently. Sinsemilla takes bits.
    if any(b not in (0, 1) for b in bits):
        raise ValueError("Sinsemilla takes a sequence of bits (0 or 1)")
    n = -(-len(bits) // SINSEMILLA_K)
    if n > SINSEMILLA_C:
        raise ValueError(f"Sinsemilla message of {len(bits)} bits is too long")
    bits += [0] * (n * SINSEMILLA_K - len(bits))
    acc = group_hash(b"z.cash:SinsemillaQ", d)
    for i in range(n):
        m_i = lebs2ip(bits[i * SINSEMILLA_K:(i + 1) * SINSEMILLA_K])
        s = group_hash(b"z.cash:SinsemillaS", m_i.to_bytes(4, "little"))
        acc = acc.incomplete_add(s).incomplete_add(acc)
    return acc


def sinsemilla_hash(d: bytes, bits) -> int:
    return sinsemilla_hash_to_point(d, bits).extract()


def sinsemilla_commit(r: int, d: bytes, bits) -> Point:
    """SinsemillaCommit_r(D, M): the hash, blinded by [r] a fixed generator."""
    return (sinsemilla_hash_to_point(d + b"-M", bits)
            + group_hash(d + b"-r", b"") * r)


def sinsemilla_short_commit(r: int, d: bytes, bits) -> int:
    return sinsemilla_commit(r, d, bits).extract()


# ── Fixed generators ────────────────────────────────────────────────────────

def spending_key_base() -> Point:
    """G: the base point of Orchard's spend authorization signatures."""
    return group_hash(b"z.cash:Orchard", b"G")


def nullifier_k_base() -> Point:
    """K: the base point the nullifier derivation adds the note commitment to."""
    return group_hash(b"z.cash:Orchard", b"K")


def value_commit_bases() -> tuple:
    """(V, R) of ValueCommit^Orchard = [v] V + [rcv] R."""
    return (group_hash(b"z.cash:Orchard-cv", b"v"),
            group_hash(b"z.cash:Orchard-cv", b"r"))


def value_commitment(value: int, rcv: int) -> Point:
    """cv = [v] V + [rcv] R, with v signed (a note's value net of the spend)."""
    v_base, r_base = value_commit_bases()
    v = value % Q
    return v_base * v + r_base * rcv
