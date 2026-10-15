# zuma: anonymous subnets on Starknet and NEAR

*v0.3 · October 2026*

## Abstract

In a Bittensor-style subnet, miners do work and validators score it. Both sides
are identified, so a validator can favour a friend's miner and a miner can tell
who scored it down. zuma removes the identities and keeps the rules. Miners and
validators are public **sets** of keys. Every action is a **linkable ring
signature** that proves "one member of this set did this", plus a key image that
enforces one action per member per scope. The contract learns that the rules were
followed and never learns who followed them.

zuma ships the same protocol on two chains:

- **Starknet.** A Cairo contract, where the STARK curve is native: the EC builtin
  does the curve math.
- **NEAR.** A Rust/wasm contract that verifies **the same signatures, byte for
  byte**. NEAR has no STARK-curve host function, so zuma brings its own field
  and curve arithmetic, built for wasm gas.

Both contracts are signed for by one prover, a Cairo program, and the two test
suites pin the Rust port to Cairo's output.

## 1. The problem

A subnet needs four things:

1. Only registered miners submit, once per epoch.
2. Only registered validators score, once per submission.
3. A miner can claim its share after the epoch closes, exactly once.
4. Nobody can match a submission, a score or a payout to a member.

Points 1 to 3 normally come from identity: `msg.sender` is the miner. Point 4
rules that out. zuma swaps identity for **set membership plus a scope-bound
pseudonym**.

## 2. The scheme: scope-linkable LSAG over the STARK curve

Each member holds a secret `x` and a public key `P = x·G` on the STARK curve
(`y² = x³ + x + β` over `p = 2^251 + 17·2^192 + 1`, prime order, no cofactor).

For a **scope** `σ` (a field element), let `Hp(σ)` be a curve point with no known
discrete log. zuma uses try-and-increment: the first `ctr` such that
`X = poseidon('zuma-h2p', σ, ctr)` is a valid x-coordinate, taking the *smaller*
of the two roots as `y`.

A signature on message `m` by a ring `P_0 … P_{n-1}` is `(c_0, s_0 … s_{n-1}, I)`:

```
I     = x · Hp(σ)                                    key image
d     = poseidon('zuma-msg', σ, m, I, n, P_0 … P_{n-1})
L_i   = s_i·G     + c_i·P_i
R_i   = s_i·Hp(σ) + c_i·I
c_{i+1} = poseidon('zuma-chal', d, L_i, R_i)
valid  ⇔  c_n = c_0
```

It is Liu–Wei–Wong LSAG with Poseidon as the random oracle. Two properties carry
the whole protocol:

- **Anonymity.** The signature reveals no information about which `P_i` signed,
  beyond membership in the ring.
- **Scope linkability.** One key signing twice in the same scope produces the
  same `I`. The same key in two different scopes produces images that can't be
  linked, under DDH.

Rings must be sorted by strictly increasing `key_id = poseidon('zuma-key', P)`,
so a ring can't be padded with duplicates to look bigger than it is.

## 3. The protocol

| action | signer | scope `σ` | message | the key image is… | rule it enforces |
|---|---|---|---|---|---|
| `submit` | a miner | `H('zuma-mine', subnet, epoch)` | work commitment | the miner's **tag** | one submission per miner per epoch |
| `score` | a validator | `H('zuma-score', subnet, epoch, tag)` | score 0..100 | a **nullifier** | one score per validator per tag |
| `claim` | a miner | the submit scope again | recipient | the same tag | one claim per tag, only by its owner |

A validator sees a tag, never a miner. A miner's tags in different epochs can't
be linked. A validator's scores on different tags can't be linked either, because
each tag has its own scope. A claim re-signs in the epoch's mine scope, and only
the key that made the tag can reproduce its image. The contract then credits
`emission · tag_sum / epoch_total` points to any recipient, and a second claim
hits the same image and is rejected.

**Subnet and epoch.** The subnet felt binds every scope to one deployment.
`epoch = block / tempo`.

**Relayers.** The contracts never check the caller on the anonymous actions.
Members hand signed payloads to a relayer, because a transaction's sender is
always public.

## 4. Two implementations, one wire format

| | Starknet | NEAR |
|---|---|---|
| contract | `contracts/src/zuma.cairo` | `near/src/lib.rs` (near-sdk 5) |
| LSAG verifier | `contracts/src/lsag.cairo` | `near/src/lsag.rs`, a line-by-line port |
| curve math | native EC builtin | `near/src/fp.rs`, in wasm |
| Poseidon | native builtin | `near/src/poseidon.rs`, in wasm |
| subnet felt | the contract address | `sha256(account id)`, top 5 bits cleared |
| recipient | a felt (address) | a NEAR account, signed as `sha256(account)` |
| epoch clock | block number | block height |
| max ring | 64 | 32 (gas-bound, §5) |
| tests | snforge | `cargo test`, including Cairo fixtures |

**One prover signs for both.** `prover/` is a Cairo program built on the same
`lsag` module the Starknet contract runs. On NEAR, the driver asks the contract
for its scope through the `mine_scope` and `score_scope` views, then signs with
that same prover. Only the subnet felt differs between chains, so the signatures
are interchangeable. The Rust test suite checks this directly:

- `hash_to_point_matches_cairo`, `keys_and_ids_match_cairo` and
  `scopes_match_cairo` compare against values produced by Cairo.
- `cairo_signature_verifies_in_rust` verifies a signature made by the Cairo
  prover.

## 5. Making the STARK curve affordable on NEAR

Starknet gets the STARK curve for free. NEAR charges gas for every wasm
instruction and caps a call at 300 Tgas. The first port called
starknet-types-core and lambdaworks directly. It worked, but it cost
**~214 Tgas for a ring of 4**, which caps rings at about 5 members — too few
for real anonymity.

We measured each primitive inside a deployed contract (`near/src/bench.rs`,
which returns `env::used_gas()` deltas). Four fixes followed.

1. **A field type shaped for the prime** (`fp.rs`, generated by
   `tools/gen_fp.py`). Elements are 9 limbs of 29 bits. A 29×29 product is
   below 2^58, so a schoolbook column of up to 9 products adds up inside a u64
   with no carry handling. The modulus has only three nonzero limbs in radix
   2^29 (`1`, `17<<18`, `1<<19`) and `p ≡ 1 (mod 2^29)`. That makes Montgomery
   reduction multiply-free: `m = −t_i` and `m·p` is three shifted adds.
   Dedicated squaring needs 45 products instead of 81.
2. **No `__multi3`.** In wasm, rustc lowers every checked u64 multiply and every
   u128 multiply to a call. The field core uses explicit wrapping arithmetic,
   where the bounds are proven by construction. The contract's own sums use
   explicit `checked_*` operations, and the release profile drops blanket
   overflow checks.
3. **Better curve formulas.** Jacobian coordinates with `a = 1` doubling
   (1M+8S) and mixed addition (7M+4S), handling every exceptional case exactly.
   Each `L_i` and `R_i` is one **Straus double-scalar multiplication** with 4-bit
   windows: 252 shared doublings and at most 126 mixed additions. The `G`, `Hp`
   and `I` tables are built once per call, and point normalization uses
   Montgomery's batch-inversion trick.
4. **Cheaper hashing and hash-to-point.** Poseidon (the Hades permutation with
   Starknet's optimized round constants) runs on the fast field. `Hp(σ)` can
   take a **hint**: the client fetches `y` from the free `hash_point` view, and
   the contract checks it instead of taking a square root. That root is
   expensive here because the field's 2-adicity is 192. Every earlier counter is
   proven a non-square with Euler's criterion, and since `(p−1)/2 = 2^250 +
   2^195 + 2^191` that costs 250 squarings and 2 multiplies. A wrong hint is
   never trusted; it only costs the slow path.

| primitive (in-contract) | before | after |
|---|---|---|
| field multiply | 1.85 Ggas | 0.87 Ggas |
| double-scalar mul `a·A + b·B` | 2 × 11.1 Tgas | 2.68 Tgas |
| Poseidon, 6 inputs | 1.57 Tgas | 1.06 Tgas |
| **submit, ring 4** | **214 Tgas** | **36 Tgas** |

Gas per submit, measured on near-sandbox (neard 2.0):

| ring | 4 | 8 | 16 | 24 | 32 | 40 |
|---|---|---|---|---|---|---|
| Tgas | 36.3 | 66.5 | 126.3 | 186.7 | 246.7 | over the limit |

That is about **7.5 Tgas per member plus 6 Tgas fixed**, so the NEAR contract
caps rings at 32. Each optimized path is differentially tested against the
reference implementation: field ops, the group law including doubling inside an
add, `P + (−P)` and zero scalars, Poseidon, hinted hash-to-point under wrong
hints, and full verification on random signatures.

**Getting it to load at all.** Modern rustc emits bulk-memory and overlong
`call_indirect` encodings, even with `-C target-cpu=mvp`, because they come from
the precompiled std. nearcore rejects both with `PrepareError: Deserialization`.
`tools/wasmlower` rewrites `memory.copy` and `memory.fill` into calls to
memmove-safe helpers, re-encodes every instruction, drops the DataCount section,
and **validates the output as MVP + sign-ext before it is written**. It does the
same job as `cargo near`'s wasm-opt pass, without binaryen. The crate also builds
with `--cfg near`, as cargo-near does. Without it, near-sdk 5.2x compiles its
local fallbacks: panics trap with no message, and sha256 runs in wasm instead of
the host.

## 6. Security and limits

- **The anonymity set is the ring.** With 3 validators, a score points to 1 of
  3. `min_ring` is enforced on-chain, and padding is impossible. No contract can
  create members who don't exist.
- **Metadata.** Transaction senders, timing, IP addresses and work content can
  all identify a member. Use a relayer, and don't post from a registered
  member's own account.
- **Collusion.** A validator who is also a miner knows its own tag. Scores are
  summed, so an honest majority dominates. A Yuma-style clipped median is the
  next step; see `cairo.zuma` for the Yuma-math variant.
- **Payouts are public books.** Credits are points in contract storage, and no
  token moves. zuma deliberately doesn't turn into a pay-anyone mixer.
- **Cross-chain replay is impossible.** Every scope contains the subnet felt,
  which is the contract address on Starknet and `sha256(account)` on NEAR, so a
  signature for one deployment is meaningless on any other.

## 7. Reproduce

```bash
m zuma/test                          # snforge + cargo test, both chains
m zuma/simulate chain=starknet       # full epoch on starknet-devnet :51332
m zuma/simulate chain=near           # full epoch on near-sandbox :51333
m zuma/gas_sweep                     # NEAR gas by ring size -> data/near_gas.json
m zuma/deploy network=near-testnet account=<you>.testnet
m zuma/deploy network=sepolia account=<sncast account>
```

The console at `/zuma/` runs either chain live, shows both codebases side by
side, compares the measured costs, and renders this paper.
