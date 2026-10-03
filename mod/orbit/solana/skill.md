# solana

Solana, read and write. Twenty-six MCP tools, a REST API and a console on one
port (`:50710`), all the same code — an agent, a shell and a human never see
different answers. Every token on the chain ranked by real liquidity, and
programs: deploy one, load one, call it.

API `:50710` (`/api/solana`) · console `/solana` · MCP `POST /mcp`

## When to reach for it

- someone pasted a Solana address and you do not know what it is
- "what does this wallet hold" / "what is it worth"
- "what did this transaction actually do" — swaps, transfers, failures
- "is this token safe" — mint and freeze authorities, liquidity, holders
- "show me every token and how much liquidity each one has" → `sol_tokens`
- "could I actually sell $50k of this" → `sol_liquidity` (it measures, it does
  not repeat what an index claims)
- "which DEX is this token's liquidity on" → `sol_pools`
- "what would I get for N of X" before committing to a size
- chain state: slot, epoch, TPS, validator concentration
- sending SOL or an SPL token from a key this box holds
- "what is this program, and can whoever deployed it still change it"
- calling a program — by instruction name if it publishes an IDL, by raw bytes
  if it does not — and seeing what would happen before signing
- deploying a program, upgrading one, or revoking the right to upgrade it

Not for: EVM chains (`eth`, `evm`), Bittensor (`bt`), Polymarket (`polymarket`),
Hyperliquid (`hyperliquid`).

## The order that matters

Identification is the hard part; everything else is easy once you know what you
are holding.

1. **`sol_account`** — always first with an unfamiliar address. It returns
   `kind`: `wallet`, `mint`, `account` (a token account), `stake`, `program` or
   `unused`. Branch on it. Guessing wrong wastes a call and can be misleading —
   a token account's "owner" field is the wallet, but its `owner` at the account
   level is the token program, and only this tool disentangles that for you.
2. Wallet → **`sol_portfolio`** for holdings, **`sol_history detail=true`** for
   behaviour, **`sol_stake`** for delegated SOL that no balance call shows.
3. Mint → **`sol_token`** for supply, authorities and the `risk` list.
4. Signature → **`sol_tx`**.
5. Program → **`sol_program`**, which brings the IDL with it; then
   **`sol_invoke`** to call it.
6. "which token" rather than "this token" → **`sol_tokens`** first, then open
   the one that matters with `sol_liquidity`.

## Liquidity: three numbers, and only one is measured

`sol_tokens` ranks the routable universe (~3,200 mints) by `liquidity_usd`,
which is **quotable** — what an aggregator says it can route. That is the number
every screener shows and it is not the number you can sell at.

`sol_liquidity <mint>` returns four:

- `quotable_usd` — the router's claim, one-sided
- `pool_reserves_usd` — every pool the token trades in, **both sides**, so
  always the largest and roughly half counter-asset
- `token_side_usd` — just the token's half, where a source itemised the sides
- `executable_usd` — **measured**: real sells priced at $1k/$10k/$100k/$1m, then
  bisected for the largest that clears under a 1% all-in cost

Quote the last one when someone asks how much they can get out. The gap between
it and the headline is routinely 10-20×; `depth.ladder` is the evidence and
`depth.means` is the sentence to repeat. `depth=false` skips the measurement
when you only need the reported figures fast.

Read the `flags` rather than the raw fields — each one carries a `means` written
to be quoted. `redeemable` in particular: Jupiter reports staked-SOL wrappers as
having liquidity equal to their whole market cap, which is redemption, not a
book, and it is why twenty LSTs sit above SOL in an unfiltered ranking.

## Programs

`sol_program <address>` answers "what is this" for code: the loader, the
**upgrade authority** (or `immutable: true`, which is what "this cannot rug
you" actually means on Solana), the code size, the syscalls it imports, and the
anchor IDL if it publishes one. `accounts=true` lists the state it owns,
decoded.

`sol_invoke` builds one instruction and **simulates it first, always**. Read
`simulation.reason` before anything else — an anchor custom error comes back as
the name and message from the program's own IDL, not as `Custom(6001)`. Nothing
is signed until `send=true`, and a call that fails simulation is not sent at all
unless you pass `force=true`.

- with an IDL: `ix=<name>`, `args={...}` by name, `accounts={"name": "<addr>"}`.
  Sysvars, your wallet and PDAs whose seeds the IDL declares fill themselves in;
  anything still missing is named back at you, so the error IS the form.
- without one: `data=` as hex, base64 or `text:<literal>`, and
  `accounts=["ws:<addr>", "self"]` — `w` writable, `s` signer, `self` your
  wallet. `sol_idl action=set` saves an IDL for a program that never published
  one, and every later call can use names.
- **simulation does not verify signatures.** A call can pass with a signer whose
  key nobody here holds. Sending is where that stops being true.

`sol_deploy` takes `path=` (a .so on this box), `data=` (base64) or
`clone=<program>` (the deployed bytes of a program on another cluster — the way
to get something real to play with when there is no Rust toolchain around). It
returns a **job**: poll `sol_deploy action=status job=<id>`. Deploy is hundreds
of transactions and costs rent that only comes back if you close the program;
mainnet needs `confirm=true`.

## Things that will bite you

- **A symbol is not an identifier.** Anyone can mint a token called USDC. Only
  `SOL`, `USDC` and `USDT` are pinned; everything else resolves by liquidity and
  returns the mint it picked. Read that mint back before quoting a price to
  anyone.
- **`sol_balance` is not net worth.** It is SOL only. Tokens are in
  `sol_portfolio`; delegated SOL is in `sol_stake`. A wallet can show 0.01 SOL
  and hold six figures.
- **One mint, several token accounts.** `sol_portfolio` merges them; raw RPC
  does not.
- **A pool can lie about its depth.** `sol_pools` keeps pools claiming more
  reserves than the token's market cap in the list, with the reason, and
  excludes them from every total — read `suspect` before quoting a pool figure.
  Where two indexes disagree about the same pool the smaller reading is counted
  and `disputed` says by how much.
- **A missing source is not a zero.** If an index does not answer, `sources`
  and `sources_unavailable` say so and the reserve total is a floor. Same for
  `list_truncated` — one index caps at 30 pairs per token.
- **Public RPC throttles.** A 429 means pass `rpc=` with your own endpoint or
  set `SOLANA_RPC`. When the *price* API throttles instead, USD fields go null
  and a `warnings` array says so — the on-chain amounts are still correct. Do
  not read a null USD as zero.
- **`sol_quote` prices; `sol_swap` trades.** The swap re-uses the exact quote it
  priced — Jupiter builds the transaction from that route and this module signs
  those bytes rather than re-quoting, so what you saw is what you got. Mainnet
  only: Jupiter has no devnet liquidity to route through.
- **Devnet and testnet faucets are frequently dry.** `sol_airdrop` failing with
  a 429 is the faucet, not the module. Deploying needs SOL — roughly
  `2 × ELF bytes` of rent — so an unfunded wallet stops a deploy before it
  starts, with the number it needed.
- **A program cannot grow.** `max_data_len` is set at deploy time (default:
  twice the ELF); a bigger upgrade has to go to a new address.
- **A deploy that dies mid-write leaves a buffer** holding your bytes and your
  rent. The job says so and gives you both ways out: retry with `buffer=<addr>`,
  or `sol_authority action=close account=<addr>` for the rent.
- **The program keypair is the address.** `sol_deploy` writes it to the keystore
  before using it; lose it and that address can never be deployed to again.

## Sending value

`sol_transfer` signs locally and broadcasts. It handles SPL tokens with `mint=`
and creates the recipient's token account when they have never held that token —
the usual reason a hand-rolled token transfer fails.

Two things stand between a tool call and a loss:

- anything over `SOLANA_SPEND_USD` (default $25) returns `needs_confirm` with a
  full plan and moves nothing; retry with `confirm=true`
- the signing routes need a bearer token from `~/.mod/solana/server.secret`, or
  loopback if no secret is set — over both REST and `/mcp`

Rehearse on `network=devnet` first. A landed transfer cannot be undone.

`sol_wallet` manages `~/.mod/solana/keys.json` (0600, off-tree). It never
returns a secret except from an explicit `action=export`.
