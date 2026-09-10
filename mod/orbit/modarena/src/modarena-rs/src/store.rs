//! Registry state — modules, players and matches.
//!
//! One JSON document beside the blobs under ~/.mod/modarena/. The document holds
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

// ── mods ─────────────────────────────────────────────────────────────────

/// One file inside a stored mod folder. The bytes themselves are in the blob
/// store under `id`, so two mods that ship the same README hold one copy of it
/// and a file can never be half-written into the index.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct FileRef {
    pub path: String,
    /// SHA-256 of this file's bytes — the blob key.
    pub id: String,
    #[serde(default)]
    pub size: usize,
}

/// A stored mod: a folder with a `config.json` and an anchor.
///
/// `config` is what the folder claimed and `info` is what the reader found in
/// the anchor. They are both kept because the interesting fact about a mod is
/// that they agree — [`crate::folder::Folder::verify`] checked it once at
/// upload time, and holding both is what lets anyone check it again later
/// without the bytes.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ModEntry {
    /// SHA-256 of the folder manifest — the id of the whole mod, not of a file.
    pub id: String,
    #[serde(default)]
    pub name: String,
    /// game | player | command | class | wasm — read out of the anchor, never
    /// taken from the uploader.
    #[serde(default)]
    pub role: String,
    /// What config.json declared. Equal to `role` for anything stored, because
    /// a mismatch is refused; kept so the claim is on the record too.
    #[serde(default)]
    pub kind: String,
    /// python | rust | wasm.
    #[serde(default)]
    pub lang: String,
    /// The file the mod is about: mod.py, mod.rs or mod.wasm.
    #[serde(default)]
    pub anchor: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub tags: Vec<String>,
    /// Every byte in the folder.
    #[serde(default)]
    pub size: usize,
    /// The folder, in order.
    #[serde(default)]
    pub files: Vec<FileRef>,
    /// config.json, parsed.
    #[serde(default)]
    pub config: Value,
    /// The reader's description of the anchor.
    #[serde(default)]
    pub info: Value,
    /// The verification this folder passed on the way in.
    #[serde(default)]
    pub report: Value,
    /// example | upload | generated — where the folder came from.
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub runs: u64,
    #[serde(default)]
    pub created: u64,
}

impl ModEntry {
    /// What container the anchor is: `python`, `rust` or `wasm`. The stored
    /// field first, the reader's word as the fallback.
    pub fn lang(&self) -> &str {
        if !self.lang.is_empty() {
            return &self.lang;
        }
        self.info.get("lang").and_then(|v| v.as_str()).unwrap_or("wasm")
    }

    /// The blob holding the anchor — the bytes that actually execute.
    pub fn anchor_id(&self) -> Option<&str> {
        self.files
            .iter()
            .find(|f| f.path == self.anchor)
            .or_else(|| self.files.first())
            .map(|f| f.id.as_str())
    }

    pub fn file(&self, path: &str) -> Option<&FileRef> {
        self.files.iter().find(|f| f.path == path)
    }

    pub fn card(&self) -> Value {
        json!({
            "id": self.id,
            "short": self.short(),
            "name": self.name,
            "role": self.role,
            "kind": self.kind,
            "lang": self.lang(),
            "anchor": self.anchor,
            "class": self.info.get("class").and_then(|v| v.as_str()),
            "description": self.description,
            "author": self.author,
            "tags": self.tags,
            "size": self.size,
            "files": self.files.iter().map(|f| json!({
                "path": f.path, "size": f.size, "id": f.id,
                "anchor": f.path == self.anchor,
            })).collect::<Vec<_>>(),
            "players": self.config.get("players").cloned().unwrap_or(Value::Null),
            "runs": self.runs,
            "source": self.source,
            "created": self.created,
            "verified": self.report.get("ok").cloned().unwrap_or(Value::Null),
            "warnings": self.report.get("warnings").cloned().unwrap_or(json!(0)),
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

    /// Where the execution layer fetches the anchor from — relative, so it
    /// works under the fleet router and standalone alike.
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
        if let Some(m) = self.config.get("module").and_then(|m| m.as_str()) {
            v["module"] = json!(m);
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
    pub modules: HashMap<String, ModEntry>,
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
    pub fn module(&self, key: &str) -> Option<&ModEntry> {
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

    pub fn module_list(&self) -> Vec<&ModEntry> {
        let mut v: Vec<&ModEntry> = self.modules.values().collect();
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
