---
name: govmod
description: Modular governance for multi-option staked disputes — players stake on the options of a question (two or many), then a token-weighted vote or an M-of-N multisig picks the winning option whose backers split the pot pro-rata. Supports SEALED (private commit-reveal) voting that hides individual votes until settlement (only aggregates show), with zk-friendly nullifiers + eligibility-proof hook, and gas-minimal Merkle-claim settlement. Use for stake-backed bets/disputes/proposals resolved by token vote or arbiter multisig.
---

# govmod

Players stake on the **options** of a question; a **modular** verdict mechanism
picks the winning option, whose backers split the pot pro-rata. All players agree
to the terms first.

## Terms (agreed by every player, hashed into `terms_hash`)
- `options` — ≥ 2 choices to vote on.
- `token` — voting token, default `bloctime` (any chain token).
- `time_limit` — voting window **in blocks** (BlocTime clock); `deadline = activated_block + time_limit`.
- `threshold` — quorum: min participating weight for a valid verdict.
- `min_stake` — agreed buy-in.
- `verdict_mode` — `token` (weighted vote) or `multisig` (`signers`/`required_sigs`).
- `privacy` — `public` or `sealed` (commit-reveal; votes hidden until resolve).

Players `join` (stake an option) only by agreeing to `terms_hash` (optional sig).
Pre-activation the opener can `amend`/`cancel`; a player can `leave`.

## Lifecycle
`open` → `join…` → opener `activate` → `active` → `resolve` → `resolved` | `no_verdict`
(below-quorum / tie / no-multisig-quorum → `no_verdict`, all backers refunded).
Winner = option chosen by the verdict mechanism; its backers split the pot pro-rata.

## Sealed (private) voting
- `seal(option, salt)` / `gen_salt` — voter hashes the choice CLIENT-SIDE.
- `commit(case, commitment, weight)` — submit only the hash; ballot stored under a
  **nullifier** (not the address). Live views show only aggregates.
- `reveal(case, option, salt)` — after the deadline; verified vs the commitment.
- `resolve` — first place the per-option tally is exposed. Unrevealed ballots don't count.
- `verify_eligibility(case, proof, addr)` — pluggable zk hook (Semaphore / SNARK);
  default trusts the coordinator's on-chain power. Tally is homomorphic (Pedersen-ready).

## Gas-minimal settlement
Participation is off-chain (zero gas). `settle_plan` picks: 1 recipient → `transfer`;
many → **Merkle-claim** (post one 32-byte `merkle_root`, winners pull with
`merkle_proof`, O(1) gas each). `net_flows` gives the deltas to move. OZ sorted-pair
hashing → proofs verify against an on-chain `MerkleProof` claim contract.

## Key functions
`open`, `join`, `leave`, `activate`, `amend`, `cancel`, `vote` (public),
`commit`/`reveal`/`seal`/`gen_salt` (sealed), `sign_verdict` (multisig),
`tally`, `resolve`, `settle_plan`/`merkle_root`/`merkle_proof`, `case`/`cases`, `info`.

## Examples
```bash
m govmod/open question="best pet?" options='["cats","dogs","birds"]' \
    stake=1000 option=cats token=bloctime threshold=5000 time_limit=200000
m govmod/join 0 option=dogs stake=1000 key=bob
m govmod/activate 0
m govmod/vote 0 option=cats key=carol
m govmod/resolve 0

# sealed
m govmod/open question="proposal?" options='["yes","no","abstain"]' \
    stake=100 option=yes privacy=sealed threshold=1000 time_limit=50000
SALT=$(m govmod/gen_salt); m govmod/commit 1 commitment=$(m govmod/seal yes $SALT) key=dave
m govmod/reveal 1 option=yes salt=$SALT key=dave   # after deadline
m govmod/resolve 1
```

State: local index `~/.mod/govmod/cases.json` (usable standalone; settlement is an
explicit `_settle`/`settle_plan` hook). Tests: `pytest mod/orbit/govmod/tests` (17).
Related: `bloctime`, `multisig`, `staketime`, `chain`, `webchain`.
