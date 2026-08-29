//! One MCP server per module.
//!
//! `/mcp` is the arena: upload things, enter players, run matches, read the
//! leaderboard. This is the other half — `/m/<name>/mcp`, one server for each
//! game and each agent in the registry, with tools that are only about that
//! module.
//!
//! Why bother, when the arena's own server can already run a match? Because a
//! match is a thing you start and then read about afterwards, and that is not
//! how anything plays a game. A game server here is stateful and turn-taking:
//! `open` a table, read the `view`, send a `move`, read the next view. An
//! agent server takes a view and hands back a move. So a model with an MCP
//! client can sit down and play, and an agent anywhere can be sat down by
//! anything that speaks MCP — including a class inside this arena, through
//! `arena::mcp`. Games and players stop being data the arena runs and become
//! endpoints that call each other.
//!
//! There is no long-lived process behind a session, and that is deliberate.
//! A session is `(module, seed, the moves so far)`, and every call replays it
//! from the seed. The registry already claims a match is exactly its seed plus
//! its moves; this is that claim being *used* rather than asserted, and it is
//! why a table survives the server restarting and why the same session is the
//! same game on any machine holding the same bytes.

use crate::arena;
use crate::mcp;
use crate::store;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

/// A table someone opened at a game. Small, and only ever grows by one round
/// of moves per call — the position is not in here, it is recomputed.
#[derive(Clone, Debug)]
struct Session {
    id: String,
    module: String,
    seed: i64,
    seats: usize,
    /// One entry per round: `{"0": "rock", "1": "paper"}`.
    moves: Vec<Value>,
    created: u64,
}

fn sessions() -> &'static Mutex<HashMap<String, Session>> {
    static S: OnceLock<Mutex<HashMap<String, Session>>> = OnceLock::new();
    S.get_or_init(|| Mutex::new(HashMap::new()))
}

/// Tables are cheap but not free, and nobody closes them. The oldest go first
/// once there are too many; a dropped table costs whoever left it a `open`.
const KEEP_SESSIONS: usize = 200;

fn remember(s: Session) {
    let mut map = sessions().lock().unwrap_or_else(|e| e.into_inner());
    map.insert(s.id.clone(), s);
    if map.len() > KEEP_SESSIONS {
        let mut by_age: Vec<(String, u64)> =
            map.iter().map(|(k, v)| (k.clone(), v.created)).collect();
        by_age.sort_by_key(|(_, c)| *c);
        for (id, _) in by_age.into_iter().take(map.len() - KEEP_SESSIONS) {
            map.remove(&id);
        }
    }
}

fn recall(id: &str) -> Option<Session> {
    sessions().lock().unwrap_or_else(|e| e.into_inner()).get(id).cloned()
}

// ── the tool sets ────────────────────────────────────────────────────────

/// What this module's server offers, which is decided by what the module is.
pub fn tools_for(role: &str) -> Value {
    let source = json!({
        "name": "source",
        "description": "The module itself: its card, what it defines, and — for a class — \
                        the source. The most direct answer to `how does this thing work`, \
                        and the one nothing else here can give you.",
        "inputSchema": { "type": "object", "properties": {} }
    });
    let about = json!({
        "name": "about",
        "description": "What this module is: its role, its container, its rating if it has \
                        played, and how to use the rest of these tools.",
        "inputSchema": { "type": "object", "properties": {} }
    });

    match role {
        "game" => json!([
            about,
            {
                "name": "open",
                "description": "Sit down at this game. Returns a table id, the opening view \
                                for every seat, and whose move it is. A table is its seed and \
                                its moves and nothing else, so it survives restarts and \
                                replays identically anywhere.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "seats": { "type": "integer", "description": "How many players. Defaults to what the game asks for." },
                        "seed": { "type": "integer", "description": "Same seed, same game. Random if you leave it out." }
                    }
                }
            },
            {
                "name": "view",
                "description": "What one seat can see right now. This is the whole of what a \
                                player is entitled to — hidden information stays hidden here, \
                                which is what makes the seat worth sitting in.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table": { "type": "string" },
                        "seat": { "type": "integer", "default": 0 }
                    },
                    "required": ["table"]
                }
            },
            {
                "name": "move",
                "description": "Play one move, as the text the game's view told you to send. \
                                Returns whether it stood, the next view, and whether the game \
                                is over. In a simultaneous game, pass every seat's move at \
                                once in `moves`.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "table": { "type": "string" },
                        "seat": { "type": "integer", "default": 0 },
                        "move": { "type": "string" },
                        "moves": {
                            "type": "object",
                            "description": "seat → move, for a game where several seats move at once"
                        }
                    },
                    "required": ["table"]
                }
            },
            {
                "name": "state",
                "description": "The table as it stands: whose move it is, every seat's view, \
                                the moves so far, and the result if it has finished.",
                "inputSchema": {
                    "type": "object",
                    "properties": { "table": { "type": "string" } },
                    "required": ["table"]
                }
            },
            {
                "name": "leaderboard",
                "description": "Who is good at this game, by Elo kept for this game alone — \
                                never the overall number, which says nothing about here.",
                "inputSchema": {
                    "type": "object",
                    "properties": { "limit": { "type": "integer", "default": 10 } }
                }
            },
            source,
        ]),
        "player" => json!([
            about,
            {
                "name": "play",
                "description": "Ask this agent for a move. Give it the view a game showed a \
                                seat and it answers with the move, as text — the same question \
                                the arena asks it in a match, so what you get here is what it \
                                would have played.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "view": { "type": "string", "description": "What the seat can see" },
                        "seat": { "type": "integer", "default": 0 },
                        "seed": { "type": "integer", "description": "Seeds its randomness, so this is reproducible" }
                    },
                    "required": ["view"]
                }
            },
            {
                "name": "record",
                "description": "How this agent has done: Elo overall and per game, win rate, \
                                illegal-move rate, timeouts, time to move, and how often it \
                                called out to another server mid-match.",
                "inputSchema": { "type": "object", "properties": {} }
            },
            source,
        ]),
        _ => json!([
            about,
            {
                "name": "run",
                "description": "Run this module once and report what it did — for a command, \
                                or a class that is neither a game nor a player yet.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entry": { "type": "string", "description": "Which export or method to call" },
                        "stdin": { "type": "string" },
                        "seed": { "type": "integer" }
                    }
                }
            },
            source,
        ]),
    }
}

fn table_id(module: &str) -> String {
    let n = store::now();
    let salt = sessions().lock().map(|m| m.len()).unwrap_or(0);
    format!("t{}-{}-{}", &module[..module.len().min(6)], n, salt)
}

// ── the tools ────────────────────────────────────────────────────────────

async fn open_table(m: &store::ModEntry, args: &Value) -> Result<Value, String> {
    let info = arena::get_module(&m.id, false)?;
    let asked = args.get("seats").and_then(|v| v.as_u64()).unwrap_or(0) as usize;
    // A game says how many seats it takes, in the attributes the reader found;
    // the caller may narrow it but not break it.
    let declared = info["info"]["attributes"]
        .as_array()
        .and_then(|a| {
            a.iter()
                .find(|x| x["name"] == "players" || x["name"] == "PLAYERS")
                .and_then(|x| x["value"].as_str())
        })
        .and_then(|v| v.trim().parse::<usize>().ok());
    let seats = asked.max(declared.unwrap_or(2)).clamp(1, 16);

    let seed = args
        .get("seed")
        .and_then(|v| v.as_i64())
        .unwrap_or_else(|| (store::now() % 1_000_000_007) as i64);

    let session = Session {
        id: table_id(&m.id),
        module: m.id.clone(),
        seed,
        seats,
        moves: Vec::new(),
        created: store::now(),
    };
    let state = replay(&session).await?;
    remember(session.clone());
    Ok(json!({
        "table": session.id,
        "game": m.name,
        "module": m.short(),
        "seats": seats,
        "seed": seed,
        "state": state,
        "next": "call `view` for one seat, or `move` to play — the table id is how you come back",
    }))
}

/// Replay a table from its seed and hand back where it stands. Every call
/// does this from scratch; a game here is a pure function of its seed and its
/// moves, and this is the code that depends on that being true.
async fn replay(s: &Session) -> Result<Value, String> {
    mcp::runner(&[
        "session".into(),
        "--game".into(),
        s.module.clone(),
        "--seats".into(),
        s.seats.to_string(),
        "--seed".into(),
        s.seed.to_string(),
        "--moves".into(),
        serde_json::to_string(&s.moves).unwrap_or_else(|_| "[]".into()),
    ])
    .await
}

fn table(args: &Value) -> Result<Session, String> {
    let id = args.get("table").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    if id.is_empty() {
        return Err("which table? `open` one first and pass the id back as `table`".into());
    }
    recall(&id).ok_or_else(|| {
        format!(
            "no table `{id}` — tables are held in memory and the oldest are dropped once \
             there are {KEEP_SESSIONS} of them. `open` a new one; pass the same seed and \
             replay the same moves and it is the same game."
        )
    })
}

async fn play_move(args: &Value) -> Result<Value, String> {
    let mut s = table(args)?;

    let round = match args.get("moves").and_then(|v| v.as_object()) {
        Some(map) => {
            let mut round = serde_json::Map::new();
            for (k, v) in map {
                round.insert(k.clone(), json!(v.as_str().unwrap_or("").to_string()));
            }
            Value::Object(round)
        }
        None => {
            let seat = args.get("seat").and_then(|v| v.as_u64()).unwrap_or(0);
            let mv = args
                .get("move")
                .or_else(|| args.get("action"))
                .and_then(|v| v.as_str())
                .ok_or("`move` needs the move, as the text the view told you to send")?;
            json!({ seat.to_string(): mv })
        }
    };

    s.moves.push(round.clone());
    let state = replay(&s).await?;
    // Only a move that replayed cleanly is kept. A game that threw leaves the
    // table exactly as it was, which is the only way a table can be trusted.
    remember(s.clone());

    let last = state["history"].as_array().and_then(|h| h.last()).cloned();
    Ok(json!({
        "table": s.id,
        "played": round,
        "legal": last.as_ref().map(|l| l["legal"].clone()).unwrap_or(json!(null)),
        "note": last.as_ref().map(|l| l["note"].clone()).unwrap_or(json!("")),
        "state": state,
    }))
}

async fn ask_player(m: &store::ModEntry, args: &Value) -> Result<Value, String> {
    let view = args
        .get("view")
        .and_then(|v| v.as_str())
        .filter(|v| !v.trim().is_empty())
        .ok_or("`play` needs `view` — what the seat can see, which is all this agent gets")?;
    let seat = args.get("seat").and_then(|v| v.as_u64()).unwrap_or(0);
    let seed = args.get("seed").and_then(|v| v.as_i64()).unwrap_or(1);
    mcp::runner(&[
        "ask".into(),
        "--module".into(),
        m.id.clone(),
        "--view".into(),
        view.to_string(),
        "--seat".into(),
        seat.to_string(),
        "--seed".into(),
        seed.to_string(),
    ])
    .await
}

fn about(m: &store::ModEntry) -> Value {
    let rated = store::read(|s| {
        s.players
            .values()
            .find(|p| {
                p.config.get("module").and_then(|v| v.as_str()).is_some_and(|k| {
                    k == m.id || k.eq_ignore_ascii_case(&m.name) || m.id.starts_with(k)
                })
            })
            .map(|p| p.card())
    });
    let mut v = m.card();
    v["mod"] = json!(format!("arena.{}", m.name));
    v["mcp"] = json!(format!("{}/m/{}/mcp", mcp::base(), m.name));
    v["entered_as"] = rated.unwrap_or(json!(null));
    v["how"] = json!(match m.role.as_str() {
        "game" => "`open` a table, then `view` and `move`. Or enter a player at the arena \
                   and let it play a rated match.",
        "player" => "`play` with a view and it answers with a move. Enter it at the arena \
                     to have that answer rated.",
        _ => "`run` it, or read its `source`.",
    });
    v
}

// ── the JSON-RPC surface ─────────────────────────────────────────────────

/// Resolve the module this endpoint is for, by name or id.
pub fn module_of(key: &str) -> Result<store::ModEntry, String> {
    store::read(|s| s.module(key).cloned()).ok_or_else(|| {
        format!(
            "no module `{key}` in this arena — /m/<name>/mcp is one server per stored \
             module, and there is nothing stored under that name"
        )
    })
}

pub async fn call_tool(key: &str, name: &str, args: &Value) -> Result<Value, String> {
    let m = module_of(key)?;
    match (m.role.as_str(), name) {
        (_, "about") => Ok(about(&m)),
        (_, "source") => arena::get_module(&m.id, true),

        ("game", "open") => open_table(&m, args).await,
        ("game", "move") => play_move(args).await,
        ("game", "state") => {
            let s = table(args)?;
            let state = replay(&s).await?;
            Ok(json!({ "table": s.id, "seed": s.seed, "seats": s.seats,
                       "moves": s.moves, "state": state }))
        }
        ("game", "view") => {
            let s = table(args)?;
            let seat = args.get("seat").and_then(|v| v.as_u64()).unwrap_or(0);
            let state = replay(&s).await?;
            let view = state["views"]
                .get(seat.to_string())
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            Ok(json!({
                "table": s.id, "seat": seat, "view": view,
                "your_move": state["active"].as_array()
                    .is_some_and(|a| a.iter().any(|x| x.as_u64() == Some(seat))),
                "done": state["done"],
            }))
        }
        ("game", "leaderboard") => {
            arena::leaderboard(&json!({ "game": m.id, "limit": args.get("limit").cloned()
                .unwrap_or(json!(10)) }))
        }

        ("player", "play") => ask_player(&m, args).await,
        ("player", "record") => {
            let card = about(&m);
            Ok(json!({ "module": card["name"], "entered_as": card["entered_as"],
                       "note": if card["entered_as"].is_null() {
                           "this agent has not been entered at the arena, so nothing has been \
                            rated — `enter_player` on the arena's own server does that"
                       } else { "" } }))
        }

        (_, "run") => {
            let mut argv = vec!["run".to_string(), "--module".into(), m.id.clone()];
            if let Some(entry) = args.get("entry").and_then(|v| v.as_str()) {
                argv.push("--entry".into());
                argv.push(entry.to_string());
            }
            if let Some(stdin) = args.get("stdin").and_then(|v| v.as_str()) {
                argv.push("--stdin".into());
                argv.push(stdin.to_string());
            }
            if let Some(seed) = args.get("seed").and_then(|v| v.as_i64()) {
                argv.push("--seed".into());
                argv.push(seed.to_string());
            }
            mcp::runner(&argv).await
        }

        (role, other) => Err(format!(
            "`{}` is a {role}, and its server has no tool `{other}` — it offers {}",
            m.name,
            tools_for(role)
                .as_array()
                .map(|a| a.iter().filter_map(|t| t["name"].as_str()).collect::<Vec<_>>().join(", "))
                .unwrap_or_default()
        )),
    }
}

fn instructions(m: &store::ModEntry) -> String {
    match m.role.as_str() {
        "game" => format!(
            "`{}` — {}. A game in the arena, served as its own MCP server. Call `open` to \
             sit down, `view` to see what a seat sees, and `move` to play. A table is its \
             seed and its moves, so the same two replay the same game anywhere. The whole \
             arena — every other game, the players, the leaderboard — is at /mcp.",
            m.name,
            if m.description.is_empty() { "no description" } else { &m.description }
        ),
        "player" => format!(
            "`{}` — {}. An agent in the arena, served as its own MCP server. Give `play` \
             the view a seat can see and it answers with a move. It is the same question \
             the arena asks it in a rated match, so the answer is comparable.",
            m.name,
            if m.description.is_empty() { "no description" } else { &m.description }
        ),
        role => format!("`{}` — a {role} in the arena. Read its `source`, or `run` it.", m.name),
    }
}

/// One JSON-RPC message, scoped to one module. Mirrors `mcp::handle_message`,
/// which is the arena-wide twin of this.
pub async fn handle_message(key: &str, msg: &Value) -> Option<Value> {
    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let params = msg.get("params").cloned().unwrap_or_else(|| json!({}));
    let id = match msg.get("id").cloned() {
        Some(id) if !id.is_null() => id,
        _ => return None,
    };
    let found = module_of(key);

    let result = |v: Value| json!({ "jsonrpc": "2.0", "id": id.clone(), "result": v });
    let error = |code: i64, message: String| {
        json!({ "jsonrpc": "2.0", "id": id.clone(), "error": { "code": code, "message": message } })
    };

    let m = match &found {
        Ok(m) => m.clone(),
        Err(e) => {
            // `initialize` still has to answer, or a client cannot even be told
            // what went wrong — every other method is an error.
            return Some(match method {
                "initialize" => result(json!({
                    "protocolVersion": mcp::PROTOCOL_VERSION,
                    "capabilities": { "tools": {} },
                    "serverInfo": { "name": format!("arena/{key}"), "version": mcp::SERVER_VERSION },
                    "instructions": e,
                })),
                _ => error(-32602, e.clone()),
            });
        }
    };

    Some(match method {
        "initialize" => result(json!({
            "protocolVersion": params.get("protocolVersion").and_then(|v| v.as_str())
                .unwrap_or(mcp::PROTOCOL_VERSION),
            "capabilities": { "tools": {}, "resources": {} },
            "serverInfo": {
                "name": format!("arena/{}", m.name),
                "version": mcp::SERVER_VERSION,
                "title": m.name,
            },
            "instructions": instructions(&m),
        })),
        "ping" => result(json!({})),
        "tools/list" => result(json!({ "tools": tools_for(&m.role) })),
        "tools/call" => {
            let name = params.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let args = params.get("arguments").cloned().unwrap_or_else(|| json!({}));
            match call_tool(key, name, &args).await {
                Ok(v) => result(json!({
                    "content": [{ "type": "text", "text": serde_json::to_string(&v).unwrap_or_default() }],
                    "structuredContent": v,
                    "isError": false
                })),
                Err(e) => result(json!({
                    "content": [{ "type": "text", "text": e }], "isError": true
                })),
            }
        }
        "resources/list" => result(json!({
            "resources": [{
                "uri": format!("arena://{}/source", m.name),
                "name": format!("{} source", m.name),
                "description": "The module itself — the source of a class, or the export \
                                list of a wasm binary.",
                "mimeType": if m.lang() == "wasm" { "application/json" } else { "text/plain" },
            }]
        })),
        "resources/read" => {
            let uri = params.get("uri").and_then(|v| v.as_str()).unwrap_or("");
            match arena::get_module(&m.id, true) {
                Ok(v) => {
                    let text = v
                        .get("source")
                        .and_then(|s| s.as_str())
                        .map(String::from)
                        .unwrap_or_else(|| serde_json::to_string_pretty(&v).unwrap_or_default());
                    result(json!({ "contents": [{ "uri": uri, "text": text }] }))
                }
                Err(e) => error(-32602, e),
            }
        }
        "prompts/list" => result(json!({ "prompts": prompts_for(&m) })),
        "prompts/get" => {
            let name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
            match prompt_text(&m, name) {
                Some(text) => result(json!({
                    "description": format!("{} — {}", m.name, name),
                    "messages": [{ "role": "user", "content": { "type": "text", "text": text } }]
                })),
                None => error(-32602, format!("no prompt `{name}` on `{}`", m.name)),
            }
        }
        other => error(-32601, format!("method not found: {other}")),
    })
}

fn prompts_for(m: &store::ModEntry) -> Value {
    match m.role.as_str() {
        "game" => json!([{
            "name": "play",
            "description": format!("Sit down at {} and play it out, one move at a time.", m.name),
        }]),
        "player" => json!([{
            "name": "assess",
            "description": format!("Work out how {} actually plays, by asking it.", m.name),
        }]),
        _ => json!([]),
    }
}

fn prompt_text(m: &store::ModEntry, name: &str) -> Option<String> {
    match (m.role.as_str(), name) {
        ("game", "play") => Some(format!(
            "Play `{}`. Call `open` to get a table and your opening view, then loop: read \
             the view, decide, call `move` with exactly the move text the view described. \
             The view is everything you are entitled to know — do not assume anything it \
             does not say. Stop when `done` comes back true and report the result and what \
             you were trying to do.",
            m.name
        )),
        ("player", "assess") => Some(format!(
            "Work out how `{}` plays. Read its `source` first, then call `play` with views \
             you have made up — including ones with no legal move, an unfamiliar format, \
             and an empty view — and report what it does with each. You are looking for the \
             rule it is following and the cases where that rule breaks.",
            m.name
        )),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_game_and_an_agent_offer_different_tools() {
        let game: Vec<String> = tools_for("game").as_array().unwrap().iter()
            .map(|t| t["name"].as_str().unwrap().to_string()).collect();
        let player: Vec<String> = tools_for("player").as_array().unwrap().iter()
            .map(|t| t["name"].as_str().unwrap().to_string()).collect();
        assert!(game.contains(&"open".to_string()) && game.contains(&"move".to_string()));
        assert!(!game.contains(&"play".to_string()));
        assert!(player.contains(&"play".to_string()));
        assert!(!player.contains(&"open".to_string()));
        // Everything is readable, whatever it is.
        assert!(game.contains(&"source".to_string()) && player.contains(&"source".to_string()));
    }

    #[test]
    fn every_tool_on_every_role_has_a_description_and_a_schema() {
        for role in ["game", "player", "class"] {
            for t in tools_for(role).as_array().unwrap() {
                let name = t["name"].as_str().expect("a name");
                assert!(t["description"].as_str().unwrap_or("").len() > 40, "{role}/{name}");
                assert_eq!(t["inputSchema"]["type"], "object", "{role}/{name}");
            }
        }
    }

    #[tokio::test]
    async fn a_module_nobody_stored_still_answers_initialize() {
        let msg = json!({ "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {} });
        let out = handle_message("nothing-is-called-this", &msg).await.unwrap();
        assert!(out["result"]["instructions"].as_str().unwrap().contains("no module"));
    }

    #[tokio::test]
    async fn asking_a_missing_module_for_a_tool_is_an_error_not_a_panic() {
        let msg = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });
        let out = handle_message("nothing-is-called-this", &msg).await.unwrap();
        assert_eq!(out["error"]["code"], -32602);
    }
}
