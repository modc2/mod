"""SLH-DSA (FIPS 205) — Stateless Hash-Based Digital Signature Algorithm.

SPHINCS+ as standardised: a hypertree of one-time WOTS+ signatures over a
few-time FORS forest, every node of it a SHAKE256 call. Nothing here rests on
a lattice, let alone a curve — forging a signature means inverting SHAKE256,
which is the most conservative assumption post-quantum cryptography knows how
to make. That is the point of offering it next to ML-DSA: two key types whose
failures would have to be independent.

    pk, sk = keygen('SLH-DSA-SHAKE-128f')
    sig = sign(sk, b'msg', 'SLH-DSA-SHAKE-128f')
    verify(pk, b'msg', sig, 'SLH-DSA-SHAKE-128f')    # True

The price is size and hashing, not structure:

                        pk    sk     sig   cat
    SLH-DSA-SHAKE-128s   32    64    7856   1  (~AES-128, slow to sign)
    SLH-DSA-SHAKE-128f   32    64   17088   1  (~AES-128)
    SLH-DSA-SHAKE-192s   48    96   16224   3  (~AES-192, slow to sign)
    SLH-DSA-SHAKE-192f   48    96   35664   3  (~AES-192)
    SLH-DSA-SHAKE-256s   64   128   29792   5  (~AES-256, slow to sign)
    SLH-DSA-SHAKE-256f   64   128   49856   5  (~AES-256)

The "s" sets trade signing work for signature bytes: in pure python an
"s" signature takes tens of seconds to make (verification stays quick).
The "f" sets sign in about a second and are the ones the keystore offers.

Pure python, stdlib only, SHAKE from hashlib. Deterministic keygen expands a
32-byte seed into the three FIPS 205 seeds so wallets store one seed whatever
their scheme.
"""

from __future__ import annotations

import hashlib
import secrets

PARAMS = {
    # n, h (total tree height), d (layers), hp = h/d, a (FORS height),
    # k (FORS trees). lg_w = 4 for every set.
    "SLH-DSA-SHAKE-128s": dict(n=16, h=63, d=7, hp=9, a=12, k=14),
    "SLH-DSA-SHAKE-128f": dict(n=16, h=66, d=22, hp=3, a=6, k=33),
    "SLH-DSA-SHAKE-192s": dict(n=24, h=63, d=7, hp=9, a=14, k=17),
    "SLH-DSA-SHAKE-192f": dict(n=24, h=66, d=22, hp=3, a=8, k=33),
    "SLH-DSA-SHAKE-256s": dict(n=32, h=64, d=8, hp=8, a=14, k=22),
    "SLH-DSA-SHAKE-256f": dict(n=32, h=68, d=17, hp=4, a=9, k=35),
}
DEFAULT = "SLH-DSA-SHAKE-128f"


def _params(name):
    try:
        return PARAMS[name]
    except KeyError:
        raise ValueError(f"unknown parameter set {name!r} — "
                         f"pick one of {', '.join(PARAMS)}")


def sizes(name: str = DEFAULT) -> dict:
    """Byte lengths for one parameter set."""
    p = _params(name)
    n, h, d, hp, a, k = p["n"], p["h"], p["d"], p["hp"], p["a"], p["k"]
    wots_len = 2 * n + 3
    return {
        "name": name,
        "pk": 2 * n,
        "sk": 4 * n,
        "sig": n * (1 + k * (a + 1) + h + d * wots_len),
        "seed": 32,
    }


def _digest_len(p) -> int:
    """m — bytes of H_msg output the scheme consumes."""
    return (-(-(p["k"] * p["a"]) // 8) + -(-(p["h"] - p["hp"]) // 8)
            + -(-p["hp"] // 8))


# ---------------------------------------------------------------- hashes

# All six FIPS 205 functions (F, H, T, PRF, PRF_msg, H_msg) are SHAKE256 in
# the SHAKE instantiation; they differ only in what is fed in and how much
# comes out.


def _shake(data: bytes, length: int) -> bytes:
    return hashlib.shake_256(data).digest(length)


# ---------------------------------------------------------------- ADRS

# A 32-byte big-endian address ties every hash call to its exact position in
# the hypertree: layer (4) | tree (12) | type (4) | 12 type-specific bytes.
# Domain separation is the whole security argument of a hash-based scheme, so
# there are no shortcuts taken here.

A_WOTS_HASH, A_WOTS_PK, A_TREE = 0, 1, 2
A_FORS_TREE, A_FORS_ROOTS, A_WOTS_PRF, A_FORS_PRF = 3, 4, 5, 6


def _adrs(layer: int, tree: int, typ: int) -> bytearray:
    a = bytearray(32)
    a[0:4] = layer.to_bytes(4, "big")
    a[4:16] = tree.to_bytes(12, "big")
    a[16:20] = typ.to_bytes(4, "big")
    return a


def _retype(adrs, typ: int, keep_kp=True) -> bytearray:
    """setTypeAndClear, then copy the key pair address back when asked —
    the pattern Algorithms 6-19 all share."""
    a = bytearray(adrs)
    kp = a[20:24]
    a[16:20] = typ.to_bytes(4, "big")
    a[20:32] = bytes(12)
    if keep_kp:
        a[20:24] = kp
    return a


# ---------------------------------------------------------------- WOTS+

# w = 16 for every parameter set: each chain signs one nibble, plus a
# 3-nibble checksum so lowering a message nibble always raises a checksum
# nibble and chains only ever run forward.


def _wots_msg(m: bytes) -> list:
    nibbles = []
    for b in m:
        nibbles.append(b >> 4)
        nibbles.append(b & 15)
    csum = sum(15 - x for x in nibbles)
    cb = (csum << 4).to_bytes(2, "big")
    return nibbles + [cb[0] >> 4, cb[0] & 15, cb[1] >> 4]


def _chain(x: bytes, start: int, steps: int, pk_seed: bytes, adrs, n: int):
    for j in range(start, start + steps):
        adrs[28:32] = j.to_bytes(4, "big")
        x = _shake(pk_seed + bytes(adrs) + x, n)
    return x


def _wots_sk(sk_seed, pk_seed, sk_adrs, i: int, n: int) -> bytes:
    sk_adrs[24:28] = i.to_bytes(4, "big")
    return _shake(pk_seed + bytes(sk_adrs) + sk_seed, n)


def _wots_pk_gen(sk_seed, pk_seed, adrs, n: int) -> bytes:
    """adrs arrives as WOTS_HASH with the key pair address set."""
    sk_adrs = _retype(adrs, A_WOTS_PRF)
    tmp = []
    for i in range(2 * n + 3):
        sk = _wots_sk(sk_seed, pk_seed, sk_adrs, i, n)
        adrs[24:28] = i.to_bytes(4, "big")
        tmp.append(_chain(sk, 0, 15, pk_seed, adrs, n))
    pk_adrs = _retype(adrs, A_WOTS_PK)
    return _shake(pk_seed + bytes(pk_adrs) + b"".join(tmp), n)


def _wots_sign(m, sk_seed, pk_seed, adrs, n: int) -> bytes:
    sk_adrs = _retype(adrs, A_WOTS_PRF)
    sig = []
    for i, mi in enumerate(_wots_msg(m)):
        sk = _wots_sk(sk_seed, pk_seed, sk_adrs, i, n)
        adrs[24:28] = i.to_bytes(4, "big")
        sig.append(_chain(sk, 0, mi, pk_seed, adrs, n))
    return b"".join(sig)


def _wots_pk_from_sig(sig, m, pk_seed, adrs, n: int) -> bytes:
    tmp = []
    for i, mi in enumerate(_wots_msg(m)):
        adrs[24:28] = i.to_bytes(4, "big")
        tmp.append(_chain(sig[i * n:(i + 1) * n], mi, 15 - mi,
                          pk_seed, adrs, n))
    pk_adrs = _retype(adrs, A_WOTS_PK)
    return _shake(pk_seed + bytes(pk_adrs) + b"".join(tmp), n)


# ---------------------------------------------------------------- XMSS

# One Merkle tree of WOTS+ public keys. The whole tree is computed and kept
# by level, so signing reads its auth path instead of recomputing subtrees —
# same hash count as the spec's recursion, an order less bookkeeping.


def _xmss_levels(sk_seed, pk_seed, layer, tree, hp: int, n: int):
    leaves = []
    for i in range(1 << hp):
        adrs = _adrs(layer, tree, A_WOTS_HASH)
        adrs[20:24] = i.to_bytes(4, "big")
        leaves.append(_wots_pk_gen(sk_seed, pk_seed, adrs, n))
    levels = [leaves]
    adrs = _adrs(layer, tree, A_TREE)
    for z in range(1, hp + 1):
        prev, cur = levels[-1], []
        adrs[24:28] = z.to_bytes(4, "big")
        for i in range(len(prev) // 2):
            adrs[28:32] = i.to_bytes(4, "big")
            cur.append(_shake(pk_seed + bytes(adrs) + prev[2 * i]
                              + prev[2 * i + 1], n))
        levels.append(cur)
    return levels


def _xmss_sign(m, sk_seed, pk_seed, layer, tree, idx, hp, n):
    levels = _xmss_levels(sk_seed, pk_seed, layer, tree, hp, n)
    adrs = _adrs(layer, tree, A_WOTS_HASH)
    adrs[20:24] = idx.to_bytes(4, "big")
    sig = _wots_sign(m, sk_seed, pk_seed, adrs, n)
    auth = b"".join(levels[z][(idx >> z) ^ 1] for z in range(hp))
    return sig + auth, levels[hp][0]


def _xmss_pk_from_sig(idx, sig_xmss, m, pk_seed, layer, tree, hp, n):
    wots_bytes = (2 * n + 3) * n
    adrs = _adrs(layer, tree, A_WOTS_HASH)
    adrs[20:24] = idx.to_bytes(4, "big")
    node = _wots_pk_from_sig(sig_xmss[:wots_bytes], m, pk_seed, adrs, n)
    adrs = _adrs(layer, tree, A_TREE)
    t = idx
    for z in range(hp):
        sib = sig_xmss[wots_bytes + z * n:wots_bytes + (z + 1) * n]
        pair = node + sib if t % 2 == 0 else sib + node
        t >>= 1
        adrs[24:28] = (z + 1).to_bytes(4, "big")
        adrs[28:32] = t.to_bytes(4, "big")
        node = _shake(pk_seed + bytes(adrs) + pair, n)
    return node


# ---------------------------------------------------------------- hypertree


def _ht_sign(m, sk_seed, pk_seed, idx_tree, idx_leaf, p):
    d, hp, n = p["d"], p["hp"], p["n"]
    sig, root = [], m
    for j in range(d):
        s, root = _xmss_sign(root, sk_seed, pk_seed, j, idx_tree,
                             idx_leaf, hp, n)
        sig.append(s)
        idx_leaf = idx_tree & ((1 << hp) - 1)
        idx_tree >>= hp
    return b"".join(sig)


def _ht_verify(m, sig_ht, pk_seed, idx_tree, idx_leaf, pk_root, p) -> bool:
    d, hp, n = p["d"], p["hp"], p["n"]
    xmss_bytes = ((2 * n + 3) + hp) * n
    node = m
    for j in range(d):
        s = sig_ht[j * xmss_bytes:(j + 1) * xmss_bytes]
        node = _xmss_pk_from_sig(idx_leaf, s, node, pk_seed, j, idx_tree,
                                 hp, n)
        idx_leaf = idx_tree & ((1 << hp) - 1)
        idx_tree >>= hp
    return node == pk_root


# ---------------------------------------------------------------- FORS


def _base_2b(data: bytes, b: int, out_len: int) -> list:
    acc = bits = 0
    it = iter(data)
    out = []
    for _ in range(out_len):
        while bits < b:
            acc = (acc << 8) | next(it)
            bits += 8
        bits -= b
        out.append((acc >> bits) & ((1 << b) - 1))
    return out


def _fors_tree(sk_seed, pk_seed, adrs, tree_i, a_h, n):
    """One of the k FORS trees, whole: its secret leaves and every level.
    Node indices in the ADRS are global across the forest, per Algorithm 14."""
    base = tree_i << a_h
    sk_adrs = _retype(adrs, A_FORS_PRF)
    node_adrs = bytearray(adrs)
    sks, leaves = [], []
    for j in range(1 << a_h):
        idx = base + j
        sk_adrs[24:28] = bytes(4)
        sk_adrs[28:32] = idx.to_bytes(4, "big")
        sk = _shake(pk_seed + bytes(sk_adrs) + sk_seed, n)
        sks.append(sk)
        node_adrs[24:28] = bytes(4)
        node_adrs[28:32] = idx.to_bytes(4, "big")
        leaves.append(_shake(pk_seed + bytes(node_adrs) + sk, n))
    levels = [leaves]
    for z in range(1, a_h + 1):
        prev, cur = levels[-1], []
        node_adrs[24:28] = z.to_bytes(4, "big")
        for i in range(len(prev) // 2):
            node_adrs[28:32] = ((base >> z) + i).to_bytes(4, "big")
            cur.append(_shake(pk_seed + bytes(node_adrs) + prev[2 * i]
                              + prev[2 * i + 1], n))
        levels.append(cur)
    return sks, levels


def _fors_sign(md, sk_seed, pk_seed, adrs, p):
    a_h, k, n = p["a"], p["k"], p["n"]
    indices = _base_2b(md, a_h, k)
    sig, roots = [], []
    for i in range(k):
        idx = indices[i]
        sks, levels = _fors_tree(sk_seed, pk_seed, adrs, i, a_h, n)
        sig.append(sks[idx])
        sig.extend(levels[z][(idx >> z) ^ 1] for z in range(a_h))
        roots.append(levels[a_h][0])
    pk_adrs = _retype(adrs, A_FORS_ROOTS)
    pk_fors = _shake(pk_seed + bytes(pk_adrs) + b"".join(roots), n)
    return b"".join(sig), pk_fors


def _fors_pk_from_sig(sig_fors, md, pk_seed, adrs, p):
    a_h, k, n = p["a"], p["k"], p["n"]
    indices = _base_2b(md, a_h, k)
    chunk = (a_h + 1) * n
    node_adrs = bytearray(adrs)
    roots = []
    for i in range(k):
        s = sig_fors[i * chunk:(i + 1) * chunk]
        idx_g = (i << a_h) + indices[i]
        node_adrs[24:28] = bytes(4)
        node_adrs[28:32] = idx_g.to_bytes(4, "big")
        node = _shake(pk_seed + bytes(node_adrs) + s[:n], n)
        for z in range(a_h):
            sib = s[(z + 1) * n:(z + 2) * n]
            pair = node + sib if idx_g % 2 == 0 else sib + node
            idx_g >>= 1
            node_adrs[24:28] = (z + 1).to_bytes(4, "big")
            node_adrs[28:32] = idx_g.to_bytes(4, "big")
            node = _shake(pk_seed + bytes(node_adrs) + pair, n)
        roots.append(node)
    pk_adrs = _retype(adrs, A_FORS_ROOTS)
    return _shake(pk_seed + bytes(pk_adrs) + b"".join(roots), n)


# ---------------------------------------------------------------- the scheme


def keygen_internal(xi: bytes, name: str = DEFAULT):
    """Deterministic from a 32-byte seed: SHAKE256 expands it into the three
    n-byte seeds FIPS 205 keygen takes (SK.seed, SK.prf, PK.seed), domain-
    separated by the parameter set so one wallet seed never yields related
    keys under two schemes."""
    p = _params(name)
    n = p["n"]
    if len(xi) != 32:
        raise ValueError("seed must be 32 bytes")
    seeds = _shake(b"slh-keygen\x00" + name.encode() + b"\x00" + xi, 3 * n)
    sk_seed, sk_prf, pk_seed = seeds[:n], seeds[n:2 * n], seeds[2 * n:]
    levels = _xmss_levels(sk_seed, pk_seed, p["d"] - 1, 0, p["hp"], n)
    pk_root = levels[p["hp"]][0]
    return pk_seed + pk_root, sk_seed + sk_prf + pk_seed + pk_root


def keygen(name: str = DEFAULT, seed: bytes | None = None):
    """A fresh keypair. Pass a 32-byte seed for a deterministic one."""
    xi = seed if seed is not None else secrets.token_bytes(32)
    return keygen_internal(xi, name)


def _split_digest(digest: bytes, p):
    k, a_h, h, hp = p["k"], p["a"], p["h"], p["hp"]
    n1 = -(-(k * a_h) // 8)
    n2 = -(-(h - hp) // 8)
    md = digest[:n1]
    idx_tree = int.from_bytes(digest[n1:n1 + n2], "big") % (1 << (h - hp))
    idx_leaf = int.from_bytes(digest[n1 + n2:], "big") % (1 << hp)
    return md, idx_tree, idx_leaf


def sign_internal(sk: bytes, m_prime: bytes, addrnd: bytes,
                  name: str = DEFAULT) -> bytes:
    """Algorithm 19. FORS signs the message digest, the hypertree signs the
    FORS public key, and which few-time key gets spent is itself derived from
    the message — statelessness bought with tree height."""
    p = _params(name)
    n = p["n"]
    sk_seed, sk_prf = sk[:n], sk[n:2 * n]
    pk_seed, pk_root = sk[2 * n:3 * n], sk[3 * n:]
    r = _shake(sk_prf + addrnd + m_prime, n)
    digest = _shake(r + pk_seed + pk_root + m_prime, _digest_len(p))
    md, idx_tree, idx_leaf = _split_digest(digest, p)
    adrs = _adrs(0, idx_tree, A_FORS_TREE)
    adrs[20:24] = idx_leaf.to_bytes(4, "big")
    sig_fors, pk_fors = _fors_sign(md, sk_seed, pk_seed, adrs, p)
    sig_ht = _ht_sign(pk_fors, sk_seed, pk_seed, idx_tree, idx_leaf, p)
    return r + sig_fors + sig_ht


def verify_internal(pk: bytes, m_prime: bytes, sig: bytes,
                    name: str = DEFAULT) -> bool:
    """Algorithm 20."""
    p = _params(name)
    n = p["n"]
    if len(pk) != 2 * n or len(sig) != sizes(name)["sig"]:
        return False
    pk_seed, pk_root = pk[:n], pk[n:]
    r = sig[:n]
    fors_bytes = p["k"] * (p["a"] + 1) * n
    sig_fors = sig[n:n + fors_bytes]
    sig_ht = sig[n + fors_bytes:]
    digest = _shake(r + pk_seed + pk_root + m_prime, _digest_len(p))
    md, idx_tree, idx_leaf = _split_digest(digest, p)
    adrs = _adrs(0, idx_tree, A_FORS_TREE)
    adrs[20:24] = idx_leaf.to_bytes(4, "big")
    pk_fors = _fors_pk_from_sig(sig_fors, md, pk_seed, adrs, p)
    return _ht_verify(pk_fors, sig_ht, pk_seed, idx_tree, idx_leaf,
                      pk_root, p)


def _m_prime(msg: bytes, ctx: bytes) -> bytes:
    if len(ctx) > 255:
        raise ValueError("context must be at most 255 bytes")
    return b"\x00" + bytes([len(ctx)]) + ctx + msg


def sign(sk: bytes, msg: bytes, name: str = DEFAULT, ctx: bytes = b"",
         deterministic: bool = False) -> bytes:
    """Algorithm 22. Hedged by default: n fresh random bytes randomise which
    message digest — and so which few-time keys — a given message spends."""
    p = _params(name)
    addrnd = sk[2 * p["n"]:3 * p["n"]] if deterministic \
        else secrets.token_bytes(p["n"])
    return sign_internal(sk, _m_prime(msg, ctx), addrnd, name)


def verify(pk: bytes, msg: bytes, sig: bytes, name: str = DEFAULT,
           ctx: bytes = b"") -> bool:
    """Algorithm 24."""
    try:
        return verify_internal(pk, _m_prime(msg, ctx), sig, name)
    except Exception:
        return False
