# wasmland

A marketplace for verifiable computation. Store an artifact, run it in a
browser or on the server, and settle whether the result is real by having an
independent party replay it.

## When to reach for this

* someone wants to publish a computation others can run and pay for
* a result needs to be checkable by a party who trusts nobody
* something must run untrusted code without letting it reach the network
* a wasm game needs to become a mod that agents can play (→ `arena`)

## The model in four sentences

1. An artifact is bytes, addressed by their SHA-256, and what it *is* gets read
   out of the binary rather than taken from the uploader.
2. A run is `(artifact, input, seed)` — the host seeds the clock and the PRNG
   and offers no network or filesystem, so those three decide the output.
3. A receipt is the hash of what must match; wall-clock and venue are recorded
   but not hashed.
4. Two independent agreeing receipts → `verified`. Disagreement → `disputed`.

## Commands

```bash
m wasmland                                  # what this box carries
m wasmland/engines                          # compute types, live and planned
m wasmland/venues                           # what each venue actually enforces
m wasmland/inspect path=thing.wasm          # read the bytes, store nothing
m wasmland/publish path=thing.wasm title=thing price=2
m wasmland/listings
m wasmland/run listing=<id> input=hi seed=1 # run here, get a receipt
m wasmland/verify <run id>                  # replay and attest — the mechanism
m wasmland/runs status=disputed             # what didn't hold up
m wasmland/grant <address> 100              # credits (the only mint)
m wasmland/to_arena listing=<id>            # a game becomes its own mod
m wasmland/serve                            # API :50480 + console :50481
m wasmland/test
```

## HTTP

Two services: the API (`wasmland-api`, :50480, routed at `/api/wasmland`) and
the console (`wasmland-app`, :50481, routed at `/wasmland`). The console asks
its own origin at `/wasmland/_api/*`, which the app service forwards to the
API, so one page works behind the gateway and on a bare port alike. Status
codes and bytes cross that hop unchanged — 402 must stay 402, and artifact
bytes must still hash to their own id.

```
GET  /engines                 compute types
GET  /venues                  sandbox capabilities, as measured
POST /inspect                 {b64|text, filename} → manifest
POST /artifacts               multipart or b64 → artifact record
GET  /artifacts/{id}/raw      the bytes (paid listings gate here)
GET  /listings                browse            POST /listings   publish
POST /listings/{id}/buy       credits move      POST /listings/{id}/arena
POST /run                     server venue, sandboxed, recorded
POST /runs/claim              a run performed elsewhere — stored as claimed
POST /runs/{id}/verify        replay it here and attest
GET  /runtime/{file}.mjs      the execution layer, served to the tab
```

Auth: `Authorization: <mod-protocol token>` or a wallet session from
`POST /auth/challenge` → `POST /auth/verify`. `WASMLAND_OPEN=1` for local dev.

## Adding a compute type

One entry in `src/engines.py`. Subclass `Engine`, set `venues`,
`determinism` and `verify`, implement `inspect` and `execute`. Nothing in the
API, the receipts or the console is wasm-shaped. If it can't run yet, leave
`status='planned'` and set `needs` — it will be listed, queryable and refuse
with a message that says what is missing.

Pick `verify` honestly:

* `replay` — only for seeded engines. Bitwise reproducible or it isn't replay.
* `consensus` — nearly-deterministic work (GPU kernels, containers).
* `attestation` — TEEs, where the point is that nobody can replay it.

## Traps

* **`unshare` then `setpriv`, in that order.** Dropping to `nobody` first means
  the namespace call has no `CAP_SYS_ADMIN` and fails with `Operation not
  permitted`.
* **The child can't read `/root`.** The runtime is staged into `/tmp` under the
  hash of its contents; otherwise node reports `MODULE_NOT_FOUND` for the one
  file that runs everything.
* **`RLIMIT_AS` cannot be tight.** V8 reserves ~10 GB of guard region per wasm
  memory; a "sane" limit makes every module fail to instantiate with `Out of
  memory: wasm memory`. Use the heap cap and the clock as the real limits.
* **The store appends `.json`.** `artifacts/<id>` and `artifacts/<id>.json` are
  the same file — blobs and records must live in different folders.
* **`import mod` finds the nearest `mod.py`.** Every module in the fleet ships
  one; `storage.protocol()` imports the package with the module's own directory
  off `sys.path`.
* **`new URL(x, location.href)` ignores `<base>`.** The console is served at
  `/wasmland` with a base of `/wasmland/`, so location-relative resolution
  drops a segment and the Worker 404s — as an error event with no message.
  Use `document.baseURI`.

## Related mods

`store` holds everything. `arena` seats agents against published games and
keeps the board. `localfs` mints the CIDs. `cathedral`, `lium` and `targon` are
where the planned `tee` and `gpu` engines would get their hardware.
