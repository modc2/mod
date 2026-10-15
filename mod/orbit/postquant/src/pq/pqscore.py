"""The post-quantum score: one number and one word per key type or wallet.

complexity.py prices attacks in bits; this folds that into what a person
asks — "is my key safe from a quantum computer?" — SAFE or NOT SAFE, plus a
0-100 score that ranks how much margin there is:

    quantum strength   up to 60   Grover-bound bits / 128 (0 if Shor breaks it)
    independent walls  up to 30   +15 per extra hard-problem family an ALL
                                  composite forces a forger through
    standardized        10        a NIST FIPS 204/205 scheme is one of the walls
    hidden scheme       10        private wallet: no scheme, no pk on chain
    few/one-time       -20        the only wall is a few-time or one-time
                                  signature (key reuse erodes it)

SAFE means: no polynomial quantum attack is declared and the quantum bound is
at least SAFE_BITS. The score is a ranking aid, not a proof — the reasons list
says exactly what moved it.
"""

from __future__ import annotations

import re

from . import algos

SAFE_BITS = 100
_FEW = re.compile(r"\b(few|one)[- ]time\b", re.I)


def _walls(algo, seen=()):
    """The set of independent quantum-safe families a forger must break.
    ALL = union of the parts' walls; ANY / threshold k = the k-th weakest
    part's (a forger picks the easiest way in)."""
    comp = getattr(algo, "composite", None)
    if comp and algo.name not in seen:
        parts = [algos.maybe(p) for p in comp["parts"]]
        sub = [_walls(p, seen + (algo.name,)) for p in parts if p is not None]
        if comp["op"] == "all":
            out = {}
            for w in sub:
                out.update(w)
            return out
        # any = 1-of-n, threshold = k-of-n: the forger breaks the k weakest
        # parts, so what stands is the k-th weakest part's walls.
        sub.sort(key=len)
        k = int(comp.get("k", 1))
        return sub[min(k, len(sub)) - 1] if sub else {}
    if not algo.quantum_safe:
        return {}
    return {algo.family: algo}


def score_algo(name, *, hidden=False, pk_exposed=None) -> dict:
    """name may be a key type name or a SigAlgo (one not registered yet)."""
    a = name if isinstance(name, algos.SigAlgo) else algos.maybe(name)
    name = a.name if a is not None else name
    if a is None:
        return {"scheme": name, "score": 0, "safe": False,
                "verdict": "UNKNOWN", "reasons": ["unknown key type"]}
    reasons = []
    qbits = 0 if not a.quantum_safe else float(
        a.quantum_bits if a.quantum_bits is not None
        else (min(a.classical_bits or 256, 256)) / 2)
    if not a.quantum_safe:
        s = 0
        reasons.append("Shor-class: a quantum computer breaks this in "
                       "polynomial time")
        if pk_exposed:
            reasons.append("public key already on chain — harvest now, "
                           "forge later")
        elif pk_exposed is False:
            s = 10
            reasons.append("public key not yet revealed — it will be on the "
                           "first transaction")
        if hidden:
            s += 10
            reasons.append("scheme hidden behind a private wallet")
        return {"scheme": name, "score": s, "safe": False,
                "verdict": "NOT SAFE", "quantum_bits": 0, "walls": [],
                "reasons": reasons}

    s = 60 * min(qbits, 128) / 128
    reasons.append(f"quantum attack ≈ 2^{qbits:g} (+{s:.0f})")
    walls = _walls(a)
    extra = min(30, 15 * max(0, len(walls) - 1))
    if extra:
        s += extra
        reasons.append(f"{len(walls)} independent walls: "
                       f"{', '.join(sorted(walls))} (+{extra})")
    if any("FIPS" in (w.standard or "") for w in walls.values()):
        s += 10
        reasons.append("NIST-standardized scheme inside (+10)")
    if len(walls) == 1 and any(_FEW.search(w.note or "")
                               for w in walls.values()):
        s -= 20
        reasons.append("only wall is few/one-time — reusing the key "
                       "weakens it (-20)")
    if hidden:
        s += 10
        reasons.append("scheme and public key hidden (+10)")
    s = max(0, min(100, round(s)))
    safe = qbits >= SAFE_BITS
    if not safe:
        reasons.append(f"quantum bound below 2^{SAFE_BITS}")
    return {"scheme": name, "score": s, "safe": safe,
            "verdict": "SAFE" if safe else "NOT SAFE",
            "quantum_bits": qbits, "walls": sorted(walls),
            "reasons": reasons}


def score_wallet(w, pk_exposed=None, owner=True) -> dict:
    """A wallet's score. A private wallet is scored on its hidden inner key
    type plus the hiding — and only for its owner: the score, the walls and
    the reasons all narrow down which scheme is inside."""
    if w.get("scheme") == "private":
        if not owner:
            return {"scheme": "private", "score": None, "safe": None,
                    "verdict": "HIDDEN", "walls": [],
                    "reasons": ["scored for the owner only — the score "
                                "would narrow down the hidden key type"]}
        r = score_algo(w.get("inner") or "ML-DSA-44", hidden=True,
                       pk_exposed=pk_exposed)
        r["scheme"] = "private"
        return r
    return score_algo(w["scheme"], pk_exposed=pk_exposed)
