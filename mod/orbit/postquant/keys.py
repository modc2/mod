"""Keys, addresses and transaction signing — all of it post-quantum.

An address is 20 bytes of SHA3-256 over an ML-DSA public key, printed as
`pq` + 40 hex. Nothing on this chain is ed25519 or secp256k1: there is no
elliptic curve anywhere in the trust path, because a curve is exactly the thing
Shor's algorithm takes apart. What is left is lattices for signatures (ML-DSA,
FIPS 204), lattices for key exchange (ML-KEM, FIPS 203) and SHA3 for every
commitment, all of which survive a quantum adversary with at worst a square-root
loss that the parameter sizes already absorb.

The keystore lives at ~/.mod/postquant/keys.json, mode 0600, off the source
tree and never committed. What is stored per wallet is the 32-byte seed, not
the 2560-byte expanded key — ML-DSA key generation is deterministic from that
seed, so the file stays small and the key is reconstructible.

    w = create('alice')                       # a wallet
    tx = sign_tx(w, {'kind': 'xfer', ...})    # a signed transaction
    verify_tx(tx)                             # True

The address commits to the public key, so a first transaction from an address
carries its key inline (~1.3KB) and every later one does not. That is why the
chain charges witness gas per byte: on a post-quantum L1 the signature is the
transaction, and pretending otherwise mis-prices the whole block.
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

from pq import mldsa                                            # noqa: E402
from state import StateError, canonical, is_hex, sha3           # noqa: E402
import state as S                                               # noqa: E402

KEY_DIR = os.path.expanduser(os.environ.get("POSTQUANT_KEY_DIR",
                                            "~/.mod/postquant"))
KEY_FILE = os.path.join(KEY_DIR, "keys.json")
SCHEME = os.environ.get("POSTQUANT_SCHEME", "ML-DSA-44")
# Signatures are bound to this string, so a signature minted here can never be
# replayed as one of this module's ML-DSA signatures over anything else.
TX_CONTEXT = b"postquant/tx/v1"
ADDRESS_PREFIX = "pq"

# state.quote() prices a witness; tell it how big one actually is.
S.SIG_BYTES = mldsa.sizes(SCHEME)["sig"]
S.PK_BYTES = mldsa.sizes(SCHEME)["pk"]


# ── addresses ─────────────────────────────────────────────────────


def address(pk: bytes) -> str:
    """pq + the first 20 bytes of a domain-separated SHA3-256 over the key."""
    return ADDRESS_PREFIX + sha3(b"pq-addr\x00", pk)[:20].hex()


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


def create(name="default", seed=None, scheme=SCHEME, overwrite=False):
    """A new wallet. Deterministic if you pass a 32-byte hex seed."""
    data = _load()
    if name in data["wallets"] and not overwrite:
        raise StateError(f"wallet {name!r} exists — pass overwrite=1 to replace "
                         "it, and understand that its address changes",
                         code="wallet_exists")
    raw = bytes.fromhex(seed) if seed else secrets.token_bytes(32)
    if len(raw) != 32:
        raise StateError("seed must be 32 bytes of hex", code="bad_seed")
    pk, _sk = mldsa.keygen_internal(raw, scheme)
    w = {"name": name, "address": address(pk), "scheme": scheme,
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
            "scheme": SCHEME}


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
    """Expand a stored seed back into an ML-DSA secret key."""
    _pk, sk = mldsa.keygen_internal(bytes.fromhex(w["seed"]), w["scheme"])
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
    sk = secret_key(w)
    sig = mldsa.sign(sk, canonical(body), w["scheme"], ctx=TX_CONTEXT)
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

    Three things have to hold and all three matter: the signature verifies, the
    public key hashes to the `from` address, and the key matches whatever the
    chain already recorded for that address. Drop the second and anyone signs
    for anyone; drop the third and an account can silently swap its key.
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
        pk = bytes.fromhex(pk_hex)
        if address(pk) != body.get("from"):
            return False
        if scheme not in mldsa.PARAMS:
            return False
        return mldsa.verify(pk, canonical(body), sig, scheme, ctx=TX_CONTEXT)
    except Exception:
        return False
