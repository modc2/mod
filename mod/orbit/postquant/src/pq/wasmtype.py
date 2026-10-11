"""Bring-your-own key types: one wasm blob, verified deterministically, or
refused.

The plugin dirs let the NODE OPERATOR add a key type as a python file —
arbitrary code, so that door is theirs alone. This module is the door for
everyone else: a key type arrives as a single wasm blob exporting the pq2 ABI
(keygen/sign/verify — see pq/wasmvm.py), and the node admits it if and only
if it can stand behind the scheme deterministically:

STRUCTURAL — the blob may import nothing
    An importless wasm module has no clock, no randomness, no filesystem and
    no host call: every export is a pure function of its inputs, so every
    node that holds the same bytes computes the same verdicts forever. The
    import section is checked here (the blob is parsed, not trusted) and
    again by the host at every call. Declared memory is capped so a blob
    cannot demand the node's RAM.

EMPIRICAL — the proof battery must pass before the name exists
    The same refutation battery composites face (pq/compose.proofs): sign/
    verify roundtrip, tampered message/signature/context/key rejected,
    entropy probe over the blob's own keygen — plus a determinism double-run
    (the same witness judged twice in two fresh instances must agree) and
    runtime budgets, so a verifier that takes seconds cannot be wedged into
    the mempool's hot path. Refutation-only, as ever: a pass means no lie
    was caught, never "secure".

THE GATE — same floor as everyone
    After the battery, pq/algos.refusal() judges the scheme like any plugin:
    the declared best-known classical attack must clear the complexity
    floor, and POSTQUANT_REQUIRE_PQ still turns classical schemes away.
    Registration is refused outright on any failure — names are hashed into
    addresses and can never be rebound, so junk must not squat them.

What registers is pinned to bytes: the blob is stored content-addressed by
its SHA3-256 in <data dir>/wasmtypes/, the spec in wasmtypes.json, and from
then on that hash IS the algorithm — keygen, sign and verify all dispatch
into it, hash-checked on every call, exactly as the builtin verifiers are.
Secrets touch the blob only on this node's side: the probe uses throwaway
seeds, and a wallet under the scheme hands its seed to the very blob its
owner chose to trust.
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
    # One module instance per module, whatever the door — same rule as
    # algos.py/compose.py: bare imports here would mint second registries.
    from . import algos                                          # noqa: E402
    from . import complexity                                     # noqa: E402
    from . import wasmvm                                         # noqa: E402
except ImportError:                                              # run as a script
    import algos                                                 # noqa: E402
    import complexity                                            # noqa: E402
    import wasmvm                                                # noqa: E402

DATA_DIR = os.path.expanduser(
    os.environ.get("POSTQUANT_DATA_DIR", "~/.mod/postquant"))
STORE_FILE = os.path.join(DATA_DIR, "wasmtypes.json")
BLOB_DIR = os.path.join(DATA_DIR, "wasmtypes")

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{2,47}$")

MAX_BLOB = int(os.environ.get("POSTQUANT_WASMTYPE_MAX_BYTES", 1 << 20))
MAX_MEMORY_PAGES = 1024            # 64MB of declared linear memory, tops
MAX_PK = 1 << 16                   # SLH-DSA-256f's 49856-byte sig fits; a
MAX_SIG = 1 << 16                  # "key type" past 64KB is a payload
MAX_SK = 1 << 20
# Budgets the admission battery enforces, in ms. verify sits on the mempool
# and replay hot paths, so it gets the tight one; keygen/sign only run for
# this node's own wallets.
VERIFY_BUDGET_MS = int(os.environ.get("POSTQUANT_WASMTYPE_VERIFY_MS", 2000))
KEYGEN_BUDGET_MS = int(os.environ.get("POSTQUANT_WASMTYPE_KEYGEN_MS", 15000))
SIGN_BUDGET_MS = int(os.environ.get("POSTQUANT_WASMTYPE_SIGN_MS", 15000))

STORE_ERRORS: list[dict] = []      # specs that failed to re-register at load

PQ2_EXPORTS = ("memory", "pq_reset", "pq_alloc",
               "pq_keygen", "pq_sign", "pq_verify")


class WasmTypeError(ValueError):
    pass


# ── reading the blob without running it ───────────────────────────


def _leb(buf, i):
    """Unsigned LEB128 at buf[i:] -> (value, next index)."""
    out = shift = 0
    while True:
        b = buf[i]
        i += 1
        out |= (b & 0x7F) << shift
        if not b & 0x80:
            return out, i
        shift += 7
        if shift > 35:
            raise WasmTypeError("malformed LEB128 in the wasm binary")


def inspect(blob: bytes) -> dict:
    """What the binary declares, read straight from its sections — imports,
    export names, linear-memory size. No instantiation: this runs before the
    node agrees to execute a single instruction of it."""
    if len(blob) < 8 or blob[:4] != b"\x00asm":
        raise WasmTypeError("not a wasm binary (bad magic)")
    if blob[4:8] != b"\x01\x00\x00\x00":
        raise WasmTypeError("unsupported wasm version")
    imports = 0
    exports: list[str] = []
    memory_pages = 0
    i = 8
    try:
        while i < len(blob):
            sec_id = blob[i]
            size, i = _leb(blob, i + 1)
            body, nxt = blob[i:i + size], i + size
            if sec_id == 2:                                  # imports
                imports, _ = _leb(body, 0)
            elif sec_id == 5:                                # memory
                n, j = _leb(body, 0)
                for _ in range(n):
                    flags, j = _leb(body, j)
                    pages, j = _leb(body, j)
                    memory_pages = max(memory_pages, pages)
                    if flags & 1:
                        _, j = _leb(body, j)                 # max — ignored
            elif sec_id == 7:                                # exports
                n, j = _leb(body, 0)
                for _ in range(n):
                    ln, j = _leb(body, j)
                    name = body[j:j + ln].decode("utf-8", "replace")
                    j += ln
                    j += 1                                   # export kind
                    _, j = _leb(body, j)                     # index
                    exports.append(name)
            i = nxt
    except (IndexError, WasmTypeError) as e:
        raise WasmTypeError(f"malformed wasm binary: {e}") from None
    return {"bytes": len(blob), "imports": imports, "exports": exports,
            "memory_pages": memory_pages}


def _admit_binary(blob: bytes) -> dict:
    """The structural half of the determinism rule — raises with the reason."""
    if len(blob) > MAX_BLOB:
        raise WasmTypeError(f"blob is {len(blob)} bytes — this node caps a "
                            f"key type at {MAX_BLOB}")
    info = inspect(blob)
    if info["imports"]:
        raise WasmTypeError(
            f"the module declares {info['imports']} import(s) — a key type "
            "must import NOTHING, because an importless module has no clock, "
            "no randomness and no host call and is therefore deterministic; "
            "compile with no WASI and no env imports")
    missing = [e for e in PQ2_EXPORTS if e not in info["exports"]]
    if missing:
        raise WasmTypeError(
            f"the module does not export {', '.join(missing)} — a key type "
            "is the pq2 ABI: memory, pq_reset, pq_alloc, "
            "pq_keygen(seed,seed_len,pk_out,sk_out), "
            "pq_sign(sk,sk_len,msg,msg_len,ctx,ctx_len,sig_out), "
            "pq_verify(pk,pk_len,msg,msg_len,sig,sig_len,ctx,ctx_len)")
    if info["memory_pages"] > MAX_MEMORY_PAGES:
        raise WasmTypeError(
            f"the module declares {info['memory_pages']} pages of linear "
            f"memory — this node caps a key type at {MAX_MEMORY_PAGES} "
            f"({MAX_MEMORY_PAGES * 64}KB)")
    return info


def _check_sizes(sizes) -> dict:
    out = {"seed": int(sizes.get("seed", 32) or 32),
           "pk": int(sizes.get("pk") or 0),
           "sk": int(sizes.get("sk") or 0),
           "sig": int(sizes.get("sig") or 0)}
    if out["seed"] != 32:
        raise WasmTypeError("seed size must be exactly 32 bytes — every "
                            "wallet on this chain feeds keygen a 32-byte "
                            "seed; expand it inside the blob (SHAKE) if the "
                            "scheme wants more")
    for k, cap in (("pk", MAX_PK), ("sig", MAX_SIG), ("sk", MAX_SK)):
        if not 1 <= out[k] <= cap:
            raise WasmTypeError(f"{k} size must be 1-{cap} bytes — declare "
                                "the exact fixed sizes the blob produces")
    return out


# ── building the SigAlgo ──────────────────────────────────────────


def _blob_path(sha3: str) -> str:
    return os.path.join(BLOB_DIR, f"{sha3}.wasm")


def _build(spec) -> algos.SigAlgo:
    """A SigAlgo whose three functions all dispatch into the blob the spec
    pinned. keygen/sign raise on failure (a wallet must hear why); verify
    fails closed through wasmvm.verify like every enforced key type."""
    name = spec["name"]
    sizes = spec["sizes"]
    wspec = {"file": _blob_path(spec["sha3_256"]),
             "sha3_256": spec["sha3_256"], "abi": "pq2", "pure": True}

    def keygen(seed):
        if len(seed) != sizes["seed"]:
            raise ValueError(f"{name} takes a {sizes['seed']}-byte seed")
        r = wasmvm.run(wspec, "keygen", seed=bytes(seed).hex(),
                       pk_bytes=sizes["pk"], sk_bytes=sizes["sk"])
        return bytes.fromhex(r["pk"]), bytes.fromhex(r["sk"])

    def sign(sk, msg, ctx=b""):
        r = wasmvm.run(wspec, "sign", sk=bytes(sk).hex(),
                       msg=bytes(msg).hex(), ctx=bytes(ctx).hex(),
                       sig_bytes=sizes["sig"])
        return bytes.fromhex(r["sig"])

    def verify(pk, msg, sig, ctx=b""):
        return wasmvm.verify(name, pk, msg, sig, ctx, spec=wspec)

    algo = algos.SigAlgo(
        name, keygen, sign, verify,
        sizes={"pk": sizes["pk"], "sig": sizes["sig"], "seed": sizes["seed"]},
        family=spec.get("family") or "wasm",
        standard="",
        basis=spec.get("basis") or "user-registered wasm key type — the blob "
                                   "is the whole claim",
        quantum_safe=bool(spec.get("quantum_safe")),
        classical_bits=spec.get("classical_bits"),
        quantum_bits=spec.get("quantum_bits"),
        note=spec.get("note", ""))
    algo.wasm = wspec
    algo.wasm_enforced = True       # verify already IS the wasm dispatch
    algo.wasmtype = {"sha3_256": spec["sha3_256"], "sizes": dict(sizes),
                     "bytes": spec.get("bytes")}
    return algo


# ── the admission battery ─────────────────────────────────────────


def admission(algo) -> dict:
    """Everything the node tests before a stranger's key type may exist:
    the full proof battery, a determinism double-run across fresh instances,
    and the runtime budgets. Returns the whole card; ok=False lists why."""
    from_compose = sys.modules.get("pq.compose") or sys.modules.get("compose")
    if from_compose is None:
        try:
            from . import compose as from_compose                # noqa: N813
        except ImportError:
            import compose as from_compose                       # noqa: N813
    battery = from_compose.proofs(algo)
    failures = list(battery["failed"])
    # Determinism across instances: the host gives every call a fresh
    # instance, so two identical verifications agreeing means the verdict is
    # a function of the inputs, not of anything a previous call left behind.
    seed = hashlib.sha3_256(b"pq-wasmtype-det\x00" + algo.name.encode()) \
        .digest()
    pk, sk = algo.keygen(seed)
    sig = algo.sign(sk, b"determinism", b"pq-wasmtype")
    runs = [algo.verify(pk, b"determinism", sig, b"pq-wasmtype")
            for _ in range(2)]
    deterministic = runs[0] is True and runs[0] == runs[1]
    if not deterministic:
        failures.append("determinism: the same witness judged twice did not "
                        "give the same true verdict")
    t = battery["timings_ms"]
    budgets = {"verify_ms": {"took": t["verify"], "budget": VERIFY_BUDGET_MS},
               "keygen_ms": {"took": t["keygen"], "budget": KEYGEN_BUDGET_MS},
               "sign_ms": {"took": t["sign"], "budget": SIGN_BUDGET_MS}}
    if t["verify"] > VERIFY_BUDGET_MS:
        failures.append(f"verify took {t['verify']}ms — over this node's "
                        f"{VERIFY_BUDGET_MS}ms budget; a slow verifier is a "
                        "mempool denial of service")
    if t["keygen"] > KEYGEN_BUDGET_MS:
        failures.append(f"keygen took {t['keygen']}ms — over the "
                        f"{KEYGEN_BUDGET_MS}ms budget")
    if t["sign"] > SIGN_BUDGET_MS:
        failures.append(f"sign took {t['sign']}ms — over the "
                        f"{SIGN_BUDGET_MS}ms budget")
    return {"ok": not failures, "failures": failures, "battery": battery,
            "determinism": deterministic, "budgets": budgets}


# ── the store ─────────────────────────────────────────────────────


def _read_store():
    try:
        with open(STORE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return data.get("wasmtypes", []) if isinstance(data, dict) else []


def _write_store(entries):
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
    tmp = STORE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"wasmtypes": entries}, f, indent=2)
    os.replace(tmp, STORE_FILE)


def _write_blob(blob: bytes, sha3: str):
    os.makedirs(BLOB_DIR, mode=0o700, exist_ok=True)
    path = _blob_path(sha3)
    if not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
    return path


def specs():
    return _read_store()


def load_store():
    """Re-register every stored wasm key type. Called from algos.py after
    the plugin dirs load and BEFORE composites (a composite may use one of
    these as a part). Admission ran at create; here the hash pin is the
    guard — a blob that changed on disk simply stops verifying."""
    for spec in _read_store():
        if algos.maybe(spec.get("name", "")) is not None:
            continue
        try:
            if not os.path.exists(_blob_path(spec["sha3_256"])):
                raise WasmTypeError("blob missing from " + BLOB_DIR)
            a = algos.register(_build(spec))
            a.origin = "wasm"
        except Exception as e:                              # noqa: BLE001
            STORE_ERRORS.append({"keytype": spec.get("name"),
                                 "error": f"{type(e).__name__}: {e}"})


# ── create / delete ───────────────────────────────────────────────


def create(name, blob, *, sizes, family="", basis="", quantum_safe=False,
           classical_bits=None, quantum_bits=None, note=""):
    """Admit one wasm key type, or refuse it with the measurement attached.

    Order matters: the binary is judged before it runs, the battery runs
    before the name persists, and any failure unwinds the registration —
    names are hashed into addresses and can never be rebound, so nothing
    may squat one.  Returns (algo, admission card).
    """
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise WasmTypeError("a key type name is 3-48 chars of letters, "
                            "digits and _.+- (it is hashed into every "
                            "address)")
    if algos.maybe(name) is not None:
        raise WasmTypeError(f"{name!r} is already registered — key type "
                            "names cannot be rebound")
    if not wasmvm.ENABLED or not wasmvm.NODE:
        raise WasmTypeError("this node cannot run wasm (POSTQUANT_WASM is "
                            "off or no node runtime) — it cannot admit a "
                            "wasm key type it cannot verify")
    blob = bytes(blob)
    _admit_binary(blob)
    checked = _check_sizes(sizes)
    sha3 = hashlib.sha3_256(blob).hexdigest()
    spec = {"name": name, "sha3_256": sha3, "bytes": len(blob),
            "sizes": checked, "family": str(family or "wasm"),
            "basis": str(basis or ""), "quantum_safe": bool(quantum_safe),
            "classical_bits": classical_bits, "quantum_bits": quantum_bits,
            "note": str(note or ""), "created": int(time.time())}
    blob_existed = os.path.exists(_blob_path(sha3))
    _write_blob(blob, sha3)
    algo = algos.register(_build(spec))
    algo.origin = "wasm"
    try:
        card = admission(algo)
        if not card["ok"]:
            raise WasmTypeError(
                "refused — deterministic verification could not stand "
                "behind this blob: " + "; ".join(card["failures"]))
        r = algos.refusal(name)
        if r is not None:
            raise WasmTypeError(f"refused by the gate: {r[1]}")
    except Exception:
        algos.REGISTRY.pop(name, None)
        complexity.clear_cache(name)
        if not blob_existed and not any(
                s.get("sha3_256") == sha3 for s in _read_store()):
            try:
                os.remove(_blob_path(sha3))
            except OSError:
                pass
        raise
    _write_store(_read_store() + [spec])
    return algo, card


def delete(name):
    """Remove a wasm key type from the registry, the store and (when no
    other spec shares the blob) the disk. Refused while a composite uses it
    as a part; the caller additionally refuses while on-chain accounts
    witness under it."""
    algo = algos.maybe(name)
    if algo is None:
        raise WasmTypeError(f"unknown key type {name!r}")
    if getattr(algo, "wasmtype", None) is None:
        raise WasmTypeError(f"{name} is {algo.origin} — only wasm key types "
                            "registered here can be deleted")
    try:
        from . import compose
    except ImportError:
        import compose
    deps = compose.dependents(name)
    if deps:
        raise WasmTypeError(f"{name} is a part of {', '.join(deps)} — "
                            "delete those first")
    sha3 = algo.wasmtype["sha3_256"]
    algos.REGISTRY.pop(name)
    complexity.clear_cache(name)
    remaining = [s for s in _read_store() if s.get("name") != name]
    _write_store(remaining)
    if not any(s.get("sha3_256") == sha3 for s in remaining):
        try:
            os.remove(_blob_path(sha3))
        except OSError:
            pass
    return {"deleted": name, "sha3_256": sha3}
