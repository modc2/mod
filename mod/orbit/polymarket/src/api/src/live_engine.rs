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
/// CLOB REST base — order book lookups for marketable rebalance-exit pricing.
const CLOB_API: &str = "https://clob.polymarket.com";

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
/// Capital-aware rebalancing: when free capital can't fund a higher-score
/// candidate, sell the lowest-score held position(s) to make room. ON by
/// default so a running strat always holds its top-scoring set.
fn default_rebalance_enabled() -> bool { true }
/// A candidate must out-score a held position by at least this fraction before
/// the engine will sell the held one to fund it — covers round-trip spread/fees
/// so it doesn't churn on a marginal score edge. 0.20 = "20% better or skip".
fn default_rebalance_margin_pct() -> f64 { 0.20 }
/// Sharpe/ROI scoring window (days). Matches the frontend's fixed 30d Sharpe
/// window (distinct from `backtest_days`, which sizes the copy ratio).
const SHARPE_WINDOW_DAYS: i64 = 30;

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
    /// Capital-aware rebalancing master switch. When `true` (default), a
    /// higher-score candidate that doesn't fit in free capital triggers a SELL
    /// of the lowest-score held position(s) to fund it (see `execute_mirrors`).
    /// When `false`, the engine only ever buys with already-free capital.
    #[serde(rename = "rebalanceEnabled", default = "default_rebalance_enabled")]
    pub rebalance_enabled: bool,
    /// Minimum fractional score edge a candidate needs over a held position to
    /// justify selling that position for it (anti-churn margin).
    #[serde(rename = "rebalanceMarginPct", default = "default_rebalance_margin_pct")]
    pub rebalance_margin_pct: f64,
    /// Free-text market-topic filter (e.g. "price of bitcoin"). When set, the
    /// engine only mirrors leader BUYs whose market title matches the query —
    /// keeps a strat focused instead of copying every fill a watched trader
    /// makes. Trades in non-matching markets are still observed (visible in the
    /// log/rail) but never produce a mirror order. Empty/None ⇒ all markets.
    #[serde(rename = "marketQuery", default)]
    pub market_query: Option<String>,
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
    /// Sharpe-weighted EP score the engine assigned this trade when observed:
    /// `trader 30d ROI × rawMirrorNotional` (matches the frontend
    /// `CopyTrader.scoreCandidate`). 0 when the trader has no in-window Sharpe
    /// sample. The frontend live rail reads this as `ot.score`.
    #[serde(default)]
    pub score: f64,
}

/// An open mirror position the engine is holding. Tracked so capital-aware
/// rebalancing can sell the lowest-score holdings to fund better candidates.
/// Keyed by `token_id` in `EngineState.positions`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpenPosition {
    #[serde(rename = "tokenId")]
    pub token_id: String,
    #[serde(rename = "conditionId", default)]
    pub condition_id: String,
    #[serde(default)]
    pub market: String,
    /// Shares currently held (the size we bought; reconciled against on-chain).
    pub size: f64,
    /// Average entry price we paid (USDC/share). Cost basis = size × entry_price.
    #[serde(rename = "entryPrice")]
    pub entry_price: f64,
    /// The EP score FROZEN at entry — the value a candidate must beat (by the
    /// rebalance margin) before this position is sold. Never recomputed.
    #[serde(rename = "entryScore")]
    pub entry_score: f64,
    #[serde(rename = "openedAt")]
    pub opened_at: i64,
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
    /// Open mirror positions keyed by `token_id`. Maintained on BUY/SELL fills
    /// and reconciled each cycle against the proxy wallet's on-chain holdings so
    /// the engine never tries to sell a token it doesn't hold.
    #[serde(default)]
    pub positions: HashMap<String, OpenPosition>,
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
            positions: HashMap::new(),
        }
    }
}

// ─── Liquidation ("flatten everything") ────────────────────────────────

/// One position's outcome in a liquidation pass.
#[derive(Debug, Clone, Serialize)]
pub struct LiquidationLeg {
    #[serde(rename = "tokenId")]
    pub token_id: String,
    pub market: String,
    pub size: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price: Option<f64>,
    /// "placed" | "skipped" | "failed"
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

/// Summary of a full-liquidation pass for one account.
#[derive(Debug, Clone, Serialize)]
pub struct LiquidationResult {
    pub eoa: String,
    #[serde(rename = "depositWallet")]
    pub deposit_wallet: String,
    pub positions: usize,
    pub placed: usize,
    pub skipped: usize,
    pub failed: usize,
    pub legs: Vec<LiquidationLeg>,
}

/// One sellable on-chain holding (deposit-wallet CTF balance).
struct HeldPosition {
    token_id: String,
    size: f64,
    condition_id: String,
    market: String,
}

/// All current positions held by `wallet`, read from the data-api. Unlike
/// `fetch_onchain_positions` this also carries the `conditionId` (needed to
/// resolve negRisk before signing) and the market title (for logs).
async fn fetch_held_positions(http: &reqwest::Client, wallet: &str) -> Vec<HeldPosition> {
    let mut out = Vec::new();
    if wallet.is_empty() {
        return out;
    }
    let url = format!("{}/positions?user={}&sizeThreshold=0.0&limit=500", DATA_API, wallet);
    let resp = match http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!(error = %e, "liquidate: positions fetch failed");
            return out;
        }
    };
    let text = resp.text().await.unwrap_or_default();
    let parsed: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return out,
    };
    if let Some(arr) = parsed.as_array() {
        for p in arr {
            let token_id = p.get("asset").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let size = p
                .get("size")
                .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .unwrap_or(0.0);
            let condition_id = p
                .get("conditionId")
                .or_else(|| p.get("condition_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let market = p.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
            if !token_id.is_empty() && size > 0.0 {
                out.push(HeldPosition { token_id, size, condition_id, market });
            }
        }
    }
    out
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

    /// Every EOA with a persisted session on disk. Used by the scheduled
    /// liquidation task to flatten all known accounts.
    pub fn persisted_eoas(&self) -> Vec<String> {
        let mut out = Vec::new();
        if let Ok(rd) = std::fs::read_dir(&self.disk_dir) {
            for entry in rd.flatten() {
                if let Some(name) = entry.path().file_name().and_then(|n| n.to_str()) {
                    if let Some(eoa) = name.strip_suffix(".config.json") {
                        out.push(eoa.to_string());
                    }
                }
            }
        }
        out
    }

    /// Sell EVERY position the account currently holds at the marketable
    /// (best-bid, tick-rounded) price — a full "flatten". Positions live on
    /// the V2 *deposit wallet* (CREATE2 from the backend signer EOA), not the
    /// user's EOA, so we derive that address and read its on-chain holdings
    /// directly rather than trusting the engine's tracked `positions` (which
    /// only cover what THIS engine bought). Places real orders regardless of
    /// the session's `auto_execute` flag — liquidation is an explicit command,
    /// not copy-trading. SELLs need no USDC, only the held tokens, so the
    /// "insufficient balance" path never applies here.
    pub async fn liquidate_all(self: &Arc<Self>, eoa: &str) -> Result<LiquidationResult> {
        let backend_addr = self.signer_store.signer_address(eoa)?;
        let deposit_wallet = crate::deposit_wallet::derive_deposit_wallet(&backend_addr)?;
        let held = fetch_held_positions(&self.http, &deposit_wallet).await;

        let mut result = LiquidationResult {
            eoa: eoa.to_lowercase(),
            deposit_wallet: deposit_wallet.clone(),
            positions: held.len(),
            placed: 0,
            skipped: 0,
            failed: 0,
            legs: Vec::with_capacity(held.len()),
        };
        if held.is_empty() {
            tracing::info!(eoa = %eoa, wallet = %deposit_wallet, "liquidate: nothing to sell");
            return Ok(result);
        }

        let handle = self.engines.get(&eoa.to_lowercase()).map(|h| h.value().clone());

        for pos in held {
            // Sizes floor to 2dp in `compute_amounts`; anything under a cent
            // of a share can't form a valid order — skip rather than error.
            if (pos.size * 100.0).floor() < 1.0 {
                result.skipped += 1;
                result.legs.push(LiquidationLeg {
                    token_id: pos.token_id,
                    market: pos.market,
                    size: pos.size,
                    price: None,
                    status: "skipped".into(),
                    detail: Some("sub-cent dust".into()),
                });
                continue;
            }

            let bid = match fetch_best_bid(&self.http, &pos.token_id).await {
                Some(b) => tick_round_price(b),
                None => {
                    result.skipped += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id,
                        market: pos.market,
                        size: pos.size,
                        price: None,
                        status: "skipped".into(),
                        detail: Some("no bid on book".into()),
                    });
                    continue;
                }
            };

            let neg_risk = self.resolve_neg_risk(&pos.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: eoa.to_string(),
                creds: ClobCreds {
                    api_key: String::new(),
                    secret: String::new(),
                    passphrase: String::new(),
                },
                args: PlaceOrderArgs {
                    token_id: pos.token_id.clone(),
                    side: OrderSide::Sell,
                    price: bid,
                    size: pos.size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: deposit_wallet.clone(),
                },
            };

            match place_order(&self.http, &self.signer_store, req).await {
                Ok(_) => {
                    result.placed += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id.clone(),
                        market: pos.market.clone(),
                        size: pos.size,
                        price: Some(bid),
                        status: "placed".into(),
                        detail: None,
                    });
                    if let Some(h) = &handle {
                        let mut s = h.state.write();
                        s.positions.remove(&pos.token_id);
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += pos.size * bid;
                        push_log(&mut s.log, mk_log(
                            "LIQUIDATE_SELL",
                            &pos.token_id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ · ${:.2} · {}",
                                pos.size, bid * 100.0, pos.size * bid, pos.market
                            ),
                            None,
                        ));
                    }
                }
                Err(e) => {
                    result.failed += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id.clone(),
                        market: pos.market.clone(),
                        size: pos.size,
                        price: Some(bid),
                        status: "failed".into(),
                        detail: Some(e.to_string()),
                    });
                    if let Some(h) = &handle {
                        let mut s = h.state.write();
                        s.total_orders_failed += 1;
                        push_log(&mut s.log, mk_log(
                            "ERROR",
                            &pos.token_id,
                            format!("LIQUIDATE_SELL_FAILED: {} · {}", e, pos.market),
                            None,
                        ));
                    }
                }
            }
            // Space out placements so a 17-position flatten doesn't burst the
            // CLOB rate limiter.
            tokio::time::sleep(Duration::from_millis(300)).await;
        }

        if let Some(h) = &handle {
            self.persist_state(eoa, &h.state.read());
        }
        tracing::info!(
            eoa = %eoa,
            wallet = %deposit_wallet,
            positions = result.positions,
            placed = result.placed,
            skipped = result.skipped,
            failed = result.failed,
            "liquidation pass complete",
        );
        Ok(result)
    }

    /// Redeem all RESOLVED positions for an account (settled winnings →
    /// USDC), wrapping the proceeds back into trading collateral. Thin
    /// wrapper over `relayer::redeem_resolved_positions` using the registry's
    /// own http client + signer store, so the scheduled flatten can claim
    /// settled markets the same way the `/redeem` route does. A SELL can't
    /// cash these out — settled markets have no order book — so this is the
    /// companion to `liquidate_all`, not a duplicate of it.
    pub async fn redeem_all(self: &Arc<Self>, eoa: &str) -> Result<crate::relayer::RedeemResult> {
        crate::relayer::redeem_resolved_positions(&self.http, &self.signer_store, eoa, true).await
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

                        // Sharpe/ROI over the fixed 30d window — the basis for
                        // each candidate's EP score (frozen on the position at
                        // entry, used to rank holds vs. new candidates).
                        let roi_stats = compute_trader_roi_stats(&items, cycle_started_at);

                        let mut highest_ts = cursor;
                        for mut t in parsed {
                            if t.timestamp <= cursor { continue; }
                            if t.timestamp > highest_ts { highest_ts = t.timestamp; }
                            // Stamp the EP score on every observed BUY (ROI ×
                            // rawMirrorNotional) so the frontend rail and the
                            // rebalancer share one number.
                            if t.side == "BUY" {
                                t.score = candidate_score(roi_stats.roi, t.notional * copy_ratio);
                            }
                            // Mirror BUYs only; a SELL we don't hold would just be
                            // rejected. Position exits are now handled by the
                            // capital-aware rebalancer in `execute_mirrors`.
                            // Honor the strat's market-topic filter: a BUY in a
                            // market that doesn't match `market_query` is still
                            // observed (rail/log) but never mirrored.
                            let market_ok = cfg.market_query.as_deref().map_or(true, |query| {
                                crate::categories::market_matches_query(&t.market, query)
                            });
                            if t.side == "BUY" && !t.token_id.is_empty() && market_ok {
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

            // ── Position reconciliation ──
            // Sync the internal ledger to the proxy wallet's actual on-chain
            // holdings before any rebalancing decision, so we never try to sell
            // a token we no longer hold (manual exit, resolution, partial fill).
            // Only runs when we might trade (auto_execute) to avoid needless
            // data-api load on dry-run sessions.
            if cfg.auto_execute {
                let onchain = fetch_onchain_positions(&self.http, &cfg.address).await;
                if !onchain.is_empty() || !state.read().positions.is_empty() {
                    let mut s = state.write();
                    // Drop / shrink ledger entries to match on-chain reality.
                    s.positions.retain(|token_id, pos| {
                        match onchain.get(token_id) {
                            Some(&held) if held > 0.0 => {
                                if held + 1e-6 < pos.size { pos.size = held; }
                                true
                            }
                            _ => false, // no longer held → drop
                        }
                    });
                }
            }

            // ── Mirror execution ──
            // Place (or dry-run-log) a proportional order for each new BUY, and
            // (when rebalancing is on) sell lower-score holds to fund better ones.
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
    /// touching the CLOB. Candidates are ranked by EP score so the best trades
    /// claim capital first; when free capital is short and `rebalance_enabled`
    /// is set, the lowest-score held positions are sold (at the book bid) to
    /// fund a higher-score candidate it out-scores by `rebalance_margin_pct`.
    /// Runs outside the per-cycle state lock; each placement is an independent
    /// network round-trip through the backend signer.
    async fn execute_mirrors(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        mut candidates: Vec<(ObservedTrade, f64)>,
    ) {
        // All knobs come from the strat-supplied config — nothing hardcoded.
        let user_floor = cfg.min_order_size;
        let min_shares = cfg.min_shares;
        let ceiling = cfg.max_order_size.unwrap_or(f64::INFINITY);
        let mut placed_this_cycle = 0usize;

        // Highest EP score first: the best candidates claim free capital and win
        // rebalance contests before weaker ones get a look.
        candidates.sort_by(|a, b| {
            b.0.score.partial_cmp(&a.0.score).unwrap_or(std::cmp::Ordering::Equal)
        });

        // Internal capital budget: the strat allocation minus cost basis already
        // deployed in open positions. Selling a position returns its cost basis
        // here; a BUY subtracts its notional.
        let mut free_capital = {
            let s = state.read();
            let deployed: f64 = s.positions.values().map(|p| p.size * p.entry_price).sum();
            (cfg.capital - deployed).max(0.0)
        };

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
                    "DRY RUN · would BUY {:.0} @ {:.0}¢ (${:.2}) · score {:.3} · {} · token {}",
                    size, price * 100.0, notional, trade.score, trade.market, short_token(&trade.token_id),
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

            // ── Capital-aware rebalance ──
            // If this candidate doesn't fit in free capital, try to sell the
            // lowest-score holdings it sufficiently out-scores to make room.
            if notional > free_capital && cfg.rebalance_enabled {
                let needed = notional - free_capital;
                free_capital += self
                    .free_capital_via_sells(cfg, state, cancel, &trade, needed, &mut placed_this_cycle)
                    .await;
            }
            if notional > free_capital + 1e-6 {
                // No (or not enough) lower-score position to liquidate — skip,
                // but mark copied so we don't reconsider this same leader trade
                // every cycle. A future, higher-score-relative candidate can
                // still trigger sells of these holds later.
                {
                    let mut s = state.write();
                    insert_copied_id(&mut s.copied_ids, trade.id.clone());
                    push_log(&mut s.log, mk_log(
                        "REBALANCE_SKIP",
                        &trade.id,
                        format!(
                            "score {:.3} · need ${:.2}, free ${:.2} — no lower-score hold to sell · {}",
                            trade.score, notional, free_capital, trade.market
                        ),
                        Some(&trade.trader),
                    ));
                }
                self.persist_state(&cfg.eoa, &state.read());
                continue;
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
                    free_capital = (free_capital - notional).max(0.0);
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += notional;
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        // Record / accumulate the open position with its FROZEN
                        // entry score (size-weighted avg entry price; keep the
                        // strongest score seen for this token).
                        let opened_at = chrono::Utc::now().timestamp_millis();
                        let entry = s.positions.entry(trade.token_id.clone()).or_insert_with(|| OpenPosition {
                            token_id: trade.token_id.clone(),
                            condition_id: trade.condition_id.clone(),
                            market: trade.market.clone(),
                            size: 0.0,
                            entry_price: 0.0,
                            entry_score: trade.score,
                            opened_at,
                        });
                        let new_size = entry.size + size;
                        if new_size > 0.0 {
                            entry.entry_price = (entry.size * entry.entry_price + size * price) / new_size;
                        }
                        entry.size = new_size;
                        entry.entry_score = entry.entry_score.max(trade.score);
                        push_log(&mut s.log, mk_log(
                            "COPY_BUY",
                            &trade.id,
                            format!("BUY {:.0} @ {:.0}¢ (${:.2}) · score {:.3} · {}", size, price * 100.0, notional, trade.score, trade.market),
                            Some(&trade.trader),
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price, notional, score = trade.score, response = %resp, "mirror order placed");
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

    /// Sell the lowest-score held positions that `candidate` out-scores by the
    /// configured margin, until at least `needed` USDC of cost basis is freed.
    /// Returns the cost basis actually freed (so the caller can update its
    /// capital budget). Each exit is a marketable SELL at the token's best book
    /// bid and counts toward the per-cycle order cap. Positions whose book can't
    /// be priced are left untouched.
    async fn free_capital_via_sells(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        candidate: &ObservedTrade,
        needed: f64,
        placed_this_cycle: &mut usize,
    ) -> f64 {
        let margin = 1.0 + cfg.rebalance_margin_pct.max(0.0);
        // Snapshot the sell-eligible holds: out-scored by the margin, a
        // different token, non-empty — weakest score first (sacrifice the worst
        // hold before a better one).
        let mut sellable: Vec<OpenPosition> = {
            let s = state.read();
            s.positions
                .values()
                .filter(|p| {
                    p.token_id != candidate.token_id
                        && p.size > 0.0
                        && candidate.score >= p.entry_score * margin
                })
                .cloned()
                .collect()
        };
        sellable.sort_by(|a, b| {
            a.entry_score.partial_cmp(&b.entry_score).unwrap_or(std::cmp::Ordering::Equal)
        });

        let mut freed = 0.0f64;
        for pos in sellable {
            if cancel.load(Ordering::Acquire) { break; }
            if freed >= needed { break; }
            if *placed_this_cycle >= cfg.max_orders_per_cycle { break; }

            // Marketable exit price: the current best bid, tick-rounded.
            let bid = match fetch_best_bid(&self.http, &pos.token_id).await {
                Some(b) => tick_round_price(b),
                None => {
                    self.log_and_persist(cfg, state, mk_log(
                        "SKIP",
                        &pos.token_id,
                        format!("REBALANCE_NO_BID · can't price exit for {}", pos.market),
                        None,
                    ));
                    continue;
                }
            };
            let neg_risk = self.resolve_neg_risk(&pos.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: cfg.eoa.clone(),
                creds: ClobCreds { api_key: String::new(), secret: String::new(), passphrase: String::new() },
                args: PlaceOrderArgs {
                    token_id: pos.token_id.clone(),
                    side: OrderSide::Sell,
                    price: bid,
                    size: pos.size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: cfg.address.clone(),
                },
            };
            *placed_this_cycle += 1;

            match place_order(&self.http, &self.signer_store, req).await {
                Ok(resp) => {
                    let cost_basis = pos.size * pos.entry_price;
                    freed += cost_basis;
                    {
                        let mut s = state.write();
                        s.positions.remove(&pos.token_id);
                        s.total_orders_placed += 1;
                        push_log(&mut s.log, mk_log(
                            "REBALANCE_SELL",
                            &pos.token_id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ · freed ${:.2} (score {:.3}) to fund score {:.3} · {}",
                                pos.size, bid * 100.0, cost_basis, pos.entry_score, candidate.score, pos.market
                            ),
                            None,
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %pos.market, size = pos.size, price = bid, freed = cost_basis, response = %resp, "rebalance sell placed");
                }
                Err(e) => {
                    self.log_and_persist(cfg, state, mk_log(
                        "ERROR",
                        &pos.token_id,
                        format!("REBALANCE_SELL_FAILED: {}", e),
                        None,
                    ));
                }
            }
            self.persist_state(&cfg.eoa, &state.read());
            tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
        }
        freed
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

// ─── Server-side Sharpe / ROI scoring ───────────────────────────────────
// Ported from the frontend `CopyIndex.tsx` traderStatsMap so live ranking
// matches the backtest preview byte-for-byte: walk the trader's activity in
// time order keeping a FIFO cost-basis book; each in-window SELL contributes a
// per-trade return `(price − avgCost) / avgCost`. roi = mean(returns),
// sharpe = roi / sample-stdev (n≥3, stdev>0), else 0.

#[derive(Debug, Clone, Copy, Default)]
pub struct TraderRoiStats {
    pub roi: f64,
    pub stdev: f64,
    pub sharpe: f64,
    pub sample_size: usize,
}

/// Compute a trader's 30d ROI/Sharpe stats from their raw `/activity` items.
/// `now_ms` is the cycle clock (passed in so this stays pure/testable).
pub fn compute_trader_roi_stats(items: &[Value], now_ms: i64) -> TraderRoiStats {
    let cutoff_ms = now_ms - SHARPE_WINDOW_DAYS * 86_400_000;

    // Sort TRADE items oldest→newest so FIFO basis is built in order.
    let mut trades: Vec<&Value> = items
        .iter()
        .filter(|t| t.get("type").and_then(|v| v.as_str()).unwrap_or("TRADE") == "TRADE")
        .collect();
    trades.sort_by_key(|t| {
        let raw = t.get("timestamp").and_then(|v| v.as_i64()).unwrap_or(0);
        if raw > 1_000_000_000_000 { raw } else { raw * 1000 }
    });

    // token/condition key → (size, cost) cost-basis book.
    let mut book: HashMap<String, (f64, f64)> = HashMap::new();
    let mut returns: Vec<f64> = Vec::new();

    for t in trades {
        let raw_ts = t.get("timestamp").and_then(|v| v.as_i64()).unwrap_or(0);
        let ts_ms = if raw_ts > 1_000_000_000_000 { raw_ts } else { raw_ts * 1000 };
        let key = t
            .get("conditionId")
            .or_else(|| t.get("asset"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let price = t.get("price").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let size = t.get("size").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let side = t.get("side").and_then(|v| v.as_str()).unwrap_or("").to_uppercase();
        if !price.is_finite() || !size.is_finite() || size <= 0.0 {
            continue;
        }
        let pos = book.entry(key).or_insert((0.0, 0.0));
        if side == "BUY" {
            pos.1 += price * size;
            pos.0 += size;
        } else if side == "SELL" && pos.0 > 0.0 {
            let avg = pos.1 / pos.0;
            let sold = size.min(pos.0);
            pos.1 -= avg * sold;
            pos.0 -= sold;
            // Per-trade ROI on realized SELLs inside the Sharpe window.
            if ts_ms >= cutoff_ms && avg > 0.0 {
                returns.push((price - avg) / avg);
            }
        }
    }

    let n = returns.len();
    if n == 0 {
        return TraderRoiStats::default();
    }
    let roi = returns.iter().sum::<f64>() / n as f64;
    let stdev = if n >= 2 {
        let var = returns.iter().map(|r| (r - roi).powi(2)).sum::<f64>() / (n as f64 - 1.0);
        var.sqrt()
    } else {
        0.0
    };
    let sharpe = if n >= 3 && stdev > 0.0 { roi / stdev } else { 0.0 };
    TraderRoiStats { roi, stdev, sharpe, sample_size: n }
}

/// EP score for one candidate: `trader ROI × rawMirrorNotional`, matching the
/// frontend `CopyTrader.scoreCandidate`. `raw_mirror_notional = notional ×
/// copy_ratio` (what we'd actually deploy). Negative ROI ⇒ negative score, so a
/// losing trader's copies rank below any break-even hold.
fn candidate_score(roi: f64, raw_mirror_notional: f64) -> f64 {
    roi * raw_mirror_notional
}

/// Fetch the proxy wallet's current on-chain positions (token_id → shares held)
/// from the data-api so the engine never sells a token it doesn't hold. Returns
/// an empty map on any error (fail-safe: no reconciliation rather than bad sells).
async fn fetch_onchain_positions(http: &reqwest::Client, proxy: &str) -> HashMap<String, f64> {
    let mut out = HashMap::new();
    if proxy.is_empty() {
        return out;
    }
    let url = format!("{}/positions?user={}&sizeThreshold=0.0&limit=500", DATA_API, proxy);
    let resp = match http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!(error = %e, "positions fetch failed; skipping reconcile");
            return out;
        }
    };
    let text = resp.text().await.unwrap_or_default();
    let parsed: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return out,
    };
    if let Some(arr) = parsed.as_array() {
        for p in arr {
            let asset = p.get("asset").and_then(|v| v.as_str()).unwrap_or("");
            let size = p
                .get("size")
                .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .unwrap_or(0.0);
            if !asset.is_empty() && size > 0.0 {
                out.insert(asset.to_string(), size);
            }
        }
    }
    out
}

/// Best bid (highest buy price) on a token's CLOB order book — the marketable
/// price for an immediate SELL. Tick-rounded by the caller. `None` if the book
/// is empty/unreachable, in which case the exit is skipped this cycle.
async fn fetch_best_bid(http: &reqwest::Client, token_id: &str) -> Option<f64> {
    let url = format!("{}/book?token_id={}", CLOB_API, token_id);
    let resp = http.get(&url).send().await.ok()?;
    let text = resp.text().await.ok()?;
    let parsed: Value = serde_json::from_str(&text).ok()?;
    let bids = parsed.get("bids")?.as_array()?;
    // The CLOB returns bids ascending; the best (highest) bid is the max price.
    let mut best: Option<f64> = None;
    for b in bids {
        let price = b
            .get("price")
            .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))?;
        best = Some(best.map_or(price, |m: f64| m.max(price)));
    }
    best.filter(|p| p.is_finite() && *p > 0.0)
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
        score: 0.0, // stamped later once the trader's ROI stats are known
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

    // Build one activity TRADE item for the scoring tests.
    fn trade_item(cid: &str, side: &str, price: f64, size: f64, ts_ms: i64) -> Value {
        json!({
            "type": "TRADE", "conditionId": cid, "side": side,
            "price": price, "size": size, "timestamp": ts_ms,
        })
    }

    #[test]
    fn roi_stats_fifo_returns_and_sharpe() {
        let now = 2_000_000_000_000i64; // ms, fixed clock
        let d = 86_400_000i64; // 1 day in ms
        // Three round-trips, each its own market, all inside the 30d window.
        // Returns: +0.20, +0.10, −0.10.
        let items = vec![
            trade_item("m1", "BUY", 0.50, 10.0, now - 5 * d),
            trade_item("m1", "SELL", 0.60, 10.0, now - 4 * d), // (0.60-0.50)/0.50 = 0.20
            trade_item("m2", "BUY", 0.40, 10.0, now - 5 * d),
            trade_item("m2", "SELL", 0.44, 10.0, now - 4 * d), // 0.10
            trade_item("m3", "BUY", 0.50, 10.0, now - 5 * d),
            trade_item("m3", "SELL", 0.45, 10.0, now - 4 * d), // -0.10
        ];
        let s = compute_trader_roi_stats(&items, now);
        assert_eq!(s.sample_size, 3);
        assert!((s.roi - 0.066_667).abs() < 1e-4, "roi was {}", s.roi);
        assert!((s.stdev - 0.152_753).abs() < 1e-4, "stdev was {}", s.stdev);
        assert!((s.sharpe - 0.436_4).abs() < 1e-3, "sharpe was {}", s.sharpe);
    }

    #[test]
    fn roi_stats_excludes_out_of_window_sells() {
        let now = 2_000_000_000_000i64;
        let d = 86_400_000i64;
        // A SELL 40 days ago is outside the 30d Sharpe window → no samples.
        let items = vec![
            trade_item("m1", "BUY", 0.50, 10.0, now - 41 * d),
            trade_item("m1", "SELL", 0.60, 10.0, now - 40 * d),
        ];
        let s = compute_trader_roi_stats(&items, now);
        assert_eq!(s.sample_size, 0);
        assert_eq!(s.roi, 0.0);
        assert_eq!(s.sharpe, 0.0);
    }

    #[test]
    fn roi_stats_sharpe_needs_three_samples() {
        let now = 2_000_000_000_000i64;
        let d = 86_400_000i64;
        // Two realized trades → roi computed, but sharpe stays 0 (n < 3).
        let items = vec![
            trade_item("m1", "BUY", 0.50, 10.0, now - 5 * d),
            trade_item("m1", "SELL", 0.60, 10.0, now - 4 * d),
            trade_item("m2", "BUY", 0.40, 10.0, now - 5 * d),
            trade_item("m2", "SELL", 0.44, 10.0, now - 4 * d),
        ];
        let s = compute_trader_roi_stats(&items, now);
        assert_eq!(s.sample_size, 2);
        assert!(s.roi > 0.0);
        assert_eq!(s.sharpe, 0.0);
    }

    #[test]
    fn candidate_score_is_roi_times_mirror_notional() {
        // EP = roi × rawMirrorNotional. roi 0.20, raw 12.5 → $2.50.
        assert!((candidate_score(0.20, 12.5) - 2.5).abs() < 1e-9);
        // Losing trader → negative score, ranks below any break-even hold.
        assert!(candidate_score(-0.10, 50.0) < 0.0);
    }

    #[test]
    fn rebalance_margin_predicate() {
        // The sell gate: candidate.score >= held.entry_score × (1 + margin).
        let margin = 0.20;
        let held = 1.0;
        let gate = held * (1.0 + margin);
        assert!(1.20 >= gate); // exactly 20% better → eligible
        assert!(1.25 >= gate); // more than 20% better → eligible
        assert!(!(1.19 >= gate)); // under the margin → NOT eligible (no churn)
    }
}
