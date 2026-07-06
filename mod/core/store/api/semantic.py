"""
1-bit semantic encoder — a fully LOCAL (no network, no model download, pure
stdlib) binary semantic hash for store objects.

Each object gets a fixed-width **binary latent vector** (default 64 bits). The
encoder is a SimHash / random-hyperplane LSH: content is turned into a weighted
bag of features (word tokens + character n-grams for text, byte n-grams for
binary), each feature deterministically projects onto every bit axis via a
BLAKE2b digest, and the sign of the per-axis accumulation becomes that bit.

Why this shape:
  • cosine-similar content → small **Hamming distance** between fingerprints, so
    semantic / near-duplicate search is just popcount(a ^ b) — O(bits) per pair,
    trivially fast to scan over thousands of objects (and LSH-bandable later);
  • deterministic + dependency-free + offline → runs anywhere the module runs;
  • the fingerprint is short (16 hex chars at 64 bits) so it can be shown in the
    UI as the object's "semantic hash".

This is a similarity fingerprint, NOT a learned transformer embedding — it
captures lexical/structural semantics, which is what you can do locally with
zero dependencies. The interface (encode → int, hamming, similarity) would let a
heavier local model be swapped in behind it later without changing callers.
"""
import hashlib
import re
from collections import Counter

DEFAULT_BITS = 64

_WORD = re.compile(r"[a-z0-9]+")


def _text_tokens(text: str) -> list:
    text = text.lower()
    words = _WORD.findall(text)
    # char 3-grams give signal on short / out-of-vocabulary text too.
    grams = [text[i:i + 3] for i in range(len(text) - 2)] if len(text) >= 3 else []
    return words + grams


def _byte_tokens(data: bytes, n: int = 4, step: int = 2, cap: int = 8192) -> list:
    """Overlapping byte n-grams across (a capped prefix of) binary content."""
    end = min(len(data), cap * step)
    return [data[i:i + n].hex() for i in range(0, max(0, end - n + 1), step)]


def _features(data) -> Counter:
    if isinstance(data, (bytes, bytearray)):
        data = bytes(data)
        try:
            toks = _text_tokens(data.decode("utf-8"))
            if not toks:
                toks = _byte_tokens(data)
        except UnicodeDecodeError:
            toks = _byte_tokens(data)
    else:
        toks = _text_tokens(str(data))
    return Counter(toks)


def encode(data, bits: int = DEFAULT_BITS) -> int:
    """Return the integer binary fingerprint of `data` (str or bytes)."""
    feats = _features(data)
    if not feats:
        return 0
    nbytes = (bits + 7) // 8
    acc = [0] * bits
    for term, w in feats.items():
        h = int.from_bytes(hashlib.blake2b(term.encode("utf-8"), digest_size=nbytes).digest(), "big")
        for b in range(bits):
            acc[b] += w if (h >> b) & 1 else -w
    fp = 0
    for b in range(bits):
        if acc[b] > 0:
            fp |= (1 << b)
    return fp


def to_hex(fp: int, bits: int = DEFAULT_BITS) -> str:
    return format(fp, "0{}x".format((bits + 3) // 4))


def from_hex(s: str) -> int:
    return int(s, 16) if s else 0


def encode_hex(data, bits: int = DEFAULT_BITS) -> str:
    return to_hex(encode(data, bits), bits)


def encode_file(path: str, bits: int = DEFAULT_BITS, cap: int = 1 << 20) -> str:
    """Encode a file's content (reads up to `cap` bytes). Returns hex; '' on error."""
    try:
        with open(path, "rb") as f:
            return encode_hex(f.read(cap), bits)
    except Exception:
        return ""


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def similarity(a: int, b: int, bits: int = DEFAULT_BITS) -> float:
    """Cosine-correlated similarity in [0,1] = 1 − Hamming/bits."""
    return 1.0 - hamming(a, b) / bits
