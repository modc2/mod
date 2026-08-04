//! Arena state — tasks, competitors, submissions and matches.
//!
//! One JSON document under ~/.mod/openarena/ (off-tree: the repo carries the
//! seed task pack and nothing else). Every mutation goes through `write`,
//! which persists before it returns, so a crash can lose at most the call in
//! flight.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{SystemTime, UNIX_EPOCH};

/// Every competitor starts here; only matches with two or more entrants move it.
pub const START_ELO: f64 = 1200.0;

/// The task pack that ships with the module, planted on an empty arena.
const SEED: &str = include_str!("../../tasks/seed.json");

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn one() -> f64 {
    1.0
}
fn kind_coding() -> String {
    "coding".into()
}
fn lang_any() -> String {
    "any".into()
}
fn mode_io() -> String {
    "io".into()
}
fn cmp_trim() -> String {
    "trim".into()
}
fn default_timeout() -> u64 {
    10_000
}

// ── tasks ────────────────────────────────────────────────────────────────

/// One graded case. `io` tasks feed `stdin` to the program and compare stdout
/// with `expect`; `unit` tasks run `program` (which imports the submission)
/// and pass when it exits 0.
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct TestCase {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub stdin: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub expect: String,
    /// exact | trim | contains
    #[serde(default = "cmp_trim")]
    pub compare: String,
    #[serde(default)]
    pub program: String,
    /// Hidden cases are graded but never shown — not in the task, not in results.
    #[serde(default)]
    pub hidden: bool,
    #[serde(default = "one")]
    pub weight: f64,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Task {
    pub id: String,
    #[serde(default)]
    pub slug: String,
    #[serde(default)]
    pub title: String,
    #[serde(default = "kind_coding")]
    pub kind: String,
    #[serde(default)]
    pub statement: String,
    /// "any" lets the competitor pick; otherwise the submission runs as this.
    #[serde(default = "lang_any")]
    pub language: String,
    #[serde(default)]
    pub starter: String,
    /// io | unit
    #[serde(default = "mode_io")]
    pub mode: String,
    #[serde(default)]
    pub tests: Vec<TestCase>,
    #[serde(default = "default_timeout")]
    pub timeout_ms: u64,
    #[serde(default)]
    pub author: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub created: u64,
}

impl Task {
    /// The task as an entrant may see it: hidden cases keep their name and
    /// weight, and give up nothing else.
    pub fn view(&self, reveal: bool) -> Value {
        let mut v = serde_json::to_value(self).unwrap_or_else(|_| json!({}));
        if !reveal {
            let cases: Vec<Value> = self
                .tests
                .iter()
                .map(|c| {
                    if c.hidden {
                        json!({ "name": c.name, "hidden": true, "weight": c.weight })
                    } else {
                        serde_json::to_value(c).unwrap_or_else(|_| json!({}))
                    }
                })
                .collect();
            v["tests"] = Value::Array(cases);
        }
        v["total_tests"] = json!(self.tests.len());
        v["hidden_tests"] = json!(self.tests.iter().filter(|c| c.hidden).count());
        v
    }
}

// ── competitors ──────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Agent {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub owner: String,
    /// agent_mod | http | ap | static
    pub kind: String,
    #[serde(default)]
    pub config: Value,
    #[serde(default)]
    pub note: String,
    #[serde(default)]
    pub elo: f64,
    #[serde(default)]
    pub matches: u64,
    #[serde(default)]
    pub wins: u64,
    #[serde(default)]
    pub draws: u64,
    #[serde(default)]
    pub losses: u64,
    /// A task counts as solved when every case passed.
    #[serde(default)]
    pub solved: u64,
    #[serde(default)]
    pub attempts: u64,
    #[serde(default)]
    pub score_sum: f64,
    #[serde(default)]
    pub ms_sum: u64,
    #[serde(default)]
    pub created: u64,
}

impl Agent {
    pub fn card(&self) -> Value {
        let a = self.attempts.max(1) as f64;
        json!({
            "id": self.id, "name": self.name, "owner": self.owner, "kind": self.kind,
            "note": self.note, "elo": (self.elo * 10.0).round() / 10.0,
            "matches": self.matches, "wins": self.wins, "draws": self.draws, "losses": self.losses,
            "attempts": self.attempts, "solved": self.solved,
            "pass_rate": if self.attempts == 0 { 0.0 } else { (self.solved as f64 / a * 1000.0).round() / 1000.0 },
            "avg_score": if self.attempts == 0 { 0.0 } else { (self.score_sum / a * 1000.0).round() / 1000.0 },
            "avg_ms": if self.attempts == 0 { 0 } else { self.ms_sum / self.attempts },
            "created": self.created,
        })
    }
}

// ── results ──────────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct CaseResult {
    pub name: String,
    pub passed: bool,
    pub hidden: bool,
    pub weight: f64,
    pub ms: u64,
    pub exit: i32,
    pub timed_out: bool,
    #[serde(default)]
    pub expected: String,
    #[serde(default)]
    pub got: String,
    #[serde(default)]
    pub stderr: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Submission {
    pub id: String,
    pub task_id: String,
    pub agent_id: String,
    #[serde(default)]
    pub match_id: String,
    pub language: String,
    pub code: String,
    #[serde(default)]
    pub meta: Value,
    pub created: u64,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Outcome {
    pub agent_id: String,
    pub agent_name: String,
    #[serde(default)]
    pub submission_id: String,
    pub passed: usize,
    pub total: usize,
    pub score: f64,
    /// Wall clock for the competitor to answer, and for the judge to grade it.
    pub answer_ms: u64,
    pub judge_ms: u64,
    pub elo_before: f64,
    pub elo_after: f64,
    #[serde(default)]
    pub language: String,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub cases: Vec<CaseResult>,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Match {
    pub id: String,
    pub task_id: String,
    pub task_title: String,
    pub created: u64,
    pub ms: u64,
    #[serde(default)]
    pub rated: bool,
    pub results: Vec<Outcome>,
}

impl Match {
    /// The scoreboard without the per-case detail — what a list wants.
    pub fn brief(&self) -> Value {
        json!({
            "id": self.id, "task_id": self.task_id, "task_title": self.task_title,
            "created": self.created, "ms": self.ms, "rated": self.rated,
            "results": self.results.iter().map(|r| json!({
                "agent_id": r.agent_id, "agent_name": r.agent_name,
                "passed": r.passed, "total": r.total, "score": r.score,
                "answer_ms": r.answer_ms, "judge_ms": r.judge_ms,
                "elo_after": (r.elo_after * 10.0).round() / 10.0,
                "delta": ((r.elo_after - r.elo_before) * 10.0).round() / 10.0,
                "error": r.error,
            })).collect::<Vec<_>>(),
        })
    }
}

// ── the document ─────────────────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone, Default)]
pub struct Store {
    #[serde(default)]
    pub tasks: HashMap<String, Task>,
    #[serde(default)]
    pub agents: HashMap<String, Agent>,
    #[serde(default)]
    pub submissions: HashMap<String, Submission>,
    #[serde(default)]
    pub matches: Vec<Match>,
    #[serde(default)]
    pub seq: u64,
}

pub fn state_dir() -> PathBuf {
    if let Ok(d) = std::env::var("OPENARENA_STATE") {
        return PathBuf::from(d);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".into());
    PathBuf::from(home).join(".mod").join("openarena")
}

fn state_file() -> PathBuf {
    state_dir().join("arena.json")
}

impl Store {
    fn load() -> Store {
        let mut s: Store = fs::read_to_string(state_file())
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .unwrap_or_default();
        if s.tasks.is_empty() {
            s.plant_seed();
            s.save();
        }
        s
    }

    /// The shipped task pack, planted once so a fresh arena is never empty.
    fn plant_seed(&mut self) {
        let pack: Vec<Value> = serde_json::from_str(SEED).unwrap_or_default();
        for mut t in pack {
            let id = self.next("t");
            t["id"] = json!(id);
            t["created"] = json!(now());
            if let Ok(task) = serde_json::from_value::<Task>(t) {
                self.tasks.insert(task.id.clone(), task);
            }
        }
    }

    fn save(&self) {
        let dir = state_dir();
        if fs::create_dir_all(&dir).is_err() {
            return;
        }
        let body = match serde_json::to_string_pretty(self) {
            Ok(b) => b,
            Err(_) => return,
        };
        let tmp = dir.join("arena.json.tmp");
        if fs::write(&tmp, body).is_ok() {
            let _ = fs::rename(&tmp, state_file());
        }
    }

    pub fn next(&mut self, prefix: &str) -> String {
        self.seq += 1;
        format!("{prefix}{}", self.seq)
    }

    /// Tasks resolve by id or slug, competitors by id or name — ids are ugly
    /// and every caller would otherwise look one up first.
    pub fn task(&self, key: &str) -> Option<&Task> {
        self.tasks.get(key).or_else(|| {
            self.tasks
                .values()
                .find(|t| t.slug == key || t.title.eq_ignore_ascii_case(key))
        })
    }

    pub fn agent(&self, key: &str) -> Option<&Agent> {
        self.agents.get(key).or_else(|| {
            self.agents
                .values()
                .find(|a| a.name.eq_ignore_ascii_case(key))
        })
    }

    pub fn task_list(&self) -> Vec<&Task> {
        let mut v: Vec<&Task> = self.tasks.values().collect();
        v.sort_by_key(|t| t.created);
        v
    }

    pub fn agent_list(&self) -> Vec<&Agent> {
        let mut v: Vec<&Agent> = self.agents.values().collect();
        v.sort_by(|a, b| b.elo.partial_cmp(&a.elo).unwrap_or(std::cmp::Ordering::Equal));
        v
    }
}

static CELL: OnceLock<Mutex<Store>> = OnceLock::new();

fn cell() -> &'static Mutex<Store> {
    CELL.get_or_init(|| Mutex::new(Store::load()))
}

/// Read the arena. Never hold this across an await — competitors are slow.
pub fn read<T>(f: impl FnOnce(&Store) -> T) -> T {
    let guard = cell().lock().unwrap_or_else(|e| e.into_inner());
    f(&guard)
}

/// Mutate the arena and persist before returning.
pub fn write<T>(f: impl FnOnce(&mut Store) -> T) -> T {
    let mut guard = cell().lock().unwrap_or_else(|e| e.into_inner());
    let out = f(&mut guard);
    guard.save();
    out
}
