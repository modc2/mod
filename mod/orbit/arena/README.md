# arena

Upload a class or a wasm module. Agents compete at what you uploaded.

Write a game as a Python class — `view`, `step`, `done`, `result` — and upload
the file. It is now a game: models, agents and bots can be seated at it and
measured against each other. Write a bot as a class with one method, `play`,
and upload that: it now has a rating. Compile something to wasm instead and
nothing about the above changes.

One idea, said twice: **the unit is the thing you uploaded, and what it defines
is what it becomes.** The registry does not take your word for it — it reads
the file. A class defining the four game methods is a game; a wasm module
exporting the five game functions is a game; neither had to be registered,
approved or added to a plugin list.

```
                    store                  execute                 assess
   a .py class ──►  sha256 id  ──►  python subprocess   ──►   Elo, per game
   any .wasm   ──►  read it    ──►  browser Worker/node ──►   illegal-move rate
                                     no fs, no net, seeded
```

```python
class Countdown:
    """Say a number lower than the last. Whoever cannot, loses."""
    name, players = "countdown", 2

    def __init__(self, seed):  self.at = 10 + seed % 3
    def view(self, seat):      return f"The number is {self.at}. Legal moves: any integer below it."
    def step(self, moves):     ...      # {seat: was_it_legal}
    def done(self):            return self.at <= 0
    def result(self):          return {"scores": [1, 0], "summary": "…"}
```

```console
$ m arena/upload path=countdown.py
{ "name": "countdown", "role": "game", "lang": "python", "id": "3f2a…" }

$ m arena/enter name=opus kind=model config='{"model":"anthropic/claude-opus-5"}'
$ m arena/play game=countdown players=opus,lucky
```

---

## The three layers

### 1. Storage

A module's id **is** the SHA-256 of its bytes — a class and a binary are stored
the same way. Uploading the same thing twice is idempotent, an id can be
verified without trusting the server, and `/blob/:id` is cacheable forever
because the id changing *is* the invalidation.

Before anything is stored it is **read**, by whichever of the two readers the
bytes call for — `src/arena-rs/src/wasm.rs` for a binary, `klass.rs` for source
— and what it found is what the card says:

```console
$ m arena/module module=connect4
{ "name": "connect4", "role": "game", "lang": "python", "class": "ConnectFour",
  "size": 5051,
  "info": { "doc": "Drop a disc down a column; four in a row, any direction, wins.",
            "exports": [ { "name": "view", "signature": "(self, seat)" },
                         { "name": "step", "signature": "(self, moves)" }, … ],
            "imports": [], "attributes": [ { "name": "players", "value": "2" } ] },
  "source": "…the class itself…" }
```

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

| role | as a class | as wasm | what it means |
|---|---|---|---|
| `game` | defines `view` `step` `done` `result` | exports `game_init` `game_view` `game_step` `game_done` `game_result` | can be played |
| `player` | defines `play` | exports `play` | can fill a seat |
| `command` | — | exports `_start` | an ordinary program |
| `class` / `wasm` | anything else | anything else | stored, readable, runnable |

An upload that lands as `class` is told what it lacks rather than just refused,
because "you are two methods away" is more use than "no".

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

### 2b. Execution — a class

A class runs in a python subprocess started by the runner (`runtime/host.py`,
driven over JSON lines by `runtime/pyhost.mjs`). It wears the same face the
wasm host does — `view`, `step`, `done`, `result` — so `match.mjs` never learns
which kind it got, and a class game and a wasm game are played by one loop and
rated on one leaderboard.

The subprocess gets:

| | |
|---|---|
| no filesystem | `open` is not in builtins, `RLIMIT_FSIZE` is 0 |
| no network | `socket`, `urllib`, `http`, `subprocess` are not importable |
| no clock | `time` and `datetime` are not importable, so replays cannot drift |
| seeded `random` | seeded from the match seed before `__init__` runs |
| bounded | 512 MiB, 30 CPU seconds, and a per-move timeout that kills the process |

Anything the class prints goes into the match transcript, the way `arena.log`
does for wasm.

**This is not the wasm sandbox, and the difference is not a detail.** Wasm
cannot reach anything the host does not hand it. CPython can be talked out of a
restricted namespace by someone who knows the language well enough. The limits
here stop accidents, casual mischief and runaways. Run classes the way you
would run any code you have decided to trust; upload wasm for the rest.

Because the state is `self` rather than a string the host holds, a class match
replays by starting the process from the seed and feeding it the recorded moves
— the transcript is still the whole match.

The one thing a class cannot do is run in a browser tab, which cannot start a
python process. The console notices and plays those matches through the runner
instead; nothing else about them differs.

### 3. The arena

A **player** fills a seat. Seven kinds:

| kind | who moves | where it runs |
|---|---|---|
| `class` | a stored class defining `play` | a python process, from the runner |
| `wasm` | a stored module exporting `play` | the browser / the runner |
| `model` | any OpenAI-compatible `/chat/completions` | the server (it holds the key) |
| `agent_mod` | an agent in this fleet's `agent` module | the server |
| `mcp` | **any module of this fleet**, over its own MCP server | the server |
| `http` | your endpoint, posted a view, answering a move | the server |
| `human` | you, in the console | the tab |

The fifth is the one that makes the fleet playable. Every module here already
answers on an MCP server, and so does every other module on this box, so
"which of my modules is any good at this" is a question with an answer:

```console
$ m arena/fleet                                  # 50-odd modules, each a seat
$ m arena/fleet module=bt                        # what it offers to be asked
$ m arena/seat module=bt tool=bt_ask             # entered; now sit it down
$ m arena/play game=nim players=bt,minimax
```

A module is **named, never addressed**: the call goes through the fleet
gateway, which is what wakes a module the activator has put to sleep — being
seated is enough to bring it back. `tool` and the argument the position goes
in are read off the module's own `tools/list` when they are not given, a
server whose argument is called `view` is handed the position rather than the
brief, and `auth=1` signs the call with this box's own key for a module that
will only answer a caller it can identify.

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

### As a class

Four methods. The state is `self`, the moves are strings, and there is no
calling convention to learn:

```python
class Blotto:
    """Split 20 soldiers across 3 fields; take the most fields."""

    name = "blotto"
    players = 2

    def __init__(self, seed):
        self.round, self.fields_won = 0, [0, 0]

    def turn(self):
        return [0, 1]              # both at once — a simultaneous game

    def view(self, seat):
        return (f"Round {self.round + 1}. You have {self.fields_won[seat]} fields.\n"
                f"Legal moves: any 3 numbers adding to 20, e.g. `10 5 5`.")

    def step(self, moves):         # {seat: "10 5 5"} → {seat: was_it_legal}
        ...
        return {0: True, 1: True, "note": "what happened, for the transcript"}

    def done(self):
        return self.round >= 6

    def result(self):
        return {"scores": [1, 0], "summary": "seat 0 took the most fields"}
```

```console
$ m arena/template role=game > mygame.py    # the starting point, from the server
$ m arena/upload path=mygame.py             # it is a game now
$ m arena/abi lang=class                    # the contract, in full, at run time
```

`view` is the whole interface a player has: whatever it says is what a model,
a bot and a person all get, which is what makes their ratings comparable. Say
`Legal moves: …` in it. Whatever `step` marks `False` is counted against that
player for good, and that number — the illegal-move rate — is most of what
separates a model that can play from one that can only talk about playing.

A player is a class with one method:

```python
class MyBot:
    name = "mybot"

    def play(self, view, seat):
        return "4"                 # the move, as text. That is the whole job.
```

Optional on a game: `turn` (who moves now — several seats for a simultaneous
game, omitted means alternating), `info`, and the `name` / `players` /
`max_turns` attributes.

### With the agent

```console
$ m arena/vibe prompt="tic-tac-toe on a 4x4 board, three in a row wins"
$ m arena/vibe session=3f2a prompt="print the board every move"
$ m arena/vibe_store session=3f2a name=ttt4
```

A vibe session is one file under `~/.mod/arena/vibe/<id>/` that the build
module's agent (orbit/build — Claude Code with a task ledger) edits a sentence
at a time, with the contract written beside it as `ARENA.md`. It starts from
the template or from a fork of any stored class (`m arena/fork module=connect4`;
every game and player page in the console has a fork button). Nothing is
stored until you say so, and storing is the same upload as anything typed by
hand — the registry reads what the agent wrote. `ARENA_BUILD_URL=off` turns
the agent off; a fork and a template still work without it.

### As wasm

A game is also a wasm module that exports five functions. The whole calling
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

It is a game now. The contract is also readable at run time — `m arena/abi`,
`m arena/abi lang=class`, or the `game_abi` MCP tool — so an agent can write
one without reading this file.

Which container to reach for: **a class** to get a game out of your head and
onto the leaderboard this afternoon, **wasm** when you want it running in a
browser tab, in a real sandbox, at compiled speed, or written in something
other than Python.

**Ranking honestly.** The runner is trusted for the outcome; it is the thing
that ran the wasm. What makes that honest rather than hopeful is the
transcript: the seed and every move are recorded and the game module is pure
over its state, so anyone can replay a match and check the scores. A
leaderboard here is a claim with its working attached.

---

## The example pack

Thirteen modules, planted on startup: eight compiled from one `.rs` file each
by `src/examples/build.sh`, and five classes that are just files.

| module | role | what it is for |
|---|---|---|
| `connect4` | game · class | Connect Four in ninety lines — the class ABI, plainly |
| `blotto` | game · class | simultaneous and hidden: both seats allocate, neither sees first |
| `center` | player · class | reads the board out of the view, takes the win, blocks theirs |
| `spread` | player · class | reads Blotto's *rules* out of the view and answers legally |
| `lucky` | player · class | eight lines: picks from `Legal moves:`. The floor |
| `rps` | game | best of five, both seats throwing at once — simultaneous moves |
| `ttt` | game | tic-tac-toe: solved, so a loss is a mistake rather than luck |
| `nim` | game | 21 stones, take 1–3 — a numeric move and an arithmetic rule |
| `bot-random` | player | reads the `Legal moves:` line and picks one. The floor |
| `bot-ttt` | player | perfect tic-tac-toe by minimax. The reference — it never loses |
| `mlp` | wasm | a 2-2-1 neural net computing XOR, nine parameters |
| `markov` | wasm | an order-2 character Markov chain over a baked corpus |
| `hello` | command | an ordinary WASI program that knows nothing about the arena |

The classes are the shortest way to see the shape of a game; the `.rs` files
are the same shape with a compiler in the way. `mlp` and `markov` are the model
examples. Neither is big, and that is the
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
$ pytest src/tests -q                # end to end, on a throwaway server
```

Then the console at `/arena`. It has two nouns and nothing else: **games**
and **players**. A game is a card showing who is winning it, and opening it
opens its leaderboard — the ranking, the seats you play into, and the matches
that produced the ranking. A player is a card that says who it is and how it
plays, and opening it opens its sheet: rating, record, win rate, average
score, illegal-move rate, timeouts, pace per move, calls out, recent form and
streak; then the same numbers per game it has played, who it has met and how
that went, its matches as it saw them (result, rating move, faults), and what
it is — a class shows its source. **+ add** takes a `.wasm` or a `.py` (or a class written in the panel),
reads it before storing it, and puts it where it belongs: a game becomes a
board, a player is entered so it can be sat down at one.

Entering a player is picking where it comes from rather than writing JSON:
**a module here** lists what is stored, **a module of the fleet** lists every
module on this box with an MCP server and then lists that module's own tools,
**an agent** lists the agent module's personas, **a model** takes a model id,
**an endpoint** takes a URL. The seat selects at a game do the same thing from
the other end — they hold everyone entered *and* every module that could be,
and picking one of those enters it on the way to the table.

**host** is whose arena this is, and the chip in the header carries the
address of the key that signs for the box on every page.

The whole console is built for a phone as well as a desk: below 900px the tabs
become a bottom bar, the cards go to one column, and every table stacks into
records that carry their own column headings rather than scrolling sideways.
Themes come off one button in the header: a menu of swatches — dark, light,
a green tube, an amber tube, ocean, paper, rose, and mono — plus **system**,
which follows the OS between dark and light. The pick is remembered in the
browser; `?theme=<name>` on the URL wears one for that tab without
remembering it, so a tiled or screenshotted console can be told what to look
like. A theme is one block of CSS variables and a line in the script.

**docs** is the manual — eight pages,
rendered from the markdown the server hands out at `GET /docs/:slug`, which is
the same text `docs_page` returns over MCP. There is one documentation, not a
page for people and a paragraph for agents.

### As an MCP server

Thirty-one tools, over Streamable HTTP at `/mcp` or stdio:

```console
$ m arena/mcp_config
{"mcpServers": {"arena": {"command": ".../arena-api", "args": ["--stdio"]},
                "arena-http": {"type": "http", "url": "http://localhost:50470/mcp"}}}
```

Every REST route dispatches through the same tool layer, so what an agent can
do over MCP is exactly what a browser can do over HTTP. An agent can read
`game_abi`, write a class, `put_class` it, enter itself, play and read the
leaderboard with nobody in the loop — which is the case this was built for.

The documentation is on the same footing: `docs_pages`, `docs_page` and
`docs_search` are the eight pages of the console's **docs** tab, and each one
is also an MCP resource at `arena://docs/<slug>`, so a client that would rather
attach documentation than call a tool for it can. `initialize` says as much in
its `instructions`, before anything has been called.

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

### Whose arena this is

A rating is a claim about somebody else's code, and a claim is worth what its
host is worth. `GET /host` — `m arena/host`, `arena_host` over MCP, the **host**
tab in the console — says who is making it: the address of the key this box
signs with (the same one that signs a store push), the machine, the process and
its uptime, every door in, how much of the registry has reached the store and
under which address, the quota left there, and whether this box can compile a
Rust class at all.

```console
$ m arena/host
address   0x7d7c…d123        the key this box signs with
machine   ams1-blade161-2
uptime    3h 12m             pid 566520 on linux/x86_64
store     24 of 24 have a cid · 41 MB of 100 MB left
rustc     1.95.0 → wasm32-unknown-unknown
```

---

## Layout

```
config.json                  the mod protocol surface: ports, fns, tools, ABI
src/
  mod.py                     the anchor — a thin client over the MCP tool layer
  arena-rs/                  the server: MCP + REST + registry + rating
    src/wasm.rs                the wasm binary reader (no dependencies)
    src/klass.rs               the class reader: the `def`s decide the role
    src/blobs.rs               content-addressed blob store
    src/store.rs               modules, players, matches
    src/storelink.rs           the bridge to the store module: every blob is an object there too
    src/rating.rs              Elo for matches with more than two seats
    src/players.rs             model / agent_mod / mcp / http drivers
    src/mcpout.rs              the door out: the servers a class may call, and the fleet
    src/hostcard.rs            who is running this: the box, its key, its uptime
    src/arena.rs               every capability, once
    src/mcp.rs                 the tool layer
    src/http.rs                REST adapters, blobs, the runtime, the console
    src/console.html           the console: games, players, servers, host, docs — one file
    src/docs.rs                the documentation, as data: REST, tools and resources
    docs/*.md                  the eight pages themselves
  runtime/                   the execution layer — browser and node both
    host.mjs                   the wasm host: WASI shim, arena shim, auto-stub
    host.py                    the class host: the sandbox a class runs in
    pyhost.mjs                 the class host from node, wearing host.mjs's face
    abi.mjs                    the packed-pointer calling convention
    match.mjs                  the match loop and the player drivers
    worker.mjs                 the browser sandbox
    run.mjs                    the CLI
  examples/                  the pack: one .rs per wasm module, plus build.sh
    classes/                   the class half — five files, nothing compiled
  tests/                     pytest, end to end through every surface
```

## Two hashes, and the code

Every module has two names for its bytes, and the console shows both on the
game's page and on the player's page, above the code:

- **sha256** — the arena's id, computed here from the bytes. `GET /blob/:id`
  serves them, immutable.
- **cid** — the store module's name for the same bytes. After every upload
  (and once at startup for anything older) the arena pushes the blob to the
  fleet's **store** as a public object under the key `arena/<sha256>`, and
  keeps the CID it gets back. `/store/o/<cid>` is the object's page in the
  store; `/api/store/get?cid=` is the bytes, readable without a token and
  without this arena. `POST /store/sync {verify: true}` reads every copy back
  and checks it still hashes to the id.

The **code** card shows a class as itself — its bytes *are* its source — and
a wasm module as the source it was uploaded with: `put_module` takes a
`source_text` beside `bytes`, kept under its own hash (and pushed to the
store under its own CID, `src_cid`). The example pack plants each binary with
the Rust it was built from. A wasm upload that came without its source says
so, and shows its exports instead.

`GET /store` (`m arena/store_status`) says where the store is, whose key the
copies are recorded under, and how many modules have a CID yet. The bridge
holds no credential: it asks the box's own key for a mod-protocol token the
way the CLI would, or reads `ARENA_STORE_TOKEN` / `~/.mod/arena/store_token`.
`ARENA_STORE_URL=off` turns it off — the tests run that way.

## Where this stops

- **The sandbox is the engine's.** A wasm module gets no filesystem and no
  network, and in the browser it can be terminated. It is still running in the
  same process as the page. Treat a public arena's modules as untrusted code
  that you have chosen to run.
- **A class is sandboxed by convention, not by construction.** No filesystem,
  no network, no clock, capped memory and CPU, and a timeout that kills the
  process — but it is CPython, and CPython can be argued with. Classes are for
  code you would run anyway; wasm is for code you would not.
- **A class cannot play in a browser tab.** A tab cannot start a python
  process, so those matches go through the runner. Same loop, same ratings.
- **Results are reported by the runner.** Rated, recorded, replayable — but not
  independently re-executed on submission. The transcript is what makes a
  disputed match checkable.
- **`run_match` needs node on PATH**, because that is the execution layer.
  Without it, matches play in the browser only.
