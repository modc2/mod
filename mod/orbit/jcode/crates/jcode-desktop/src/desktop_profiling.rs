use super::*;

#[allow(clippy::too_many_arguments)]
pub(crate) fn log_desktop_session_event_batch_profile(
    raw_event_count: usize,
    raw_payload_bytes: usize,
    accumulated_for: Duration,
    ui_queue_delay: Duration,
    apply_stats: &DesktopSessionApplyStats,
    redraw_requested: bool,
    redraw_deferred: bool,
    session_card_refresh_spawned: bool,
) {
    if raw_event_count < 128
        && raw_payload_bytes < 8 * 1024
        && accumulated_for < Duration::from_millis(40)
        && ui_queue_delay < DESKTOP_INPUT_LATENCY_BUDGET
        && apply_stats.elapsed < DESKTOP_120FPS_FRAME_BUDGET
        && !apply_stats.session_card_refresh_requested
        && !session_card_refresh_spawned
    {
        return;
    }
    emit_desktop_profile_event(
        "jcode-desktop-session-event-profile",
        serde_json::json!({
            "raw_events": raw_event_count,
            "coalesced_events": apply_stats.event_count,
            "raw_payload_bytes": raw_payload_bytes,
            "text_delta_bytes": apply_stats.text_delta_bytes,
            "forwarder_accumulated_ms": accumulated_for.as_secs_f64() * 1000.0,
            "ui_queue_delay_ms": ui_queue_delay.as_secs_f64() * 1000.0,
            "apply_ms": apply_stats.elapsed.as_secs_f64() * 1000.0,
            "visible_changed": apply_stats.visible_changed,
            "redraw_requested": redraw_requested,
            "redraw_deferred": redraw_deferred,
            "session_card_refresh_requested": apply_stats.session_card_refresh_requested,
            "session_card_refresh_spawned": session_card_refresh_spawned,
        }),
    );
}

pub(crate) fn log_desktop_session_card_refresh_profile(
    session_id: &str,
    loaded_in: Duration,
    card_found: bool,
    applied: bool,
) {
    if loaded_in < Duration::from_millis(40) && card_found {
        return;
    }
    emit_desktop_profile_event(
        "jcode-desktop-session-card-refresh-profile",
        serde_json::json!({
            "session_id": session_id,
            "loaded_in_ms": duration_ms(loaded_in),
            "card_found": card_found,
            "applied": applied,
            "ui_thread_blocking": false,
        }),
    );
}

pub(crate) fn log_desktop_session_cards_load_profile(
    purpose: DesktopSessionCardsPurpose,
    loaded_in: Duration,
    card_count: usize,
    applied: bool,
) {
    if loaded_in < Duration::from_millis(40) && applied {
        return;
    }
    emit_desktop_profile_event(
        "jcode-desktop-session-cards-load-profile",
        serde_json::json!({
            "purpose": format!("{purpose:?}"),
            "loaded_in_ms": duration_ms(loaded_in),
            "card_count": card_count,
            "applied": applied,
            "ui_thread_blocking": false,
        }),
    );
}

pub(crate) fn log_desktop_preferences_save_profile(
    saved_in: Duration,
    queued_for: Duration,
    coalesced_saves: usize,
    error: Option<&str>,
) {
    if saved_in < Duration::from_millis(40) && coalesced_saves <= 1 && error.is_none() {
        return;
    }
    emit_desktop_profile_event(
        "jcode-desktop-preferences-save-profile",
        serde_json::json!({
            "saved_in_ms": duration_ms(saved_in),
            "queued_for_ms": duration_ms(queued_for),
            "coalesced_saves": coalesced_saves,
            "error": error,
            "ui_thread_blocking": false,
        }),
    );
}

pub(crate) fn log_desktop_crashed_sessions_restore_profile(
    restored: usize,
    errors: usize,
    elapsed: Duration,
) {
    if elapsed < Duration::from_millis(40) && errors == 0 {
        return;
    }
    emit_desktop_profile_event(
        "jcode-desktop-crashed-sessions-restore-profile",
        serde_json::json!({
            "restored": restored,
            "errors": errors,
            "elapsed_ms": duration_ms(elapsed),
            "ui_thread_blocking": false,
        }),
    );
}

#[derive(Clone)]
pub(crate) struct DesktopFrameStageProfile {
    pub(crate) name: &'static str,
    pub(crate) duration: Duration,
}

pub(crate) struct DesktopFrameProfile {
    pub(crate) started_at: Instant,
    pub(crate) last_checkpoint: Instant,
    pub(crate) stages: Vec<DesktopFrameStageProfile>,
}

impl DesktopFrameProfile {
    pub(crate) fn new() -> Self {
        let now = Instant::now();
        Self {
            started_at: now,
            last_checkpoint: now,
            stages: Vec::with_capacity(20),
        }
    }

    pub(crate) fn checkpoint(&mut self, name: &'static str) {
        let now = Instant::now();
        self.stages.push(DesktopFrameStageProfile {
            name,
            duration: now.saturating_duration_since(self.last_checkpoint),
        });
        self.last_checkpoint = now;
    }

    pub(crate) fn total_duration(&self) -> Duration {
        self.last_checkpoint
            .saturating_duration_since(self.started_at)
    }

    pub(crate) fn stage_duration(&self, name: &'static str) -> Duration {
        self.stages
            .iter()
            .filter(|stage| stage.name == name)
            .fold(Duration::ZERO, |total, stage| total + stage.duration)
    }

    pub(crate) fn cpu_duration(&self) -> Duration {
        self.stages
            .iter()
            .filter(|stage| !matches!(stage.name, "surface_acquire" | "queue_submit" | "present"))
            .fold(Duration::ZERO, |total, stage| total + stage.duration)
    }
}

#[derive(Clone, Copy)]
pub(crate) struct DesktopFrameContext {
    pub(crate) mode: &'static str,
    pub(crate) smooth_scroll_lines: f32,
    pub(crate) text_buffer_count: usize,
    pub(crate) text_area_count: usize,
    pub(crate) primitive_vertices: usize,
    pub(crate) body_line_count: usize,
    pub(crate) viewport_line_count: usize,
    pub(crate) body_text_window_line_count: usize,
    pub(crate) streaming_text_line_count: usize,
    pub(crate) inline_widget_line_count: usize,
    pub(crate) text_prepared: bool,
    pub(crate) primitive_geometry_cache_hit: bool,
}

#[derive(Clone)]
pub(crate) struct DesktopRenderFrameResult {
    pub(crate) animation_active: bool,
    pub(crate) content_ready: bool,
    pub(crate) frame_wall: Duration,
    pub(crate) frame_cpu: Duration,
    pub(crate) context: DesktopFrameContext,
    pub(crate) stages: Vec<DesktopFrameStageProfile>,
}

#[derive(Clone)]
pub(crate) struct DesktopFrameSlowSample {
    pub(crate) wall: Duration,
    pub(crate) cpu: Duration,
    pub(crate) surface_acquire: Duration,
    pub(crate) queue_submit: Duration,
    pub(crate) present: Duration,
    pub(crate) score: Duration,
    pub(crate) stages: Vec<DesktopFrameStageProfile>,
    pub(crate) context: DesktopFrameContext,
}

pub(crate) struct DesktopFrameProfiler {
    pub(crate) enabled: bool,
    pub(crate) log_all: bool,
    pub(crate) budget: Duration,
    pub(crate) report_interval: Duration,
    pub(crate) frames: usize,
    pub(crate) slow_cpu_frames: usize,
    pub(crate) present_stall_frames: usize,
    pub(crate) worst: Option<DesktopFrameSlowSample>,
    pub(crate) last_report: Option<Instant>,
}

impl DesktopFrameProfiler {
    pub(crate) fn new() -> Self {
        let mode = desktop_frame_profile_mode();
        let enabled = desktop_frame_profile_enabled(mode.as_deref());
        let log_all = desktop_frame_profile_log_all(mode.as_deref());
        let budget =
            duration_millis_env("JCODE_DESKTOP_FRAME_BUDGET_MS", DESKTOP_120FPS_FRAME_BUDGET);
        Self {
            enabled,
            log_all,
            budget,
            report_interval: DESKTOP_FRAME_PROFILE_REPORT_INTERVAL,
            frames: 0,
            slow_cpu_frames: 0,
            present_stall_frames: 0,
            worst: None,
            last_report: None,
        }
    }

    pub(crate) fn observe(&mut self, profile: DesktopFrameProfile, context: DesktopFrameContext) {
        if !self.enabled {
            return;
        }

        self.frames += 1;
        let wall = profile.total_duration();
        let cpu = profile.cpu_duration();
        let surface_acquire = profile.stage_duration("surface_acquire");
        let queue_submit = profile.stage_duration("queue_submit");
        let present = profile.stage_duration("present");
        let cpu_slow = cpu >= self.budget;
        let present_stall = surface_acquire >= DESKTOP_PRESENT_STALL_BUDGET
            || queue_submit >= DESKTOP_PRESENT_STALL_BUDGET
            || present >= DESKTOP_PRESENT_STALL_BUDGET;
        if cpu_slow || present_stall || self.log_all {
            if cpu_slow {
                self.slow_cpu_frames += 1;
            }
            if present_stall {
                self.present_stall_frames += 1;
            }
            let score = cpu.max(surface_acquire).max(queue_submit).max(present);
            let replace_worst = self
                .worst
                .as_ref()
                .is_none_or(|sample| score > sample.score);
            if replace_worst {
                self.worst = Some(DesktopFrameSlowSample {
                    wall,
                    cpu,
                    surface_acquire,
                    queue_submit,
                    present,
                    score,
                    stages: profile.stages,
                    context,
                });
            }
        }

        let now = Instant::now();
        let report_due = self.last_report.is_none_or(|last_report| {
            now.saturating_duration_since(last_report) >= self.report_interval
        });
        if report_due && (self.slow_cpu_frames > 0 || self.present_stall_frames > 0 || self.log_all)
        {
            self.report(now);
        }
    }

    pub(crate) fn report(&mut self, now: Instant) {
        if let Some(worst) = self.worst.as_ref() {
            emit_desktop_profile_event(
                "jcode-desktop-frame-profile",
                serde_json::json!({
                    "cpu_budget_ms": duration_ms(self.budget),
                    "present_stall_budget_ms": duration_ms(DESKTOP_PRESENT_STALL_BUDGET),
                    "window_frames": self.frames,
                    "slow_frames": self.slow_cpu_frames,
                    "slow_cpu_frames": self.slow_cpu_frames,
                    "present_stall_frames": self.present_stall_frames,
                    "worst_frame_ms": duration_ms(worst.wall),
                    "worst_wall_ms": duration_ms(worst.wall),
                    "worst_cpu_ms": duration_ms(worst.cpu),
                    "surface_acquire_ms": duration_ms(worst.surface_acquire),
                    "queue_submit_ms": duration_ms(worst.queue_submit),
                    "present_ms": duration_ms(worst.present),
                    "submit_present_ms": duration_ms(worst.queue_submit + worst.present),
                    "mode": worst.context.mode,
                    "smooth_scroll_lines": worst.context.smooth_scroll_lines,
                    "text_buffer_count": worst.context.text_buffer_count,
                    "text_area_count": worst.context.text_area_count,
                    "primitive_vertices": worst.context.primitive_vertices,
                    "body_line_count": worst.context.body_line_count,
                    "viewport_line_count": worst.context.viewport_line_count,
                    "body_text_window_line_count": worst.context.body_text_window_line_count,
                    "streaming_text_line_count": worst.context.streaming_text_line_count,
                    "inline_widget_line_count": worst.context.inline_widget_line_count,
                    "text_prepared": worst.context.text_prepared,
                    "primitive_geometry_cache_hit": worst.context.primitive_geometry_cache_hit,
                    "stages": worst.stages.iter().map(|stage| serde_json::json!({
                        "name": stage.name,
                        "ms": duration_ms(stage.duration),
                    })).collect::<Vec<_>>(),
                }),
            );
        }
        self.frames = 0;
        self.slow_cpu_frames = 0;
        self.present_stall_frames = 0;
        self.worst = None;
        self.last_report = Some(now);
    }
}

#[derive(Clone, Copy)]
pub(crate) struct DesktopPendingInteraction {
    pub(crate) kind: &'static str,
    pub(crate) started_at: Instant,
    pub(crate) count: usize,
}

pub(crate) struct DesktopInteractionLatencyProfiler {
    pub(crate) enabled: bool,
    pub(crate) log_all: bool,
    pub(crate) budget: Duration,
    pub(crate) pending: Option<DesktopPendingInteraction>,
}

impl DesktopInteractionLatencyProfiler {
    pub(crate) fn new() -> Self {
        let mode = desktop_frame_profile_mode();
        let enabled = desktop_frame_profile_enabled(mode.as_deref());
        let log_all = desktop_frame_profile_log_all(mode.as_deref());
        let budget = duration_millis_env(
            "JCODE_DESKTOP_INPUT_LATENCY_BUDGET_MS",
            DESKTOP_INPUT_LATENCY_BUDGET,
        );
        Self {
            enabled,
            log_all,
            budget,
            pending: None,
        }
    }

    pub(crate) fn mark(&mut self, kind: &'static str, started_at: Instant) {
        if !self.enabled {
            return;
        }
        match self.pending.as_mut() {
            Some(pending) => {
                if started_at < pending.started_at {
                    pending.started_at = started_at;
                }
                if pending.kind != kind {
                    pending.kind = "mixed";
                }
                pending.count += 1;
            }
            None => {
                self.pending = Some(DesktopPendingInteraction {
                    kind,
                    started_at,
                    count: 1,
                });
            }
        }
    }

    pub(crate) fn pending_kind(&self) -> Option<&'static str> {
        self.pending.as_ref().map(|pending| pending.kind)
    }

    pub(crate) fn observe_presented(&mut self, frame: &DesktopRenderFrameResult) {
        let Some(pending) = self.pending.take() else {
            return;
        };
        if !self.enabled {
            return;
        }
        let latency = Instant::now().saturating_duration_since(pending.started_at);
        if latency < self.budget && !self.log_all {
            return;
        }
        eprintln!(
            "jcode-desktop-latency-profile {}",
            serde_json::json!({
                "kind": pending.kind,
                "interaction_count": pending.count,
                "latency_budget_ms": duration_ms(self.budget),
                "latency_ms": duration_ms(latency),
                "frame_wall_ms": duration_ms(frame.frame_wall),
                "frame_cpu_ms": duration_ms(frame.frame_cpu),
                "mode": frame.context.mode,
                "smooth_scroll_lines": frame.context.smooth_scroll_lines,
                "text_buffer_count": frame.context.text_buffer_count,
                "text_area_count": frame.context.text_area_count,
                "primitive_vertices": frame.context.primitive_vertices,
                "body_line_count": frame.context.body_line_count,
                "viewport_line_count": frame.context.viewport_line_count,
                "body_text_window_line_count": frame.context.body_text_window_line_count,
                "streaming_text_line_count": frame.context.streaming_text_line_count,
                "inline_widget_line_count": frame.context.inline_widget_line_count,
                "text_prepared": frame.context.text_prepared,
                "stages": frame.stages.iter().map(|stage| serde_json::json!({
                    "name": stage.name,
                    "ms": duration_ms(stage.duration),
                })).collect::<Vec<_>>(),
            })
        );
    }
}

#[derive(Clone, Copy)]
pub(crate) struct NoPaintWatchdogContext {
    pub(crate) active: bool,
    pub(crate) mode: &'static str,
    pub(crate) has_background_work: bool,
    pub(crate) frame_animation_active: bool,
    pub(crate) pending_backend_redraw: bool,
    pub(crate) pending_interaction_kind: Option<&'static str>,
}

pub(crate) struct DesktopNoPaintWatchdog {
    pub(crate) enabled: bool,
    pub(crate) log_all: bool,
    pub(crate) budget: Duration,
    pub(crate) last_presented_at: Instant,
    pub(crate) last_reported_at: Option<Instant>,
    pub(crate) last_redraw_request_at: Option<Instant>,
}

impl DesktopNoPaintWatchdog {
    pub(crate) fn new() -> Self {
        let now = Instant::now();
        Self::new_with_start(now)
    }

    pub(crate) fn new_with_start(now: Instant) -> Self {
        let mode = desktop_frame_profile_mode();
        Self::new_with_start_and_mode(now, mode.as_deref())
    }

    pub(crate) fn new_with_start_and_mode(now: Instant, mode: Option<&str>) -> Self {
        let enabled = desktop_frame_profile_enabled(mode);
        let log_all = desktop_frame_profile_log_all(mode);
        let budget =
            duration_millis_env("JCODE_DESKTOP_NO_PAINT_BUDGET_MS", DESKTOP_NO_PAINT_BUDGET);
        Self {
            enabled,
            log_all,
            budget,
            last_presented_at: now,
            last_reported_at: None,
            last_redraw_request_at: None,
        }
    }

    pub(crate) fn observe_presented(&mut self, now: Instant, _frame: &DesktopRenderFrameResult) {
        self.last_presented_at = now;
        self.last_reported_at = None;
        self.last_redraw_request_at = None;
    }

    pub(crate) fn observe_active_tick(
        &mut self,
        now: Instant,
        context: NoPaintWatchdogContext,
    ) -> bool {
        if !self.enabled {
            return false;
        }
        if !context.active {
            self.last_reported_at = None;
            self.last_redraw_request_at = None;
            return false;
        }
        let gap = now.saturating_duration_since(self.last_presented_at);
        if gap < self.budget && !self.log_all {
            return false;
        }

        let report_due = self.last_reported_at.is_none_or(|last_reported| {
            now.saturating_duration_since(last_reported) >= DESKTOP_FRAME_PROFILE_REPORT_INTERVAL
        });
        if report_due {
            self.last_reported_at = Some(now);
            emit_desktop_profile_event(
                "jcode-desktop-no-paint-profile",
                serde_json::json!({
                    "budget_ms": duration_ms(self.budget),
                    "gap_ms": duration_ms(gap),
                    "mode": context.mode,
                    "has_background_work": context.has_background_work,
                    "frame_animation_active": context.frame_animation_active,
                    "pending_backend_redraw": context.pending_backend_redraw,
                    "pending_interaction_kind": context.pending_interaction_kind,
                }),
            );
        }

        let redraw_due = self.last_redraw_request_at.is_none_or(|last_request| {
            now.saturating_duration_since(last_request) >= BACKEND_REDRAW_FRAME_INTERVAL
        });
        if redraw_due {
            self.last_redraw_request_at = Some(now);
        }
        redraw_due
    }
}

#[cfg(test)]
mod desktop_no_paint_watchdog_tests {
    use super::*;

    #[test]
    fn no_paint_watchdog_requests_redraw_after_active_gap_budget() {
        let start = Instant::now();
        let mut watchdog = DesktopNoPaintWatchdog::new_with_start_and_mode(start, Some("1"));
        let context = NoPaintWatchdogContext {
            active: true,
            mode: "single_session",
            has_background_work: true,
            frame_animation_active: false,
            pending_backend_redraw: false,
            pending_interaction_kind: Some("backend_events"),
        };

        assert!(!watchdog.observe_active_tick(start + watchdog.budget / 2, context));
        assert!(watchdog.observe_active_tick(start + watchdog.budget, context));
        assert!(!watchdog.observe_active_tick(
            start + watchdog.budget + BACKEND_REDRAW_FRAME_INTERVAL / 2,
            context
        ));
        assert!(watchdog.observe_active_tick(
            start + watchdog.budget + BACKEND_REDRAW_FRAME_INTERVAL,
            context
        ));
    }

    #[test]
    fn no_paint_watchdog_resets_when_idle_or_presented() {
        let start = Instant::now();
        let mut watchdog = DesktopNoPaintWatchdog::new_with_start_and_mode(start, Some("1"));
        let active_context = NoPaintWatchdogContext {
            active: true,
            mode: "single_session",
            has_background_work: true,
            frame_animation_active: false,
            pending_backend_redraw: false,
            pending_interaction_kind: None,
        };
        let idle_context = NoPaintWatchdogContext {
            active: false,
            ..active_context
        };

        assert!(watchdog.observe_active_tick(start + watchdog.budget, active_context));
        assert!(
            !watchdog.observe_active_tick(start + watchdog.budget + watchdog.budget, idle_context)
        );
        assert!(watchdog.last_redraw_request_at.is_none());
    }

    #[test]
    fn no_paint_watchdog_is_off_without_explicit_frame_profile_mode() {
        let start = Instant::now();
        let mut watchdog = DesktopNoPaintWatchdog::new_with_start_and_mode(start, None);
        let context = NoPaintWatchdogContext {
            active: true,
            mode: "single_session",
            has_background_work: true,
            frame_animation_active: false,
            pending_backend_redraw: false,
            pending_interaction_kind: Some("backend_events"),
        };

        assert!(!watchdog.enabled);
        assert!(!watchdog.observe_active_tick(start + DESKTOP_NO_PAINT_BUDGET * 4, context));
    }
}

pub(crate) fn duration_ms(duration: Duration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

pub(crate) static DESKTOP_PROFILE_LOG_TX: OnceLock<Option<mpsc::Sender<DesktopProfileLogLine>>> =
    OnceLock::new();
pub(crate) static DESKTOP_PROFILE_LAUNCH_ID: OnceLock<String> = OnceLock::new();

#[derive(Debug)]
pub(crate) struct DesktopProfileLogLine {
    pub(crate) stderr_line: String,
    pub(crate) jsonl_line: String,
}

pub(crate) fn desktop_profile_log_path() -> Option<PathBuf> {
    if std::env::var_os("JCODE_DESKTOP_PROFILE_LOG").is_some_and(|value| !env_flag_enabled(value)) {
        return None;
    }
    if let Some(path) = std::env::var_os("JCODE_DESKTOP_PROFILE_LOG_PATH") {
        if path.is_empty() {
            return None;
        }
        return Some(PathBuf::from(path));
    }
    let cache_root = std::env::var_os("XDG_CACHE_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".cache")))?;
    Some(
        cache_root
            .join("jcode")
            .join("desktop")
            .join("performance.log"),
    )
}

pub(crate) fn desktop_profile_stderr_enabled() -> bool {
    std::env::var_os("JCODE_DESKTOP_PROFILE_STDERR").is_none_or(env_flag_enabled)
}

pub(crate) fn desktop_profile_launch_id() -> &'static str {
    DESKTOP_PROFILE_LAUNCH_ID
        .get_or_init(|| {
            let timestamp_unix_ms = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|duration| duration.as_millis().min(u64::MAX as u128) as u64)
                .unwrap_or_default();
            format!("{timestamp_unix_ms}-{}", std::process::id())
        })
        .as_str()
}

pub(crate) fn desktop_profile_log_sender() -> Option<&'static mpsc::Sender<DesktopProfileLogLine>> {
    DESKTOP_PROFILE_LOG_TX
        .get_or_init(|| {
            let path = desktop_profile_log_path();
            let stderr_enabled = desktop_profile_stderr_enabled();
            if path.is_none() && !stderr_enabled {
                return None;
            }
            let (tx, rx) = mpsc::channel::<DesktopProfileLogLine>();
            match std::thread::Builder::new()
                .name("jcode-desktop-profile-log".to_string())
                .spawn(move || {
                    let mut file = path.and_then(|path| {
                        if let Some(parent) = path.parent()
                            && let Err(error) = std::fs::create_dir_all(parent)
                        {
                            desktop_log::error(format_args!(
                                "jcode-desktop: failed to create profile log directory {}: {error}",
                                parent.display()
                            ));
                            return None;
                        }
                        match OpenOptions::new().create(true).append(true).open(&path) {
                            Ok(file) => Some(file),
                            Err(error) => {
                                desktop_log::error(format_args!(
                                    "jcode-desktop: failed to open profile log {}: {error}",
                                    path.display()
                                ));
                                None
                            }
                        }
                    });
                    while let Ok(line) = rx.recv() {
                        if stderr_enabled {
                            eprintln!("{}", line.stderr_line);
                        }
                        if let Some(profile_file) = file.as_mut()
                            && let Err(error) = writeln!(profile_file, "{}", line.jsonl_line)
                        {
                            desktop_log::error(format_args!(
                                "jcode-desktop: failed to write profile log: {error}"
                            ));
                            file = None;
                        }
                    }
                }) {
                Ok(_) => Some(tx),
                Err(error) => {
                    desktop_log::error(format_args!(
                        "jcode-desktop: failed to start profile logger: {error:#}"
                    ));
                    None
                }
            }
        })
        .as_ref()
}

pub(crate) fn emit_desktop_profile_event(event: &'static str, payload: serde_json::Value) {
    let timestamp_unix_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u64::MAX as u128) as u64)
        .unwrap_or_default();
    if let Some(tx) = desktop_profile_log_sender() {
        let stderr_line = format!("{event} {payload}");
        let jsonl_line = serde_json::json!({
            "timestamp_unix_ms": timestamp_unix_ms,
            "launch_id": desktop_profile_launch_id(),
            "build_hash": desktop_build_hash_label(),
            "pid": std::process::id(),
            "event": event,
            "payload": payload,
        })
        .to_string();
        if tx
            .send(DesktopProfileLogLine {
                stderr_line,
                jsonl_line,
            })
            .is_err()
        {
            desktop_log::warn(format_args!(
                "jcode-desktop: failed to queue profile event {event}, logger is closed"
            ));
        }
    }
}

pub(crate) fn log_desktop_slow_interaction(
    kind: &'static str,
    duration: Duration,
    details: serde_json::Value,
) {
    if duration < DESKTOP_120FPS_FRAME_BUDGET {
        return;
    }
    let mode = desktop_frame_profile_mode();
    if !desktop_frame_profile_enabled(mode.as_deref()) {
        return;
    }
    emit_desktop_profile_event(
        "jcode-desktop-interaction-profile",
        serde_json::json!({
            "kind": kind,
            "budget_ms": duration_ms(DESKTOP_120FPS_FRAME_BUDGET),
            "duration_ms": duration_ms(duration),
            "details": details,
        }),
    );
}
