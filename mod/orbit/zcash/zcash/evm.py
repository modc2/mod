"""
Minimal Keccak-256 and EIP-55 address checksumming.

Bridging sends funds to an address on another chain; a mistyped EVM recipient
is unrecoverable. EIP-55 catches that locally, before a deposit is made, so we
carry a small self-contained Keccak rather than pull in a web3 dependency.
(Keccak-256 is *not* hashlib's sha3_256 -- the padding byte differs.)
"""

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
_MASK = (1 << 64) - 1


def _rotl(x, n):
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f(a):
    for rnd in range(24):
        # theta
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        # rho + pi
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rotl(a[x][y], _ROT[x][y])
        # chi
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y] & _MASK)
        # iota
        a[0][0] ^= _RC[rnd]
    return a


def keccak256(data: bytes) -> bytes:
    rate = 136  # 1088 bits, for 256-bit output
    padded = bytearray(data)
    padded.append(0x01)                       # Keccak padding (SHA-3 would use 0x06)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] ^= 0x80

    state = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
            state[i % 5][i // 5] ^= lane
        _keccak_f(state)

    out = bytearray()
    for i in range(4):   # 4 lanes = 32 bytes
        out += state[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out)


def to_checksum_address(address: str) -> str:
    """EIP-55 mixed-case checksum encoding."""
    body = address.lower().removeprefix("0x")
    if len(body) != 40 or any(c not in "0123456789abcdef" for c in body):
        raise ValueError(f"not a 20-byte hex address: {address!r}")
    digest = keccak256(body.encode()).hex()
    return "0x" + "".join(
        ch.upper() if int(digest[i], 16) >= 8 else ch
        for i, ch in enumerate(body))


def is_valid_evm_address(address: str) -> bool:
    """True for a well-formed address that is either all one case or EIP-55 valid."""
    try:
        body = address.removeprefix("0x")
        if len(body) != 40:
            return False
        if body == body.lower() or body == body.upper():
            int(body, 16)
            return True
        return to_checksum_address(address) == address
    except ValueError:
        return False
