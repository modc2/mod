# The fleet's MCP servers, as tools

The agent reaches the fleet two ways. `mod.<name>` (docs: the fleet
registry) calls a module's `forward()` in-process with one generic
fn/params convention. This is the richer door: ~50 fleet modules serve
Model Context Protocol (Streamable HTTP, JSON-RPC 2.0) off their own
ports, and every MCP tool carries a real JSON schema — name, description,
typed arguments. Mounted here each becomes a first-class agent tool:

    mcp.<module>.<tool>          mcp.dns.dns_resolve, mcp.build.list_modules

The model sees the tool's own argument schema in its prompt, so it calls
`mcp.dns.dns_resolve(query="agent")` directly instead of guessing kwargs
through a generic bridge.

## Local-first discovery

Nothing is configured by hand and nothing leaves the box. Discovery scans
the fleet's own `config.json` files for any MCP declaration (`urls.mcp`,
`endpoints.mcp`, or an `mcp` block), builds candidate URLs (falling back
to the `{api}/mcp` convention), and probes them with a real `initialize` +
`tools/list` handshake — whichever candidate answers wins. The probed
catalog persists off-tree in `~/.mod/agent/mcp/index.json` (TTL
`AGENT_MCP_TTL`, default 1h; a bad probe keeps the previous catalog, so a
rebuilding module doesn't vanish from the registry). The orbit/mcp hub is
just one more discovered server — connect through it and its whole
aggregated union rides along as `mcp.mcp.*`.

A scaled-to-zero module refuses its own port: a background probe leaves it
asleep, but an actual tool call retries through the activator
(`:9000/api/<mod>/mcp`), which wakes it. Using a tool is worth waking a
module for; indexing one is not.

## Rules

Same rules as the fleet registry:

- **Potential tools.** 1200+ schemas would drown any prompt, so MCP tools
  are off the default loadout — snapped on by name (`/tools/select`,
  a toolbox, or the console's per-tool switch) like `mod.*` tools.
- **Host-only.** A sandboxed (portal) run can't call them — an MCP server
  runs host code against host state, the same trust level as the fleet
  bridge. `run_plan` blocks every non-builtin kind inside a sandbox.
- **Secrets off-tree.** A server that wants auth gets its headers from
  `~/.mod/agent/mcp/auth.json` — `{"build": {"Authorization": "Bearer …"}}`
  or `{"build": "<token>"}` — never from the module dir.

## API

    GET  /tools/mcp                 search the tool catalog (?q=, ?limit=)
    GET  /tools/mcp?servers=1       one row per server: url, ok, tools, error
    POST /tools/mcp/refresh         re-discover + re-probe (?server= wakes one)
    POST /tools/{name}/run          execute one (sign-in, like any fleet tool)

Forward actions (all public reads): `mcp_servers`, `mcp_tools` (`q`,
`limit`), `mcp_refresh` (`server`, `wake`).

## Where the pieces live

`src/tools/mcp/mod.py` is the whole thing: `Client` (stdlib-only
Streamable HTTP JSON-RPC — session ids, SSE or plain-JSON replies, one
re-handshake when a restarted server forgot the session) and `McpTools`
(discovery, the persisted index, schema conversion, calling). It is the
fourth kind in `src/tools/mod.py`'s union registry, beside `builtin`,
`custom` and `mod`. Tests: `tests/test_mcp_tools.py` (fake MCP server over
both transports).
