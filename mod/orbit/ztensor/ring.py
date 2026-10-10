"""
ring.py — Linkable Spontaneous Anonymous Group signatures (LSAG).

An LSAG lets one member of a group sign a message so that a verifier learns
ONLY that *some* member signed it — never which one. Each signature also
carries a "key image" (a linkable tag) derived from the signer's secret and a
per-context base, so the same signer signing the same context twice produces
the same tag. That is exactly what anonymous voting needs:

  * anonymity   — you cannot tell which validator/miner cast a given vote;
  * linkability — one member cannot vote twice on the same topic (same tag);
  * soundness   — only a true member of the set can produce a valid signature.

This is the Liu-Wei-Wong (2004) construction over a prime-order subgroup of
Z_p* (RFC 3526 MODP-2048, a safe prime p = 2q+1). We work in the order-q
subgroup of quadratic residues, so any non-identity QR generates it.

Pure python, stdlib only. SECRET KEYS NEVER LEAVE THE SIGNER: this module
signs/verifies, and the ztensor API only ever stores PUBLIC keys and tags.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import List, Tuple

# RFC 3526, 2048-bit MODP Group (id 14). Safe prime: p = 2q + 1, q prime.
_P_HEX = (
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF"
)
P = int(_P_HEX, 16)
Q = (P - 1) // 2          # prime order of the QR subgroup
G = 4                     # 2^2: always a QR, hence a generator of the order-q subgroup


def _h_int(*parts: bytes) -> int:
    h = hashlib.sha512()
    for pt in parts:
        h.update(len(pt).to_bytes(8, "big"))
        h.update(pt)
    return int.from_bytes(h.digest(), "big")


def _b(n: int) -> bytes:
    return n.to_bytes((P.bit_length() + 7) // 8, "big")


def _hash_to_group(*parts: bytes) -> int:
    """Hash arbitrary bytes to a non-identity element of the order-q subgroup.
    Squaring maps any nonzero residue into the QR subgroup."""
    x = _h_int(b"ztensor-h2g", *parts) % P
    if x == 0:
        x = 2
    e = pow(x, 2, P)
    return e if e != 1 else G


def _challenge(ring: List[int], tag: int, msg: bytes, a: int, b: int) -> int:
    payload = b"".join(_b(y) for y in ring)
    return _h_int(b"ztensor-lsag-c", payload, _b(tag), msg, _b(a), _b(b)) % Q


def keygen() -> Tuple[int, int]:
    """Return (secret, public). The secret never leaves the holder."""
    x = secrets.randbelow(Q - 1) + 1
    return x, pow(G, x, P)


def topic_base(ring: List[int], topic: bytes) -> int:
    """Per-(ring, topic) group element the key image is computed against.
    Baking the topic in means the linkable tag catches double-votes WITHIN a
    topic while staying unlinkable ACROSS topics."""
    payload = b"".join(_b(y) for y in sorted(ring))
    return _hash_to_group(payload, topic)


def key_image(secret: int, ring: List[int], topic: bytes) -> int:
    return pow(topic_base(ring, topic), secret, P)


def sign(secret: int, ring: List[int], topic: bytes, msg: bytes) -> dict:
    """Produce an LSAG signature over `msg` as a member of `ring` for `topic`.
    The signer's public key g^secret must be present in `ring`."""
    pub = pow(G, secret, P)
    if pub not in ring:
        raise ValueError("signer public key is not in the ring")
    n = len(ring)
    pi = ring.index(pub)
    h = topic_base(ring, topic)
    tag = pow(h, secret, P)

    c = [0] * n
    s = [0] * n
    u = secrets.randbelow(Q - 1) + 1
    c[(pi + 1) % n] = _challenge(ring, tag, msg, pow(G, u, P), pow(h, u, P))

    i = (pi + 1) % n
    while i != pi:
        s[i] = secrets.randbelow(Q - 1) + 1
        a = (pow(G, s[i], P) * pow(ring[i], c[i], P)) % P
        b = (pow(h, s[i], P) * pow(tag, c[i], P)) % P
        c[(i + 1) % n] = _challenge(ring, tag, msg, a, b)
        i = (i + 1) % n

    s[pi] = (u - secret * c[pi]) % Q
    return {"c0": c[0], "s": s, "tag": tag}


def verify(ring: List[int], topic: bytes, msg: bytes, sig: dict) -> bool:
    """True iff `sig` was produced by some member of `ring` for `topic`/`msg`."""
    try:
        n = len(ring)
        s = sig["s"]
        tag = int(sig["tag"])
        if len(s) != n:
            return False
        h = topic_base(ring, topic)
        c = int(sig["c0"]) % Q
        for i in range(n):
            a = (pow(G, int(s[i]), P) * pow(ring[i], c, P)) % P
            b = (pow(h, int(s[i]), P) * pow(tag, c, P)) % P
            c = _challenge(ring, tag, msg, a, b)
        return c == int(sig["c0"]) % Q
    except (KeyError, TypeError, ValueError):
        return False
