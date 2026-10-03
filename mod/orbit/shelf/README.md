# shelf

**One lens over everything the fleet keeps.**

Seventy-two modules write their state into `~/.mod/<name>`, by convention and
without supervision. It is 31 gigabytes. Two directories are 12G each, the
shared store is the fleet's actual database, and until now nothing could tell
you any of that — not what is in it, not what is safe to delete, and not
whether the content-addressed bytes still hash to the names they are filed
under.

```
m shelf                          # what is on the box, and the top of the pile
m shelf/space                    # every module's state, largest first
m shelf/usage build              # 12G of what, exactly
m shelf/big limit=10             # the largest individual files
m shelf/prefixes                 # namespaces in the shared store
m shelf/keys prefix=wasmland     # what is filed under one
m shelf/read wasmland/index      # one value, secrets redacted
m shelf/grep 0x89bc              # which record mentions this
m shelf/verify                   # do the blobs still hash to their names
m shelf/orphans                  # bytes nothing refers to
m shelf/gc confirm=True          # take them (dry by default)
m shelf/snapshot wasmland        # freeze a root under a CID
m shelf/serve                    # API :50570, console :50571
```

## What it found on the box it was written on

Not hypotheticals — the first run of `m shelf/verify` against the live store:

- **One blob does not hash to its own name.** `blobs/ca354a0d…` holds bytes that
  hash to `c5f7ba3a…`. Every receipt citing that id attested to something other
  than what is stored there now. Nothing else in the fleet was checking.
- **Three blobs are filed twice**, at `blobs/<id>` and `wasmland/blobs/<id>` —
  leftovers from a layout that moved. Identical names mean identical bytes, so
  the copies are safe to drop.
- **12.5G in `build/blobs`** across 5,055 files, and 12.3G more in `claude/` —
  which is 80% of `~/.mod` in two directories.

## The four things it does

**Space.** Per-module accounting that walks with `os.scandir` and reads `stat`
only — nothing here opens a file, so ~46k entries cost about a tenth of a
second and no accounting pass can leak what it is counting. Vendored caches
(`node_modules`, `target`, `.next`) are counted but reported separately: "12G of
node_modules" and "12G of your data" call for different reactions.

**The store.** Browse, search and grep `~/.mod/store` — the shared database
wasmland and the arena both write into. It holds no index: every answer is
computed from the directory at the moment it is asked, which costs a walk per
call and buys the only property an operator tool really needs, which is that it
cannot be stale and cannot disagree with the disk.

**Integrity.** A store that files bytes under the SHA-256 of themselves is
making a promise that nothing enforces — the hash is computed once, by the
writer, and taken on faith forever after. `verify` recomputes them. It also
distinguishes a *blob* (bytes; the name is a claim about them) from a *record*
(metadata filed under a blob's id; its bytes were never meant to hash to its
name). Conflating those made the first version of this module report six
healthy records as corrupt, which is the failure an integrity checker can least
afford.

**Snapshots.** A deterministic tar of one root, pinned to `localfs`: entries
sorted, mtimes and uids zeroed, gzip stamped with `mtime=0`, so the same tree
always produces the same bytes and therefore the same CID. Secret files are
*excluded* rather than redacted, because these bytes leave the box and a
redacted secret is still a decision about how much of one to hand over.

## Reading private state without becoming a way to steal it

`~/.mod` holds HMAC secrets, owner claims, wallets and PATs sitting next to the
boring JSON somebody actually wants to look at. A state browser that renders
every value it finds is a credential exfiltrator with a nice console. So:

- **Redaction is on the API read path**, in `src/redact.py`, before a value is
  ever serialised — not in the page, where it would be one `curl` away from
  irrelevant.
- **Secret files are never opened.** `server.secret`, `owner.json`, `*.pem`,
  `.env`, anything under `vault/`, `keys/`, `wallets/`, `.ssh/`. Not read and
  then hidden — not read.
- **Secret fields come back as fingerprints**: `sha256:1f3a9c02 (64b)`. That is
  deliberately more than a row of asterisks. It tells you whether two modules
  were handed the same key and whether it rotated since yesterday, without the
  bytes leaving the box.
- **Matching is on names and over-redacts on purpose** — `pubkey` is hidden
  because it contains `key`. A redacted public key is an inconvenience; a
  rendered private one is an incident.
- **Both halves bind `127.0.0.1` and `route` is `false`.** The gateway will not
  publish this. Reach it over an SSH tunnel.
- **Grep skips secret files rather than searching them**, because a hit/miss on
  a secret file is a disclosure one bit at a time.

## Writes are dry until you ask twice

`gc`, `rm` and `restore` all plan first and return exactly what they would do;
only `confirm=True` touches the disk. `gc` is age-gated even when confirmed —
a blob can legitimately be written seconds before the record that will cite it,
and the one thing worse than a full disk is a collector that won that race.
`rm` refuses to delete a secret file at all. The console can plan a restore but
deliberately makes you run the write from the CLI.

## Console

```
m shelf/serve          # pm2: shelf-api :50570, shelf-app :50571
open http://127.0.0.1:50571/shelf
```

Four views — space, store, integrity, snapshots. Stdlib on both sides and
plain ES modules in the page, with no build step and no `node_modules`, so the
console still renders and reports "API down" while the API half is restarting.
Chart colours come from the fleet's validated data-viz palette, stepped for
this dark surface and checked with the palette validator; status never reads by
colour alone, so every health pill carries a glyph and a word.

## Why it is not called `store`

It was, for a day, at `orbit/store` — and it was unreachable. Module names are
derived from paths, and `core.tree.tree()` applies the orbits in an order that
lets `core` overwrite `orbit`, so `m.mod('store')` resolves to `core/store` and
always will. Anything built under that name is dead code. `shelf` is a lens
over what the store holds; `core/store` is still the thing that writes.

## Tests

```
m shelf/test          # or: python3 -m pytest tests/ -q
```

22 tests, and the interesting ones are negative: that a secret file is never
opened (asserted by watching `builtins.open`), that keys cannot escape their
root, that a healthy record is not called corrupt, that a changed blob is
caught, that `gc` spares young orphans, and that a snapshot round-trips
filenames exactly. Each of those was wrong at some point while this was being
written.
