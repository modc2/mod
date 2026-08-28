# wasmland

A marketplace for computations anyone can check.

Upload something that computes. Run it in your own browser tab, or on the box.
Get a receipt. Have somebody else run it again and see whether they agree.

That last step is the product. Everything else here exists to make it possible.

```bash
m wasmland/serve                                     # API + console on :50480
m wasmland/publish path=examples/montecarlo.js title="Monte Carlo pi"
m wasmland/run listing=monte-carlo-pi-a1b2 input=200000 seed=1
m wasmland/verify <run id>                           # → verified, or disputed
```

## Why a receipt means something

A run is a claim: *this artifact, on this input, with this seed, produced these
bytes*. On its own it is worth nothing — it may have come from a tab on a
machine nobody controls. It becomes worth something the ordinary way: somebody
else does it again and gets the same answer.

For that to be possible at all, two runs of the same computation have to be the
same computation. So the host removes everything a module could use to tell two
identical runs apart:

| | |
| --- | --- |
| clock | a fixed epoch plus a counter — `now()` advances one tick per call |
| randomness | one seeded PRNG, feeding `random()` and WASI `random_get` alike |
| network | not imported; on the server the process has an empty netns |
| filesystem | no preopens, so WASI `path_open` has nothing to open |

What is left is the artifact, the input and the seed. `examples/montecarlo.js`
is the demonstration: a Monte Carlo estimate of π, built out of randomness and
a clock, reproducing bit for bit on replay.

The browser and the server run **the same files** — `src/runtime/` is imported
by the tab's Worker and by the node runner off disk. That is not tidiness; it
is the reason a browser claim and a server replay can be compared at all.

### The verdicts

A receipt is the SHA-256 of what must match: artifact, engine, entry, input
hash, seed, output hash, exit code, effect counts. Wall-clock time, venue and
who ran it are recorded but never hashed — a slower replay is the same
computation; one that used the clock a different number of times is not.

| status | what it means |
| --- | --- |
| `unverified` | nobody has attested yet |
| `claimed` | one party says so — including, always, a fresh browser run |
| `verified` | two independent parties ran it and got the same receipt |
| `disputed` | they got different ones. Someone is wrong, or it isn't deterministic |

A verifier re-attesting replaces its own earlier word rather than stacking a
second vote on one opinion. The replay reads the job out of the stored run, so
a claimant cannot smuggle a different computation into its own verification.

## Compute types are a plug-in

An engine answers four questions — what an artifact is, where it can run, how a
run is made repeatable, and how it is verified. Nothing above `src/engines.py`
knows what WebAssembly is.

| type | status | venues | verified by |
| --- | --- | --- | --- |
| `wasm` | live | browser, server | replay |
| `js` | live | browser, server | replay |
| `python` | planned | browser, server | replay |
| `container` | planned | server | consensus |
| `tee` | planned | server | attestation |
| `gpu` | planned | server | consensus |

Planned types are declared, not stubbed out of politeness: they are listed,
queryable, carry the verification each would actually need, and refuse to run
with a message naming what is missing. A TEE is verified by hardware evidence
because nobody — including this box — can replay it; a GPU kernel is verified
by independent runs agreeing within a tolerance, because bitwise equality is
the wrong test for floating-point reduction order. Getting those distinctions
into the type system before the code exists is most of the work.

## Where a run happens

**Browser.** A module Worker the page can terminate — the only real timeout
there is, since wasm cannot be interrupted from outside. The host offers no
sockets and no filesystem. Results are claims.

**Server.** `unshare -n` → `setpriv` → `node`, in that order: the namespace
call needs privileges that the privilege drop then spends, and doing it the
other way round fails with `Operation not permitted`. Plus `RLIMIT_CPU`,
`RLIMIT_FSIZE 0`, a heap cap and a wall-clock kill.

`GET /venues` reports what is actually in force rather than what the code
hopes for — including saying plainly when there is no `CAP_SYS_ADMIN` and the
network namespace isn't there.

> One honest note about `RLIMIT_AS`: it is set to 16 GB, which is not a typo.
> V8 *reserves* about 10 GB of guard region per wasm memory without touching
> it, and any limit a human would call a memory limit makes every wasm module
> on the box fail to instantiate. The heap cap and the clock are the real
> limits; see `DEFAULT_LIMITS` in `src/sandbox.py`.

## Everything lives in the store mod

There is no private database. Artifacts, listings, runs, receipts and the
credit ledger are keys in `m.mod('store')`:

```
blobs/<sha256>.json                  bytes, addressed by their own hash
wasmland/artifacts/<sha256>.json     what reading those bytes says they are
wasmland/listings/<id>.json          one thing for sale
wasmland/runs/<id>.json              one execution and its receipt
wasmland/ledger/<address>.json       credits and earnings
```

`blobs/` sits outside the wasmland prefix on purpose: bytes under their own
hash belong to nobody, so the arena reads the same key for a game published
here rather than being handed a second copy. Where localfs is reachable the
bytes are pinned there too and the CID is recorded alongside.

## Prices, precisely

A paid listing charges per buyer, in credits, and the seller is credited the
same amount. Being exact about what that can enforce:

* a **server** run is metered — the box does the work, so it charges for it;
* a **browser** run needs the bytes, so buying gives you the bytes, and after
  that the tab can run them all day without asking again.

So a price buys access to an artifact plus this box's willingness to run it,
not a meter on your own CPU. The console says so where it takes the money.
Credits are an internal unit: an owner grants them, and after that they only
move between accounts. Nothing here mints them on a user's say-so, and making
them redeemable against BlocTime stake or a chain balance is a bridge this
module has not crossed.

## Games go to the arena

A module whose exports implement the game ABI *is* a game — read from the
binary, not from what the uploader typed. Publishing one to the arena mints it
as its own mod:

```bash
m wasmland/to_arena listing=nim-c3d4
m nim                     # a module in the fleet: card, ABI, bytes, play
m arena/play game=nim players=rando,rando2
```

The minted directory (`orbit/<slug>/`) is a pointer, never a copy: a
`config.json` in the arena schema and a short `mod.py` that fetches its own
bytes from the store. Publishing a game does not commit somebody's binary to
this repository, and deleting the directory does not lose the game.

The arena stays its own mod. wasmland stores, prices, runs and verifies; the
arena seats players, rates them and keeps the board. It re-checks the ABI from
the bytes rather than taking wasmland's word for it.

## Identity

Two doors, one answer — an address. A mod-protocol token (what every other mod
in this fleet already speaks), or a wallet signature over a challenge. The
address *is* the account: no password, no session table, nothing to create.
`WASMLAND_OPEN=1` skips the gate for local development and `GET /auth/me` says
which mode is on, because a module that quietly runs open is a module that will
one day be open in production.

## Layout

```
mod.py                 the CLI face
src/engines.py         compute types — the plug-in point, and a wasm reader
src/sandbox.py         the server venue: namespaces, privileges, limits
src/storage.py         everything, in the store mod
src/receipts.py        receipts, attestations, verdicts
src/market.py          listings, credits, entitlements
src/games.py           the arena bridge
src/identity.py        tokens, wallet signatures, sessions
src/runtime/*.mjs      the execution layer — one implementation, two venues
src/api/api.py         HTTP, and the console served from the same port
src/app/               the console (no framework, no build step)
examples/              two js artifacts; wasm ones live in the arena's pack
tests/                 24 tests, most of them about what must be refused
```

```bash
m wasmland/test
```
