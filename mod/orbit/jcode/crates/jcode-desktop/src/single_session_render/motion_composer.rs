//! Motion state machines for composer chrome, attachment chips, and the stdin overlay: targets, visuals, frames, registries, and cache keys.

use super::*;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) struct ComposerMotionTarget {
    pub(crate) line_count: usize,
    pub(crate) empty: bool,
    pub(crate) blocked: bool,
    pub(crate) processing: bool,
    pub(crate) ready_to_submit: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct ComposerMotionVisual {
    pub(crate) height_lines: f32,
    pub(crate) placeholder_opacity: f32,
    pub(crate) focus_opacity: f32,
    pub(crate) blocked_progress: f32,
    pub(crate) submit_opacity: f32,
    pub(crate) submit_scale: f32,
    pub(crate) processing_progress: f32,
}

impl ComposerMotionVisual {
    pub(crate) fn settled(target: ComposerMotionTarget) -> Self {
        Self {
            height_lines: target.line_count.max(1) as f32,
            placeholder_opacity: if target.empty && !target.processing {
                1.0
            } else {
                0.0
            },
            focus_opacity: if target.blocked { 0.28 } else { 1.0 },
            blocked_progress: if target.blocked { 1.0 } else { 0.0 },
            submit_opacity: if target.ready_to_submit || target.processing {
                1.0
            } else {
                0.0
            },
            submit_scale: if target.ready_to_submit || target.processing {
                1.0
            } else {
                0.82
            },
            processing_progress: if target.processing { 1.0 } else { 0.0 },
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ComposerMotionTransition {
    pub(crate) from: ComposerMotionVisual,
    pub(crate) to: ComposerMotionVisual,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ComposerMotionFrame {
    pub(crate) visual: ComposerMotionVisual,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl ComposerMotionFrame {
    pub(crate) fn visual(&self) -> ComposerMotionVisual {
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
pub(crate) struct ComposerMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) target: Option<ComposerMotionTarget>,
    pub(crate) visual: Option<ComposerMotionVisual>,
    pub(crate) transition: Option<ComposerMotionTransition>,
}

impl ComposerMotionRegistry {
    pub(crate) fn frame(&mut self, app: &SingleSessionApp, now: Instant) -> ComposerMotionFrame {
        self.frame_for_target(composer_motion_target(app), now)
    }

    pub(crate) fn frame_for_target(
        &mut self,
        target: ComposerMotionTarget,
        now: Instant,
    ) -> ComposerMotionFrame {
        let target_visual = ComposerMotionVisual::settled(target);
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        if reduced_motion || !self.initialized {
            self.initialized = true;
            self.target = Some(target);
            self.visual = Some(target_visual);
            self.transition = None;
            return ComposerMotionFrame {
                visual: target_visual,
                active: false,
                cache_key: composer_motion_cache_key(target, target_visual, false),
            };
        }

        if self.target != Some(target) {
            let from = self.current_visual(now);
            self.transition = Some(ComposerMotionTransition {
                from,
                to: target_visual,
                started_at: now,
            });
            self.target = Some(target);
        }

        let mut active = false;
        let visual = if let Some(transition) = self.transition {
            let (progress, running) =
                timed_animation_progress(transition.started_at, now, COMPOSER_MOTION_DURATION);
            let eased = ease_out_cubic_local(progress);
            let visual = composer_motion_visual_lerp(transition.from, transition.to, eased);
            active = running;
            if !running {
                self.transition = None;
            }
            visual
        } else {
            target_visual
        };
        self.visual = Some(visual);

        ComposerMotionFrame {
            visual,
            active,
            cache_key: composer_motion_cache_key(target, visual, active),
        }
    }

    pub(crate) fn current_visual(&mut self, now: Instant) -> ComposerMotionVisual {
        if let Some(transition) = self.transition {
            let (progress, running) =
                timed_animation_progress(transition.started_at, now, COMPOSER_MOTION_DURATION);
            if !running {
                self.transition = None;
                transition.to
            } else {
                composer_motion_visual_lerp(
                    transition.from,
                    transition.to,
                    ease_out_cubic_local(progress),
                )
            }
        } else {
            self.visual
                .or_else(|| self.target.map(ComposerMotionVisual::settled))
                .unwrap_or_else(|| ComposerMotionVisual::settled(ComposerMotionTarget::default()))
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.target = None;
        self.visual = None;
        self.transition = None;
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct AttachmentChipRun {
    pub(crate) key: u64,
    pub(crate) index: usize,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct AttachmentChipVisual {
    pub(crate) opacity: f32,
    pub(crate) x_offset_pixels: f32,
    pub(crate) y_offset_pixels: f32,
    pub(crate) scale: f32,
}

impl AttachmentChipVisual {
    pub(crate) fn settled() -> Self {
        Self {
            opacity: 1.0,
            x_offset_pixels: 0.0,
            y_offset_pixels: 0.0,
            scale: 1.0,
        }
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct AttachmentChipShift {
    pub(crate) from_index: usize,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct AttachmentChipState {
    pub(crate) run: AttachmentChipRun,
    pub(crate) entered_at: Option<Instant>,
    pub(crate) exiting_at: Option<Instant>,
    pub(crate) shift: Option<AttachmentChipShift>,
    pub(crate) last_seen_generation: u64,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct AttachmentChipMotionFrame {
    pub(crate) visuals: HashMap<u64, AttachmentChipVisual>,
    pub(crate) exiting: Vec<(AttachmentChipRun, AttachmentChipVisual)>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl AttachmentChipMotionFrame {
    pub(crate) fn visual_for_key(&self, key: u64) -> Option<AttachmentChipVisual> {
        self.visuals.get(&key).copied()
    }

    pub(crate) fn exiting(&self) -> &[(AttachmentChipRun, AttachmentChipVisual)] {
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
pub(crate) struct AttachmentChipMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) generation: u64,
    pub(crate) states: HashMap<u64, AttachmentChipState>,
}

impl AttachmentChipMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        app: &SingleSessionApp,
        now: Instant,
    ) -> AttachmentChipMotionFrame {
        self.frame_for_images(&app.pending_images, now)
    }

    pub(crate) fn frame_for_images(
        &mut self,
        images: &[(String, String)],
        now: Instant,
    ) -> AttachmentChipMotionFrame {
        self.generation = self.generation.wrapping_add(1).max(1);
        let generation = self.generation;
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        let animate_new_chips = self.initialized && !reduced_motion;
        self.initialized = true;

        let runs = attachment_chip_runs(images);
        let mut visuals = HashMap::new();
        let mut active = false;
        for run in runs {
            let state = self
                .states
                .entry(run.key)
                .or_insert_with(|| AttachmentChipState {
                    run,
                    entered_at: animate_new_chips.then_some(now),
                    exiting_at: None,
                    shift: None,
                    last_seen_generation: generation,
                });
            state.last_seen_generation = generation;
            state.exiting_at = None;

            if reduced_motion {
                state.entered_at = None;
                state.shift = None;
            } else if state.run.index != run.index {
                state.shift = Some(AttachmentChipShift {
                    from_index: state.run.index,
                    started_at: now,
                });
            }
            state.run = run;

            let (visual, visual_active) = attachment_chip_visual_from_state(state, now);
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
                let (progress, running) =
                    timed_animation_progress(exiting_at, now, ATTACHMENT_CHIP_EXIT_DURATION);
                if !running {
                    continue;
                }
                state.last_seen_generation = generation;
                active = true;
                exiting.push((state.run, exiting_attachment_chip_visual(progress)));
            }
        }

        self.states
            .retain(|_, state| state.last_seen_generation == generation);

        AttachmentChipMotionFrame {
            cache_key: attachment_chip_motion_cache_key(&visuals, &exiting, active),
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

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) struct StdinOverlayTarget {
    pub(crate) key: u64,
    pub(crate) line_count: usize,
    pub(crate) input_line_start: usize,
    pub(crate) input_line_count: usize,
    pub(crate) password: bool,
    pub(crate) has_input: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct StdinOverlayVisual {
    pub(crate) opacity: f32,
    pub(crate) y_offset_pixels: f32,
    pub(crate) scale: f32,
    pub(crate) height_lines: f32,
    pub(crate) input_glow: f32,
    pub(crate) submit_opacity: f32,
}

impl StdinOverlayVisual {
    pub(crate) fn settled(target: StdinOverlayTarget) -> Self {
        Self {
            opacity: 1.0,
            y_offset_pixels: 0.0,
            scale: 1.0,
            height_lines: target.line_count.max(1) as f32,
            input_glow: if target.has_input { 1.0 } else { 0.22 },
            submit_opacity: if target.has_input { 1.0 } else { 0.0 },
        }
    }

    pub(crate) fn entry(target: StdinOverlayTarget) -> Self {
        let mut visual = Self::settled(target);
        visual.opacity = 0.0;
        visual.y_offset_pixels = STDIN_OVERLAY_ENTRY_OFFSET_PIXELS;
        visual.scale = STDIN_OVERLAY_ENTRY_SCALE;
        visual.input_glow = 0.0;
        visual.submit_opacity = 0.0;
        visual
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct StdinOverlayTransition {
    pub(crate) from: StdinOverlayVisual,
    pub(crate) to: StdinOverlayVisual,
    pub(crate) started_at: Instant,
    pub(crate) duration: Duration,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct StdinOverlayExit {
    pub(crate) target: StdinOverlayTarget,
    pub(crate) from: StdinOverlayVisual,
    pub(crate) started_at: Instant,
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct StdinOverlayMotionFrame {
    pub(crate) current: Option<(StdinOverlayTarget, StdinOverlayVisual)>,
    pub(crate) exiting: Option<(StdinOverlayTarget, StdinOverlayVisual)>,
    pub(crate) active: bool,
    pub(crate) cache_key: u64,
}

impl StdinOverlayMotionFrame {
    pub(crate) fn is_active(&self) -> bool {
        self.active
    }

    pub(crate) fn cache_key(&self) -> u64 {
        self.cache_key
    }
}

#[derive(Default)]
pub(crate) struct StdinOverlayMotionRegistry {
    pub(crate) initialized: bool,
    pub(crate) target: Option<StdinOverlayTarget>,
    pub(crate) visual: Option<StdinOverlayVisual>,
    pub(crate) transition: Option<StdinOverlayTransition>,
    pub(crate) exit: Option<StdinOverlayExit>,
}

impl StdinOverlayMotionRegistry {
    pub(crate) fn frame(
        &mut self,
        app: &SingleSessionApp,
        rendered_body_lines: &[SingleSessionStyledLine],
        now: Instant,
    ) -> StdinOverlayMotionFrame {
        self.frame_for_target(stdin_overlay_target(app, rendered_body_lines), now)
    }

    pub(crate) fn frame_for_target(
        &mut self,
        target: Option<StdinOverlayTarget>,
        now: Instant,
    ) -> StdinOverlayMotionFrame {
        let reduced_motion = crate::animation::desktop_reduced_motion_enabled();
        if reduced_motion || !self.initialized {
            self.initialized = true;
            self.target = target;
            self.visual = target.map(StdinOverlayVisual::settled);
            self.transition = None;
            self.exit = None;
            return self.frame_from_state(false, now);
        }

        if self.target != target {
            let from = self
                .current_visual(now)
                .or_else(|| {
                    self.exit
                        .map(|exit| stdin_overlay_exit_visual(exit.from, 0.0))
                })
                .unwrap_or_else(|| {
                    target.map_or_else(
                        || StdinOverlayVisual::entry(StdinOverlayTarget::empty()),
                        StdinOverlayVisual::entry,
                    )
                });
            match (self.target, target) {
                (Some(previous), None) => {
                    self.exit = Some(StdinOverlayExit {
                        target: previous,
                        from,
                        started_at: now,
                    });
                    self.transition = None;
                    self.visual = None;
                    self.target = None;
                }
                (_, Some(next)) => {
                    let entering = self.target.is_none() && self.exit.is_none();
                    let entry_from = if entering {
                        StdinOverlayVisual::entry(next)
                    } else {
                        from
                    };
                    self.exit = None;
                    self.transition = Some(StdinOverlayTransition {
                        from: entry_from,
                        to: StdinOverlayVisual::settled(next),
                        started_at: now,
                        duration: if entering {
                            STDIN_OVERLAY_ENTRY_DURATION
                        } else {
                            STDIN_OVERLAY_RESIZE_DURATION
                        },
                    });
                    self.target = Some(next);
                }
                (None, None) => {}
            }
        }

        self.frame_from_state(false, now)
    }

    pub(crate) fn frame_from_state(
        &mut self,
        mut active: bool,
        now: Instant,
    ) -> StdinOverlayMotionFrame {
        let current = if let Some(target) = self.target {
            let visual = if let Some(transition) = self.transition {
                let (progress, running) =
                    timed_animation_progress(transition.started_at, now, transition.duration);
                active |= running;
                if !running {
                    self.transition = None;
                    transition.to
                } else {
                    stdin_overlay_visual_lerp(
                        transition.from,
                        transition.to,
                        ease_out_cubic_local(progress),
                    )
                }
            } else {
                self.visual
                    .unwrap_or_else(|| StdinOverlayVisual::settled(target))
            };
            self.visual = Some(visual);
            Some((target, visual))
        } else {
            None
        };

        let exiting = if let Some(exit) = self.exit {
            let (progress, running) =
                timed_animation_progress(exit.started_at, now, STDIN_OVERLAY_EXIT_DURATION);
            if running {
                active = true;
                Some((exit.target, stdin_overlay_exit_visual(exit.from, progress)))
            } else {
                self.exit = None;
                None
            }
        } else {
            None
        };

        StdinOverlayMotionFrame {
            current,
            exiting,
            active,
            cache_key: stdin_overlay_motion_cache_key(current, exiting, active),
        }
    }

    pub(crate) fn current_visual(&mut self, now: Instant) -> Option<StdinOverlayVisual> {
        if let Some(transition) = self.transition {
            let (progress, running) =
                timed_animation_progress(transition.started_at, now, transition.duration);
            if !running {
                self.transition = None;
                Some(transition.to)
            } else {
                Some(stdin_overlay_visual_lerp(
                    transition.from,
                    transition.to,
                    ease_out_cubic_local(progress),
                ))
            }
        } else {
            self.visual
        }
    }

    pub(crate) fn clear(&mut self) {
        self.initialized = false;
        self.target = None;
        self.visual = None;
        self.transition = None;
        self.exit = None;
    }
}

impl StdinOverlayTarget {
    pub(crate) fn empty() -> Self {
        Self {
            key: 0,
            line_count: 1,
            input_line_start: 0,
            input_line_count: 1,
            password: false,
            has_input: false,
        }
    }
}

impl Default for ComposerMotionTarget {
    fn default() -> Self {
        Self {
            line_count: 1,
            empty: true,
            blocked: false,
            processing: false,
            ready_to_submit: false,
        }
    }
}

pub(crate) fn composer_motion_target(app: &SingleSessionApp) -> ComposerMotionTarget {
    let line_count = app.composer_text().split('\n').count().max(1);
    let ready_to_submit = !app.draft.trim().is_empty();
    ComposerMotionTarget {
        line_count,
        empty: app.draft.is_empty(),
        blocked: !app.should_draw_composer_caret(),
        processing: app.is_processing,
        ready_to_submit,
    }
}

pub(crate) fn composer_motion_visual_lerp(
    from: ComposerMotionVisual,
    to: ComposerMotionVisual,
    progress: f32,
) -> ComposerMotionVisual {
    ComposerMotionVisual {
        height_lines: lerp_f32(from.height_lines, to.height_lines, progress),
        placeholder_opacity: lerp_f32(from.placeholder_opacity, to.placeholder_opacity, progress),
        focus_opacity: lerp_f32(from.focus_opacity, to.focus_opacity, progress),
        blocked_progress: lerp_f32(from.blocked_progress, to.blocked_progress, progress),
        submit_opacity: lerp_f32(from.submit_opacity, to.submit_opacity, progress),
        submit_scale: lerp_f32(from.submit_scale, to.submit_scale, progress),
        processing_progress: lerp_f32(from.processing_progress, to.processing_progress, progress),
    }
}

pub(crate) fn composer_motion_cache_key(
    target: ComposerMotionTarget,
    visual: ComposerMotionVisual,
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    target.hash(&mut hasher);
    active.hash(&mut hasher);
    hash_f32(visual.height_lines, &mut hasher);
    hash_f32(visual.placeholder_opacity, &mut hasher);
    hash_f32(visual.focus_opacity, &mut hasher);
    hash_f32(visual.blocked_progress, &mut hasher);
    hash_f32(visual.submit_opacity, &mut hasher);
    hash_f32(visual.submit_scale, &mut hasher);
    hash_f32(visual.processing_progress, &mut hasher);
    hasher.finish()
}

pub(crate) fn attachment_chip_runs(images: &[(String, String)]) -> Vec<AttachmentChipRun> {
    let mut runs = Vec::new();
    let mut occurrences = HashMap::new();
    for (index, (media_type, base64_data)) in images
        .iter()
        .take(ATTACHMENT_CHIP_VISIBLE_LIMIT)
        .enumerate()
    {
        let base_key = attachment_chip_base_key(media_type, base64_data);
        let key = motion_occurrence_key(base_key, &mut occurrences);
        runs.push(AttachmentChipRun { key, index });
    }
    runs
}

pub(crate) fn attachment_chip_base_key(media_type: &str, base64_data: &str) -> u64 {
    let mut hasher = DefaultHasher::new();
    media_type.hash(&mut hasher);
    base64_data.len().hash(&mut hasher);
    let bytes = base64_data.as_bytes();
    let sample = bytes.len().min(48);
    bytes[..sample].hash(&mut hasher);
    if bytes.len() > sample {
        bytes[bytes.len() - sample..].hash(&mut hasher);
    }
    hasher.finish()
}

pub(crate) fn attachment_chip_visual_from_state(
    state: &mut AttachmentChipState,
    now: Instant,
) -> (AttachmentChipVisual, bool) {
    let mut visual = AttachmentChipVisual::settled();
    let mut active = false;

    if let Some(entered_at) = state.entered_at {
        let (progress, running) =
            timed_animation_progress(entered_at, now, ATTACHMENT_CHIP_ENTRY_DURATION);
        let eased = ease_out_cubic_local(progress);
        visual.opacity = eased;
        visual.y_offset_pixels += 5.0 * (1.0 - eased);
        visual.scale *= 0.90 + 0.10 * eased;
        active |= running;
        if !running {
            state.entered_at = None;
        }
    }

    if let Some(shift) = state.shift {
        let (progress, running) =
            timed_animation_progress(shift.started_at, now, ATTACHMENT_CHIP_SHIFT_DURATION);
        let eased = ease_out_cubic_local(progress);
        let index_delta = shift.from_index as f32 - state.run.index as f32;
        visual.x_offset_pixels +=
            index_delta * (ATTACHMENT_CHIP_WIDTH + ATTACHMENT_CHIP_GAP) * (1.0 - eased);
        active |= running;
        if !running {
            state.shift = None;
        }
    }

    (visual, active)
}

pub(crate) fn exiting_attachment_chip_visual(progress: f32) -> AttachmentChipVisual {
    let eased = ease_out_cubic_local(progress);
    AttachmentChipVisual {
        opacity: 1.0 - eased,
        x_offset_pixels: 0.0,
        y_offset_pixels: -5.0 * eased,
        scale: 1.0 - 0.08 * eased,
    }
}

pub(crate) fn attachment_chip_motion_cache_key(
    visuals: &HashMap<u64, AttachmentChipVisual>,
    exiting: &[(AttachmentChipRun, AttachmentChipVisual)],
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    for (key, visual) in sorted_u64_visual_entries(visuals) {
        key.hash(&mut hasher);
        hash_f32(visual.opacity, &mut hasher);
        hash_f32(visual.x_offset_pixels, &mut hasher);
        hash_f32(visual.y_offset_pixels, &mut hasher);
        hash_f32(visual.scale, &mut hasher);
    }
    for (run, visual) in exiting {
        run.key.hash(&mut hasher);
        run.index.hash(&mut hasher);
        hash_f32(visual.opacity, &mut hasher);
        hash_f32(visual.x_offset_pixels, &mut hasher);
        hash_f32(visual.y_offset_pixels, &mut hasher);
        hash_f32(visual.scale, &mut hasher);
    }
    hasher.finish()
}

pub(crate) fn stdin_overlay_target(
    app: &SingleSessionApp,
    rendered_body_lines: &[SingleSessionStyledLine],
) -> Option<StdinOverlayTarget> {
    let state = app.stdin_response.as_ref()?;
    let mut hasher = DefaultHasher::new();
    state.request_id.hash(&mut hasher);
    state.prompt.hash(&mut hasher);
    state.tool_call_id.hash(&mut hasher);
    state.is_password.hash(&mut hasher);
    let key = hasher.finish();
    let input_line_start = rendered_body_lines
        .iter()
        .position(|line| line.style == SingleSessionLineStyle::OverlaySelection)
        .unwrap_or_else(|| rendered_body_lines.len().saturating_sub(1));
    let input_line_count = rendered_body_lines
        .get(input_line_start..)
        .unwrap_or_default()
        .iter()
        .take_while(|line| line.style == SingleSessionLineStyle::OverlaySelection)
        .count()
        .max(1);
    Some(StdinOverlayTarget {
        key,
        line_count: rendered_body_lines.len().max(1),
        input_line_start,
        input_line_count,
        password: state.is_password,
        has_input: !state.input.is_empty(),
    })
}

pub(crate) fn stdin_overlay_visual_lerp(
    from: StdinOverlayVisual,
    to: StdinOverlayVisual,
    progress: f32,
) -> StdinOverlayVisual {
    StdinOverlayVisual {
        opacity: lerp_f32(from.opacity, to.opacity, progress),
        y_offset_pixels: lerp_f32(from.y_offset_pixels, to.y_offset_pixels, progress),
        scale: lerp_f32(from.scale, to.scale, progress),
        height_lines: lerp_f32(from.height_lines, to.height_lines, progress),
        input_glow: lerp_f32(from.input_glow, to.input_glow, progress),
        submit_opacity: lerp_f32(from.submit_opacity, to.submit_opacity, progress),
    }
}

pub(crate) fn stdin_overlay_exit_visual(
    from: StdinOverlayVisual,
    progress: f32,
) -> StdinOverlayVisual {
    let eased = ease_out_cubic_local(progress);
    StdinOverlayVisual {
        opacity: from.opacity * (1.0 - eased),
        y_offset_pixels: from.y_offset_pixels - STDIN_OVERLAY_ENTRY_OFFSET_PIXELS * 0.55 * eased,
        scale: from.scale * (1.0 - (1.0 - STDIN_OVERLAY_ENTRY_SCALE) * eased),
        height_lines: from.height_lines,
        input_glow: from.input_glow * (1.0 - eased * 0.45),
        submit_opacity: (from.submit_opacity + 0.35 * (1.0 - eased)).clamp(0.0, 1.0),
    }
}

pub(crate) fn stdin_overlay_motion_cache_key(
    current: Option<(StdinOverlayTarget, StdinOverlayVisual)>,
    exiting: Option<(StdinOverlayTarget, StdinOverlayVisual)>,
    active: bool,
) -> u64 {
    let mut hasher = DefaultHasher::new();
    active.hash(&mut hasher);
    current.is_some().hash(&mut hasher);
    if let Some((target, visual)) = current {
        stdin_overlay_target_hash(target, &mut hasher);
        stdin_overlay_visual_hash(visual, &mut hasher);
    }
    exiting.is_some().hash(&mut hasher);
    if let Some((target, visual)) = exiting {
        stdin_overlay_target_hash(target, &mut hasher);
        stdin_overlay_visual_hash(visual, &mut hasher);
    }
    hasher.finish()
}

pub(crate) fn stdin_overlay_target_hash(target: StdinOverlayTarget, hasher: &mut impl Hasher) {
    target.hash(hasher);
}

pub(crate) fn stdin_overlay_visual_hash(visual: StdinOverlayVisual, hasher: &mut impl Hasher) {
    hash_f32(visual.opacity, hasher);
    hash_f32(visual.y_offset_pixels, hasher);
    hash_f32(visual.scale, hasher);
    hash_f32(visual.height_lines, hasher);
    hash_f32(visual.input_glow, hasher);
    hash_f32(visual.submit_opacity, hasher);
}
