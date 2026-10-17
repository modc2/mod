# zkworld.custom

The Any framework (runner protocol) framework as a running submod of zkworld. Bring any ZK toolchain: the project's config.json `zk` block names its build/test/run commands, and its runner speaks the two-call protocol (--abi, <input.json>). zkVMs and libraries plug in here.

Its own server (`src/api/fwserver.py custom 51360`, loopback) is kept up by the parent and proxied at `/zkworld/custom/` (page) and `/zkworld/custom/api/<fn>`. It owns the Any framework (runner protocol) projects: `projects()` lists them, and new/build/test/vibe/witness only take those. Writes are owner-only (the parent gates them).

```sh
m zkworld.custom/info
m zkworld.custom/projects
m zkworld.custom/serve
```
