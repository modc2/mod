//! How long a wallet has actually been trading.
//!
//! Every stat on the leaderboard is windowed — 1D, 7D, 14D, 30D. None of them
//! can answer "has this trader been doing this for a month, or did they open
//! the account last Tuesday", because the enrichment pull stops at the window
//! cutoff by design: on the 7D board the oldest trade anyone has is 7 days
//! old. A track-record filter built on that would report every trader as one
//! week old and cut the entire board.
//!
//! So the first trade is fetched separately, once per wallet, with
//! `?limit=1&sortDirection=ASC` — data-api hands back the oldest activity row
//! directly, which sidesteps the `offset > 5000` ceiling that makes paging
//! backwards impossible for heavy accounts.
//!
//! It is cached FOREVER (`<data dir>/first_trade.json`). A first trade is
//! immutable; the only reason to re-ask is a wallet we have never seen. That
//! turns a per-cycle cost of ~2000 requests into a one-off, and after the
//! first warmup the whole filter is free.

use std::collections::HashMap;
use std::path::PathBuf;

use parking_lot::RwLock;

/// Persist every N newly resolved wallets. See the checkpoint note in `put`.
const SAVE_EVERY: usize = 100;

/// Address (lowercased) → unix-seconds of its first-ever activity.
///
/// `0` is a real, cached answer meaning "data-api returned no activity at
/// all" — kept so a dormant address is not re-fetched on every cycle. It
/// reads downstream as "no history", which is what it is.
pub struct FirstTradeStore {
    path: PathBuf,
    map: RwLock<HashMap<String, u64>>,
    /// Entries added since the last save. Persisting is O(map), so a warmup
    /// cycle that resolves 2000 new wallets should write in checkpoints, not
    /// 2000×.
    dirty: RwLock<bool>,
    /// New entries since the last checkpoint write.
    since_save: RwLock<usize>,
}

impl FirstTradeStore {
    pub fn new() -> Self {
        let data_dir = std::env::var("POLYMARKET_DATA_DIR")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/tmp"));
        let _ = std::fs::create_dir_all(&data_dir);
        Self::at(data_dir.join("first_trade.json"))
    }

    /// Explicit path — what tests use. `set_var` on POLYMARKET_DATA_DIR would
    /// be a data race against every other test in the binary (they run on one
    /// process, and several read the environment on construction).
    pub fn at(path: PathBuf) -> Self {
        let map = std::fs::read_to_string(&path)
            .ok()
            .and_then(|raw| serde_json::from_str::<HashMap<String, u64>>(&raw).ok())
            .unwrap_or_default();
        tracing::info!(entries = map.len(), path = %path.display(), "first-trade cache loaded");
        Self {
            path,
            map: RwLock::new(map),
            dirty: RwLock::new(false),
            since_save: RwLock::new(0),
        }
    }

    /// In-memory only — never hits the network. `None` = never resolved.
    pub fn get(&self, address: &str) -> Option<u64> {
        self.map.read().get(&address.to_lowercase()).copied()
    }

    pub fn put(&self, address: &str, ts: u64) {
        let is_new = {
            let mut map = self.map.write();
            map.insert(address.to_lowercase(), ts).is_none()
        };
        if !is_new {
            return;
        }
        *self.dirty.write() = true;
        // Checkpoint mid-sweep. A warmup pass resolves ~2000 wallets over ten
        // minutes and the fleet activator stops this process after ~60s idle,
        // so waiting for the end of the pass to persist meant that in practice
        // it never persisted — every wake re-fetched every address it had
        // already looked up. Writing every 100 caps the loss at 100 lookups
        // and the cost at ~1% of the requests it saves.
        let should_checkpoint = {
            let mut n = self.since_save.write();
            *n += 1;
            if *n >= SAVE_EVERY {
                *n = 0;
                true
            } else {
                false
            }
        };
        if should_checkpoint {
            self.flush();
        }
    }

    pub fn len(&self) -> usize {
        self.map.read().len()
    }

    /// Write the map out if anything new landed. Best-effort: a failed write
    /// costs one re-fetch per wallet next cycle, nothing more.
    pub fn flush(&self) {
        if !*self.dirty.read() {
            return;
        }
        let snapshot = self.map.read().clone();
        match serde_json::to_string(&snapshot) {
            Ok(json) => {
                if let Err(e) = std::fs::write(&self.path, json) {
                    tracing::warn!(error = %e, path = %self.path.display(), "first-trade cache write failed");
                } else {
                    *self.dirty.write() = false;
                }
            }
            Err(e) => tracing::warn!(error = %e, "first-trade cache serialize failed"),
        }
    }

    /// Cache-through resolve. One request per wallet, ever.
    pub async fn resolve(&self, http: &reqwest::Client, address: &str, base_url: &str) -> Option<u64> {
        if let Some(ts) = self.get(address) {
            return Some(ts);
        }
        let url = format!(
            "{}/activity?user={}&limit=1&sortDirection=ASC",
            base_url, address
        );
        let rows = http
            .get(&url)
            .send()
            .await
            .ok()?
            .json::<Vec<serde_json::Value>>()
            .await
            .ok()?;
        // Empty list = this wallet has no activity. Cache the 0 so it does
        // not get re-asked forever; an upstream ERROR, by contrast, fails the
        // `.ok()?` above and caches nothing, so it retries next cycle.
        let ts = rows
            .first()
            .and_then(|r| r.get("timestamp"))
            .and_then(|t| t.as_u64().or_else(|| t.as_f64().map(|f| f as u64)))
            .map(|raw| if raw > 1_000_000_000_000 { raw / 1000 } else { raw })
            .unwrap_or(0);
        self.put(address, ts);
        Some(ts)
    }
}

impl Default for FirstTradeStore {
    fn default() -> Self {
        Self::new()
    }
}

/// Days between a first trade and now. `None` in, `None` out — an unresolved
/// wallet must never read as "0 days of history", or every filter built on
/// this would cut it.
pub fn history_days(first_trade_ts: Option<u64>, now_sec: u64) -> Option<f64> {
    let ts = first_trade_ts?;
    if ts == 0 {
        return Some(0.0);
    }
    Some(now_sec.saturating_sub(ts.min(now_sec)) as f64 / 86_400.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn history_days_is_none_for_unresolved() {
        assert!(history_days(None, 1_000_000).is_none());
    }

    #[test]
    fn history_days_zero_sentinel_is_no_history() {
        assert_eq!(history_days(Some(0), 1_000_000), Some(0.0));
    }

    #[test]
    fn history_days_counts_back_from_now() {
        let now = 30 * 86_400u64;
        let d = history_days(Some(0 + 1), now).unwrap();
        assert!((d - 30.0).abs() < 0.001, "{d}");
        assert_eq!(history_days(Some(now - 6 * 86_400), now), Some(6.0));
    }

    #[test]
    fn future_timestamp_clamps_to_zero_not_negative() {
        let now = 1_000_000u64;
        assert_eq!(history_days(Some(now + 86_400), now), Some(0.0));
    }

    #[test]
    fn store_roundtrips_through_disk() {
        let dir = std::env::temp_dir().join(format!("pm-first-trade-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("first_trade.json");
        let store = FirstTradeStore::at(path.clone());
        store.put("0xABC", 123);
        store.flush();
        let reloaded = FirstTradeStore::at(path);
        // Addresses normalize to lowercase on both write and read.
        assert_eq!(reloaded.get("0xabc"), Some(123));
        assert_eq!(reloaded.get("0xAbC"), Some(123));
        assert_eq!(reloaded.get("0xdef"), None);
        std::fs::remove_dir_all(&dir).ok();
    }
}
