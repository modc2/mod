# mcp — MCP Hub

One hub for every MCP server. The local mod fleet is discovered two ways, remote
servers register by URL or straight from the public directories, and the union is
re-exposed as **one MCP server**: point a single client at `POST /mcp` and every
upstream tool is callable as `server__tool`.

```
┌ fleet mods ─ declared: any config.json with endpoints.mcp / urls.mcp / an mcp block
├ swept mods ─ undeclared: every fleet port knocked on, whatever answers MCP is kept
├ user servers ─ any Streamable HTTP endpoint, optional auth headers
├ directories ─ featured (keyless) · registry.modelcontextprotocol.io · smithery
└──────────────► mcp-api :50360 ─► POST /mcp  = union of every tool + 5 native ones
                                └► REST registry for the console (:50361, /mcp)
```

## Finding servers

Two passes, because config.json is not a reliable witness:

- **Declared** — `fleet::discover()` reads every mod's config.json and accepts
  all three spellings the fleet actually uses (`endpoints.mcp`, `urls.mcp`, a
  top-level `mcp` block). A declared mod keeps its row even while it is asleep.
- **Swept** — `POST /discover` (and a background pass every `MCP_SWEEP_SECS`)
  knocks on every port any config mentions and keeps whatever completes an MCP
  handshake. This is how mods that serve `/mcp` and never say so are found.

A local mod that is scaled to zero refuses its own port. An explicit re-probe
(`POST /servers/:id/refresh`) and any tool call to it go through the activator at
`:9000/api/{mod}/mcp` instead, which wakes it — background probes deliberately do
not, so idle mods stay idle.

## The web

The hub carries `web_search` and `web_fetch` itself, so a client that connects
here can search the web with nothing else configured:

- providers are tried in order — `brave`, `tavily`, `exa`, `serper` (only when a
  key is present), then **keenable**, a public keyless MCP search server, then
  DuckDuckGo instant answers;
- keys come from the environment (`BRAVE_API_KEY`, …) or `~/.mod/mcp/web.json`
  (`{"brave": "…", "smithery": "…"}`), never from the tree;
- `web_fetch` refuses loopback and private addresses — the hub answers on a
  public route and the rest of the fleet expects to be unreachable from it.

```sh
curl "localhost:50360/search?q=model+context+protocol&count=5"
curl "localhost:50360/fetch?url=https://modelcontextprotocol.io/&max_chars=2000"
```

## Connecting other hubs

`GET /catalog` searches the public directories live and returns rows that are
ready to POST to `/servers`. A hub aggregating another hub is fine: names are
split on the first `__`, so `otherhub__github__create_issue` routes correctly.

```sh
curl "localhost:50360/catalog?q=github&registry=all&limit=10"
```

`POST /intake` takes whatever you have — a URL, a store/IPFS CID, an
`mcpServers` config blob, a `claude mcp add` one-liner, the text a QR decoded to
— and returns candidate servers.

## Layout

- `mcp-rs/` — Rust API (axum). REST registry + JSON-RPC 2.0 gateway + `--stdio`.
  `fleet.rs` discovery + sweep · `upstream.rs` MCP client + activator wake ·
  `web.rs` search/fetch · `catalog.rs` public directories · `intake.rs` paste
  parser · `auth.rs`/`keys.rs` identity.
- `app/` — Next.js console (`/mcp`): server grid, live probes, tool runner, web
  search, directory browser, client-config snippets and API keys.
- `mod.py` — thin Python client mirroring the REST surface.
- State lives off-tree in `~/.mod/mcp/` (`hub.json`, `probes.json`, `keys.json`,
  `web.json`, `server.secret`).

## Quick start

```sh
cd mcp-rs && cargo build --release
pm2 start mcp-rs/target/release/mcp-api --name mcp-api
cd app && npm install && npx next build && pm2 start npm --name mcp-app -- start
```

Connect a client to everything at once:

```sh
claude mcp add hub --transport http https://modc2.com/api/mcp/mcp
```

Register a remote server:

```sh
curl -X POST localhost:50360/servers \
  -d '{"url":"https://host/mcp","id":"myserver","headers":{"Authorization":"Bearer …"}}'
```

Servers are probed (initialize + tools/list) before registration; a server that
won't shake hands is rejected unless `force:true`.

## Who may do what

Identity is borrowed from `orbit/build` — sign in there and the hub already
knows you (same origin, same token).

| | anonymous, via the gateway | on this host | API key | owner / editor |
|---|---|---|---|---|
| browse registry, tools, catalog, web search | yes | yes | yes | yes |
| run an aggregated tool | no | yes | yes | yes |
| edit the registry | no | yes | no | yes |
| mint API keys | no | no | no | owner only |

"On this host" means the request carried no `X-Forwarded-*` header, i.e. it did
not come through Caddy. Anything already on the box can reach every upstream
directly and can edit `~/.mod/mcp/hub.json` by hand, so gating it would be
ceremony; set `MCP_GATE_LOCAL=1` to demand a credential anyway. `ACCESS_OPEN=1`
turns every gate off.

Mint a key in the console (owner only) and give it to a remote client:

```sh
claude mcp add hub --transport http https://modc2.com/api/mcp/mcp \
  --header "Authorization: Bearer mcphub_…"
```

See `config.json` for the full endpoint map, the `ServerEntry`/`Probe` schemas,
and every env knob.
