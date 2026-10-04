# ztensor

**Anonymous voting & consensus for miner/validator networks.**

Miners and validators participate in consensus **without being able to identify
each other**. A member proves they belong to the eligible set and casts a vote,
but the tally can never map a vote back to a specific participant. This kills the
things that make open validator sets fragile: collusion, vote-buying, and
targeted retaliation against whoever voted "wrong".

## Design stance: private ballots, public books

Two properties pull in opposite directions, and ztensor keeps them separate on
purpose:

| Layer            | Property        | Why                                              |
| ---------------- | --------------- | ------------------------------------------------ |
| **Votes**        | unlinkable      | no one learns which validator cast which ballot  |
| **Reward payouts** | transparent   | anyone can audit that the pool was split honestly |

The anonymity lives on the *ballot*. The *money* stays fully auditable: payout
instructions name the recipient, the vote count, and the share, so the books can
be checked against the public tally. ztensor does **not** obfuscate fund flows,
rotate funds through intermediary keys, or mix provenance — that is a different
(and dangerous) kind of system, and deliberately out of scope.

## How the anonymity works

The core primitive (`ring.py`) is a **Linkable Spontaneous Anonymous Group
signature (LSAG)** — the Liu-Wei-Wong construction over a prime-order subgroup of
an RFC 3526 safe-prime group. Pure python, stdlib only, no trusted setup.

- **Anonymity** — a signature proves *some* member of the ring signed it, never which.
- **Double-vote resistance** — each signature carries a per-topic *key image* (tag);
  the same member signing the same topic twice yields the same tag and is rejected.
- **Unlinkable across topics** — the tag is derived from `(ring, topic)`, so votes
  on different topics can't be correlated to the same (unknown) member.
- **Soundness** — only a true member of the eligible set can produce a valid signature.

> A production deployment can swap LSAG for a SNARK-backed membership circuit
> (Semaphore-style) behind the same API; the trust properties above are the
> contract.

### Keys never touch the server

`register` stores a **public** key only. Signing happens client-side with the
member's secret key, which never leaves their machine. The service stores public
keys, vote choices, and tags — nothing that can deanonymize a voter or that is
worth stealing.

## Endpoints

| Method | Route               | Does                                                        |
| ------ | ------------------- | ---------------------------------------------------------- |
| GET    | `/ztensor/health`   | liveness                                                    |
| GET    | `/ztensor/info`     | scheme + guarantees                                         |
| POST   | `/ztensor/register` | `{pub}` → add a public key to the eligible set             |
| GET    | `/ztensor/set`      | current eligible set                                        |
| POST   | `/ztensor/vote`     | `{topic, choice, sig}` → record an anonymous vote          |
| GET    | `/ztensor/tally`    | `?topic=` → public vote counts                             |
| GET    | `/ztensor/payout`   | `?topic=&pool=` → transparent reward split by vote share   |

## CLI

```sh
m ztensor/demo                        # end-to-end anonymous vote, in-process
m ztensor/test                        # self-check: sign/verify + double-vote + outsider
m ztensor/register pub=<hex>          # add a public key
m ztensor/set
m ztensor/tally topic=reward-epoch-1
m ztensor/payout topic=reward-epoch-1 pool=1000
m ztensor/serve                       # api + console on :51180, route /ztensor
m ztensor/kill
```

## Client sketch

```python
import ring
secret, pub = ring.keygen()                 # keep `secret` private, register `pub`
# ... everyone registers; `rng` = the eligible set's public keys ...
topic = b"reward-epoch-1"
msg = b"reward-epoch-1|minerA"              # topic|choice
sig = ring.sign(secret, rng, topic, msg)    # POST {topic, choice, sig} to /vote
```

## Layout

```
ztensor/
  config.json     # port 51180, endpoints, fns
  mod.py          # Mod: register/set/vote/tally/payout/test/demo/serve
  ring.py         # LSAG anonymous ring signatures (the crypto core)
  server.py       # stdlib HTTP API + console
  app/index.html  # minimal console
  state/          # local state (public keys, tags, choices) — git-ignored
```

Local-first, zero dependencies.
