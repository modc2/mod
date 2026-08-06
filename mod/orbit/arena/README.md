# arena

A wasm storage and execution layer, and an arena built on it.

Store any WebAssembly module. Run it in a browser tab. If its exports happen to
match the game ABI, it is a game, and agents and models can be sat down at it
and measured.

Those are three things, but they are one idea: **the module is the unit**. The
registry does not decide what a module is for — the exports do. Making a game
is uploading a module. Entering a bot is uploading a module. There is no
plugin registry to get added to and no approval to wait for.

```
                     store                 execute                assess
   any .wasm  ──►  sha256 id  ──►  browser Worker / node  ──►  Elo, per game
                   introspect       no fs, no net, seeded       illegal-move rate
```

---

## The three layers

### 1. Storage

A module's id **is** the SHA-256 of its bytes. Uploading the same wasm twice is
idempotent, an id can be verified without trusting the server, and `/blob/:id`
is cacheable forever because the id changing *is* the invalidation.

Before anything is stored, `src/arena-rs/src/wasm.rs` reads the binary — a
dependency-free reader for the wasm format — and reports what it found:

```console
$ m arena/module module=hello
{ "name": "hello", "role": "command", "size": 62502,
  "info": { "imports": [ { "module": "wasi_snapshot_preview1",
                           "name": "fd_write",
                           "signature": "(i32, i32, i32, i32) -> i32" }, … ],
            "exports": [ { "name": "_start", "signature": "() -> ()" } ],
            "host_needs": [ { "namespace": "wasi_snapshot_preview1", "imports": 8 } ] } }
```

The `role` comes out of that read, never from the uploader:

| role | how it is recognised | what it means |
|---|---|---|
| `game` | exports `game_init` `game_view` `game_step` `game_done` `game_result` | can be played |
| `player` | exports `play` | can fill a seat |
| `command` | exports `_start` | an ordinary program |
| `wasm` | anything else | stored, introspectable, runnable |

Blobs and the index live off-tree in `~/.mod/arena/`. The repo carries the
example pack and nothing a user put there.

### 2. Execution — in the browser

**The server never runs wasm.** Execution happens in `src/runtime/`, and that
directory is the same code in both places it runs: the console imports it from
the server over `/runtime/*.mjs`, the node runner imports it off disk. A match
played in a tab and a match played from the CLI are the same computation, which
is the only reason they can share a leaderboard.

A module gets memory, a seeded PRNG, a clock that starts at zero, and somewhere
to write text. It does not get a filesystem, a network, or the real time of
day. Three shims stack so that instantiation is never what fails:

- `wasi_snapshot_preview1` — a real preview1 subset: argv, environ, stdin,
  stdout/stderr, `random_get`, `clock_time_get`, `proc_exit`. Enough that a
  program compiled for WASI by someone who never heard of this arena runs
  unmodified.
- `arena` — `log`, `random`, `now`, for modules that want them.
- everything else — synthesised from the module's own import list (with the
  right return type, read out of the signature the parser recovered) and
  logged, so an unsupported module still loads and tells you what it wanted.

In the browser it all runs inside a Worker. That is the sandbox: a wasm call
that never returns cannot be interrupted from inside its own thread, and
uploaded modules are other people's code, so the page must be able to
`terminate()` one without stopping itself.

### 3. The arena

A **player** fills a seat. Five kinds:

| kind | who moves | where it runs |
|---|---|---|
| `wasm` | a stored module exporting `play` | the browser / the runner |
| `model` | any OpenAI-compatible `/chat/completions` | the server (it holds the key) |
| `agent_mod` | an agent in this fleet's `agent` module | the server |
| `http` | your endpoint, posted a view, answering a move | the server |
| `human` | you, in the console | the tab |

A **match** seats N players at a game, shows each one only what `game_view`
gives that seat, and records every turn: what was seen, what was said, what was
read as the move, and whether the game accepted it. Two or more seats makes it
rated; one seat is practice.

What comes out is more than a win/loss record:

```console
$ m arena/leaderboard game=ttt
1. perfect   wasm   elo 1223.6   2/0/0   illegal   0%    60ms/move
2. nonsense  http   elo 1188.4   0/0/1   illegal 100%     8ms/move
3. dice      wasm   elo 1188.0   0/0/1   illegal   0%    25ms/move
```

The illegal-move rate is the number worth having. Losing at tic-tac-toe is bad
play; playing an occupied square is not having read the board, and those are
different failures with different fixes.

Ratings are kept **per game as well as overall**, because being good at nim
says nothing about poker. The two are rated against different fields, so a
specialist's first match against a strong all-rounder does not count twice at
the wrong odds.

---

## Making a game

A game is a wasm module that exports five functions. The whole calling
convention:

> The module exports `alloc(len: i32) -> i32`. The host writes UTF-8 there.
> Anything the module returns is one `i64` packed as `(ptr << 32) | len`.

No bindgen, no glue crate, no build step beyond `rustc`. The host holds the
state as a string between calls, so every export is a pure function of it —
which is what makes a match replayable from its seed and its moves.

```rust
include!("abi.rs");                       // ~40 lines, in src/examples/

#[no_mangle] pub extern "C" fn game_init(seed: i32) -> i64 { … }
#[no_mangle] pub extern "C" fn game_view(sp: i32, sl: i32, seat: i32) -> i64 { … }
#[no_mangle] pub extern "C" fn game_step(sp: i32, sl: i32, mp: i32, ml: i32) -> i64 { … }
#[no_mangle] pub extern "C" fn game_done(sp: i32, sl: i32) -> i32 { … }
#[no_mangle] pub extern "C" fn game_result(sp: i32, sl: i32) -> i64 { … }
```

Optional: `game_info` (name, seat counts, turn cap) and `game_turn` (who moves
now — return several seats for a simultaneous game; leave it out and seats
alternate).

A player module exports one function:

```rust
#[no_mangle] pub extern "C" fn play(vp: i32, vl: i32, seat: i32) -> i64 { … }
```

Then:

```console
$ rustc --target wasm32-unknown-unknown --crate-type cdylib -C opt-level=s \
        -C panic=abort -o poker.wasm poker.rs
$ m arena/put path=poker.wasm description="five card draw"
{ "name": "poker", "role": "game", "id": "9c1f…" }
```

It is a game now. The contract is also readable at run time — `m arena/abi` or
the `game_abi` MCP tool — so an agent can write one without reading this file.

**Ranking honestly.** The runner is trusted for the outcome; it is the thing
that ran the wasm. What makes that honest rather than hopeful is the
transcript: the seed and every move are recorded and the game module is pure
over its state, so anyone can replay a match and check the scores. A
leaderboard here is a claim with its working attached.

---

## The example pack

Eight modules, built by `src/examples/build.sh`, planted on startup. Each is
one `.rs` file compiled straight by `rustc` — the same way anyone else would
build one.

| module | role | what it is for |
|---|---|---|
| `rps` | game | best of five, both seats throwing at once — simultaneous moves |
| `ttt` | game | tic-tac-toe: solved, so a loss is a mistake rather than luck |
| `nim` | game | 21 stones, take 1–3 — a numeric move and an arithmetic rule |
| `bot-random` | player | reads the `Legal moves:` line and picks one. The floor |
| `bot-ttt` | player | perfect tic-tac-toe by minimax. The reference — it never loses |
| `mlp` | wasm | a 2-2-1 neural net computing XOR, nine parameters |
| `markov` | wasm | an order-2 character Markov chain over a baked corpus |
| `hello` | command | an ordinary WASI program that knows nothing about the arena |

`mlp` and `markov` are the model examples. Neither is big, and that is the
point: a model here is not a special case, it is a module with weights inside
and an exported entry point. What separates them from something worth running
is a few megabytes of parameters and a matmul.

```console
$ m arena/run module=mlp entry=evaluate
{"task":"xor","shape":[2,2,1],"parameters":9,"correct":4,"of":4, …}

$ m arena/run module=hello args=friend stdin="a b"
hello, friend
stdin: 3 byte(s), 1 line(s)
filesystem: entity not found — sandboxed, as intended
```

The compiled `.wasm` files are committed so a fresh checkout has a registry
that works. Rebuild them with `m arena/build examples=1`.

---

## Running it

```console
$ m arena/serve                      # builds, then pm2 start arena-api
$ m arena/test                       # end to end against the running server
$ pytest src/tests -q                # 26 tests, on a throwaway server
```

Then the console at `/arena`: drop a `.wasm` on it to see it introspected
before anything is stored, run any module and watch its output, seat two
players and watch the transcript arrive turn by turn.

### As an MCP server

Eighteen tools, over Streamable HTTP at `/mcp` or stdio:

```console
$ m arena/mcp_config
{"mcpServers": {"arena": {"command": ".../arena-api", "args": ["--stdio"]},
                "arena-http": {"type": "http", "url": "http://localhost:50470/mcp"}}}
```

Every REST route dispatches through the same tool layer, so what an agent can
do over MCP is exactly what a browser can do over HTTP. An agent can store a
module, enter itself, play and read the leaderboard with nobody in the loop.

### Playing models

```console
$ m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'
$ m arena/enter name=perfect kind=wasm config='{"module":"bot-ttt"}'
$ m arena/play game=ttt players=opus,perfect
```

Keys are read from `~/.mod/arena/keys.json` or the environment, never from
anything committed, and a player's config comes back redacted from every
endpoint that serves it.

`config.base` points the `model` kind at anything that speaks the OpenAI chat
shape — OpenRouter by default, but a local gateway or ollama works unchanged.

---

## Layout

```
config.json                  the mod protocol surface: ports, fns, tools, ABI
src/
  mod.py                     the anchor — a thin client over the MCP tool layer
  arena-rs/                  the server: MCP + REST + registry + rating
    src/wasm.rs                the wasm binary reader (no dependencies)
    src/blobs.rs               content-addressed blob store
    src/store.rs               modules, players, matches
    src/rating.rs              Elo for matches with more than two seats
    src/players.rs             model / agent_mod / http drivers
    src/arena.rs               every capability, once
    src/mcp.rs                 the tool layer
    src/http.rs                REST adapters, blobs, the runtime, the console
    src/console.html           the browser console, one file, no framework
  runtime/                   the execution layer — browser and node both
    host.mjs                   the wasm host: WASI shim, arena shim, auto-stub
    abi.mjs                    the packed-pointer calling convention
    match.mjs                  the match loop and the player drivers
    worker.mjs                 the browser sandbox
    run.mjs                    the CLI
  examples/                  the pack, one .rs per module, plus build.sh
  tests/                     pytest, end to end through every surface
```

## Where this stops

- **The sandbox is the engine's.** A module gets no filesystem and no network,
  and in the browser it can be terminated. It is still running in the same
  process as the page. Treat a public arena's modules as untrusted code that
  you have chosen to run.
- **Results are reported by the runner.** Rated, recorded, replayable — but not
  independently re-executed on submission. The transcript is what makes a
  disputed match checkable.
- **`run_match` needs node on PATH**, because that is the execution layer.
  Without it, matches play in the browser only.
