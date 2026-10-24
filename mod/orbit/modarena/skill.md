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

- **Make a game out of a class**: `m modarena/template role=game > mygame.py`,
  edit it, `m modarena/upload path=mygame.py`. It is playable at that point —
  nothing to register, nothing to compile. `m modarena/abi lang=class` is the
  whole contract; `m modarena/template role=player` is the bot version.
- See what can be played: `m modarena/modules role=game`; everything stored:
  `m modarena/modules`; just the classes: `m modarena/classes`; one in full
  (methods and source, or imports/exports/memory): `m modarena/module module=ttt`;
  the source alone: `m modarena/source module=connect4`
- Run any module, no match involved:
  `m modarena/run module=hello args=friend stdin="a b"` ·
  `m modarena/run module=mlp entry=evaluate`
- Enter players:
  `m modarena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'`
  `m modarena/enter name=perfect kind=wasm config='{"module":"bot-ttt"}'`
  `m modarena/enter name=centre kind=class config='{"module":"center"}'`
- Check a player answers before seating it:
  `m modarena/probe player=opus view="Legal moves: rock, paper, scissors"`
- Play: `m modarena/play game=ttt players=opus,perfect seed=42`
- Assess: `m modarena/leaderboard game=ttt` (per game — the ranking that means
  something), `m modarena/player player=opus` (a rating per game plus the
  illegal-move rate), `m modarena/match id=m3` (every turn, what was seen and said)
- Store something you built: `m modarena/upload path=poker.py` for a class,
  `m modarena/put path=poker.wasm` for a binary (`put` takes either); look before
  storing: `m modarena/inspect path=poker.py`
- Write one: `m modarena/abi lang=class` (or `role=player`, or `lang=wasm`) — the
  contract, at run time, with the template it hands out

## Endpoints

One port: `:50800` serves the API, the MCP endpoint, the runtime and the
console. Gateway: `/arena` (console), `/api/modarena` (API).
`m modarena/serve` builds if needed and starts it under pm2 (`modarena-api`).

MCP: `POST /mcp`, or `modarena-api --stdio` for MCP clients. 19 tools —
`arena_info`, `game_abi`, `list_modules`, `get_module`, `put_module`,
`put_class`, `inspect_module`, `delete_module`, `list_players`, `get_player`,
`enter_player`, `remove_player`, `run_match`, `play_move`, `record_match`,
`list_matches`, `get_match`, `leaderboard`, `plant_examples`.

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
in the card. `m modarena/template role=game|player` prints a working starting
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
| `http` | `{"url": "…", "headers"?}` — posted a view, answers `{"move": "…"}` | server |
| `human` | — | the console |

The server drives anything holding a key or facing CORS; the tab only ever runs
wasm. Keys come from `~/.mod/modarena/keys.json` or the environment, and configs
are redacted on every endpoint that serves them.

## Gotchas

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
- **Ratings are per game and overall, rated separately.** Overall is the front
  page; per game is the number to quote.
- **Results are reported by the runner**, which is the thing that ran the wasm.
  The transcript (seed plus every move) is what makes a disputed match
  checkable — matches are not re-executed on submission.
- State is off-tree in `~/.mod/modarena/`. The example pack is
  `src/examples/wasm/` (committed binaries, rebuilt with
  `m modarena/build examples=1`) plus `src/examples/classes/` (five .py files,
  nothing to build). Both are planted at startup and by `m modarena/examples`.

## Tests

`pytest src/tests -q` — 39 tests against a real server on a throwaway state
directory, going through HTTP, MCP and the node runner, because the thing worth
testing is that the three surfaces agree. Thirteen of them are the class layer:
a class plays a class, a class player sits at a wasm game, the sandbox refuses
the filesystem and the network, a runaway is killed rather than hanging the
match, and the template the arena hands out is itself a valid game.
`cargo test` in `src/modarena-rs/` covers both readers, the Elo maths and reading
a move out of a model's narration.
`m modarena/test` is the end-to-end check against whatever is already running.
