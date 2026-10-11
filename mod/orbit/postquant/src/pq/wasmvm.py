"""The wasm layer: every witness is checked by the wasm that IS its key type.

The registry (pq/algos.py) says which algorithms exist; this module makes
verification run through a compiled wasm module per algorithm rather than
through anyone's python. The point is constitutional, not stylistic: a key
type on this chain is DEFINED by a wasm blob and its SHA3-256, so what it
means for a witness to be valid is pinned to exact bytes that every node can
hash, ship, and re-run — and adding a future key type means shipping a new
blob with a declared hash, not editing the node.

    bind(algos)     wrap each registered SigAlgo whose key type has a wasm
                    verifier so algo.verify() dispatches into the wasm.
                    Idempotent; never raises. Called by pq/algos.d/zz_wasm.py
                    through the plugin loader, so it runs after every other
                    plugin has registered.
    verify(...)     one witness through one wasm module, hash-enforced.
    status()        cheap, never raises — what pq_algos merges into the
                    catalog so the console can show what is enforced by what.

WHERE THE BLOBS COME FROM
    Builtins live in pq/wasm/ with their hashes committed in
    pq/wasm/manifest.json. A plugin key type declares its own by stamping the
    SigAlgo it registers:

        algo.wasm = {"file": "/abs/path/to/verifier.wasm",
                     "sha3_256": "<hex the author commits to>"}

    Both the python side and the node host re-hash the file and refuse to run
    bytes that do not match the declared hash. Two doors, same lock.

FAIL CLOSED
    If node is missing, the host dies, the blob is absent or its hash is
    wrong, verification returns False — a witness that cannot be checked by
    the algorithm's own wasm is not a valid witness. The python reference
    implementations still exist for keygen and signing (secrets never enter
    a wasm module) and for the parity tests in wasm-src/, but consensus
    acceptance runs on the wasm alone. POSTQUANT_WASM=off is the explicit,
    logged escape hatch for an airgapped dev box with no node.

THE ABI (pq1)
    exports: memory, pq_reset(), pq_alloc(n)->ptr,
             pq_verify(pk,pk_len, msg,msg_len, sig,sig_len, ctx,ctx_len)->i32
    The FIPS m' context wrapping happens inside the module — the host hands
    raw bytes to the algorithm and gets a verdict, so the wasm is the whole
    algorithm, not a fragment of one.
"""

from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
import threading
import time

# Dual-import guard. This file is reachable both as `pq.wasmvm` (mcp/api)
# and as bare `wasmvm` (the zz_wasm plugin, scripts run from pq/). Two module
# instances would mean two hosts and a status() blind to the bindings the
# other instance made — so whichever name loads second gets the first one's
# object. Same trap as [[mod-import-shadowing]], same cure.
_other = "pq.wasmvm" if __name__ == "wasmvm" else "wasmvm"
if _other in sys.modules:
    sys.modules[__name__] = sys.modules[_other]
else:
    sys.modules.setdefault(_other, sys.modules[__name__])

HERE = os.path.dirname(os.path.abspath(__file__))
WASM_DIR = os.path.join(HERE, "wasm")
HOST_JS = os.path.join(HERE, "wasm-src", "host.mjs")
MANIFEST_FILE = os.path.join(WASM_DIR, "manifest.json")

ENABLED = os.environ.get("POSTQUANT_WASM", "on").lower() not in (
    "off", "0", "false", "no")
NODE = os.environ.get("POSTQUANT_WASM_NODE") or shutil.which("node")
CALL_TIMEOUT = float(os.environ.get("POSTQUANT_WASM_TIMEOUT", 30))

_LAST_ERROR = None


def manifest():
    """The builtin blobs, name -> {file(abs), sha3_256, bytes, abi}."""
    try:
        with open(MANIFEST_FILE) as f:
            data = json.load(f)
        out = {}
        for name, e in (data.get("algorithms") or {}).items():
            out[name] = {**e, "file": os.path.join(WASM_DIR, e["file"])}
        return out
    except Exception:                                     # noqa: BLE001
        return {}


# ── the host process ──────────────────────────────────────────────


class _Host:
    """One node process, JSON lines, restarted if it dies. All calls are
    serialised under a lock — verification is not the bottleneck (a call is
    ~1ms) and one host means one wasm cache."""

    def __init__(self):
        self.lock = threading.Lock()
        self.proc = None
        self.n = 0

    def _ensure(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        if not NODE:
            raise RuntimeError(
                "no node runtime for the wasm verifiers — install node, or "
                "set POSTQUANT_WASM_NODE, or explicitly opt out with "
                "POSTQUANT_WASM=off")
        self.proc = subprocess.Popen(
            [NODE, HOST_JS], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True)

    def call(self, req):
        with self.lock:
            self._ensure()
            self.n += 1
            req = {**req, "id": self.n}
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
                ready, _, _ = select.select([self.proc.stdout],
                                            [], [], CALL_TIMEOUT)
                if not ready:
                    raise RuntimeError(
                        f"wasm host gave no answer in {CALL_TIMEOUT}s")
                line = self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("wasm host closed its pipe")
                return json.loads(line)
            except Exception:
                try:
                    self.proc.kill()
                finally:
                    self.proc = None
                raise


_HOST = _Host()

# name -> resolved spec for every bound algorithm
_BOUND: dict[str, dict] = {}
_ALGOS = None                       # the registry module bind() was handed
_HASH_CACHE: dict[str, tuple[float, str]] = {}   # path -> (mtime, sha3)


def _file_hash(path):
    mtime = os.stat(path).st_mtime
    cached = _HASH_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, "rb") as f:
        digest = hashlib.sha3_256(f.read()).hexdigest()
    _HASH_CACHE[path] = (mtime, digest)
    return digest


def verify(name, pk, msg, sig, ctx=b"", spec=None):
    """One witness through the wasm registered for its key type.

    True only when the module the registry committed to, hash-checked on
    both sides of the process boundary, says so. Any failure to run the
    check is False — and remembered, so status() can say why.
    """
    global _LAST_ERROR
    spec = spec or _BOUND.get(name) or manifest().get(name)
    if spec is None:
        _LAST_ERROR = f"{name}: no wasm verifier registered"
        return False
    try:
        path = spec["file"]
        declared = spec["sha3_256"].lower()
        if _file_hash(path) != declared:
            raise RuntimeError(
                f"{os.path.basename(path)} does not hash to the "
                f"{declared[:16]}… the registry committed to")
        resp = _HOST.call({"wasm": path, "sha3": declared,
                           "pk": bytes(pk).hex(), "msg": bytes(msg).hex(),
                           "sig": bytes(sig).hex(), "ctx": bytes(ctx).hex()})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error") or "wasm host error")
        return bool(resp.get("valid"))
    except Exception as e:                                # noqa: BLE001
        _LAST_ERROR = f"{name}: {e}"
        print(f"postquant wasmvm: verification unavailable, witness refused "
              f"— {_LAST_ERROR}", file=sys.stderr, flush=True)
        return False


# ── binding the registry ──────────────────────────────────────────


def bind(algos):
    """Wrap every registered SigAlgo that has a wasm verifier.

    After this, algos.get(name).verify IS the wasm dispatch — every path the
    chain checks a witness through (mempool, replay, block seals) runs the
    algorithm's own module without knowing wasm exists. Algorithms without a
    blob keep their python verify and show up as unenforced in status().
    Idempotent, and never raises: a problem here must not take the node down
    at import, it must refuse witnesses at verify time, visibly.
    """
    if not ENABLED:
        return _BOUND
    global _ALGOS
    _ALGOS = algos
    try:
        built_in = manifest()
        for name, algo in list(algos.REGISTRY.items()):
            if getattr(algo, "wasm_enforced", False):
                continue
            spec = getattr(algo, "wasm", None) or built_in.get(name)
            if not spec or not spec.get("file") or not spec.get("sha3_256"):
                continue
            spec = {"file": os.path.abspath(spec["file"]),
                    "sha3_256": spec["sha3_256"].lower(),
                    "abi": spec.get("abi", "pq1")}
            _BOUND[name] = spec

            def wasm_verify(pk, msg, sig, ctx=b"", _n=name, _s=spec):
                return verify(_n, pk, msg, sig, ctx, spec=_s)

            algo.verify = wasm_verify
            algo.wasm = spec
            algo.wasm_enforced = True
    except Exception as e:                                # noqa: BLE001
        global _LAST_ERROR
        _LAST_ERROR = f"bind: {e}"
    return _BOUND


def status():
    """What is enforced by what. Cheap — no node is spawned, no file hashed
    beyond the mtime cache — and it never raises; pq_algos merges this into
    the catalog verbatim."""
    try:
        host_up = _HOST.proc is not None and _HOST.proc.poll() is None
        algorithms = {}
        for name, spec in sorted(_BOUND.items()):
            algorithms[name] = {
                "file": os.path.basename(spec["file"]),
                "sha3_256": spec["sha3_256"],
                "abi": spec.get("abi", "pq1"),
                "enforced": True,
            }
        return {
            "what": ("witness verification runs inside the wasm module "
                     "registered for the key type; the blob's SHA3-256 is "
                     "the commitment and a witness that cannot be checked "
                     "by it is refused"),
            "enabled": ENABLED,
            "engine": {
                "runtime": NODE or None,
                "host": os.path.relpath(HOST_JS, os.path.dirname(HERE)),
                "up": host_up,
                "calls": _HOST.n,
                "ok": bool(ENABLED and NODE),
            },
            "algorithms": algorithms,
            "last_error": _LAST_ERROR,
            "amend": ("a plugin registers a future key type by stamping its "
                      "SigAlgo with wasm={file, sha3_256} — the blob it "
                      "ships is then the algorithm, hash-enforced on every "
                      "witness"),
        }
    except Exception as e:                                # noqa: BLE001
        return {"enabled": ENABLED, "error": f"{type(e).__name__}: {e}",
                "algorithms": {}}


def selfcheck():
    """One real signature through every bound module — the smoke test the
    tests and the deploy gate call. Slowish (a python SLH-DSA sign is ~1s);
    never part of a request path."""
    if _ALGOS is None:
        raise RuntimeError("nothing bound yet — import the registry first")
    out = {}
    for name in sorted(_BOUND):
        algo = _ALGOS.get(name)
        t0 = time.time()
        seed = hashlib.sha3_256(b"postquant/wasm-selfcheck" +
                                name.encode()).digest()
        pk, sk = algo.keygen(seed)
        msg = b"the wasm is the algorithm"
        sig = algo.sign(sk, msg, ctx=b"selfcheck")
        good = algo.verify(pk, msg, sig, ctx=b"selfcheck")
        bad = algo.verify(pk, msg + b"?", sig, ctx=b"selfcheck")
        out[name] = {"ok": bool(good and not bad),
                     "ms": int((time.time() - t0) * 1000)}
    return out
