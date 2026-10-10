"""Text in, integers out — the step before every model in this module.

Real tokenizers learn their vocabulary from a corpus and ship a 500KB file of
it. This one hashes: a word's id is `hash(word) % vocab`, decided by arithmetic
rather than by a table, so there is no vocabulary file at all and any word — a
typo, a name, a word invented after the model was trained — still gets an id.

The cost is collisions. Two unrelated words landing on the same id are
indistinguishable to the model from that point on, and `collisions()` will tell
you how many the corpus you care about actually suffers. At 8192 buckets and a
few thousand distinct words that is a small number; at 256 buckets it is not,
and the search results get visibly worse. That trade is the lesson.

The hash is FNV-1a, written out, because `hash()` in Python is salted per
process and a model whose token ids change between runs is not a model.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List

import numpy as np

VOCAB = 8192
WORD = re.compile(r"[a-z0-9']+")

STOPWORDS = {
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'for', 'from', 'has',
    'have', 'in', 'is', 'it', 'its', 'of', 'on', 'or', 'that', 'the', 'to', 'was',
    'were', 'will', 'with',
}


def fnv1a(word: str) -> int:
    """32-bit FNV-1a. Same answer in every process, on every box, forever."""
    h = 0x811C9DC5
    for byte in word.encode('utf-8'):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


def words(text: str, drop_stopwords: bool = False) -> List[str]:
    found = WORD.findall(str(text).lower())
    return [w for w in found if w not in STOPWORDS] if drop_stopwords else found


def token_ids(text: str, vocab: int = VOCAB, drop_stopwords: bool = False) -> np.ndarray:
    ids = [fnv1a(w) % vocab for w in words(text, drop_stopwords)]
    return np.array(ids or [0], dtype=np.int64)      # never empty: graphs dislike it


def bag(text: str, vocab: int = VOCAB, drop_stopwords: bool = False) -> np.ndarray:
    """A counts-per-bucket vector, L2-normalised so sentence length cannot dominate.

    L2 rather than L1, and not only for cosine's sake: dividing by the sum makes
    each value about 1/8 for an eight-word sentence, the first layer's outputs
    come out near zero, half the ReLUs never fire and the network does not train
    at all. It is a good example of a scaling choice three files away from the
    training loop deciding whether the training loop works.
    """
    vector = np.zeros(vocab, dtype=np.float32)
    ids = token_ids(text, vocab, drop_stopwords)
    np.add.at(vector, ids, 1.0)
    length = float(np.linalg.norm(vector))
    return vector / length if length else vector


def collisions(texts: Iterable[str], vocab: int = VOCAB) -> Dict[str, object]:
    """How much this vocabulary size is costing you on this corpus."""
    buckets: Dict[int, set] = {}
    for text in texts:
        for word in words(text):
            buckets.setdefault(fnv1a(word) % vocab, set()).add(word)
    clashing = {b: sorted(w) for b, w in buckets.items() if len(w) > 1}
    distinct = sum(len(w) for w in buckets.values())
    return {
        'vocab': vocab,
        'distinct_words': distinct,
        'buckets_used': len(buckets),
        'colliding_buckets': len(clashing),
        'words_affected': sum(len(w) for w in clashing.values()),
        'collision_rate': round(sum(len(w) for w in clashing.values()) / distinct, 4)
        if distinct else 0.0,
        'examples': [v for v in list(clashing.values())[:5]],
    }


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """The only similarity in this module: the angle, ignoring length."""
    a, b = np.asarray(a, dtype=np.float64).ravel(), np.asarray(b, dtype=np.float64).ravel()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b / denom) if denom else 0.0
