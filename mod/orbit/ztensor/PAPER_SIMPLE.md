# ztensor, simply

*Anonymous subnets: everyone follows the rules, nobody knows who did what.*

## The idea in one minute

Think of a talent show. Contestants (**miners**) perform, and judges
(**validators**) score them. In most shows you can see which judge gave which
score and who was on stage. That invites favours: a judge can help a friend,
and a contestant can hold a grudge against the judge who marked them down.

ztensor runs the show behind a curtain. Everyone knows **who is on the list**
of contestants and judges. Nobody knows **which one of them** did a given
thing. The rules still hold: each contestant performs once, each judge scores
each performance once, and the prize money is split fairly at the end.

> ztensor hides **who** you are inside a group. It never hides **what** the
> group did: every submission, score and payout is on the public books.

## The trick: signing for a group

Normally you sign with your own key, and everyone can see it was you. ztensor
uses a **ring signature** instead. You pick the whole group, and the signature
says "one of these people signed this", without saying which one.

::: fig ring | When someone in the ring signs, the whole ring lights up. Looking at the signature, every member is equally likely to be the signer.

There is one more piece, the **stamp**. Every signature leaves a stamp that
depends only on *your key* and *the situation* (this round, this performance).
Sign twice in the same situation and you leave the same stamp twice, so the
contract can say "you already did this". Sign in a different situation and the
stamp looks completely unrelated, so nobody can follow you around.

## One round, step by step

::: fig flow | A round has four steps. The first two are signed for a group, the third is plain arithmetic anyone can run, and the last one only works for whoever made the submission.

1. **Submit.** A miner sends in their work, signed for the miner group. Their
   stamp becomes the submission's **tag**, a nickname that lasts only this
   round. Next round they get a fresh, unrelated nickname.
2. **Score.** Validators score each tag from 0 to 100, signed for the validator
   group. They never learn which miner is behind a tag, and the miner never
   learns which validator scored it.
3. **Settle.** When the round ends, anyone can ask the contract to work out the
   result for each tag (see the next section). It's public arithmetic.
4. **Claim.** The miner signs once more in the same situation as their
   submission. Only the key that made the tag can make that stamp again, so only
   the real owner can collect. A second claim makes the same stamp and is
   refused.

## Fair scores: Zuma consensus

What if a validator is secretly also a miner, and gives their own submission a
perfect 100? ztensor uses **Zuma consensus** so that doesn't work:

- Every validator counts the same.
- For each tag, the contract finds the highest score that **at least half the
  validators** agreed on. That is the **consensus** score.
- Any score above consensus is cut down to it.

::: fig consensus | Three validators said 10, one said 100. Half the group only agrees on 10, so the 100 is cut down to 10. A lone cheater changes nothing.

To move a score you need half the group on your side. Prize money is then shared
in proportion to each tag's (cut-down) total.

## Many chains, one signature

The same signature works on every blockchain ztensor supports. One signer makes
it, and each chain has its own contract that checks it.

::: fig overview | One signer on your machine; a checker contract on each chain. Adding a new chain means adding one adapter.

| chain | biggest group | how it checks |
|---|---|---|
| Starknet | 64 | the curve is built into the chain, so it's cheapest |
| NEAR | 32 | ztensor brings its own fast math |
| Ethereum | 32 | the same math, written for the EVM |
| Solana | 32 | the check is split into small pieces |

At launch, one score with a group of 4 cost roughly **\$0.002 on Starknet**,
\$0.02 on NEAR or Solana, and \$0.46 on Ethereum. The COSTS tab shows live prices.

## Making it cheaper without giving anyone away

Each round, every validator scores every miner, so the bill grows with
miners × validators. A subnet the size of a Bittensor one (256 miners, 64
validators) would cost about \$3 000 a day on Starknet and millions on Ethereum.

The usual shortcuts are a trap. Signing all of a validator's scores at once, or
collecting many rounds of pay in one claim, does save money. But it ties actions
together that ztensor keeps apart, so someone watching can start to recognise a
validator by its pattern of scores.

Two fixes save as much without revealing anything new:

- **Secret committees.** Each miner is scored by about 32 validators instead of
  all of them. Who is on the committee follows from a code each validator already
  publishes, so nobody can pick their seat, nobody knows the committee until it
  votes, and anyone can check it. The price is a small chance that colluders end
  up with a majority on one committee: about 1 in 5 million when a quarter of the
  validators collude.
- **One proof per round.** All the signatures are checked off the chain, and the
  chain receives one short proof that everything was correct. Individual scores
  never have to be published at all.

Together they are modeled to make that subnet 300 to 5 000 times cheaper,
depending on the chain, while hiding every member as well as today. Longer
rounds, and picking the group size you actually need, save money for free.

## What it does not hide

- **Small groups leak.** With 3 validators, a score could have come from 1 of 3.
  Bigger groups mean better hiding. The contract enforces a minimum group size,
  and can't be fooled by listing the same person twice.
- **Your behaviour can give you away.** The account that sends a transaction is
  public, so actions go through a helper account (a **relayer**). Timing, your
  IP address or the content of your work can still identify you.
- **Majorities can still collude.** If more than half the validators gang up,
  they can move a score. That is true of any vote.
- **It is not quantum-proof.** A future quantum computer could unmask past
  signatures. The postquant version protects the record of what happened, but
  not the hiding itself yet.

## Want the maths?

Switch to the **Technical** paper for the full scheme: the ring signature
equations, the consensus formula with a proof, and how the curve maths was made
cheap enough to run on NEAR, Ethereum and Solana.
