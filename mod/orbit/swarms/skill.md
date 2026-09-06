# swarms

Use this module for anything touching the **Swarms protocol**: running
multi-agent swarms on `api.swarms.world`, browsing the swarms.world
marketplace, or reading the **$swarms** SPL token on Solana.

## The two halves

| | |
|---|---|
| **runtime** | `api.swarms.world` — sixteen orchestration architectures, BYOK |
| **token** | `$swarms`, mint `74SBV4zDXxTRgv1pEMoECskKBkZHc2yGPnc7GYVepump`, read-only |

## Running a swarm — the order that matters

1. `m swarms/architectures` — the sixteen types and what each is for.
   **Do this first.** Picking the architecture *is* the design decision; they
   all cost about the same and produce very different work.
2. `m swarms/build "<task>"` — don't guess a roster. The auto-builder is one
   cheap agent call and returns an AgentSpec list to read and edit before you
   pay to run it.
3. `m swarms/cost agents=5 loops=3` — billing is **per agent AND per token**,
   so agent count and `max_loops` multiply.
4. `m swarms/run "<task>" agents=a,b,c type=<SwarmType>` — go.

For anything one competent agent can finish, use `m swarms/agent` instead — a
committee costs more and is not automatically better.

`agents=` takes plain names (`researcher,analyst,critic`) or full AgentSpec
objects. Unknown AgentSpec fields fail locally with the list of valid ones.

## Money rules

- **BYOK.** Every completion spends the *caller's* credits. No house key. Set
  one with `m swarms/set_key key=…`, `SWARMS_API_KEY`, or the `x-swarms-key`
  header. Keys are masked everywhere and returned by nothing.
- **The guard.** A run estimated above `SWARMS_SPEND_USD` (default `$0.50`)
  returns `needs_confirm` instead of running. Re-call with `confirm=true` once
  you've read the estimate. Don't reflexively pass `confirm=true` on the first
  call — the guard exists because one wrong `swarm_type` turns one task into
  fifty agent calls.
- `m swarms/cost` is an **upper bound**, not a quote. The receipt is the
  `usage` block on the completion.
- When a call fails, `m swarms/account` distinguishes **no key (401)** from
  **no credit (402)** from **too fast (429)** — three different fixes that all
  read as "it didn't work".

## The token — read-only, and it stays that way

```
m swarms/token                 identity, on-chain supply, price, venues, FDV
m swarms/price                 spot + every pool ranked by liquidity
m swarms/holders               largest token accounts, share of supply
m swarms/balance owner=<addr>  SOL + $swarms + USD value
m swarms/quote side=buy amount=1 pay_with=SOL
```

**This module holds no Solana keypair and cannot sign or submit a
transaction.** `quote` returns a route and a price impact; a human with a
wallet executes it elsewhere. Never tell a user this module placed a trade.

Two things to say accurately when reporting:

- `holders` lists token **accounts, not people** — pools and exchange wallets
  appear as single large holders. It's a concentration signal, not a rich list.
  The public RPC rate-limits it; `SOLANA_RPC` fixes that.
- **Liquidity bounds size, not market cap.** Quote the price impact from
  `swarms/quote` rather than implying a position fills at spot.

Pass `mint=` to any chain call to check a *different* token — that's how you
verify something claiming to be `$swarms`. The mint is the identity, never the
ticker.

## Free without a key

`m swarms/market kind=agents|prompts|tools` (swarms.world listings) and every
chain call. `m swarms/architectures` returns the sixteen names keyless, and
the descriptions only with a key.

## Serving

`m swarms/serve` runs REST + console + MCP on **:50690** under pm2 as
`swarms-api`. MCP at `POST /mcp` (Streamable HTTP) or `python3 mcp.py`
(stdio); `m swarms/mcp_config` prints paste-ready client blocks.

Python stdlib only — no dependencies, no build step.
