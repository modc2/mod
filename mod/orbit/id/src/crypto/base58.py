"""Base58 and Base58Check — Bitcoin's alphabet, also Solana's and Tron's."""
from __future__ import annotations

import hashlib

ALPHABET = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_INDEX = {char: value for value, char in enumerate(ALPHABET)}


def encode(data: bytes) -> str:
    number = int.from_bytes(data, 'big')
    out = ''
    while number > 0:
        number, remainder = divmod(number, 58)
        out = ALPHABET[remainder] + out
    leading = 0
    for byte in data:
        if byte:
            break
        leading += 1
    return ALPHABET[0] * leading + out


def decode(text: str) -> bytes:
    number = 0
    for char in text:
        if char not in _INDEX:
            raise ValueError(f'not base58: {char!r}')
        number = number * 58 + _INDEX[char]
    body = number.to_bytes((number.bit_length() + 7) // 8, 'big') if number else b''
    leading = 0
    for char in text:
        if char != ALPHABET[0]:
            break
        leading += 1
    return b'\x00' * leading + body


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def check_encode(payload: bytes) -> str:
    return encode(payload + _checksum(payload))


def check_decode(text: str) -> bytes:
    raw = decode(text)
    if len(raw) < 5:
        raise ValueError('base58check payload too short')
    payload, checksum = raw[:-4], raw[-4:]
    if _checksum(payload) != checksum:
        raise ValueError('base58check checksum mismatch')
    return payload
