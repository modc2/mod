//! One wallet's PnL curve for one window — the shape behind the number.
//!
//! The board prices every row with a single figure: "+$9,186 over 7d". That
//! figure cannot tell you whether the wallet ground it out or won it in one
//! lucky hour and gave half of it back, and those are not the same trader to
//! copy. This module answers that question with the cheapest honest thing
//! there is: the curve itself.
//!
//! The source is Hyperliquid's own `portfolio` info payload —
//! `[[period, { accountValueHistory, pnlHistory, vlm }], …]` for
//! day / week / month / allTime. It is the same series the trader detail page
//! charts and the same definition the leaderboard prices a window with
//! (realised *and* unrealised), so a row's number and its curve agree. Fills
//! would not: they only know realised PnL, and the board's `pnl` column comes
//! from the leaderboard.
//!
//! Everything here is derived from one already-cached upstream call
//! ([`crate::hl::Client::user_pnl`] holds a portfolio for 5 minutes), so a
//! board of hovers costs one request per wallet per 5 minutes and nothing
//! after that. A curve never fails a caller: upstream trouble comes back as
//! `available: false` with a sentence, because a hover that 500s is a hover
//! that looks broken.

use crate::hl::Client;
use serde::Serialize;
use serde_json::Value;
use std::sync::Arc;
use std::time::Duration;

/// Most points a curve carries. HL returns 23–70 samples per period, which is
/// already sparkline-sized; this only guards against a period that decides to
/// get chatty. 64 is well past the pixel budget of any row-height chart.
pub const MAX_POINTS: usize = 64;

/// How long a hover is allowed to wait on Hyperliquid. `Client::info` retries
/// 429s for up to ~30s, which is right for a background board scan and wrong
/// for a cursor: past a couple of seconds the user has moved on. Give up and
/// say so instead.
const UPSTREAM_BUDGET: Duration = Duration::from_secs(9);

/// A window's PnL over time, rebased so the window opens at zero.
///
/// `pnl` is the curve's own last point, not the board's number for the same
/// wallet. They usually agree within a fraction of a percent; when they don't
/// (a wallet that moved funds between sub-accounts, a stale leaderboard row),
/// the honest thing is to publish the number the drawn line actually ends at
/// and let the caller show that one.
#[derive(Debug, Clone, Serialize, Default, PartialEq)]
pub struct Curve {
    pub address: String,
    pub days: u32,
    /// The HL portfolio period these points came from.
    pub period: String,
    /// `[ms epoch, cumulative net PnL]`, oldest first, rebased to 0 at the
    /// first point inside the window.
    pub points: Vec<[f64; 2]>,
    pub start_ms: i64,
    pub end_ms: i64,
    /// Last point — what this window made, as the curve tells it.
    pub pnl: f64,
    /// Best and worst the cumulative curve ever got, in USD.
    pub high: f64,
    pub low: f64,
    /// Deepest peak → trough fall anywhere in the window, in USD (never
    /// negative). The number a copier feels: it is what you would have been
    /// down had you started at the worst moment.
    pub max_drawdown: f64,
    /// The same fall as a percent of the peak it fell from. `0.0` when the
    /// peak was not positive — a percentage of nothing means nothing.
    pub max_drawdown_pct: f64,
    /// False when there is no curve to draw. `points` is then empty and
    /// `note` says why in one sentence.
    pub available: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

impl Curve {
    fn empty(address: &str, days: u32, note: &str) -> Curve {
        Curve {
            address: address.to_string(),
            days,
            period: period_for_days(days).to_string(),
            available: false,
            note: Some(note.to_string()),
            ..Default::default()
        }
    }
}

/// Which HL portfolio period covers a window of `days`.
///
/// The board offers 1 / 7 / 30 because those are the windows HL itself prices
/// (`day` / `week` / `month`); anything else lands on the nearest period that
/// contains it, and `allTime` catches the rest rather than inventing history.
pub fn period_for_days(days: u32) -> &'static str {
    match days {
        0..=1 => "day",
        2..=7 => "week",
        8..=30 => "month",
        _ => "allTime",
    }
}

/// `[[name, body], …]` → the body named `name`.
fn slot<'a>(portfolio: &'a Value, name: &str) -> Option<&'a Value> {
    portfolio.as_array()?.iter().find_map(|row| {
        let pair = row.as_array()?;
        (pair.len() == 2 && pair[0].as_str() == Some(name)).then(|| &pair[1])
    })
}

/// HL writes history values as strings (`[1788207360033, "-4691.65"]`) and
/// occasionally as numbers. Take either, drop anything that is neither.
fn parse_history(v: Option<&Value>) -> Vec<(i64, f64)> {
    let Some(arr) = v.and_then(|x| x.as_array()) else { return Vec::new() };
    let mut out: Vec<(i64, f64)> = arr
        .iter()
        .filter_map(|p| {
            let pair = p.as_array()?;
            if pair.len() < 2 { return None; }
            let t = pair[0].as_i64().or_else(|| pair[0].as_f64().map(|x| x as i64))?;
            let v = pair[1]
                .as_f64()
                .or_else(|| pair[1].as_str().and_then(|s| s.parse::<f64>().ok()))?;
            v.is_finite().then_some((t, v))
        })
        .collect();
    out.sort_by_key(|(t, _)| *t);
    out
}

/// Thin a series to at most `max` points, always keeping the first and the
/// last. Even stride: the shape survives, the endpoints are exact, and the
/// last point stays the one the caller prints as `pnl`.
fn downsample(pts: Vec<(i64, f64)>, max: usize) -> Vec<(i64, f64)> {
    if pts.len() <= max || max < 2 {
        return pts;
    }
    let last = pts.len() - 1;
    let stride = (last as f64) / (max - 1) as f64;
    let mut out: Vec<(i64, f64)> = (0..max - 1)
        .map(|i| pts[(i as f64 * stride).round() as usize])
        .collect();
    out.dedup_by_key(|(t, _)| *t);
    out.push(pts[last]);
    out
}

/// Round to cents. A curve is drawn, not audited — 15 significant digits of
/// float noise per point is payload nobody can see.
fn cents(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

/// Shape one portfolio payload into a window curve. Pure — `now_ms` is passed
/// in so the window boundary (and therefore the tests) are deterministic.
pub fn shape(address: &str, days: u32, portfolio: &Value, now_ms: i64) -> Curve {
    let period = period_for_days(days);
    let raw = parse_history(slot(portfolio, period).and_then(|s| s.get("pnlHistory")));
    if raw.len() < 2 {
        // Every wallet on the board traded in the last 24h, so an empty
        // history is an upstream gap, not an idle account.
        return Curve::empty(address, days, "hyperliquid has no pnl history for this wallet yet");
    }

    // HL's periods run slightly long (its "week" spans ~7.9 days), so trim to
    // the window the board actually priced. Keep the trim only if it leaves a
    // line — a wallet whose samples all predate the cutoff still deserves its
    // curve rather than a blank.
    let cutoff = now_ms - (days as i64) * 86_400_000;
    let trimmed: Vec<(i64, f64)> = raw.iter().copied().filter(|(t, _)| *t >= cutoff).collect();
    let pts = if trimmed.len() >= 2 { trimmed } else { raw };

    // Rebase to the window open: the curve answers "what did this window
    // make", the same question the board's pnl column asks.
    let base = pts[0].1;
    let pts: Vec<(i64, f64)> = downsample(pts, MAX_POINTS)
        .into_iter()
        .map(|(t, v)| (t, cents(v - base)))
        .collect();

    let (mut high, mut low) = (f64::MIN, f64::MAX);
    let (mut peak, mut dd, mut dd_pct) = (f64::MIN, 0.0f64, 0.0f64);
    for (_, v) in &pts {
        high = high.max(*v);
        low = low.min(*v);
        peak = peak.max(*v);
        let fall = peak - v;
        if fall > dd {
            dd = fall;
            // Against the peak it fell from, not against equity — this module
            // never sees equity, and claiming a percent of it would be a lie.
            dd_pct = if peak > 0.0 { fall / peak * 100.0 } else { 0.0 };
        }
    }

    Curve {
        address: address.to_string(),
        days,
        period: period.to_string(),
        start_ms: pts[0].0,
        end_ms: pts[pts.len() - 1].0,
        pnl: pts[pts.len() - 1].1,
        high: cents(high),
        low: cents(low),
        max_drawdown: cents(dd),
        max_drawdown_pct: (dd_pct * 10.0).round() / 10.0,
        points: pts.into_iter().map(|(t, v)| [t as f64, v]).collect(),
        available: true,
        note: None,
    }
}

/// `0x` + 40 hex characters. Checked before spending an upstream call,
/// because Hyperliquid answers a malformed address with an error that would
/// otherwise be reported to the user as "rate-limited" — blaming the exchange
/// for a typo is how a five-second fix becomes a support thread.
fn is_wallet(addr: &str) -> bool {
    addr.len() == 42
        && addr.starts_with("0x")
        && addr[2..].chars().all(|c| c.is_ascii_hexdigit())
}

/// Fetch and shape one wallet's window curve.
///
/// Never returns an error: a curve is decoration on a row that already has
/// its numbers, so upstream trouble degrades to `available: false` with a
/// sentence the UI can print, not a status code that paints the row red.
/// Each sentence names the actual cause — a curve that says "rate-limited"
/// when it means "no such wallet" is worse than no curve.
pub async fn trader_curve(hl: Arc<Client>, address: &str, days: u32) -> Curve {
    let addr = address.trim().to_lowercase();
    if !is_wallet(&addr) {
        return Curve::empty(&addr, days, "not a wallet address — expected 0x and 40 hex characters");
    }
    match tokio::time::timeout(UPSTREAM_BUDGET, hl.user_pnl(&addr)).await {
        Ok(Ok(v)) => shape(&addr, days, &v, chrono::Utc::now().timestamp_millis()),
        Ok(Err(e)) => {
            let msg = e.to_string();
            tracing::warn!("curve: portfolio fetch failed for {addr}: {msg}");
            Curve::empty(&addr, days, if msg.contains("429") {
                "hyperliquid is rate-limiting right now — try again in a moment"
            } else {
                "hyperliquid could not answer for this wallet right now"
            })
        }
        Err(_) => {
            tracing::warn!("curve: portfolio fetch timed out for {addr}");
            Curve::empty(&addr, days, "hyperliquid did not answer in time — try again in a moment")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const HOUR: i64 = 3_600_000;
    const NOW: i64 = 1_788_298_563_175;

    /// A portfolio payload in HL's exact shape: string values, one row per
    /// period, periods we don't ask for included so selection is tested too.
    fn portfolio(week: Vec<(i64, &str)>) -> Value {
        json!([
            ["day", { "accountValueHistory": [], "pnlHistory": [[NOW, "1.0"]], "vlm": "0.0" }],
            ["week", {
                "accountValueHistory": [],
                "pnlHistory": week.iter().map(|(t, v)| json!([t, v])).collect::<Vec<_>>(),
                "vlm": "0.0"
            }],
            ["allTime", { "accountValueHistory": [], "pnlHistory": [], "vlm": "0.0" }]
        ])
    }

    #[test]
    fn period_follows_the_board_windows() {
        assert_eq!(period_for_days(1), "day");
        assert_eq!(period_for_days(7), "week");
        assert_eq!(period_for_days(30), "month");
        assert_eq!(period_for_days(90), "allTime");
    }

    #[test]
    fn shapes_a_week_and_rebases_to_the_window_open() {
        let p = portfolio(vec![
            (NOW - 72 * HOUR, "1000.0"),
            (NOW - 48 * HOUR, "1500.0"),
            (NOW - 24 * HOUR, "800.0"),
            (NOW, "1200.0"),
        ]);
        let c = shape("0xabc", 7, &p, NOW);
        assert!(c.available);
        assert_eq!(c.address, "0xabc");
        assert_eq!(c.period, "week");
        assert_eq!(c.points.len(), 4);
        // rebased: the window opens at zero, not at HL's lifetime figure
        assert_eq!(c.points[0][1], 0.0);
        assert_eq!(c.pnl, 200.0);
        assert_eq!(c.high, 500.0);
        assert_eq!(c.low, -200.0);
        // peak +500 → trough -200 is a $700 fall, 140% of the peak
        assert_eq!(c.max_drawdown, 700.0);
        assert_eq!(c.max_drawdown_pct, 140.0);
    }

    #[test]
    fn trims_to_the_window_hl_runs_long() {
        // HL's "week" spans ~7.9 days; a 7d window must not price the extra.
        let p = portfolio(vec![
            (NOW - 190 * HOUR, "0.0"),      // outside 7d
            (NOW - 180 * HOUR, "-5000.0"),  // outside 7d
            (NOW - 100 * HOUR, "1000.0"),
            (NOW - 10 * HOUR, "1400.0"),
            (NOW, "1500.0"),
        ]);
        let c = shape("0xabc", 7, &p, NOW);
        assert_eq!(c.points.len(), 3, "the two pre-cutoff samples are dropped");
        assert_eq!(c.pnl, 500.0, "rebased to the first sample inside the window");
        assert_eq!(c.max_drawdown, 0.0, "the pre-window slide is not this window's");
    }

    #[test]
    fn keeps_the_raw_series_when_trimming_would_empty_it() {
        // A wallet whose samples all predate the cutoff still gets its shape.
        let p = portfolio(vec![(NOW - 400 * HOUR, "0.0"), (NOW - 390 * HOUR, "250.0")]);
        let c = shape("0xabc", 7, &p, NOW);
        assert!(c.available);
        assert_eq!(c.points.len(), 2);
        assert_eq!(c.pnl, 250.0);
    }

    #[test]
    fn downsamples_to_the_cap_keeping_both_ends() {
        let week: Vec<(i64, String)> = (0..400)
            .map(|i| (NOW - (399 - i) * 60_000, format!("{}.0", i)))
            .collect();
        let p = portfolio(week.iter().map(|(t, v)| (*t, v.as_str())).collect());
        let c = shape("0xabc", 7, &p, NOW);
        assert!(c.points.len() <= MAX_POINTS);
        assert_eq!(c.points[0][1], 0.0, "first sample is the rebase point");
        assert_eq!(c.pnl, 399.0, "last sample survives downsampling exactly");
        assert_eq!(c.end_ms, NOW);
    }

    #[test]
    fn a_flat_wallet_has_a_curve_and_no_drawdown() {
        let p = portfolio(vec![(NOW - 48 * HOUR, "10.0"), (NOW - 24 * HOUR, "10.0"), (NOW, "10.0")]);
        let c = shape("0xabc", 7, &p, NOW);
        assert!(c.available);
        assert_eq!((c.pnl, c.high, c.low, c.max_drawdown), (0.0, 0.0, 0.0, 0.0));
    }

    #[test]
    fn a_pure_loser_reports_the_whole_slide() {
        let p = portfolio(vec![(NOW - 48 * HOUR, "0.0"), (NOW - 24 * HOUR, "-300.0"), (NOW, "-900.0")]);
        let c = shape("0xabc", 7, &p, NOW);
        assert_eq!(c.pnl, -900.0);
        assert_eq!(c.max_drawdown, 900.0);
        assert_eq!(c.max_drawdown_pct, 0.0, "no positive peak ⇒ no percentage to quote");
    }

    #[test]
    fn missing_or_short_history_is_unavailable_not_an_error() {
        let c = shape("0xabc", 7, &portfolio(vec![]), NOW);
        assert!(!c.available && c.points.is_empty() && c.note.is_some());

        let c = shape("0xabc", 7, &portfolio(vec![(NOW, "1.0")]), NOW);
        assert!(!c.available, "one point is not a line");

        let c = shape("0xabc", 7, &Value::Null, NOW);
        assert!(!c.available && c.period == "week");
    }

    #[test]
    fn only_wallet_shaped_addresses_are_worth_a_call() {
        assert!(is_wallet("0x85ecf584f25db6f146718b86d493e33c5af72052"));
        assert!(!is_wallet("not-an-address"));
        assert!(!is_wallet("0x85ecf584f25db6f146718b86d493e33c5af7205"));  // 39 hex
        assert!(!is_wallet("85ecf584f25db6f146718b86d493e33c5af72052"));   // no 0x
        assert!(!is_wallet("0xZZecf584f25db6f146718b86d493e33c5af72052")); // not hex
        assert!(!is_wallet(""));
    }

    #[test]
    fn numbers_arrive_as_numbers_too() {
        let p = json!([["week", { "pnlHistory": [[NOW - HOUR, 100.0], [NOW, 250.5]] }]]);
        let c = shape("0xabc", 7, &p, NOW);
        assert!(c.available);
        assert_eq!(c.pnl, 150.5);
    }
}
