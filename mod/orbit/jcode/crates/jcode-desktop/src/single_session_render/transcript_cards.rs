//! Transcript card and message-highlight rendering plus tool-card rendering: per-viewport card pushes, geometries, palettes, and motion-driven visuals.

use super::*;

pub(crate) fn push_single_session_transcript_cards(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
) {
    let viewport = single_session_body_viewport_for_tick(app, size, tick, smooth_scroll_lines);
    push_single_session_transcript_cards_from_viewport(
        vertices,
        app,
        size,
        &viewport,
        viewport.total_lines,
        None,
    );
}

pub(crate) fn push_single_session_transcript_cards_from_viewport(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    viewport: &SingleSessionBodyViewport,
    total_lines: usize,
    transcript_motion: Option<&TranscriptCardMotionFrame>,
) {
    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let width = (single_session_content_right(size) - (PANEL_TITLE_LEFT_PADDING - 6.0)).max(1.0);
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, total_lines);

    let mut occurrences = HashMap::new();
    for run in single_session_transcript_card_runs(&viewport.lines) {
        let motion_key = transcript_card_motion_key(&viewport.lines, &run, &mut occurrences);
        let visual = transcript_motion
            .and_then(|motion| motion.visual_for_key(motion_key))
            .unwrap_or_default();
        let width = transcript_card_run_width(app, &viewport.lines, &run, width);
        push_single_session_transcript_card(
            vertices,
            run,
            visual,
            TranscriptCardGeometryContext {
                size,
                line_height,
                width,
                body_top,
                body_bottom,
                top_offset_pixels: viewport.top_offset_pixels,
            },
        );
    }

    if let Some(transcript_motion) = transcript_motion {
        for (run, visual) in transcript_motion.exiting() {
            push_single_session_transcript_card(
                vertices,
                *run,
                *visual,
                TranscriptCardGeometryContext {
                    size,
                    line_height,
                    width,
                    body_top,
                    body_bottom,
                    top_offset_pixels: viewport.top_offset_pixels,
                },
            );
        }
    }
}

pub(crate) fn push_single_session_transcript_message_highlights_from_viewport(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    viewport: &SingleSessionBodyViewport,
    total_lines: usize,
    message_motion: Option<&TranscriptMessageMotionFrame>,
) {
    if app.messages.is_empty() && app.streaming_response.is_empty() && app.error.is_none() {
        return;
    }

    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let width = (single_session_content_right(size) - (PANEL_TITLE_LEFT_PADDING - 7.0)).max(1.0);
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, total_lines);

    let mut occurrences = HashMap::new();
    for run in single_session_transcript_message_runs(&viewport.lines) {
        let motion_key = transcript_message_motion_key(&viewport.lines, &run, &mut occurrences);
        let visual = message_motion
            .and_then(|motion| motion.visual_for_key(motion_key))
            .unwrap_or_default();
        push_single_session_transcript_message_highlight(
            vertices,
            run,
            visual,
            TranscriptCardGeometryContext {
                size,
                line_height,
                width,
                body_top,
                body_bottom,
                top_offset_pixels: viewport.top_offset_pixels,
            },
        );
    }
}

pub(crate) fn push_single_session_transcript_message_highlight(
    vertices: &mut Vec<Vertex>,
    run: TranscriptMessageRun,
    visual: TranscriptMessageVisual,
    context: TranscriptCardGeometryContext,
) {
    if visual.opacity <= 0.001 {
        return;
    }
    let Some(color) = transcript_message_highlight_color(run.role) else {
        return;
    };
    let rect = Rect {
        x: PANEL_TITLE_LEFT_PADDING - 7.0,
        y: context.body_top
            + context.top_offset_pixels
            + run.line as f32 * context.line_height
            + 2.0,
        width: context.width,
        height: (run.line_count as f32 * context.line_height - 4.0).max(1.0),
    };
    let rect = transcript_message_visual_rect(rect, visual);
    let Some(rect) = clip_rect_to_vertical_bounds(rect, context.body_top, context.body_bottom)
    else {
        return;
    };
    let opacity = visual.opacity.clamp(0.0, 1.0);
    push_rounded_rect(
        vertices,
        rect,
        8.0,
        transcript_message_alpha(color, opacity),
        context.size,
    );
    push_rounded_rect(
        vertices,
        Rect {
            x: rect.x,
            y: rect.y + 2.0,
            width: 2.2,
            height: (rect.height - 4.0).max(1.0),
        },
        1.1,
        transcript_message_alpha(color, opacity * TRANSCRIPT_MESSAGE_ACCENT_ALPHA_MULTIPLIER),
        context.size,
    );
}

pub(crate) fn transcript_message_highlight_color(role: TranscriptMessageRole) -> Option<[f32; 4]> {
    Some(match role {
        TranscriptMessageRole::User => TRANSCRIPT_MESSAGE_USER_HIGHLIGHT_COLOR,
        TranscriptMessageRole::Assistant => TRANSCRIPT_MESSAGE_ASSISTANT_HIGHLIGHT_COLOR,
        TranscriptMessageRole::Meta => TRANSCRIPT_MESSAGE_META_HIGHLIGHT_COLOR,
        TranscriptMessageRole::Error => TRANSCRIPT_MESSAGE_ERROR_HIGHLIGHT_COLOR,
    })
}

pub(crate) fn transcript_message_visual_rect(rect: Rect, visual: TranscriptMessageVisual) -> Rect {
    surface_motion_visual_rect(rect, visual)
}

pub(crate) fn transcript_message_alpha(color: [f32; 4], opacity: f32) -> [f32; 4] {
    surface_motion_alpha(color, opacity)
}

#[derive(Clone, Copy)]
pub(crate) struct TranscriptCardGeometryContext {
    pub(crate) size: PhysicalSize<u32>,
    pub(crate) line_height: f32,
    pub(crate) width: f32,
    pub(crate) body_top: f32,
    pub(crate) body_bottom: f32,
    pub(crate) top_offset_pixels: f32,
}

/// Card width for a transcript run. Markdown tables hug their content width
/// (plus padding) instead of banding the full content column, so the zebra
/// background doesn't stretch far past a narrow table.
pub(crate) fn transcript_card_run_width(
    app: &SingleSessionApp,
    lines: &[SingleSessionStyledLine],
    run: &SingleSessionTranscriptCardRun,
    full_width: f32,
) -> f32 {
    if run.style != SingleSessionLineStyle::AssistantTable {
        return full_width;
    }
    let max_columns = lines
        .iter()
        .skip(run.line)
        .take(run.line_count)
        .map(|line| line.text.chars().count())
        .max()
        .unwrap_or(0);
    if max_columns == 0 {
        return full_width;
    }
    let char_width = single_session_body_char_width_for_scale(app.text_scale());
    (max_columns as f32 * char_width + 18.0).min(full_width)
}

pub(crate) fn push_single_session_transcript_card(
    vertices: &mut Vec<Vertex>,
    run: SingleSessionTranscriptCardRun,
    visual: TranscriptCardVisual,
    context: TranscriptCardGeometryContext,
) {
    let Some(color) = single_session_line_card_color(run.style) else {
        return;
    };
    if visual.opacity <= 0.001 {
        return;
    }
    let rect = Rect {
        x: PANEL_TITLE_LEFT_PADDING - 6.0,
        y: context.body_top
            + context.top_offset_pixels
            + run.line as f32 * context.line_height
            + 3.0,
        width: context.width,
        height: (run.line_count as f32 * context.line_height - 6.0).max(1.0),
    };
    let rect = transcript_card_visual_rect(rect, visual);
    let Some(rect) = clip_rect_to_vertical_bounds(rect, context.body_top, context.body_bottom)
    else {
        return;
    };
    push_rounded_rect(
        vertices,
        rect,
        7.0,
        transcript_card_alpha(color, visual.opacity),
        context.size,
    );
}

pub(crate) fn push_single_session_tool_cards(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
    tool_motion: Option<&ToolCardMotionFrame>,
) {
    let viewport = single_session_body_viewport_for_tick(app, size, tick, smooth_scroll_lines);
    push_single_session_tool_cards_from_viewport(
        vertices,
        app,
        size,
        &viewport,
        viewport.total_lines,
        motion_seconds_for_tick(tick),
        tool_motion,
    );
}

pub(crate) fn push_single_session_tool_cards_from_viewport(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    viewport: &SingleSessionBodyViewport,
    total_lines: usize,
    motion_seconds: f32,
    tool_motion: Option<&ToolCardMotionFrame>,
) {
    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let width = (single_session_content_right(size) - (PANEL_TITLE_LEFT_PADDING - 10.0)).max(1.0);
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, total_lines);
    let pulse = active_tool_card_pulse(motion_seconds);

    for run in single_session_tool_card_runs(&viewport.lines) {
        let rect = Rect {
            x: PANEL_TITLE_LEFT_PADDING - 10.0,
            y: body_top + viewport.top_offset_pixels + run.line as f32 * line_height + 2.0,
            width,
            height: (run.line_count as f32 * line_height - 4.0).max(1.0),
        };
        let Some(rect) = clip_rect_to_vertical_bounds(rect, body_top, body_bottom) else {
            continue;
        };
        let visual = tool_motion
            .and_then(|motion| motion.visual_for(&run.call_id))
            .unwrap_or_else(|| default_tool_card_visual(&run, pulse, motion_seconds));
        push_single_session_tool_card(vertices, &run, rect, line_height, pulse, visual, size);
    }

    if let Some(tool_motion) = tool_motion {
        for (run, visual) in tool_motion.exiting() {
            let rect = Rect {
                x: PANEL_TITLE_LEFT_PADDING - 10.0,
                y: body_top + viewport.top_offset_pixels + run.line as f32 * line_height + 2.0,
                width,
                height: (run.line_count as f32 * line_height - 4.0).max(1.0),
            };
            let Some(rect) = clip_rect_to_vertical_bounds(rect, body_top, body_bottom) else {
                continue;
            };
            push_single_session_tool_card(vertices, run, rect, line_height, pulse, *visual, size);
        }
    }
}

pub(crate) fn push_single_session_tool_card(
    vertices: &mut Vec<Vertex>,
    run: &SingleSessionToolCardRun,
    rect: Rect,
    line_height: f32,
    _pulse: f32,
    visual: ToolCardVisual,
    size: PhysicalSize<u32>,
) {
    let radius = 9.0;
    let opacity = visual.opacity.clamp(0.0, 1.0);
    if opacity <= 0.001 {
        return;
    }
    let rect = tool_card_visual_rect(rect, visual);

    let shadow = Rect {
        x: rect.x + 1.5,
        y: rect.y + 2.0,
        width: rect.width,
        height: rect.height,
    };
    push_rounded_rect(
        vertices,
        shadow,
        radius,
        tool_card_alpha([0.030, 0.050, 0.090, 0.035], opacity),
        size,
    );
    push_rounded_rect(
        vertices,
        rect,
        radius,
        tool_card_alpha(visual.border, opacity),
        size,
    );
    let inner = Rect {
        x: rect.x + 1.0,
        y: rect.y + 1.0,
        width: (rect.width - 2.0).max(1.0),
        height: (rect.height - 2.0).max(1.0),
    };
    push_rounded_rect(
        vertices,
        inner,
        radius - 1.0,
        tool_card_alpha(visual.background, opacity),
        size,
    );

    if visual.flash_alpha > 0.001 {
        push_rounded_rect(
            vertices,
            inner,
            radius - 1.0,
            tool_card_alpha(with_alpha(visual.flash_color, visual.flash_alpha), opacity),
            size,
        );
        push_rounded_rect_border(
            vertices,
            rect,
            radius,
            1.5,
            tool_card_alpha(
                with_alpha(visual.flash_color, visual.flash_alpha * 1.35),
                opacity,
            ),
            size,
        );
    }

    let rail_rect = tool_card_rail_rect(rect);
    push_rounded_rect(
        vertices,
        rail_rect,
        rail_rect.width / 2.0,
        tool_card_alpha(visual.rail, opacity),
        size,
    );
    if run.active || run.state.is_active() {
        push_active_tool_card_motion(vertices, rect, rail_rect, visual, opacity, size);
    }

    let dot_size = 9.0;
    push_rounded_rect(
        vertices,
        Rect {
            x: rail_rect.x + (rail_rect.width - dot_size) * 0.5,
            y: rect.y + line_height * 0.44 - dot_size * 0.5,
            width: dot_size,
            height: dot_size,
        },
        dot_size / 2.0,
        tool_card_alpha(visual.rail, opacity),
        size,
    );

    if run.detail_line_count > 0 {
        let drawer_target_height = (rect.height - line_height - 7.0).max(1.0);
        let drawer_height = (drawer_target_height * visual.output_reveal.clamp(0.0, 1.0)).max(1.0);
        let drawer = Rect {
            x: rect.x + 26.0,
            y: rect.y + line_height + 1.0,
            width: (rect.width - 38.0).max(1.0),
            height: drawer_height,
        };
        push_rounded_rect(
            vertices,
            drawer,
            7.0,
            tool_card_alpha(
                TOOL_OUTPUT_DRAWER_COLOR,
                opacity * visual.output_reveal.clamp(0.0, 1.0),
            ),
            size,
        );
    }
}

pub(crate) fn default_tool_card_visual(
    run: &SingleSessionToolCardRun,
    pulse: f32,
    motion_seconds: f32,
) -> ToolCardVisual {
    let mut palette = tool_card_palette(run.state, run.active);
    if run.active || run.state.is_active() {
        palette.background[3] = (palette.background[3] + 0.08 * pulse).clamp(0.0, 0.82);
        palette.border[3] = (palette.border[3] + 0.16 * pulse).clamp(0.0, 0.62);
        palette.rail[3] = (palette.rail[3] + 0.24 * pulse).clamp(0.0, 0.78);
    }
    ToolCardVisual {
        background: palette.background,
        border: palette.border,
        rail: palette.rail,
        chip: palette.chip,
        active_phase: active_tool_card_sweep_phase(motion_seconds),
        ..ToolCardVisual::default()
    }
}

pub(crate) fn tool_card_visual_rect(rect: Rect, visual: ToolCardVisual) -> Rect {
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

pub(crate) fn tool_card_alpha(mut color: [f32; 4], opacity: f32) -> [f32; 4] {
    color[3] = (color[3] * opacity.clamp(0.0, 1.0)).clamp(0.0, 1.0);
    color
}

pub(crate) fn push_active_tool_card_motion(
    vertices: &mut Vec<Vertex>,
    rect: Rect,
    rail_rect: Rect,
    visual: ToolCardVisual,
    opacity: f32,
    size: PhysicalSize<u32>,
) {
    let phase = visual.active_phase.fract();
    let mut head_color = visual.rail;
    head_color[3] = (head_color[3] + 0.20).clamp(0.0, 0.92);
    let head_color = tool_card_alpha(head_color, opacity);

    let head_height = (rail_rect.height * 0.34)
        .clamp(10.0, 34.0)
        .min(rail_rect.height);
    let head_top = rail_rect.y - head_height + (rail_rect.height + head_height) * phase;
    let visible_top = head_top.max(rail_rect.y);
    let visible_bottom = (head_top + head_height).min(rail_rect.y + rail_rect.height);
    if visible_bottom > visible_top {
        push_rounded_rect(
            vertices,
            Rect {
                x: rail_rect.x - 0.5,
                y: visible_top,
                width: rail_rect.width + 1.0,
                height: (visible_bottom - visible_top).max(1.0),
            },
            (rail_rect.width + 1.0) * 0.5,
            head_color,
            size,
        );
    }

    let sweep_width = (rect.width * 0.16)
        .clamp(26.0, 92.0)
        .min(rect.width.max(1.0));
    let travel = rect.width + sweep_width;
    let sweep_x = rect.x - sweep_width + travel * phase;
    let top_rect = clipped_horizontal_sweep(sweep_x, sweep_width, rect.x, rect.x + rect.width).map(
        |(x, width)| Rect {
            x,
            y: rect.y + 1.0,
            width,
            height: 1.5,
        },
    );
    if let Some(top_rect) = top_rect {
        push_rounded_rect(vertices, top_rect, 1.0, head_color, size);
    }

    let reverse_x = rect.x - sweep_width + travel * (1.0 - phase);
    let bottom_rect = clipped_horizontal_sweep(reverse_x, sweep_width, rect.x, rect.x + rect.width)
        .map(|(x, width)| Rect {
            x,
            y: rect.y + rect.height - 2.5,
            width,
            height: 1.5,
        });
    if let Some(bottom_rect) = bottom_rect {
        push_rounded_rect(vertices, bottom_rect, 1.0, head_color, size);
    }
}

pub(crate) fn clipped_horizontal_sweep(
    x: f32,
    width: f32,
    min_x: f32,
    max_x: f32,
) -> Option<(f32, f32)> {
    let left = x.max(min_x);
    let right = (x + width).min(max_x);
    (right > left).then_some((left, right - left))
}

#[cfg(test)]
pub(crate) fn single_session_tool_card_geometries(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    rendered_body_lines: &[SingleSessionStyledLine],
) -> Vec<SingleSessionToolCardGeometry> {
    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let width = (single_session_content_right(size) - (PANEL_TITLE_LEFT_PADDING - 10.0)).max(1.0);
    let body_top = single_session_body_top_for_app(app, size);

    single_session_tool_card_runs(rendered_body_lines)
        .into_iter()
        .map(|run| {
            let card_rect = Rect {
                x: PANEL_TITLE_LEFT_PADDING - 10.0,
                y: body_top + run.line as f32 * line_height + 2.0,
                width,
                height: (run.line_count as f32 * line_height - 4.0).max(1.0),
            };
            SingleSessionToolCardGeometry {
                run,
                rail_rect: tool_card_rail_rect(card_rect),
                card_rect,
                line_height,
            }
        })
        .collect()
}

pub(crate) fn single_session_tool_card_runs(
    lines: &[SingleSessionStyledLine],
) -> Vec<SingleSessionToolCardRun> {
    let mut runs = Vec::new();
    let mut current: Option<SingleSessionToolCardRun> = None;

    for (line, styled_line) in lines.iter().enumerate() {
        let Some(metadata) = styled_line.tool.as_ref() else {
            if let Some(run) = current.take() {
                runs.push(run);
            }
            continue;
        };

        match &mut current {
            Some(run) if run.call_id == metadata.call_id && run.line + run.line_count == line => {
                run.line_count += 1;
                run.active |= metadata.active;
                run.expanded |= metadata.expanded;
                if metadata.kind == SingleSessionToolLineKind::Detail {
                    run.detail_line_count += 1;
                }
                if metadata.state.is_active() || !run.state.is_active() {
                    run.state = metadata.state;
                }
            }
            Some(run) => {
                runs.push(run.clone());
                current = Some(tool_card_run_from_metadata(line, metadata));
            }
            None => current = Some(tool_card_run_from_metadata(line, metadata)),
        }
    }

    if let Some(run) = current {
        runs.push(run);
    }

    runs
}

pub(crate) fn tool_card_run_from_metadata(
    line: usize,
    metadata: &SingleSessionToolLineMetadata,
) -> SingleSessionToolCardRun {
    SingleSessionToolCardRun {
        line,
        line_count: 1,
        call_id: metadata.call_id.clone(),
        name: metadata.name.clone(),
        state: metadata.state,
        active: metadata.active,
        expanded: metadata.expanded,
        detail_line_count: usize::from(metadata.kind == SingleSessionToolLineKind::Detail),
        kind: metadata.kind,
    }
}

pub(crate) fn tool_card_rail_rect(card_rect: Rect) -> Rect {
    Rect {
        x: card_rect.x + 9.0,
        y: card_rect.y + 7.0,
        width: 3.0,
        height: (card_rect.height - 14.0).max(6.0),
    }
}

/// Breathing pulse for active tool cards, driven by continuous seconds so the
/// live render loop (smooth wall clock) and deterministic captures (tick
/// seconds) share one code path.
pub(crate) fn active_tool_card_pulse(motion_seconds: f32) -> f32 {
    if crate::animation::desktop_reduced_motion_enabled() {
        return 0.0;
    }
    let phase = (motion_seconds / TOOL_CARD_PULSE_PERIOD_SECONDS).rem_euclid(1.0);
    0.5 + 0.5 * (phase * std::f32::consts::TAU).sin()
}

/// Continuous 0..1 phase for the active tool card rail/edge sweep.
pub(crate) fn active_tool_card_sweep_phase(motion_seconds: f32) -> f32 {
    if crate::animation::desktop_reduced_motion_enabled() {
        return 0.0;
    }
    (motion_seconds / TOOL_CARD_SWEEP_PERIOD_SECONDS).rem_euclid(1.0)
}

pub(crate) fn single_session_tool_card_background(
    state: SingleSessionToolVisualState,
    active: bool,
) -> [f32; 4] {
    if active || state.is_active() {
        return TOOL_CARD_ACTIVE_BACKGROUND_COLOR;
    }
    match state {
        SingleSessionToolVisualState::Succeeded => TOOL_CARD_SUCCESS_BACKGROUND_COLOR,
        SingleSessionToolVisualState::Failed => TOOL_CARD_FAILED_BACKGROUND_COLOR,
        SingleSessionToolVisualState::Group => TOOL_CARD_GROUP_BACKGROUND_COLOR,
        _ => TOOL_CARD_BACKGROUND_COLOR,
    }
}

pub(crate) fn single_session_tool_state_accent(state: SingleSessionToolVisualState) -> [f32; 4] {
    match state {
        SingleSessionToolVisualState::Succeeded => TOOL_SUCCESS_TEXT_COLOR,
        SingleSessionToolVisualState::Failed => TOOL_FAILED_TEXT_COLOR,
        SingleSessionToolVisualState::Running => TOOL_RUNNING_TEXT_COLOR,
        SingleSessionToolVisualState::Preparing => TOOL_PENDING_TEXT_COLOR,
        SingleSessionToolVisualState::Group => TOOL_TEXT_COLOR,
        SingleSessionToolVisualState::Unknown => TOOL_TIMELINE_RAIL_COLOR,
    }
}

#[cfg(test)]
pub(crate) fn single_session_transcript_card_geometries(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    rendered_body_lines: &[SingleSessionStyledLine],
) -> Vec<SingleSessionTranscriptCardGeometry> {
    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let width = (single_session_content_right(size) - (PANEL_TITLE_LEFT_PADDING - 6.0)).max(1.0);
    let body_top = single_session_body_top_for_app(app, size);

    single_session_transcript_card_runs(rendered_body_lines)
        .into_iter()
        .filter_map(|run| {
            single_session_line_card_color(run.style)?;
            let card_rect = Rect {
                x: PANEL_TITLE_LEFT_PADDING - 6.0,
                y: body_top + run.line as f32 * line_height + 3.0,
                width,
                height: (run.line_count as f32 * line_height - 6.0).max(1.0),
            };
            Some(SingleSessionTranscriptCardGeometry {
                run,
                card_rect,
                text_left: PANEL_TITLE_LEFT_PADDING,
                line_height,
            })
        })
        .collect()
}
