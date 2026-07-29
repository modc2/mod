//! Adaptive streaming-text reveal motion.
//!
//! Provider deltas arrive in bursty chunks. Instead of popping each chunk in,
//! the renderer reveals a smoothly-growing prefix of `streaming_response`:
//! `revealed_chars` eases toward the full length at a rate that adapts to the
//! backlog, so the reveal never lags far behind the model but always flows.
//! The trailing characters of the revealed prefix get a per-character alpha
//! ramp (the "tail fade") that settles to fully opaque when the stream pauses.

use super::super::*;

/// Base reveal speed in characters per second when there is no backlog.
pub(crate) const STREAMING_REVEAL_BASE_CPS: f32 = 220.0;
/// Extra reveal speed proportional to the backlog; the backlog roughly halves
/// every `ln(2)/CATCHUP ≈ 80ms`, so bursts catch up quickly without snapping.
pub(crate) const STREAMING_REVEAL_CATCHUP_PER_SEC: f32 = 9.0;
/// Hard cap on how far the reveal may lag behind the full response.
pub(crate) const STREAMING_REVEAL_MAX_LAG_CHARS: f32 = 480.0;
/// Characters revealed instantly when the first delta arrives, so the
/// transcript shows text on the very first frame of a response.
pub(crate) const STREAMING_REVEAL_INITIAL_CHARS: f32 = 12.0;
/// Width of the trailing alpha ramp, in characters.
pub(crate) const STREAMING_REVEAL_TAIL_FADE_CHARS: f32 = 14.0;
/// How long after reveal progress stops before the tail ramp fully settles
/// to opaque.
pub(crate) const STREAMING_REVEAL_TAIL_SETTLE: Duration = Duration::from_millis(240);
/// Clamp per-frame dt so a stalled event loop cannot teleport the reveal.
const STREAMING_REVEAL_MAX_FRAME_DT_SECONDS: f32 = 0.1;

/// Largest follow offset, in lines, the streaming auto-scroll will hold. Bursts
/// that append more lines at once snap the excess (rare) but keep the tail
/// smooth for the final lines.
pub(crate) const STREAMING_FOLLOW_MAX_OFFSET_LINES: f32 = 3.0;
/// Exponential decay time-constant for the follow offset. ~60ms reads as a
/// quick, smooth slide rather than a snap, settling in roughly 180ms.
const STREAMING_FOLLOW_DECAY_TAU_SECONDS: f32 = 0.06;
/// Clamp per-frame dt so a stalled event loop cannot teleport the follow slide.
const STREAMING_FOLLOW_MAX_FRAME_DT_SECONDS: f32 = 0.1;

/// Per-frame inputs for the streaming follow-scroll motion.
#[derive(Clone, Copy, Debug)]
pub(crate) struct StreamingFollowInput {
    /// Total wrapped body line count for the current frame.
    pub(crate) total_lines: usize,
    /// True when the transcript is pinned to the bottom (not user-scrolled).
    pub(crate) anchored_to_bottom: bool,
    /// True while a streaming response is actively growing.
    pub(crate) streaming_active: bool,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct StreamingFollowFrame {
    /// Extra scroll offset, in lines, to add to `smooth_scroll_lines`. Positive
    /// holds the viewport one or more lines above the very bottom so newly
    /// appended streaming lines slide in instead of snapping.
    pub(crate) offset_lines: f32,
    /// True while the slide is still settling and a redraw should be scheduled.
    pub(crate) active: bool,
}

/// Smoothly follows streaming text growth.
///
/// While the transcript is bottom-anchored and a response streams, each newly
/// wrapped body line would otherwise jump the viewport up by a full line. This
/// motion injects a transient positive scroll offset equal to the number of
/// lines just appended, then eases it back to zero, turning the per-line snap
/// into a continuous slide. It does nothing when the user has scrolled up or
/// when the transcript still has vertical slack (offset is clamped away by the
/// viewport in that case anyway).
#[derive(Default)]
pub(crate) struct StreamingFollowMotion {
    offset_lines: f32,
    last_total_lines: Option<usize>,
    last_update: Option<Instant>,
}

impl StreamingFollowMotion {
    pub(crate) fn frame(
        &mut self,
        input: StreamingFollowInput,
        now: Instant,
    ) -> StreamingFollowFrame {
        if crate::animation::desktop_reduced_motion_enabled() {
            self.clear();
            self.last_total_lines = Some(input.total_lines);
            return StreamingFollowFrame::default();
        }

        let dt = self
            .last_update
            .map(|last| {
                now.saturating_duration_since(last)
                    .as_secs_f32()
                    .min(STREAMING_FOLLOW_MAX_FRAME_DT_SECONDS)
            })
            .unwrap_or(0.0);
        self.last_update = Some(now);
        if dt > 0.0 && self.offset_lines != 0.0 {
            self.offset_lines *= (-dt / STREAMING_FOLLOW_DECAY_TAU_SECONDS).exp();
        }

        if !input.streaming_active || !input.anchored_to_bottom {
            // Reset the baseline so resuming follow never replays a stale delta,
            // and never fight a user who has scrolled up.
            self.last_total_lines = Some(input.total_lines);
            if !input.anchored_to_bottom {
                self.offset_lines = 0.0;
            }
        } else {
            if let Some(previous) = self.last_total_lines {
                if input.total_lines > previous {
                    let grew = (input.total_lines - previous) as f32;
                    self.offset_lines =
                        (self.offset_lines + grew).min(STREAMING_FOLLOW_MAX_OFFSET_LINES);
                } else if input.total_lines < previous {
                    self.offset_lines = self.offset_lines.min(input.total_lines as f32).max(0.0);
                }
            }
            self.last_total_lines = Some(input.total_lines);
        }

        if self.offset_lines.abs() < 0.01 {
            self.offset_lines = 0.0;
        }
        StreamingFollowFrame {
            offset_lines: self.offset_lines,
            active: self.offset_lines > 0.0,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.offset_lines = 0.0;
        self.last_total_lines = None;
        self.last_update = None;
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct StreamingTextRevealFrame {
    /// Byte offset (on a char boundary) of the revealed prefix.
    pub(crate) revealed_bytes: usize,
    /// Current tail-fade window width in characters; 0 disables the fade.
    pub(crate) tail_fade_chars: f32,
    /// True while the reveal or tail settle is still animating.
    pub(crate) active: bool,
}

#[derive(Default)]
pub(crate) struct StreamingTextRevealMotion {
    revealed_chars: f32,
    last_update: Option<Instant>,
    last_progress_at: Option<Instant>,
}

impl StreamingTextRevealMotion {
    pub(crate) fn frame(&mut self, response: &str, now: Instant) -> StreamingTextRevealFrame {
        if response.is_empty() {
            self.clear();
            return StreamingTextRevealFrame::default();
        }

        let target_chars = response.chars().count() as f32;
        if crate::animation::desktop_reduced_motion_enabled() {
            self.revealed_chars = target_chars;
            self.last_update = Some(now);
            self.last_progress_at = Some(now);
            return StreamingTextRevealFrame {
                revealed_bytes: response.len(),
                tail_fade_chars: 0.0,
                active: false,
            };
        }

        let dt = self
            .last_update
            .map(|last| {
                now.saturating_duration_since(last)
                    .as_secs_f32()
                    .min(STREAMING_REVEAL_MAX_FRAME_DT_SECONDS)
            })
            .unwrap_or(0.0);
        let previous = if self.last_update.is_some() {
            self.revealed_chars
        } else {
            STREAMING_REVEAL_INITIAL_CHARS.min(target_chars)
        };
        let revealed = advance_revealed_chars(previous, target_chars, dt);
        if revealed > previous + 0.01 || self.last_progress_at.is_none() {
            self.last_progress_at = Some(now);
        }
        self.revealed_chars = revealed;
        self.last_update = Some(now);

        let idle = self
            .last_progress_at
            .map(|at| now.saturating_duration_since(at))
            .unwrap_or_default();
        let tail_fade_chars = streaming_reveal_tail_fade_chars(idle);
        StreamingTextRevealFrame {
            revealed_bytes: char_floor_byte_offset(response, revealed),
            tail_fade_chars,
            active: revealed + 0.5 < target_chars || tail_fade_chars > 0.05,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.revealed_chars = 0.0;
        self.last_update = None;
        self.last_progress_at = None;
    }
}

/// Pure reveal-rate integration step. Monotonic, clamped to the target, and
/// never lags more than `STREAMING_REVEAL_MAX_LAG_CHARS` behind it.
pub(crate) fn advance_revealed_chars(revealed: f32, target: f32, dt_seconds: f32) -> f32 {
    if target <= 0.0 {
        return 0.0;
    }
    let revealed = revealed.min(target);
    let backlog = target - revealed;
    let rate = STREAMING_REVEAL_BASE_CPS + backlog * STREAMING_REVEAL_CATCHUP_PER_SEC;
    (revealed + rate * dt_seconds.max(0.0))
        .max(target - STREAMING_REVEAL_MAX_LAG_CHARS)
        .min(target)
}

/// Tail-fade window width given the time since the reveal last advanced. The
/// window shrinks to zero while the stream is paused so the trailing text
/// settles to full opacity instead of staying dim.
pub(crate) fn streaming_reveal_tail_fade_chars(idle: Duration) -> f32 {
    let settle = (idle.as_secs_f32() / STREAMING_REVEAL_TAIL_SETTLE.as_secs_f32()).clamp(0.0, 1.0);
    STREAMING_REVEAL_TAIL_FADE_CHARS * (1.0 - ease_out_cubic_local(settle))
}

/// Byte offset of the first `chars.floor()` characters of `text`.
pub(crate) fn char_floor_byte_offset(text: &str, chars: f32) -> usize {
    let count = chars.max(0.0).floor() as usize;
    if count == 0 {
        return 0;
    }
    text.char_indices()
        .nth(count)
        .map(|(offset, _)| offset)
        .unwrap_or(text.len())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn follow_input(total_lines: usize) -> StreamingFollowInput {
        StreamingFollowInput {
            total_lines,
            anchored_to_bottom: true,
            streaming_active: true,
        }
    }

    #[test]
    fn follow_offsets_on_growth_then_eases_to_zero() {
        let mut motion = StreamingFollowMotion::default();
        let mut now = Instant::now();
        // Establish a baseline line count.
        let baseline = motion.frame(follow_input(10), now);
        assert_eq!(baseline.offset_lines, 0.0);
        // One line appended -> a positive follow offset appears immediately.
        now += Duration::from_millis(16);
        let grew = motion.frame(follow_input(11), now);
        assert!(grew.offset_lines > 0.0);
        assert!(grew.offset_lines <= STREAMING_FOLLOW_MAX_OFFSET_LINES);
        assert!(grew.active);
        // With no further growth it eases back toward zero and settles.
        let mut frame = grew;
        for _ in 0..120 {
            now += Duration::from_millis(16);
            frame = motion.frame(follow_input(11), now);
        }
        assert_eq!(frame.offset_lines, 0.0);
        assert!(!frame.active);
    }

    #[test]
    fn follow_offset_is_clamped_for_large_bursts() {
        let mut motion = StreamingFollowMotion::default();
        let mut now = Instant::now();
        motion.frame(follow_input(10), now);
        now += Duration::from_millis(16);
        let burst = motion.frame(follow_input(40), now);
        assert!(burst.offset_lines <= STREAMING_FOLLOW_MAX_OFFSET_LINES + f32::EPSILON);
    }

    #[test]
    fn follow_does_nothing_when_user_scrolled_up() {
        let mut motion = StreamingFollowMotion::default();
        let mut now = Instant::now();
        motion.frame(follow_input(10), now);
        now += Duration::from_millis(16);
        let scrolled = motion.frame(
            StreamingFollowInput {
                total_lines: 11,
                anchored_to_bottom: false,
                streaming_active: true,
            },
            now,
        );
        assert_eq!(scrolled.offset_lines, 0.0);
        assert!(!scrolled.active);
    }

    #[test]
    fn follow_ignores_growth_while_idle() {
        let mut motion = StreamingFollowMotion::default();
        let mut now = Instant::now();
        motion.frame(
            StreamingFollowInput {
                total_lines: 10,
                anchored_to_bottom: true,
                streaming_active: false,
            },
            now,
        );
        now += Duration::from_millis(16);
        // Transcript grew (e.g. committed message) but no stream is active.
        let frame = motion.frame(
            StreamingFollowInput {
                total_lines: 14,
                anchored_to_bottom: true,
                streaming_active: false,
            },
            now,
        );
        assert_eq!(frame.offset_lines, 0.0);
    }

    #[test]
    fn follow_reduced_motion_disables_offset() {
        let _guard = crate::animation::DesktopReducedMotionEnvGuard::set(true);
        let mut motion = StreamingFollowMotion::default();
        let mut now = Instant::now();
        motion.frame(follow_input(10), now);
        now += Duration::from_millis(16);
        let frame = motion.frame(follow_input(20), now);
        assert_eq!(frame.offset_lines, 0.0);
        assert!(!frame.active);
    }

    #[test]
    fn advance_is_monotonic_and_clamped() {
        let mut revealed = 0.0;
        for _ in 0..240 {
            let next = advance_revealed_chars(revealed, 100.0, 1.0 / 60.0);
            assert!(next >= revealed);
            assert!(next <= 100.0);
            revealed = next;
        }
        assert!((revealed - 100.0).abs() < f32::EPSILON);
    }

    #[test]
    fn advance_catches_up_on_large_backlog() {
        // A huge burst must stay within the max-lag bound immediately.
        let revealed = advance_revealed_chars(10.0, 10_000.0, 0.0);
        assert!(revealed >= 10_000.0 - STREAMING_REVEAL_MAX_LAG_CHARS);
    }

    #[test]
    fn advance_handles_shrunk_target() {
        // If the response is replaced by shorter text, clamp down instead of
        // overflowing past the end.
        assert_eq!(advance_revealed_chars(50.0, 20.0, 0.016), 20.0);
        assert_eq!(advance_revealed_chars(50.0, 0.0, 0.016), 0.0);
    }

    #[test]
    fn char_floor_byte_offset_is_char_boundary_safe() {
        let text = "héllo wörld";
        for chars in 0..=text.chars().count() {
            let offset = char_floor_byte_offset(text, chars as f32);
            assert!(text.is_char_boundary(offset));
        }
        assert_eq!(char_floor_byte_offset(text, 100.0), text.len());
        assert_eq!(char_floor_byte_offset(text, 0.4), 0);
    }

    #[test]
    fn tail_fade_settles_when_idle() {
        assert!(streaming_reveal_tail_fade_chars(Duration::ZERO) > 10.0);
        assert_eq!(
            streaming_reveal_tail_fade_chars(STREAMING_REVEAL_TAIL_SETTLE),
            0.0
        );
        assert_eq!(
            streaming_reveal_tail_fade_chars(Duration::from_secs(5)),
            0.0
        );
    }

    #[test]
    fn frame_bootstraps_with_initial_chars() {
        let mut motion = StreamingTextRevealMotion::default();
        let now = Instant::now();
        let frame = motion.frame("a streaming response that is fairly long", now);
        assert!(frame.revealed_bytes >= STREAMING_REVEAL_INITIAL_CHARS as usize);
        assert!(frame.active);
    }

    #[test]
    fn frame_reaches_full_text_and_settles() {
        let mut motion = StreamingTextRevealMotion::default();
        let text = "short answer";
        let mut now = Instant::now();
        let mut frame = motion.frame(text, now);
        for _ in 0..600 {
            now += Duration::from_millis(16);
            frame = motion.frame(text, now);
        }
        assert_eq!(frame.revealed_bytes, text.len());
        assert!(!frame.active);
        assert_eq!(frame.tail_fade_chars, 0.0);
    }

    #[test]
    fn frame_resets_when_response_clears() {
        let mut motion = StreamingTextRevealMotion::default();
        let now = Instant::now();
        motion.frame("some streaming text", now);
        let frame = motion.frame("", now + Duration::from_millis(16));
        assert_eq!(frame, StreamingTextRevealFrame::default());
        // A new response restarts from the bootstrap reveal.
        let frame = motion.frame("fresh response", now + Duration::from_millis(32));
        assert!(frame.revealed_bytes > 0);
    }

    #[test]
    fn reduced_motion_reveals_everything_instantly() {
        let _guard = crate::animation::DesktopReducedMotionEnvGuard::set(true);
        let mut motion = StreamingTextRevealMotion::default();
        let text = "complete response text";
        let frame = motion.frame(text, Instant::now());
        assert_eq!(frame.revealed_bytes, text.len());
        assert!(!frame.active);
        assert_eq!(frame.tail_fade_chars, 0.0);
    }
}
