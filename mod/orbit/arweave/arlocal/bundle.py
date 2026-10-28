"""
ANS-104 data items — real Arweave uploads without an AR balance.

Bundlers (ArDrive Turbo, Irys) accept signed data items and post them to
Arweave inside their own bundles; items under ~100 KiB are subsidized, so a
signing key is all that's needed — no funded wallet. The data item id is a
real 43-char Arweave id, resolvable at any gateway once seeded.

Implements just enough of ANS-104: Arweave deep-hash (SHA-384), avro tag
encoding, RSA-PSS (SHA-256, salt 32) signing with a JWK, and the binary
data-item layout. Key loading/generation uses `cryptography` only.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# Both subsidize data items under ~100 KiB; tried in order.
BUNDLERS = [
    ("turbo", "https://upload.ardrive.io/v1/tx"),
    ("irys", "https://node2.irys.xyz/tx/arweave"),
]
FREE_LIMIT = 100 * 1024

OWNER_LEN = 512  # sig type 1 = Arweave RSA-4096: 512-byte owner and signature


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ─── wallet (JWK) ────────────────────────────────────────────────────────────

def generate_jwk(path: Path) -> Dict[str, str]:
    """Generate an Arweave RSA-4096 wallet JWK at `path` (0600). Returns the JWK."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    nums = key.private_numbers()
    pub = nums.public_numbers

    def enc(i: int) -> str:
        return b64u(i.to_bytes((i.bit_length() + 7) // 8, "big"))

    jwk = {
        "kty": "RSA",
        "n": enc(pub.n),
        "e": enc(pub.e),
        "d": enc(nums.d),
        "p": enc(nums.p),
        "q": enc(nums.q),
        "dp": enc(nums.dmp1),
        "dq": enc(nums.dmq1),
        "qi": enc(nums.iqmp),
    }
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(jwk))
    path.chmod(0o600)
    return jwk


def load_jwk(path) -> Tuple[rsa.RSAPrivateKey, bytes]:
    """Load a JWK; returns (private key, owner bytes = raw 512-byte modulus)."""
    jwk = json.loads(Path(path).expanduser().read_text())
    required = ("n", "e", "d", "p", "q", "dp", "dq", "qi")
    if jwk.get("kty") != "RSA" or any(k not in jwk for k in required):
        raise ValueError("not a full RSA private JWK")
    ints = {k: int.from_bytes(b64u_dec(jwk[k]), "big") for k in required}
    owner = ints["n"].to_bytes(OWNER_LEN, "big")
    key = rsa.RSAPrivateNumbers(
        p=ints["p"], q=ints["q"], d=ints["d"],
        dmp1=ints["dp"], dmq1=ints["dq"], iqmp=ints["qi"],
        public_numbers=rsa.RSAPublicNumbers(e=ints["e"], n=ints["n"]),
    ).private_key()
    return key, owner


def address(path) -> str:
    """Arweave address for a wallet JWK: b64url(sha256(owner))."""
    _, owner = load_jwk(path)
    return b64u(hashlib.sha256(owner).digest())


# ─── ANS-104 primitives ──────────────────────────────────────────────────────

def deep_hash(data) -> bytes:
    """Arweave deepHash: SHA-384 over tagged chunks (list = fold, blob = pair)."""
    if isinstance(data, (list, tuple)):
        acc = hashlib.sha384(b"list" + str(len(data)).encode()).digest()
        for chunk in data:
            acc = hashlib.sha384(acc + deep_hash(chunk)).digest()
        return acc
    tag = hashlib.sha384(b"blob" + str(len(data)).encode()).digest()
    return hashlib.sha384(tag + hashlib.sha384(data).digest()).digest()


def _varint(n: int) -> bytes:
    """Avro zigzag-varint for a non-negative long."""
    z = n << 1
    out = bytearray()
    while True:
        b = z & 0x7F
        z >>= 7
        out.append(b | 0x80 if z else b)
        if not z:
            return bytes(out)


def encode_tags(tags: List[Tuple[str, str]]) -> bytes:
    """Avro array<record{name,value: string}> encoding used by ANS-104."""
    if not tags:
        return b""
    out = bytearray(_varint(len(tags)))
    for name, value in tags:
        for s in (name, value):
            raw = s.encode()
            out += _varint(len(raw)) + raw
    out += b"\x00"  # array terminator block
    return bytes(out)


def create_data_item(
    payload: bytes,
    wallet_path,
    tags: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[str, bytes]:
    """Sign an ANS-104 data item. Returns (id, raw bytes ready to POST)."""
    key, owner = load_jwk(wallet_path)
    tags = tags or []
    tags_bytes = encode_tags(tags)
    target = b""
    anchor = b""

    msg = deep_hash([
        b"dataitem", b"1", b"1",  # format, version, signature type (1 = arweave)
        owner, target, anchor, tags_bytes, payload,
    ])
    sig = key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )
    item_id = b64u(hashlib.sha256(sig).digest())

    raw = bytearray()
    raw += (1).to_bytes(2, "little")          # signature type
    raw += sig                                 # 512
    raw += owner                               # 512
    raw += b"\x00"                             # no target
    raw += b"\x00"                             # no anchor
    raw += len(tags).to_bytes(8, "little")
    raw += len(tags_bytes).to_bytes(8, "little")
    raw += tags_bytes
    raw += payload
    return item_id, bytes(raw)


# ─── upload ──────────────────────────────────────────────────────────────────

def upload(
    payload: bytes,
    wallet_path,
    tags: Optional[List[Tuple[str, str]]] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    """Sign `payload` as a data item and POST it to the first bundler that
    accepts it. Returns {txid, bundler, free_limit_exceeded?}. Raises on total
    failure (caller falls back)."""
    item_id, raw = create_data_item(payload, wallet_path, tags)
    errors = []
    for name, url in BUNDLERS:
        try:
            r = requests.post(
                url, data=raw,
                headers={"Content-Type": "application/octet-stream"},
                timeout=timeout,
            )
            if r.ok:
                return {"txid": item_id, "bundler": name}
            detail = (r.text or "")[:200]
            if r.status_code == 402:
                detail = f"payment required (free tier is <{FREE_LIMIT // 1024} KiB): {detail}"
            errors.append(f"{name}: {r.status_code} {detail}")
        except requests.RequestException as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError("all bundlers refused the data item — " + "; ".join(errors))
