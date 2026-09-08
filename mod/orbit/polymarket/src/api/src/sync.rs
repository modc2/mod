//! Background sync schedule — every 5 minutes by default, owner-settable.
//!
//! The traders pipeline (leaderboards for the 1/7/14/30d windows) is re-warmed
//! by a background task in main.rs. This module owns *when* that task runs and
//! what the console shows about it:
//!
//!   * cadence persists to `<state dir>/sync.json` so a restart keeps the
//!     owner's choice (the old cadence was a `const` in main.rs);
//!   * changing it takes effect immediately — the scheduler sleeps on
//!     `wait_for_next_run`, which is woken by every config write;
//!   * "sync now" is the same path with a manual trigger flag, so a manual and
//!     a scheduled run can never overlap.
//!
//! Every route that reaches here is already behind the owner-only access gate
//! (access.rs), so "owner-settable" needs no extra check.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::Notify;

/// Five minutes — the floor, i.e. as fresh as this module will ever schedule.
/// Note this is a *start-to-start target*, not a promise: a full 4-window sweep
/// of ~6k traders takes 8–10 min in practice, so cycles queue back-to-back and
/// the effective cadence is `max(interval, cycle duration)`. Expect sustained
/// data-api pressure (429s drop individual traders from a window) at this
/// setting; raise it (`/sync/config`, or the AUTO chip) to trade freshness for
/// a quieter upstream. See the cadence note in main.rs.
pub const DEFAULT_INTERVAL_SECS: u64 = 300;
/// A full cold sweep takes minutes, so anything under 5 min would only stack
/// scheduler wakeups behind a cycle that is already running.
pub const MIN_INTERVAL_SECS: u64 = 300;
/// A week. Past this the disk cache (24h) is long dead anyway.
pub const MAX_INTERVAL_SECS: u64 = 7 * 24 * 3600;

/// How many windows the owner may keep warm at once. Each one is a full
/// ~2.5-minute sweep of the candidate pool, so eight is already a cycle that
/// runs back to back at the 5-minute floor — past this the list stops being a
/// cache and becomes a queue nothing ever drains.
pub const MAX_WARM_WINDOWS: usize = 8;
/// Bounds on a single window, mirroring what `/active-traders` itself clamps
/// to (routes.rs): a window outside these can never be READ back out of the
/// cache, so warming it would burn a sweep on an entry with no reader.
pub const MIN_POOL: u32 = 50;
pub const MAX_POOL: u32 = 2000;
pub const MAX_DAYS: u32 = 365;
pub const MAX_MIN_PER_DAY: f64 = 1000.0;

/// One leaderboard the background sweep keeps warm.
///
/// These three fields ARE the pipeline cache key (`days:minPerDay:pool`), which
/// is why they are the thing worth making configurable. The board sends
/// whatever the console's DAYS / MIN-PER-DAY filters say; if that combination
/// isn't on this list it was never aggregated, and the read falls through to a
/// cold ~10-minute pipeline run that the fleet activator kills at ~60s idle —
/// so a filter nobody warmed is a filter that never loads, no matter how long
/// you leave it. Editing this list is how the owner says "cache the view I
/// actually browse".
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct WarmWindow {
    pub days: u32,
    #[serde(default, rename = "minPerDay")]
    pub min_per_day: f64,
    #[serde(default = "default_pool")]
    pub pool: u32,
}

fn default_pool() -> u32 {
    MAX_POOL
}

impl WarmWindow {
    /// The pipeline cache key. Must format IDENTICALLY to the one
    /// `active_traders` builds from the query string, or the sweep warms
    /// entries no reader ever looks up — `{}` on an f64 is what both sides
    /// use, so 0.0 renders "0" on both.
    pub fn key(&self) -> String {
        format!("{}:{}:{}", self.days, self.min_per_day, self.pool)
    }

    fn validate(&self) -> Result<(), String> {
        if self.days < 1 || self.days > MAX_DAYS {
            return Err(format!("days must be 1–{} (got {})", MAX_DAYS, self.days));
        }
        if !self.min_per_day.is_finite() || self.min_per_day < 0.0 || self.min_per_day > MAX_MIN_PER_DAY {
            return Err(format!(
                "minPerDay must be 0–{} (got {})",
                MAX_MIN_PER_DAY, self.min_per_day
            ));
        }
        if self.pool < MIN_POOL || self.pool > MAX_POOL {
            return Err(format!(
                "pool must be {}–{} (got {})",
                MIN_POOL, MAX_POOL, self.pool
            ));
        }
        Ok(())
    }
}

/// The windows the sweep warmed before the list was configurable. Kept as the
/// default so an existing `sync.json` (which has no `windows` key) and a fresh
/// deployment both come up warming exactly what they warmed before.
pub fn default_windows() -> Vec<WarmWindow> {
    [1u32, 7, 14, 30]
        .into_iter()
        .map(|days| WarmWindow { days, min_per_day: 0.0, pool: MAX_POOL })
        .collect()
}

/// What woke the scheduler.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Trigger {
    Scheduled,
    Manual,
}

impl Trigger {
    fn label(self) -> &'static str {
        match self {
            Trigger::Scheduled => "scheduled",
            Trigger::Manual => "manual",
        }
    }
}

#[derive(Clone, Serialize, Deserialize)]
struct SyncConfig {
    #[serde(default = "default_enabled")]
    enabled: bool,
    #[serde(default = "default_interval", rename = "intervalSecs")]
    interval_secs: u64,
    /// Which leaderboards the sweep keeps warm. See `WarmWindow` — absent from
    /// an older `sync.json`, which is why this defaults rather than failing the
    /// whole parse (a rejected config would silently reset the owner's cadence
    /// too).
    #[serde(default = "default_windows")]
    windows: Vec<WarmWindow>,
}

fn default_enabled() -> bool {
    true
}

fn default_interval() -> u64 {
    DEFAULT_INTERVAL_SECS
}

impl Default for SyncConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            interval_secs: DEFAULT_INTERVAL_SECS,
            windows: default_windows(),
        }
    }
}

#[derive(Default)]
struct SyncStatus {
    running: bool,
    /// Unix seconds. `None` until the first cycle of this process — which is
    /// also what makes the first run fire right after boot.
    last_start: Option<i64>,
    last_finish: Option<i64>,
    last_duration_secs: Option<i64>,
    last_error: Option<String>,
    last_trigger: Option<&'static str>,
    runs: u64,
}

pub struct SyncSchedule {
    path: PathBuf,
    config: RwLock<SyncConfig>,
    status: RwLock<SyncStatus>,
    /// Woken on every config write and on "sync now" so the sleeping
    /// scheduler re-reads the schedule instead of finishing a stale sleep.
    wake: Notify,
    manual: AtomicBool,
}

impl SyncSchedule {
    /// Load the persisted cadence, falling back to
    /// `POLYMARKET_SYNC_INTERVAL_SECS` and then the 5-minute default.
    pub fn from_env() -> Arc<Self> {
        let dir = crate::access::state_dir();
        let path = dir.join("sync.json");

        let mut config = std::fs::read_to_string(&path)
            .ok()
            .and_then(|raw| serde_json::from_str::<SyncConfig>(&raw).ok())
            .unwrap_or_else(|| SyncConfig {
                interval_secs: std::env::var("POLYMARKET_SYNC_INTERVAL_SECS")
                    .ok()
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(DEFAULT_INTERVAL_SECS),
                ..SyncConfig::default()
            });
        config.interval_secs = clamp_interval(config.interval_secs);

        tracing::info!(
            interval_secs = config.interval_secs,
            enabled = config.enabled,
            path = %path.display(),
            "background sync schedule",
        );

        Arc::new(Self {
            path,
            config: RwLock::new(config),
            status: RwLock::new(SyncStatus::default()),
            wake: Notify::new(),
            manual: AtomicBool::new(false),
        })
    }

    /// Seconds between cycles.
    pub fn interval_secs(&self) -> u64 {
        self.config.read().interval_secs
    }

    /// How stale a cached window must be before a cycle re-pulls it. Derived
    /// from the cadence (92%) rather than fixed: with a hard-coded 55min floor
    /// a 15-minute cadence would skip every combo and silently never sync.
    pub fn resync_after_secs(&self) -> i64 {
        (self.interval_secs() as i64 * 11 / 12).max(60)
    }

    /// The leaderboards the background sweep keeps warm, in the order the
    /// owner listed them. Never empty — an empty list would mean the sweep
    /// warms nothing and every board read goes cold, so it falls back to the
    /// built-in windows.
    pub fn windows(&self) -> Vec<WarmWindow> {
        let w = self.config.read().windows.clone();
        if w.is_empty() { default_windows() } else { w }
    }

    /// Apply an owner change. Every field may be omitted. Returns the error
    /// message for an out-of-range value instead of silently clamping — the
    /// console shows it next to the field.
    pub fn update(
        &self,
        enabled: Option<bool>,
        interval_secs: Option<u64>,
        windows: Option<Vec<WarmWindow>>,
    ) -> Result<(), String> {
        if let Some(secs) = interval_secs {
            if !(MIN_INTERVAL_SECS..=MAX_INTERVAL_SECS).contains(&secs) {
                return Err(format!(
                    "intervalSecs must be between {} and {} ({}m–{}d)",
                    MIN_INTERVAL_SECS,
                    MAX_INTERVAL_SECS,
                    MIN_INTERVAL_SECS / 60,
                    MAX_INTERVAL_SECS / 86400,
                ));
            }
        }
        // Validate and normalize the whole list BEFORE taking the write lock,
        // so a rejected entry leaves the running schedule untouched rather
        // than half-applied.
        let windows = match windows {
            None => None,
            Some(list) => {
                if list.is_empty() {
                    return Err("windows must list at least one leaderboard to keep warm".into());
                }
                for w in &list {
                    w.validate()?;
                }
                // De-duplicate on the cache key: two entries that warm the same
                // key are one window and one wasted sweep per cycle. Keeps the
                // owner's order.
                let mut seen = std::collections::HashSet::new();
                let deduped: Vec<WarmWindow> =
                    list.into_iter().filter(|w| seen.insert(w.key())).collect();
                if deduped.len() > MAX_WARM_WINDOWS {
                    return Err(format!(
                        "at most {} warm windows \u{2014} each one is a full sweep of the candidate pool",
                        MAX_WARM_WINDOWS
                    ));
                }
                Some(deduped)
            }
        };
        let snapshot = {
            let mut cfg = self.config.write();
            if let Some(e) = enabled {
                cfg.enabled = e;
            }
            if let Some(secs) = interval_secs {
                cfg.interval_secs = secs;
            }
            if let Some(w) = windows {
                cfg.windows = w;
            }
            cfg.clone()
        };
        self.persist(&snapshot);
        tracing::info!(
            enabled = snapshot.enabled,
            interval_secs = snapshot.interval_secs,
            windows = snapshot.windows.len(),
            "background sync schedule updated",
        );
        // Re-schedule against the new cadence right away: a pending sleep was
        // sized by the OLD interval, so without this a 24h → 15min change
        // wouldn't take effect for a day.
        self.wake.notify_one();
        Ok(())
    }

    /// Request a cycle as soon as the scheduler is free. Idempotent while one
    /// is pending — the flag is a bool, not a queue.
    pub fn trigger_now(&self) {
        self.manual.store(true, Ordering::SeqCst);
        self.wake.notify_one();
    }

    fn persist(&self, cfg: &SyncConfig) {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        match serde_json::to_string_pretty(cfg) {
            Ok(json) => {
                if let Err(e) = std::fs::write(&self.path, json) {
                    tracing::warn!(path = %self.path.display(), error = %e,
                        "could not persist sync schedule — reverts to default on restart");
                }
            }
            Err(e) => tracing::warn!(error = %e, "could not serialize sync schedule"),
        }
    }

    /// Unix seconds of the next scheduled cycle, or `None` when auto-sync is
    /// off. `Some(<= now)` means it is due right now.
    pub fn next_run_at(&self) -> Option<i64> {
        // Field reads, not a snapshot: `SyncConfig` carries the warm-window
        // list now, so cloning it here would allocate on every countdown tick.
        let (enabled, interval_secs) = {
            let cfg = self.config.read();
            (cfg.enabled, cfg.interval_secs)
        };
        if !enabled {
            return None;
        }
        // No run yet this process: due immediately. The cycle itself skips
        // windows that a previous process already synced within the interval,
        // so a restart loop can't hammer the data-api.
        Some(match self.status.read().last_start {
            Some(last) => last + interval_secs as i64,
            None => now_secs(),
        })
    }

    /// Park until the next cycle is due, waking early when the owner changes
    /// the schedule or asks for an immediate sync.
    pub async fn wait_for_next_run(&self) -> Trigger {
        loop {
            if self.manual.swap(false, Ordering::SeqCst) {
                return Trigger::Manual;
            }
            if !self.config.read().enabled {
                // Nothing to schedule — sleep until something changes.
                self.wake.notified().await;
                continue;
            }
            let wait = match self.next_run_at() {
                // Disabled between the check above and here — park rather
                // than spin.
                None => {
                    self.wake.notified().await;
                    continue;
                }
                Some(next) => next - now_secs(),
            };
            if wait <= 0 {
                return Trigger::Scheduled;
            }
            let sleep = tokio::time::sleep(std::time::Duration::from_secs(wait as u64));
            tokio::select! {
                _ = sleep => return Trigger::Scheduled,
                // Config changed or "sync now" — recompute from the top.
                _ = self.wake.notified() => continue,
            }
        }
    }

    pub fn mark_started(&self, trigger: Trigger) {
        let mut st = self.status.write();
        st.running = true;
        st.last_start = Some(now_secs());
        st.last_trigger = Some(trigger.label());
    }

    pub fn mark_finished(&self, error: Option<String>) {
        let mut st = self.status.write();
        let now = now_secs();
        st.running = false;
        st.last_finish = Some(now);
        st.last_duration_secs = st.last_start.map(|s| now - s);
        st.last_error = error;
        st.runs += 1;
    }

    /// Everything the console's SYNC panel renders. `now` ships alongside so
    /// the client can count down without trusting its own clock offset.
    pub fn status_json(&self) -> Value {
        let cfg = self.config.read().clone();
        let st = self.status.read();
        json!({
            "enabled": cfg.enabled,
            "intervalSecs": cfg.interval_secs,
            "minIntervalSecs": MIN_INTERVAL_SECS,
            "maxIntervalSecs": MAX_INTERVAL_SECS,
            // The warm list, so the console can show WHICH boards answer from
            // cache and let the owner add the one they are actually browsing.
            "windows": self.windows(),
            "maxWindows": MAX_WARM_WINDOWS,
            "minPool": MIN_POOL,
            "maxPool": MAX_POOL,
            "maxDays": MAX_DAYS,
            "running": st.running,
            "lastRunAt": st.last_start,
            "lastFinishedAt": st.last_finish,
            "lastDurationSecs": st.last_duration_secs,
            "lastError": st.last_error,
            "lastTrigger": st.last_trigger,
            "runs": st.runs,
            "nextRunAt": self.next_run_at(),
            "now": now_secs(),
            "configPath": self.path.display().to_string(),
        })
    }
}

fn clamp_interval(secs: u64) -> u64 {
    secs.clamp(MIN_INTERVAL_SECS, MAX_INTERVAL_SECS)
}

fn now_secs() -> i64 {
    chrono::Utc::now().timestamp()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_schedule() -> SyncSchedule {
        let path = std::env::temp_dir().join(format!(
            "polymarket-sync-test-{}.json",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        SyncSchedule {
            path,
            config: RwLock::new(SyncConfig::default()),
            status: RwLock::new(SyncStatus::default()),
            wake: Notify::new(),
            manual: AtomicBool::new(false),
        }
    }

    #[test]
    fn defaults_to_five_minutes() {
        let s = tmp_schedule();
        assert_eq!(s.interval_secs(), 300);
        // 4m35s — a cycle on schedule must not skip a window about to fall due.
        assert_eq!(s.resync_after_secs(), 275);
    }

    #[test]
    fn owner_can_change_the_cadence_and_it_persists() {
        let s = tmp_schedule();
        s.update(None, Some(3600), None).unwrap();
        assert_eq!(s.interval_secs(), 3600);
        // Threshold tracks the cadence, else a cycle on schedule would skip
        // every window about to fall due and never actually sync.
        assert!(s.resync_after_secs() < 3600);

        let raw = std::fs::read_to_string(&s.path).unwrap();
        let saved: SyncConfig = serde_json::from_str(&raw).unwrap();
        assert_eq!(saved.interval_secs, 3600);
        assert!(saved.enabled);
        std::fs::remove_file(&s.path).ok();
    }

    #[test]
    fn out_of_range_intervals_are_rejected() {
        let s = tmp_schedule();
        assert!(s.update(None, Some(60), None).is_err());
        assert!(s.update(None, Some(30 * 86400), None).is_err());
        assert_eq!(s.interval_secs(), 300); // unchanged
    }

    #[test]
    fn next_run_is_immediate_before_the_first_cycle_then_one_interval_out() {
        let s = tmp_schedule();
        assert!(s.next_run_at().unwrap() <= now_secs());
        s.mark_started(Trigger::Scheduled);
        s.mark_finished(None);
        let next = s.next_run_at().unwrap();
        assert!(next > now_secs() + 290 && next <= now_secs() + 300);
    }

    #[test]
    fn disabling_stops_scheduling() {
        let s = tmp_schedule();
        s.update(Some(false), None, None).unwrap();
        assert!(s.next_run_at().is_none());
        std::fs::remove_file(&s.path).ok();
    }

    /// The whole point of the warm list: the sweep must warm the SAME cache
    /// key `/active-traders` reads. A formatting drift here (0 vs 0.0) warms
    /// entries nothing looks up, and every board read goes cold.
    #[test]
    fn a_windows_key_is_the_pipeline_cache_key() {
        let w = WarmWindow { days: 30, min_per_day: 0.0, pool: 2000 };
        assert_eq!(w.key(), "30:0:2000");
        let w = WarmWindow { days: 3, min_per_day: 2.5, pool: 500 };
        assert_eq!(w.key(), "3:2.5:500");
    }

    #[test]
    fn owner_can_choose_which_boards_stay_warm_and_it_persists() {
        let s = tmp_schedule();
        assert_eq!(s.windows(), default_windows());

        let mine = vec![
            WarmWindow { days: 3, min_per_day: 2.0, pool: 2000 },
            WarmWindow { days: 30, min_per_day: 0.0, pool: 500 },
        ];
        s.update(None, None, Some(mine.clone())).unwrap();
        assert_eq!(s.windows(), mine);

        let raw = std::fs::read_to_string(&s.path).unwrap();
        let saved: SyncConfig = serde_json::from_str(&raw).unwrap();
        assert_eq!(saved.windows, mine);
        // The cadence rides along untouched — a windows-only patch is not a
        // reset of everything else.
        assert_eq!(saved.interval_secs, DEFAULT_INTERVAL_SECS);
        std::fs::remove_file(&s.path).ok();
    }

    /// A `sync.json` written before this list existed has no `windows` key.
    /// It must come back warming what it warmed before, not warming nothing.
    #[test]
    fn an_old_config_without_windows_still_warms_the_defaults() {
        let cfg: SyncConfig =
            serde_json::from_str(r#"{"enabled":true,"intervalSecs":900}"#).unwrap();
        assert_eq!(cfg.interval_secs, 900);
        assert_eq!(cfg.windows, default_windows());
    }

    #[test]
    fn a_rejected_window_leaves_the_running_list_alone() {
        let s = tmp_schedule();
        // Out of range on each dimension in turn.
        assert!(s.update(None, None, Some(vec![WarmWindow { days: 0, min_per_day: 0.0, pool: 2000 }])).is_err());
        assert!(s.update(None, None, Some(vec![WarmWindow { days: 7, min_per_day: -1.0, pool: 2000 }])).is_err());
        assert!(s.update(None, None, Some(vec![WarmWindow { days: 7, min_per_day: 0.0, pool: 5000 }])).is_err());
        assert!(s.update(None, None, Some(vec![])).is_err());
        // Nine distinct windows — one past the cap.
        let too_many: Vec<WarmWindow> = (1..=9)
            .map(|d| WarmWindow { days: d, min_per_day: 0.0, pool: 2000 })
            .collect();
        assert!(s.update(None, None, Some(too_many)).is_err());

        assert_eq!(s.windows(), default_windows(), "a rejected patch changed the live list");
        std::fs::remove_file(&s.path).ok();
    }

    /// Two entries with the same key are one window and one wasted sweep per
    /// cycle — and at the cap, the duplicate would evict a real one.
    #[test]
    fn duplicate_windows_collapse_instead_of_burning_a_sweep() {
        let s = tmp_schedule();
        s.update(
            None,
            None,
            Some(vec![
                WarmWindow { days: 7, min_per_day: 0.0, pool: 2000 },
                WarmWindow { days: 7, min_per_day: 0.0, pool: 2000 },
                WarmWindow { days: 1, min_per_day: 0.0, pool: 2000 },
            ]),
        )
        .unwrap();
        assert_eq!(
            s.windows(),
            vec![
                WarmWindow { days: 7, min_per_day: 0.0, pool: 2000 },
                WarmWindow { days: 1, min_per_day: 0.0, pool: 2000 },
            ],
        );
        std::fs::remove_file(&s.path).ok();
    }

    #[tokio::test]
    async fn manual_trigger_wins_over_a_disabled_schedule() {
        let s = tmp_schedule();
        s.update(Some(false), None, None).unwrap();
        s.trigger_now();
        assert_eq!(s.wait_for_next_run().await, Trigger::Manual);
        std::fs::remove_file(&s.path).ok();
    }
}
