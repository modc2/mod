# zQuorum — anonymous quorums in Cairo

A group reaches a quorum **without revealing who voted**. The ledger is
public books — tallies, nullifiers, the member set — but no vote is
linkable to a member. Private ballots, public outcomes (the same stance as
`orbit/ztensor`, which does this with LSAG ring signatures; zquorum is the
Cairo-native sibling).

## How it works

Commit/nullifier scheme (Semaphore-style), Pedersen over the STARK field.
**Every hash lives in one Cairo program** — `circuit/` (package `zq`) — so
the anchor, a future on-chain verifier and a future STARK prover can never
disagree:

```
commitment  C = pedersen('zq-commit', secret)            registered once per member
nullifier   N = pedersen(pedersen('zq-null', secret), topic)   revealed when voting
root        R = fold pedersen('zq-root', ...) over sorted commitments
```

- A member keeps a `secret` and registers only `C`.
- A vote reveals `(N, choice)`. Without the secret, `N` is unlinkable to
  any `C` — the voter hides in the full member set.
- The same member voting twice on the same topic reproduces `N` exactly,
  so double votes are rejected mechanically.
- The member set **freezes at the first vote** (ztensor's ring-freeze
  rule), fixing the root `R` the ceremony is accounted against.
- A quorum is **reached** when YES votes ≥ threshold.

## Ceremony

```sh
# organiser
m zquorum/create name=launch threshold=2 desc='ship it?'

# each member, CLIENT-SIDE (the secret never travels):
python3 mod.py commit secret='my passphrase'     # -> C
m zquorum/join name=launch commitment=<C>

# voting (freezes the member set on the first vote)
m zquorum/vote name=launch secret='my passphrase' choice=yes

# anyone
m zquorum/tally name=launch    # {yes, no, threshold, reached, root} — never who
```

Secrets may be any felt (decimal/0x-hex) or an arbitrary passphrase
(hashed `sha256 % PRIME` client-side, so the same passphrase works from
any client).

## Trust model (v0.1) and the proof upgrade (v0.2)

v0.1 verifies a vote by **executing** the circuit (`scarb cairo-run`):
the anchor is a *trusted tallier* that sees a secret transiently, uses it
to check membership and derive the nullifier, and persists only
`(nullifier, choice)`. The ledger and all other members learn nothing
about who voted.

The circuit's vote statement is already prover-shaped — private input:
the secret; public outputs: `(root, nullifier, ok)`. v0.2 replaces
execution with a **STARK proof** of the same program, so votes verify
without any party ever seeing a secret, and `orbit/cairo` can host the
on-chain verifier contract. Until then: don't reuse a ceremony secret as
anything valuable.

## Layout

```
mod.py             stdlib anchor: ledger + scarb orchestration (port 51300, route=false)
circuit/           Cairo 2.8.4 package `zq` — THE protocol (hashes, vote check, CLI main)
data/quorums.json  runtime ledger (gitignored); secrets are never stored
```

- Circuit ground truth: `cd circuit && scarb cairo-test` (6 tests) or
  `m zquorum/test`.
- Circuit CLI: `scarb cairo-run --available-gas 800000000 '[[4, <secret>, <topic>, <leaf>...]]'`
  returns `(root, nullifier, ok)`.
- Toolchain: scarb 2.8.4 from `~/.local/bin` (shared with `orbit/cairo`;
  Scarb 2.8 needs `cairo_test = "2.8.4"` in dev-dependencies for `#[test]`).
