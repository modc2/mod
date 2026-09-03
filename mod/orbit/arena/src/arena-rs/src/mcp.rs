//! MCP server core — JSON-RPC 2.0, shared by the Streamable HTTP endpoint
//! (/mcp) and stdio mode (--stdio).
//!
//! Every REST route funnels through `call_tool` too, so a capability is
//! defined exactly once: what an agent can do over MCP is what a browser can
//! do over HTTP, always.

use crate::arena;
use crate::players;
use crate::wasm;
use serde_json::{json, Value};
use std::sync::OnceLock;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

pub const PROTOCOL_VERSION: &str = "2025-06-18";
pub const SERVER_NAME: &str = "arena";
pub const SERVER_VERSION: &str = env!("CARGO_PKG_VERSION");

/// What a client is told at initialize, before it has called anything. Short
/// on purpose: where the documentation is, and the one loop this exists for.
pub const INSTRUCTIONS: &str = "\
Upload a class or a wasm module; agents compete at what you uploaded. What a \
file defines is what it becomes: view/step/done/result is a game, play is a \
player, and nothing has to be registered.\n\n\
Read the docs first — docs_pages lists eight pages, docs_page reads one, \
docs_search finds a section, and every page is also the resource \
arena://docs/<slug>. game_abi is the contract at run time.\n\n\
The loop with nobody in it: game_abi -> put_class -> enter_player -> \
run_match -> leaderboard. Every stored module is also an MCP server of its \
own at /m/<name>/mcp, where a game can be played a turn at a time.";

/// Where this server answers, so the node runner it spawns can call back in.
static BASE: OnceLock<String> = OnceLock::new();

pub fn set_base(url: String) {
    let _ = BASE.set(url);
}

pub fn base() -> String {
    BASE.get().cloned().unwrap_or_else(|| "http://127.0.0.1:50470".into())
}

fn runner_path() -> std::path::PathBuf {
    if let Ok(p) = std::env::var("ARENA_RUNNER") {
        return std::path::PathBuf::from(p);
    }
    std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../runtime/run.mjs"))
}

pub fn tool_list() -> Value {
    json!([
        {
            "name": "arena_info",
            "description": "What this arena is and what is in it: how many modules are stored, how many of them are games, who is entered, and the ABI a module has to implement to be one.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "game_abi",
            "description": "The contract a module implements to become a game or a player here, with a worked example. Two containers implement it: a wasm binary (lang=wasm) or a Python class (lang=class). Read this before writing one — it is the whole specification, and it is short.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": { "type": "string", "enum": ["game", "player"], "default": "game" },
                    "lang": { "type": "string", "enum": ["wasm", "class", "rust"], "default": "wasm",
                              "description": "`class` for the Python-class form and `rust` for the Rust-class form — the methods to define, rather than the exports to compile" }
                }
            }
        },
        {
            "name": "docs_pages",
            "description": "The documentation of this arena: eight pages, with a one-line summary of each. Start here if you are about to write a game, seat a player or drive this over MCP — it is the same text the console's docs tab shows, and every page is also the resource arena://docs/<slug>.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "docs_page",
            "description": "One documentation page, as markdown. Slugs: start, upload, game, player, match, sandbox, mcp, api.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "slug": { "type": "string", "default": "start",
                              "description": "start (what this is) · upload (what the reader reads) · game (the contract, in three containers) · player (the seven kinds of seat) · match (the loop and the ratings) · sandbox (what uploaded code may reach) · mcp (this server) · api (routes, state, the fleet)" }
                }
            }
        },
        {
            "name": "docs_search",
            "description": "Free text over the documentation, scored by section rather than by page — the answer to `where does it say that` comes back as a heading and a snippet, with the slug to read in full.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "q": { "type": "string" },
                    "limit": { "type": "integer", "default": 8 }
                },
                "required": ["q"]
            }
        },
        {
            "name": "list_modules",
            "description": "Every module in the registry, oldest first — wasm binaries and Python classes alike. Filter by role (game, player, command, class, wasm), by lang (wasm, python), by tag or by free text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": { "type": "string", "enum": ["game", "player", "command", "class", "wasm"] },
                    "lang": { "type": "string", "enum": ["wasm", "python", "rust"] },
                    "q": { "type": "string" },
                    "tag": { "type": "string" }
                }
            }
        },
        {
            "name": "get_module",
            "description": "One module in full. For wasm: imports and exports with signatures, the host namespaces it needs, its memory. For a class: the classes it defines, their methods and signatures, what it imports — and, unless you pass source=false, the source itself.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": { "type": "string", "description": "id, id prefix, or name" },
                    "source": { "type": "boolean", "default": true,
                                "description": "include the source of a class module" }
                },
                "required": ["module"]
            }
        },
        {
            "name": "put_module",
            "description": "Store a module — a wasm binary or a Python class. The id is the SHA-256 of the bytes, so storing the same thing twice updates its metadata and never duplicates it. The role is read out of the bytes, not taken on trust: exporting the game ABI (or defining view/step/done/result) makes a game, exporting `play` (or defining `play`) makes a player, `_start` makes a command, anything else is stored and still runs. To send a class as plain text rather than base64, use put_class.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bytes": { "type": "string", "description": "The module, base64 (a data: URL is fine) or hex" },
                    "source_text": { "type": "string", "description": "For a wasm binary: the code it was built from, as plain text, kept beside the bytes under its own hash and shown as the module's code" },
                    "name": { "type": "string" },
                    "description": { "type": "string" },
                    "author": { "type": "string" },
                    "tags": { "type": "array", "items": { "type": "string" } }
                },
                "required": ["bytes"]
            }
        },
        {
            "name": "inspect_module",
            "description": "Describe bytes without storing them — for wasm, its imports, exports and memory; for a class, the classes and methods it defines and whether the sandbox will allow its imports. Either way, the role they would take. What the console shows the moment a file is dropped on it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "bytes": { "type": "string", "description": "base64 or hex" },
                    "text": { "type": "string", "description": "class source, as plain text" }
                }
            }
        },
        {
            "name": "put_class",
            "description": "Upload a class and it is playable. Pass the source as plain text, in Python or in Rust. Python: a class defining `view`, `step`, `done`, `result` is a game, one defining `play(self, view, seat)` is a player. Rust: a struct whose impl block defines the same four is a game, one defining `play` is a player. Same registry, same ids, same leaderboard as wasm. What differs is where it runs — a Python class runs in a sandboxed python subprocess (no filesystem, no network, seeded random) through the node runner, while a Rust class is compiled to wasm on upload and runs in the wasm sandbox, in a browser tab as happily as in the runner. Call game_abi with lang=class or lang=rust for the contract.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "source": { "type": "string", "description": "The class, as Python or Rust source — which it is, is read off the source" },
                    "lang": { "type": "string", "enum": ["python", "rust"], "description": "Only a tie-break; a file that is plainly one is that one however it was labelled" },
                    "name": { "type": "string", "description": "What to call it. Defaults to the class name." },
                    "description": { "type": "string" },
                    "author": { "type": "string" },
                    "tags": { "type": "array", "items": { "type": "string" } }
                },
                "required": ["source"]
            }
        },
        {
            "name": "delete_module",
            "description": "Remove a module and its bytes. Refused while a player is entered with it; past matches keep their record either way.",
            "inputSchema": {
                "type": "object",
                "properties": { "module": { "type": "string" } },
                "required": ["module"]
            }
        },
        {
            "name": "list_players",
            "description": "Everyone entered, strongest first, with the numbers that assess them: Elo, win rate, mean score, illegal-move rate, timeouts and mean time to move.",
            "inputSchema": {
                "type": "object",
                "properties": { "kind": { "type": "string", "enum": ["wasm", "class", "model", "agent_mod", "mcp", "http", "human"] } }
            }
        },
        {
            "name": "get_player",
            "description": "One player in full, including a rating per game — which is the number that means something, since being good at nim says nothing about poker.",
            "inputSchema": {
                "type": "object",
                "properties": { "player": { "type": "string", "description": "id or name" } },
                "required": ["player"]
            }
        },
        {
            "name": "enter_player",
            "description": "Connect an agent (enter a player). `agent_mod` connects an agent of this fleet over the agent mod protocol (config: agent?, model?, base?, steps?, free?). `model` plays a Liquid AI model served by the liquidai module on this box — leave config.model blank for whatever is resident (config: model?, base?, key?, system?, temperature?). `wasm` plays with a stored module that exports `play` (config: module). `mcp` seats an MCP server — anything with a tool that takes a view and returns a move, including another module's own /m/<name>/mcp (config: server, tool?). `http` posts the view to config.url and reads a move back. `human` is asked in the console. Entering a name that already exists updates it and keeps its record.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": { "type": "string" },
                    "kind": { "type": "string", "enum": ["model", "wasm", "class", "agent_mod", "mcp", "http", "human"], "default": "model" },
                    "config": { "type": "object", "description": "Driver settings — see the description" },
                    "owner": { "type": "string" },
                    "note": { "type": "string" }
                },
                "required": ["name"]
            }
        },
        {
            "name": "remove_player",
            "description": "Withdraw a player. Past matches keep their record.",
            "inputSchema": {
                "type": "object",
                "properties": { "player": { "type": "string" } },
                "required": ["player"]
            }
        },
        {
            "name": "run_match",
            "description": "Play a match: seat the given players at the given game and run it to the end. The wasm executes in the node runner (the same execution layer the browser console uses), every move is recorded, and the result is rated. Two or more seats makes it rated; one seat is practice.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": { "type": "string", "description": "Game module id, prefix or name" },
                    "players": { "type": "array", "items": { "type": "string" }, "description": "Player ids or names, in seat order" },
                    "seed": { "type": "integer", "description": "Replay handle — the same seed and moves replay the match" },
                    "turns": { "type": "integer", "description": "Cap the turns; defaults to the game's own limit" },
                    "mcp": {
                        "type": "array",
                        "items": { "type": "string" },
                        "description": "MCP servers the classes in this match may call out to, by name. Left out, they have no way out at all — which is the default, and the only setting under which a move is a function of its view alone. See mcp_servers."
                    },
                    "timeout_ms": { "type": "integer", "default": 300000 }
                },
                "required": ["game", "players"]
            }
        },
        {
            "name": "play_move",
            "description": "Ask one player for one move, given what a seat can see. This is what a running match calls for anything the execution layer cannot drive itself — a model, a fleet agent, someone's endpoint. Useful on its own to check a player answers before entering it in a match.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "player": { "type": "string" },
                    "view": { "type": "string", "description": "What this seat can see" },
                    "seat": { "type": "integer", "default": 0 }
                },
                "required": ["player", "view"]
            }
        },
        {
            "name": "record_match",
            "description": "Submit a finished match from a runner: the game, the seats with their scores, and the transcript. Rates it and keeps it. This is how the browser console reports what it played.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": { "type": "string" },
                    "seed": { "type": "integer" },
                    "runtime": { "type": "string", "enum": ["browser", "node"] },
                    "summary": { "type": "string" },
                    "seats": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "player_id": { "type": "string" },
                                "score": { "type": "number" },
                                "moves": { "type": "integer" },
                                "illegal": { "type": "integer" },
                                "timeouts": { "type": "integer" },
                                "ms": { "type": "integer" },
                                "error": { "type": "string" }
                            }
                        }
                    },
                    "turns": { "type": "array", "items": { "type": "object" } }
                },
                "required": ["game", "seats"]
            }
        },
        {
            "name": "list_matches",
            "description": "Recent matches, newest first, with the scoreboard of each. Name a game, a player, or both to narrow it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": { "type": "integer", "default": 20 },
                    "game": { "type": "string" },
                    "player": { "type": "string", "description": "a player id or name — only matches it sat in" }
                }
            }
        },
        {
            "name": "get_match",
            "description": "One match in full, including every turn: what each seat saw, what it said, what was read as its move, and whether the game accepted it.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string" } },
                "required": ["id"]
            }
        },
        {
            "name": "leaderboard",
            "description": "Players ranked by Elo. Name a game to rank at that game, which is the ranking that means something.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "game": { "type": "string" },
                    "limit": { "type": "integer", "default": 20 }
                }
            }
        },
        {
            "name": "plant_examples",
            "description": "Re-read the example pack from disk and store anything new. Idempotent — ids are content, so nothing is duplicated.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "module_tool",
            "description": "Call a tool on one module's own server without opening a second MCP connection to it. `module_tool module=nim tool=open` sits you down at nim; `tool=move` plays. Exactly what /m/<name>/mcp does, from here.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": { "type": "string", "description": "id, prefix or name" },
                    "tool": { "type": "string", "description": "about, source, open, view, move, state, play, run…" },
                    "arguments": { "type": "object" }
                },
                "required": ["module", "tool"]
            }
        },
        {
            "name": "mcp_servers",
            "description": "The MCP servers a class running here is allowed to call out to. A class names one of these — never a URL — and this server makes the call for it, so the sandbox never grows a socket and the credentials never reach the code that uses them. Configure the list in ~/.mod/arena/mcp_servers.json.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "mcp_call",
            "description": "Call a tool on one of those servers. This is the same door a class goes through — `arena::mcp(server, tool, args)` in Rust, `self.mcp(...)` in Python — exposed so you can try a call before writing a class that depends on it.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "server": { "type": "string", "description": "A name from mcp_servers, not a URL" },
                    "tool": { "type": "string", "description": "Leave empty or pass __tools__ to list what it offers" },
                    "arguments": { "type": "object" }
                },
                "required": ["server"]
            }
        },
        {
            "name": "store_status",
            "description": "The bridge to the store module. Every module here is pushed to the fleet's store as a public object, so it has two hashes of the same bytes — the arena's SHA-256 (its id) and the store's CID — and a page anyone can read it from. This says where the store is, which address the copies are recorded under, and how many modules have a CID yet.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "store_sync",
            "description": "Push every module that has no store CID yet (force=true re-pushes all of them), and with verify=true read each store copy back and check that it still hashes to the module id. Pushes also happen on their own — after every upload and at startup — so this is for seeing that they did, or making them.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "force": { "type": "boolean", "default": false },
                    "verify": { "type": "boolean", "default": false }
                }
            }
        },
        {
            "name": "fleet_modules",
            "description": "Every module of this fleet that a player could be seated on. Anything with an MCP server can take a seat: enter_player with kind=mcp and config {module, tool?} asks it one of its own tools each move and reads a move out of the answer. Modules are named, never addressed — the call goes through the gateway, which wakes one that is asleep. Pass `module` to get that module's tools instead, which is how you find out which tool plays.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": { "type": "string", "description": "a module name — its tools, rather than the whole fleet" }
                }
            }
        },
        {
            "name": "rust_toolchain",
            "description": "Whether this box can compile a Rust class: the rustc it would use, the target, and where the compiled artefacts are cached. A Rust class needs rustc and the wasm32-unknown-unknown target; a Python class needs neither.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "vibe",
            "description": "Write a game or a player with the build agent, one sentence at a time. A session starts from the template (role + lang) or from a stored class (`from` — a fork), and each `prompt` is a round: the file and the sentence go to the build module's job server, the agent edits the file, and the session comes back holding the result and what the registry reads it as. No `prompt` makes the session without running anything — a fork, ready to be written to. Pass `session` to continue one; pass `source` with it to hand back a file you edited by hand. The result is not stored until store_vibe.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": { "type": "string", "description": "what to write or change, in a sentence or a paragraph" },
                    "session": { "type": "string", "description": "continue this session (its id, or a prefix of it)" },
                    "role": { "type": "string", "enum": ["game", "player"], "default": "game", "description": "for a new session from the template" },
                    "lang": { "type": "string", "enum": ["python", "rust"], "default": "python" },
                    "from": { "type": "string", "description": "a stored class module to fork — its id, its name, or a prefix" },
                    "source": { "type": "string", "description": "start from this text instead (or, with `session`, replace the file before the round)" },
                    "name": { "type": "string", "description": "what the result should be called when stored" },
                    "model": { "type": "string", "description": "the model build should run the round on; blank is build's default" }
                }
            }
        },
        {
            "name": "fork_module",
            "description": "Fork a stored class into a vibe session: the source of a game or a player, copied under a new name, ready for a sentence or for editing by hand. The same as vibe with `from` and no `prompt`. Compiled wasm cannot be forked — a fork starts from source.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module": { "type": "string", "description": "the module to fork — id, name, or prefix" },
                    "name": { "type": "string", "description": "the fork's name (default: <name>-fork)" }
                },
                "required": ["module"]
            }
        },
        {
            "name": "get_vibe",
            "description": "One vibe session: its status (ready, running, done, failed, cancelled, stored), the file as it is right now, what the registry reads it as, every round with its transcript tail and cost, and the build job behind it.",
            "inputSchema": {
                "type": "object",
                "properties": { "session": { "type": "string" } },
                "required": ["session"]
            }
        },
        {
            "name": "list_vibes",
            "description": "Every vibe session on this arena, newest first, and whether the build agent is reachable from here.",
            "inputSchema": { "type": "object", "properties": {} }
        },
        {
            "name": "store_vibe",
            "description": "Put what a vibe session holds into the registry — put_class on the text, so what it becomes is read off the file. A player is entered too (enter=false to only store). A Rust class is compiled on the way in and a compile error comes back with the card.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session": { "type": "string" },
                    "name": { "type": "string" },
                    "description": { "type": "string" },
                    "enter": { "type": "boolean", "default": true }
                },
                "required": ["session"]
            }
        },
        {
            "name": "cancel_vibe",
            "description": "Stop a running vibe round. The file keeps whatever the agent had written by then.",
            "inputSchema": {
                "type": "object",
                "properties": { "session": { "type": "string" } },
                "required": ["session"]
            }
        }
    ])
}

fn s(args: &Value, key: &str) -> String {
    args.get(key).and_then(|v| v.as_str()).unwrap_or("").trim().to_string()
}

fn u(args: &Value, key: &str, default: u64) -> u64 {
    args.get(key).and_then(|v| v.as_u64()).unwrap_or(default)
}

fn list_of(args: &Value, key: &str) -> Vec<String> {
    match args.get(key) {
        Some(Value::Array(a)) => a
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.trim().to_string()))
            .filter(|s| !s.is_empty())
            .collect(),
        // A comma-separated string is what a CLI hands us, and what a model
        // reaches for when the schema says "array" and it is in a hurry.
        Some(Value::String(one)) => one
            .split(',')
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect(),
        _ => vec![],
    }
}

/// The class ABI: the same game, written the way Python is written.
fn class_abi(role: &str) -> Value {
    let common = json!({
        "container": "one .py file holding a class. Upload it with put_class (source as text) \
                      or put_module (base64) — the registry reads the `def`s and decides what \
                      it is, exactly as it reads a wasm module's exports.",
        "state": "`self`. The object is built once per match and kept, so a class is written \
                  the way you would write it anywhere else — no packed pointers, no state \
                  string threaded through every call.",
        "replay": "the process starts from the match seed and is fed the recorded moves in \
                   order, so a transcript still replays.",
        "sandbox": {
            "runs_in": "a python subprocess started by the node runner — never in the server, \
                        and never in a browser tab (a tab cannot start python)",
            "filesystem": "none — `open` is not defined and RLIMIT_FSIZE is 0",
            "network": "none — socket, urllib, http and subprocess are not importable",
            "clock": "none — `time` and `datetime` are not importable, so replays cannot drift",
            "random": "`random` is imported for you and seeded from the match seed",
            "limits": "512 MiB of address space, 30 CPU seconds, and a per-move timeout",
            "honest_warning": "this is a convenience sandbox, not the wasm one. CPython can be \
                               talked out of a restricted namespace by someone who knows how. \
                               Upload wasm for code you do not trust.",
        },
        "printing": "anything the class prints goes into the match transcript",
    });
    if role == "player" {
        return json!({
            "role": "player",
            "lang": "class",
            "required_methods": {
                "play(self, view, seat) -> str": "the move, as text — the same question a model \
                                                  in that seat is asked, and the same answer",
            },
            "optional": { "name": "a class attribute, what to call it" },
            "template": crate::klass::PLAYER_TEMPLATE,
            "then": "m arena/upload path=bot.py, then \
                     m arena/enter name=bot kind=class config='{\"module\":\"bot\"}' \
                     — or drop the file on the console's registry tab",
            "example": "src/examples/classes/bot_center.py reads a board out of the view; \
                        bot_lucky.py is the eight-line baseline.",
            "abi": common,
        });
    }
    json!({
        "role": "game",
        "lang": "class",
        "required_methods": {
            "view(self, seat) -> str": "what that seat can see. Show it only what it is \
                                        entitled to and hidden information works. Say \
                                        `Legal moves: …` somewhere in it — every player, \
                                        model or bot, has only this text to go on.",
            "step(self, moves) -> dict": "apply one round. `moves` is {seat: \"text\"}, keyed by \
                                          both int and str. Return {seat: was_it_legal}, and add \
                                          \"note\": \"…\" to put a line in the transcript. Return \
                                          nothing and every move counted as legal.",
            "done(self) -> bool": "True when the match is over",
            "result(self) -> dict": "{\"scores\": [one per seat], \"summary\": \"…\"} — higher is better, \
                                     and the ratings are computed from the order",
        },
        "optional_methods": {
            "__init__(self, seed)": "the opening position. The seed is the match seed.",
            "turn(self) -> int | [int]": "who moves now. Return several seats for a simultaneous \
                                          game — they are asked at once and neither sees the other. \
                                          Leave it out and seats alternate.",
            "info(self) -> dict": "override the card below entirely",
        },
        "class_attributes": {
            "name": "what to call the game",
            "players": "seats — an int, or [min, max]",
            "max_turns": "the turn cap (default 200)",
        },
        "template": crate::klass::GAME_TEMPLATE,
        "illegal_moves": "whatever `step` marks False is counted against that player for good. \
                          That number is most of what separates a model that can play from one \
                          that can only talk about playing.",
        "example": "src/examples/classes/connect4.py — a whole game in ninety lines. \
                    src/examples/classes/blotto.py — the simultaneous, hidden-information case.",
        "abi": common,
    })
}

/// The Rust class ABI: the same class, with a compiler in the way.
///
/// It is worth being clear about what that compiler buys, because it is not
/// speed. A Python class is interpreted in a sandbox that is a convenience; a
/// Rust class is compiled to wasm and runs in a sandbox that is a guarantee.
/// The language people would reach for to write something fast is here the
/// language to reach for to run something you did not write.
fn rust_abi(role: &str) -> Value {
    let common = json!({
        "container": "one .rs file holding a struct and its impl block. Upload it with \
                      put_class (source as text) or put_module (base64) — the registry \
                      reads the `fn`s in the impl and decides what it is, exactly as it \
                      reads a wasm module's exports.",
        "state": "`self`. The struct is built once per match with `new(seed)` and kept.",
        "compiled": "on upload, to wasm32-unknown-unknown: the prelude, then your file \
                     unedited, then a generated shim that exports the wasm game ABI. \
                     Cached under the module id, which is the hash of the source, so it \
                     compiles once. Errors come back with your file's line numbers.",
        "no_crates": "one file, rustc direct — no Cargo, no registry, no dependencies. \
                      std, core and alloc resolve and nothing else does.",
        "sandbox": {
            "runs_in": "a wasm engine — the browser (a Worker) or the node runner. A Rust \
                        class is the only class that plays in a tab.",
            "filesystem": "none, and not by policy: wasm32-unknown-unknown has no syscall \
                           to name one with",
            "network": "the same — except `arena::mcp`, which is not a socket but a request \
                        this server makes on the class's behalf",
            "clock": "`arena::elapsed_ms` is milliseconds since the instance started. \
                      `std::time` compiles and then does nothing, so replays cannot drift.",
            "random": "`arena::random()` is seeded from the match seed",
        },
        "prelude": {
            "Moves": "moves.get(seat) -> &str, .lower(seat), .number(seat) -> Option<i64>, \
                      .seats() -> Vec<usize>",
            "Step": "Step::ok(), Step::legal(&[bool]), .seat(n, false), .note(\"…\")",
            "Outcome": "Outcome::points(&[i64]), ::scores(&[f64]), ::winner(Some(seat), seats), \
                        .summary(\"…\")",
            "arena::log": "a line in the transcript. `log!(\"…\")` is the format! version.",
            "arena::random / below(n) / choice(&[T])": "seeded from the match seed",
            "arena::mcp(server, tool, args_json) -> String": "call a tool on an MCP server \
                        this arena knows. `arena::ask` is the same with the error unwrapped, \
                        `arena::tools(server)` lists what it offers.",
            "read_it": "GET /runtime/prelude.rs — the whole file, which is the specification",
        },
        "printing": "arena::log goes into the match transcript",
    });
    if role == "player" {
        return json!({
            "role": "player",
            "lang": "rust",
            "required_methods": {
                "fn play(&mut self, view: &str, seat: usize) -> String": "the move, as text — \
                    the same question a model in that seat is asked, and the same answer",
            },
            "optional": {
                "fn new(seed: i64) -> Self": "otherwise Default::default() is used",
                "const NAME: &'static str": "what to call it",
            },
            "template": crate::rsklass::PLAYER_TEMPLATE,
            "then": "m arena/upload path=bot.rs, then \
                     m arena/enter name=bot kind=class config='{\"module\":\"bot\"}' \
                     — or drop the file on the console's registry tab",
            "example": "src/examples/rust/bot_greedy.rs, and bot_oracle.rs for one that \
                        asks another server what to play.",
            "abi": common,
        });
    }
    json!({
        "role": "game",
        "lang": "rust",
        "required_methods": {
            "fn view(&self, seat: usize) -> String": "what that seat can see. Show it only \
                what it is entitled to and hidden information works. Say `Legal moves: …` \
                somewhere — every player, model or bot, has only this text to go on.",
            "fn step(&mut self, moves: &Moves) -> Step": "apply one round. Return Step::ok(), \
                or mark a seat false; .note(…) puts a line in the transcript.",
            "fn done(&self) -> bool": "true when the match is over",
            "fn result(&self) -> Outcome": "higher is better; the ratings come out of the order",
        },
        "optional_methods": {
            "fn new(seed: i64) -> Self": "the opening position. Without it, Default::default().",
            "fn turn(&self) -> Vec<usize>": "who moves now. Several seats makes it simultaneous \
                — they are asked at once and neither sees the other. Leave it out and seats \
                alternate.",
        },
        "consts": {
            "NAME": "&'static str — what to call the game",
            "DESCRIPTION": "&'static str",
            "PLAYERS / MIN_PLAYERS / MAX_PLAYERS": "usize — seats",
            "MAX_TURNS": "usize — the turn cap (default 200)",
        },
        "forgiving_returns": "`step` may return Step, bool, [bool; N] or (); `result` may \
                              return Outcome, [i64; N] or Vec<f64>; `turn` may return one \
                              seat or many. The prelude's IntoStep / IntoOutcome / IntoTurn \
                              are how — write the obvious thing and it compiles.",
        "template": crate::rsklass::GAME_TEMPLATE,
        "illegal_moves": "whatever `step` marks false is counted against that player for good. \
                          That number is most of what separates a model that can play from one \
                          that can only talk about playing.",
        "example": "src/examples/rust/nim.rs — a whole game in fifty lines, no unsafe, \
                    no pointers, no build step you have to run.",
        "abi": common,
    })
}

/// The ABI, as documentation an agent can read at run time.
pub fn game_abi(role: &str, lang: &str) -> Value {
    if matches!(lang, "class" | "python" | "py" | "classes") {
        return class_abi(role);
    }
    if matches!(lang, "rust" | "rs") {
        return rust_abi(role);
    }
    let common = json!({
        "strings": "The module exports `alloc(i32) -> i32`. The host writes UTF-8 there. \
                    Anything the module returns is one i64 packed as (ptr << 32) | len.",
        "state": "The host holds the game state as a string between calls, so every \
                  export is a pure function of it. That is what makes a match replayable \
                  from its seed and its moves.",
        "host_imports": {
            "arena.log(ptr, len)": "write a line into the transcript",
            "arena.random() -> f64": "seeded from the match seed, so replays match",
            "arena.now() -> f64": "milliseconds since the match started",
        },
        "runs_in": "the browser (a Worker) or the node runner — never on the server",
    });
    if role == "player" {
        return json!({
            "role": "player",
            "required_exports": {
                "alloc(i32) -> i32": "so the host can pass the view in",
                "play(view_ptr, view_len, seat) -> i64": "the move, as packed text",
            },
            "notes": "A player module sees exactly what `game_view` gave its seat and \
                      returns a move as text. The game decides whether it was legal.",
            "abi": common,
        });
    }
    json!({
        "role": "game",
        "required_exports": {
            "game_init(seed: i32) -> i64": "the opening state, as packed JSON",
            "game_view(state_ptr, state_len, seat: i32) -> i64": "what that seat can see, as packed text — show it only what it is entitled to and hidden information works",
            "game_step(state_ptr, state_len, moves_ptr, moves_len) -> i64": "apply one round of moves; moves is JSON keyed by seat. Return {\"state\": …, \"legal\": {\"0\": true}, \"note\": \"\"}",
            "game_done(state_ptr, state_len) -> i32": "1 when the match is over",
            "game_result(state_ptr, state_len) -> i64": "{\"scores\": [n per seat], \"summary\": \"…\"}",
        },
        "optional_exports": {
            "alloc(i32) -> i32": "required in practice — nothing can be passed in without it",
            "game_info() -> i64": "{\"name\", \"description\", \"min_players\", \"max_players\", \"max_turns\"}",
            "game_turn(state_ptr, state_len) -> i64": "{\"seats\": [n]} — who moves now. Return several seats for a simultaneous game. Left out, seats alternate.",
        },
        "example": "src/examples/rps/ — thirty lines of Rust, compiled with \
                    `cargo build --target wasm32-unknown-unknown --release`",
        "also": "call this with lang=class for the same game written as a Python class, or \
                 lang=rust for it as a Rust class — no pointers either way, and the same \
                 leaderboard for all three",
        "abi": common,
    })
}

/// Spawn the node runner and read its JSON back.
///
/// Everything that executes goes through here: a match, one turn of a table on
/// a game's own MCP server, one question put to an agent. The server never
/// runs a module itself, so this one function is the whole of how it makes
/// anything happen — and because the runner is the same file the browser
/// console imports, what happens is the same computation either way.
pub async fn runner(args: &[String]) -> Result<Value, String> {
    let runner = runner_path();
    if !runner.exists() {
        return Err(format!("no runner at {} — set ARENA_RUNNER", runner.display()));
    }
    let mut cmd = tokio::process::Command::new("node");
    cmd.arg(&runner).args(args).args(["--base", &base()]);

    let timeout = std::time::Duration::from_millis(
        std::env::var("ARENA_RUNNER_TIMEOUT_MS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(300_000u64)
            .clamp(1_000, 3_600_000),
    );
    let out = tokio::time::timeout(timeout, cmd.output())
        .await
        .map_err(|_| format!("the runner ran past {timeout:?} and was abandoned"))?
        .map_err(|e| format!("could not start node: {e} — the runner needs node on PATH"))?;

    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if !out.status.success() {
        let stderr = String::from_utf8_lossy(&out.stderr);
        return Err(format!(
            "the runner failed: {}",
            if stderr.trim().is_empty() { stdout.trim() } else { stderr.trim() }
        ));
    }
    serde_json::from_str(&stdout)
        .map_err(|e| format!("the runner returned unreadable output ({e}): {}", stdout.trim()))
}

/// Play a match by spawning the node runner — the same execution layer the
/// browser uses, so a match run from an MCP client and a match run in a tab
/// are the same computation.
async fn run_match(args: &Value) -> Result<Value, String> {
    let game = s(args, "game");
    let names = list_of(args, "players");
    if game.is_empty() || names.is_empty() {
        return Err("run_match needs `game` and at least one player in `players`".into());
    }
    let mut argv = vec![
        "match".to_string(),
        "--game".into(),
        game,
        "--players".into(),
        names.join(","),
        "--quiet".into(),
    ];
    if let Some(seed) = args.get("seed").and_then(|v| v.as_i64()) {
        argv.push("--seed".into());
        argv.push(seed.to_string());
    }
    if let Some(turns) = args.get("turns").and_then(|v| v.as_u64()) {
        argv.push("--turns".into());
        argv.push(turns.to_string());
    }
    // Opt-in, per match, and named: the classes in this match may call these
    // servers and no others. Left out, they have no way out at all.
    let allow = list_of(args, "mcp");
    if !allow.is_empty() {
        argv.push("--mcp".into());
        argv.push(allow.join(","));
    }
    runner(&argv).await
}

/// The one place an arena capability is implemented.
pub async fn call_tool(name: &str, args: &Value) -> Result<Value, String> {
    match name {
        "arena_info" => Ok(arena::info()),
        "game_abi" => Ok(game_abi(&s(args, "role"), &s(args, "lang"))),

        "list_modules" => Ok(arena::list_modules(args)),
        "get_module" => {
            let key = s(args, "module");
            if key.is_empty() {
                return Err("get_module requires `module`".into());
            }
            let with_source = args.get("source").and_then(|v| v.as_bool()).unwrap_or(true);
            arena::get_module(&key, with_source)
        }
        "put_module" => arena::put_module(args),
        "put_class" => arena::put_class(args),
        "inspect_module" => arena::inspect(args),
        "delete_module" => {
            let key = s(args, "module");
            if key.is_empty() {
                return Err("delete_module requires `module`".into());
            }
            arena::delete_module(&key)
        }

        "list_players" => Ok(arena::list_players(args)),
        "get_player" => {
            let key = s(args, "player");
            if key.is_empty() {
                return Err("get_player requires `player`".into());
            }
            arena::get_player(&key)
        }
        "enter_player" => arena::enter_player(args),
        "remove_player" => {
            let key = s(args, "player");
            if key.is_empty() {
                return Err("remove_player requires `player`".into());
            }
            arena::remove_player(&key)
        }

        "run_match" => run_match(args).await,
        "play_move" => {
            let key = s(args, "player");
            let view = args.get("view").and_then(|v| v.as_str()).unwrap_or("");
            if key.is_empty() || view.trim().is_empty() {
                return Err("play_move requires `player` and `view`".into());
            }
            arena::play(&key, view, u(args, "seat", 0) as usize).await
        }
        "record_match" => arena::record_match(args),
        "list_matches" => Ok(arena::list_matches(args)),
        "get_match" => {
            let id = s(args, "id");
            if id.is_empty() {
                return Err("get_match requires `id`".into());
            }
            arena::get_match(&id)
        }
        "leaderboard" => arena::leaderboard(args),
        "plant_examples" => Ok(arena::plant_examples()),

        "module_tool" => {
            let key = s(args, "module");
            let tool = s(args, "tool");
            if key.is_empty() || tool.is_empty() {
                return Err("module_tool requires `module` and `tool` — call list_modules \
                            to see which modules there are; every stored module answers \
                            on /m/<name>/mcp"
                    .into());
            }
            let inner = args.get("arguments").cloned().unwrap_or_else(|| json!({}));
            crate::modmcp::call_tool(&key, &tool, &inner).await
        }

        "docs_pages" => Ok(crate::docs::index()),
        "docs_page" => crate::docs::page(args),
        "docs_search" => Ok(crate::docs::search(args)),

        "mcp_servers" => Ok(crate::mcpout::list()),
        "mcp_call" => {
            if s(args, "server").is_empty() {
                return Err("mcp_call requires `server` — a name from mcp_servers, never a URL"
                    .into());
            }
            Ok(crate::mcpout::call(args).await)
        }
        "fleet_modules" => {
            let name = s(args, "module");
            if name.is_empty() {
                Ok(crate::mcpout::fleet().await)
            } else {
                let server = crate::mcpout::resolve(None, Some(&name), None)?;
                let tools = crate::mcpout::tools_of(&server).await?;
                Ok(json!({ "module": name, "mcp": server.url, "count": tools.len(), "tools": tools }))
            }
        }
        "rust_toolchain" => Ok(crate::rustc::toolchain()),
        "vibe" => crate::vibe::vibe(args).await,
        "fork_module" => {
            let key = s(args, "module");
            if key.is_empty() {
                return Err("fork_module requires `module`".into());
            }
            let mut a = json!({ "from": key });
            if let Some(n) = args.get("name") {
                a["name"] = n.clone();
            }
            crate::vibe::vibe(&a).await
        }
        "get_vibe" => crate::vibe::get(&s(args, "session")).await,
        "list_vibes" => {
            let mut v = crate::vibe::list();
            v["build"] = crate::vibe::availability().await;
            Ok(v)
        }
        "store_vibe" => crate::vibe::store(args).await,
        "cancel_vibe" => crate::vibe::cancel(&s(args, "session")).await,
        "store_status" => Ok(crate::storelink::status().await),
        "store_sync" => Ok(crate::storelink::sync(args).await),

        other => Err(format!(
            "unknown tool: {other} — this arena stores wasm, seats {:?} at modules that \
             export {:?}, and runs them in the browser or the node runner",
            players::KINDS,
            wasm::GAME_EXPORTS,
        )),
    }
}

fn rpc_result(id: Value, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "result": result })
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// Handle one JSON-RPC message. Returns None for notifications (no reply).
pub async fn handle_message(msg: &Value) -> Option<Value> {
    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or_else(|| json!({}));

    let id = match msg.get("id").cloned() {
        Some(id) if !id.is_null() => id,
        _ => return None,
    };

    Some(match method {
        "initialize" => rpc_result(
            id,
            json!({
                "protocolVersion": params.get("protocolVersion").and_then(|v| v.as_str()).unwrap_or(PROTOCOL_VERSION),
                "capabilities": { "tools": {}, "resources": {} },
                "serverInfo": { "name": SERVER_NAME, "version": SERVER_VERSION },
                "instructions": INSTRUCTIONS
            }),
        ),
        "ping" => rpc_result(id, json!({})),
        "tools/list" => rpc_result(id, json!({ "tools": tool_list() })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
            match call_tool(name, &args).await {
                Ok(v) => rpc_result(
                    id,
                    json!({
                        "content": [{ "type": "text", "text": serde_json::to_string(&v).unwrap_or_default() }],
                        "structuredContent": v,
                        "isError": false
                    }),
                ),
                Err(e) => rpc_result(
                    id,
                    json!({ "content": [{ "type": "text", "text": e }], "isError": true }),
                ),
            }
        }
        "resources/list" => rpc_result(id, json!({ "resources": crate::docs::resource_list() })),
        "resources/read" => {
            let uri = params.get("uri").and_then(|u| u.as_str()).unwrap_or("");
            match crate::docs::resource_read(uri) {
                Ok(contents) => rpc_result(id, json!({ "contents": contents })),
                Err(e) => rpc_error(id, -32602, &e),
            }
        }
        "resources/templates/list" => rpc_result(
            id,
            json!({ "resourceTemplates": [{
                "uriTemplate": "arena://docs/{slug}",
                "name": "arena docs",
                "description": "One documentation page as markdown — docs_pages lists the slugs",
                "mimeType": "text/markdown"
            }] }),
        ),
        "prompts/list" => rpc_result(id, json!({ "prompts": [] })),
        _ => rpc_error(id, -32601, &format!("method not found: {method}")),
    })
}

/// stdio transport: newline-delimited JSON-RPC on stdin/stdout.
/// Usage: arena-api --stdio
pub async fn run_stdio() {
    let stdin = BufReader::new(tokio::io::stdin());
    let mut stdout = tokio::io::stdout();
    let mut lines = stdin.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim().to_string();
        if line.is_empty() {
            continue;
        }
        let msg: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                let err = rpc_error(Value::Null, -32700, &format!("parse error: {e}"));
                let _ = stdout.write_all(format!("{err}\n").as_bytes()).await;
                let _ = stdout.flush().await;
                continue;
            }
        };
        if let Some(resp) = handle_message(&msg).await {
            let _ = stdout.write_all(format!("{resp}\n").as_bytes()).await;
            let _ = stdout.flush().await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_tool_has_a_description_and_a_schema() {
        for t in tool_list().as_array().expect("a list") {
            let name = t["name"].as_str().expect("a name");
            assert!(t["description"].as_str().unwrap_or("").len() > 40, "{name} needs a real description");
            assert_eq!(t["inputSchema"]["type"], "object", "{name} schema");
        }
    }

    #[test]
    fn a_comma_separated_list_is_still_a_list() {
        assert_eq!(list_of(&json!({ "players": "a, b ,c" }), "players"), vec!["a", "b", "c"]);
        assert_eq!(list_of(&json!({ "players": ["a", "b"] }), "players"), vec!["a", "b"]);
        assert!(list_of(&json!({}), "players").is_empty());
    }
}
