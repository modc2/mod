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
implemented from the FIPS specs in pure python under `src/pq/`, no dependencies.

An account picks its key type at wallet creation and its address commits to
the choice: `pq` + 20 bytes of domain-separated SHA3-256 over the algorithm's
domain and the public key, so a signature can never be replayed across key
types. The first transaction from an address carries its key inline; every
later one does not.

## The wasm is the algorithm

A key type is not a branch in the node's code — it is a compiled **wasm
verifier** plus the SHA3-256 of its bytes (`src/pq/wasm/manifest.json`). Every
transaction the chain accepts has its witness checked *inside* the wasm
module registered for its key's algorithm: the node refuses to run a blob
whose hash does not match the registry, and refuses the witness outright if
the module cannot run. What "valid signature" means is thereby pinned to
exact bytes any node can hash, ship and re-run.

That is also what makes the key-type set amendable. A future algorithm is one
plugin file — `src/pq/algos.d/<name>.py` in-tree, or `~/.mod/postquant/algos/`
per node — that registers keygen/sign/verify and stamps its `SigAlgo` with
`wasm={file, sha3_256}`; the binding pass (`src/pq/algos.d/zz_wasm.py` →
`src/pq/wasmvm.py`) then enforces that blob on every witness of that type. The
gate prices complexity instead of prohibiting classical: any key type may
witness — `src/pq/algos.d/ed25519.py` is the worked classical example, admitted
at ~2^126 best-known classical work — provided the best-known classical
attack clears a 2^100 floor (brute force assuming a maximal-entropy seed)
and, for plugin schemes, an empirical entropy probe holds up: deterministic
keygen, distinct keys, every seed region load-bearing, sign/verify that
binds the message. What a quantum adversary pays is printed per scheme
(`pq_complexity` / `GET /complexity`) — polynomial for anything that
declared `quantum_safe=false` — and is reflected in witness gas, but only
`POSTQUANT_REQUIRE_PQ=1` turns it back into a refusal. A scheme below the
floor or failing the probe is refused with `insufficient_complexity` and
the measurement attached.

The builtin verifiers are dependency-free Rust in `src/pq/wasm-src/`, compiled
with bare rustc (no cargo, no network) by `build.sh`, executed by a
persistent node host, and parity-tested against the python references
(`parity_test.py`: valid / tampered-sig / tampered-msg / wrong-ctx /
wrong-key across every scheme).

### Bring your own key type

The plugin dirs are the node operator's door (python runs as the node). The
door for everyone else is `pq_keytype action=create wasm=<base64>`: the whole
scheme — keygen, sign *and* verify — compiled to **one wasm blob exporting
the pq2 ABI** (`pq_keygen(seed,seed_len,pk_out,sk_out)`,
`pq_sign(sk,sk_len,msg,msg_len,ctx,ctx_len,sig_out)`, plus pq1's
`pq_verify`), with the exact pk/sig/sk sizes declared at registration. The
admission rule is that the scheme can be **verified deterministically**, and
it is enforced, not promised: the blob must declare **zero imports** (parsed
before a single instruction runs, and re-checked by the host on every call —
an importless module has no clock, no randomness and no host call, so every
export is a pure function of its inputs that any node can re-run forever),
and it must survive the full proof battery (roundtrip, tampered
message/signature, truncation, context and key binding, the entropy probe
against its own keygen), a determinism double-run across fresh instances,
runtime budgets (a verifier slower than `POSTQUANT_WASMTYPE_VERIFY_MS`,
default 2s, is a mempool denial of service and is refused), and the same
complexity floor as everything else. Any failure refuses the registration
outright with the measurement attached — names are hashed into addresses and
can never be rebound, so junk must not squat them. What admits is stored
content-addressed (`~/.mod/postquant/wasmtypes/<sha3>.wasm` +
`wasmtypes.json`) and from then on the hash *is* the algorithm: wallets pick
it by name, witness gas bills its real bytes, composites may use it as a
part, and replay re-judges its witnesses through the same pinned blob.
`src/pq/wasm-src/examples/lamport.rs` is the worked example — Lamport
one-time signatures over SHAKE256, ~60 lines of scheme, built with bare
rustc and registered through the console's BUILDER tab or the tool.

## The key type builder

Key types compose. `pq_keytype action=create op=all parts=ML-DSA-44,ed25519`
mints a NEW key type out of registered ones and stores its spec in
`~/.mod/postquant/keytypes.json`: `op=all` is the hybrid AND — every part
signs, verify demands every part, so it falls only if *every* part falls and
is quantum-safe if *any* part is; `op=any` is the 1-of-n OR — one part's
signature suffices, so it is exactly as strong as its weakest part, and the
catalog card says so. Parts may themselves be composites, so the registry is
a DAG (`pq_keytype_dag` returns it; the console's BUILDER tab draws it and
lets you mix and match by clicking). A composite is gated like any plugin —
the entropy probe runs against the composed keygen — and `pq_keytype_test`
runs the full proof battery on demand: roundtrip, tampered message and
signature, truncation, context and key binding, per-part ablation (an "all"
that survives a corrupted part is lying about that part), plus a fresh
probe. Refutation-only, like everything here: green means no failure was
found, never "secure". Deleting a composite is refused while another
composite or any on-chain account depends on it — a replay audit must always
be able to re-judge every historic witness. The sub-seeds are domain-
separated per composite name and slot, so a part key inside a hybrid is
never the same key as a bare wallet under that part scheme.

### Threshold: the dial between them

`op=threshold k=2 parts=ML-DSA-44,SLH-DSA-SHAKE-128f,ed25519` is k-of-n: a
signature is a one-byte bitmap naming exactly k parts plus their
signatures. An attacker forges the k cheapest parts, so the card's
classical cost is the k-th weakest part's, and it is quantum-safe only if
fewer than k parts fall to Shor. k=1 is `any`, k=n is `all`; 2-of-3 across
lattice + hash + curve survives any one family falling *and* any one key
being lost. The battery adds `below_threshold_rejected` and `bitmap_bound`.

## Prims and the key type market

Everything that is not built from something else is a **prim** — the
builtin ML-DSA/SLH-DSA sets, plugin schemes like ed25519, and anyone's wasm
blob. `pq_prims` lists them with what they rest on, what breaking them
costs, which key types use them and how many accounts sign with them;
`pq_prim action=add wasm=<base64>` is the door for a new one (same
admission as above: zero imports, proof battery, determinism double-run,
complexity gate). Key types are built from prims with all / any / threshold.

`pq_keymarket` is the shelf: every prim, composite and the private cloak,
with author, accounts using it, key types forked from it, witness bytes and
gas, classical/quantum attack cost and a rarity tier, sortable
hot / new / safe / cheap / deep / used. Pass `author=<wallet> publish=true`
when creating a key type or prim and the author's wallet writes
`keytype/<name> = SHA3(spec)` on chain: **authorship is a key in the store**,
so it is provable, and it rides the market that already exists — `pq_list`
it, `pq_buy` it.

## Private key types

Every other account tells the world two things on its first spend: which
algorithm it signs with and its public key — the hard problem to attack, and
the input every attack (Shor, lattice reduction, forgery search) starts
from. A **private** account shows neither. `pq_private action=create
inner=<scheme>` makes one: its address commits to
`SHA3(inner, salt, inner_pk)`, and every witness is a sealed envelope —
ML-KEM-768 to the proposer's seal key, the inner scheme name, salt, public
key and signature inside, padded to one fixed size (64 KiB,
`POSTQUANT_SEAL_BYTES`) so ML-DSA, SLH-DSA, ed25519, wasm prims and
composites are byte-for-byte indistinguishable. The **cloak set** (every
accepted key type that fits) is the attacker's guessing space before they
have seen a single public key. Private wallets work in every write tool.

`pq_private action=prove wallet=… message=…` makes a standalone proof
(bound to its own context, so it never doubles as a tx witness); `action=
verify` answers valid / invalid with the proposer's signed attestation and
never names the scheme; `action=open` is the owner's selective disclosure
and `check_open` lets anyone recompute the commitment.

Honestly: the proposer opens envelopes — it already orders and signs every
block in this single-proposer chain, and it re-runs the gate and the inner
scheme's own (wasm-enforced) verifier, so a replay audit re-judges private
witnesses too. That makes this privacy from the world and from any future
quantum computer replaying the public chain — not from the proposer. If the
seal key (`~/.mod/postquant/seal.key`, never rotated) leaks, each account
falls back to its hidden scheme's own card. A zero-knowledge proof would
take the proposer out of the trust path; that is the upgrade, not a claim
made here.

**Forge it private in one step.** The BUILDER's *make it private* switch
(`pq_keytype action=create … private=true wallet=<name>`) forges the key
type and opens a private wallet hiding it — so a three-part composite you
just invented joins the cloak set and never appears on chain.

## Cairo: one more wall

`CAIRO-MSS-SHA256` (`src/pq/cairomss.py`, plugin `src/pq/algos.d/cairo_mss.py`)
is a hash-based key type resting on **SHA-256 alone** — independent of the
module lattices under ML-DSA and the SHAKE256 under SLH-DSA — so stacking it
into an ALL composite makes a quantum forger break one more unrelated
assumption (Grover on SHA-256: ~2^128). Construction: SPHINCS-shaped
hypertree without FORS — 4 layers of 6-high Merkle trees of WOTS keys
(n=32, w=16, 67 chains, 2^24 leaves), T = SHA-256(pub_seed | adrs | data);
pk 64 B, sig 9376 B; keygen ~0.15 s, sign ~0.6 s, verify ~10 ms.

Its verifier is a **Cairo 2 program** (`src/pq/cairo/src/lib.cairo`, a Scarb
package; every hash input is whole u32 words through `core::sha256`).
`pq_cairo action=check` signs with a throwaway key and runs the witness in
the Cairo VM (`scarb cairo-run`, ~1.5 s, serialized), reporting parity with
the Python verifier the chain's hot path uses; `tamper=true` must come back
invalid. A Cairo execution is what a STARK proves, and STARKs are hash-based —
that is the route to verifying hidden witnesses without trusting the
proposer. This node ships the verifier and the parity check, **not a prover**.

Honest limit: **few-time** — the leaf is picked by the message hash, so
one-time-key reuse odds are ~q²/2^25 after q signatures (~0.03% at 100).
It is a layer, not a solo wallet for heavy use. The BUILDER's
**QUANTUM-HARD PRESET** picks ML-DSA-44 + SLH-DSA-SHAKE-128f +
CAIRO-MSS-SHA256 as ALL-of: lattice, SHAKE and SHA-256, all must fall.

## The vault: encrypted before it leaves you

The chain stores 32-byte commitments; the **vault** stores the file, and
the nodes that hold it can't read it. The client seals with AES-256-GCM
(WebCrypto in the browser, byte-identical pure Python in `src/pq/aesgcm.py`,
pinned by NIST SP 800-38D vectors) into a `PQV1` envelope: a 37-byte header
(magic, mode, PBKDF2 rounds, salt, IV) bound as AEAD data, then the
ciphertext of `u32 meta-length | {name,type,size} | data | zero pad` to a
power-of-two bucket. The node stores ciphertext only, under
`cid = SHA3-256(blob)`. It refuses blobs with no valid header or with a body
below 7.5 bits/byte of entropy (an accident guard; no node can prove a blob
is encrypted), and keeps a blob as long as a live chain entry commits to its
cid. Rent on the 32-byte pointer keeps the bytes.

```
pq_vault action=put blob=<base64 PQV1> key=vault/notes wallet=pioneer days=7
pq_vault action=get key=vault/notes          # ciphertext + cid check
mod.py vault_put path=~/notes.md key=vault/notes   # seals HERE, uploads ciphertext
mod.py vault_get secret=pqv1:<cid>#<key>           # fetches, opens HERE
```

Key mode returns a share link `pqv1:<cid>#<key>`. The key sits after the
`#`, which browsers never send. Passphrase mode uses PBKDF2-HMAC-SHA256 at
600k rounds. Env: `POSTQUANT_VAULT_MAX` (8 MiB per blob),
`POSTQUANT_VAULT_QUOTA` (1 GiB), `POSTQUANT_VAULT_GRACE` (3600 s before an
unanchored blob is pruned).

## Smart contracts: stored by hash, owned by key

*Store, don't execute.* `pq_contract action=deploy name=counter lang=cairo
code=…` (or `code_b64=` for wasm, `file=` locally) checks that the code is
what it claims to be (wasm starts with `\0asm`, sierra has
`sierra_program`) and stores it public in the vault. It then writes
`contract/<name> = SHA3-256(code)`. The anchor key has an owner, rent and a
market price like any key. Re-deploying the name is an upgrade, and the block
log keeps every version. `show` returns the code, the hash check and a Merkle
proof to the state root. Contract state lives at `contract/<name>/<k>`, and
only the anchor's owner can write it. Execution is off consensus: anyone
fetches the code by cid and re-runs it. A Cairo run can be STARK-proven and
its proof hash anchored next to the code.

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
python3 src/api.py             # REST + console + MCP on :51030, block loop on
python3 src/mcp.py             # MCP over stdio
python3 -m pytest tests -q     # the suite, against a throwaway chain
```

- Console: `http://localhost:51030/postquant`
- REST: `GET /head /market /keys /get?key= /quote?key= /prove?key= …`,
  `POST /set /del /fund /sweep /list /buy /transfer /wallet /faucet /mine`
- MCP: `POST /mcp` (Streamable HTTP), 28 tools, `pq_head` through `pq_keytype_dag`

## Ask it

The chain speaks the fleet's agent contract, so you can just say what you
want — in the console's ASK tab, over HTTP, or from the shell:

```
python3 src/agent.py "create wallet alice then faucet 100 to alice"
curl -N localhost:51030/run/stream -d '{"query":"set greeting to hi for 2 days from alice then get greeting"}'
GET  /agents                     # the roster orbit/build probes: pq, pq-reader
```

- **Three brains.** `rules` (default, always on) is a plain-English command
  parser: no model, no network, no key, free. `chat` turns on by itself
  whenever the **liquidai module** answers on this box: commands in the
  message are still parsed and executed by rules — deterministic, gated,
  free — and then the LFM resident in liquidai's server runtime answers in
  plain language, grounded in those results, the whitepaper glossary and
  live chain state, streaming real tokens (`lfm.py`; it mints liquidai's
  own local session token, no key to configure). The model never picks
  tools or amounts, so a fallback can never re-sign anything — liquidai
  down degrades to the rules summary, out loud. `llm` is optional — point
  `POSTQUANT_AGENT_LLM` at any OpenAI-compatible endpoint with tool calling
  (a local ollama: `http://127.0.0.1:11434/v1`) and it drives the same tools;
  if it fails the run falls back to rules and says so.
- **Conversation is a parameter.** `POST /run|/run/stream` take
  `history: [{"role":"user|assistant","content":"..."}]` and
  `brain: "chat"|"rules"|"llm"` (default auto: llm if configured, else chat
  if liquidai is up, else rules).
- **One door.** Every step is `mcp.call_tool()`, the function REST and MCP
  use, so the agent cannot see a different chain or skip a check.
- **Same write gate.** With `server.secret` present, a run without the bearer
  still answers: writes that can be priced come back as dry runs, the rest
  are refused with the reason. The `pq-reader` agent never signs.
- Events (SSE): `model_start`, `token`, `tool_start`, `step`, `done|error` —
  orbit/agent's names, so orbit/build draws a postquant run like any other
  job. Mount it there with `POST /agents/mods {"mod":"orbit/postquant"}`.

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

A local caller — the shell, stdio MCP, or loopback with no proxy in front —
can anchor a file on this box's disk instead of inline data: `file=` streams
it through SHA3-256 and only the 32-byte digest lands on-chain.

```
POST /set     {"key":"fs/model.bin","file":"/path/on/this/box","days":7}
POST /check   {"key":"fs/model.bin","file":"/path/on/this/box"}   # re-verify
```

The public HTTP surface refuses `file=` (403 `local_only`): a node that will
hash any path a remote caller names is an existence-and-content oracle on the
operator's disk. Remote callers hash the file where it lives and pass `hash=`.

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
src/pq/mldsa.py   ML-DSA (FIPS 204), pure python — keygen/sign/reference verify
src/pq/slhdsa.py  SLH-DSA (FIPS 205), pure python — the hash-based hedge
src/pq/mlkem.py   ML-KEM (FIPS 203), pure python
src/pq/algos.py   the key-type registry; plugins in src/pq/algos.d/
src/pq/wasmvm.py  the enforcement layer — every witness runs its type's wasm
src/pq/wasm/      the verifier blobs + manifest.json (SHA3-256 per blob)
src/pq/wasm-src/  their Rust sources, build.sh (bare rustc), parity_test.py
src/pq/cairomss.py CAIRO-MSS-SHA256 — hash-based key type + Cairo VM bridge
src/pq/cairo/     its verifier as a Cairo 2 program (Scarb package)
src/pq/aesgcm.py  AES-256-GCM, pure python — the vault's client-side cipher
src/vault.py      PQV1 envelope (seal/open) + the ciphertext-only blob store
src/state.py      the state machine — pure functions of (state, tx, timestamp)
src/keys.py       keystore, addresses, transaction signing
src/chain.py      blocks, mempool, the proposer, replay + verify
src/mcp.py        35 tools; call_tool() is the one door
src/agent.py      plain English → call_tool(); rules + chat (liquidai) + llm brains
src/lfm.py        the chat brain's model: liquidai /chat SSE + its local token mint
src/api.py        REST + console + MCP on one port
src/console.html  the app
mod.py            the module surface
```
