# postquant

A post-quantum L1 whose entire state machine is a market in key/value space.

There is one thing to own on this chain — a key in the store — and the whole
protocol is the market for it. A key maps to a value, the value is bytes and
is usually a 32-byte SHA3-256 hash of something kept off-chain, and holding
that pair costs money for as long as you hold it.

## No curves

Every signature is ML-DSA (FIPS 204, lattice), key exchange is ML-KEM
(FIPS 203), and every commitment is SHA3-256. Nothing here is ed25519 or
secp256k1, because a curve is exactly the thing Shor's algorithm takes apart.
What survives a quantum adversary is lattices and hashes, at worst with a
square-root loss the parameter sizes already absorb. Both schemes are
implemented from the FIPS specs in pure python under `pq/`, no dependencies.

An address is `pq` + 20 bytes of domain-separated SHA3-256 over the ML-DSA
public key. The first transaction from an address carries its key inline
(~1.3KB); every later one does not.

## Three prices, separate on purpose

- **write gas** — one-time, per byte entering the state. A key byte costs 4x
  a value byte (a key is an index entry the whole network sorts forever), and
  a value declared `kind=hash` is cheaper still: the chain prices commitments
  below blobs, out loud.
- **witness gas** — per byte of signature and public key. An ML-DSA-44
  signature is 2420 bytes against ed25519's 64. A chain that charges a flat
  21000 for a transfer is quietly subsidising its own witnesses; this one
  bills them.
- **rent** — per byte per hour against a prepaid escrow. When the escrow runs
  dry the entry expires and anyone may sweep it for the bond the writer put
  up, which is what makes expiry real rather than advisory. Delete the entry
  yourself and the bond comes back whole.

Write gas settles at a base fee that floats with state growth — EIP-1559,
but the scarce resource metered is every node's disk forever, not execution.
Base fee burned, tip to the proposer.

Keys can be **listed and bought**: the buyer takes the entry with its
remaining lease and bond. Human-readable keys are the point of a namespace
market — you cannot bid on something you cannot say.

## Consensus, honestly

Single proposer — authority, not agreement. What is real is the ordered log
(`blocks.jsonl`), deterministic execution, and a state root anyone can
recompute: `pq_verify` replays every block from genesis and re-checks every
parent link, header hash, transaction root, state root and proposer
signature, which is the check that would still matter under any consensus.
`pq_prove` returns a Merkle path from any key to the state root — what a
light client needs to trust a commitment without holding the store.

## Run it

```
python3 api.py                 # REST + console + MCP on :51030, block loop on
python3 mcp.py                 # MCP over stdio
python3 -m pytest tests -q     # the suite, against a throwaway chain
```

- Console: `http://localhost:51030/postquant`
- REST: `GET /head /market /keys /get?key= /quote?key= /prove?key= …`,
  `POST /set /del /fund /sweep /list /buy /transfer /wallet /faucet /mine`
- MCP: `POST /mcp` (Streamable HTTP), 23 tools, `pq_head` through `pq_verify`

The usual life of a key:

```
POST /wallet  {"action":"create","name":"alice"}
POST /faucet  {"wallet":"alice","amount":"100"}          # amounts as strings are PQ
GET  /quote?key=docs/readme&data=hello&days=7            # price before signing
POST /set     {"key":"docs/readme","data":"hello","days":7,"wallet":"alice"}
GET  /get?key=docs/readme                                # the 32-byte commitment
POST /check   {"key":"docs/readme","data":"hello"}       # matches: true
GET  /prove?key=docs/readme                              # Merkle path to the root
```

Amounts: a **string** (`"1.5"`, `"25"`) is PQ, the display unit; a **bare
integer** is nq, the base unit (1 PQ = 1e9 nq). Nothing is guessed from
magnitude.

## State

`~/.mod/postquant/` — `keys.json` (32-byte wallet seeds, mode 0600, never
committed; ML-DSA keys are deterministic from the seed), and per chain
`blocks.jsonl` (the truth) plus `state.json` (a snapshot, thrown away and
replayed if it disagrees with the log).

Writes are open by default on a local devnet. Create
`~/.mod/postquant/server.secret` to gate the spending routes behind
`Authorization: Bearer <secret>`.

## Layout

```
pq/mldsa.py     ML-DSA (FIPS 204), pure python
pq/mlkem.py     ML-KEM (FIPS 203), pure python
state.py        the state machine — pure functions of (state, tx, timestamp)
keys.py         keystore, addresses, transaction signing
chain.py        blocks, mempool, the proposer, replay + verify
mcp.py          23 tools; call_tool() is the one door
api.py          REST + console + MCP on one port
console.html    the app
mod.py          the module surface
```
