"""Keys, addresses and transaction signing — post-quantum, and pluggable.

An address is 20 bytes of SHA3-256 over (key type, public key), printed as
`pq` + 40 hex. Which key type is no longer one answer: every algorithm in
pq/algos.py — ML-DSA out of the lattice family, SLH-DSA out of the hash
family, whatever a plugin in pq/algos.d/ adds — signs transactions here
through the same four functions, and the chain's quantum gate decides which
of them may witness. Nothing accepted by default rides an elliptic curve,
because a curve is exactly the thing Shor's algorithm takes apart; SHA3
carries every commitment either way.

The keystore lives at ~/.mod/postquant/keys.json, mode 0600, off the source
tree and never committed. What is stored per wallet is the 32-byte seed and
the scheme name, not the expanded key — every registered algorithm's key
generation is deterministic from that seed, so the file stays small whatever
the key type and the key is reconstructible.

    w = create('alice')                       # ML-DSA-44, the default
    w = create('bob', scheme='SLH-DSA-SHAKE-128f')   # the hash-based hedge
    tx = sign_tx(w, {'kind': 'xfer', ...})    # a signed transaction
    verify_tx(tx)                             # True

The address commits to the public key, so a first transaction from an
address carries its key inline and every later one does not. That is why the
chain charges witness gas per byte: a post-quantum witness runs 2420 bytes
(ML-DSA-44) to 17088 (SLH-DSA-128f) against ed25519's 64, and a chain that
does not price that difference is quietly subsidising its own signatures.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

from pq import algos                                            # noqa: E402
from state import StateError, canonical, is_hex, sha3           # noqa: E402
import state as S                                               # noqa: E402

KEY_DIR = os.path.expanduser(os.environ.get("POSTQUANT_KEY_DIR",
                                            "~/.mod/postquant"))
KEY_FILE = os.path.join(KEY_DIR, "keys.json")
SCHEME = os.environ.get("POSTQUANT_SCHEME", "ML-DSA-44")
# Signatures are bound to this string, so a signature minted here can never be
# replayed as one of this module's witnesses over anything else.
TX_CONTEXT = b"postquant/tx/v1"
ADDRESS_PREFIX = "pq"

# state.quote() prices a witness when no scheme is known; tell it what the
# default key type's witness weighs. Actual charging always uses real bytes,
# and per-scheme callers pass witness_bytes themselves.
S.SIG_BYTES = algos.get(SCHEME).sizes["sig"]
S.PK_BYTES = algos.get(SCHEME).sizes["pk"]


def witness_bytes(scheme=None) -> int:
    """What one signature plus one public key weighs under a key type —
    the number witness gas multiplies."""
    a = algos.get(scheme or SCHEME)
    return a.sizes["sig"] + a.sizes["pk"]


# ── addresses ─────────────────────────────────────────────────────


def address(pk: bytes, scheme: str = SCHEME) -> str:
    """pq + the first 20 bytes of a domain-separated SHA3-256 over the key
    type and the key. The algorithm's addr_domain is hashed in, so the same
    key bytes under two schemes are two different addresses and a witness can
    never be replayed across key types. The ML-DSA sets carry an empty domain
    because their addresses predate the registry and are already on chain."""
    return ADDRESS_PREFIX + sha3(b"pq-addr\x00", algos.get(scheme).addr_domain,
                                 pk)[:20].hex()


def valid_address(addr) -> bool:
    return (isinstance(addr, str) and addr.startswith(ADDRESS_PREFIX)
            and is_hex(addr[len(ADDRESS_PREFIX):], 20))


# ── the keystore ──────────────────────────────────────────────────


def _load():
    try:
        with open(KEY_FILE) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"wallets": {}, "default": None}
    except json.JSONDecodeError as e:
        raise StateError(f"{KEY_FILE} is not valid JSON ({e}) — move it aside "
                         "rather than letting this overwrite it", code="keystore")
    data.setdefault("wallets", {})
    data.setdefault("default", None)
    return data


def _save(data):
    os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
    tmp = KEY_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, KEY_FILE)          # atomic: a crash never truncates keys


def _public(w):
    return {k: v for k, v in w.items() if k != "seed"}


def _algo_for_wallet(scheme):
    """The algorithm a wallet may be created under: registered, and past the
    quantum gate — a wallet whose witnesses the chain refuses is a trap."""
    a = algos.maybe(scheme)
    if a is None:
        raise StateError(
            f"unknown key type {scheme!r} — this node knows "
            f"{', '.join(algos.names())}. New types are one file in "
            "pq/algos.d/ (see its README)", code="unknown_scheme")
    if not algos.allowed(scheme):
        raise StateError(
            f"{scheme} declared quantum_safe=false and this chain is "
            "post-quantum — it is listed in pq_algos but cannot witness a "
            "transaction (POSTQUANT_ALLOW_CLASSICAL=1 opens the gate on a "
            "throwaway devnet)", code="not_quantum_safe", status=403)
    return a


def create(name="default", seed=None, scheme=None, overwrite=False):
    """A new wallet under any accepted key type. Deterministic if you pass a
    32-byte hex seed; the same seed under two schemes is two unrelated keys
    and two different addresses."""
    scheme = scheme or SCHEME
    algo = _algo_for_wallet(scheme)
    data = _load()
    if name in data["wallets"] and not overwrite:
        raise StateError(f"wallet {name!r} exists — pass overwrite=1 to replace "
                         "it, and understand that its address changes",
                         code="wallet_exists")
    raw = bytes.fromhex(seed) if seed else secrets.token_bytes(32)
    if len(raw) != 32:
        raise StateError("seed must be 32 bytes of hex", code="bad_seed")
    pk, _sk = algo.keygen(raw)
    w = {"name": name, "address": address(pk, scheme), "scheme": scheme,
         "pk": pk.hex(), "seed": raw.hex(), "created": int(time.time())}
    data["wallets"][name] = w
    if not data["default"]:
        data["default"] = name
    _save(data)
    return _public(w)


def wallets():
    data = _load()
    return {"wallets": [_public(w) for w in data["wallets"].values()],
            "default": data["default"], "keystore": KEY_FILE,
            "scheme": SCHEME, "schemes": algos.names(pq_only=True)}


def get(name=None, required=True):
    """A wallet by name or by address, with its seed. Never leaves the process."""
    data = _load()
    name = name or data["default"]
    w = data["wallets"].get(name)
    if w is None and name:
        w = next((x for x in data["wallets"].values()
                  if x["address"] == name), None)
    if w is None:
        if not required:
            return None
        have = ", ".join(data["wallets"]) or "none"
        raise StateError(f"no wallet {name!r} — have: {have}. Create one with "
                         "wallet action=create", code="no_wallet", status=404)
    return w


def use(name):
    data = _load()
    if name not in data["wallets"]:
        raise StateError(f"no wallet {name!r}", code="no_wallet", status=404)
    data["default"] = name
    _save(data)
    return {"default": name, "address": data["wallets"][name]["address"]}


def remove(name):
    data = _load()
    if name not in data["wallets"]:
        raise StateError(f"no wallet {name!r}", code="no_wallet", status=404)
    gone = data["wallets"].pop(name)
    if data["default"] == name:
        data["default"] = next(iter(data["wallets"]), None)
    _save(data)
    return {"removed": name, "address": gone["address"]}


def secret_key(w):
    """Expand a stored seed back into the wallet's secret key — deterministic
    key generation is what every registered algorithm signed up for."""
    _pk, sk = algos.get(w["scheme"]).keygen(bytes.fromhex(w["seed"]))
    return sk


# ── transactions ──────────────────────────────────────────────────


def tx_hash(tx) -> str:
    """The identity of a transaction: its body and its witness. Both, because
    two different signatures over one body are two different transactions and
    a chain that hashes only the body has a malleability bug."""
    return sha3(b"pq-tx\x00", canonical(tx["body"]),
                bytes.fromhex(tx.get("sig", ""))).hex()


def sign_body(w, body, include_pk=True):
    """Sign a transaction body with a wallet. The signature covers the exact
    canonical bytes of the body and nothing else."""
    algo = algos.get(w["scheme"])
    sig = algo.sign(secret_key(w), canonical(body), TX_CONTEXT)
    tx = {"body": body, "sig": sig.hex(), "scheme": w["scheme"]}
    if include_pk:
        tx["pk"] = w["pk"]
    tx["hash"] = tx_hash(tx)
    return tx


def sign_tx(w, body, include_pk=True):
    """Fill in `from` and sign."""
    body = dict(body)
    body.setdefault("from", w["address"])
    if body["from"] != w["address"]:
        raise StateError(f"wallet {w['name']} is {w['address']}, cannot sign "
                         f"for {body['from']}", code="wrong_wallet")
    return sign_body(w, body, include_pk)


def verify_tx(tx, known_pk=None) -> bool:
    """Check a transaction's witness.

    Four things have to hold and all four matter: the scheme is one this
    chain accepts (the quantum gate lives here as well as at the mempool, so
    a full replay audit re-judges every witness against current policy), the
    signature verifies under that scheme, the public key hashes with that
    scheme's domain to the `from` address, and the key matches whatever the
    chain already recorded for the address. Drop the third and anyone signs
    for anyone; drop the fourth and an account can silently swap its key.
    """
    try:
        body = tx["body"]
        sig = bytes.fromhex(tx["sig"])
        scheme = tx.get("scheme", SCHEME)
        pk_hex = tx.get("pk") or known_pk
        if not pk_hex:
            return False
        if known_pk and tx.get("pk") and tx["pk"] != known_pk:
            return False
        if not algos.allowed(scheme):
            return False
        pk = bytes.fromhex(pk_hex)
        if address(pk, scheme) != body.get("from"):
            return False
        return algos.get(scheme).verify(pk, canonical(body), sig, TX_CONTEXT)
    except Exception:
        return False
