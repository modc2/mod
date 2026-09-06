"""Throwaway wallets, so the module can be shown working — and tested — with none.

Every chain in `chains.py` has a signer here that produces exactly what a real
wallet on that chain produces: the same digest, the same encoding, the same
extra bytes. That makes the test suite meaningful in both directions — the
verifier is checked against signatures made from the spec, and the signer is
checked against `eth_account` and `pynacl` where those exist.

These are not a keystore. Keys are made in memory, used, and dropped. Nothing
here writes a private key anywhere, and nothing in the rest of the module ever
asks for one.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from . import chains
from .crypto import base58, bech32, ed25519, secp256k1
from .crypto.keccak import keccak256


@dataclass
class Wallet:
    chain: str
    address: str
    sign: Callable[[str], str]
    pubkey: Optional[str] = None
    secret: Optional[str] = None

    @property
    def account(self) -> str:
        return f'{self.chain}:{self.address}'

    def proof(self, text: str) -> Dict[str, Any]:
        payload = {'signature': self.sign(text)}
        if self.pubkey:
            payload['pubkey'] = self.pubkey
        return payload


def _secp_secret(seed: Optional[bytes] = None) -> int:
    while True:
        value = int.from_bytes(seed or os.urandom(32), 'big') % secp256k1.N
        if value:
            return value
        seed = None


# ── secp256k1, keccak-flavoured ──────────────────────────────────────────

def _evm(secret: int, prefix: bytes) -> Callable[[str], str]:
    def sign(text: str) -> str:
        body = text.encode()
        digest = keccak256(prefix + str(len(body)).encode() + body)
        r, s, recovery = secp256k1.sign(digest, secret)
        return '0x' + (r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
                       + bytes([recovery + 27])).hex()
    return sign


def ethereum(seed: bytes = None) -> Wallet:
    secret = _secp_secret(seed)
    raw = secp256k1.uncompressed(secp256k1.public_key(secret))
    return Wallet('ethereum', '0x' + keccak256(raw)[-20:].hex(),
                  _evm(secret, b'\x19Ethereum Signed Message:\n'),
                  secret=hex(secret))


def tron(seed: bytes = None) -> Wallet:
    secret = _secp_secret(seed)
    raw = secp256k1.uncompressed(secp256k1.public_key(secret))
    address = base58.check_encode(b'\x41' + keccak256(raw)[-20:])
    return Wallet('tron', address, _evm(secret, b'\x19TRON Signed Message:\n'),
                  secret=hex(secret))


# ── secp256k1, bitcoin-flavoured ─────────────────────────────────────────

def _bitcoin_like(spec: chains.BitcoinLike, form: str, seed: bytes = None) -> Wallet:
    secret = _secp_secret(seed)
    compressed = secp256k1.compress(secp256k1.public_key(secret))
    address = chains._btc_addresses(compressed, spec)[form]
    header = {'p2pkh': 31, 'p2pkh-uncompressed': 27, 'p2sh-p2wpkh': 35, 'p2wpkh': 39}[form]

    def sign(text: str) -> str:
        body = text.encode()
        digest = chains.sha256d(chains.varint(len(spec.magic)) + spec.magic
                                + chains.varint(len(body)) + body)
        r, s, recovery = secp256k1.sign(digest, secret)
        return base64.b64encode(bytes([header + recovery]) + r.to_bytes(32, 'big')
                                + s.to_bytes(32, 'big')).decode()
    return Wallet(spec.name, address, sign, secret=hex(secret))


def bitcoin(form: str = 'p2wpkh', seed: bytes = None) -> Wallet:
    return _bitcoin_like(chains._BITCOIN, form, seed)


def litecoin(form: str = 'p2wpkh', seed: bytes = None) -> Wallet:
    return _bitcoin_like(chains._LITECOIN, form, seed)


def dogecoin(form: str = 'p2pkh', seed: bytes = None) -> Wallet:
    return _bitcoin_like(chains._DOGECOIN, form, seed)


# ── secp256k1, cosmos-flavoured ──────────────────────────────────────────

def cosmos(prefix: str = 'cosmos', seed: bytes = None) -> Wallet:
    secret = _secp_secret(seed)
    compressed = secp256k1.compress(secp256k1.public_key(secret))
    address = bech32.encode_data(prefix, chains.hash160(compressed))

    def sign(text: str) -> str:
        digest = hashlib.sha256(chains._adr036_document(address, text)).digest()
        r, s, _ = secp256k1.sign(digest, secret)
        return base64.b64encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big')).decode()
    return Wallet('cosmos', address, sign, pubkey=compressed.hex(), secret=hex(secret))


# ── ed25519 ──────────────────────────────────────────────────────────────

def solana(seed: bytes = None) -> Wallet:
    seed = seed or os.urandom(32)
    key = ed25519.public_key(seed)
    return Wallet('solana', base58.encode(key),
                  lambda text: base58.encode(ed25519.sign(text.encode(), seed)),
                  secret=seed.hex())


def sui(seed: bytes = None) -> Wallet:
    seed = seed or os.urandom(32)
    key = ed25519.public_key(seed)

    def sign(text: str) -> str:
        body = text.encode()
        intent = bytes([3, 0, 0]) + chains.uleb128(len(body)) + body
        signature = ed25519.sign(chains.blake2b256(intent), seed)
        return base64.b64encode(b'\x00' + signature + key).decode()
    return Wallet('sui', chains._sui_address(key), sign, secret=seed.hex())


def aptos(seed: bytes = None) -> Wallet:
    seed = seed or os.urandom(32)
    key = ed25519.public_key(seed)
    return Wallet('aptos', chains._aptos_address(key),
                  lambda text: '0x' + ed25519.sign(text.encode(), seed).hex(),
                  pubkey='0x' + key.hex(), secret=seed.hex())


def near(seed: bytes = None) -> Wallet:
    seed = seed or os.urandom(32)
    key = ed25519.public_key(seed)
    return Wallet('near', key.hex(),
                  lambda text: '0x' + ed25519.sign(text.encode(), seed).hex(),
                  secret=seed.hex())


def substrate(prefix: int = 42, seed: bytes = None) -> Wallet:
    seed = seed or os.urandom(32)
    key = ed25519.public_key(seed)
    return Wallet('substrate', chains.ss58_encode(key, prefix),
                  lambda text: '0x' + ed25519.sign(
                      b'<Bytes>' + text.encode() + b'</Bytes>', seed).hex(),
                  secret=seed.hex())


MAKERS: Dict[str, Callable[..., Wallet]] = {
    'ethereum': ethereum, 'tron': tron, 'bitcoin': bitcoin, 'litecoin': litecoin,
    'dogecoin': dogecoin, 'cosmos': cosmos, 'solana': solana, 'sui': sui,
    'aptos': aptos, 'near': near, 'substrate': substrate,
}


def make(chain: str, **options: Any) -> Wallet:
    """A throwaway wallet on any chain this module verifies."""
    name = chains.get(chain).name
    return MAKERS[name](**options)


def every() -> Dict[str, Wallet]:
    return {name: maker() for name, maker in MAKERS.items()}
