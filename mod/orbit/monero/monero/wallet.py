"""
Encrypted wallet storage for the monero module.

Wallets live outside the repo, in ~/.mod/monero/wallets/<name>.json, so keys
are never committed. Each file has two parts:

  * plaintext metadata -- the address, subaddresses and public keys, so you can
    show a receive address without typing a password;
  * an AES-256-GCM blob holding the seed phrase, the private spend key and the
    private view key, unlocked by a PBKDF2-SHA256 password (600k iterations).

The private view key is inside the encrypted half deliberately. In Monero it is
not a public value: anyone holding it can see every payment you have ever
received. That is also why scanning asks for a password when a balance lookup
on a transparent chain would not.

Two kinds of wallet:

  full        seed phrase -> spend key -> view key. Can do everything the
              module can do, and can be handed to monero-wallet-rpc.
  view-only   an address plus its view key, with no spend key. Can find
              incoming payments and nothing else -- useful for watching a cold
              wallet from a host you do not fully trust.
"""

import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    from . import crypto, mnemonic as _mnemonic
except ImportError:  # loaded as a loose module by the mod runtime
    import crypto
    import mnemonic as _mnemonic

KDF_ITERATIONS = 600_000
WALLET_VERSION = 1


class WalletError(Exception):
    pass


def wallet_dir() -> Path:
    base = Path(os.environ.get("MONERO_WALLET_DIR")
                or Path.home() / ".mod" / "monero" / "wallets")
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _path(name: str) -> Path:
    safe = "".join(c for c in (name or "") if c.isalnum() or c in "-_")
    if not safe:
        raise WalletError(f"invalid wallet name: {name!r}")
    return wallet_dir() / f"{safe}.json"


# ── Crypto at rest ──────────────────────────────────────────────────────────

def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode())


def _encrypt(secret: dict, password: str) -> dict:
    if not password:
        raise WalletError("a password is required to encrypt a wallet")
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _derive_key(password, salt, KDF_ITERATIONS)
    blob = AESGCM(key).encrypt(nonce, json.dumps(secret).encode(), None)
    return {"kdf": "pbkdf2-sha256", "iterations": KDF_ITERATIONS,
            "cipher": "aes-256-gcm", "salt": salt.hex(), "nonce": nonce.hex(),
            "ciphertext": blob.hex()}


def _decrypt(enc: dict, password: str) -> dict:
    key = _derive_key(password, bytes.fromhex(enc["salt"]), int(enc["iterations"]))
    try:
        plain = AESGCM(key).decrypt(bytes.fromhex(enc["nonce"]),
                                    bytes.fromhex(enc["ciphertext"]), None)
    except Exception:
        raise WalletError("wrong password")
    return json.loads(plain)


# ── Persistence ─────────────────────────────────────────────────────────────

def _read(name: str) -> dict:
    p = _path(name)
    if not p.exists():
        raise WalletError(f"no wallet named {name!r} (create one with wallet_create)")
    return json.loads(p.read_text())


def _write(name: str, data: dict):
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def exists(name: str) -> bool:
    return _path(name).exists()


def list_wallets() -> list:
    out = []
    for p in sorted(wallet_dir().glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        out.append({"name": d.get("name", p.stem), "network": d.get("network"),
                    "address": d.get("address"),
                    "view_only": d.get("view_only", False),
                    "subaddresses": len(d.get("subaddresses", [])),
                    "restore_height": d.get("restore_height"),
                    "created": d.get("created")})
    return out


# ── Lifecycle ───────────────────────────────────────────────────────────────

def create(name: str, password: str, seed_phrase: str = None,
           network: str = "mainnet", restore_height: int = None) -> dict:
    """Create a wallet, or restore one from its 25-word seed phrase."""
    if exists(name):
        raise WalletError(f"wallet {name!r} already exists")
    if network not in crypto.NETWORKS:
        raise WalletError(f"unknown network {network!r}")

    restored = bool(seed_phrase)
    if restored:
        phrase = " ".join(seed_phrase.split())
        try:
            seed = _mnemonic.decode(phrase)
        except _mnemonic.MnemonicError as e:
            raise WalletError(str(e))
    else:
        phrase = _mnemonic.generate()
        seed = _mnemonic.decode(phrase)

    keys = crypto.keys_from_seed(seed, network)
    data = {
        "name": name, "version": WALLET_VERSION, "network": network,
        "created": int(time.time()), "view_only": False,
        "address": keys["address"],
        "spend_public_key": keys["spend_public_key"],
        "view_public_key": keys["view_public_key"],
        "restore_height": restore_height,
        "subaddresses": [],
        "encrypted": _encrypt({"seed_phrase": phrase,
                               "spend_secret_key": keys["spend_secret_key"],
                               "view_secret_key": keys["view_secret_key"]}, password),
    }
    _write(name, data)
    return {
        "name": name, "network": network, "restored": restored,
        "address": keys["address"],
        "seed_phrase": None if restored else phrase,
        "restore_height": restore_height,
        "warning": None if restored else
        "Write this seed phrase down now. It is the only way to recover this "
        "wallet, it is not shown again, and anyone who reads it can spend "
        "your funds.",
        "note": None if restored else
        "A new wallet has no history to scan. If you restore this phrase "
        "elsewhere later, give it a restore height so scanning does not start "
        "at block 0.",
    }


def import_view_only(name: str, password: str, address: str, view_secret_key: str,
                     restore_height: int = None) -> dict:
    """Create a watch-only wallet from an address and its private view key."""
    if exists(name):
        raise WalletError(f"wallet {name!r} already exists")
    try:
        parsed = crypto.decode_address(address)
    except crypto.CryptoError as e:
        raise WalletError(f"bad address: {e}")
    if parsed["type"] == "subaddress":
        raise WalletError(
            "give the wallet's main address, not a subaddress -- subaddresses "
            "are derived from it, and scanning needs the main spend key")

    view_sec = bytes.fromhex((view_secret_key or "").strip())
    if len(view_sec) != 32:
        raise WalletError("the private view key is 32 bytes of hex (64 characters)")
    if crypto.secret_to_public(view_sec).hex() != parsed["view_public_key"]:
        raise WalletError(
            "that view key does not belong to that address -- its public form "
            "does not match the one encoded in the address")

    data = {
        "name": name, "version": WALLET_VERSION, "network": parsed["network"],
        "created": int(time.time()), "view_only": True,
        "address": parsed["address"],
        "spend_public_key": parsed["spend_public_key"],
        "view_public_key": parsed["view_public_key"],
        "restore_height": restore_height,
        "subaddresses": [],
        "encrypted": _encrypt({"seed_phrase": None, "spend_secret_key": None,
                               "view_secret_key": view_sec.hex()}, password),
    }
    _write(name, data)
    return {"name": name, "address": parsed["address"], "view_only": True,
            "network": parsed["network"],
            "note": "This wallet can find incoming payments and nothing else. "
                    "It cannot spend, and it cannot tell whether what it finds "
                    "has already been spent."}


def info(name: str) -> dict:
    d = _read(name)
    return {"name": d["name"], "network": d.get("network"),
            "address": d.get("address"), "view_only": d.get("view_only", False),
            "created": d.get("created"), "restore_height": d.get("restore_height"),
            "spend_public_key": d.get("spend_public_key"),
            "view_public_key": d.get("view_public_key"),
            "subaddresses": d.get("subaddresses", [])}


def addresses(name: str) -> list:
    d = _read(name)
    return ([{"address": d["address"], "label": "main", "major": 0, "minor": 0}]
            + d.get("subaddresses", []))


def secrets(name: str, password: str) -> dict:
    d = _read(name)
    s = _decrypt(d["encrypted"], password)
    return {"view_secret_key": s.get("view_secret_key"),
            "spend_secret_key": s.get("spend_secret_key"),
            "seed_phrase": s.get("seed_phrase"),
            "spend_public_key": d["spend_public_key"],
            "view_public_key": d["view_public_key"],
            "network": d.get("network", "mainnet"),
            "view_only": d.get("view_only", False)}


def reveal(name: str, password: str) -> dict:
    s = secrets(name, password)
    return dict(s, warning="Anyone with the seed phrase or spend key owns these "
                           "funds. The view key alone reveals every payment "
                           "you have received.")


def new_subaddress(name: str, password: str, label: str = "", major: int = 0) -> dict:
    """Derive the next subaddress in an account.

    Subaddresses are the right way to accept more than one payment: they are
    unlinkable to each other and to the main address, so reusing one address
    for everything is the only real privacy mistake a receiver can make.
    """
    d = _read(name)
    s = _decrypt(d["encrypted"], password)
    view_sec = bytes.fromhex(s["view_secret_key"])
    spend_pub = bytes.fromhex(d["spend_public_key"])

    existing = [e for e in d.get("subaddresses", []) if e.get("major") == major]
    minor = max([e["minor"] for e in existing], default=0) + 1
    address = crypto.subaddress(view_sec, spend_pub, major, minor,
                                d.get("network", "mainnet"))
    entry = {"address": address, "label": label, "major": major, "minor": minor}
    d.setdefault("subaddresses", []).append(entry)
    _write(name, d)
    return entry


def make_integrated(name: str, payment_id: str = None) -> dict:
    """An integrated address: the main address with a payment id folded in.

    Useful when a payer cannot be given a fresh subaddress (an exchange
    withdrawal form, say) and you still need to tell two payments apart.
    """
    d = _read(name)
    pid = bytes.fromhex(payment_id) if payment_id else crypto.random_payment_id()
    if len(pid) != 8:
        raise WalletError("a payment id for an integrated address is 8 bytes (16 hex chars)")
    address = crypto.integrated_address(bytes.fromhex(d["spend_public_key"]),
                                        bytes.fromhex(d["view_public_key"]),
                                        pid, d.get("network", "mainnet"))
    return {"integrated_address": address, "payment_id": pid.hex(),
            "base_address": d["address"]}


def label(name: str, address: str, text: str) -> dict:
    d = _read(name)
    for entry in d.get("subaddresses", []):
        if entry["address"] == address:
            entry["label"] = text
            _write(name, d)
            return entry
    raise WalletError(f"{address} is not a subaddress of wallet {name!r}")


def set_restore_height(name: str, height: int) -> dict:
    d = _read(name)
    d["restore_height"] = int(height)
    _write(name, d)
    return {"name": name, "restore_height": d["restore_height"]}


def delete(name: str, password: str) -> dict:
    """Remove a wallet file. The password must verify first."""
    d = _read(name)
    _decrypt(d["encrypted"], password)
    _path(name).unlink()
    return {"deleted": name,
            "note": "The file is gone. Without the seed phrase the funds are too."}
