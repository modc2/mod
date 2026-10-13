"""CAIRO-MSS-SHA256 — a hash-based key type whose verifier is a Cairo program.

Why it exists: every other quantum-safe prim here rests on one of two
things — module lattices (ML-DSA) or SHAKE256 (SLH-DSA, Lamport). This one
rests on SHA-256 alone, a third assumption that shares nothing with either.
Put it inside an ALL-of composite next to ML-DSA and a quantum attacker has
to break a lattice AND invert SHA-256 (Grover: ~2^128) to forge — each part
is one more independent wall, not one more copy of the same wall.

And its verifier is written in Cairo (src/pq/cairo/src/lib.cairo). Cairo
programs are what STARKs prove, and STARKs are themselves hash-based — so
"this witness verifies" becomes a statement a STARK can attest to without
re-running it, and without trusting anything Shor touches. That is the path
by which private key types could drop the proposer from their trust story;
this module ships the verifier and a parity check (cairo_verify), not a
prover — the box has scarb 2.8.4 and its Cairo VM, no STARK prover.

The construction (SPHINCS-shaped, without FORS — a 3-layer hypertree of
WOTS one-time keys, n=32, w=16, 67 chains, 8-high trees, 2^24 leaves):

    T(adrs, data)  = SHA-256(pub_seed | adrs (8 u32 BE) | data)
    chain          = WOTS chain steps of T, type 0
    wots pk        = T(type 1, all 67 chain ends)
    tree node      = T(type 2, left | right)
    pk             = pub_seed | root                          64 bytes
    mhash          = SHA-256(FIPS-style ctx frame | msg)
    R              = SHA-256(prf | mhash)                     deterministic
    dg             = SHA-256(R | pub_seed | root | mhash)
    leaf index     = first 3 bytes of dg  (layer 0 tree = idx>>6, ...)
    sig            = R | 4 x (67 WOTS chains | 6 auth nodes)  9376 bytes

The bottom WOTS key signs dg; each upper WOTS key signs the root of the
tree below it (always the same root, so upper keys are never reused).

HONEST LIMIT — FEW-TIME: the leaf is picked by the message hash, so two
signatures land on the same bottom one-time key with probability about
q^2 / 2^25 after q signatures (~0.03% at 100, ~3% at 1000). One reuse does
not hand over the key, but it does leak chain positions. That is why this
prim is shelved as a LAYER: alone it suits low-volume accounts; inside an
ALL composite it only ever adds work for the attacker.

Every hash input is a multiple of 4 bytes, so the Cairo side runs it on
u32 words through core::sha256 with no byte shuffling.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shutil
import struct
import subprocess
import threading
import time

NAME = "CAIRO-MSS-SHA256"
N = 32
W = 16
LEN1 = 64            # 256 bits / 4
LEN2 = 3             # checksum <= 64*15 = 960 < 16^3
LEN = LEN1 + LEN2    # 67
H = 6                # tree height per layer
D = 4                # layers -> 2^24 leaves
PK_BYTES = 2 * N
SIG_BYTES = N + D * (LEN * N + H * N)       # 9376

WOTS_HASH, WOTS_PK, TREE = 0, 1, 2

HERE = os.path.dirname(os.path.abspath(__file__))
_VM = threading.Lock()      # one Cairo VM at a time — it is public and ~1.5 s
CAIRO_DIR = os.path.join(HERE, "cairo")


def _h(*parts) -> bytes:
    return hashlib.sha256(b"".join(parts)).digest()


_PACK = struct.Struct(">8I").pack


def _adrs(layer, tree, typ, kp=0, chain=0, hsh=0) -> bytes:
    return _PACK(layer, 0, tree, typ, kp, chain, hsh, 0)


def _frame(msg: bytes, ctx: bytes) -> bytes:
    if len(ctx) > 255:
        raise ValueError("ctx longer than 255 bytes")
    return b"\x00" + bytes([len(ctx)]) + ctx + msg


def _base_w(m: bytes):
    out = []
    for b in m:
        out += [b >> 4, b & 15]
    csum = sum(W - 1 - v for v in out)
    return out + [(csum >> 8) & 15, (csum >> 4) & 15, csum & 15]


# ── one-time keys and trees (signer side) ──────────────────────────


def _chain(ps, x, start, steps, layer, tree, kp, i):
    # the hot loop: pub_seed is absorbed once, each step copies that state
    base = hashlib.sha256(ps)
    for j in range(start, start + steps):
        h = base.copy()
        h.update(_PACK(layer, 0, tree, WOTS_HASH, kp, i, j, 0) + x)
        x = h.digest()
    return x


def _wots_sk(ss, ps, layer, tree, kp, i):
    return _h(b"cmss/sk\x00\x00", ss, _adrs(layer, tree, WOTS_HASH, kp, i, 0))


def _wots_pk(ss, ps, layer, tree, kp):
    ends = b"".join(_chain(ps, _wots_sk(ss, ps, layer, tree, kp, i), 0, W - 1,
                           layer, tree, kp, i) for i in range(LEN))
    return _h(ps, _adrs(layer, tree, WOTS_PK, kp), ends)


def _wots_sign(ss, ps, layer, tree, kp, m):
    return b"".join(_chain(ps, _wots_sk(ss, ps, layer, tree, kp, i), 0, v,
                           layer, tree, kp, i)
                    for i, v in enumerate(_base_w(m)))


def _node(ps, layer, tree, z, idx, left, right):
    return _h(ps, _adrs(layer, tree, TREE, 0, z, idx), left, right)


@functools.lru_cache(maxsize=16)
def _tree(ss, ps, layer, tree):
    """All levels of one 2^H tree: levels[0] = leaves, levels[H] = [root]."""
    lv = [[_wots_pk(ss, ps, layer, tree, kp) for kp in range(1 << H)]]
    for z in range(1, H + 1):
        prev = lv[-1]
        lv.append([_node(ps, layer, tree, z, i, prev[2 * i], prev[2 * i + 1])
                   for i in range(len(prev) // 2)])
    return lv


def _auth(levels, leaf):
    return b"".join(levels[z][(leaf >> z) ^ 1] for z in range(H))


def _seeds(seed: bytes):
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    return (_h(b"cmss/sk-seed", seed), _h(b"cmss/pub-seed", seed),
            _h(b"cmss/prf", seed))


def keygen(seed: bytes):
    ss, ps, prf = _seeds(seed)
    root = _tree(ss, ps, D - 1, 0)[H][0]
    pk = ps + root
    return pk, seed + pk          # sk = seed | pk (96 bytes)


def _digest(R, pk, mhash):
    dg = _h(R, pk, mhash)
    return dg, int.from_bytes(dg[:3], "big")


def sign(sk: bytes, msg: bytes, ctx: bytes = b"") -> bytes:
    seed, pk = sk[:32], sk[32:96]
    ss, ps, prf = _seeds(seed)
    mhash = _h(_frame(msg, ctx))
    R = _h(prf, mhash)
    dg, idx = _digest(R, pk, mhash)
    out, m = [R], dg
    for layer in range(D):
        tree, leaf = idx >> (H * (layer + 1)), (idx >> (H * layer)) & ((1 << H) - 1)
        lv = _tree(ss, ps, layer, tree)
        out += [_wots_sign(ss, ps, layer, tree, leaf, m), _auth(lv, leaf)]
        m = lv[H][0]
    return b"".join(out)


# ── verify (the Python twin of lib.cairo) ──────────────────────────


def verify_mhash(pk: bytes, mhash: bytes, sig: bytes) -> bool:
    if len(pk) != PK_BYTES or len(sig) != SIG_BYTES or len(mhash) != N:
        return False
    ps, root = pk[:N], pk[N:]
    R = sig[:N]
    dg, idx = _digest(R, pk, mhash)
    m, off = dg, N
    for layer in range(D):
        tree, leaf = idx >> (H * (layer + 1)), (idx >> (H * layer)) & ((1 << H) - 1)
        ws, off = sig[off:off + LEN * N], off + LEN * N
        auth, off = sig[off:off + H * N], off + H * N
        ends = b"".join(
            _chain(ps, ws[i * N:(i + 1) * N], v, W - 1 - v, layer, tree, leaf, i)
            for i, v in enumerate(_base_w(m)))
        node = _h(ps, _adrs(layer, tree, WOTS_PK, leaf), ends)
        for z in range(H):
            sib = auth[z * N:(z + 1) * N]
            i = leaf >> (z + 1)
            node = (_node(ps, layer, tree, z + 1, i, sib, node)
                    if (leaf >> z) & 1 else
                    _node(ps, layer, tree, z + 1, i, node, sib))
        m = node
    return m == root


def verify(pk, msg, sig, ctx: bytes = b"") -> bool:
    try:
        return verify_mhash(bytes(pk), _h(_frame(bytes(msg), bytes(ctx))),
                            bytes(sig))
    except Exception:                                        # noqa: BLE001
        return False


# ── the Cairo verifier ─────────────────────────────────────────────


def _scarb():
    for p in (os.environ.get("POSTQUANT_SCARB"), shutil.which("scarb"),
              os.path.expanduser("~/.local/bin/scarb")):
        if p and os.path.exists(p):
            return p
    return None


def _fresh():
    """True when the compiled program is newer than lib.cairo — then the
    VM runs it as-is (~1.5 s) instead of recompiling (~13 s)."""
    art = os.path.join(CAIRO_DIR, "target", "dev", "cairo_mss.sierra.json")
    src = os.path.join(CAIRO_DIR, "src", "lib.cairo")
    return (os.path.exists(art) and
            os.path.getmtime(art) >= os.path.getmtime(src))


def cairo_status():
    s = _scarb()
    src = os.path.join(CAIRO_DIR, "src", "lib.cairo")
    return {"program": os.path.relpath(src, os.path.dirname(HERE)),
            "program_sha3": hashlib.sha3_256(open(src, "rb").read()).hexdigest()
            if os.path.exists(src) else None,
            "scarb": s, "available": bool(s and os.path.exists(src)),
            "built": _fresh()}


def _words(b: bytes):
    return [int.from_bytes(b[i:i + 4], "big") for i in range(0, len(b), 4)]


def cairo_verify(pk: bytes, msg: bytes, sig: bytes, ctx: bytes = b"",
                 timeout: float = 180.0) -> dict:
    """Run the witness through lib.cairo in the Cairo VM. Returns
    {valid, python, agree, ms, gas_used} — `agree` is the parity check
    between the Cairo program and verify() above."""
    scarb = _scarb()
    if not scarb:
        return {"available": False, "error": "scarb not found (install "
                "scarb 2.8.x or set POSTQUANT_SCARB)"}
    mhash = _h(_frame(bytes(msg), bytes(ctx)))
    py = verify_mhash(bytes(pk), mhash, bytes(sig))
    if len(pk) != PK_BYTES or len(sig) != SIG_BYTES:
        return {"available": True, "valid": False, "python": py,
                "agree": not py, "note": "wrong sizes — refused before the VM"}
    words = _words(pk) + _words(mhash) + _words(sig)
    gas = 40_000_000_000
    env = dict(os.environ, PATH=os.path.dirname(scarb) + ":" +
               os.environ.get("PATH", ""))
    with _VM:
        return _run_vm(scarb, words, gas, env, py, timeout)


def _run_vm(scarb, words, gas, env, py, timeout):
    built = _fresh()
    t0 = time.time()
    p = subprocess.run(
        [scarb, "cairo-run", *(["--no-build"] if built else []),
         "--available-gas", str(gas), "--",
         json.dumps([words])],
        cwd=CAIRO_DIR, capture_output=True, text=True, timeout=timeout, env=env)
    ms = round((time.time() - t0) * 1000)
    out = p.stdout + p.stderr
    line = next((l for l in out.splitlines()
                 if "Run completed successfully" in l), "")
    if not line:
        return {"available": True, "valid": False, "python": py,
                "agree": False, "ms": ms, "error": out.strip()[-600:]}
    ret = json.loads(line.split("returning", 1)[1].strip())
    valid = bool(ret and ret[-1] == 1)
    rem = next((l for l in out.splitlines() if l.startswith("Remaining gas")),
               "")
    used = gas - int(rem.split(":")[1]) if rem else None
    return {"available": True, "valid": valid, "python": py,
            "agree": valid == py, "ms": ms, "gas_used": used,
            "words": len(words)}


def make_algo(algos):
    return algos.SigAlgo(
        NAME, keygen, sign, verify,
        sizes={"pk": PK_BYTES, "sig": SIG_BYTES, "seed": 32},
        family="CAIRO-MSS", standard="SPHINCS-style hypertree, verifier in "
                                     "Cairo (src/pq/cairo)",
        basis="SHA-256 preimage/second-preimage only — an assumption "
              "independent of lattices (ML-DSA) and SHAKE (SLH-DSA)",
        quantum_safe=True, quantum_bits=128,
        note="few-time: the leaf is chosen by the message hash, so reuse "
             "odds are ~q^2/2^25 after q signatures. Built to be a LAYER — "
             "inside an ALL composite next to ML-DSA it makes a forger "
             "break a lattice AND invert SHA-256. Its verifier is a Cairo "
             "program (STARK-provable); pq_cairo runs it in the Cairo VM.")
