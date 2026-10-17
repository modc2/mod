"""Private key types: a valid signature whose key type is never revealed.

Every other account on this chain publishes two things the moment it first
spends: which algorithm it signs with (the tx's `scheme`) and its public key.
That is a gift to an attacker. The scheme name says which hard problem to
attack, and the public key is the input every attack needs — Shor's
algorithm against a curve, a lattice reduction against ML-DSA, a forgery
search against a wasm prim all start from pk. An account that never shows
either gives a quantum computer nothing to point at.

A private account is one SigAlgo named "private" that wraps a hidden one:

    commitment = SHA3-256("pq-private/commit" | inner | salt | inner_pk)
    address    = the usual pq address over (private, commitment)

The chain sees only the commitment. A witness is a SEALED ENVELOPE:

    (K, c)   = ML-KEM-768.encaps(seal_ek, m)       m derived from seed+msg
    body     = inner | salt | inner_pk | inner_sig | zero pad
    envelope = c | body XOR SHAKE256(K, c) | SHA3-256 tag

padded to ONE fixed size, so an ML-DSA-44 witness, an SLH-DSA-256f
witness, an ed25519 witness and a wasm prim's witness are byte-for-byte the
same length and look like the same noise. The set of key types that fit
the envelope and pass the gate is the anonymity set (cloak_set()).

WHO CAN CHECK IT — the honest part
    Verifying means opening the envelope, which takes the seal key's
    decapsulation half. That half lives with the proposer: the one party
    this single-proposer chain already trusts to order blocks and whose
    signature already seals every header. The proposer opens, checks the
    commitment, runs the gate on the inner scheme (current policy, so a
    replay audit re-judges private witnesses too) and runs the inner
    scheme's own verifier — wasm-enforced, same as a public witness.
    Everyone else sees a block the proposer signed, or asks for an
    attestation (attest()): the proposer's own signature over "this proof
    verifies for this commitment and this message".

    What that buys: no observer — and no future quantum computer replaying
    the public chain — ever learns the key type or the public key. ML-KEM
    keeps harvest-now-decrypt-later off the table. What it costs: if the
    seal key leaks, envelopes open and each account falls back to its inner
    scheme's own card (a hidden ed25519 is then Shor-able again). It is
    privacy from the world, not from the proposer — a zero-knowledge proof
    would remove that trust, and is the upgrade path, not a claim made here.

    The owner can always prove what they used (open()/check_open()):
    revealing inner, salt and inner_pk lets anyone recompute the commitment.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

try:
    from . import algos                                          # noqa: E402
except ImportError:                                              # run as a script
    import algos                                                 # noqa: E402

import mlkem                                                     # noqa: E402

NAME = "private"
DATA_DIR = os.path.expanduser(
    os.environ.get("POSTQUANT_DATA_DIR", "~/.mod/postquant"))
SEAL_FILE = os.path.join(DATA_DIR, "seal.key")
KEM = "ML-KEM-768"
KEM_CT = mlkem.sizes(KEM)["ciphertext"]               # 1088
TAG = 32
# One size for every witness. 64 KiB fits every builtin — SLH-DSA-256f's
# 49856-byte signature included — so the whole accepted registry can hide
# in one crowd. Witness gas bills it like any other witness.
ENVELOPE = int(os.environ.get("POSTQUANT_SEAL_BYTES", 1 << 16))
BODY = ENVELOPE - KEM_CT - TAG
MAGIC = b"pqseal1"
DEFAULT_INNER = "ML-DSA-44"


class PrivateError(ValueError):
    pass


def _shake(*parts, n=32):
    h = hashlib.shake_256()
    for p in parts:
        h.update(p)
    return h.digest(n)


# ── the seal key ──────────────────────────────────────────────────

_SEAL = None


def seal_keys():
    """(ek, dk) of this node's seal key — made once, then never rotated:
    every private witness ever sealed to it must stay openable for replay."""
    global _SEAL
    if _SEAL is None:
        try:
            with open(SEAL_FILE) as f:
                seed = bytes.fromhex(json.load(f)["seed"])
        except FileNotFoundError:
            seed = os.urandom(64)
            os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
            tmp = SEAL_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"kem": KEM, "seed": seed.hex(),
                           "note": "decapsulation seed for private-key-type "
                                   "envelopes; losing it makes every "
                                   "private witness unverifiable"}, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, SEAL_FILE)
        _SEAL = mlkem.keygen(KEM, seed)
    return _SEAL


def seal_info():
    ek, _ = seal_keys()
    return {"kem": KEM, "ek_sha3": hashlib.sha3_256(ek).hexdigest(),
            "ek_bytes": len(ek), "ek": ek.hex()}


# ── commitments ───────────────────────────────────────────────────


def commitment(inner: str, salt: bytes, inner_pk: bytes) -> bytes:
    return hashlib.sha3_256(b"pq-private/commit\x00" + inner.encode()
                            + b"\x00" + salt + inner_pk).digest()


def fits(name) -> bool:
    a = algos.maybe(name)
    if a is None or name == NAME:
        return False
    head = len(MAGIC) + 1 + len(name.encode()) + 32 + 8
    return head + a.sizes.get("pk", 0) + a.sizes.get("sig", 0) <= BODY


def cloak_set():
    """Every key type a private account may hide: registered, accepted by
    the gate right now, and small enough for the envelope. Its size is the
    attacker's guessing space before they have seen a single public key."""
    return [n for n in algos.names() if fits(n) and algos.allowed(n)]


# ── keygen / sign / verify ────────────────────────────────────────


def keygen_inner(seed: bytes, inner: str = DEFAULT_INNER):
    """(commitment, sk) for a private account hiding `inner`. The inner key
    and the salt are SHAKE-derived from the wallet seed with their own
    domains, so the hidden key is never the same as a bare wallet's."""
    if inner == NAME:
        raise PrivateError("a private key type cannot hide another private "
                           "key type")
    if not fits(inner):
        raise PrivateError(f"{inner} does not fit the {ENVELOPE}-byte "
                           "envelope (or is unknown) — private_info lists "
                           "the cloak set")
    algo = algos.get(inner)
    inner_seed = _shake(b"pq-private/seed\x00", inner.encode(), b"\x00", seed)
    salt = _shake(b"pq-private/salt\x00", inner.encode(), b"\x00", seed)
    ipk, isk = algo.keygen(inner_seed)
    sk = {"inner": inner, "salt": salt, "pk": ipk, "sk": isk, "seed": seed}
    return commitment(inner, salt, ipk), sk


def _stream(key, c):
    return _shake(b"pq-private/stream\x00", key, c, n=BODY)


def _tag(key, c, ct):
    return hashlib.sha3_256(b"pq-private/tag\x00" + key + c + ct).digest()


def _xor(a, b):
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")) \
        .to_bytes(len(a), "big")


def sign(sk, msg, ctx=b""):
    inner = sk["inner"]
    isig = algos.get(inner).sign(sk["sk"], msg, ctx)
    name = inner.encode()
    body = (MAGIC + bytes([len(name)]) + name + sk["salt"]
            + struct.pack(">I", len(sk["pk"])) + sk["pk"]
            + struct.pack(">I", len(isig)) + isig)
    if len(body) > BODY:
        raise PrivateError("inner witness outgrew the envelope")
    body += b"\x00" * (BODY - len(body))
    ek, _ = seal_keys()
    # Deterministic sealing: the KEM coins come from the secret seed and the
    # message, so the same signature over the same bytes seals the same way.
    m = _shake(b"pq-private/coins\x00", sk["seed"], msg, b"\x00", ctx)
    key, c = mlkem.encaps_internal(ek, m, KEM)
    ct = _xor(body, _stream(key, c))
    return c + ct + _tag(key, c, ct)


def open_envelope(env: bytes):
    """Decrypt and parse an envelope -> (inner, salt, inner_pk, inner_sig).
    Raises PrivateError on anything malformed — including non-zero padding,
    which would otherwise make one witness many."""
    if len(env) != ENVELOPE:
        raise PrivateError(f"an envelope is exactly {ENVELOPE} bytes")
    c, ct, tag = env[:KEM_CT], env[KEM_CT:-TAG], env[-TAG:]
    _, dk = seal_keys()
    key = mlkem.decaps(dk, c, KEM)
    if not hmac.compare_digest(tag, _tag(key, c, ct)):
        raise PrivateError("envelope tag does not verify")
    body = _xor(ct, _stream(key, c))
    if body[:len(MAGIC)] != MAGIC:
        raise PrivateError("bad envelope magic")
    i = len(MAGIC)
    n = body[i]
    i += 1
    inner = body[i:i + n].decode()
    i += n
    salt = body[i:i + 32]
    i += 32
    (lpk,) = struct.unpack(">I", body[i:i + 4])
    i += 4
    ipk = body[i:i + lpk]
    i += lpk
    (lsig,) = struct.unpack(">I", body[i:i + 4])
    i += 4
    isig = body[i:i + lsig]
    i += lsig
    if i > BODY or any(body[i:]):
        raise PrivateError("envelope padding is not zero")
    return inner, salt, ipk, isig


def verify(pk, msg, sig, ctx=b""):
    """pk is the 32-byte commitment. Fails closed on everything."""
    try:
        if len(pk) != 32:
            return False
        inner, salt, ipk, isig = open_envelope(sig)
        if inner == NAME or not hmac.compare_digest(
                commitment(inner, salt, ipk), pk):
            return False
        if algos.refusal(inner) is not None:      # current policy, always
            return False
        return bool(algos.get(inner).verify(ipk, msg, isig, ctx))
    except Exception:                                       # noqa: BLE001
        return False


def _keygen_default(seed):
    return keygen_inner(seed, DEFAULT_INNER)


def make_algo():
    a = algos.SigAlgo(
        NAME, _keygen_default, sign, verify,
        sizes={"pk": 32, "sig": ENVELOPE, "seed": 32},
        family="private", standard="",
        basis=("hidden key type: the address commits to SHA3(inner, salt, "
               "pk); witnesses are fixed-size ML-KEM-768 envelopes opened "
               "only by the proposer"),
        quantum_safe=True, quantum_bits=128,
        note=("the scheme and public key never appear on chain — an attacker "
              "must guess the key type out of the cloak set AND recover a "
              "public key it was never shown; trust: the proposer opens "
              "envelopes"))
    a.private = True
    return a


# ── attestations and selective disclosure ─────────────────────────


ATTEST_CTX = b"postquant/private-attest/v1"


def attest(validator, keys_mod, commit: bytes, msg: bytes, env: bytes,
           chain_id: str):
    """Check a standalone proof and have the proposer sign the verdict —
    the only thing a non-proposer needs to trust, and exactly as much as it
    already trusts a block."""
    ok = verify(commit, msg, env, ATTEST_CTX)
    over = {"chain_id": chain_id, "commitment": commit.hex(),
            "message_sha3": hashlib.sha3_256(msg).hexdigest(),
            "proof_sha3": hashlib.sha3_256(env).hexdigest(),
            "valid": ok}
    canon = json.dumps(over, sort_keys=True, separators=(",", ":")).encode()
    algo = algos.get(validator["scheme"])
    sig = algo.sign(keys_mod.secret_key(validator), canon, ATTEST_CTX)
    return ok, {"by": validator["address"], "scheme": validator["scheme"],
                "pk": validator["pk"], "over": over, "sig": sig.hex(),
                "context": ATTEST_CTX.decode()}


def prove(sk, msg: bytes):
    """A standalone proof over arbitrary bytes, bound to the attest context
    so it can never double as a transaction witness."""
    return sign(sk, msg, ATTEST_CTX)


def check_open(commit_hex, inner, salt_hex, pk_hex) -> bool:
    try:
        return hmac.compare_digest(
            commitment(inner, bytes.fromhex(salt_hex), bytes.fromhex(pk_hex)),
            bytes.fromhex(commit_hex))
    except Exception:                                       # noqa: BLE001
        return False
