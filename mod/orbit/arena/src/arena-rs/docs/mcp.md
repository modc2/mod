# The MCP server

The arena is an MCP server, and so is every module stored in it. Everything on
this page is JSON-RPC 2.0 — over Streamable HTTP, where one `POST` gets one
JSON reply, or over stdio, one message per line. There is no session header,
no SSE stream and nothing to keep open: a client that can send one HTTP request
can drive all of it.

The server is `arena`, protocol `2025-06-18`. It offers **tools** (the
thirty-five below), **resources** (these pages) and no prompts of its own —
the per-module servers have those. The full tool reference at the foot of this
page is generated from the server's own tool table every time the page is
read, so it cannot fall behind the code.

## Connect

```json
{"mcpServers": {
  "arena":      {"command": "…/src/arena-rs/target/release/arena-api", "args": ["--stdio"]},
  "arena-http": {"type": "http", "url": "http://localhost:50470/mcp"}
}}
```

`m arena/mcp_config` prints exactly that, with the paths of this box filled
in. Use the stdio entry for a client on this machine (Claude Code, Cursor, an
agent you run yourself) and the HTTP entry for anything else. Behind the fleet
gateway the same endpoint is `/api/arena/mcp`, which is the address the other
modules of this fleet use — and the one that wakes the arena if it is asleep.

```console
$ curl -s localhost:50470/mcp -H 'content-type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools|length'
31
```

`GET /tools` is the same list without the JSON-RPC envelope, for anything that
would rather read it over REST, and `m arena/mcp_call tool=<name> k=v …` calls
one tool from the shell.

## The protocol

| method | what comes back |
|---|---|
| `initialize` | `protocolVersion`, `capabilities` (`tools`, `resources`), `serverInfo` and `instructions` — a short text that says where the docs are and the one loop this exists for, so a client that reads nothing else still knows |
| `ping` | `{}` |
| `tools/list` | every tool with its `description` and `inputSchema` |
| `tools/call` | `content` (one text block holding the result as JSON), `structuredContent` (the same result as an object) and `isError` |
| `resources/list` | the eight documentation pages, `arena://docs/<slug>`, `text/markdown` |
| `resources/read` | one page's markdown |
| `resources/templates/list` | `arena://docs/{slug}` |
| `prompts/list` | empty here — a module's own server is where the prompts are |

A tool that fails answers with `isError: true` and the reason in `content`,
never a JSON-RPC error: the call reached the server and this is its answer.
JSON-RPC errors are for the protocol itself — an unknown method is `-32601`, a
resource that does not exist is `-32602`, and on stdio a line that is not JSON
is `-32700`. A message with no `id` is a notification and gets no reply.

## One definition, two doors

Every REST route dispatches through the same tool layer the MCP endpoint does.
A capability is defined exactly once, so what an agent can do over MCP is
exactly what a browser can do over HTTP — never a subset, and never stale.
`GET /leaderboard?game=nim` and `tools/call leaderboard {game: "nim"}` are one
function; the console's upload box and `put_class` are one function.

## The tools

| group | tools |
|---|---|
| the arena | `arena_info`, `game_abi`, `rust_toolchain` |
| the docs | `docs_pages`, `docs_page`, `docs_search` |
| modules | `list_modules`, `get_module`, `inspect_module`, `put_module`, `put_class`, `delete_module` |
| players | `list_players`, `get_player`, `enter_player`, `remove_player` |
| playing | `run_match`, `play_move`, `record_match` |
| results | `list_matches`, `get_match`, `leaderboard` |
| the modules' own servers | `module_tool` |
| the door out | `mcp_servers`, `mcp_call` |
| the fleet | `fleet_modules` |
| the store | `store_status`, `store_sync`, `plant_examples` |
| the vibe desk | `vibe`, `fork_module`, `get_vibe`, `list_vibes`, `store_vibe`, `cancel_vibe` |

The loop this was built for needs nobody in it: read `game_abi`, write a class,
`put_class` it, `enter_player` yourself, `run_match`, read `leaderboard`.

## A session, end to end

One envelope, then the rest in shorthand — `m arena/mcp_call` sends exactly
this envelope for you.

```console
$ curl -s localhost:50470/mcp -H 'content-type: application/json' -d '{
    "jsonrpc":"2.0","id":2,"method":"tools/call",
    "params":{"name":"put_class","arguments":{"name":"corner",
      "source":"class Corner:\n    def play(self, view, seat):\n        return \"0\""}}}' \
  | jq '.result.structuredContent | {id, role, lang}'
{"id": "…", "role": "player", "lang": "python"}

$ m arena/mcp_call tool=enter_player name=corner kind=class config='{"module":"corner"}'
$ m arena/mcp_call tool=run_match game=ttt players=corner,minimax
$ m arena/mcp_call tool=leaderboard game=ttt
```

`put_class` read the role off the source (a `play` method is a player);
`enter_player` gave it a seat; `run_match` executed the match in the node
runner and rated it; `leaderboard` is where it landed — low, since a player
that always sends `0` is illegal from its second move, and the illegal-move
rate is what the rating is for. Four calls, no console.

## Resources

The pages you are reading are also MCP resources, `arena://docs/<slug>`.
`resources/list` enumerates them, `resources/read` returns the markdown, and
`resources/templates/list` gives the pattern — so a client that prefers to
attach documentation rather than call a tool for it can do that instead.
`docs_pages` / `docs_page` / `docs_search` are the same text through the tool
door, and `GET /docs/<slug>?format=md` is the same text through REST.

## One server per module

Every stored module answers on its own endpoint, `/m/<name>/mcp`, and
introduces itself at `initialize` as `arena/<name>`. What it offers is decided
by what it is:

- **a game** — `open` sits you down (a table id, the opening view of every
  seat, whose move it is), then `view`, `move` and `state` play it a turn at a
  time; `leaderboard` is the Elo kept for this game alone. A table is its seed
  and its moves and nothing else, so it survives a restart and replays
  identically anywhere. The prompt `play` is the whole instruction for a model
  to sit down and play it out.
- **a player** — `play` hands it a view and gets a move back, which is exactly
  the question the arena asks it in a match; `record` is how it has done. The
  prompt `assess` asks a model to work out how it plays by trying it.
- **anything else** — `run` executes it once and reports what it did.

Everything, whatever it is, also answers `about` and `source`, and exposes one
resource, `arena://<name>/source` — the source of a class, the export list of
a wasm binary.

```console
$ curl -s localhost:50470/m/nim/mcp -H 'content-type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"open","arguments":{"seed":7}}}'
$ curl -s localhost:50470/m/nim/mcp -H 'content-type: application/json' \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"move","arguments":{"table":"…","move":"2 1"}}}'
```

`GET /m/<name>` is one module's card, `GET /m/<name>/tools` its tool list —
one server per stored module, no index to learn first. From this server,
`module_tool` calls a module's tool without opening a second connection: `module_tool
module=nim tool=open` sits you down, `tool=move` plays. Point a client at one
of these and a model can sit down at a game with none of this console in the
way.

## A server in a seat

The traffic runs both ways. Any MCP server can be entered as a player:
`enter_player kind=mcp` with `config.server` (a name from `mcp_servers`),
`config.module` (any module of this fleet, addressed through the gateway —
which wakes one that is asleep) or `config.url`, plus `config.tool` for which
tool plays. Each move, the arena calls that tool with the view and reads a
move out of whatever came back. `fleet_modules` lists what can be seated, and
`fleet_modules module=<name>` says which tools it has — which is how you find
out which one plays. See [filling a seat](#docs/player).

## Calling out

And a class running here can call an MCP server mid-move — `self.mcp(server,
tool, args)` in Python, `arena::mcp(server, tool, args_json)` in Rust. It
names a server, never a URL; `mcp_servers` lists the names, configured in
`~/.mod/arena/mcp_servers.json`, and `mcp_call` makes one of those calls
directly so you can try one before writing a class that depends on it. The
sandbox never grows a socket: this server makes the call, and every call is
counted onto the seat that made it. A match allows it only when `run_match`
is given `mcp` — the default is none, which is the only setting under which
a move is a function of its view alone. See [the door out](#docs/sandbox).

## stdio

```console
$ arena-api --stdio
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
```

Newline-delimited JSON-RPC on stdin and stdout: same tools, same handlers,
one process per client, no port. It is the same binary that serves the
console, reading the same state directory (`~/.mod/arena`, or `ARENA_STATE`),
so a stdio client sees the registry the console does.
