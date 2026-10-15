# zkworld.circom

The Circom framework as a running submod of zkworld. Arithmetic circuits compiled to R1CS for Groth16/PLONK. circom builds the constraints + a wasm witness generator; tests are input/expected-output JSON cases run through the real witness.

Its own server (`src/api/fwserver.py circom 51354`, loopback) is kept up by the parent and proxied at `/zkworld/circom/` (page) and `/zkworld/circom/api/<fn>`. It owns the Circom projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.circom/info
m zkworld.circom/projects
m zkworld.circom/serve
```
