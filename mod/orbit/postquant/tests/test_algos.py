"""The key-type registry: many algorithms, one dynamic, one gate.

What is under test is the contract pq/algos.py makes: every registered
algorithm — two built-in post-quantum families plus whatever a plugin adds —
signs and verifies through the same four functions; addresses commit to the
key type; the quantum gate refuses classical witnesses without refusing to
list them; and a whole transaction lifecycle works under a scheme that is
not the default one.
"""

import hashlib
import os
import tempfile

import pytest

import chain as C
import keys as K
import mcp as mcpsrv
import state as S
from pq import algos
from state import StateError


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


# ── the registry ──────────────────────────────────────────────────


def test_both_pq_families_registered():
    names = algos.names()
    assert {"ML-DSA-44", "ML-DSA-65", "ML-DSA-87"} <= set(names)
    assert "SLH-DSA-SHAKE-128f" in names
    families = {algos.get(n).family for n in names if algos.get(n).quantum_safe}
    assert {"ML-DSA", "SLH-DSA"} <= families    # two independent assumptions
    assert not algos.catalog()["plugin_errors"]


def test_every_accepted_algo_roundtrips():
    seed = bytes(range(32))
    for name in algos.names(pq_only=True):
        a = algos.get(name)
        pk, sk = a.keygen(seed)
        pk2, sk2 = a.keygen(seed)
        assert (pk, sk) == (pk2, sk2), f"{name}: keygen not deterministic"
        assert len(pk) == a.sizes["pk"]
        sig = a.sign(sk, b"m", b"ctx")
        assert len(sig) == a.sizes["sig"]
        assert a.verify(pk, b"m", sig, b"ctx")
        assert not a.verify(pk, b"m", sig, b"other")     # ctx is load-bearing
        assert not a.verify(pk, b"x", sig, b"ctx")


def test_schemes_do_not_cross():
    """One seed, two schemes: unrelated keys, different addresses, and a
    signature that never verifies under the other algorithm."""
    seed = b"\x07" * 32
    ml, slh = algos.get("ML-DSA-44"), algos.get("SLH-DSA-SHAKE-128f")
    ml_pk, ml_sk = ml.keygen(seed)
    slh_pk, slh_sk = slh.keygen(seed)
    assert ml_pk != slh_pk
    assert K.address(ml_pk, "ML-DSA-44") != K.address(slh_pk,
                                                      "SLH-DSA-SHAKE-128f")
    sig = slh.sign(slh_sk, b"m", b"ctx")
    assert not ml.verify(ml_pk, b"m", sig, b"ctx")


def test_mldsa_addresses_unchanged_by_the_registry():
    """The ML-DSA sets carry an empty addr_domain so every address already on
    chain still resolves — this is the back-compat line nothing may cross."""
    pk = b"\x42" * algos.get("ML-DSA-44").sizes["pk"]
    legacy = "pq" + S.sha3(b"pq-addr\x00", pk)[:20].hex()
    assert K.address(pk, "ML-DSA-44") == legacy
    # ...and a plugin scheme's address is domain-separated from that.
    assert K.address(pk[:32], "ed25519") != \
        "pq" + S.sha3(b"pq-addr\x00", pk[:32])[:20].hex()


def test_ed25519_example_plugin_loaded_and_gated():
    a = algos.maybe("ed25519")
    assert a is not None and a.family == "EdDSA"
    assert a.origin.endswith("algos.d/ed25519.py")
    assert not a.quantum_safe
    assert not algos.allowed("ed25519")
    with pytest.raises(StateError) as e:
        K.create("curveball", scheme="ed25519")
    assert e.value.code == "not_quantum_safe"
    with pytest.raises(StateError) as e:
        K.create("mystery", scheme="no-such-algo")
    assert e.value.code == "unknown_scheme"


def test_ed25519_matches_rfc8032_vector(monkeypatch):
    """The plugin's curve arithmetic against RFC 8032 test vector 1 — with
    the module's ctx framing bypassed, since the RFC signs raw messages."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "pq", "algos.d", "ed25519.py")
    spec = importlib.util.spec_from_file_location("ed25519_vec", path)
    ed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ed)
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc4"
                         "4449c5697b326919703bac031cae7f60")
    pk, sk = ed.keygen(seed)
    assert pk.hex() == ("d75a980182b10ab7d54bfed3c964073a"
                        "0ee172f3daa62325af021a68f707511a")
    monkeypatch.setattr(ed, "_wrap", lambda msg, ctx: msg)
    sig = ed.sign(sk, b"")
    assert sig.hex() == ("e5564300c360ac729086e2cc806e828a"
                         "84877f1eb8e5d974d873e06522490155"
                         "5fb8821590a33bacc61e39701cf9b46b"
                         "d25bf5f0595bbe24655141438e7a100b")
    assert ed.verify(pk, b"", sig)


# ── the chain under a non-default scheme ──────────────────────────


def test_slh_dsa_wallet_full_lifecycle():
    """Create a hash-based wallet, fund it, write a key with it, and audit
    the chain — the whole economy under the second family."""
    w = K.create("hedge", scheme="SLH-DSA-SHAKE-128f", overwrite=True)
    assert w["scheme"] == "SLH-DSA-SHAKE-128f"
    assert w["address"].startswith("pq") and len(w["address"]) == 42
    call("pq_faucet", wallet="hedge", amount="500")

    r = call("pq_set", key="hedge/proof", data="hash-based witness", hours=2,
             wallet="hedge")
    assert r["status"] == "included"
    # The witness is a real SLH-DSA signature and is billed by the byte:
    # sig + pk, at the witness rate, on top of the flat base.
    wb = K.witness_bytes("SLH-DSA-SHAKE-128f")
    assert r["witness_bytes"] == wb
    acct = call("pq_account", wallet="hedge")
    assert acct["scheme"] == "SLH-DSA-SHAKE-128f"     # recorded on first tx

    v = call("pq_verify", signatures=True)
    assert v["ok"], v["problems"]


def test_witness_gas_scales_with_the_scheme():
    """An SLH-DSA transfer pays more witness gas than an ML-DSA one — the
    price difference IS the design, so pin it."""
    n = mcpsrv.node()
    ml = K.create("ml_payer", scheme="ML-DSA-44", overwrite=True)
    slh = K.create("slh_payer", scheme="SLH-DSA-SHAKE-128f", overwrite=True)
    for w in (ml, slh):
        call("pq_faucet", address=w["address"], amount="200")
    r_ml = call("pq_transfer", wallet="ml_payer", to=slh["address"],
                amount="1")
    r_slh = call("pq_transfer", wallet="slh_payer", to=ml["address"],
                 amount="1")
    g_ml, g_slh = r_ml["receipt"]["gas"], r_slh["receipt"]["gas"]
    expected_delta = (K.witness_bytes("SLH-DSA-SHAKE-128f")
                      - K.witness_bytes("ML-DSA-44")) * S.GAS_WITNESS_BYTE
    assert g_slh - g_ml == expected_delta
    assert n.state.accounts[slh["address"]]["scheme"] == "SLH-DSA-SHAKE-128f"


def test_quote_can_price_a_witness_per_scheme():
    n = mcpsrv.node()
    q_default = n.state.quote("k", "00" * 32, "hash", 3600)
    q_slh = n.state.quote("k", "00" * 32, "hash", 3600,
                          witness_bytes=K.witness_bytes("SLH-DSA-SHAKE-128f"))
    assert q_slh["gas"]["witness"] > q_default["gas"]["witness"]


# ── the quantum gate, end to end ──────────────────────────────────


def test_classical_witness_refused_at_the_mempool():
    """A hand-built, correctly signed ed25519 transaction is turned away
    with the real reason, not a generic bad-signature error."""
    n = mcpsrv.node()
    ed = algos.get("ed25519")
    pk, sk = ed.keygen(b"\x01" * 32)
    addr = K.address(pk, "ed25519")
    body = {"chain_id": n.chain_id, "kind": "xfer", "from": addr,
            "nonce": 0, "max_fee": 10 ** 6, "tip": 0,
            "to": addr, "amount": 1}
    sig = ed.sign(sk, S.canonical(body), K.TX_CONTEXT)
    tx = {"body": body, "sig": sig.hex(), "scheme": "ed25519",
          "pk": pk.hex()}
    with pytest.raises(StateError) as e:
        n.submit(tx)
    assert e.value.code == "not_quantum_safe"


def test_open_gate_devnet_then_audit_flags_it(monkeypatch, tmp_path):
    """On a throwaway node with POSTQUANT_ALLOW_CLASSICAL: the ed25519
    transaction gets in. Close the gate and the full audit names that
    witness — the log does not forget what signed it."""
    monkeypatch.setattr(algos, "ALLOW_CLASSICAL", True)
    n = C.Node(chain_id="gate-test", data_dir=str(tmp_path))
    K.create("curveball", scheme="ed25519", overwrite=True)
    w = K.get("curveball")
    treasury = K.get(n.genesis["validator_name"])
    n.send(treasury, "xfer", to=w["address"], amount=50 * S.PQ)
    n.produce()
    out = n.send(w, "xfer", to=treasury["address"], amount=1 * S.PQ)
    assert out["queued"]
    n.produce()
    assert n.verify(signatures=True)["ok"]

    monkeypatch.setattr(algos, "ALLOW_CLASSICAL", False)
    v = n.verify(signatures=True)
    assert not v["ok"]
    assert any("witness" in p for p in v["problems"])
    K.remove("curveball")


# ── plugins ───────────────────────────────────────────────────────


def test_dropping_a_file_adds_a_key_type():
    """The extension story itself: a toy scheme written into the node-local
    plugin dir is a first-class key type after one load_plugins()."""
    plugin_dir = algos.PLUGIN_DIRS[1]
    assert plugin_dir.startswith(os.environ["POSTQUANT_DATA_DIR"])
    os.makedirs(plugin_dir, exist_ok=True)
    path = os.path.join(plugin_dir, "toy.py")
    with open(path, "w") as f:
        f.write('''
import hashlib

def keygen(seed):
    pk = hashlib.sha3_256(b"toy" + seed).digest()
    return pk, pk                    # symmetric: a TOY, not a cryptosystem

def sign(sk, msg, ctx=b""):
    return hashlib.sha3_256(sk + bytes([len(ctx)]) + ctx + msg).digest()

def verify(pk, msg, sig, ctx=b""):
    return sign(pk, msg, ctx) == sig

def register(algos):
    algos.register(algos.SigAlgo(
        "toy-mac", keygen, sign, verify,
        sizes={"pk": 32, "sig": 32, "seed": 32},
        family="toy", quantum_safe=True,
        note="test fixture — a MAC wearing a signature costume"))
''')
    try:
        algos.load_plugins()
        a = algos.get("toy-mac")
        assert a.origin == path
        assert algos.allowed("toy-mac")
        # ...and the whole wallet/witness path works through it.
        K.create("toybox", scheme="toy-mac", overwrite=True)
        w = K.get("toybox")
        tx = K.sign_tx(w, {"kind": "set", "from": w["address"]})
        assert K.verify_tx(tx)
    finally:
        os.remove(path)
        algos.REGISTRY.pop("toy-mac", None)
        K.remove("toybox")


def test_broken_plugin_is_reported_not_fatal():
    plugin_dir = algos.PLUGIN_DIRS[1]
    os.makedirs(plugin_dir, exist_ok=True)
    path = os.path.join(plugin_dir, "broken.py")
    with open(path, "w") as f:
        f.write("raise RuntimeError('deliberately broken plugin')\n")
    try:
        algos.load_plugins()
        errs = [e for e in algos.PLUGIN_ERRORS if e["plugin"] == path]
        assert errs and "deliberately broken" in errs[0]["error"]
        assert algos.maybe("ML-DSA-44") is not None   # node survived
    finally:
        os.remove(path)
        algos.PLUGIN_ERRORS[:] = [e for e in algos.PLUGIN_ERRORS
                                  if e["plugin"] != path]
