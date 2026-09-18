# aipg

A handle on [AI Power Grid](https://aipowergrid.io) — community-run workers
serving open-weight **text, image, video and audio** models behind an
OpenAI-shaped API at `https://api.aipowergrid.io/v1`, paid in USD credits.

Stdlib only. No service, no port, no install — the fn surface is served at
`/aipg/api`. Key at `~/.mod/aipg/key.json` (0600), media in `~/.mod/aipg/out`.

## When to reach for it

- "what open models can I actually call right now, and what do they cost" —
  `m aipg/grid` answers both in one screen
- cheap text, image, video or music generation against community compute
  instead of a vendor
- checking whether a model is *online* before building on it — a price in the
  book does not mean a worker is holding the weights
- finding the models a plain OpenAI client **cannot see** (all the media ones)

Not for: running a worker, staking, or the AIPG chain. It wraps the generation
API; it is not a node.

## The one idea

**Three public reads, three partial answers — so join them.**

| read | what it alone knows | what it does not |
|---|---|---|
| `GET /v1/status/models` | the real roster: name, type, worker count, context | prices |
| `GET /v1/pricing` | USD rates per model (`micro_usd` ledger) | *"rates do not assert that a model is online"* — its own words |
| `GET /v1/models` | the ids an OpenAI client can see | **text only** — 6 ids vs 12 in the roster |

`m aipg/models` is that join: one row per model with `type`, `online`,
`workers`, `context`, `price` and `openai_visible`.

Two traps the join encodes:

- **case.** The roster says `FLUX.2 Klein 4B FP8`; the book says
  `flux.2 klein 4b fp8`. Match case-insensitively or lose every media price.
- **`/api/v2` is retired.** The old AI-Horde surface (kudos, `find_user`,
  `generate/async`) answers `{"status":"gone"}`. Use `/v1`.

## Calls

```
m aipg/grid                             online counts by type, cheapest text model,
                                        what OpenAI clients can't see, key status
m aipg/models                           the full join
m aipg/models type=video online=true    one type, only what's serving
m aipg/workers                          {count, workers:[{name, models, job_types}]}
m aipg/pricing model=qwen3-27b          one rate card (case-insensitive)
m aipg/estimate qwen3-27b 1000 500      USD for that many in/out tokens
m aipg/test                             the reads, cross-checked against each other

m aipg/set_key <key>                    from console.aipowergrid.io → 0600 file
m aipg/key_status                        present? from where? (never the secret)
m aipg/credits                           remaining spend
m aipg/ask "one sentence on GPUs"        text, that's all
m aipg/chat prompt=... model=gpt-oss-120b stream=true
m aipg/image "a tin robot"               → ~/.mod/aipg/out/image-<ts>-0.png
m aipg/video "a tin robot waving"        LTX workers; slow, timeout is 180s
m aipg/audio "a synth march"             ACE-Step
```

`model` defaults to `auto` for chat (the grid routes) and to the **first online
model of the right type** for media — never a pinned name, because the media
roster turns over with whoever is running a worker.

## Gotchas

- **No key ⇒ `NoKey`,** raised with the console URL in the message. The read
  half keeps working without one; don't assume a failure is network.
- **`online=false` is common and normal.** A model with a configured rate and
  zero workers is not callable. `models(online=True)` is the useful filter.
- **Video is slow.** `AIPG_TIMEOUT` defaults to 180s for that reason.
- **Media response shape is worker-dependent.** `_save()` handles both
  `b64_json` and `url`.
- **The keyed paths are unexecuted.** Written to the documented shape; no key
  existed on this machine. Confirm with `m aipg/credits` first.
- **Community workers read your prompts** — upstream says so on its own front
  page. Nothing secret goes into a grid call.
