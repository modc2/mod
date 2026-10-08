"""Brute-force complexity: what breaking a key type costs, said out loud.

The chain no longer answers "is it quantum-safe?" with yes or no alone — any
key type may witness here, classical curves included, provided the node can
say what an attacker pays. This module produces that answer, in two halves:

ANALYTIC — the bound, assuming maximal entropy
    Every wallet seed on this chain is uniformly random over its full seed
    space, so the generic attack on ANY scheme in the four-function dynamic
    is the same: enumerate seeds, run keygen, compare public keys. For a
    k-bit seed that is 2^k keygen trials classically and 2^(k/2) with Grover.
    A scheme may declare a cheaper structural attack (classical_bits — e.g.
    Pollard rho takes ed25519 to ~2^126) and the quantum column is decided by
    what the scheme itself declared: quantum_safe=False on this chain MEANS
    "a quantum computer solves my hard problem in polynomial time" (Shor and
    its relatives), so the quantum cost is reported as polynomial, not as a
    Grover bound it will never enjoy.

EMPIRICAL — the probe, testing that "maximal entropy" is earned
    2^256 is the bound only if keygen actually consumes the seed. The probe
    can never certify security, but it can refute the entropy assumption:
    keygen must be deterministic, distinct seeds must give distinct keys,
    flipping a bit in any region of the seed must change the public key
    (a keygen that reads seed[:4] has a 32-bit keyspace wearing a 256-bit
    costume), and sign/verify must round-trip while refusing a tampered
    message. A plugin that fails the probe is refused at the gate with the
    measurement attached.

THE FLOOR
    allowed() in pq/algos.py asks verdict() here: best-known CLASSICAL cost
    must be at least MIN_BITS (128 by default — below that, brute force is a
    budget, not a bound), and plugin-origin schemes must pass the probe.
    The built-in FIPS families are exempt from the probe at the gate (the
    test suite holds them to conformance vectors, which is stronger) but can
    be probed on demand through pq_complexity. Quantum weakness does not
    close the gate by default — it is priced and printed instead — unless
    the operator sets POSTQUANT_REQUIRE_PQ=1.
"""

from __future__ import annotations

import os
import random

# Below this many bits of best-known classical attack, a key is not a lock.
MIN_BITS = int(os.environ.get("POSTQUANT_MIN_BITS", "128"))

# Probe budget: keygens are pure python (9-45ms per scheme here), so the
# whole probe is well under a second and the verdict is cached per algorithm.
DISTINCT_SAMPLES = 6
SENSITIVITY_SPOTS = 8

_VERDICTS: dict[str, dict] = {}     # algo name -> cached verdict
_PROBES: dict[str, dict] = {}       # algo name -> cached probe result


def _pow2(bits):
    return f"2^{bits:g}" if bits == int(bits) else f"2^{bits:.1f}"


# ── the analytic half ─────────────────────────────────────────────


def analytic(algo) -> dict:
    """The cost bound, assuming the seed is maximal-entropy — which is what
    the probe below exists to check."""
    seed_bits = int(algo.sizes.get("seed", 32)) * 8
    declared = getattr(algo, "classical_bits", None)
    classical = min(seed_bits, declared) if declared else seed_bits
    qbits = getattr(algo, "quantum_bits", None)
    if not algo.quantum_safe:
        quantum = {"attack": "structural — the scheme declared its hard "
                             "problem falls to a quantum computer (Shor-class)",
                   "cost": "polynomial", "bits": 0, "polynomial": True}
    else:
        bits = qbits if qbits is not None else classical / 2
        quantum = {"attack": "Grover search over the keyspace — no known "
                             "structural shortcut" if qbits is None
                   else "best declared quantum attack",
                   "cost": _pow2(bits), "bits": bits, "polynomial": False}
    return {
        "assumes": "maximal entropy — a uniformly random seed over its full "
                   f"{seed_bits}-bit space (the probe tests this)",
        "seed_bits": seed_bits,
        "brute_force_classical": {
            "attack": "enumerate seeds, run keygen, compare public keys — "
                      "the generic attack on every scheme in this dynamic",
            "cost": _pow2(seed_bits), "bits": seed_bits},
        "best_classical": {
            "cost": _pow2(classical), "bits": classical,
            "attack": ("declared structural attack (classical_bits)"
                       if declared and declared < seed_bits
                       else "none cheaper than brute force is declared")},
        "quantum": quantum,
        "floor_bits": MIN_BITS,
        "meets_floor": classical >= MIN_BITS,
    }


# ── the empirical half ────────────────────────────────────────────


def probe(algo, *, fresh=False) -> dict:
    """Refutation-only testing of the maximal-entropy assumption. A pass
    means "no entropy loss detected", never "secure"."""
    if not fresh and algo.name in _PROBES:
        return _PROBES[algo.name]
    rng = random.Random(0xC0FFEE ^ hash(algo.name))
    seed_len = int(algo.sizes.get("seed", 32))
    failures = []
    avalanche = None
    dead_spots = []
    try:
        base_seed = bytes(rng.randrange(256) for _ in range(seed_len))
        pk1, sk1 = algo.keygen(base_seed)
        pk2, _ = algo.keygen(base_seed)
        if pk1 != pk2:
            failures.append("keygen is not deterministic — the same seed "
                            "gave two public keys")
        # Distinct seeds -> distinct keys. A collision in 6 samples means
        # the keyspace is a puddle, not an ocean.
        pks = {pk1}
        for _ in range(DISTINCT_SAMPLES):
            s = bytes(rng.randrange(256) for _ in range(seed_len))
            pk, _ = algo.keygen(s)
            if pk in pks:
                failures.append("distinct seeds produced the same public "
                                "key — the keyspace is far smaller than the "
                                "seed space")
                break
            pks.add(pk)
        # Sensitivity: one bit flipped anywhere in the seed must move the
        # key. Spots are spread across the whole seed so a keygen that
        # ignores a region is caught in that region.
        flips = 0
        total_dist = 0.0
        for i in range(SENSITIVITY_SPOTS):
            pos = (i * seed_len) // SENSITIVITY_SPOTS
            bit = rng.randrange(8)
            flipped = bytearray(base_seed)
            flipped[pos] ^= 1 << bit
            pk, _ = algo.keygen(bytes(flipped))
            if pk == pk1:
                dead_spots.append(pos)
            else:
                flips += 1
                diff = int.from_bytes(pk1, "big") ^ int.from_bytes(pk, "big")
                total_dist += bin(diff).count("1") / (len(pk1) * 8)
        if dead_spots:
            failures.append(
                f"keygen ignored seed byte(s) {dead_spots} — flipping a bit "
                "there left the public key unchanged, so the effective "
                "keyspace is smaller than the seed space")
        if flips:
            avalanche = round(total_dist / flips, 3)
        # Round-trip: the signature must verify, and must stop verifying the
        # moment the message changes. A verify() that always says yes is the
        # worst key type a chain can accept.
        msg, ctx = b"complexity-probe", b"pq-probe"
        sig = algo.sign(sk1, msg, ctx)
        if not algo.verify(pk1, msg, sig, ctx):
            failures.append("sign/verify does not round-trip")
        if algo.verify(pk1, msg + b"!", sig, ctx):
            failures.append("verify accepted a tampered message — the "
                            "signature binds nothing")
    except Exception as e:                                  # noqa: BLE001
        failures.append(f"probe crashed in the scheme's own code: "
                        f"{type(e).__name__}: {e}")
    out = {
        "ok": not failures,
        "failures": failures,
        "keygens": 1 + 1 + DISTINCT_SAMPLES + SENSITIVITY_SPOTS,
        "avalanche": avalanche,     # mean fraction of pk bits moved per
                                    # seed-bit flip; ~0.5 is healthy
        "note": "refutation-only: a pass means no entropy loss was "
                "detected, not that the scheme is secure",
    }
    _PROBES[algo.name] = out
    return out


# ── the verdict the gate asks for ─────────────────────────────────


def verdict(algo) -> dict:
    """May this key type witness, as far as complexity is concerned?
    {ok, code, reason} — cached, because the mempool asks on every tx."""
    cached = _VERDICTS.get(algo.name)
    if cached is not None:
        return cached
    a = analytic(algo)
    out = {"ok": True, "code": None, "reason": None}
    if not a["meets_floor"]:
        out = {"ok": False, "code": "insufficient_complexity",
               "reason": (f"best-known classical attack is "
                          f"{a['best_classical']['cost']} keygen-equivalents "
                          f"({a['best_classical']['bits']:g} bits) — below "
                          f"this chain's floor of 2^{MIN_BITS}")}
    elif algo.origin != "builtin":
        p = probe(algo)
        if not p["ok"]:
            out = {"ok": False, "code": "insufficient_complexity",
                   "reason": ("the entropy probe refuted the brute-force "
                              "bound: " + "; ".join(p["failures"]))}
    _VERDICTS[algo.name] = out
    return out


def summary(algo) -> dict:
    """The one-line security card the catalog carries per algorithm."""
    a = analytic(algo)
    v = verdict(algo)
    return {
        "brute_force": a["brute_force_classical"]["cost"],
        "best_classical": a["best_classical"]["cost"],
        "classical_bits": a["best_classical"]["bits"],
        "quantum": a["quantum"]["cost"] + (
            "" if a["quantum"]["polynomial"] else " (Grover)"),
        "quantum_polynomial": a["quantum"]["polynomial"],
        "floor": f"2^{MIN_BITS}",
        "meets_floor": a["meets_floor"],
        "probe": ("passed" if _PROBES.get(algo.name, {}).get("ok")
                  else "failed" if algo.name in _PROBES
                  else "exempt (builtin — held to conformance vectors "
                       "instead)" if algo.origin == "builtin"
                  else "not yet run"),
        "ok": v["ok"],
        **({"refused": v["reason"]} if not v["ok"] else {}),
    }


def profile(algo, *, run_probe=True) -> dict:
    """The full card for one key type: the analytic bound, the probe, the
    verdict. What pq_complexity returns."""
    out = {
        "name": algo.name, "family": algo.family,
        "standard": algo.standard, "basis": algo.basis,
        "quantum_safe": algo.quantum_safe,
        "origin": algo.origin,
        "analytic": analytic(algo),
    }
    if run_probe:
        out["probe"] = probe(algo)
    elif algo.name in _PROBES:
        out["probe"] = _PROBES[algo.name]
    out["verdict"] = verdict(algo)
    return out


def clear_cache(name=None):
    """Forget verdicts/probes — for tests and for load_plugins(), which may
    replace an algorithm under an existing name."""
    if name is None:
        _VERDICTS.clear()
        _PROBES.clear()
    else:
        _VERDICTS.pop(name, None)
        _PROBES.pop(name, None)
