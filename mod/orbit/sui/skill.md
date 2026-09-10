# sui

Sui, read and write. Seventeen MCP tools, a REST API and a console on one port
(`:50740`), all the same code — an agent, a shell and a human never see
different answers.

API `:50740` (`/api/sui`) · console `/sui` · MCP `POST /mcp`

## When to reach for it

- someone pasted a `0x…` string and you do not know what it is
- "what does this address hold" / "what is it worth"
- "what did this transaction actually do"
- "what can this contract be asked to do" — Move signatures, on chain
- NFTs, capabilities, or any object a wallet owns
- chain state: epoch, checkpoint, TPS, validator concentration
- sending SUI or any coin from a key this box holds

Not for: Solana (`solana`), EVM chains (`eth`, `evm`), Bittensor (`bt`),
Polymarket (`polymarket`), Hyperliquid (`hyperliquid`).

## The order that matters

Identification is the hard part, and on Sui it is genuinely impossible by
inspection: an account address and an object ID are the same 32 bytes of hex.

1. **`sui_what`** — always first with an unfamiliar string. It returns `kind`:
   `package`, `coin`, `nft`, `object`, `address`, `unused`, or a decoded
   transaction. Branch on that. It also handles base58 digests, coin types and
   `.sui` names, so you can hand it anything a human pasted.
2. Address → **`sui_portfolio`** for holdings, **`sui_objects`** for NFTs and
   capabilities, **`sui_history`** for behaviour, **`sui_stake`** for delegated
   SUI that no balance call shows.
3. Object → **`sui_object`**, and read `ownership` before planning anything:
   shared objects need consensus, immutable ones can never change, object-owned
   ones are wrapped and not directly usable.
4. Package → **`sui_package`**, then `module=` for signatures.
5. Digest → **`sui_tx`**.

## Things that will bite you

- **Mysten's public fullnodes have dropped JSON-RPC.** `fullnode.mainnet.sui.io`
  answers every method with "deprecated". This module runs against a pool of
  third-party endpoints and fails over; if answers get slow or a `warnings`
  entry says a later endpoint replied, pass `rpc=` with your own node.
- **`sui_balance` is not net worth.** It is one coin type. Everything else is in
  `sui_portfolio`; delegated SUI is in `sui_stake` and appears in neither of the
  other two. A wallet can show 0.01 SUI and control six figures.
- **A symbol is not an identifier.** Anyone can publish a coin called USDC. Only
  `SUI`, `USDC` and `USDT` are pinned; everything else resolves by liquidity,
  returns the coin type it picked, and warns. Read that type back before
  quoting a price to anyone.
- **A null price is not zero.** A coin DexScreener has never indexed comes back
  `usd: null, priced: false`. Do not sum it as zero, and do not tell the user
  the position is worthless — tell them it has no market.
- **An address and an object ID can be the same string.** `sui_what` reports
  both when that happens; do not assume one excludes the other.
- **Some SUI cannot be spent as a transaction input.** Sui keeps part of a
  balance in an address accumulator, and `suix_getCoins` reports it with a
  synthetic digest. This module filters those out; if a transfer says there is
  less available than the balance implies, that is why, and the error says how
  much is stuck there.
- **Digests are base58, never 0x.** If it starts with 0x it is not a
  transaction.

## Sending value

`sui_transfer` sends SUI, or any coin type with `coin_type=`. It handles coin
selection, merging several coin objects and splitting the remainder — the usual
reason a hand-rolled Sui transfer fails. The recipient may be a SuiNS name.

Three things stand between a tool call and a loss:

- **it always simulates first.** The gas budget comes from that dry run, and a
  transfer that would fail is reported *before* it costs anything. Use
  `dry_run=true` to stop there deliberately.
- anything over `SUI_SPEND_USD` (default $25) returns `needs_confirm` with a
  full plan and moves nothing; retry with `confirm=true`
- the signing routes need a bearer token from `~/.mod/sui/server.secret`, or
  loopback if no secret is set — over both REST and `/mcp`

Rehearse on `network=testnet` first. A landed transfer cannot be undone.

`sui_wallet` manages `~/.mod/sui/keys.json` (0600, off-tree) and imports
`suiprivkey1…` strings, `sui.keystore` base64, hex, or a keystore path. It
never returns a secret except from an explicit `action=export`. Signing is
ed25519 only; a key with another scheme flag is refused rather than misread
into an address you do not control.
