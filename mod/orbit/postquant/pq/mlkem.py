"""ML-KEM (FIPS 203) - Module-Lattice-Based Key-Encapsulation Mechanism.

Pure Python, stdlib only: SHA3/SHAKE come from hashlib, the rest is integer
arithmetic. Written against the final FIPS 203 (August 2024) text, algorithm
numbers in the comments refer to that document.

This is a reference implementation for correctness, not a hardened one. It is
not constant time; the sampling loops and the modular reductions both branch on
secret data, so do not run it where an attacker can time it. Its job is to give
the module a real, verifiable ML-KEM that matches the NIST vectors.
"""

from __future__ import annotations

import hashlib
import secrets

Q = 3329
N = 256

# Every parameter set differs only in these five numbers.
PARAMS = {
    "ML-KEM-512": dict(k=2, eta1=3, eta2=2, du=10, dv=4),
    "ML-KEM-768": dict(k=3, eta1=2, eta2=2, du=10, dv=4),
    "ML-KEM-1024": dict(k=4, eta1=2, eta2=2, du=11, dv=5),
}
DEFAULT = "ML-KEM-768"


def _bitrev7(i: int) -> int:
    return int(format(i, "07b")[::-1], 2)


# zeta = 17 is a primitive 256th root of unity mod q.
ZETAS = [pow(17, _bitrev7(i), Q) for i in range(128)]
GAMMAS = [pow(17, 2 * _bitrev7(i) + 1, Q) for i in range(128)]

# ---------------------------------------------------------------- hashes


def _H(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()


def _G(b: bytes) -> bytes:
    return hashlib.sha3_512(b).digest()


def _J(b: bytes) -> bytes:
    return hashlib.shake_256(b).digest(32)


def _prf(eta: int, s: bytes, b: int) -> bytes:
    return hashlib.shake_256(s + bytes([b])).digest(64 * eta)


# ---------------------------------------------------------------- NTT


def ntt(f):
    """Algorithm 9. Maps a polynomial into the NTT domain, in place on a copy."""
    f = list(f)
    i = 1
    length = 128
    while length >= 2:
        for start in range(0, N, 2 * length):
            zeta = ZETAS[i]
            i += 1
            for j in range(start, start + length):
                t = (zeta * f[j + length]) % Q
                f[j + length] = (f[j] - t) % Q
                f[j] = (f[j] + t) % Q
        length //= 2
    return f


def intt(f):
    """Algorithm 10. The inverse, including the 128^-1 scaling."""
    f = list(f)
    i = 127
    length = 2
    while length <= 128:
        for start in range(0, N, 2 * length):
            zeta = ZETAS[i]
            i -= 1
            for j in range(start, start + length):
                t = f[j]
                f[j] = (t + f[j + length]) % Q
                f[j + length] = (zeta * (f[j + length] - t)) % Q
        length *= 2
    return [(x * 3303) % Q for x in f]  # 3303 = 128^-1 mod q


def ntt_mul(f, g):
    """Algorithm 11/12. Pointwise product of two NTT-domain polynomials."""
    h = [0] * N
    for i in range(128):
        a0, a1 = f[2 * i], f[2 * i + 1]
        b0, b1 = g[2 * i], g[2 * i + 1]
        gamma = GAMMAS[i]
        h[2 * i] = (a0 * b0 + a1 * b1 * gamma) % Q
        h[2 * i + 1] = (a0 * b1 + a1 * b0) % Q
    return h


def _add(f, g):
    return [(a + b) % Q for a, b in zip(f, g)]


def _sub(f, g):
    return [(a - b) % Q for a, b in zip(f, g)]


# ---------------------------------------------------------------- coding


def compress(x: int, d: int) -> int:
    """round(2^d/q * x) mod 2^d, with the tie rounding up."""
    return (((x << (d + 1)) + Q) // (2 * Q)) & ((1 << d) - 1)


def decompress(y: int, d: int) -> int:
    return ((y * Q) + (1 << (d - 1))) >> d


def byte_encode(f, d: int) -> bytes:
    """Algorithm 5. Pack 256 d-bit integers little-endian into 32d bytes."""
    acc = 0
    for i, x in enumerate(f):
        acc |= (x & ((1 << d) - 1)) << (i * d)
    return acc.to_bytes(32 * d, "little")


def byte_decode(b: bytes, d: int):
    """Algorithm 6. The inverse of byte_encode."""
    acc = int.from_bytes(b, "little")
    mask = (1 << d) - 1
    return [(acc >> (i * d)) & mask for i in range(N)]


# ---------------------------------------------------------------- sampling


def sample_ntt(seed: bytes):
    """Algorithm 7. Rejection-sample a uniform NTT-domain polynomial."""
    xof = hashlib.shake_128(seed)
    out = []
    # 3 bytes yield 2 candidates; ~19% are rejected, so ask for a generous
    # block and extend only if we were unlucky.
    need = 0
    while True:
        need += 768
        buf = xof.digest(need)
        out = []
        i = 0
        while i + 3 <= len(buf) and len(out) < N:
            d1 = buf[i] + 256 * (buf[i + 1] & 0x0F)
            d2 = (buf[i + 1] >> 4) + 16 * buf[i + 2]
            i += 3
            if d1 < Q:
                out.append(d1)
            if d2 < Q and len(out) < N:
                out.append(d2)
        if len(out) == N:
            return out


def sample_poly_cbd(b: bytes, eta: int):
    """Algorithm 8. Centered binomial distribution over 64*eta bytes."""
    bits = int.from_bytes(b, "little")
    out = [0] * N
    for i in range(N):
        base = 2 * i * eta
        x = 0
        y = 0
        for j in range(eta):
            x += (bits >> (base + j)) & 1
            y += (bits >> (base + eta + j)) & 1
        out[i] = (x - y) % Q
    return out


# ---------------------------------------------------------------- K-PKE


def _expand_a(rho: bytes, k: int):
    """A_hat[i][j] = SampleNTT(rho || j || i). Note the index order."""
    return [[sample_ntt(rho + bytes([j, i])) for j in range(k)] for i in range(k)]


def _pke_keygen(d: bytes, p):
    k, eta1 = p["k"], p["eta1"]
    g = _G(d + bytes([k]))
    rho, sigma = g[:32], g[32:]
    a_hat = _expand_a(rho, k)
    s = [sample_poly_cbd(_prf(eta1, sigma, i), eta1) for i in range(k)]
    e = [sample_poly_cbd(_prf(eta1, sigma, k + i), eta1) for i in range(k)]
    s_hat = [ntt(x) for x in s]
    e_hat = [ntt(x) for x in e]
    t_hat = []
    for i in range(k):
        acc = [0] * N
        for j in range(k):
            acc = _add(acc, ntt_mul(a_hat[i][j], s_hat[j]))
        t_hat.append(_add(acc, e_hat[i]))
    ek = b"".join(byte_encode(t, 12) for t in t_hat) + rho
    dk = b"".join(byte_encode(s, 12) for s in s_hat)
    return ek, dk


def _pke_encrypt(ek: bytes, m: bytes, r: bytes, p) -> bytes:
    k, eta1, eta2, du, dv = p["k"], p["eta1"], p["eta2"], p["du"], p["dv"]
    t_hat = [byte_decode(ek[384 * i : 384 * (i + 1)], 12) for i in range(k)]
    rho = ek[384 * k : 384 * k + 32]
    a_hat = _expand_a(rho, k)
    y = [sample_poly_cbd(_prf(eta1, r, i), eta1) for i in range(k)]
    e1 = [sample_poly_cbd(_prf(eta2, r, k + i), eta2) for i in range(k)]
    e2 = sample_poly_cbd(_prf(eta2, r, 2 * k), eta2)
    y_hat = [ntt(x) for x in y]

    # u = INTT(A_hat^T . y_hat) + e1
    u = []
    for i in range(k):
        acc = [0] * N
        for j in range(k):
            acc = _add(acc, ntt_mul(a_hat[j][i], y_hat[j]))
        u.append(_add(intt(acc), e1[i]))

    mu = [decompress(x, 1) for x in byte_decode(m, 1)]
    acc = [0] * N
    for j in range(k):
        acc = _add(acc, ntt_mul(t_hat[j], y_hat[j]))
    v = _add(_add(intt(acc), e2), mu)

    c1 = b"".join(byte_encode([compress(x, du) for x in ui], du) for ui in u)
    c2 = byte_encode([compress(x, dv) for x in v], dv)
    return c1 + c2


def _pke_decrypt(dk: bytes, c: bytes, p) -> bytes:
    k, du, dv = p["k"], p["du"], p["dv"]
    split = 32 * du * k
    u = [
        [decompress(x, du) for x in byte_decode(c[32 * du * i : 32 * du * (i + 1)], du)]
        for i in range(k)
    ]
    v = [decompress(x, dv) for x in byte_decode(c[split : split + 32 * dv], dv)]
    s_hat = [byte_decode(dk[384 * i : 384 * (i + 1)], 12) for i in range(k)]
    acc = [0] * N
    for i in range(k):
        acc = _add(acc, ntt_mul(s_hat[i], ntt(u[i])))
    w = _sub(v, intt(acc))
    return byte_encode([compress(x, 1) for x in w], 1)


# ---------------------------------------------------------------- ML-KEM


def sizes(name: str = DEFAULT) -> dict:
    p = PARAMS[name]
    k, du, dv = p["k"], p["du"], p["dv"]
    return {
        "name": name,
        "encapsulation_key": 384 * k + 32,
        "decapsulation_key": 768 * k + 96,
        "ciphertext": 32 * (du * k + dv),
        "shared_secret": 32,
        "category": {"ML-KEM-512": 1, "ML-KEM-768": 3, "ML-KEM-1024": 5}[name],
    }


def keygen_internal(d: bytes, z: bytes, name: str = DEFAULT):
    """Algorithm 16. Deterministic keygen from the two 32-byte seeds."""
    p = PARAMS[name]
    ek, dk_pke = _pke_keygen(d, p)
    return ek, dk_pke + ek + _H(ek) + z


def keygen(name: str = DEFAULT, seed: bytes | None = None):
    """Algorithm 19. Returns (encapsulation_key, decapsulation_key)."""
    if seed is None:
        seed = secrets.token_bytes(64)
    if len(seed) != 64:
        raise ValueError("seed must be 64 bytes (d || z)")
    return keygen_internal(seed[:32], seed[32:], name)


def _check_ek(ek: bytes, p, name: str):
    k = p["k"]
    if len(ek) != 384 * k + 32:
        raise ValueError(f"{name}: encapsulation key must be {384 * k + 32} bytes, got {len(ek)}")
    # Modulus check: the 12-bit words have to survive a decode/encode round trip,
    # which is exactly the FIPS 203 requirement that every coefficient is < q.
    body = ek[: 384 * k]
    for i in range(k):
        chunk = body[384 * i : 384 * (i + 1)]
        if byte_encode(byte_decode(chunk, 12), 12) != chunk:
            raise ValueError(f"{name}: encapsulation key is not a valid encoding (coefficient >= q)")


def encaps_internal(ek: bytes, m: bytes, name: str = DEFAULT):
    """Algorithm 17. Deterministic encapsulation from a 32-byte message."""
    p = PARAMS[name]
    _check_ek(ek, p, name)
    g = _G(m + _H(ek))
    key, r = g[:32], g[32:]
    return key, _pke_encrypt(ek, m, r, p)


def encaps(ek: bytes, name: str = DEFAULT, m: bytes | None = None):
    """Algorithm 20. Returns (shared_secret, ciphertext)."""
    if m is None:
        m = secrets.token_bytes(32)
    return encaps_internal(ek, m, name)


def decaps(dk: bytes, c: bytes, name: str = DEFAULT) -> bytes:
    """Algorithm 21. Returns the shared secret, or an implicit-reject key."""
    p = PARAMS[name]
    k, du, dv = p["k"], p["du"], p["dv"]
    if len(dk) != 768 * k + 96:
        raise ValueError(f"{name}: decapsulation key must be {768 * k + 96} bytes, got {len(dk)}")
    if len(c) != 32 * (du * k + dv):
        raise ValueError(f"{name}: ciphertext must be {32 * (du * k + dv)} bytes, got {len(c)}")
    dk_pke = dk[: 384 * k]
    ek_pke = dk[384 * k : 768 * k + 32]
    h = dk[768 * k + 32 : 768 * k + 64]
    z = dk[768 * k + 64 :]
    if _H(ek_pke) != h:
        raise ValueError(f"{name}: decapsulation key failed its hash check")
    m2 = _pke_decrypt(dk_pke, c, p)
    g = _G(m2 + h)
    key2, r2 = g[:32], g[32:]
    kbar = _J(z + c)
    c2 = _pke_encrypt(ek_pke, m2, r2, p)
    # Implicit rejection: a ciphertext that does not re-encrypt yields a key
    # derived from z instead of an error, so failure is indistinguishable.
    return key2 if secrets.compare_digest(c, c2) else kbar
