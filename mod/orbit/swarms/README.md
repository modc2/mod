# swarms

**The Swarms protocol as one mod — the runtime and the token.**

Swarms is two things wearing one name, and neither explains itself without the
other:

- **The runtime.** `api.swarms.world` takes a task and a roster of agents and
  runs them through one of **sixteen** orchestration architectures —
  SequentialWorkflow, HierarchicalSwarm, MixtureOfAgents, MajorityVoting,
  DebateWithJudge, HeavySwarm and the rest.
- **The token.** `$swarms` on Solana — mint
  `74SBV4zDXxTRgv1pEMoECskKBkZHc2yGPnc7GYVepump`, 6 decimals, launched
  17 Dec 2024 on pump.fun — is what the agent economy around it is priced in.

This module puts both behind one implementation with four transports: the mod
protocol, a REST API, a browser console, and an MCP server with **18 tools**.
An agent, a shell and a human never see different answers, because there is no
second implementation to drift.

```
m swarms/architectures                    the sixteen, and what each is for
m swarms/build "audit this contract"      task in, agent roster out
m swarms/cost agents=5 loops=3            what that would cost, before it runs
m swarms/run "…" agents=a,b,c type=…      run it

m swarms/token                            $swarms: supply, price, venues, FDV
m swarms/holders                          where the supply actually sits
m swarms/balance owner=…                  what one wallet holds
m swarms/quote side=buy amount=1          what a position would cost

m swarms/serve                            API + console + MCP on :50690
```

## Two rules the module will not bend

**BYOK on the runtime.** Every completion spends the *caller's* Swarms credits.
This module holds no house key. Send `x-swarms-key: …` per request, set
`SWARMS_API_KEY`, or store one off-tree with `m swarms/set_key`. Keys are
masked in every response and returned by nothing.

**The chain half is read-only, and structurally so.** There is no keypair in
this module, no transaction builder, and no `sendTransaction` call — a test
asserts the last part against the source. `quote` prices a swap and hands back
the route; a person with a wallet executes it somewhere else. An MCP server an
agent can call should not be able to empty an account, so it cannot.

## The spend guard

Swarms bills **per agent and per token**. That means agent count and
`max_loops` multiply, and one mis-read `swarm_type` turns one task into fifty
agent calls. So any run whose upper-bound estimate exceeds `SWARMS_SPEND_USD`
(default `$0.50`) comes back as `needs_confirm` instead of running:

```json
{
  "needs_confirm": true,
  "reason": "estimated $30.0000 is above the $0.50 spend guard",
  "how": "call again with confirm=true, or raise SWARMS_SPEND_USD",
  "estimate": { "agent_calls": 500, "usd": { "total": 30.0 }, "...": "..." }
}
```

`swarms_cost` / `GET /cost` runs that same arithmetic on demand. It is
deliberately an **upper bound** — it assumes every agent fills its whole output
budget on every loop, which almost never happens. Use it to decide whether a
shape is affordable; the receipt is the `usage` block on the completion.

## Designing a swarm

The architecture *is* the design decision. They all cost about the same and
produce very different work, so the order that pays off is:

1. `swarms_architectures` — read what each of the sixteen is for.
2. `swarms_build` — if you don't know the roster, hand the task to the
   auto-builder. That's one cheap agent call, and it returns an AgentSpec list
   you can read and edit *before* paying to run it.
3. `swarms_cost` — price the shape.
4. `swarms_run` — run it.

A roster can be plain names, which is the shortest path from an idea to a
running swarm:

```bash
m swarms/run "compare these three vendor contracts" \
  agents=lawyer,cfo,skeptic type=DebateWithJudge
```

…or full `AgentSpec` objects with system prompts, models, temperatures, tools
and their own MCP servers. Unknown fields fail *here*, with the list of known
ones, rather than as a 422 from the far end with no hint which agent caused it.

## The token half

```bash
m swarms/token          # identity, on-chain supply, price, every venue, FDV
m swarms/price          # spot + pools ranked by liquidity
m swarms/holders        # largest token accounts and their share of supply
m swarms/balance owner=<addr>
m swarms/quote side=buy amount=1 pay_with=SOL
```

Three upstreams, because no single one answers everything and each fails
differently — Solana JSON-RPC for supply, holders and balances; Jupiter for
price and routing; DexScreener for per-venue liquidity and volume. `token`
fetches all of them independently and reports a failure *in place*: a
rate-limited RPC should not cost you the price, and a dead price feed should
not cost you the supply.

Two things worth reading carefully:

- **`holders` returns token *accounts*, not people.** A liquidity pool and an
  exchange's hot wallet each appear as one large holder. It is a concentration
  signal, not a rich list. The public RPC also rate-limits
  `getTokenLargestAccounts` — set `SOLANA_RPC` to a node with headroom.
- **Liquidity, not market cap, is what bounds size.** `price` returns total
  liquidity across every venue next to the price for exactly this reason, and
  `quote` shows the price impact your size would actually pay.

Pass `mint=` to any chain route to point it at another SPL token — which is
how you check whether something claiming to be `$swarms` really is. The answer
is the mint, never the ticker.

## HTTP

Everything is on one port: REST at `/`, the console at `/swarms`, MCP at
`/mcp`. The console asks its *own origin* for `/_api`, so one build works
behind the gateway and on a bare port alike.

```bash
curl localhost:50690/token
curl "localhost:50690/quote?side=buy&amount=1&pay_with=SOL"
curl "localhost:50690/market?kind=prompts&q=trading"      # public, no key
curl -X POST localhost:50690/run -H 'x-swarms-key: …' \
     -d '{"task":"…","agents":["researcher","critic"],"swarm_type":"MajorityVoting"}'
```

`POST /raw {path, method, body, params}` reaches any upstream route, including
ones that shipped after this file. `market: true` sends it to the public
swarms.world marketplace API instead.

## MCP

```bash
claude mcp add --transport http swarms http://localhost:50690/mcp
python3 mcp.py            # or stdio, with this box's own state
```

`GET /mcp_config` prints paste-ready blocks for whatever is pointing at this
deployment. The 18 tools:

| | |
|---|---|
| `swarms_architectures` | the sixteen, with what each is for — **call this first** |
| `swarms_run` | run a multi-agent swarm |
| `swarms_agent` | one agent, one task — the cheap path |
| `swarms_build` | task in, roster out |
| `swarms_reasoning` | reasoning agents: self-consistency, reflection |
| `swarms_batch` | parallel fan-out over independent jobs |
| `swarms_models` | models the runtime accepts as `model_name` |
| `swarms_tools` | hosted tools an agent can be given by name |
| `swarms_cost` | price a run before making it |
| `swarms_account` | key state, credits, rate limits — the 401/402/429 triage |
| `swarms_market` | swarms.world listings (public, no key) |
| `swarms_token` | the `$swarms` card |
| `swarms_price` | spot price + pools by liquidity |
| `swarms_holders` | concentration |
| `swarms_balance` | one wallet |
| `swarms_quote` | what a position would cost — **a quote, never a trade** |
| `swarms_set_key` | store a key off-tree |
| `swarms_raw` | escape hatch to any upstream route |

A tool failure comes back as MCP `isError` with the hint attached, not as a
dead session — `swarms_account` in particular exists to tell "no key" from "no
credit" from "too fast", which are three different fixes that all read as *it
didn't work*.

## Layout

```
mod.py         the module surface — every fn the CLI and other mods call
client.py      the Swarms cloud API + the swarms.world marketplace, BYOK
chain.py       $swarms on Solana — read-only, no keypair, cannot sign
mcp.py         18 tools, JSON-RPC 2.0, stdio + Streamable HTTP
server.py      REST + console + MCP on one port
console.html   the console — one file, plain ES modules, no build step
test/          29 offline tests; SWARMS_LIVE=1 adds the network ones
```

Python **stdlib only** — `http.server` and `urllib`, no dependencies. That is
why it drops onto any box with python3 and no install step.

```bash
python3 -m pytest test -q          # 29 passed, 4 skipped
SWARMS_LIVE=1 python3 -m pytest test -q   # + mainnet and upstream
```

## Config

Everything is overridable by environment variable — see `env` in
`config.json`. The ones that matter most:

| | |
|---|---|
| `SWARMS_API_KEY` | the runtime key, checked before the keystore |
| `SWARMS_SPEND_USD` | the guard (default `0.50`) |
| `SOLANA_RPC` | your own node — the public one rate-limits holders |
| `SWARMS_MINT` | point the chain routes at a different SPL token |
| `PORT` | API + console + MCP (default `50690`) |
