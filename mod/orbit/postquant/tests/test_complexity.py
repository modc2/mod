"""The complexity gate: what breaking a key costs, and who gets refused.

pq/complexity.py makes two promises. Analytically: assuming a maximal-
entropy seed, every scheme's brute-force cost is 2^(seed bits) classically
and Grover's square root of that — unless the scheme itself declared a
cheaper structural attack (ed25519's Pollard rho) or declared quantum_safe=
False, which on this chain MEANS polynomial. Empirically: the probe refutes
the maximal-entropy assumption when it is unearned — a keygen that ignores
seed bytes, collapses the keyspace or signs without binding the message is
refused at the gate with code insufficient_complexity, while an honest
classical scheme is admitted with its price printed on the catalog card.
"""

import os

import pytest

import keys as K
import mcp as mcpsrv
from pq import algos, complexity
from state import StateError


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


def _drop_plugin(filename, body):
    plugin_dir = algos.PLUGIN_DIRS[1]
    os.makedirs(plugin_dir, exist_ok=True)
    path = os.path.join(plugin_dir, filename)
    with open(path, "w") as f:
        f.write(body)
    algos.load_plugins()
    return path


def _cleanup(path, name):
    os.remove(path)
    algos.REGISTRY.pop(name, None)
    complexity.clear_cache(name)


# ── the analytic half ─────────────────────────────────────────────


def test_mldsa_brute_force_bounds():
    a = complexity.analytic(algos.get("ML-DSA-44"))
    assert a["seed_bits"] == 256
    assert a["brute_force_classical"]["cost"] == "2^256"
    assert a["best_classical"]["bits"] == 256     # nothing cheaper declared
    assert not a["quantum"]["polynomial"]
    assert a["quantum"]["cost"] == "2^128"        # Grover, nothing better
    assert a["meets_floor"]


def test_ed25519_bounds_are_honest():
    """The classical example declares its own cryptanalysis: Pollard rho at
    ~2^126 beats brute force, and quantum is polynomial because it said so
    (quantum_safe=False means Shor, not Grover)."""
    a = complexity.analytic(algos.get("ed25519"))
    assert a["brute_force_classical"]["cost"] == "2^256"
    assert a["best_classical"]["bits"] == 126
    assert a["quantum"]["polynomial"] and a["quantum"]["cost"] == "polynomial"
    assert a["meets_floor"]                       # 126 >= the 2^100 floor


# ── the empirical half ────────────────────────────────────────────


def test_probe_passes_a_real_scheme():
    p = complexity.probe(algos.get("ed25519"), fresh=True)
    assert p["ok"], p["failures"]
    # one flipped seed bit moves roughly half the public key
    assert p["avalanche"] and 0.3 < p["avalanche"] < 0.7


def test_seed_ignoring_keygen_is_refused():
    """A keygen that reads seed[:4] has a 2^32 keyspace in a 2^256 costume.
    It registers, it is listed, and the gate refuses it with the dead seed
    regions named."""
    path = _drop_plugin("lazy.py", '''
import hashlib

def keygen(seed):
    pk = hashlib.sha3_256(b"lazy" + seed[:4]).digest()
    return pk, seed[:4]

def sign(sk, msg, ctx=b""):
    return hashlib.sha3_256(sk + bytes([len(ctx)]) + ctx + msg).digest()

def verify(pk, msg, sig, ctx=b""):
    return hashlib.sha3_256(pk[:0] + sig[:0] + b"") == b"x"  # never called

def register(algos):
    algos.register(algos.SigAlgo(
        "lazy-4byte", keygen, sign, verify,
        sizes={"pk": 32, "sig": 32, "seed": 32},
        family="toy", quantum_safe=True,
        note="test fixture - entropy thief"))
''')
    try:
        assert algos.maybe("lazy-4byte") is not None      # listed...
        assert not algos.allowed("lazy-4byte")            # ...not admitted
        with pytest.raises(StateError) as e:
            K.create("lazybox", scheme="lazy-4byte")
        assert e.value.code == "insufficient_complexity"
        assert "ignored seed byte" in str(e.value)
        row = next(r for r in algos.catalog()["algorithms"]
                   if r["name"] == "lazy-4byte")
        assert not row["accepted"] and row["refused"]
    finally:
        _cleanup(path, "lazy-4byte")


def test_small_declared_keyspace_fails_the_floor():
    """An 8-byte seed is 2^64 brute force — below the floor, refused on the
    arithmetic alone, before any probe runs."""
    path = _drop_plugin("tiny.py", '''
import hashlib

def keygen(seed):
    pk = hashlib.sha3_256(b"tiny" + seed).digest()
    return pk, pk

def sign(sk, msg, ctx=b""):
    return hashlib.sha3_256(sk + bytes([len(ctx)]) + ctx + msg).digest()

def verify(pk, msg, sig, ctx=b""):
    return sign(pk, msg, ctx) == sig

def register(algos):
    algos.register(algos.SigAlgo(
        "tiny-64", keygen, sign, verify,
        sizes={"pk": 32, "sig": 32, "seed": 8},
        family="toy", quantum_safe=True,
        note="test fixture - 64-bit keyspace"))
''')
    try:
        assert not algos.allowed("tiny-64")
        with pytest.raises(StateError) as e:
            K.create("tinybox", scheme="tiny-64")
        assert e.value.code == "insufficient_complexity"
        assert "2^64" in str(e.value)
    finally:
        _cleanup(path, "tiny-64")


def test_rubber_stamp_verify_is_refused():
    """A verify() that says yes to everything is the worst key type a chain
    can accept — the probe's tamper check catches it."""
    path = _drop_plugin("stamp.py", '''
import hashlib

def keygen(seed):
    pk = hashlib.sha3_256(b"stamp" + seed).digest()
    return pk, pk

def sign(sk, msg, ctx=b""):
    return b"\\x00" * 32

def verify(pk, msg, sig, ctx=b""):
    return True

def register(algos):
    algos.register(algos.SigAlgo(
        "rubber-stamp", keygen, sign, verify,
        sizes={"pk": 32, "sig": 32, "seed": 32},
        family="toy", quantum_safe=True,
        note="test fixture - verify always true"))
''')
    try:
        assert not algos.allowed("rubber-stamp")
        v = complexity.verdict(algos.get("rubber-stamp"))
        assert "tampered" in v["reason"]
    finally:
        _cleanup(path, "rubber-stamp")


def test_devnet_override_admits_even_the_weak(monkeypatch):
    """POSTQUANT_ALLOW_CLASSICAL keeps its old meaning: everything
    registered gets in, floor and probe included — devnet only."""
    path = _drop_plugin("weak_dev.py", '''
import hashlib

def keygen(seed):
    pk = hashlib.sha3_256(b"weakdev" + seed[:2]).digest()
    return pk, seed[:2]

def sign(sk, msg, ctx=b""):
    return hashlib.sha3_256(sk + bytes([len(ctx)]) + ctx + msg).digest()

def verify(pk, msg, sig, ctx=b""):
    return len(sig) == 32

def register(algos):
    algos.register(algos.SigAlgo(
        "weak-dev", keygen, sign, verify,
        sizes={"pk": 32, "sig": 32, "seed": 32},
        family="toy", quantum_safe=True, note="test fixture"))
''')
    try:
        assert not algos.allowed("weak-dev")
        monkeypatch.setattr(algos, "ALLOW_CLASSICAL", True)
        assert algos.allowed("weak-dev")
    finally:
        _cleanup(path, "weak-dev")


# ── the whole economy under a classical key ───────────────────────


def test_classical_wallet_full_lifecycle():
    """Create an ed25519 wallet with no override, fund it, write a key with
    it, audit the chain — a user-added classical key type is a first-class
    citizen, priced per its 96-byte witness."""
    w = K.create("curve_first_class", scheme="ed25519", overwrite=True)
    try:
        assert w["scheme"] == "ed25519"
        call("pq_faucet", wallet="curve_first_class", amount="100")
        r = call("pq_set", key="curve/proof", data="classical but priced",
                 hours=1, wallet="curve_first_class")
        assert r["status"] == "included"
        assert r["witness_bytes"] == K.witness_bytes("ed25519")   # 96 bytes
        v = call("pq_verify", signatures=True)
        assert v["ok"], v["problems"]
    finally:
        K.remove("curve_first_class")


# ── the tool ──────────────────────────────────────────────────────


def test_complexity_tool_single_scheme_probes():
    out = call("pq_complexity", scheme="ed25519")
    assert out["verdict"]["ok"]
    assert out["probe"]["ok"]
    assert out["analytic"]["quantum"]["polynomial"]
    assert "maximal entropy" in out["analytic"]["assumes"]


def test_complexity_tool_scan_is_analytic():
    out = call("pq_complexity")
    names = {r["name"] for r in out["algorithms"]}
    assert {"ML-DSA-44", "SLH-DSA-SHAKE-128f", "ed25519"} <= names
    row = next(r for r in out["algorithms"] if r["name"] == "ML-DSA-44")
    assert row["analytic"]["brute_force_classical"]["cost"] == "2^256"
    assert out["floor"].startswith(f"2^{complexity.MIN_BITS}")
    with pytest.raises(StateError):
        call("pq_complexity", scheme="no-such-scheme")
