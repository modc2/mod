# mod2mcp · any mod, compiled to an MCP contract

Turn any mod's `Mod` class into an MCP server the way you would program a
smart contract. The mod's public methods are the contract's functions:
mod2mcp **compiles** them into a deterministic ABI, **deploys** that interface
onto a gateway, and **executes** calls against it — with view/write mutability
enforced at the door.

The analogy is load-bearing, not decoration:

| smart contract        | mod2mcp                                                        |
|-----------------------|----------------------------------------------------------------|
| source code           | the mod's `Mod` class                                          |
| compile → ABI         | `abi(mod)` — typed functions from signatures, docstrings as natspec |
| function selector     | `sha256("name(type,...)")[:8]` per function                    |
| contract address      | `0x` + hash of the interface (names, input types, mutability)  |
| `view` vs non-view    | name-based mutability: `get_*`/`list_*`/`status`… = view, rest = write |
| deploy                | `deploy(mod)` → registered at `/mcp/<mod>`, persisted in a registry |
| revert                | denied / readonly-violating calls raise **before** the mod is touched |
| no constructor in ABI | lifecycle (`serve`, `kill`, `install`, `build`, `test`) is never exposed |

Same interface → same address, every time. Changing a parameter type moves
the address; rewording a docstring does not.

## Quick start

```bash
m mod2mcp/mods                               # what can be compiled
m mod2mcp/abi news                           # compile — reads are free, no deploy needed
m mod2mcp/serve                              # gateway on :51550 (pm2 `mod2mcp`)
m mod2mcp/deploy news                        # register at /mcp/news
m mod2mcp/deploy polymarket readonly=1       # views only; writes revert
m mod2mcp/call news search query=bittensor
m mod2mcp/deployments | m mod2mcp/undeploy news
m mod2mcp/status | m mod2mcp/kill | m mod2mcp/test
```

## Connect an agent

```bash
# the control plane (compile/deploy/call from inside a conversation)
claude mcp add --transport http mod2mcp http://localhost:51550/mcp

# one deployed mod as its own MCP server
claude mcp add --transport http news http://localhost:51550/mcp/news

# or skip the gateway entirely — stdio, compiled on the fly (cwd = orbit/mod2mcp)
claude mcp add news -- python3 -m mod2mcp.bridge news
python3 -m mod2mcp.bridge polymarket --readonly
python3 -m mod2mcp.bridge bt --fns search,read,status
```

Tools are named `<mod>_<fn>` and carry `readOnlyHint`/`destructiveHint`
annotations derived from mutability, so a client can decide what is safe to
call without reading prose.

## Interface selection

Which functions make the ABI, in priority order:

1. `fns=` passed to `abi`/`deploy`/the bridge — the explicit interface;
2. else the mod's own `config.json` `fns` list — its declared surface;
3. else every public method.

Minus, always: `exclude=`, anything starting with `_`, and the lifecycle
denylist. `readonly=1` then drops every write function at compile time.

## HTTP surface (`:51550`)

```
GET  /health               liveness + deployed contracts
GET  /mods                 compilable mods
GET  /abi/<mod>            compile (no deploy needed)
GET  /deployments          the registry
POST /deploy               {"mod", "fns"?, "exclude"?, "readonly"?}
POST /undeploy             {"mod"}
POST /call/<mod>/<fn>      body = kwargs; 403 on readonly-write, 404 undeployed
POST /mcp                  control-plane MCP (mod2mcp_abi, mod2mcp_deploy, …)
POST /mcp/<mod>            the deployed mod's own MCP endpoint
```

## State & layout

State: `~/.mod/mod2mcp/deployments.json` — the deploy registry; a gateway
restart recompiles and redeploys everything in it. `MOD2MCP_HOME` overrides
(tests use a tmpdir).

```
mod.py                 anchor — CLI surface, pm2 serve/kill
mod2mcp/compiler.py    Mod class → ABI + MCP tools (pure, no I/O)
mod2mcp/runtime.py     loads mods via core, registry, call guards
mod2mcp/rpc.py         one generic MCP JSON-RPC dispatcher
mod2mcp/server.py      the HTTP gateway
mod2mcp/bridge.py      stdio MCP for one mod (stdout kept clean for JSON-RPC)
test/                  offline — compiler determinism, rpc, guards, registry
```

Stdlib only, no keys, loopback by default. Calls run in-process with the
target mod's own credentials and side effects — deploy write-capable
contracts deliberately, and prefer `readonly=1` when an agent only needs to
look.
