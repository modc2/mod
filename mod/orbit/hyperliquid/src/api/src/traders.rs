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
    pub roi: f64,           // window return on equity, percent (the ranking metric)
    pub account_value: f64, // current account equity (USD), for context / sizing ROI
    pub volume: f64,        // USD volume in window
    pub pnl: f64,           // closedPnl - fees, summed
    pub win_rate: f64,      // 0..100, -1 if no realised fills
    pub trades: usize,
    pub coins: Vec<String>, // distinct coins, top-N
    pub avg_trade_usd: f64,
    pub sharpe: f64,        // simple daily-pnl Sharpe (if enough days)
    pub last_active: i64,   // ms
}

/// What "top" means. `Roi` is the default board (best return on equity);
/// `Pnl` is HL's own leaderboard order (biggest dollar winners); `Volume` is
/// the most active books. All three select AND sort on the same metric, so a
/// board is literally the top-`pool` of the scrape by that metric.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Rank { Roi, Pnl, Volume }

impl Rank {
    pub fn parse(s: &str) -> Option<Rank> {
        match s.trim().to_ascii_lowercase().as_str() {
            "" | "roi" => Some(Rank::Roi),
            "pnl" => Some(Rank::Pnl),
            "volume" | "vlm" | "vol" => Some(Rank::Volume),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self { Rank::Roi => "roi", Rank::Pnl => "pnl", Rank::Volume => "volume" }
    }
}

/// Liveness gate. `Day` = traded in the last 24h (the strict default — a
/// dormant book can't be copied). `Window` = traded at some point inside the
/// ranking window, which is what HL's own leaderboard implies and what keeps a
/// weekend-quiet whale on the 7d/30d PnL board.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Active { Day, Window }

impl Active {
    pub fn parse(s: &str) -> Option<Active> {
        match s.trim().to_ascii_lowercase().as_str() {
            "" | "24h" | "day" | "1d" => Some(Active::Day),
            "window" | "any" => Some(Active::Window),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self { Active::Day => "24h", Active::Window => "window" }
    }
}

/// Cache key for one computed board.
pub fn board_key(days: u32, rank: Rank, active: Active) -> String {
    format!("{days}:{}:{}", rank.as_str(), active.as_str())
}

/// Inverse of `board_key` — the prewarm loop uses it to keep refreshing any
/// board a visitor has asked for, not only the standard set.
pub fn parse_board_key(key: &str) -> Option<(u32, Rank, Active)> {
    let mut it = key.split(':');
    let days = it.next()?.parse::<u32>().ok()?;
    let rank = Rank::parse(it.next()?)?;
    let active = Active::parse(it.next()?)?;
    if it.next().is_some() { return None; }
    Some((days, rank, active))
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
// Dust guard for ROI ranking. ROI rewards small accounts that got lucky
// (a $50 account that doubles shows +100% ROI), which would otherwise crowd
// out real, copyable traders. Require a minimum account equity so the board
// is "top ROI among accounts worth copying", not "top ROI among dust".
const MIN_ACCOUNT_VALUE: f64 = 1_000.0;

// We render every selected row straight from the leaderboard (real ROI / PnL /
// volume), and only hit /info for per-fill colour (win%, sharpe, trade count,
// coins) on the top slice. HL 429s aggressive /info scans, so enriching all of
// a 600-pool would take minutes; cap it so the board stays responsive. Rows
// past the cap still show the scraped ROI/PnL/volume, just "—" for fill stats.
const ENRICH_CAP: usize = 120;

#[derive(Debug, Clone, Default)]
struct LbRow {
    roi: f64,               // ranking-window ROI as a fraction (0.05 == +5%)
    pnl: f64,               // ranking-window PnL (USD)
    vlm: f64,               // ranking-window volume (USD)
    day_vlm: f64,           // last-24h volume — our "traded recently" signal
    account_value: f64,
}

impl LbRow {
    fn metric(&self, rank: Rank) -> f64 {
        match rank { Rank::Roi => self.roi, Rank::Pnl => self.pnl, Rank::Volume => self.vlm }
    }
    fn is_active(&self, active: Active) -> bool {
        match active { Active::Day => self.day_vlm > 0.0, Active::Window => self.vlm > 0.0 }
    }
}

// Parse the stats-CDN leaderboard. We rank candidates by the chosen window's
// metric (`rank`: HL's time-weighted `roi`, or `pnl`, or `vlm`) and, separately,
// read the "day" window's volume so the `active` gate can keep only accounts
// that actually traded recently. pnl/vlm/accountValue ride along for display.
fn parse_lb_ranked(v: &Value, window: &str, rank: Rank, active: Active) -> Vec<(String, LbRow)> {
    let rows = v.get("leaderboardRows").and_then(|x| x.as_array());
    let mut scored: Vec<(String, LbRow)> = Vec::new();
    if let Some(rows) = rows {
        for row in rows {
            let addr = row.get("ethAddress").and_then(|x| x.as_str()).unwrap_or("");
            if !addr.starts_with("0x") || addr.len() != 42 { continue; }
            let mut data = LbRow::default();
            data.account_value = row.get("accountValue").and_then(|x| x.as_str())
                .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
            let mut found = false;
            if let Some(perfs) = row.get("windowPerformances").and_then(|x| x.as_array()) {
                for p in perfs {
                    let Some(pair) = p.as_array() else { continue };
                    if pair.len() != 2 { continue; }
                    let name = pair[0].as_str().unwrap_or("");
                    let body = &pair[1];
                    let vlm = body.get("vlm").and_then(|x| x.as_str())
                        .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                    if name == "day" { data.day_vlm = vlm; }
                    if name == window {
                        data.roi = body.get("roi").and_then(|x| x.as_str())
                            .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                        data.pnl = body.get("pnl").and_then(|x| x.as_str())
                            .and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                        data.vlm = vlm;
                        found = true;
                    }
                }
            }
            if !found { data.roi = f64::NEG_INFINITY; }
            scored.push((addr.to_lowercase(), data));
        }
    }
    // Keep accounts that (a) have ranking-window data, (b) clear the dust
    // floor, and (c) pass the liveness gate — last-24h volume by default, or
    // any volume inside the window. A book with zero volume in every window is
    // a holder, not a trader (the $1B+ "top PnL" rows on HL's raw leaderboard
    // are exactly that), and can't be copied no matter how big its PnL.
    scored.retain(|(_, d)| {
        d.roi > f64::NEG_INFINITY && d.account_value >= MIN_ACCOUNT_VALUE && d.is_active(active)
    });
    scored.sort_by(|a, b| b.1.metric(rank).partial_cmp(&a.1.metric(rank)).unwrap_or(std::cmp::Ordering::Equal));
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

/// Computed board for one window, kept in memory and mirrored to disk so a
/// freshly (re)started API serves data instantly instead of making the first
/// visitor sit through a multi-minute 429-throttled scan. The prewarm loop in
/// main.rs is the writer; /traders/top is a pure cache read for the standard
/// windows.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BoardEntry {
    pub updated_at: i64, // ms epoch of the compute that produced this board
    pub pool: usize,     // how many rows were captured (requests truncate down)
    pub traders: Vec<TopTrader>,
}

pub struct BoardCache {
    path: std::path::PathBuf,
    // Keyed by `board_key(days, rank, active)`.
    boards: Mutex<std::collections::HashMap<String, BoardEntry>>,
}

impl BoardCache {
    pub fn load(dir: &str) -> Self {
        let path = std::path::PathBuf::from(dir).join("boards.json");
        let boards = std::fs::read_to_string(&path).ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        Self { path, boards: Mutex::new(boards) }
    }
    pub fn get(&self, days: u32, rank: Rank, active: Active) -> Option<BoardEntry> {
        self.boards.lock().get(&board_key(days, rank, active)).cloned()
    }
    /// Every board currently held, decoded — the refresher walks this so any
    /// combination a visitor asked for once keeps getting refreshed.
    pub fn keys(&self) -> Vec<(u32, Rank, Active)> {
        self.boards.lock().keys().filter_map(|k| parse_board_key(k)).collect()
    }
    pub fn put(&self, days: u32, rank: Rank, active: Active, pool: usize, traders: Vec<TopTrader>) {
        let entry = BoardEntry {
            updated_at: chrono::Utc::now().timestamp_millis(),
            pool,
            traders,
        };
        let mut g = self.boards.lock();
        g.insert(board_key(days, rank, active), entry);
        if let Ok(s) = serde_json::to_string(&*g) {
            let _ = std::fs::write(&self.path, s);
        }
    }
}

pub async fn top_traders(
    hl: Arc<Client>,
    days: u32,
    min_per_day: f64,
    pool: usize,
    extra_addrs: Vec<String>,
) -> anyhow::Result<Vec<TopTrader>> {
    top_traders_with_progress(hl, days, min_per_day, pool, extra_addrs, None, Rank::Roi, Active::Day).await
}

pub async fn top_traders_with_progress(
    hl: Arc<Client>,
    days: u32,
    _min_per_day: f64,   // retired: liveness is the `active` gate (see parse_lb_ranked)
    pool: usize,
    extra_addrs: Vec<String>,
    progress: Option<Arc<ProgressTracker>>,
    rank: Rank,
    active: Active,
) -> anyhow::Result<Vec<TopTrader>> {
    let lb = hl.leaderboard().await.unwrap_or(Value::Null);
    // Whole-universe leaderboard, ranked by `rank` and already filtered by the
    // `active` gate. This IS the source of truth for the board — every row's
    // ROI/PnL/volume comes from here, so "top `pool`" means literally the top
    // `pool` of the scrape, not a sample of whatever we could scan.
    let ranked = parse_lb_ranked(&lb, window_for_days(days), rank, active);
    let lb_by_addr: std::collections::HashMap<String, LbRow> =
        ranked.iter().cloned().collect();
    let mut addrs: Vec<String> = ranked.into_iter().map(|(a, _)| a).collect();
    addrs.truncate(pool.max(1));
    // Seed wallets are always included on top of the ranked pool.
    for a in extra_addrs {
        if !addrs.contains(&a) { addrs.push(a); }
    }

    let cutoff_ms: i64 = chrono::Utc::now().timestamp_millis()
        - (days as i64) * 86_400_000;

    // Enrich only the top slice with per-fill stats (win%/sharpe/trades/coins).
    let enrich: std::collections::HashSet<String> =
        addrs.iter().take(ENRICH_CAP).cloned().collect();
    if let Some(p) = progress.as_ref() { p.start(days, enrich.len()); }
    let counter = Arc::new(AtomicUsize::new(0));

    // Hyperliquid /info rate-limits aggressive parallel scans; cap concurrency
    // so longer windows don't silently drop addresses to 429s. Rows that 429
    // here aren't dropped — they just keep their leaderboard-only stats.
    let scanned: Vec<(String, Vec<Fill>)> = stream::iter(enrich.iter().cloned())
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
        .buffer_unordered(2)
        .collect()
        .await;
    if let Some(p) = progress.as_ref() { p.finish(); }
    let fills_by_addr: std::collections::HashMap<String, Vec<Fill>> =
        scanned.into_iter().collect();

    // Build one row per selected address, straight from the leaderboard, and
    // layer per-fill colour on top where we managed to scan it.
    let mut out = Vec::with_capacity(addrs.len());
    for addr in &addrs {
        let row = lb_by_addr.get(addr).cloned().unwrap_or_default();
        let mut t = TopTrader {
            address: addr.clone(),
            roi: row.roi * 100.0,          // HL's official window ROI (percent)
            account_value: row.account_value,
            volume: row.vlm,
            pnl: row.pnl,
            win_rate: -1.0,                // "—" until/unless fills enrich it
            trades: 0,
            coins: Vec::new(),
            avg_trade_usd: 0.0,
            sharpe: 0.0,
            last_active: 0,                // 0 ⇒ UI shows "≤24h" (liveness gate)
        };
        if let Some(fills) = fills_by_addr.get(addr) {
            if !fills.is_empty() {
                let (vol, _pnl, wr, n, coins, sharpe, last) = score_fills(fills, cutoff_ms);
                if n > 0 {
                    t.win_rate = wr;
                    t.trades = n;
                    t.coins = coins;
                    t.sharpe = sharpe;
                    t.avg_trade_usd = vol / n as f64;
                    t.last_active = last;
                }
            }
        }
        out.push(t);
    }
    // Rank the board by the same metric we selected the cohort on.
    let metric = |t: &TopTrader| match rank { Rank::Roi => t.roi, Rank::Pnl => t.pnl, Rank::Volume => t.volume };
    out.sort_by(|a, b| metric(b).partial_cmp(&metric(a)).unwrap_or(std::cmp::Ordering::Equal));
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
    // Equity from the user's clearinghouse state, so ROI here matches the
    // board's pnl/equity definition.
    let account_value = state.get("marginSummary")
        .and_then(|m| m.get("accountValue"))
        .and_then(|x| x.as_str())
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.0);
    let roi = if account_value > 0.0 { pnl / account_value * 100.0 } else { 0.0 };
    Ok(serde_json::json!({
        "address": addr,
        "days": days,
        "summary": TopTrader {
            address: addr.to_string(),
            roi, account_value,
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // A miniature stats-CDN leaderboard: one row per archetype.
    fn fixture() -> Value {
        let row = |addr: &str, acct: &str, day: (&str, &str, &str), week: (&str, &str, &str)| json!({
            "ethAddress": addr, "accountValue": acct,
            "windowPerformances": [
                ["day",  {"pnl": day.0,  "roi": day.1,  "vlm": day.2}],
                ["week", {"pnl": week.0, "roi": week.1, "vlm": week.2}],
            ]
        });
        json!({"leaderboardRows": [
            // small account, huge ROI, traded today — the classic ROI-board row
            row("0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "2000",
                ("500", "0.30", "1000"), ("1800", "7.2", "9000")),
            // whale, low ROI, biggest PnL, traded today
            row("0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "90000000",
                ("100000", "0.001", "5000000"), ("5600000", "0.067", "22000000")),
            // whale, traded this week but NOT in the last 24h
            row("0xcccccccccccccccccccccccccccccccccccccccc", "77000000",
                ("0", "0", "0"), ("4800000", "0.066", "83000")),
            // holder: $1B, big PnL, zero volume in every window → never a trader
            row("0xdddddddddddddddddddddddddddddddddddddddd", "1100000000",
                ("43000000", "0.041", "0"), ("55000000", "0.051", "0")),
            // dust: $50 account, +100% — below the equity floor
            row("0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "50",
                ("50", "1.0", "100"), ("50", "1.0", "100")),
        ]})
    }

    fn addrs(v: Vec<(String, LbRow)>) -> Vec<char> {
        v.into_iter().map(|(a, _)| a.chars().nth(2).unwrap()).collect()
    }

    #[test]
    fn roi_board_is_the_old_behaviour() {
        let r = parse_lb_ranked(&fixture(), "week", Rank::Roi, Active::Day);
        // a (7.2) > b (0.067); c dropped (no 24h volume); d dropped (holder); e dropped (dust)
        assert_eq!(addrs(r), vec!['a', 'b']);
    }

    #[test]
    fn pnl_board_puts_the_whale_first_and_never_the_holder() {
        let r = parse_lb_ranked(&fixture(), "week", Rank::Pnl, Active::Day);
        assert_eq!(addrs(r), vec!['b', 'a']);
        let r = parse_lb_ranked(&fixture(), "week", Rank::Pnl, Active::Window);
        // window liveness admits c (traded this week), holder d still excluded
        assert_eq!(addrs(r), vec!['b', 'c', 'a']);
    }

    #[test]
    fn volume_board_sorts_by_window_volume() {
        let r = parse_lb_ranked(&fixture(), "week", Rank::Volume, Active::Window);
        assert_eq!(addrs(r), vec!['b', 'c', 'a']);
        let r = parse_lb_ranked(&fixture(), "day", Rank::Volume, Active::Day);
        assert_eq!(addrs(r), vec!['b', 'a']);
    }

    #[test]
    fn rank_and_active_parse_leniently_and_roundtrip() {
        assert_eq!(Rank::parse(""), Some(Rank::Roi));
        assert_eq!(Rank::parse("PnL"), Some(Rank::Pnl));
        assert_eq!(Rank::parse("vol"), Some(Rank::Volume));
        assert_eq!(Rank::parse("sharpe"), None);
        assert_eq!(Active::parse("24h"), Some(Active::Day));
        assert_eq!(Active::parse("window"), Some(Active::Window));
        assert_eq!(Active::parse("never"), None);
        for days in [1u32, 7, 30] {
            for rank in [Rank::Roi, Rank::Pnl, Rank::Volume] {
                for active in [Active::Day, Active::Window] {
                    let k = board_key(days, rank, active);
                    assert_eq!(parse_board_key(&k), Some((days, rank, active)), "{k}");
                }
            }
        }
        // legacy boards.json keys (bare day counts) are ignored, not misread
        assert_eq!(parse_board_key("7"), None);
        assert_eq!(parse_board_key("7:roi:24h:extra"), None);
    }

    #[test]
    fn board_cache_keys_are_independent_per_axis() {
        let dir = std::env::temp_dir().join(format!("hl-boards-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let c = BoardCache::load(dir.to_str().unwrap());
        c.put(7, Rank::Roi, Active::Day, 10, vec![]);
        assert!(c.get(7, Rank::Roi, Active::Day).is_some());
        assert!(c.get(7, Rank::Pnl, Active::Day).is_none());
        assert!(c.get(7, Rank::Roi, Active::Window).is_none());
        c.put(7, Rank::Pnl, Active::Window, 10, vec![]);
        let mut keys = c.keys(); keys.sort_by_key(|k| board_key(k.0, k.1, k.2));
        assert_eq!(keys, vec![(7, Rank::Pnl, Active::Window), (7, Rank::Roi, Active::Day)]);
        // survives a reload from disk
        let c2 = BoardCache::load(dir.to_str().unwrap());
        assert_eq!(c2.keys().len(), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
