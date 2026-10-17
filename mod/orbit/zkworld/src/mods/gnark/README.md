# zkworld.gnark

The gnark framework as a running submod of zkworld. Consensys' Go library for zkSNARK circuits: a Define(api) method over frontend.Variable fields. The starter ships a runner (zkworld.go) that solves the circuit with test.IsSolved; `go test` runs the native asserts.

Its own server (`src/api/fwserver.py gnark 51358`, loopback) is kept up by the parent and proxied at `/zkworld/gnark/` (page) and `/zkworld/gnark/api/<fn>`. It owns the gnark projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.gnark/info
m zkworld.gnark/projects
m zkworld.gnark/serve
```
