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
    /// A coin scan stops early once it has enough matches — report the depth
    /// it actually reached instead of pretending it walked the whole budget.
    pub fn finish_at(&self, scanned: usize) {
        { let mut s = self.state.lock(); s.total = scanned; }
        self.finish();
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
pub const ENRICH_CAP: usize = 120;
/// Hard ceiling on how many top rows one request may ask to enrich. Past the
/// background deepener's reach every extra row is a cold, throttled fetch.
pub const ENRICH_MAX: usize = 400;
/// `pool` sentinel: the whole gated leaderboard, not a top-N slice. The
/// leaderboard CDN already prices every wallet (ROI / PnL / volume / equity),
/// so showing all of them costs nothing — only fill stats are rationed.
pub const ALL: usize = usize::MAX;
/// How many rows of a board survive to disk. Memory keeps whole boards
/// (thousands of rows); the persisted copy is the top slice, enough to answer
/// the first visitors after a restart while the refresher rebuilds the rest.
pub const PERSIST_CAP: usize = 600;

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
    #[serde(default)]
    pub all: bool,       // true ⇒ `traders` is the entire gated leaderboard
    pub traders: Vec<TopTrader>,
}

impl BoardEntry {
    /// Can this entry answer a request for `pool` rows (`ALL` = everything)?
    pub fn covers(&self, pool: usize) -> bool {
        if pool == ALL { self.all } else { self.all || self.pool >= pool }
    }
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
        let all = pool == ALL;
        let entry = BoardEntry {
            updated_at: chrono::Utc::now().timestamp_millis(),
            pool: if all { traders.len() } else { pool },
            all,
            traders,
        };
        let mut g = self.boards.lock();
        g.insert(board_key(days, rank, active), entry);
        // Disk gets the top slice only: a whole-leaderboard board is a few MB
        // per key and the refresher rewrites it every couple of minutes.
        let slim: std::collections::HashMap<&String, BoardEntry> = g.iter().map(|(k, e)| {
            let mut c = e.clone();
            if c.traders.len() > PERSIST_CAP {
                c.traders.truncate(PERSIST_CAP);
                c.pool = PERSIST_CAP;
                c.all = false;
            }
            (k, c)
        }).collect();
        if let Ok(s) = serde_json::to_string(&slim) {
            let _ = std::fs::write(&self.path, s);
        }
    }
}

/// One wallet's fill-derived stats for one window, as scraped from HL. The
/// leaderboard CDN knows ROI/PnL/volume for everyone but says nothing about
/// WHAT a wallet trades — that only comes from its fills, one throttled /info
/// call per wallet. This index is where those calls accumulate, so a request
/// that needs "the top 50 who trade ZEC" walks the ranked leaderboard against
/// memory and only pays for wallets nobody has looked at yet.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexEntry {
    pub scanned_at: i64,    // ms epoch of the fills fetch
    pub volume: f64,
    pub pnl: f64,
    pub win_rate: f64,      // -1 if no realised fills
    pub trades: usize,
    pub coins: Vec<String>, // every distinct coin traded in the window, most-traded first
    pub sharpe: f64,
    pub avg_trade_usd: f64,
    pub last_active: i64,
}

impl IndexEntry {
    pub fn is_fresh(&self, now_ms: i64) -> bool {
        now_ms - self.scanned_at <= INDEX_TTL_MS
    }
    /// True when this wallet traded at least one of `wanted` (uppercased).
    pub fn trades_any(&self, wanted: &[String]) -> bool {
        wanted.is_empty() || self.coins.iter().any(|c| wanted.iter().any(|w| w == &c.to_ascii_uppercase()))
    }
}

/// How long an index entry counts as current. Coins traded over a window
/// barely move minute to minute; 30 min keeps win%/sharpe honest without
/// re-scraping every wallet on every 60s board refresh.
pub const INDEX_TTL_MS: i64 = 30 * 60 * 1000;
/// Entries older than this are dropped when the index is written to disk.
const INDEX_EVICT_MS: i64 = 48 * 3_600_000;
/// Wallets per fills round-trip batch during a coin scan — small enough that
/// a popular coin (BTC) stops after one or two batches.
const COIN_CHUNK: usize = 40;
/// Deepest a single request will walk the ranked leaderboard looking for coin
/// matches before giving up and returning what it found.
pub const COIN_SCAN_CAP: usize = 600;

/// Normalise a user-supplied coin list: trimmed, uppercased, de-duplicated.
pub fn wanted_coins(raw: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for c in raw {
        let c = c.trim().to_ascii_uppercase();
        if !c.is_empty() && !out.contains(&c) { out.push(c); }
    }
    out
}

pub struct TraderIndex {
    path: std::path::PathBuf,
    // Keyed by `"{days}:{addr}"` — stats are window-scoped.
    entries: Mutex<std::collections::HashMap<String, IndexEntry>>,
}

impl TraderIndex {
    fn key(days: u32, addr: &str) -> String { format!("{days}:{}", addr.to_lowercase()) }

    pub fn load(dir: &str) -> Self {
        let path = std::path::PathBuf::from(dir).join("traderindex.json");
        let entries = std::fs::read_to_string(&path).ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        Self { path, entries: Mutex::new(entries) }
    }
    pub fn len(&self) -> usize { self.entries.lock().len() }
    pub fn get(&self, days: u32, addr: &str) -> Option<IndexEntry> {
        self.entries.lock().get(&Self::key(days, addr)).cloned()
    }
    pub fn is_fresh(&self, days: u32, addr: &str, now_ms: i64) -> bool {
        self.get(days, addr).map(|e| e.is_fresh(now_ms)).unwrap_or(false)
    }
    pub fn put(&self, days: u32, addr: &str, e: IndexEntry) {
        self.entries.lock().insert(Self::key(days, addr), e);
    }
    /// Persist, dropping entries nobody will trust again.
    pub fn save(&self) {
        let now = chrono::Utc::now().timestamp_millis();
        let snapshot = {
            let mut g = self.entries.lock();
            g.retain(|_, e| now - e.scanned_at <= INDEX_EVICT_MS);
            g.clone()
        };
        if let Ok(s) = serde_json::to_string(&snapshot) {
            let _ = std::fs::write(&self.path, s);
        }
    }
    /// Which of `addrs` still need a fills fetch for this window.
    pub fn stale(&self, days: u32, addrs: &[String], now_ms: i64) -> Vec<String> {
        addrs.iter().filter(|a| !self.is_fresh(days, a, now_ms)).cloned().collect()
    }

    /// Fetch fills for every wallet in `addrs` that isn't fresh in the index
    /// and score them into it. Failed fetches (HL 429 storms) keep whatever
    /// stale entry was there. `progress`, if given, is ticked per wallet
    /// starting from `base` so a multi-batch scan reports a running total.
    /// Returns how many wallets were actually fetched.
    pub async fn enrich(
        &self,
        hl: &Arc<Client>,
        days: u32,
        addrs: &[String],
        progress: Option<&Arc<ProgressTracker>>,
        base: usize,
    ) -> usize {
        let now = chrono::Utc::now().timestamp_millis();
        let todo = self.stale(days, addrs, now);
        if todo.is_empty() { return 0; }
        let cutoff_ms = now - (days as i64) * 86_400_000;
        let counter = Arc::new(AtomicUsize::new(0));
        // Hyperliquid /info rate-limits aggressive parallel scans; cap
        // concurrency so a deep walk doesn't collapse into 429s.
        let scanned: Vec<(String, Option<Vec<Fill>>)> = stream::iter(todo.iter().cloned())
            .map(|addr| {
                let hl = hl.clone();
                let counter = counter.clone();
                async move {
                    let r = match hl.user_fills_by_time(&addr, cutoff_ms).await {
                        Ok(v) => (addr.clone(), Some(parse_fills(&v))),
                        Err(e) => {
                            tracing::warn!("fills fetch failed for {addr}: {e}");
                            (addr.clone(), None)
                        }
                    };
                    let n = counter.fetch_add(1, Ordering::Relaxed) + 1;
                    if let Some(p) = progress { p.tick(base + n); }
                    r
                }
            })
            .buffer_unordered(2)
            .collect()
            .await;
        let stamp = chrono::Utc::now().timestamp_millis();
        for (addr, fills) in scanned {
            let Some(fills) = fills else { continue };
            let (vol, pnl, wr, n, coins, sharpe, last) = score_fills(&fills, cutoff_ms);
            self.put(days, &addr, IndexEntry {
                scanned_at: stamp,
                volume: vol, pnl, win_rate: wr, trades: n, coins, sharpe,
                avg_trade_usd: if n == 0 { 0.0 } else { vol / n as f64 },
                last_active: last,
            });
        }
        self.save();
        todo.len()
    }
}

/// What a scan returns: the board plus how it was found. `depth` is how far
/// down the ranked leaderboard the walk went; `candidates` is how many wallets
/// passed the leaderboard-side gates (equity floor + liveness) in total.
#[derive(Debug, Clone, Serialize)]
pub struct Board {
    pub traders: Vec<TopTrader>,
    pub depth: usize,
    pub candidates: usize,
    pub coins: Vec<String>,
}

/// Which rows a caller wants to keep. Every threshold is a floor; the
/// fill-derived ones (`sharpe`, `win`, `trades`) can only be met by rows that
/// have fill stats, so setting any of them implies `with_stats`.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct ScoreFilter {
    pub min_roi: Option<f64>,     // percent
    pub min_pnl: Option<f64>,     // USD
    pub min_volume: Option<f64>,  // USD
    pub min_equity: Option<f64>,  // USD account value
    pub min_sharpe: Option<f64>,
    pub min_win: Option<f64>,     // percent
    pub min_trades: Option<usize>,
    pub with_stats: bool,
}

impl ScoreFilter {
    pub fn is_empty(&self) -> bool { *self == ScoreFilter::default() }
    fn needs_stats(&self) -> bool {
        self.with_stats || self.min_sharpe.is_some() || self.min_win.is_some() || self.min_trades.is_some()
    }
    pub fn keeps(&self, t: &TopTrader) -> bool {
        let has_stats = t.win_rate >= 0.0;
        if self.needs_stats() && !has_stats { return false; }
        self.min_roi.map_or(true, |m| t.roi >= m)
            && self.min_pnl.map_or(true, |m| t.pnl >= m)
            && self.min_volume.map_or(true, |m| t.volume >= m)
            && self.min_equity.map_or(true, |m| t.account_value >= m)
            && self.min_sharpe.map_or(true, |m| t.sharpe >= m)
            && self.min_win.map_or(true, |m| t.win_rate >= m)
            && self.min_trades.map_or(true, |m| t.trades >= m)
    }
    pub fn apply(&self, rows: &mut Vec<TopTrader>) {
        if !self.is_empty() { rows.retain(|t| self.keeps(t)); }
    }
}

/// Column a board can be ordered by. The leaderboard-priced ones (`roi`,
/// `pnl`, `volume`, `equity`) rank every row; the fill-derived ones
/// (`sharpe`, `win_rate`, `trades`) sink rows without stats to the bottom
/// instead of letting an unmeasured 0 outrank a measured negative.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SortKey { Roi, Pnl, Volume, Equity, Sharpe, WinRate, Trades }

impl SortKey {
    pub fn parse(s: &str) -> Option<SortKey> {
        match s.trim().to_ascii_lowercase().as_str() {
            "" => None,
            "roi" => Some(SortKey::Roi),
            "pnl" => Some(SortKey::Pnl),
            "volume" | "vlm" | "vol" => Some(SortKey::Volume),
            "equity" | "account_value" => Some(SortKey::Equity),
            "sharpe" => Some(SortKey::Sharpe),
            "win" | "win_rate" | "winrate" => Some(SortKey::WinRate),
            "trades" => Some(SortKey::Trades),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            SortKey::Roi => "roi", SortKey::Pnl => "pnl", SortKey::Volume => "volume",
            SortKey::Equity => "equity", SortKey::Sharpe => "sharpe",
            SortKey::WinRate => "win_rate", SortKey::Trades => "trades",
        }
    }
    fn needs_stats(self) -> bool { matches!(self, SortKey::Sharpe | SortKey::WinRate | SortKey::Trades) }
    fn metric(self, t: &TopTrader) -> f64 {
        if self.needs_stats() && t.win_rate < 0.0 { return f64::NEG_INFINITY; }
        match self {
            SortKey::Roi => t.roi, SortKey::Pnl => t.pnl, SortKey::Volume => t.volume,
            SortKey::Equity => t.account_value, SortKey::Sharpe => t.sharpe,
            SortKey::WinRate => t.win_rate, SortKey::Trades => t.trades as f64,
        }
    }
    pub fn sort(self, rows: &mut [TopTrader]) {
        rows.sort_by(|a, b| self.metric(b).partial_cmp(&self.metric(a)).unwrap_or(std::cmp::Ordering::Equal));
    }
}

/// Rows that carry fill stats (win%/sharpe/trades) — the ones a query was
/// actually spent on.
pub fn enriched_count(rows: &[TopTrader]) -> usize {
    rows.iter().filter(|t| t.win_rate >= 0.0).count()
}

/// The ranked, gated leaderboard for one window — the walk order every scan
/// and the background deepener share.
pub async fn ranked_addrs(hl: &Arc<Client>, days: u32, rank: Rank, active: Active) -> Vec<String> {
    let lb = hl.leaderboard().await.unwrap_or(Value::Null);
    parse_lb_ranked(&lb, window_for_days(days), rank, active)
        .into_iter().map(|(a, _)| a).collect()
}

fn row_from(addr: &str, lb: &LbRow, e: Option<&IndexEntry>) -> TopTrader {
    let mut t = TopTrader {
        address: addr.to_string(),
        roi: lb.roi * 100.0,           // HL's official window ROI (percent)
        account_value: lb.account_value,
        volume: lb.vlm,
        pnl: lb.pnl,
        win_rate: -1.0,                // "—" until/unless fills enrich it
        trades: 0,
        coins: Vec::new(),
        avg_trade_usd: 0.0,
        sharpe: 0.0,
        last_active: 0,                // 0 ⇒ UI shows "≤24h" (liveness gate)
    };
    if let Some(e) = e {
        if e.trades > 0 {
            t.win_rate = e.win_rate;
            t.trades = e.trades;
            t.coins = e.coins.clone();
            t.sharpe = e.sharpe;
            t.avg_trade_usd = e.avg_trade_usd;
            t.last_active = e.last_active;
        }
    }
    t
}

pub async fn top_traders(
    hl: Arc<Client>,
    index: Arc<TraderIndex>,
    days: u32,
    pool: usize,
    extra_addrs: Vec<String>,
    coins: Vec<String>,
) -> anyhow::Result<Vec<TopTrader>> {
    top_traders_with_progress(hl, index, days, pool, extra_addrs, None, Rank::Roi, Active::Day, coins, ENRICH_CAP)
        .await.map(|b| b.traders)
}

pub async fn top_traders_with_progress(
    hl: Arc<Client>,
    index: Arc<TraderIndex>,
    days: u32,
    pool: usize,
    extra_addrs: Vec<String>,
    progress: Option<Arc<ProgressTracker>>,
    rank: Rank,
    active: Active,
    coins: Vec<String>,
    enrich: usize,
) -> anyhow::Result<Board> {
    // Fill stats are the only thing that costs a throttled /info call per
    // wallet, so they're rationed to the top `enrich` rows by `rank` — the
    // rest of the board is priced straight from the leaderboard CDN.
    let enrich = enrich.min(ENRICH_MAX);
    let lb = hl.leaderboard().await.unwrap_or(Value::Null);
    // Whole-universe leaderboard, ranked by `rank` and already filtered by the
    // `active` gate. This IS the source of truth for the board — every row's
    // ROI/PnL/volume comes from here, so "top `pool`" means literally the top
    // `pool` of the scrape that meet the requirements, not a sample of
    // whatever we could scan.
    let ranked = parse_lb_ranked(&lb, window_for_days(days), rank, active);
    let candidates = ranked.len();
    let lb_by_addr: std::collections::HashMap<String, LbRow> =
        ranked.iter().cloned().collect();
    let ranked: Vec<String> = ranked.into_iter().map(|(a, _)| a).collect();
    let wanted = wanted_coins(&coins);
    let pool = pool.max(1);

    let mut addrs: Vec<String>;
    let depth;
    if wanted.is_empty() {
        // Plain board: the top `pool` by metric (`ALL` = every gated wallet).
        // Enrich only the top slice with per-fill stats (win%/sharpe/trades/
        // coins); rows past the cap keep their real leaderboard ROI/PnL/volume
        // and show "—".
        addrs = ranked.iter().take(pool).cloned().collect();
        depth = addrs.len();
        let enrich: Vec<String> = addrs.iter().take(enrich).cloned().collect();
        if let Some(p) = progress.as_ref() { p.start(days, enrich.len()); }
        index.enrich(&hl, days, &enrich, progress.as_ref(), 0).await;
        if let Some(p) = progress.as_ref() { p.finish(); }
    } else {
        // Coin requirement: walk the ranked leaderboard in batches, scraping
        // fills for anyone the index hasn't seen lately, and keep wallets that
        // traded one of the wanted coins — until we hold `pool` of them or the
        // walk budget runs out. Every row on a coin board is enriched by
        // construction: we had to read its fills to know it qualifies.
        let budget = COIN_SCAN_CAP.min(ranked.len());
        if let Some(p) = progress.as_ref() { p.start(days, budget); }
        addrs = Vec::with_capacity(pool);
        let mut walked = 0usize;
        for chunk in ranked[..budget].chunks(COIN_CHUNK) {
            index.enrich(&hl, days, chunk, progress.as_ref(), walked).await;
            for a in chunk {
                walked += 1;
                if index.get(days, a).map(|e| e.trades > 0 && e.trades_any(&wanted)).unwrap_or(false) {
                    addrs.push(a.clone());
                    if addrs.len() >= pool { break; }
                }
            }
            if let Some(p) = progress.as_ref() { p.tick(walked); }
            if addrs.len() >= pool { break; }
        }
        depth = walked;
        if let Some(p) = progress.as_ref() { p.finish_at(walked); }
    }

    // Seed wallets are always included on top of the ranked pool, scraped
    // even when they'd fall outside the enrichment cap.
    let seeds: Vec<String> = extra_addrs.into_iter()
        .filter(|a| !addrs.contains(a)).collect();
    if !seeds.is_empty() {
        index.enrich(&hl, days, &seeds, None, 0).await;
        addrs.extend(seeds);
    }

    // Build one row per selected address, straight from the leaderboard, and
    // layer per-fill colour on top where the index has it.
    let mut out: Vec<TopTrader> = addrs.iter().map(|addr| {
        let row = lb_by_addr.get(addr).cloned().unwrap_or_default();
        row_from(addr, &row, index.get(days, addr).as_ref())
    }).collect();
    // Rank the board by the same metric we selected the cohort on.
    let metric = |t: &TopTrader| match rank { Rank::Roi => t.roi, Rank::Pnl => t.pnl, Rank::Volume => t.volume };
    out.sort_by(|a, b| metric(b).partial_cmp(&metric(a)).unwrap_or(std::cmp::Ordering::Equal));
    Ok(Board { traders: out, depth, candidates, coins: wanted })
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

    fn entry(coins: &[&str], age_ms: i64) -> IndexEntry {
        IndexEntry {
            scanned_at: chrono::Utc::now().timestamp_millis() - age_ms,
            volume: 1.0, pnl: 0.0, win_rate: 50.0, trades: 3,
            coins: coins.iter().map(|c| c.to_string()).collect(),
            sharpe: 0.0, avg_trade_usd: 1.0, last_active: 0,
        }
    }

    #[test]
    fn wanted_coins_normalises() {
        let w = wanted_coins(&["zec".into(), " ETH ".into(), "".into(), "ZEC".into()]);
        assert_eq!(w, vec!["ZEC", "ETH"]);
        assert!(wanted_coins(&[]).is_empty());
    }

    #[test]
    fn coin_match_is_any_of_and_case_insensitive() {
        let e = entry(&["ETH", "xyz:HYUNDAI"], 0);
        assert!(e.trades_any(&wanted_coins(&["eth".into()])));
        assert!(e.trades_any(&wanted_coins(&["ZEC".into(), "ETH".into()])));
        assert!(e.trades_any(&wanted_coins(&["xyz:hyundai".into()])));
        assert!(!e.trades_any(&wanted_coins(&["ZEC".into()])));
        // no requirement → everyone qualifies
        assert!(e.trades_any(&[]));
    }

    #[test]
    fn index_freshness_and_persistence() {
        let dir = std::env::temp_dir().join(format!("hl-index-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let ix = TraderIndex::load(dir.to_str().unwrap());
        let now = chrono::Utc::now().timestamp_millis();
        let a = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA".to_string();
        let b = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".to_string();
        ix.put(7, &a, entry(&["ZEC"], 0));
        ix.put(7, &b, entry(&["BTC"], INDEX_TTL_MS + 1));
        // keys are case-insensitive and window-scoped
        assert!(ix.get(7, &a.to_lowercase()).is_some());
        assert!(ix.get(1, &a).is_none());
        assert!(ix.is_fresh(7, &a, now));
        assert!(!ix.is_fresh(7, &b, now));
        assert_eq!(ix.stale(7, &[a.clone(), b.clone()], now), vec![b.clone()]);
        // survives a save/reload; a stale-but-recent entry is kept (fallback
        // when a refetch 429s), an ancient one is evicted
        ix.put(7, "0xcccccccccccccccccccccccccccccccccccccccc", entry(&["SOL"], INDEX_EVICT_MS + 1));
        ix.save();
        let ix2 = TraderIndex::load(dir.to_str().unwrap());
        assert_eq!(ix2.len(), 2);
        assert!(ix2.get(7, &b).is_some());
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn tt(roi: f64, pnl: f64, equity: f64, stats: Option<(f64, f64, usize)>) -> TopTrader {
        let (win_rate, sharpe, trades) = stats.unwrap_or((-1.0, 0.0, 0));
        TopTrader {
            address: format!("0x{roi}"), roi, account_value: equity, volume: pnl.abs() * 10.0, pnl,
            win_rate, trades, coins: vec![], avg_trade_usd: 0.0, sharpe, last_active: 0,
        }
    }

    #[test]
    fn score_filter_is_a_set_of_floors_and_stat_floors_imply_stats() {
        let rows = vec![
            tt(50.0, 5000.0, 20_000.0, Some((60.0, 1.8, 40))),   // measured, strong
            tt(80.0, 800.0, 1_500.0, None),                      // unmeasured, high roi
            tt(-10.0, -900.0, 90_000.0, Some((30.0, -0.5, 12))), // measured, losing
        ];
        let mut f = ScoreFilter::default();
        assert!(f.is_empty());
        let mut r = rows.clone(); f.apply(&mut r); assert_eq!(r.len(), 3);
        f.min_roi = Some(0.0);
        let mut r = rows.clone(); f.apply(&mut r); assert_eq!(r.len(), 2);
        // a sharpe floor can only be met by rows that were measured
        f = ScoreFilter { min_sharpe: Some(-1.0), ..Default::default() };
        let mut r = rows.clone(); f.apply(&mut r);
        assert_eq!(r.iter().map(|t| t.roi as i64).collect::<Vec<_>>(), vec![50, -10]);
        f = ScoreFilter { with_stats: true, min_equity: Some(50_000.0), ..Default::default() };
        let mut r = rows.clone(); f.apply(&mut r);
        assert_eq!(r.len(), 1); assert_eq!(r[0].roi, -10.0);
        f = ScoreFilter { min_win: Some(50.0), min_trades: Some(50), ..Default::default() };
        let mut r = rows.clone(); f.apply(&mut r); assert!(r.is_empty());
        assert_eq!(enriched_count(&rows), 2);
    }

    #[test]
    fn sort_keys_parse_and_unmeasured_rows_sink_on_stat_sorts() {
        assert_eq!(SortKey::parse("sharpe"), Some(SortKey::Sharpe));
        assert_eq!(SortKey::parse("win"), Some(SortKey::WinRate));
        assert_eq!(SortKey::parse("account_value"), Some(SortKey::Equity));
        assert_eq!(SortKey::parse(""), None);
        assert_eq!(SortKey::parse("luck"), None);
        let mut rows = vec![
            tt(80.0, 800.0, 1_500.0, None),                      // unmeasured (sharpe field is 0)
            tt(-10.0, -900.0, 90_000.0, Some((30.0, -0.5, 12))), // measured negative sharpe
            tt(50.0, 5000.0, 20_000.0, Some((60.0, 1.8, 40))),
        ];
        SortKey::Sharpe.sort(&mut rows);
        assert_eq!(rows.iter().map(|t| t.roi as i64).collect::<Vec<_>>(), vec![50, -10, 80]);
        SortKey::Roi.sort(&mut rows);
        assert_eq!(rows.iter().map(|t| t.roi as i64).collect::<Vec<_>>(), vec![80, 50, -10]);
        SortKey::Equity.sort(&mut rows);
        assert_eq!(rows[0].account_value, 90_000.0);
    }

    #[test]
    fn whole_boards_stay_in_memory_but_only_a_slice_hits_disk() {
        let dir = std::env::temp_dir().join(format!("hl-boards-all-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let c = BoardCache::load(dir.to_str().unwrap());
        let rows: Vec<TopTrader> = (0..(PERSIST_CAP + 25)).map(|i| tt(i as f64, 0.0, 1.0, None)).collect();
        c.put(7, Rank::Roi, Active::Day, ALL, rows);
        let e = c.get(7, Rank::Roi, Active::Day).unwrap();
        assert!(e.all && e.traders.len() == PERSIST_CAP + 25 && e.pool == PERSIST_CAP + 25);
        assert!(e.covers(ALL) && e.covers(50) && e.covers(PERSIST_CAP + 25));
        // reload from disk: the top slice, no longer claiming to be everyone
        let c2 = BoardCache::load(dir.to_str().unwrap());
        let e2 = c2.get(7, Rank::Roi, Active::Day).unwrap();
        assert!(!e2.all && e2.traders.len() == PERSIST_CAP && e2.pool == PERSIST_CAP);
        assert!(e2.covers(PERSIST_CAP) && !e2.covers(ALL) && !e2.covers(PERSIST_CAP + 1));
        // a top-N board never answers an `all` request
        c.put(1, Rank::Roi, Active::Day, 150, vec![]);
        assert!(!c.get(1, Rank::Roi, Active::Day).unwrap().covers(ALL));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rows_only_take_fill_stats_from_entries_with_trades() {
        let lb = LbRow { roi: 0.05, pnl: 10.0, vlm: 100.0, day_vlm: 1.0, account_value: 5000.0 };
        let t = row_from("0xa", &lb, None);
        assert_eq!(t.roi, 5.0); assert_eq!(t.win_rate, -1.0); assert!(t.coins.is_empty());
        let t = row_from("0xa", &lb, Some(&entry(&["ETH"], 0)));
        assert_eq!(t.trades, 3); assert_eq!(t.coins, vec!["ETH"]);
        let mut empty = entry(&[], 0); empty.trades = 0;
        let t = row_from("0xa", &lb, Some(&empty));
        assert_eq!(t.win_rate, -1.0);
    }
}
