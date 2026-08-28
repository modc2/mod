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

#[derive(Clone, Copy, Serialize, Deserialize)]
struct SyncConfig {
    #[serde(default = "default_enabled")]
    enabled: bool,
    #[serde(default = "default_interval", rename = "intervalSecs")]
    interval_secs: u64,
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

    /// Apply an owner change. Either field may be omitted. Returns the error
    /// message for an out-of-range interval instead of silently clamping — the
    /// console shows it next to the field.
    pub fn update(&self, enabled: Option<bool>, interval_secs: Option<u64>) -> Result<(), String> {
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
        let snapshot = {
            let mut cfg = self.config.write();
            if let Some(e) = enabled {
                cfg.enabled = e;
            }
            if let Some(secs) = interval_secs {
                cfg.interval_secs = secs;
            }
            *cfg
        };
        self.persist(&snapshot);
        tracing::info!(
            enabled = snapshot.enabled,
            interval_secs = snapshot.interval_secs,
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
        let cfg = *self.config.read();
        if !cfg.enabled {
            return None;
        }
        // No run yet this process: due immediately. The cycle itself skips
        // windows that a previous process already synced within the interval,
        // so a restart loop can't hammer the data-api.
        Some(match self.status.read().last_start {
            Some(last) => last + cfg.interval_secs as i64,
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
        let cfg = *self.config.read();
        let st = self.status.read();
        json!({
            "enabled": cfg.enabled,
            "intervalSecs": cfg.interval_secs,
            "minIntervalSecs": MIN_INTERVAL_SECS,
            "maxIntervalSecs": MAX_INTERVAL_SECS,
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
        s.update(None, Some(3600)).unwrap();
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
        assert!(s.update(None, Some(60)).is_err());
        assert!(s.update(None, Some(30 * 86400)).is_err());
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
        s.update(Some(false), None).unwrap();
        assert!(s.next_run_at().is_none());
        std::fs::remove_file(&s.path).ok();
    }

    #[tokio::test]
    async fn manual_trigger_wins_over_a_disabled_schedule() {
        let s = tmp_schedule();
        s.update(Some(false), None).unwrap();
        s.trigger_now();
        assert_eq!(s.wait_for_next_run().await, Trigger::Manual);
        std::fs::remove_file(&s.path).ok();
    }
}
