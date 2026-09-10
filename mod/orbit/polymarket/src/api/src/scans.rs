//! Scan history — every warmup cycle archived as a timestamped snapshot.
//!
//! The background sweep (pipeline.rs `warmup_cycle`) rebuilds the trader
//! leaderboards on the owner's cadence, and until now each rebuild OVERWROTE
//! the last one: the console could only ever show "the data as of the latest
//! sync". This module keeps each cycle as its own snapshot so the console can
//! step back through previous scans — "the 30D board as it stood at 14:00" —
//! and show which windows each scan actually holds.
//!
//! Layout, under the durable state dir (NOT /tmp — history must survive a
//! reboot):
//!
//!   scans/<unix-secs>/scan.json          — meta: when, what triggered it,
//!                                          which windows it holds/skipped
//!   scans/<unix-secs>/<days>_<mpd>_<pool>.json.gz
//!                                        — one gzipped AggPayload per window
//!
//! The payload serializes WITHOUT `market_metrics` (`#[serde(skip)]`, same as
//! the live disk cache) — the per-market breakdown is ~6× the payload and a
//! historical board falls back to title-based filtering exactly like a
//! disk-cache read does today. Gzipped, a 4-window hourly scan is ~10–15MB,
//! so pruning caps history by total bytes and scan count rather than age.

use std::io::{Read, Write};
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::types::AggPayload;

/// ~2 GiB of history ≈ a week of hourly 4-window scans. Overridable because
/// hosts differ, but there must always be a cap: this grows forever otherwise.
const DEFAULT_MAX_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const DEFAULT_MAX_SCANS: usize = 400;

/// One window inside a scan: what was archived, or why nothing was.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanWindowMeta {
    /// The pipeline cache key (`days:minPerDay:pool`) — also how the console
    /// asks for this window back.
    pub key: String,
    pub days: u32,
    #[serde(rename = "minPerDay")]
    pub min_per_day: f64,
    pub pool: u32,
    /// Rows in the archived payload. 0 when skipped or errored.
    pub count: usize,
    /// When the underlying data was pulled from Polymarket. For a skipped
    /// window this is the STILL-CURRENT previous sync — the honesty field:
    /// "this scan didn't re-pull 30D, the data it saw was from 13:58".
    #[serde(rename = "syncedAt")]
    pub synced_at: i64,
    /// Gzipped size on disk; 0 when nothing was archived.
    pub bytes: u64,
    /// The window was fresh enough that the cycle didn't re-pull it. No
    /// archive is written — the previous scan holds this data.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub skipped: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScanMeta {
    /// Unix seconds when the cycle started — also the directory name, which
    /// is what makes ids collision-free (`begin` bumps past an existing dir).
    pub id: i64,
    #[serde(rename = "startedAt")]
    pub started_at: i64,
    /// None while the cycle is still running (or died mid-cycle).
    #[serde(rename = "finishedAt", default)]
    pub finished_at: Option<i64>,
    /// "scheduled" | "manual" — same labels as /sync/status.
    pub trigger: String,
    pub windows: Vec<ScanWindowMeta>,
}

pub struct ScanStore {
    dir: PathBuf,
    max_bytes: u64,
    max_scans: usize,
}

impl ScanStore {
    pub fn new() -> Self {
        let max_bytes = std::env::var("POLYMARKET_SCANS_MAX_BYTES")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(DEFAULT_MAX_BYTES);
        let max_scans = std::env::var("POLYMARKET_SCANS_MAX")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(DEFAULT_MAX_SCANS);
        Self::with_dir(crate::access::state_dir().join("scans"), max_bytes, max_scans)
    }

    pub fn with_dir(dir: PathBuf, max_bytes: u64, max_scans: usize) -> Self {
        std::fs::create_dir_all(&dir).ok();
        Self { dir, max_bytes, max_scans }
    }

    fn scan_dir(&self, id: i64) -> PathBuf {
        self.dir.join(id.to_string())
    }

    fn meta_path(&self, id: i64) -> PathBuf {
        self.scan_dir(id).join("scan.json")
    }

    fn window_path(&self, id: i64, key: &str) -> PathBuf {
        // Same `:` → `_` mapping as the live cache's filenames.
        self.scan_dir(id).join(format!("{}.json.gz", key.replace(':', "_")))
    }

    /// Open a new scan. Returns its id (unix seconds, bumped past any
    /// existing dir so two cycles in the same second can't share one).
    pub fn begin(&self, trigger: &str) -> i64 {
        let now = chrono::Utc::now().timestamp();
        let mut id = now;
        while self.scan_dir(id).exists() {
            id += 1;
        }
        let meta = ScanMeta {
            id,
            started_at: now,
            finished_at: None,
            trigger: trigger.to_string(),
            windows: Vec::new(),
        };
        std::fs::create_dir_all(self.scan_dir(id)).ok();
        self.write_meta(&meta);
        id
    }

    /// Archive one warmed window. Best-effort: an IO failure loses this
    /// window's history, never the cycle — the live cache write already
    /// happened and is what trading reads.
    pub fn record_window(&self, id: i64, days: u32, min_per_day: f64, pool: u32, payload: &AggPayload) {
        let key = format!("{}:{}:{}", days, min_per_day, pool);
        let bytes = match serde_json::to_vec(payload) {
            Ok(json) => {
                let mut enc = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::new(6));
                let gz = enc
                    .write_all(&json)
                    .and_then(|_| enc.finish())
                    .unwrap_or_default();
                let path = self.window_path(id, &key);
                let len = gz.len() as u64;
                match std::fs::write(&path, gz) {
                    Ok(()) => len,
                    Err(e) => {
                        tracing::warn!(scan = id, key = %key, error = %e, "could not archive scan window");
                        0
                    }
                }
            }
            Err(e) => {
                tracing::warn!(scan = id, key = %key, error = %e, "could not serialize scan window");
                0
            }
        };
        self.push_window(id, ScanWindowMeta {
            key,
            days,
            min_per_day,
            pool,
            count: payload.count,
            synced_at: payload.synced_at,
            bytes,
            skipped: false,
            error: None,
        });
    }

    /// The cycle judged this window fresh enough to leave alone. Recorded so
    /// the coverage view can say "not re-pulled — data from `synced_at` still
    /// current" instead of showing a hole.
    pub fn record_skip(&self, id: i64, days: u32, min_per_day: f64, pool: u32, synced_at: i64) {
        self.push_window(id, ScanWindowMeta {
            key: format!("{}:{}:{}", days, min_per_day, pool),
            days,
            min_per_day,
            pool,
            count: 0,
            synced_at,
            bytes: 0,
            skipped: true,
            error: None,
        });
    }

    pub fn record_error(&self, id: i64, days: u32, min_per_day: f64, pool: u32, error: &str) {
        self.push_window(id, ScanWindowMeta {
            key: format!("{}:{}:{}", days, min_per_day, pool),
            days,
            min_per_day,
            pool,
            count: 0,
            synced_at: 0,
            bytes: 0,
            skipped: false,
            error: Some(error.to_string()),
        });
    }

    /// Stamp the cycle finished and prune old scans past the byte/count caps.
    ///
    /// A cycle that skipped EVERYTHING (typical right after a restart — all
    /// windows still fresh) is discarded rather than kept: it archived no
    /// data, and a restart loop would otherwise fill the history with
    /// timestamped rows of nothing.
    pub fn finish(&self, id: i64) {
        if let Some(mut meta) = self.read_meta(id) {
            if !meta.windows.is_empty() && meta.windows.iter().all(|w| w.skipped) {
                std::fs::remove_dir_all(self.scan_dir(id)).ok();
                return;
            }
            meta.finished_at = Some(chrono::Utc::now().timestamp());
            self.write_meta(&meta);
        }
        self.prune();
    }

    /// Newest first. `limit` bounds the JSON the console pulls per poll, not
    /// what is kept on disk.
    pub fn list(&self, limit: usize) -> Vec<ScanMeta> {
        let mut ids = self.all_ids();
        ids.sort_unstable_by(|a, b| b.cmp(a));
        ids.into_iter()
            .take(limit)
            .filter_map(|id| self.read_meta(id))
            .collect()
    }

    /// The archived leaderboard for one window of one scan. None when the
    /// scan was pruned, the window was skipped that cycle, or the archive is
    /// unreadable — the route turns all three into the same 404 and the
    /// console falls back to live.
    pub fn read_window(&self, id: i64, key: &str) -> Option<AggPayload> {
        let gz = std::fs::read(self.window_path(id, key)).ok()?;
        let mut dec = flate2::read::GzDecoder::new(&gz[..]);
        let mut json = Vec::new();
        dec.read_to_end(&mut json).ok()?;
        serde_json::from_slice(&json).ok()
    }

    fn all_ids(&self) -> Vec<i64> {
        let entries = match std::fs::read_dir(&self.dir) {
            Ok(e) => e,
            Err(_) => return Vec::new(),
        };
        entries
            .filter_map(|e| e.ok())
            .filter_map(|e| e.file_name().to_string_lossy().parse::<i64>().ok())
            .collect()
    }

    fn read_meta(&self, id: i64) -> Option<ScanMeta> {
        let raw = std::fs::read_to_string(self.meta_path(id)).ok()?;
        serde_json::from_str(&raw).ok()
    }

    fn write_meta(&self, meta: &ScanMeta) {
        if let Ok(json) = serde_json::to_string(meta) {
            if let Err(e) = std::fs::write(self.meta_path(meta.id), json) {
                tracing::warn!(scan = meta.id, error = %e, "could not write scan meta");
            }
        }
    }

    fn push_window(&self, id: i64, w: ScanWindowMeta) {
        let Some(mut meta) = self.read_meta(id) else {
            tracing::warn!(scan = id, key = %w.key, "scan meta missing — window not recorded");
            return;
        };
        // A re-run of the same key inside one cycle replaces its entry.
        meta.windows.retain(|x| x.key != w.key);
        meta.windows.push(w);
        self.write_meta(&meta);
    }

    /// Delete oldest scans until history fits the caps. The newest scan is
    /// always kept even if it alone exceeds the byte cap — zero history would
    /// make the whole feature disappear whenever a payload grows.
    fn prune(&self) {
        let mut ids = self.all_ids();
        ids.sort_unstable_by(|a, b| b.cmp(a)); // newest first
        let mut total: u64 = 0;
        for (i, id) in ids.iter().enumerate() {
            let size = self.dir_size(*id);
            total += size;
            let over = i > 0 && (total > self.max_bytes || i + 1 > self.max_scans);
            if over {
                if let Err(e) = std::fs::remove_dir_all(self.scan_dir(*id)) {
                    tracing::warn!(scan = id, error = %e, "could not prune scan");
                } else {
                    total -= size;
                }
            }
        }
    }

    fn dir_size(&self, id: i64) -> u64 {
        std::fs::read_dir(self.scan_dir(id))
            .map(|entries| {
                entries
                    .filter_map(|e| e.ok())
                    .filter_map(|e| e.metadata().ok())
                    .map(|m| m.len())
                    .sum()
            })
            .unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_store(max_bytes: u64, max_scans: usize) -> ScanStore {
        let dir = std::env::temp_dir().join(format!(
            "polymarket-scans-test-{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        ScanStore::with_dir(dir, max_bytes, max_scans)
    }

    fn payload(count: usize) -> AggPayload {
        AggPayload {
            count,
            candidate_pool: 2000,
            days_window: 30,
            min_trades_per_day: 0.0,
            synced_at: 1_700_000_000,
            traders: Vec::new(),
        }
    }

    #[test]
    fn a_scan_round_trips_through_the_archive() {
        let s = tmp_store(u64::MAX, 100);
        let id = s.begin("scheduled");
        s.record_window(id, 30, 0.0, 2000, &payload(42));
        s.record_skip(id, 7, 0.0, 2000, 1_699_999_000);
        s.record_error(id, 1, 0.0, 2000, "upstream 429");
        s.finish(id);

        let scans = s.list(10);
        assert_eq!(scans.len(), 1);
        let m = &scans[0];
        assert_eq!(m.id, id);
        assert!(m.finished_at.is_some());
        assert_eq!(m.trigger, "scheduled");
        assert_eq!(m.windows.len(), 3);
        let archived = m.windows.iter().find(|w| w.key == "30:0:2000").unwrap();
        assert_eq!(archived.count, 42);
        assert!(archived.bytes > 0);
        assert!(!archived.skipped);
        let skipped = m.windows.iter().find(|w| w.key == "7:0:2000").unwrap();
        assert!(skipped.skipped);
        assert_eq!(skipped.synced_at, 1_699_999_000);
        let errored = m.windows.iter().find(|w| w.key == "1:0:2000").unwrap();
        assert_eq!(errored.error.as_deref(), Some("upstream 429"));

        // The archived window reads back as the payload that went in; the
        // skipped one reads back as nothing (the previous scan holds it).
        let back = s.read_window(id, "30:0:2000").unwrap();
        assert_eq!(back.count, 42);
        assert_eq!(back.synced_at, 1_700_000_000);
        assert!(s.read_window(id, "7:0:2000").is_none());

        std::fs::remove_dir_all(&s.dir).ok();
    }

    #[test]
    fn scans_list_newest_first_and_ids_never_collide() {
        let s = tmp_store(u64::MAX, 100);
        // Two begins in the same second must still be two scans.
        let a = s.begin("scheduled");
        let b = s.begin("manual");
        assert_ne!(a, b);
        let scans = s.list(10);
        assert_eq!(scans.len(), 2);
        assert!(scans[0].id > scans[1].id, "newest first");
        std::fs::remove_dir_all(&s.dir).ok();
    }

    #[test]
    fn prune_drops_oldest_past_the_count_cap_but_keeps_the_newest() {
        let s = tmp_store(u64::MAX, 2);
        let a = s.begin("scheduled");
        s.record_window(a, 30, 0.0, 2000, &payload(1));
        s.finish(a);
        let b = s.begin("scheduled");
        s.record_window(b, 30, 0.0, 2000, &payload(2));
        s.finish(b);
        let c = s.begin("scheduled");
        s.record_window(c, 30, 0.0, 2000, &payload(3));
        s.finish(c);

        let scans = s.list(10);
        assert_eq!(scans.len(), 2);
        assert_eq!(scans[0].id, c);
        assert_eq!(scans[1].id, b);
        assert!(s.read_window(a, "30:0:2000").is_none(), "oldest was pruned");
        std::fs::remove_dir_all(&s.dir).ok();
    }

    /// A restart's first cycle usually skips every window (all still fresh).
    /// That scan holds nothing — keeping it would fill the history with
    /// empty timestamps on every restart loop.
    #[test]
    fn an_all_skip_cycle_leaves_no_scan_behind() {
        let s = tmp_store(u64::MAX, 100);
        let id = s.begin("scheduled");
        s.record_skip(id, 7, 0.0, 2000, 1_699_999_000);
        s.record_skip(id, 30, 0.0, 2000, 1_699_999_100);
        s.finish(id);
        assert!(s.list(10).is_empty());

        // But one archived window is enough to keep the scan.
        let id = s.begin("scheduled");
        s.record_skip(id, 7, 0.0, 2000, 1_699_999_000);
        s.record_window(id, 30, 0.0, 2000, &payload(5));
        s.finish(id);
        assert_eq!(s.list(10).len(), 1);
        std::fs::remove_dir_all(&s.dir).ok();
    }

    #[test]
    fn prune_by_bytes_keeps_the_newest_even_when_it_alone_is_over() {
        // A 1-byte cap: everything is over it, but the newest must survive.
        let s = tmp_store(1, 100);
        let a = s.begin("scheduled");
        s.record_window(a, 30, 0.0, 2000, &payload(1));
        s.finish(a);
        let b = s.begin("scheduled");
        s.record_window(b, 30, 0.0, 2000, &payload(2));
        s.finish(b);

        let scans = s.list(10);
        assert_eq!(scans.len(), 1);
        assert_eq!(scans[0].id, b);
        std::fs::remove_dir_all(&s.dir).ok();
    }
}
