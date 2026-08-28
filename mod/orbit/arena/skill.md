# arena

A wasm storage and execution layer with an arena on top (arena/1.0). Store any
WebAssembly module under the hash of its bytes; run it in a browser Worker or
the node runner; if its exports match the game ABI it is a game, and agents and
models can be seated at it and measured. Rust backend, MCP over Streamable HTTP
and stdio.

The idea to hold on to: **the module is the unit, and the exports decide what
it is**. There is no plugin registry to be added to. Uploading the wasm is the
whole act of making a game.

## When to use

- See what can be played: `m arena/modules role=game`; everything stored:
  `m arena/modules`; one in full (imports, exports, signatures, memory):
  `m arena/module module=ttt`
- Run any module, no match involved:
  `m arena/run module=hello args=friend stdin="a b"` ·
  `m arena/run module=mlp entry=evaluate`
- Enter players:
  `m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'`
  `m arena/enter name=perfect kind=wasm config='{"module":"bot-ttt"}'`
- Check a player answers before seating it:
  `m arena/probe player=opus view="Legal moves: rock, paper, scissors"`
- Play: `m arena/play game=ttt players=opus,perfect seed=42`
- Assess: `m arena/leaderboard game=ttt` (per game — the ranking that means
  something), `m arena/player player=opus` (a rating per game plus the
  illegal-move rate), `m arena/match id=m3` (every turn, what was seen and said)
- Store a module you built: `m arena/put path=poker.wasm description="…"`;
  look before storing: `m arena/inspect path=poker.wasm`
- Write one: `m arena/abi` (or `m arena/abi role=player`) — the contract, at
  run time, with a worked example

## Endpoints

One port: `:50470` serves the API, the MCP endpoint, the runtime and the
console. Gateway: `/arena` (console), `/api/arena` (API).
`m arena/serve` builds if needed and starts it under pm2 (`arena-api`).

MCP: `POST /mcp`, or `arena-api --stdio` for MCP clients. 18 tools —
`arena_info`, `game_abi`, `list_modules`, `get_module`, `put_module`,
`inspect_module`, `delete_module`, `list_players`, `get_player`,
`enter_player`, `remove_player`, `run_match`, `play_move`, `record_match`,
`list_matches`, `get_match`, `leaderboard`, `plant_examples`.

Every REST route dispatches through the same tool layer, so an agent over MCP
and a browser over HTTP have exactly the same reach.

## The ABI

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
| `wasm` | `{"module": "…"}` (a module exporting `play`) | browser / node runner |
| `model` | `{"model": "…", "base"?, "key"?, "system"?}` | server — any OpenAI-compatible endpoint, OpenRouter by default |
| `agent_mod` | `{"agent"?, "model"?, "steps"?, "free"?}` | server — the fleet's `agent` module |
| `http` | `{"url": "…", "headers"?}` — posted a view, answers `{"move": "…"}` | server |
| `human` | — | the console |

The server drives anything holding a key or facing CORS; the tab only ever runs
wasm. Keys come from `~/.mod/arena/keys.json` or the environment, and configs
are redacted on every endpoint that serves them.

## Gotchas

- **Execution is not on the server.** `run_match` spawns the node runner; no
  node on PATH means matches play in the browser only.
- **The id is the content.** Re-uploading the same bytes keeps the name they
  already had — otherwise anyone could rename a game out from under the players
  entered at it. Rename by deleting and re-adding.
- **`role` is read from the binary**, not from what the uploader claims. A
  module missing one of the five game exports is stored as plain `wasm` and
  cannot be played.
- **Ratings are per game and overall, rated separately.** Overall is the front
  page; per game is the number to quote.
- **Results are reported by the runner**, which is the thing that ran the wasm.
  The transcript (seed plus every move) is what makes a disputed match
  checkable — matches are not re-executed on submission.
- State is off-tree in `~/.mod/arena/`. The committed `.wasm` files under
  `src/examples/wasm/` are the example pack; rebuild with
  `m arena/build examples=1`.

## Tests

`pytest src/tests -q` — 26 tests against a real server on a throwaway state
directory, going through HTTP, MCP and the node runner, because the thing worth
testing is that the three surfaces agree. `cargo test` in `src/arena-rs/` covers
the wasm reader, the Elo maths and reading a move out of a model's narration.
`m arena/test` is the end-to-end check against whatever is already running.
