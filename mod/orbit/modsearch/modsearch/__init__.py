"""
modsearch engine — rank short documents by what a query MEANS, not just the
words it shares with them.

Two scorers, fused:

  semantic   cosine between sentence vectors. The encoder is a BACKEND chain,
             first healthy one wins:
               liquid   LiquidAI/LFM2.5-Embedding-350M, served by the liquidai
                        module on this box (POST /retrieve/embed) — a retrieval-
                        trained, asymmetric (query:/document:) 1024-dim encoder
                        in its own process, so it never evicts liquidai's chat
                        model. Server-side inference; nothing leaves the box.
               minilm   all-MiniLM-L6-v2 in-process (384-dim, CPU) — the
                        fallback when liquidai is down.
  lexical    BM25 over the same text, so an exact name or a rare token
             ("0x6478", "sn51") still wins outright — embeddings are bad at
             identifiers.

No encoder at all → `mode: "lexical"`, BM25 alone. A search box that 500s is
worse than one that is merely literal.

EMBEDDING HASHES. Every document's vector is content-addressed:

    text_hash       sha256(text)                         — what was embedded
    embedding_hash  sha256(model \\0 kind \\0 float16(vec)) — the vector itself

so each mod has its own embedding hash: it changes exactly when the mod's
text or the encoder changes, two boxes running the same model can compare
mods by hash without shipping vectors, and the hub can tell "this mod was
re-described" from "the model moved". The fleet index (`mods.json`) keeps one
row per mod: {name, path, text_hash, embedding_hash, model, dim}.

Vectors are cached on disk by sha256(model \\0 kind \\0 text), so re-ranking the
fleet is one encode of the query. Queries are cached in RAM only — persisting
every keystroke would grow the file forever.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import re
import struct
import threading
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

STATE = Path(os.path.expanduser(os.environ.get('MODSEARCH_STATE', '~/.mod/modsearch')))
MINILM = os.environ.get('MODSEARCH_MODEL', 'sentence-transformers/all-MiniLM-L6-v2')
LIQUIDAI_URL = os.environ.get('MODSEARCH_LIQUIDAI', 'http://127.0.0.1:50460')
LIQUIDAI_AUTH = Path(os.environ.get(
    'MODSEARCH_LIQUIDAI_AUTH',
    Path(__file__).resolve().parents[2] / 'liquidai' / 'src' / 'api' / 'auth.py'))
# auto = liquid then minilm · liquid = liquid only · minilm = local only
BACKEND = os.environ.get('MODSEARCH_BACKEND', 'auto')
MAX_DOCS = 2000
MAX_TEXT = 1200

# Fusion weights. Semantic leads; lexical is the tiebreaker that rescues
# identifiers. The name bonus is for the query that IS a module name.
W_SEM, W_LEX, W_NAME = 0.72, 0.28, 0.25

_WORD = re.compile(r'[a-z0-9]+')
_STOP = frozenset('a an and are as at be by for from has i in is it its me my of on or that the this to with want need find show some any'.split())


def tokens(text: str) -> List[str]:
    return [w for w in _WORD.findall((text or '').lower()) if w not in _STOP]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def embedding_hash(model: str, kind: str, vec: List[float]) -> str:
    """Content address of a vector. float16 so the hash is of the meaning, not
    of the last decimal a JSON round-trip happened to keep."""
    raw = struct.pack(f'<{len(vec)}e', *vec)
    return hashlib.sha256(f'{model}\0{kind}\0'.encode() + raw).hexdigest()


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


# ── encoder backends ────────────────────────────────────────────────

class LiquidBackend:
    """LFM2.5-Embedding-350M via the liquidai module's retrieval slot.

    The token is minted from liquidai's own server secret, by loading its
    auth.py by FILE PATH (a module named `api` already on sys.modules — ours —
    would shadow `from api import auth`; see liquidai's mod.py for the same
    trap). Reading that 0600 secret is what makes this process the operator.
    """
    name = 'liquid'
    model = 'LiquidAI/LFM2.5-Embedding-350M'
    # LFM's cosine scale sits lower than MiniLM's: on the fleet, related blurbs
    # land ~0.2–0.45 and unrelated ones -0.1–0.12.
    floor = 0.14
    batch = 64

    def __init__(self, url: str = LIQUIDAI_URL, auth_path: Path = LIQUIDAI_AUTH):
        self.url = url.rstrip('/')
        self.auth_path = Path(auth_path)
        self._auth = None
        self._token: Tuple[str, float] = ('', 0.0)

    def _bearer(self) -> str:
        tok, exp = self._token
        if tok and exp > time.time() + 60:
            return tok
        if self._auth is None:
            spec = importlib.util.spec_from_file_location('modsearch_liquidai_auth', self.auth_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._auth = mod
        tok = self._auth.mint_local(3600)
        self._token = (tok, time.time() + 3600)
        return tok

    def embed(self, texts: List[str], kind: str) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i:i + self.batch]
            body = json.dumps({'texts': chunk, 'kind': kind}).encode()
            req = urllib.request.Request(self.url + '/retrieve/embed', data=body, headers={
                'Content-Type': 'application/json', 'Authorization': f'Bearer {self._bearer()}'})
            # First call pays the worker's load (~15 s); later batches ~1 s.
            with urllib.request.urlopen(req, timeout=300) as r:
                j = json.load(r)
            self.model = j.get('repo') or self.model
            out.extend(j['vectors'])
        return out


class MiniLMBackend:
    name = 'minilm'
    floor = 0.2           # MiniLM puts unrelated short texts around 0–0.15

    def __init__(self, model: str = MINILM):
        self.model = model
        self._st = None

    def embed(self, texts: List[str], kind: str) -> List[List[float]]:
        # Symmetric model: `kind` doesn't change the vector, but it stays in
        # the cache key so a backend swap can never mix the two.
        if self._st is None:
            from sentence_transformers import SentenceTransformer
            self._st = SentenceTransformer(self.model, device='cpu')
        vecs = self._st.encode(texts, batch_size=64, normalize_embeddings=True,
                               show_progress_bar=False)
        return [[float(x) for x in v] for v in vecs]


def default_backends(choice: str = BACKEND) -> list:
    if choice == 'liquid':
        return [LiquidBackend()]
    if choice == 'minilm':
        return [MiniLMBackend()]
    return [LiquidBackend(), MiniLMBackend()]


class Encoder:
    """A backend chain with an on-disk vector cache.

    One backend answers a whole search — docs and query from different models
    are not comparable — so `encode_search` picks the backend once. A backend
    that fails is skipped for COOLDOWN seconds, then tried again: liquidai
    coming back up puts search back on LFM without a restart.
    """
    COOLDOWN = 60
    CHUNK = 32

    def __init__(self, backends: Optional[list] = None, state: Path = STATE):
        self.backends = backends if backends is not None else default_backends()
        self.cache_path = state / 'vectors.jsonl'
        self._lock = threading.Lock()
        self._cache: Dict[str, List[float]] = {}
        self._loaded_cache = False
        self._queries: 'OrderedDict[str, List[float]]' = OrderedDict()
        self._down: Dict[str, Tuple[float, str]] = {}   # backend -> (until, error)
        self.active: Optional[object] = None

    # what the API / health report
    @property
    def model_name(self) -> Optional[str]:
        b = self.active or (self.backends[0] if self.backends else None)
        return b.model if b else None

    @property
    def error(self) -> Optional[str]:
        if self.active is not None and self.active.name not in self._down:
            return None
        errs = [f'{n}: {e}' for n, (_, e) in self._down.items()]
        return '; '.join(errs) or None

    def backend_state(self) -> List[dict]:
        now = time.time()
        return [{'name': b.name, 'model': b.model, 'active': b is self.active,
                 'down': (self._down.get(b.name, (0, ''))[0] > now),
                 'error': self._down.get(b.name, (0, None))[1]} for b in self.backends]

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

    @staticmethod
    def key(model: str, kind: str, text: str) -> str:
        return sha(f'{model}\0{kind}\0{text}')[:32]

    def _with(self, b, texts: List[str], kind: str, persist: bool) -> List[List[float]]:
        """Cached vectors for `texts` from backend `b`. Caller holds the lock."""
        keys = [self.key(b.model, kind, t) for t in texts]
        store = self._cache if persist else self._queries
        todo = [i for i, k in enumerate(keys) if k not in store]
        # Chunked, and each chunk persisted as it lands: a cold fleet on a
        # 350M CPU encoder is minutes of work, and a restart halfway through
        # must not throw the finished half away.
        for at in range(0, len(todo), self.CHUNK):
            part = todo[at:at + self.CHUNK]
            vecs = b.embed([texts[i] for i in part], kind)
            fresh = []
            for i, v in zip(part, vecs):
                row = [round(float(x), 5) for x in v]
                store[keys[i]] = row
                fresh.append((keys[i], row))
            if persist and fresh:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.cache_path, 'a') as f:
                    for k, row in fresh:
                        f.write(json.dumps([k, row]) + '\n')
        if todo:
            if not persist:
                while len(self._queries) > 2048:
                    self._queries.popitem(last=False)
        return [store[k] for k in keys]

    def _chain(self):
        now = time.time()
        for b in self.backends:
            until, _ = self._down.get(b.name, (0, ''))
            if until <= now:
                yield b

    def encode_search(self, texts: List[str], query: str):
        """→ (backend, doc vectors, query vector) or (None, None, None)."""
        with self._lock:
            self._read_cache()
            for b in self._chain():
                try:
                    docs = self._with(b, texts, 'document', persist=True)
                    q = self._with(b, [query], 'query', persist=False)[0]
                    self._down.pop(b.name, None)
                    self.active = b
                    return b, docs, q
                except Exception as e:  # backend down / no weights: fall through
                    self._down[b.name] = (time.time() + self.COOLDOWN,
                                          f'{type(e).__name__}: {e}'[:300])
            self.active = None
            return None, None, None

    def encode_docs(self, texts: List[str]):
        """→ (backend, vectors) for documents, cached on disk."""
        with self._lock:
            self._read_cache()
            for b in self._chain():
                try:
                    vecs = self._with(b, texts, 'document', persist=True)
                    self._down.pop(b.name, None)
                    self.active = b
                    return b, vecs
                except Exception as e:
                    self._down[b.name] = (time.time() + self.COOLDOWN,
                                          f'{type(e).__name__}: {e}'[:300])
            return None, None

    def embed(self, texts: List[str], kind: str = 'document',
              backend: Optional[str] = None):
        """Uncached vectors for callers that keep their own store (x402 holds
        37k rows in its SQLite). `backend` pins one; the default is the first
        healthy one. → (backend, vectors) or (None, None)."""
        with self._lock:
            for b in self._chain():
                if backend and b.name != backend:
                    continue
                try:
                    return b, [[round(float(x), 5) for x in v]
                               for v in b.embed([str(t)[:MAX_TEXT] for t in texts], kind)]
                except Exception as e:
                    self._down[b.name] = (time.time() + self.COOLDOWN,
                                          f'{type(e).__name__}: {e}'[:300])
            return None, None


def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm01(xs: List[float]) -> List[float]:
    hi = max(xs) if xs else 0.0
    return [x / hi if hi > 0 else 0.0 for x in xs]


# ── fused search ────────────────────────────────────────────────────

def search(query: str, docs: Iterable[dict], k: int = 50,
           encoder: Optional[Encoder] = None) -> dict:
    """Rank `docs` ([{id, text, name?}]) against `query`.

    Returns {mode, model, results: [{id, score, sem, lex, why, hash,
    text_hash}], elapsed_ms}. Only documents that clear the relevance floor
    come back — a semantic search that returns all 300 modules in some order
    has not searched anything.
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
    mode, backend, vecs = 'lexical', None, None
    if encoder:
        backend, vecs, qv = encoder.encode_search(texts, query)
    if backend is not None:
        try:
            import numpy as np
            sem = (np.asarray(vecs, dtype=np.float32) @ np.asarray(qv, dtype=np.float32)).tolist()
        except ImportError:
            sem = [_dot(qv, v) for v in vecs]
        mode = 'semantic'

    ql = query.lower().strip()
    top_sem = max(sem) if sem else 0.0
    # Floor: absolute (per model — cosine scales differ) and relative to the
    # best hit, so a vague query still shows its neighbourhood and a sharp one
    # doesn't drag in the tail.
    floor = max(backend.floor, top_sem * 0.6) if backend else 0.0
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
        row = {'id': d['id'], 'score': round(score, 4), 'sem': round(sem[i], 4),
               'lex': round(lex[i], 4), 'why': hit[:6]}
        if vecs is not None:
            row['hash'] = embedding_hash(backend.model, 'document', vecs[i])
            row['text_hash'] = sha(texts[i])
        results.append(row)
    results.sort(key=lambda r: -r['score'])
    return {'mode': mode, 'model': backend.model if backend else None,
            'backend': backend.name if backend else None,
            'error': (encoder.error if encoder and mode != 'semantic' else None),
            'results': results[:max(1, int(k))], 'considered': len(docs),
            'elapsed_ms': int((time.time() - t0) * 1000)}


# ── the fleet as a default corpus ───────────────────────────────────

def mod_text(name: str, description: str = '', fns: Iterable[str] = ()) -> str:
    """THE text a mod is embedded as: `name. description. fns[:12]`, compressed.

    No tree word — 'orbit'/'core' is where a mod lives, not what it does, and
    it put the same token in every vector. Compression collapses whitespace and
    drops repeats (a description that just restates the name, duplicate fns).
    The build hub builds the identical string (page.tsx `modText`), so both
    sides agree on the embedding hash and share this box's warmed vectors."""
    out, seen = [], set()
    for p in [name, description, *list(fns)[:12]]:
        p = ' '.join(str(p or '').split())
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return '. '.join(out)[:MAX_TEXT]


_DESC_RE = re.compile(r'^\s*description\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\'|"([^"\n]*)"|\'([^\'\n]*)\')', re.S | re.M)


def _is_mod_dir(d: Path) -> bool:
    """Same test the fleet uses: a config, a mod.py, or an api/app/server dir."""
    if d.name.startswith(('.', '_')) or not d.is_dir():
        return False
    return (d / 'config.json').is_file() or (d / 'mod.py').is_file() or \
        any((d / x).is_dir() for x in ('api', 'app', 'server'))


def _describe(d: Path) -> str:
    """Fallback blurb for a mod with no config description: its mod.py
    `description = ...`, else the first prose line of its README (markup,
    badges and a bare restated name don't count)."""
    lines = []
    try:
        m = _DESC_RE.search((d / 'mod.py').read_text(errors='ignore'))
        if m:
            lines.append(next(g for g in m.groups() if g is not None))
    except Exception:
        pass
    try:
        lines += (d / 'README.md').read_text(errors='ignore').splitlines()
    except Exception:
        pass
    for line in lines:
        line = line.strip().lstrip('#').strip()
        if line and line[0] not in '<![|`>-=*' and line.lower() != d.name.lower():
            return line
    return ''


def fleet_docs(roots: Iterable[Path]) -> List[dict]:
    """Every module under the given trees (one level deep), as search docs.

    One doc per name; a later root overrides an earlier one, so pass
    [orbit, core] and core wins a name collision, like the fleet does."""
    out: Dict[str, dict] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not _is_mod_dir(d):
                continue
            try:
                c = json.loads((d / 'config.json').read_text())
                c = c if isinstance(c, dict) else {}
            except Exception:
                c = {}
            name = str(c.get('name') or d.name)
            fns = c.get('fns') if isinstance(c.get('fns'), list) else []
            desc = str(c.get('description') or '') or _describe(d)
            out[name] = {'id': name, 'name': name, 'path': str(d),
                         'text': mod_text(name, desc, fns)}
    return list(out.values())


class ModIndex:
    """One row per fleet mod: its text hash and its embedding hash.

    Persisted to `mods.json` (no vectors — those live in the vector cache,
    addressable by the same key) so `GET /mods` answers without an encoder.
    """

    def __init__(self, roots: Iterable[Path], encoder: Encoder, state: Path = STATE):
        self.roots = list(roots)
        self.encoder = encoder
        self.path = state / 'mods.json'
        self._lock = threading.Lock()
        try:
            self.data = json.loads(self.path.read_text())
        except Exception:
            self.data = {'model': None, 'built_at': None, 'mods': {}}

    def rebuild(self) -> dict:
        docs = fleet_docs(self.roots)
        backend, vecs = self.encoder.encode_docs([d['text'] for d in docs])
        with self._lock:
            old = self.data.get('mods', {})
            mods = {}
            for i, d in enumerate(docs):
                th = sha(d['text'])
                row = {'name': d['name'], 'path': d['path'], 'text_hash': th}
                if vecs is not None:
                    row.update(embedding_hash=embedding_hash(backend.model, 'document', vecs[i]),
                               model=backend.model, dim=len(vecs[i]))
                elif d['name'] in old and old[d['name']].get('text_hash') == th:
                    row = old[d['name']]        # unchanged + encoder down: keep its hash
                prev = old.get(d['name'], {})
                row['changed_at'] = (prev.get('changed_at') if prev.get('embedding_hash') == row.get('embedding_hash')
                                     and prev.get('changed_at') else time.time())
                mods[d['name']] = row
            self.data = {'model': backend.model if backend else self.data.get('model'),
                         'backend': backend.name if backend else None,
                         'built_at': time.time(), 'count': len(mods), 'mods': mods}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.data, indent=1))
            tmp.replace(self.path)
            return {k: v for k, v in self.data.items() if k != 'mods'}

    def get(self, name: str, vector: bool = False) -> Optional[dict]:
        row = self.data.get('mods', {}).get(name)
        if not row:
            return None
        row = dict(row)
        if vector and row.get('model'):
            docs = {d['name']: d for d in fleet_docs(self.roots)}
            if name in docs:
                k = Encoder.key(row['model'], 'document', docs[name]['text'])
                self.encoder._read_cache()
                row['vector'] = self.encoder._cache.get(k)
        return row
