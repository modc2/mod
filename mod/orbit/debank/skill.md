# debank

What an EVM address **actually owns**, across every chain — via
[DeBank](https://cloud.debank.com). Not transaction history (that's an
explorer's job): balances, LP and staked positions, lending collateral, the debt
against it, NFTs, and the standing approvals that let someone else move it all.

BYOK: every call spends the **caller's** DeBank units. No house key.

API `:50720` (`/api/debank`) · console `/debank` · MCP `POST /mcp` (24 tools)

## When to reach for it

- "what is this wallet worth, and where" — one call, every chain
- "what is this address farming / lending / borrowing right now"
- "is this wallet leveraged" — DeFi value net of debt, with health rate
- "what could still be drained from it" — live approvals ranked by exposure
- whale discovery: biggest holders of a token, biggest depositors in a protocol
- valuing a position at a past date, or checking what a swap was worth then
- "where should this wallet's stablecoins earn" — `debank_funds`: curated index
  funds (Aave/Compound/Morpho/Maple/Sky/Spark) with live projected ROI, the
  liquidity locked in each protocol read from chain, and exit terms; keyless
- "place $N into a fund" — `debank_savings_plan` builds the exact
  approve+deposit txs for the OWNER's wallet; `debank_savings` shows idle vs
  placed (keyless, from public RPCs). Nothing is signed server-side.

Not for: raw transaction traces or receipts (use a chain explorer / `chain`),
Bittensor (`bt`, `copytensor`), Hyperliquid perps (`hyperliquid`), Polymarket
(`polymarket`), price-only questions (`coingecko`).

## The order that matters

Scanning every chain costs units and is slow. Narrow first.

1. **`debank_portfolio id=0x…`** — the total, and which chains carry it. Always
   first. Its chain list is what you pass as `chain=` to everything else.
2. `debank_tokens` / `debank_protocols` **with `chain=`** — drill only into the
   chains that hold value. Omitting `chain` scans all ~73 and bills accordingly.
3. `debank_position id=… protocol=…` — the full unsummarized position, once
   step 2 names the protocol you care about.
4. `debank_approvals id=… chain=…` — the risk half of a wallet review. Per chain
   by design; run it on the chains step 1 flagged.

`debank_chains_used` is the cheap way to bound a scan when you don't need
values, only presence.

## Read the numbers correctly

**The total is not the token list.** `debank_portfolio.total_usd` covers wallet
tokens *and* DeFi positions. Summing `debank_tokens` alone under-counts any
address that farms or lends.

**Debt is already subtracted.** `debank_protocols` returns `usd` = supplied +
rewards − borrowed, with `supplied_usd` / `borrowed_usd` broken out. A position
worth less than its deposits is leverage, not a bug. `health_rate` rides along
where the protocol reports one.

**`exposure_usd` is what's at risk today**, not the allowance: `min(allowance,
balance) × price`. An infinite approval on an empty balance scores 0 and belongs
at the bottom of the revoke list; a capped approval on a large balance does not.
`unlimited: true` flags the infinite grants separately.

**Dust is dropped but counted.** Every list route returns
`hidden_below_min_usd`, and totals are computed *before* filtering. Pass
`min_usd=0` to see everything.

**Spam tokens are excluded by default** — their prices are invented, so any
total including them is invented too. `all_tokens=true` opts back in and the
response says so.

**A `0` floor price on an NFT means unpriced, not worthless.**

## Traps

- **Addresses only, never ENS.** `vitalik.eth` returns a 400 pointing this out —
  resolve the name first. Case doesn't matter; it's lowercased for you.
- **Chain names are translated** (`ethereum`→`eth`, `polygon`→`matic`,
  `gnosis`→`xdai`, `optimism`→`op`, `zksync`→`era`). Unknown ids pass through
  untouched, so a new chain works the day DeBank adds it. `debank_chains` lists
  them, alias table included.
- **History pages at 20 rows, hard cap.** Page back by passing the returned
  `oldest_time` as `start_time`. A larger `page_count` is silently clamped by
  DeBank, so this module clamps it visibly instead.
- **429 is requests-per-second, not units exhausted.** The client retries twice
  with backoff before surfacing it.
- **Only `debank_chains` works signed-out** (it falls back to DeBank's public
  catalog and labels the answer `source: "public"`). Everything else is a 401
  with the fix in the `hint` field.

## Keyless floor

`debank_balances` and `debank_networks` need no key at all. When
`debank_portfolio` answers 401, call `debank_balances` — native coin plus
USDC/USDT/DAI on eth, base, arb, op, matic, bsc, avax, xdai, read from public
RPCs and priced by CoinGecko. It is deliberately narrow (no LP, no DeFi, no long
tail) and says so in `coverage`; do not present it as the whole portfolio.
`debank_networks` is the table a wallet needs to switch chains or encode a
transfer: hex chain id, RPC, explorer, stablecoin contract + decimals.
`debank_humanity` is keyless too: the proof-of-humanity tag on an id — Proof of
Humanity v1/v2 and the Coinbase Verified Account attestation read straight from
their registry contracts, with a SHA3-256 commitment over the evidence so the
claim stays verifiable post-quantum. It says a human is behind the address, not
who they are; `human: false` means no registry vouches, not that it's a bot.

The browser console at `/debank` is a bank over the same routes: it connects
the user's own wallet and signs sends and revokes there. Nothing in this module
ever holds a private key.

## Keyless floor

`debank_balances` and `debank_networks` need no key at all. When
`debank_portfolio` answers 401, call `debank_balances` — native coin plus
USDC/USDT/DAI on eth, base, arb, op, matic, bsc, avax, xdai, read from public
RPCs and priced by CoinGecko. It is deliberately narrow (no LP, no DeFi, no long
tail) and says so in `coverage`; do not present it as the whole portfolio.
`debank_networks` is the table a wallet needs to switch chains or encode a
transfer: hex chain id, RPC, explorer, stablecoin contract + decimals.
`debank_humanity` is keyless too: the proof-of-humanity tag on an id — Proof of
Humanity v1/v2 and the Coinbase Verified Account attestation read straight from
their registry contracts, with a SHA3-256 commitment over the evidence so the
claim stays verifiable post-quantum. It says a human is behind the address, not
who they are; `human: false` means no registry vouches, not that it's a bot.

The browser console at `/debank` is a bank over the same routes: it connects
the user's own wallet and signs sends and revokes there. Nothing in this module
ever holds a private key.

## Keys

```
explicit key argument → x-debank-key header → DEBANK_ACCESS_KEY
→ ~/.mod/debank/key.json (0600, off-tree)
```

`m debank/set_key <key>` writes the last one. `m debank/account` says whether a
key resolved, where from, and what's left on it — call it first on any 401/403.
An `Authorization: Bearer` header is never read as a DeBank key; the gateway
puts its own session tokens there.

## Escape hatch

`debank_raw path=/v1/… params={…}` calls any Cloud API route with the caller's
key attached — for endpoints newer than this module, or fields the summaries
drop. A 404 from a named tool means the route moved; try `debank_raw` and file
the drift.
