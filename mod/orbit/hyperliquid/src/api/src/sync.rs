//! Sync ledger — the module's data-integrity record.
//!
//! Every background pass (board refresh, index deepen, curve prewarm) has
//! always logged its outcome to tracing and then lost it. This keeps the same
//! facts as data: a capped, disk-backed history of passes plus the deepener's
//! latest coverage reading per window, so `/sync` can answer "when did we last
//! sync, did it work, and how complete is what we hold" without grepping logs.

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

/// Events kept (and persisted). At one refresher cycle every ~2 minutes this
/// is several hours of history — enough to see a 429 storm or a dead loop.
pub const HISTORY_CAP: usize = 400;

/// A board whose last successful compute is older than this is stale: the
/// refresher aims for ~2 min per cycle, but a full pass over six boards under
/// HL's 429 throttling can legitimately take several minutes. Past 15 the
/// loop is wedged, not slow.
pub const BOARD_STALE_MS: i64 = 15 * 60_000;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SyncEvent {
    pub ts_ms: i64,        // when the pass finished
    pub kind: String,      // "board" | "deepen" | "curves"
    pub key: String,       // board key ("7:roi:24h") or window ("7d")
    pub ok: bool,
    pub rows: usize,       // traders published / wallets fetched / curves warmed
    pub duration_ms: u64,
    #[serde(default)]
    pub note: String,      // error text on failure, extra detail on success
}

/// The deepener's latest completeness reading for one window: of the top
/// `target` ranked wallets it keeps warm, how many were inside the index TTL
/// when it last checked.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Coverage {
    pub days: u32,
    pub target: usize,
    pub fresh: usize,
    pub checked_ms: i64,
}

#[derive(Default, Serialize, Deserialize)]
struct LogState {
    #[serde(default)]
    events: VecDeque<SyncEvent>,
    #[serde(default)]
    coverage: Vec<Coverage>,
}

pub struct SyncLog {
    path: std::path::PathBuf,
    inner: Mutex<LogState>,
}

impl SyncLog {
    pub fn load(dir: &str) -> Self {
        let path = std::path::PathBuf::from(dir).join("synclog.json");
        let inner = std::fs::read_to_string(&path).ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        Self { path, inner: Mutex::new(inner) }
    }

    fn flush(&self, g: &LogState) {
        if let Ok(s) = serde_json::to_string(g) {
            let _ = std::fs::write(&self.path, s);
        }
    }

    pub fn push(&self, ev: SyncEvent) {
        let mut g = self.inner.lock();
        g.events.push_back(ev);
        while g.events.len() > HISTORY_CAP { g.events.pop_front(); }
        self.flush(&g);
    }

    /// Replace the coverage reading for `days` (one live row per window).
    pub fn set_coverage(&self, c: Coverage) {
        let mut g = self.inner.lock();
        g.coverage.retain(|x| x.days != c.days);
        g.coverage.push(c);
        g.coverage.sort_by_key(|x| x.days);
        self.flush(&g);
    }

    /// Newest-first history slice plus the current per-window coverage.
    pub fn snapshot(&self, limit: usize) -> (Vec<SyncEvent>, Vec<Coverage>) {
        let g = self.inner.lock();
        let events = g.events.iter().rev().take(limit).cloned().collect();
        (events, g.coverage.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(ts: i64) -> SyncEvent {
        SyncEvent { ts_ms: ts, kind: "board".into(), key: "7:roi:24h".into(),
                    ok: true, rows: 600, duration_ms: 1000, note: String::new() }
    }

    #[test]
    fn history_caps_and_survives_reload() {
        let dir = std::env::temp_dir().join(format!("hl-synclog-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let log = SyncLog::load(dir.to_str().unwrap());
        for i in 0..(HISTORY_CAP + 10) { log.push(ev(i as i64)); }
        let (events, _) = log.snapshot(HISTORY_CAP * 2);
        assert_eq!(events.len(), HISTORY_CAP);
        // Newest first, and the oldest 10 were dropped.
        assert_eq!(events[0].ts_ms, (HISTORY_CAP + 9) as i64);
        assert_eq!(events.last().unwrap().ts_ms, 10);

        let log2 = SyncLog::load(dir.to_str().unwrap());
        let (events2, _) = log2.snapshot(5);
        assert_eq!(events2.len(), 5);
        assert_eq!(events2[0].ts_ms, (HISTORY_CAP + 9) as i64);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn coverage_is_one_row_per_window() {
        let dir = std::env::temp_dir().join(format!("hl-synclog-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let log = SyncLog::load(dir.to_str().unwrap());
        log.set_coverage(Coverage { days: 7, target: 400, fresh: 100, checked_ms: 1 });
        log.set_coverage(Coverage { days: 1, target: 200, fresh: 50, checked_ms: 1 });
        log.set_coverage(Coverage { days: 7, target: 400, fresh: 390, checked_ms: 2 });
        let (_, cov) = log.snapshot(0);
        assert_eq!(cov.len(), 2);
        assert_eq!(cov[1].days, 7);
        assert_eq!(cov[1].fresh, 390);
        std::fs::remove_dir_all(&dir).ok();
    }
}
