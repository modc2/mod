# mcp — MCP Hub

One hub for every MCP server. The local mod fleet is discovered two ways, remote
servers register by URL or straight from the public directories, and the union is
re-exposed as **one MCP server**: point a single client at `POST /mcp` and every
upstream tool is callable as `server__tool`.

```
┌ fleet mods ─ declared: any config.json with endpoints.mcp / urls.mcp / an mcp block
├ swept mods ─ undeclared: every fleet port knocked on, whatever answers MCP is kept
├ user servers ─ any Streamable HTTP endpoint, optional auth headers
├ peer hubs ─ other mod hubs by URL, browsed live or connected whole (nested)
├ index ─ mcpscan, the fleet's internet-wide crawl, every row probed
├ directories ─ featured (keyless) · official · smithery · glama · pulsemcp · docker
└──────────────► mcp-api :50360 ─► POST /mcp  = union of every tool + 7 native ones
                                ├► GET /hub   = this hub's manifest (a `mod` hub)
                                ├► GET /hubs  = every hub type it can see
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

## Hub types

Anything that lists MCP servers is a hub, and this module is one kind of hub
among several. `GET /hubs` is the list of every hub type it can see and connect
to, each probed for what it holds:

| kind | what it is | browse | connect |
|---|---|---|---|
| **mod** | a mod-protocol hub — this software. `GET /hub` is its manifest (`type: mod-hub`), `GET /servers` its registry, `POST /mcp` every tool. This deployment is one (`self: true`); any other deployment is a peer added by URL | `registry=<peer id>` reads its `/servers` live | `POST /hubs/:id/connect` registers its `/mcp` as one upstream — tools arrive nested as `peer__server__tool` |
| **index** | an internet-wide crawl with a probe status per server — `orbit/mcpscan` (31k servers, ~8k live). Recognised automatically when it is aggregated here | `registry=mcpscan` (live rows), `mcpscan:docker` / `mcpscan:github` for one directory it mirrors | connect it whole and `mcpscan__mcp_find` / `mcpscan__mcp_call` search and call any indexed server from your client |
| **directory** | a public registry: `official` (registry.modelcontextprotocol.io), `smithery`, `glama`, `pulsemcp`, `docker`. Glama and PulseMCP want a key (`web.json` → `glama` / `pulsemcp`) and stay out of `all` without one | `registry=<id>` | rows one at a time — `POST /servers` with the row's URL |

```sh
curl localhost:50360/hub                      # what this hub is
curl localhost:50360/hubs                     # every hub type, with counts
curl -X POST localhost:50360/hubs -d '{"url":"https://other-host/api/mcp"}'   # add a peer (write-gated)
curl -X POST localhost:50360/hubs/other/connect                              # its tools, nested
```

Adding a peer identifies its kind from what the URL answers — a manifest at
`/hub` means a mod hub, an index-shaped `/stats` means an index. A bare MCP
endpoint is a server, not a hub, and belongs in `POST /servers`. Peers live in
`~/.mod/mcp/hubs.json`, off-tree, headers included.

## Finding servers across every hub

`GET /catalog` is one search over all of them — the keyless featured list, the
index (rows carry `status: live|auth|down` and a real tool count), every peer,
and the directories — returning rows ready to POST to `/servers`. A row with
`via` is behind a peer hub: its URL is that hub's `/mcp`, so connect the hub
once and the row's tools arrive nested. Names split on the first `__`, so
`otherhub__github__create_issue` routes correctly however deep it goes.

```sh
curl "localhost:50360/catalog?q=github&registry=all&limit=10"
curl "localhost:50360/catalog?q=weather&registry=mcpscan"
```

The same surface is on the MCP face: `hub_hubs` lists the hub types,
`hub_catalog` searches across them, and `hub_connect` registers a server or a
whole hub (`{hub: "mcpscan"}`) — that last one needs registry-edit rights; an
API key buys tool calls, not edits.

`POST /intake` takes whatever you have — a URL, a store/IPFS CID, an
`mcpServers` config blob, a `claude mcp add` one-liner, the text a QR decoded to
— and returns candidate servers.

## Layout

- `mcp-rs/` — Rust API (axum). REST registry + JSON-RPC 2.0 gateway + `--stdio`.
  `fleet.rs` discovery + sweep · `upstream.rs` MCP client + activator wake ·
  `web.rs` search/fetch · `hubs.rs` hub types, manifest, peers · `catalog.rs`
  search across hubs · `intake.rs` paste parser · `auth.rs`/`keys.rs` identity.
- `app/` — Next.js console (`/mcp`): server grid, live probes, tool runner, web
  search, directory browser, client-config snippets and API keys.
- `mod.py` — thin Python client mirroring the REST surface.
- State lives off-tree in `~/.mod/mcp/` (`hub.json`, `hubs.json`, `probes.json`,
  `keys.json`, `web.json`, `server.secret`).

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
