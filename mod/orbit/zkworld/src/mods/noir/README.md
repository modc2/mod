# zkworld.noir

The Noir framework as a running submod of zkworld. A Rust-like language for ZK programs (Aztec). nargo compiles to ACIR and runs #[test] fns; any backend (Barretenberg bb) proves.

Its own server (`src/api/fwserver.py noir 51355`, loopback) is kept up by the parent and proxied at `/zkworld/noir/` (page) and `/zkworld/noir/api/<fn>`. It owns the Noir projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.noir/info
m zkworld.noir/projects
m zkworld.noir/serve
```
