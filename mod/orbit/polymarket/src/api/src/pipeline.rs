use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;

use futures::stream::{self, StreamExt};
use parking_lot::RwLock;
use serde_json::Value;

use crate::cache::PipelineCache;
use crate::first_trade::FirstTradeStore;
use crate::types::{AggPayload, MarketMetric, Trader};

const DATA_API: &str = "https://data-api.polymarket.com";
const PAGE_SIZE: u32 = 50;

pub struct PipelineState {
    pub cache: PipelineCache,
    pub http: reqwest::Client,
    /// Wallet → first-ever trade. Shared across every window so the 30D pass
    /// pays nothing for what the 1D pass already resolved.
    pub first_trades: Arc<FirstTradeStore>,
    warmup_running: RwLock<bool>,
}

impl PipelineState {
    pub fn new(http: reqwest::Client) -> Self {
        Self {
            cache: PipelineCache::new(),
            http,
            first_trades: Arc::new(FirstTradeStore::new()),
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

        let mut combos = vec![(1u32, 0.0, 2000u32), (7, 0.0, 2000), (14, 0.0, 2000), (30, 0.0, 2000)];

        // STALEST FIRST — not 1D, 7D, 14D, 30D in that order.
        //
        // A cycle is not guaranteed to finish. The fleet activator stops this
        // process after ~60s with no connections, and a full four-window pass
        // takes ~10 minutes; every wake used to restart the list at 1D, finish
        // it in ~2 minutes, and die somewhere in 7D. The deep windows were
        // never reached: while 1D was re-synced every few minutes, the 30D
        // board sat untouched for three days, aged past the disk cache's 24h
        // ceiling, and the console answered "the 30D leaderboard hasn't been
        // aggregated yet" — a starved queue reading as missing data.
        //
        // Ordering by last sync makes an interrupted cycle still make
        // progress: whatever is furthest behind goes first, so the window
        // nobody has rebuilt is the one that gets rebuilt. The sort key falls
        // through to the DISK tier (`synced_at_any_age`) on purpose — memory
        // is empty right after the restart that caused the problem, and a
        // memory-only read would call every window equally stale and re-fix
        // the fixed order this sort exists to break.
        combos.sort_by_key(|(days, min_per_day, pool)| {
            let key = format!("{}:{}:{}", days, min_per_day, pool);
            self.cache
                .synced_at_any_age(&key)
                .unwrap_or(i64::MIN) // never synced at all = most urgent
        });

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
            tracing::info!("warming {}D…", days);
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
            // Persist per window, not per cycle: the cycle may not reach its
            // end (see the ordering note above), and losing 2000 resolved
            // first-trades to a stop would mean re-fetching them next wake.
            self.first_trades.flush();
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
                                        decided_positions: 0,
exit_entry: -1.0,
                    positions: 0,
                    market_titles: vec![],
                    recent_trades: 0,
                    trades_24h: 0,
                    last_trade_ts: None, first_trade_ts: None,
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
        let first_trades = self.first_trades.clone();
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
                let first_trades = first_trades.clone();
                async move {
                    let (trader_opt, oldest_ts) = enrich_trader(http.clone(), t, cutoff, min_trades).await;
                    let depth = now_sec.saturating_sub(oldest_ts.min(now_sec));
                    depth_accum.fetch_add(depth, Ordering::Relaxed);
                    let done = done_counter.fetch_add(1, Ordering::Relaxed) + 1;
                    // How long this wallet has existed, resolved only for the
                    // traders that survived enrichment — the dropped ones are
                    // ~2/3 of the candidate pool and nothing reads their age.
                    // Cached forever, so this is one request per NEW wallet,
                    // not one per cycle.
                    let mut trader_opt = trader_opt;
                    if let Some(ref mut trader) = trader_opt {
                        trader.first_trade_ts =
                            first_trades.resolve(&http, &trader.address, DATA_API).await;
                    }
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

        // Accuracy pass, deliberately AFTER the activity fan-out rather than
        // inside it. Both endpoints are data-api and share one rate limit;
        // interleaved with a 64-wide activity storm every settled-book fetch
        // came back 429 and 67 of 90 traders scored "unknown". Run as its own
        // bounded pass it lands. Only surviving traders are asked about, so
        // this is a fraction of the requests the enrichment above spends.
        fill_settled_accuracy(&self.http, DATA_API, &mut out, cutoff_sec).await;

        out.sort_by(|a, b| b.pnl.partial_cmp(&a.pnl).unwrap_or(std::cmp::Ordering::Equal));
        // Newly resolved account ages go to disk here too, so an on-demand
        // run (SYNC NOW, a cold `days=` the warmup hasn't reached) keeps what
        // it learned.
        self.first_trades.flush();

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

/// Fill `win_rate` / `decided_positions` (and the per-market split behind a
/// filtered board) from each trader's settled book.
///
/// Traders whose settled book cannot be fetched keep `win_rate = -1`. That is
/// the point: the activity-derived number these rows used to carry could only
/// see winners, so "unknown" is the only honest fallback. A blank cell is
/// recoverable; a fabricated 100% gets copied with money.
pub async fn fill_settled_accuracy(
    http: &reqwest::Client,
    base_url: &str,
    traders: &mut [Trader],
    cutoff_sec: u64,
) {
    let addrs: Vec<String> = traders.iter().map(|t| t.address.clone()).collect();
    let fetched: Vec<Option<crate::settled::SettledAccuracy>> = stream::iter(addrs)
        .map(|addr| {
            let http = http.clone();
            async move {
                crate::settled::fetch_settled_legs(&http, base_url, &addr, cutoff_sec)
                    .await
                    .map(|legs| crate::settled::accuracy_from_legs(&legs, cutoff_sec))
            }
        })
        // The settled fetch has its own internal concurrency gate; this bound
        // just keeps the futures from all queueing at once.
        .buffer_unordered(8)
        .collect()
        .await;

    for (trader, acc) in traders.iter_mut().zip(fetched) {
        let Some(acc) = acc else { continue };
        trader.decided_positions = acc.decided;
        trader.win_rate = if acc.decided > 0 {
            ((acc.wins as f64 / acc.decided as f64) * 100.0).round().min(100.0)
        } else {
            -1.0
        };
        // The filtered board (routes.rs) recomputes the rate by summing these,
        // so it has to see the same settled book the unfiltered number does.
        if let Some(ref mut mm) = trader.market_metrics {
            for m in mm.iter_mut() {
                let (w, d) = acc.per_title.get(&m.title).copied().unwrap_or((0, 0));
                m.wins = w;
                m.decided = d;
            }
        }
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
    // 11 pages, not 40. data-api 400s on `offset > 5000` ("max historical
    // activity offset of 5000 exceeded"), so pages 12..40 could only ever
    // return an error object — which `json::<Vec<Value>>` fails to parse and
    // this loop then breaks on. Same result, one wasted upstream request per
    // heavy trader, on precisely the traders the leaderboard is made of.
    const MAX_PAGES: u32 = 11; // 5500 activity rows — the upstream ceiling
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
    // Through `normalize_ts`, not a raw `as_u64()`: data-api rows come back in
    // both seconds and milliseconds, and a raw ms value is ~1000× the cutoff so
    // every row passed the 24h test, while a float-typed second value read as
    // `None` and dropped the row entirely.
    trader.trades_24h = all_trades
        .iter()
        .map(normalize_ts)
        .filter(|&ts| ts >= cutoff_24h)
        .count() as u32;
    // Stamp the most recent in-window trade so the leaderboard can show
    // "last trade Xm ago" — surfaces dormants that have a good window-
    // total but went silent days ago. Uses the same timestamp pass as
    // trades_24h above so no extra network cost.
    trader.last_trade_ts = all_trades
        .iter()
        .map(normalize_ts)
        .filter(|&ts| ts > 0)
        .max();
    trader.volume = metrics.volume;
    trader.buy_volume = metrics.buy_volume;
    trader.sell_volume = metrics.sell_volume;
    trader.pnl = metrics.pnl;
    // Sharpe + exit/entry over the same window the rest of the row's stats
    // use — SCORE presets in the leaderboard. Reuses the live engine's ONE
    // stats formula so ranking here matches copy-candidate scoring. The
    // exit/entry ratio is `1 + mean return` (each return is
    // `(exit − entry) / entry`), -1 when no closed trades decided anything.
    let rs = crate::live_engine::stats_from_returns(&metrics.returns);
    trader.sharpe = rs.sharpe;
    trader.exit_entry = if rs.sample_size > 0 { 1.0 + rs.roi } else { -1.0 };
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

    // Accuracy is NOT derived here. `/activity` can only show a position
    // ending when the ending leaves a row — a sell or a redeem — and losers
    // leave neither: the tokens burn to zero and nobody pays gas to claim $0.
    // Counting observable endings therefore drops losers only, which is how
    // this column read 100%. `fill_settled_accuracy` runs afterwards, off the
    // settled book. Until it does, the honest value is "unknown".
    trader.win_rate = -1.0;
    trader.decided_positions = 0;

    trader.market_metrics = if metrics.per_market.is_empty() { None } else { Some(metrics.per_market) };

    (Some(trader), oldest_ts)
}

struct WindowMetrics {
    volume: f64,
    buy_volume: f64,
    sell_volume: f64,
    pnl: f64,
    count: u32,
    /// Per-closed-SELL fractional returns `(price − avgCost) / avgCost`
    /// realized in-window — the series Sharpe is computed from.
    returns: Vec<f64>,
    per_market: Vec<MarketMetric>,
}

fn compute_window_metrics(trades: &[Value], cutoff_sec: u64) -> WindowMetrics {
    let mut sorted: Vec<&Value> = trades.iter()
        .filter(|t| {
            let ty = t.get("type").and_then(|v| v.as_str()).unwrap_or("TRADE");
            // REDEEMs carry no volume, but they are settlement events and
            // must not be mistaken for trades in the volume/count pass.
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
    struct MktAccum { volume: f64, buy_volume: f64, sell_volume: f64, pnl: f64, trades: u32, wins: u32, decided: u32, returns: Vec<f64>, curve: Vec<f64> }
    let mut per_market: HashMap<String, MktAccum> = HashMap::new();

    // Bucketing for the per-market realized-PnL curve — the SAME 12-bucket
    // window split `compute_pnl_curve` uses for the trader-level curve, so a
    // query-scoped curve summed from these lines up with the unscoped one.
    const CURVE_BUCKETS: usize = 12;
    let curve_now_sec = chrono::Utc::now().timestamp() as u64;
    let curve_bucket_size = (curve_now_sec.saturating_sub(cutoff_sec) / CURVE_BUCKETS as u64).max(1);

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

        // A REDEEM settles a position; it carries no price or size to book
        // into volume, PnL or the return series. Win/loss for it is read off
        // the settled book instead — see settled.rs.
        if ty == "REDEEM" {
            continue;
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
                    curve: vec![0.0; CURVE_BUCKETS],
                });
                mkt.volume += usdc_size;
                mkt.trades += 1;
                mkt.pnl += realized;
                if realized != 0.0 {
                    let idx = ((ts.saturating_sub(cutoff_sec)) / curve_bucket_size)
                        .min(CURVE_BUCKETS as u64 - 1) as usize;
                    mkt.curve[idx] += realized;
                }
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

    // Accuracy counters stay at zero here. They used to be folded in from
    // the exits visible in this activity pull, which structurally could not
    // see a loser — `enrich_trader_with_url` fills them from the settled book
    // (settled.rs) after this returns.

    let market_metrics: Vec<MarketMetric> = per_market.into_iter().map(|(title, m)| {
        MarketMetric {
            title, volume: m.volume, buy_volume: m.buy_volume,
            sell_volume: m.sell_volume, pnl: m.pnl, trades: m.trades,
            wins: m.wins, decided: m.decided, returns: m.returns,
            curve: m.curve,
        }
    }).collect();

    WindowMetrics {
        volume: buy_volume + sell_volume,
        buy_volume,
        sell_volume,
        pnl,
        count,
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

    // ── settlement rows ──────────────────────────────────────────
    //
    // Buy-accuracy is no longer derived here. It used to be, and it could
    // only ever see the endings that leave an activity row — which is every
    // winner and no loser. The counters now come from the settled book; see
    // `settled.rs` and `win_rate_counts_losers_that_left_no_activity_row`.
    // What is still this function's job is not letting REDEEM rows leak into
    // the money numbers.

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
    fn redeems_do_not_move_volume_pnl_or_returns() {
        // A redeem has no price and no side. Booking it as a trade would
        // inflate volume and push a bogus return into the Sharpe series.
        let trades = vec![
            trade(2000, "BUY", 0.60, 100.0, "mkt1"),
            redeem(3000, 100.0, "mkt1"),
        ];
        let m = compute_window_metrics(&trades, 1000);
        assert_eq!(m.count, 1, "only the BUY is a trade");
        assert_eq!(m.sell_volume, 0.0);
        assert_eq!(m.pnl, 0.0, "a redeem books no realized PnL here");
        assert!(m.returns.is_empty(), "a redeem is not a closed-trade return");
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

    /// A zero-valued Trader row — every enrichment test starts from one and
    /// asserts on what the pipeline filled in.
    fn blank_trader(address: &str) -> Trader {
        Trader {
            address: address.to_string(),
            volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, exit_entry: -1.0,
            positions: 0, decided_positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0,
            last_trade_ts: None, first_trade_ts: None,
            pnl_curve: None, market_metrics: None,
        }
    }

    /// The regression behind "your winrate says 100% but man its bs".
    ///
    /// This wallet made four calls in the window: two winners it redeemed for
    /// a payout, and two losers it never touched again — the markets resolved
    /// against it and the tokens burned to zero, leaving no SELL and no
    /// REDEEM in `/activity`. Reading accuracy off observable exits sees only
    /// the two winners and scores 100%. The settled book has all four.
    #[tokio::test]
    async fn win_rate_counts_losers_that_left_no_activity_row() {
        let now = chrono::Utc::now().timestamp() as u64;
        let cutoff = now - 7 * 86400;

        let settled = serde_json::to_string(&vec![
            json!({"title":"Won A","realizedPnl": 50.0,"curPrice":1.0,"totalBought":50.0,"timestamp": now-100}),
            json!({"title":"Won B","realizedPnl": 50.0,"curPrice":1.0,"totalBought":50.0,"timestamp": now-100}),
            json!({"title":"Lost A","realizedPnl":-50.0,"curPrice":0.0,"totalBought":50.0,"timestamp": now-100}),
            json!({"title":"Lost B","realizedPnl":-50.0,"curPrice":0.0,"totalBought":50.0,"timestamp": now-100}),
        ]).unwrap();

        let mut server = mockito::Server::new_async().await;
        let _c = server.mock("GET", mockito::Matcher::Regex(r"^/closed-positions.*".to_string()))
            .with_status(200).with_header("content-type", "application/json")
            .with_body(&settled).expect_at_least(1).create_async().await;

        // Two of the four markets are on the row's per-market breakdown; the
        // filtered board recomputes the rate by summing those, so they have to
        // pick up the losers too.
        let mut t = blank_trader("0xburned");
        t.market_metrics = Some(vec![
            mkt_metric("Won A"),
            mkt_metric("Lost A"),
        ]);
        let mut traders = vec![t];

        fill_settled_accuracy(&reqwest::Client::new(), &server.url(), &mut traders, cutoff).await;

        assert_eq!(traders[0].win_rate, 50.0, "two of four settled positions won");
        assert_eq!(traders[0].decided_positions, 4, "all four settled legs are the denominator");

        let mm = traders[0].market_metrics.as_ref().unwrap();
        let won = mm.iter().find(|m| m.title == "Won A").unwrap();
        let lost = mm.iter().find(|m| m.title == "Lost A").unwrap();
        assert_eq!((won.wins, won.decided), (1, 1));
        assert_eq!((lost.wins, lost.decided), (0, 1), "the loser must be in the per-market denominator");
    }

    /// A failed settled-book fetch must read as unknown — never as the
    /// exit-derived number, which is the biased one.
    #[tokio::test]
    async fn win_rate_is_unknown_when_the_settled_book_is_unreachable() {
        let now = chrono::Utc::now().timestamp() as u64;
        let mut server = mockito::Server::new_async().await;
        let _c = server.mock("GET", mockito::Matcher::Regex(r"^/closed-positions.*".to_string()))
            .with_status(404).expect_at_least(1).create_async().await;

        let mut traders = vec![blank_trader("0xnofetch")];
        traders[0].win_rate = -1.0;

        fill_settled_accuracy(&reqwest::Client::new(), &server.url(), &mut traders, now - 7 * 86400).await;

        assert_eq!(traders[0].win_rate, -1.0, "unknown, not the 100% the exits imply");
        assert_eq!(traders[0].decided_positions, 0);
    }

    /// A trader who settled nothing in the window is unknown, not 0%.
    #[tokio::test]
    async fn win_rate_is_unknown_when_nothing_settled() {
        let now = chrono::Utc::now().timestamp() as u64;
        let mut server = mockito::Server::new_async().await;
        let _c = server.mock("GET", mockito::Matcher::Regex(r"^/closed-positions.*".to_string()))
            .with_status(200).with_header("content-type", "application/json")
            .with_body("[]").expect_at_least(1).create_async().await;

        let mut traders = vec![blank_trader("0xnothing")];
        fill_settled_accuracy(&reqwest::Client::new(), &server.url(), &mut traders, now - 7 * 86400).await;

        assert_eq!(traders[0].win_rate, -1.0);
        assert_eq!(traders[0].decided_positions, 0);
    }

    /// The enrichment pass leaves accuracy unknown — it has no source for it.
    #[tokio::test]
    async fn enrichment_alone_does_not_claim_a_win_rate() {
        let now = chrono::Utc::now().timestamp() as u64;
        let activity = serde_json::to_string(&vec![
            json!({"type":"TRADE","timestamp": now-6000,"side":"BUY","price":0.50,"size":100.0,"conditionId":"w1","title":"Won A","usdcSize":50.0}),
            json!({"type":"REDEEM","timestamp": now-100,"conditionId":"w1","title":"Won A","usdcSize":100.0}),
        ]).unwrap();

        let mut server = mockito::Server::new_async().await;
        let _a = server.mock("GET", mockito::Matcher::Regex(r"^/activity.*".to_string()))
            .with_status(200).with_header("content-type", "application/json")
            .with_body(&activity).expect_at_least(1).create_async().await;

        let (out, _) = enrich_trader_with_url(
            reqwest::Client::new(), blank_trader("0xenrich"), now - 7 * 86400, 1, &server.url()).await;
        let t = out.expect("trader should enrich");

        assert_eq!(t.win_rate, -1.0, "the redeemed winner alone is not a 100% record");
        assert!(t.volume > 0.0, "the rest of the row is still real");
    }

    /// A zero-valued per-market row — the accuracy pass fills wins/decided.
    fn mkt_metric(title: &str) -> MarketMetric {
        MarketMetric {
            title: title.to_string(),
            volume: 0.0, buy_volume: 0.0, sell_volume: 0.0, pnl: 0.0,
            trades: 0, wins: 0, decided: 0, returns: vec![], curve: vec![],
        }
    }

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
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, exit_entry: -1.0, positions: 0, decided_positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None, first_trade_ts: None, pnl_curve: None, market_metrics: None,
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
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, exit_entry: -1.0, positions: 0, decided_positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None, first_trade_ts: None, pnl_curve: None, market_metrics: None,
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
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, exit_entry: -1.0, positions: 0, decided_positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None, first_trade_ts: None, pnl_curve: None, market_metrics: None,
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
                    pnl: 150.0, win_rate: 65.0, sharpe: 0.0, exit_entry: -1.0, positions: 10,
                    decided_positions: 0,
                    market_titles: vec!["Market A".into()], recent_trades: 10, trades_24h: 0, last_trade_ts: None, first_trade_ts: None,
                    pnl_curve: Some(vec![0.0; 12]), market_metrics: None,
                },
                Trader {
                    address: "0xbbb".to_string(),
                    volume: 3000.0, buy_volume: 1500.0, sell_volume: 1500.0,
                    pnl: -50.0, win_rate: 40.0, sharpe: 0.0, exit_entry: -1.0, positions: 5,
                    decided_positions: 0,
                    market_titles: vec![], recent_trades: 5, trades_24h: 0, last_trade_ts: None, first_trade_ts: None,
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
                pnl: 5.0, win_rate: 50.0, sharpe: 0.0, exit_entry: -1.0, positions: 2,
                    decided_positions: 0,
                market_titles: vec!["Test".into()], recent_trades: 2, trades_24h: 0, last_trade_ts: None, first_trade_ts: None,
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

        // The cache dir is the durable state dir, not /tmp — clean up or
        // every test run leaves a test_disk_* file beside the real payloads.
        std::fs::remove_file(cache2.disk_path(&key)).ok();
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
            decided_positions: 0,
            sharpe: 1.25,
            exit_entry: 1.08,
            positions: 42,
            market_titles: vec!["Will BTC hit 100k?".into(), "US Election".into()],
            recent_trades: 42,
            trades_24h: 7, last_trade_ts: None, first_trade_ts: None,
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
        assert!(v.get("exitEntry").is_some());
        assert_eq!(parsed.exit_entry, 1.08);
    }

    #[test]
    fn trader_exit_entry_defaults_to_unknown_on_old_payloads() {
        // Disk caches written before the exitEntry field existed must still
        // load — and surface the same -1 "unknown" sentinel winRate uses,
        // not a fake break-even 0.
        let json = r#"{"address":"0x","volume":1.0,"buyVolume":1.0,"sellVolume":0.0,
            "pnl":0.0,"winRate":-1.0,"positions":0,"marketTitles":[],"recentTrades":0}"#;
        let parsed: Trader = serde_json::from_str(json).unwrap();
        assert_eq!(parsed.exit_entry, -1.0);
    }

    #[test]
    fn trader_pnl_curve_omitted_when_none() {
        let trader = Trader {
            address: "0x".to_string(),
            volume: 0.0, buy_volume: 0.0, sell_volume: 0.0,
            pnl: 0.0, win_rate: 0.0, sharpe: 0.0, exit_entry: -1.0, positions: 0, decided_positions: 0,
            market_titles: vec![], recent_trades: 0, trades_24h: 0, last_trade_ts: None, first_trade_ts: None,
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
