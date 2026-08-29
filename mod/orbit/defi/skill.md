# defi

Compose a DeFi protocol out of typed Solidity blocks, see what every DeFi
protocol is actually paying, lock what you pick into a treasury that pays out
weekly on BlocTime's clock, and trade on the DEXes of Solana, Ethereum, Base and
Bittensor. 34 MCP tools, a REST API on `:50500`, a canvas console on `/defi`.

API `:50500` (`/api/defi`) · console `/defi` · MCP `POST /mcp`

## When to reach for it

**Composing** — "build me a vault that farms", "wire a lending market to an
oracle", "what would this protocol cost to deploy", "type-check this graph".

**Yields** — "what's the APR on Aave right now", "best stablecoin yield on
Base", "which protocols pay over 8% with real depth", "is that rate fees or
emissions". `defi_yield_protocols` for one row per protocol, `defi_yields` for
one row per pool, `defi_yield_pool` for a year of history behind a rate.

**Treasury** — "lock $10k of that into the treasury", "who gets paid on Friday",
"when's the next distribution", "distribute this week". A treasury that holds
what you chose and splits it across BLOC holders every Friday 12:00 EST.

**Trading** — "what would 1 ETH get me on Base", "swap 500 USDC for SOL",
"buy into subnet 64", "what am I holding on Solana". One desk, four chains, and
the price you are quoted is the price you trade.

Not for: raw chain ops on one chain (`eth`, `solana`, `bt` do those better and
this module just calls them), perps (`hyperliquid`), prediction markets
(`polymarket`), portfolio reads across many EVM chains (`debank`).

## Yields: reading a rate honestly

Every number comes from DefiLlama's index and none of it is computed here. What
this module adds is the refusal to blur two things people conflate:

- **`apy_base` is fees. `apy_reward` is token emissions.** A 40% rate that is
  39% emissions is a farm with an expiry date, not a yield. `emissions_share`
  says what fraction of the headline is emissions; `organic=true` filters to
  pools where most of the rate is fees. Quote both parts, never just `apy`.
- **`apy_mean_30d` and `apy_change_7d` are the reality check.** A headline far
  above its own 30-day mean is a spike, and saying so is more useful than
  repeating the spike.
- **Depth decides whether a rate is takeable.** `min_tvl` defaults to $100k and
  `sort=score` marks a rate down by how far the pool falls short of $10m, so a
  dust pool's 400% ranks below a deep pool's 8%. That is usually the ordering
  someone actually wants.
- **`tradable_on`** names the chain this module's desk could trade the pool on,
  or null — the honest join between the table and everything else here.

## The treasury: what "locked" means

`defi_treasury_choose` writes a **plan**. `defi_treasury_lock` writes a
**transaction**. Never describe the first as if it were the second — the whole
point of the ledger's `status` field is that "planned" and "locked" are
different facts, and every response says which it is.

Once locked, the principal is not recallable before the term. Two shapes:

- **`return_principal: false`** — the principal *is* the payout, released in
  equal weekly slices for `term_weeks` weeks. Nothing comes back.
- **`return_principal: true`** — the principal is escrowed for the term and only
  the yield on top is shared. It becomes withdrawable when the term ends.

The clock is BlocTime's, copied from BlocTime.sol rather than re-derived:
`DISTRIBUTION_PERIOD` 7 days, `DISTRIBUTION_OFFSET` 1 day 17 hours — Friday
12:00 EST, 17:00 UTC, pinned year round. A payout here lands in the same instant
BlocTime sweeps its own pot.

The split is pro-rata by BLOC across the contract's **registered set**,
snapshotted when `distribute()` runs. A BLOC holder who never called
`register()` earns nothing, and that is deliberate: this contract reads the BLOC
token from outside and cannot checkpoint transfers the way BlocTime's own
`_update` hook does, so an accumulator over `totalSupply` would pay whoever
bought BLOC *after* the distribution. Registration is the eligibility.

Order of operations: `defi_yields` → `defi_treasury_choose` →
`defi_treasury_preview` (see who gets what before committing) → deploy the
`treasury` block if there is not one yet → `defi_treasury_bind` →
`defi_treasury_lock`. Then `defi_treasury_distribute` on a Friday, and
`defi_treasury_claim` to pull your share.

## Trading: the order that matters

1. **`defi_dex_venues check=true`** — which chains this desk trades and whether
   the module behind each one is up. A venue whose module is down cannot quote,
   and finding that out first is one call instead of a confusing failure.
2. **`defi_dex_quote`** — free, signs nothing, tells the truth about price
   impact and the minimum you would receive after slippage. Always before a
   swap; the numbers move.
3. **`defi_dex_swap`** — the same call plus `account` and `confirm: true`.
   Without `confirm` on a mainnet venue you get `needs_confirm` and the quote
   back, which is a safe way to see the whole plan.

`sell` and `buy` take a symbol (`ETH`, `USDC`, `AERO`), a contract address or
mint, or `TAO` / `SN64` on Bittensor. `amount` is always in whole units of
`sell` — `"1.5"`, never wei.

## Things that will bite you

- **The treasury has three layers and they are not the same fact.** The LEDGER
  (`~/.mod/defi/treasury/`) is local bookkeeping. The SCHEDULE and the SPLIT are
  arithmetic over it — `projected: true` is on the response for a reason, and
  the yield line extrapolates the APY *at the time of choosing*, which will not
  be what lands. The CONTRACT (`/treasury/onchain`) is the only on-chain fact.
- **`m defi/treasury` works with no treasury deployed.** It will happily show a
  schedule and a split for a pot that does not exist yet. Check
  `binding.address` before telling anyone money is anywhere.
- **A rate frozen into an allocation is never refreshed.** `apy_at_choice` is
  the number the decision was made on, on purpose. Re-read the pool if you want
  today's.
- **This module holds no keys, and that is load-bearing.** Trades are executed
  by the chain module that owns the key: `eth`, `solana`, `bt`. Your bearer
  token is forwarded to it unchanged, so if that module does not know you, the
  trade fails at *its* door — sign in there (or pass `auth=<its token>` on the
  call), not here.
- **`account` means an account NAME, not an address.** On EVM it is an `eth`
  keystore account (`eth_accounts` lists them, `eth_unlock` opens one, or pass
  `password`). On Solana it is a keystore wallet name; on Bittensor a coldkey.
- **Two guards, both real.** This desk returns `needs_confirm` on any mainnet
  venue until `confirm: true`. Underneath, the chain module's own guard still
  applies — eth refuses non-testnet writes without its confirm, solana holds
  anything over `SOLANA_SPEND_USD` (and holds it too when the price API is
  throttled and it *cannot tell* how big the trade is).
- **A quote is a moment, not a promise.** `min_received` is what the slippage
  tolerance guarantees; the rest is the market's. Widen `slippageBps` for a thin
  pool rather than retrying into the same failure.
- **Bittensor is staking, not swapping.** Buying SN64 stakes TAO into that
  subnet's pool; selling unstakes. One side of the pair must be `TAO`. A
  subnet-to-subnet move is `bt_swap` on the bt module directly.
- **Token → ETH gives you WETH.** The router delivers the wrapped token; unwrap
  it with `withdraw()` via `eth_write` if you want native ETH.
- **Testnets are for rehearsal, not for prices.** `sepolia` and `base-sepolia`
  are wired so a trade can be run end to end for free, but their pools are thin
  and their quotes mean nothing about the real market.

## Composing

`defi_catalog` lists the blocks and their typed ports; `defi_block` returns one
in full, with Solidity source and compiled ABI/bytecode (also readable as the
resource `defi://block/{id}`). Build `{name, nodes[], edges[]}`, run
`defi_validate` until it is clean, then `defi_plan` for the ordered deployment —
one deploy step per node with resolved constructor args, then the wiring calls.

The plan is *not* executed here. It is signed by a wallet: the browser console
does it, or hand the steps to `eth_deploy`/`eth_write`. `defi_save`,
`defi_publish` (→ CID) and `defi_import` share a design; `defi_compose` turns a
sentence into a validated graph using the agent mod's prompt library.

The **BlocTime Treasury** (`treasury`) is the block that makes a choice
enforceable: two `erc20` ports — the asset it distributes and the BLOC token
whose balances decide the split — and a `sink` output, so a fee splitter's
revenue can be wired straight into it. Leave the weight port unwired to use the
fleet's live BlocTime. It has a real test suite against a real EVM
(`m defi/test_contract`), because it holds other people's money and promises a
lock cannot be undone.

The catalog contracts are unaudited reference implementations. Say so when
someone is about to deploy one with real money behind it.
