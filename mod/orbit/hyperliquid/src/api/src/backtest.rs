//! "$N on this trader for D days" — a capital-scaled replay with the data
//! checks attached to the answer.
//!
//! The question a copier actually asks is not "what did this wallet make",
//! it is "what would MY money have made". This module answers it with two
//! honest models side by side, and refuses to hand either over without
//! saying how good the data behind it is:
//!
//! - **equity model** — your $N rides the trader's perp-book return curve
//!   proportionally, the way a vault deposit or the invest engine would.
//!   ROI at each sample is (pnl(t) − pnl(t₀)) / equity(t₀), from HL's own
//!   portfolio series (perp sub-account, deposit-immune `pnlHistory`), so
//!   the curve includes unrealised PnL and funding — the money you would
//!   have felt, not just the money that closed.
//! - **fills mirror** — realised-only: every in-window fill's exchange-
//!   reported `closedPnl` and `fee`, scaled by capital / trader-equity.
//!   The same convention the live engine and the Python strat base use.
//!
//! The two legitimately disagree on any wallet holding risk overnight, and
//! `checks` names the gap instead of letting the reader discover it.
//!
//! Every result carries `checks`: pass/warn/fail verdicts on the history's
//! coverage, the equity basis the scaling divides by, fills truncation (HL
//! caps a fills call at ~2000 rows), sample freshness, and whether your $N
//! is bigger than the book you'd be copying. `ok` is false the moment any
//! check fails — a backtest on bad data is worse than no backtest.

use crate::curve::{cents, downsample, parse_history, period_for_days, slot, MAX_POINTS};
use crate::hl::{parse_fills, Client, Fill};
use serde::Serialize;
use serde_json::Value;
use std::sync::Arc;

/// HL returns at most ~2000 fills per `userFillsByTime` call. At or past
/// this count the window's early fills are simply missing, and the realised
/// mirror silently understates — which is exactly the kind of silence the
/// checks exist to break.
pub const FILLS_CAP: usize = 2000;

/// Below this equity at window start, dividing by it turns noise into ROI.
/// Same guard the vaults board uses against dust-basis APRs.
pub const MIN_BASIS_USD: f64 = 1_000.0;

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct Check {
    pub name: &'static str,
    /// "pass" | "warn" | "fail". A warn is a caveat on a usable number; a
    /// fail means the number above it should not be trusted at all.
    pub status: &'static str,
    pub detail: String,
}

impl Check {
    fn pass(name: &'static str, detail: String) -> Check { Check { name, status: "pass", detail } }
    fn warn(name: &'static str, detail: String) -> Check { Check { name, status: "warn", detail } }
    fn fail(name: &'static str, detail: String) -> Check { Check { name, status: "fail", detail } }
}

/// The realised-only replay: in-window fills scaled by capital / basis.
#[derive(Debug, Clone, Serialize, Default, PartialEq)]
pub struct Mirror {
    /// capital / basis_equity — the size multiple a 1:1 proportional copy
    /// would trade at.
    pub ratio: f64,
    /// Fills inside the window (opens included — they carry the fees).
    pub fills: usize,
    /// Σ closedPnl × ratio, the trader's realised result at your size.
    pub realized_pnl: f64,
    /// Σ fee × ratio.
    pub fees: f64,
    /// realized − fees.
    pub net_pnl: f64,
    pub roi_pct: f64,
    /// True when the fills payload cannot cover the window (hit HL's row
    /// cap before reaching the window start). The figures above then cover
    /// only the tail of the window.
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Default, PartialEq)]
pub struct Backtest {
    pub address: String,
    pub days: u32,
    /// The $N the caller put on this trader.
    pub capital: f64,
    /// Which portfolio book the curve came from: "perp" (what a copy would
    /// actually mirror) or "combined" (spot included — only when HL
    /// publishes no perp slot for this wallet).
    pub source: String,
    /// [ms epoch, your equity in USD], oldest first, starting at `capital`.
    pub points: Vec<[f64; 2]>,
    pub start_ms: i64,
    pub end_ms: i64,
    /// Where your $N ends the window.
    pub final_value: f64,
    /// final_value − capital.
    pub pnl: f64,
    pub roi_pct: f64,
    /// Deepest peak→trough fall of YOUR equity, in USD and as % of the peak.
    pub max_drawdown: f64,
    pub max_drawdown_pct: f64,
    /// The trader's own window PnL the curve was scaled from, and the
    /// equity it was divided by — published so the scaling is auditable.
    pub trader_window_pnl: f64,
    pub basis_equity: f64,
    /// True if the scaled equity ever reached zero — past that point the
    /// curve is floored, because a wiped account does not recover.
    pub wiped: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mirror: Option<Mirror>,
    /// The data-quality verdicts. Read these before the numbers.
    pub checks: Vec<Check>,
    /// False when any check failed. The numbers are then either absent or
    /// not to be trusted, and `note` says why in one sentence.
    pub ok: bool,
    /// False when there was nothing to replay at all.
    pub available: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

impl Backtest {
    fn unavailable(address: &str, days: u32, capital: f64, note: &str, checks: Vec<Check>) -> Backtest {
        Backtest {
            address: address.to_string(),
            days,
            capital,
            available: false,
            ok: false,
            note: Some(note.to_string()),
            checks,
            ..Default::default()
        }
    }
}

fn perp_period(days: u32) -> String {
    let p = period_for_days(days);
    let mut c = p.chars();
    format!("perp{}{}", c.next().unwrap_or('d').to_ascii_uppercase(), c.as_str())
}

/// Last sample at-or-before `t`, else the first one after it — the equity
/// the window actually opened on, without inventing a number when the grid
/// starts late.
fn value_at(series: &[(i64, f64)], t: i64) -> Option<f64> {
    series.iter().rev().find(|(st, _)| *st <= t).map(|(_, v)| *v)
        .or_else(|| series.first().map(|(_, v)| *v))
}

fn parse_f(s: &str) -> f64 {
    s.parse::<f64>().unwrap_or(0.0)
}

/// Shape one backtest from already-fetched payloads. Pure — `now_ms` comes
/// in from the caller so the window boundary (and the tests) are
/// deterministic.
pub fn shape(
    address: &str,
    days: u32,
    capital: f64,
    portfolio: &Value,
    fills: &[Fill],
    now_ms: i64,
) -> Backtest {
    let mut checks: Vec<Check> = Vec::new();
    let period = period_for_days(days);
    let perp = slot(portfolio, &perp_period(days));
    let combined = slot(portfolio, period);

    // A copy mirrors the perp book; spot bags never fill on your account.
    let (chosen, source) = match perp {
        Some(s) => (Some(s), "perp"),
        None => (combined, "combined"),
    };
    if source == "combined" && chosen.is_some() {
        checks.push(Check::warn("source", "no perp sub-account curve published for this wallet — using the combined account, which includes spot bags a copy would never hold".into()));
    }

    let raw = parse_history(chosen.and_then(|s| s.get("pnlHistory")));
    if raw.len() < 2 {
        checks.push(Check::fail("history", "hyperliquid has no pnl history for this wallet yet".into()));
        return Backtest::unavailable(address, days, capital, "hyperliquid has no pnl history for this wallet yet", checks);
    }

    // HL answers a portfolio for ANY address as an all-zero synthetic grid.
    // An all-zero perp book next to a non-zero combined one is a real wallet
    // that just doesn't trade perps — copying it buys you nothing, and the
    // honest answer is that sentence, not a flat line dressed as a result.
    if raw.iter().all(|(_, v)| *v == 0.0) {
        let combined_moves = parse_history(combined.and_then(|s| s.get("pnlHistory")))
            .iter().any(|(_, v)| *v != 0.0);
        let note = if combined_moves {
            "this wallet's perp book has never traded — its moves are spot bags, which a copy would not hold"
        } else {
            "no pnl history — this wallet hasn't traded on hyperliquid"
        };
        checks.push(Check::fail("history", note.into()));
        return Backtest::unavailable(address, days, capital, note, checks);
    }
    checks.push(Check::pass("history", format!("{} portfolio samples on hand", raw.len())));

    // Trim to the requested window; keep the raw series only if trimming
    // would leave nothing to draw.
    let window_ms = (days as i64) * 86_400_000;
    let cutoff = now_ms - window_ms;
    let trimmed: Vec<(i64, f64)> = raw.iter().copied().filter(|(t, _)| *t >= cutoff).collect();
    let pts = if trimmed.len() >= 2 { trimmed } else { raw.clone() };
    let (t0, base_pnl) = pts[0];
    let t_end = pts[pts.len() - 1].0;

    // Coverage: how much of the asked-for window the samples actually span.
    let covered = (t_end - t0.max(cutoff)).max(0) as f64 / window_ms as f64 * 100.0;
    let cov_detail = format!("samples span {:.0}% of the requested {days}d window", covered.min(100.0));
    checks.push(if covered >= 90.0 {
        Check::pass("coverage", cov_detail)
    } else if covered >= 50.0 {
        Check::warn("coverage", format!("{cov_detail} — the missing stretch is simply not in the result"))
    } else {
        Check::fail("coverage", format!("{cov_detail} — too little history to call this a {days}d backtest"))
    });

    // Freshness: a curve whose last sample is a day old is describing a
    // different account than the one trading right now.
    // HL sometimes stamps its newest sample a breath ahead of our clock;
    // "-0.0h old" is a truthful number that reads as a bug.
    let age_h = (now_ms - t_end).max(0) as f64 / 3_600_000.0;
    checks.push(if age_h <= 24.0 {
        Check::pass("freshness", format!("last portfolio sample {age_h:.1}h old", ))
    } else {
        Check::warn("freshness", format!("last portfolio sample is {age_h:.0}h old — hyperliquid has published nothing newer"))
    });

    // The equity the scaling divides by: the trader's account value where
    // the window opens. Zero or missing makes every scaled number undefined.
    let av = parse_history(chosen.and_then(|s| s.get("accountValueHistory")));
    let basis = value_at(&av, t0).unwrap_or(0.0);
    if basis <= 0.0 {
        checks.push(Check::fail("basis", "no account equity at the window start — nothing to scale your capital against".into()));
        return Backtest::unavailable(address, days, capital, "no account equity at the window start to scale against", checks);
    }
    if basis < MIN_BASIS_USD {
        checks.push(Check::warn("basis", format!("trader equity at window start was only ${basis:.0} — ROI scaled off a dust basis is unstable")));
    } else {
        checks.push(Check::pass("basis", format!("scaled against ${basis:.0} of trader equity at window start")));
    }

    let ratio = capital / basis;
    checks.push(if ratio <= 1.0 {
        // A $1k copy of an $18M whale is 0.005% — one decimal place would
        // print "0.0%", which reads as "no money at all".
        let pct = ratio * 100.0;
        let pct = if pct >= 0.1 { format!("{pct:.1}%") } else { format!("{pct:.3}%") };
        Check::pass("scale", format!("your capital is {pct} of the trader's book — fills at that size were realistic"))
    } else {
        Check::warn("scale", format!("your ${capital:.0} is {ratio:.1}× the trader's own equity — a real copy at this size would move the market more than they did"))
    });

    // Build YOUR equity curve. Once it touches zero it stays there: a wiped
    // account does not ride the rest of the trader's recovery.
    let mut wiped = false;
    let scaled: Vec<(i64, f64)> = pts.iter().map(|(t, v)| {
        let equity = if wiped { 0.0 } else {
            let e = capital * (1.0 + (v - base_pnl) / basis);
            if e <= 0.0 { wiped = true; 0.0 } else { e }
        };
        (*t, equity)
    }).collect();
    if wiped {
        checks.push(Check::fail("wipeout", format!("${capital:.0} riding this book at 1:1 leverage-parity hit zero inside the window — the curve is floored from that point")));
    }

    let trader_window_pnl = cents(pts[pts.len() - 1].1 - base_pnl);
    let final_value = scaled[scaled.len() - 1].1;

    let (mut peak, mut dd, mut dd_pct) = (f64::MIN, 0.0f64, 0.0f64);
    for (_, v) in &scaled {
        peak = peak.max(*v);
        let fall = peak - v;
        if fall > dd {
            dd = fall;
            dd_pct = if peak > 0.0 { fall / peak * 100.0 } else { 0.0 };
        }
    }

    // The realised-only mirror off the fills tape, same scaling.
    let in_window: Vec<&Fill> = fills.iter().filter(|f| f.time >= cutoff).collect();
    let earliest_any = fills.iter().map(|f| f.time).min().unwrap_or(i64::MAX);
    let truncated = fills.len() >= FILLS_CAP && earliest_any > cutoff;
    let realized: f64 = in_window.iter().map(|f| parse_f(&f.closed_pnl)).sum();
    let fees: f64 = in_window.iter().map(|f| parse_f(&f.fee)).sum();
    let mirror = Mirror {
        ratio: (ratio * 1e6).round() / 1e6,
        fills: in_window.len(),
        realized_pnl: cents(realized * ratio),
        fees: cents(fees * ratio),
        net_pnl: cents((realized - fees) * ratio),
        roi_pct: cents((realized - fees) * ratio / capital * 100.0),
        truncated,
    };
    checks.push(if truncated {
        Check::warn("fills", format!("fills call hit hyperliquid's ~{FILLS_CAP}-row cap before reaching the window start — the realised mirror covers only the last part of the window"))
    } else if in_window.is_empty() {
        Check::warn("fills", "no fills inside the window — the realised mirror is $0 by construction".into())
    } else {
        Check::pass("fills", format!("{} fills inside the window, tape reaches the window start", in_window.len()))
    });

    // Name the model gap instead of letting the two numbers fight. Opposite
    // material signs is the case a reader must not miss: every close won,
    // and the book still bled on the marks (or the reverse).
    let scaled_curve_pnl = cents(final_value - capital);
    let gap = scaled_curve_pnl - mirror.net_pnl;
    let material = |x: f64| x.abs() > f64::max(1.0, capital * 0.01);
    checks.push(if material(scaled_curve_pnl) && material(mirror.net_pnl)
        && scaled_curve_pnl.signum() != mirror.net_pnl.signum() {
        Check::warn("agreement", format!("the equity model says {scaled_curve_pnl:+.0} while realised fills say {:+.0} — open positions, funding or the window's edges account for the {gap:+.0} gap; both are real", mirror.net_pnl))
    } else {
        Check::pass("agreement", format!("equity model {scaled_curve_pnl:+.0} vs realised fills {:+.0} — gap {gap:+.0} is funding + positions still open at the marks", mirror.net_pnl))
    });

    let ok = !checks.iter().any(|c| c.status == "fail");
    let points: Vec<[f64; 2]> = downsample(scaled, MAX_POINTS)
        .into_iter().map(|(t, v)| [t as f64, cents(v)]).collect();

    Backtest {
        address: address.to_string(),
        days,
        capital,
        source: source.to_string(),
        start_ms: t0,
        end_ms: t_end,
        final_value: cents(final_value),
        pnl: scaled_curve_pnl,
        roi_pct: cents(scaled_curve_pnl / capital * 100.0),
        max_drawdown: cents(dd),
        max_drawdown_pct: (dd_pct * 10.0).round() / 10.0,
        trader_window_pnl,
        basis_equity: cents(basis),
        wiped,
        mirror: Some(mirror),
        checks,
        ok,
        available: true,
        note: None,
        points,
    }
}

/// Fetch and shape: the route behind `GET /trader/:addr/backtest`.
///
/// Both upstream calls are the same cached ones the trader page already
/// spends, so a backtest costs nothing new. Never errors — bad input or a
/// silent exchange degrades to `available: false` with the reason.
pub async fn run(hl: Arc<Client>, address: &str, days: u32, capital: f64) -> Backtest {
    if !crate::curve::is_wallet(address) {
        return Backtest::unavailable(address, days, capital,
            "not a wallet address (0x + 40 hex characters)",
            vec![Check::fail("input", "not a wallet address (0x + 40 hex characters)".into())]);
    }
    if !capital.is_finite() || capital <= 0.0 {
        return Backtest::unavailable(address, days, capital,
            "capital must be a positive USD amount",
            vec![Check::fail("input", "capital must be a positive USD amount".into())]);
    }
    let now_ms = chrono::Utc::now().timestamp_millis();
    let cutoff = now_ms - (days as i64) * 86_400_000;
    let portfolio = match hl.user_pnl(address).await {
        Ok(v) => v,
        Err(e) => {
            return Backtest::unavailable(address, days, capital,
                "hyperliquid would not answer for this wallet right now — try again shortly",
                vec![Check::fail("history", format!("portfolio fetch failed: {e}"))]);
        }
    };
    // Fills are decoration on the equity model — a failed tape costs the
    // mirror, not the backtest.
    let fills = hl.user_fills_by_time(address, cutoff).await
        .map(|v| parse_fills(&v)).unwrap_or_default();
    shape(address, days, capital, &portfolio, &fills, now_ms)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const DAY: i64 = 86_400_000;
    const NOW: i64 = 1_700_000_000_000;

    /// A portfolio with one perp slot: samples spread evenly across `span`
    /// days so the coverage check sees a full window. The coverage check is
    /// exact on purpose — a fixture that quietly spans 2 days of a 7d ask
    /// SHOULD fail it, which is how these fixtures got this parameter.
    fn portfolio_span(pnl: &[f64], equity: &[f64], span: i64) -> Value {
        let n = pnl.len() as i64 - 1;
        let t = move |i: usize| NOW - span * DAY * (n - i as i64) / n.max(1);
        let series = |vals: &[f64]| -> Value {
            vals.iter().enumerate().map(|(i, v)| json!([t(i), format!("{v}")])).collect()
        };
        json!([
            ["week", {"pnlHistory": series(pnl), "accountValueHistory": series(equity)}],
            ["perpWeek", {"pnlHistory": series(pnl), "accountValueHistory": series(equity)}],
        ])
    }

    fn portfolio(pnl: &[f64], equity: &[f64]) -> Value {
        portfolio_span(pnl, equity, 7)
    }

    fn fill(time: i64, closed_pnl: f64, fee: f64) -> Fill {
        Fill {
            coin: "ETH".into(), side: "B".into(), px: "1".into(), sz: "1".into(),
            time, closed_pnl: format!("{closed_pnl}"), fee: format!("{fee}"),
            tid: 1, oid: 1,
        }
    }

    fn check<'a>(b: &'a Backtest, name: &str) -> &'a Check {
        b.checks.iter().find(|c| c.name == name)
            .unwrap_or_else(|| panic!("no `{name}` check in {:?}", b.checks))
    }

    #[test]
    fn a_dollar_rides_the_roi_not_the_dollars() {
        // Trader: $10k equity, made $1k over the window (+10%).
        // Your $1k should end at $1.1k regardless of their dollar pnl.
        let p = portfolio(&[0.0, 400.0, 1000.0], &[10_000.0, 10_400.0, 11_000.0]);
        let b = shape("0xA", 7, 1_000.0, &p, &[], NOW);
        assert!(b.available && b.ok, "{:?}", b.checks);
        assert_eq!(b.final_value, 1_100.0);
        assert_eq!(b.pnl, 100.0);
        assert_eq!(b.roi_pct, 10.0);
        assert_eq!(b.basis_equity, 10_000.0);
        assert_eq!(b.trader_window_pnl, 1_000.0);
        assert_eq!(b.source, "perp");
        // Curve starts at the capital and is monotone here.
        assert_eq!(b.points.first().unwrap()[1], 1_000.0);
        assert_eq!(b.max_drawdown, 0.0);
    }

    #[test]
    fn drawdown_is_measured_on_your_equity() {
        // +20% then down to -10% then back to +10%: peak $1.2k → trough $0.9k.
        let p = portfolio(&[0.0, 2_000.0, -1_000.0, 1_000.0],
                          &[10_000.0, 12_000.0, 9_000.0, 11_000.0]);
        let b = shape("0xA", 7, 1_000.0, &p, &[], NOW);
        assert_eq!(b.final_value, 1_100.0);
        assert_eq!(b.max_drawdown, 300.0);
        assert_eq!(b.max_drawdown_pct, 25.0);
    }

    #[test]
    fn a_wipeout_floors_at_zero_and_fails_the_checks() {
        // Trader loses 120% of the window-open equity, then recovers. Your
        // copy died at -100% and does not ride the recovery.
        let p = portfolio(&[0.0, -12_000.0, 5_000.0], &[10_000.0, 1.0, 5_001.0]);
        let b = shape("0xA", 7, 1_000.0, &p, &[], NOW);
        assert!(b.wiped);
        assert!(!b.ok);
        assert_eq!(b.final_value, 0.0);
        assert_eq!(check(&b, "wipeout").status, "fail");
        // Every point after the wipe stays floored.
        assert_eq!(b.points.last().unwrap()[1], 0.0);
    }

    #[test]
    fn mirror_scales_realised_pnl_and_fees_by_capital_over_basis() {
        let p = portfolio(&[0.0, 500.0, 1_000.0], &[10_000.0, 10_500.0, 11_000.0]);
        // Two in-window fills, one stale fill outside the window.
        let fills = vec![
            fill(NOW - DAY, 300.0, 10.0),
            fill(NOW - 2 * DAY + 1, 700.0, 20.0),
            fill(NOW - 30 * DAY, 9_999.0, 1.0),
        ];
        let b = shape("0xA", 2, 1_000.0, &p, &fills, NOW);
        let m = b.mirror.as_ref().unwrap();
        // basis = $10k at window open → ratio 0.1.
        assert_eq!(m.ratio, 0.1);
        assert_eq!(m.fills, 2);
        assert_eq!(m.realized_pnl, 100.0);
        assert_eq!(m.fees, 3.0);
        assert_eq!(m.net_pnl, 97.0);
        assert!(!m.truncated);
    }

    #[test]
    fn a_capped_fills_tape_that_misses_the_window_start_warns() {
        let p = portfolio(&[0.0, 500.0, 1_000.0], &[10_000.0, 10_500.0, 11_000.0]);
        // FILLS_CAP rows, all newer than the cutoff: the tape was truncated.
        let fills: Vec<Fill> = (0..FILLS_CAP).map(|i| fill(NOW - (i as i64), 1.0, 0.1)).collect();
        let b = shape("0xA", 7, 1_000.0, &p, &fills, NOW);
        assert!(b.mirror.as_ref().unwrap().truncated);
        assert_eq!(check(&b, "fills").status, "warn");
        assert!(b.ok, "truncation is a caveat, not a failure");
    }

    #[test]
    fn dust_basis_warns_zero_basis_fails() {
        let dust = portfolio(&[0.0, 10.0, 20.0], &[500.0, 510.0, 520.0]);
        let b = shape("0xA", 7, 1_000.0, &dust, &[], NOW);
        assert_eq!(check(&b, "basis").status, "warn");
        assert!(b.ok);

        let zero = portfolio(&[0.0, 10.0, 20.0], &[0.0, 0.0, 0.0]);
        let b = shape("0xA", 7, 1_000.0, &zero, &[], NOW);
        assert!(!b.available && !b.ok);
        assert_eq!(check(&b, "basis").status, "fail");
    }

    #[test]
    fn capital_bigger_than_the_book_warns_about_scale() {
        let p = portfolio(&[0.0, 500.0, 1_000.0], &[10_000.0, 10_500.0, 11_000.0]);
        let b = shape("0xA", 7, 50_000.0, &p, &[], NOW);
        assert_eq!(check(&b, "scale").status, "warn");
        // The math still scales linearly: 5× the book, 5× the pnl ratio.
        assert_eq!(b.roi_pct, 10.0);
        assert_eq!(b.pnl, 5_000.0);
    }

    #[test]
    fn opposite_material_signs_warn_on_agreement() {
        // Curve says +10%, fills say a material realised loss.
        let p = portfolio(&[0.0, 500.0, 1_000.0], &[10_000.0, 10_500.0, 11_000.0]);
        let fills = vec![fill(NOW - DAY, -2_000.0, 0.0)];
        let b = shape("0xA", 7, 1_000.0, &p, &fills, NOW);
        assert_eq!(check(&b, "agreement").status, "warn");
    }

    #[test]
    fn a_never_traded_wallet_is_unavailable_not_a_flat_line() {
        let p = portfolio(&[0.0, 0.0, 0.0], &[0.0, 0.0, 0.0]);
        let b = shape("0xA", 7, 1_000.0, &p, &[], NOW);
        assert!(!b.available);
        assert!(b.points.is_empty());
        assert_eq!(check(&b, "history").status, "fail");
    }

    #[test]
    fn a_spot_only_wallet_names_the_reason() {
        // Perp slot flat at zero, combined slot moving: spot bags, no perps.
        let t = |i: i64| NOW - DAY * (2 - i);
        let p = json!([
            ["week", {"pnlHistory": [[t(0), "0"], [t(1), "5000"], [t(2), "9000"]],
                      "accountValueHistory": [[t(0), "10000"], [t(1), "15000"], [t(2), "19000"]]}],
            ["perpWeek", {"pnlHistory": [[t(0), "0.0"], [t(1), "0.0"], [t(2), "0.0"]],
                          "accountValueHistory": [[t(0), "0"], [t(1), "0"], [t(2), "0"]]}],
        ]);
        let b = shape("0xA", 7, 1_000.0, &p, &[], NOW);
        assert!(!b.available);
        assert!(b.note.as_deref().unwrap().contains("spot"));
    }

    #[test]
    fn short_history_fails_coverage_but_still_reports() {
        // Only ~half a day of samples against a 30d ask.
        let t = |i: i64| NOW - 3_600_000 * (12 - i);
        let p = json!([
            ["month", {"pnlHistory": [[t(0), "0"], [t(6), "50"], [t(12), "100"]],
                       "accountValueHistory": [[t(0), "10000"], [t(6), "10050"], [t(12), "10100"]]}],
            ["perpMonth", {"pnlHistory": [[t(0), "0"], [t(6), "50"], [t(12), "100"]],
                           "accountValueHistory": [[t(0), "10000"], [t(6), "10050"], [t(12), "10100"]]}],
        ]);
        let b = shape("0xA", 30, 1_000.0, &p, &[], NOW);
        assert!(b.available, "numbers still come back");
        assert!(!b.ok, "but the coverage check fails the result");
        assert_eq!(check(&b, "coverage").status, "fail");
    }

    #[test]
    fn bad_input_is_refused_before_any_fetch() {
        // run() is async; the input guards are what shape can't test, so
        // exercise them through a blocking runtime.
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        let hl = Arc::new(Client::new(false));
        let b = rt.block_on(run(hl.clone(), "not-a-wallet", 7, 1_000.0));
        assert!(!b.available);
        assert_eq!(check(&b, "input").status, "fail");
        let b = rt.block_on(run(hl, "0x0000000000000000000000000000000000000001", 7, -5.0));
        assert!(!b.available);
        assert!(b.note.as_deref().unwrap().contains("positive"));
    }
}
