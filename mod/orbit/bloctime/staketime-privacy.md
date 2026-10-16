# StakeTime on Starknet — Privacy-Preserving Port

StakeTime (EVM, this module) is a public ledger: every `registerSubnet` and
`stake` call is signed by `msg.sender`, so anyone reading the chain learns
exactly **who owns which mod** and **who staked how much on whom**. The goal of
this port is to keep the same economics — time-weighted stake, pluggable
emission, a mod registry — while making those two links unreadable on-chain.

The port lives in the Starknet module **`orbit/starktime`** as the Cairo
contract `ShadowStake` (`contracts/src/shadowstake.cairo`, 12 passing snforge
tests). This document is the design of record; the contract is the
implementation.

## What we hide, and from whom

Threat model: a public observer who can read **all contract storage, all
events, and the sender account of every transaction**.

| Link to hide | EVM StakeTime | ShadowStake |
|---|---|---|
| account → mod it owns | `Registry.owner[id] = msg.sender` (public) | only `owner_commitment = H(secret, salt)` stored; no address ever |
| account → its stake positions | `StakeTime.positions[owner]` keyed by address | notes keyed only by a Poseidon `commitment`; `Note` has **no owner field** |
| account → total stake size | summable by address | notes unlinkable to each other or to an identity (set anonymity) |

## Mechanism

Identity is **knowledge of a secret**, never `get_caller_address()`.

### 1. Private stake pool (hides who / how-much)

A stake is a **note**, identified only by

```
commitment = Poseidon(secret, nullifier_secret, denom_index, lock_seconds, mod_id)
```

- **Deposit** `stake(commitment, denom_index, lock_seconds, mod_id)` pulls one
  fixed **denomination** of the token in and records
  `weight = amount × multiplier(lock) / 1e18` against the *commitment* — never
  against an address. Rewards stream pro-rata by weight through the same
  Synthetix acc-per-weight index as the public `StarkTime` pool.
- **Claim / unstake** re-present the preimage; the contract recomputes the
  commitment, settles the note, and pays a **recipient you name at spend time**
  — a fresh address or a relayer — so the payout is not tied to the depositor.
- **Nullifier** `Poseidon(nullifier_secret, commitment)` is burned on unstake
  (double-spend guard; also the hook the phase-2 ZK path keys on).

Amounts are hidden below **denomination granularity** (default 1 / 10 / 100 /
1000 tokens). You see "a 100-token note exists," not who holds it, and an
account's total across many notes is not summable.

### 2. Private mod registry (hides who owns a mod)

- `register_mod(owner_commitment, metadata_hash)` stores only
  `owner_commitment = Poseidon(owner_secret, salt)` plus public metadata.
- `update_mod(id, owner_secret, salt, new_owner_commitment, metadata_hash)`
  re-presents the preimage and **rotates** to a fresh commitment, so the
  revealed secret is burned after one use (a hash-lock with rotation).
- Notes may target a `mod_id`; the mod accrues anonymous weight.

## Honest limitations (MVP, no SNARK)

- **Unlinkable *deposit*, linkable *spend*.** A spend re-presents the
  commitment, so one deposit tx can be tied to its *own* later spend. Breaking
  that needs a ZK membership proof. Full unlinkability therefore depends on
  depositing and spending through **fresh accounts / a relayer** (a paymaster
  pattern Starknet supports natively) so neither tx names a persistent identity.
- **Amounts are bucketed, not hidden**, down to denomination granularity.
- `note_root` is an append-only commitment **accumulator** (hash chain), not yet
  a Merkle tree — it exists so phase 2 has a membership anchor.

## Phase 2 — true ZK (roadmap)

Swap the "re-present the commitment" spend for a **zero-knowledge membership
proof** verified on-chain via **garaga** (Starknet Groth16/STARK verifier) or a
**Noir** circuit:

- withdraw proves "I know the preimage of *some* commitment in the tree with
  this nullifier" without revealing *which* → unlinkable spend;
- a range proof over a Pedersen-committed amount hides stake size below
  denomination granularity.

The `note_root` accumulator and `nullifier` set are already in place as the
anchors those circuits plug into. Starknet Foundation Seed Grants (up to $25k
STRK, rolling, tooling-eligible) are a fit to fund the verifier work.
