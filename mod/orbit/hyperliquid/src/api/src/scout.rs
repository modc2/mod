// The scout — a background agent whose one job is finding the most
// profitable trader to copy, and proving it.
//
// Every SCOUT_EVERY it:
//   1. reads the cached 7d board (no scan — the prewarm loop keeps it warm),
//   2. keeps the rows a copier could actually use: measured, enough closes
//      to be evidence, a real basis (≥ $1k), a positive expected profit per
//      day (expect.rs) and a profit factor above 1,
//   3. takes the best CANDIDATES by expected $/day per $1k and BACKTESTS each
//      with $1,000 at 1, 7 and 30 days (backtest::run_windows — two upstream
//      calls per wallet for all three windows),
//   4. scores each pick by the LOWER of the two answers — the model's
//      expected $/day per $1k, and the median $/day the trusted backtest
//      windows actually paid — so a wallet only ranks high when its fills
//      AND its equity curve agree it makes money.
//
// The model alone is a forecast from fills; the backtest alone is one path
// of history that a single lucky day can carry. Taking the minimum is the
// cheapest honest way to demand both. The report keeps a run history so
// "who has the scout liked lately" is answerable without re-running.

use crate::backtest::run_windows;
use crate::expect::Expected;
use crate::strats_backtest::{trusted, Cell};
use crate::traders::{Active, BoardCache, Rank, TopTrader};
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::Duration;

/// The board window the scout reads.
pub const BOARD_DAYS: u32 = 7;
/// Horizons every candidate is backtested at.
pub const WINDOWS: [u32; 3] = [1, 7, 30];
/// The $ the backtest puts on each candidate — and the "per $1k" unit.
pub const CAPITAL: f64 = 1_000.0;
/// Wallets backtested per run. Two /info calls each; HL punishes bursts.
pub const CANDIDATES: usize = 12;
const CONCURRENCY: usize = 2;
/// First run waits for the prewarm loop to have a board to read.
const BOOT_DELAY: Duration = Duration::from_secs(150);
pub const SCOUT_EVERY: Duration = Duration::from_secs(15 * 60);
/// `?run=true` is public, so it can't re-run more often than this.
const MIN_RERUN_MS: i64 = 5 * 60 * 1000;
const HISTORY: usize = 48;

pub const METHOD: &str = "expected $/day per $1k copied = min(model, backtest). \
model = (winRateLo·avgWin − (1−winRateLo)·avgLoss) × closes/day, scaled by 1000/basis — the \
profit factor's halves priced at the win rate the sample can defend. backtest = median $/day \
of the trusted 1/7/30d replays of $1,000 riding the trader's book.";

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct ScoutCell {
    pub days: u32,
    /// No failed check and a non-dust basis — the only cells that count.
    pub trusted: bool,
    pub pnl: Option<f64>,
    /// pnl ÷ days on $1,000.
    pub per_day: Option<f64>,
    pub roi_pct: Option<f64>,
    pub max_drawdown_pct: Option<f64>,
    pub flags: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Pick {
    pub address: String,
    /// Position on the 7d board the scout read.
    pub board_rank: usize,
    pub roi: f64,
    pub equity: f64,
    pub closes: usize,
    pub win_rate: f64,
    pub profit_factor: f64,
    pub model: Expected,
    pub cells: Vec<ScoutCell>,
    /// Median $/day over trusted backtest windows, per $1k.
    pub backtest_per_day: Option<f64>,
    /// THE number: min(model per $1k, backtest per $1k). `None` when the
    /// backtest had no trusted window to agree or disagree with.
    pub expected_per_day: Option<f64>,
    pub trusted_windows: usize,
    pub green_windows: usize,
    /// "consistent" | "mixed" | "losing" | "unverified".
    pub verdict: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct RunLog {
    pub ts_ms: i64,
    pub eligible: usize,
    pub best: Option<String>,
    pub best_expected_per_day: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
pub struct Report {
    pub board_days: u32,
    pub windows: Vec<u32>,
    pub capital: f64,
    /// Rows on the board the scout read.
    pub scanned: usize,
    /// Rows that passed the copier gates.
    pub eligible: usize,
    pub picks: Vec<Pick>,
    pub updated_ms: i64,
    pub build_ms: u64,
    pub note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(default)]
struct Disk {
    report: Option<Report>,
    history: Vec<RunLog>,
}

pub struct Scout {
    path: std::path::PathBuf,
    disk: Mutex<Disk>,
    running: Mutex<bool>,
    next_ms: Mutex<i64>,
}

fn r2(x: f64) -> f64 { (x * 100.0).round() / 100.0 }

/// Can a copier use this row at all? Pure, so the gate is tested.
pub fn eligible(t: &TopTrader) -> bool {
    let Some(e) = &t.expected else { return false };
    !e.thin
        && e.per_1k.is_some_and(|x| x > 0.0)
        && (t.profit_factor > 1.0 || t.profit_factor < 0.0)
}

/// Fold a candidate's backtest cells into a pick. Pure.
pub fn judge(t: &TopTrader, board_rank: usize, cells: &[Cell]) -> Pick {
    let model = t.expected.clone().unwrap_or_default();
    let sc: Vec<ScoutCell> = cells.iter().map(|c| ScoutCell {
        days: c.days,
        trusted: trusted(c),
        pnl: c.pnl,
        per_day: c.pnl.map(|p| r2(p / c.days.max(1) as f64)),
        roi_pct: c.roi_pct,
        max_drawdown_pct: c.max_drawdown_pct,
        flags: c.flags.clone(),
    }).collect();
    let mut per: Vec<f64> = sc.iter().filter(|c| c.trusted).filter_map(|c| c.per_day).collect();
    per.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let bt = match per.len() {
        0 => None,
        n if n % 2 == 1 => Some(per[n / 2]),
        n => Some(r2((per[n / 2 - 1] + per[n / 2]) / 2.0)),
    };
    let trusted_windows = per.len();
    let green_windows = per.iter().filter(|x| **x > 0.0).count();
    let expected = match (model.per_1k, bt) {
        (Some(m), Some(b)) => Some(r2(m.min(b))),
        _ => None,
    };
    let verdict = match expected {
        None => "unverified",
        Some(x) if x <= 0.0 => "losing",
        Some(_) if green_windows == trusted_windows => "consistent",
        Some(_) => "mixed",
    }.to_string();
    Pick {
        address: t.address.clone(),
        board_rank,
        roi: t.roi,
        equity: t.account_value,
        closes: t.closes,
        win_rate: t.win_rate,
        profit_factor: t.profit_factor,
        model,
        cells: sc,
        backtest_per_day: bt,
        expected_per_day: expected,
        trusted_windows,
        green_windows,
        verdict,
    }
}

/// Best first: verified expected $/day, then the model alone for the rest.
pub fn rank(picks: &mut [Pick]) {
    let key = |p: &Pick| (
        p.expected_per_day.is_some(),
        p.expected_per_day.or(p.model.per_1k).unwrap_or(f64::NEG_INFINITY),
    );
    picks.sort_by(|a, b| {
        let (ka, kb) = (key(a), key(b));
        kb.0.cmp(&ka.0).then(kb.1.partial_cmp(&ka.1).unwrap_or(std::cmp::Ordering::Equal))
    });
}

impl Scout {
    pub fn load(dir: &str) -> Self {
        let path = std::path::PathBuf::from(dir).join("scout.json");
        let disk = std::fs::read_to_string(&path).ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        Self { path, disk: Mutex::new(disk), running: Mutex::new(false), next_ms: Mutex::new(0) }
    }

    pub fn snapshot(&self) -> serde_json::Value {
        let d = self.disk.lock();
        serde_json::json!({
            "method": METHOD,
            "running": *self.running.lock(),
            "next_run_ms": *self.next_ms.lock(),
            "every_ms": SCOUT_EVERY.as_millis() as u64,
            "report": d.report,
            "best": d.report.as_ref().and_then(|r| r.picks.first()),
            "history": d.history,
        })
    }

    fn last_ms(&self) -> i64 {
        self.disk.lock().report.as_ref().map_or(0, |r| r.updated_ms)
    }

    /// Kick a run in the background unless one is running or one finished
    /// under MIN_RERUN_MS ago. Returns whether it started.
    pub fn poke(self: &Arc<Self>, hl: Arc<crate::hl::Client>, boards: Arc<BoardCache>,
                syncs: Arc<crate::sync::SyncLog>) -> bool {
        let now = chrono::Utc::now().timestamp_millis();
        if *self.running.lock() || now - self.last_ms() < MIN_RERUN_MS { return false; }
        let me = self.clone();
        tokio::spawn(async move { me.run(hl, boards, syncs).await });
        true
    }

    /// The forever loop main.rs spawns.
    pub async fn forever(self: Arc<Self>, hl: Arc<crate::hl::Client>, boards: Arc<BoardCache>,
                         syncs: Arc<crate::sync::SyncLog>) {
        *self.next_ms.lock() = chrono::Utc::now().timestamp_millis() + BOOT_DELAY.as_millis() as i64;
        tokio::time::sleep(BOOT_DELAY).await;
        loop {
            self.run(hl.clone(), boards.clone(), syncs.clone()).await;
            *self.next_ms.lock() = chrono::Utc::now().timestamp_millis() + SCOUT_EVERY.as_millis() as i64;
            tokio::time::sleep(SCOUT_EVERY).await;
        }
    }

    pub async fn run(&self, hl: Arc<crate::hl::Client>, boards: Arc<BoardCache>,
                     syncs: Arc<crate::sync::SyncLog>) {
        use futures::stream::{self, StreamExt};
        {
            let mut r = self.running.lock();
            if *r { return; }
            *r = true;
        }
        let t0 = std::time::Instant::now();
        let mut board = boards.get(BOARD_DAYS, Rank::Roi, Active::Day)
            .map(|e| e.traders).unwrap_or_default();
        crate::expect::stamp(&mut board, BOARD_DAYS);
        let scanned = board.len();
        let mut pool: Vec<(usize, TopTrader)> = board.into_iter().enumerate()
            .filter(|(_, t)| eligible(t)).map(|(i, t)| (i + 1, t)).collect();
        let n_eligible = pool.len();
        let per_1k = |t: &TopTrader| t.expected.as_ref().and_then(|e| e.per_1k).unwrap_or(0.0);
        pool.sort_by(|a, b| per_1k(&b.1).partial_cmp(&per_1k(&a.1)).unwrap_or(std::cmp::Ordering::Equal));
        pool.truncate(CANDIDATES);

        let mut picks: Vec<Pick> = stream::iter(pool.into_iter().map(|(rank, t)| {
            let hl = hl.clone();
            async move {
                let cells: Vec<Cell> = run_windows(hl, &t.address, &WINDOWS, CAPITAL).await
                    .iter().map(Cell::from).collect();
                judge(&t, rank, &cells)
            }
        }))
        .buffer_unordered(CONCURRENCY)
        .collect()
        .await;
        rank(&mut picks);

        let now = chrono::Utc::now().timestamp_millis();
        let build_ms = t0.elapsed().as_millis() as u64;
        let note = if scanned == 0 { Some("the 7d board isn't warm yet — next run will have it".into()) }
            else if n_eligible == 0 { Some("no wallet on the board passed the copier gates this run".into()) }
            else { None };
        let best = picks.first().filter(|p| p.expected_per_day.is_some_and(|x| x > 0.0));
        let log = RunLog {
            ts_ms: now, eligible: n_eligible,
            best: best.map(|p| p.address.clone()),
            best_expected_per_day: best.and_then(|p| p.expected_per_day),
        };
        syncs.push(crate::sync::SyncEvent {
            ts_ms: now, kind: "scout".into(), key: format!("{BOARD_DAYS}d"), ok: true,
            rows: picks.len(), duration_ms: build_ms,
            note: match &log.best {
                Some(a) => format!("{n_eligible} eligible · best {a} at ${:.2}/day per $1k",
                                   log.best_expected_per_day.unwrap_or(0.0)),
                None => format!("{n_eligible} eligible · no verified profitable copy"),
            },
        });
        tracing::info!("scout: {scanned} scanned, {n_eligible} eligible, {} backtested in {build_ms}ms", picks.len());
        {
            let mut d = self.disk.lock();
            d.report = Some(Report {
                board_days: BOARD_DAYS, windows: WINDOWS.to_vec(), capital: CAPITAL,
                scanned, eligible: n_eligible, picks, updated_ms: now, build_ms, note,
            });
            d.history.insert(0, log);
            d.history.truncate(HISTORY);
            if let Ok(s) = serde_json::to_string(&*d) { let _ = std::fs::write(&self.path, s); }
        }
        *self.running.lock() = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cell(days: u32, pnl: Option<f64>, ok: bool) -> Cell {
        Cell {
            days, available: pnl.is_some(), ok, roi_pct: pnl.map(|p| p / 10.0), pnl,
            final_value: pnl.map(|p| 1000.0 + p), max_drawdown_pct: Some(1.0),
            realized_roi_pct: None, wiped: false, flags: vec![], note: None,
        }
    }
    fn trader(per_1k: Option<f64>) -> TopTrader {
        TopTrader {
            address: "0xa".into(), win_rate: 60.0, closes: 50, profit_factor: 1.5,
            expected: Some(Expected { per_1k, ..Default::default() }),
            ..Default::default()
        }
    }

    #[test]
    fn the_lower_of_model_and_backtest_wins() {
        // backtest per day: 1d 5, 7d 21/7=3, 30d 120/30=4 → median 4.
        let cells = vec![cell(1, Some(5.0), true), cell(7, Some(21.0), true), cell(30, Some(120.0), true)];
        let p = judge(&trader(Some(9.0)), 1, &cells);
        assert_eq!(p.backtest_per_day, Some(4.0));
        assert_eq!(p.expected_per_day, Some(4.0));
        assert_eq!(p.verdict, "consistent");
        let p = judge(&trader(Some(2.5)), 1, &cells);
        assert_eq!(p.expected_per_day, Some(2.5));
    }

    #[test]
    fn untrusted_windows_dont_vote() {
        let cells = vec![cell(1, Some(900.0), false), cell(7, Some(-7.0), true), cell(30, None, false)];
        let p = judge(&trader(Some(9.0)), 1, &cells);
        assert_eq!(p.trusted_windows, 1);
        assert_eq!(p.backtest_per_day, Some(-1.0));
        assert_eq!(p.verdict, "losing");
        let none = judge(&trader(Some(9.0)), 1, &[cell(1, None, false)]);
        assert_eq!(none.expected_per_day, None);
        assert_eq!(none.verdict, "unverified");
    }

    #[test]
    fn verified_picks_rank_above_model_only_ones() {
        let cells = vec![cell(7, Some(7.0), true)]; // $1/day
        let mut picks = vec![
            judge(&trader(Some(50.0)), 1, &[cell(7, None, false)]), // unverified, big model
            judge(&trader(Some(3.0)), 2, &cells),                    // verified $1
            judge(&trader(Some(9.0)), 3, &[cell(7, Some(35.0), true)]), // verified $5
        ];
        rank(&mut picks);
        let ranks: Vec<usize> = picks.iter().map(|p| p.board_rank).collect();
        assert_eq!(ranks, vec![3, 2, 1]);
    }

    #[test]
    fn gates_drop_thin_dust_and_losing_books() {
        assert!(eligible(&trader(Some(1.0))));
        assert!(!eligible(&trader(None)), "dust basis");
        assert!(!eligible(&trader(Some(-1.0))), "negative expectation");
        let mut thin = trader(Some(1.0));
        thin.expected.as_mut().unwrap().thin = true;
        assert!(!eligible(&thin));
        let mut pf = trader(Some(1.0));
        pf.profit_factor = 0.8;
        assert!(!eligible(&pf));
        let mut flawless = trader(Some(1.0));
        flawless.profit_factor = -1.0;
        assert!(eligible(&flawless), "no losing close is undefined, not bad");
    }
}
