// Top-trader scoring over an N-day window — modelled on polymarket's
// active-traders endpoint: paginate the leaderboard, hydrate each
// candidate's recent fills, score against the window.

use crate::hl::{parse_fills, Client, Fill};
use futures::stream::{self, StreamExt};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

// Live progress for the latest top_traders scan. The frontend polls this
// while scanning so we can show "X of N addresses scanned · Yh history".
#[derive(Debug, Clone, Serialize, Default)]
pub struct ScanProgress {
    pub scanned: usize,
    pub total: usize,
    pub days: u32,
    pub hours_total: u32,
    pub hours_scanned: u32,
    pub started_ms: i64,
    pub finished_ms: i64,
    pub running: bool,
}

#[derive(Default)]
pub struct ProgressTracker { pub state: Mutex<ScanProgress> }

impl ProgressTracker {
    pub fn snapshot(&self) -> ScanProgress { self.state.lock().clone() }
    pub fn start(&self, days: u32, total: usize) {
        let mut s = self.state.lock();
        *s = ScanProgress {
            scanned: 0,
            total,
            days,
            hours_total: days.saturating_mul(24),
            hours_scanned: 0,
            started_ms: chrono::Utc::now().timestamp_millis(),
            finished_ms: 0,
            running: true,
        };
    }
    pub fn tick(&self, scanned: usize) {
        let mut s = self.state.lock();
        s.scanned = scanned;
        if s.total > 0 {
            s.hours_scanned = ((scanned as u64 * s.hours_total as u64)
                / s.total.max(1) as u64) as u32;
        }
    }
    pub fn finish(&self) {
        let mut s = self.state.lock();
        s.scanned = s.total;
        s.hours_scanned = s.hours_total;
        s.finished_ms = chrono::Utc::now().timestamp_millis();
        s.running = false;
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TopTrader {
    pub address: String,
    pub volume: f64,        // USD volume in window
    pub pnl: f64,           // closedPnl - fees, summed
    pub win_rate: f64,      // 0..100, -1 if no realised fills
    pub trades: usize,
    pub coins: Vec<String>, // distinct coins, top-N
    pub avg_trade_usd: f64,
    pub sharpe: f64,        // simple daily-pnl Sharpe (if enough days)
    pub last_active: i64,   // ms
}

// Pick the leaderboard window key whose meaning is closest to the user's
// requested days. The stats CDN exposes "day", "week", "month", "allTime".
fn window_for_days(days: u32) -> &'static str {
    if days <= 1 { "day" }
    else if days <= 7 { "week" }
    else if days <= 30 { "month" }
    else { "allTime" }
}

/// Per-row leaderboard data we keep around for the fast-path fallback.
/// When `/info` 429s on every candidate, we still want the UI to show
/// SOMETHING — the leaderboard CDN already gives us pnl + vlm per window,
/// so we synthesize a TopTrader directly from those.
#[derive(Debug, Clone, Default)]
struct LbRow {
    pnl: f64,
    vlm: f64,
    last_active_ms: i64,
}

// Parse the stats-CDN leaderboard, ranking candidates by the chosen window's
// PnL so the cohort we score with fills actually reflects performance in
// that window — not just the all-time top accounts.
fn parse_lb_ranked(v: &Value, window: &str) -> Vec<(String, LbRow)> {
    let rows = v.get("leaderboardRows").and_then(|x| x.as_array());
    let mut scored: Vec<(String, LbRow)> = Vec::new();
    if let Some(rows) = rows {
        for row in rows {
            let addr = row.get("ethAddress").and_then(|x| x.as_str()).unwrap_or("");
            if !addr.starts_with("0x") || addr.len() != 42 { continue; }
            let mut data = LbRow::default();
            let mut found = false;
            if let Some(perfs) = row.get("windowPerformances").and_then(|x| x.as_array()) {
                for p in perfs {
                    if let Some(pair) = p.as_array() {
                        if pair.len() == 2 && pair[0].as_str() == Some(window) {
                            let body = &pair[1];
                            data.pnl = body.get("pnl").and_then(|x| x.as_str())
                                .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                            data.vlm = body.get("vlm").and_then(|x| x.as_str())
                                .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                            found = true;
                        }
                    }
                }
            }
            if !found { data.pnl = f64::NEG_INFINITY; }
            scored.push((addr.to_lowercase(), data));
        }
    }
    scored.sort_by(|a, b| b.1.pnl.partial_cmp(&a.1.pnl).unwrap_or(std::cmp::Ordering::Equal));
    // Drop the sentinel -inf rows (no window data) before returning.
    scored.retain(|(_, d)| d.pnl > f64::NEG_INFINITY);
    scored
}

fn score_fills(fills: &[Fill], cutoff_ms: i64) -> (f64, f64, f64, usize, Vec<String>, f64, i64) {
    let mut volume = 0.0;
    let mut pnl = 0.0;
    let mut wins = 0usize;
    let mut realised = 0usize;
    let mut coins: std::collections::BTreeMap<String, usize> = Default::default();
    let mut last = 0i64;
    let mut daily: std::collections::BTreeMap<i64, f64> = Default::default();
    let mut count = 0usize;
    for f in fills {
        if f.time < cutoff_ms { continue; }
        count += 1;
        let px: f64 = f.px.parse().unwrap_or(0.0);
        let sz: f64 = f.sz.parse().unwrap_or(0.0);
        let cp: f64 = f.closed_pnl.parse().unwrap_or(0.0);
        let fee: f64 = f.fee.parse().unwrap_or(0.0);
        volume += px * sz;
        pnl += cp - fee;
        if cp != 0.0 {
            realised += 1;
            if cp > 0.0 { wins += 1; }
        }
        *coins.entry(f.coin.clone()).or_insert(0) += 1;
        if f.time > last { last = f.time; }
        let day = f.time / 86_400_000;
        *daily.entry(day).or_insert(0.0) += cp - fee;
    }
    let win_rate = if realised == 0 { -1.0 } else { (wins as f64 / realised as f64) * 100.0 };
    let sharpe = if daily.len() < 2 {
        0.0
    } else {
        let xs: Vec<f64> = daily.values().copied().collect();
        let mean = xs.iter().sum::<f64>() / xs.len() as f64;
        let var = xs.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / xs.len() as f64;
        let sd = var.sqrt();
        if sd == 0.0 { 0.0 } else { mean / sd * (xs.len() as f64).sqrt() }
    };
    let mut coins_v: Vec<(String, usize)> = coins.into_iter().collect();
    coins_v.sort_by(|a, b| b.1.cmp(&a.1));
    let coins: Vec<String> = coins_v.into_iter().take(8).map(|(c, _)| c).collect();
    (volume, pnl, win_rate, count, coins, sharpe, last)
}

pub async fn top_traders(
    hl: Arc<Client>,
    days: u32,
    min_per_day: f64,
    pool: usize,
    extra_addrs: Vec<String>,
) -> anyhow::Result<Vec<TopTrader>> {
    top_traders_with_progress(hl, days, min_per_day, pool, extra_addrs, None).await
}

pub async fn top_traders_with_progress(
    hl: Arc<Client>,
    days: u32,
    min_per_day: f64,
    pool: usize,
    extra_addrs: Vec<String>,
    progress: Option<Arc<ProgressTracker>>,
) -> anyhow::Result<Vec<TopTrader>> {
    let lb = hl.leaderboard().await.unwrap_or(Value::Null);
    let ranked = parse_lb_ranked(&lb, window_for_days(days));
    // Keep the per-row data accessible by address for the fast-path
    // fallback below. We also keep the ordered Vec<String> for the fills
    // fan-out so the rest of the loop is unchanged.
    let mut lb_by_addr: std::collections::HashMap<String, LbRow> =
        ranked.iter().cloned().collect();
    let mut addrs: Vec<String> = ranked.into_iter().map(|(a, _)| a).collect();
    for a in extra_addrs {
        if !addrs.contains(&a) {
            addrs.push(a.clone());
            lb_by_addr.entry(a).or_default();
        }
    }
    addrs.truncate(pool.max(1));

    let cutoff_ms: i64 = chrono::Utc::now().timestamp_millis()
        - (days as i64) * 86_400_000;
    let min_trades = ((days as f64) * min_per_day).ceil() as usize;

    if let Some(p) = progress.as_ref() { p.start(days, addrs.len()); }
    let counter = Arc::new(AtomicUsize::new(0));

    // Hyperliquid /info rate-limits aggressive parallel scans; cap concurrency
    // so longer windows don't silently drop addresses to 429s.
    let results: Vec<(String, Vec<Fill>)> = stream::iter(addrs.iter().cloned())
        .map(|addr| {
            let hl = hl.clone();
            let progress = progress.clone();
            let counter = counter.clone();
            async move {
                let r = match hl.user_fills_by_time(&addr, cutoff_ms).await {
                    Ok(v) => (addr.clone(), parse_fills(&v)),
                    Err(e) => {
                        tracing::warn!("fills fetch failed for {addr}: {e}");
                        (addr.clone(), Vec::new())
                    }
                };
                let n = counter.fetch_add(1, Ordering::Relaxed) + 1;
                if let Some(p) = progress.as_ref() { p.tick(n); }
                r
            }
        })
        // Down from 4 — HL's /info aggressively 429s above ~2 concurrent
        // per-IP, and each 429 drops that candidate from the cohort
        // (no fill data = no score). 2 keeps us under the throttle while
        // the scan stays usable.
        .buffer_unordered(2)
        .collect()
        .await;
    if let Some(p) = progress.as_ref() { p.finish(); }

    let mut out = Vec::new();
    let mut fill_scored = 0usize;
    for (addr, fills) in results {
        if fills.len() < min_trades.max(1) { continue; }
        let (vol, pnl, wr, n, coins, sharpe, last) = score_fills(&fills, cutoff_ms);
        if n == 0 { continue; }
        fill_scored += 1;
        out.push(TopTrader {
            address: addr,
            volume: vol, pnl, win_rate: wr,
            trades: n, coins,
            avg_trade_usd: if n == 0 { 0.0 } else { vol / n as f64 },
            sharpe,
            last_active: last,
        });
    }
    // Fallback: if /info was rate-limited so hard that we couldn't score
    // enough candidates (less than half of the requested pool), synthesize
    // rows from the leaderboard CDN data we already have. PnL + volume
    // come from the window, but per-fill stats (win_rate, sharpe, trades,
    // coins) are unknown — sentinel as -1 / 0 / [] so the UI can render
    // them as "—". Better to show partial data than an empty table.
    let want_at_least = (pool / 2).max(1);
    if fill_scored < want_at_least {
        let have: std::collections::HashSet<String> =
            out.iter().map(|t| t.address.clone()).collect();
        for addr in &addrs {
            if have.contains(addr) { continue; }
            let row = lb_by_addr.get(addr).cloned().unwrap_or_default();
            // Skip if the window had no PnL data at all (already filtered
            // upstream, but keep the guard for the extra_addrs path).
            if row.pnl == 0.0 && row.vlm == 0.0 { continue; }
            out.push(TopTrader {
                address: addr.clone(),
                volume: row.vlm,
                pnl: row.pnl,
                win_rate: -1.0,   // unknown — UI renders "—"
                trades: 0,
                coins: Vec::new(),
                avg_trade_usd: 0.0,
                sharpe: 0.0,
                last_active: row.last_active_ms,
            });
            if out.len() >= pool { break; }
        }
    }
    out.sort_by(|a, b| b.pnl.partial_cmp(&a.pnl).unwrap_or(std::cmp::Ordering::Equal));
    Ok(out)
}

pub async fn analyze(hl: Arc<Client>, addr: &str, days: u32) -> anyhow::Result<Value> {
    let cutoff_ms: i64 = chrono::Utc::now().timestamp_millis()
        - (days as i64) * 86_400_000;
    let fills_v = hl.user_fills_by_time(addr, cutoff_ms).await.unwrap_or(Value::Null);
    let fills = parse_fills(&fills_v);
    let (vol, pnl, wr, n, coins, sharpe, last) = score_fills(&fills, cutoff_ms);
    let state = hl.user_state(addr).await.unwrap_or(Value::Null);
    let pnl_hist = hl.user_pnl(addr).await.unwrap_or(Value::Null);
    let open = hl.open_orders(addr).await.unwrap_or(Value::Null);
    Ok(serde_json::json!({
        "address": addr,
        "days": days,
        "summary": TopTrader {
            address: addr.to_string(),
            volume: vol, pnl, win_rate: wr, trades: n,
            coins, sharpe,
            avg_trade_usd: if n == 0 { 0.0 } else { vol / n as f64 },
            last_active: last,
        },
        "state": state,
        "pnl_history": pnl_hist,
        "open_orders": open,
        "fills": fills_v,
    }))
}
