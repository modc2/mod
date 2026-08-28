"""
aes_gcm — a reference circuit for the `encrypt` mod.

Authenticated encryption: scrypt stretches your passphrase into a 256-bit key,
AES-256-GCM encrypts and authenticates. A wrong key fails loudly (InvalidTag)
instead of returning garbage.

    wire format:  b'MEG1' | salt(16) | nonce(12) | ciphertext+tag

Params (optional, stored with the message so `open` reproduces them):
    n, r, p   scrypt cost parameters (default 2**15, 8, 1)

Requires the `cryptography` package on the server. This is an *example* — the
point of the module is that you bring your own. Read it, change it, or throw it
away and upload something better.
"""
import hashlib
import os

MAGIC = b'MEG1'
SALT_BYTES = 16
NONCE_BYTES = 12


def _derive(key: bytes, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(
        key, salt=salt,
        n=int(params.get('n', 2 ** 15)), r=int(params.get('r', 8)), p=int(params.get('p', 1)),
        maxmem=256 * 1024 * 1024, dklen=32,
    )


def encrypt(data: bytes, key: bytes, params: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_derive(key, salt, params)).encrypt(nonce, data, MAGIC)
    return MAGIC + salt + nonce + sealed


def decrypt(data: bytes, key: bytes, params: dict) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not data.startswith(MAGIC):
        raise ValueError('not an aes_gcm ciphertext (bad magic)')
    head = len(MAGIC)
    salt = data[head:head + SALT_BYTES]
    nonce = data[head + SALT_BYTES:head + SALT_BYTES + NONCE_BYTES]
    body = data[head + SALT_BYTES + NONCE_BYTES:]
    return AESGCM(_derive(key, salt, params)).decrypt(nonce, body, MAGIC)
