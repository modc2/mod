"""SERVER runtime — LFM weights running in this process, on this box's CPU.

One model is resident at a time. That is a deliberate ceiling, not an
oversight: an LFM2.5-1.2B in fp32 is ~5 GB of RAM, and a console that lets you
click four models into memory is a console that OOMs the module. Loading a
second model evicts the first.

Liquid ships four kinds of model and this runtime carries all four, because a
catalog that lists a vision model and can only chat with it is a catalog that
lies:

    text     AutoModelForCausalLM         → streamed tokens
    vision   AutoModelForImageTextToText  → same, with images in the turn
    audio    AutoModelForSpeechSeq2Seq    → transcribe()
    embed    AutoModel + mean pooling     → embed()

Which one a repo is gets read off its own config (`architectures`), not guessed
from its name — the ONNX and GGUF mirrors don't always carry a pipeline tag,
and a wrong AutoClass fails deep inside `from_pretrained` with a traceback
nobody can act on.

Downloads land in the ordinary HuggingFace cache, so anything you already
pulled with `huggingface-cli` is already here and vice versa. `pull` runs in a
thread and reports progress by watching the cache directory grow, because
`snapshot_download` gives no usable callback.
"""

import base64
import io
import os
import re
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

# Transformers is imported lazily — the module has to answer /health and serve
# the catalog on a box with no torch installed.
_LOCK = threading.RLock()          # guards which model is resident
# Generation is serialised separately from residency: two chats against one
# CPU model would just take twice as long each and spike memory together — but
# holding the residency lock for the whole generation also froze /health and
# /runtimes behind it, which made a slow answer look like a dead API.
_GEN_LOCK = threading.Lock()
_LOADED: Dict[str, Any] = {"repo": None, "model": None, "tokenizer": None,
                           "processor": None, "modality": None,
                           "loaded_at": None, "load_sec": None}
_PULLS: Dict[str, Dict[str, Any]] = {}

MAX_NEW_TOKENS = 512


def available() -> Dict[str, Any]:
    """What this box can actually do, asked of the box rather than assumed."""
    out: Dict[str, Any] = {"runtime": "server", "ok": False}
    try:
        import torch
        import transformers
        out.update({
            "ok": True,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "threads": torch.get_num_threads(),
        })
        if torch.cuda.is_available():
            out["device"] = "cuda"
            out["gpu"] = torch.cuda.get_device_name(0)
        else:
            out["device"] = "cpu"
            out["note"] = "CPU inference — small models (≤1.2B) are the usable ones here"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["hint"] = "pip install torch transformers accelerate"
    out["loaded"] = loaded()
    return out


def loaded() -> Optional[Dict[str, Any]]:
    """Deliberately lock-free — a status read must never queue behind a run."""
    repo = _LOADED["repo"]
    if not repo:
        return None
    return {"repo": repo, "loaded_at": _LOADED["loaded_at"],
            "load_sec": _LOADED["load_sec"], "modality": _LOADED["modality"]}


# ── which of the four a repo is ──────────────────────────────────────

def modality(repo: str) -> str:
    """text | vision | audio | embed, read off the repo's own config."""
    from transformers import AutoConfig

    cfg = AutoConfig.from_pretrained(repo, trust_remote_code=False)
    arch = " ".join(getattr(cfg, "architectures", None) or []) or type(cfg).__name__
    lowered = arch.lower()
    if hasattr(cfg, "vision_config") or "vl" in lowered or "vision" in lowered:
        return "vision"
    if (hasattr(cfg, "audio_config") or "audio" in lowered or "speech" in lowered
            or "whisper" in lowered):
        return "audio"
    if "causallm" in lowered or "conditionalgeneration" in lowered:
        return "text"
    # An encoder with no head is an embedding model — that's what's left.
    return "embed"


# ── the HuggingFace cache, read as "what's on disk" ──────────────────

def cache_root() -> str:
    hub = os.environ.get("HF_HUB_CACHE")
    if hub:
        return hub
    home = os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    return os.path.join(home, "hub")


def _cache_dir(repo: str) -> str:
    return os.path.join(cache_root(), "models--" + repo.replace("/", "--"))


def _dir_bytes(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name), follow_symlinks=False).st_size
            except OSError:
                pass
    return total


def _cached_repos() -> List[str]:
    """Every repo already in this box's HuggingFace cache, LFM or not."""
    root = cache_root()
    if not os.path.isdir(root):
        return []
    return [name.replace("models--", "", 1).replace("--", "/", 1)
            for name in os.listdir(root) if name.startswith("models--")]


class UnservableModel(ValueError):
    """The caller named something this runtime cannot load. Not a 500."""


def resolve(model: str) -> str:
    """A caller's model string → the HuggingFace repo to load.

    Callers arrive by three doors — the console, the OpenAI-compatible /v1
    face, and other mods — and they don't all speak repo ids. A catalog name
    ("LFM2.5-350M") is what the console shows, `owner/name` is what HuggingFace
    understands, and a bare hosted-model slug ("venice-uncensored-1-2") belongs
    to somebody else's API entirely.

    That last case used to reach `from_pretrained` and come back as a raw
    OSError about private repositories and `hf auth login`, which sends you
    hunting for a token you never needed. Catch it here and say the true
    thing: this runtime loads weights, that name is not weights.
    """
    name = (model or "").strip()
    if not name:
        raise UnservableModel("no model named")
    if "/" in name:
        return name                     # any HF repo — this box is not LFM-only
    if name in _cached_repos():
        return name

    try:
        import catalog                  # sibling module, imported lazily
        rows = catalog.load()["models"]
    except Exception:
        rows = []
    for row in rows:
        if str(row.get("id", "")).lower() == name.lower():
            repo = row.get("torch_repo") or row.get("repo")
            if repo:
                return repo
            raise UnservableModel(
                f"{name} publishes no torch weights — the server runtime needs "
                f"them. Its formats: {', '.join(row.get('formats') or []) or 'none'}")

    known = [r["id"] for r in rows if r.get("torch_repo")][:8]
    raise UnservableModel(
        f"{name!r} is not a model this runtime can load. The server runtime "
        "runs weights on this box, so it wants a HuggingFace repo id "
        "('owner/name') or a catalog name"
        + (f" — e.g. {', '.join(known)}" if known else "")
        + ". A bare slug like this is usually a hosted model on somebody's "
          "API; call it with runtime='cloud' against a provider that serves it.")


def local_models() -> List[Dict[str, Any]]:
    """LFM weights already on this disk."""
    root = cache_root()
    if not os.path.isdir(root):
        return []
    out = []
    for name in sorted(os.listdir(root)):
        if not name.startswith("models--LiquidAI--"):
            continue
        path = os.path.join(root, name)
        repo = name.replace("models--", "", 1).replace("--", "/", 1)
        # A pull in flight is not a usable local model; say which it is.
        pull = _PULLS.get(repo)
        out.append({
            "repo": repo,
            "bytes": _dir_bytes(path),
            "path": path,
            "state": "pulling" if pull and pull["state"] == "running" else "ready",
            "resident": _LOADED["repo"] == repo,
        })
    return out


def repo_bytes(repo: str) -> Optional[int]:
    """Total weight size from the HF API — the denominator for pull progress."""
    import requests
    try:
        # The repo endpoint lists filenames without sizes even with
        # files_metadata=true; the tree endpoint is the one that carries them.
        r = requests.get(f"https://huggingface.co/api/models/{repo}/tree/main",
                         params={"recursive": "true"}, timeout=30)
        r.raise_for_status()
        # Only the formats `pull` actually fetches, or the bar never fills.
        wanted = (".safetensors", ".json", ".txt", ".model", ".py")
        return sum(f.get("size") or 0 for f in r.json()
                   if f.get("type") == "file"
                   and f.get("path", "").endswith(wanted)) or None
    except Exception:
        return None


def pull(repo: str) -> Dict[str, Any]:
    """Download a repo's weights in the background. Idempotent while running."""
    with _LOCK:
        job = _PULLS.get(repo)
        if job and job["state"] == "running":
            return pull_status(repo)
        total = repo_bytes(repo)
        _PULLS[repo] = {"repo": repo, "state": "running", "started": time.time(),
                        "total": total, "error": None}

    def _run():
        try:
            from huggingface_hub import snapshot_download
            # Weight formats only: the *-GGUF mirrors carry every quant, and
            # pulling all of them to run one is gigabytes of waste.
            snapshot_download(
                repo, allow_patterns=["*.json", "*.safetensors", "*.txt",
                                      "*.model", "*.py"],
            )
            _PULLS[repo].update(state="done", finished=time.time())
        except Exception as e:
            _PULLS[repo].update(state="error", error=f"{type(e).__name__}: {e}",
                                finished=time.time())

    threading.Thread(target=_run, daemon=True, name=f"pull:{repo}").start()
    return pull_status(repo)


def pull_status(repo: Optional[str] = None) -> Any:
    def one(job: Dict[str, Any]) -> Dict[str, Any]:
        got = _dir_bytes(_cache_dir(job["repo"]))
        total = job.get("total")
        return {**job, "bytes": got,
                "pct": round(100 * got / total, 1) if total else None,
                "elapsed": round(time.time() - job["started"], 1)}

    if repo:
        job = _PULLS.get(repo)
        return one(job) if job else {"repo": repo, "state": "idle"}
    return [one(j) for j in _PULLS.values()]


# ── load / generate ──────────────────────────────────────────────────

def load_model(repo: str) -> Dict[str, Any]:
    """Make `repo` the resident model, evicting whatever was there.

    Vision and audio models want a processor (image/feature extraction plus the
    tokenizer) rather than a bare tokenizer; embedding models want neither a
    generation head nor a chat template. So the modality decides all three of
    the AutoClass, the pre-processor and what `generate` is even allowed to do.

    The name is resolved first — every door into this runtime comes through
    here, so this is the one place that has to know what is loadable.
    """
    repo = resolve(repo)
    with _LOCK:
        if _LOADED["repo"] == repo:
            return loaded()
        import torch
        import transformers
        from transformers import AutoModel, AutoProcessor, AutoTokenizer

        unload()
        started = time.time()
        kind = modality(repo)
        dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32
        device = "cuda" if torch.cuda.is_available() else "cpu"

        cls = {
            "text": transformers.AutoModelForCausalLM,
            "vision": getattr(transformers, "AutoModelForImageTextToText",
                              transformers.AutoModelForCausalLM),
            "audio": getattr(transformers, "AutoModelForSpeechSeq2Seq", AutoModel),
            "embed": AutoModel,
        }[kind]

        # Liquid's audio models (Lfm2AudioForConditionalGeneration) ship no
        # remote code and aren't in stock transformers — they want Liquid's own
        # `liquid-audio` stack. Say that here rather than letting AutoProcessor
        # fail three frames deep with "unrecognized configuration".
        if kind == "audio" and not hasattr(transformers,
                                           "Lfm2AudioForConditionalGeneration"):
            import json as _json
            import os.path
            from huggingface_hub import hf_hub_download
            with open(hf_hub_download(repo, "config.json")) as f:
                arch = " ".join(_json.load(f).get("architectures") or [])
            if "Lfm2Audio" in arch:
                raise RuntimeError(
                    f"{repo} needs Liquid's own audio runtime — transformers "
                    f"{transformers.__version__} has no {arch} and the repo "
                    "ships no remote code. /transcribe serves speech-seq2seq "
                    "repos (Whisper-family) that this box can load.")

        processor, tokenizer = None, None
        if kind in ("vision", "audio"):
            processor = AutoProcessor.from_pretrained(repo)
            # The processor owns a tokenizer; reuse it so decoding and the chat
            # template come from one place.
            tokenizer = getattr(processor, "tokenizer", None)
        if tokenizer is None:
            tokenizer = AutoTokenizer.from_pretrained(repo)

        model = cls.from_pretrained(repo, dtype=dtype, device_map=device)
        model.eval()
        _LOADED.update(repo=repo, model=model, tokenizer=tokenizer,
                       processor=processor, modality=kind,
                       loaded_at=time.time(),
                       load_sec=round(time.time() - started, 2))
        return loaded()


def unload() -> Dict[str, Any]:
    with _LOCK:
        was = _LOADED["repo"]
        _LOADED.update(repo=None, model=None, tokenizer=None, processor=None,
                       modality=None, loaded_at=None, load_sec=None)
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    return {"unloaded": was}


def _decode_image(ref: str):
    """A data: URL, a bare base64 blob or an http(s) URL → a PIL image."""
    from PIL import Image

    if ref.startswith("data:"):
        ref = ref.split(",", 1)[-1]
    if ref.startswith(("http://", "https://")):
        import requests
        raw = requests.get(ref, timeout=30,
                           headers={"User-Agent": "mod-liquidai/1.0"}).content
    else:
        raw = base64.b64decode(ref)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _split_parts(messages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Pull the images out of the turns, leaving placeholders behind.

    Processors want (rendered text, list of images) rather than images buried
    in the transcript, and doing the extraction here means one code path
    whether the caller sent a plain string, OpenAI-style `image_url` parts, or
    this module's own `{"type":"image","image":…}`.
    """
    images: List[Any] = []
    out: List[Dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": msg["role"], "content": [{"type": "text", "text": content}]})
            continue
        parts: List[Dict[str, Any]] = []
        for part in content or []:
            kind = part.get("type")
            if kind in ("image", "image_url"):
                ref = (part.get("image") or part.get("url")
                       or (part.get("image_url") or {}).get("url") or "")
                if ref:
                    images.append(_decode_image(ref))
                    parts.append({"type": "image"})
            elif kind == "audio":
                # Audio turns route to transcribe(); in a chat they're a label.
                parts.append({"type": "text", "text": part.get("text") or "[audio]"})
            else:
                parts.append({"type": "text", "text": part.get("text", "")})
        out.append({"role": msg["role"], "content": parts})
    return out, images


def _flatten(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Content parts → one string per turn, for text-only models."""
    flat = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            flat.append({"role": msg["role"], "content": content})
            continue
        text = " ".join(p.get("text", "") for p in (content or [])
                        if p.get("type") == "text").strip()
        flat.append({"role": msg["role"], "content": text})
    return flat


def generate(repo: str, messages: List[Dict[str, Any]],
             max_tokens: int = MAX_NEW_TOKENS, temperature: float = 0.3,
             top_p: float = 0.95) -> Iterator[Dict[str, Any]]:
    """Stream a completion. Yields {'type': 'token'|'done'|'error', …}.

    Turns may carry images; a text-only model gets them stripped rather than a
    crash, because the honest failure there is a worse answer, not a 500.
    """
    with _GEN_LOCK:
        try:
            load_model(repo)
        except Exception as e:
            yield {"type": "error", "error": f"load failed: {type(e).__name__}: {e}"}
            return

        import torch
        from transformers import TextIteratorStreamer

        model, tokenizer = _LOADED["model"], _LOADED["tokenizer"]
        processor, kind = _LOADED["processor"], _LOADED["modality"]

        if kind == "embed":
            yield {"type": "error",
                   "error": f"{repo} is an embedding model — use /embed, it has "
                            "no chat head to stream from"}
            return

        try:
            if kind == "vision" and processor is not None:
                turns, images = _split_parts(messages)
                text = processor.apply_chat_template(
                    turns, add_generation_prompt=True, tokenize=False)
                inputs = processor(text=[text], images=images or None,
                                   return_tensors="pt")
            else:
                inputs = tokenizer.apply_chat_template(
                    _flatten(messages), add_generation_prompt=True,
                    return_tensors="pt", return_dict=True, tokenize=True,
                )
        except Exception as e:
            yield {"type": "error", "error": f"chat template: {type(e).__name__}: {e}"}
            return
        inputs = {k: (v.to(model.device) if hasattr(v, "to") else v)
                  for k, v in inputs.items()}
        prompt_tokens = int(inputs["input_ids"].shape[-1])

        streamer = TextIteratorStreamer(tokenizer, skip_prompt=True,
                                        skip_special_tokens=True)
        kwargs = dict(
            **inputs, streamer=streamer, max_new_tokens=int(max_tokens),
            do_sample=temperature > 0, temperature=max(temperature, 1e-5),
            top_p=top_p, pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
        )
        error: Dict[str, Any] = {}

        def _run():
            try:
                with torch.no_grad():
                    model.generate(**kwargs)
            except Exception as e:  # surfaced after the stream drains
                error["error"] = f"{type(e).__name__}: {e}"
                streamer.end()

        started = time.time()
        threading.Thread(target=_run, daemon=True).start()

        count = 0
        first_at = None
        for chunk in streamer:
            if not chunk:
                continue
            count += 1
            first_at = first_at or time.time()
            yield {"type": "token", "text": chunk}

        if error:
            yield {"type": "error", **error}
            return
        elapsed = time.time() - started
        yield {
            "type": "done",
            "runtime": "server",
            "repo": repo,
            "modality": kind,
            "prompt_tokens": prompt_tokens,
            "chunks": count,
            "elapsed_sec": round(elapsed, 2),
            "ttft_sec": round(first_at - started, 2) if first_at else None,
            "chunks_per_sec": round(count / elapsed, 2) if elapsed else None,
        }


# ── the other two modalities ─────────────────────────────────────────

def embed(repo: str, texts: List[str], normalize: bool = True) -> Dict[str, Any]:
    """Mean-pooled sentence vectors, plus the cosine matrix between them.

    The matrix is the point: a bare list of 768 floats tells you nothing on a
    screen, and "are these two sentences close" is the only question anyone
    actually asks an embedding model interactively.
    """
    if not texts:
        raise ValueError("nothing to embed")
    with _GEN_LOCK:
        load_model(repo)
        import torch

        model, tokenizer = _LOADED["model"], _LOADED["tokenizer"]
        started = time.time()
        batch = tokenizer(texts, padding=True, truncation=True, max_length=512,
                          return_tensors="pt")
        batch = {k: v.to(model.device) for k, v in batch.items()}
        with torch.no_grad():
            out = model(**batch)
        hidden = getattr(out, "last_hidden_state", None)
        if hidden is None:
            raise ValueError(f"{repo} returned no hidden states to pool")
        # Mask-weighted mean: padding tokens are not part of the sentence, and
        # including them makes short texts drift toward each other.
        mask = batch["attention_mask"].unsqueeze(-1).to(hidden.dtype)
        vectors = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        if normalize:
            vectors = torch.nn.functional.normalize(vectors, p=2, dim=1)
        sim = (vectors @ vectors.T).tolist()
        vectors = vectors.float().tolist()

    return {
        "runtime": "server",
        "repo": repo,
        "dim": len(vectors[0]),
        "count": len(vectors),
        "vectors": vectors,
        "similarity": [[round(v, 4) for v in row] for row in sim],
        "elapsed_sec": round(time.time() - started, 2),
    }


def transcribe(repo: str, audio: bytes, language: Optional[str] = None) -> Dict[str, Any]:
    """Speech → text. Whatever the box can decode, on whatever it has."""
    with _GEN_LOCK:
        load_model(repo)
        import numpy as np
        import torch

        model, processor = _LOADED["model"], _LOADED["processor"]
        if processor is None:
            raise ValueError(f"{repo} has no audio processor — is it a speech model?")

        rate = getattr(getattr(processor, "feature_extractor", None),
                       "sampling_rate", 16000)
        wave = _decode_audio(audio, rate)
        started = time.time()
        inputs = processor(wave, sampling_rate=rate, return_tensors="pt")
        inputs = {k: v.to(model.device) for k, v in inputs.items()}
        kwargs = {"language": language} if language else {}
        with torch.no_grad():
            ids = model.generate(**inputs, max_new_tokens=440, **kwargs)
        text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

    return {"runtime": "server", "repo": repo, "text": text,
            "seconds": round(len(wave) / rate, 2),
            "elapsed_sec": round(time.time() - started, 2)}


def _decode_audio(raw: bytes, rate: int):
    """Bytes → mono float32 at `rate`, using whatever decoder is installed.

    soundfile and librosa are both optional; ffmpeg is on nearly every box that
    has a browser recording feature pointed at it, so it's the fallback that
    actually fires. WAV we can read ourselves.
    """
    import numpy as np

    try:
        import soundfile as sf
        wave, got = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
        wave = wave.mean(axis=1)
        return _resample(wave, got, rate)
    except ImportError:
        pass

    import shutil
    import subprocess
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        proc = subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-i", "pipe:0",
             "-f", "f32le", "-ac", "1", "-ar", str(rate), "pipe:1"],
            input=raw, capture_output=True, timeout=300)
        if proc.returncode == 0 and proc.stdout:
            return np.frombuffer(proc.stdout, dtype=np.float32)

    import wave as wavelib
    with wavelib.open(io.BytesIO(raw)) as wav:   # raises on anything but PCM WAV
        frames = wav.readframes(wav.getnframes())
        data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if wav.getnchannels() > 1:
            data = data.reshape(-1, wav.getnchannels()).mean(axis=1)
        return _resample(data, wav.getframerate(), rate)


def _resample(wave, got: int, want: int):
    import numpy as np
    if got == want:
        return wave
    n = int(round(len(wave) * want / got))
    return np.interp(np.linspace(0, len(wave) - 1, n),
                     np.arange(len(wave)), wave).astype("float32")
