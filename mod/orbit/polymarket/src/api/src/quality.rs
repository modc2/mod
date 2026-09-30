//! Quality control for leaderboard rows — "is this number something you can
//! copy, and did we see enough of the trader to trust it?"
//!
//! Every row the sweep keeps is graded here once, while the raw fills are
//! still in hand, and carries the verdict as `Trader.qc`. The board then
//! filters on it (`?qc=off|standard|strict`, see `routes.rs`). Pure functions
//! only — no I/O — so the thresholds are pinned by the tests at the bottom.
//!
//! What it catches, measured on the live 30D board (2026-09-30): 57 of the top
//! 100 BEST rows sold >5x what they bought in-window, 74 made >90% of their
//! P&L in one curve bucket, and the top five were four sybil wallets each
//! running the same 2c -> 98c round trip fifteen seconds apart. Polymarket's
//! own leaderboard agrees with those P&L figures — the arithmetic was right,
//! the traders were uncopyable. So QC is a filter over honest numbers, not a
//! correction of them.
//!
//! Severity decides what each mode hides:
//! - HARD  (`flash`, `cluster`)       hidden from `standard` up — not trading
//! - SOFT  (`one_hit`, `no_basis`)    hidden in `strict` — real, but not a record
//! - INFO  (`partial`)                never hidden — OUR data gap, not theirs
//!
//! A row with no `qc` (payload written before this existed) is KEPT in every
//! mode: unknown quality must not empty the board over a data gap, the same
//! rule `firstTradeTs` follows. The next hourly sweep grades it.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Round trip shorter than this, at a price jump of at least `FLASH_MIN_MOVE`,
/// is a flash trade: nobody copying off a feed can get either fill.
pub const FLASH_MAX_HOLD_SECS: u64 = 600;
/// Minimum `exit − avg entry` (in $ per share) for a quick round trip to count
/// as a flash. 50c inside ten minutes is a crossed book or a counterparty you
/// own, not a read on the market.
pub const FLASH_MIN_MOVE: f64 = 0.5;
/// Share of gross realized profit from flash trades that makes the row HARD-flagged.
pub const FLASH_SHARE_FLAG: f64 = 0.5;
/// Largest single realized gain as a share of gross realized profit.
pub const ONE_HIT_SHARE_FLAG: f64 = 0.8;
/// Below this gross realized profit the one-hit test is noise.
pub const ONE_HIT_MIN_GROSS: f64 = 50.0;
/// Share of in-window SOLD shares whose purchase we observed.
pub const BASIS_FLAG: f64 = 0.5;
/// Below this in-window sell notional the basis test is noise.
pub const BASIS_MIN_SELL_USD: f64 = 100.0;
/// Fraction of the ranking window the activity walk actually reached.
pub const COVERAGE_FLAG: f64 = 0.95;
/// Flash fills from different wallets on the same outcome token inside the
/// same bucket of this many seconds are one coordinated trade.
pub const CLUSTER_BUCKET_SECS: u64 = 300;
/// Fingerprints kept per trader — enough to link a cluster, bounded memory.
const MAX_FINGERPRINTS: usize = 16;

pub const FLAG_FLASH: &str = "flash";
pub const FLAG_CLUSTER: &str = "cluster";
pub const FLAG_ONE_HIT: &str = "one_hit";
pub const FLAG_NO_BASIS: &str = "no_basis";
pub const FLAG_PARTIAL: &str = "partial";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Hard,
    Soft,
    Info,
}

pub fn severity(flag: &str) -> Severity {
    match flag {
        FLAG_FLASH | FLAG_CLUSTER => Severity::Hard,
        FLAG_ONE_HIT | FLAG_NO_BASIS => Severity::Soft,
        _ => Severity::Info,
    }
}

/// Which rows a board read hides. `standard` is the server default so every
/// caller (console, MCP, strats) gets a board with the wash trades gone.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Off,
    Standard,
    Strict,
}

impl Mode {
    pub fn parse(s: Option<&str>) -> Mode {
        match s.map(|s| s.trim().to_ascii_lowercase()).as_deref() {
            Some("off") | Some("0") | Some("none") => Mode::Off,
            Some("strict") | Some("2") => Mode::Strict,
            _ => Mode::Standard,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Off => "off",
            Mode::Standard => "standard",
            Mode::Strict => "strict",
        }
    }
    /// Would this mode hide a row carrying `flags`?
    pub fn hides(self, flags: &[String]) -> bool {
        flags.iter().any(|f| match (self, severity(f)) {
            (Mode::Off, _) | (_, Severity::Info) => false,
            (Mode::Standard, Severity::Hard) => true,
            (Mode::Standard, Severity::Soft) => false,
            (Mode::Strict, _) => true,
        })
    }
}

/// The verdict a row carries. Ratios are 0–1; `-1` = not measurable (nothing
/// realized / nothing sold), same sentinel discipline as `winRate`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Quality {
    pub flags: Vec<String>,
    /// Fraction of the window the activity walk reached (1 = all of it).
    pub coverage: f64,
    /// Largest single realized gain ÷ gross realized profit.
    #[serde(rename = "topShare")]
    pub top_share: f64,
    /// Realized profit from flash round trips ÷ gross realized profit.
    #[serde(rename = "flashShare")]
    pub flash_share: f64,
    /// In-window sold shares with an observed purchase ÷ all sold shares.
    #[serde(rename = "basisCoverage")]
    pub basis_coverage: f64,
    /// Wallets (including this one) sharing a flash fingerprint. 1 = alone.
    #[serde(default = "one")]
    pub cluster: u32,
}

fn one() -> u32 {
    1
}

/// One realized SELL, as `compute_window_metrics` books it.
#[derive(Debug, Clone)]
pub struct Exit {
    /// Outcome token (asset id, or conditionId when the row has none).
    pub key: String,
    pub ts: u64,
    pub realized: f64,
    /// Seconds since the lot was opened (position went 0 → >0).
    pub hold_secs: u64,
    /// Exit price − average entry, $/share.
    pub move_per_share: f64,
}

impl Exit {
    pub fn is_flash(&self) -> bool {
        self.realized > 0.0
            && self.hold_secs <= FLASH_MAX_HOLD_SECS
            && self.move_per_share >= FLASH_MIN_MOVE
    }
    fn fingerprint(&self) -> String {
        format!("{}@{}", self.key, self.ts / CLUSTER_BUCKET_SECS)
    }
}

/// Everything the grader needs from one trader's in-window book.
#[derive(Debug, Default)]
pub struct Evidence {
    pub exits: Vec<Exit>,
    /// Shares sold in-window, and how many of those had an observed purchase.
    pub sold_shares: f64,
    pub matched_shares: f64,
    pub sell_usd: f64,
    /// Fraction of the window the activity walk covered.
    pub coverage: f64,
}

/// Grade one trader. Returns the verdict plus the flash fingerprints the
/// cross-trader `link_clusters` pass needs (not serialized — they only live
/// for the length of one sweep).
pub fn grade(ev: &Evidence) -> (Quality, Vec<String>) {
    let gross: f64 = ev.exits.iter().filter(|e| e.realized > 0.0).map(|e| e.realized).sum();
    let top = ev.exits.iter().map(|e| e.realized).fold(0.0f64, f64::max);
    let flash: f64 = ev.exits.iter().filter(|e| e.is_flash()).map(|e| e.realized).sum();

    let (top_share, flash_share) = if gross > 0.0 {
        (top / gross, flash / gross)
    } else {
        (-1.0, -1.0)
    };
    let basis_coverage = if ev.sold_shares > 0.0 {
        (ev.matched_shares / ev.sold_shares).clamp(0.0, 1.0)
    } else {
        -1.0
    };
    let coverage = ev.coverage.clamp(0.0, 1.0);

    let mut flags = Vec::new();
    if flash_share >= FLASH_SHARE_FLAG {
        flags.push(FLAG_FLASH.to_string());
    }
    if gross >= ONE_HIT_MIN_GROSS && top_share >= ONE_HIT_SHARE_FLAG {
        flags.push(FLAG_ONE_HIT.to_string());
    }
    if ev.sell_usd >= BASIS_MIN_SELL_USD && basis_coverage >= 0.0 && basis_coverage < BASIS_FLAG {
        flags.push(FLAG_NO_BASIS.to_string());
    }
    if coverage < COVERAGE_FLAG {
        flags.push(FLAG_PARTIAL.to_string());
    }

    let mut prints: Vec<(f64, String)> = ev
        .exits
        .iter()
        .filter(|e| e.is_flash())
        .map(|e| (e.realized, e.fingerprint()))
        .collect();
    prints.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
    let mut prints: Vec<String> = prints.into_iter().map(|(_, p)| p).collect();
    prints.dedup();
    prints.truncate(MAX_FINGERPRINTS);

    let q = Quality {
        flags,
        coverage: round3(coverage),
        top_share: round3(top_share),
        flash_share: round3(flash_share),
        basis_coverage: round3(basis_coverage),
        cluster: 1,
    };
    (q, prints)
}

/// Cross-trader pass: wallets that share a flash fingerprint (same outcome
/// token, same 5-minute bucket) are one operator on several addresses. Each
/// gets `cluster = size of its largest group` and the HARD `cluster` flag.
/// `rows[i]` = (fingerprints, &mut verdict) for trader i.
pub fn link_clusters(rows: &mut [(Vec<String>, &mut Quality)]) {
    let mut members: HashMap<&str, Vec<usize>> = HashMap::new();
    for (i, (prints, _)) in rows.iter().enumerate() {
        for p in prints {
            members.entry(p.as_str()).or_default().push(i);
        }
    }
    let mut size = vec![1u32; rows.len()];
    for idxs in members.values() {
        if idxs.len() < 2 {
            continue;
        }
        for &i in idxs {
            size[i] = size[i].max(idxs.len() as u32);
        }
    }
    for (i, (_, q)) in rows.iter_mut().enumerate() {
        q.cluster = size[i];
        if size[i] > 1 && !q.flags.iter().any(|f| f == FLAG_CLUSTER) {
            q.flags.push(FLAG_CLUSTER.to_string());
        }
    }
}

fn round3(x: f64) -> f64 {
    if x < 0.0 {
        x
    } else {
        (x * 1000.0).round() / 1000.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn exit(key: &str, ts: u64, realized: f64, hold: u64, mv: f64) -> Exit {
        Exit { key: key.into(), ts, realized, hold_secs: hold, move_per_share: mv }
    }

    fn ev(exits: Vec<Exit>) -> Evidence {
        Evidence { exits, sold_shares: 100.0, matched_shares: 100.0, sell_usd: 500.0, coverage: 1.0 }
    }

    #[test]
    fn wash_round_trip_is_flash() {
        // The live case: buy 20.8k @ 2c, sell @ 98c fifteen seconds later.
        let (q, prints) = grade(&ev(vec![exit("tok", 1_000, 19_969.0, 15, 0.96)]));
        assert!(q.flags.contains(&"flash".to_string()));
        assert!(q.flags.contains(&"one_hit".to_string()));
        assert_eq!(q.flash_share, 1.0);
        assert_eq!(prints.len(), 1);
        assert!(Mode::Standard.hides(&q.flags));
    }

    #[test]
    fn slow_underdog_win_is_not_flash() {
        // Same price jump held for six hours is a real call on the market.
        let (q, prints) = grade(&ev(vec![exit("tok", 1_000, 900.0, 6 * 3600, 0.9), exit("b", 2_000, 800.0, 60, 0.05)]));
        assert!(!q.flags.contains(&"flash".to_string()));
        assert!(prints.is_empty());
    }

    #[test]
    fn steady_record_is_clean() {
        let exits = (0..20).map(|i| exit(&format!("m{i}"), i * 10_000, 50.0 + i as f64, 7200, 0.1)).collect();
        let (q, _) = grade(&ev(exits));
        assert!(q.flags.is_empty(), "{:?}", q.flags);
        assert!(!Mode::Strict.hides(&q.flags));
    }

    #[test]
    fn one_hit_is_soft() {
        let (q, _) = grade(&ev(vec![exit("a", 0, 1_000.0, 7200, 0.2), exit("b", 0, 20.0, 7200, 0.1)]));
        assert_eq!(q.flags, vec!["one_hit".to_string()]);
        assert!(!Mode::Standard.hides(&q.flags));
        assert!(Mode::Strict.hides(&q.flags));
        assert!(!Mode::Off.hides(&q.flags));
    }

    #[test]
    fn unmatched_sells_flag_no_basis() {
        let mut e = ev(vec![exit("a", 0, 10.0, 7200, 0.1)]);
        e.sold_shares = 1_000.0;
        e.matched_shares = 100.0;
        let (q, _) = grade(&e);
        assert!(q.flags.contains(&"no_basis".to_string()));
        assert_eq!(q.basis_coverage, 0.1);
        // Tiny books are noise, not a finding.
        e.sell_usd = 20.0;
        assert!(!grade(&e).0.flags.contains(&"no_basis".to_string()));
    }

    #[test]
    fn partial_window_is_info_only() {
        let mut e = ev(vec![]);
        e.coverage = 0.4;
        let (q, _) = grade(&e);
        assert_eq!(q.flags, vec!["partial".to_string()]);
        assert!(!Mode::Strict.hides(&q.flags));
        assert_eq!(q.top_share, -1.0);
    }

    #[test]
    fn sybil_wallets_link_into_one_cluster() {
        let (mut a, pa) = grade(&ev(vec![exit("tok", 1_000, 20_000.0, 15, 0.96)]));
        let (mut b, pb) = grade(&ev(vec![exit("tok", 1_100, 20_100.0, 20, 0.96)]));
        let (mut c, pc) = grade(&ev(vec![exit("tok", 1_050, 20_050.0, 12, 0.96)]));
        let (mut d, pd) = grade(&ev(vec![exit("other", 1_000, 50.0, 15, 0.96)]));
        let mut rows = vec![(pa, &mut a), (pb, &mut b), (pc, &mut c), (pd, &mut d)];
        link_clusters(&mut rows);
        assert_eq!(a.cluster, 3);
        assert!(a.flags.contains(&"cluster".to_string()));
        assert_eq!(d.cluster, 1);
        assert!(!d.flags.contains(&"cluster".to_string()));
    }

    #[test]
    fn mode_parse_defaults_to_standard() {
        assert_eq!(Mode::parse(None), Mode::Standard);
        assert_eq!(Mode::parse(Some("OFF")), Mode::Off);
        assert_eq!(Mode::parse(Some("strict")), Mode::Strict);
        assert_eq!(Mode::parse(Some("bogus")), Mode::Standard);
    }
}
