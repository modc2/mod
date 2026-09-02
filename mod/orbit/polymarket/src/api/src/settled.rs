//! Win rate, from positions that actually settled.
//!
//! The accuracy column used to be reconstructed from `/activity`: a bought
//! position counted as *decided* once it was redeemed, fully exited, or seen
//! exiting at ≥95¢. That denominator is missing exactly one thing — the
//! losers.
//!
//! A winning position always leaves a trace. It is redeemed (a REDEEM row
//! with a payout) or sold into the 95–99¢ band, and either way `/activity`
//! reports it. A losing position leaves **nothing**: the market resolves the
//! other way, the tokens burn to zero, and there is no sell and no redeem to
//! book — nobody pays gas to claim $0. So the old rule dropped it from the
//! denominator, and a trader who redeems winners but holds losers to expiry
//! (which is most of them) scored 100%.
//!
//! Measured on 2026-09-02, 30D window, against `/closed-positions`:
//!
//! | trader     | shown | decided | dropped | cost in dropped | truth |
//! |------------|-------|---------|---------|-----------------|-------|
//! | AV23IUa    | 100%  | 88      | 95      | $3,529,384      | 92%   |
//! | 0xd570e634 | 100%  | 36      | 61      | $733,931        | 97%   |
//! | 0xd3a0b4e9 | 100%  | 5       | 7       | $829,782        | n/a   |
//!
//! The bias is one-directional by construction, so it can only ever read
//! high, and it reads highest for the traders staking the most.
//!
//! `/closed-positions` is the settled book: one row per position the market
//! has finished with, carrying `realizedPnl` and `curPrice` (1 = the outcome
//! won, 0 = it burned). Losers are in it. That makes win/loss a money
//! question — did this position return more than it cost — instead of a
//! question about which exits happen to be observable.
//!
//! Cost is bounded: rows come back newest-first, so paging stops at the
//! window edge. The heaviest trader measured needed 5 pages for 30 days,
//! against the 11 that `/activity` enrichment already spends on the same
//! wallet.

use std::collections::HashMap;

use serde::Deserialize;

/// Rows per page. data-api's `/closed-positions` maximum.
const PAGE: usize = 50;

/// Hard page ceiling per trader — ~5000 settled legs in one window is far
/// past any real roster, and the loop must not become unbounded if the
/// upstream sort ever stops honoring `sortDirection`.
const MAX_PAGES: usize = 100;

/// Attempts per page before giving up on the trader.
///
/// Enrichment runs 64 traders wide and this is a second paged endpoint on top
/// of the activity pull, so data-api answers 429 under a cold full-pool
/// rebuild. Without a retry the whole board reads "unknown" — safe, but
/// useless. Backoff doubles from `BASE_BACKOFF_MS`, and `Retry-After` wins
/// when the server sends one.
const MAX_ATTEMPTS: usize = 5;
const BASE_BACKOFF_MS: u64 = 500;

/// Concurrent settled-book fetches across the whole enrichment fan-out.
///
/// Retrying alone was not enough: at 64 traders wide every request was
/// throttled, every retry landed in the same storm, and 64 of 65 traders came
/// back "unknown". Capping the second endpoint is what actually fixes it —
/// the pull is bounded by data-api's patience, not by our parallelism, and
/// the activity fan-out above is unaffected.
const MAX_CONCURRENT: usize = 6;

fn gate() -> &'static tokio::sync::Semaphore {
    static GATE: std::sync::OnceLock<tokio::sync::Semaphore> = std::sync::OnceLock::new();
    GATE.get_or_init(|| tokio::sync::Semaphore::new(MAX_CONCURRENT))
}

/// One settled position: an outcome the market has finished deciding.
#[derive(Debug, Clone, Deserialize)]
pub struct SettledLeg {
    #[serde(default)]
    pub title: String,
    /// Net realized dollars on this position — proceeds (sells + redeem
    /// payout) less cost basis. The sign is the win/loss verdict.
    #[serde(rename = "realizedPnl", default)]
    pub realized_pnl: f64,
    /// Settlement price of the outcome held: 1 = it won, 0 = it burned.
    #[serde(rename = "curPrice", default)]
    pub cur_price: f64,
    /// Dollars bought into this position over its life.
    #[serde(rename = "totalBought", default)]
    pub total_bought: f64,
    #[serde(default)]
    pub timestamp: u64,
}

/// Wins/decided over the settled legs of one window, plus the same split per
/// market title so a filtered board can rescope it.
#[derive(Debug, Clone, Default)]
pub struct SettledAccuracy {
    pub wins: u32,
    pub decided: u32,
    /// Market title → (wins, decided).
    pub per_title: HashMap<String, (u32, u32)>,
}

/// A settled position won when it returned more money than it cost.
///
/// Deliberately not "the outcome resolved YES" — a position bought at 97¢ and
/// sold at 96¢ resolves the trader's way and still loses money, and the old
/// ≥95¢ rule booked it as a win. `curPrice` breaks the tie only when
/// `realizedPnl` is exactly zero, which is what an unpriced or dust row looks
/// like.
pub fn leg_won(leg: &SettledLeg) -> bool {
    if leg.realized_pnl != 0.0 {
        return leg.realized_pnl > 0.0;
    }
    leg.cur_price >= 0.99
}

/// Fold settled legs into win/decided counters, counting only legs that
/// settled inside the window.
///
/// Pure — this is the part worth testing, and it is what the HTTP paging
/// below exists to feed.
pub fn accuracy_from_legs(legs: &[SettledLeg], cutoff_sec: u64) -> SettledAccuracy {
    let mut acc = SettledAccuracy::default();
    for leg in legs {
        if leg.timestamp < cutoff_sec {
            continue;
        }
        // A row with no money on either side is a dust artifact, not a
        // trade the wallet made a call on.
        if leg.total_bought <= 0.0 && leg.realized_pnl == 0.0 {
            continue;
        }
        let won = leg_won(leg);
        acc.decided += 1;
        if won {
            acc.wins += 1;
        }
        if !leg.title.is_empty() {
            let e = acc.per_title.entry(leg.title.clone()).or_insert((0, 0));
            e.1 += 1;
            if won {
                e.0 += 1;
            }
        }
    }
    acc
}

/// Page `/closed-positions` back to the window edge.
///
/// `None` means the fetch failed — distinct from `Some(empty)`, which means
/// the trader genuinely settled nothing in the window. Callers must keep that
/// distinction: falling back to the activity-derived number on a failed fetch
/// would put the 100% back on screen.
pub async fn fetch_settled_legs(
    http: &reqwest::Client,
    base_url: &str,
    address: &str,
    cutoff_sec: u64,
) -> Option<Vec<SettledLeg>> {
    // Held for the whole paged pull: interleaving pages from 64 traders is
    // what triggers the throttle in the first place.
    let _permit = gate().acquire().await.ok()?;

    let mut out: Vec<SettledLeg> = Vec::new();
    let mut any_ok = false;

    for page in 0..MAX_PAGES {
        let url = format!(
            "{}/closed-positions?user={}&limit={}&offset={}&sortBy=TIMESTAMP&sortDirection=DESC",
            base_url,
            address,
            PAGE,
            page * PAGE
        );
        let mut rows: Option<Vec<SettledLeg>> = None;
        for attempt in 0..MAX_ATTEMPTS {
            match http.get(&url).send().await {
                Ok(resp) => {
                    let status = resp.status();
                    if status.as_u16() == 429 || status.is_server_error() {
                        let wait = resp
                            .headers()
                            .get("retry-after")
                            .and_then(|v| v.to_str().ok())
                            .and_then(|v| v.parse::<u64>().ok())
                            .map(|secs| secs.saturating_mul(1000))
                            .unwrap_or(BASE_BACKOFF_MS << attempt);
                        tokio::time::sleep(std::time::Duration::from_millis(wait)).await;
                        continue;
                    }
                    if !status.is_success() {
                        return None;
                    }
                    match resp.json::<Vec<SettledLeg>>().await {
                        Ok(r) => {
                            rows = Some(r);
                            break;
                        }
                        // A mid-paging decode failure leaves a truncated
                        // window, and a truncated window is a biased
                        // denominator again. Give up on the whole trader
                        // rather than score a partial one.
                        Err(_) => return None,
                    }
                }
                Err(_) => {
                    tokio::time::sleep(std::time::Duration::from_millis(
                        BASE_BACKOFF_MS << attempt,
                    ))
                    .await;
                }
            }
        }
        // Every attempt was throttled or errored — unknown, not a partial
        // denominator built from the pages that happened to get through.
        let rows = rows?;
        any_ok = true;
        let short = rows.len() < PAGE;
        let oldest = rows.iter().map(|r| r.timestamp).min().unwrap_or(0);
        out.extend(rows);
        // Rows arrive newest-first, so the first page reaching past the
        // cutoff has everything the window can contain.
        if short || oldest < cutoff_sec {
            break;
        }
    }

    if any_ok {
        Some(out)
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leg(pnl: f64, cur: f64, bought: f64, ts: u64, title: &str) -> SettledLeg {
        SettledLeg {
            title: title.to_string(),
            realized_pnl: pnl,
            cur_price: cur,
            total_bought: bought,
            timestamp: ts,
        }
    }

    /// The regression this module exists for: a wallet that redeems its
    /// winners and lets its losers expire. Under the activity-derived rule
    /// only the three redeemed winners were decided → 100%. The settled book
    /// has the burned legs in it.
    #[test]
    fn losers_that_expired_are_in_the_denominator() {
        let legs = vec![
            leg(500.0, 1.0, 300.0, 200, "A"),
            leg(500.0, 1.0, 300.0, 200, "B"),
            leg(500.0, 1.0, 300.0, 200, "C"),
            leg(-300.0, 0.0, 300.0, 200, "D"),
            leg(-300.0, 0.0, 300.0, 200, "E"),
        ];
        let acc = accuracy_from_legs(&legs, 100);
        assert_eq!((acc.wins, acc.decided), (3, 5));
    }

    /// Resolving your way is not the same as making money. The old rule
    /// booked any exit at ≥95¢ as a win; this one asks the ledger.
    #[test]
    fn a_winning_outcome_bought_too_high_is_a_loss() {
        let legs = vec![leg(-40.0, 1.0, 970.0, 200, "favorite")];
        let acc = accuracy_from_legs(&legs, 100);
        assert_eq!((acc.wins, acc.decided), (0, 1));
    }

    #[test]
    fn legs_settled_before_the_window_are_excluded() {
        let legs = vec![
            leg(100.0, 1.0, 50.0, 50, "old win"),
            leg(-50.0, 0.0, 50.0, 300, "in window loss"),
        ];
        let acc = accuracy_from_legs(&legs, 100);
        assert_eq!((acc.wins, acc.decided), (0, 1));
    }

    #[test]
    fn per_title_split_matches_the_totals() {
        let legs = vec![
            leg(10.0, 1.0, 5.0, 200, "Yankees"),
            leg(-5.0, 0.0, 5.0, 200, "Yankees"),
            leg(10.0, 1.0, 5.0, 200, "Election"),
        ];
        let acc = accuracy_from_legs(&legs, 100);
        assert_eq!(acc.per_title.get("Yankees"), Some(&(1, 2)));
        assert_eq!(acc.per_title.get("Election"), Some(&(1, 1)));
        let tw: u32 = acc.per_title.values().map(|v| v.0).sum();
        let td: u32 = acc.per_title.values().map(|v| v.1).sum();
        assert_eq!((tw, td), (acc.wins, acc.decided));
    }

    /// `curPrice` decides only when there is no money signal at all.
    #[test]
    fn zero_pnl_falls_back_to_settlement_price() {
        assert!(leg_won(&leg(0.0, 1.0, 10.0, 200, "won at cost")));
        assert!(!leg_won(&leg(0.0, 0.0, 10.0, 200, "burned at zero cost")));
    }

    #[test]
    fn dust_rows_are_not_decisions() {
        let legs = vec![leg(0.0, 0.0, 0.0, 200, "dust")];
        assert_eq!(accuracy_from_legs(&legs, 100).decided, 0);
    }
}
