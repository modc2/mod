//! Performance statistics — the single definition of "how good is this book".
//!
//! Everything that scores a wallet (the trader board, `/trader/:addr/analyze`,
//! index leg performance) goes through [`score`]. Before this module those
//! three sites each carried their own copy of the arithmetic, and each copy
//! had the same four defects:
//!
//! 1. **The win rate's denominator was invisible.** It counts only fills that
//!    realise PnL, but the tile beside it showed *all* fills. "100% · 16
//!    trades" really meant "8 of 8 closes, plus 8 opens you can't see".
//! 2. **Wins were decided gross, PnL was reported net.** A close that made
//!    $0.16 and paid $0.18 of fees counted as a win while losing money.
//! 3. **Sharpe divided by days that had fills, not days in the window.** Two
//!    consecutive green days scored 13.37 — a number with no fourth digit of
//!    meaning, printed to two decimals next to a genuine one.
//! 4. **A ratio was published without its sample size.** 8/8 outranked
//!    180/200 on every board.
//!
//! The fix is not to hide the numbers; it is to make each one carry the
//! evidence that justifies it. [`PerfStats`] therefore reports `closes`
//! alongside `win_rate`, `sharpe_days` alongside `sharpe`, and a Wilson lower
//! bound (`win_rate_lo`) that is what you should rank and filter on: it is the
//! win rate you can defend at 95% confidence given how many closes you have
//! actually seen. 8/8 defends 67.6%. 180/200 defends 85.0%. The second book is
//! the better book, and only the lower bound says so.

use crate::hl::Fill;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const DAY_MS: i64 = 86_400_000;

/// Closes needed before a win rate is treated as measured rather than
/// anecdotal. Ten is where the Wilson bound stops being dominated by the
/// prior: at 10/10 you can defend 72%, at 3/3 only 44%.
pub const MIN_CLOSES: usize = 10;

/// Active days needed before a Sharpe ratio is worth printing. Below this the
/// standard deviation is an artifact of the sample, not of the strategy.
pub const MIN_SHARPE_DAYS: usize = 7;

/// 95% two-sided normal quantile, for the Wilson interval.
const Z: f64 = 1.959_963_984_540_054;

/// Trading days per year, for annualising a daily Sharpe. Crypto never closes.
const TRADING_DAYS: f64 = 365.0;

/// How much the sample supports the ratio printed on top of it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Confidence {
    /// No realised closes at all — there is nothing to take a ratio of.
    None,
    /// Fewer than [`MIN_CLOSES`]. Show the fraction, never the bare percent.
    Low,
    /// Enough closes that the point estimate means something.
    Measured,
}

impl Default for Confidence {
    fn default() -> Self { Confidence::None }
}

/// One window of trading, scored. Every ratio here is published next to the
/// count it was computed from.
///
/// `#[serde(default)]` at the container level is load-bearing: the trader
/// index and the board snapshots are JSON on disk, written by whatever build
/// last ran. A cache from before this struct existed still decodes — the
/// fields it never heard of come back zeroed and get overwritten by the next
/// scan, instead of poisoning the whole file into `unwrap_or_default()`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct PerfStats {
    // ── flow ──
    /// USD notional traded (Σ px·sz over every fill).
    pub volume: f64,
    /// Net PnL: Σ(closedPnl) − Σ(fee). This is what the wallet actually kept.
    pub pnl: f64,
    /// Σ(closedPnl), before fees — HL's own convention, kept for reconciliation.
    pub gross_pnl: f64,
    /// Σ(fee). `gross_pnl - fees == pnl`, always.
    pub fees: f64,

    // ── counts ──
    /// Every fill in the window, opens included. The "trades" tile.
    pub trades: usize,
    /// Fills that realised PnL. **This is the win-rate denominator.**
    pub closes: usize,
    /// Closes that kept money after fees.
    pub wins: usize,
    /// Closes that lost money after fees.
    pub losses: usize,
    /// Closes that came out exactly flat net of fees (neither win nor loss).
    pub scratches: usize,

    // ── ratios, each with its evidence ──
    /// `wins / closes` as a percent, **net of fees**. `-1.0` when `closes == 0`
    /// — the historical sentinel for "unmeasurable", preserved so existing
    /// callers and cached boards keep working.
    pub win_rate: f64,
    /// Wilson 95% lower bound on `win_rate`, in percent. The defensible
    /// number: what a book this size has actually earned the right to claim.
    /// `-1.0` when `closes == 0`. **Rank and filter on this.**
    pub win_rate_lo: f64,
    /// Win rate computed the old way — gross, ignoring fees. Kept only so the
    /// UI can show how much of a book's "edge" is really the exchange's.
    pub win_rate_gross: f64,
    /// How far to trust `win_rate`.
    pub confidence: Confidence,

    /// Annualised Sharpe of daily net PnL: mean/sd × √365, sample sd (n−1).
    /// Idle days inside the window count as 0-return days, because they are.
    /// `0.0` when fewer than two days of history exist.
    pub sharpe: f64,
    /// Days in the Sharpe sample. Below [`MIN_SHARPE_DAYS`] the ratio is an
    /// artifact — render it as "—".
    pub sharpe_days: usize,

    // ── shape ──
    /// Distinct coins traded, most-traded first, capped at 8.
    pub coins: Vec<String>,
    /// Mean USD notional per fill.
    pub avg_trade_usd: f64,
    /// Largest single net loss on one close. The number a copier feels.
    pub worst_close: f64,
    /// Largest single net win on one close.
    pub best_close: f64,
    /// Σ wins ÷ |Σ losses|, net of fees. `-1.0` when there were no losses in
    /// the window — the ratio is genuinely undefined there, and `0.0` would
    /// render as the worst possible book when it means the opposite.
    pub profit_factor: f64,

    // ── provenance ──
    /// ms epoch of the most recent fill in the window.
    pub last_active: i64,
    /// ms epoch of the earliest fill in the window.
    pub first_active: i64,
    /// Calendar days actually spanned by the fills scored here — which is not
    /// the requested window when a wallet is younger than it.
    pub span_days: usize,
}

impl PerfStats {
    /// True when the win rate rests on enough closes to quote as a percent.
    pub fn win_rate_measured(&self) -> bool {
        matches!(self.confidence, Confidence::Measured)
    }
    /// True when the Sharpe rests on enough days to print at all.
    pub fn sharpe_measured(&self) -> bool {
        self.sharpe_days >= MIN_SHARPE_DAYS
    }
}

/// Wilson score interval, lower bound, for `wins` successes in `n` trials at
/// 95% confidence. Returns a fraction in `0.0..=1.0`.
///
/// Chosen over the textbook normal interval because the normal one is degenerate
/// exactly where this data lives: at 8/8 it computes a ±0 interval and declares
/// certainty. Wilson stays honest at the boundary.
pub fn wilson_lower(wins: usize, n: usize) -> f64 {
    if n == 0 { return 0.0; }
    let n = n as f64;
    let p = wins as f64 / n;
    let z2 = Z * Z;
    let centre = p + z2 / (2.0 * n);
    let margin = Z * ((p * (1.0 - p) / n) + (z2 / (4.0 * n * n))).sqrt();
    let lo = (centre - margin) / (1.0 + z2 / n);
    lo.clamp(0.0, 1.0)
}

/// Score every fill at or after `cutoff_ms`.
///
/// `now_ms` bounds the calendar-day axis used for Sharpe, so the caller
/// controls "today" (and tests are deterministic).
pub fn score(fills: &[Fill], cutoff_ms: i64, now_ms: i64) -> PerfStats {
    let mut s = PerfStats::default();
    let mut coins: BTreeMap<String, usize> = BTreeMap::new();
    let mut daily: BTreeMap<i64, f64> = BTreeMap::new();
    let mut gross_wins = 0usize;
    let mut win_sum = 0.0f64;
    let mut loss_sum = 0.0f64;
    let mut first = i64::MAX;

    for f in fills {
        if f.time < cutoff_ms { continue; }
        s.trades += 1;

        let px: f64 = f.px.parse().unwrap_or(0.0);
        let sz: f64 = f.sz.parse().unwrap_or(0.0);
        let cp: f64 = f.closed_pnl.parse().unwrap_or(0.0);
        let fee: f64 = f.fee.parse().unwrap_or(0.0);

        s.volume += px * sz;
        s.gross_pnl += cp;
        s.fees += fee;

        // A fill that realises PnL is a close. Fees are charged on opens too,
        // so an open is `cp == 0` with a nonzero fee — it belongs in `pnl` and
        // in `volume`, but it is not a close and must not reach the win rate.
        if cp != 0.0 {
            s.closes += 1;
            if cp > 0.0 { gross_wins += 1; }
            // Decide the outcome on what the trader kept, not on what the
            // exchange printed before taking its cut.
            let net = cp - fee;
            if net > 0.0 {
                s.wins += 1;
                win_sum += net;
                if net > s.best_close { s.best_close = net; }
            } else if net < 0.0 {
                s.losses += 1;
                loss_sum += -net;
                if net < s.worst_close { s.worst_close = net; }
            } else {
                s.scratches += 1;
            }
        }

        *coins.entry(f.coin.clone()).or_insert(0) += 1;
        if f.time > s.last_active { s.last_active = f.time; }
        if f.time < first { first = f.time; }
        *daily.entry(f.time / DAY_MS).or_insert(0.0) += cp - fee;
    }

    s.pnl = s.gross_pnl - s.fees;
    s.first_active = if first == i64::MAX { 0 } else { first };
    s.avg_trade_usd = if s.trades == 0 { 0.0 } else { s.volume / s.trades as f64 };
    s.profit_factor = if loss_sum > 0.0 { win_sum / loss_sum } else { -1.0 };

    // ── win rate, with its denominator attached ──
    if s.closes == 0 {
        s.win_rate = -1.0;
        s.win_rate_lo = -1.0;
        s.win_rate_gross = -1.0;
        s.confidence = Confidence::None;
    } else {
        s.win_rate = s.wins as f64 / s.closes as f64 * 100.0;
        s.win_rate_lo = wilson_lower(s.wins, s.closes) * 100.0;
        s.win_rate_gross = gross_wins as f64 / s.closes as f64 * 100.0;
        s.confidence = if s.closes >= MIN_CLOSES { Confidence::Measured } else { Confidence::Low };
    }

    // ── Sharpe over calendar days, not over days that happened to trade ──
    //
    // The old code took the standard deviation of {308, 381} and called the
    // result 13.37. Two green days in a row will do that to any n-sample sd.
    // A day inside the window with no fill is not a missing observation, it is
    // a 0% return, and including it is what makes the denominator mean risk.
    //
    // The axis runs from the wallet's first fill (never earlier — a wallet is
    // not penalised for days before it existed) to today.
    let (start_day, end_day) = (
        (s.first_active.max(cutoff_ms)) / DAY_MS,
        (now_ms.max(s.last_active)) / DAY_MS,
    );
    if s.closes > 0 || s.trades > 0 {
        let n_days = (end_day - start_day + 1).max(1) as usize;
        s.span_days = n_days;
        if n_days >= 2 {
            let xs: Vec<f64> = (start_day..=end_day)
                .map(|d| *daily.get(&d).unwrap_or(&0.0))
                .collect();
            let n = xs.len() as f64;
            let mean = xs.iter().sum::<f64>() / n;
            // Sample standard deviation (n−1): these days are a sample of the
            // strategy's behaviour, not the whole population of it.
            let var = xs.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (n - 1.0);
            let sd = var.sqrt();
            s.sharpe = if sd > 0.0 { mean / sd * TRADING_DAYS.sqrt() } else { 0.0 };
            s.sharpe_days = xs.len();
        }
    }

    let mut cv: Vec<(String, usize)> = coins.into_iter().collect();
    cv.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    s.coins = cv.into_iter().take(8).map(|(c, _)| c).collect();

    s
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fill(coin: &str, t_day: i64, px: f64, sz: f64, cp: f64, fee: f64) -> Fill {
        Fill {
            coin: coin.into(),
            side: if cp >= 0.0 { "B".into() } else { "A".into() },
            px: px.to_string(),
            sz: sz.to_string(),
            time: t_day * DAY_MS + 3_600_000,
            closed_pnl: cp.to_string(),
            fee: fee.to_string(),
            tid: 0,
            oid: 0,
        }
    }

    // The exact shape from the screenshot that started this: sixteen fills,
    // eight of which closed, all eight green, spread over two days.
    fn screenshot_case() -> Vec<Fill> {
        let mut v = Vec::new();
        for i in 0..8 {
            v.push(fill("XRP", 10 + i % 2, 3.0, 100.0, 0.0, 0.5)); // opens: no cp
        }
        for i in 0..8 {
            v.push(fill("XRP", 10 + i % 2, 3.0, 100.0, 90.0, 0.2)); // closes: all green
        }
        v
    }

    #[test]
    fn win_rate_publishes_its_denominator() {
        let s = score(&screenshot_case(), 0, 11 * DAY_MS);
        assert_eq!(s.trades, 16, "the trades tile counts every fill");
        assert_eq!(s.closes, 8, "but only 8 of them could win or lose");
        assert_eq!(s.win_rate, 100.0);
        // The point of the whole exercise: 8/8 is not a 100% claim.
        assert!(s.win_rate_lo > 60.0 && s.win_rate_lo < 70.0, "got {}", s.win_rate_lo);
        assert_eq!(s.confidence, Confidence::Low, "8 closes is anecdote, not measurement");
        assert!(!s.win_rate_measured());
    }

    #[test]
    fn a_bigger_sample_outranks_a_perfect_tiny_one() {
        let perfect: Vec<Fill> = (0..8).map(|i| fill("A", i, 1.0, 1.0, 5.0, 0.1)).collect();
        let mut broad: Vec<Fill> = (0..180).map(|i| fill("B", i % 30, 1.0, 1.0, 5.0, 0.1)).collect();
        broad.extend((0..20).map(|i| fill("B", i % 30, 1.0, 1.0, -5.0, 0.1)));

        let p = score(&perfect, 0, 30 * DAY_MS);
        let b = score(&broad, 0, 30 * DAY_MS);

        assert_eq!(p.win_rate, 100.0);
        assert_eq!(b.win_rate, 90.0);
        // Point estimate ranks the anecdote first; the lower bound does not.
        assert!(p.win_rate > b.win_rate);
        assert!(b.win_rate_lo > p.win_rate_lo, "{} !> {}", b.win_rate_lo, p.win_rate_lo);
        assert_eq!(b.confidence, Confidence::Measured);
    }

    #[test]
    fn a_gross_win_that_lost_money_is_a_loss() {
        // closedPnl +0.16, fee 0.18 — the exchange printed green, the wallet bled.
        let s = score(&[fill("KAITO", 1, 1.0, 1.0, 0.1641, 0.18)], 0, 2 * DAY_MS);
        assert_eq!(s.closes, 1);
        assert_eq!(s.wins, 0, "net of fees this trade lost money");
        assert_eq!(s.losses, 1);
        assert_eq!(s.win_rate, 0.0);
        assert_eq!(s.win_rate_gross, 100.0, "and the old gross rule called it a win");
        assert!(s.pnl < 0.0);
    }

    #[test]
    fn idle_days_are_zero_return_days_not_missing_ones() {
        // Two green days at the end of a seven-day window. The old formula saw
        // n=2, a tiny sd, and returned a Sharpe in the teens.
        let fills = vec![
            fill("X", 5, 1.0, 1.0, 308.0, 0.0),
            fill("X", 6, 1.0, 1.0, 381.0, 0.0),
        ];
        let s = score(&fills, 0, 6 * DAY_MS);
        assert_eq!(s.sharpe_days, 2, "the wallet's history really is 2 days long");
        assert!(!s.sharpe_measured(), "so the ratio must not be presented as one");

        // Same two green days inside a book that has been running for a week.
        let mut long = vec![fill("X", 0, 1.0, 1.0, 1.0, 0.0)];
        long.extend(fills);
        let l = score(&long, 0, 6 * DAY_MS);
        assert_eq!(l.sharpe_days, 7, "idle days 1-4 count, at 0");
        assert!(l.sharpe_measured());
    }

    #[test]
    fn cutoff_is_respected_and_span_is_reported() {
        let fills = vec![
            fill("OLD", 0, 1.0, 1.0, 999.0, 0.0),   // outside
            fill("NEW", 20, 1.0, 1.0, 10.0, 0.0),   // inside
            fill("NEW", 21, 1.0, 1.0, -4.0, 0.0),   // inside
        ];
        let s = score(&fills, 20 * DAY_MS, 21 * DAY_MS);
        assert_eq!(s.trades, 2);
        assert_eq!(s.closes, 2);
        assert_eq!(s.wins, 1);
        assert_eq!(s.losses, 1);
        assert_eq!(s.win_rate, 50.0);
        assert_eq!(s.span_days, 2);
        assert!((s.pnl - 6.0).abs() < 1e-9);
        assert!(!s.coins.contains(&"OLD".to_string()));
    }

    #[test]
    fn no_closes_is_unmeasurable_not_zero() {
        let s = score(&[fill("X", 1, 1.0, 1.0, 0.0, 0.5)], 0, 2 * DAY_MS);
        assert_eq!(s.trades, 1);
        assert_eq!(s.closes, 0);
        assert_eq!(s.win_rate, -1.0, "-1 is the 'no data' sentinel, not 0%");
        assert_eq!(s.win_rate_lo, -1.0);
        assert_eq!(s.confidence, Confidence::None);
        assert!(s.pnl < 0.0, "an open still costs a fee");
    }

    #[test]
    fn gross_and_net_pnl_reconcile() {
        let fills = vec![
            fill("A", 1, 10.0, 2.0, 5.0, 0.25),
            fill("A", 2, 10.0, 2.0, -3.0, 0.25),
        ];
        let s = score(&fills, 0, 2 * DAY_MS);
        assert!((s.gross_pnl - 2.0).abs() < 1e-9);
        assert!((s.fees - 0.5).abs() < 1e-9);
        assert!((s.pnl - (s.gross_pnl - s.fees)).abs() < 1e-12);
        assert!((s.volume - 40.0).abs() < 1e-9);
        assert!((s.avg_trade_usd - 20.0).abs() < 1e-9);
        assert!((s.profit_factor - (4.75 / 3.25)).abs() < 1e-9);
        // No losses ⇒ undefined, not zero.
        let flawless = score(&[fill("A", 1, 1.0, 1.0, 5.0, 0.1)], 0, DAY_MS);
        assert_eq!(flawless.profit_factor, -1.0);
        assert!((s.worst_close + 3.25).abs() < 1e-9);
        assert!((s.best_close - 4.75).abs() < 1e-9);
    }

    #[test]
    fn wilson_bounds_are_sane() {
        assert_eq!(wilson_lower(0, 0), 0.0);
        assert!((wilson_lower(8, 8) - 0.6756).abs() < 0.001);
        assert!((wilson_lower(10, 10) - 0.7225).abs() < 0.001);
        assert!(wilson_lower(1, 1) < wilson_lower(3, 3));
        assert!(wilson_lower(3, 3) < wilson_lower(100, 100));
        assert!(wilson_lower(50, 100) < 0.5, "a lower bound sits below the estimate");
        assert!(wilson_lower(0, 20) >= 0.0, "never negative");
    }

    #[test]
    fn empty_window_is_all_zeros_and_no_panic() {
        let s = score(&[], 0, DAY_MS);
        assert_eq!(s.trades, 0);
        assert_eq!(s.win_rate, -1.0);
        assert_eq!(s.sharpe, 0.0);
        assert_eq!(s.sharpe_days, 0);
        assert_eq!(s.span_days, 0);
        assert!(s.coins.is_empty());
    }
}
