// The strats board — every strat you can put money behind, on one surface.
//
// A "strat" is one of exactly TWO types for now, matching the invest engine's
// own Position kinds:
//   trader — copy a single trader's book (kind "trader")
//   vault  — deposit into an HL vault, the leader trades it (kind "vault")
// More types will join later; user-composed baskets (Indexes) still exist as
// their own API and pages but are off this board until they return as a type.
//
// Sources (all cached, so a board build is cheap):
//   vaults   — stats-CDN vault dump (vaults.rs), per-window `pnls` series
//   traders  — leaderboard scrape (traders.rs::parse_lb_windows), HL's own
//              day/week/month ROI, which IS "return had you invested at
//              window start"
//
// Everything reported here is trailing, not predictive; the UI names it so.

use crate::traders::{parse_lb_windows, LbWindows, MIN_ACCOUNT_VALUE};
use serde::Serialize;

/// One row of the board, whatever its type.
#[derive(Debug, Serialize)]
pub struct StratRow {
    /// "trader" | "vault" — the two strat types.
    pub kind: &'static str,
    /// Trader address / vault address. With `kind`, enough for the UI to open
    /// the row's own page.
    pub id: String,
    pub name: String,
    /// Vault leader / the trader itself — the face on the card.
    pub by: String,
    /// Trailing 24h return annualized, percent. `None` = not measurable
    /// (no window data, dust basis) — render "—", never 0.
    pub apr_24h: Option<f64>,
    /// Trailing 7d return annualized, percent.
    pub apr_7d: Option<f64>,
    /// Raw trailing-window returns as ratios (+5% == 0.05, +102% == 1.02,
    /// −20% == −0.2), NOT annualized. `None` = window not measurable.
    pub roi_1d: Option<f64>,
    pub roi_7d: Option<f64>,
    pub roi_30d: Option<f64>,
    /// Recommendation score: roi_1d × roi_7d × roi_30d. Rewards strats that
    /// are up across all three horizons at once; `None` unless every window
    /// is measurable — a row missing a window is unscored, not zero.
    pub rec_score: Option<f64>,
    /// Money behind the row: vault TVL / trader equity.
    pub capital: f64,
    pub age_days: i64,
    /// ms epoch of the most recent fill we have actually seen for this row.
    /// `None` when this wallet isn't in the fills index yet: the board's
    /// liveness gate still guarantees a trader row traded inside 24h, but we
    /// won't invent a minute we never observed.
    pub last_trade_ms: Option<i64>,
    /// When that fill scan ran. A last trade is only as current as the look
    /// that found it, so the UI can say "as of" instead of implying we are
    /// watching the wallet live.
    pub last_trade_scanned_ms: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct StratsBoard {
    pub rows: Vec<StratRow>,
    pub vaults: usize,
    pub traders: usize,
    pub updated_ms: i64,
}

fn annualize(roi: Option<f64>, periods_per_year: f64) -> Option<f64> {
    roi.map(|r| r * periods_per_year * 100.0)
}

/// The recommendation score: the product of the three trailing window returns
/// as ratios. All three must be present — multiplying a made-up 0 in would
/// zero honest rows, and skipping a missing factor would inflate them.
/// (Sign quirk accepted by design: two negative windows multiply positive;
/// the UI shows the three factors next to the score so nothing hides.)
fn rec_score(r1: Option<f64>, r7: Option<f64>, r30: Option<f64>) -> Option<f64> {
    Some(r1? * r7? * r30?)
}

/// Windows the prewarm loop keeps in the fills index. A wallet's last fill is
/// the same instant whichever window scanned it, so take the freshest scan
/// that knows about it rather than insisting on one window.
const INDEXED_WINDOWS: [u32; 3] = [1, 7, 30];

/// Wallets one warm pass will scan. The board shows a few dozen rows; scanning
/// more than that per pass would spend HL calls on wallets nobody is looking at.
const WARM_CAP: usize = 40;
/// Window a warm pass scores. Wide enough to date a wallet that paused for a
/// few days, and it shares the trader board's own 7d index slot.
const WARM_WINDOW_DAYS: u32 = 7;
/// One warm pass at a time, however many people are looking at the board.
static WARMING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Scan fills for board rows whose last trade we could not name, in the
/// background, so the NEXT build of this board can print the minute instead of
/// falling back to "traded within 24h".
///
/// Deliberately not awaited: dating a row is decoration on a board that
/// already has its numbers, and `TraderIndex::enrich` walks Hyperliquid at
/// concurrency 2 precisely because /info punishes anything faster. The
/// response goes out now; the timestamps land on the next load.
fn warm_last_trades(s: &crate::AppState, addrs: Vec<String>) {
    use std::sync::atomic::Ordering;
    if addrs.is_empty() { return; }
    if WARMING.swap(true, Ordering::AcqRel) { return; }
    let index = s.index.clone();
    let hl = s.hl.clone();
    let syncs = s.syncs.clone();
    tokio::spawn(async move {
        let t0 = std::time::Instant::now();
        // Scan a week, not a day: a row can be on this board (or be a vault)
        // without having traded in the last 24h, and a 1d scan would come back
        // empty for exactly the wallets we could not date. The entry lands in
        // the same 7d slot the trader board keeps warm, so the work is shared.
        let n = index.enrich(&hl, WARM_WINDOW_DAYS, &addrs, None, 0).await;
        index.save();
        syncs.push(crate::sync::SyncEvent {
            ts_ms: chrono::Utc::now().timestamp_millis(),
            kind: "strats".into(),
            key: "last-trade".into(),
            ok: true,
            rows: n,
            duration_ms: t0.elapsed().as_millis() as u64,
            note: format!("dated {n}/{} undated board rows", addrs.len()),
        });
        WARMING.store(false, Ordering::Release);
    });
}

/// The most recent fill the fills index has ever seen for `addr`, across every
/// indexed window, and when the scan that saw it ran. `None` = never scanned
/// (or scanned and it had no fills in that window).
fn last_trade(index: &crate::traders::TraderIndex, addr: &str) -> Option<(i64, i64)> {
    INDEXED_WINDOWS.iter()
        .filter_map(|d| index.get(*d, addr))
        .filter(|e| e.stats.last_active > 0)
        .map(|e| (e.stats.last_active, e.scanned_at))
        .max_by_key(|(fill, _)| *fill)
}

/// Build the board. `vault_pool` / `trader_pool` cap the discovered rows.
pub async fn board(
    s: &crate::AppState,
    vault_pool: usize,
    trader_pool: usize,
    min_tvl: Option<f64>,
) -> StratsBoard {
    let now_ms = chrono::Utc::now().timestamp_millis();
    let lb_raw = s.hl.leaderboard().await.unwrap_or(serde_json::Value::Null);
    let lb = parse_lb_windows(&lb_raw);

    let mut rows: Vec<StratRow> = Vec::new();

    // ── vaults: CDN universe, ranked by trailing 7d APR ──
    let mut vlist = crate::vaults::top_vaults(s.hl.clone(), min_tvl, usize::MAX)
        .await.unwrap_or_default();
    vlist.sort_by(|a, b| {
        b.apr_7d.unwrap_or(f64::NEG_INFINITY)
            .partial_cmp(&a.apr_7d.unwrap_or(f64::NEG_INFINITY))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    vlist.truncate(vault_pool);
    let vaults = vlist.len();
    for v in vlist {
        let v_last = last_trade(&s.index, &v.address);
        rows.push(StratRow {
            kind: "vault",
            id: v.address.clone(),
            name: v.name.clone(),
            by: v.leader.clone(),
            apr_24h: v.apr_24h,
            apr_7d: v.apr_7d,
            roi_1d: v.roi_1d,
            roi_7d: v.roi_7d,
            roi_30d: v.roi_30d,
            rec_score: rec_score(v.roi_1d, v.roi_7d, v.roi_30d),
            capital: v.tvl,
            age_days: v.age_days,
            last_trade_ms: v_last.map(|(fill, _)| fill),
            last_trade_scanned_ms: v_last.map(|(_, seen)| seen),
        });
    }

    // ── traders: the copyable book, ranked by trailing 7d ROI. Same gates as
    // the traders board: real equity, traded in the last 24h. ──
    let mut tlist: Vec<(&String, &LbWindows)> = lb.iter()
        .filter(|(_, w)| w.account_value >= MIN_ACCOUNT_VALUE && w.day_vlm > 0.0
            && w.roi_week.is_some())
        .collect();
    tlist.sort_by(|a, b| {
        b.1.roi_week.unwrap_or(f64::NEG_INFINITY)
            .partial_cmp(&a.1.roi_week.unwrap_or(f64::NEG_INFINITY))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    tlist.truncate(trader_pool);
    let traders = tlist.len();
    for (addr, w) in tlist {
        let t_last = last_trade(&s.index, addr);
        rows.push(StratRow {
            kind: "trader",
            id: addr.clone(),
            name: format!("{}…{}", &addr[..6], &addr[addr.len() - 4..]),
            by: addr.clone(),
            apr_24h: annualize(w.roi_day, 365.0),
            apr_7d: annualize(w.roi_week, 365.0 / 7.0),
            roi_1d: w.roi_day,
            roi_7d: w.roi_week,
            roi_30d: w.roi_month,
            rec_score: rec_score(w.roi_day, w.roi_week, w.roi_month),
            capital: w.account_value,
            age_days: 0,
            last_trade_ms: t_last.map(|(fill, _)| fill),
            last_trade_scanned_ms: t_last.map(|(_, seen)| seen),
        });
    }

    // The board's order IS the recommendation: rec_score desc, unscored rows
    // fall back to their 7d APR footing, more capital breaks ties.
    rows.sort_by(|a, b| {
        let ka = (a.rec_score.unwrap_or(f64::NEG_INFINITY), a.apr_7d.unwrap_or(f64::NEG_INFINITY), a.capital);
        let kb = (b.rec_score.unwrap_or(f64::NEG_INFINITY), b.apr_7d.unwrap_or(f64::NEG_INFINITY), b.capital);
        kb.partial_cmp(&ka).unwrap_or(std::cmp::Ordering::Equal)
    });

    // Rows we could not date, dated in the background for the next load.
    let undated: Vec<String> = rows.iter()
        .filter(|r| r.last_trade_ms.is_none())
        .map(|r| r.id.clone())
        .take(WARM_CAP)
        .collect();
    warm_last_trades(s, undated);

    StratsBoard { rows, vaults, traders, updated_ms: now_ms }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::traders::{IndexEntry, TraderIndex};

    fn entry(last_active: i64) -> IndexEntry {
        IndexEntry {
            scanned_at: 1_700_000_000_000,
            stats: crate::stats::PerfStats { last_active, ..Default::default() },
        }
    }

    #[test]
    fn rec_score_is_the_product_of_window_ratios_and_needs_all_three() {
        // +102% · +50% · +10% — the ratios multiply, they do not add.
        let s = rec_score(Some(1.02), Some(0.5), Some(0.1)).unwrap();
        assert!((s - 0.051).abs() < 1e-12, "got {s}");
        // one red window drags the score negative
        let s = rec_score(Some(-0.2), Some(0.5), Some(0.1)).unwrap();
        assert!((s + 0.01).abs() < 1e-12, "got {s}");
        // any unmeasurable window → unscored, never a fabricated factor
        assert_eq!(rec_score(None, Some(0.5), Some(0.1)), None);
        assert_eq!(rec_score(Some(1.02), None, Some(0.1)), None);
        assert_eq!(rec_score(Some(1.02), Some(0.5), None), None);
    }

    #[test]
    fn last_trade_takes_the_freshest_window_that_knows_the_wallet() {
        let dir = std::env::temp_dir().join(format!("hl-lt-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let ix = TraderIndex::load(dir.to_str().unwrap());
        let a = "0xAbC0000000000000000000000000000000000001";

        // Never scanned: no timestamp, and the caller must not invent one.
        assert_eq!(last_trade(&ix, a), None);

        // The 30d scan saw an older fill than the 1d scan; the wallet's last
        // trade is the later instant, whichever window reported it — and it
        // comes back with the scan that saw it.
        ix.put(30, a, entry(1_000));
        ix.put(1, a, entry(9_000));
        assert_eq!(last_trade(&ix, a), Some((9_000, 1_700_000_000_000)));

        // A scanned window with no fills in it reports 0 — that is "nothing in
        // this window", not "traded at the epoch".
        let b = "0xAbC0000000000000000000000000000000000002";
        ix.put(1, b, entry(0));
        assert_eq!(last_trade(&ix, b), None);

        // Lookups are case-insensitive, like every other address key here.
        assert_eq!(last_trade(&ix, &a.to_lowercase()), Some((9_000, 1_700_000_000_000)));
    }
}
