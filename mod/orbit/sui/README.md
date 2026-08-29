# sui

Sui as one mod: seventeen MCP tools, a REST API and a browser console, all
running the same code on one port.

```
m sui/what 0x2
m sui/what bob.sui
m sui/portfolio <address>
m sui/tx <digest>
m sui/package 0x2 module=coin
m sui/serve
```

API `:50740` (`/api/sui`) · console `/sui` · MCP `POST /mcp` (17 tools)

## The problem it is shaped around

On Sui, an account address and an object ID are both 32 bytes of hex and
**nothing distinguishes them**. The same `0x…` string could be somebody's
wallet, a coin, an NFT, a shared object, or a published package — and every one
of those wants a different question asked of it. Sui makes this worse than
Solana does, because the two namespaces genuinely overlap: one hex string can be
both a live object and an address holding value.

So `sui_what` comes first. Give it any string — hex, a base58 digest, a coin
type, or a `.sui` name — and it asks the chain what that turned out to be, then
returns the detail that matches. Everything else branches from there.

## What each tool is for

**Identify and hold**
- `sui_what` — what a string IS. Start here.
- `sui_balance` — one coin type, one or many addresses, in USD.
- `sui_portfolio` — every coin type an address holds, priced and sorted, **plus
  staked SUI**. Dust is counted and excluded rather than padding the list;
  coins with no market are listed with `usd: null` and add nothing. The total is
  what could be sold, not what is nominally held.
- `sui_objects` — NFTs, capabilities, receipts, liquidity positions. On Sui
  everything is an object, so this is where most of what a wallet "has" lives.
- `sui_object` — one object, and how it is **owned**: address-owned takes the
  fast path, shared needs consensus, immutable can never change again,
  object-owned means it is a dynamic field or wrapped and not directly usable.
- `sui_stake` — delegated SUI. It sits in a StakedSui object and appears in no
  balance call, so an address can look nearly empty and control a large
  position.

**Understand what happened**
- `sui_history` — recent transactions with the net change *for the address you
  asked about*.
- `sui_tx` — one transaction, decoded: balance change per owner, which Move
  commands ran and what they targeted, objects created and mutated, gas paid.

**Value**
- `sui_price` — by coin type or symbol.
- `sui_coin` — decimals, supply, market cap, liquidity, 24h change.

**Move**
- `sui_package` — a package's modules, and each module's callable functions with
  full type signatures. Move keeps its interface on chain, so unlike an EVM
  bytecode blob you can read what a contract offers before calling it.

**The chain**
- `sui_network` — epoch, checkpoint, measured TPS, gas price, stake.
- `sui_validators` — the set by stake and APY, with the Nakamoto coefficient.

**Writing**
- `sui_wallet` — the off-tree keystore.
- `sui_transfer` — SUI or any coin type, simulated then signed here.
- `sui_faucet` — testnet/devnet.
- `sui_rpc` — any JSON-RPC method, for the long tail.

## Mysten's fullnodes no longer speak JSON-RPC

`https://fullnode.mainnet.sui.io` answers every method with *"JSON-RPC on public
fullnodes has been deprecated"*. The protocol itself is alive and third-party
nodes still serve it, so this module keeps a **pool** of working endpoints and
fails over between them rather than reporting that the chain is down. When a
call is answered by anything other than the first endpoint, the response says
so in `warnings`.

Set `SUI_RPC` (or pass `rpc=`) to point at your own node and the pool stops
mattering. That is worth doing: the public endpoints throttle, and one of them
returns 403 to any request without a User-Agent.

## Symbols are not identifiers

Anyone can publish a coin called USDC. `SUI`, `USDC` and `USDT` are pinned to
canonical types and never looked up. Every other symbol resolves to the
deepest-liquidity match, **the coin type it chose comes back with the answer**,
and a warning says it guessed. Read that type before trusting the number.

Prices come from Sui AMM pools via DexScreener. A coin it has never indexed
comes back `usd: null, priced: false` — that means no market was found, never
that the price is zero. DexScreener also caps every response at 30 pairs
regardless of how many coins you asked about, so a busy coin can crowd a quiet
one out of its own answer; when that cap is hit the missing coins are re-queried
individually rather than being recorded as worthless.

## Keys and money

There is no house wallet. A transfer is signed in-process with a seed that came
from the caller, from `SUI_SECRET_KEY`, or from `~/.mod/sui/keys.json` (mode
0600, off the source tree). The seed is never logged and never leaves the
process.

Imports accept every shape a Sui secret travels in: the CLI's `suiprivkey1…`
bech32 string, the base64 `flag || seed` from `~/.sui/sui_config/sui.keystore`,
raw hex, or a path to a keystore file. A key with a non-ed25519 scheme flag is
**refused rather than misread** — reading a secp256k1 seed as ed25519 would
derive an address the caller does not control.

Three guards:

- **Simulation.** Every transfer is dry-run first. That is how the gas budget is
  computed, and it is the only chance to see a transfer fail before it costs
  anything. `dry_run=true` stops there and signs nothing.
- **Value guard.** A transfer worth more than `SUI_SPEND_USD` (default $25)
  comes back as `needs_confirm` with a full plan and moves nothing. Call again
  with `confirm=true`.
- **Write gate.** Anything touching the keystore or moving value needs
  `Authorization: Bearer <~/.mod/sui/server.secret>`. With no secret file those
  routes answer only on loopback. The gate covers `/mcp` tool calls as well as
  the REST routes — a gate on one and not the other would be no gate.

Reads are open. The chain is public.

### The address-balance trap

Sui now keeps some balances in an address accumulator rather than in `Coin`
objects, and `suix_getCoins` reports those alongside real coins — with a
*synthetic* object digest. They are real money, but they are not objects: pass
one as a transaction input and the node rejects the entire transaction with a
"withdraw reservation" error that never says which coin it meant. This module
filters them out of coin selection and, when a transfer comes up short, says how
much is sitting in the accumulator instead of failing silently.

## The bytes

A Sui transaction is BCS, and the node either deserializes it exactly or refuses
it. `bcs.py` builds `TransactionData` by hand, and every rule in it was pinned
against mainnet rather than against a document:

- `SuiAddress` and `ObjectID` are 32 raw bytes; `ObjectDigest` is a byte
  *vector* with a length prefix. Two fixed 32-byte fields, encoded differently,
  next to each other.
- the signing hash is `blake2b256(intent || bcs)` — the signature covers the
  hash, not the message.
- the transaction digest is `blake2b256("TransactionData::" || bcs)`, base58 —
  a different prefix from the signing hash entirely.
- an address is `blake2b256(scheme_flag || pubkey)`, so a public key cannot be
  recovered from it and travels with the signature instead.

`python3 bcs.py --selftest` pulls signed transactions off mainnet and
re-derives all four.

## Connect an agent

```json
{"mcpServers": {"sui": {"type": "http", "url": "http://localhost:50740/mcp"}}}
```

or `python3 mcp.py` for stdio.

## Tests

```
python3 -m pytest -q            # 34 tests, 10 of them live
SUI_OFFLINE=1 python3 -m pytest -q
```

The offline half pins the cryptography and the wire format — BIP-173 bech32
vectors, RFC 8032 ed25519 vectors, base58 leading zeros, ULEB128, the
address/digest encoding asymmetry, and the fact that the two digests are
different functions. Those must be exactly right or a transfer is either
rejected or, worse, accepted and wrong.

The live half checks the same rules against the chain: real mainnet signatures
are re-verified against our address derivation, real transactions are re-hashed,
and **every shape of transfer this module can build** — SUI, a single coin, a
merge of several, an exact whole-coin send — is dry-run against a node to prove
the node accepts the bytes.
