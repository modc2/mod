"""Keccak-256 — the pre-standard variant Ethereum uses.

`hashlib.sha3_256` is NOT this. NIST changed the padding byte between Keccak's
submission and the final SHA-3 (0x01 became 0x06), and Ethereum kept the
original, so every address and every EIP-191 digest in this module is Keccak.
This is Keccak-f[1600] with rate 1088 and the 0x01 pad, written out so that
nothing in the verification path is delegated to a package that may not be
installed. The test suite pins it against `eth_hash` when that is present.
"""
from __future__ import annotations

_MASK = (1 << 64) - 1

_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# rotation offsets, indexed [x][y]
_R = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)


def _rol(value: int, count: int) -> int:
    if count == 0:
        return value
    return ((value << count) | (value >> (64 - count))) & _MASK


def _permute(state: list) -> None:
    for rnd in range(24):
        # theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
             for x in range(5)]
        d = [c[(x + 4) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(0, 25, 5):
                state[x + y] ^= d[x]
        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(state[x + 5 * y], _R[x][y])
        # chi
        for y in range(0, 25, 5):
            for x in range(5):
                state[x + y] = b[x + y] ^ (~b[(x + 1) % 5 + y] & _MASK) & b[(x + 2) % 5 + y]
        # iota
        state[0] ^= _RC[rnd]


def keccak(data: bytes, digest_size: int = 32, pad: int = 0x01) -> bytes:
    """Keccak sponge. `pad=0x01` is Keccak proper; 0x06 would be SHA-3."""
    rate = 200 - 2 * digest_size
    state = [0] * 25

    padded = bytearray(data)
    padded.append(pad)
    while len(padded) % rate:
        padded.append(0x00)
    padded[-1] ^= 0x80

    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:i * 8 + 8], 'little')
        _permute(state)

    out = bytearray()
    while len(out) < digest_size:
        for i in range(rate // 8):
            out += state[i].to_bytes(8, 'little')
            if len(out) >= digest_size:
                break
        if len(out) < digest_size:
            _permute(state)
    return bytes(out[:digest_size])


def keccak256(data: bytes) -> bytes:
    return keccak(data, 32)
