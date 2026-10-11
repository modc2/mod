"""ML-DSA (FIPS 204) — Module-Lattice-Based Digital Signature Algorithm.

The chain needs signatures, not a KEM, and this is the signature half of the
NIST post-quantum suite: the scheme formerly called CRYSTALS-Dilithium, written
against the final FIPS 204 (August 2024) text. Algorithm numbers in the
comments refer to that document.

Pure Python, stdlib only — SHAKE128/256 come from hashlib, everything else is
integer arithmetic over Z_q with q = 8380417. It is a reference implementation
for correctness, not a hardened one: nothing here is constant time, the
rejection loops branch on secret data, and the sampling is not masked. It is
here so the chain has a real, vector-checked ML-DSA rather than a stand-in.

    pk, sk = keygen('ML-DSA-44')
    sig = sign(sk, b'message')
    verify(pk, b'message', sig)          # True

Sizes (bytes):

    parameter set   pk     sk     sig    security category
    ML-DSA-44       1312   2560   2420   2  (~AES-128)
    ML-DSA-65       1952   4032   3309   3  (~AES-192)
    ML-DSA-87       2592   4896   4627   5  (~AES-256)

Those numbers are why a post-quantum L1 looks different from an ed25519 one: a
witness is kilobytes, not 64 bytes, so bytes have to be priced.
"""

from __future__ import annotations

import hashlib
import secrets

Q = 8380417
N = 256
D = 13
ZETA = 1753
# 256^-1 mod q, folded into the inverse NTT.
F_INV = 8347681

PARAMS = {
    # tau, lambda(bits), gamma1, gamma2, (k,l), eta, beta, omega
    "ML-DSA-44": dict(tau=39, lam=128, gamma1=1 << 17, gamma2=(Q - 1) // 88,
                      k=4, ell=4, eta=2, omega=80),
    "ML-DSA-65": dict(tau=49, lam=192, gamma1=1 << 19, gamma2=(Q - 1) // 32,
                      k=6, ell=5, eta=4, omega=55),
    "ML-DSA-87": dict(tau=60, lam=256, gamma1=1 << 19, gamma2=(Q - 1) // 32,
                      k=8, ell=7, eta=2, omega=75),
}
DEFAULT = "ML-DSA-44"


def _params(name):
    try:
        p = dict(PARAMS[name])
    except KeyError:
        raise ValueError(f"unknown parameter set {name!r} — "
                         f"pick one of {', '.join(PARAMS)}")
    p["beta"] = p["tau"] * p["eta"]
    return p


def sizes(name: str = DEFAULT) -> dict:
    """Byte lengths for one parameter set."""
    p = _params(name)
    k, ell, eta, omega = p["k"], p["ell"], p["eta"], p["omega"]
    bl_eta = (2 * eta).bit_length()
    return {
        "name": name,
        "pk": 32 + 32 * k * (Q - 1).bit_length() - 32 * k * D,
        "sk": 32 + 32 + 64 + 32 * ((ell + k) * bl_eta + D * k),
        "sig": p["lam"] // 4 + ell * 32 * (1 + (p["gamma1"] - 1).bit_length()) +
               omega + k,
        "seed": 32,
    }


# ---------------------------------------------------------------- hashes


def _h(data: bytes, length: int) -> bytes:
    """H — SHAKE256 as an extendable hash."""
    return hashlib.shake_256(data).digest(length)


def _shake128(data: bytes, length: int) -> bytes:
    return hashlib.shake_128(data).digest(length)


def _i2b(x: int, n: int) -> bytes:
    return x.to_bytes(n, "little")


# ---------------------------------------------------------------- NTT


def _bitrev8(i: int) -> int:
    return int(format(i, "08b")[::-1], 2)


ZETAS = [pow(ZETA, _bitrev8(i), Q) for i in range(256)]


def ntt(w):
    """Algorithm 41. Coefficient form → NTT form, in place on a copy."""
    w = list(w)
    k, length = 0, 128
    while length >= 1:
        start = 0
        while start < N:
            k += 1
            z = ZETAS[k]
            for j in range(start, start + length):
                t = z * w[j + length] % Q
                w[j + length] = (w[j] - t) % Q
                w[j] = (w[j] + t) % Q
            start += 2 * length
        length //= 2
    return w


def intt(w):
    """Algorithm 42. NTT form → coefficient form."""
    w = list(w)
    k, length = 256, 1
    while length <= 128:
        start = 0
        while start < N:
            k -= 1
            z = -ZETAS[k] % Q
            for j in range(start, start + length):
                t = w[j]
                w[j] = (t + w[j + length]) % Q
                w[j + length] = z * (t - w[j + length]) % Q
            start += 2 * length
        length *= 2
    return [F_INV * x % Q for x in w]


def _mul(f, g):
    return [a * b % Q for a, b in zip(f, g)]


def _add(f, g):
    return [(a + b) % Q for a, b in zip(f, g)]


def _sub(f, g):
    return [(a - b) % Q for a, b in zip(f, g)]


def _vec_add(u, v):
    return [_add(a, b) for a, b in zip(u, v)]


def _vec_sub(u, v):
    return [_sub(a, b) for a, b in zip(u, v)]


def _matrix_vec(a_hat, v_hat, k, ell):
    """A_hat ∘ v_hat, one NTT-domain matrix-vector product."""
    out = []
    for i in range(k):
        acc = [0] * N
        for j in range(ell):
            acc = _add(acc, _mul(a_hat[i][j], v_hat[j]))
        out.append(acc)
    return out


# ---------------------------------------------------------------- rounding


def _mod_pm(r: int, alpha: int) -> int:
    """r mod± alpha — the representative in (-alpha/2, alpha/2]."""
    r %= alpha
    if r > alpha // 2:
        r -= alpha
    return r


def _inf_norm(poly) -> int:
    """||w||inf with coefficients taken mod± q."""
    return max(abs(_mod_pm(c, Q)) for c in poly)


def _vec_inf_norm(v) -> int:
    return max((_inf_norm(p) for p in v), default=0)


def power2round(r: int):
    """Algorithm 35. r = r1*2^d + r0 with r0 the centred low part."""
    r %= Q
    r0 = _mod_pm(r, 1 << D)
    return (r - r0) >> D, r0


def decompose(r: int, gamma2: int):
    """Algorithm 36. r = r1*(2*gamma2) + r0, with the q-1 edge case folded."""
    r %= Q
    r0 = _mod_pm(r, 2 * gamma2)
    if r - r0 == Q - 1:
        return 0, r0 - 1
    return (r - r0) // (2 * gamma2), r0


def high_bits(r: int, gamma2: int) -> int:
    return decompose(r, gamma2)[0]


def low_bits(r: int, gamma2: int) -> int:
    return decompose(r, gamma2)[1]


def make_hint(z: int, r: int, gamma2: int) -> int:
    """Algorithm 39. 1 when adding z would carry into the high bits."""
    return int(high_bits(r, gamma2) != high_bits((r + z) % Q, gamma2))


def use_hint(h: int, r: int, gamma2: int) -> int:
    """Algorithm 40. Recover the high bits the signer saw."""
    m = (Q - 1) // (2 * gamma2)
    r1, r0 = decompose(r, gamma2)
    if h == 1:
        return (r1 + 1) % m if r0 > 0 else (r1 - 1) % m
    return r1


# ---------------------------------------------------------------- packing


def _pack(vals, bits: int) -> bytes:
    acc = 0
    mask = (1 << bits) - 1
    for i, v in enumerate(vals):
        acc |= (v & mask) << (i * bits)
    return acc.to_bytes(len(vals) * bits // 8, "little")


def _unpack(b: bytes, bits: int, n: int):
    acc = int.from_bytes(b, "little")
    mask = (1 << bits) - 1
    return [(acc >> (i * bits)) & mask for i in range(n)]


def simple_bit_pack(w, b: int) -> bytes:
    """Algorithm 16. Coefficients in [0, b]."""
    return _pack(w, b.bit_length())


def simple_bit_unpack(v: bytes, b: int):
    """Algorithm 18."""
    return _unpack(v, b.bit_length(), N)


def bit_pack(w, a: int, b: int) -> bytes:
    """Algorithm 17. Coefficients in [-a, b], stored as b - w."""
    bits = (a + b).bit_length()
    return _pack([(b - x) for x in w], bits)


def bit_unpack(v: bytes, a: int, b: int):
    """Algorithm 19."""
    bits = (a + b).bit_length()
    return [b - z for z in _unpack(v, bits, N)]


def hint_bit_pack(h, k: int, omega: int) -> bytes:
    """Algorithm 20. The hint as positions plus per-polynomial end offsets."""
    y = bytearray(omega + k)
    index = 0
    for i in range(k):
        for j in range(N):
            if h[i][j] != 0:
                y[index] = j
                index += 1
        y[omega + i] = index
    return bytes(y)


def hint_bit_unpack(y: bytes, k: int, omega: int):
    """Algorithm 21. None when the encoding is malformed — a verifier must
    reject those rather than guess, since a permissive decoder is a forgery
    surface (the same signature would have two valid encodings)."""
    h = [[0] * N for _ in range(k)]
    index = 0
    for i in range(k):
        end = y[omega + i]
        if end < index or end > omega:
            return None
        first = index
        while index < end:
            if index > first and y[index - 1] >= y[index]:
                return None                     # positions must strictly rise
            h[i][y[index]] = 1
            index += 1
    for j in range(index, omega):
        if y[j] != 0:
            return None                         # padding must be zero
    return h


# ---------------------------------------------------------------- sampling


def sample_in_ball(rho: bytes, tau: int):
    """Algorithm 29. A polynomial with tau coefficients in {-1, 1}."""
    c = [0] * N
    stream = hashlib.shake_256(rho)
    buf = stream.digest(8 + 136)
    pos = 8
    sign_bits = int.from_bytes(buf[:8], "little")
    for i in range(N - tau, N):
        while True:
            if pos >= len(buf):
                buf += hashlib.shake_256(rho).digest(len(buf) + 136)[len(buf):]
            j = buf[pos]
            pos += 1
            if j <= i:
                break
        c[i] = c[j]
        c[j] = 1 - 2 * (sign_bits & 1)
        sign_bits >>= 1
    return c


def _coeff_from_three_bytes(b0, b1, b2):
    z = ((b2 & 0x7F) << 16) | (b1 << 8) | b0
    return z if z < Q else None


def _coeff_from_half_byte(b, eta):
    if eta == 2 and b < 15:
        return 2 - (b % 5)
    if eta == 4 and b < 9:
        return 4 - b
    return None


def rej_ntt_poly(rho: bytes):
    """Algorithm 30. Uniform in Z_q, straight into NTT form."""
    a = []
    ctx = hashlib.shake_128(rho)
    take = 3 * 256
    buf = ctx.digest(take)
    pos = 0
    while len(a) < N:
        if pos + 3 > len(buf):
            take *= 2
            buf = hashlib.shake_128(rho).digest(take)
            continue
        c = _coeff_from_three_bytes(buf[pos], buf[pos + 1], buf[pos + 2])
        pos += 3
        if c is not None:
            a.append(c)
    return a


def rej_bounded_poly(rho: bytes, eta: int):
    """Algorithm 31. Coefficients in [-eta, eta]."""
    a = []
    take = 256
    buf = hashlib.shake_256(rho).digest(take)
    pos = 0
    while len(a) < N:
        if pos >= len(buf):
            take *= 2
            buf = hashlib.shake_256(rho).digest(take)
            continue
        z = buf[pos]
        pos += 1
        z0 = _coeff_from_half_byte(z & 0x0F, eta)
        if z0 is not None:
            a.append(z0 % Q)
        if len(a) < N:
            z1 = _coeff_from_half_byte(z >> 4, eta)
            if z1 is not None:
                a.append(z1 % Q)
    return a


def expand_a(rho: bytes, k: int, ell: int):
    """Algorithm 32. The public matrix, derived not stored."""
    return [[rej_ntt_poly(rho + _i2b(s, 1) + _i2b(r, 1)) for s in range(ell)]
            for r in range(k)]


def expand_s(rho: bytes, k: int, ell: int, eta: int):
    """Algorithm 33."""
    s1 = [rej_bounded_poly(rho + _i2b(r, 2), eta) for r in range(ell)]
    s2 = [rej_bounded_poly(rho + _i2b(r + ell, 2), eta) for r in range(k)]
    return s1, s2


def expand_mask(rho: bytes, mu: int, ell: int, gamma1: int):
    """Algorithm 34. The per-attempt masking vector y."""
    c = 1 + (gamma1 - 1).bit_length()
    out = []
    for r in range(ell):
        v = hashlib.shake_256(rho + _i2b(mu + r, 2)).digest(32 * c)
        out.append([x % Q for x in bit_unpack(v, gamma1 - 1, gamma1)])
    return out


# ---------------------------------------------------------------- encoding


def pk_encode(rho: bytes, t1, p) -> bytes:
    bits = (Q - 1).bit_length() - D                        # 10
    return rho + b"".join(simple_bit_pack(t, (1 << bits) - 1) for t in t1)


def pk_decode(pk: bytes, p):
    bits = (Q - 1).bit_length() - D
    step = 32 * bits
    rho, rest = pk[:32], pk[32:]
    t1 = [simple_bit_unpack(rest[i * step:(i + 1) * step], (1 << bits) - 1)
          for i in range(p["k"])]
    return rho, t1


def sk_encode(rho, key, tr, s1, s2, t0, p) -> bytes:
    eta = p["eta"]
    out = [rho, key, tr]
    for s in s1:
        out.append(bit_pack([_mod_pm(x, Q) for x in s], eta, eta))
    for s in s2:
        out.append(bit_pack([_mod_pm(x, Q) for x in s], eta, eta))
    for t in t0:
        out.append(bit_pack([_mod_pm(x, Q) for x in t],
                            (1 << (D - 1)) - 1, 1 << (D - 1)))
    return b"".join(out)


def sk_decode(sk: bytes, p):
    eta, k, ell = p["eta"], p["k"], p["ell"]
    bl = (2 * eta).bit_length()
    rho, key, tr = sk[:32], sk[32:64], sk[64:128]
    pos = 128
    s1 = []
    for _ in range(ell):
        s1.append([x % Q for x in bit_unpack(sk[pos:pos + 32 * bl], eta, eta)])
        pos += 32 * bl
    s2 = []
    for _ in range(k):
        s2.append([x % Q for x in bit_unpack(sk[pos:pos + 32 * bl], eta, eta)])
        pos += 32 * bl
    t0 = []
    for _ in range(k):
        t0.append([x % Q for x in bit_unpack(sk[pos:pos + 32 * D],
                                             (1 << (D - 1)) - 1, 1 << (D - 1))])
        pos += 32 * D
    return rho, key, tr, s1, s2, t0


def sig_encode(c_tilde: bytes, z, h, p) -> bytes:
    gamma1 = p["gamma1"]
    out = [c_tilde]
    for zi in z:
        out.append(bit_pack([_mod_pm(x, Q) for x in zi], gamma1 - 1, gamma1))
    out.append(hint_bit_pack(h, p["k"], p["omega"]))
    return b"".join(out)


def sig_decode(sig: bytes, p):
    gamma1, k, ell, omega = p["gamma1"], p["k"], p["ell"], p["omega"]
    clen = p["lam"] // 4
    bits = 1 + (gamma1 - 1).bit_length()
    step = 32 * bits
    c_tilde = sig[:clen]
    pos = clen
    z = []
    for _ in range(ell):
        z.append(bit_unpack(sig[pos:pos + step], gamma1 - 1, gamma1))
        pos += step
    h = hint_bit_unpack(sig[pos:pos + omega + k], k, omega)
    return c_tilde, z, h


def w1_encode(w1, gamma2: int) -> bytes:
    b = (Q - 1) // (2 * gamma2) - 1
    return b"".join(simple_bit_pack(w, b) for w in w1)


# ---------------------------------------------------------------- the scheme


def keygen_internal(xi: bytes, name: str = DEFAULT):
    """Algorithm 6. Deterministic from a 32-byte seed."""
    p = _params(name)
    k, ell, eta = p["k"], p["ell"], p["eta"]
    seed = _h(xi + _i2b(k, 1) + _i2b(ell, 1), 128)
    rho, rho_prime, key = seed[:32], seed[32:96], seed[96:128]

    a_hat = expand_a(rho, k, ell)
    s1, s2 = expand_s(rho_prime, k, ell, eta)
    t = _vec_add([intt(v) for v in _matrix_vec(a_hat, [ntt(s) for s in s1],
                                               k, ell)], s2)
    t1, t0 = [], []
    for poly in t:
        rounded = [power2round(c) for c in poly]
        t1.append([r[0] for r in rounded])
        t0.append([r[1] for r in rounded])

    pk = pk_encode(rho, t1, p)
    tr = _h(pk, 64)
    sk = sk_encode(rho, key, tr, s1, s2, t0, p)
    return pk, sk


def keygen(name: str = DEFAULT, seed: bytes | None = None):
    """A fresh keypair. Pass a 32-byte seed for a deterministic one."""
    xi = seed if seed is not None else secrets.token_bytes(32)
    if len(xi) != 32:
        raise ValueError("seed must be 32 bytes")
    return keygen_internal(xi, name)


def sign_internal(sk: bytes, m_prime: bytes, rnd: bytes, name: str = DEFAULT):
    """Algorithm 7. Fiat-Shamir with aborts: loop until z and r0 are small
    enough that the signature leaks nothing about the secret."""
    p = _params(name)
    k, ell = p["k"], p["ell"]
    gamma1, gamma2, beta = p["gamma1"], p["gamma2"], p["beta"]
    rho, key, tr, s1, s2, t0 = sk_decode(sk, p)

    s1_hat = [ntt(s) for s in s1]
    s2_hat = [ntt(s) for s in s2]
    t0_hat = [ntt(t) for t in t0]
    a_hat = expand_a(rho, k, ell)

    mu = _h(tr + m_prime, 64)
    rho_pp = _h(key + rnd + mu, 64)

    kappa = 0
    while True:
        y = expand_mask(rho_pp, kappa, ell, gamma1)
        kappa += ell
        w = [intt(v) for v in _matrix_vec(a_hat, [ntt(yi) for yi in y], k, ell)]
        w1 = [[high_bits(c, gamma2) for c in poly] for poly in w]
        c_tilde = _h(mu + w1_encode(w1, gamma2), p["lam"] // 4)
        c_hat = ntt(sample_in_ball(c_tilde, p["tau"]))

        cs1 = [intt(_mul(c_hat, s)) for s in s1_hat]
        cs2 = [intt(_mul(c_hat, s)) for s in s2_hat]
        z = _vec_add(y, cs1)
        w_minus_cs2 = _vec_sub(w, cs2)
        r0 = [[low_bits(c, gamma2) for c in poly] for poly in w_minus_cs2]

        if _vec_inf_norm(z) >= gamma1 - beta:
            continue
        if max(max(abs(c) for c in poly) for poly in r0) >= gamma2 - beta:
            continue

        ct0 = [intt(_mul(c_hat, t)) for t in t0_hat]
        if _vec_inf_norm(ct0) >= gamma2:
            continue
        target = _vec_add(w_minus_cs2, ct0)
        h = [[make_hint(-_mod_pm(ct0[i][j], Q), target[i][j], gamma2)
              for j in range(N)] for i in range(k)]
        if sum(sum(row) for row in h) > p["omega"]:
            continue
        return sig_encode(c_tilde, z, h, p)


def verify_internal(pk: bytes, m_prime: bytes, sig: bytes,
                    name: str = DEFAULT) -> bool:
    """Algorithm 8."""
    p = _params(name)
    k, ell, gamma2, beta = p["k"], p["ell"], p["gamma2"], p["beta"]
    if len(pk) != sizes(name)["pk"] or len(sig) != sizes(name)["sig"]:
        return False

    rho, t1 = pk_decode(pk, p)
    c_tilde, z, h = sig_decode(sig, p)
    if h is None:
        return False
    if _vec_inf_norm(z) >= p["gamma1"] - beta:
        return False

    a_hat = expand_a(rho, k, ell)
    tr = _h(pk, 64)
    mu = _h(tr + m_prime, 64)
    c_hat = ntt(sample_in_ball(c_tilde, p["tau"]))

    az = _matrix_vec(a_hat, [ntt(zi) for zi in z], k, ell)
    ct1 = [_mul(c_hat, ntt([(c << D) % Q for c in t])) for t in t1]
    w_approx = [intt(_sub(a, b)) for a, b in zip(az, ct1)]
    w1 = [[use_hint(h[i][j], w_approx[i][j], gamma2) for j in range(N)]
          for i in range(k)]
    return c_tilde == _h(mu + w1_encode(w1, gamma2), p["lam"] // 4)


def _m_prime(msg: bytes, ctx: bytes) -> bytes:
    """The domain separator FIPS 204 wraps a message in before signing. The
    0x00 marks a plain (non-prehashed) message; ctx binds the signature to an
    application domain, so a signature minted for one context cannot be
    replayed into another."""
    if len(ctx) > 255:
        raise ValueError("context must be at most 255 bytes")
    return b"\x00" + bytes([len(ctx)]) + ctx + msg


def sign(sk: bytes, msg: bytes, name: str = DEFAULT, ctx: bytes = b"",
         deterministic: bool = False) -> bytes:
    """Algorithm 2. Hedged by default: 32 fresh random bytes go into the seed
    that derives the mask, so two signatures over the same message differ and a
    faulty RNG degrades to the deterministic mode rather than leaking the key."""
    rnd = b"\x00" * 32 if deterministic else secrets.token_bytes(32)
    return sign_internal(sk, _m_prime(msg, ctx), rnd, name)


def verify(pk: bytes, msg: bytes, sig: bytes, name: str = DEFAULT,
           ctx: bytes = b"") -> bool:
    """Algorithm 3."""
    try:
        return verify_internal(pk, _m_prime(msg, ctx), sig, name)
    except Exception:
        return False                      # a malformed signature is a false one
