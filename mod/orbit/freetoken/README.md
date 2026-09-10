# freetoken

A mod handle on [FreeToken](https://github.com/FlashML-org/FreeToken) — FlashML's
edge-native Mixture-of-Experts serving engine, the one that runs 290B-class
open-weight models on a gaming PC by keeping the experts in host RAM and either
streaming them over PCIe or computing them on the CPU, whichever the machine's
bandwidth favours.

The engine needs Linux, an NVIDIA card on driver r580+, and CUDA 13. **This module
needs none of that.** Its whole client half is stdlib over HTTP, because the box
with the GPU is almost never the box you are sitting at — a fact upstream already
designed for (`ft ctl --base-url`, `ft shell --server`) and this module takes as
its shape.

API `:50660` · console `:50661/freetoken` · state `~/.mod/freetoken`
Upstream: Apache-2.0, [paper](https://arxiv.org/abs/2608.16157), `pip install "freetoken[accel]"`

## The two halves

**On a machine that qualifies** it tells you so line by line, installs `ft` into a
venv of its own, and supervises `ft serve` with a pidfile and a log.

**On a machine that does not** — a laptop, a CI box, this one — it registers the
machines that do and drives them. Every read, every token, every model switch
goes over HTTP. Nothing about the module degrades.

```
m freetoken/preflight     →  os ✓  arch ✓  python ✓  nvidia_gpu ✗  driver ✗  cuda_toolkit ✗
                             this machine cannot host the engine — point it at one that can
m freetoken/add_box name=rig url=rig.lan:1919 daemon=rig.lan:1900
m freetoken/start model=Qwen/Qwen3.6-35B-A3B moe_backend=hybrid
m freetoken/ask "what is a Mixture-of-Experts model"
```

## Why a box is two URLs

FreeToken ships two servers, and the difference is the whole reason the remote
half works:

| | port | what it is |
|---|---|---|
| **serve** | 1919 | the engine. OpenAI `/v1/*`, Anthropic `/v1/messages`, plus `/health`, `/v1/stats`, `/v1/cache/*`, `/v1/requests` |
| **daemon** | 1900 | `ft daemon` — `/engine/start`, `/engine/stop`, `/engine/switch`, `/engine/logs`, `/bench/*`. `X-FT-Token` when it was started with `--token` |

A box with only a serve URL can be read from and generated from. A box that also
has a daemon can be told to load a different model from anywhere — that is what
the `steerable` pill in the console means. `m freetoken/start` uses the daemon
when there is one and falls back to supervising a local process when there is
not; if the box is remote and has neither, it says so rather than pretending.

## When to reach for it

- running a frontier open-weight model on hardware you own, and wanting the rest
  of the fleet to be able to call it
- pointing anything that takes a `base_url` at a GPU in the next room: `:50660/v1`
  speaks OpenAI **and** Anthropic, and streams pass through chunk for chunk
- finding out whether a machine can host the engine, before spending an hour on it
- moving VRAM between the expert cache and KV **live** — `m freetoken/resize
  moe=3000 kv=200k`, no restart, no weight reload
- watching an engine: throughput, latency, VRAM, pool occupancy, the request ring
- `ft launch claude` — pointing a coding agent at your own engine, with the cloud
  API keys cleared from its environment so it cannot quietly fall back to a paid one

Not for: training, fine-tuning, or serving a model this module wrote. It wraps an
engine; it is not one.

## One base_url the fleet can hold

Running the module as a service is worth it for a single reason:

```bash
export OPENAI_BASE_URL=http://localhost:50660/v1     # or ANTHROPIC_BASE_URL
```

That URL stays put while the engine behind it moves between machines, models and
ports. `m freetoken/use_box rig` is a fleet-wide provider swap. Streaming is
forwarded untouched — no re-framing, no buffering — so a client that renders
tokens as they arrive still does.

## Installing the engine

`m freetoken/install` builds `~/.mod/freetoken/venv` and installs
`freetoken[accel]` into it, detached, with the output in a log. The venv is not
tidiness: FreeToken pins `torch>=2.11,<2.12` and two kernel packages exactly, and
those pins belong nowhere near a shared environment.

```bash
m freetoken/install dry=1        # print the commands, run nothing
m freetoken/install              # detached; watch it with m freetoken/install_log
m freetoken/install source=1     # clone the repo, install it editable
```

An `ft` already on PATH is used as-is when there is no managed venv, so an
existing install is never duplicated.

## Models

`m freetoken/models` is three things at once: the known-good table from upstream's
`docs/models.md` (the checkpoints the prebuilt kernels are tuned for), what is
already on this disk, and what the current box reports from `/v1/models`. A
checkpoint converted with `ft checkpoint` shows up as `ftw` rather than
`safetensors` — that is the one that skips the conversion on load.

The MoE backend is the knob that matters, and `auto` is usually right:

| `--moe-backend` | |
|---|---|
| `auto` | dense → `fused`; MoE → `offload`, upgraded to `hybrid` when a cached `ft bench bw` profile recommends it |
| `fused` | experts resident on the GPU — needs the VRAM, never auto-selected |
| `offload` | experts in host RAM, an LRU cache of expert slots on the GPU; misses stream over PCIe |
| `cpu` | misses are computed on the CPU instead of fetched |
| `hybrid` | some misses fetched, the rest computed, overlapped — run `m freetoken/bench` once per machine |

Every `ft serve` flag is reachable with dashes written as underscores
(`moe_cache_auto=1`, `memory_ratio=0.85`, `attn=trtllm`, `page_size=128`), and a
typo is rejected here by name rather than thirty seconds into a model load.

## CLI

```bash
m freetoken/info                                  what this is, and what it can reach
m freetoken/preflight                             found vs wanted, one row per requirement
m freetoken/install                               ft, into a venv of its own
m freetoken/boxes                                 every engine, probed
m freetoken/add_box name=rig url=rig.lan:1919 daemon=rig.lan:1900
m freetoken/use_box rig                           what every other call talks to
m freetoken/models                                known-good + on this disk + being served
m freetoken/start model=Qwen/Qwen3.6-35B-A3B      daemon if there is one, else here
m freetoken/switch model=openai/gpt-oss-20b       swap the resident model
m freetoken/ask "explain expert routing"          one turn
m freetoken/stats                                 throughput, latency, VRAM, pools
m freetoken/cache                                 the pool table
m freetoken/resize moe=3000 kv=200k               live, no restart
m freetoken/requests                              what has been asked of this engine
m freetoken/bench                                 host RAM vs PCIe, once per machine
m freetoken/checkpoint model=<hf> out=<dir>       convert to FTW
m freetoken/launch claude                         point a coding agent at it (dry by default)
m freetoken/logs                                  the local serve log
m freetoken/ft ctl health                         any ft subcommand, passed through
m freetoken/serve                                 this module's API + console
```

`m freetoken/start` starts the **engine**. `m freetoken/serve` starts **this
module**. They are different verbs on purpose.

## The console

`:50661/freetoken`, one stdlib file, no build step. Preflight first, because on
most machines that is the answer; then the engines, with a live pill per box;
then the model, the pools, the stats and a streaming chat. Everything is proxied
through `/freetoken/_api`, so the browser talks to one origin and nothing needs
CORS.

## State

`~/.mod/freetoken/` — `boxes.json` (0600, it can hold a daemon token), `logs/`,
`venv/`, `models/`. Tokens are masked everywhere they are listed and are only
ever sent to the box they belong to. Nothing private is written into this repo.

Endpoints that change the machine — install, start, stop, cache rebuild, box
edits — answer loopback only. Reads and inference do not.

## Tests

```bash
m freetoken/test        # or: python -m pytest -q tests
```

46 tests, and they pass on a machine with no GPU and no FreeToken installed —
which is the point rather than a compromise. The half that drives an engine is
tested against a stand-in server; the half that touches the machine is tested by
asserting on the commands it *would* run.

## Credit

FreeToken is by the FlashML team, Apache-2.0. This module vendors none of it — it
installs it or reaches it where it already runs.

> Yang, Fan, Pan, Xi, Wang, Sun, Keutzer, Han, Zaharia, Xu, Stoica.
> *FreeToken: Efficient Edge-Native MoE Serving with Bandwidth-Adaptive Execution.*
> arXiv:2608.16157, 2026.
