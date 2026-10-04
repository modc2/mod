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

The core primitive is a **Linkable Spontaneous Anonymous Group signature
(LSAG)** — the Liu-Wei-Wong construction over a prime-order subgroup of an
RFC 3526 safe-prime group. No trusted setup. It exists in three byte-for-byte
compatible implementations, cross-checked against each other in CI-able tests:

| Implementation      | Role                                                |
| ------------------- | --------------------------------------------------- |
| `ring.py`           | reference (pure python, stdlib only)                |
| `api/src/lsag.rs`   | the verifier the Rust service runs                  |
| `web/lib/lsag.mjs`  | the browser signer (keygen + sign, WebCrypto BigInt)|

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

Every API route is mounted at `/`, `/api/*`, `/_api/*`, `/ztensor/api/*` and
`/ztensor/_api/*` (the gateway strips `/ztensor/api` on the api route and keeps
the prefix on the app route; the console calls `/ztensor/_api/*`).

| Method | Route       | Does                                                          |
| ------ | ----------- | ------------------------------------------------------------- |
| GET    | `/health`   | liveness                                                       |
| GET    | `/info`     | scheme + guarantees                                            |
| POST   | `/register` | `{pub}` → add a public key to the eligible set                |
| GET    | `/set`      | current eligible set                                           |
| GET    | `/ring`     | `?topic=` → the ring to sign against (frozen at first vote)   |
| POST   | `/vote`     | `{topic, choice, sig}` → record an anonymous vote             |
| GET    | `/tally`    | `?topic=` → public vote counts                                |
| GET    | `/payout`   | `?topic=&pool=` → transparent reward split by vote share      |
| GET    | `/topics`   | all topics with ring size + vote count                         |
| GET    | `/status`   | participants + topic names                                     |

`sig` fields (`c0`, `s[]`, `tag`) are accepted as decimal strings, `0x`-hex
strings, or raw JSON integers (what `json.dumps` of `ring.sign(...)` emits).

## CLI

```sh
m ztensor/demo                        # end-to-end anonymous vote, in-process
m ztensor/test                        # self-check: sign/verify + double-vote + outsider
m ztensor/register pub=<hex>          # add a public key
m ztensor/set
m ztensor/tally topic=reward-epoch-1
m ztensor/payout topic=reward-epoch-1 pool=1000
m ztensor/build                       # next build -> dist (atomic) + cargo release
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
  config.json        # port 51180, endpoints, fns
  mod.py             # Mod: register/set/vote/tally/payout/test/build/serve
  ring.py            # LSAG reference implementation (pure python)
  api/               # Rust service (axum): JSON API + serves the console
    src/lsag.rs      #   LSAG verifier (+ sign for self-check endpoints)
    src/main.rs      #   routes, state, static dist serving
    tests/           #   python-signed fixture for cross-implementation tests
  web/               # Next.js console (output: 'export', basePath /ztensor)
    lib/lsag.mjs     #   browser keygen + signer — secrets never leave the client
    scripts/crosstest.mjs  # JS<->python wire-compatibility check
  dist -> releases/<ts>   # published console, atomic symlink swap (build.sh)
  server.py          # pure-python fallback server (used when api/ not built)
  app/index.html     # legacy single-file console (fallback when dist missing)
  state/             # local state (public keys, tags, choices) — git-ignored
```

One process, one port: the Rust binary serves the API and the static console;
no node process at runtime. Verify the three LSAG implementations agree with
`cd api && cargo test` (verifies a python-signed fixture) and
`cd web && node scripts/crosstest.mjs` (JS signs, python verifies).
