# zkworld.cairo

The Cairo · Starknet framework as a running submod of zkworld. STARK-provable contracts for Starknet. scarb builds Sierra/CASM, snforge tests them, every contract gets a live app on a local devnet.

Its own server (`src/api/fwserver.py cairo 51353`, loopback) is kept up by the parent and proxied at `/zkworld/cairo/` (page) and `/zkworld/cairo/api/<fn>`. It owns the Cairo · Starknet projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.cairo/info
m zkworld.cairo/projects
m zkworld.cairo/serve
```
