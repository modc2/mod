# govmod — modular governance for multi-option staked disputes

Players stake on the **options** of a question (two or many). A **token** is then
used to vote over those options and pick a winner; the **winning option's backers
split the whole pot** pro-rata to their stake.

What makes it *modular*: the verdict mechanism, the voting token, the privacy of
the ballots, and the clock are all knobs — and **every player must agree to the
terms** before the case goes live.

| Term | Meaning | Default |
| --- | --- | --- |
| `options` | the choices being voted on (≥ 2) | — |
| `token` | which token's holders vote | `bloctime` (any chain token) |
| `time_limit` | voting window, in **blocks** | `200000` |
| `threshold` | quorum — min participating weight for a valid verdict | `0` |
| `min_stake` | agreed buy-in every backer must meet | `0` |
| `verdict_mode` | how the winner is chosen: `token` or `multisig` | `token` |
| `privacy` | `public` ballots or `sealed` (commit-reveal) | `public` |

**BlocTime is the clock.** `time_limit` is a number of chain blocks; a live case's
`deadline = activated_block + time_limit`. Token voting power defaults to each
address's BlocTime (time-weighted stake) balance.

## Verdict modes (modular governance)

- **`token`** — token holders `vote` for an option; weight = their `token` balance.
  After the deadline the option with the most weight wins, *iff* total participating
  weight meets `threshold` (quorum) and there's no tie for first. Otherwise
  `no_verdict` → every backer is refunded their own stake.
- **`multisig`** — an `M-of-N` signer set endorses an option via `sign_verdict`. The
  first option to reach `required_sigs` wins immediately; if the deadline passes with
  no option at quorum → `no_verdict`.

The winning option's backers **split the entire pot pro-rata** to their stake (a
sole backer takes it all — the classic two-player duel is just the N=2 case).

## Mutual agreement (everyone, not just two)

The opener sets the terms; the agreed fields are hashed into a `terms_hash`. Each
player can only `join` (stake an option) by agreeing to that exact hash, optionally
proving it with a signature. Until the opener `activate`s the case, players may
`join`/`leave` and the opener may `amend` (hash changes) or `cancel`. This
guarantees all participants committed to the **same** token, threshold, min-stake,
and time limit.

## Lifecycle

```
open ──join…──► (opener) activate ──► active ──resolve──► resolved | no_verdict
  │                                      │
  ├─ amend / cancel  (pre-activation)    └─ vote / commit+reveal / sign_verdict
  └─ leave           (a player exits)
```

## Sealed (private) voting — votes hidden until settlement

Set `privacy='sealed'` (verdict_mode=`token`) and individual votes stay private; only
**aggregate statistics** are visible until the case resolves.

- **Commit** — a voter hashes their choice *client-side* (`seal(option, salt)`,
  `salt` from `gen_salt`) and submits only the hash via `commit`. The coordinator
  never sees the option.
- **Anonymized ballots** — each ballot is stored under a **nullifier**
  (`keccak(voter, case)`), not the address. The published index reveals neither who
  voted nor how. While voting is open, `tally`/`case` expose only counts and total
  committed weight (`per-option tally hidden until the case resolves`).
- **Reveal at settlement** — after the deadline voters `reveal` their `(option, salt)`;
  each opening is verified against its commitment, and the per-option tally is
  computed and exposed only at `resolve`. Unrevealed ballots don't count.
- **zk hooks** — `verify_eligibility(case, proof, addr)` is a pluggable proof-of-
  eligibility hook (the default trusts the coordinator's on-chain power lookup).
  Wire it to **Semaphore** (Merkle membership + nullifier) or a SNARK proving
  `power(addr) ≥ weight` for trustless, coordinator-blind voting. The tally is
  additively homomorphic, so a production build can swap hash commitments for
  **Pedersen weight commitments** and open only the *sum*.

## Gas efficiency

Participation is **entirely off-chain** (agreement + votes are signatures/hashes →
zero gas). The only on-chain cost is moving the pot, and `settle_plan` makes that
minimal:

- **1 recipient** → a single `transfer` (no tree).
- **many recipients** → a **Merkle-claim distribution**: the settler posts a single
  32-byte `merkle_root` (~1 SSTORE + 1 event); each winner pulls their share with a
  `merkle_proof` — O(1) gas, paid by the claimer, no unbounded payout loop / OOG.
- `net_flows` reports the deltas vs each address's own stake, so a trustful settler
  can move only what actually changes hands.

`merkle_root` / `merkle_proof` use OpenZeppelin-compatible sorted-pair hashing, so
the same proofs verify against an on-chain `MerkleProof` claim contract.

## CLI

```bash
# Public, multi-option token vote (3 options)
m govmod/open question="best pet?" options='["cats","dogs","birds"]' \
    stake=1000 option=cats token=bloctime threshold=5000 time_limit=200000
m govmod/join 0 option=dogs stake=1000 key=bob
m govmod/join 0 option=birds stake=500 key=carol
m govmod/activate 0
m govmod/vote 0 option=cats key=dave
m govmod/resolve 0            # winning option's backers split the pot

# Sealed (private) voting — commit, then reveal at settlement
m govmod/open question="proposal?" options='["yes","no","abstain"]' \
    stake=100 option=yes privacy=sealed threshold=1000 time_limit=50000
m govmod/join 1 option=no stake=100 key=bob
m govmod/activate 1
SALT=$(m govmod/gen_salt)
m govmod/commit 1 commitment=$(m govmod/seal yes $SALT) key=dave   # option never sent
# … after the deadline …
m govmod/reveal 1 option=yes salt=$SALT key=dave
m govmod/resolve 1

# Multisig verdict instead of a token vote
m govmod/open question="ship it?" options='["yes","no"]' stake=500 option=yes \
    verdict_mode=multisig signers='["0xAaa","0xBbb","0xCcc"]' required_sigs=2
```

## Storage & on-chain

State lives in a local index (`~/.mod/govmod/cases.json`) mirroring the rules, so the
whole flow is usable before/independently of an on-chain deploy — same pattern as
`core/webchain`. Settlement is an explicit hook (`_settle` → `settle_plan`); wire it
to a Governance escrow + `MerkleProof` claim contract for trustless payout.

## Related modules

`bloctime` (the clock + default voting token) · `multisig` · `staketime` ·
`chain` (token balances / settlement) · `webchain` (local-index pattern).

Tests: `pytest mod/orbit/govmod/tests/test_govmod.py` (17 cases; inject
`weight=`/`now=` and a movable clock so no live chain is required).
