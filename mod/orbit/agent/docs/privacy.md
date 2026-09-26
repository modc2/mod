# Module privacy — public by default, sealed when it isn't

Every module on this host is **public**. Public means auditable: anyone,
signed in or not, can list the fleet, walk a module's file tree and read its
source. That is the point of a module — you are being asked to run someone
else's code on your box, so you get to read it first.

The MODULES tab in the console is that surface. So is the API, which is what
an outside auditor actually uses:

```
GET /api/agent/modules                       # the fleet + each visibility
GET /api/agent/modules/{name}/tree           # file list
GET /api/agent/modules/{name}/file?path=…    # one file
```

No key, no account, no rate gate.

## What a public module does not show

Auditing source is not the same as dumping a host. Three things never come
back from those endpoints, for anyone including the owner:

- **secrets** — `.env*`, `*.key`, `*.pem`, `*secret*`, `credentials*`,
  `id_rsa*`, `owner.json`, `*.key.enc`. Keys live in the vault, and reading
  one is a vault operation with a vault's rules.
- **build output and dependency trees** — `node_modules`, `.next`, `target`,
  `dist`, `__pycache__`, `.git`, and the rest of `SKIP_DIRS`. Not source, and
  walking them turns a 200-file audit into a 200,000-file one.
- **anything outside the module directory.** The reader resolves the path and
  compares realpaths, so `../../etc/passwd`, an absolute path and a symlink
  pointing out of the tree all fail the same check.

Files are capped at 512 KB of text and binaries come back flagged rather than
inlined.

## Going private

The owner can flip one module, or the whole fleet:

```
POST /api/agent/modules/{name}/visibility  {"visibility": "private", "key": …}
POST /api/agent/modules/visibility         {"visibility": "private", "key": …}
```

The fleet-wide call also moves the **default**, so modules created later
inherit it rather than quietly appearing in public.

A private module drops out of the audit endpoints — it is still *listed*, by
name, with nothing else. That a module exists is not the secret; its source
is. Hiding the name too would make the fleet lie about its own shape.

## Sealing: what a public push actually carries

Going private also **seals** the module. Three things happen:

1. `.modseal` is written into the module directory — a deterministic
   `tar.gz` of the source, encrypted with AES-256-GCM under a random
   per-seal content key, which is itself wrapped under the fleet key.
2. A managed `.gitignore` replaces the module's own (parked in the state
   dir): ignore everything, except the blob.
3. The plaintext is dropped from the git index — `.gitignore` does not
   untrack what git already tracks — and the blob is staged in its place.

So after a seal, `git commit && git push` publishes ciphertext. A clone gets
`.gitignore` and `.modseal`, and whoever holds the key runs:

```
POST /api/agent/modules/{name}/restore  {"key": …}
```

to get the tree back. Without the key the blob is noise.

### Why not encrypt the files in place?

Because a module is not an archive, it is a running service. Encrypting
`agent/src/mod.py` in place stops the agent from importing it, and doing that
across a fleet takes the host down. So the plaintext stays on the owner's own
disk — which is the owner's already — and the *published* form is the blob.

### What sealing cannot do

**It cannot unpublish.** If a module's source was pushed before it was
sealed, it is in the repo's history and on every clone that ever pulled. The
seal covers everything from here on. Rewriting history (`git filter-repo`)
and rotating anything that leaked is a separate, deliberate job.

## The key

One 32-byte key for the whole fleet, at `~/.mod/agent/privacy/master.key`,
mode 0600, off the module tree so it is never a candidate for a commit.

```
POST /api/agent/privacy/key  {"op": "state"|"export"|"import"|"passphrase", …}
```

- **export** hands you the base64 key. Write it down. It is the only thing
  that opens a sealed push — lose it and the blob stays noise for you too.
- **passphrase** wraps the key file under scrypt, so holding the host disk is
  no longer holding the key; every seal and restore then needs the passphrase
  typed. Without one, root on this box is enough.

## State

Nothing here is committed. All of it lives under `~/.mod/agent/privacy/`:

```
visibility.json    the default + per-module overrides
master.key         the fleet key (raw, or scrypt-wrapped)
gitignore/<mod>    the module's own .gitignore, parked while it is sealed
```
