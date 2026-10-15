# zkworld.halo2

The halo2 framework as a running submod of zkworld. PLONKish circuits in Rust (zcash halo2_proofs): columns, gates, regions. The starter's binary is the runner and checks the circuit with MockProver; `cargo test` runs the native tests.

Its own server (`src/api/fwserver.py halo2 51359`, loopback) is kept up by the parent and proxied at `/zkworld/halo2/` (page) and `/zkworld/halo2/api/<fn>`. It owns the halo2 projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.halo2/info
m zkworld.halo2/projects
m zkworld.halo2/serve
```
