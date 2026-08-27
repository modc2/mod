# prerank

A prediction market on which model is on top, settled once a day — and a way
of paying people for having used a good model before it was obvious.

Two things happen in a round:

**You can bet.** Back a model to finish first. Your stake is public and locked
the moment you commit, but *which* model you backed is a hash until the reveal
window — unreadable by other bettors and by this server alike. So the odds you
take are the odds of being early, not the odds of having waited to see where
everyone else went. The winners divide the pool.

**You can just use the models.** Every metered call carries a spend and a
cost, and the difference is the house's margin on you. That margin is handed
back as a claim on the model you used, weighted by how early the call was: the
first credits through a model buy a full unit of claim, and by the time it has
absorbed K credits the same margin buys half as much. Nobody has to opt in and
nobody has to be clever. If you were early to something good, you are holding
it when the round settles.

The second is the part worth arguing about, so here is the argument for why
it is safe: you are handed back the *margin*, never the spend. Buying usage to
farm a position always costs strictly more than the position it builds. The
rebate is a rebate, not a faucet.

```
m prerank                          what this is
m prerank/round                    the round that is taking bets
m prerank/bet model=opus amount=5  seal a bet — the hash is computed on your machine
m prerank/reveal                   open your sealed bets
m prerank/models                   the earliness curve, per model
m prerank/verify                   replay the whole log and check the server against it
m prerank/serve                    API :50630 + console :50631
```

## The day

Round ids are UTC dates. One round per day, four phases, all of them pure
functions of the clock — a client can compute the current phase without asking
the server, and does.

| phase | when (UTC) | what happens |
|---|---|---|
| `open` | 00:00 – 18:00 | commitments. Amounts public and locked; models hidden. |
| `reveal` | 18:00 – 23:00 | commitments are opened. No new money enters the round. |
| `sealed` | 23:00 – 00:00 | pools frozen and published; graders rank; the round token trades. |
| `settled` | 00:00 | quorum counted once, payouts made. |

`PRERANK_DAY_SECONDS` compresses all of that, which is how the tests watch a
whole day go by in half a minute and how you can demonstrate one in ten
minutes. Nothing else changes: the boundaries are computed from the schedule
either way.

## Betting

A bet is a hash:

```
commitment = sha256("prerank:bet" | round | address | model | amount | salt)
```

computed on your machine — by `mod.py` for the CLI, by the browser for the
console. **There is deliberately no endpoint that will do this for you.** A
market that hides your bet from other bettors but not from the server it runs
on is not a sealed market, and an endpoint taking `model` and `salt` would be
exactly that.

The salt lives in `~/.mod/prerank/bets.json` (CLI) or `localStorage` (console).
Lose it before the reveal window and the stake forfeits to the pool. That is
not an oversight to be smoothed over — it is the cost of the server being
unable to open your bet, and forfeiting is what makes withholding a reveal a
bad idea rather than a free option on the round.

Payouts are parimutuel and integer-only:

```
fee           = pool × fee_bps / 10000
distributable = pool − fee
payout(you)   = distributable × your_units / winning_units      (floor)
dust          = distributable − Σ payouts                       (to the treasury)
```

`Σ payouts + fee + dust == pool`, exactly, on every path. There is no floating
point anywhere on the money path; everything is `u128` micro-credits.

Three outcomes:

- **paid** — a quorum agreed and somebody held the winner.
- **no winner** — a quorum agreed but nobody backed the winning model. Stakes
  returned, no fee: a market that failed to form is not a market to tax.
- **void** — no quorum, or two contradicting quorums. Everything refunded,
  including forfeits.

## The early-user edge

A meter posts a signed receipt for a metered call:

```json
{"id": "...", "user": "0x…", "model": "opus", "spend": 12000000,
 "cost": 7000000, "at": 1755109000, "meter": "0x…", "signature": "0x…"}
```

`margin = spend − cost`, and the claim it buys is

```
units = margin × K / (K + c)
```

where `c` is how many credits that model had already absorbed at the moment of
the call. `c` is a usage clock, not a wall clock — being early means being
early to the *model*, not to the calendar. The weight is computed once, when
the receipt is posted, and frozen: being early is a fact about when you paid.

Live numbers from a seeded instance, same 5-credit margin on `opus` three
times:

| when | opus had taken | units for 5.00 of margin |
|---|---|---|
| first call | 0 | 5.00 |
| after 12 credits | 12.00 | 4.46 |
| after 212 credits | 212.00 | 1.60 |

Credit earned during round N lands in round N+1, never in the round that was
already taking bets — otherwise you could watch a day's rank take shape and
then buy in at the close with the house's money. It is capped per user per
model per round, so a whale's spend cannot own a round outright, and it is
funded out of the treasury, so it is never minted from nothing.

## The round token

Each model in each round has a token — `PRE-OPUS-2026-08-13`. Opening a bet
mints it; so does an edge position landing. It transfers only between the seal
and the settlement (moving it earlier would announce which model you are on,
which is what your commitment is hiding), and at settlement it is redeemed for
the payout or it expires worthless.

## Why it cannot be cheated

The server keeps no authoritative state. It keeps a hash-linked log, and
everything you can read is the fold of that log:

```
hash(n) = sha256(n | hash(n−1) | canonical_json(event))
```

Change event 40 of 900 and events 41 through 900 change too, so a head hash
published yesterday is a commitment to everything that preceded it. `GET
/verify` replays the log from genesis, rebuilds every balance, pool and payout
from scratch, and compares that against what the running server is serving. A
disagreement is reported, not smoothed over.

On top of that:

- **Sealed bids.** Amounts public and locked, models hashed until the reveal.
- **Forfeit on silence.** An unopened commitment loses its stake to the pool.
- **Merkle roots.** Every sealed round publishes a root over its commitments;
  `GET /proof/:round/:commitment` returns a sibling path, and the console
  walks it in your tab against the published root.
- **A committed spec.** The field and every payout parameter are hashed into
  `spec_hash` when the round opens and never read again — changing the roster
  mid-round cannot touch a round in flight.
- **Quorum grading.** A ranking needs N independent registered graders to
  agree. No quorum voids; *two* quorums voids. Quorum is counted once, when
  grading closes, so a late contradicting grader can never be outrun by an
  early settlement.
- **Sealed rankings.** A grader's ranking is public as a hash until the round
  settles, so the second grader cannot copy the first.
- **Conflict of interest.** A grader holding a position in the round it grades
  is recorded — the conflict stays on the log — and not counted.
- **Single-use nonces.** Every signed action carries one, so a captured
  request cannot be replayed.
- **Signed metering.** Usage only counts from a registered meter's signature,
  receipt ids are single-use, and `cost > spend` is refused.
- **Conservation.** `issued == balances + locked + open pools + treasury`,
  asserted after every scenario in the test suite and exposed at `/verify`.

Every one of those is a test in `src/api/tests/cheatproof.rs`, written as an
attempt to break it rather than as a demonstration that it works.

## Layout

```
mod.py                  the CLI and inter-mod surface; hashes and signs locally
config.json
src/api/                Rust — axum, the chain, the rules
  src/crypto.rs         sha256, EIP-191 recovery, Merkle trees
  src/types.rs          money, rounds, and the event enum everything is written in
  src/chain.rs          the append-only hash-linked log
  src/state.rs          the fold: events in, balances and pools out
  src/market.rs         the payout math, as pure functions
  src/engine.rs         every rule, once, as a refusal to write an event
  src/routes.rs         HTTP, and nothing more than that
  tests/market.rs       a day from open to payout, and the arithmetic
  tests/cheatproof.rs   one test per way of cheating
src/app/                Next.js 14 console at /prerank
tests/test_prerank.py   the module surface against a live server
```

The engine is the only writer, and validation lives there and nowhere else.
The HTTP layer parses and calls one method; the CLI goes through HTTP; the
tests call the same methods the routes do. A rule that is not in `engine.rs`
is not enforced.

## Identity

EIP-191 `personal_sign` over secp256k1, so a browser wallet and the CLI are
the same kind of caller. `mod.py` signs with this box's own protocol key —
already a secp256k1 Ethereum key — rather than inventing a second identity.

`PRERANK_OPEN=1` accepts unsigned actions and enables eight deterministic
development wallets for the console. It is local-only, and `/health` and the
INFO tab both say so out loud when it is on.

## Running it

```
m prerank/build                    cargo build --release + next build
m prerank/serve                    both halves
m prerank/serve open_mode=true     … with unsigned actions, for a local demo
m prerank/test                     cargo test (37) + pytest (12)
```

A market needs three things before it can settle a round: a field of at least
two models, a treasury with something in it, and a quorum of graders.

```
m prerank/roster models=opus,sonnet,haiku
m prerank/grant account=treasury amount=5000
m prerank/grader address=0x…            # twice — the default quorum is two
m prerank/meter address=0x…             # whoever is allowed to post usage
```

## Roles

| role | can |
|---|---|
| owner | set the field, issue credits, appoint graders and meters |
| grader | rank a sealed round; a quorum must agree |
| meter | post signed usage receipts — the only way credits enter the earliness curve |
| anyone | bet, reveal, hold and transfer round tokens, audit the whole thing |
