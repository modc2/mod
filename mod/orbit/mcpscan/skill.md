---
name: mcpscan
description: Find any MCP server that exists — search a crawled index of every public directory, see which endpoints are actually live, and call tools on any of them without registering anything
---

# mcpscan

An index of every MCP server on the internet, kept current by a scraper that
crawls the public directories, probes every endpoint on a loop, and hunts for
endpoints nobody published.

```
scan-rs/src/
  sources.rs   # the crawlers: official registry, smithery, docker, github, pulsemcp, glama
  prober.rs    # the three loops — crawl, probe (never idle), hunt
  index.rs     # in-memory index, merge-by-url, search/ranking, stats
  face.rs      # the MCP face: mcp_find / mcp_server / mcp_tools / mcp_call / mcp_probe / mcp_stats
  upstream.rs  # MCP client built for volume; classifies live | auth | error | down
  console.html # the single-page console (compiled into the binary)
```

## Use it from Python

```python
import mod as m
scan = m.mod('mcpscan')

scan.search(q="postgres", status="live", limit=10)   # search names, descriptions, tool names
scan.server("io.github.foo-bar")                     # one server, full detail
scan.call(server="deepwiki", tool="ask_question", args={"question": "…"})
scan.probe("https://example.com/mcp")                # handshake now, index the result
scan.stats()                                         # index size + scraper telemetry
scan.crawl()                                         # re-read the directories now
scan.hunt(budget=20)                                 # knock on undeclared domains
scan.export(status="live", format="mcp")             # mcpServers block of everything live
```

## Use it from an MCP client

```bash
claude mcp add mcpscan --transport http https://modc2.com/api/mcpscan/mcp
```

Six tools, because a flat union of ~200k tools is not a thing a client can
load: `mcp_find` (search), `mcp_server` (detail), `mcp_tools` (live schemas for
one server), `mcp_call` (run any tool on any indexed server), `mcp_probe`,
`mcp_stats`.

## What the statuses mean

- `live` — handshook anonymously and listed its tools. Callable right now.
- `auth` — the endpoint exists and answered, but wants a credential. Pass
  `headers` to `mcp_call`/`probe` and it works.
- `error` — something answered and it wasn't MCP (a website at `/mcp`, a 404).
- `down` — nothing answered.
- `unknown` — not probed yet, or a package-only server (`npm:…`, `docker:mcp/…`)
  with no endpoint. Still indexed; install it locally from `packages`.

## Gotchas

- **Rows merge on endpoint URL, not just id.** The same server listed by the
  official registry and by GitHub is one row with two `sources`. Don't assume
  `id` came from any particular directory.
- **Most Smithery endpoints answer `auth`** — `server.smithery.ai/{name}/mcp`
  wants a Smithery key. That's a true fact about the server, not a probe bug.
- **The probe loop is deliberately never idle.** If you need the box quiet,
  raise `MCPSCAN_PROBE_GAP` or drop `MCPSCAN_BATCH`; don't kill the process,
  the re-check clocks are the whole design.
- **First run seeds itself.** An empty `~/.mod/mcpscan/catalog.json` triggers a
  full crawl at startup (~2 minutes, ~20k rows). After that the crawl clock is
  6 hours.
- `crawl` and `hunt` are gated by `~/.mod/mcpscan/server.secret` when that file
  exists; every read is open.

## Related

`orbit/mcp` is the hub for servers you have *chosen* — the local fleet plus
whatever you registered. Register mcpscan there and one client endpoint reaches
both: the fleet's tools directly, and everything else through `mcpscan__mcp_*`.
