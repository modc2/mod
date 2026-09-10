# mcpscan — every MCP server on the internet

An index of the whole public MCP surface, kept warm by a scraper that never
stops. It reads the directories, knocks on every endpoint they publish, goes
looking for the ones they don't, and hands the result to any MCP client as six
tools.

```
{host}/mcpscan          the console
{host}/api/mcpscan      the REST index
{host}/api/mcpscan/mcp  the index as an MCP server
```

## What it actually does

**Crawls.** Every six hours it re-reads:

| source | what it is | key |
|---|---|---|
| `official` | `registry.modelcontextprotocol.io` — the MCP project's own registry | none |
| `smithery` | `registry.smithery.ai` — ~11k servers, deployed ones reachable at `server.smithery.ai/{name}/mcp` | none |
| `docker` | `docker/mcp-registry` — containerised servers | none |
| `github` | repo search on a rotating set of queries/pages, so the long tail arrives over time | none (a token raises limits) |
| `pulsemcp` | `api.pulsemcp.com` v0.1 | `PULSEMCP_API_KEY` |
| `glama` | `glama.ai/api/mcp/v1` | `GLAMA_API_KEY` |

A server listed in three directories is **one row** — rows merge on id *and* on
endpoint URL, so `io.github.foo/bar` from the registry and `foo/bar` from GitHub
don't fork into two.

**Probes, forever.** Every endpoint has its own re-check clock (live 6h, needs
a key 12h, dead 6h doubling per consecutive failure up to 14 days), so there is
always a batch due and the loop never idles. Each probe is a real MCP
handshake — `initialize`, then `tools/list` — and the outcome is classified,
not just recorded:

- **live** — shook hands anonymously and listed its tools
- **auth** — the endpoint is *there* and answered, but wants a credential
- **error** — something HTTP-shaped answered and it wasn't MCP
- **down** — nothing answered
- **unknown** — not probed yet, or it's a package-only (stdio) server with no
  endpoint to probe. Those are still indexed: they're still servers that exist.

**Hunts.** Most indexed servers publish a repo and a homepage but no endpoint.
Every two minutes the hunter takes a few of those domains and knocks on `/mcp`,
`/sse`, `/api/mcp`, `/mcp/sse`, `/v1/mcp`, `/mcp/v1`. A hit is an MCP server no
directory knew how to reach. One knock per domain per 30 days; code hosts,
package registries and the directories themselves are skipped.

## Aggregation that survives the scale

Twenty thousand servers is roughly two hundred thousand tools. No client can
hold that in one `tools/list`, so the MCP face doesn't try — it aggregates by
search-and-call instead:

| tool | what it does |
|---|---|
| `mcp_find` | search every indexed server by name, description or **tool name** |
| `mcp_server` | everything the index holds about one server |
| `mcp_tools` | live `tools/list` against one server — real schemas, fetched now |
| `mcp_call` | call any tool on any indexed server; fresh session, nothing registered first |
| `mcp_probe` | handshake with a URL now and add it to the index |
| `mcp_stats` | index size and what the scraper has been doing |

```bash
claude mcp add mcpscan --transport http https://modc2.com/api/mcpscan/mcp
```

Registered on the fleet's hub (`orbit/mcp`) the same tools arrive as
`mcpscan__mcp_find` and friends, so one client endpoint reaches both the local
fleet and the whole public internet.

## REST

```bash
curl 'localhost:50700/catalog?q=postgres&status=live&limit=5'
curl 'localhost:50700/catalog/io.github.foo-bar'
curl 'localhost:50700/stats'
curl 'localhost:50700/sources'
curl 'localhost:50700/recent'                      # the scraper's live feed
curl -X POST localhost:50700/crawl -d '{}'         # re-read the directories now
curl -X POST localhost:50700/hunt  -d '{"budget":20}'
curl -X POST localhost:50700/probe -d '{"url":"https://example.com/mcp"}'
curl -X POST localhost:50700/call  -d '{"server":"deepwiki","tool":"ask_question","args":{}}'
curl 'localhost:50700/export?status=live&format=mcp'   # mcpServers block of everything live
```

`?sort=` takes `relevance` (default), `tools`, `fast`, `recent`, `name`.
`?source=` filters to one directory — including `hunt` and `probe`, the two
"directories" this module writes itself.

## From Python

```python
m mcpscan/search q="github issues" status=live
m mcpscan/call server=deepwiki tool=ask_question args='{"question":"..."}'
m mcpscan/stats
```

## State and manners

State is off-tree in `~/.mod/mcpscan/`: `catalog.json` (the index, flushed on a
timer because it is megabytes), `sources.json` (crawl reports), `server.secret`
(present ⇒ `crawl` and `hunt` need it as a Bearer; reads are always open).

The crawler identifies itself (`User-Agent: mcpscan/1.0`), caps concurrency per
batch, spaces its GitHub search calls, and never knocks on a domain more than
once a month. It holds no credentials for anyone else's server: `call` and
`probe` send only the headers the caller supplies, so an `auth` row stays `auth`
until someone with a key asks.

## Env

`MCPSCAN_PORT` 50700 · `MCPSCAN_APP_PORT` 50701 · `MCPSCAN_DIR` ~/.mod/mcpscan ·
`MCPSCAN_CRAWL_SECS` 21600 · `MCPSCAN_BATCH` 64 · `MCPSCAN_PROBE_GAP` 2 ·
`MCPSCAN_PROBE_TIMEOUT` 8 · `MCPSCAN_HUNT_SECS` 120 · `MCPSCAN_HUNT_BATCH` 8 ·
`MCPSCAN_HUNT_COOLDOWN` 2592000 · `MCPSCAN_MAX_PAGES` 400 ·
`MCPSCAN_GITHUB_PAGES` 3 · `MCPSCAN_GITHUB_TOKEN` · `PULSEMCP_API_KEY` ·
`GLAMA_API_KEY` · `ACCESS_OPEN=1`

## Build

```bash
cd scan-rs && cargo build --release
pm2 restart mcpscan-api --update-env
```
