//! Long-running copy engine that lives inside the Rust API process.
//!
//! Each live session is a tokio task keyed by EOA. The task polls each
//! watched trader's `/activity` from Polymarket, pushes observed trades
//! into a ring buffer, and stamps `last_cycle_at` / `cycle_count` so the
//! browser (or any HTTP client) can observe progress via `GET /live/status`.
//!
//! **Execution.** Each cycle, every newly-observed leader BUY becomes a
//! proportionally-sized mirror order placed through the backend signer
//! (`order_place::place_order`) — no browser wallet required. Sizing mirrors
//! the frontend `copyEngine.ts` (capital × weight ÷ trader-volume, clamped up
//! to the user's `minOrderSize` floor). Placement is gated by
//! `EngineConfig.auto_execute`: while it's `false` (the default) the engine
//! runs DRY RUN — it computes and logs every order it *would* place but sends
//! nothing to the CLOB, so sizing can be verified before risking real USDC.
//!
//! **Persistence**: every cycle writes the latest state to disk, and the
//! session config is written once on start. On API boot the registry
//! scans for `<eoa>.config.json` files and auto-resumes any session whose
//! config is present (the engine "always runs in the background until the
//! user stops it" — explicit STOP deletes the config file).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use dashmap::DashMap;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::task::JoinHandle;

use crate::order_place::{
    place_order, ClobCreds, OrderSide, OrderTimeInForce, PlaceOrderArgs, PlaceOrderRequest,
};
use crate::signer::SignerStore;

const PERSIST_DIR_NAME: &str = "polymarket-live-engine";
const DATA_API: &str = "https://data-api.polymarket.com";
const GAMMA_API: &str = "https://gamma-api.polymarket.com";

// ─── Internal bounds (not strategy — pure memory/disk caps) ─────────────
/// Cap on the copied-id dedup set kept in state (bounded disk + memory).
const COPIED_IDS_CAP: usize = 2000;
/// Max trades held in the observed-trades ring buffer. Bounded so a long
/// session doesn't grow state unbounded (memory + per-cycle disk writes).
const OBSERVED_CAP: usize = 500;
/// Max log entries kept. Older entries fall off.
const LOG_CAP: usize = 1000;

// ─── Strat-supplied tunable defaults ────────────────────────────────────
// Every knob below is owner-configurable through the strat (it travels on
// `EngineConfig`, which the LIVE tab builds from the selected strat). These
// `default_*` fns only supply a value when the strat omits the field, so old
// configs and partial payloads keep working — nothing is hardcoded into the
// engine's behavior.
//
/// Polymarket's hard $1 minimum notional per order (sizing floor default).
fn default_min_order_size() -> f64 { 1.0 }
/// Polymarket's 5-share minimum per order (sizing floor default).
fn default_min_shares() -> f64 { 5.0 }
/// Proportional-sizing lookback window (days) for a trader's volume.
fn default_backtest_days() -> u32 { 3 }
/// Orders placed per cycle before the rest defer to the next one — a fan-out
/// backstop. Defaults to the strat's `maxPerCycle` notion.
fn default_max_orders_per_cycle() -> usize { 10 }
/// Spacing between successive order placements within a cycle (ms).
fn default_order_delay_ms() -> u64 { 300 }
/// Spacing between successive per-trader `/activity` fetches inside one cycle
/// (ms). Spreads requests so we don't burst past Cloudflare's per-second limit.
fn default_inter_request_delay_ms() -> u64 { 400 }
/// Floor on the cycle interval (ms). Polymarket's data-api sits behind
/// Cloudflare, which 429s once sustained rate crosses a few req/s; a too-small
/// interval × N traders gets rate-limited into zero observations. The owner can
/// lower this through the strat, but the default keeps a stale fast config safe.
fn default_min_interval_ms() -> u64 { 60_000 }

// ─── Types ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineStatus {
    Stopped,
    Starting,
    Running,
    Paused,
    Error,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraderEntry {
    pub address: String,
    pub weight: f64,
    #[serde(default = "default_true")]
    pub enabled: bool,
}
fn default_true() -> bool { true }

/// What the engine needs to run a session. Persisted to disk verbatim.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineConfig {
    /// User's EOA — used as the registry key and the on-disk filename.
    pub eoa: String,
    /// Saved strat id (echoed back to the client; not used by the loop).
    #[serde(rename = "strategyId")]
    pub strategy_id: String,
    /// Proxy (Safe / POLY_PROXY) address where funds live.
    pub address: String,
    pub traders: Vec<TraderEntry>,
    pub capital: f64,
    #[serde(rename = "intervalMs")]
    pub interval_ms: u64,
    // ── Strat-supplied tunables (the LIVE tab fills these from the strat) ──
    /// Owner's minimum order size in USDC (strat `minTrade`). A mirror whose
    /// proportional notional lands below this — but whose leader trade still
    /// clears the CLOB floor — is clamped UP to it so it fills instead of being
    /// skipped. Defaults to Polymarket's $1 hard floor when omitted.
    #[serde(rename = "minOrderSize", default = "default_min_order_size")]
    pub min_order_size: f64,
    /// Owner's per-order ceiling in USDC (strat `maxTrade`). `None` ⇒ no cap.
    #[serde(rename = "maxOrderSize", default)]
    pub max_order_size: Option<f64>,
    /// Minimum shares per order used in the CLOB sizing floor (strat-supplied;
    /// defaults to Polymarket's 5-share minimum).
    #[serde(rename = "minShares", default = "default_min_shares")]
    pub min_shares: f64,
    #[serde(rename = "maxSlippageBps", default)]
    pub max_slippage_bps: u32,
    /// Proportional-sizing lookback window (days) — a trader's recent volume
    /// over this window sets the copy ratio (strat `backtestDays`).
    #[serde(rename = "backtestDays", default = "default_backtest_days")]
    pub backtest_days: u32,
    /// Max mirror orders placed per cycle before the rest defer (strat
    /// `maxPerCycle`). Caps fan-out so one bursty cycle can't fire dozens of
    /// fills at once.
    #[serde(rename = "maxOrdersPerCycle", default = "default_max_orders_per_cycle")]
    pub max_orders_per_cycle: usize,
    /// Spacing between successive order placements within a cycle (ms).
    #[serde(rename = "orderDelayMs", default = "default_order_delay_ms")]
    pub order_delay_ms: u64,
    /// Spacing between successive per-trader activity fetches (ms) — rate-limit
    /// protection the owner can tune through the strat.
    #[serde(rename = "interRequestDelayMs", default = "default_inter_request_delay_ms")]
    pub inter_request_delay_ms: u64,
    /// Floor the effective poll interval is clamped up to (ms). Guards against a
    /// too-fast cadence getting data-api 429s; owner-overridable via the strat.
    #[serde(rename = "minIntervalMs", default = "default_min_interval_ms")]
    pub min_interval_ms: u64,
    /// Master switch for real order placement. `false` (default) = DRY RUN:
    /// the engine computes and logs every mirror it *would* place but sends
    /// nothing to the CLOB, so the user can verify sizing before risking USDC.
    /// `true` = place real orders via the backend signer.
    #[serde(rename = "autoExecute", default)]
    pub auto_execute: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObservedTrade {
    pub id: String,
    pub timestamp: i64,
    pub trader: String,
    pub market: String,
    #[serde(rename = "conditionId")]
    pub condition_id: String,
    pub side: String, // "BUY" | "SELL"
    pub size: f64,
    pub price: f64,
    pub notional: f64,
    /// CLOB outcome-token id (the `asset` field from the data-api activity
    /// item) — the exact token to trade, so no gamma token-id lookup is
    /// needed. `#[serde(default)]` keeps old persisted state deserializable.
    #[serde(rename = "tokenId", default)]
    pub token_id: String,
    /// Outcome label the leader traded (e.g. "Yes"/"No"/team name). Mirrored
    /// verbatim — the token id already encodes the branch.
    #[serde(default)]
    pub outcome: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEntry {
    pub id: String,
    pub timestamp: i64,
    #[serde(rename = "type")]
    pub kind: String,
    pub reason: Option<String>,
    #[serde(rename = "traderAddress", skip_serializing_if = "Option::is_none")]
    pub trader_address: Option<String>,
    #[serde(rename = "tradesSeen", skip_serializing_if = "Option::is_none")]
    pub trades_seen: Option<usize>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineState {
    pub status: EngineStatus,
    #[serde(rename = "lastCycleAt")]
    pub last_cycle_at: Option<i64>,
    #[serde(rename = "nextCycleAt")]
    pub next_cycle_at: Option<i64>,
    #[serde(rename = "cycleCount")]
    pub cycle_count: u64,
    /// Cumulative mirror orders the engine has successfully placed (DRY RUN
    /// placements don't count). Surfaced to the frontend live rail.
    #[serde(rename = "totalOrdersPlaced")]
    pub total_orders_placed: u64,
    #[serde(rename = "totalOrdersFailed")]
    pub total_orders_failed: u64,
    #[serde(rename = "totalVolumeMirrored")]
    pub total_volume_mirrored: f64,
    pub balance: Option<f64>,
    pub log: Vec<LogEntry>,
    #[serde(rename = "observedTrades")]
    pub observed_trades: Vec<ObservedTrade>,
    pub error: Option<String>,
    /// Highest trade timestamp (ms) seen per trader. Used to filter new trades.
    #[serde(rename = "traderCursors", default)]
    pub trader_cursors: HashMap<String, i64>,
    /// Wall-clock ms of the last successful fetch per trader. Surfaces traders
    /// the engine hasn't been able to reach.
    #[serde(rename = "traderLastSync", default)]
    pub trader_last_sync: HashMap<String, i64>,
    /// Trade ids we've already placed a mirror for. Guards against
    /// double-copying a leader trade that reappears across the cursor's 60s
    /// overlap window. Bounded at `COPIED_IDS_CAP`.
    #[serde(rename = "copiedIds", default)]
    pub copied_ids: HashSet<String>,
}

impl EngineState {
    fn empty() -> Self {
        Self {
            status: EngineStatus::Stopped,
            last_cycle_at: None,
            next_cycle_at: None,
            cycle_count: 0,
            total_orders_placed: 0,
            total_orders_failed: 0,
            total_volume_mirrored: 0.0,
            balance: None,
            log: Vec::new(),
            observed_trades: Vec::new(),
            error: None,
            trader_cursors: HashMap::new(),
            trader_last_sync: HashMap::new(),
            copied_ids: HashSet::new(),
        }
    }
}

// ─── Handle / Registry ─────────────────────────────────────────────────

pub struct EngineHandle {
    pub config: EngineConfig,
    pub state: Arc<RwLock<EngineState>>,
    pub cancel: Arc<AtomicBool>,
    pub task: parking_lot::Mutex<Option<JoinHandle<()>>>,
}

pub struct EngineRegistry {
    engines: DashMap<String, Arc<EngineHandle>>,
    http: reqwest::Client,
    disk_dir: PathBuf,
    /// Per-EOA signing keys — the engine signs and places mirror orders
    /// autonomously through these (no browser wallet needed).
    signer_store: Arc<SignerStore>,
    /// conditionId → negRisk flag, resolved once via gamma and reused.
    neg_risk_cache: DashMap<String, bool>,
}

impl EngineRegistry {
    pub fn new(http: reqwest::Client, signer_store: Arc<SignerStore>) -> Self {
        // Same POLYMARKET_DATA_DIR convention the signer store uses — a
        // single volume-mounted dir holds every persistence artifact so the
        // container can be recycled (or fully recreated) without losing
        // user state. Falls back to OS temp for tests + local dev.
        let base = std::env::var("POLYMARKET_DATA_DIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|_| std::env::temp_dir());
        let disk_dir = base.join(PERSIST_DIR_NAME);
        std::fs::create_dir_all(&disk_dir).ok();
        let reg = Self {
            engines: DashMap::new(),
            http,
            disk_dir,
            signer_store,
            neg_risk_cache: DashMap::new(),
        };
        reg
    }

    /// Toggle real order placement on/off for a running session at runtime,
    /// persisting the change so it survives a restart. Returns the new value,
    /// or `None` if no session exists for this EOA. Because `EngineConfig`
    /// lives behind an immutable `Arc` snapshot inside the spawned task, the
    /// clean way to change it is to restart the session with the patched
    /// config — `start()` already stops + replaces any existing engine and
    /// preserves on-disk state (cursors, copied ids) across the swap.
    pub fn set_auto_execute(self: &Arc<Self>, eoa: &str, on: bool) -> Option<bool> {
        let mut cfg = self.config_of(eoa)?;
        cfg.auto_execute = on;
        self.start(cfg);
        Some(on)
    }

    /// Resolve (and cache) the negRisk flag for a market via gamma. A wrong
    /// value yields a CLOB "bad signature" rejection, so we resolve it rather
    /// than guessing; defaults to `false` only when gamma is unreachable.
    async fn resolve_neg_risk(&self, condition_id: &str) -> bool {
        if condition_id.is_empty() {
            return false;
        }
        if let Some(v) = self.neg_risk_cache.get(condition_id) {
            return *v;
        }
        let url = format!("{}/markets?condition_id={}", GAMMA_API, condition_id);
        let resolved = match self.http.get(&url).send().await {
            Ok(resp) => {
                let txt = resp.text().await.unwrap_or_default();
                serde_json::from_str::<Value>(&txt)
                    .ok()
                    .and_then(|v| {
                        let first = if v.is_array() { v.get(0).cloned() } else { Some(v) };
                        first.and_then(|m| {
                            m.get("negRisk")
                                .or_else(|| m.get("neg_risk"))
                                .and_then(|b| b.as_bool())
                        })
                    })
                    .unwrap_or(false)
            }
            Err(e) => {
                tracing::warn!(condition_id, error = %e, "negRisk resolve failed; defaulting false");
                false
            }
        };
        self.neg_risk_cache.insert(condition_id.to_string(), resolved);
        resolved
    }

    fn path_for_config(&self, eoa: &str) -> PathBuf {
        self.disk_dir.join(format!("{}.config.json", eoa.to_lowercase()))
    }
    fn path_for_state(&self, eoa: &str) -> PathBuf {
        self.disk_dir.join(format!("{}.state.json", eoa.to_lowercase()))
    }

    /// Resume any engines that were persisted to disk. Called once at API
    /// startup so a process restart doesn't kill live sessions.
    pub fn resume_persisted(self: &Arc<Self>) {
        let dir = self.disk_dir.clone();
        let Ok(read_dir) = std::fs::read_dir(&dir) else { return; };
        for entry in read_dir.flatten() {
            let p = entry.path();
            if !p.is_file() { continue; }
            let name = match p.file_name().and_then(|n| n.to_str()) {
                Some(n) => n,
                None => continue,
            };
            if !name.ends_with(".config.json") { continue; }
            let raw = match std::fs::read_to_string(&p) {
                Ok(r) => r,
                Err(e) => { tracing::warn!("resume: read {}: {}", name, e); continue; }
            };
            let cfg: EngineConfig = match serde_json::from_str(&raw) {
                Ok(c) => c,
                Err(e) => { tracing::warn!("resume: parse {}: {}", name, e); continue; }
            };
            tracing::info!("resuming live engine for {}", cfg.eoa);
            // Restore state if present, else start fresh.
            let restored_state = self.load_persisted_state(&cfg.eoa);
            self.start_internal(cfg, restored_state);
        }
    }

    pub fn status_of(&self, eoa: &str) -> Option<EngineState> {
        self.engines
            .get(&eoa.to_lowercase())
            .map(|h| h.state.read().clone())
    }

    pub fn config_of(&self, eoa: &str) -> Option<EngineConfig> {
        self.engines
            .get(&eoa.to_lowercase())
            .map(|h| h.config.clone())
    }

    /// Start an engine. If one already exists for this EOA, it's stopped
    /// and replaced (lets the user reconfigure mid-session without manual stop).
    ///
    /// Restores the EOA's persisted state across the swap so a reconfigure or
    /// an auto-execute toggle does NOT wipe order counters, the copy log, or
    /// `copied_ids`. Without this, every restart began from an empty state:
    /// the UI showed 0 trades despite real fills, and the engine re-mirrored
    /// trades it had already copied (cleared `copied_ids` → duplicate orders).
    pub fn start(self: &Arc<Self>, cfg: EngineConfig) {
        let lc = cfg.eoa.to_lowercase();
        if let Some((_, existing)) = self.engines.remove(&lc) {
            existing.cancel.store(true, Ordering::Release);
            if let Some(t) = existing.task.lock().take() {
                t.abort();
            }
        }
        self.persist_config(&cfg);
        // Re-load whatever the engine last persisted (counters, log, copied
        // ids, cursors). Falls back to a fresh state for a brand-new session.
        let restored = self.load_persisted_state(&cfg.eoa);
        self.start_internal(cfg, restored);
    }

    /// Read the persisted `EngineState` for an EOA from disk, if any.
    fn load_persisted_state(&self, eoa: &str) -> Option<EngineState> {
        let state_path = self.path_for_state(eoa);
        std::fs::read_to_string(&state_path)
            .ok()
            .and_then(|s| serde_json::from_str::<EngineState>(&s).ok())
    }

    /// Read the persisted `EngineConfig` for an EOA from disk, if any.
    fn load_persisted_config(&self, eoa: &str) -> Option<EngineConfig> {
        let path = self.path_for_config(eoa);
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str::<EngineConfig>(&s).ok())
    }

    /// The `auto_execute` currently in effect for an EOA — from the running
    /// session if one exists, else the last persisted config. `None` when
    /// neither exists (a brand-new session, so the caller's own value stands).
    /// Lets `/live/start` keep a live session's execution mode sticky across a
    /// config re-post that omits the flag, instead of reverting to DRY RUN.
    pub fn current_auto_execute(&self, eoa: &str) -> Option<bool> {
        self.config_of(eoa)
            .or_else(|| self.load_persisted_config(eoa))
            .map(|c| c.auto_execute)
    }

    fn persist_config(&self, cfg: &EngineConfig) {
        let path = self.path_for_config(&cfg.eoa);
        if let Ok(json) = serde_json::to_string_pretty(cfg) {
            let _ = std::fs::write(&path, json);
            restrict_perms(&path);
        }
    }

    fn persist_state(&self, eoa: &str, state: &EngineState) {
        let path = self.path_for_state(eoa);
        if let Ok(json) = serde_json::to_string(state) {
            let _ = std::fs::write(&path, json);
            restrict_perms(&path);
        }
    }

    fn start_internal(self: &Arc<Self>, cfg: EngineConfig, restore: Option<EngineState>) {
        let mut initial = restore.unwrap_or_else(EngineState::empty);
        initial.status = EngineStatus::Running;
        initial.error = None;
        let state = Arc::new(RwLock::new(initial));
        let cancel = Arc::new(AtomicBool::new(false));

        let handle = Arc::new(EngineHandle {
            config: cfg.clone(),
            state: state.clone(),
            cancel: cancel.clone(),
            task: parking_lot::Mutex::new(None),
        });
        let lc = cfg.eoa.to_lowercase();
        self.engines.insert(lc.clone(), handle.clone());

        // Spawn the loop.
        let registry = Arc::clone(self);
        let task_cfg = cfg;
        let task = tokio::spawn(async move {
            registry.run_loop(task_cfg, state, cancel).await;
        });
        *handle.task.lock() = Some(task);
    }

    /// Explicit user stop. Clears the persisted config so the next API boot
    /// doesn't auto-resume the session.
    pub fn stop(&self, eoa: &str) -> bool {
        let lc = eoa.to_lowercase();
        let Some((_, handle)) = self.engines.remove(&lc) else { return false; };
        handle.cancel.store(true, Ordering::Release);
        if let Some(t) = handle.task.lock().take() {
            t.abort();
        }
        // Mark state as stopped, then persist final shape so a quick reload
        // sees the stopped state before the file deletion lands.
        {
            let mut s = handle.state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&lc, &handle.state.read());
        // Delete the config so resume_persisted() skips it on next boot.
        let _ = std::fs::remove_file(self.path_for_config(&lc));
        true
    }

    /// The main loop.
    async fn run_loop(
        self: Arc<Self>,
        cfg: EngineConfig,
        state: Arc<RwLock<EngineState>>,
        cancel: Arc<AtomicBool>,
    ) {
        // First cycle cursor: now - intervalMs, NOT now. Without this, a fresh
        // engine's first cycle filters every fetched trade out because
        // `trade.ts > cursor` is false the moment after start (matches the JS
        // engine's bug we already fixed there).
        // Clamp the configured cadence up to the rate-limit floor. Old sessions
        // persisted a 5s interval that hammered the data-api into 429s; the floor
        // makes a stale config self-heal on resume without the user re-saving.
        let effective_interval_ms = cfg.interval_ms.max(cfg.min_interval_ms);

        let now_ms = chrono::Utc::now().timestamp_millis();
        {
            let mut s = state.write();
            for t in &cfg.traders {
                let key = t.address.to_lowercase();
                s.trader_cursors
                    .entry(key)
                    .or_insert_with(|| now_ms - cfg.interval_ms as i64);
            }
        }

        loop {
            if cancel.load(Ordering::Acquire) { break; }

            let cycle_started_at = chrono::Utc::now().timestamp_millis();
            let mut new_observed: Vec<ObservedTrade> = Vec::new();
            let mut trader_sync_updates: Vec<(String, i64)> = Vec::new();
            let mut cursor_updates: Vec<(String, i64)> = Vec::new();
            let mut errors: Vec<(String, String)> = Vec::new();
            // Mirror candidates collected this cycle: (newly-observed BUY, copyRatio).
            // Executed after the state commit so order-placement HTTP never runs
            // under the state lock.
            let mut mirror_candidates: Vec<(ObservedTrade, f64)> = Vec::new();

            // Snapshot cursors so we don't hold the RwLock across the HTTP fan-out.
            let (cursors, enabled_traders): (HashMap<String, i64>, Vec<TraderEntry>) = {
                let s = state.read();
                let cursors = s.trader_cursors.clone();
                (cursors, cfg.traders.iter().filter(|t| t.enabled).cloned().collect())
            };
            // Sum of enabled weights — denominator for each trader's capital
            // allocation. Guarded so a degenerate all-zero-weight config can't
            // divide by zero.
            let total_weight: f64 = enabled_traders
                .iter()
                .map(|t| t.weight)
                .sum::<f64>()
                .max(1e-9);
            // Proportional-sizing window: a trader's volume over the last N days.
            let window_cutoff_ms =
                cycle_started_at - (cfg.backtest_days.max(1) as i64) * 86_400_000;

            for (idx, trader) in enabled_traders.iter().enumerate() {
                if cancel.load(Ordering::Acquire) { break; }
                // Spread the per-trader fetches out so we never burst N requests
                // at once and trip Cloudflare's per-second limit (1015). Skip the
                // delay before the first request so a single-trader engine isn't
                // needlessly slowed.
                if idx > 0 {
                    tokio::time::sleep(Duration::from_millis(cfg.inter_request_delay_ms)).await;
                }
                let key = trader.address.to_lowercase();
                let cursor = *cursors.get(&key).unwrap_or(&(now_ms - cfg.interval_ms as i64));
                match fetch_recent_activity(&self.http, &trader.address).await {
                    Ok(items) => {
                        trader_sync_updates.push((key.clone(), chrono::Utc::now().timestamp_millis()));
                        // Parse the trader's full recent activity once, then use
                        // it both for the proportional copyRatio (volume over the
                        // window) and for new-trade detection.
                        let parsed: Vec<ObservedTrade> = items
                            .iter()
                            .filter_map(|v| parse_activity_trade(v, &trader.address))
                            .collect();

                        // copyRatio = (capital × weight/totalWeight) / traderVol,
                        // where traderVol = max(buyVol, sellVol, 1) over the window.
                        // Matches copyEngine.ts / the backtest tab exactly.
                        let mut buy_vol = 0.0f64;
                        let mut sell_vol = 0.0f64;
                        for t in &parsed {
                            if t.timestamp >= window_cutoff_ms {
                                if t.side == "BUY" {
                                    buy_vol += t.notional;
                                } else if t.side == "SELL" {
                                    sell_vol += t.notional;
                                }
                            }
                        }
                        let trader_vol = buy_vol.max(sell_vol).max(1.0);
                        let capital_alloc = cfg.capital * (trader.weight / total_weight);
                        let copy_ratio = capital_alloc / trader_vol;

                        let mut highest_ts = cursor;
                        for t in parsed {
                            if t.timestamp <= cursor { continue; }
                            if t.timestamp > highest_ts { highest_ts = t.timestamp; }
                            // v1 mirrors BUYs only — a SELL we don't hold would
                            // just be rejected, and position-aware exits aren't
                            // ported yet. SELLs are still recorded as observed.
                            if t.side == "BUY" && !t.token_id.is_empty() {
                                mirror_candidates.push((t.clone(), copy_ratio));
                            }
                            new_observed.push(t);
                        }
                        if highest_ts > cursor {
                            cursor_updates.push((key, highest_ts));
                        }
                    }
                    Err(e) => {
                        tracing::warn!(trader = %trader.address, error = %e, "activity fetch failed");
                        errors.push((trader.address.clone(), e.to_string()));
                    }
                }
            }

            // Commit all the cycle's effects in a single state-lock window.
            let cycle_ended_at = chrono::Utc::now().timestamp_millis();
            {
                let mut s = state.write();
                // Merge observed trades, newest-first, capped at OBSERVED_CAP.
                let mut combined: Vec<ObservedTrade> = new_observed;
                combined.extend(s.observed_trades.drain(..));
                combined.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));
                combined.truncate(OBSERVED_CAP);
                s.observed_trades = combined;

                for (k, ts) in trader_sync_updates {
                    s.trader_last_sync.insert(k, ts);
                }
                for (k, ts) in cursor_updates {
                    s.trader_cursors.insert(k, ts);
                }

                // Log a heartbeat so quiet cycles still produce a signal.
                let summary = if errors.is_empty() {
                    format!(
                        "polled {} traders · {} new trades observed",
                        enabled_traders.len(),
                        s.observed_trades.iter().filter(|o| o.timestamp >= cycle_started_at).count(),
                    )
                } else {
                    format!(
                        "polled {} traders · {} fetch errors",
                        enabled_traders.len(),
                        errors.len(),
                    )
                };
                let next_cycle_count = s.cycle_count + 1;
                tracing::info!(eoa = %cfg.eoa, cycle = next_cycle_count, interval_ms = effective_interval_ms, "{}", summary);
                push_log(&mut s.log, LogEntry {
                    id: format!("cycle-{}", next_cycle_count),
                    timestamp: cycle_ended_at,
                    kind: "CYCLE_END".into(),
                    reason: Some(summary),
                    trader_address: None,
                    trades_seen: None,
                });
                for (addr, err) in &errors {
                    push_log(&mut s.log, LogEntry {
                        id: format!("err-{}-{}", cycle_ended_at, addr),
                        timestamp: cycle_ended_at,
                        kind: "ERROR".into(),
                        reason: Some(format!("FETCH_FAILED: {}", err)),
                        trader_address: Some(addr.clone()),
                        trades_seen: None,
                    });
                }

                s.cycle_count += 1;
                s.last_cycle_at = Some(cycle_ended_at);
                s.next_cycle_at = Some(cycle_ended_at + effective_interval_ms as i64);
                s.status = EngineStatus::Running;
            }

            // Persist snapshot so a restart restores state.
            self.persist_state(&cfg.eoa, &state.read());

            // ── Mirror execution ──
            // Place (or dry-run-log) a proportional order for each new BUY.
            if !mirror_candidates.is_empty() {
                self.execute_mirrors(&cfg, &state, &cancel, mirror_candidates).await;
            }

            // Sleep until the next cycle, but break out early on cancel.
            // Polled sleep so cancel kicks in within ~200ms instead of waiting
            // for the full interval.
            let mut elapsed = 0u64;
            let step = 200u64;
            while elapsed < effective_interval_ms {
                if cancel.load(Ordering::Acquire) { break; }
                tokio::time::sleep(Duration::from_millis(step.min(effective_interval_ms - elapsed))).await;
                elapsed += step;
            }
        }

        // Loop exited (stop requested). Mark stopped + final persist.
        {
            let mut s = state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&cfg.eoa, &state.read());
    }

    /// Place a proportional mirror BUY for each candidate, or — when
    /// `auto_execute` is off — log the order it *would* place (DRY RUN) without
    /// touching the CLOB. Runs outside the per-cycle state lock; each placement
    /// is an independent network round-trip through the backend signer.
    async fn execute_mirrors(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        candidates: Vec<(ObservedTrade, f64)>,
    ) {
        // All knobs come from the strat-supplied config — nothing hardcoded.
        let user_floor = cfg.min_order_size;
        let min_shares = cfg.min_shares;
        let ceiling = cfg.max_order_size.unwrap_or(f64::INFINITY);
        let mut placed_this_cycle = 0usize;

        for (trade, copy_ratio) in candidates {
            if cancel.load(Ordering::Acquire) { break; }
            // Don't re-mirror a trade we've already acted on.
            if state.read().copied_ids.contains(&trade.id) { continue; }

            let (size, notional, price) = match plan_mirror(&trade, copy_ratio, user_floor, min_shares, ceiling) {
                MirrorPlan::Skip(reason) => {
                    self.log_and_persist(cfg, state, mk_log("SKIP", &trade.id, reason, Some(&trade.trader)));
                    continue;
                }
                MirrorPlan::Place { size, notional, price } => (size, notional, price),
            };

            // DRY RUN: surface intent, place nothing, leave the trade un-copied
            // so it isn't retroactively filled when auto_execute is later enabled.
            if !cfg.auto_execute {
                let msg = format!(
                    "DRY RUN · would BUY {:.0} @ {:.0}¢ (${:.2}) · {} · token {}",
                    size, price * 100.0, notional, trade.market, short_token(&trade.token_id),
                );
                tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price, notional, "{}", msg);
                self.log_and_persist(cfg, state, mk_log("DRY_RUN", &trade.id, msg, Some(&trade.trader)));
                continue;
            }

            if placed_this_cycle >= cfg.max_orders_per_cycle {
                self.log_and_persist(cfg, state, mk_log(
                    "INFO",
                    &format!("cap-{}", trade.id),
                    format!("order cap {} reached this cycle — remaining mirrors deferred", cfg.max_orders_per_cycle),
                    None,
                ));
                break;
            }

            // Real placement through the backend signer (no browser wallet).
            let neg_risk = self.resolve_neg_risk(&trade.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: cfg.eoa.clone(),
                // place_order ignores creds (it mints backend-owned ones), but
                // the field is required by the request shape.
                creds: ClobCreds {
                    api_key: String::new(),
                    secret: String::new(),
                    passphrase: String::new(),
                },
                args: PlaceOrderArgs {
                    token_id: trade.token_id.clone(),
                    side: OrderSide::Buy,
                    price,
                    size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: cfg.address.clone(),
                },
            };
            placed_this_cycle += 1;

            match place_order(&self.http, &self.signer_store, req).await {
                Ok(resp) => {
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += notional;
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        push_log(&mut s.log, mk_log(
                            "COPY_BUY",
                            &trade.id,
                            format!("BUY {:.0} @ {:.0}¢ (${:.2}) · {}", size, price * 100.0, notional, trade.market),
                            Some(&trade.trader),
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price, notional, response = %resp, "mirror order placed");
                }
                Err(e) => {
                    {
                        let mut s = state.write();
                        s.total_orders_failed += 1;
                        // Mark copied so a hard rejection isn't retried every cycle.
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        push_log(&mut s.log, mk_log(
                            "ERROR",
                            &trade.id,
                            format!("ORDER_FAILED: {}", e),
                            Some(&trade.trader),
                        ));
                    }
                    tracing::warn!(eoa = %cfg.eoa, market = %trade.market, error = %e, "mirror order failed");
                }
            }
            self.persist_state(&cfg.eoa, &state.read());
            tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
        }
    }

    fn log_and_persist(&self, cfg: &EngineConfig, state: &Arc<RwLock<EngineState>>, entry: LogEntry) {
        {
            let mut s = state.write();
            push_log(&mut s.log, entry);
        }
        self.persist_state(&cfg.eoa, &state.read());
    }
}

// ─── Mirror sizing ──────────────────────────────────────────────────────

enum MirrorPlan {
    Place { size: f64, notional: f64, price: f64 },
    Skip(String),
}

/// Smallest mirror notional the CLOB accepts at this price — the larger of the
/// owner's `min_order` floor and the `min_shares × price` floor. Matches
/// copyEngine.ts `clobMinNotional`, but with both floors strat-supplied.
fn clob_min_notional(price: f64, min_order: f64, min_shares: f64) -> f64 {
    (min_shares * price.max(1e-9)).max(min_order)
}

/// Round a price to the 1¢ tick grid. Leader fills arrive with full f64
/// precision which trips "Price breaks minimum tick size"; 2dp always lands
/// on a tick. Matches copyEngine.ts `tickRoundPrice`.
fn tick_round_price(p: f64) -> f64 {
    if !p.is_finite() { return 0.0; }
    (p * 100.0).round() / 100.0
}

/// Decide what to mirror for one leader BUY. `user_floor` is the user's
/// configured minimum order (≥ $1); a proportional notional below the
/// effective floor is clamped UP so it fills, unless the leader's own trade
/// was sub-floor dust (then we skip rather than over-mirror). Mirrors the
/// sizing in copyEngine.ts `executeCycle`.
fn plan_mirror(
    trade: &ObservedTrade,
    copy_ratio: f64,
    user_floor: f64,
    min_shares: f64,
    ceiling: f64,
) -> MirrorPlan {
    let pm_floor = clob_min_notional(trade.price, user_floor, min_shares);
    if ceiling < pm_floor {
        return MirrorPlan::Skip(format!(
            "CEILING_BELOW_FLOOR · ${:.2} < CLOB min ${:.2}",
            ceiling, pm_floor
        ));
    }
    let raw = trade.notional * copy_ratio;
    let min_notional = user_floor.max(pm_floor);
    let notional = if raw < min_notional {
        if trade.notional < pm_floor {
            return MirrorPlan::Skip(format!(
                "LEADER_DUST · leader ${:.2} < CLOB floor ${:.2}",
                trade.notional, pm_floor
            ));
        }
        // Clamp the sub-floor mirror UP to the floor so it actually fills.
        min_notional.min(ceiling)
    } else {
        raw.min(ceiling)
    };
    let price = tick_round_price(trade.price);
    // Whole shares on the 1¢ grid, with the strat's share floor as a backstop.
    let size = (notional / trade.price.max(1e-9)).ceil().max(min_shares);
    MirrorPlan::Place { size, notional, price }
}

fn insert_copied_id(set: &mut HashSet<String>, id: String) {
    if set.len() >= COPIED_IDS_CAP {
        // Cheap bound: clear when full. Worst case we briefly lose dedup
        // history, but the per-trader cursor already prevents re-observing
        // old trades, so a rare double-copy window is acceptable.
        set.clear();
    }
    set.insert(id);
}

fn short_token(t: &str) -> String {
    if t.len() <= 12 { t.to_string() } else { format!("{}…{}", &t[..6], &t[t.len() - 4..]) }
}

/// Build a `LogEntry` stamped at the current wall-clock ms.
fn mk_log(kind: &str, id: &str, reason: String, trader: Option<&str>) -> LogEntry {
    LogEntry {
        id: format!("{}-{}", kind.to_lowercase(), id),
        timestamp: chrono::Utc::now().timestamp_millis(),
        kind: kind.to_string(),
        reason: Some(reason),
        trader_address: trader.map(|t| t.to_string()),
        trades_seen: None,
    }
}

// ─── HTTP helpers ──────────────────────────────────────────────────────

async fn fetch_recent_activity(http: &reqwest::Client, address: &str) -> Result<Vec<Value>> {
    let url = format!(
        "{}/activity?user={}&limit=500&offset=0",
        DATA_API,
        address,
    );
    let resp = http.get(&url).send().await.context("activity GET")?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(anyhow::anyhow!("activity HTTP {}: {}", status, text));
    }
    let arr: Value = serde_json::from_str(&text).context("activity parse")?;
    if let Some(items) = arr.as_array() {
        Ok(items.clone())
    } else {
        Ok(Vec::new())
    }
}

fn parse_activity_trade(v: &Value, trader: &str) -> Option<ObservedTrade> {
    if v.get("type").and_then(|t| t.as_str()) != Some("TRADE") { return None; }
    let price = v.get("price").and_then(|p| p.as_f64()).unwrap_or(0.0);
    let size = v.get("size").and_then(|p| p.as_f64()).unwrap_or(0.0);
    if !(price.is_finite()) || !(size.is_finite()) || size <= 0.0 { return None; }
    let raw_ts = v.get("timestamp").and_then(|t| t.as_i64()).unwrap_or(0);
    if raw_ts <= 0 { return None; }
    let timestamp_ms = if raw_ts > 1_000_000_000_000 { raw_ts } else { raw_ts * 1000 };
    let id = v.get("transactionHash").and_then(|h| h.as_str()).unwrap_or("").to_string();
    let side = v.get("side").and_then(|s| s.as_str()).unwrap_or("BUY").to_uppercase();
    Some(ObservedTrade {
        id,
        timestamp: timestamp_ms,
        trader: trader.to_string(),
        market: v.get("title").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        condition_id: v.get("conditionId").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        side,
        size,
        price,
        notional: price * size,
        token_id: v.get("asset").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        outcome: v.get("outcome").and_then(|s| s.as_str()).unwrap_or("").to_string(),
    })
}

fn push_log(log: &mut Vec<LogEntry>, entry: LogEntry) {
    log.push(entry);
    if log.len() > LOG_CAP {
        let drop = log.len() - LOG_CAP;
        log.drain(0..drop);
    }
}

#[cfg(unix)]
fn restrict_perms(path: &PathBuf) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = std::fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(0o600);
        let _ = std::fs::set_permissions(path, perms);
    }
}
#[cfg(not(unix))]
fn restrict_perms(_path: &PathBuf) {}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parse_activity_trade_happy_path() {
        let v = json!({
            "type": "TRADE",
            "price": 0.55,
            "size": 10.0,
            "side": "BUY",
            "timestamp": 1700000000,
            "transactionHash": "0xabc",
            "title": "Will X happen?",
            "conditionId": "0xcid",
        });
        let t = parse_activity_trade(&v, "0xtrader").unwrap();
        assert_eq!(t.size, 10.0);
        assert_eq!(t.price, 0.55);
        assert_eq!(t.notional, 5.5);
        assert_eq!(t.timestamp, 1_700_000_000_000); // promoted to ms
        assert_eq!(t.side, "BUY");
    }

    #[test]
    fn parse_activity_skips_non_trade() {
        let v = json!({"type": "MERGE", "size": 1, "price": 0.5, "timestamp": 1});
        assert!(parse_activity_trade(&v, "0x").is_none());
    }

    #[test]
    fn parse_activity_handles_millisecond_timestamps() {
        let v = json!({"type": "TRADE", "price": 0.5, "size": 1.0, "timestamp": 1_700_000_000_000i64});
        let t = parse_activity_trade(&v, "0x").unwrap();
        assert_eq!(t.timestamp, 1_700_000_000_000);
    }

    #[test]
    fn log_buffer_respects_cap() {
        let mut log = Vec::new();
        for i in 0..(LOG_CAP + 50) {
            push_log(&mut log, LogEntry {
                id: format!("e{}", i),
                timestamp: i as i64,
                kind: "T".into(),
                reason: None,
                trader_address: None,
                trades_seen: None,
            });
        }
        assert_eq!(log.len(), LOG_CAP);
        // Oldest entries got dropped — first kept entry's id is e{LOG_CAP+50-1 - LOG_CAP + 1}? Just check it's not e0.
        assert_ne!(log.first().unwrap().id, "e0");
    }
}
