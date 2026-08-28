use super::*;

#[derive(Clone, Debug, Default)]
pub(crate) struct ScrollLineAccumulator {
    pub(crate) velocity_lines_per_second: f32,
    pub(crate) last_event_at: Option<Instant>,
    pub(crate) last_frame_at: Option<Instant>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct ScrollAnimationFrame {
    pub(crate) scroll_lines: Option<f32>,
    pub(crate) active: bool,
}

impl ScrollLineAccumulator {
    pub(crate) fn scroll_lines(&mut self, delta: MouseScrollDelta, now: Instant) -> Option<f32> {
        if self
            .last_event_at
            .is_some_and(|last| now.saturating_duration_since(last) > SCROLL_GESTURE_IDLE_RESET)
        {
            self.stop();
        }
        self.last_event_at = Some(now);
        self.last_frame_at = Some(now);
        self.input_delta(mouse_scroll_delta_lines(delta))
    }

    pub(crate) fn frame(&mut self, now: Instant) -> ScrollAnimationFrame {
        let Some(last_frame_at) = self.last_frame_at else {
            self.last_frame_at = Some(now);
            return ScrollAnimationFrame {
                scroll_lines: None,
                active: self.is_active(),
            };
        };

        let dt = now
            .saturating_duration_since(last_frame_at)
            .as_secs_f32()
            .min(SCROLL_FRAME_MAX_DT_SECONDS);
        self.last_frame_at = Some(now);

        if dt <= 0.0 || !self.is_active() {
            return ScrollAnimationFrame {
                scroll_lines: None,
                active: self.is_active(),
            };
        }

        let scroll_lines = if self.velocity_lines_per_second.abs() >= SCROLL_MOMENTUM_STOP_VELOCITY
        {
            let lines = self.velocity_lines_per_second * dt;
            let decay = (-SCROLL_MOMENTUM_DECAY_PER_SECOND * dt).exp();
            self.velocity_lines_per_second *= decay;
            if self.velocity_lines_per_second.abs() < SCROLL_MOMENTUM_STOP_VELOCITY {
                self.velocity_lines_per_second = 0.0;
            }
            (lines.abs() >= SCROLL_FRACTIONAL_EPSILON).then_some(lines)
        } else {
            self.velocity_lines_per_second = 0.0;
            None
        };

        ScrollAnimationFrame {
            scroll_lines,
            active: self.is_active(),
        }
    }

    pub(crate) fn reset(&mut self) {
        self.stop();
        self.last_event_at = None;
        self.last_frame_at = None;
    }

    pub(crate) fn stop(&mut self) {
        self.velocity_lines_per_second = 0.0;
    }

    pub(crate) fn pending_lines(&self) -> f32 {
        0.0
    }

    pub(crate) fn is_active(&self) -> bool {
        self.velocity_lines_per_second.abs() >= SCROLL_MOMENTUM_STOP_VELOCITY
    }

    pub(crate) fn input_delta(&mut self, lines: f32) -> Option<f32> {
        if !lines.is_finite() || lines.abs() < SCROLL_FRACTIONAL_EPSILON {
            return None;
        }

        let lines = lines.clamp(
            -MAX_MOUSE_SCROLL_LINES_PER_EVENT,
            MAX_MOUSE_SCROLL_LINES_PER_EVENT,
        );
        if self.velocity_lines_per_second.abs() >= SCROLL_MOMENTUM_STOP_VELOCITY
            && self.velocity_lines_per_second.signum() != lines.signum()
        {
            self.stop();
        }

        self.velocity_lines_per_second = (self.velocity_lines_per_second
            + lines * SCROLL_MOMENTUM_GAIN)
            .clamp(-SCROLL_MOMENTUM_MAX_VELOCITY, SCROLL_MOMENTUM_MAX_VELOCITY);
        Some(lines)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct SingleSessionScrollMotionFrame {
    pub(crate) visual_scroll_lines: f32,
    pub(crate) smooth_scroll_lines: f32,
    pub(crate) active: bool,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct SingleSessionScrollMotion {
    pub(crate) initialized: bool,
    pub(crate) start_lines: f32,
    pub(crate) current_lines: f32,
    pub(crate) target_lines: f32,
    pub(crate) started_at: Option<Instant>,
}

impl SingleSessionScrollMotion {
    pub(crate) fn frame(
        &mut self,
        target_lines: f32,
        now: Instant,
    ) -> SingleSessionScrollMotionFrame {
        let target_lines = if target_lines.is_finite() {
            target_lines.max(0.0)
        } else {
            0.0
        };

        if !self.initialized || animation::desktop_reduced_motion_enabled() {
            self.initialized = true;
            self.start_lines = target_lines;
            self.current_lines = target_lines;
            self.target_lines = target_lines;
            self.started_at = None;
            return SingleSessionScrollMotionFrame {
                visual_scroll_lines: target_lines,
                smooth_scroll_lines: 0.0,
                active: false,
            };
        }

        if (self.target_lines - target_lines).abs() >= SCROLL_FRACTIONAL_EPSILON {
            self.start_lines = self.current_lines;
            self.target_lines = target_lines;
            self.started_at = Some(now);
        }

        if let Some(started_at) = self.started_at {
            let progress = (now.saturating_duration_since(started_at).as_secs_f32()
                / SINGLE_SESSION_SCROLL_ANIMATION_DURATION.as_secs_f32())
            .clamp(0.0, 1.0);
            let eased = animation::ease_out_cubic(progress);
            self.current_lines = animation::lerp(self.start_lines, self.target_lines, eased);
            if progress >= 1.0
                || (self.current_lines - self.target_lines).abs() < SCROLL_FRACTIONAL_EPSILON
            {
                self.current_lines = self.target_lines;
                self.started_at = None;
            }
        }

        SingleSessionScrollMotionFrame {
            visual_scroll_lines: self.current_lines,
            smooth_scroll_lines: self.current_lines - target_lines,
            active: self.is_active(),
        }
    }

    pub(crate) fn is_active(&self) -> bool {
        self.started_at.is_some()
            || (self.current_lines - self.target_lines).abs() >= SCROLL_FRACTIONAL_EPSILON
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.start_lines = 0.0;
        self.current_lines = 0.0;
        self.target_lines = 0.0;
        self.started_at = None;
    }
}

#[cfg(test)]
pub(crate) fn mouse_scroll_lines(delta: MouseScrollDelta) -> Option<f32> {
    ScrollLineAccumulator::default().scroll_lines(delta, Instant::now())
}

pub(crate) fn mouse_scroll_delta_lines(delta: MouseScrollDelta) -> f32 {
    match delta {
        MouseScrollDelta::LineDelta(_, y) => y * MOUSE_WHEEL_LINES_PER_DETENT,
        MouseScrollDelta::PixelDelta(position) => position.y as f32 / body_scroll_line_pixels(),
    }
}

pub(crate) fn body_scroll_line_pixels() -> f32 {
    let typography = single_session_typography();
    typography.body_size * typography.body_line_height
}

pub(crate) fn desktop_spinner_tick(_now: Instant) -> u64 {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);
    (millis / DESKTOP_SPINNER_FRAME_MS) as u64
}

/// Continuous wall-clock seconds for smooth (unquantized) pulse animations.
/// Unlike `desktop_spinner_tick`, this is not stepped to 180ms frames, so
/// breathing cues animate fluidly at the paced 16ms redraw interval.
pub(crate) fn desktop_pulse_seconds() -> f32 {
    let millis = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0);
    // Wrap at a day to keep f32 precision; pulse phases only use fract().
    ((millis % 86_400_000) as f64 / 1000.0) as f32
}

pub(crate) fn single_session_text_buffer_cache_key(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    _tick: u64,
    rendered_body_key: u64,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    rendered_body_key.hash(&mut hasher);
    (size.width, size.height).hash(&mut hasher);
    app.is_welcome_timeline_visible().hash(&mut hasher);
    app.has_activity_indicator().hash(&mut hasher);
    app.text_scale().to_bits().hash(&mut hasher);
    app.header_title().hash(&mut hasher);
    app.welcome_hero_text().hash(&mut hasher);
    // Use the render-time styled lines (which honor the reveal animation) so the
    // text buffer cache key matches the content actually placed into the buffer.
    // Hashing the pre-reveal lines here while the buffer is built from the
    // reveal-aware lines causes a stale buffer: the picker chrome re-renders every
    // frame but the glyph text is never re-prepared as the reveal progresses.
    app.render_inline_widget_styled_lines().hash(&mut hasher);
    // The inline-widget card grows via a reveal animation, and the glyph text is
    // vertically clipped to the animating card bounds. Quantize the reveal
    // progress into the cache key so the text buffer is re-prepared across the
    // whole animation; otherwise the text is prepared once early (while the card
    // is still small and clips everything but the first line) and never refreshed,
    // leaving stale/partial text under fully-rendered chrome.
    if app.render_inline_widget_kind().is_some() {
        let reveal_bucket = (app.render_inline_widget_reveal_progress() * 32.0).round() as u32;
        reveal_bucket.hash(&mut hasher);
    }
    app.composer_text().hash(&mut hasher);
    hasher.finish()
}

pub(crate) fn single_session_body_text_window_bounds(
    viewport: &SingleSessionBodyViewport,
) -> (usize, usize) {
    let start = viewport
        .start_line
        .saturating_sub(SINGLE_SESSION_BODY_TEXT_WINDOW_BEFORE_LINES);
    let end = viewport
        .start_line
        .saturating_add(viewport.lines.len())
        .saturating_add(SINGLE_SESSION_BODY_TEXT_WINDOW_AFTER_LINES)
        .min(viewport.total_lines);
    (start, end.max(start))
}

pub(crate) fn single_session_body_text_window_contains(
    window_start: usize,
    window_end: usize,
    viewport: &SingleSessionBodyViewport,
) -> bool {
    let visible_end = viewport.start_line.saturating_add(viewport.lines.len());
    window_start <= viewport.start_line && visible_end <= window_end
}

#[derive(Default)]
pub(crate) struct SingleSessionScrollMetricsCache {
    pub(crate) key: Option<u64>,
    pub(crate) total_lines: usize,
    pub(crate) raw_body_key: Option<u64>,
    pub(crate) raw_body_lines: Vec<SingleSessionStyledLine>,
    pub(crate) streaming_base_key: Option<u64>,
    pub(crate) streaming_base_total_lines: usize,
}

impl SingleSessionScrollMetricsCache {
    pub(crate) fn metrics(
        &mut self,
        app: &SingleSessionApp,
        size: PhysicalSize<u32>,
    ) -> Option<SingleSessionBodyScrollMetrics> {
        let body_layout_size = single_session_body_layout_cache_size(app, size);
        let key = app.rendered_body_cache_key(body_layout_size);
        if self.key != Some(key) {
            if !app.streaming_response.is_empty() {
                let base_key = app.rendered_body_static_cache_key(body_layout_size);
                if self.streaming_base_key != Some(base_key) {
                    if let Some(base_lines) =
                        single_session_rendered_static_body_lines_for_streaming(app, size, 0)
                    {
                        self.streaming_base_total_lines = base_lines.len();
                        self.streaming_base_key = Some(base_key);
                    } else {
                        self.streaming_base_key = None;
                        self.streaming_base_total_lines = 0;
                    }
                }
                if self.streaming_base_key == Some(base_key) {
                    self.total_lines = self.streaming_base_total_lines
                        + single_session_streaming_response_rendered_body_line_count(app, size);
                } else {
                    self.total_lines =
                        single_session_rendered_body_lines_for_tick(app, size, 0).len();
                }
            } else {
                let raw_key = app.rendered_body_cache_key((0, 0));
                if self.raw_body_key != Some(raw_key) {
                    self.raw_body_lines = app.body_styled_lines_for_tick(0);
                    self.raw_body_key = Some(raw_key);
                }
                self.total_lines = single_session_rendered_body_lines_from_raw_ref(
                    app,
                    size,
                    &self.raw_body_lines,
                )
                .len();
                self.streaming_base_key = None;
                self.streaming_base_total_lines = 0;
            }
            self.key = Some(key);
        }
        single_session_body_scroll_metrics_for_total_lines(app, size, self.total_lines)
    }

    pub(crate) fn clear(&mut self) {
        self.key = None;
        self.total_lines = 0;
        self.raw_body_key = None;
        self.raw_body_lines.clear();
        self.streaming_base_key = None;
        self.streaming_base_total_lines = 0;
    }
}
