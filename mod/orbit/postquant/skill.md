# postquant

A post-quantum L1 that is nothing but a market in key/value space. ML-DSA
signatures (FIPS 204), SHA3-256 commitments, no elliptic curve anywhere. A key
maps to bytes — usually a 32-byte hash of data kept off-chain — and holding it
costs three separate prices: write gas per byte, witness gas per signature
byte (an ML-DSA signature is 2420 bytes and the chain bills it), and rent per
byte-hour against a prepaid escrow. Expired entries pay their sweeper.

API `:51030` (`/postquant/api`) · console `/postquant` · MCP `POST /mcp`
(23 tools) · state `~/.mod/postquant`

## When to reach for it

- committing to data verifiably without publishing it: `pq_set data=` stores
  only the SHA3-256; `pq_check` later proves what was committed and when
- a namespace market: human-readable keys that are claimed, leased, listed
  and bought (`pq_list` / `pq_buy`), with expiry enforced by paid sweepers
- studying state-rent economics: EIP-1559 over state growth instead of
  execution — watch `pq_market` move the base fee as the store grows
- light-client patterns: `pq_prove` returns a Merkle path from a key to the
  state root, verifiable anywhere with SHA3-256

Not for: blob storage (8KB value cap — store the hash, keep the blob in
`arweave`/`lighthouse`/`store`), general smart contracts (seven tx kinds, no
VM), or real value (single-proposer devnet; the faucet pays from genesis).

## The order that matters

1. `pq_head` — the tip, the base fee, what the store weighs.
2. `pq_wallet action=create` then `pq_faucet` — writes need a funded ML-DSA
   wallet. Amounts: a **string** ("25", "1.5") is PQ; a bare int is nq (1e-9).
3. `pq_quote` before every `pq_set` — the base fee floats with state growth
   and a signed transaction cannot be repriced. The quote splits write gas /
   witness gas / rent deposit and names the refundable sweep bond.
4. `pq_set key=… data=…` — data is hashed, only the digest lands on-chain.
   `value=` + `value_kind=raw` stores literal hex at 2.5x the per-byte rate.
5. `pq_get`, `pq_check data=`, `pq_prove` — read, test, prove.
6. Lease upkeep: `pq_fund` (anyone may pay anyone's rent), `pq_del` (refunds
   escrow + bond), `pq_sweep` (clear an expired key, collect its bond —
   `pq_keys include_expired=true` finds targets).
7. `pq_verify signatures=true` — replay from genesis, re-check every root and
   every witness. The real audit; slow on long chains (~80ms/signature).

## Traps

- Writes mine a block by default (`mine=false` leaves the tx in the mempool).
  The block loop also heartbeats every 60s so rent keeps settling.
- Only its owner may overwrite a live key (`not_owner`), but an **expired**
  key can be claimed by anyone's `pq_set` — the write collects the old bond.
- `pq_set` on a key you already hold ignores the lease args and reuses the
  existing escrow; extend with `pq_fund`, not by re-setting.
- The keystore (`~/.mod/postquant/keys.json`) holds 32-byte seeds; keys are
  regenerated from seed per signature. Deleting a wallet orphans its PQ.
- REST mirrors the tools 1:1 (`/set` = `pq_set`, args identical; reads GET,
  writes POST). `server.secret` in the state dir gates the write routes.
