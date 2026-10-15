# cheapy

The cheapest LLM that can actually do the job.

Say how many tokens go in and come out per call, how many calls you'll make,
and what the model has to support (tool calls, images, reasoning, a big
context). cheapy returns every model that fits, ranked by what *that job*
costs on it, not by a headline per-token price.

Prices are OpenRouter's public model list (no key). They are cached for an
hour at `~/.mod/cheapy/models.json` (`CHEAPY_DIR` to move it). If the network
is down, the last cache is used; with no cache, the bundled
`src/snapshot.json` is. Every answer carries `source`
(`live | cache | stale | snapshot`) and `fetched_at`, so you know how fresh
the prices are. Python stdlib only.

## Use it

```bash
m cheapy/serve                    # console + API on :51370 → http://localhost:51370/cheapy/

m cheapy/cheapest input_tokens=8000 output_tokens=1000 calls=500 tools=1 free=0
m cheapy/cheapest vision=1 limit=5
m cheapy/compare ids=openai/gpt-4o-mini,google/gemini-2.5-flash input_tokens=20000
m cheapy/model id=openai/gpt-4o-mini
m cheapy/providers
m cheapy/estimate text="how many tokens is this"
m cheapy/refresh
m cheapy/test
m cheapy/kill
```

## The console

Pick a preset (chat reply, summarize a doc, coding agent step, classify rows,
describe images, whole book in) or type your own numbers. Paste a sample
input to count its tokens. Tick models to compare them in a bar at the
bottom, which shows each one's cost as a multiple of the cheapest.

## How a model "fits"

- its context holds input + output (or `min_context`, if that's larger)
- it has every capability you ticked
- `free=0` drops $0 models (free tiers are rate-limited), `free=1` keeps
  only them, empty keeps both

Cost per call = `in_m × input_tokens / 1M + out_m × output_tokens / 1M`.
Router pseudo-models (price −1) are dropped. Cache-read discounts are listed
(`cache_m`) but not used in the ranking.

## API

| route | what |
|---|---|
| `GET /cheapest` | models that fit, cheapest first |
| `GET /compare?ids=a,b` | same job on chosen models, with `x` = times the cheapest |
| `GET /model?id=` | one model |
| `GET /providers` | providers with counts and their cheapest paid model |
| `GET /estimate?text=` | ~4 chars per token |
| `GET /refresh` | re-pull prices now |
| `GET /health`, `/info`, `/readme` | the usual |

The console calls them as `/cheapy/api/<fn>`; the gateway form
`/api/cheapy/<fn>` and bare `/<fn>` work too.

## Layout

```
config.json  README.md
src/
  mod.py        anchor: the Mod class
  cheapy.py     price list, cache, ranking
  serve.py      HTTP server: console + API on one port
  snapshot.json offline price floor
  web/index.html
  tests/test_cheapy.py
```
