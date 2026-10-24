"""
The Monero 25-word mnemonic.

Monero does not use BIP39. Its seed phrase is 24 words encoding 32 bytes
directly (three words per 4-byte group, base 1626) plus a 25th checksum word
derived from the CRC32 of the first three letters of each word. Words are
uniquely identified by that 3-letter prefix, which is why a phrase written
down with a typo in a word's tail still restores.

The word list is the one shipped with monero-project/monero
(src/mnemonics/english.h), kept verbatim in english.txt.
"""

import os
import zlib
from pathlib import Path

try:
    from . import crypto
except ImportError:  # loaded as a loose module by the mod runtime
    import crypto

PREFIX_LEN = 3
RADIX = 1626
_WORDS = None
_INDEX = None


class MnemonicError(Exception):
    pass


def words() -> list:
    global _WORDS, _INDEX
    if _WORDS is None:
        path = Path(__file__).parent / "english.txt"
        if not path.exists():
            raise MnemonicError(f"word list missing at {path}")
        _WORDS = path.read_text().split()
        if len(_WORDS) != RADIX:
            raise MnemonicError(
                f"word list has {len(_WORDS)} words, expected {RADIX}")
        _INDEX = {w[:PREFIX_LEN]: i for i, w in enumerate(_WORDS)}
    return _WORDS


def _index_of(word: str) -> int:
    words()
    key = word.strip().lower()[:PREFIX_LEN]
    if key not in _INDEX:
        raise MnemonicError(f"{word!r} is not in the Monero word list")
    return _INDEX[key]


def checksum_word(phrase_words: list) -> str:
    """The 25th word: CRC32 over the 3-letter prefixes picks one of the 24."""
    trimmed = "".join(w[:PREFIX_LEN] for w in phrase_words)
    return phrase_words[zlib.crc32(trimmed.encode()) % len(phrase_words)]


def encode(seed: bytes) -> str:
    """32-byte seed -> 25 words."""
    if len(seed) != 32:
        raise MnemonicError(f"a seed is 32 bytes, got {len(seed)}")
    wl = words()
    out = []
    for i in range(0, 32, 4):
        val = int.from_bytes(seed[i:i + 4], "little")
        w1 = val % RADIX
        w2 = (val // RADIX + w1) % RADIX
        w3 = (val // RADIX // RADIX + w2) % RADIX
        out += [wl[w1], wl[w2], wl[w3]]
    return " ".join(out + [checksum_word(out)])


def decode(phrase: str) -> bytes:
    """25 (or 24) words -> the 32-byte seed."""
    parts = (phrase or "").split()
    if len(parts) not in (24, 25):
        raise MnemonicError(
            f"a Monero seed phrase is 25 words (24 without the checksum), got {len(parts)}")
    body = parts[:24]
    if len(parts) == 25:
        expected = checksum_word(body)
        if expected[:PREFIX_LEN] != parts[24].strip().lower()[:PREFIX_LEN]:
            raise MnemonicError(
                "checksum word does not match -- a word is wrong or out of order")

    seed = bytearray()
    for i in range(0, 24, 3):
        w1, w2, w3 = (_index_of(w) for w in body[i:i + 3])
        val = w1 + RADIX * ((w2 - w1) % RADIX) + RADIX * RADIX * ((w3 - w2) % RADIX)
        if val % RADIX != w1:
            raise MnemonicError(f"word group {i // 3 + 1} does not decode consistently")
        if val >= 1 << 32:
            raise MnemonicError(f"word group {i // 3 + 1} overflows 4 bytes")
        seed += int.to_bytes(val, 4, "little")
    return bytes(seed)


def generate() -> str:
    """A fresh phrase from the OS entropy source.

    The seed is reduced mod l first so the phrase always round-trips to the
    same spend key the wallet will actually use.
    """
    return encode(crypto.sc_reduce32(os.urandom(32)))


def is_valid(phrase: str) -> bool:
    try:
        decode(phrase)
        return True
    except MnemonicError:
        return False


def self_test() -> dict:
    """Round-trip a seed, and prove the checksum actually catches a bad word."""
    seed = crypto.sc_reduce32(crypto.keccak256(b"monero-mnemonic-self-test"))
    phrase = encode(seed)
    round_trip = decode(phrase) == seed

    parts = phrase.split()
    swapped = " ".join(parts[1:2] + parts[0:1] + parts[2:])
    caught = not is_valid(swapped)

    return {"ok": round_trip and caught and len(parts) == 25,
            "words": len(parts), "round_trip": round_trip,
            "checksum_catches_swap": caught}
