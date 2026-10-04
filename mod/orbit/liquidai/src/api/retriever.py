"""RETRIEVER — a second resident slot, for search embeddings only.

server_rt keeps ONE model resident, and that ceiling is right for chat. It is
wrong for search: a semantic search box fires on every keystroke, and if each
query evicted the resident chat model (and the next chat evicted the encoder)
the box would spend its life reloading weights while arena matches died
mid-game. So retrieval gets its own slot, its own lock and its own model:

    LiquidAI/LFM2.5-Embedding-350M   dense bi-encoder, 1024-dim CLS vector,
                                     cosine, 512 tokens, 11 languages

It is loaded through sentence-transformers, not server_rt's AutoModel + mean
pool, because the model card is specific and both shortcuts are wrong for it:

  * the repo ships `modeling_lfm2_bidirectional.py` — without
    trust_remote_code the backbone loads CAUSAL and every vector is a
    left-to-right summary;
  * pooling is the CLS token, not the mean;
  * it is asymmetric — queries are prefixed `query: `, passages
    `document: ` (stored as prompts in the repo's ST config). Omitting them
    "silently degrades retrieval quality".

`kind` on every call is therefore not optional decoration: a query encoded as
a document lands in the wrong neighbourhood.

~1.4 GB of RAM in fp32 (this CPU has no bf16 path worth using). The slot
loads on first use and stays; POST /retrieve/unload gives it back.
"""

import os
import threading
import time
from typing import Any, Dict, List, Optional

REPO = os.environ.get("LIQUIDAI_RETRIEVER", "LiquidAI/LFM2.5-Embedding-350M")
KINDS = ("query", "document")
MAX_TEXTS = 256
MAX_CHARS = 4000            # ~512 tokens is the model's ceiling; trim before the tokenizer

_LOCK = threading.Lock()    # residency AND runs: one encoder, CPU-bound, serial is fastest
_SLOT: Dict[str, Any] = {"repo": None, "model": None, "dim": None,
                         "prompts": {}, "loaded_at": None, "load_sec": None,
                         "error": None, "calls": 0, "texts": 0}


def status() -> Dict[str, Any]:
    """Lock-free, like server_rt.loaded() — a status read never queues."""
    return {"repo": _SLOT["repo"] or REPO, "resident": _SLOT["model"] is not None,
            "dim": _SLOT["dim"], "prompts": sorted(_SLOT["prompts"]),
            "loaded_at": _SLOT["loaded_at"], "load_sec": _SLOT["load_sec"],
            "error": _SLOT["error"], "calls": _SLOT["calls"], "texts": _SLOT["texts"]}


def _load(repo: str):
    if _SLOT["model"] is not None and _SLOT["repo"] == repo:
        return _SLOT["model"]
    from sentence_transformers import SentenceTransformer

    _SLOT.update(model=None, repo=None)
    started = time.time()
    try:
        model = SentenceTransformer(repo, device="cpu", trust_remote_code=True)
    except Exception as e:
        _SLOT["error"] = f"{type(e).__name__}: {e}"[:400]
        raise
    model.eval()
    _SLOT.update(repo=repo, model=model, error=None,
                 dim=int(model.get_sentence_embedding_dimension() or 0) or None,
                 prompts=dict(getattr(model, "prompts", None) or {}),
                 loaded_at=time.time(), load_sec=round(time.time() - started, 2))
    return model


def embed(texts: List[str], kind: str = "document",
          repo: Optional[str] = None) -> Dict[str, Any]:
    """Unit vectors for `texts`, encoded as `kind` (query | document)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    texts = [str(t)[:MAX_CHARS] for t in texts]
    if not texts:
        raise ValueError("nothing to embed")
    if len(texts) > MAX_TEXTS:
        raise ValueError(f"at most {MAX_TEXTS} texts per call")
    with _LOCK:
        model = _load(repo or REPO)
        started = time.time()
        # The repo's own prompt when it has one; the card's literal prefix when
        # an older sentence-transformers didn't read the prompts block.
        if kind in _SLOT["prompts"]:
            vecs = model.encode(texts, prompt_name=kind, batch_size=32,
                                normalize_embeddings=True, show_progress_bar=False)
        else:
            vecs = model.encode([f"{kind}: {t}" for t in texts], batch_size=32,
                                normalize_embeddings=True, show_progress_bar=False)
        _SLOT["calls"] += 1
        _SLOT["texts"] += len(texts)
    return {"runtime": "server", "repo": _SLOT["repo"], "kind": kind,
            "dim": int(vecs.shape[1]), "count": len(texts),
            "vectors": [[round(float(x), 6) for x in v] for v in vecs],
            "elapsed_sec": round(time.time() - started, 3)}


def unload() -> Dict[str, Any]:
    with _LOCK:
        was = _SLOT["repo"]
        _SLOT.update(repo=None, model=None, dim=None, prompts={},
                     loaded_at=None, load_sec=None)
    import gc
    gc.collect()
    return {"unloaded": was}
