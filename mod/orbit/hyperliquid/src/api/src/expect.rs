// Expected profit per day — what a trader's edge is worth per day, and what
// it would be worth to a copier per $1,000.
//
// The window's pace (`pnl / days`) is the naive answer, and it is exactly
// the number a lucky streak inflates: 20 closes at a 90% win rate print the
// same pace as 2,000 closes at 90%, though only one of them is evidence.
// So the estimate is rebuilt from the profit factor's own parts, at the win
// rate the sample can DEFEND rather than the one it printed:
//
//   avg_win   = Σ net wins   / wins          (the profit factor's numerator,
//   avg_loss  = |Σ net losses| / losses       and its denominator, per close)
//   p         = Wilson 95% lower bound on the win rate (winRateLo)
//   edge      = p · avg_win − (1 − p) · avg_loss      USD per close
//   per_day   = edge · closes / days  +  open-fee drag / day
//
// With a big sample p ≈ the printed win rate and `per_day` ≈ the pace; with
// a thin one p falls and the estimate shrinks toward — or below — zero. That
// shrink is the point: `haircut` publishes how much of the pace survived.
//
// For a copier, a proportional copy rides the trader's RETURN, not their
// dollars, so `per_1k = per_day × 1000 / basis` where `basis` is the equity
// the window opened on (same basis the backtest scales by). Under $1k of
// basis the division turns noise into ROI, so `per_1k` is withheld there.

use crate::traders::TopTrader;
use serde::{Deserialize, Serialize};

/// Below this window-start equity a per-$1k figure is not quoted.
pub const MIN_BASIS_USD: f64 = crate::backtest::MIN_BASIS_USD;

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
#[serde(default)]
pub struct Expected {
    /// Conservative expected net profit per day at the TRADER's size, USD.
    pub per_day: f64,
    /// The window's raw pace, `pnl / days` — what `per_day` is a haircut of.
    pub pace_per_day: f64,
    /// `per_day` per $1,000 copied proportionally. `None` on a dust basis.
    pub per_1k: Option<f64>,
    /// Equity the window opened on (account value − window pnl).
    pub basis: f64,
    /// Expected USD per close at the defensible win rate.
    pub edge_per_close: f64,
    pub closes_per_day: f64,
    pub avg_win: f64,
    pub avg_loss: f64,
    /// The win rate `edge` was priced at — Wilson lower bound, 0–1.
    pub win_lo: f64,
    /// per_day ÷ pace_per_day, when both are positive — the share of the
    /// window's pace the sample size can defend.
    pub haircut: Option<f64>,
    /// "exact" when win/loss sums came from the fills; "derived" when they
    /// were reconstructed from pnl and the profit factor (older cached rows).
    pub source: String,
    /// True under `stats::MIN_CLOSES` closes — anecdote, not evidence.
    pub thin: bool,
}

fn round(x: f64, dp: i32) -> f64 {
    let m = 10f64.powi(dp);
    (x * m).round() / m
}

/// The estimate for one measured row over a `days` window, or `None` when
/// the row carries no realised closes to price an edge from.
pub fn expected(t: &TopTrader, days: u32) -> Option<Expected> {
    if t.win_rate < 0.0 || t.closes == 0 || days == 0 { return None; }
    let days = days as f64;

    // The profit factor's two halves. Rows scored by an older build carry
    // only the ratio — rebuild the halves from pnl ≈ W − L and PF = W / L.
    let (w, l, source) = if t.win_sum > 0.0 || t.loss_sum > 0.0 {
        (t.win_sum, t.loss_sum, "exact")
    } else if t.profit_factor < 0.0 {
        // No losing close: everything realised was a win.
        (t.pnl.max(0.0), 0.0, "derived")
    } else if t.profit_factor > 0.0 && (t.profit_factor - 1.0).abs() > 1e-9 {
        let l = t.pnl / (t.profit_factor - 1.0);
        if !(l.is_finite() && l > 0.0) { return None; }
        (t.profit_factor * l, l, "derived")
    } else {
        return None;
    };

    let avg_win = if t.wins > 0 { w / t.wins as f64 } else { 0.0 };
    // A book with no losing close still has a loss size — it just hasn't
    // shown it yet. Assume losses as big as wins (no edge from asymmetry)
    // rather than free; the worst close is a floor when it says more.
    let avg_loss = if t.losses > 0 { l / t.losses as f64 }
        else { avg_win.max(-t.worst_close) };
    let p = if t.win_rate_lo >= 0.0 { t.win_rate_lo / 100.0 } else { t.win_rate / 100.0 };
    let edge = p * avg_win - (1.0 - p) * avg_loss;
    let closes_per_day = t.closes as f64 / days;
    // Fees on opening fills land in pnl but in no close; carry them as drag.
    // Only knowable when the halves are exact.
    let drag = if source == "exact" { (t.pnl - (w - l)).min(0.0) / days } else { 0.0 };
    let per_day = edge * closes_per_day + drag;
    let pace = t.pnl / days;
    let basis = t.account_value - t.pnl;
    let per_1k = (basis >= MIN_BASIS_USD).then(|| round(per_day * 1000.0 / basis, 2));
    let haircut = (pace > 0.0 && per_day > 0.0).then(|| round((per_day / pace).min(9.99), 3));
    Some(Expected {
        per_day: round(per_day, 2),
        pace_per_day: round(pace, 2),
        per_1k,
        basis: round(basis, 2),
        edge_per_close: round(edge, 4),
        closes_per_day: round(closes_per_day, 2),
        avg_win: round(avg_win, 4),
        avg_loss: round(avg_loss, 4),
        win_lo: round(p, 4),
        haircut,
        source: source.to_string(),
        thin: t.closes < crate::stats::MIN_CLOSES,
    })
}

/// Stamp every row of a board served at `days`.
pub fn stamp(rows: &mut [TopTrader], days: u32) {
    for t in rows { t.expected = expected(t, days); }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(wins: usize, losses: usize, w: f64, l: f64, equity: f64, days_pnl: f64) -> TopTrader {
        let closes = wins + losses;
        let wr = wins as f64 / closes as f64 * 100.0;
        TopTrader {
            address: "0xa".into(), account_value: equity, pnl: days_pnl,
            win_rate: wr, win_rate_lo: crate::stats::wilson_lower(wins, closes) * 100.0,
            closes, wins, losses, win_sum: w, loss_sum: l,
            profit_factor: if l > 0.0 { w / l } else { -1.0 },
            ..Default::default()
        }
    }

    #[test]
    fn a_big_sample_keeps_most_of_its_pace() {
        // 600 wins of $10, 400 losses of $10 over 10 days: pace $200/day.
        let t = row(600, 400, 6000.0, 4000.0, 102_000.0, 2000.0);
        let e = expected(&t, 10).unwrap();
        assert_eq!(e.pace_per_day, 200.0);
        assert!(e.per_day > 100.0 && e.per_day < 200.0, "{e:?}");
        assert_eq!(e.basis, 100_000.0);
        // per $1k = per_day / 100
        assert!((e.per_1k.unwrap() - e.per_day / 100.0).abs() < 0.01);
        assert_eq!(e.source, "exact");
        assert!(!e.thin);
    }

    #[test]
    fn a_lucky_streak_is_haircut_harder_than_a_record() {
        // Same 60% win rate and $10/$10 payoff, 100× less evidence.
        let big = expected(&row(600, 400, 6000.0, 4000.0, 102_000.0, 2000.0), 10).unwrap();
        let small = expected(&row(3, 2, 30.0, 20.0, 1_010.0, 10.0), 10).unwrap();
        assert!(small.per_day < 0.0, "3/5 at even payoff can't defend an edge: {small:?}");
        assert!(big.haircut.unwrap() > 0.5);
        assert!(small.thin);
    }

    #[test]
    fn a_dust_basis_gets_no_per_1k() {
        let e = expected(&row(60, 40, 600.0, 400.0, 700.0, 200.0), 7).unwrap();
        assert_eq!(e.per_1k, None);
    }

    #[test]
    fn no_losses_assumes_losses_as_big_as_wins() {
        let e = expected(&row(30, 0, 300.0, 0.0, 50_000.0, 300.0), 7).unwrap();
        assert_eq!(e.avg_win, 10.0);
        assert_eq!(e.avg_loss, 10.0);
        // p < 1 so the edge is strictly smaller than the pace implies.
        assert!(e.per_day < e.pace_per_day);
    }

    #[test]
    fn old_rows_rebuild_the_halves_from_pnl_and_profit_factor() {
        let mut t = row(600, 400, 6000.0, 4000.0, 102_000.0, 2000.0);
        let exact = expected(&t, 10).unwrap();
        t.win_sum = 0.0;
        t.loss_sum = 0.0; // as an index entry from before the fields existed
        let derived = expected(&t, 10).unwrap();
        assert_eq!(derived.source, "derived");
        assert!((derived.per_day - exact.per_day).abs() < 0.01, "{derived:?} vs {exact:?}");
    }

    #[test]
    fn unmeasured_rows_have_no_estimate() {
        let t = TopTrader { win_rate: -1.0, ..Default::default() };
        assert_eq!(expected(&t, 7), None);
    }
}
