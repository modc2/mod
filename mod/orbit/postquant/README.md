# postquant

A post-quantum L1 whose entire state machine is a market in key/value space.

There is one thing to own on this chain — a key in the store — and the whole
protocol is the market for it. A key maps to a value, the value is bytes and
is usually a 32-byte SHA3-256 hash of something kept off-chain, and holding
that pair costs money for as long as you hold it.

## No curves

Signatures come from two families that share nothing: ML-DSA (FIPS 204,
module lattices — 44/65/87) and SLH-DSA (FIPS 205, nothing but SHAKE256).
If lattices fall, a hash-based witness still stands. Key exchange is ML-KEM
(FIPS 203) and every commitment is SHA3-256. Nothing here is ed25519 or
secp256k1, because a curve is exactly the thing Shor's algorithm takes apart.
What survives a quantum adversary is lattices and hashes, at worst with a
square-root loss the parameter sizes already absorb. Every scheme is
implemented from the FIPS specs in pure python under `pq/`, no dependencies.

An account picks its key type at wallet creation and its address commits to
the choice: `pq` + 20 bytes of domain-separated SHA3-256 over the algorithm's
domain and the public key, so a signature can never be replayed across key
types. The first transaction from an address carries its key inline; every
later one does not.

## The wasm is the algorithm

A key type is not a branch in the node's code — it is a compiled **wasm
verifier** plus the SHA3-256 of its bytes (`pq/wasm/manifest.json`). Every
transaction the chain accepts has its witness checked *inside* the wasm
module registered for its key's algorithm: the node refuses to run a blob
whose hash does not match the registry, and refuses the witness outright if
the module cannot run. What "valid signature" means is thereby pinned to
exact bytes any node can hash, ship and re-run.

That is also what makes the key-type set amendable. A future algorithm is one
plugin file — `pq/algos.d/<name>.py` in-tree, or `~/.mod/postquant/algos/`
per node — that registers keygen/sign/verify and stamps its `SigAlgo` with
`wasm={file, sha3_256}`; the binding pass (`pq/algos.d/zz_wasm.py` →
`pq/wasmvm.py`) then enforces that blob on every witness of that type. The
quantum gate still applies: an algorithm that declares itself classical
registers, shows up in `pq_algos`, and is turned away at the mempool
(`pq/algos.d/ed25519.py` is the worked example).

The builtin verifiers are dependency-free Rust in `pq/wasm-src/`, compiled
with bare rustc (no cargo, no network) by `build.sh`, executed by a
persistent node host, and parity-tested against the python references
(`parity_test.py`: valid / tampered-sig / tampered-msg / wrong-ctx /
wrong-key across every scheme).

## Three prices, separate on purpose

- **write gas** — one-time, per byte entering the state. A key byte costs 4x
  a value byte (a key is an index entry the whole network sorts forever), and
  a value declared `kind=hash` is cheaper still: the chain prices commitments
  below blobs, out loud.
- **witness gas** — per byte of signature and public key. An ML-DSA-44
  signature is 2420 bytes against ed25519's 64, and an SLH-DSA-SHAKE-128f
  one is 17088 — the honest price of resting on nothing but hashes. A chain
  that charges a flat 21000 for a transfer is quietly subsidising its own
  witnesses; this one bills each key type for exactly the bytes it puts on
  the wire (`pq_quote scheme=` prices the difference).
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
- MCP: `POST /mcp` (Streamable HTTP), 24 tools, `pq_head` through `pq_algos`

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
committed; every scheme's keys are deterministic from the seed), and per chain
`blocks.jsonl` (the truth) plus `state.json` (a snapshot, thrown away and
replayed if it disagrees with the log).

Writes are open by default on a local devnet. Create
`~/.mod/postquant/server.secret` to gate the spending routes behind
`Authorization: Bearer <secret>`.

## Layout

```
pq/mldsa.py     ML-DSA (FIPS 204), pure python — keygen/sign/reference verify
pq/slhdsa.py    SLH-DSA (FIPS 205), pure python — the hash-based hedge
pq/mlkem.py     ML-KEM (FIPS 203), pure python
pq/algos.py     the key-type registry; plugins in pq/algos.d/
pq/wasmvm.py    the enforcement layer — every witness runs its type's wasm
pq/wasm/        the verifier blobs + manifest.json (SHA3-256 per blob)
pq/wasm-src/    their Rust sources, build.sh (bare rustc), parity_test.py
state.py        the state machine — pure functions of (state, tx, timestamp)
keys.py         keystore, addresses, transaction signing
chain.py        blocks, mempool, the proposer, replay + verify
mcp.py          24 tools; call_tool() is the one door
api.py          REST + console + MCP on one port
console.html    the app
mod.py          the module surface
```
