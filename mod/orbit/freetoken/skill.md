# freetoken

A handle on [FreeToken](https://github.com/FlashML-org/FreeToken) — FlashML's
edge-native Mixture-of-Experts serving engine, which runs 290B-class open-weight
models on one consumer GPU by holding the experts in host RAM and either
streaming them over PCIe or computing them on the CPU, whichever the machine's
bandwidth favours.

The engine needs Linux, an NVIDIA card on r580+, and CUDA 13. **This module does
not** — its client half is stdlib over HTTP, so a laptop can drive an engine in
another room, which is the normal case rather than the exception.

Dependencies: fastapi/uvicorn for the API; the console is stdlib. FreeToken
itself is installed on demand into a venv of its own, never as a dependency of
this module.

API `:50660` · console `:50661/freetoken` · state `~/.mod/freetoken`

## When to reach for it

- serving a frontier open-weight model on hardware you own, and letting the rest
  of the fleet call it
- pointing anything with a `base_url` at that GPU: `:50660/v1` speaks OpenAI
  **and** Anthropic, streams passed through chunk for chunk
- "can this machine run it" — `m freetoken/preflight` answers row by row before
  an hour is spent finding out
- watching a running engine: throughput, latency, VRAM, pool occupancy, requests
- moving VRAM between the expert cache and KV live, with no restart
- `ft launch claude` — a coding agent against your own engine, cloud keys cleared
  from its environment

Not for: training, fine-tuning, or quantising. It wraps an engine; it is not one.

## The one idea

**A box is two URLs, and the second one is what makes remote work.**

| | port | what it is |
|---|---|---|
| serve | 1919 | the engine — OpenAI `/v1/*`, Anthropic `/v1/messages`, `/health`, `/v1/stats`, `/v1/cache/*`, `/v1/requests` |
| daemon | 1900 | `ft daemon` — `/engine/start|stop|switch|status|logs`, `/bench/*`; `X-FT-Token` when started with `--token` |

Serve alone: read it, generate from it. Serve **and** daemon: load a different
model on it from anywhere — the `steerable` pill in the console. `start` uses the
daemon when there is one, supervises a local process when the box is this one,
and says plainly which it did. It never guesses the served model name either —
that comes from `/v1/models`.

## CLI

```bash
m freetoken/info                                   # the card, and what it can reach
m freetoken/preflight                              # found vs wanted, per requirement
m freetoken/install dry=1                          # print the commands, run nothing
m freetoken/install                                # ft into ~/.mod/freetoken/venv, detached
m freetoken/install_log                            # watch it
m freetoken/boxes                                  # every engine, probed
m freetoken/add_box name=rig url=rig.lan:1919 daemon=rig.lan:1900
m freetoken/use_box rig                            # what every other call talks to
m freetoken/models                                 # known-good + on disk + being served
m freetoken/start model=Qwen/Qwen3.6-35B-A3B moe_backend=hybrid
m freetoken/switch model=openai/gpt-oss-20b        # swap the resident model (daemon)
m freetoken/server                                 # is it up, what is it serving
m freetoken/ask "explain expert routing"           # one turn, text back
m freetoken/chat '[{"role":"user","content":"hi"}]' # OpenAI shape
m freetoken/stats                                  # throughput, latency, VRAM, pools
m freetoken/cache                                  # the pool table
m freetoken/resize moe=3000 kv=200k                # live, no restart, no reload
m freetoken/requests                               # the request ring
m freetoken/bench                                  # host RAM vs PCIe, once per machine
m freetoken/profile                                # the cached profile a box decides from
m freetoken/checkpoint model=<hf> out=<dir>        # convert to FTW (fast load)
m freetoken/launch claude                          # a coding agent at it; dry by default
m freetoken/logs                                   # the local serve log
m freetoken/ft ctl health                          # any ft subcommand, passed through
m freetoken/serve                                  # THIS module: API :50660 + console
m freetoken/stop                                   # the engine, not the module
```

`start`/`stop`/`switch` act on the **engine**. `serve`/`kill` act on **this
module**. Different verbs on purpose.

## Flags

Every `ft serve` flag is reachable with dashes as underscores — `moe_backend`,
`moe_cache_auto`, `memory_ratio`, `attn`, `graph`, `page_size`, `cache_type`,
`reasoning_parser`, `moe_cpu_layers`. `--model` is the only one that is required;
the rest resolve from the checkpoint and the GPU. A typo is rejected by name
here, not thirty seconds into a model load.

`--moe-backend`: `auto` (dense → fused, MoE → offload, hybrid with a bench
profile) · `fused` (resident, needs the VRAM) · `offload` (host RAM + a GPU LRU,
misses stream) · `cpu` (misses computed) · `hybrid` (both, overlapped).

## Gotchas

- **No GPU here is not an error.** `preflight` returning `can_serve_here: false`
  is the expected answer on most machines; register a box that can and carry on.
- **`start` on a remote box with no daemon cannot work.** Run `ft daemon` there
  and register it with `daemon=http://host:1900`.
- **Never install FreeToken into a shared environment.** It pins
  `torch>=2.11,<2.12` plus two exact kernel packages. `m freetoken/install` uses
  `~/.mod/freetoken/venv` for exactly that reason.
- **Writes are loopback-only.** Install, start, stop, cache rebuild and box edits
  answer the machine they run on and nothing else. Reads and inference are open.
- **Daemon tokens live in `~/.mod/freetoken/boxes.json` at 0600**, are masked in
  every listing, and go out only as `X-FT-Token` to the box they belong to.
- **`m freetoken/serve` does not start a model.** It starts this module.
