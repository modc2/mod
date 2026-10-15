# zQuorum — anonymous quorums in Cairo

A group reaches a quorum **without revealing who voted**. The ledger is
public books — tallies, nullifiers, the member set — but no vote is
linkable to a member. Private ballots, public outcomes (the same stance as
`orbit/ztensor`, which does this with LSAG ring signatures; zquorum is the
Cairo-native sibling).

## How it works

Commit/nullifier scheme (Semaphore-style), Pedersen over the STARK field.
**Every hash lives in one Cairo program** — `src/circuit/` (package `zq`) — so
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
python3 src/mod.py commit secret='my passphrase'     # -> C
m zquorum/join name=launch commitment=<C>

# voting (freezes the member set on the first vote)
m zquorum/vote name=launch secret='my passphrase' choice=yes

# anyone
m zquorum/tally name=launch    # {yes, no, threshold, reached, root} — never who
```

Secrets may be any felt (decimal/0x-hex) or an arbitrary passphrase
(hashed `sha256 % PRIME` client-side, so the same passphrase works from
any client).

## On Starknet — the `ZQuorum` contract

`src/contract/` (Scarb package `zquorum`, path-depends on `circuit/` so it
hashes with the very same `zq` functions) keeps a quorum's books on-chain:

| on-chain | enforced by the contract |
|----------|--------------------------|
| member commitments | no duplicates, max 64, none after the freeze |
| frozen root | computed at the first vote as `zq::root` over the **sorted** set — byte-identical to the anchor's |
| spent nullifiers | one vote per nullifier per topic; never more votes than members |
| yes/no tally | `reached` = yes ≥ threshold, `Reached` event when it trips |

The anchor is the contract's **tallier**: it checks membership by running
the circuit (as in v0.1) and posts only `(nullifier, choice)`. Every
transaction comes from the tallier's account, so the sender reveals nothing
about who voted. Anyone can read the books and diff them against the ledger.

```sh
m zquorum/create name=launch threshold=2 network=devnet   # deploys on demand
m zquorum/join name=launch commitment=<C>                 # mirrored on-chain
m zquorum/vote name=launch secret=... choice=yes          # mirrored on-chain
m zquorum/onchain name=launch        # contract tally vs ledger -> match: true
m zquorum/anchor name=old network=devnet   # move an existing quorum on-chain (replays it)
m zquorum/chain                      # devnet + deployment + quorums on-chain
m zquorum/contract_test              # snforge, 8 tests
```

- **devnet**: zquorum's own `starknet-devnet` on `:51302` (seed 0, account 0
  = tallier). It starts on demand and dumps its state to `data/devnet/`
  on every block, so the contract and its books survive restarts.
- **sepolia / mainnet**: `m zquorum/deploy network=sepolia account=<sncast
  account>` (fund it with STRK, then `sncast account deploy`). After that,
  `create ... network=sepolia` or `anchor` works the same. Each join or vote
  is one invoke paid by the tallier. Not exercised yet: it needs a funded
  account.
- `deploy`, `devnet` and anchoring on a paid network are **loopback-only** on
  the HTTP API. The public route can read, join and vote, but it cannot
  spend.
- The starknet module's MODS hub (`/starknet/mods`) shows zquorum's
  contract, devnet status and how many quorums are on-chain.

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

## App & API (mod-protocol shape)

zQuorum carries the canonical mod-protocol web layout — an `app/` console and
an API — the same shape as its sibling `orbit/ztensor`, kept Python-stdlib:

- **app/** — a Next.js **static export** (`output:'export'`, `basePath:/zquorum`).
  `build.sh` writes `out/`, publishes it to `releases/<ts>` and flips the
  `dist` symlink atomically (no restart).
- **server.py** — the API server. One stdlib process on `:51300` serves the
  static console **and** the JSON API; it loads `mod.py` by path and maps URLs
  onto its methods, reimplementing no cryptography.
- **route: true** links the module onto the gateway router. Reachable at:

  | URL | serves |
  |-----|--------|
  | `{host}/zquorum` | the console |
  | `{host}/zquorum/api/<fn>` | the protocol API (prefix stripped) — canonical |
  | `{host}/zquorum/_api/<fn>` | the app's own self-calls (prefix kept) |
  | `{host}/api/zquorum/<fn>` | permanent legacy alias |

```sh
bash src/build.sh    # build + publish the console
m zquorum/serve      # run src/server.py (app + API) on :51300
```

## Runs by itself

`src/server.py` calls `boot()` in a background thread every time it starts:
compile the circuit if stale, start the devnet on `:51302`, make sure ZQuorum
is deployed there, and build the console if `dist/` is missing. The port
answers immediately; `GET /zquorum/api/boot` shows each step. The pm2 process
`zquorum` runs `src/server.py` with autorestart and is saved in the pm2 dump
(restored by `pm2-root` on boot), so it comes back after a crash or a reboot.

## Layout

```
config.json        module manifest (anchor = src/mod.py, server = src/server.py)
data/              quorums.json ledger, deployments.json, devnet/ (gitignored); secrets never stored
src/mod.py         stdlib anchor: ledger + scarb orchestration + boot() + CLI (port 51300)
src/server.py      stdlib HTTP server: static console (dist/) + JSON API; runs boot() on start
src/app/           Next.js console (static export) — create/join/vote/tally UI
src/build.sh       next export -> releases/<ts> -> atomic dist symlink
src/dist           published console (gitignored symlink)
src/circuit/       Cairo 2.8.4 package `zq` — THE protocol (hashes, vote check, CLI main)
src/contract/      Starknet package `zquorum` — ZQuorum contract (books on-chain) + snforge tests
src/tests/         pytest: ledger + full on-chain ceremony on the devnet
```

- Circuit ground truth: `cd src/circuit && scarb cairo-test` (6 tests) or
  `m zquorum/test`.
- Circuit CLI: `scarb cairo-run --available-gas 800000000 '[[4, <secret>, <topic>, <leaf>...]]'`
  returns `(root, nullifier, ok)`.
- Toolchain: scarb 2.8.4 from `~/.local/bin` (shared with `orbit/cairo`;
  Scarb 2.8 needs `cairo_test = "2.8.4"` in dev-dependencies for `#[test]`).
