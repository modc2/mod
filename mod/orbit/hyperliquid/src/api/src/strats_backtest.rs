// Backtest the whole strats board at several horizons in one answer.
//
// The question: "if I had put $N on each strat 1, 3, 7, 14 and 30 days ago,
// where would each be now — and which numbers can I trust?" Every board row
// (copy-a-trader AND vault: a vault is an HL account with its own portfolio
// curve, and a depositor rides it proportionally — the same equity model)
// goes through `backtest::run_windows`, which spends ONE portfolio call and
// ONE fills call per strat however many windows are asked for.
//
// Nothing new is invented here: each cell is the trader-page backtest,
// compacted. Its data checks travel with it as `flags`, and only `ok`
// cells (no failed check) count toward the per-window summary and leaders.
//
// Cost control: rows run at a small concurrency (HL's /info punishes
// bursts), and whole reports are cached for REPORT_TTL keyed by
// (capital, windows, pools). One build at a time — a second caller waits on
// the first and then reads its cache instead of doubling the upstream load.

use crate::backtest::{run_windows, Backtest};
use serde::Serialize;
use std::collections::HashMap;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};

/// The horizons asked for when the caller names none.
pub const DEFAULT_WINDOWS: [u32; 5] = [1, 3, 7, 14, 30];
/// More windows than this is a different question (use the trader page).
pub const MAX_WINDOWS: usize = 8;
/// Strats backtested at once. The board's own curve fan-out uses the same
/// order of magnitude; higher trips HL's 429s on a cold cache.
const CONCURRENCY: usize = 2;
/// Breather before re-asking for the wallets HL refused on the first pass.
const RETRY_PAUSE: Duration = Duration::from_secs(5);
/// How `backtest::run_windows` words an upstream refusal.
const REFUSED: &str = "hyperliquid would not answer";
/// A finished report is reused this long. Portfolio samples move on a
/// minutes grid, so a fresher rebuild would mostly re-spend calls.
const REPORT_TTL: Duration = Duration::from_secs(600);

/// One strat at one horizon — the trader-page backtest, compacted.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Cell {
    pub days: u32,
    /// Something to replay at all.
    pub available: bool,
    /// No data check failed — the only cells the summary counts.
    pub ok: bool,
    /// Equity model: your $N riding the book, realised + unrealised.
    pub roi_pct: Option<f64>,
    pub pnl: Option<f64>,
    pub final_value: Option<f64>,
    pub max_drawdown_pct: Option<f64>,
    /// Realised-only fills mirror, for the gap the checks already name.
    pub realized_roi_pct: Option<f64>,
    pub wiped: bool,
    /// Every non-pass check as "name:status" (e.g. "coverage:fail") — the
    /// full sentences live on the trader page's backtest.
    pub flags: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

impl From<&Backtest> for Cell {
    fn from(b: &Backtest) -> Cell {
        let some = |x: f64| if b.available { Some(x) } else { None };
        Cell {
            days: b.days,
            available: b.available,
            ok: b.ok,
            roi_pct: some(b.roi_pct),
            pnl: some(b.pnl),
            final_value: some(b.final_value),
            max_drawdown_pct: some(b.max_drawdown_pct),
            realized_roi_pct: b.mirror.as_ref().filter(|_| b.available).map(|m| m.roi_pct),
            wiped: b.wiped,
            flags: b.checks.iter().filter(|c| c.status != "pass")
                .map(|c| format!("{}:{}", c.name, c.status)).collect(),
            note: b.note.clone(),
        }
    }
}

/// One board row with a cell per window, in window order.
#[derive(Debug, Clone, Serialize)]
pub struct StratBacktest {
    /// "trader" | "vault" — same as the board.
    pub kind: &'static str,
    pub id: String,
    pub name: String,
    pub by: String,
    /// Vault TVL / trader equity, from the board.
    pub strat_capital: f64,
    pub rec_score: Option<f64>,
    pub cells: Vec<Cell>,
    /// Trusted windows that ended in the green — the default sort key.
    pub green_windows: usize,
}

/// Trusted windows that ended in the green.
fn green(cells: &[Cell]) -> usize {
    cells.iter().filter(|c| c.ok && c.roi_pct.unwrap_or(0.0) > 0.0).count()
}

/// One horizon across the whole board.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct WindowSummary {
    pub days: u32,
    /// Rows with anything to replay.
    pub tested: usize,
    /// Rows whose checks all passed or only warned.
    pub trusted: usize,
    /// Trusted rows that made money.
    pub in_green: usize,
    /// Over trusted rows only.
    pub median_roi_pct: Option<f64>,
    pub mean_roi_pct: Option<f64>,
    /// Best trusted row at this horizon.
    pub best_id: Option<String>,
    pub best_name: Option<String>,
    pub best_roi_pct: Option<f64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Report {
    pub capital: f64,
    pub windows: Vec<u32>,
    pub rows: Vec<StratBacktest>,
    pub summary: Vec<WindowSummary>,
    pub updated_ms: i64,
    /// Wall time the build took — the cold-cache price of this report.
    pub build_ms: u64,
    /// True when this answer came out of the report cache.
    pub cached: bool,
}

/// `"1,3,7"` → `[1, 3, 7]`: each clamped to 1..=90, deduped, ascending,
/// at most MAX_WINDOWS. Empty / unparseable → DEFAULT_WINDOWS.
pub fn parse_windows(raw: Option<&str>) -> Vec<u32> {
    let mut w: Vec<u32> = raw.unwrap_or("")
        .split(',')
        .filter_map(|s| s.trim().parse::<u32>().ok())
        .map(|d| d.clamp(1, 90))
        .collect();
    w.sort_unstable();
    w.dedup();
    w.truncate(MAX_WINDOWS);
    if w.is_empty() { DEFAULT_WINDOWS.to_vec() } else { w }
}

fn round2(x: f64) -> f64 { (x * 100.0).round() / 100.0 }

/// Per-window roll-up over trusted cells. Pure, so the arithmetic is tested.
pub fn summarize(rows: &[StratBacktest], windows: &[u32]) -> Vec<WindowSummary> {
    windows.iter().enumerate().map(|(i, d)| {
        let cells: Vec<(&StratBacktest, &Cell)> = rows.iter()
            .filter_map(|r| r.cells.get(i).map(|c| (r, c))).collect();
        let tested = cells.iter().filter(|(_, c)| c.available).count();
        let trusted: Vec<(&StratBacktest, f64)> = cells.iter()
            .filter(|(_, c)| c.available && c.ok)
            .filter_map(|(r, c)| c.roi_pct.map(|x| (*r, x)))
            .collect();
        let mut rois: Vec<f64> = trusted.iter().map(|(_, x)| *x).collect();
        rois.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let median = match rois.len() {
            0 => None,
            n if n % 2 == 1 => Some(rois[n / 2]),
            n => Some((rois[n / 2 - 1] + rois[n / 2]) / 2.0),
        };
        let mean = if rois.is_empty() { None } else { Some(rois.iter().sum::<f64>() / rois.len() as f64) };
        let best = trusted.iter()
            .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
        WindowSummary {
            days: *d,
            tested,
            trusted: trusted.len(),
            in_green: trusted.iter().filter(|(_, x)| *x > 0.0).count(),
            median_roi_pct: median.map(round2),
            mean_roi_pct: mean.map(round2),
            best_id: best.map(|(r, _)| r.id.clone()),
            best_name: best.map(|(r, _)| r.name.clone()),
            best_roi_pct: best.map(|(_, x)| *x),
        }
    }).collect()
}

/// Most trusted green windows first, then the longest window's ROI — a strat
/// up at every horizon outranks one that spiked once.
pub fn rank(rows: &mut [StratBacktest]) {
    let longest = |r: &StratBacktest| r.cells.last()
        .filter(|c| c.ok).and_then(|c| c.roi_pct).unwrap_or(f64::NEG_INFINITY);
    rows.sort_by(|a, b| b.green_windows.cmp(&a.green_windows)
        .then(longest(b).partial_cmp(&longest(a)).unwrap_or(std::cmp::Ordering::Equal)));
}

type CacheMap = HashMap<String, (Instant, Arc<Report>)>;
fn cache() -> &'static tokio::sync::Mutex<CacheMap> {
    static C: OnceLock<tokio::sync::Mutex<CacheMap>> = OnceLock::new();
    C.get_or_init(|| tokio::sync::Mutex::new(HashMap::new()))
}

/// Backtest every board row at every window. `refresh` skips the cache.
pub async fn report(
    s: &crate::AppState,
    capital: f64,
    windows: Vec<u32>,
    vault_pool: usize,
    trader_pool: usize,
    refresh: bool,
) -> Report {
    use futures::stream::{self, StreamExt};
    let key = format!("{capital}|{windows:?}|{vault_pool}|{trader_pool}");
    // Held across the build on purpose: single-flight.
    let mut guard = cache().lock().await;
    if !refresh {
        if let Some((at, r)) = guard.get(&key) {
            if at.elapsed() < REPORT_TTL {
                return Report { cached: true, ..(**r).clone() };
            }
        }
    }
    let t0 = Instant::now();
    let board = crate::strats_board::board(s, vault_pool, trader_pool, None).await;
    let hl = s.hl.clone();
    let mut rows: Vec<StratBacktest> = stream::iter(board.rows.into_iter().map(|row| {
        let hl = hl.clone();
        let windows = windows.clone();
        async move {
            let cells: Vec<Cell> = run_windows(hl, &row.id, &windows, capital).await
                .iter().map(Cell::from).collect();
            let green_windows = green(&cells);
            StratBacktest {
                kind: row.kind,
                id: row.id,
                name: row.name,
                by: row.by,
                strat_capital: row.capital,
                rec_score: row.rec_score,
                cells,
                green_windows,
            }
        }
    }))
    .buffer_unordered(CONCURRENCY)
    .collect()
    .await;
    // Rows HL refused (429 / timeout) get one slower, sequential retry after
    // a breather — the alternative is a board full of "—" for wallets that
    // have perfectly good data, which reads as "these strats are broken".
    let refused = |r: &StratBacktest| r.cells.iter()
        .any(|c| c.note.as_deref().is_some_and(|n| n.starts_with(REFUSED)));
    if rows.iter().any(refused) {
        tokio::time::sleep(RETRY_PAUSE).await;
        for r in rows.iter_mut().filter(|r| refused(r)) {
            r.cells = run_windows(hl.clone(), &r.id, &windows, capital).await
                .iter().map(Cell::from).collect();
            r.green_windows = green(&r.cells);
        }
    }
    rank(&mut rows);
    let summary = summarize(&rows, &windows);
    let build_ms = t0.elapsed().as_millis() as u64;
    s.syncs.push(crate::sync::SyncEvent {
        ts_ms: chrono::Utc::now().timestamp_millis(),
        kind: "strats".into(),
        key: "backtest".into(),
        ok: true,
        rows: rows.len(),
        duration_ms: build_ms,
        note: format!("backtested {} strats × {:?}d at ${capital:.0}", rows.len(), windows),
    });
    let r = Report {
        capital,
        windows,
        rows,
        summary,
        updated_ms: chrono::Utc::now().timestamp_millis(),
        build_ms,
        cached: false,
    };
    guard.retain(|_, (at, _)| at.elapsed() < REPORT_TTL);
    guard.insert(key, (Instant::now(), Arc::new(r.clone())));
    r
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cell(days: u32, roi: Option<f64>, ok: bool) -> Cell {
        Cell {
            days, available: roi.is_some(), ok, roi_pct: roi, pnl: roi.map(|r| r * 10.0),
            final_value: roi.map(|r| 1000.0 + r * 10.0), max_drawdown_pct: Some(0.0),
            realized_roi_pct: None, wiped: false, flags: vec![], note: None,
        }
    }
    fn row(id: &str, cells: Vec<Cell>) -> StratBacktest {
        let green_windows = cells.iter().filter(|c| c.ok && c.roi_pct.unwrap_or(0.0) > 0.0).count();
        StratBacktest {
            kind: "trader", id: id.into(), name: id.into(), by: id.into(),
            strat_capital: 1e5, rec_score: None, cells, green_windows,
        }
    }

    #[test]
    fn windows_parse_clamp_dedupe_and_default() {
        assert_eq!(parse_windows(None), vec![1, 3, 7, 14, 30]);
        assert_eq!(parse_windows(Some("")), vec![1, 3, 7, 14, 30]);
        assert_eq!(parse_windows(Some("30, 7,7,0,999,x")), vec![1, 7, 30, 90]);
        assert_eq!(parse_windows(Some("1,2,3,4,5,6,7,8,9,10")).len(), MAX_WINDOWS);
    }

    #[test]
    fn summary_counts_only_trusted_cells() {
        let rows = vec![
            row("a", vec![cell(1, Some(10.0), true), cell(7, Some(-5.0), true)]),
            row("b", vec![cell(1, Some(500.0), false), cell(7, Some(2.0), true)]),
            row("c", vec![cell(1, None, false), cell(7, Some(4.0), true)]),
        ];
        let s = summarize(&rows, &[1, 7]);
        // 1d: b failed a check (its 500% must not win), c had nothing.
        assert_eq!((s[0].tested, s[0].trusted, s[0].in_green), (2, 1, 1));
        assert_eq!(s[0].best_id.as_deref(), Some("a"));
        assert_eq!(s[0].median_roi_pct, Some(10.0));
        // 7d: three trusted, median of [-5, 2, 4] is 2, mean 0.33.
        assert_eq!((s[1].trusted, s[1].in_green), (3, 2));
        assert_eq!(s[1].median_roi_pct, Some(2.0));
        assert_eq!(s[1].mean_roi_pct, Some(0.33));
        assert_eq!(s[1].best_id.as_deref(), Some("c"));
    }

    #[test]
    fn empty_window_summarizes_to_none_not_zero() {
        let s = summarize(&[row("a", vec![cell(1, None, false)])], &[1]);
        assert_eq!(s[0].trusted, 0);
        assert_eq!(s[0].median_roi_pct, None);
        assert_eq!(s[0].best_id, None);
    }

    #[test]
    fn rank_prefers_consistency_then_longest_window() {
        let mut rows = vec![
            row("spike", vec![cell(1, Some(900.0), true), cell(30, Some(-10.0), true)]),
            row("steady", vec![cell(1, Some(1.0), true), cell(30, Some(5.0), true)]),
            row("steadier", vec![cell(1, Some(1.0), true), cell(30, Some(8.0), true)]),
            row("broken", vec![cell(1, None, false), cell(30, None, false)]),
        ];
        rank(&mut rows);
        let ids: Vec<&str> = rows.iter().map(|r| r.id.as_str()).collect();
        assert_eq!(ids, vec!["steadier", "steady", "spike", "broken"]);
    }

    #[test]
    fn a_cell_without_history_carries_no_numbers() {
        let b = Backtest { days: 7, available: false, ok: false, roi_pct: 0.0, ..Default::default() };
        let c = Cell::from(&b);
        assert_eq!(c.roi_pct, None);
        assert_eq!(c.final_value, None);
    }
}
