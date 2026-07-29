//! Motion state machines for transcript surfaces: transcript cards, per-message highlights, inline markdown pills, tool cards, and the shared SurfaceMotionVisual plus motion hashing helpers.

use super::*;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct SurfaceMotionVisual {
    pub(crate) opacity: f32,
    pub(crate) y_offset_pixels: f32,
    pub(crate) scale: f32,
}

impl Default for SurfaceMotionVisual {
    fn default() -> Self {
        Self {
            opacity: 1.0,
            y_offset_pixels: 0.0,
            scale: 1.0,
        }
    }
}

impl SurfaceMotionVisual {
    pub(crate) fn entry(entry_offset_pixels: f32, entry_scale: f32, progress: f32) -> Self {
        let eased = ease_out_cubic_local(progress);
        Self {
            opacity: eased,
            y_offset_pixels: (1.0 - eased) * entry_offset_pixels,
            scale: lerp_f32(entry_scale, 1.0, eased),
        }
    }

    pub(crate) fn exit(
        entry_offset_pixels: f32,
        entry_scale: f32,
        exit_offset_multiplier: f32,
        exit_scale_multiplier: f32,
        progress: f32,
    ) -> Self {
        let eased = ease_out_cubic_local(progress);
        Self {
            opacity: 1.0 - eased,
            y_offset_pixels: -entry_offset_pixels * exit_offset_multiplier * eased,
            scale: 1.0 - (1.0 - entry_scale) * exit_scale_multiplier * eased,
        }
    }

    pub(crate) fn apply_line_shift(
        &mut self,
        from_line: usize,
        to_line: usize,
        line_height: f32,
        progress: f32,
    ) {
        let eased = ease_out_cubic_local(progress);
        let line_delta = from_line as f32 - to_line as f32;
        self.y_offset_pixels += line_delta * line_height * (1.0 - eased);
    }
}

pub(crate) type TranscriptCardVisual = SurfaceMotionVisual;

#[derive(Clone, Copy, Debug)]
pub(crate) struct TranscriptCardLineShift {
    pub(crate) from_line: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct TranscriptCardMotionState {
    pub(crate) line: usize,
    pub(crate) last_run: SingleSessionTranscriptCardRun,
    pub(crate) entered_at: Option<Instant>,
    pub(crate) exiting_at: Option<Instant>,
    pub(crate) line_shift: Option<TranscriptCardLineShift>,
    pub(crate) last_seen_generation: u64,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct TranscriptCardMotionFrame {
    pub(crate) visuals: HashMap<u64, TranscriptCardVisual>,
    pub(crate) exiting: Vec<(SingleSessionTranscriptCardRun, TranscriptCardVisual)>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl TranscriptCardMotionFrame {
    pub(crate) fn visual_for_key(&self, key: u64) -> Option<TranscriptCardVisual> {
        self.visuals.get(&key).copied()
    }

    pub(crate) fn exiting(&self) -> &[(SingleSessionTranscriptCardRun, TranscriptCardVisual)] {
        &self.exiting
    }

    pub(crate) fn is_active(&self) -> bool {
        self.active
    }

    pub(crate) fn cache_key(&self) -> u64 {
        self.cache_key
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum TranscriptMessageRole {
    User,
    Assistant,
    Meta,
    Error,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) struct TranscriptMessageRun {
    pub(crate) line: usize,
    pub(crate) line_count: usize,
    pub(crate) role: TranscriptMessageRole,
}

pub(crate) type TranscriptMessageVisual = SurfaceMotionVisual;

#[derive(Clone, Copy, Debug)]
pub(crate) struct TranscriptMessageLineShift {
    pub(crate) from_line: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct TranscriptMessageMotionState {
    pub(crate) run: TranscriptMessageRun,
    pub(crate) entered_at: Option<Instant>,
    pub(crate) line_shift: Option<TranscriptMessageLineShift>,
    pub(crate) last_seen_generation: u64,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct TranscriptMessageMotionFrame {
    pub(crate) visuals: HashMap<u64, TranscriptMessageVisual>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl TranscriptMessageMotionFrame {
    pub(crate) fn visual_for_key(&self, key: u64) -> Option<TranscriptMessageVisual> {
        self.visuals.get(&key).copied()
    }

    pub(crate) fn is_active(&self) -> bool {
        self.active
    }

    pub(crate) fn cache_key(&self) -> u64 {
        self.cache_key
    }
}

#[derive(Default)]
pub(crate) struct TranscriptMessageMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) generation: u64,
    pub(crate) states: HashMap<u64, TranscriptMessageMotionState>,
}

#[derive(Default)]
pub(crate) struct TranscriptCardMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) generation: u64,
    pub(crate) states: HashMap<u64, TranscriptCardMotionState>,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum InlineMarkdownPillKind {
    Code,
    Math,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) struct InlineMarkdownPillRun {
    pub(crate) line: usize,
    pub(crate) start_column: usize,
    pub(crate) column_count: usize,
    pub(crate) kind: InlineMarkdownPillKind,
}

pub(crate) type InlineMarkdownPillVisual = SurfaceMotionVisual;

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineMarkdownPillLineShift {
    pub(crate) from_line: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineMarkdownPillMotionState {
    pub(crate) run: InlineMarkdownPillRun,
    pub(crate) entered_at: Option<Instant>,
    pub(crate) exiting_at: Option<Instant>,
    pub(crate) line_shift: Option<InlineMarkdownPillLineShift>,
    pub(crate) last_seen_generation: u64,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct InlineMarkdownPillMotionFrame {
    pub(crate) visuals: HashMap<u64, InlineMarkdownPillVisual>,
    pub(crate) exiting: Vec<(InlineMarkdownPillRun, InlineMarkdownPillVisual)>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl InlineMarkdownPillMotionFrame {
    pub(crate) fn visual_for_key(&self, key: u64) -> Option<InlineMarkdownPillVisual> {
        self.visuals.get(&key).copied()
    }

    pub(crate) fn exiting(&self) -> &[(InlineMarkdownPillRun, InlineMarkdownPillVisual)] {
        &self.exiting
    }

    pub(crate) fn is_active(&self) -> bool {
        self.active
    }

    pub(crate) fn cache_key(&self) -> u64 {
        self.cache_key
    }
}

#[derive(Default)]
pub(crate) struct InlineMarkdownPillMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) generation: u64,
    pub(crate) states: HashMap<u64, InlineMarkdownPillMotionState>,
}

impl TranscriptMessageMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        lines: &[SingleSessionStyledLine],
        line_height: f32,
        now: Instant,
    ) -> TranscriptMessageMotionFrame {
        self.generation = self.generation.wrapping_add(1).max(1);
        let generation = self.generation;
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        let animate_new_messages = self.initialized && !reduced_motion;
        self.initialized = true;

        let mut visuals = HashMap::new();
        let mut active = false;
        let mut occurrences = HashMap::new();
        for run in single_session_transcript_message_runs(lines) {
            let key = transcript_message_motion_key(lines, &run, &mut occurrences);
            let state = self
                .states
                .entry(key)
                .or_insert_with(|| TranscriptMessageMotionState {
                    run,
                    entered_at: animate_new_messages.then_some(now),
                    line_shift: None,
                    last_seen_generation: generation,
                });
            state.last_seen_generation = generation;

            if reduced_motion {
                state.entered_at = None;
                state.line_shift = None;
            }

            if state.run.line != run.line {
                if reduced_motion {
                    state.line_shift = None;
                } else {
                    state.line_shift = Some(TranscriptMessageLineShift {
                        from_line: state.run.line,
                        started_at: now,
                    });
                }
            }
            state.run = run;

            let (visual, visual_active) =
                transcript_message_visual_from_state(state, line_height, now);
            active |= visual_active;
            visuals.insert(key, visual);
        }

        self.states
            .retain(|_, state| state.last_seen_generation == generation);

        TranscriptMessageMotionFrame {
            cache_key: transcript_message_motion_cache_key(&visuals, active),
            visuals,
            active,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.generation = 0;
        self.states.clear();
    }
}

impl TranscriptCardMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        lines: &[SingleSessionStyledLine],
        line_height: f32,
        now: Instant,
    ) -> TranscriptCardMotionFrame {
        self.generation = self.generation.wrapping_add(1).max(1);
        let generation = self.generation;
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        let animate_new_cards = self.initialized && !reduced_motion;
        self.initialized = true;

        let mut visuals = HashMap::new();
        let mut active = false;
        let mut occurrences = HashMap::new();
        for run in single_session_transcript_card_runs(lines) {
            let key = transcript_card_motion_key(lines, &run, &mut occurrences);
            let state = self
                .states
                .entry(key)
                .or_insert_with(|| TranscriptCardMotionState {
                    line: run.line,
                    last_run: run,
                    entered_at: animate_new_cards.then_some(now),
                    exiting_at: None,
                    line_shift: None,
                    last_seen_generation: generation,
                });
            state.last_seen_generation = generation;
            state.last_run = run;
            state.exiting_at = None;

            if reduced_motion {
                state.entered_at = None;
                state.line_shift = None;
            }

            if state.line != run.line {
                if reduced_motion {
                    state.line_shift = None;
                } else {
                    state.line_shift = Some(TranscriptCardLineShift {
                        from_line: state.line,
                        started_at: now,
                    });
                }
                state.line = run.line;
            }

            let (visual, visual_active) =
                transcript_card_visual_from_state(state, line_height, now);
            active |= visual_active;
            visuals.insert(key, visual);
        }

        let mut exiting = Vec::new();
        if !reduced_motion {
            for state in self.states.values_mut() {
                if state.last_seen_generation == generation {
                    continue;
                }
                let exiting_at = *state.exiting_at.get_or_insert(now);
                let (progress, running) =
                    timed_animation_progress(exiting_at, now, TRANSCRIPT_CARD_EXIT_DURATION);
                if !running {
                    continue;
                }
                active = true;
                state.last_seen_generation = generation;
                exiting.push((state.last_run, exiting_transcript_card_visual(progress)));
            }
        }

        self.states
            .retain(|_, state| state.last_seen_generation == generation);

        TranscriptCardMotionFrame {
            cache_key: transcript_card_motion_cache_key(&visuals, &exiting, active),
            visuals,
            exiting,
            active,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.generation = 0;
        self.states.clear();
    }
}

impl InlineMarkdownPillMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        lines: &[SingleSessionStyledLine],
        line_height: f32,
        now: Instant,
    ) -> InlineMarkdownPillMotionFrame {
        self.generation = self.generation.wrapping_add(1).max(1);
        let generation = self.generation;
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        let animate_new_pills = self.initialized && !reduced_motion;
        self.initialized = true;

        let mut visuals = HashMap::new();
        let mut active = false;
        let mut occurrences = HashMap::new();
        for run in single_session_inline_markdown_pill_runs(lines) {
            let key = inline_markdown_pill_motion_key(lines, &run, &mut occurrences);
            let state = self
                .states
                .entry(key)
                .or_insert_with(|| InlineMarkdownPillMotionState {
                    run,
                    entered_at: animate_new_pills.then_some(now),
                    exiting_at: None,
                    line_shift: None,
                    last_seen_generation: generation,
                });
            state.last_seen_generation = generation;
            state.exiting_at = None;

            if reduced_motion {
                state.entered_at = None;
                state.line_shift = None;
            }

            if state.run.line != run.line {
                if reduced_motion {
                    state.line_shift = None;
                } else {
                    state.line_shift = Some(InlineMarkdownPillLineShift {
                        from_line: state.run.line,
                        started_at: now,
                    });
                }
            }
            state.run = run;

            let (visual, visual_active) =
                inline_markdown_pill_visual_from_state(state, line_height, now);
            active |= visual_active;
            visuals.insert(key, visual);
        }

        let mut exiting = Vec::new();
        if !reduced_motion {
            for state in self.states.values_mut() {
                if state.last_seen_generation == generation {
                    continue;
                }
                let exiting_at = *state.exiting_at.get_or_insert(now);
                let (progress, running) =
                    timed_animation_progress(exiting_at, now, INLINE_MARKDOWN_PILL_EXIT_DURATION);
                if !running {
                    continue;
                }
                active = true;
                state.last_seen_generation = generation;
                exiting.push((state.run, exiting_inline_markdown_pill_visual(progress)));
            }
        }

        self.states
            .retain(|_, state| state.last_seen_generation == generation);

        InlineMarkdownPillMotionFrame {
            cache_key: inline_markdown_pill_motion_cache_key(&visuals, &exiting, active),
            visuals,
            exiting,
            active,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.generation = 0;
        self.states.clear();
    }
}

#[cfg(test)]
#[derive(Clone, Copy, Debug)]
pub(crate) struct SingleSessionTranscriptCardGeometry {
    pub(crate) run: SingleSessionTranscriptCardRun,
    pub(crate) card_rect: Rect,
    pub(crate) text_left: f32,
    pub(crate) line_height: f32,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SingleSessionToolCardRun {
    pub(crate) line: usize,
    pub(crate) line_count: usize,
    pub(crate) call_id: String,
    pub(crate) name: String,
    pub(crate) state: SingleSessionToolVisualState,
    pub(crate) active: bool,
    pub(crate) expanded: bool,
    pub(crate) detail_line_count: usize,
    pub(crate) kind: SingleSessionToolLineKind,
}

#[cfg(test)]
#[derive(Clone, Debug)]
pub(crate) struct SingleSessionToolCardGeometry {
    pub(crate) run: SingleSessionToolCardRun,
    pub(crate) card_rect: Rect,
    pub(crate) rail_rect: Rect,
    pub(crate) line_height: f32,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ToolCardPalette {
    pub(crate) background: [f32; 4],
    pub(crate) border: [f32; 4],
    pub(crate) rail: [f32; 4],
    pub(crate) chip: [f32; 4],
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ToolCardStateTransition {
    pub(crate) from_state: SingleSessionToolVisualState,
    pub(crate) from_active: bool,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ToolCardOutputTransition {
    pub(crate) from_detail_line_count: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ToolCardResolutionFlash {
    pub(crate) state: SingleSessionToolVisualState,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Debug)]
pub(crate) struct ToolCardMotionState {
    pub(crate) target_state: SingleSessionToolVisualState,
    pub(crate) target_active: bool,
    pub(crate) detail_line_count: usize,
    pub(crate) last_run: SingleSessionToolCardRun,
    pub(crate) entered_at: Option<Instant>,
    pub(crate) exiting_at: Option<Instant>,
    pub(crate) state_transition: Option<ToolCardStateTransition>,
    pub(crate) output_transition: Option<ToolCardOutputTransition>,
    pub(crate) resolution_flash: Option<ToolCardResolutionFlash>,
    pub(crate) last_seen_generation: u64,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ToolCardVisual {
    pub(crate) opacity: f32,
    pub(crate) y_offset_pixels: f32,
    pub(crate) scale: f32,
    pub(crate) background: [f32; 4],
    pub(crate) border: [f32; 4],
    pub(crate) rail: [f32; 4],
    pub(crate) chip: [f32; 4],
    pub(crate) output_reveal: f32,
    pub(crate) flash_color: [f32; 4],
    pub(crate) flash_alpha: f32,
    pub(crate) active_phase: f32,
}

impl Default for ToolCardVisual {
    fn default() -> Self {
        Self {
            opacity: 1.0,
            y_offset_pixels: 0.0,
            scale: 1.0,
            background: TOOL_CARD_BACKGROUND_COLOR,
            border: TOOL_CARD_BORDER_COLOR,
            rail: TOOL_TIMELINE_RAIL_COLOR,
            chip: TOOL_STATUS_CHIP_COLOR,
            output_reveal: 1.0,
            flash_color: TOOL_TIMELINE_RAIL_COLOR,
            flash_alpha: 0.0,
            active_phase: 0.0,
        }
    }
}

#[derive(Clone, Debug, Default)]
pub(crate) struct ToolCardMotionFrame {
    pub(crate) visuals: HashMap<String, ToolCardVisual>,
    pub(crate) exiting: Vec<(SingleSessionToolCardRun, ToolCardVisual)>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl ToolCardMotionFrame {
    pub(crate) fn visual_for(&self, call_id: &str) -> Option<ToolCardVisual> {
        self.visuals.get(call_id).copied()
    }

    pub(crate) fn is_active(&self) -> bool {
        self.active
    }

    pub(crate) fn cache_key(&self) -> u64 {
        self.cache_key
    }

    pub(crate) fn exiting(&self) -> &[(SingleSessionToolCardRun, ToolCardVisual)] {
        &self.exiting
    }
}

#[derive(Default)]
pub(crate) struct ToolCardMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) generation: u64,
    pub(crate) states: HashMap<String, ToolCardMotionState>,
}

impl ToolCardMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        lines: &[SingleSessionStyledLine],
        now: Instant,
        motion_seconds: f32,
    ) -> ToolCardMotionFrame {
        self.generation = self.generation.wrapping_add(1).max(1);
        let generation = self.generation;
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        let animate_new_cards = self.initialized && !reduced_motion;
        self.initialized = true;

        let mut visuals = HashMap::new();
        let mut active = false;
        for run in single_session_tool_card_runs(lines) {
            let state =
                self.states
                    .entry(run.call_id.clone())
                    .or_insert_with(|| ToolCardMotionState {
                        target_state: run.state,
                        target_active: run.active,
                        detail_line_count: run.detail_line_count,
                        last_run: run.clone(),
                        entered_at: animate_new_cards.then_some(now),
                        exiting_at: None,
                        state_transition: None,
                        output_transition: None,
                        resolution_flash: None,
                        last_seen_generation: generation,
                    });
            state.last_seen_generation = generation;
            state.exiting_at = None;

            if state.target_state != run.state || state.target_active != run.active {
                let previous_state = state.target_state;
                let previous_active = state.target_active;
                state.state_transition = Some(ToolCardStateTransition {
                    from_state: previous_state,
                    from_active: previous_active,
                    started_at: now,
                });
                if (previous_state.is_active() || previous_active)
                    && !(run.state.is_active() || run.active)
                    && matches!(
                        run.state,
                        SingleSessionToolVisualState::Succeeded
                            | SingleSessionToolVisualState::Failed
                    )
                {
                    state.resolution_flash = Some(ToolCardResolutionFlash {
                        state: run.state,
                        started_at: now,
                    });
                }
                state.target_state = run.state;
                state.target_active = run.active;
            }

            if state.detail_line_count != run.detail_line_count {
                state.output_transition = Some(ToolCardOutputTransition {
                    from_detail_line_count: state.detail_line_count,
                    started_at: now,
                });
                state.detail_line_count = run.detail_line_count;
            }

            state.last_run = run.clone();

            let (visual, visual_active) =
                tool_card_visual_from_state(state, &run, now, motion_seconds, reduced_motion);
            active |= visual_active || (!reduced_motion && (run.active || run.state.is_active()));
            visuals.insert(run.call_id, visual);
        }

        let mut exiting = Vec::new();
        for state in self.states.values_mut() {
            if state.last_seen_generation == generation {
                continue;
            }
            let exiting_at = *state.exiting_at.get_or_insert(now);
            let (progress, running) =
                timed_animation_progress(exiting_at, now, TOOL_CARD_EXIT_DURATION);
            if !running {
                continue;
            }
            let visual = exiting_tool_card_visual(&state.last_run, progress, motion_seconds);
            active = true;
            state.last_seen_generation = generation;
            exiting.push((state.last_run.clone(), visual));
        }

        self.states
            .retain(|_, state| state.last_seen_generation == generation);

        ToolCardMotionFrame {
            cache_key: tool_card_motion_cache_key(&visuals, &exiting, active),
            visuals,
            exiting,
            active,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.generation = 0;
        self.states.clear();
    }
}

pub(crate) fn tool_card_visual_from_state(
    state: &mut ToolCardMotionState,
    run: &SingleSessionToolCardRun,
    now: Instant,
    motion_seconds: f32,
    reduced_motion: bool,
) -> (ToolCardVisual, bool) {
    let target_palette = tool_card_palette(run.state, run.active);
    let mut palette = target_palette;
    let mut active = false;

    if let Some(transition) = state.state_transition {
        let (progress, running) = timed_animation_progress(
            transition.started_at,
            now,
            TOOL_CARD_STATE_TRANSITION_DURATION,
        );
        let eased = ease_out_cubic_local(progress);
        let from = tool_card_palette(transition.from_state, transition.from_active);
        palette = mix_tool_card_palette(from, target_palette, eased);
        active |= running;
        if !running {
            state.state_transition = None;
        }
    }

    let mut opacity = 1.0;
    let mut y_offset_pixels = 0.0;
    let mut scale = 1.0;
    if let Some(entered_at) = state.entered_at {
        let (progress, running) =
            timed_animation_progress(entered_at, now, TOOL_CARD_ENTRY_DURATION);
        let eased = ease_out_cubic_local(progress);
        opacity = eased;
        y_offset_pixels = (1.0 - eased) * TOOL_CARD_ENTRY_OFFSET_PIXELS;
        scale = TOOL_CARD_ENTRY_SCALE + (1.0 - TOOL_CARD_ENTRY_SCALE) * eased;
        active |= running;
        if !running {
            state.entered_at = None;
        }
    }

    let mut output_reveal = 1.0;
    if let Some(transition) = state.output_transition {
        let (progress, running) =
            timed_animation_progress(transition.started_at, now, TOOL_CARD_OUTPUT_REVEAL_DURATION);
        let eased = ease_out_cubic_local(progress);
        if state.detail_line_count > transition.from_detail_line_count {
            output_reveal = eased;
        } else {
            output_reveal = 1.0 - eased;
        }
        active |= running;
        if !running {
            state.output_transition = None;
            output_reveal = 1.0;
        }
    }

    let mut flash_color = TOOL_TIMELINE_RAIL_COLOR;
    let mut flash_alpha = 0.0;
    if let Some(flash) = state.resolution_flash {
        let (progress, running) =
            timed_animation_progress(flash.started_at, now, TOOL_CARD_RESOLUTION_FLASH_DURATION);
        let fade = 1.0 - ease_out_cubic_local(progress);
        flash_color = single_session_tool_state_accent(flash.state);
        flash_alpha = (0.34 * fade).clamp(0.0, 0.34);
        active |= running;
        if !running {
            state.resolution_flash = None;
        }
    }

    let pulse = if reduced_motion {
        0.0
    } else {
        active_tool_card_pulse(motion_seconds)
    };
    // Only active cards consume the sweep phase; keeping it zero for settled
    // cards lets the primitive geometry cache stay warm while nothing moves.
    let card_is_active = run.active || run.state.is_active();
    let active_phase = if reduced_motion || !card_is_active {
        0.0
    } else {
        active_tool_card_sweep_phase(motion_seconds)
    };
    if card_is_active {
        palette.background[3] = (palette.background[3] + 0.08 * pulse).clamp(0.0, 0.82);
        palette.border[3] = (palette.border[3] + 0.16 * pulse).clamp(0.0, 0.62);
        palette.rail[3] = (palette.rail[3] + 0.24 * pulse).clamp(0.0, 0.78);
    }

    (
        ToolCardVisual {
            opacity,
            y_offset_pixels,
            scale,
            background: palette.background,
            border: palette.border,
            rail: palette.rail,
            chip: palette.chip,
            output_reveal,
            flash_color,
            flash_alpha,
            active_phase,
        },
        active,
    )
}

pub(crate) fn exiting_tool_card_visual(
    run: &SingleSessionToolCardRun,
    progress: f32,
    motion_seconds: f32,
) -> ToolCardVisual {
    let eased = ease_out_cubic_local(progress);
    let mut visual =
        default_tool_card_visual(run, active_tool_card_pulse(motion_seconds), motion_seconds);
    visual.opacity = 1.0 - eased;
    visual.y_offset_pixels = -TOOL_CARD_ENTRY_OFFSET_PIXELS * 0.55 * eased;
    visual.scale = 1.0 - (1.0 - TOOL_CARD_ENTRY_SCALE) * eased;
    visual.output_reveal = 1.0 - eased * 0.65;
    visual
}

pub(crate) fn timed_animation_progress(
    started_at: Instant,
    now: Instant,
    duration: Duration,
) -> (f32, bool) {
    if duration.is_zero() || crate::animation::desktop_reduced_motion_enabled() {
        return (1.0, false);
    }
    let progress = (now.saturating_duration_since(started_at).as_secs_f32()
        / duration.as_secs_f32())
    .clamp(0.0, 1.0);
    (progress, progress < 1.0)
}

pub(crate) fn tool_card_palette(
    state: SingleSessionToolVisualState,
    active: bool,
) -> ToolCardPalette {
    let accent = single_session_tool_state_accent(state);
    let background = single_session_tool_card_background(state, active);
    let border = if active || state.is_active() {
        TOOL_CARD_ACTIVE_BORDER_COLOR
    } else if matches!(
        state,
        SingleSessionToolVisualState::Succeeded | SingleSessionToolVisualState::Failed
    ) {
        with_alpha(accent, 0.44)
    } else {
        TOOL_CARD_BORDER_COLOR
    };
    let rail = if active || state.is_active() {
        TOOL_TIMELINE_ACTIVE_RAIL_COLOR
    } else {
        accent
    };
    let chip = mix_color(
        TOOL_STATUS_CHIP_COLOR,
        with_alpha(accent, TOOL_STATUS_CHIP_COLOR[3]),
        0.22,
    );
    ToolCardPalette {
        background,
        border,
        rail,
        chip,
    }
}

pub(crate) fn mix_tool_card_palette(
    from: ToolCardPalette,
    to: ToolCardPalette,
    progress: f32,
) -> ToolCardPalette {
    ToolCardPalette {
        background: mix_color(from.background, to.background, progress),
        border: mix_color(from.border, to.border, progress),
        rail: mix_color(from.rail, to.rail, progress),
        chip: mix_color(from.chip, to.chip, progress),
    }
}

pub(crate) fn tool_card_motion_cache_key(
    visuals: &HashMap<String, ToolCardVisual>,
    exiting: &[(SingleSessionToolCardRun, ToolCardVisual)],
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    let mut entries = visuals.iter().collect::<Vec<_>>();
    entries.sort_by_key(|(left, _)| *left);
    for (call_id, visual) in entries {
        call_id.hash(&mut hasher);
        hash_f32(visual.opacity, &mut hasher);
        hash_f32(visual.y_offset_pixels, &mut hasher);
        hash_f32(visual.scale, &mut hasher);
        hash_color(visual.background, &mut hasher);
        hash_color(visual.border, &mut hasher);
        hash_color(visual.rail, &mut hasher);
        hash_color(visual.chip, &mut hasher);
        hash_f32(visual.output_reveal, &mut hasher);
        hash_color(visual.flash_color, &mut hasher);
        hash_f32(visual.flash_alpha, &mut hasher);
        hash_f32(visual.active_phase, &mut hasher);
    }
    for (run, visual) in exiting {
        run.call_id.hash(&mut hasher);
        run.line.hash(&mut hasher);
        run.line_count.hash(&mut hasher);
        hash_f32(visual.opacity, &mut hasher);
        hash_f32(visual.y_offset_pixels, &mut hasher);
        hash_f32(visual.scale, &mut hasher);
        hash_color(visual.background, &mut hasher);
        hash_color(visual.border, &mut hasher);
        hash_color(visual.rail, &mut hasher);
        hash_color(visual.chip, &mut hasher);
        hash_f32(visual.output_reveal, &mut hasher);
        hash_f32(visual.active_phase, &mut hasher);
    }
    hasher.finish()
}

pub(crate) fn hash_color(color: [f32; 4], hasher: &mut impl Hasher) {
    for component in color {
        hash_f32(component, hasher);
    }
}

pub(crate) fn hash_f32(value: f32, hasher: &mut impl Hasher) {
    value.to_bits().hash(hasher);
}

pub(crate) fn motion_occurrence_key(base_key: u64, occurrences: &mut HashMap<u64, usize>) -> u64 {
    let occurrence = occurrences.entry(base_key).or_insert(0);
    let occurrence_index = *occurrence;
    *occurrence += 1;

    let mut hasher = DefaultHasher::new();
    base_key.hash(&mut hasher);
    occurrence_index.hash(&mut hasher);
    hasher.finish()
}

pub(crate) fn sorted_u64_visual_entries<V>(visuals: &HashMap<u64, V>) -> Vec<(&u64, &V)> {
    let mut entries = visuals.iter().collect::<Vec<_>>();
    entries.sort_by_key(|(key, _)| **key);
    entries
}

pub(crate) fn hash_surface_motion_visual(visual: SurfaceMotionVisual, hasher: &mut impl Hasher) {
    hash_f32(visual.opacity, hasher);
    hash_f32(visual.y_offset_pixels, hasher);
    hash_f32(visual.scale, hasher);
}

pub(crate) fn surface_motion_visual_rect(rect: Rect, visual: SurfaceMotionVisual) -> Rect {
    let scale = visual.scale.clamp(0.01, 1.5);
    let width = rect.width * scale;
    let height = rect.height * scale;
    Rect {
        x: rect.x + (rect.width - width) * 0.5,
        y: rect.y + (rect.height - height) * 0.5 + visual.y_offset_pixels,
        width,
        height,
    }
}

pub(crate) fn surface_motion_alpha(mut color: [f32; 4], opacity: f32) -> [f32; 4] {
    color[3] *= opacity.clamp(0.0, 1.0);
    color
}

pub(crate) fn transcript_card_visual_from_state(
    state: &mut TranscriptCardMotionState,
    line_height: f32,
    now: Instant,
) -> (TranscriptCardVisual, bool) {
    let mut visual = TranscriptCardVisual::default();
    let mut active = false;

    if let Some(entered_at) = state.entered_at {
        let (progress, running) =
            timed_animation_progress(entered_at, now, TRANSCRIPT_CARD_ENTRY_DURATION);
        visual = SurfaceMotionVisual::entry(
            TRANSCRIPT_CARD_ENTRY_OFFSET_PIXELS,
            TRANSCRIPT_CARD_ENTRY_SCALE,
            progress,
        );
        active |= running;
        if !running {
            state.entered_at = None;
        }
    }

    if let Some(shift) = state.line_shift {
        let (progress, running) =
            timed_animation_progress(shift.started_at, now, TRANSCRIPT_CARD_SHIFT_DURATION);
        visual.apply_line_shift(shift.from_line, state.line, line_height, progress);
        active |= running;
        if !running {
            state.line_shift = None;
        }
    }

    (visual, active)
}

pub(crate) fn transcript_message_visual_from_state(
    state: &mut TranscriptMessageMotionState,
    line_height: f32,
    now: Instant,
) -> (TranscriptMessageVisual, bool) {
    let mut visual = TranscriptMessageVisual::default();
    let mut active = false;

    if let Some(entered_at) = state.entered_at {
        let (progress, running) =
            timed_animation_progress(entered_at, now, TRANSCRIPT_MESSAGE_ENTRY_DURATION);
        visual = SurfaceMotionVisual::entry(
            TRANSCRIPT_MESSAGE_ENTRY_OFFSET_PIXELS,
            TRANSCRIPT_MESSAGE_ENTRY_SCALE,
            progress,
        );
        active |= running;
        if !running {
            state.entered_at = None;
        }
    }

    if let Some(shift) = state.line_shift {
        let (progress, running) =
            timed_animation_progress(shift.started_at, now, TRANSCRIPT_MESSAGE_SHIFT_DURATION);
        visual.apply_line_shift(shift.from_line, state.run.line, line_height, progress);
        active |= running;
        if !running {
            state.line_shift = None;
        }
    }

    (visual, active)
}

pub(crate) fn exiting_transcript_card_visual(progress: f32) -> TranscriptCardVisual {
    SurfaceMotionVisual::exit(
        TRANSCRIPT_CARD_ENTRY_OFFSET_PIXELS,
        TRANSCRIPT_CARD_ENTRY_SCALE,
        0.42,
        1.35,
        progress,
    )
}

pub(crate) fn inline_markdown_pill_visual_from_state(
    state: &mut InlineMarkdownPillMotionState,
    line_height: f32,
    now: Instant,
) -> (InlineMarkdownPillVisual, bool) {
    let mut visual = InlineMarkdownPillVisual::default();
    let mut active = false;

    if let Some(entered_at) = state.entered_at {
        let (progress, running) =
            timed_animation_progress(entered_at, now, INLINE_MARKDOWN_PILL_ENTRY_DURATION);
        visual = SurfaceMotionVisual::entry(
            INLINE_MARKDOWN_PILL_ENTRY_OFFSET_PIXELS,
            INLINE_MARKDOWN_PILL_ENTRY_SCALE,
            progress,
        );
        active |= running;
        if !running {
            state.entered_at = None;
        }
    }

    if let Some(shift) = state.line_shift {
        let (progress, running) =
            timed_animation_progress(shift.started_at, now, INLINE_MARKDOWN_PILL_SHIFT_DURATION);
        visual.apply_line_shift(shift.from_line, state.run.line, line_height, progress);
        active |= running;
        if !running {
            state.line_shift = None;
        }
    }

    (visual, active)
}

pub(crate) fn exiting_inline_markdown_pill_visual(progress: f32) -> InlineMarkdownPillVisual {
    SurfaceMotionVisual::exit(
        INLINE_MARKDOWN_PILL_ENTRY_OFFSET_PIXELS,
        INLINE_MARKDOWN_PILL_ENTRY_SCALE,
        0.55,
        1.0,
        progress,
    )
}

pub(crate) fn transcript_card_visual_rect(rect: Rect, visual: TranscriptCardVisual) -> Rect {
    surface_motion_visual_rect(rect, visual)
}

pub(crate) fn transcript_card_alpha(color: [f32; 4], opacity: f32) -> [f32; 4] {
    surface_motion_alpha(color, opacity)
}

pub(crate) fn inline_markdown_pill_visual_rect(
    rect: Rect,
    visual: InlineMarkdownPillVisual,
) -> Rect {
    surface_motion_visual_rect(rect, visual)
}

pub(crate) fn inline_markdown_pill_alpha(color: [f32; 4], opacity: f32) -> [f32; 4] {
    surface_motion_alpha(color, opacity)
}

pub(crate) fn transcript_message_motion_key(
    lines: &[SingleSessionStyledLine],
    run: &TranscriptMessageRun,
    occurrences: &mut HashMap<u64, usize>,
) -> u64 {
    let base_key = transcript_message_motion_base_key(lines, run);
    motion_occurrence_key(base_key, occurrences)
}

pub(crate) fn transcript_message_motion_base_key(
    lines: &[SingleSessionStyledLine],
    run: &TranscriptMessageRun,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    run.role.hash(&mut hasher);
    run.line_count.hash(&mut hasher);
    let end = run.line.saturating_add(run.line_count).min(lines.len());
    for line in &lines[run.line.min(lines.len())..end] {
        line.style.hash(&mut hasher);
        line.text.hash(&mut hasher);
        line.inline_spans.hash(&mut hasher);
        line.tool.hash(&mut hasher);
    }
    hasher.finish()
}

pub(crate) fn transcript_message_motion_cache_key(
    visuals: &HashMap<u64, TranscriptMessageVisual>,
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    for (key, visual) in sorted_u64_visual_entries(visuals) {
        key.hash(&mut hasher);
        hash_surface_motion_visual(*visual, &mut hasher);
    }
    hasher.finish()
}

pub(crate) fn transcript_card_motion_key(
    lines: &[SingleSessionStyledLine],
    run: &SingleSessionTranscriptCardRun,
    occurrences: &mut HashMap<u64, usize>,
) -> u64 {
    let base_key = transcript_card_motion_base_key(lines, run);
    motion_occurrence_key(base_key, occurrences)
}

pub(crate) fn transcript_card_motion_base_key(
    lines: &[SingleSessionStyledLine],
    run: &SingleSessionTranscriptCardRun,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    run.style.hash(&mut hasher);
    run.line_count.hash(&mut hasher);
    let end = run.line.saturating_add(run.line_count).min(lines.len());
    for line in &lines[run.line.min(lines.len())..end] {
        line.style.hash(&mut hasher);
        line.text.hash(&mut hasher);
        line.inline_spans.len().hash(&mut hasher);
    }
    hasher.finish()
}

pub(crate) fn transcript_card_motion_cache_key(
    visuals: &HashMap<u64, TranscriptCardVisual>,
    exiting: &[(SingleSessionTranscriptCardRun, TranscriptCardVisual)],
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    for (key, visual) in sorted_u64_visual_entries(visuals) {
        key.hash(&mut hasher);
        hash_surface_motion_visual(*visual, &mut hasher);
    }
    for (run, visual) in exiting {
        run.line.hash(&mut hasher);
        run.line_count.hash(&mut hasher);
        run.style.hash(&mut hasher);
        hash_surface_motion_visual(*visual, &mut hasher);
    }
    hasher.finish()
}

pub(crate) fn inline_markdown_pill_motion_key(
    lines: &[SingleSessionStyledLine],
    run: &InlineMarkdownPillRun,
    occurrences: &mut HashMap<u64, usize>,
) -> u64 {
    let base_key = inline_markdown_pill_motion_base_key(lines, run);
    motion_occurrence_key(base_key, occurrences)
}

pub(crate) fn inline_markdown_pill_motion_base_key(
    lines: &[SingleSessionStyledLine],
    run: &InlineMarkdownPillRun,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    run.kind.hash(&mut hasher);
    run.start_column.hash(&mut hasher);
    run.column_count.hash(&mut hasher);
    if let Some(line) = lines.get(run.line) {
        line.style.hash(&mut hasher);
        line.text.hash(&mut hasher);
        line.inline_spans.hash(&mut hasher);
    }
    hasher.finish()
}

pub(crate) fn inline_markdown_pill_motion_cache_key(
    visuals: &HashMap<u64, InlineMarkdownPillVisual>,
    exiting: &[(InlineMarkdownPillRun, InlineMarkdownPillVisual)],
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    for (key, visual) in sorted_u64_visual_entries(visuals) {
        key.hash(&mut hasher);
        hash_surface_motion_visual(*visual, &mut hasher);
    }
    for (run, visual) in exiting {
        run.hash(&mut hasher);
        hash_surface_motion_visual(*visual, &mut hasher);
    }
    hasher.finish()
}
