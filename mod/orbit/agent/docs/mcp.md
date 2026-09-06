# MCP

The module's API, spoken as Model Context Protocol. Twenty tools, seven
resources, and the prompt library — over one endpoint.

```
POST https://modc2.com/api/agent/mcp        Streamable HTTP (JSON-RPC 2.0)
python3 src/mcp.py                          stdio
```

```bash
claude mcp add --transport http agent https://modc2.com/api/agent/mcp \
    --header "Authorization: Bearer $MOD_TOKEN"

# or, on the host itself
claude mcp add agent -- python3 /root/mod/mod/orbit/agent/src/mcp.py
```

`GET /mcp/schema` returns the tool list and the connection details as plain
JSON, which is what the console's TOOLS tab renders.

## Interlaced, not bolted on

`src/mcp.py` holds the tools and the JSON-RPC dispatch. It holds no logic:
every handler is a call into the same function the REST route calls, and
through it into `Mod.forward()`.

    tools/call agent_run   →  api.run_agent(RunRequest(…))  →  forward('run')
    tools/call agent_tool_run → api.run_tool(…)             →  forward('tool_run')
    tools/call agent_recall   →                                forward('recall')

That is the whole design, and it buys three things a parallel implementation
would have to keep in step by hand:

- **One permission gate.** `forward()` runs `require_allowed(key, action)` before
  anything, so an MCP client is exactly as privileged as an HTTP client holding
  the same token — no more, no less.
- **One task registry.** A run started over MCP is minted in the same in-memory
  registry the console polls, so it appears in the TASKS tab, keeps running
  after the MCP call returns, and can be followed with `agent_task`. That is why
  `_api()` resolves the *already-loaded* API module out of `sys.modules` instead
  of importing `src.api.api`: uvicorn loads it as top-level `api`, and a second
  import would build a second `Mod` with a second registry.
- **One write sandbox.** `agent_tool_run` goes through the API's own handler,
  which is where the path check lives, so a non-owner calling `write` lands in
  their own directory here the same way they do over HTTP.

## Auth

Reads are open — the board, the registry, the library, the fleet's audit
surface. Running the loop, writing a fact, snapping a toolbox, calling a shell
tool and reading a vault want a signed protocol-auth token:

```
Authorization: Bearer <token>          # the whole connection
{"name": "agent_run", "arguments": {"key": "<token>", …}}   # one call
```

A per-call `key` beats the header. `agent_whoami` says which identity the
server actually resolved, and what credit balance a billed run would draw on.

One subtlety worth stating, because it is the reason the guard exists at all:
`forward()` reads a key of `None` as *the process itself*, which is correct for
a CLI call on the host and wrong for anything that arrived over a network. So
the HTTP transport refuses the writing tools outright when the connection
carried no token, while the stdio transport — a process someone started on the
host — keeps the CLI reading. Refusals come back as a normal JSON-RPC result
with `isError: true`, so the model reads the reason and adapts instead of the
connection dying under it.

## The tools

| tool | what it does |
| --- | --- |
| `agent_run` | run the agent loop; `wait`/`timeout` bound the call |
| `agent_task` | follow a run that outlived the call, or list recent ones |
| `agent_agents` | the personas, with owner, model, toolbox, memory, prompt |
| `agent_build` | write a new agent, or change one you wrote |
| `agent_parts` | the live agent box |
| `agent_tools` | the registry: shipped, custom, and the fleet |
| `agent_toolbox` | the bundles; snap one on, pin an exact list, save a box |
| `agent_tool_run` | call one tool with no model in the loop |
| `agent_recall` | facts, scored against a query |
| `agent_retrieve` | every memory layer at once, ranked and comparable |
| `agent_remember` | write a durable fact |
| `agent_memory` | the layers: state, episodes, dialogue, facts, modules, notes |
| `agent_library` | prompts, tool documents, memory notes, agents |
| `agent_discover` | scan GitHub / npm / the MCP registry / Glama for tools |
| `agent_install` | keep one, as a document attachable to a run |
| `agent_arena` | the board, four ways: agent, model, task, match log |
| `agent_arena_run` | play a match |
| `agent_modules` | the fleet's audit surface: list, tree, file |
| `agent_vault` | the caller's own key-value vaults |
| `agent_whoami` | who this token is, and what it may spend |

### Long runs

An agent run takes minutes and an MCP client will not wait that long. `agent_run`
therefore blocks for `timeout` seconds (default 120, max 900) and then hands
back the `task_id` with the trace so far:

```json
{"status": "running", "task_id": "c1097b562fbf", "steps": 4,
 "hint": "the run is still going — poll agent_task with this task_id"}
```

The run itself keeps going server-side. `wait: false` returns the id
immediately. Traces are trimmed to keep a 25-step run readable; `full: true`
returns every step whole.

## Resources and prompts

Resources are the live module read as documents: `agent://parts`,
`agent://tools`, `agent://agents`, `agent://arena/board`, plus `agent://docs/mcp`,
`agent://docs/uploads` and `agent://docs/arena`.

Prompts are the module's own prompt library, re-served under MCP — so a
client's slash-command menu is the same shelf the console shows. Each takes an
optional `task` argument appended to the prompt.

## Discovery

`config.json` declares `endpoints.mcp` and `urls.mcp`, which is what the fleet's
own MCP hub (`orbit/mcp`) reads when it aggregates the host's servers. `m
agent/mcp` prints the same connection block from the CLI, and `/health` and
`/status` carry it too.
