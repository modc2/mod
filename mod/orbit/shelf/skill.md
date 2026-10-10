# shelf

One lens over everything the fleet keeps. Accounts for the 31G under `~/.mod`,
browses the shared store, and checks that content-addressed bytes still hash to
the names they are filed under.

## When to reach for this

* the disk is filling and nobody knows which module is doing it
* something needs to be found in the shared store — a key, a record, an address
* a content-addressed store needs checking, or garbage collecting
* a module's state needs freezing under a CID, or restoring from one
* you want to read another module's state *safely* — this redacts, `cat` does not

## The model in four sentences

1. Every module keeps state in `~/.mod/<name>`; the shared store at
   `~/.mod/store` is the one more than one module reads.
2. A **blob** is bytes filed under their own SHA-256 — the name is a claim
   about the payload, and `verify` recomputes it.
3. A **record** is metadata filed under a blob's id; its bytes were never meant
   to hash to its name, and treating it like a blob invents corruption.
4. Nothing here is cached: every answer is read off the disk when asked, so it
   cannot go stale.

## Commands

```bash
m shelf                          # what is on the box, and the top of the pile
m shelf/space                    # every module's state, largest first
m shelf/usage build              # 12G of what, exactly
m shelf/big limit=10             # the largest individual files
m shelf/prefixes                 # namespaces in the shared store
m shelf/keys prefix=wasmland     # what is filed under one
m shelf/read wasmland/index      # one value, secrets redacted
m shelf/grep 0x89bc              # which record mentions this
m shelf/verify                   # do the blobs still hash to their names
m shelf/strays                   # the same bytes filed in two places
m shelf/orphans                  # bytes nothing refers to
m shelf/gc                       # plan a sweep (dry)
m shelf/gc confirm=True          # take them
m shelf/snapshot wasmland        # freeze a root under a CID
m shelf/inspect <cid>            # what is inside one
m shelf/restore <cid> confirm=True
m shelf/serve                    # API :50570, console :50571
m shelf/test
```

`root=` takes a module name or an absolute path and defaults to the shared
store: `m shelf/keys root=claude`, `m shelf/snapshot root=~/.mod/agent`.

## Things worth knowing

* **Writes are dry until confirmed.** `gc`, `rm` and `restore` return the plan
  and touch nothing without `confirm=True`. `gc` stays age-gated even then —
  a blob can precede the record that cites it by seconds.
* **Secret files are never opened**, not opened-then-hidden: `server.secret`,
  `owner.json`, `*.pem`, `.env`, anything under `vault/ keys/ wallets/ .ssh/`.
* **Secret fields come back as `sha256:1f3a9c02 (64b)`** — enough to tell
  whether two modules share a key or whether it rotated, never enough to use.
  `raw=True` does not defeat this; there is no argument that turns it off.
* **Loopback only.** `route` is `false` and both halves bind `127.0.0.1`,
  because this reads private state. Use an SSH tunnel, not the gateway.
* **Snapshots are deterministic** — same tree, same CID — and pinned as a
  base64 JSON envelope because `localfs` is not binary-safe: `get(put(bytes))`
  returns a lossily-decoded `str` and no encoding recovers it.
* **It is not `core/store`.** Module names are path-derived and `core`
  overwrites `orbit` in the tree, so anything at `orbit/store` is unreachable.
  This reads what the store writes; it is not a second store.
