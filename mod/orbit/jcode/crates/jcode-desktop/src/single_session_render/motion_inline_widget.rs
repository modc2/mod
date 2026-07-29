//! Motion state machines for inline-widget chrome: selection highlight, preview panes, and list reflow (targets, visuals, frames, registries, cache keys, and row-run extraction).

use super::*;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct InlineWidgetSelectionTarget {
    pub(crate) kind: InlineWidgetKind,
    pub(crate) line: usize,
    pub(crate) line_span: usize,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct InlineWidgetSelectionVisual {
    pub(crate) opacity: f32,
    pub(crate) y_offset_lines: f32,
    pub(crate) line_span: f32,
}

impl InlineWidgetSelectionVisual {
    pub(crate) fn settled(target: InlineWidgetSelectionTarget) -> Self {
        Self {
            opacity: 1.0,
            y_offset_lines: 0.0,
            line_span: target.line_span as f32,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineWidgetSelectionTransition {
    pub(crate) from_line: usize,
    pub(crate) from_line_span: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct InlineWidgetSelectionMotionFrame {
    pub(crate) target: Option<InlineWidgetSelectionTarget>,
    pub(crate) visual: Option<InlineWidgetSelectionVisual>,
    pub(crate) active: bool,
}

impl InlineWidgetSelectionMotionFrame {
    pub(crate) fn visual_for_target(
        &self,
        target: InlineWidgetSelectionTarget,
    ) -> Option<InlineWidgetSelectionVisual> {
        (self.target == Some(target)).then_some(self.visual?)
    }

    pub(crate) fn is_active(&self) -> bool {
        self.active
    }
}

#[derive(Default)]
pub(crate) struct InlineWidgetSelectionMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) current: Option<InlineWidgetSelectionTarget>,
    pub(crate) transition: Option<InlineWidgetSelectionTransition>,
}

impl InlineWidgetSelectionMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        app: &SingleSessionApp,
        now: Instant,
    ) -> InlineWidgetSelectionMotionFrame {
        let kind = app.render_inline_widget_kind();
        let lines = app.render_inline_widget_styled_lines();
        let visible_line_count = kind
            .map(|kind| lines.len().min(kind.visible_line_limit()))
            .unwrap_or(0);
        let target = inline_widget_selection_target(kind, &lines, visible_line_count);
        self.frame_for_target(target, now)
    }

    pub(crate) fn frame_for_target(
        &mut self,
        target: Option<InlineWidgetSelectionTarget>,
        now: Instant,
    ) -> InlineWidgetSelectionMotionFrame {
        let Some(target) = target else {
            self.clear();
            return InlineWidgetSelectionMotionFrame::default();
        };

        if !self.initialized {
            self.initialized = true;
            self.current = Some(target);
            self.transition = None;
        } else if self.current != Some(target) {
            self.transition = self.current.and_then(|current| {
                (current.kind == target.kind && !crate::animation::desktop_reduced_motion_enabled())
                    .then_some(InlineWidgetSelectionTransition {
                        from_line: current.line,
                        from_line_span: current.line_span,
                        started_at: now,
                    })
            });
            self.current = Some(target);
        }

        let (visual, active) =
            inline_widget_selection_visual_from_transition(&mut self.transition, target, now);
        InlineWidgetSelectionMotionFrame {
            target: Some(target),
            visual: Some(visual),
            active,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.current = None;
        self.transition = None;
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct InlineWidgetPreviewPaneTarget {
    pub(crate) kind: InlineWidgetKind,
    pub(crate) focus_pane: usize,
    pub(crate) preview_key: u64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct InlineWidgetPreviewPaneVisual {
    pub(crate) focus_pane_position: f32,
    pub(crate) preview_opacity: f32,
    pub(crate) preview_y_offset_pixels: f32,
}

impl InlineWidgetPreviewPaneVisual {
    pub(crate) fn settled(target: InlineWidgetPreviewPaneTarget) -> Self {
        Self {
            focus_pane_position: target.focus_pane as f32,
            preview_opacity: 1.0,
            preview_y_offset_pixels: 0.0,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineWidgetPreviewPaneFocusTransition {
    pub(crate) from_pane: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct InlineWidgetPreviewPaneMotionFrame {
    pub(crate) visual: Option<InlineWidgetPreviewPaneVisual>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl InlineWidgetPreviewPaneMotionFrame {
    pub(crate) fn visual(&self) -> Option<InlineWidgetPreviewPaneVisual> {
        self.visual
    }

    pub(crate) fn is_active(&self) -> bool {
        self.active
    }

    pub(crate) fn cache_key(&self) -> u64 {
        self.cache_key
    }
}

#[derive(Default)]
pub(crate) struct InlineWidgetPreviewPaneMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) current: Option<InlineWidgetPreviewPaneTarget>,
    pub(crate) focus_transition: Option<InlineWidgetPreviewPaneFocusTransition>,
    pub(crate) content_started_at: Option<Instant>,
}

impl InlineWidgetPreviewPaneMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        app: &SingleSessionApp,
        now: Instant,
    ) -> InlineWidgetPreviewPaneMotionFrame {
        let kind = app.render_inline_widget_kind();
        let lines = app.render_inline_widget_styled_lines();
        let visible_line_count = app.render_inline_widget_visible_line_count();
        let target = inline_widget_preview_pane_target(kind, &lines, visible_line_count);
        self.frame_for_target(target, now)
    }

    pub(crate) fn frame_for_target(
        &mut self,
        target: Option<InlineWidgetPreviewPaneTarget>,
        now: Instant,
    ) -> InlineWidgetPreviewPaneMotionFrame {
        let Some(target) = target else {
            self.clear();
            return InlineWidgetPreviewPaneMotionFrame::default();
        };

        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        if !self.initialized {
            self.initialized = true;
            self.current = Some(target);
            self.focus_transition = None;
            self.content_started_at = None;
        } else if self.current != Some(target) {
            if reduced_motion {
                self.focus_transition = None;
                self.content_started_at = None;
            } else if let Some(current) = self.current {
                if current.focus_pane != target.focus_pane {
                    self.focus_transition = Some(InlineWidgetPreviewPaneFocusTransition {
                        from_pane: current.focus_pane,
                        started_at: now,
                    });
                }
                if current.preview_key != target.preview_key {
                    self.content_started_at = Some(now);
                }
            }
            self.current = Some(target);
        }

        if reduced_motion {
            self.focus_transition = None;
            self.content_started_at = None;
        }

        let (visual, active) = inline_widget_preview_pane_visual_from_state(
            target,
            &mut self.focus_transition,
            &mut self.content_started_at,
            now,
        );
        InlineWidgetPreviewPaneMotionFrame {
            visual: Some(visual),
            active,
            cache_key: inline_widget_preview_pane_cache_key(Some(visual), active),
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.current = None;
        self.focus_transition = None;
        self.content_started_at = None;
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct InlineWidgetListRowRun {
    pub(crate) kind: InlineWidgetKind,
    pub(crate) key: u64,
    pub(crate) line: usize,
    pub(crate) line_span: usize,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct InlineWidgetListReflowVisual {
    pub(crate) opacity: f32,
    pub(crate) y_offset_lines: f32,
    pub(crate) line_span: f32,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineWidgetListReflowShift {
    pub(crate) from_line: usize,
    pub(crate) from_line_span: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineWidgetListReflowState {
    pub(crate) run: InlineWidgetListRowRun,
    pub(crate) entered_at: Option<Instant>,
    pub(crate) exiting_at: Option<Instant>,
    pub(crate) shift: Option<InlineWidgetListReflowShift>,
    pub(crate) last_seen_generation: u64,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct InlineWidgetListReflowMotionFrame {
    pub(crate) visuals: HashMap<u64, InlineWidgetListReflowVisual>,
    pub(crate) exiting: Vec<(InlineWidgetListRowRun, InlineWidgetListReflowVisual)>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl InlineWidgetListReflowMotionFrame {
    pub(crate) fn visual_for_key(&self, key: u64) -> Option<InlineWidgetListReflowVisual> {
        self.visuals.get(&key).copied()
    }

    pub(crate) fn exiting(&self) -> &[(InlineWidgetListRowRun, InlineWidgetListReflowVisual)] {
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
pub(crate) struct InlineWidgetListReflowMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) kind: Option<InlineWidgetKind>,
    pub(crate) generation: u64,
    pub(crate) states: HashMap<u64, InlineWidgetListReflowState>,
}

impl InlineWidgetListReflowMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        app: &SingleSessionApp,
        now: Instant,
    ) -> InlineWidgetListReflowMotionFrame {
        let kind = app.render_inline_widget_kind();
        let lines = app.render_inline_widget_styled_lines();
        let visible_line_count = app.render_inline_widget_visible_line_count();
        self.frame_for_rows(kind, &lines, visible_line_count, now)
    }

    pub(crate) fn frame_for_rows(
        &mut self,
        kind: Option<InlineWidgetKind>,
        lines: &[SingleSessionStyledLine],
        visible_line_count: usize,
        now: Instant,
    ) -> InlineWidgetListReflowMotionFrame {
        let Some(kind) = kind else {
            self.clear();
            return InlineWidgetListReflowMotionFrame::default();
        };

        if self.kind != Some(kind) {
            self.clear();
            self.kind = Some(kind);
        }

        self.generation = self.generation.wrapping_add(1).max(1);
        let generation = self.generation;
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        let animate_new_rows = self.initialized && !reduced_motion;
        self.initialized = true;

        let runs = inline_widget_list_row_runs(Some(kind), lines, visible_line_count);
        let mut visuals = HashMap::new();
        let mut active = false;
        for run in runs {
            let state = self
                .states
                .entry(run.key)
                .or_insert_with(|| InlineWidgetListReflowState {
                    run,
                    entered_at: animate_new_rows.then_some(now),
                    exiting_at: None,
                    shift: None,
                    last_seen_generation: generation,
                });
            state.last_seen_generation = generation;
            state.exiting_at = None;

            if reduced_motion {
                state.entered_at = None;
                state.shift = None;
            }

            if state.run.line != run.line || state.run.line_span != run.line_span {
                if reduced_motion {
                    state.shift = None;
                } else {
                    state.shift = Some(InlineWidgetListReflowShift {
                        from_line: state.run.line,
                        from_line_span: state.run.line_span,
                        started_at: now,
                    });
                }
            }
            state.run = run;

            let (visual, visual_active) = inline_widget_list_reflow_visual_from_state(state, now);
            active |= visual_active;
            if visual.opacity > 0.001 {
                visuals.insert(run.key, visual);
            }
        }

        let mut exiting = Vec::new();
        if !reduced_motion {
            for state in self.states.values_mut() {
                if state.last_seen_generation == generation {
                    continue;
                }
                let exiting_at = *state.exiting_at.get_or_insert(now);
                let (progress, running) = timed_animation_progress(
                    exiting_at,
                    now,
                    INLINE_WIDGET_LIST_REFLOW_EXIT_DURATION,
                );
                if !running {
                    continue;
                }
                state.last_seen_generation = generation;
                active = true;
                exiting.push((
                    state.run,
                    exiting_inline_widget_list_reflow_visual(progress),
                ));
            }
        }

        self.states
            .retain(|_, state| state.last_seen_generation == generation);

        InlineWidgetListReflowMotionFrame {
            cache_key: inline_widget_list_reflow_cache_key(&visuals, &exiting, active),
            visuals,
            exiting,
            active,
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.kind = None;
        self.generation = 0;
        self.states.clear();
    }
}

pub(crate) fn inline_widget_selection_target(
    kind: Option<InlineWidgetKind>,
    lines: &[SingleSessionStyledLine],
    visible_line_count: usize,
) -> Option<InlineWidgetSelectionTarget> {
    let kind = kind?;
    let visible_len = visible_line_count.min(lines.len());
    let visible_lines = &lines[..visible_len];
    let selected_line = visible_lines
        .iter()
        .position(|line| line.style == SingleSessionLineStyle::OverlaySelection)?;
    let line_span = match kind {
        InlineWidgetKind::ModelPicker => {
            // Model rows use a selected primary line followed by a metadata
            // detail line. Keep the highlight as one two-line target so the
            // keyboard selection feels like a card moving through the list.
            if selected_line + 1 < visible_len {
                2
            } else {
                1
            }
        }
        InlineWidgetKind::SessionSwitcher => visible_lines[selected_line..]
            .iter()
            .take_while(|line| line.style == SingleSessionLineStyle::OverlaySelection)
            .count()
            .max(1),
        InlineWidgetKind::SlashSuggestions => 1,
        InlineWidgetKind::HotkeyHelp | InlineWidgetKind::SessionInfo => return None,
    };

    Some(InlineWidgetSelectionTarget {
        kind,
        line: selected_line,
        line_span: line_span
            .min(visible_len.saturating_sub(selected_line))
            .max(1),
    })
}

pub(crate) fn inline_widget_preview_pane_target(
    kind: Option<InlineWidgetKind>,
    lines: &[SingleSessionStyledLine],
    visible_line_count: usize,
) -> Option<InlineWidgetPreviewPaneTarget> {
    let kind = kind?;
    if kind != InlineWidgetKind::SessionSwitcher {
        return None;
    }
    let visible_len = visible_line_count.min(lines.len());
    let visible_lines = &lines[..visible_len];
    let header_line = visible_lines
        .iter()
        .position(|line| line.text.contains("sessions") && line.text.contains("preview"))?;
    let focus_pane = usize::from(visible_lines[header_line].text.contains("preview ›"));
    let mut hasher = DefaultHasher::new();
    kind.hash(&mut hasher);
    for line in visible_lines.iter().skip(header_line + 1) {
        if line.text.contains("preview lines ") {
            break;
        }
        line.text.hash(&mut hasher);
        line.style.hash(&mut hasher);
    }
    Some(InlineWidgetPreviewPaneTarget {
        kind,
        focus_pane,
        preview_key: hasher.finish(),
    })
}

pub(crate) fn inline_widget_preview_pane_visual_from_state(
    target: InlineWidgetPreviewPaneTarget,
    focus_transition: &mut Option<InlineWidgetPreviewPaneFocusTransition>,
    content_started_at: &mut Option<Instant>,
    now: Instant,
) -> (InlineWidgetPreviewPaneVisual, bool) {
    let settled = InlineWidgetPreviewPaneVisual::settled(target);
    let mut active = false;
    let mut focus_pane_position = settled.focus_pane_position;
    if let Some(transition) = *focus_transition {
        let (progress, running) = timed_animation_progress(
            transition.started_at,
            now,
            INLINE_WIDGET_PREVIEW_PANE_FOCUS_DURATION,
        );
        let eased = ease_out_cubic_local(progress);
        focus_pane_position =
            lerp_f32(transition.from_pane as f32, target.focus_pane as f32, eased);
        active |= running;
        if !running {
            *focus_transition = None;
            focus_pane_position = target.focus_pane as f32;
        }
    }

    let mut preview_opacity = settled.preview_opacity;
    let mut preview_y_offset_pixels = settled.preview_y_offset_pixels;
    if let Some(started_at) = *content_started_at {
        let (progress, running) =
            timed_animation_progress(started_at, now, INLINE_WIDGET_PREVIEW_PANE_CONTENT_DURATION);
        let eased = ease_out_cubic_local(progress);
        preview_opacity = 0.35 + 0.65 * eased;
        preview_y_offset_pixels = 5.0 * (1.0 - eased);
        active |= running;
        if !running {
            *content_started_at = None;
            preview_opacity = 1.0;
            preview_y_offset_pixels = 0.0;
        }
    }

    (
        InlineWidgetPreviewPaneVisual {
            focus_pane_position,
            preview_opacity,
            preview_y_offset_pixels,
        },
        active,
    )
}

pub(crate) fn inline_widget_preview_pane_cache_key(
    visual: Option<InlineWidgetPreviewPaneVisual>,
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    visual.is_some().hash(&mut hasher);
    if let Some(visual) = visual {
        hash_f32(visual.focus_pane_position, &mut hasher);
        hash_f32(visual.preview_opacity, &mut hasher);
        hash_f32(visual.preview_y_offset_pixels, &mut hasher);
    }
    hasher.finish()
}

pub(crate) fn inline_widget_list_row_runs(
    kind: Option<InlineWidgetKind>,
    lines: &[SingleSessionStyledLine],
    visible_line_count: usize,
) -> Vec<InlineWidgetListRowRun> {
    let Some(kind) = kind else {
        return Vec::new();
    };
    let visible_len = visible_line_count.min(lines.len());
    let mut runs = Vec::new();
    let mut occurrences = HashMap::new();

    match kind {
        InlineWidgetKind::SlashSuggestions => {
            for line in 1..visible_len {
                if matches!(
                    lines[line].style,
                    SingleSessionLineStyle::OverlaySelection | SingleSessionLineStyle::Overlay
                ) {
                    push_inline_widget_list_row_run(
                        &mut runs,
                        &mut occurrences,
                        kind,
                        lines,
                        line,
                        1,
                    );
                }
            }
        }
        InlineWidgetKind::ModelPicker => {
            let mut line = 2;
            while line < visible_len {
                let primary_style = lines[line].style;
                let looks_like_primary = matches!(
                    primary_style,
                    SingleSessionLineStyle::OverlaySelection | SingleSessionLineStyle::Overlay
                ) && line + 1 < visible_len
                    && lines[line + 1].style == SingleSessionLineStyle::Meta
                    && lines[line + 1].text.trim_start().contains('·');
                if looks_like_primary {
                    push_inline_widget_list_row_run(
                        &mut runs,
                        &mut occurrences,
                        kind,
                        lines,
                        line,
                        2,
                    );
                    line += 2;
                } else {
                    line += 1;
                }
            }
        }
        InlineWidgetKind::SessionSwitcher => {
            let mut line = 0;
            while line < visible_len {
                if lines[line].text.starts_with("Preview") {
                    break;
                }
                let looks_like_session_card = matches!(
                    lines[line].style,
                    SingleSessionLineStyle::OverlaySelection | SingleSessionLineStyle::Overlay
                ) && lines[line].text.contains(" session ·")
                    && line + 1 < visible_len
                    && lines[line + 1].text.trim_start().starts_with("Status ");
                if looks_like_session_card {
                    let mut span = 1;
                    while line + span < visible_len
                        && span < 4
                        && !lines[line + span].text.starts_with("Preview")
                        && lines[line + span].style != SingleSessionLineStyle::Blank
                        && lines[line + span].style != SingleSessionLineStyle::OverlayTitle
                    {
                        span += 1;
                    }
                    push_inline_widget_list_row_run(
                        &mut runs,
                        &mut occurrences,
                        kind,
                        lines,
                        line,
                        span,
                    );
                    line += span;
                } else {
                    line += 1;
                }
            }
        }
        InlineWidgetKind::HotkeyHelp | InlineWidgetKind::SessionInfo => {}
    }

    runs
}

pub(crate) fn push_inline_widget_list_row_run(
    runs: &mut Vec<InlineWidgetListRowRun>,
    occurrences: &mut HashMap<u64, usize>,
    kind: InlineWidgetKind,
    lines: &[SingleSessionStyledLine],
    line: usize,
    line_span: usize,
) {
    let base_key = inline_widget_list_row_base_key(kind, lines, line, line_span);
    let key = motion_occurrence_key(base_key, occurrences);
    runs.push(InlineWidgetListRowRun {
        kind,
        key,
        line,
        line_span,
    });
}

pub(crate) fn inline_widget_list_row_base_key(
    kind: InlineWidgetKind,
    lines: &[SingleSessionStyledLine],
    line: usize,
    line_span: usize,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    kind.hash(&mut hasher);
    line_span.hash(&mut hasher);
    let end = line.saturating_add(line_span).min(lines.len());
    for styled_line in &lines[line.min(lines.len())..end] {
        styled_line.style.hash(&mut hasher);
        normalized_inline_widget_list_row_text(&styled_line.text).hash(&mut hasher);
    }
    hasher.finish()
}

pub(crate) fn normalized_inline_widget_list_row_text(text: &str) -> String {
    text.chars()
        .map(|ch| match ch {
            '›' | '▶' => ' ',
            _ => ch,
        })
        .collect()
}

pub(crate) fn inline_widget_selection_visual_from_transition(
    transition: &mut Option<InlineWidgetSelectionTransition>,
    target: InlineWidgetSelectionTarget,
    now: Instant,
) -> (InlineWidgetSelectionVisual, bool) {
    let Some(active_transition) = *transition else {
        return (InlineWidgetSelectionVisual::settled(target), false);
    };

    let (progress, running) = timed_animation_progress(
        active_transition.started_at,
        now,
        INLINE_WIDGET_SELECTION_TRANSITION_DURATION,
    );
    let eased = ease_out_cubic_local(progress);
    let from_line = active_transition.from_line as f32;
    let to_line = target.line as f32;
    let from_span = active_transition.from_line_span as f32;
    let to_span = target.line_span as f32;
    let visual = InlineWidgetSelectionVisual {
        opacity: 1.0,
        y_offset_lines: (from_line - to_line) * (1.0 - eased),
        line_span: from_span + (to_span - from_span) * eased,
    };
    if !running {
        *transition = None;
    }
    (visual, running)
}

pub(crate) fn inline_widget_list_reflow_visual_from_state(
    state: &mut InlineWidgetListReflowState,
    now: Instant,
) -> (InlineWidgetListReflowVisual, bool) {
    let mut visual = InlineWidgetListReflowVisual {
        opacity: 0.0,
        y_offset_lines: 0.0,
        line_span: state.run.line_span as f32,
    };
    let mut active = false;

    if let Some(entered_at) = state.entered_at {
        let (progress, running) =
            timed_animation_progress(entered_at, now, INLINE_WIDGET_LIST_REFLOW_ENTRY_DURATION);
        let eased = ease_out_cubic_local(progress);
        visual.opacity = visual.opacity.max(1.0 - eased);
        visual.y_offset_lines += 0.45 * (1.0 - eased);
        active |= running;
        if !running {
            state.entered_at = None;
        }
    }

    if let Some(shift) = state.shift {
        let (progress, running) = timed_animation_progress(
            shift.started_at,
            now,
            INLINE_WIDGET_LIST_REFLOW_SHIFT_DURATION,
        );
        let eased = ease_out_cubic_local(progress);
        let line_delta = shift.from_line as f32 - state.run.line as f32;
        let span_delta = shift.from_line_span as f32 - state.run.line_span as f32;
        visual.opacity = visual.opacity.max(1.0 - eased * 0.15);
        visual.y_offset_lines += line_delta * (1.0 - eased);
        visual.line_span = state.run.line_span as f32 + span_delta * (1.0 - eased);
        active |= running;
        if !running {
            state.shift = None;
        }
    }

    (visual, active)
}

pub(crate) fn exiting_inline_widget_list_reflow_visual(
    progress: f32,
) -> InlineWidgetListReflowVisual {
    let eased = ease_out_cubic_local(progress);
    InlineWidgetListReflowVisual {
        opacity: 1.0 - eased,
        y_offset_lines: -0.35 * eased,
        line_span: 1.0,
    }
}

pub(crate) fn inline_widget_list_reflow_cache_key(
    visuals: &HashMap<u64, InlineWidgetListReflowVisual>,
    exiting: &[(InlineWidgetListRowRun, InlineWidgetListReflowVisual)],
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    for (key, visual) in sorted_u64_visual_entries(visuals) {
        key.hash(&mut hasher);
        hash_f32(visual.opacity, &mut hasher);
        hash_f32(visual.y_offset_lines, &mut hasher);
        hash_f32(visual.line_span, &mut hasher);
    }
    for (run, visual) in exiting {
        run.kind.hash(&mut hasher);
        run.key.hash(&mut hasher);
        run.line.hash(&mut hasher);
        run.line_span.hash(&mut hasher);
        hash_f32(visual.opacity, &mut hasher);
        hash_f32(visual.y_offset_lines, &mut hasher);
        hash_f32(visual.line_span, &mut hasher);
    }
    hasher.finish()
}
