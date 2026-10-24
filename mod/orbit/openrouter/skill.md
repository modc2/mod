# openrouter

Every model on [OpenRouter](https://openrouter.ai) — 400+ across 100+ providers —
behind one mod: search the catalog by price and capability, compare the providers
serving a model, price a call before making it, make it, read what it really
cost.

BYOK: every call spends the **caller's** OpenRouter credits. No house key.

API `:50600` (`/api/openrouter`) · console `/openrouter` · MCP `POST /mcp` (12 tools)

## When to reach for it

- "which model is cheapest that still does tool calling / 200k context / vision"
- "what would this call cost" — before spending, and "what did it cost" after
- "who serves kimi-k2, and which one is fastest / cheapest / not quantized"
- running one prompt against a model this fleet has no direct integration for
- pinning a request to a provider, or away from one (jurisdiction, data policy)
- fallback chains — try model A, fall back to B and C
- checking a key's balance, limit or rate limit after a 402 / 429

Not for: Claude jobs (`claude`, `agent`), local/on-device models (`liquidai`),
general fleet LLM gateway duties (`dev`), embeddings (`embed`).

## The order that matters

Picking is the hard part; calling is easy. Do it in this order.

1. `openrouter_models` — narrow by capability and price. Everything else needs a
   model id and this is where ids come from.
2. `openrouter_endpoints id=…` — the providers serving that model, cheapest
   first, with uptime, latency, throughput and quantization. Names from here go
   in `provider.order` / `provider.only`.
3. `openrouter_cost` — price the call. With `model` it's a quote; without one it
   ranks the catalog by what *this* call costs, which is a different order than
   prompt price alone because output tokens are usually the expensive half.
4. `openrouter_chat` — make it.
5. `openrouter_generation id=gen-…` — the receipt, in the provider's own token
   accounting. Differs from the response's normalized usage block.

## Prices are per MILLION tokens

`prompt_usd_m`, `completion_usd_m`. Per-call charges (image, request, web
search) keep their own units and never carry `_m` — don't compare the two.

`free: true` means free **both ways**. A $0 prompt with a priced completion is a
loss leader, not a free model, and the filter knows the difference.

## The router trap

`openrouter/auto` and the other routers are priced `-1` upstream — a sentinel
for "depends on which model this lands on", not a price. This module normalizes
that to `null` with `variable_price: true`, so routers sort **last**, fail every
price ceiling, and never claim to be free. If you see `total_usd: null` with
`variable_price: true`, the cost is genuinely unknowable up front — the spend
guard says so and the response carries the real cost.

Anything reporting a router as the cheapest model in the catalog is reading the
sentinel as a number.

## Spend guard

Worst case — `max_tokens` × completion price — over `$0.50` returns
`needs_confirm` instead of running. Retry with `confirm=true`. Tune with
`OPENROUTER_SPEND_USD`. It cannot price a variable-price router.

## Keys

Request header → `OPENROUTER_API_KEY` → `~/.mod/openrouter/key.json` (0600,
off-tree, never committed).

```bash
m openrouter/set_key key=sk-or-v1-…
curl -H 'x-openrouter-key: sk-or-v1-…' :50600/models
```

Only a bearer starting with `sk-or` is treated as a key, so the gateway's own
session token is never forwarded upstream. Provisioning (`openrouter_provision`)
needs a separate, stronger provisioning key — inference keys can't mint keys. A
new key's secret comes back exactly once and is never stored.

## Provider routing

`provider` is the full OpenRouter preference object (`order`, `only`, `ignore`,
`sort`, `allow_fallbacks`, `quantizations`, `data_collection`) or a
comma-separated string, which means `order`. Unknown keys raise — a silently
dropped `only` means a provider you didn't choose served your request.

`models=[a, b, c]` is a fallback list: the first that can serve the request wins.

## Shell

```bash
m openrouter/models tools=1 max_prompt_usd_m=1 sort=price
m openrouter/endpoints id=moonshotai/kimi-k2
m openrouter/cost prompt_tokens=8000 completion_tokens=2000
m openrouter/ask "…" model=anthropic/claude-sonnet-4.5    # just the text
m openrouter/key
m openrouter/serve | m openrouter/kill | m openrouter/test
m openrouter/raw path=/models/user                         # escape hatch
```

`openrouter_raw` reaches any OpenRouter route with the caller's key attached —
use it for beta endpoints and fields the normalized summaries drop.
