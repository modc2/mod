"""
gemscan.semantic — meaning-based matching for typed themes.

A typed theme ("frog coins", "nuclear drones", "stuff like palantir") is
matched two ways: the word regex in gemscan.narratives (exact, fast, offline)
and here, by embedding similarity, so "humanoid robots" also finds Figure,
"anon" finds privacy coins and "frog" finds Pepe even when the word is absent.

Vectors come from the modsearch module (MiniLM through its POST /embed, ~1.5s
for 300 texts, 20ms for a query) over plain urllib, cached in-process by text.
Cosine is pure Python: 384 floats x a few hundred candidates is nothing.
When modsearch is down everything here returns None/{} and the scan falls
back to words only — the module stays keyless and stdlib-only.
"""
from __future__ import annotations

import json
import math
import os
import threading
import urllib.request
from typing import Optional

URL = os.environ.get('GEMFINDER_SEMANTIC', 'http://127.0.0.1:51090').rstrip('/')
BACKEND = os.environ.get('GEMFINDER_SEMANTIC_BACKEND', 'minilm')
TIMEOUT = float(os.environ.get('GEMFINDER_SEMANTIC_TIMEOUT', 25))
MAX_TEXT = 320
# MiniLM puts unrelated short texts around 0.0-0.15; a clear topical hit is 0.35+.
FLOOR = float(os.environ.get('GEMFINDER_SEMANTIC_FLOOR', 0.32))

_vec: dict = {}            # text -> unit vector
_lock = threading.Lock()
_state = {'ok': None, 'model': None, 'error': None}


def enabled() -> bool:
    return os.environ.get('GEMFINDER_SEMANTIC', '1') != '0'


def status() -> dict:
    return {'url': URL, 'backend': BACKEND, 'enabled': enabled(), 'cached_texts': len(_vec), **_state}


def doc_text(c: dict) -> str:
    """A stable blurb per candidate: name + the distinct words of what feeds said about it.
    Stable text = same vector across scans, so the cache hits even as signals come and go."""
    words, seen = [], set()
    for w in f"{c.get('name', '')} {c.get('text', '')}".split():
        k = w.lower().strip('.,;:()[]"\'')
        if k and k not in seen and len(k) > 1:
            seen.add(k)
            words.append(w.strip('.,;:()[]"\''))
    return f"{c.get('symbol', '')} {c.get('name', '')}. {' '.join(words)}"[:MAX_TEXT]


def _post(texts: list, kind: str) -> list:
    body = json.dumps({'texts': texts, 'backend': BACKEND, 'kind': kind}).encode()
    req = urllib.request.Request(f'{URL}/embed', data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        d = json.loads(r.read())
    _state.update({'ok': True, 'model': d.get('model'), 'error': None})
    return d['vectors']


def embed(texts: list, kind: str = 'document') -> Optional[list]:
    """Unit vectors for texts (None when the encoder is unavailable). Cached per text."""
    if not enabled() or not texts:
        return None if not enabled() else []
    with _lock:
        missing = [t for t in dict.fromkeys(texts) if t not in _vec]
    try:
        for i in range(0, len(missing), 256):
            chunk = missing[i:i + 256]
            vecs = _post(chunk, kind)
            with _lock:
                _vec.update(zip(chunk, vecs))
                if len(_vec) > 6000:  # a few scans' worth; drop the oldest half
                    for k in list(_vec)[:3000]:
                        del _vec[k]
    except Exception as e:
        _state.update({'ok': False, 'error': f'{type(e).__name__}: {e}'[:200]})
        return None
    with _lock:
        return [_vec[t] for t in texts]


def cos(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


def rank(query: str, docs: dict) -> Optional[dict]:
    """{id: text} -> {id: similarity 0..1} for a query, or None when the encoder is down."""
    if not docs:
        return {}
    ids = list(docs)
    qv = embed([query], 'query')
    dv = embed([docs[i] for i in ids], 'document')
    if not qv or dv is None:
        return None
    return {i: round(max(0.0, cos(qv[0], v)), 4) for i, v in zip(ids, dv)}


def prime(cands: dict) -> None:
    """Embed every candidate in the background so the first typed theme answers fast."""
    if not enabled():
        return
    texts = [doc_text(c) for c in cands.values()]
    threading.Thread(target=embed, args=(texts, 'document'), name='gemfinder-embed', daemon=True).start()
