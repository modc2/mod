# Filling a seat

A **player** is a name, a kind, and a config. Entering one is a claim that it
can answer the question every game asks: here is what your seat can see, what
do you play?

```console
$ m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'
$ m arena/enter name=perfect kind=wasm config='{"module":"bot-ttt"}'
$ m arena/play game=ttt players=opus,perfect
```

## Seven kinds

| kind | who moves | where it runs | config |
|---|---|---|---|
| `class` | a stored class defining `play` | a python process, from the runner | `{module}` |
| `wasm` | a stored module exporting `play` | the browser or the runner | `{module}` |
| `model` | any OpenAI-compatible `/chat/completions` | the server (it holds the key) | `{model, base?, key?, system?, temperature?}` |
| `agent_mod` | an agent in this fleet's `agent` module | the server | `{agent, base?, prompt?}` |
| `mcp` | a tool on any MCP server the arena can reach | the server | `{server\|module\|url, tool?, arg?}` |
| `http` | your endpoint, posted a view, answering a move | the server | `{url, field?}` |
| `human` | you, in the console | the tab | — |

`class`, `wasm` and `human` move in the [execution layer](#docs/sandbox); the
other four move on the server, because that is where the credentials are.

## Models

`config.base` points the `model` kind at anything that speaks the OpenAI chat
shape — OpenRouter by default, a local gateway or ollama unchanged. Naming
neither `base` nor `model` takes the free seat: whatever this box is serving
locally, so an arena anyone can play in does not ask for a key first.

Keys are read from `~/.mod/arena/keys.json` or the environment, never from
anything committed, and a player's config comes back **redacted** from every
endpoint that serves it.

## MCP players

An `mcp` player is a seat filled by a tool. Point it at a server by `server`
(a name from `mcp_servers.json`), by `module` (a module in this fleet), or by
`url`, and the arena works out which tool to call and which argument the view
goes in — or you name them with `tool` and `arg`.

That is how a module that was never written to play anything ends up with a
rating: it is asked a question, it answers, and the answer is graded by a game.

```console
$ m arena/fleet                       # every module of this fleet, as a seat
$ m arena/fleet module=bt             # what that one offers to be asked
$ m arena/seat module=bt tool=bt_ask  # entered as an mcp player
$ m arena/play game=nim players=bt,minimax
```

A module of the fleet is **named, not addressed**. The call goes through the
gateway rather than at a port, which is what wakes a module the activator has
put to sleep — being seated is enough to bring it back. Four details worth
knowing:

- **`tool` and `arg` are optional.** Left off, the arena reads the server's own
  `tools/list` and takes the tool that sounds like an answer (`play`, `ask`,
  `run`, `query`…) and the first required string argument. Naming them saves a
  round trip per move.
- **What goes in that argument depends on its name.** A server whose argument
  is called `view` is asking for the position and gets exactly that; anything
  else is being asked a question and gets the brief wrapped round the view, the
  same one a model gets. `raw` settles it either way.
- **`arguments`** is anything else the tool needs, sent every move.
- **`auth: true`** signs the call with this box's own key, for a module that
  will only answer a caller it can identify. It is opt-in: a token is an
  identity, and handing one to a server nobody asked for is not a default.

An `mcp` player is **not sandboxed**. A `class` or `wasm` player moves inside
the [execution layer](#docs/sandbox) with no network and a seeded PRNG; a
seated module is another module on this box doing whatever it does, and its
move is not a function of its view alone. The leaderboard prints the kind for
that reason.

## Try one before you seat it

```console
$ m arena/probe player=opus view="Legal moves: rock, paper, scissors"
{ "move": "rock", "ms": 812, "raw": "…" }
```

`probe` (`POST /play`) asks for exactly one move, outside any match. Nothing is
rated, nothing is recorded — it is the fastest way to find out that a player
answers with a paragraph where a game wanted a number.

Every entered player also answers on its own MCP endpoint at `/m/<name>/mcp`
with a `play` tool: the same question, asked by anything at all — including
another agent in the next seat.
