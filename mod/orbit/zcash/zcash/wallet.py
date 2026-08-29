"""
Encrypted wallet storage for the zcash module.

Wallets live outside the repo, in ~/.mod/zcash/wallets/<name>.json, so secrets
are never committed. Each file has two parts:

  * plaintext metadata -- derived addresses and labels, so balances and receive
    addresses work without ever entering a password;
  * an AES-256-GCM blob holding the mnemonic and any imported private keys,
    unlocked by a PBKDF2-SHA256 password (600k iterations).

A password is required for anything that spends.
"""

import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    from . import keys as _k
    from . import shielded as _shielded
except ImportError:  # loaded as a loose module
    import keys as _k
    import shielded as _shielded

KDF_ITERATIONS = 600_000
WALLET_VERSION = 2          # v2 adds the Sapling account


class WalletError(Exception):
    pass


def wallet_dir() -> Path:
    base = Path(os.environ.get("ZCASH_WALLET_DIR")
                or Path.home() / ".mod" / "zcash" / "wallets")
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    if not safe:
        raise WalletError(f"invalid wallet name: {name!r}")
    return wallet_dir() / f"{safe}.json"


# ── Crypto ──────────────────────────────────────────────────────────────────

def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode())


def _encrypt(secret: dict, password: str) -> dict:
    if not password:
        raise WalletError("a password is required to encrypt a wallet")
    salt, nonce = os.urandom(16), os.urandom(12)
    key = _derive_key(password, salt, KDF_ITERATIONS)
    blob = AESGCM(key).encrypt(nonce, json.dumps(secret).encode(), None)
    return {"kdf": "pbkdf2-sha256", "iterations": KDF_ITERATIONS, "cipher": "aes-256-gcm",
            "salt": salt.hex(), "nonce": nonce.hex(), "ciphertext": blob.hex()}


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
        out.append({"name": d.get("name", p.stem),
                    "addresses": len(d.get("addresses", [])),
                    "watch_only": d.get("watch_only", False),
                    "created": d.get("created")})
    return out


# ── Lifecycle ───────────────────────────────────────────────────────────────

def create(name: str, password: str, mnemonic: str = None, passphrase: str = "",
           strength: int = 256, addresses: int = 1, birthday: int = None) -> dict:
    """Create (or restore, if `mnemonic` is given) an HD wallet.

    One seed, both pools: the transparent addresses come from BIP44
    m/44'/133'/... and the shielded account from ZIP-32 m/32'/133'/0', which
    is what every other Zcash wallet does with the same words.

    `birthday` is the chain height the wallet starts at; a shielded scan has
    no cheap way to find its own first block, so recording it here is the
    difference between scanning a day and scanning the whole chain.
    """
    if exists(name):
        raise WalletError(f"wallet {name!r} already exists")
    restored = mnemonic is not None
    if restored:
        mnemonic = " ".join(mnemonic.split())
        if not _k.validate_mnemonic(mnemonic):
            raise WalletError("invalid BIP39 mnemonic (bad word or checksum)")
    else:
        mnemonic = _k.generate_mnemonic(strength)

    seed = _k.mnemonic_to_seed(mnemonic, passphrase)
    entries = [_derive_entry(seed, i) for i in range(max(1, addresses))]
    shielded = _shielded_account(mnemonic, passphrase, entries[0]["address"], birthday)
    data = {
        "name": name, "version": WALLET_VERSION, "created": int(time.time()),
        "watch_only": False, "next_index": len(entries), "addresses": entries,
        "shielded": shielded,
        "encrypted": _encrypt({"mnemonic": mnemonic, "passphrase": passphrase, "keys": {}},
                              password),
    }
    _write(name, data)
    return {"name": name, "restored": restored,
            "addresses": [e["address"] for e in entries],
            "shielded_address": shielded["addresses"][0]["address"],
            "unified_address": shielded["addresses"][0]["unified_address"],
            "birthday": shielded["birthday"],
            "mnemonic": None if restored else mnemonic,
            "warning": None if restored else
            "Write this mnemonic down now. It is the only way to recover this wallet "
            "and it is not shown again."}


# ── Shielded (Sapling) ──────────────────────────────────────────────────────

def _shielded_account(mnemonic: str, passphrase: str, transparent: str,
                      birthday: int = None, account: int = 0) -> dict:
    """The plaintext half of the Sapling account: addresses only.

    Viewing keys stay inside the encrypted blob. A z-address is public, but an
    incoming viewing key reveals every payment ever made to the account, so
    unlike transparent balances a shielded scan needs the password.
    """
    first = _shielded.derive_address(mnemonic, passphrase, account, 0, transparent)
    return {
        "account": account,
        "birthday": birthday,
        "next_index": first["diversifier_index"] + 1,
        "addresses": [{
            "diversifier_index": first["diversifier_index"],
            "address": first["address"],
            "unified_address": first["unified_address"],
            "label": "",
        }],
    }


def shielded(name: str) -> dict:
    """The wallet's Sapling addresses (no keys, no password needed)."""
    data = _read(name)
    account = data.get("shielded")
    if not account:
        raise WalletError(
            f"wallet {name!r} predates shielded support or holds only imported "
            f"keys; run shielded_upgrade to derive its Sapling account")
    return account


def shielded_key(name: str, password: str, account: int = None):
    """The ZIP-32 extended spending key for this wallet's Sapling account."""
    data = _read(name)
    secret = _decrypt(data["encrypted"], password)
    if not secret.get("mnemonic"):
        raise WalletError(
            f"wallet {name!r} holds only imported transparent keys; shielded "
            f"addresses come from a seed, so there is nothing to derive")
    if account is None:
        account = (data.get("shielded") or {}).get("account", 0)
    return _shielded.account_key(secret["mnemonic"],
                                 secret.get("passphrase", ""), account)


def new_shielded_address(name: str, password: str, label: str = "") -> dict:
    """Derive the next diversified address of the Sapling account."""
    data = _read(name)
    secret = _decrypt(data["encrypted"], password)
    if not secret.get("mnemonic"):
        raise WalletError(f"wallet {name!r} has no seed to derive from")
    account = data.get("shielded") or _shielded_account(
        secret["mnemonic"], secret.get("passphrase", ""),
        data["addresses"][0]["address"] if data["addresses"] else None)
    t_addr = data["addresses"][0]["address"] if data["addresses"] else None
    derived = _shielded.derive_address(
        secret["mnemonic"], secret.get("passphrase", ""),
        account.get("account", 0), account.get("next_index", 0), t_addr)
    entry = {"diversifier_index": derived["diversifier_index"],
             "address": derived["address"],
             "unified_address": derived["unified_address"], "label": label}
    account["addresses"].append(entry)
    # Half of all diversifier indices are unusable, so step past the one the
    # search actually landed on rather than the one we asked for.
    account["next_index"] = derived["diversifier_index"] + 1
    data["shielded"] = account
    _write(name, data)
    return entry


def upgrade_shielded(name: str, password: str, birthday: int = None) -> dict:
    """Add a Sapling account to a wallet created before shielded support."""
    data = _read(name)
    if data.get("shielded"):
        return data["shielded"]
    secret = _decrypt(data["encrypted"], password)
    if not secret.get("mnemonic"):
        raise WalletError(f"wallet {name!r} has no seed; nothing to derive")
    t_addr = data["addresses"][0]["address"] if data["addresses"] else None
    data["shielded"] = _shielded_account(
        secret["mnemonic"], secret.get("passphrase", ""), t_addr, birthday)
    data["version"] = WALLET_VERSION
    _write(name, data)
    return data["shielded"]


def _derive_entry(seed: bytes, index: int, account: int = 0, change: int = 0) -> dict:
    hd = _k.derive_account(seed, account, change, index)
    return {"index": index, "path": _k.account_path(account, change, index),
            "address": hd.address(), "label": ""}


def import_key(name: str, password: str, wif: str, label: str = "") -> dict:
    """Add a single WIF private key to a wallet, creating it if needed."""
    priv, compressed = _k.wif_to_privkey(wif)
    address = _k.pubkey_to_address(_k.privkey_to_pubkey(priv, compressed))
    if exists(name):
        data = _read(name)
        secret = _decrypt(data["encrypted"], password)
    else:
        data = {"name": name, "version": WALLET_VERSION, "created": int(time.time()),
                "watch_only": False, "next_index": 0, "addresses": []}
        secret = {"mnemonic": None, "passphrase": "", "keys": {}}
    if any(e["address"] == address for e in data["addresses"]):
        return {"address": address, "added": False, "note": "already in wallet"}
    secret.setdefault("keys", {})[address] = wif
    data["addresses"].append({"index": None, "path": "imported",
                              "address": address, "label": label})
    data["encrypted"] = _encrypt(secret, password)
    _write(name, data)
    return {"address": address, "added": True}


def new_address(name: str, password: str, label: str = "") -> dict:
    data = _read(name)
    secret = _decrypt(data["encrypted"], password)
    if not secret.get("mnemonic"):
        raise WalletError("this wallet holds only imported keys; use wallet_import")
    seed = _k.mnemonic_to_seed(secret["mnemonic"], secret.get("passphrase", ""))
    index = data.get("next_index", len(data["addresses"]))
    entry = _derive_entry(seed, index)
    entry["label"] = label
    data["addresses"].append(entry)
    data["next_index"] = index + 1
    _write(name, data)
    return entry


def addresses(name: str) -> list:
    return _read(name)["addresses"]


def info(name: str) -> dict:
    d = _read(name)
    account = d.get("shielded") or {}
    return {"name": d["name"], "created": d.get("created"),
            "address_count": len(d["addresses"]),
            "has_mnemonic": d.get("next_index", 0) > 0,
            "addresses": d["addresses"],
            "shielded_addresses": account.get("addresses", []),
            "shielded_account": account.get("account"),
            "birthday": account.get("birthday")}


def reveal(name: str, password: str) -> dict:
    """Return the mnemonic and imported keys. Handle with care."""
    data = _read(name)
    secret = _decrypt(data["encrypted"], password)
    return {"name": name, "mnemonic": secret.get("mnemonic"),
            "passphrase": secret.get("passphrase", ""),
            "imported_keys": secret.get("keys", {})}


def private_keys(name: str, password: str) -> dict:
    """Map of address -> 32-byte private key for every address in the wallet."""
    data = _read(name)
    secret = _decrypt(data["encrypted"], password)
    out = {}
    if secret.get("mnemonic"):
        seed = _k.mnemonic_to_seed(secret["mnemonic"], secret.get("passphrase", ""))
        for entry in data["addresses"]:
            if entry.get("index") is None:
                continue
            parts = entry["path"].split("/")
            account = int(parts[3].rstrip("'"))
            change = int(parts[4])
            hd = _k.derive_account(seed, account, change, entry["index"])
            out[hd.address()] = hd.priv
    for address, wif in (secret.get("keys") or {}).items():
        out[address] = _k.wif_to_privkey(wif)[0]
    return out


def rename_label(name: str, address: str, label: str) -> dict:
    data = _read(name)
    for entry in data["addresses"]:
        if entry["address"] == address:
            entry["label"] = label
            _write(name, data)
            return entry
    raise WalletError(f"{address} is not in wallet {name!r}")


def delete(name: str, password: str) -> dict:
    """Remove a wallet file. The password must verify first."""
    data = _read(name)
    _decrypt(data["encrypted"], password)
    _path(name).unlink()
    return {"deleted": name}
