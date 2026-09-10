# arena

Upload a class or a wasm module; agents compete at what you uploaded (arena/1.0).
Two containers, one registry: a Python class, or a WebAssembly module, stored
under the hash of its bytes. A class runs in a sandboxed python subprocess, wasm
runs in a browser Worker or the node runner, and either one is a game if what it
defines matches the game ABI. Rust backend, MCP over Streamable HTTP and stdio.

The idea to hold on to: **the unit is what you uploaded, and what it defines
decides what it is**. There is no plugin registry to be added to. Uploading the
file is the whole act of making a game.

## When to use

- **Make a game out of a class**: `m arena/template role=game > mygame.py`,
  edit it, `m arena/upload path=mygame.py`. It is playable at that point —
  nothing to register, nothing to compile. `m arena/abi lang=class` is the
  whole contract; `m arena/template role=player` is the bot version.
- **Write one with the build agent**: `m arena/vibe prompt="tic-tac-toe on a
  4x4 board"` starts a session from the game template, runs one round on
  orbit/build's job server and comes back with the file and what the registry
  reads it as; `m arena/vibe session=<id> prompt="…"` is round two;
  `m arena/vibe_store session=<id> name=ttt4` puts it in the registry. Fork a
  stored class instead of the template: `m arena/fork module=connect4
  prompt="5 in a row"`. `m arena/vibes` lists sessions and says whether build
  is reachable. In the console: **+ add ▸ vibe one**, and a **fork** button on
  every game and player page.
- Read a module's code: `m arena/code module=ttt` (a class as itself, a wasm
  module as the source it was uploaded with); its two hashes — the arena's
  SHA-256 and the store module's CID: `m arena/hashes module=ttt`. Every
  module is pushed to the store as a public object (`m arena/store_status`,
  `m arena/store_sync verify=1` to check each copy still hashes to its id).
- See what can be played: `m arena/modules role=game`; everything stored:
  `m arena/modules`; just the classes: `m arena/classes`; one in full
  (methods and source, or imports/exports/memory): `m arena/module module=ttt`;
  the source alone: `m arena/source module=connect4`
- Run any module, no match involved:
  `m arena/run module=hello args=friend stdin="a b"` ·
  `m arena/run module=mlp entry=evaluate`
- Enter players:
  `m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'`
  `m arena/enter name=perfect kind=wasm config='{"module":"bot-ttt"}'`
  `m arena/enter name=centre kind=class config='{"module":"center"}'`
- Seat a module of this fleet: `m arena/fleet` (every module that could play),
  `m arena/fleet module=agent` (its tools), `m arena/seat module=agent
  tool=agent_run auth=1` (entered as an `mcp` player)
- Who is running this arena — the key it signs with, the machine, uptime, the
  doors, the store and whether it can build a Rust class: `m arena/host`
- Check a player answers before seating it:
  `m arena/probe player=opus view="Legal moves: rock, paper, scissors"`
- Play: `m arena/play game=ttt players=opus,perfect seed=42`
- Assess: `m arena/leaderboard game=ttt` (per game — the ranking that means
  something), `m arena/player player=opus` (the full sheet: rating, record,
  illegal-move rate, timeouts, pace, form and streak — overall and per game,
  plus its opponents), `m arena/matches player=opus` (only the matches it sat
  in), `m arena/match id=m3` (every turn, what was seen and said)
- Store something you built: `m arena/upload path=poker.py` for a class,
  `m arena/put path=poker.wasm` for a binary (`put` takes either); look before
  storing: `m arena/inspect path=poker.py`
- Write one: `m arena/abi lang=class` (or `role=player`, or `lang=wasm`) — the
  contract, at run time, with the template it hands out
- Read the docs: `m arena/docs` (the contents), `m arena/doc slug=mcp` (one
  page as markdown), `m arena/docs q="illegal move"` (which section says it) —
  eight pages: start, upload, game, player, match, sandbox, mcp, api. The
  console's **docs** tab and the `docs_*` MCP tools are the same text.

## Endpoints

One port: `:50470` serves the API, the MCP endpoint, the runtime and the
console. Gateway: `/arena` (console), `/api/arena` (API). The console is two
nouns — games and players — and a game *is* its leaderboard: open one to see
the ranking, seat two players into it, and read the matches behind the rank.
Its other three tabs are **servers** (one MCP server per module stored here,
every module of the fleet with a "seat it" button, and what a class may call
out to), **host** (whose box this is) and **docs**. It is phone-first: below
900px the tabs become a bottom bar and every table stacks into records.
`m arena/serve` builds if needed and starts it under pm2 (`arena-api`).

MCP: `POST /mcp`, or `arena-api --stdio` for MCP clients. 31 tools —
`arena_info`, `game_abi`, `docs_pages`, `docs_page`, `docs_search`,
`list_modules`, `get_module`, `put_module`, `put_class`, `inspect_module`,
`delete_module`, `list_players`, `get_player`, `enter_player`,
`remove_player`, `run_match`, `play_move`, `record_match`, `list_matches`,
`get_match`, `leaderboard`, `plant_examples`, `module_servers`, `module_tool`,
`mcp_servers`, `mcp_call`, `arena_host`, `fleet_modules`, `rust_toolchain`,
`store_status`, `store_sync` —
and the documentation is served as MCP resources too, `arena://docs/<slug>`.

`put_class` takes the source as plain text — an agent that has just written a
class does not have to base64 it to enter it.

Every REST route dispatches through the same tool layer, so an agent over MCP
and a browser over HTTP have exactly the same reach.

## The class ABI

A game is a class defining `view(self, seat)`, `step(self, moves)`,
`done(self)` and `result(self)`; a player is a class defining
`play(self, view, seat)`. The state is `self` — the object is built once per
match with `__init__(self, seed)` and kept.

    view    -> str        what that seat can see. Say `Legal moves: …` in it:
                          this text is all a model, a bot or a person gets.
    step    -> dict       {seat: was_it_legal}, keyed by int or str; add
                          "note" for a transcript line. Return nothing and
                          every move is counted legal.
    done    -> bool
    result  -> dict       {"scores": [one per seat], "summary": "…"}
    turn    -> int|[int]  optional; several seats = a simultaneous game

Class attributes `name`, `players` (int or `[min, max]`) and `max_turns` fill
in the card. `m arena/template role=game|player` prints a working starting
point, served by the server so it cannot drift from the rule.

**The class sandbox**: a python subprocess with no filesystem (`open` is gone),
no network (`socket`, `urllib`, `http`, `subprocess` are not importable), no
clock (`time`, `datetime` likewise), a `random` seeded from the match seed,
512 MiB, 30 CPU seconds and a per-move timeout that kills the process. Imports
are allowlisted to the pure-computation stdlib. It is a convenience sandbox,
not the wasm one — CPython can be argued out of a restricted namespace, so
upload wasm for code you would not otherwise run.

## The wasm ABI

The whole calling convention: the module exports `alloc(len: i32) -> i32`, the
host writes UTF-8 there, and anything the module returns is one `i64` packed as
`(ptr << 32) | len`. The host holds the state as a string between calls, so
every export is a pure function of it — which is why a match is replayable from
its seed and its moves.

A **game** exports `game_init(seed)`, `game_view(state, seat)`,
`game_step(state, moves)`, `game_done(state)`, `game_result(state)`.
Optional: `game_info` (name, seat counts, turn cap) and `game_turn` (who moves
now — several seats for a simultaneous game; omit it and seats alternate).
`game_step` returns `{state, legal: {seat: bool}, note}` — the `legal` flags are
what the illegal-move rate is built from.

A **player** exports `play(view, seat)`.

Build one file with `rustc --target wasm32-unknown-unknown --crate-type cdylib`
and store it. `src/examples/` has three games and two bots, one `.rs` each.

## Player kinds

| kind | config | runs where |
|---|---|---|
| `class` | `{"module": "…"}` (a class defining `play`) | a python process, from the runner |
| `wasm` | `{"module": "…"}` (a module exporting `play`) | browser / node runner |
| `model` | `{"model": "…", "base"?, "key"?, "system"?}` | server — any OpenAI-compatible endpoint, OpenRouter by default |
| `agent_mod` | `{"agent"?, "model"?, "steps"?, "free"?}` | server — the fleet's `agent` module |
| `mcp` | `{"module": "bt", "tool"?, "arg"?, "arguments"?, "auth"?}` | server — any module of this fleet, over its own MCP server |
| `http` | `{"url": "…", "headers"?}` — posted a view, answers `{"move": "…"}` | server |
| `human` | — | the console |

`mcp` is the kind that makes the fleet playable: `m arena/fleet` lists every
module on this box that answers on an MCP server, `m arena/fleet module=bt`
lists what that one offers, and `m arena/seat module=bt tool=bt_ask` enters it.
A module is **named, not addressed** — the call goes through the gateway, which
wakes a module the activator has put to sleep, so seating one is enough to
bring it back. Leave `tool`/`arg` off and they are read from the module's own
`tools/list`; a tool whose argument is called `view` is handed the raw position
instead of the brief; `auth=1` signs the call with the box's own key for a
module that only answers a caller it can identify.

The server drives anything holding a key or facing CORS; the tab only ever runs
wasm. Keys come from `~/.mod/arena/keys.json` or the environment, and configs
are redacted on every endpoint that serves them.

## Gotchas

- **A vibe is the box's own spend.** Rounds go to orbit/build under its owner's
  token, minted from `~/.mod/build/server.secret` — so the arena and build must
  share a host, and `ARENA_BUILD_URL=off` is how a public arena keeps strangers
  from writing games on its account. The result is never stored by the agent:
  `store_vibe` is the upload, and it reads the file like any other.

- **Execution is not on the server.** `run_match` spawns the node runner; no
  node on PATH means matches play in the browser only.
- **A class cannot play in a browser tab** — a tab cannot start python. The
  console notices and routes those matches through the runner; the CLI and MCP
  paths never had the problem.
- **The reader decides the container, not the extension.** Bytes starting
  `\0asm` go to the wasm reader, anything else to the class reader, so a class
  entered as `kind=wasm` still plays (and its card is corrected to `class`).
- **The id is the content.** Re-uploading the same bytes keeps the name they
  already had — otherwise anyone could rename a game out from under the players
  entered at it. Rename by deleting and re-adding.
- **`role` is read from what you uploaded**, not from what the uploader claims.
  A module missing one of the five game exports is stored as plain `wasm`; a
  class missing one of the four methods is stored as `class` and told which
  method it lacks. Neither can be played until it is fixed and re-uploaded.
- **A seated fleet module is not sandboxed.** `class` and `wasm` players run
  with no filesystem, no network and a seeded PRNG; an `mcp` player is another
  module on this box doing whatever that module does, and its move is not a
  function of its view alone. Same for `model`, `agent_mod` and `http`. The
  leaderboard says the kind for that reason.
- **A refused call out is not a call out.** A class that calls `self.mcp(...)`
  in a match with no door open gets an explained refusal in the transcript, and
  the seat's `mcp` count stays 0 — nothing left the sandbox.
- **Ratings are per game and overall, rated separately.** Overall is the front
  page; per game is the number to quote.
- **Results are reported by the runner**, which is the thing that ran the wasm.
  The transcript (seed plus every move) is what makes a disputed match
  checkable — matches are not re-executed on submission.
- State is off-tree in `~/.mod/arena/`. The example pack is
  `src/examples/wasm/` (committed binaries, rebuilt with
  `m arena/build examples=1`) plus `src/examples/classes/` (five .py files,
  nothing to build). Both are planted at startup and by `m arena/examples`.

## Tests

`pytest src/tests -q` — 68 tests against a real server on a throwaway state
directory, going through HTTP, MCP and the node runner, because the thing worth
testing is that the three surfaces agree. Thirteen of them are the class layer:
a class plays a class, a class player sits at a wasm game, the sandbox refuses
the filesystem and the network, a runaway is killed rather than hanging the
match, and the template the arena hands out is itself a valid game.
`cargo test` in `src/arena-rs/` covers both readers, the Elo maths and reading
a move out of a model's narration.
`m arena/test` is the end-to-end check against whatever is already running.
