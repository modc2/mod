"""
modsearch engine — rank short documents by what a query MEANS, not just the
words it shares with them.

Two scorers, fused:

  semantic   cosine between sentence vectors from a small local encoder
             (all-MiniLM-L6-v2, 384-dim, CPU, ~80 MB, no key, no network once
             cached). "chart my portfolio" finds the module whose blurb says
             "visualize holdings".
  lexical    BM25 over the same text, so an exact name or a rare token
             ("0x6478", "sn51") still wins outright — embeddings are bad at
             identifiers.

If the encoder cannot load (no torch, no cached weights) the engine says so
(`mode: "lexical"`) and keeps answering on BM25 alone rather than failing —
a search box that 500s is worse than one that is merely literal.

Vectors are cached by sha256(model + text) on disk, so re-ranking the same
fleet is one encode of the query, not of 300 blurbs.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

MODEL = os.environ.get('MODSEARCH_MODEL', 'sentence-transformers/all-MiniLM-L6-v2')
STATE = Path(os.path.expanduser(os.environ.get('MODSEARCH_STATE', '~/.mod/modsearch')))
MAX_DOCS = 2000
MAX_TEXT = 1200

# Fusion weights. Semantic leads; lexical is the tiebreaker that rescues
# identifiers. The name bonus is for the query that IS a module name.
W_SEM, W_LEX, W_NAME = 0.72, 0.28, 0.25

_WORD = re.compile(r'[a-z0-9]+')
_STOP = frozenset('a an and are as at be by for from has i in is it its me my of on or that the this to with want need find show some any'.split())


def tokens(text: str) -> List[str]:
    return [w for w in _WORD.findall((text or '').lower()) if w not in _STOP]


# ── lexical ─────────────────────────────────────────────────────────

def bm25(query: str, texts: List[str], k1: float = 1.4, b: float = 0.75) -> List[float]:
    q = set(tokens(query))
    if not q or not texts:
        return [0.0] * len(texts)
    docs = [tokens(t) for t in texts]
    n = len(docs)
    avg = sum(len(d) for d in docs) / n or 1.0
    df = {w: sum(1 for d in docs if w in d) for w in q}
    out = []
    for d in docs:
        tf: Dict[str, int] = {}
        for w in d:
            if w in q:
                tf[w] = tf.get(w, 0) + 1
        s = 0.0
        for w, f in tf.items():
            idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        out.append(s)
    return out


# ── semantic ────────────────────────────────────────────────────────

class Encoder:
    """Lazy sentence encoder with an on-disk vector cache. Thread-safe: the
    model is loaded once and encodes are serialized (torch on CPU gains
    nothing from two at once)."""

    def __init__(self, model: str = MODEL, state: Path = STATE):
        self.model_name = model
        self.cache_path = state / 'vectors.jsonl'
        self._model = None
        self._error: Optional[str] = None
        self._lock = threading.Lock()
        self._cache: Dict[str, List[float]] = {}
        self._loaded_cache = False

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _load(self) -> bool:
        if self._model is not None:
            return True
        if self._error:
            return False
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device='cpu')
            return True
        except Exception as e:  # no torch, no weights, no network to fetch them
            self._error = f'{type(e).__name__}: {e}'[:300]
            return False

    def _read_cache(self):
        if self._loaded_cache:
            return
        self._loaded_cache = True
        try:
            with open(self.cache_path) as f:
                for line in f:
                    try:
                        k, v = json.loads(line)
                        self._cache[k] = v
                    except Exception:
                        continue
        except FileNotFoundError:
            pass

    def _key(self, text: str) -> str:
        return hashlib.sha256(f'{self.model_name}\0{text}'.encode()).hexdigest()[:32]

    def encode(self, texts: List[str], persist: bool = True) -> Optional[List[List[float]]]:
        """Unit vectors for `texts`, or None when no encoder is available."""
        with self._lock:
            if not self._load():
                return None
            self._read_cache()
            keys = [self._key(t) for t in texts]
            todo = [i for i, k in enumerate(keys) if k not in self._cache]
            if todo:
                vecs = self._model.encode([texts[i] for i in todo], batch_size=64,
                                          normalize_embeddings=True, show_progress_bar=False)
                fresh = []
                for i, v in zip(todo, vecs):
                    row = [round(float(x), 5) for x in v]
                    self._cache[keys[i]] = row
                    fresh.append((keys[i], row))
                if persist and fresh:
                    self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.cache_path, 'a') as f:
                        for k, row in fresh:
                            f.write(json.dumps([k, row]) + '\n')
            return [self._cache[k] for k in keys]


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm01(xs: List[float]) -> List[float]:
    hi = max(xs) if xs else 0.0
    return [x / hi if hi > 0 else 0.0 for x in xs]


# ── fused search ────────────────────────────────────────────────────

def search(query: str, docs: Iterable[dict], k: int = 50,
           encoder: Optional[Encoder] = None) -> dict:
    """Rank `docs` ([{id, text, name?}]) against `query`.

    Returns {mode, results: [{id, score, sem, lex, why}], elapsed_ms}. Only
    documents that clear the relevance floor come back — a semantic search
    that returns all 300 modules in some order has not searched anything.
    """
    t0 = time.time()
    query = (query or '').strip()[:400]
    docs = [d for d in docs if d.get('id') is not None][:MAX_DOCS]
    if not query or not docs:
        return {'mode': 'empty', 'results': [], 'elapsed_ms': 0}
    texts = [str(d.get('text') or d.get('id'))[:MAX_TEXT] for d in docs]
    names = [str(d.get('name') or d.get('id')).lower() for d in docs]
    qtok = tokens(query)

    lex = _norm01(bm25(query, texts))
    sem = [0.0] * len(docs)
    mode = 'lexical'
    vecs = encoder.encode(texts + [query]) if encoder else None
    if vecs:
        try:
            import numpy as np
            sem = (np.asarray(vecs[:-1], dtype=np.float32) @ np.asarray(vecs[-1], dtype=np.float32)).tolist()
        except ImportError:
            sem = [_dot(vecs[-1], v) for v in vecs[:-1]]
        mode = 'semantic'

    ql = query.lower().strip()
    top_sem = max(sem) if sem else 0.0
    # Floor: absolute (MiniLM puts unrelated short texts around 0–0.15) and
    # relative to the best hit, so a vague query still shows its neighbourhood
    # and a sharp one doesn't drag in the tail.
    floor = max(0.2, top_sem * 0.6)
    results = []
    for i, d in enumerate(docs):
        name_hit = W_NAME if ql == names[i] else (W_NAME / 2 if ql and ql in names[i] else 0.0)
        if mode == 'semantic':
            score = W_SEM * max(sem[i], 0.0) + W_LEX * lex[i] + name_hit
            keep = sem[i] >= floor or lex[i] >= 0.5 or name_hit > 0
        else:
            score = lex[i] + name_hit
            keep = score > 0
        if not keep:
            continue
        hit = sorted({w for w in qtok if w in tokens(texts[i])})
        results.append({'id': d['id'], 'score': round(score, 4), 'sem': round(sem[i], 4),
                        'lex': round(lex[i], 4), 'why': hit[:6]})
    results.sort(key=lambda r: -r['score'])
    return {'mode': mode, 'model': encoder.model_name if mode == 'semantic' else None,
            'error': (encoder.error if encoder and mode != 'semantic' else None),
            'results': results[:max(1, int(k))], 'considered': len(docs),
            'elapsed_ms': int((time.time() - t0) * 1000)}


# ── the fleet as a default corpus ───────────────────────────────────

def fleet_docs(roots: Iterable[Path]) -> List[dict]:
    """Every module under the given trees (one level deep), as search docs:
    name + description + a README's first paragraph."""
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            cfg = d / 'config.json'
            if not cfg.is_file():
                continue
            try:
                c = json.loads(cfg.read_text())
            except Exception:
                continue
            name = c.get('name') or d.name
            desc = str(c.get('description') or '')
            readme = ''
            for r in ('README.md', 'readme.md'):
                p = d / r
                if p.is_file():
                    body = [ln for ln in p.read_text(errors='ignore').splitlines()
                            if ln.strip() and not ln.startswith(('#', '```', '!['))]
                    readme = ' '.join(body[:4])
                    break
            out.append({'id': name, 'name': name, 'path': str(d),
                        'text': f'{name}. {desc} {readme}'.strip()})
    return out
