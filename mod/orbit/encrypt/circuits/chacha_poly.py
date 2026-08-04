"""
chacha_poly — a second reference circuit, standard library only.

ChaCha20 (RFC 8439) written out in ~40 lines, keyed by PBKDF2-HMAC-SHA256 and
authenticated with a separate HMAC-SHA256 tag (encrypt-then-MAC). No third-party
packages, so it runs anywhere python does.

    wire format:  b'MCP1' | salt(16) | nonce(12) | ciphertext | hmac(32)

Params (optional):
    iterations   PBKDF2 rounds (default 200_000)

Hand-rolled crypto is exactly the thing you are told never to ship. It is here
because the module's job is to run *your* circuit, and a dependency-free one
makes that testable on any box. For anything real, bring a reviewed circuit.
"""
import hashlib
import hmac
import os
import struct

MAGIC = b'MCP1'
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 32


def _derive(key: bytes, salt: bytes, params: dict) -> bytes:
    """One PBKDF2 call, split into an encryption key and a MAC key."""
    material = hashlib.pbkdf2_hmac('sha256', key, salt,
                                   int(params.get('iterations', 200_000)), dklen=64)
    return material[:32], material[32:]


def _quarter(s, a, b, c, d):
    mask = 0xffffffff
    rot = lambda v, n: ((v << n) & mask) | (v >> (32 - n))
    s[a] = (s[a] + s[b]) & mask; s[d] = rot(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & mask; s[b] = rot(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & mask; s[d] = rot(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & mask; s[b] = rot(s[b] ^ s[c], 7)


def _block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = list(struct.unpack('<4I', b'expand 32-byte k')) \
        + list(struct.unpack('<8I', key)) + [counter] + list(struct.unpack('<3I', nonce))
    working = list(state)
    for _ in range(10):                      # 20 rounds = 10 double rounds
        _quarter(working, 0, 4, 8, 12);  _quarter(working, 1, 5, 9, 13)
        _quarter(working, 2, 6, 10, 14); _quarter(working, 3, 7, 11, 15)
        _quarter(working, 0, 5, 10, 15); _quarter(working, 1, 6, 11, 12)
        _quarter(working, 2, 7, 8, 13);  _quarter(working, 3, 4, 9, 14)
    return struct.pack('<16I', *[(w + s) & 0xffffffff for w, s in zip(working, state)])


def _stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 64):
        chunk = data[i:i + 64]
        out += bytes(a ^ b for a, b in zip(chunk, _block(key, 1 + i // 64, nonce)))
    return bytes(out)


def encrypt(data: bytes, key: bytes, params: dict) -> bytes:
    salt, nonce = os.urandom(SALT_BYTES), os.urandom(NONCE_BYTES)
    enc_key, mac_key = _derive(key, salt, params)
    body = MAGIC + salt + nonce + _stream(data, enc_key, nonce)
    return body + hmac.new(mac_key, body, hashlib.sha256).digest()


def decrypt(data: bytes, key: bytes, params: dict) -> bytes:
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + SALT_BYTES + NONCE_BYTES + TAG_BYTES:
        raise ValueError('not a chacha_poly ciphertext')
    body, tag = data[:-TAG_BYTES], data[-TAG_BYTES:]
    head = len(MAGIC)
    salt = body[head:head + SALT_BYTES]
    nonce = body[head + SALT_BYTES:head + SALT_BYTES + NONCE_BYTES]
    enc_key, mac_key = _derive(key, salt, params)
    if not hmac.compare_digest(hmac.new(mac_key, body, hashlib.sha256).digest(), tag):
        raise ValueError('authentication failed — wrong key or tampered ciphertext')
    return _stream(body[head + SALT_BYTES + NONCE_BYTES:], enc_key, nonce)
