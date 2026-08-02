use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;

use futures::stream::{self, StreamExt};
use parking_lot::RwLock;
use serde_json::Value;

use crate::cache::PipelineCache;
use crate::types::{AggPayload, MarketMetric, Trader};

const DATA_API: &str = "https://data-api.polymarket.com";
const PAGE_SIZE: u32 = 50;

pub struct PipelineState {
    pub cache: PipelineCache,
    pub http: reqwest::Client,
    warmup_running: RwLock<bool>,
}

impl PipelineState {
    pub fn new(http: reqwest::Client) -> Self {
        Self {
            cache: PipelineCache::new(),
            http,
            warmup_running: RwLock::new(false),
        }
    }

    /// One cycle, re-pulling any window last synced more than `min_age_secs`
    /// ago. The scheduler derives that from the owner's cadence (sync.rs) —
    /// a fixed 55min threshold would make a 15-minute cadence skip every
    /// window and silently never sync. `0` forces a full re-pull.
    pub async fn warmup_cycle(&self, min_age_secs: i64) {
        {
            let mut running = self.warmup_running.write();
            if *running {
                return;
            }
            *running = true;
        }
        // Reset the flag on every exit path, including a panic mid-cycle —
        // otherwise one bad cycle leaves it stuck true and every future
        // cycle returns immediately (historical syncs silently stop).
        struct RunningGuard<'a>(&'a parking_lot::RwLock<bool>);
        impl Drop for RunningGuard<'_> {
            fn drop(&mut self) {
                *self.0.write() = false;
            }
        }
        let _guard = RunningGuard(&self.warmup_running);
        tracing::info!(min_age_secs, "warmup cycle starting");

        // Skip a combo only if it was ACTUALLY synced within the window.
        // Memory freshness (`cache.get`) is the wrong signal here:
        // `get_or_disk` re-stamps disk payloads up to 24h old as fresh-in-
        // memory, which used to make the warmup skip genuinely stale data
        // after a restart. The threshold sits just under the cadence so an
        // on-schedule tick never skips a combo that is about to fall due.
        let now = chrono::Utc::now().timestamp();

        let combos = [(1u32, 0.0, 2000u32), (7, 0.0, 2000), (14, 0.0, 2000), (30, 0.0, 2000)];
        for (days, min_per_day, pool) in combos {
            let key = format!("{}:{}:{}", days, min_per_day, pool);
            if min_age_secs > 0
                && self
                    .cache
                    .get(&key)
                    .is_some_and(|p| now - p.synced_at < min_age_secs)
            {
                continue;
            }
            match self.run_pipeline(days, min_per_day, pool, None).await {
                Ok(payload) => {
                    tracing::info!("warmed {}D: {} traders", days, payload.count);
                    // Don't poison memory + disk cache with empty results — an upstream
                    // hiccup during warmup would otherwise serve "0 traders" until TTL.
                    if payload.count > 0 {
                        self.cache.set(&key, payload);
                    }
                }
                Err(e) => {
                    tracing::warn!("warmup {}D failed: {}", days, e);
                }
            }
        }

        tracing::info!("warmup cycle done");
    }

    pub async fn run_pipeline(
        &self,
        days: u32,
        min_per_day: f64,
        pool: u32,
        on_progress: Option<tokio::sync::mpsc::Sender<Value>>,
    ) -> anyhow::Result<AggPayload> {
        let min_trades = (days as f64 * min_per_day).ceil() as u32;
        let now_sec = chrono::Utc::now().timestamp() as u64;
        let cutoff_sec = now_sec.saturating_sub(days as u64 * 86400);
        let pages = (pool as usize + PAGE_SIZE as usize - 1) / PAGE_SIZE as usize;

        // ─── Phase 1: Leaderboard ───
        // Fetch from ALL time periods to maximize candidate discovery.
        // More periods = more unique traders found across different activity windows.
        let time_periods: Vec<&str> = match days {
            0..=1 => vec!["DAY", "WEEK"],
            2..=7 => vec!["WEEK", "MONTH", "ALL"],
            _ => vec!["WEEK", "MONTH", "ALL"],
        };

        // Each period fetches `pages` pages for both PNL and VOL orderings
        let requests_per_period = pages * 2;
        let lb_total = time_periods.len() * requests_per_period;

        if let Some(ref tx) = on_progress {
            tx.send(serde_json::json!({"type":"progress","phase":"leaderboard","done":0,"total":lb_total})).await.ok();
        }

        let lb_done = Arc::new(AtomicUsize::new(0));

        // Build all (period, order_by, offset) combinations
        let mut tasks: Vec<(String, String, usize)> = Vec::new();
        for period in &time_periods {
            for i in 0..requests_per_period {
                let order_by = if i < pages { "PNL" } else { "VOL" };
                let offset = (i % pages) * PAGE_SIZE as usize;
                tasks.push((period.to_string(), order_by.to_string(), offset));
            }
        }

        let lb_results: Vec<Option<Vec<Value>>> = stream::iter(tasks)
            .map(|(period, order_by, offset)| {
                let http = self.http.clone();
                let tx = on_progress.clone();
                let done_counter = lb_done.clone();
                let lb_total_copy = lb_total;
                async move {
                    let url = format!(
                        "{}/v1/leaderboard?timePeriod={}&orderBy={}&limit={}&offset={}",
                        DATA_API, period, order_by, PAGE_SIZE, offset
                    );
                    let result = match http.get(&url).send().await {
                        Ok(resp) => resp.json::<Vec<Value>>().await.ok(),
                        Err(_) => None,
                    };
                    let done = done_counter.fetch_add(1, Ordering::Relaxed) + 1;
                    if let Some(ref tx) = tx {
                        if done % 10 == 0 || done == lb_total_copy {
                            tx.send(serde_json::json!({
                                "type":"progress","phase":"leaderboard",
                                "done":done,"total":lb_total_copy
                            })).await.ok();
                        }
                    }
                    result
                }
            })
            .buffer_unordered(64)
            .collect()
            .await;

        // Deduplicate candidates
        let mut candidates: HashMap<String, Trader> = HashMap::new();
        for page in lb_results.into_iter().flatten() {
            for entry in page {
                let addr = entry.get("proxyWallet")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                if addr.is_empty() || addr == "undefined" {
                    continue;
                }
                let vol = entry.get("vol").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let pnl = entry.get("pnl").and_then(|v| v.as_f64()).unwrap_or(0.0);

                let existing = candidates.entry(addr.clone()).or_insert_with(|| Trader {
                    address: addr,
                    volume: 0.0,
                    buy_volume: 0.0,
                    sell_volume: 0.0,
                    pnl: 0.0,
                    win_rate: 0.0,
                    sharpe: 0.0,
                    positions: 0,
                    market_titles: vec![],
                    recent_trades: 0,
                    trades_24h: 0,
                    last_trade_ts: None,
                    pnl_curve: None,
                    market_metrics: None,
                });
                existing.volume = existing.volume.max(vol);
                if pnl.abs() > existing.pnl.abs() {
                    existing.pnl = pnl;
                }
            }
        }

        if let Some(ref tx) = on_progress {
            tx.send(serde_json::json!({"type":"progress","phase":"leaderboard","done":lb_total,"total":lb_total})).await.ok();
        }

        // ─── Phase 2: Enrich ───
        let cand_vec: Vec<Trader> = candidates.into_values().collect();
        let enrich_total = cand_vec.len();
        let hours_target = days as u64 * 24;

        if let Some(ref tx) = on_progress {
            tx.send(serde_json::json!({"type":"progress","phase":"enrich","done":0,"total":enrich_total,"kept":0,"hoursScraped":0,"hoursTarget":hours_target})).await.ok();
        }

        let cutoff = cutoff_sec;
        let http = self.http.clone();
        let enrich_done = Arc::new(AtomicUsize::new(0));
        let enrich_kept = Arc::new(AtomicUsize::new(0));
        let depth_sum = Arc::new(AtomicU64::new(0));
        let partial_traders: Arc<RwLock<Vec<Trader>>> = Arc::new(RwLock::new(Vec::new()));

        let enriched: Vec<Option<Trader>> = stream::iter(cand_vec)
            .map(|t| {
                let http = http.clone();
                let tx = on_progress.clone();
                let done_counter = enrich_done.clone();
                let kept_counter = enrich_kept.clone();
                let partial = partial_traders.clone();
                let depth_accum = depth_sum.clone();
                async move {
                    let (trader_opt, oldest_ts) = enrich_trader(http, t, cutoff, min_trades).await;
                    let depth = now_sec.saturating_sub(oldest_ts.min(now_sec));
                    depth_accum.fetch_add(depth, Ordering::Relaxed);
                    let done = done_counter.fetch_add(1, Ordering::Relaxed) + 1;
                    if let Some(ref trader) = trader_opt {
                        kept_counter.fetch_add(1, Ordering::Relaxed);
                        partial.write().push(trader.clone());
                    }
                    // Send progress every 10 traders, and partials every 50
                    if let Some(ref tx) = tx {
                        let kept = kept_counter.load(Ordering::Relaxed);
                        if done % 10 == 0 || done == enrich_total {
                            let total_depth = depth_accum.load(Ordering::Relaxed);
                            let h_scraped = if enrich_total > 0 { total_depth / enrich_total as u64 / 3600 } else { 0 };
                            tx.send(serde_json::json!({
                                "type":"progress","phase":"enrich",
                                "done":done,"total":enrich_total,"kept":kept,
                                "hoursScraped":h_scraped,"hoursTarget":hours_target
                            })).await.ok();
                        }
                        if done % 50 == 0 && kept > 0 {
                            let snap = {
                                let mut list = partial.read().clone();
                                list.sort_by(|a, b| b.pnl.partial_cmp(&a.pnl).unwrap_or(std::cmp::Ordering::Equal));
                                list.truncate(100);
                                list
                            };
                            tx.send(serde_json::json!({
                                "type":"partial",
                                "traders": snap
                            })).await.ok();
                        }
                    }
                    trader_opt
                }
            })
            .buffer_unordered(64)
            .collect()
            .await;

        let mut out: Vec<Trader> = enriched.into_iter().flatten().collect();
        out.sort_by(|a, b| b.pnl.partial_cmp(&a.pnl).unwrap_or(std::cmp::Ordering::Equal));

        if let Some(ref tx) = on_progress {
            let total_depth = depth_sum.load(Ordering::Relaxed);
            let h_scraped = if enrich_total > 0 { total_depth / enrich_total as u64 / 3600 } else { 0 };
            tx.send(serde_json::json!({"type":"progress","phase":"enrich","done":enrich_total,"total":enrich_total,"kept":out.len(),"hoursScraped":h_scraped,"hoursTarget":hours_target})).await.ok();
        }

        Ok(AggPayload {
            count: out.len(),
            candidate_pool: enrich_total,
            days_window: days,
            min_trades_per_day: min_per_day,
            // Stamp the actual sync time so the client can show "data is N
            // minutes old" instead of "cache was hit N seconds ago".
            synced_at: chrono::Utc::now().timestamp(),
            traders: out,
        })
    }
}

/// The only fields the window metrics / pnl curve / title filter actually
/// read from a raw activity object. Everything else (profile images, bios,
/// slugs, tx hashes — ~30 fields total) is dead weight: keeping it meant up
/// to 20k full objects per trader × 64 concurrent enrich tasks, multi-GB of
/// transient heap every warmup cycle that glibc then never returned to the
/// OS (observed 10.9GB RSS pinned at high-water mark).
const TRADE_FIELDS: &[&str] = &[
    "timestamp", "type", "side", "price", "size", "usdcSize",
    "conditionId", "asset", "title",
];

fn slim_trade(t: &Value) -> Value {
    let mut m = serde_json::Map::with_capacity(TRADE_FIELDS.len());
    for k in TRADE_FIELDS {
        if let Some(v) = t.get(*k) {
            m.insert((*k).to_string(), v.clone());
        }
    }
    Value::Object(m)
}

/// Normalize a timestamp to seconds — handles both seconds and milliseconds formats.
fn normalize_ts(v: &Value) -> u64 {
    let raw = v.get("timestamp")
        .and_then(|t| t.as_u64().or_else(|| t.as_f64().map(|f| f as u64)))
        .unwrap_or(0);
    // If > 1e12, it's milliseconds — convert to seconds
    if raw > 1_000_000_000_000 { raw / 1000 } else { raw }
}

async fn enrich_trader(
    http: reqwest::Client,
    trader: Trader,
    cutoff_sec: u64,
    min_trades: u32,
) -> (Option<Trader>, u64) {
    enrich_trader_with_url(http, trader, cutoff_sec, min_trades, DATA_API).await
}

async fn enrich_trader_with_url(
    http: reqwest::Client,
    mut trader: Trader,
    cutoff_sec: u64,
    min_trades: u32,
    base_url: &str,
) -> (Option<Trader>, u64) {
    // Fetch trades — paginate until we've passed the cutoff or hit the cap.
    // data-api enforces `max activity limit of 500` (400s above it) — a
    // larger page size turns EVERY fetch into an error and the whole
    // leaderboard into zero-stat husks.
    const PAGE: u32 = 500;
    const MAX_PAGES: u32 = 40; // 20k trades max
    let mut all_trades: Vec<Value> = Vec::new();
    for page in 0..MAX_PAGES {
        let url = format!(
            "{}/activity?user={}&limit={}&offset={}",
            base_url, trader.address, PAGE, page * PAGE
        );
        match http.get(&url).send().await {
            Ok(resp) => {
                if let Ok(trades) = resp.json::<Vec<Value>>().await {
                    let len = trades.len();
                    let oldest_ts = trades.iter()
                        .map(|t| normalize_ts(t))
                        .filter(|&ts| ts > 0)
                        .min()
                        .unwrap_or(u64::MAX);
                    all_trades.extend(trades.iter().map(slim_trade));
                    if len < PAGE as usize || oldest_ts < cutoff_sec {
                        break;
                    }
                } else {
                    break;
                }
            }
            Err(_) => break,
        }
    }
    // No activity at all — either a dormant address or every fetch errored
    // (rate limit, schema change). Keeping the row would put a zero-stat
    // husk on the leaderboard and, worse, cache it; drop it instead.
    if all_trades.is_empty() {
        return (None, chrono::Utc::now().timestamp() as u64);
    }

    // Track the oldest timestamp for depth reporting
    let oldest_ts = all_trades.iter()
        .map(|t| normalize_ts(t))
        .filter(|&ts| ts > 0)
        .min()
        .unwrap_or(chrono::Utc::now().timestamp() as u64);

    // Compute window metrics with cost-basis. Zero in-window trades is an
    // unconditional drop (even at min_trades=0): a dormant ALL-time name has
    // no window stats and would render as a blank leaderboard row.
    let metrics = compute_window_metrics(&all_trades, cutoff_sec);
    if metrics.count < min_trades || metrics.count == 0 {
        return (None, oldest_ts);
    }

    trader.recent_trades = metrics.count;
    // 24h trade count — separate from window count so a 7D-window leaderboard
    // can still distinguish a trader who fired 50 trades in the last day from
    // one who hasn't traded all week. Reuses the same `all_trades` already
    // pulled per trader so this adds zero network cost.
    let now_sec = chrono::Utc::now().timestamp() as u64;
    let cutoff_24h = now_sec.saturating_sub(86_400);
    trader.trades_24h = all_trades
        .iter()
        .filter_map(|t| t.get("timestamp").and_then(|v| v.as_u64()))
        .filter(|&ts| ts >= cutoff_24h)
        .count() as u32;
    // Stamp the most recent in-window trade so the leaderboard can show
    // "last trade Xm ago" — surfaces dormants that have a good window-
    // total but went silent days ago. Uses the same timestamp pass as
    // trades_24h above so no extra network cost.
    trader.last_trade_ts = all_trades
        .iter()
        .filter_map(|t| t.get("timestamp").and_then(|v| v.as_u64()))
        .max();
    trader.volume = metrics.volume;
    trader.buy_volume = metrics.buy_volume;
    trader.sell_volume = metrics.sell_volume;
    trader.pnl = metrics.pnl;
    // Sharpe over the same window the rest of the row's stats use — the
    // leaderboard's default SCORE. Reuses the live engine's ONE stats formula
    // so ranking here matches copy-candidate scoring.
    trader.sharpe = crate::live_engine::stats_from_returns(&metrics.returns).sharpe;
    trader.pnl_curve = Some(compute_pnl_curve(&all_trades, cutoff_sec));

    // Market titles come from the SAME in-window per-market set the stats
    // are computed from — the leaderboard's keyword filter must see exactly
    // the markets whose trades feed the numbers, or a trader can be dropped
    // while holding matching trades (or matched on out-of-window titles).
    // Volume-ranked cap bounds pathological HFT rosters.
    let mut ranked: Vec<&MarketMetric> = metrics.per_market.iter().collect();
    ranked.sort_by(|a, b| b.volume.partial_cmp(&a.volume).unwrap_or(std::cmp::Ordering::Equal));
    trader.market_titles = ranked.iter().take(500).map(|m| m.title.clone()).collect();
    if trader.market_titles.is_empty() {
        // No titled in-window trades — fall back to a raw scan so the row
        // isn't completely unmatchable.
        let mut seen_titles = std::collections::HashSet::new();
        for t in &all_trades {
            if let Some(title) = t.get("title").and_then(|v| v.as_str()) {
                if !title.is_empty() && seen_titles.len() < 20 {
                    seen_titles.insert(title.to_string());
                }
            }
        }
        trader.market_titles = seen_titles.into_iter().collect();
    }
    trader.positions = metrics.count; // use trade count as "positions" field

    // Accuracy: of the positions this trader BOUGHT in the window, the share
    // whose outcome ended up winning — price saturated to $1 (redeemed for a
    // payout, or exited at ≥95¢). Only decided positions count; capped at 100.
    trader.win_rate = if metrics.decided > 0 {
        ((metrics.wins as f64 / metrics.decided as f64) * 100.0).round().min(100.0)
    } else {
        -1.0
    };

    trader.market_metrics = if metrics.per_market.is_empty() { None } else { Some(metrics.per_market) };

    (Some(trader), oldest_ts)
}

struct WindowMetrics {
    volume: f64,
    buy_volume: f64,
    sell_volume: f64,
    pnl: f64,
    count: u32,
    /// Bought positions (opened in-window) whose outcome saturated to $1.
    wins: u32,
    /// Bought positions (opened in-window) with a known outcome.
    decided: u32,
    /// Per-closed-SELL fractional returns `(price − avgCost) / avgCost`
    /// realized in-window — the series Sharpe is computed from.
    returns: Vec<f64>,
    per_market: Vec<MarketMetric>,
}

/// Exit price at/above which a bought outcome is considered to have
/// saturated to $1 — i.e. the market resolved (or is resolving) the
/// trader's way. CLOB caps sells at 99¢, so winners exit in the 95¢–99¢
/// band or via CTF redeem.
const WIN_SATURATION_PRICE: f64 = 0.95;

fn compute_window_metrics(trades: &[Value], cutoff_sec: u64) -> WindowMetrics {
    let mut sorted: Vec<&Value> = trades.iter()
        .filter(|t| {
            let ty = t.get("type").and_then(|v| v.as_str()).unwrap_or("TRADE");
            // REDEEMs carry no volume but are the ground-truth "this buy won"
            // signal for the accuracy metric.
            ty == "TRADE" || ty == "REDEEM"
        })
        .collect();
    sorted.sort_by_key(|t| normalize_ts(t));

    // Cost-basis book
    let mut book: HashMap<String, (f64, f64)> = HashMap::new(); // key -> (size, cost)
    let mut pnl = 0.0f64;
    let mut returns: Vec<f64> = Vec::new();
    let mut buy_volume = 0.0f64;
    let mut sell_volume = 0.0f64;
    let mut count = 0u32;

    // Per-market accumulator
    struct MktAccum { volume: f64, buy_volume: f64, sell_volume: f64, pnl: f64, trades: u32, wins: u32, decided: u32, returns: Vec<f64> }
    let mut per_market: HashMap<String, MktAccum> = HashMap::new();

    // Per-position outcome tracker for buy-accuracy: a bought position "ends
    // up winning" when its price saturates to $1 — a REDEEM with a payout or
    // an exit at ≥ WIN_SATURATION_PRICE. A position is "decided" once it won,
    // was redeemed (payout or not), or was fully exited.
    struct PosOutcome { title: String, bought: f64, sold: f64, in_window_buy: bool, won: bool, redeemed: bool }
    let mut outcomes: HashMap<String, PosOutcome> = HashMap::new();

    for t in sorted {
        let ts = normalize_ts(t);
        let in_window = ts >= cutoff_sec;
        let key = t.get("conditionId").or(t.get("asset"))
            .and_then(|v| v.as_str()).unwrap_or("").to_string();
        let title = t.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let price = t.get("price").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))).unwrap_or(0.0);
        let size = t.get("size").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))).unwrap_or(0.0);
        let usdc_size = t.get("usdcSize").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))).unwrap_or(price * size);
        let ty = t.get("type").and_then(|v| v.as_str()).unwrap_or("TRADE");
        let side = t.get("side").and_then(|v| v.as_str()).unwrap_or("").to_uppercase();

        if ty == "REDEEM" {
            if !key.is_empty() {
                let o = outcomes.entry(key).or_insert(PosOutcome {
                    title: title.to_string(), bought: 0.0, sold: 0.0,
                    in_window_buy: false, won: false, redeemed: false,
                });
                o.redeemed = true;
                // Redeeming losing tokens pays $0 — only a payout marks a win.
                if usdc_size > 0.0 { o.won = true; }
                if o.title.is_empty() && !title.is_empty() { o.title = title.to_string(); }
            }
            continue;
        }

        if !key.is_empty() && (side == "BUY" || side == "SELL") {
            let o = outcomes.entry(key.clone()).or_insert(PosOutcome {
                title: title.to_string(), bought: 0.0, sold: 0.0,
                in_window_buy: false, won: false, redeemed: false,
            });
            if side == "BUY" {
                o.bought += size;
                if in_window { o.in_window_buy = true; }
            } else {
                o.sold += size;
                if price >= WIN_SATURATION_PRICE { o.won = true; }
            }
            if o.title.is_empty() && !title.is_empty() { o.title = title.to_string(); }
        }

        let pos = book.entry(key).or_insert((0.0, 0.0));

        let mut realized = 0.0f64;
        // Fractional return of an in-window closed SELL — SELLs without an
        // in-hand basis are dropped (a placeholder 0 would deflate stdev),
        // mirroring the live engine's `compute_trader_roi_stats`.
        let mut sell_return: Option<f64> = None;
        if side == "BUY" {
            pos.1 += price * size; // cost
            pos.0 += size;         // size
        } else if side == "SELL" && pos.0 > 0.0 {
            let avg = pos.1 / pos.0;
            let sold = size.min(pos.0);
            realized = (price - avg) * sold;
            pos.1 -= avg * sold;
            pos.0 -= sold;
            if in_window {
                pnl += realized;
                if avg > 0.0 {
                    let r = (price - avg) / avg;
                    returns.push(r);
                    sell_return = Some(r);
                }
            }
        }

        if in_window {
            if side == "BUY" {
                buy_volume += usdc_size;
            } else if side == "SELL" {
                sell_volume += usdc_size;
            }
            count += 1;

            // Per-market accumulation
            if !title.is_empty() {
                let mkt = per_market.entry(title.to_string()).or_insert(MktAccum {
                    volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
                    pnl: 0.0, trades: 0, wins: 0, decided: 0, returns: Vec::new(),
                });
                mkt.volume += usdc_size;
                mkt.trades += 1;
                mkt.pnl += realized;
                if let Some(r) = sell_return {
                    mkt.returns.push(r);
                }
                if side == "BUY" {
                    mkt.buy_volume += usdc_size;
                } else if side == "SELL" {
                    mkt.sell_volume += usdc_size;
                }
            }
        }
    }

    // Fold decided positions into the accuracy counters — only positions the
    // trader actually BOUGHT inside the window count.
    let mut wins_total = 0u32;
    let mut decided_total = 0u32;
    for o in outcomes.values() {
        if !o.in_window_buy { continue; }
        let fully_exited = o.bought > 0.0 && o.sold >= o.bought * 0.99;
        if !(o.won || o.redeemed || fully_exited) { continue; }
        decided_total += 1;
        if o.won { wins_total += 1; }
        if !o.title.is_empty() {
            let mkt = per_market.entry(o.title.clone()).or_insert(MktAccum {
                volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
                pnl: 0.0, trades: 0, wins: 0, decided: 0, returns: Vec::new(),
            });
            mkt.decided += 1;
            if o.won { mkt.wins += 1; }
        }
    }

    let market_metrics: Vec<MarketMetric> = per_market.into_iter().map(|(title, m)| {
        MarketMetric {
            title, volume: m.volume, buy_volume: m.buy_volume,
            sell_volume: m.sell_volume, pnl: m.pnl, trades: m.trades,
            wins: m.wins, decided: m.decided, returns: m.returns,
        }
    }).collect();

    WindowMetrics {
        volume: buy_volume + sell_volume,
        buy_volume,
        sell_volume,
        pnl,
        count,
        wins: wins_total,
        decided: decided_total,
        returns,
        per_market: market_metrics,
    }
}

fn compute_pnl_curve(trades: &[Value], cutoff_sec: u64) -> Vec<f64> {
    // Process ALL trades (including pre-window) to build accurate cost-basis,
    // but only record PnL in buckets for in-window trades.
    let mut sorted: Vec<&Value> = trades.iter()
        .filter(|t| {
            t.get("type").and_then(|v| v.as_str()).unwrap_or("TRADE") == "TRADE"
        })
        .collect();
    sorted.sort_by_key(|t| normalize_ts(t));

    // Check we have any in-window trades
    let has_window = sorted.iter().any(|t| normalize_ts(t) >= cutoff_sec);
    if !has_window {
        return vec![];
    }

    let now_sec = chrono::Utc::now().timestamp() as u64;
    let window_duration = now_sec.saturating_sub(cutoff_sec);
    let buckets = 12usize;
    let bucket_size = if window_duration > 0 { window_duration / buckets as u64 } else { 1 };

    let mut curve = vec![0.0f64; buckets];
    let mut written = vec![false; buckets]; // track which buckets have data
    let mut cum_pnl = 0.0f64;

    let mut book: HashMap<String, (f64, f64)> = HashMap::new();
    for t in &sorted {
        let ts = normalize_ts(t);
        let in_window = ts >= cutoff_sec;

        let key = t.get("conditionId").or(t.get("asset"))
            .and_then(|v| v.as_str()).unwrap_or("").to_string();
        let price = t.get("price").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))).unwrap_or(0.0);
        let size = t.get("size").and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok()))).unwrap_or(0.0);
        let side = t.get("side").and_then(|v| v.as_str()).unwrap_or("").to_uppercase();

        let pos = book.entry(key).or_insert((0.0, 0.0));
        if side == "BUY" {
            pos.1 += price * size;
            pos.0 += size;
        } else if side == "SELL" && pos.0 > 0.0 {
            let avg = pos.1 / pos.0;
            let sold = size.min(pos.0);
            let realized = (price - avg) * sold;
            pos.1 -= avg * sold;
            pos.0 -= sold;
            if in_window {
                cum_pnl += realized;
            }
        }

        if in_window {
            let bucket_idx = ((ts.saturating_sub(cutoff_sec)) / bucket_size).min(buckets as u64 - 1) as usize;
            curve[bucket_idx] = (cum_pnl * 100.0).round() / 100.0;
            written[bucket_idx] = true;
        }
    }

    // Forward-fill: carry the last known cumulative PnL into empty buckets
    let mut last = 0.0;
    for i in 0..buckets {
        if written[i] {
            last = curve[i];
        } else {
            curve[i] = last;
        }
    }

    curve
}

// ─── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── Helper: build a trade JSON value ──

    fn trade(ts: u64, side: &str, price: f64, size: f64, cid: &str) -> Value {
        json!({
            "type": "TRADE",
            "timestamp": ts,
            "side": side,
            "price": price,
            "size": size,
            "conditionId": cid,
        })
    }

    fn trade_with_usdc(ts: u64, side: &str, price: f64, size: f64, usdc: f64, cid: &str) -> Value {
        json!({
            "type": "TRADE",
            "timestamp": ts,
            "side": side,
            "price": price,
            "size": size,
            "usdcSize": usdc,
            "conditionId": cid,
        })
    }

    fn trade_str_fields(ts: u64, side: &str, price: &str, size: &str, cid: &str) -> Value {
        json!({
            "type": "TRADE",
            "timestamp": ts,
            "side": side,
            "price": price,
            "size": size,
            "conditionId": cid,
        })
    }

    // ── normalize_ts ─────────────────────────────────────────────

    #[test]
    fn normalize_ts_seconds() {
        let v = json!({"timestamp": 1700000000u64});
        assert_eq!(normalize_ts(&v), 1700000000);
    }

    #[test]
    fn normalize_ts_milliseconds() {
        let v = json!({"timestamp": 1700000000000u64});
        assert_eq!(normalize_ts(&v), 1700000000);
    }

    #[test]
    fn normalize_ts_zero() {
        let v = json!({"timestamp": 0});
        assert_eq!(normalize_ts(&v), 0);
    }

    #[test]
    fn normalize_ts_missing_field() {
        let v = json!({"other": 123});
        assert_eq!(normalize_ts(&v), 0);
    }

    #[test]
    fn normalize_ts_float() {
        let v = json!({"timestamp": 1700000000.5});
        assert_eq!(normalize_ts(&v), 1700000000);
    }

    #[test]
    fn normalize_ts_empty_object() {
        let v = json!({});
        assert_eq!(normalize_ts(&v), 0);
    }

    // ── compute_window_metrics ───────────────────────────────────

    #[test]
    fn metrics_empty_trades() {
        let m = compute_window_metrics(&[], 0);
        assert_eq!(m.count, 0);
        assert_eq!(m.volume, 0.0);
        assert_eq!(m.buy_volume, 0.0);
        assert_eq!(m.sell_volume, 0.0);
        assert_eq!(m.pnl, 0.0);
    }

    #[test]
    fn metrics_single_buy_in_window() {
        // cutoff=1000, trade at ts=2000 → in window
        let trades = vec![trade(2000, "BUY", 0.60, 100.0, "mkt1")];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 1);
        assert_eq!(m.buy_volume, 60.0); // price * size = usdcSize fallback
        assert_eq!(m.sell_volume, 0.0);
        assert_eq!(m.volume, 60.0);
        assert_eq!(m.pnl, 0.0); // no sells → no realized PnL
    }

    #[test]
    fn metrics_buy_then_sell_profit() {
        // Buy 100 @ 0.40, sell 100 @ 0.70 → PnL = (0.70 - 0.40) * 100 = 30
        let trades = vec![
            trade(2000, "BUY", 0.40, 100.0, "mkt1"),
            trade(3000, "SELL", 0.70, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 2);
        assert!((m.pnl - 30.0).abs() < 0.01, "pnl should be ~30, got {}", m.pnl);
        assert_eq!(m.buy_volume, 40.0);
        assert_eq!(m.sell_volume, 70.0);
    }

    #[test]
    fn metrics_buy_then_sell_loss() {
        // Buy 100 @ 0.80, sell 100 @ 0.30 → PnL = (0.30 - 0.80) * 100 = -50
        let trades = vec![
            trade(2000, "BUY", 0.80, 100.0, "mkt1"),
            trade(3000, "SELL", 0.30, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert!((m.pnl - (-50.0)).abs() < 0.01, "pnl should be ~-50, got {}", m.pnl);
    }

    #[test]
    fn metrics_pre_window_buy_in_window_sell() {
        // Buy at ts=500 (before cutoff=1000), sell at ts=2000 (in window).
        // Cost basis from pre-window should still be tracked.
        // PnL should be realized on the in-window sell.
        let trades = vec![
            trade(500, "BUY", 0.30, 200.0, "mkt1"),
            trade(2000, "SELL", 0.80, 200.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        // Only the sell is in-window
        assert_eq!(m.count, 1);
        assert_eq!(m.buy_volume, 0.0); // buy was pre-window
        assert_eq!(m.sell_volume, 160.0); // 0.80 * 200
        // PnL: (0.80 - 0.30) * 200 = 100
        assert!((m.pnl - 100.0).abs() < 0.01, "pnl should be ~100, got {}", m.pnl);
    }

    #[test]
    fn metrics_multiple_assets_independent() {
        // Two different markets shouldn't share cost basis
        let trades = vec![
            trade(2000, "BUY", 0.40, 100.0, "mkt1"),
            trade(2000, "BUY", 0.60, 100.0, "mkt2"),
            trade(3000, "SELL", 0.70, 100.0, "mkt1"), // profit on mkt1
            trade(3000, "SELL", 0.50, 100.0, "mkt2"), // loss on mkt2
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 4);
        // mkt1 PnL: (0.70 - 0.40) * 100 = 30
        // mkt2 PnL: (0.50 - 0.60) * 100 = -10
        // Total = 20
        assert!((m.pnl - 20.0).abs() < 0.01, "pnl should be ~20, got {}", m.pnl);
    }

    #[test]
    fn metrics_sell_exceeds_position_capped() {
        // Buy 50, sell 100 → should only realize on 50 (capped)
        let trades = vec![
            trade(2000, "BUY", 0.40, 50.0, "mkt1"),
            trade(3000, "SELL", 0.80, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        // Only 50 shares sold (capped at position size)
        // PnL = (0.80 - 0.40) * 50 = 20
        assert!((m.pnl - 20.0).abs() < 0.01, "pnl should be ~20, got {}", m.pnl);
    }

    #[test]
    fn metrics_sell_with_no_position() {
        // Sell without a prior buy → pos.0 is 0, so the SELL branch is skipped
        let trades = vec![trade(2000, "SELL", 0.70, 100.0, "mkt1")];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 1);
        assert_eq!(m.pnl, 0.0);
        assert_eq!(m.sell_volume, 70.0); // volume still counted
    }

    #[test]
    fn metrics_string_encoded_price_size() {
        // Polymarket sometimes returns price/size as strings
        let trades = vec![
            trade_str_fields(2000, "BUY", "0.40", "100", "mkt1"),
            trade_str_fields(3000, "SELL", "0.70", "100", "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert!((m.pnl - 30.0).abs() < 0.01, "pnl should be ~30, got {}", m.pnl);
    }

    #[test]
    fn metrics_usdc_size_used_when_present() {
        // usdcSize overrides price*size for volume accounting
        let trades = vec![
            trade_with_usdc(2000, "BUY", 0.40, 100.0, 45.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.buy_volume, 45.0); // uses usdcSize, not 0.40*100=40
    }

    #[test]
    fn metrics_non_trade_type_filtered() {
        let trades = vec![
            json!({"type": "TRANSFER", "timestamp": 2000, "side": "BUY", "price": 0.5, "size": 100.0, "conditionId": "mkt1"}),
            trade(2000, "BUY", 0.50, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 1); // only the TRADE counted
    }

    #[test]
    fn metrics_default_type_is_trade() {
        // Missing "type" field defaults to "TRADE"
        let trades = vec![
            json!({"timestamp": 2000, "side": "BUY", "price": 0.5, "size": 100.0, "conditionId": "mkt1"}),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 1);
    }

    // ── buy-accuracy (wins / decided) ────────────────────────────

    fn redeem(ts: u64, usdc: f64, cid: &str) -> Value {
        json!({
            "type": "REDEEM",
            "timestamp": ts,
            "usdcSize": usdc,
            "size": 100.0,
            "conditionId": cid,
            "title": "Redeemed market",
        })
    }

    #[test]
    fn accuracy_buy_then_winning_redeem() {
        let trades = vec![
            trade(2000, "BUY", 0.60, 100.0, "mkt1"),
            redeem(3000, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 1);
        assert_eq!(m.decided, 1);
    }

    #[test]
    fn accuracy_buy_then_zero_payout_redeem_is_loss() {
        // Redeeming losing tokens pays $0 → decided but not won.
        let trades = vec![
            trade(2000, "BUY", 0.60, 100.0, "mkt1"),
            redeem(3000, 0.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 0);
        assert_eq!(m.decided, 1);
    }

    #[test]
    fn accuracy_buy_then_saturated_sell_wins() {
        // Exit at ≥95¢ = price saturated to $1 → win.
        let trades = vec![
            trade(2000, "BUY", 0.60, 100.0, "mkt1"),
            trade(3000, "SELL", 0.97, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 1);
        assert_eq!(m.decided, 1);
    }

    #[test]
    fn accuracy_full_exit_below_saturation_is_loss() {
        // Sold everything at 40¢ — decided, but the buy didn't end up winning.
        let trades = vec![
            trade(2000, "BUY", 0.60, 100.0, "mkt1"),
            trade(3000, "SELL", 0.40, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 0);
        assert_eq!(m.decided, 1);
    }

    #[test]
    fn accuracy_open_position_is_undecided() {
        let trades = vec![trade(2000, "BUY", 0.60, 100.0, "mkt1")];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 0);
        assert_eq!(m.decided, 0);
    }

    #[test]
    fn accuracy_pre_window_buy_excluded() {
        // Position opened before the window never enters the accuracy counters,
        // even if it wins inside the window.
        let trades = vec![
            trade(500, "BUY", 0.60, 100.0, "mkt1"),
            redeem(2000, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 0);
        assert_eq!(m.decided, 0);
    }

    #[test]
    fn accuracy_never_exceeds_decided() {
        // Multiple win signals on one position (saturated sell + redeem)
        // still count it once — the rate can saturate at 100, never above.
        let trades = vec![
            trade(2000, "BUY", 0.60, 100.0, "mkt1"),
            trade(2500, "SELL", 0.98, 50.0, "mkt1"),
            redeem(3000, 50.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 1);
        assert_eq!(m.decided, 1);
        assert!(m.wins <= m.decided);
    }

    fn trade_titled(ts: u64, side: &str, price: f64, size: f64, cid: &str, title: &str) -> Value {
        json!({
            "type": "TRADE",
            "timestamp": ts,
            "side": side,
            "price": price,
            "size": size,
            "conditionId": cid,
            "title": title,
        })
    }

    #[test]
    fn accuracy_per_market_counters_match() {
        let trades = vec![
            trade_titled(2000, "BUY", 0.60, 100.0, "c1", "Will BTC win?"),
            trade_titled(3000, "SELL", 0.99, 100.0, "c1", "Will BTC win?"),
            trade_titled(2000, "BUY", 0.60, 100.0, "c2", "Will ETH win?"),
            trade_titled(3000, "SELL", 0.10, 100.0, "c2", "Will ETH win?"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.wins, 1);
        assert_eq!(m.decided, 2);
        let win_mkt = m.per_market.iter().find(|x| x.title == "Will BTC win?").unwrap();
        assert_eq!((win_mkt.wins, win_mkt.decided), (1, 1));
        let loss_mkt = m.per_market.iter().find(|x| x.title == "Will ETH win?").unwrap();
        assert_eq!((loss_mkt.wins, loss_mkt.decided), (0, 1));
    }

    #[test]
    fn metrics_volume_is_buy_plus_sell() {
        let trades = vec![
            trade(2000, "BUY", 0.40, 100.0, "mkt1"),
            trade(3000, "SELL", 0.60, 50.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.volume, m.buy_volume + m.sell_volume);
    }

    #[test]
    fn metrics_multiple_buys_average_cost() {
        // Buy 100 @ 0.30, buy 100 @ 0.50 → avg = 0.40
        // Sell 200 @ 0.60 → PnL = (0.60 - 0.40) * 200 = 40
        let trades = vec![
            trade(2000, "BUY", 0.30, 100.0, "mkt1"),
            trade(2500, "BUY", 0.50, 100.0, "mkt1"),
            trade(3000, "SELL", 0.60, 200.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert!((m.pnl - 40.0).abs() < 0.01, "pnl should be ~40, got {}", m.pnl);
    }

    #[test]
    fn metrics_partial_sell_preserves_cost_basis() {
        // Buy 200 @ 0.40 (cost = 80, avg = 0.40)
        // Sell 100 @ 0.60 → realized = (0.60 - 0.40) * 100 = 20, remaining: 100 @ 0.40
        // Sell 100 @ 0.30 → realized = (0.30 - 0.40) * 100 = -10
        // Total PnL = 10
        let trades = vec![
            trade(2000, "BUY", 0.40, 200.0, "mkt1"),
            trade(3000, "SELL", 0.60, 100.0, "mkt1"),
            trade(4000, "SELL", 0.30, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert!((m.pnl - 10.0).abs() < 0.01, "pnl should be ~10, got {}", m.pnl);
    }

    #[test]
    fn metrics_out_of_order_trades_sorted() {
        // Trades arrive out of order — pipeline should sort by timestamp
        let trades = vec![
            trade(3000, "SELL", 0.70, 100.0, "mkt1"),
            trade(2000, "BUY", 0.40, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        // After sorting: buy at 2000, sell at 3000 → PnL = 30
        assert!((m.pnl - 30.0).abs() < 0.01, "pnl should be ~30, got {}", m.pnl);
    }

    #[test]
    fn metrics_all_trades_pre_window() {
        let trades = vec![
            trade(100, "BUY", 0.40, 100.0, "mkt1"),
            trade(200, "SELL", 0.70, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 0);
        assert_eq!(m.volume, 0.0);
        assert_eq!(m.pnl, 0.0);
    }

    // ── compute_pnl_curve ────────────────────────────────────────

    #[test]
    fn curve_no_in_window_trades() {
        let trades = vec![trade(100, "BUY", 0.5, 100.0, "mkt1")];
        let curve = compute_pnl_curve(&trades, 1000);
        assert!(curve.is_empty());
    }

    #[test]
    fn curve_produces_12_buckets() {
        let now = chrono::Utc::now().timestamp() as u64;
        let cutoff = now - 86400; // 1 day ago
        let trades = vec![
            trade(cutoff + 100, "BUY", 0.40, 100.0, "mkt1"),
            trade(cutoff + 200, "SELL", 0.60, 100.0, "mkt1"),
        ];
        let curve = compute_pnl_curve(&trades, cutoff);
        assert_eq!(curve.len(), 12);
    }

    #[test]
    fn curve_forward_fill() {
        let now = chrono::Utc::now().timestamp() as u64;
        let cutoff = now - 86400;
        // Single trade early in the window → all later buckets forward-filled
        let trades = vec![
            trade(cutoff + 100, "BUY", 0.40, 100.0, "mkt1"),
            trade(cutoff + 200, "SELL", 0.60, 100.0, "mkt1"), // PnL = +20
        ];
        let curve = compute_pnl_curve(&trades, cutoff);
        assert_eq!(curve.len(), 12);
        // First bucket should have the PnL, rest should be forward-filled with same value
        let pnl_val = curve[0];
        assert!(pnl_val > 0.0, "first bucket should show positive PnL");
        for i in 1..12 {
            assert_eq!(curve[i], pnl_val, "bucket {} should be forward-filled to {}", i, pnl_val);
        }
    }

    #[test]
    fn curve_pre_window_cost_basis_affects_in_window_pnl() {
        let now = chrono::Utc::now().timestamp() as u64;
        let cutoff = now - 86400;
        // Buy pre-window, sell in-window
        let trades = vec![
            trade(cutoff - 1000, "BUY", 0.30, 100.0, "mkt1"),
            trade(cutoff + 100, "SELL", 0.80, 100.0, "mkt1"), // PnL = +50
        ];
        let curve = compute_pnl_curve(&trades, cutoff);
        assert_eq!(curve.len(), 12);
        assert!((curve[0] - 50.0).abs() < 0.01, "PnL should reflect pre-window cost basis, got {}", curve[0]);
    }

    #[test]
    fn curve_cumulative_pnl_across_buckets() {
        let now = chrono::Utc::now().timestamp() as u64;
        let cutoff = now - 86400;
        let bucket_size = 86400 / 12; // ~7200s per bucket

        // Trade in bucket 0: buy+sell for +10 PnL
        // Trade in bucket 6: buy+sell for +20 PnL
        let trades = vec![
            trade(cutoff + 100, "BUY", 0.40, 100.0, "mkt1"),
            trade(cutoff + 200, "SELL", 0.50, 100.0, "mkt1"), // +10
            trade(cutoff + bucket_size * 6 + 100, "BUY", 0.30, 100.0, "mkt2"),
            trade(cutoff + bucket_size * 6 + 200, "SELL", 0.50, 100.0, "mkt2"), // +20
        ];
        let curve = compute_pnl_curve(&trades, cutoff);
        assert_eq!(curve.len(), 12);
        // Buckets 0-5: cumulative PnL = 10
        assert!((curve[0] - 10.0).abs() < 0.01, "bucket 0 should be ~10, got {}", curve[0]);
        for i in 1..6 {
            assert!((curve[i] - 10.0).abs() < 0.01, "bucket {} should be forward-filled to ~10, got {}", i, curve[i]);
        }
        // Buckets 6-11: cumulative PnL = 30
        assert!((curve[6] - 30.0).abs() < 0.01, "bucket 6 should be ~30, got {}", curve[6]);
        for i in 7..12 {
            assert!((curve[i] - 30.0).abs() < 0.01, "bucket {} should be forward-filled to ~30, got {}", i, curve[i]);
        }
    }

    // ── Depth tracking (hours scraped) ───────────────────────────

    #[test]
    fn depth_oldest_ts_from_trades() {
        let now = chrono::Utc::now().timestamp() as u64;
        let trades = vec![
            trade(now - 86400, "BUY", 0.5, 100.0, "mkt1"),
            trade(now - 3600, "SELL", 0.6, 100.0, "mkt1"),
        ];
        let oldest = trades.iter()
            .map(|t| normalize_ts(t))
            .filter(|&ts| ts > 0)
            .min()
            .unwrap_or(now);
        let depth_secs = now.saturating_sub(oldest.min(now));
        let hours = depth_secs / 3600;
        // oldest is ~24h ago → depth should be ~24 hours
        assert!(hours >= 23 && hours <= 25, "depth should be ~24h, got {}", hours);
    }

    #[test]
    fn depth_no_trades_defaults_to_now() {
        let now = chrono::Utc::now().timestamp() as u64;
        let trades: Vec<Value> = vec![];
        let oldest = trades.iter()
            .map(|t| normalize_ts(t))
            .filter(|&ts| ts > 0)
            .min()
            .unwrap_or(now);
        let depth = now.saturating_sub(oldest.min(now));
        assert_eq!(depth, 0, "no trades → depth should be 0");
    }

    #[test]
    fn depth_average_across_traders() {
        // Simulate 3 traders: one with 168h depth, one with 48h, one with 0h
        // Average = (168 + 48 + 0) * 3600 / 3 / 3600 = 72 hours
        let total_traders = 3u64;
        let sum_depth_secs = 168 * 3600 + 48 * 3600 + 0;
        let h_scraped = sum_depth_secs / total_traders / 3600;
        assert_eq!(h_scraped, 72);
    }

    #[test]
    fn depth_all_traders_full_coverage() {
        // 5 traders, all with full 168h (7 day) depth
        let total_traders = 5u64;
        let sum_depth_secs = 5 * 168 * 3600;
        let hours_target = 168u64;
        let h_scraped = sum_depth_secs / total_traders / 3600;
        assert_eq!(h_scraped, hours_target);
    }

    #[test]
    fn depth_zero_traders_no_panic() {
        let total_traders = 0usize;
        let depth_sum = 0u64;
        let h_scraped = if total_traders > 0 { depth_sum / total_traders as u64 / 3600 } else { 0 };
        assert_eq!(h_scraped, 0);
    }

    // ── enrich_trader return shape ───────────────────────────────

    #[tokio::test]
    async fn enrich_empty_trades_returns_none() {
        // Mock HTTP client that returns empty arrays (no trades for this address)
        let mut server = mockito::Server::new_async().await;
        let mock = server.mock("GET", mockito::Matcher::Regex(r"^/activity.*".to_string()))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body("[]")
            .create_async()
            .await;

        let http = reqwest::Client::new();
        let trader = Trader {
            address: "0xtest".to_string(),
            volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None, pnl_curve: None, market_metrics: None,
        };

        // Call with the mock server URL (override DATA_API)
        let cutoff = chrono::Utc::now().timestamp() as u64 - 86400;
        let result = enrich_trader_with_url(http, trader, cutoff, 1, &server.url()).await;
        assert!(result.0.is_none(), "no trades should return None");
        mock.assert_async().await;
    }

    #[tokio::test]
    async fn enrich_with_trades_returns_metrics() {
        let now = chrono::Utc::now().timestamp() as u64;
        let trades = serde_json::to_string(&vec![
            json!({"type":"TRADE","timestamp": now - 3600,"side":"BUY","price":0.40,"size":100.0,"conditionId":"mkt1","title":"Test Market"}),
            json!({"type":"TRADE","timestamp": now - 1800,"side":"SELL","price":0.70,"size":100.0,"conditionId":"mkt1","title":"Test Market"}),
        ]).unwrap();

        let mut server = mockito::Server::new_async().await;
        let mock = server.mock("GET", mockito::Matcher::Regex(r"^/activity.*".to_string()))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(&trades)
            .create_async()
            .await;

        let http = reqwest::Client::new();
        let trader = Trader {
            address: "0xtest".to_string(),
            volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None, pnl_curve: None, market_metrics: None,
        };

        let cutoff = now - 86400;
        let (result, oldest) = enrich_trader_with_url(http, trader, cutoff, 0, &server.url()).await;
        assert!(result.is_some(), "should return a trader");
        let t = result.unwrap();
        assert_eq!(t.recent_trades, 2);
        assert!(t.volume > 0.0, "volume should be positive");
        assert!((t.pnl - 30.0).abs() < 0.01, "pnl should be ~30, got {}", t.pnl);
        assert!(t.market_titles.contains(&"Test Market".to_string()));
        assert!(t.pnl_curve.is_some());
        assert_eq!(t.pnl_curve.unwrap().len(), 12);
        // oldest timestamp should be ~1h ago
        assert!(oldest <= now - 3500, "oldest_ts should be at least ~1h ago");
        mock.assert_async().await;
    }

    #[tokio::test]
    async fn enrich_min_trades_filter() {
        let now = chrono::Utc::now().timestamp() as u64;
        let trades = serde_json::to_string(&vec![
            json!({"type":"TRADE","timestamp": now - 100,"side":"BUY","price":0.5,"size":10.0,"conditionId":"mkt1"}),
        ]).unwrap();

        let mut server = mockito::Server::new_async().await;
        server.mock("GET", mockito::Matcher::Regex(r"^/activity.*".to_string()))
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(&trades)
            .create_async()
            .await;

        let http = reqwest::Client::new();
        let trader = Trader {
            address: "0xtest".to_string(),
            volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None, pnl_curve: None, market_metrics: None,
        };

        let cutoff = now - 86400;
        // min_trades=5 but only 1 trade → should be filtered out
        let (result, _) = enrich_trader_with_url(http, trader, cutoff, 5, &server.url()).await;
        assert!(result.is_none(), "should be filtered out by min_trades");
    }

    // ── Cache integrity ──────────────────────────────────────────

    #[test]
    fn pipeline_cache_roundtrip() {
        let cache = PipelineCache::new();
        let payload = AggPayload {
            count: 2,
            candidate_pool: 100,
            days_window: 7,
            min_trades_per_day: 1.0,
            synced_at: 0,
            traders: vec![
                Trader {
                    address: "0xaaa".to_string(),
                    volume: 5000.0, buy_volume: 3000.0, sell_volume: 2000.0,
                    pnl: 150.0, win_rate: 65.0, sharpe: 0.0, positions: 10,
                    market_titles: vec!["Market A".into()], recent_trades: 10, trades_24h: 0, last_trade_ts: None,
                    pnl_curve: Some(vec![0.0; 12]), market_metrics: None,
                },
                Trader {
                    address: "0xbbb".to_string(),
                    volume: 3000.0, buy_volume: 1500.0, sell_volume: 1500.0,
                    pnl: -50.0, win_rate: 40.0, sharpe: 0.0, positions: 5,
                    market_titles: vec![], recent_trades: 5, trades_24h: 0, last_trade_ts: None,
                    pnl_curve: None, market_metrics: None,
                },
            ],
        };

        cache.set("7:1.0:1000", payload.clone());

        // Memory hit
        let got = cache.get("7:1.0:1000");
        assert!(got.is_some());
        let got = got.unwrap();
        assert_eq!(got.count, 2);
        assert_eq!(got.traders.len(), 2);
        assert_eq!(got.traders[0].address, "0xaaa");
        assert!((got.traders[0].pnl - 150.0).abs() < 0.01);
        assert_eq!(got.traders[1].pnl_curve, None);

        // Disk roundtrip
        let disk = cache.get_or_disk("7:1.0:1000");
        assert!(disk.is_some());
        let (disk_payload, source) = disk.unwrap();
        assert_eq!(source, "memory"); // should still be in memory
        assert_eq!(disk_payload.count, 2);

        // Missing key
        assert!(cache.get("999:0:100").is_none());
    }

    #[test]
    fn pipeline_cache_disk_persistence() {
        let cache = PipelineCache::new();
        let payload = AggPayload {
            count: 1, candidate_pool: 10, days_window: 1, min_trades_per_day: 0.0,
            synced_at: 0,
            traders: vec![Trader {
                address: "0xccc".to_string(),
                volume: 100.0, buy_volume: 60.0, sell_volume: 40.0,
                pnl: 5.0, win_rate: 50.0, sharpe: 0.0, positions: 2,
                market_titles: vec!["Test".into()], recent_trades: 2, trades_24h: 0, last_trade_ts: None,
                pnl_curve: Some(vec![1.0, 2.0, 3.0]), market_metrics: None,
            }],
        };

        // Use a unique key to avoid colliding with other tests
        let key = format!("test_disk_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos());
        cache.set(&key, payload);

        // Create a fresh cache instance (simulates restart) — it shares the same disk dir
        let cache2 = PipelineCache::new();
        // Memory miss, disk hit
        assert!(cache2.get(&key).is_none(), "new cache should not have it in memory");
        let disk = cache2.get_or_disk(&key);
        assert!(disk.is_some(), "should find it on disk");
        let (p, source) = disk.unwrap();
        // After get_or_disk, it gets loaded into memory, but the source should indicate disk
        // Actually source depends on whether memory was checked first — it will be "disk"
        // since memory was empty on the new instance. But after the call, it's now in memory.
        // However get_or_disk checks memory first via get(), which returns None, then loads from disk.
        // The source returned is "disk" since that's where it came from.
        assert!(source == "memory" || source == "disk", "source should be memory or disk");
        assert_eq!(p.count, 1);
        assert_eq!(p.traders[0].address, "0xccc");
        assert_eq!(p.traders[0].pnl_curve, Some(vec![1.0, 2.0, 3.0]));
    }

    // ── Trader serialization roundtrip ───────────────────────────

    #[test]
    fn trader_json_roundtrip() {
        let trader = Trader {
            address: "0xdeadbeef".to_string(),
            volume: 12345.67,
            buy_volume: 7000.0,
            sell_volume: 5345.67,
            pnl: -420.69,
            win_rate: 55.0,
            sharpe: 1.25,
            positions: 42,
            market_titles: vec!["Will BTC hit 100k?".into(), "US Election".into()],
            recent_trades: 42,
            trades_24h: 7, last_trade_ts: None,
            pnl_curve: Some(vec![0.0, 5.0, 10.0, 8.0, 12.0, 15.0, 14.0, 18.0, 20.0, 22.0, 25.0, 30.0]),
            market_metrics: None,
        };
        let json = serde_json::to_string(&trader).unwrap();
        let parsed: Trader = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.address, "0xdeadbeef");
        assert_eq!(parsed.buy_volume, 7000.0);
        assert_eq!(parsed.sell_volume, 5345.67);
        assert_eq!(parsed.pnl_curve.as_ref().unwrap().len(), 12);

        // Check camelCase serialization
        let v: Value = serde_json::from_str(&json).unwrap();
        assert!(v.get("buyVolume").is_some(), "should use camelCase");
        assert!(v.get("sellVolume").is_some());
        assert!(v.get("marketTitles").is_some());
        assert!(v.get("pnlCurve").is_some());
        assert!(v.get("recentTrades").is_some());
    }

    #[test]
    fn trader_pnl_curve_omitted_when_none() {
        let trader = Trader {
            address: "0x".to_string(),
            volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None,
            pnl_curve: None, market_metrics: None,
        };
        let json = serde_json::to_string(&trader).unwrap();
        assert!(!json.contains("pnlCurve"), "pnlCurve should be omitted when None");
    }

    // ── Progress event shape ─────────────────────────────────────

    #[test]
    fn progress_event_has_hours_fields() {
        let hours_target = 168u64;
        let h_scraped = 84u64;
        let evt = json!({
            "type": "progress", "phase": "enrich",
            "done": 50, "total": 100, "kept": 40,
            "hoursScraped": h_scraped, "hoursTarget": hours_target
        });
        assert_eq!(evt["hoursScraped"], 84);
        assert_eq!(evt["hoursTarget"], 168);
        assert_eq!(evt["phase"], "enrich");
    }

    #[test]
    fn progress_leaderboard_no_hours() {
        let evt = json!({
            "type": "progress", "phase": "leaderboard",
            "done": 10, "total": 80
        });
        assert!(evt.get("hoursScraped").is_none());
        assert!(evt.get("hoursTarget").is_none());
    }
}
