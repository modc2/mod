# zuma — zk Yuma consensus

`zkworld.zuma`, forked from [`zkworld.subnet`](../subnet/) (sealed Yuma). It adds the
ring signatures from [`orbit/zuma`](../../../../zuma/) so that **miners and
validators know they are in the set, but nobody knows who is who.**

Collusion in a subnet needs one party to recognize the other. Zuma removes that:

| who | proves | learns nothing about |
|---|---|---|
| miner | "I'm one of the miners" (ring sig, key image = this round's **tag**) | which validators scored it |
| validator | "I'm one of the validators of stake S" (ring sig, key image = this round's **nullifier**) | which miner is behind a tag |

Tags and nullifiers are new every round, so a member can't be followed from one
round to the next. Weights are sealed until every validator has locked in, so
nobody can copy anyone else's weights.

## A round

| stage | who | what |
|---|---|---|
| 0 SUBMIT | miner | `submit(ring, work, sig)`: one per miner, scope `H('zuma-mine', contract, epoch)` |
| 1 COMMIT | validator | `commit(ring, seal, sig)`: one per validator, scope `H('zuma-vote', contract, epoch)`, seal = `H('zuma', contract, epoch, nullifier, salt, w..)` |
| 2 REVEAL | anyone holding salt | `reveal(nullifier, weights, salt)` |
| 3 FINALIZE | anyone | `finalize()` runs Yuma, then the round rolls |
| after | member | `claim_miner` / `claim_validator(epoch, ring, to, sig)`: re-sign the same scope (same key image), credit any address |

Each stage ends when its window closes, or early once every member is in.

Yuma uses fixed point with ONE = 1e9, exactly as in `subnet`: a stake-weighted
median (kappa ½), clip, incentive, bonds, dividends, and a 50/50 emission split.
Two deliberate differences:

- **Stake tiers.** A ring has to be one stake tier (every member has equal
  stake), and the vote weighs that stake. Stake is public; the voter could be
  anyone in the tier.
- **Bonds per round, no EMA.** An EMA needs a single identity across rounds,
  which is exactly the link zuma refuses to make.

Credits are paper points on public books; no token moves. This is on purpose:
paying out to arbitrary addresses would turn it into a mixer.

## Use it (sandbox devnet)

```sh
m zkworld.zuma/build && m zkworld.zuma/deploy args='{"owner":"alice","min_ring":3,"submit_secs":600,"commit_secs":600,"reveal_secs":600,"emission":1000}'
m zkworld.zuma/keygen name=m1 role=miner             # secrets stay in data/keys.json (0600)
m zkworld.zuma/keygen name=v1 role=validator stake=10
m zkworld.zuma/register name=m1                      # owner account adds the public key
m zkworld.zuma/submit name=m1 work=answer-hash       # posted by a relayer account (bob)
m zkworld.zuma/commit name=v1 weights=4,3,2,1        # one weight per tag, in tags(epoch) order
m zkworld.zuma/reveal name=v1
m zkworld.zuma/finalize
m zkworld.zuma/claim name=m1 epoch=0 to=0x...
```

`prover/` is a Cairo program run with `scarb cairo-run`. It links the same
`zuma::lsag`, `mine_scope_of`, `vote_scope_of` and `seal_of` that the contract
uses, so the signer and the verifier can't drift apart. Those fns live in
`ext.py`, which the template `mod.py` mixes in, so `adopt()` can't overwrite them.

## Limits, honestly

- You are only as anonymous as your ring. A ring of 3 hides you among 3, so
  `min_ring` sets the floor.
- The tx sender is public, so post through a relayer, never from an account
  tied to you. No call checks the caller.
- A miner can still tell a validator its tag off-chain, or watermark its work.
  Zuma makes collusion **unverifiable on-chain**; it can't stop people from talking.
- Limits: 32 tags and 16 voters per round, and rings of up to 32 members.

Tests: `snforge test`, 13/13 (Yuma clipping, claims, unlinkability across
rounds, double submit/vote/claim, ring rules, stake tiers, windows).
