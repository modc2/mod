"""
Zcash key material: BIP39 mnemonics, BIP32 HD derivation, transparent addresses.

Transparent Zcash addresses are Bitcoin-style P2PKH/P2SH with two-byte version
prefixes (ZIP-32 / Zcash protocol spec §5.6.1):

    t1  P2PKH  0x1CB8      t3  P2SH  0x1CBD
    WIF        0x80        coin type (BIP44) 133

Shielded addresses are decoded for real here (see `sapling.py`): a `zs1`
address is checked against its bech32 checksum and split into diversifier and
transmission key, and a `u1` unified address is unjumbled into its receivers.
Paying one still needs a zk-SNARK prover, with one exception -- a unified
address that publishes a transparent receiver can be paid transparently, which
is exactly what that receiver is there for.
"""

import hashlib
import hmac
import os
import unicodedata
from pathlib import Path

import ecdsa
from ecdsa.ecdsa import generator_secp256k1
from ecdsa.ellipticcurve import INFINITY

try:
    from . import sapling as _sapling
except ImportError:  # loaded as a loose module
    import sapling as _sapling

CURVE = ecdsa.SECP256k1
CURVE_ORDER = CURVE.order
GEN = generator_secp256k1

# ── Address versions ────────────────────────────────────────────────────────
P2PKH_PREFIX = b"\x1c\xb8"   # t1
P2SH_PREFIX = b"\x1c\xbd"    # t3
WIF_PREFIX = b"\x80"
COIN_TYPE = 133              # BIP44 registered coin type for ZEC

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

_WORDLIST_PATH = Path(__file__).parent / "bip39_english.txt"
_wordlist_cache = None


# ── Hashing helpers ─────────────────────────────────────────────────────────

def sha256d(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def hash160(b: bytes) -> bytes:
    return hashlib.new("ripemd160", hashlib.sha256(b).digest()).digest()


# ── Base58Check ─────────────────────────────────────────────────────────────

def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    # leading zero bytes become '1'
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + out


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        idx = _B58.find(ch)
        if idx < 0:
            raise ValueError(f"invalid base58 character: {ch!r}")
        n = n * 58 + idx
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + body


def b58check_encode(payload: bytes) -> str:
    return b58encode(payload + sha256d(payload)[:4])


def b58check_decode(s: str) -> bytes:
    raw = b58decode(s)
    if len(raw) < 5:
        raise ValueError("base58check payload too short")
    payload, checksum = raw[:-4], raw[-4:]
    if sha256d(payload)[:4] != checksum:
        raise ValueError("bad base58check checksum")
    return payload


# ── Addresses ───────────────────────────────────────────────────────────────

def pubkey_to_address(pubkey: bytes) -> str:
    """Compressed secp256k1 public key -> t1 P2PKH address."""
    return b58check_encode(P2PKH_PREFIX + hash160(pubkey))


def script_to_address(script: bytes) -> str:
    """Redeem script -> t3 P2SH address."""
    return b58check_encode(P2SH_PREFIX + hash160(script))


def decode_address(addr: str) -> dict:
    """Parse any Zcash address. Returns {type, hash160?, script_pubkey?, spendable}.

    `spendable` means this module can construct and sign a spend of it.
    """
    a = addr.strip()
    if a.startswith("zs1"):
        pa = _sapling.decode_payment_address(a)       # raises on a bad checksum
        return {"type": "sapling", "address": a, "spendable": False,
                "pool": "sapling", "diversifier": pa.d.hex(),
                "reason": "paying a Sapling address requires Groth16 proving; "
                          "this module can receive and read Sapling notes but "
                          "not create them"}
    if a.startswith("u1"):
        return _decode_unified(a)
    if a.startswith("zc"):
        return {"type": "sprout", "address": a, "spendable": False,
                "reason": "Sprout pool is deprecated and cannot receive funds"}
    try:
        payload = b58check_decode(a)
    except ValueError as e:
        raise ValueError(f"unrecognised Zcash address {a!r}: {e}")
    version, h160 = payload[:2], payload[2:]
    if len(h160) != 20:
        raise ValueError(f"bad transparent address length for {a!r}")
    if version == P2PKH_PREFIX:
        return {"type": "p2pkh", "address": a, "hash160": h160,
                "script_pubkey": p2pkh_script(h160), "spendable": True}
    if version == P2SH_PREFIX:
        return {"type": "p2sh", "address": a, "hash160": h160,
                "script_pubkey": p2sh_script(h160), "spendable": False,
                "reason": "P2SH spends need the redeem script"}
    raise ValueError(f"unknown address version {version.hex()} for {a!r}")


_RECEIVER_NAMES = {0x00: "p2pkh", 0x01: "p2sh", 0x02: "sapling", 0x03: "orchard"}


def _decode_unified(a: str) -> dict:
    """A ZIP-316 unified address, resolved to the receiver we can actually pay.

    A sender is expected to use the best receiver it supports. This module
    supports the transparent ones, so a UA that publishes a P2PKH receiver is
    payable -- transparently, which the response says in as many words.
    """
    receivers = _sapling.decode_unified_address(a)     # raises if malformed
    kinds = [_RECEIVER_NAMES.get(tc, f"unknown({tc})") for tc, _ in receivers]
    out = {"type": "unified", "address": a, "receivers": kinds}
    for tc, data in receivers:
        if tc == _sapling.TYPECODE_P2PKH and len(data) == 20:
            out.update({
                "hash160": data, "script_pubkey": p2pkh_script(data),
                "spendable": True, "paid_receiver": "p2pkh",
                "transparent_address": b58check_encode(P2PKH_PREFIX + data),
                "note": "This unified address publishes a transparent "
                        "receiver, so the payment is a transparent one and is "
                        "visible on chain.",
            })
            return out
    shielded = [k for k in kinds if k in ("sapling", "orchard")]
    out.update({
        "spendable": False,
        "reason": f"this unified address only offers {', '.join(shielded) or 'unknown'} "
                  f"receivers; paying them needs zk-SNARK proving, which this "
                  f"module does not have",
    })
    return out


def is_valid_address(addr: str) -> bool:
    try:
        decode_address(addr)
        return True
    except ValueError:
        return False


def p2pkh_script(h160: bytes) -> bytes:
    """OP_DUP OP_HASH160 <20> OP_EQUALVERIFY OP_CHECKSIG"""
    return b"\x76\xa9\x14" + h160 + b"\x88\xac"


def p2sh_script(h160: bytes) -> bytes:
    """OP_HASH160 <20> OP_EQUAL"""
    return b"\xa9\x14" + h160 + b"\x87"


def address_to_script(addr: str) -> bytes:
    info = decode_address(addr)
    script = info.get("script_pubkey")
    if script is None:
        raise ValueError(
            f"cannot build an output script for {info['type']} address: "
            f"{info.get('reason', 'unsupported')}")
    return script


# ── WIF ─────────────────────────────────────────────────────────────────────

def privkey_to_wif(priv: bytes, compressed: bool = True) -> str:
    body = WIF_PREFIX + priv + (b"\x01" if compressed else b"")
    return b58check_encode(body)


def wif_to_privkey(wif: str) -> tuple:
    payload = b58check_decode(wif)
    if payload[:1] != WIF_PREFIX:
        raise ValueError("not a Zcash mainnet WIF")
    body = payload[1:]
    if len(body) == 33 and body[-1] == 1:
        return body[:-1], True
    if len(body) == 32:
        return body, False
    raise ValueError("bad WIF length")


def privkey_to_pubkey(priv: bytes, compressed: bool = True) -> bytes:
    sk = ecdsa.SigningKey.from_string(priv, curve=CURVE)
    return sk.get_verifying_key().to_string("compressed" if compressed else "uncompressed")


# ── BIP39 ───────────────────────────────────────────────────────────────────

def _wordlist() -> list:
    global _wordlist_cache
    if _wordlist_cache is None:
        _wordlist_cache = _WORDLIST_PATH.read_text().split()
        if len(_wordlist_cache) != 2048:
            raise RuntimeError("BIP39 wordlist is corrupt")
    return _wordlist_cache


def generate_mnemonic(strength: int = 128) -> str:
    if strength not in (128, 160, 192, 224, 256):
        raise ValueError("strength must be one of 128/160/192/224/256")
    return entropy_to_mnemonic(os.urandom(strength // 8))


def entropy_to_mnemonic(entropy: bytes) -> str:
    words = _wordlist()
    checksum_bits = len(entropy) * 8 // 32
    checksum = hashlib.sha256(entropy).digest()
    bits = int.from_bytes(entropy, "big") << checksum_bits
    bits |= int.from_bytes(checksum, "big") >> (256 - checksum_bits)
    total = len(entropy) * 8 + checksum_bits
    return " ".join(
        words[(bits >> (total - 11 * (i + 1))) & 0x7FF]
        for i in range(total // 11)
    )


def validate_mnemonic(mnemonic: str) -> bool:
    words = _wordlist()
    parts = unicodedata.normalize("NFKD", mnemonic).split()
    if len(parts) not in (12, 15, 18, 21, 24):
        return False
    try:
        idxs = [words.index(w) for w in parts]
    except ValueError:
        return False
    bits = 0
    for i in idxs:
        bits = (bits << 11) | i
    total = len(parts) * 11
    checksum_bits = total // 33
    ent_bits = total - checksum_bits
    entropy = (bits >> checksum_bits).to_bytes(ent_bits // 8, "big")
    expected = int.from_bytes(hashlib.sha256(entropy).digest(), "big") >> (256 - checksum_bits)
    return (bits & ((1 << checksum_bits) - 1)) == expected


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    m = unicodedata.normalize("NFKD", " ".join(mnemonic.split()))
    salt = unicodedata.normalize("NFKD", "mnemonic" + passphrase)
    return hashlib.pbkdf2_hmac("sha512", m.encode(), salt.encode(), 2048, 64)


# ── BIP32 ───────────────────────────────────────────────────────────────────

HARDENED = 0x80000000


class HDKey:
    """Minimal BIP32 extended private key over secp256k1."""

    __slots__ = ("priv", "chain_code", "depth", "index")

    def __init__(self, priv: bytes, chain_code: bytes, depth: int = 0, index: int = 0):
        self.priv = priv
        self.chain_code = chain_code
        self.depth = depth
        self.index = index

    @classmethod
    def from_seed(cls, seed: bytes) -> "HDKey":
        I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
        priv = I[:32]
        if int.from_bytes(priv, "big") == 0 or int.from_bytes(priv, "big") >= CURVE_ORDER:
            raise ValueError("invalid master key from seed")
        return cls(priv, I[32:])

    @property
    def pubkey(self) -> bytes:
        return privkey_to_pubkey(self.priv)

    def child(self, index: int) -> "HDKey":
        if index >= HARDENED:
            data = b"\x00" + self.priv + index.to_bytes(4, "big")
        else:
            data = self.pubkey + index.to_bytes(4, "big")
        I = hmac.new(self.chain_code, data, hashlib.sha512).digest()
        tweak = int.from_bytes(I[:32], "big")
        if tweak >= CURVE_ORDER:
            raise ValueError("derived tweak out of range; use the next index")
        k = (tweak + int.from_bytes(self.priv, "big")) % CURVE_ORDER
        if k == 0:
            raise ValueError("derived key is zero; use the next index")
        return HDKey(k.to_bytes(32, "big"), I[32:], self.depth + 1, index)

    def derive_path(self, path: str) -> "HDKey":
        node = self
        for part in path.strip().split("/"):
            if part in ("m", "M", ""):
                continue
            hardened = part.endswith(("'", "h", "H"))
            num = int(part.rstrip("'hH"))
            node = node.child(num + HARDENED if hardened else num)
        return node

    def address(self) -> str:
        return pubkey_to_address(self.pubkey)

    def wif(self) -> str:
        return privkey_to_wif(self.priv)


def account_path(account: int = 0, change: int = 0, index: int = 0) -> str:
    """BIP44 path for Zcash transparent keys."""
    return f"m/44'/{COIN_TYPE}'/{account}'/{change}/{index}"


def derive_account(seed: bytes, account: int = 0, change: int = 0, index: int = 0) -> HDKey:
    return HDKey.from_seed(seed).derive_path(account_path(account, change, index))


# ── Signing ─────────────────────────────────────────────────────────────────

def sign_digest(priv: bytes, digest: bytes) -> bytes:
    """RFC6979 deterministic ECDSA over a 32-byte digest, DER, canonical low-s."""
    sk = ecdsa.SigningKey.from_string(priv, curve=CURVE)
    return sk.sign_digest_deterministic(
        digest, hashfunc=hashlib.sha256, sigencode=ecdsa.util.sigencode_der_canonize)


def verify_digest(pubkey: bytes, digest: bytes, der_sig: bytes) -> bool:
    try:
        vk = ecdsa.VerifyingKey.from_string(pubkey, curve=CURVE)
        return vk.verify_digest(der_sig, digest, sigdecode=ecdsa.util.sigdecode_der)
    except Exception:
        return False
