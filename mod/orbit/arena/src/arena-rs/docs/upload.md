# Uploading

An upload is one door: a `.wasm` binary, a `.py` class, or a `.rs` class. Before
anything is stored the bytes are **read** — `wasm.rs` reads the exports of a
binary, `klass.rs` reads the `def`s of a Python class, `rsklass.rs` reads the
`fn`s in a Rust class's impl block — and what the reader found is what the card
says. The role comes out of the bytes, never out of what the uploader claimed.

## The id is the bytes

A module's id **is** the SHA-256 of its bytes. A class and a binary are stored
the same way. Uploading the same thing twice is idempotent, the id can be
verified without trusting this server, and `GET /blob/:id` is cacheable forever
because the id changing *is* the invalidation.

## What a role means

| role | as a class | as wasm | what it means |
|---|---|---|---|
| `game` | defines `view` `step` `done` `result` | exports `game_init` `game_view` `game_step` `game_done` `game_result` | can be played |
| `player` | defines `play` | exports `play` | can fill a seat |
| `command` | — | exports `_start` | an ordinary WASI program |
| `class` / `wasm` | anything else | anything else | stored, readable, runnable |

An upload that lands as `class` is told what it lacks rather than refused,
because "you are two methods away" is more use than "no".

## Three containers

- **A Python class** is stored as its own source. Its bytes *are* its source,
  so the code card on its page is the file you uploaded.
- **A Rust class** is compiled to `wasm32-unknown-unknown` on upload and cached
  under its own id, so it runs in the same sandbox any other wasm module does.
  `GET /toolchain` says whether this box can compile one.
- **A wasm binary** is taken as it is. `put_module` accepts a `source_text`
  beside the bytes, kept under its own hash — a wasm upload that came without
  its source says so and shows its exports instead.

## Inspect before you store

```console
$ m arena/inspect path=mygame.py
{ "role": "game", "class": "MyGame", "missing": [], "exports": [...] }
```

`POST /inspect {bytes|text}` runs the same reader the upload runs and stores
nothing. It is the fastest way to find out that `done` is spelled `is_done`.

## Or write one with the agent

```console
$ m arena/vibe prompt="tic-tac-toe on a 4x4 board, three in a row wins"
{ "session": "3f2a9c…", "status": "done", "reads_as": { "role": "game", "class": "TicTacToe4" }, "source": "…" }
$ m arena/vibe session=3f2a prompt="print the board every move"        # round two
$ m arena/vibe_store session=3f2a name=ttt4                             # now it is a game
```

A **vibe session** is one file under `~/.mod/arena/vibe/<id>/` with
`ARENA.md` — the game or player page of these docs, the sandbox page, and
for Rust the prelude — beside it. It starts from the template, or from the
source of any stored class: `m arena/fork module=connect4` copies connect4
into a session under a new name, and every game's and player's page in the
console has a **fork** button that does the same. Each sentence is a round:
the file and the sentence go to the build module's job server (orbit/build —
Claude Code, with a public task ledger), the agent edits the file in place
and checks it against `POST /inspect`, and the session comes back holding
the result and what the registry reads it as. Edit it by hand between
rounds if you like; the text you hand back is what the next round starts
from.

Nothing is stored until you say so, and storing is `put_class` on the text —
the same upload as anything typed by hand, so what it became is read off the
file rather than off the session. A player is entered at the same time.

The arena talks to build as the box itself: build's job server validates its
own session token, which only a process on build's host can mint, so the
jobs land in build's ledger under its owner. `ARENA_BUILD_URL` names the
server (default `http://127.0.0.1:8890`; `off` turns the agent off — a fork
and a template still work, the sentence answers 424). `ARENA_VIBE_MAX` rounds
run at once (2), a round is cancelled after 15 minutes, and
`ARENA_VIBE_MODEL` picks the model.

## Whose it is

Every module has an **owner**: the wallet address that signed the upload.
Ownership is proved, never claimed — a request carries a mod-protocol token
in its `token` header (a browser wallet's `personal_sign` over
`{"data","time"}`, or `m.mod('auth')().token(...)` from the box), the arena
verifies the signature itself, offline, and that address is the caller.
`GET /whoami` says who a token proves. A module uploaded without one belongs
to the box. Re-uploading somebody else's bytes changes nothing about them.

Only the owner — or whoever runs the box (build's owner, the box key, and
`ARENA_OWNERS`) — may **edit** or **delete** a module:

```console
$ m arena/vibe edit=connect4 prompt="make the board 8x7"   # a session bound to you
$ m arena/vibe_store session=7c1e                            # connect4, version 2
```

An edit is new bytes, so a new id: the new version takes the game's name and
owner, `parent` points at the old one, and the old one leaves the shelf (its
matches still point at it; `GET /modules?all=1` lists it). Each version has
its own board. `DELETE /modules/<id>` on the current version puts the
previous one back; `?all=1` deletes every version. A vibe **round** needs a
signed-in caller — it runs the box's agent — and the session is then theirs
alone to steer, store or discard. Every module also carries `made_with`: the
model the agent ran on when a vibe wrote it, else how it arrived.

## Two hashes

Every stored module has two names for the same bytes, and both are on its page:

- **sha256** — the arena's id, computed here. `GET /blob/:id` serves the bytes.
- **cid** — the store module's name for them. After every upload the arena
  pushes the blob to the fleet's store as a public object under the key
  `arena/<sha256>` and keeps the CID it gets back, so the code is readable
  without a token and without this arena. `POST /store/sync {verify: true}`
  reads every copy back and checks it still hashes to its id.

`ARENA_STORE_URL=off` turns the bridge off — the tests run that way.

## And a mod, and a server

Every stored module is minted as a nested mod under `orbit/arena/mods/`
(`m arena/mint`, then `m arena.<name>`), and answers as an [MCP server of its
own](#docs/mcp) at `/m/<name>/mcp`. Uploading a file is the whole of making
one.
