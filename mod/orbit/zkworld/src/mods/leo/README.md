# zkworld.leo

The Leo · Aleo framework as a running submod of zkworld. A Rust-like, private-by-default language for Aleo programs. leo builds Aleo instructions, `leo run` executes off-chain and @test fns run in-VM. Shared JSON cases run through `leo run`.

Its own server (`src/api/fwserver.py leo 51357`, loopback) is kept up by the parent and proxied at `/zkworld/leo/` (page) and `/zkworld/leo/api/<fn>`. It owns the Leo · Aleo projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.leo/info
m zkworld.leo/projects
m zkworld.leo/serve
```
