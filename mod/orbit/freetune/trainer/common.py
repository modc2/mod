"""Shared helpers for the freetune CPU LoRA trainer.

Everything here is deliberately dependency-light at import time so the Rust API
can shell out to `python3 -m trainer.common --models` to read the model registry
without importing torch/transformers (which are heavy and only needed at train /
infer time).
"""
import json
import os
import sys

# ── Paths ───────────────────────────────────────────────────────────────────
# All mutable state (runs, datasets, downloaded adapters) lives OFF the repo
# tree under ~/.mod/freetune so a checkout stays clean and per-host artifacts
# never get committed.
HOME = os.path.expanduser("~")
STATE_DIR = os.environ.get("FREETUNE_STATE_DIR", os.path.join(HOME, ".mod", "freetune"))
RUNS_DIR = os.path.join(STATE_DIR, "runs")
DATA_DIR = os.path.join(STATE_DIR, "datasets")


def ensure_dirs():
    for d in (STATE_DIR, RUNS_DIR, DATA_DIR):
        os.makedirs(d, exist_ok=True)


def run_dir(run_id: str) -> str:
    return os.path.join(RUNS_DIR, run_id)


# ── Model registry ────────────────────────────────────────────────────────────
# CPU-friendly Qwen models the UI offers in a dropdown. `params` is approximate
# and `cpu` is a rough "is this sane to finetune on a laptop CPU" hint the app
# shows as a badge. A user can always type a custom HF id; these are the curated
# defaults.
MODELS = [
    {
        "id": "Qwen/Qwen2.5-Coder-0.5B-Instruct",
        "label": "Qwen2.5-Coder 0.5B (Instruct)",
        "params": "0.5B",
        "cpu": "good",
        "note": "Default. Smallest coder model — actually trainable on CPU.",
    },
    {
        "id": "Qwen/Qwen2.5-0.5B-Instruct",
        "label": "Qwen2.5 0.5B (Instruct)",
        "params": "0.5B",
        "cpu": "good",
        "note": "General 0.5B. Use when the corpus isn't only code.",
    },
    {
        "id": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
        "label": "Qwen2.5-Coder 1.5B (Instruct)",
        "params": "1.5B",
        "cpu": "slow",
        "note": "Higher quality, noticeably slower on CPU.",
    },
    {
        "id": "Qwen/Qwen2.5-1.5B-Instruct",
        "label": "Qwen2.5 1.5B (Instruct)",
        "params": "1.5B",
        "cpu": "slow",
        "note": "General 1.5B.",
    },
    {
        "id": "Qwen/Qwen2.5-Coder-3B-Instruct",
        "label": "Qwen2.5-Coder 3B (Instruct)",
        "params": "3B",
        "cpu": "heavy",
        "note": "Best quality here; CPU training is painful — prefer a GPU.",
    },
]

DEFAULT_MODEL = MODELS[0]["id"]


def models_json() -> str:
    return json.dumps({"models": MODELS, "default": DEFAULT_MODEL})


# ── Small JSON helpers used across the trainer scripts ─────────────────────────
def write_json(path: str, obj) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)  # atomic — the API may read mid-write


def read_json(path: str, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


if __name__ == "__main__":
    if "--models" in sys.argv:
        print(models_json())
    else:
        print(json.dumps({"state_dir": STATE_DIR, "default_model": DEFAULT_MODEL}))
