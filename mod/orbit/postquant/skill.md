# postquant

A post-quantum L1 that is nothing but a market in key/value space. Multiple
key types — ML-DSA 44/65/87 (FIPS 204, lattice) and SLH-DSA-SHAKE-128f
(FIPS 205, hash-only) — chosen per wallet, SHA3-256 commitments, no elliptic
curve anywhere. Every witness is verified by the wasm module registered for
its key type (hash-committed in `pq/wasm/manifest.json`; fail closed), so an
algorithm is a pluggable blob, not a branch in the node. A key maps to bytes —
usually a 32-byte hash of data kept off-chain — and holding it costs three
separate prices: write gas per byte, witness gas per signature byte (2420 for
ML-DSA-44, 17088 for SLH-DSA — billed as-is), and rent per byte-hour against
a prepaid escrow. Expired entries pay their sweeper.

API `:51030` (`/postquant/api`) · console `/postquant` · MCP `POST /mcp`
(24 tools) · state `~/.mod/postquant`

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

1. `pq_head` — the tip, the base fee, what the store weighs. `pq_algos` —
   every key type, its witness bytes, the quantum gate, and which wasm blob
   (by SHA3-256) enforces it.
2. `pq_wallet action=create scheme=…` then `pq_faucet` — writes need a funded
   wallet; scheme defaults to ML-DSA-44, `pq_algos` lists the choices.
   Amounts: a **string** ("25", "1.5") is PQ; a bare int is nq (1e-9).
3. `pq_quote` before every `pq_set` — the base fee floats with state growth
   and a signed transaction cannot be repriced. The quote splits write gas /
   witness gas / rent deposit and names the refundable sweep bond; it prices
   the witness for the scheme that will actually sign (`scheme=` overrides —
   an SLH-DSA witness costs ~7x an ML-DSA-44 one).
4. `pq_set key=… data=…` — data is hashed, only the digest lands on-chain.
   `value=` + `value_kind=raw` stores literal hex at 2.5x the per-byte rate.
5. `pq_get`, `pq_check data=`, `pq_prove` — read, test, prove.
6. Lease upkeep: `pq_fund` (anyone may pay anyone's rent), `pq_del` (refunds
   escrow + bond), `pq_sweep` (clear an expired key, collect its bond —
   `pq_keys include_expired=true` finds targets).
7. `pq_verify signatures=true` — replay from genesis, re-check every root and
   re-run every witness through its key type's wasm. The real audit, and fast
   now that verification is native (~1ms/signature via the wasm host).

## Traps

- Writes mine a block by default (`mine=false` leaves the tx in the mempool).
  The block loop also heartbeats every 60s so rent keeps settling.
- Only its owner may overwrite a live key (`not_owner`), but an **expired**
  key can be claimed by anyone's `pq_set` — the write collects the old bond.
- `pq_set` on a key you already hold ignores the lease args and reuses the
  existing escrow; extend with `pq_fund`, not by re-setting.
- The keystore (`~/.mod/postquant/keys.json`) holds 32-byte seeds; keys are
  regenerated from seed per signature. Deleting a wallet orphans its PQ.
- Addresses commit to (key type, public key): the same seed under two schemes
  is two unrelated wallets. An SLH-DSA sign takes ~1s in pure python.
- Verification needs node (the wasm host). No node → witnesses are refused,
  visibly (`pq_algos` → `wasm.engine`); `POSTQUANT_WASM=off` is the explicit
  dev-only opt-out. A new key type ships as a plugin .py (+ .wasm with its
  declared sha3) in `pq/algos.d/` or `~/.mod/postquant/algos/`; classical
  algorithms register but the mempool gate refuses their witnesses.
- REST mirrors the tools 1:1 (`/set` = `pq_set`, args identical; reads GET,
  writes POST). `server.secret` in the state dir gates the write routes.
