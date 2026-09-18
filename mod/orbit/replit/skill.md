# replit — the mod ⇄ Repl bridge, and MCP over the mods on Replit

Two directions. **Out:** any fleet module becomes a Replit-runnable project.
**In:** a deployed Repl becomes a first-class mod — discovered, catalogued,
callable, and lifted into MCP tools an agent can use directly.

**Ports:** console + API + MCP on `50530` at `/replit`. Start with `m replit/serve`.

## Quick reference

```sh
m replit                          # status: mods on replit, bundles, db, mcp
m replit/catalog                  # the mods running on Repls + every fn
m replit/link demo https://x.replit.app   # link + discover in one step
m replit/repl demo refresh=1      # one Repl, re-discovered over the network
m replit/call demo hello name=x   # call a fn on it (args= for a dict)
m replit/mcp_tools                # the MCP tool surface
m replit/mcp tools/list           # one JSON-RPC message, from the CLI
m replit/bundle git               # package orbit/git as a Repl project
m replit/zip git                  # …and zip it for upload
m replit/run_url owner/repo       # one-click Run-on-Replit URL
m replit/import_repl owner/repo   # a Repl's repo → an orbit module
m replit/db_set hello world       # Replit KV over your own REPLIT_DB_URL
```

## MCP

```sh
claude mcp add replit -- python3 /root/mod/mod/orbit/replit/mcp.py   # stdio
curl -s http://localhost:50530/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Two layers of tools:

- **the bridge** — `replit_catalog`, `replit_repl`, `replit_call`, `replit_link`,
  plus bundle / db / import. Start at `replit_catalog`.
- **the mods on Replit** — every fn of every linked Repl as its own tool,
  `repl_{remote}_{fn}`, typed from the signatures the Repl reports on its null
  call. An agent calls the Repl-hosted mod directly instead of driving this
  module.

The dynamic half is rebuilt on every `tools/list` from the local cache, so
linking a Repl adds tools with no restart and no network round trip.

## Things to know before changing this module

- **Replit has no usable public API.** GraphQL is persisted-query-only (400),
  `.zip` export is 403, `/data/repls/...` is 404. Everything here goes through
  what is actually reachable: `replit.com/github/{owner}/{repo}`, the KV store
  at your own `REPLIT_DB_URL`, and HTTP to a deployment you control. Do not add
  a "list my Repls" fn — there is nothing behind it.
- **The null call is a contract between two files.** `templates/main.py.tmpl`
  reports `{name, description, fns, params, docs}`; `discover()` caches it and
  `mcp.py` turns `params` into tool schemas. Change one, change all three —
  `tests/test_replit.py` boots the generated `main.py` for real to keep them
  honest.
- **Remote args go in `args`, never `**kwargs`.** A Repl-hosted mod is entitled
  to a parameter called `name` or `timeout`, which are `call()`'s own. The MCP
  dynamic handler always passes a dict.
- **Writes are loopback-only** — a request with `X-Forwarded-For` gets a
  read-only console, and MCP mirrors that per tool (reads answer, writes return
  `isError` with the reason). The CLI is unrestricted.
- **Repls sleep.** A slow first call is a wake, not a failure. `ping` before
  concluding a remote is down, and expect the first `discover` after idle to
  take seconds.
- **A module that imports the fleet SDK will not boot on Replit** — `mod` is
  not on PyPI. `bundle()` says so in `warnings`; the generated `main.py` reports
  the ImportError on `/health` instead of crashing.
- **State is off-chain** under `~/.mod/replit` (`secrets.json` is `0600`).
  Never put a `REPLIT_DB_URL` in `config.json` — it embeds a token.
