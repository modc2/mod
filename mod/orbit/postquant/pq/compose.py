"""Composite key types: the builder behind pq_keytype and the console DAG.

A composite is a key type made out of key types already in the registry.
Because every algorithm here — builtin, plugin or composite — is the same
four functions (keygen/sign/verify + sizes), a composite is just those four
functions written over its parts, and it registers like anything else: it
gets an address domain, a complexity card, a witness-gas price, and the gate
judges it like any plugin (the entropy probe runs against the composed
keygen, not against a claim).

Two ways to combine, chosen because their security stories are opposite:

    all   hybrid AND — keygen derives one domain-separated sub-seed per
          part, the public key and the signature are the parts' own,
          concatenated, and verify demands every part verify. An attacker
          must break EVERY part: best classical attack = the strongest
          part's, quantum-safe if ANY part is. This is the classic hybrid
          (ML-DSA + ed25519 survives both a lattice break and a curve
          break — it falls only to both).

    any   1-of-n OR — same keys, but a signature is one byte of part index
          plus that single part's signature, and verify checks only the
          part named. An attacker needs only the WEAKEST part: best
          classical attack = min over parts, quantum-safe only if ALL
          parts are. Useful as an escape hatch (sign with whichever part
          still works / is cheapest) and honest about what that costs.

Parts may themselves be composites, so the registry forms a DAG — acyclic
by construction, because a part must already exist under a name that can
never be rebound (names are hashed into addresses).

The store: every composite built on this node persists as its spec in
<data dir>/keytypes.json and is re-registered at import, after the plugin
dirs load (a part may be a plugin scheme). Deleting one is refused while
anything depends on it — another composite, or an on-chain account whose
witnesses a replay audit would no longer be able to judge.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

try:
    # Same rule as algos.py: one module instance per module, whatever the
    # door — a bare import here would mint second copies with their own
    # registry and verdict cache.
    from . import algos                                          # noqa: E402
    from . import complexity                                     # noqa: E402
except ImportError:                                              # run as a script
    import algos                                                 # noqa: E402
    import complexity                                            # noqa: E402

DATA_DIR = os.path.expanduser(
    os.environ.get("POSTQUANT_DATA_DIR", "~/.mod/postquant"))
STORE_FILE = os.path.join(DATA_DIR, "keytypes.json")

OPS = ("all", "any")
MAX_PARTS = 6
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{2,47}$")

STORE_ERRORS: list[dict] = []    # specs that failed to re-register at load


class ComposeError(ValueError):
    pass


def _subseed(name: str, index: int, part_name: str, seed: bytes) -> bytes:
    """One 32-byte sub-seed per part, domain-separated by the composite's
    name, the slot and the part — so no part key is ever shared between two
    composites, two slots, or with a bare wallet under the part scheme."""
    return hashlib.shake_256(
        b"pq-compose/v1\x00" + name.encode() + b"\x00" + bytes([index])
        + part_name.encode() + b"\x00" + seed).digest(32)


def _validate(name, op, parts):
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ComposeError("a key type name is 3-48 chars of letters, digits "
                           "and _.+- (it is hashed into every address)")
    if op not in OPS:
        raise ComposeError(f"op must be one of {', '.join(OPS)} — 'all' is "
                           "the hybrid AND (break every part), 'any' the "
                           "1-of-n OR (break the weakest)")
    if not isinstance(parts, (list, tuple)) or not (2 <= len(parts) <= MAX_PARTS):
        raise ComposeError(f"a composite takes 2-{MAX_PARTS} parts")
    if len(set(parts)) != len(parts):
        raise ComposeError("parts must be distinct — the same scheme twice "
                           "adds bytes, not security")
    if algos.maybe(name) is not None:
        raise ComposeError(f"{name!r} is already registered — key type names "
                           "cannot be rebound")
    missing = [p for p in parts if algos.maybe(p) is None]
    if missing:
        raise ComposeError(f"unknown part(s) {', '.join(missing)} — pq_algos "
                           "lists what this node knows")


def _build(spec) -> algos.SigAlgo:
    """A SigAlgo from a spec. Parts are resolved once, here — a composite
    holds its parts' functions directly, so deleting a part later cannot
    silently change what an existing composite verifies."""
    name, op = spec["name"], spec["op"]
    parts = [algos.get(p) for p in spec["parts"]]
    pk_sizes = [p.sizes["pk"] for p in parts]
    sig_sizes = [p.sizes["sig"] for p in parts]

    def keygen(seed):
        pks, sks = [], []
        for i, part in enumerate(parts):
            pk, sk = part.keygen(_subseed(name, i, part.name, seed))
            pks.append(pk)
            sks.append(sk)
        return b"".join(pks), sks

    def _split_pk(pk):
        out, off = [], 0
        for s in pk_sizes:
            out.append(pk[off:off + s])
            off += s
        return out

    if op == "all":
        def sign(sk, msg, ctx=b""):
            return b"".join(p.sign(k, msg, ctx) for p, k in zip(parts, sk))

        def verify(pk, msg, sig, ctx=b""):
            if len(pk) != sum(pk_sizes) or len(sig) != sum(sig_sizes):
                return False
            pks, off = _split_pk(pk), 0
            for part, s, ppk in zip(parts, sig_sizes, pks):
                if not part.verify(ppk, msg, sig[off:off + s], ctx):
                    return False
                off += s
            return True
        sig_size = sum(sig_sizes)
    else:                                                        # any
        def sign(sk, msg, ctx=b""):
            # The SigAlgo interface has no sign-time choice, so this node
            # always signs with part 0; verify accepts any valid index, so
            # a foreign signer may use whichever part it prefers.
            return bytes([0]) + parts[0].sign(sk[0], msg, ctx)

        def verify(pk, msg, sig, ctx=b""):
            if len(pk) != sum(pk_sizes) or not sig:
                return False
            i = sig[0]
            if i >= len(parts) or len(sig) - 1 != sig_sizes[i]:
                return False
            return parts[i].verify(_split_pk(pk)[i], msg, sig[1:], ctx)
        sig_size = 1 + sig_sizes[0]

    # The security card is arithmetic over the parts' own analytic cards:
    # 'all' must be broken everywhere (max classical, quantum-safe if any
    # part is), 'any' falls at its weakest link (min classical, quantum-safe
    # only if every part is). Costs are in bits of best-known attack.
    part_c = [complexity.analytic(p)["best_classical"]["bits"] for p in parts]
    part_q = [complexity.analytic(p)["quantum"]["bits"] for p in parts]
    if op == "all":
        classical = max(part_c)
        qsafe = any(p.quantum_safe for p in parts)
        qbits = max(part_q)
    else:
        classical = min(part_c)
        qsafe = all(p.quantum_safe for p in parts)
        qbits = min(part_q)
    seed_bits = 32 * 8
    story = {"all": "hybrid AND over %s — every part signs and must verify; "
                    "falls only if every part falls",
             "any": "1-of-n OR over %s — one part's signature suffices; "
                    "falls when the weakest part falls"}[op] \
        % " + ".join(p.name for p in parts)

    algo = algos.SigAlgo(
        name, keygen, sign, verify,
        sizes={"pk": sum(pk_sizes), "sig": sig_size, "seed": 32},
        family="composite", standard="", basis=story,
        quantum_safe=qsafe,
        classical_bits=classical if classical < seed_bits else None,
        quantum_bits=qbits if qsafe else None,
        note=spec.get("note", ""))
    algo.composite = {"op": op, "parts": list(spec["parts"])}
    return algo


# ── the store ─────────────────────────────────────────────────────


def _read_store():
    try:
        with open(STORE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data.get("keytypes", []) if isinstance(data, dict) else []


def _write_store(entries):
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    tmp = STORE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"keytypes": entries}, f, indent=2)
    os.replace(tmp, STORE_FILE)


def specs():
    """Every stored composite spec, in creation (= dependency) order."""
    return _read_store()


def load_store():
    """Re-register every stored composite. Called from algos.py AFTER the
    plugin dirs load, because a part may be a plugin scheme. Creation order
    is dependency order, so nested composites resolve in one pass. A spec
    whose parts went missing (a removed plugin) is reported and skipped —
    like a broken plugin, it must not take the node down."""
    for spec in _read_store():
        if algos.maybe(spec.get("name", "")) is not None:
            continue                       # already live (a re-load)
        try:
            _validate(spec["name"], spec["op"], spec["parts"])
            a = algos.register(_build(spec))
            a.origin = "composite"
        except Exception as e:                              # noqa: BLE001
            STORE_ERRORS.append({"keytype": spec.get("name"),
                                 "error": f"{type(e).__name__}: {e}"})


def create(name, op, parts, note=""):
    """Build, register and persist a new composite key type. The gate still
    decides acceptance separately — origin 'composite' means the entropy
    probe runs against the composed keygen before it may witness."""
    parts = list(parts)
    _validate(name, op, parts)
    spec = {"name": name, "op": op, "parts": parts,
            "note": str(note or ""), "created": int(time.time())}
    algo = algos.register(_build(spec))
    algo.origin = "composite"
    _write_store(_read_store() + [spec])
    return algo


def dependents(name):
    """Composites that use `name` as a part — what a delete would strand."""
    return sorted(n for n, a in algos.REGISTRY.items()
                  if name in getattr(a, "composite", {}).get("parts", ()))


def delete(name):
    """Remove a composite from the registry and the store. Refused for
    builtins and plugins (they are files, not store entries) and while
    another composite depends on it. The caller (mcp) additionally refuses
    while any on-chain account witnesses under it — a replay audit must
    always be able to re-judge every historic witness."""
    algo = algos.maybe(name)
    if algo is None:
        raise ComposeError(f"unknown key type {name!r}")
    if getattr(algo, "composite", None) is None:
        raise ComposeError(f"{name} is {algo.origin} — only composites built "
                           "here can be deleted")
    deps = dependents(name)
    if deps:
        raise ComposeError(f"{name} is a part of {', '.join(deps)} — delete "
                           "those first")
    algos.REGISTRY.pop(name)
    complexity.clear_cache(name)
    _write_store([s for s in _read_store() if s.get("name") != name])
    return {"deleted": name, "op": algo.composite["op"],
            "parts": algo.composite["parts"]}


# ── the DAG ───────────────────────────────────────────────────────


def _depth(name, seen=()):
    a = algos.maybe(name)
    comp = getattr(a, "composite", None) if a else None
    if not comp or name in seen:
        return 0
    return 1 + max(_depth(p, seen + (name,)) for p in comp["parts"])


def dag():
    """The registry as a graph: every key type a node, every composite an
    edge from each part to it. What the console's BUILDER tab draws."""
    nodes, edges = [], []
    for name, a in sorted(algos.REGISTRY.items()):
        comp = getattr(a, "composite", None)
        s = complexity.summary(a)
        nodes.append({
            "name": name, "family": a.family, "origin": a.origin,
            "quantum_safe": a.quantum_safe,
            "accepted": algos.refusal(name) is None,
            "composite": comp, "depth": _depth(name),
            "pk_bytes": a.sizes.get("pk"), "sig_bytes": a.sizes.get("sig"),
            "classical_bits": s["classical_bits"],
            "quantum": s["quantum"], "note": a.note,
        })
        if comp:
            edges.extend({"from": p, "to": name, "op": comp["op"]}
                         for p in comp["parts"])
    return {"nodes": nodes, "edges": edges, "ops": list(OPS),
            "store": STORE_FILE, "errors": STORE_ERRORS}


# ── the proof battery ─────────────────────────────────────────────


def _safe_verify(algo, pk, msg, sig, ctx):
    """A verifier that rejects by exception still rejects."""
    try:
        return bool(algo.verify(pk, msg, sig, ctx))
    except Exception:                                       # noqa: BLE001
        return False


def proofs(algo):
    """Everything this node can test about one key type, run now: the
    signature must round-trip, and must stop verifying the moment the
    message, the signature bytes, the context or the key changes; for a
    composite, every part must be load-bearing. Refutation-only, like the
    entropy probe it also runs — a green board means no failure was found,
    never 'secure'."""
    name = algo.name
    seed1 = hashlib.sha3_256(b"pq-proofs-1\x00" + name.encode()).digest()
    seed2 = hashlib.sha3_256(b"pq-proofs-2\x00" + name.encode()).digest()
    msg, ctx = b"keytype-proof\x00" + name.encode(), b"pq-proofs"
    out = []

    def proof(pname, ok, detail):
        out.append({"proof": pname, "ok": bool(ok), "detail": detail})

    t0 = time.time()
    pk, sk = algo.keygen(seed1)
    t_keygen = time.time() - t0
    pk2, _ = algo.keygen(seed2)
    t0 = time.time()
    sig = algo.sign(sk, msg, ctx)
    t_sign = time.time() - t0
    t0 = time.time()
    ok_rt = _safe_verify(algo, pk, msg, sig, ctx)
    t_verify = time.time() - t0

    proof("roundtrip", ok_rt, "sign then verify, same message and context")
    proof("tampered_message", not _safe_verify(algo, pk, msg + b"!", sig, ctx),
          "one byte appended to the message must kill the signature")
    flipped = bytearray(sig)
    flipped[len(flipped) // 2] ^= 0x01
    proof("tampered_signature",
          not _safe_verify(algo, pk, msg, bytes(flipped), ctx),
          "one bit flipped mid-signature must be rejected")
    proof("truncated_signature",
          not _safe_verify(algo, pk, msg, sig[:-1], ctx),
          "a signature one byte short must be rejected, not crash")
    proof("wrong_context", not _safe_verify(algo, pk, msg, sig, ctx + b"x"),
          "the context string must be bound — else witnesses replay across "
          "domains")
    proof("wrong_key", not _safe_verify(algo, pk2, msg, sig, ctx),
          "another seed's public key must not verify this signature")

    comp = getattr(algo, "composite", None)
    if comp and comp["op"] == "all":
        # Every part must be load-bearing: corrupt exactly one part's region
        # of the signature and the whole thing must die.
        off = 0
        for pname in comp["parts"]:
            s = algos.get(pname).sizes["sig"]
            cut = bytearray(sig)
            cut[off + s // 2] ^= 0x01
            proof(f"part_load_bearing:{pname}",
                  not _safe_verify(algo, pk, msg, bytes(cut), ctx),
                  "corrupting only this part's signature bytes must fail "
                  "the composite — an 'all' that ignores a part is an 'any'")
            off += s
    if comp and comp["op"] == "any":
        relabel = bytes([1 % len(comp["parts"])]) + sig[1:]
        proof("foreign_index_rejected",
              not _safe_verify(algo, pk, msg, relabel, ctx),
              "part 0's signature relabelled as part 1 must not verify")

    p = complexity.probe(algo, fresh=True)
    proof("entropy_probe", p["ok"],
          "; ".join(p["failures"]) if p["failures"] else
          f"no entropy loss detected over {p['keygens']} keygens"
          + (f", avalanche {p['avalanche']}" if p.get("avalanche") else ""))

    failed = [x["proof"] for x in out if not x["ok"]]
    return {
        "scheme": name, "ok": not failed, "proofs": out,
        "passed": len(out) - len(failed), "failed": failed,
        "timings_ms": {"keygen": round(t_keygen * 1000, 1),
                       "sign": round(t_sign * 1000, 1),
                       "verify": round(t_verify * 1000, 1)},
        "sizes": {"pk": len(pk), "sig": len(sig)},
        "probe": p,
        "verdict": complexity.verdict(algo),
        "analytic": complexity.analytic(algo),
        "note": "refutation-only: green means no failure was found here, "
                "never 'secure'",
    }
