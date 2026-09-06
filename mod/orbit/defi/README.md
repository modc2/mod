# defi

Build a DeFi protocol out of parts, find out what the market is actually paying,
lock what you choose into a treasury that pays out weekly — from one console,
and from one MCP server.

```
m defi/hub                         # legit protocols for USD, live, every chain
m defi/hub_protocol aave-v3        # one of them: every USD pool per chain
m defi/blocks                      # the block catalog
m defi/plan graph.json             # type-check + ordered deployment plan
m defi/yield_protocols             # the APR for each DeFi protocol, live
m defi/yields chain=Base organic=1 # …by pool, fees rather than emissions
m defi/treasury_choose amount=1000 pool=<id> term_weeks=12
m defi/treasury_preview            # next Friday: the pot, and who splits it
m defi/venues                      # where this desk can trade, and what is up
m defi/quote base ETH USDC 0.1     # what a trade would really get you
m defi/swap base ETH USDC 0.1 --account=trader --confirm=1
m defi/serve
```

API `:50500` (`/api/defi`) · console `/defi` (`:50501`) · MCP `POST /mcp`
(45 tools)

## Four parts, one console

**The hub.** The console opens on the question most people actually arrive
with: *which protocols are legitimate enough to put dollars into, and where do
they run?* `/hub` is a hand-vetted shortlist — Aave V3, Morpho, Sky, Spark,
Compound V3, Maple, Fluid, Ethena, Kamino, Save, Curve — each chosen for a
multi-year track record or over $1b deposited, a named team, public audits and a
plain-stablecoin way in. The names, tiers (`core` · `established` · `frontier`),
credentials and risks are written by hand in `src/api/hub.json`; every number
beside them is DefiLlama's, joined at request time. Each card lists every chain
the index sees the protocol on, deepest first — Ethereum, Base and Solana marked
as this desk's own (enterable when an adapter exists), the rest honestly
read-only. Curated is not certified: what can go wrong sits beside why it is
here on every card. PUT USD IN hands the pick to the MODULES room with the pool
already open.

**The composer.** A lending market, a yield vault and a liquidity mine are not
monoliths — they are the same handful of parts wired differently. So the parts
ship as *blocks*: self-contained Solidity contracts with typed ports, plus a
canvas for connecting them. The server type-checks the composition (port types,
required wires, constructor cycles), compiles it with solc, and hands back an
ordered deployment plan. Nothing here holds a key: the plan is signed by the
browser wallet, so the worst this service can do is propose a transaction you
still have to approve.

**The desk.** A protocol needs liquidity and a price. So `/dex/*` quotes and
trades on:

| chain | venue | who signs |
|---|---|---|
| Ethereum | Uniswap V3 | `eth` mod — SwapRouter02, keystore account |
| Base | Uniswap V3 | `eth` mod |
| Sepolia · Base Sepolia | Uniswap V3 | `eth` mod — rehearse for free |
| Solana | Jupiter, across every Solana DEX | `solana` mod — ed25519 keystore |
| Bittensor | dTAO subnet pools | `bt` mod — coldkey; buying is a stake |

**The table.** Before any of that there is a question nobody starts anywhere
else: *what is paying what?* `/yields` is DefiLlama's index — around 17,000
pools across a thousand protocols — normalised into rows you can sort, and one
row per protocol at `/yields/protocols`. The one rule it enforces is that
`apy_base` (fees) and `apy_reward` (token emissions) never merge into a single
flattering number, because a 40% rate that is 39% emissions is a farm with an
expiry date and should not read like a yield.

**The treasury.** And then the question after that: *so where does the money
go?* You pick a row, say how much and for how many weeks, and the choice becomes
an allocation. Locking it is a real transaction against a real contract — the
`treasury` block, `ModBlocTimeTreasury` — which pays out every Friday 12:00 EST
and splits each payout across BLOC holders in proportion to what they hold.

## The point: it is a client, not a chain stack

There is no RPC endpoint, no wallet and no private key in this module — for
trading either. Every chain in this fleet already has a module that owns it, and
this desk calls those modules over MCP, the same JSON-RPC an agent would use:

- a quote on Base is `eth_read` against Uniswap's QuoterV2, tried at every fee
  tier, falling back to a route through WETH when no direct pool exists;
- a trade there is `eth_approve` (only when the allowance is short) then
  `eth_write` on SwapRouter02 — `exactInputSingle`, or `exactInput` with a
  packed multi-hop path;
- on Solana it is `sol_quote` and then `sol_swap`, which signs the exact
  transaction Jupiter built for the route it priced;
- on Bittensor it is `bt_price` for the pool's reserves and `bt_buy`/`bt_sell`
  to stake into it or unstake out;
- a treasury lock is `eth_approve` then `eth_write lock()`, and the BLOC weights
  behind every split are read from the `bloctime` module, which is the module
  that owns those balances. Nothing here mints, holds or moves BLOC.

Auth is passed through, never held. Your `Authorization` header — or `auth=` on
the call — goes to the chain module unchanged, so an agent reaches exactly the
accounts it could reach by calling that module directly, and this module cannot
sign for anyone even if it is compromised.

The guards stack rather than being reimplemented: a mainnet venue here returns
`needs_confirm` with the quote until you pass `confirm=true`, and underneath
that the chain module's own rule still applies (eth refuses a non-testnet write
without its own confirm, solana holds anything over `SOLANA_SPEND_USD`, a locked
keystore signs nothing).

## The treasury, in detail

The clock is not ours. `DISTRIBUTION_PERIOD` and `DISTRIBUTION_OFFSET` are
copied out of BlocTime.sol rather than re-derived, in the contract *and* in the
API: unix time 0 was a Thursday, so every 7-day window starts on a Thursday, and
Friday 12:00 EST is 1 day 17 hours in. Pinned to EST year round. A payout here
lands in the same instant BlocTime sweeps its own pot.

A lock is one of two shapes, and the difference is the whole design:

| | `returnPrincipal: false` | `returnPrincipal: true` |
|---|---|---|
| what is distributed | the principal itself, a slice a week | only the yield earned on top |
| after the term | nothing comes back | the principal is withdrawable |
| use it for | "share this money out over a year" | "park this and share what it earns" |

Neither can be recalled early. The owner's `rescue()` explicitly cannot touch
the asset — a lock a key can shorten is not a lock.

**Eligibility is explicit, and here is why.** BlocTime pays every holder with a
Synthetix accumulator because it *is* the BLOC token and can checkpoint both
sides of a transfer. This contract only reads that token from outside, so an
accumulator over `totalSupply` would credit whoever bought BLOC *after* a
distribution — the exact retroactive-reward bug BlocTime's `_update` hook exists
to prevent. So `register()` (permissionless, anyone, including on someone else's
behalf, since enrolling a holder only dilutes you) puts an address in the set,
and a distribution snapshots `balanceOf` across that set at the moment it runs.
Holders outside the set earn nothing; the set splits the whole week. Rounding
dust stays in the pot and rides along, rather than going to whoever the loop
reached last.

Three layers, and every response says which one it is speaking from:

* the **ledger** — allocations in `~/.mod/defi/treasury/`. Local bookkeeping,
  honest about being bookkeeping: an allocation is `planned` until it is
  `locked`.
* the **schedule** and the **split** — arithmetic over that ledger and the live
  BLOC weights. Principal released is exact; the yield line extrapolates the APY
  at the time of choosing and is marked `projected`.
* the **contract** — `/treasury/onchain`, read through the `eth` module. The
  only on-chain fact in the response.

It has a test suite against a real EVM — the window really is Friday 17:00 UTC
for twenty weeks running, a claim can never eat a lock's principal, a holder who
sold BLOC before the payout gets less, one who never registered gets exactly
nothing. `m defi/test_contract`, or `src/api/blocks/tests/README.md` to run it
by hand.

## MCP

```
POST /mcp   {"jsonrpc":"2.0","id":1,"method":"tools/list"}
```

Composer: `defi_catalog` · `defi_block` · `defi_audit` · `defi_templates` · `defi_validate` ·
`defi_plan` · `defi_protocols` · `defi_protocol` · `defi_save` · `defi_publish` ·
`defi_import` · `defi_prompts` · `defi_prompt` · `defi_compose`

Yields: `defi_yields` · `defi_yield_protocols` · `defi_yield_pool` ·
`defi_yields_facets`

Treasury: `defi_treasury` · `defi_treasury_schedule` · `defi_treasury_holders` ·
`defi_treasury_preview` · `defi_treasury_onchain` · `defi_treasury_choose` ·
`defi_treasury_bind` · `defi_treasury_participants` · `defi_treasury_lock` ·
`defi_treasury_distribute` · `defi_treasury_claim` · `defi_treasury_register`

Desk: `defi_dex_venues` · `defi_dex_tokens` · `defi_dex_quote` ·
`defi_dex_swap` · `defi_dex_balances`

Blocks are also resources (`defi://block/{id}`), so an agent can read the
Solidity it is about to deploy — and their audits (`defi://audit/{id}`), so it
can read what is wrong with it first.

## Audits

Every block has been audited by an agent. The report lives next to the contract
(`src/api/blocks/audits/<id>.json`, schema in `audits/SCHEMA.md`) and is served
three ways: `GET /audits` is every verdict on one page, worst first, with the
fleet tally by severity; `GET /catalog/{id}/audit` is one report — risk verdict,
findings with `where` (function + line), `exploit` (the concrete call sequence)
and `recommendation`, and `safe_use` guidance for deploying as-is; and the
`defi_audit` MCP tool returns either. `common` is the shared base every block
inherits (`ERC20Base`, `Owned`, `SafeTransfer`), audited on its own because a bug
there is a bug in all twenty-five.

In the console every palette card carries its risk badge, the inspector shows the
worst finding under the block, the block page has an AUDIT tab beside the source,
and the ISSUES panel rolls up the audit of every block on the canvas so a
composition's weakest block is visible before the deployment plan is.

An agent audit is not an audit. It reduces the unknowns — it does not certify
anything, and it cannot see the composition you will actually deploy. Read it,
then read the source.

## Composability, the mod way

- **Blocks are data.** `blocks/catalog.json` plus a `.sol` file. Adding one
  needs no Rust and no frontend change.
- **Designs are content-addressed.** `publish` returns a CID; anyone on the
  fleet can `import` it and get the same diagram.
- **Prompts come from the agent protocol.** The agent mod already owns a shared,
  CID-pinned prompt library, so this console browses *that* one — and `compose`
  turns a sentence into a validated graph.
- **Trades come from the chain mods.** Same principle, higher stakes.
- **Rates come from the yields index, and weights from bloctime.** This module
  computes no APR and owns no BLOC. It ranks, filters, and is explicit about
  which number came from where.

## Environment

| variable | default | what it points at |
|---|---|---|
| `DEFI_ETH_URL` | `http://localhost:50730` | the eth mod |
| `DEFI_SOLANA_URL` | `http://localhost:50710` | the solana mod |
| `DEFI_BT_URL` | `http://localhost:50280` | the bt mod |
| `DEFI_ACTIVATOR_URL` | `http://localhost:9000` | knocked once to wake a slept peer |
| `DEFI_AGENT_URL` | `http://localhost:50117` | the prompt library and compose |
| `DEFI_BLOCTIME_URL` | `http://localhost:8851` | the bloctime mod — BLOC weights and the payout clock |
| `DEFI_YIELDS_URL` | `https://yields.llama.fi` | the yields index |
| `DEFI_YIELDS_TTL` | `600` | how long one snapshot of it is good for |

State lives in `~/.mod/defi/` (`server.secret` 0600, `protocols/`, `objects/`,
`treasury/`).

## Caveats

The catalog contracts are unaudited reference implementations — each ships an
agent audit (the AUDIT tab, `/catalog/{id}/audit`), which is a reading, not a
certification. Read the source on the block page, deploy to a testnet first, and
treat mainnet use as your own risk. That goes double for the treasury: it is tested, but tested is not
audited, and a lock that works exactly as designed still cannot be undone.

An APY is not a promise either. The index reports what a pool paid, not what it
will pay, and this module's projections extrapolate a floating rate in a
straight line and say so. A treasury schedule is a plan, not a forecast. The same applies to the desk: a quote is live and honest about price
impact, but slippage, MEV and a thin pool are still yours to think about.
