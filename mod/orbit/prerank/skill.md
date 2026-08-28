# prerank — skill

A daily market on which model finishes first. Reach for this module when
something needs a **rank priced before it is known** — which model is best,
which agent wins, which of N things a crowd is willing to back — or when early
users of something good should end up holding it without having to opt in.

## The one idea

Two doors into the same round. You can **bet**, and your bet is a hash until
the reveal, so the odds you take are the odds of being early rather than the
odds of having waited to see where the money went. Or you can just **use the
models**: the house's margin on every metered call is handed back as a claim
on the model you used, weighted by how early the call was. Using a good model
early *is* the position.

You are handed back the margin, never the spend — so farming usage to build a
position always costs more than the position it builds. That is the whole
reason it is safe to give away.

## Place a bet

```bash
m prerank/round                            # what is taking bets, and until when
m prerank/bet model=opus amount=5          # sealed — the hash is computed on your box
m prerank/bets                             # the salts this box is holding
m prerank/reveal                           # open them, once the reveal window is up
```

The commitment is `sha256("prerank:bet"|round|address|model|amount|salt)`,
computed by `mod.py` and by the browser. There is no endpoint that will do it
for you, on purpose: a market that hides your bet from other bettors but not
from its own server is not sealed. The salt goes to
`~/.mod/prerank/bets.json` — lose it before the reveal and the stake forfeits
to the pool.

## Bank usage as a position

Only a registered meter can do this, and it is the only way credits enter the
earliness curve.

```bash
m prerank/meter address=0x…                             # owner registers the meter
m prerank/usage user=0x… model=opus spend=12 cost=7     # margin 5 → a weighted claim
m prerank/models                                        # what a credit of margin buys now
```

`units = margin × K/(K+c)`, where `c` is how many credits that model has
already absorbed. The credit lands in the *next* round, never the one already
taking bets.

## Grade a round

```bash
m prerank/attest ranking=opus,sonnet,haiku
```

Sealed rounds only, from a registered grader. A quorum has to agree; no quorum
or two contradicting quorums voids the round and refunds everyone. A grader
holding a position in the round it is grading is recorded and not counted.

## Audit it without trusting it

```bash
m prerank/verify              # replay the log from genesis, check the server against it
m prerank/proof               # Merkle inclusion for this box's last bet
m prerank/chain start=0 limit=50
```

`verify` is the load-bearing one: the state is nothing but the fold of a
hash-linked log, so it can be rebuilt from scratch and compared. It also
checks that credits are conserved — `issued == balances + locked + open pools
+ treasury`.

## Stand one up

```bash
m prerank/serve open_mode=true            # local demo, unsigned actions allowed
m prerank/roster models=opus,sonnet,haiku
m prerank/grant account=treasury amount=5000
m prerank/grant account=0x… amount=100
m prerank/grader address=0x…              # twice — the default quorum is two
```

A round needs two models in the field, a funded treasury (it pays for the
early-user positions), and a quorum of graders. Short of any of those, rounds
open and then void.

`PRERANK_DAY_SECONDS=600` runs a full day in ten minutes — the phases are
computed from the schedule, so nothing else changes.

## Gotchas

- **A round in flight ignores the roster.** The field and the payout
  parameters are hashed into `spec_hash` at open. Roster changes land next
  round.
- **Reveals are unauthenticated on purpose.** Knowing the salt is the proof,
  and the units go to whoever committed, not whoever opened it.
- **Tokens do not trade before the seal.** An earlier transfer would announce
  which model the sender is on.
- **Nonces are single-use per address for the life of the chain**, not per
  round. `GET /account/:addr` hands back the next unused one.
- **Fewer graders than the quorum means every round voids.** That is the
  intended failure, not a bug — but check it before wondering where the
  payouts went.
