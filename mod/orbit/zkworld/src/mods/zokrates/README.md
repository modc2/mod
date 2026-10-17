# zkworld.zokrates

The ZoKrates framework as a running submod of zkworld. A high-level, Python-like language for zkSNARKs on Ethereum. zokrates compiles to R1CS + an ABI, computes witnesses and exports a Solidity verifier. Tests are the shared JSON cases.

Its own server (`src/api/fwserver.py zokrates 51356`, loopback) is kept up by the parent and proxied at `/zkworld/zokrates/` (page) and `/zkworld/zokrates/api/<fn>`. It owns the ZoKrates projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.zokrates/info
m zkworld.zokrates/projects
m zkworld.zokrates/serve
```
