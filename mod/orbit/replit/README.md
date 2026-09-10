# replit

The Replit interface for the mod protocol: a two-way bridge between a fleet
module and a Repl, and an MCP server over the mods that live on Replit.

```sh
m replit/serve      # console + API + MCP on :50530  →  modc2.com/replit
```

## What it does

**Address the mods running on Replit.** `link` registers a deployed Repl and
null-calls it once, caching the mod it serves, every function and every
parameter. `catalog` is that index; `call` reaches any function on any of them.

```sh
m replit/link demo https://my-mod.replit.app
m replit/catalog
m replit/call demo hello name=world
```

**Hand them to an agent.** `mcp.py` is a full MCP server — the bridge's own
tools, plus one typed tool per function per linked Repl (`repl_{remote}_{fn}`),
rebuilt on every `tools/list` so linking a Repl adds tools with no restart.

```sh
claude mcp add replit -- python3 /root/mod/mod/orbit/replit/mcp.py
curl -s http://localhost:50530/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

The console at :50530 answers `POST /mcp` with the same dispatcher, so
`modc2.com/replit/mcp` is a live endpoint with no second process.

**Send a module the other way.** `bundle` packages any fleet module as a
Replit-runnable project — its own files plus `.replit`, `replit.nix`,
`requirements.txt` and a stdlib `main.py` that reproduces the mod URL rule on
the Repl side (`POST /{fn}`, a null `POST` → info, `GET /health`). Take the zip,
or the one-click `replit.com/github/{owner}/{repo}` URL.

**Replit DB** over your own `REPLIT_DB_URL`, and **import** of the GitHub repo
behind a Repl as a scaffolded orbit module.

## What Replit actually leaves open

| endpoint | status | meaning |
| --- | --- | --- |
| `replit.com/github/{owner}/{repo}` | 200 | the import path — one-click Run on Replit |
| `kv.replit.com` (Replit DB) | token | works with your own `REPLIT_DB_URL` |
| `replit.com/graphql` | 400 | persisted query hash required — no ad-hoc queries |
| `replit.com/@user/slug.zip` | 403 | anonymous Repl export is blocked |
| `replit.com/data/repls/@user/slug` | 404 | the old public repl API is gone |

There is no "list my Repls" — this module bridges what is reachable and says so
where it isn't.

## Access

Writes (link, discover, bundle, import, DB writes, and any call that reaches a
Repl) require a loopback request with no `X-Forwarded-For`. Through the gateway
the console and the MCP endpoint are read-only; the CLI is unrestricted. State
lives under `~/.mod/replit`, secrets `0600`, never in `config.json`.

## Tests

```sh
python3 -m pytest orbit/replit/tests -q
```

The suite bundles a tiny module, boots the generated `main.py` on a loopback
port and drives the bridge at it — discovery, catalog, calls, the MCP
dispatcher and the HTTP surface all run against a real Repl-shaped server.
