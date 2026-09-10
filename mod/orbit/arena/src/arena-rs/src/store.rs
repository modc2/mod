//! Registry state — modules, players and matches.
//!
//! One JSON document beside the blobs under ~/.mod/arena/. The document holds
//! what a module *is*; the bytes live in the blob store keyed by the same id,
//! so the index can be rebuilt from the blobs and the blobs are never orphaned
//! by an index write that failed.
//!
//! There is no separate "game" table on purpose. A game is a module whose
//! exports match the game ABI — uploading the wasm is the whole act of making
//! one. Same for a bot player. That is what keeps this modular: the arena has
//! no list of known games to add yourself to.

use crate::blobs;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

/// Every player starts here; only rated matches move it.
pub const START_ELO: f64 = 1200.0;

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

// ── modules ──────────────────────────────────────────────────────────────

/// A stored wasm module. `info` is the structural read from `wasm::describe`,
/// kept so listings and the runner never have to touch the bytes.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct WasmModule {
    /// SHA-256 of the bytes, in hex. Also the blob key.
    pub id: String,
    #[serde(default)]
    pub name: String,
    /// game | player | command | wasm — from the export set, not from the uploader.
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub size: usize,
    #[serde(default)]
    pub info: Value,
    /// example | upload — examples ship with the module and are replanted.
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub runs: u64,
    #[serde(default)]
    pub created: u64,
    /// The store module's CID for the same bytes — empty until pushed.
    #[serde(default)]
    pub cid: String,
    /// Blob id of a readable source kept beside compiled bytes (the Rust a
    /// wasm example was built from). Empty for a class: its bytes *are* the
    /// source. Empty for a wasm upload that came without one.
    #[serde(default)]
    pub src: String,
    /// The store's CID for that source, when it has one.
    #[serde(default)]
    pub src_cid: String,
    /// When the store copy was last written.
    #[serde(default)]
    pub stored: u64,
}

impl WasmModule {
    /// What container these bytes are: `wasm`, or `python` / `rust` for a
    /// class. Read off the description the reader wrote, so an entry stored
    /// before classes existed still answers `wasm`.
    pub fn lang(&self) -> &str {
        self.info.get("lang").and_then(|v| v.as_str()).unwrap_or("wasm")
    }

    pub fn card(&self) -> Value {
        json!({
            "id": self.id,
            "short": self.short(),
            "name": self.name,
            "role": self.role,
            "lang": self.lang(),
            "class": self.info.get("class").and_then(|v| v.as_str()),
            "description": self.description,
            "author": self.author,
            "tags": self.tags,
            "size": self.size,
            "runs": self.runs,
            // Where it came from: `example` (the pack) or `upload`. Not the code —
            // that is `source` on get_module, present only when there is code.
            "origin": self.source,
            "created": self.created,
            // Two hashes of the same bytes: this registry's and the store's.
            "sha256": self.id,
            "cid": if self.cid.is_empty() { Value::Null } else { json!(self.cid) },
            "store": crate::storelink::card(&self.cid),
            "has_source": self.lang() != "wasm" || !self.src.is_empty(),
            "src_cid": if self.src_cid.is_empty() { Value::Null } else { json!(self.src_cid) },
            "exports": self.info.get("exports").and_then(|e| e.as_array())
                .map(|a| a.iter().filter_map(|e| e.get("name").and_then(|n| n.as_str()))
                    .map(String::from).collect::<Vec<_>>())
                .unwrap_or_default(),
            "host_needs": self.info.get("host_needs").cloned().unwrap_or(json!([])),
        })
    }

    pub fn short(&self) -> String {
        self.id.chars().take(12).collect()
    }

    /// The URL the browser fetches the bytes from — relative, so it works
    /// under the fleet router and standalone alike.
    pub fn url(&self) -> String {
        format!("blob/{}", self.id)
    }
}

// ── players ──────────────────────────────────────────────────────────────

/// Per-game skill, so "good at tic-tac-toe" never launders into "good at
/// everything". The overall number is the one on the front page; these are
/// the ones that mean something.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Rating {
    pub elo: f64,
    #[serde(default)]
    pub matches: u64,
    #[serde(default)]
    pub wins: u64,
    #[serde(default)]
    pub draws: u64,
    #[serde(default)]
    pub losses: u64,
    #[serde(default)]
    pub score_sum: f64,
}

impl Default for Rating {
    fn default() -> Self {
        Rating {
            elo: START_ELO,
            matches: 0,
            wins: 0,
            draws: 0,
            losses: 0,
            score_sum: 0.0,
        }
    }
}

impl Rating {
    pub fn card(&self) -> Value {
        let n = self.matches.max(1) as f64;
        json!({
            "elo": round1(self.elo),
            "matches": self.matches, "wins": self.wins, "draws": self.draws, "losses": self.losses,
            "win_rate": if self.matches == 0 { 0.0 } else { round3(self.wins as f64 / n) },
            "avg_score": if self.matches == 0 { 0.0 } else { round3(self.score_sum / n) },
        })
    }
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Player {
    pub id: String,
    pub name: String,
    /// wasm | class | model | agent_mod | mcp | http | human
    pub kind: String,
    #[serde(default)]
    pub owner: String,
    #[serde(default)]
    pub note: String,
    /// Driver settings. A `wasm` player carries `module`; the rest carry
    /// whatever their driver needs (url, model, base, key…).
    #[serde(default)]
    pub config: Value,
    #[serde(default)]
    pub overall: Rating,
    /// Keyed by game module id.
    #[serde(default)]
    pub by_game: HashMap<String, Rating>,
    // ── the assessment, beyond who won ──
    #[serde(default)]
    pub moves: u64,
    /// A move the game refused. The single most telling number for a model.
    #[serde(default)]
    pub illegal: u64,
    /// A move that never arrived in time — the arena played a forfeit for it.
    #[serde(default)]
    pub timeouts: u64,
    /// Times this player called out to an MCP server mid-match. Not a fault —
    /// it is allowed, and the whole point of the outward door — but a player
    /// that consulted something is not the same kind of player as one that
    /// did not, and a leaderboard that hides the difference is lying.
    #[serde(default)]
    pub mcp: u64,
    #[serde(default)]
    pub move_ms_sum: u64,
    #[serde(default)]
    pub created: u64,
}

impl Player {
    pub fn card(&self) -> Value {
        let moves = self.moves.max(1) as f64;
        let mut v = self.overall.card();
        v["id"] = json!(self.id);
        v["name"] = json!(self.name);
        v["kind"] = json!(self.kind);
        v["owner"] = json!(self.owner);
        v["note"] = json!(self.note);
        v["created"] = json!(self.created);
        v["moves"] = json!(self.moves);
        v["illegal"] = json!(self.illegal);
        v["timeouts"] = json!(self.timeouts);
        v["mcp"] = json!(self.mcp);
        v["illegal_rate"] = json!(if self.moves == 0 { 0.0 } else { round3(self.illegal as f64 / moves) });
        v["avg_move_ms"] = json!(if self.moves == 0 { 0 } else { self.move_ms_sum / self.moves });
        v["games_played"] = json!(self.by_game.len());
        // A model's identity is its model string; surfacing it saves a click.
        if let Some(m) = self.config.get("model").and_then(|m| m.as_str()) {
            v["model"] = json!(m);
        }
        // `module` means two different things and they must not be confused:
        // for a wasm or class player it is a module stored *here*, by id; for
        // an mcp player it is a module of the fleet, by name, which this arena
        // holds no bytes for.
        if let Some(m) = self.config.get("module").and_then(|m| m.as_str()) {
            match self.kind.as_str() {
                "mcp" | "module" => v["via"] = json!(m),
                _ => v["module"] = json!(m),
            }
        }
        if self.kind == "mcp" {
            for key in ["server", "tool", "url"] {
                if let Some(x) = self.config.get(key).and_then(|v| v.as_str()) {
                    v[key] = json!(x);
                }
            }
        }
        // What a server-driven player is told each move, so the players tab
        // can say it without a click. The full template is on get_player.
        if let Some(pc) = crate::players::prompt_card(self) {
            v["system"] = pc["system"].clone();
            v["brief"] = pc["brief"].clone();
        }
        v
    }
}

// ── matches ──────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Seat {
    pub seat: usize,
    pub player_id: String,
    pub player_name: String,
    /// The game's own number for this seat, however it scores.
    pub score: f64,
    #[serde(default)]
    pub moves: u64,
    #[serde(default)]
    pub illegal: u64,
    #[serde(default)]
    pub timeouts: u64,
    #[serde(default)]
    pub ms: u64,
    /// MCP calls made from this seat during the match.
    #[serde(default)]
    pub mcp: u64,
    #[serde(default)]
    pub elo_before: f64,
    #[serde(default)]
    pub elo_after: f64,
    #[serde(default)]
    pub error: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Turn {
    pub turn: u64,
    pub seat: usize,
    #[serde(default)]
    pub view: String,
    #[serde(default)]
    pub raw: String,
    #[serde(default)]
    pub mv: String,
    #[serde(default)]
    pub legal: bool,
    #[serde(default)]
    pub ms: u64,
    #[serde(default)]
    pub note: String,
    /// What a server-driven player was asked this turn, verbatim. Empty for
    /// wasm/class players, which see the view directly and get no prompt.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub prompt: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Match {
    pub id: String,
    pub game: String,
    #[serde(default)]
    pub game_name: String,
    #[serde(default)]
    pub seed: i64,
    pub seats: Vec<Seat>,
    #[serde(default)]
    pub turns: Vec<Turn>,
    #[serde(default)]
    pub summary: String,
    /// browser | node — where the wasm actually executed.
    #[serde(default)]
    pub runtime: String,
    #[serde(default)]
    pub rated: bool,
    #[serde(default)]
    pub ms: u64,
    #[serde(default)]
    pub created: u64,
}

impl Match {
    /// The scoreboard without the transcript — what a list wants.
    pub fn brief(&self) -> Value {
        json!({
            "id": self.id, "game": self.game, "game_name": self.game_name,
            "created": self.created, "ms": self.ms, "rated": self.rated,
            "runtime": self.runtime, "summary": self.summary,
            "turns": self.turns.len(),
            "seats": self.seats.iter().map(|s| json!({
                "seat": s.seat, "player_id": s.player_id, "player_name": s.player_name,
                "score": s.score, "moves": s.moves, "illegal": s.illegal, "timeouts": s.timeouts,
                "mcp": s.mcp,
                "elo_after": round1(s.elo_after), "delta": round1(s.elo_after - s.elo_before),
                "error": s.error,
            })).collect::<Vec<_>>(),
        })
    }
}

// ── the document ─────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct Store {
    #[serde(default)]
    pub modules: HashMap<String, WasmModule>,
    #[serde(default)]
    pub players: HashMap<String, Player>,
    #[serde(default)]
    pub matches: Vec<Match>,
    #[serde(default)]
    pub seq: u64,
}

fn state_file() -> PathBuf {
    blobs::state_dir().join("registry.json")
}

/// Matches are the only unbounded table. Keeping the newest N means a busy
/// arena's document stays a document rather than becoming a database.
const KEEP_MATCHES: usize = 500;

impl Store {
    fn load() -> Store {
        fs::read_to_string(state_file())
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default()
    }

    fn save(&self) {
        let dir = blobs::state_dir();
        if fs::create_dir_all(&dir).is_err() {
            return;
        }
        let Ok(body) = serde_json::to_string_pretty(self) else {
            return;
        };
        let tmp = dir.join("registry.json.tmp");
        if fs::write(&tmp, body).is_ok() {
            let _ = fs::rename(&tmp, state_file());
        }
    }

    pub fn next(&mut self, prefix: &str) -> String {
        self.seq += 1;
        format!("{prefix}{}", self.seq)
    }

    /// Modules resolve by full id, by any unambiguous prefix of at least
    /// [`blobs::MIN_PREFIX`] characters, or by name.
    pub fn module(&self, key: &str) -> Option<&WasmModule> {
        let k = key.trim();
        if let Some(m) = self.modules.get(k) {
            return Some(m);
        }
        if k.len() >= blobs::MIN_PREFIX && k.chars().all(|c| c.is_ascii_hexdigit()) {
            let mut hits = self.modules.values().filter(|m| m.id.starts_with(k));
            let first = hits.next();
            if hits.next().is_none() {
                return first;
            }
            return None; // ambiguous prefix resolves to nothing, never to a guess
        }
        self.modules.values().find(|m| m.name.eq_ignore_ascii_case(k))
    }

    pub fn player(&self, key: &str) -> Option<&Player> {
        let k = key.trim();
        self.players
            .get(k)
            .or_else(|| self.players.values().find(|p| p.name.eq_ignore_ascii_case(k)))
    }

    pub fn module_list(&self) -> Vec<&WasmModule> {
        let mut v: Vec<&WasmModule> = self.modules.values().collect();
        v.sort_by_key(|m| m.created);
        v
    }

    pub fn player_list(&self) -> Vec<&Player> {
        let mut v: Vec<&Player> = self.players.values().collect();
        v.sort_by(|a, b| {
            b.overall
                .elo
                .partial_cmp(&a.overall.elo)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        v
    }

    pub fn record_match(&mut self, m: Match) {
        self.matches.push(m);
        if self.matches.len() > KEEP_MATCHES {
            let cut = self.matches.len() - KEEP_MATCHES;
            self.matches.drain(0..cut);
        }
    }
}

pub fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0 + 0.0
}

pub fn round3(v: f64) -> f64 {
    (v * 1000.0).round() / 1000.0 + 0.0
}

static CELL: OnceLock<Mutex<Store>> = OnceLock::new();

fn cell() -> &'static Mutex<Store> {
    CELL.get_or_init(|| Mutex::new(Store::load()))
}

/// Read the registry. Never hold this across an await — players are slow.
pub fn read<T>(f: impl FnOnce(&Store) -> T) -> T {
    let guard = cell().lock().unwrap_or_else(|e| e.into_inner());
    f(&guard)
}

/// Mutate the registry and persist before returning.
pub fn write<T>(f: impl FnOnce(&mut Store) -> T) -> T {
    let mut guard = cell().lock().unwrap_or_else(|e| e.into_inner());
    let out = f(&mut guard);
    guard.save();
    out
}

#[cfg(test)]
mod turn_prompt_tests {
    use super::Turn;

    /// A runner that records a prompt keeps it; one that doesn't (wasm/class
    /// turns, older runners) still deserializes, and an empty prompt is not
    /// written back out.
    #[test]
    fn prompt_round_trips_and_is_optional() {
        let with: Turn = serde_json::from_str(
            r#"{"turn":1,"seat":0,"view":"v","raw":"r","mv":"m","legal":true,"ms":5,"note":"","prompt":"You are seat 0."}"#,
        ).unwrap();
        assert_eq!(with.prompt, "You are seat 0.");
        let json = serde_json::to_string(&with).unwrap();
        assert!(json.contains("\"prompt\":\"You are seat 0.\""));

        let without: Turn = serde_json::from_str(r#"{"turn":1,"seat":1}"#).unwrap();
        assert_eq!(without.prompt, "");
        assert!(!serde_json::to_string(&without).unwrap().contains("prompt"));
    }
}
