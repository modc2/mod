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
        });
    }

    StratsBoard { rows, baskets, vaults, traders, updated_ms: now_ms }
}
