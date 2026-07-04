# freetune

**CPU LoRA finetuning of Qwen over a directory of code** — a [mod](../) module with a
Rust API backend, a Next.js app, a base-model picker, and a CPU/RAM + per-task
**efficiency dashboard**.

Point it at a folder of code, pick a Qwen model, and it LoRA-finetunes on CPU
(no GPU required), then lets you chat with the result and measure exactly how
efficient the model is on this machine.

## Architecture

```
 Next.js app (50211, basePath /freetune)
        │  fetch /api/freetune/*
        ▼
 Caddy gateway  ──/api/freetune→ strip ──►  Rust API (axum, 50210)
        │                                        │ spawns
        └──/freetune→ app                        ▼
                                    Python trainer (transformers + peft, CPU)
                                      • dataset.py  walk code dir → corpus
                                      • train.py    LoRA finetune → adapter
                                      • infer.py    warm JSON-lines worker
                                      • bench.py    per-task usage metrics
```

- **Rust** does all orchestration: training-job lifecycle, a **warm inference
  worker pool** (each model loads once and stays hot), `/proc`-based CPU/RAM
  sampling, and the benchmark runner. The numeric compute is torch/MKL (native);
  Rust keeps the glue fast and non-blocking.
- **Python** does the ML (LoRA via `peft`, `transformers`) — there's no practical
  CPU LoRA training stack in Rust, and torch already runs as optimized native code.
- State (runs, datasets, adapters) lives off-tree under `~/.mod/freetune/`.

## Mod protocol

- App served at `/freetune`, API at `/api/freetune` (gateway strips the prefix) —
  the standard mod URL convention.
- `mod.py` is the protocol entry point: a null/default call returns module info.
  Optional owner gating via `FREETUNE_OWNER=<0x addr>` enforces a signed mod
  token (eip-191 ecdsa) on mutating endpoints (`auth.rs`); unset = open.
- **Automatic public routing**: `config.json` declares `"route": true` + its
  ports, so the `caddy` module auto-routes it at `modc2.com/freetune` and
  `modc2.com/api/freetune` — no hand-edited Caddyfile. Regenerate with
  `m caddy/apply` (scans every module's config, writes a managed Caddy include,
  reloads). Runs in prod under pm2 (`freetune-api`, `freetune-app`).

## Run

```bash
bash start.sh                 # builds the Rust binary + Next app, starts gateway
# App:     http://localhost:3000/freetune
# API:     http://localhost:3000/api/freetune   (or :50210 directly)
```

First training/inference call downloads the chosen model from Hugging Face.

### Python deps (CPU)

```bash
pip install --break-system-packages -r trainer/requirements.txt
# torch CPU build is expected to be preinstalled
```

## Models

Selectable in the UI (and `m freetune/models`); a custom HF id is also accepted:

| Model | Params | CPU |
|---|---|---|
| `Qwen/Qwen2.5-Coder-0.5B-Instruct` *(default)* | 0.5B | good |
| `Qwen/Qwen2.5-0.5B-Instruct` | 0.5B | good |
| `Qwen/Qwen2.5-Coder-1.5B-Instruct` | 1.5B | slow |
| `Qwen/Qwen2.5-1.5B-Instruct` | 1.5B | slow |
| `Qwen/Qwen2.5-Coder-3B-Instruct` | 3B | heavy |

## App tabs

- **train** — pick a code dir (scan to preview corpus stats), choose a model, set
  LoRA hyperparameters, start.
- **runs** — live progress (step/loss/eta), streaming logs, stop/delete.
- **chat** — talk to a base model or a finetuned run's adapter; each reply shows
  token usage, latency, tok/s, and RSS.
- **efficiency** — live CPU/RAM sparklines, warm-worker table, and a **benchmark**
  that runs example tasks and reports load time, throughput, latency, and peak
  memory — the model's efficiency profile on this box.

## API

| Method | Path | Body / notes |
|---|---|---|
| GET | `/models` | model registry |
| POST | `/dataset/preview` | `{src}` → corpus stats |
| POST | `/train` | `{src, model, epochs, lora_r, block_size, threads, max_blocks}` → `{id}` |
| GET | `/runs`, `/runs/:id`, `/runs/:id/logs` | list / status / logs |
| POST | `/runs/:id/stop` | cancel |
| POST | `/infer` | `{prompt, model | run_id, max_new_tokens}` → text + usage |
| GET | `/workers` | warm worker pool |
| GET | `/metrics` | CPU/RAM history + workers |
| POST | `/bench` | `{model | run_id, tasks?}` → per-task usage |

## Python / CLI

```python
import mod as m
ft = m.mod('freetune')()

ft.models()                              # selectable models
ft.scan('/path/to/code')                 # preview corpus
ft.train('/path/to/code', epochs=1)      # → {id}   (API must be running)
ft.status('<id>'); ft.runs()
ft.infer('explain this repo', run_id='<id>')
ft.bench(run_id='<id>')                  # efficiency over example tasks
ft.metrics()                             # live CPU/RAM
```

## Efficiency notes

- **Warm workers**: the model loads once per (model, adapter); subsequent chat
  turns skip the multi-second load. Evict via `DELETE /workers`.
- **Bounded threads**: `threads` caps MKL/OMP so a training run doesn't starve the
  API/app — set it to a fraction of your cores.
- On a no-GPU box, start small: default 0.5B model, a modest `max_blocks`, 1 epoch.
