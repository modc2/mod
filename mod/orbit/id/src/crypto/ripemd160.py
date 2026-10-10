"""RIPEMD-160 — needed for every Bitcoin-shaped address, and gone from hashlib.

OpenSSL 3 moved RIPEMD-160 into the legacy provider, so `hashlib.new('ripemd160')`
raises on most modern hosts. Bitcoin, Litecoin, Dogecoin and every Cosmos chain
hash their public keys with it, so a module that verifies those addresses has to
carry its own. This is the reference algorithm; the tests check it against the
published vectors and against hashlib wherever hashlib still has it.
"""
from __future__ import annotations

import struct

_MASK = 0xFFFFFFFF

_RL = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
    (7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8),
    (3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12),
    (1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2),
    (4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13),
)
_RR = (
    (5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12),
    (6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2),
    (15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13),
    (8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14),
    (12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11),
)
_SL = (
    (11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8),
    (7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12),
    (11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5),
    (11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12),
    (9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6),
)
_SR = (
    (8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6),
    (9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11),
    (9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5),
    (15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8),
    (8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11),
)
_KL = (0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E)
_KR = (0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000)


def _rol(value: int, count: int) -> int:
    value &= _MASK
    return ((value << count) | (value >> (32 - count))) & _MASK


def _mix(round_index: int, x: int, y: int, z: int) -> int:
    if round_index == 0:
        return x ^ y ^ z
    if round_index == 1:
        return (x & y) | (~x & _MASK & z)
    if round_index == 2:
        return (x | (~y & _MASK)) ^ z
    if round_index == 3:
        return (x & z) | (y & (~z & _MASK))
    return x ^ (y | (~z & _MASK))


def _compress(state, block):
    words = struct.unpack('<16I', block)
    al, bl, cl, dl, el = state
    ar, br, cr, dr, er = state
    for rnd in range(5):
        for i in range(16):
            t = (al + _mix(rnd, bl, cl, dl) + words[_RL[rnd][i]] + _KL[rnd]) & _MASK
            t = (_rol(t, _SL[rnd][i]) + el) & _MASK
            al, bl, cl, dl, el = el, t, bl, _rol(cl, 10), dl
            t = (ar + _mix(4 - rnd, br, cr, dr) + words[_RR[rnd][i]] + _KR[rnd]) & _MASK
            t = (_rol(t, _SR[rnd][i]) + er) & _MASK
            ar, br, cr, dr, er = er, t, br, _rol(cr, 10), dr
    return (
        (state[1] + cl + dr) & _MASK,
        (state[2] + dl + er) & _MASK,
        (state[3] + el + ar) & _MASK,
        (state[4] + al + br) & _MASK,
        (state[0] + bl + cr) & _MASK,
    )


def ripemd160(data: bytes) -> bytes:
    state = (0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0)
    message = bytearray(data)
    length = len(message) * 8
    message.append(0x80)
    while len(message) % 64 != 56:
        message.append(0x00)
    message += struct.pack('<Q', length & 0xFFFFFFFFFFFFFFFF)
    for offset in range(0, len(message), 64):
        state = _compress(state, bytes(message[offset:offset + 64]))
    return b''.join(struct.pack('<I', word) for word in state)
