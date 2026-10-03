"""Bech32 and Bech32m — BIP-173/BIP-350 addresses, and every Cosmos address.

Reference algorithm. The only thing worth remembering when reading it: a
witness-v0 address carries a bech32 checksum and a witness-v1 (taproot) address
carries a bech32m one, and using the wrong constant is the whole point of the
split. Cosmos uses plain bech32 with a chain prefix and no witness version.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
BECH32 = 1
BECH32M = 0x2BC830A3


def _polymod(values: List[int]) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    check = 1
    for value in values:
        top = check >> 25
        check = ((check & 0x1FFFFFF) << 5) ^ value
        for i in range(5):
            check ^= generator[i] if ((top >> i) & 1) else 0
    return check


def _hrp_expand(hrp: str) -> List[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _constant(hrp: str, data: List[int]) -> Optional[int]:
    value = _polymod(_hrp_expand(hrp) + data)
    return value if value in (BECH32, BECH32M) else None


def encode(hrp: str, data: List[int], spec: int = BECH32) -> str:
    combined = data + _checksum(hrp, data, spec)
    return hrp + '1' + ''.join(CHARSET[d] for d in combined)


def _checksum(hrp: str, data: List[int], spec: int) -> List[int]:
    values = _hrp_expand(hrp) + data + [0, 0, 0, 0, 0, 0]
    polymod = _polymod(values) ^ spec
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def decode(text: str) -> Tuple[Optional[str], Optional[List[int]], Optional[int]]:
    if any(ord(c) < 33 or ord(c) > 126 for c in text):
        return None, None, None
    if text.lower() != text and text.upper() != text:
        return None, None, None
    text = text.lower()
    position = text.rfind('1')
    if position < 1 or position + 7 > len(text) or len(text) > 108:
        return None, None, None
    if not all(c in CHARSET for c in text[position + 1:]):
        return None, None, None
    hrp = text[:position]
    data = [CHARSET.find(c) for c in text[position + 1:]]
    spec = _constant(hrp, data)
    if spec is None:
        return None, None, None
    return hrp, data[:-6], spec


def convertbits(data, from_bits: int, to_bits: int, pad: bool = True) -> Optional[List[int]]:
    acc = 0
    bits = 0
    out: List[int] = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or (value >> from_bits):
            return None
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        return None
    return out


def encode_segwit(hrp: str, witness_version: int, program: bytes) -> str:
    spec = BECH32 if witness_version == 0 else BECH32M
    converted = convertbits(program, 8, 5)
    return encode(hrp, [witness_version] + converted, spec)


def decode_segwit(hrp: str, address: str) -> Tuple[Optional[int], Optional[bytes]]:
    got_hrp, data, spec = decode(address)
    if got_hrp != hrp or not data:
        return None, None
    program = convertbits(data[1:], 5, 8, False)
    if program is None or not 2 <= len(program) <= 40:
        return None, None
    if data[0] > 16:
        return None, None
    if data[0] == 0 and len(program) not in (20, 32):
        return None, None
    if spec != (BECH32 if data[0] == 0 else BECH32M):
        return None, None
    return data[0], bytes(program)


def encode_data(hrp: str, payload: bytes) -> str:
    """Cosmos-style: no witness version, plain bech32 over the 8-to-5 conversion."""
    return encode(hrp, convertbits(payload, 8, 5), BECH32)


def decode_data(address: str) -> Tuple[Optional[str], Optional[bytes]]:
    hrp, data, spec = decode(address)
    if hrp is None or spec != BECH32:
        return None, None
    payload = convertbits(data, 5, 8, False)
    return (hrp, bytes(payload)) if payload is not None else (None, None)
