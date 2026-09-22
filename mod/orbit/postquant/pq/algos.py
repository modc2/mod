"""The signature-algorithm registry: every key type the chain can witness.

A key type on this chain is four functions and some honesty about what they
rest on:

    keygen(seed)             32 bytes of seed -> (public key, secret key)
    sign(sk, msg, ctx)       -> signature bytes
    verify(pk, msg, sig, ctx)-> bool
    quantum_safe             what a quantum adversary does to the hard problem

That is the whole dynamic — the same public/private/data/signature shape that
ecdsa, ed25519 and sr25519 follow on classical chains — and everything above
this file (wallets, witnesses, block seals, witness gas) is written against it
rather than against any one scheme. Two families ship built in, chosen to
share nothing: ML-DSA (FIPS 204, structured lattices) and SLH-DSA (FIPS 205,
nothing but SHAKE). If one assumption falls the other family still stands.

ADDING A KEY TYPE
    Drop a .py file into either plugin directory:

        pq/algos.d/                    in-tree, ships with the module
        ~/.mod/postquant/algos/        local to this node, survives updates

    The file defines one function, register(algos), and calls
    algos.register(algos.SigAlgo(...)) with its four functions. No pip, no
    build step, no registry server — the node loads it at import time and the
    wallet, the mempool and the console pick it up. pq/algos.d/ed25519.py is
    a complete worked example.

THE QUANTUM GATE
    Registering is not the same as being accepted. An algorithm declares
    quantum_safe, and the chain refuses witnesses from any that declared
    False — this is a post-quantum L1 and a curve is exactly the thing Shor's
    algorithm takes apart. The ed25519 example therefore registers, appears
    in the catalog, and is turned away at the mempool; set
    POSTQUANT_ALLOW_CLASSICAL=1 on a throwaway devnet to watch it get in.

ADDRESSES
    An address commits to (algorithm, public key): the algorithm's
    addr_domain is hashed in with the key, so the same 32 bytes arriving
    under two schemes are two different addresses and a signature can never
    be replayed across key types. The ML-DSA sets carry an empty domain —
    they predate this registry and their addresses are already on chain.
"""

from __future__ import annotations

import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import mldsa                                                    # noqa: E402
import slhdsa                                                   # noqa: E402

ALLOW_CLASSICAL = os.environ.get("POSTQUANT_ALLOW_CLASSICAL", "") \
    .lower() in ("1", "true", "yes")

PLUGIN_DIRS = (
    os.path.join(HERE, "algos.d"),
    os.path.join(os.path.expanduser(
        os.environ.get("POSTQUANT_DATA_DIR", "~/.mod/postquant")), "algos"),
)


class SigAlgo:
    """One key type. Everything the chain needs to know about it, in one
    object with no behaviour of its own — the four functions are the plugin's."""

    def __init__(self, name, keygen, sign, verify, sizes, *, family,
                 quantum_safe, standard="", basis="", addr_domain=None,
                 note=""):
        self.name = str(name)
        self.keygen = keygen          # (seed: bytes32) -> (pk, sk)
        self.sign = sign              # (sk, msg, ctx=b"") -> sig
        self.verify = verify          # (pk, msg, sig, ctx=b"") -> bool
        self.sizes = dict(sizes)      # {"pk": int, "sig": int, "seed": 32}
        self.family = family          # "ML-DSA", "SLH-DSA", "EdDSA", ...
        self.quantum_safe = bool(quantum_safe)
        self.standard = standard      # "FIPS 204", "RFC 8032", ...
        self.basis = basis            # what the security rests on
        # Hashed into the address with the public key. Defaults to the
        # algorithm name, so key types can never collide on an address.
        self.addr_domain = (self.name.encode() + b"\x00"
                            if addr_domain is None else addr_domain)
        self.note = note
        self.origin = "builtin"       # or the plugin path that registered it

    def describe(self):
        return {
            "name": self.name, "family": self.family,
            "standard": self.standard, "basis": self.basis,
            "quantum_safe": self.quantum_safe,
            "pk_bytes": self.sizes.get("pk"),
            "sig_bytes": self.sizes.get("sig"),
            "witness_bytes": (self.sizes.get("pk") or 0) +
                             (self.sizes.get("sig") or 0),
            "origin": self.origin,
            "note": self.note,
        }


REGISTRY: dict[str, SigAlgo] = {}
PLUGIN_ERRORS: list[dict] = []
_LOADED: set[str] = set()      # plugin paths already executed — load_plugins
                               # may be called again to pick up new files


def register(algo: SigAlgo, replace=False):
    if not isinstance(algo, SigAlgo):
        raise TypeError("register() takes a SigAlgo")
    if algo.name in REGISTRY and not replace:
        raise ValueError(f"{algo.name!r} is already registered — key type "
                         "names are load-bearing (they are hashed into "
                         "addresses) and cannot be quietly rebound")
    REGISTRY[algo.name] = algo
    return algo


def get(name) -> SigAlgo:
    algo = REGISTRY.get(name)
    if algo is None:
        raise ValueError(f"unknown signature algorithm {name!r} — this node "
                         f"knows {', '.join(sorted(REGISTRY))}")
    return algo


def maybe(name) -> SigAlgo | None:
    return REGISTRY.get(name)


def names(pq_only=False):
    return sorted(n for n, a in REGISTRY.items()
                  if a.quantum_safe or not pq_only)


def allowed(name) -> bool:
    """May this algorithm witness a transaction on this chain? Registered,
    and quantum-safe unless the operator explicitly opened the gate."""
    algo = REGISTRY.get(name)
    return algo is not None and (algo.quantum_safe or ALLOW_CLASSICAL)


def catalog():
    """The registry as one card — what the console and pq_algos show."""
    return {
        "algorithms": [{**a.describe(), "accepted": allowed(a.name)}
                       for _, a in sorted(REGISTRY.items())],
        "quantum_gate": ("open — POSTQUANT_ALLOW_CLASSICAL is set and "
                         "classical witnesses are accepted" if ALLOW_CLASSICAL
                         else "closed — algorithms that declared "
                              "quantum_safe=false register and appear here "
                              "but cannot witness a transaction"),
        "plugin_dirs": list(PLUGIN_DIRS),
        "plugin_errors": PLUGIN_ERRORS,
        "how_to_add": ("drop a .py into a plugin dir defining "
                       "register(algos) that calls "
                       "algos.register(algos.SigAlgo(name, keygen, sign, "
                       "verify, sizes, family=…, quantum_safe=…)) — "
                       "pq/algos.d/ed25519.py is a worked example"),
    }


# ── the built-in families ─────────────────────────────────────────

def _mldsa_algo(name, note=""):
    return SigAlgo(
        name,
        keygen=lambda seed, _n=name: mldsa.keygen_internal(seed, _n),
        sign=lambda sk, msg, ctx=b"", _n=name: mldsa.sign(sk, msg, _n, ctx=ctx),
        verify=lambda pk, msg, sig, ctx=b"", _n=name:
            mldsa.verify(pk, msg, sig, _n, ctx=ctx),
        sizes=mldsa.sizes(name),
        family="ML-DSA", standard="FIPS 204",
        basis="module lattices (CRYSTALS-Dilithium)",
        quantum_safe=True,
        # Empty domain: these addresses predate the registry and are on chain.
        addr_domain=b"",
        note=note)


register(_mldsa_algo("ML-DSA-44", "the default — category 2, 2420-byte sigs"))
register(_mldsa_algo("ML-DSA-65", "category 3, 3309-byte sigs"))
register(_mldsa_algo("ML-DSA-87", "category 5, 4627-byte sigs"))

def _slhdsa_algo(name, note=""):
    return SigAlgo(
        name,
        keygen=lambda seed, _n=name: slhdsa.keygen_internal(seed, _n),
        sign=lambda sk, msg, ctx=b"", _n=name:
            slhdsa.sign(sk, msg, _n, ctx=ctx),
        verify=lambda pk, msg, sig, ctx=b"", _n=name:
            slhdsa.verify(pk, msg, sig, _n, ctx=ctx),
        sizes=slhdsa.sizes(name),
        family="SLH-DSA", standard="FIPS 205",
        basis="hash functions only (SPHINCS+) — no structured assumption",
        quantum_safe=True,
        note=note)


# Only the fast sets: an "s" signature takes tens of pure-python seconds to
# make, which is not a thing to offer a wallet.
register(_slhdsa_algo("SLH-DSA-SHAKE-128f",
                      "the hedge: 17088-byte sigs, shares nothing with "
                      "lattices"))
register(_slhdsa_algo("SLH-DSA-SHAKE-192f", "category 3, 35664-byte sigs"))
register(_slhdsa_algo("SLH-DSA-SHAKE-256f", "category 5, 49856-byte sigs"))


# ── plugins ───────────────────────────────────────────────────────


def load_plugins():
    """Import every .py in the plugin dirs and let it register. A broken
    plugin is reported in the catalog and skipped — one bad file must not
    take the node's built-in key types down with it."""
    import importlib.util
    this = sys.modules[__name__]
    # One ordering across every dir, by basename — so a plugin that wants to
    # run after the others (a zz_… wrapper binding extra behaviour onto what
    # everyone else registered) sorts last wherever it lives.
    found = []
    for d in PLUGIN_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".py") and not fn.startswith("_"):
                found.append((fn, os.path.join(d, fn)))
    for fn, path in sorted(found):
        if path in _LOADED:
            continue
        _LOADED.add(path)
        try:
            spec = importlib.util.spec_from_file_location(
                f"postquant_algo_{fn[:-3]}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not hasattr(module, "register"):
                raise AttributeError("plugin defines no register(algos)")
            before = set(REGISTRY)
            module.register(this)
            for name in set(REGISTRY) - before:
                REGISTRY[name].origin = path
        except Exception as e:                              # noqa: BLE001
            PLUGIN_ERRORS.append({
                "plugin": path, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=3)})


load_plugins()
