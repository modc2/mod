//! prerank — a daily prediction market over which model is on top.
//!
//! The shape of the thing, in the order the pieces depend on each other:
//!
//!   `crypto`  — sha256, Ethereum signature recovery, Merkle trees
//!   `types`   — money, rounds, and the event enum everything is written in
//!   `chain`   — the hash-linked, append-only log
//!   `state`   — the fold: events in, balances and pools out
//!   `market`  — the payout math, as pure functions
//!   `engine`  — every rule, stated once, as a refusal to write an event
//!   `routes`  — HTTP over the engine, and nothing more than that
//!
//! The load-bearing idea is that the third and fourth of those are enough to
//! check the sixth. Anyone can pull the log, fold it themselves, and get the
//! same balances the server is reporting — or not, and then they know.

pub mod chain;
pub mod crypto;
pub mod engine;
pub mod market;
pub mod routes;
pub mod state;
pub mod testkit;
pub mod types;

use std::path::PathBuf;
use std::sync::Arc;

use parking_lot::Mutex;

pub use chain::Chain;
pub use engine::{Caller, Engine, EngineError, Schedule};
pub use state::State;
pub use types::{Event, Money, Phase, RoundParams, UsageReceipt, MICRO};

/// Wall clock, in seconds. The engine never calls this itself — every entry
/// point takes `now` — so tests can run a week in a millisecond and the
/// server cannot accidentally depend on the clock in two different places.
pub fn now() -> i64 {
    chrono::Utc::now().timestamp()
}

/// Where the log lives. `~/.mod/prerank` by convention; the module's own
/// state dir, never the repository.
pub fn data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("PRERANK_DIR") {
        return PathBuf::from(dir);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("prerank")
}

#[derive(Clone)]
pub struct App {
    pub engine: Arc<Mutex<Engine>>,
    pub started_at: i64,
}

impl App {
    pub fn new(engine: Engine) -> Self {
        Self { engine: Arc::new(Mutex::new(engine)), started_at: now() }
    }

    /// Build the app the server runs: log on disk, schedule and params from
    /// the environment, owner from `PRERANK_OWNER`.
    pub fn boot() -> anyhow::Result<Self> {
        let dir = data_dir();
        std::fs::create_dir_all(&dir)?;
        let chain = Chain::open(dir.join("chain.jsonl"))?;
        let open_mode = std::env::var("PRERANK_OPEN").map(|v| v == "1").unwrap_or(false);
        let mut params = RoundParams::default();
        if let Ok(v) = std::env::var("PRERANK_FEE_BPS") {
            if let Ok(n) = v.parse::<u64>() {
                params.fee_bps = n.min(2_000);
            }
        }
        if let Ok(v) = std::env::var("PRERANK_QUORUM") {
            if let Ok(n) = v.parse::<usize>() {
                params.quorum = n.max(1);
            }
        }
        if let Ok(v) = std::env::var("PRERANK_EARLINESS_K") {
            if let Ok(n) = v.parse::<u128>() {
                params.earliness_k = n.max(1);
            }
        }
        let mut engine = Engine::new(chain, Schedule::from_env(), params, open_mode);
        let owner = std::env::var("PRERANK_OWNER").unwrap_or_else(|_| {
            // No owner configured and no chain yet: the first address to sign
            // an owner claim takes it. Until then the zero address holds a
            // chain that can do nothing.
            "0x0000000000000000000000000000000000000000".to_string()
        });
        engine.bootstrap(&owner, now())?;
        let _ = engine.tick(now());
        Ok(Self::new(engine))
    }
}
