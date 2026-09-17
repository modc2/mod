// The unified strats board — every investable thing on one surface.
//
// A "strat" here is anything you can put money behind: a user-composed basket
// (an Index), an HL vault, or a single copyable trader. All three answer the
// same two questions — "what would a deposit made 24h ago have annualized
// to?" and "what about 7d ago?" — so they can share one board and one metric.
//
// Sources (all cached, so a board build is cheap):
//   baskets  — store.list_indexes(), legs priced off the leaderboard scrape
//   vaults   — stats-CDN vault dump (vaults.rs), per-window `pnls` series
//   traders  — leaderboard scrape (traders.rs::parse_lb_windows), HL's own
//              day/week ROI, which IS "return had you invested at window
//              start"
//
// Everything reported here is trailing, not predictive; the UI names it so.

use crate::traders::{parse_lb_windows, LbWindows, MIN_ACCOUNT_VALUE};
use serde::Serialize;
use std::collections::HashMap;

/// One row of the board, whatever its kind.
#[derive(Debug, Serialize)]
pub struct StratRow {
    /// "basket" | "vault" | "trader"
    pub kind: &'static str,
    /// Basket id (uuid) / vault address / trader address. With `kind`, enough
    /// for the UI to open the row's own page.
    pub id: String,
    pub name: String,
    /// Basket owner / vault leader / the trader itself — the face on the card.
    pub by: String,
    /// Trailing 24h return annualized, percent. `None` = not measurable
    /// (no window data, dust basis) — render "—", never 0.
    pub apr_24h: Option<f64>,
    /// Trailing 7d return annualized, percent.
    pub apr_7d: Option<f64>,
    /// Money behind the row: vault TVL / trader equity / Σ leg equity.
    pub capital: f64,
    /// Basket legs (0 for vaults and traders).
    pub legs: usize,
    /// Legs the leaderboard could actually price — when below `legs`, the
    /// basket APRs rest on partial coverage and the UI should say so.
    pub legs_priced: usize,
    pub age_days: i64,
    /// Basket's linked vault, when it has one.
    pub vault_address: Option<String>,
    /// ms epoch of the most recent fill we have actually seen for this row —
    /// the trader's own last fill, or the freshest leg of a basket. `None`
    /// when this wallet isn't in the fills index yet: the board's liveness
    /// gate still guarantees a trader row traded inside 24h, but we won't
    /// invent a minute we never observed.
    pub last_trade_ms: Option<i64>,
}

#[derive(Debug, Serialize)]
pub struct StratsBoard {
    pub rows: Vec<StratRow>,
    pub baskets: usize,
    pub vaults: usize,
    pub traders: usize,
    pub updated_ms: i64,
}

fn annualize(roi: Option<f64>, periods_per_year: f64) -> Option<f64> {
    roi.map(|r| r * periods_per_year * 100.0)
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
/// indexed window. `None` = never scanned (or scanned and it had no fills).
fn last_trade(index: &crate::traders::TraderIndex, addr: &str) -> Option<i64> {
    INDEXED_WINDOWS.iter()
        .filter_map(|d| index.get(*d, addr))
        .map(|e| e.stats.last_active)
        .filter(|t| *t > 0)
        .max()
}

/// Weight-sum a basket's leg ROIs for one window. `None` when not a single
/// leg is on the leaderboard — a basket of ghosts has no measurable window.
fn basket_roi(
    legs: &[crate::store::IndexLeg],
    lb: &HashMap<String, LbWindows>,
    pick: impl Fn(&LbWindows) -> Option<f64>,
) -> Option<f64> {
    let mut sum = 0.0;
    let mut any = false;
    for l in legs {
        if let Some(r) = lb.get(&l.address.to_lowercase()).and_then(&pick) {
            sum += r * l.weight;
            any = true;
        }
    }
    any.then_some(sum)
}

/// Build the board. `vault_pool` / `trader_pool` cap the discovered rows;
/// every stored basket is always included.
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

    // ── baskets: every saved strat, legs priced off the same scrape ──
    let indexes = s.store.list_indexes();
    let baskets = indexes.len();
    for idx in indexes {
        let priced = idx.legs.iter()
            .filter(|l| lb.contains_key(&l.address.to_lowercase())).count();
        let capital: f64 = idx.legs.iter()
            .filter_map(|l| lb.get(&l.address.to_lowercase()))
            .map(|w| w.account_value).sum();
        rows.push(StratRow {
            kind: "basket",
            id: idx.id.clone(),
            name: idx.name.clone(),
            by: idx.owner.clone(),
            apr_24h: annualize(basket_roi(&idx.legs, &lb, |w| w.roi_day), 365.0),
            apr_7d: annualize(basket_roi(&idx.legs, &lb, |w| w.roi_week), 365.0 / 7.0),
            capital,
            legs: idx.legs.len(),
            legs_priced: priced,
            age_days: ((now_ms - idx.created_ms).max(0)) / 86_400_000,
            vault_address: idx.vault_address.clone(),
            // A basket is as live as its liveliest leg.
            last_trade_ms: idx.legs.iter()
                .filter_map(|l| last_trade(&s.index, &l.address)).max(),
        });
    }

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
        rows.push(StratRow {
            kind: "vault",
            id: v.address.clone(),
            name: v.name.clone(),
            by: v.leader.clone(),
            apr_24h: v.apr_24h,
            apr_7d: v.apr_7d,
            capital: v.tvl,
            legs: 0,
            legs_priced: 0,
            age_days: v.age_days,
            last_trade_ms: last_trade(&s.index, &v.address),
            vault_address: Some(v.address),
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
        rows.push(StratRow {
            kind: "trader",
            id: addr.clone(),
            name: format!("{}…{}", &addr[..6], &addr[addr.len() - 4..]),
            by: addr.clone(),
            apr_24h: annualize(w.roi_day, 365.0),
            apr_7d: annualize(w.roi_week, 365.0 / 7.0),
            capital: w.account_value,
            legs: 0,
            legs_priced: 0,
            age_days: 0,
            vault_address: None,
            last_trade_ms: last_trade(&s.index, addr),
        });
    }

    // Rows we could not date, dated in the background for the next load.
    let undated: Vec<String> = rows.iter()
        .filter(|r| r.last_trade_ms.is_none() && r.kind != "basket")
        .map(|r| r.id.clone())
        .take(WARM_CAP)
        .collect();
    warm_last_trades(s, undated);

    StratsBoard { rows, baskets, vaults, traders, updated_ms: now_ms }
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
    fn last_trade_takes_the_freshest_window_that_knows_the_wallet() {
        let dir = std::env::temp_dir().join(format!("hl-lt-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let ix = TraderIndex::load(dir.to_str().unwrap());
        let a = "0xAbC0000000000000000000000000000000000001";

        // Never scanned: no timestamp, and the caller must not invent one.
        assert_eq!(last_trade(&ix, a), None);

        // The 30d scan saw an older fill than the 1d scan; the wallet's last
        // trade is the later instant, whichever window reported it.
        ix.put(30, a, entry(1_000));
        ix.put(1, a, entry(9_000));
        assert_eq!(last_trade(&ix, a), Some(9_000));

        // A scanned window with no fills in it reports 0 — that is "nothing in
        // this window", not "traded at the epoch".
        let b = "0xAbC0000000000000000000000000000000000002";
        ix.put(1, b, entry(0));
        assert_eq!(last_trade(&ix, b), None);

        // Lookups are case-insensitive, like every other address key here.
        assert_eq!(last_trade(&ix, &a.to_lowercase()), Some(9_000));
    }
}
