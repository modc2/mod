"""Sign-in — three keys, one session.

A visitor proves they hold a key by signing a nonce this box just minted. Which
kind of key is the only thing that changes:

    BROWSER     a P-256 keypair the tab generates and keeps in localStorage.
                No extension, no chain, no install — it is the "just let me in"
                door, and the identity it makes is per-device by design.
    METAMASK    EIP-191 personal_sign; the address is recovered from the
                signature, so nothing but the signature has to be trusted.
    BITTENSOR   sr25519 (or ed25519) from a Polkadot-family extension —
                Talisman, SubWallet, Polkadot{.js}. The SS58 address is decoded
                to a public key and the checksum has to hold.

What a session is worth: sessions are stateless HMAC tokens signed with a
secret kept in ~/.mod/liquidai/server.secret (0600). Nothing about a session
lives in a table, so restarting the API doesn't sign anybody out, and revoking
everybody is `rm server.secret`.

Who gets what:
    anyone            the catalog, model detail, runtime status, and browser
                      runs — those cost this box nothing and leak nothing
    signed in         /chat on this box's CPU/GPU or the operator's cloud key
    owner             weights on this disk, and the key vault

The owner is the first account to sign in (or LIQUIDAI_OWNER, pinned), stored
in ~/.mod/liquidai/owner.json. LIQUIDAI_OPEN=1 turns the whole gate off for
local development.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple

STORE = os.path.expanduser("~/.mod/liquidai")
SECRET_PATH = os.path.join(STORE, "server.secret")
OWNER_PATH = os.path.join(STORE, "owner.json")
ACCOUNTS_PATH = os.path.join(STORE, "accounts.json")

KINDS = ("browser", "evm", "bittensor")
NONCE_TTL = 300           # 5 min to get from "connect" to "sign"
SESSION_TTL = 7 * 86400

# Minted nonces, in memory only: a nonce that doesn't survive a restart is a
# nonce that can't be replayed after one.
_NONCES: Dict[str, Dict[str, Any]] = {}


def open_mode() -> bool:
    return os.environ.get("LIQUIDAI_OPEN", "") not in ("", "0", "false")


# ── secret / stores ──────────────────────────────────────────────────

def secret() -> bytes:
    """The HMAC key, minted on first use and never leaving this box."""
    try:
        with open(SECRET_PATH, "rb") as f:
            data = f.read().strip()
        if data:
            return data
    except FileNotFoundError:
        pass
    data = secrets.token_hex(32).encode()
    os.makedirs(STORE, exist_ok=True)
    with open(SECRET_PATH, "wb") as f:
        f.write(data)
    os.chmod(SECRET_PATH, 0o600)
    return data


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, value: Any):
    os.makedirs(STORE, exist_ok=True)
    with open(path, "w") as f:
        json.dump(value, f, indent=2)
    os.chmod(path, 0o600)


def owner() -> Optional[str]:
    pinned = os.environ.get("LIQUIDAI_OWNER", "").strip()
    if pinned:
        return pinned
    return (_read_json(OWNER_PATH, {}) or {}).get("address")


def owner_state() -> Dict[str, Any]:
    addr = owner()
    rec = _read_json(OWNER_PATH, {}) or {}
    return {
        "claimed": bool(addr),
        "address": addr,
        "kind": rec.get("kind"),
        "claimed_at": rec.get("claimed_at"),
        "pinned": bool(os.environ.get("LIQUIDAI_OWNER", "").strip()),
        "open": open_mode(),
    }


def accounts() -> Dict[str, Any]:
    return _read_json(ACCOUNTS_PATH, {}) or {}


def _remember(address: str, kind: str) -> Dict[str, Any]:
    """Record the sign-in, and let the first one through claim the box."""
    book = accounts()
    rec = book.get(address) or {"address": address, "kind": kind,
                                "first_seen": time.time(), "logins": 0}
    rec.update(kind=kind, last_seen=time.time(), logins=rec.get("logins", 0) + 1)
    book[address] = rec
    _write_json(ACCOUNTS_PATH, book)

    if not owner():
        _write_json(OWNER_PATH, {"address": address, "kind": kind,
                                 "claimed_at": time.time()})
    rec["owner"] = (owner() == address)
    return rec


# ── nonce ────────────────────────────────────────────────────────────

def _prune():
    now = time.time()
    for n, rec in list(_NONCES.items()):
        if rec["expires"] < now:
            _NONCES.pop(n, None)


def challenge(address: str, kind: str) -> Dict[str, Any]:
    """Mint the exact text the wallet has to sign."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    _prune()
    nonce = secrets.token_hex(16)
    issued = time.time()
    net = {"evm": "ethereum", "bittensor": "bittensor", "browser": "this device"}[kind]
    message = (
        "liquidai — sign in\n"
        "\n"
        f"address: {address}\n"
        f"network: {net}\n"
        f"nonce:   {nonce}\n"
        f"issued:  {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(issued))}\n"
        "\n"
        "Signing proves you hold this key. It authorises no transaction and\n"
        "moves no funds."
    )
    _NONCES[nonce] = {"address": address, "kind": kind, "message": message,
                      "expires": issued + NONCE_TTL}
    return {"nonce": nonce, "message": message, "expires_in": NONCE_TTL}


def _take(nonce: str) -> Dict[str, Any]:
    """One nonce, one use — pop it before verifying, not after."""
    _prune()
    rec = _NONCES.pop(nonce, None)
    if not rec:
        raise ValueError("nonce unknown or expired — start the sign-in again")
    return rec


# ── signature verification, one per key kind ─────────────────────────

def _verify_evm(address: str, message: str, signature: str) -> str:
    from eth_account import Account
    from eth_account.messages import encode_defunct

    recovered = Account.recover_message(encode_defunct(text=message),
                                        signature=signature)
    if recovered.lower() != address.lower():
        raise ValueError("signature does not match that address")
    return recovered  # checksummed, as MetaMask spells it


def ss58_pubkey(address: str) -> bytes:
    """SS58 → 32-byte public key, checksum enforced."""
    import base58

    raw = base58.b58decode(address)
    # A prefix < 64 is one byte; 64…16383 is two. Anything else isn't SS58.
    plen = 2 if raw[0] & 0b0100_0000 else 1
    body, checksum = raw[:plen + 32], raw[plen + 32:]
    if len(body) != plen + 32 or len(checksum) != 2:
        raise ValueError("not an SS58 address")
    want = hashlib.blake2b(b"SS58PRE" + body, digest_size=64).digest()[:2]
    if checksum != want:
        raise ValueError("SS58 checksum failed — address is mistyped")
    return body[plen:]


def _verify_bittensor(address: str, message: str, signature: str) -> str:
    pubkey = ss58_pubkey(address)
    sig = bytes.fromhex(signature[2:] if signature.startswith("0x") else signature)
    if len(sig) != 64:
        raise ValueError("expected a 64-byte sr25519/ed25519 signature")

    # signRaw wraps whatever you hand it in <Bytes>…</Bytes> so a dapp can
    # never trick a wallet into signing a transaction payload. Which of the two
    # forms reaches us depends on the extension, so try both.
    payloads = (f"<Bytes>{message}</Bytes>".encode(), message.encode())

    try:
        import sr25519
        for body in payloads:
            if sr25519.verify(sig, body, pubkey):
                return address
    except ImportError:
        pass

    try:  # ed25519 accounts exist in the same extensions
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
        vk = VerifyKey(pubkey)
        for body in payloads:
            try:
                vk.verify(body, sig)
                return address
            except BadSignatureError:
                continue
    except ImportError:
        pass

    raise ValueError("signature does not match that address")


def _b64u(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def browser_address(pubkey_b64u: str) -> str:
    """A device address is just a fingerprint of its public key."""
    digest = hashlib.sha256(_b64u(pubkey_b64u)).hexdigest()
    return f"br1{digest[:32]}"


def _verify_browser(address: str, message: str, signature: str,
                    pubkey: Optional[str]) -> str:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    if not pubkey:
        raise ValueError("browser sign-in must send its public key")
    if browser_address(pubkey) != address:
        raise ValueError("address is not this key's fingerprint")

    raw = _b64u(pubkey)
    if len(raw) != 65 or raw[0] != 0x04:
        raise ValueError("expected an uncompressed P-256 public key")
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)

    # WebCrypto emits r‖s fixed-width; `cryptography` wants DER.
    sig = _b64u(signature)
    if len(sig) != 64:
        raise ValueError("expected a 64-byte P-256 signature")
    der = utils.encode_dss_signature(int.from_bytes(sig[:32], "big"),
                                     int.from_bytes(sig[32:], "big"))
    try:
        key.verify(der, message.encode(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise ValueError("signature does not match that key")
    return address


def verify(nonce: str, signature: str, pubkey: Optional[str] = None) -> Dict[str, Any]:
    """Check the signature over a nonce we minted, then hand back a session."""
    rec = _take(nonce)
    address, kind, message = rec["address"], rec["kind"], rec["message"]

    if kind == "evm":
        address = _verify_evm(address, message, signature)
    elif kind == "bittensor":
        address = _verify_bittensor(address, message, signature)
    else:
        address = _verify_browser(address, message, signature, pubkey)

    account = _remember(address, kind)
    return {"token": mint(address, kind), "account": account,
            "expires_in": SESSION_TTL, "owner": account["owner"]}


# ── sessions ─────────────────────────────────────────────────────────

def mint(address: str, kind: str, ttl: int = SESSION_TTL) -> str:
    payload = json.dumps({"a": address, "k": kind, "exp": int(time.time() + ttl)},
                         separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def mint_local(ttl: int = 3600) -> str:
    """A token for `m liquidai/…` on this box.

    Reading server.secret already means being the operator — the file is 0600
    in their home — so a shell here doesn't have to go find a wallet to load a
    model. The `root` claim is what says so; nothing over the network can mint
    one without the secret.
    """
    payload = json.dumps({"a": "cli@localhost", "k": "cli", "root": 1,
                          "exp": int(time.time() + ttl)},
                         separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def read(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Decode a session token, or None if it's absent, forged or stale."""
    if not token:
        return None
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        body, sig = token.split(".", 1)
        want = hmac.new(secret(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64u(sig), want):
            return None
        claims = json.loads(_b64u(body))
    except Exception:
        return None
    if claims.get("exp", 0) < time.time():
        return None
    address = claims["a"]
    return {"address": address, "kind": claims["k"], "expires": claims["exp"],
            "owner": bool(claims.get("root")) or owner() == address}


def me(token: Optional[str]) -> Dict[str, Any]:
    session = read(token)
    if not session:
        return {"signed_in": False, "owner_claimed": bool(owner()), "open": open_mode()}
    rec = accounts().get(session["address"], {})
    return {"signed_in": True, **session, "logins": rec.get("logins"),
            "first_seen": rec.get("first_seen"), "open": open_mode()}


# ── gates (used as FastAPI dependencies) ─────────────────────────────

def gate(token: Optional[str], need: str = "session") -> Tuple[bool, str, Optional[Dict]]:
    """(allowed, why-not, session). `need` is "session" or "owner"."""
    session = read(token)
    if open_mode():
        return True, "", session
    if not session:
        return False, "sign in to use this — browser key, MetaMask or a Bittensor wallet", None
    if need == "owner" and not session["owner"]:
        return False, "owner only — this box is claimed by another account", session
    return True, "", session
