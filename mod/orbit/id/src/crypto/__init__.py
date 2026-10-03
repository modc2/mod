"""Every primitive the proof checker needs, written here rather than imported.

Verifying that someone controls a wallet is the one thing this module cannot be
wrong about, so the path from a signature to an address runs entirely through
this package: Keccak-256, RIPEMD-160, secp256k1 recovery, Ed25519, Base58 and
Bech32. Nothing here needs to be installed, which means an `id` document can be
re-checked on any host with a Python interpreter and no network — including
years from now, when the wallet SDK that produced the signature is gone.

The test suite pins each primitive against the reference implementation
(`eth_hash`, `pynacl`, `hashlib`) on hosts where those happen to exist.
"""
from . import base58, bech32, ed25519, keccak, ripemd160, secp256k1  # noqa: F401
from .keccak import keccak256  # noqa: F401
from .ripemd160 import ripemd160 as ripemd160_digest  # noqa: F401

__all__ = ['base58', 'bech32', 'ed25519', 'keccak', 'ripemd160', 'secp256k1',
           'keccak256', 'ripemd160_digest']
