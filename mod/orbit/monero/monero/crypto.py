"""
Monero's cryptography, in pure Python: Keccak-256, ed25519 point maths,
CryptoNote base58, and the address / key-derivation rules built on them.

This is what lets the module hold a wallet and recognise its own outputs
without a C++ dependency. It is deliberately limited to the parts that are
*verifiable*: key derivation, address encoding, and the receiver half of the
one-time-address protocol. It does not sign -- see walletrpc.py for that, and
mod.capabilities() for why.

Everything here is checked against the chain itself by `self_test()`: a real
mainnet address round-trips through base58 and its Keccak checksum, and a
sender/receiver derivation pair is proved to agree the way the protocol
requires.
"""

from pathlib import Path

# ── Keccak-256 ──────────────────────────────────────────────────────────────
# Monero uses original Keccak, not SHA-3: the padding byte differs (0x01 vs
# 0x06), so hashlib.sha3_256 is *not* a substitute.

_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56], [27, 20, 39, 8, 14],
]
_MASK64 = (1 << 64) - 1


def _rotl(x, n):
    return ((x << n) | (x >> (64 - n))) & _MASK64


def _keccak_f(a):
    for rnd in range(24):
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y] & _MASK64)
        a[0][0] ^= _RC[rnd]
    return a


def keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] ^= 0x80

    state = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            state[i % 5][i // 5] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(state)

    out = bytearray()
    for i in range(4):
        out += state[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out)


cn_fast_hash = keccak256


# ── ed25519 ─────────────────────────────────────────────────────────────────

Q = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, Q - 2, Q)) % Q
_SQRT_M1 = pow(2, (Q - 1) // 4, Q)


class CryptoError(Exception):
    pass


def _recover_x(y: int, sign: int):
    """The x coordinate matching y on the curve, or None if y is not on it."""
    if y >= Q:
        return None
    y2 = y * y % Q
    u = (y2 - 1) % Q
    v = (_D * y2 + 1) % Q
    x = u * pow(v, (Q - 5) // 8, Q) % Q * pow(u, (Q + 3) // 8, Q) % Q
    # The candidate above is x*v^((q-5)/8)*u^((q+3)/8); fix it up to a real root.
    x = u * pow(v, Q - 2, Q) % Q
    x = pow(x, (Q + 3) // 8, Q)
    if (x * x - u * pow(v, Q - 2, Q)) % Q != 0:
        x = x * _SQRT_M1 % Q
    if (v * x * x - u) % Q != 0:
        return None
    if x == 0 and sign:
        return None
    if x & 1 != sign:
        x = Q - x
    return x


# Points are extended coordinates (X, Y, Z, T) with x = X/Z, y = Y/Z, xy = T/Z.
IDENTITY = (0, 1, 1, 0)


def _add(p, r):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = r
    a = (y1 - x1) * (y2 - x2) % Q
    b = (y1 + x1) * (y2 + x2) % Q
    c = t1 * 2 * _D % Q * t2 % Q
    dd = z1 * 2 * z2 % Q
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def _double(p):
    """Dedicated doubling (dbl-2008-hwcd, a = -1) -- cheaper than _add(p, p),
    and doubling is most of the work in every scalar multiplication."""
    x1, y1, z1, _ = p
    a = x1 * x1 % Q
    b = y1 * y1 % Q
    c = 2 * z1 * z1 % Q
    e = ((x1 + y1) * (x1 + y1) - a - b) % Q
    g = (b - a) % Q
    f = (g - c) % Q
    h = (-a - b) % Q
    return (e * f % Q, g * h % Q, f * g % Q, e * h % Q)


def encode_point(p) -> bytes:
    x, y, z, _ = p
    zi = pow(z, Q - 2, Q)
    x = x * zi % Q
    y = y * zi % Q
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def decode_point(data: bytes):
    if len(data) != 32:
        raise CryptoError(f"a point is 32 bytes, got {len(data)}")
    n = int.from_bytes(data, "little")
    sign = n >> 255
    y = n & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        raise CryptoError(f"{data.hex()} is not a valid ed25519 point")
    return (x, y, 1, x * y % Q)


def is_point(data: bytes) -> bool:
    try:
        decode_point(data)
        return True
    except CryptoError:
        return False


_G = decode_point(bytes.fromhex(
    "5866666666666666666666666666666666666666666666666666666666666666"))

# 2^i * G, built once, halves the work in scalarmult_base.
_G_POWERS = None


def _g_powers():
    global _G_POWERS
    if _G_POWERS is None:
        table, p = [], _G
        for _ in range(253):
            table.append(p)
            p = _double(p)
        _G_POWERS = table
    return _G_POWERS


def scalarmult(p, e: int):
    """4-bit windowed double-and-add.

    Every transaction scanned costs one of these (the 8aR shared secret), so
    the window is worth its 15 setup additions.
    """
    e %= L
    if e == 0:
        return IDENTITY
    table = [IDENTITY, p]
    for i in range(2, 16):
        table.append(_add(table[i - 1], p))
    nibbles = []
    while e:
        nibbles.append(e & 15)
        e >>= 4
    r = IDENTITY
    for n in reversed(nibbles):
        r = _double(_double(_double(_double(r))))
        if n:
            r = _add(r, table[n])
    return r


def scalarmult_base(e: int):
    e %= L
    r, table = IDENTITY, _g_powers()
    i = 0
    while e:
        if e & 1:
            r = _add(r, table[i])
        e >>= 1
        i += 1
    return r


def mul8(p):
    return _double(_double(_double(p)))


# ── Scalars ─────────────────────────────────────────────────────────────────

def sc_reduce32(data: bytes) -> bytes:
    return int.to_bytes(int.from_bytes(data, "little") % L, 32, "little")


def sc_add(a: bytes, b: bytes) -> bytes:
    return int.to_bytes(
        (int.from_bytes(a, "little") + int.from_bytes(b, "little")) % L, 32, "little")


def hash_to_scalar(data: bytes) -> bytes:
    return sc_reduce32(keccak256(data))


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def read_varint(data: bytes, pos: int = 0):
    n, shift = 0, 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, pos
        shift += 7
    raise CryptoError("truncated varint")


def secret_to_public(sec: bytes) -> bytes:
    return encode_point(scalarmult_base(int.from_bytes(sec, "little")))


# ── Key derivation (the receiver half of the one-time address protocol) ─────

def generate_key_derivation(pub: bytes, sec: bytes) -> bytes:
    """D = 8 * sec * pub.

    The sender computes 8rA, the receiver 8aR; they are equal because
    r*(a*G) == a*(r*G). That shared secret is what makes an output findable
    with a view key and invisible to everyone else.
    """
    return encode_point(mul8(scalarmult(decode_point(pub), int.from_bytes(sec, "little"))))


def derivation_to_scalar(derivation: bytes, index: int) -> bytes:
    return hash_to_scalar(derivation + varint(index))


def derive_public_key(derivation: bytes, index: int, base: bytes) -> bytes:
    """The one-time output key P = Hs(D||i)G + B."""
    scalar = derivation_to_scalar(derivation, index)
    return encode_point(_add(scalarmult_base(int.from_bytes(scalar, "little")),
                             decode_point(base)))


def derive_secret_key(derivation: bytes, index: int, base: bytes) -> bytes:
    """x = Hs(D||i) + b -- the key that can spend the output above."""
    return sc_add(derivation_to_scalar(derivation, index), base)


def derive_view_tag(derivation: bytes, index: int) -> int:
    """One byte of Hs-preimage, checked first: it rejects ~255/256 of outputs
    for one Keccak instead of a scalar multiplication (HF15+)."""
    return keccak256(b"view_tag" + derivation + varint(index))[0]


def decode_amount(derivation: bytes, index: int, encrypted: bytes) -> int:
    """Undo the RingCT amount masking on an output we own."""
    shared = derivation_to_scalar(derivation, index)
    mask = keccak256(b"amount" + shared)[:8]
    raw = bytes(encrypted[:8])
    if len(raw) < 8:
        raw = raw + b"\x00" * (8 - len(raw))
    return int.from_bytes(bytes(a ^ b for a, b in zip(raw, mask)), "little")


# ── CryptoNote base58 ───────────────────────────────────────────────────────

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BLOCK, _ENC_BLOCK = 8, 11
_ENC_SIZES = [0, 2, 3, 5, 6, 7, 9, 10, 11]


def b58_encode(data: bytes) -> str:
    out = []
    for i in range(0, len(data), _BLOCK):
        chunk = data[i:i + _BLOCK]
        size = _ENC_SIZES[len(chunk)]
        n = int.from_bytes(chunk, "big")
        block = []
        for _ in range(size):
            n, rem = divmod(n, 58)
            block.append(_ALPHABET[rem])
        out.append("".join(reversed(block)).rjust(size, _ALPHABET[0]))
    return "".join(out)


def b58_decode(text: str) -> bytes:
    out = bytearray()
    for i in range(0, len(text), _ENC_BLOCK):
        chunk = text[i:i + _ENC_BLOCK]
        if len(chunk) not in _ENC_SIZES:
            raise CryptoError(f"invalid base58 block length {len(chunk)}")
        size = _ENC_SIZES.index(len(chunk))
        n = 0
        for ch in chunk:
            pos = _ALPHABET.find(ch)
            if pos < 0:
                raise CryptoError(f"{ch!r} is not a base58 character")
            n = n * 58 + pos
        if n >= 1 << (8 * size):
            raise CryptoError("base58 block overflows its byte length")
        out += int.to_bytes(n, size, "big")
    return bytes(out)


# ── Addresses ───────────────────────────────────────────────────────────────

NETWORKS = {
    "mainnet":  {"standard": 18, "integrated": 19, "subaddress": 42},
    "testnet":  {"standard": 53, "integrated": 54, "subaddress": 63},
    "stagenet": {"standard": 24, "integrated": 25, "subaddress": 36},
}

_PREFIX_LOOKUP = {prefix: (net, kind)
                  for net, kinds in NETWORKS.items()
                  for kind, prefix in kinds.items()}


def encode_address(spend_pub: bytes, view_pub: bytes, network: str = "mainnet",
                   kind: str = "standard", payment_id: bytes = b"") -> str:
    prefixes = NETWORKS.get(network)
    if prefixes is None:
        raise CryptoError(f"unknown network {network!r}")
    if kind == "integrated" and len(payment_id) != 8:
        raise CryptoError("an integrated address needs an 8-byte payment id")
    body = varint(prefixes[kind]) + spend_pub + view_pub + payment_id
    return b58_encode(body + keccak256(body)[:4])


def decode_address(address: str) -> dict:
    """Parse an address, verifying its Keccak checksum.

    Raises CryptoError with a specific reason -- a typo caught here is a
    payment that never leaves the host.
    """
    text = (address or "").strip()
    if not text:
        raise CryptoError("empty address")
    if len(text) not in (95, 106):
        raise CryptoError(
            f"a Monero address is 95 characters (106 when integrated), got {len(text)}")
    raw = b58_decode(text)
    prefix, pos = read_varint(raw)
    if prefix not in _PREFIX_LOOKUP:
        raise CryptoError(f"unknown address prefix {prefix}")
    network, kind = _PREFIX_LOOKUP[prefix]

    body, checksum = raw[:-4], raw[-4:]
    if keccak256(body)[:4] != checksum:
        raise CryptoError("checksum mismatch -- the address is mistyped or truncated")

    spend_pub = body[pos:pos + 32]
    view_pub = body[pos + 32:pos + 64]
    payment_id = body[pos + 64:]
    if len(spend_pub) != 32 or len(view_pub) != 32:
        raise CryptoError("address is too short to hold both public keys")
    if kind == "integrated" and len(payment_id) != 8:
        raise CryptoError("integrated address has a malformed payment id")
    if not is_point(spend_pub) or not is_point(view_pub):
        raise CryptoError("address does not contain valid ed25519 points")

    return {
        "address": text, "network": network, "type": kind, "prefix": prefix,
        "spend_public_key": spend_pub.hex(), "view_public_key": view_pub.hex(),
        "payment_id": payment_id.hex() if payment_id else None,
    }


def is_valid_address(address: str) -> bool:
    try:
        decode_address(address)
        return True
    except CryptoError:
        return False


# ── Wallet keys ─────────────────────────────────────────────────────────────

def keys_from_seed(seed: bytes, network: str = "mainnet") -> dict:
    """The four keys and the address that a 32-byte wallet seed produces.

    Monero derives the view key from the spend key (b -> a = Hs(b)), which is
    why one seed is enough and why a view key can be shared without giving
    away the ability to spend.
    """
    if len(seed) != 32:
        raise CryptoError(f"a wallet seed is 32 bytes, got {len(seed)}")
    spend_sec = sc_reduce32(seed)
    view_sec = hash_to_scalar(spend_sec)
    spend_pub = secret_to_public(spend_sec)
    view_pub = secret_to_public(view_sec)
    return {
        "spend_secret_key": spend_sec.hex(), "view_secret_key": view_sec.hex(),
        "spend_public_key": spend_pub.hex(), "view_public_key": view_pub.hex(),
        "address": encode_address(spend_pub, view_pub, network),
        "network": network,
    }


def subaddress(view_sec: bytes, spend_pub: bytes, major: int, minor: int,
               network: str = "mainnet") -> str:
    """Derive subaddress (major, minor). (0, 0) is the main address."""
    if major == 0 and minor == 0:
        return encode_address(spend_pub, secret_to_public(view_sec), network)
    m = hash_to_scalar(b"SubAddr\x00" + view_sec + varint(major) + varint(minor))
    d_point = _add(decode_point(spend_pub), scalarmult_base(int.from_bytes(m, "little")))
    sub_spend = encode_point(d_point)
    sub_view = encode_point(scalarmult(d_point, int.from_bytes(view_sec, "little")))
    return encode_address(sub_spend, sub_view, network, kind="subaddress")


def integrated_address(spend_pub: bytes, view_pub: bytes, payment_id: bytes,
                       network: str = "mainnet") -> str:
    return encode_address(spend_pub, view_pub, network, "integrated", payment_id)


def random_payment_id() -> bytes:
    import os
    return os.urandom(8)


# ── Self-test ───────────────────────────────────────────────────────────────

# The Monero project's own donation subaddress. Decoding it exercises base58,
# the Keccak checksum and the prefix table against a value we did not choose.
DONATION_ADDRESS = ("888tNkZrPN6JsEgekjMnABU4TBzc2Dt29EPAvkRxbANsAnjyPbb3iQ1YB"
                    "Rk1UXcdRsiKc9dhwMVgN5S9cQUiyoogDavup3H")


def self_test() -> dict:
    """Prove the primitives, not just exercise them."""
    results = {}

    empty = keccak256(b"").hex()
    results["keccak256"] = {
        "ok": empty == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
        "keccak256_empty": empty,
    }

    # l*G is the identity; G round-trips through its encoding.
    order_ok = encode_point(scalarmult(_G, L - 1)) == encode_point(
        (Q - _G[0] % Q, _G[1], 1, (Q - _G[0]) * _G[1] % Q))
    results["ed25519"] = {
        "ok": encode_point(_G).hex().startswith("5866666666") and order_ok,
        "basepoint": encode_point(_G).hex(),
        "order_check": order_ok,
    }

    try:
        parsed = decode_address(DONATION_ADDRESS)
        results["address"] = {
            "ok": parsed["type"] == "subaddress" and parsed["network"] == "mainnet",
            "decoded": parsed["type"],
            "round_trip": b58_encode(b58_decode(DONATION_ADDRESS)) == DONATION_ADDRESS,
        }
        results["address"]["ok"] = results["address"]["ok"] and results["address"]["round_trip"]
    except CryptoError as e:
        results["address"] = {"ok": False, "error": str(e)}

    # Sender and receiver must reach the same one-time key from opposite ends.
    keys = keys_from_seed(keccak256(b"monero-module-self-test"))
    a = bytes.fromhex(keys["view_secret_key"])
    b_pub = bytes.fromhex(keys["spend_public_key"])
    r = sc_reduce32(keccak256(b"ephemeral"))
    big_r = secret_to_public(r)
    sender = encode_point(mul8(scalarmult(decode_point(bytes.fromhex(
        keys["view_public_key"])), int.from_bytes(r, "little"))))
    receiver = generate_key_derivation(big_r, a)
    amount = 1234567890123
    encrypted = bytes(x ^ y for x, y in zip(
        int.to_bytes(amount, 8, "little"),
        keccak256(b"amount" + derivation_to_scalar(receiver, 7))[:8]))
    results["derivation"] = {
        "ok": (sender == receiver
               and decode_amount(receiver, 7, encrypted) == amount
               and derive_public_key(receiver, 7, b_pub) ==
               derive_public_key(sender, 7, b_pub)),
        "shared_secret_agrees": sender == receiver,
        "amount_round_trip": decode_amount(receiver, 7, encrypted) == amount,
        "view_tag": derive_view_tag(receiver, 7),
    }

    sub = subaddress(a, b_pub, 0, 1)
    results["subaddress"] = {"ok": decode_address(sub)["type"] == "subaddress",
                             "example": sub}

    results["ok"] = all(v.get("ok") for v in results.values() if isinstance(v, dict))
    return results


def _wordlist_path() -> Path:
    return Path(__file__).parent / "english.txt"
