"""The retrieval encoder's own process. Spawned by retriever.py; never imported.

Why a separate process and not a second model in the API's: the repo's remote
code (`modeling_lfm2_bidirectional.py`) makes the model bidirectional by
monkey-patching transformers GLOBALLY — `modeling_lfm2.create_causal_mask` and
`Lfm2ShortConv.forward` are replaced for every LFM2 in the interpreter. Loaded
next to the resident chat model (LFM2.5-*-Instruct is the same architecture)
it would quietly turn that model non-causal and every chat after it into
garbage. A process boundary is the only isolation those patches respect.

Protocol: one JSON object per line on stdin → one per line on stdout.
    {"op": "embed", "texts": [...], "kind": "query"|"document"}
        → {"ok": true, "dim": 1024, "vectors": [[...], ...], "elapsed_sec": .12}
    {"op": "status"} → {"ok": true, "repo", "dim", "prompts", "load_sec"}
Anything a library prints goes to stderr; stdout carries the protocol only.
"""

import json
import os
import sys
import time

REPO = sys.argv[1] if len(sys.argv) > 1 else "LiquidAI/LFM2.5-Embedding-350M"

_OUT = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
sys.stdout = sys.stderr          # stray prints must not corrupt the protocol


def _shim_shortconv():
    """The remote code was written before transformers' decoder layer started
    passing `seq_idx` to the conv (5.x) — its replacement forward rejects the
    kwarg and every encode dies with a TypeError. Drop it; None was the old
    behaviour anyway."""
    from transformers.models.lfm2.modeling_lfm2 import Lfm2ShortConv

    def forward(self, *args, seq_idx=None, **kwargs):
        return self.slow_forward(*args, **kwargs)

    Lfm2ShortConv.forward = forward


def load():
    from sentence_transformers import SentenceTransformer

    started = time.time()
    model = SentenceTransformer(REPO, device="cpu", trust_remote_code=True)
    _shim_shortconv()                # after: loading is what installs the remote patches
    model.eval()
    return model, round(time.time() - started, 2)


def main():
    import torch
    torch.set_num_threads(int(os.environ.get("LIQUIDAI_RETRIEVER_THREADS",
                                             max(1, (os.cpu_count() or 2) // 2))))
    try:
        model, load_sec = load()
    except Exception as e:
        _OUT.write(json.dumps({"ok": False, "error": f"load: {type(e).__name__}: {e}"[:600]}) + "\n")
        return 1
    prompts = dict(getattr(model, "prompts", None) or {})
    dim_fn = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    dim = int(dim_fn() or 0)
    _OUT.write(json.dumps({"ok": True, "ready": True, "repo": REPO, "dim": dim,
                           "prompts": sorted(prompts), "load_sec": load_sec}) + "\n")

    for line in sys.stdin:
        try:
            req = json.loads(line)
            if req.get("op") == "status":
                out = {"ok": True, "repo": REPO, "dim": dim, "prompts": sorted(prompts),
                       "load_sec": load_sec}
            else:
                kind, texts = req.get("kind", "document"), [str(t) for t in req["texts"]]
                t0 = time.time()
                with torch.inference_mode():
                    if kind in prompts:
                        vecs = model.encode(texts, prompt_name=kind, batch_size=32,
                                            normalize_embeddings=True, show_progress_bar=False)
                    else:   # an ST too old to read the repo's prompts block
                        vecs = model.encode([f"{kind}: {t}" for t in texts], batch_size=32,
                                            normalize_embeddings=True, show_progress_bar=False)
                out = {"ok": True, "dim": int(vecs.shape[1]),
                       "vectors": [[round(float(x), 6) for x in v] for v in vecs],
                       "elapsed_sec": round(time.time() - t0, 3)}
        except Exception as e:
            out = {"ok": False, "error": f"{type(e).__name__}: {e}"[:600]}
        _OUT.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
