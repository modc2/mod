# openrouter

One key, every model. [OpenRouter](https://openrouter.ai) fronts 400+ models
from 100+ providers behind an OpenAI-shaped API; this module puts all of it —
the catalog, the routing, the money — behind the mod protocol.

The thing an agent actually needs is not "call a model". It is *choose* one and
know what it will cost before spending anything. So the surface is ordered
around that: narrow the catalog by capability and price, see which provider
serves a model cheapest and fastest, price the call, make it, read the receipt.

API `:50600` (`/api/openrouter`) · console `:50600/openrouter` · MCP `POST /mcp`
State `~/.mod/openrouter/key.json` (0600, off-tree) · Upstream `openrouter.ai/api/v1`

**BYOK throughout.** Every call spends the caller's own OpenRouter credits.
This module holds no house key and never returns one.

## The five calls, in order

```bash
m openrouter/models tools=1 max_prompt_usd_m=1 sort=price   # what's cheap and capable
m openrouter/endpoints id=moonshotai/kimi-k2                # who serves it, how fast
m openrouter/cost prompt_tokens=8000 completion_tokens=2000 # what the call would cost
m openrouter/ask "explain the mod protocol" model=…         # make the call
m openrouter/generation id=gen-…                            # what it really cost
m openrouter/key                                            # usage, limit, balance
```

Prices are quoted **per million tokens** everywhere (`prompt_usd_m`,
`completion_usd_m`), because per-token prices are 1e-7-magnitude numbers that
nobody can compare by eye. Per-call charges — images, requests, web search —
keep their own units and never get an `_m` suffix, which is what stops the two
from being read as the same kind of number.

## Three surfaces, one implementation

Every route, every tool and every button on the console is a thin call into the
same `Client`. An agent, a shell and a human cannot get different answers to the
same question, and `test_config_fns_all_exist_on_the_mod` /
`test_every_tool_is_declared_and_callable` fail the build if the three drift.

| surface | how |
| --- | --- |
| **API** | `GET /` lists every route. REST + SSE streaming on `:50600` |
| **console** | `/openrouter` — chat, models, providers, spend, MCP, key |
| **MCP** | `POST /mcp` (Streamable HTTP) or `python3 mcp.py` (stdio), 12 tools |
| **CLI** | `m openrouter/<fn>` — the 25 fns in `config.json` |

`m openrouter/mcp_config` prints drop-in config for Claude Code / Desktop.

## Money safety

Two things guard spend, and both are honest about their limits.

**The spend guard** prices the *worst case* — `max_tokens` at the model's
completion price, not the likely case — and anything over `$0.50`
(`OPENROUTER_SPEND_USD`) comes back as `needs_confirm` instead of running. Call
again with `confirm=true`. Under the ceiling, which is nearly everything, it is
silent.

**The router sentinel.** OpenRouter prices `openrouter/auto` and the other
routers at `-1`: not a price, a sentinel for "depends on which model this lands
on". Read as a number it becomes -$1,000,000 per million tokens, which makes the
routers the cheapest models in the catalog, the top of every default listing,
and a pass on every price ceiling. They normalize to `null` with
`variable_price: true` instead — unknown, which sorts last and fails ceilings.
The guard cannot price them up front and says so; the response carries the real
cost either way, because `usage.include` is always on.

## Keys

Checked in this order: the request (`x-openrouter-key`, or `authorization:
Bearer sk-or-…`), then `OPENROUTER_API_KEY`, then the keystore. A locally-run
server is therefore convenient and a shared one is BYOK, with no code
difference.

```bash
m openrouter/set_key key=sk-or-v1-…          # → ~/.mod/openrouter/key.json, 0600
curl -H 'x-openrouter-key: sk-or-v1-…' :50600/key
```

A gateway session bearer is *not* forwarded upstream as a key — only a token
that actually starts with `sk-or` counts, which is the difference between BYOK
and leaking the fleet's own auth to a third party.

Key *provisioning* (`m openrouter/provision`) needs a separate, stronger
provisioning key, by OpenRouter's design: an inference key cannot mint keys. A
created key's secret is returned exactly once and is never stored here.

## Provider routing

`provider` takes the full OpenRouter preference object — `order`, `only`,
`ignore`, `sort`, `allow_fallbacks`, `quantizations`, `data_collection` — or
just a comma-separated string, which means `order`. Typos raise rather than
being silently ignored upstream, since a dropped `only` is the difference
between the provider you chose and one you didn't.

```bash
m openrouter/chat model=moonshotai/kimi-k2 prompt=hi provider=groq,cerebras
m openrouter/chat models=a/b,c/d prompt=hi      # fallback list, first that serves wins
```

Pick provider names from `m openrouter/endpoints id=…`, which is what turns
routing into a decision rather than a guess.

## Run it

```bash
m openrouter/serve            # pm2: openrouter-api, API + console + MCP on one port
m openrouter/test             # 34 tests; 32 offline, 2 hit the public catalog
m openrouter/kill
```

Python stdlib only — `http.server` and `urllib`, no dependencies. Nothing in the
test suite can make a paid call: `chat` is only ever exercised through
`_chat_payload`, which builds the request without sending it.
