# REST, state and the fleet

The API answers on this port directly and at `/api/arena` behind the fleet
router, and the console at `/arena` in both worlds. Every route below
dispatches through the [same tool layer MCP does](#docs/mcp).

## Routes

| | |
|---|---|
| `GET /info`, `/health` | what this arena is and what is in it |
| `GET /abi?role=&lang=` | the contract, at run time |
| `GET /docs`, `/docs/:slug`, `/docs/search?q=` | these pages |
| `GET /modules`, `POST /modules` | the registry · upload bytes |
| `GET /classes`, `POST /classes {source}` | the classes · upload one as text |
| `GET /modules/:id`, `DELETE /modules/:id` | one module's card |
| `POST /inspect {bytes\|text}` | read a file without storing it |
| `GET /blob/:id` | the bytes, immutable — the id is their hash |
| `GET /wasm/:id` | the compiled form of a Rust class |
| `GET /players`, `POST /players` | who is entered · enter one |
| `GET /players/:id`, `DELETE /players/:id` | the full sheet: per game, faults, form, opponents |
| `POST /play {player, view, seat}` | one move, outside any match |
| `POST /run {game, players[]}` | play a whole match headlessly |
| `GET /matches`, `POST /matches`, `GET /matches/:id` | the record · post one played elsewhere |
| `GET /leaderboard?game=` | the ranking, per game or overall |
| `GET /m/:name`, `GET /m/:name/tools` | the per-module MCP servers |
| `POST /m/:name/mcp` | one module's own MCP endpoint |
| `GET /mcp/servers`, `POST /mcp/call` | what a class may call out to |
| `GET /fleet`, `GET /fleet/:name/tools` | the fleet's own modules, as seats |
| `GET /toolchain` | can this box compile a Rust class |
| `GET /store`, `POST /store/sync` | the bridge to the store module |
| `GET /runtime/:file` | the execution layer itself |
| `POST /forward {action, …}` | any tool by name |
| `POST /mcp` | the arena's MCP endpoint |

## State

Nothing a user put here is in the repo. It all lives in `~/.mod/arena/`:

| | |
|---|---|
| `blobs/` | every uploaded module, by SHA-256 |
| `registry.json` | ids, cids, roles, sources — the index |
| `keys.json` | API keys for `model` players |
| `rustc/` | the compile cache for Rust classes |
| `mcp_servers.json` | the servers a class may call out to, with their headers |
| `store_token` | optional, overrides the token the box's own key would sign |

The repo carries the example pack and nothing else: thirteen modules planted on
startup, eight compiled from one `.rs` file each and five classes that are just
files. `plant_examples` (`m arena/examples`) replants them.

## In the fleet

- **Every stored module is a mod.** `m arena/mint` writes them under
  `orbit/arena/mods/`, and then `m arena.<name>` is an ordinary mod call.
- **Every blob is a store object.** Pushed to the fleet's store as
  `arena/<sha256>`, public, readable by CID without a token and without this
  arena. `GET /store` says where the store is and how many modules have a CID
  yet; `ARENA_STORE_URL=off` turns the bridge off.
- **Every fleet module is a possible seat.** `GET /fleet` lists them and
  `/fleet/:name/tools` says what each one offers, so a seat can be filled by
  picking a tool rather than by knowing one. A module is named, never
  addressed: the call goes through the gateway, which wakes one that is asleep.

## Running it

```console
$ m arena/serve                      # builds, then pm2 start arena-api
$ m arena/test                       # end to end against the running server
$ pytest src/tests -q                # on a throwaway server
```
