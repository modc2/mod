"""The LFM catalog — every Liquid AI model, grouped by weights instead of repo.

Liquid publishes one HuggingFace repo per *format*: LFM2.5-350M holds the
safetensors, LFM2.5-350M-GGUF the quants, LFM2.5-350M-ONNX the web build,
LFM2.5-350M-MLX-4bit the Apple one. That's four rows in the HF listing for one
model, which is the wrong unit for a console whose whole question is "where can
I run this". So we fold the format suffix off the repo name and key on what's
left: one entry per model, carrying its variants and — derived from which
variants exist — where it can actually run.

    onnx  → BROWSER  (transformers.js, WebGPU, weights fetched by the tab)
    torch → SERVER   (transformers on this box)
    gguf  → EDGE     (llama.cpp / LEAP bundles on someone else's box)
    mlx   → EDGE     (Apple silicon)
    cloud → CLOUD    (inference.liquid.ai, tagged in from the live key)

The list is fetched from the HF API, not hardcoded — Liquid ships models faster
than anyone updates a constant. It is cached off-tree for CATALOG_TTL so the
console stays fast and works through a network blip.
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

HF_API = "https://huggingface.co/api/models"
HF_AUTHOR = "LiquidAI"
STORE = os.path.expanduser("~/.mod/liquidai")
CACHE_PATH = os.path.join(STORE, "catalog.json")
CATALOG_TTL = 6 * 3600

# Repo-name suffixes that mean "same model, different format". Order matters:
# the MLX/quant tails are matched before the bare format words so
# `-MLX-4bit` doesn't leave a dangling `-4bit`.
_FORMAT_SUFFIXES = [
    (re.compile(r"-MLX-(\d+bit|bf16)$", re.I), "mlx"),
    (re.compile(r"-MLX$", re.I), "mlx"),
    (re.compile(r"-GGUF-LEAP$", re.I), "gguf"),
    (re.compile(r"-GGUF$", re.I), "gguf"),
    (re.compile(r"-ONNX$", re.I), "onnx"),
]

# Parameter count lives in the name for every LFM: 350M, 1.2B, 8B-A1B (an 8B
# MoE with 1B active). We read both numbers — active params are what decides
# whether a laptop can hold it.
_SIZE_RE = re.compile(r"-(\d+(?:\.\d+)?)([MB])(?:-A(\d+(?:\.\d+)?)([MB]))?", re.I)

# Task, in the module's own words rather than HF's pipeline slugs.
_KINDS = {
    "text-generation": "text",
    "image-text-to-text": "vision",
    "feature-extraction": "embed",
    "sentence-similarity": "embed",
    "automatic-speech-recognition": "audio",
    "audio-text-to-text": "audio",
    "any-to-any": "audio",
}


def _family(name: str) -> str:
    """LFM2.5-VL-1.6B → LFM2.5. The generation, which is how Liquid versions."""
    head = name.split("-")[0]
    return head if head.upper().startswith("LFM") else "other"


def _size(name: str) -> Dict[str, Optional[float]]:
    mt = _SIZE_RE.search(name)
    if not mt:
        return {"params_b": None, "active_b": None}
    scale = {"m": 1e-3, "b": 1.0}
    total = float(mt.group(1)) * scale[mt.group(2).lower()]
    active = (float(mt.group(3)) * scale[mt.group(4).lower()]
              if mt.group(3) else None)
    return {"params_b": round(total, 3), "active_b": active}


def _split_format(name: str) -> tuple:
    """(base name, format). The base is the key every variant folds into."""
    for rx, fmt in _FORMAT_SUFFIXES:
        stripped = rx.sub("", name)
        if stripped != name:
            return stripped, fmt
    return name, "torch"


def _quant(name: str) -> Optional[str]:
    mt = re.search(r"-MLX-(\d+bit|bf16)$", name, re.I)
    return mt.group(1).lower() if mt else None


def _kind(repo: Dict[str, Any], name: str) -> str:
    tag = repo.get("pipeline_tag") or ""
    if tag in _KINDS:
        return _KINDS[tag]
    upper = name.upper()
    if "AUDIO" in upper:
        return "audio"
    if "-VL-" in upper or upper.endswith("-VL"):
        return "vision"
    if "COLBERT" in upper or "EMBEDDING" in upper or "ENCODER" in upper:
        return "embed"
    return "text"


# HF carries languages as bare two-letter tags among everything else.
_LANG_RE = re.compile(r"^[a-z]{2}$")
_SKIP_LANGS = {"ai", "ml", "js"}


def _langs(tags: List[str]) -> List[str]:
    return sorted({t for t in tags if _LANG_RE.match(t) and t not in _SKIP_LANGS})


def _license(tags: List[str]) -> Optional[str]:
    for tag in tags:
        if tag.startswith("license:"):
            return tag.split(":", 1)[1]
    return None


def _role(name: str) -> Optional[str]:
    """The fine-tune's job, when the name carries one (…-Extract, …-Math)."""
    for word, role in (
        ("Thinking", "reasoning"), ("Extract", "extraction"), ("Math", "math"),
        ("RAG", "rag"), ("Tool", "tool-calling"), ("PII", "pii"),
        ("Transcript", "transcription"), ("MT", "translation"),
        ("Spellchecker", "spellcheck"), ("Prompt-Router", "routing"),
        ("Policy-Linter", "policy"), ("Diffusion", "diffusion"),
        ("Base", "base"), ("LoRA", "lora"),
    ):
        if re.search(rf"(^|-){re.escape(word)}(-|$)", name, re.I):
            return role.lower()
    return None


def _fetch_repos(timeout: float = 30.0) -> List[Dict[str, Any]]:
    # `expand[]` is all-or-nothing: ask for one field and HF stops volunteering
    # the rest, so every field the catalog reads has to be named here.
    r = requests.get(
        HF_API,
        params={"author": HF_AUTHOR, "limit": 500, "sort": "downloads",
                "direction": -1,
                "expand[]": ["downloads", "likes", "lastModified",
                             "pipeline_tag", "tags"]},
        timeout=timeout,
        headers={"User-Agent": "mod-liquidai/1.0"},
    )
    r.raise_for_status()
    return r.json()


def _build(repos: List[Dict[str, Any]]) -> Dict[str, Any]:
    models: Dict[str, Dict[str, Any]] = {}

    for repo in repos:
        repo_id = repo.get("id", "")
        name = repo_id.split("/", 1)[-1]
        # LEAP bundles, the shared tokenizer repo: real artefacts, but nothing
        # you can point a runtime at, and a catalog row that can't be run is
        # just a row you have to explain.
        if not name.upper().startswith("LFM") or "Tokenizer" in name:
            continue
        base, fmt = _split_format(name)
        tags = repo.get("tags") or []

        entry = models.setdefault(base, {
            "id": base,
            "family": _family(base),
            "kind": _kind(repo, base),
            "role": _role(base),
            "downloads": 0,
            "likes": 0,
            "updated": None,
            "languages": [],
            "license": None,
            "variants": {},
            **_size(base),
        })
        entry["languages"] = sorted(set(entry["languages"]) | set(_langs(tags)))
        entry["license"] = entry["license"] or _license(tags)
        # The kind is most reliable off the repo that actually carries the
        # pipeline tag; ONNX/GGUF mirrors often ship without one.
        if repo.get("pipeline_tag"):
            entry["kind"] = _kind(repo, base)
        entry["downloads"] += repo.get("downloads", 0) or 0
        entry["likes"] += repo.get("likes", 0) or 0
        updated = repo.get("lastModified")
        if updated and (entry["updated"] is None or updated > entry["updated"]):
            entry["updated"] = updated

        variant = entry["variants"].setdefault(fmt, {"repos": []})
        variant["repos"].append({
            "repo": repo_id,
            "quant": _quant(name),
            "downloads": repo.get("downloads", 0) or 0,
            "transformers_js": "transformers.js" in tags,
        })

    out = []
    for entry in models.values():
        variants = entry["variants"]
        runtimes = []
        if "onnx" in variants:
            runtimes.append("browser")
        if "torch" in variants:
            runtimes.append("server")
        if "gguf" in variants or "mlx" in variants:
            runtimes.append("edge")
        entry["runtimes"] = runtimes
        entry["formats"] = sorted(variants)
        # One canonical repo per format is all a caller ever needs; the rest
        # (five MLX quants) stay reachable under variants.
        entry["repo"] = (variants.get("torch") or variants.get("onnx")
                         or variants.get("gguf") or variants.get("mlx")
                         )["repos"][0]["repo"]
        entry["onnx_repo"] = (variants["onnx"]["repos"][0]["repo"]
                              if "onnx" in variants else None)
        entry["gguf_repo"] = (variants["gguf"]["repos"][0]["repo"]
                              if "gguf" in variants else None)
        entry["torch_repo"] = (variants["torch"]["repos"][0]["repo"]
                               if "torch" in variants else None)
        out.append(entry)

    out.sort(key=lambda e: -e["downloads"])
    return {
        "fetched_at": time.time(),
        "count": len(out),
        "models": out,
    }


def _read_cache() -> Optional[Dict[str, Any]]:
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(data: Dict[str, Any]):
    os.makedirs(STORE, exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE_PATH)


def load(refresh: bool = False) -> Dict[str, Any]:
    """The catalog, from cache unless it's stale or `refresh`.

    A failed refresh serves the stale cache rather than an error — a catalog
    six hours old is a working console, and an exception is not.
    """
    cached = _read_cache()
    fresh = cached and (time.time() - cached.get("fetched_at", 0)) < CATALOG_TTL
    if cached and fresh and not refresh:
        return {**cached, "source": "cache"}
    try:
        data = _build(_fetch_repos())
        _write_cache(data)
        return {**data, "source": "huggingface"}
    except Exception as e:
        if cached:
            return {**cached, "source": "cache", "refresh_error": str(e)}
        raise


def get(model_id: str, refresh: bool = False) -> Optional[Dict[str, Any]]:
    wanted = model_id.split("/")[-1].lower()
    for entry in load(refresh=refresh)["models"]:
        if entry["id"].lower() == wanted:
            return entry
    return None
