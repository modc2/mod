"""RETRIEVER — a second resident slot, for search embeddings only.

server_rt keeps ONE model resident, and that ceiling is right for chat. It is
wrong for search: a semantic search box fires on every keystroke, and if each
query evicted the resident chat model (and the next chat evicted the encoder)
the box would spend its life reloading weights while arena matches died
mid-game. So retrieval gets its own slot, its own lock and its own model:

    LiquidAI/LFM2.5-Embedding-350M   dense bi-encoder, 1024-dim CLS vector,
                                     cosine, 512 tokens, 11 languages

…and its own PROCESS (retriever_worker.py). The repo's remote code makes the
backbone bidirectional by patching transformers' LFM2 classes globally; in
this interpreter that would turn the resident chat model non-causal. The
worker is spawned on first use, talks JSON lines over a pipe, and is respawned
if it dies. ~1.4 GB RAM in fp32; POST /retrieve/unload gives it back.

The model card is specific, and server_rt's AutoModel + mean-pool path gets
all three wrong for this repo: it needs trust_remote_code, it pools the CLS
token, and it is asymmetric — `query: ` vs `document: ` prompts. `kind` on
every call is therefore not decoration: a query encoded as a document lands
in the wrong neighbourhood.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = os.environ.get("LIQUIDAI_RETRIEVER", "LiquidAI/LFM2.5-Embedding-350M")
KINDS = ("query", "document")
MAX_TEXTS = 256
MAX_CHARS = 4000            # ~512 tokens is the model's ceiling; trim before the pipe
LOAD_TIMEOUT = 300
LOG = Path(os.path.expanduser("~/.mod/liquidai/retriever.log"))
WORKER = Path(__file__).resolve().parent / "retriever_worker.py"

_LOCK = threading.Lock()    # one request on the pipe at a time
_SLOT: Dict[str, Any] = {"proc": None, "repo": None, "dim": None, "prompts": [],
                         "load_sec": None, "started_at": None, "error": None,
                         "calls": 0, "texts": 0}


def status() -> Dict[str, Any]:
    """Lock-free, like server_rt.loaded() — a status read never queues."""
    proc = _SLOT["proc"]
    alive = proc is not None and proc.poll() is None
    return {"repo": _SLOT["repo"] or REPO, "resident": alive and _SLOT["dim"] is not None,
            "pid": proc.pid if alive else None, "dim": _SLOT["dim"],
            "prompts": _SLOT["prompts"], "load_sec": _SLOT["load_sec"],
            "started_at": _SLOT["started_at"], "error": _SLOT["error"],
            "calls": _SLOT["calls"], "texts": _SLOT["texts"], "process": "separate"}


def _readline(proc: subprocess.Popen, timeout: float) -> Dict[str, Any]:
    box: Dict[str, Any] = {}

    def _read():
        box["line"] = proc.stdout.readline()

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        proc.kill()
        raise TimeoutError(f"retriever worker silent for {timeout:.0f}s")
    line = box.get("line") or ""
    if not line:
        raise RuntimeError(f"retriever worker exited (code {proc.poll()}) — see {LOG}")
    return json.loads(line)


def _spawn(repo: str) -> subprocess.Popen:
    """Start the worker and wait for its ready line. Caller holds _LOCK."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "ab")
    proc = subprocess.Popen([sys.executable, str(WORKER), repo],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=log, text=True, bufsize=1,
                            cwd=str(WORKER.parent))
    # No new session: the worker loops on stdin, so the pipe closing when
    # this process dies is what ends it — no orphan holding 1.4 GB.
    started = time.time()
    ready = _readline(proc, LOAD_TIMEOUT)
    if not ready.get("ok"):
        proc.kill()
        _SLOT["error"] = ready.get("error")
        raise RuntimeError(ready.get("error") or "retriever worker failed to load")
    _SLOT.update(proc=proc, repo=repo, dim=ready.get("dim"), error=None,
                 prompts=ready.get("prompts") or [], load_sec=ready.get("load_sec"),
                 started_at=started)
    return proc


def _worker(repo: str) -> subprocess.Popen:
    proc = _SLOT["proc"]
    if proc is not None and proc.poll() is None and _SLOT["repo"] == repo:
        return proc
    if proc is not None and proc.poll() is None:
        proc.kill()
    return _spawn(repo)


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
    repo = repo or REPO
    with _LOCK:
        for attempt in (1, 2):           # one respawn if the worker died between calls
            proc = _worker(repo)
            try:
                proc.stdin.write(json.dumps({"op": "embed", "kind": kind, "texts": texts}) + "\n")
                proc.stdin.flush()
                out = _readline(proc, 60 + 2 * len(texts))
                break
            except (BrokenPipeError, RuntimeError):
                if attempt == 2:
                    raise
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "embed failed")
        _SLOT["calls"] += 1
        _SLOT["texts"] += len(texts)
    return {"runtime": "server", "repo": repo, "kind": kind, "dim": out["dim"],
            "count": len(texts), "vectors": out["vectors"],
            "elapsed_sec": out.get("elapsed_sec")}


def unload() -> Dict[str, Any]:
    with _LOCK:
        proc, was = _SLOT["proc"], _SLOT["repo"]
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        _SLOT.update(proc=None, repo=None, dim=None, prompts=[], load_sec=None,
                     started_at=None)
    return {"unloaded": was}
