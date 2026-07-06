//! Live copy-trade engine for Hyperliquid.
//!
//! One tokio task per follower EOA. Each task:
//!   1. polls each watched leader's recent fills via /info userFillsByTime,
//!   2. dedupes by trade id (`tid`),
//!   3. scales the leader's fill size by `size_pct` (and clamps to
//!      max_per_trade_usd / min_order_size_usd),
//!   4. submits a slippage-padded IOC market order through `actions::place_market_order`,
//!   5. updates state + persists to disk.
//!
//! State persists per-EOA to `<HYPERLIQUID_DATA_DIR>/live-engine/<eoa>.config.json`
//! and `<eoa>.state.json`. On API boot the registry calls `resume_persisted`,
//! which restarts every session whose config file is present. Explicit STOP
//! deletes the config so it doesn't auto-resume.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use dashmap::DashMap;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use tokio::task::JoinHandle;

use crate::actions::{self, MetaCache, OrderSide};
use crate::hl::{parse_fills, Client};
use crate::signer::SignerStore;

const PERSIST_DIR_NAME: &str = "live-engine";
const OBSERVED_CAP: usize = 500;
const LOG_CAP: usize = 1000;

// ─── Types ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineStatus { Stopped, Starting, Running, Paused, Error }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraderEntry {
    pub address: String,
    pub weight: f64,
    #[serde(default = "default_true")]
    pub enabled: bool,
}
fn default_true() -> bool { true }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineConfig {
    pub eoa: String,
    #[serde(rename = "strategyId", default)]
    pub strategy_id: String,
    pub traders: Vec<TraderEntry>,
    #[serde(default)]
    pub capital: f64,
    #[serde(rename = "intervalMs")]
    pub interval_ms: u64,
    #[serde(rename = "minOrderSizeUsd", default = "default_min")]
    pub min_order_size_usd: f64,
    #[serde(rename = "maxSlippageBps", default = "default_slip")]
    pub max_slippage_bps: u32,
    /// Mirror leader.size × size_pct / 100. Capped per-trade by max_per_trade_usd.
    #[serde(rename = "sizePct", default = "default_size_pct")]
    pub size_pct: f64,
    #[serde(rename = "maxPerTradeUsd", default)]
    pub max_per_trade_usd: f64,
    #[serde(rename = "coinsAllow", default)]
    pub coins_allow: Vec<String>,
    #[serde(rename = "coinsDeny", default)]
    pub coins_deny: Vec<String>,
    /// If true, route mirror orders through this vault.
    #[serde(rename = "vaultAddress", default)]
    pub vault_address: Option<String>,
}
fn default_min() -> f64 { 10.0 }
fn default_slip() -> u32 { 100 }
fn default_size_pct() -> f64 { 10.0 }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObservedTrade {
    pub id: String,
    pub timestamp: i64,
    pub trader: String,
    pub coin: String,
    pub side: String,
    pub size: f64,
    pub price: f64,
    pub notional: f64,
    pub tid: u64,
    pub mirrored: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mirror_error: Option<String>,
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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub coin: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineState {
    pub status: EngineStatus,
    #[serde(rename = "lastCycleAt")] pub last_cycle_at: Option<i64>,
    #[serde(rename = "nextCycleAt")] pub next_cycle_at: Option<i64>,
    #[serde(rename = "cycleCount")] pub cycle_count: u64,
    #[serde(rename = "totalOrdersPlaced")] pub total_orders_placed: u64,
    #[serde(rename = "totalOrdersFailed")] pub total_orders_failed: u64,
    #[serde(rename = "totalVolumeMirrored")] pub total_volume_mirrored: f64,
    pub log: Vec<LogEntry>,
    #[serde(rename = "observedTrades")] pub observed_trades: Vec<ObservedTrade>,
    pub error: Option<String>,
    #[serde(rename = "traderCursors", default)] pub trader_cursors: HashMap<String, u64>,
    #[serde(rename = "traderLastSync", default)] pub trader_last_sync: HashMap<String, i64>,
}

impl EngineState {
    fn empty() -> Self {
        Self {
            status: EngineStatus::Stopped,
            last_cycle_at: None, next_cycle_at: None,
            cycle_count: 0, total_orders_placed: 0, total_orders_failed: 0,
            total_volume_mirrored: 0.0,
            log: Vec::new(), observed_trades: Vec::new(), error: None,
            trader_cursors: HashMap::new(), trader_last_sync: HashMap::new(),
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
    hl: Arc<Client>,
    signer: Arc<SignerStore>,
    meta: Arc<MetaCache>,
    disk_dir: PathBuf,
}

impl EngineRegistry {
    pub fn new(hl: Arc<Client>, signer: Arc<SignerStore>, meta: Arc<MetaCache>) -> Self {
        let base = std::env::var("HYPERLIQUID_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| {
                let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
                PathBuf::from(format!("{home}/.hyperliquid"))
            });
        let disk_dir = base.join(PERSIST_DIR_NAME);
        std::fs::create_dir_all(&disk_dir).ok();
        Self {
            engines: DashMap::new(),
            http: reqwest::Client::builder().timeout(Duration::from_secs(20)).build().expect("http"),
            hl, signer, meta, disk_dir,
        }
    }

    fn path_config(&self, eoa: &str) -> PathBuf { self.disk_dir.join(format!("{}.config.json", eoa.to_lowercase())) }
    fn path_state(&self, eoa: &str) -> PathBuf  { self.disk_dir.join(format!("{}.state.json",  eoa.to_lowercase())) }

    pub fn resume_persisted(self: &Arc<Self>) {
        let Ok(rd) = std::fs::read_dir(&self.disk_dir) else { return; };
        for entry in rd.flatten() {
            let p = entry.path();
            let Some(name) = p.file_name().and_then(|n| n.to_str()) else { continue; };
            if !name.ends_with(".config.json") { continue; }
            let Ok(raw) = std::fs::read_to_string(&p) else { continue; };
            let Ok(cfg): Result<EngineConfig, _> = serde_json::from_str(&raw) else { continue; };
            tracing::info!("resuming live engine for {}", cfg.eoa);
            let sp = self.path_state(&cfg.eoa);
            let st = std::fs::read_to_string(&sp).ok()
                .and_then(|s| serde_json::from_str::<EngineState>(&s).ok())
                .unwrap_or_else(EngineState::empty);
            self.start_internal(cfg, Some(st));
        }
    }

    pub fn status_of(&self, eoa: &str) -> Option<EngineState> {
        self.engines.get(&eoa.to_lowercase()).map(|h| h.state.read().clone())
    }
    pub fn config_of(&self, eoa: &str) -> Option<EngineConfig> {
        self.engines.get(&eoa.to_lowercase()).map(|h| h.config.clone())
    }

    pub fn start(self: &Arc<Self>, cfg: EngineConfig) {
        let lc = cfg.eoa.to_lowercase();
        if let Some((_, existing)) = self.engines.remove(&lc) {
            existing.cancel.store(true, Ordering::Release);
            if let Some(t) = existing.task.lock().take() { t.abort(); }
        }
        let path = self.path_config(&cfg.eoa);
        if let Ok(j) = serde_json::to_string_pretty(&cfg) { let _ = std::fs::write(&path, j); }
        self.start_internal(cfg, None);
    }

    fn persist_state(&self, eoa: &str, st: &EngineState) {
        if let Ok(j) = serde_json::to_string(st) {
            let _ = std::fs::write(self.path_state(eoa), j);
        }
    }

    fn start_internal(self: &Arc<Self>, cfg: EngineConfig, restore: Option<EngineState>) {
        let mut initial = restore.unwrap_or_else(EngineState::empty);
        initial.status = EngineStatus::Running;
        initial.error = None;
        let state = Arc::new(RwLock::new(initial));
        let cancel = Arc::new(AtomicBool::new(false));
        let handle = Arc::new(EngineHandle {
            config: cfg.clone(), state: state.clone(), cancel: cancel.clone(),
            task: parking_lot::Mutex::new(None),
        });
        self.engines.insert(cfg.eoa.to_lowercase(), handle.clone());
        let registry = Arc::clone(self);
        let task = tokio::spawn(async move { registry.run_loop(cfg, state, cancel).await; });
        *handle.task.lock() = Some(task);
    }

    pub fn stop(&self, eoa: &str) -> bool {
        let lc = eoa.to_lowercase();
        let Some((_, handle)) = self.engines.remove(&lc) else { return false; };
        handle.cancel.store(true, Ordering::Release);
        if let Some(t) = handle.task.lock().take() { t.abort(); }
        {
            let mut s = handle.state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&lc, &handle.state.read());
        let _ = std::fs::remove_file(self.path_config(&lc));
        true
    }

    async fn run_loop(
        self: Arc<Self>,
        cfg: EngineConfig,
        state: Arc<RwLock<EngineState>>,
        cancel: Arc<AtomicBool>,
    ) {
        // Seed cursors to "now - interval" so the first cycle's window is
        // bounded (rather than back-filling weeks of history).
        let now_ms = chrono::Utc::now().timestamp_millis();
        {
            let mut s = state.write();
            for t in &cfg.traders {
                s.trader_cursors.entry(t.address.to_lowercase()).or_insert(0);
            }
            let _ = now_ms;
        }

        loop {
            if cancel.load(Ordering::Acquire) { break; }
            let _cycle_start = chrono::Utc::now().timestamp_millis();
            let mut new_observed: Vec<ObservedTrade> = Vec::new();
            let mut errors: Vec<(String, String)> = Vec::new();
            let mut cursor_updates: Vec<(String, u64)> = Vec::new();
            let mut sync_updates: Vec<(String, i64)> = Vec::new();
            let mut orders_placed = 0u64;
            let mut orders_failed = 0u64;
            let mut volume = 0.0f64;

            let (cursors, traders): (HashMap<String, u64>, Vec<TraderEntry>) = {
                let s = state.read();
                (s.trader_cursors.clone(), cfg.traders.iter().filter(|t| t.enabled).cloned().collect())
            };

            for t in &traders {
                if cancel.load(Ordering::Acquire) { break; }
                let key = t.address.to_lowercase();
                let cursor_tid = *cursors.get(&key).unwrap_or(&0);
                // Window: 5 minutes back. user_fills_by_time has its own 31-day
                // cap and 5-minute response cache, so this stays cheap.
                let since = chrono::Utc::now().timestamp_millis() - 5 * 60_000;
                match self.hl.user_fills_by_time(&t.address, since).await {
                    Ok(v) => {
                        sync_updates.push((key.clone(), chrono::Utc::now().timestamp_millis()));
                        let fills = parse_fills(&v);
                        // Fills come newest-first; flip for chronological order.
                        let mut max_tid = cursor_tid;
                        for fill in fills.iter().rev() {
                            if fill.tid <= cursor_tid { continue; }
                            if !cfg.coins_allow.is_empty()
                                && !cfg.coins_allow.iter().any(|c| c.eq_ignore_ascii_case(&fill.coin)) { continue; }
                            if cfg.coins_deny.iter().any(|c| c.eq_ignore_ascii_case(&fill.coin)) { continue; }
                            let leader_px: f64 = fill.px.parse().unwrap_or(0.0);
                            let leader_sz: f64 = fill.sz.parse().unwrap_or(0.0);
                            if leader_px <= 0.0 || leader_sz <= 0.0 { continue; }

                            // Scale by both follow weight and engine size_pct.
                            let scaled_sz = leader_sz * (cfg.size_pct / 100.0) * t.weight.max(0.0);
                            let notional = scaled_sz * leader_px;
                            let (final_sz, _notional) = if cfg.max_per_trade_usd > 0.0 && notional > cfg.max_per_trade_usd {
                                let s = cfg.max_per_trade_usd / leader_px;
                                (s, s * leader_px)
                            } else { (scaled_sz, notional) };
                            let final_notional = final_sz * leader_px;
                            let side_str = if fill.side == "B" { "BUY" } else { "SELL" };
                            let mut observed = ObservedTrade {
                                id: format!("{}-{}", t.address, fill.tid),
                                timestamp: fill.time,
                                trader: t.address.clone(),
                                coin: fill.coin.clone(),
                                side: side_str.to_string(),
                                size: leader_sz, price: leader_px, notional: leader_sz * leader_px,
                                tid: fill.tid, mirrored: false, mirror_error: None,
                            };
                            if fill.tid > max_tid { max_tid = fill.tid; }

                            if final_notional < cfg.min_order_size_usd {
                                observed.mirror_error = Some(format!(
                                    "skipped: notional {:.2} < min {}", final_notional, cfg.min_order_size_usd));
                                new_observed.push(observed);
                                continue;
                            }

                            let side = if fill.side == "B" { OrderSide::Buy } else { OrderSide::Sell };
                            match actions::place_market_order(
                                &self.http, &self.hl, &self.signer, &self.meta,
                                &cfg.eoa, &fill.coin, side, final_sz,
                                cfg.max_slippage_bps, false,
                                cfg.vault_address.as_deref(),
                            ).await {
                                Ok(_resp) => {
                                    observed.mirrored = true;
                                    orders_placed += 1;
                                    volume += final_notional;
                                }
                                Err(e) => {
                                    observed.mirror_error = Some(e.to_string());
                                    orders_failed += 1;
                                    errors.push((fill.coin.clone(), e.to_string()));
                                }
                            }
                            new_observed.push(observed);
                        }
                        if max_tid > cursor_tid { cursor_updates.push((key, max_tid)); }
                    }
                    Err(e) => errors.push((t.address.clone(), e.to_string())),
                }
            }

            let cycle_end = chrono::Utc::now().timestamp_millis();
            {
                let mut s = state.write();
                let mut combined = new_observed;
                combined.extend(s.observed_trades.drain(..));
                combined.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));
                combined.truncate(OBSERVED_CAP);
                s.observed_trades = combined;
                for (k, ts) in sync_updates { s.trader_last_sync.insert(k, ts); }
                for (k, t) in cursor_updates { s.trader_cursors.insert(k, t); }
                let count = s.cycle_count + 1;
                let summary = if errors.is_empty() {
                    format!("polled {} traders · {} placed / {} skipped",
                        traders.len(), orders_placed, orders_failed)
                } else {
                    format!("polled {} traders · {} placed / {} failed",
                        traders.len(), orders_placed, orders_failed)
                };
                push_log(&mut s.log, LogEntry {
                    id: format!("cycle-{}", count),
                    timestamp: cycle_end, kind: "CYCLE_END".into(),
                    reason: Some(summary), trader_address: None, coin: None,
                });
                for (addr, err) in &errors {
                    push_log(&mut s.log, LogEntry {
                        id: format!("err-{}-{}", cycle_end, addr),
                        timestamp: cycle_end, kind: "ERROR".into(),
                        reason: Some(err.clone()),
                        trader_address: Some(addr.clone()), coin: None,
                    });
                }
                s.cycle_count += 1;
                s.last_cycle_at = Some(cycle_end);
                s.next_cycle_at = Some(cycle_end + cfg.interval_ms as i64);
                s.total_orders_placed += orders_placed;
                s.total_orders_failed += orders_failed;
                s.total_volume_mirrored += volume;
                s.status = EngineStatus::Running;
            }
            self.persist_state(&cfg.eoa, &state.read());

            // Polled sleep so cancel resolves promptly.
            let mut elapsed = 0u64;
            let step = 200u64;
            while elapsed < cfg.interval_ms {
                if cancel.load(Ordering::Acquire) { break; }
                tokio::time::sleep(Duration::from_millis(step.min(cfg.interval_ms - elapsed))).await;
                elapsed += step;
            }
        }
        {
            let mut s = state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&cfg.eoa, &state.read());
    }
}

fn push_log(log: &mut Vec<LogEntry>, e: LogEntry) {
    log.push(e);
    if log.len() > LOG_CAP {
        let drop = log.len() - LOG_CAP;
        log.drain(0..drop);
    }
}
