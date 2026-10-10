"""
retrieval - the one scorer every memory layer is searched with

Memory is only worth keeping if it can be found again, so retrieval is a
component of its own rather than a method bolted onto each layer. Facts,
past turns and the step trail are all ranked here, by the same scorer, and
a hit comes back in the same shape no matter which layer it came from.

The scorer is BM25-shaped and dependency-free:

    idf         a word that appears in every document tells you nothing;
                a rare one is most of the signal
    saturation  the tenth occurrence of a word means little more than the
                second, so term frequency is damped
    length      a long document matching one word is a weaker hit than a
                short one matching the same word
    recency     a tie between two equally good matches goes to the newer

Scores are normalised to 0..1 — the fraction of the query's information the
document actually covers — so a caller can threshold them (`min_score`) and
mean the same thing on every layer.

Usage:
    hits = rank("what port does the relay use?", facts,
                text_of=lambda f: f['name'] + ' ' + f['content'])
    # -> [(0.82, {...}), (0.31, {...})]
"""
import math
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# words that carry no retrieval signal — dropped from queries and documents
# alike, so "what is the port" doesn't match every document with "the" in it
STOP = {
    'the', 'and', 'for', 'was', 'were', 'are', 'you', 'your', 'yours', 'our',
    'ours', 'this', 'that', 'these', 'those', 'with', 'from', 'have', 'has',
    'had', 'not', 'but', 'can', 'will', 'would', 'should', 'could', 'what',
    'when', 'where', 'which', 'who', 'whom', 'why', 'how', 'all', 'any',
    'its', 'it\'s', 'they', 'them', 'their', 'there', 'here', 'been', 'being',
    'about', 'into', 'over', 'then', 'than', 'some', 'just', 'like', 'get',
    'got', 'did', 'does', 'done', 'use', 'used', 'using', 'please', 'tell',
    'give', 'want', 'need', 'let', 'make', 'made',
}

K1 = 1.2          # term-frequency saturation
B = 0.75          # length normalisation strength
HALF_LIFE = 14 * 86400.0   # recency half-life, in seconds
RECENCY_WEIGHT = 0.15      # how much fresher-is-better may move a score

TextOf = Callable[[Any], str]
TsOf = Callable[[Any], Optional[float]]


def tokens(text: Any) -> List[str]:
    """Words worth matching on: alphanumeric, lowercased, 3+ chars, no stops.

    Kept as a list (not a set) because term frequency is part of the score.
    """
    words = ''.join(c.lower() if c.isalnum() else ' ' for c in str(text)).split()
    return [w for w in words if len(w) > 2 and w not in STOP]


def token_set(text: Any) -> set:
    """The distinct words in a text — the classic overlap view."""
    return set(tokens(text))


def rank(query: str,
         items: Iterable[Any],
         text_of: TextOf = str,
         ts_of: TsOf = None,
         k: int = 5,
         min_score: float = 0.0,
         now: float = None) -> List[Tuple[float, Any]]:
    """Rank items against a query, best first, as (score, item) pairs.

    Only items that match at least one query word are returned — an empty
    query, or one made entirely of stop words, matches nothing rather than
    everything. `k=None` returns every match.
    """
    q_terms = tokens(query)
    if not q_terms:
        return []
    items = list(items)
    if not items:
        return []

    docs: List[Tuple[Any, Dict[str, int], int]] = []
    total_len = 0
    for item in items:
        toks = tokens(text_of(item))
        tf: Dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        total_len += len(toks)
        docs.append((item, tf, len(toks)))
    avg_len = (total_len / len(docs)) or 1.0

    n_docs = len(docs)
    # document frequency over the candidate set: idf is relative to what is
    # actually being searched, which is the whole layer, not the shortlist
    df: Dict[str, int] = {}
    for _, tf, _ in docs:
        for t in tf:
            if t in q_terms:
                df[t] = df.get(t, 0) + 1

    seen = list(dict.fromkeys(q_terms))     # distinct query words, in order
    idf = {t: math.log(1 + (n_docs - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
           for t in seen}
    # normalise against what is *findable*, not against the whole question: a
    # word nothing in the store has ever seen ("how do I run the tests" when
    # the fact says "runs") is not evidence the best match is a weak one, and
    # counting it dragged every score toward zero for no reason.
    ideal = sum(w for t, w in idf.items() if df.get(t)) or sum(idf.values()) or 1.0

    now = now if now is not None else time.time()
    scored: List[Tuple[float, Any]] = []
    for item, tf, length in docs:
        norm = K1 * (1 - B + B * (length / avg_len)) if length else K1
        raw = 0.0
        for t in seen:
            f = tf.get(t, 0)
            if f:
                raw += idf[t] * (f / (f + norm))
        if raw <= 0:
            continue
        score = raw / ideal
        ts = ts_of(item) if ts_of else None
        if ts:
            age = max(0.0, now - float(ts))
            fresh = 0.5 ** (age / HALF_LIFE)          # 1 now, 0.5 a fortnight on
            score *= (1 - RECENCY_WEIGHT) + RECENCY_WEIGHT * fresh
        if score >= min_score:
            scored.append((score, item))
    scored.sort(key=lambda s: (-s[0], -(ts_of(s[1]) or 0 if ts_of else 0)))
    return scored if k is None else scored[:k]


def hit(layer: str, item: Dict[str, Any], score: float, text: str,
        id: str = None, name: str = None) -> Dict[str, Any]:
    """One retrieval result, in the shape every layer answers in.

    The layer is part of the hit because what a caller does with it differs:
    a fact is stated as true, a past turn is quoted, an episode is a step
    that was already taken.
    """
    return {
        'layer': layer,
        'id': id or item.get('id') or '',
        'name': name or item.get('name') or '',
        'text': text,
        'score': round(float(score), 3),
        'ts': item.get('ts') or item.get('updated'),
    }
