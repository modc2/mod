//! Composer chrome rendering: surface outlines, composer frame, attachment chips, and the stdin overlay visuals.

use super::*;

pub(crate) fn push_single_session_surface_without_bottom_rule(
    vertices: &mut Vec<Vertex>,
    rect: Rect,
    color_index: usize,
    focus_pulse: f32,
    size: PhysicalSize<u32>,
) {
    let accent = panel_accent_color(color_index, true);
    push_rounded_rect(
        vertices,
        rect,
        PANEL_RADIUS,
        with_alpha(accent, 0.105),
        size,
    );
    push_rounded_rect(
        vertices,
        Rect {
            x: rect.x,
            y: rect.y,
            width: 5.0_f32.min(rect.width),
            height: rect.height,
        },
        PANEL_RADIUS,
        with_alpha(accent, 0.78),
        size,
    );

    let stroke_width = FOCUSED_BORDER_WIDTH + focus_pulse * 2.5;
    push_top_and_side_surface_outline(vertices, rect, stroke_width, accent, size);
    // Close the frame: a bottom stroke matching the top/side weight keeps the
    // window border symmetric instead of visually cropped at the bottom.
    push_rect(
        vertices,
        Rect {
            x: rect.x,
            y: rect.y + rect.height - stroke_width.max(1.0).min(rect.height),
            width: rect.width,
            height: stroke_width.max(1.0).min(rect.height),
        },
        accent,
        size,
    );

    if focus_pulse > 0.0 {
        let pulse_rect = inset_rect(rect, -3.0 * focus_pulse);
        push_top_and_side_surface_outline(
            vertices,
            pulse_rect,
            1.0,
            with_alpha(FOCUS_RING_COLOR, 0.32 * focus_pulse),
            size,
        );
    }
}

pub(crate) fn push_top_and_side_surface_outline(
    vertices: &mut Vec<Vertex>,
    rect: Rect,
    stroke_width: f32,
    color: [f32; 4],
    size: PhysicalSize<u32>,
) {
    let stroke_width = stroke_width.max(1.0).min(rect.width).min(rect.height);
    push_rect(
        vertices,
        Rect {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: stroke_width,
        },
        color,
        size,
    );
    push_rect(
        vertices,
        Rect {
            x: rect.x,
            y: rect.y,
            width: stroke_width,
            height: rect.height,
        },
        color,
        size,
    );
    push_rect(
        vertices,
        Rect {
            x: rect.x + rect.width - stroke_width,
            y: rect.y,
            width: stroke_width,
            height: rect.height,
        },
        color,
        size,
    );
}

pub(crate) fn push_single_session_composer_chrome(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    composer_motion: Option<&ComposerMotionFrame>,
    attachment_chip_motion: Option<&AttachmentChipMotionFrame>,
    layout: Option<SingleSessionLayout>,
) {
    if welcome_status_lane_visible(app) {
        return;
    }

    let typography = single_session_typography();
    let layout = layout.unwrap_or_else(|| single_session_layout_for_app(app, size));
    let target = composer_motion_target(app);
    let visual = composer_motion
        .map(|frame| frame.visual())
        .unwrap_or_else(|| ComposerMotionVisual::settled(target));
    let line_height = layout.metrics.composer_line_height;
    let draft_top = layout.draft_top;
    let content_width = layout.body.width;
    let rect = Rect {
        height: single_session_composer_height(size, layout.metrics, visual),
        ..layout.composer
    };
    if rect.width <= 12.0 || rect.height <= 10.0 {
        return;
    }

    push_single_session_attachment_chips(vertices, app, size, rect, attachment_chip_motion);

    if visual.placeholder_opacity > 0.001 {
        let prompt_width =
            app.composer_prompt().chars().count() as f32 * typography.code_size * 0.58;
        let rail_width = (content_width * 0.32).clamp(96.0, 260.0);
        // Baseline underline attached to the prompt: reads as "type here"
        // instead of a detached mid-row dash.
        push_rounded_rect(
            vertices,
            Rect {
                x: PANEL_TITLE_LEFT_PADDING + prompt_width + 4.0,
                y: draft_top + line_height * 0.78,
                width: rail_width,
                height: 3.0,
            },
            1.5,
            with_alpha(
                COMPOSER_PLACEHOLDER_RAIL_COLOR,
                COMPOSER_PLACEHOLDER_RAIL_COLOR[3] * visual.placeholder_opacity,
            ),
            size,
        );
    }

    if visual.submit_opacity > 0.001 {
        let pill_height = 22.0 * visual.submit_scale.max(0.72);
        let pill_width = 36.0 * visual.submit_scale.max(0.72);
        let pill_x = single_session_content_right(size) - pill_width;
        let pill_y = draft_top + (line_height - pill_height) * 0.5;
        let submit_color = mix_color(
            COMPOSER_SUBMIT_READY_COLOR,
            COMPOSER_SUBMIT_BUSY_COLOR,
            visual.processing_progress,
        );
        push_rounded_rect(
            vertices,
            Rect {
                x: pill_x,
                y: pill_y,
                width: pill_width,
                height: pill_height,
            },
            pill_height * 0.5,
            with_alpha(submit_color, submit_color[3] * visual.submit_opacity),
            size,
        );
        let arrow_alpha = (0.54 + 0.26 * visual.focus_opacity) * visual.submit_opacity;
        let arrow_y = pill_y + pill_height * 0.5 - 1.0;
        push_rect(
            vertices,
            Rect {
                x: pill_x + pill_width * 0.30,
                y: arrow_y,
                width: pill_width * 0.36,
                height: 2.0,
            },
            [1.0, 1.0, 1.0, arrow_alpha],
            size,
        );
        push_rect(
            vertices,
            Rect {
                x: pill_x + pill_width * 0.55,
                y: arrow_y - 4.0,
                width: 2.0,
                height: 10.0,
            },
            [1.0, 1.0, 1.0, arrow_alpha],
            size,
        );
    }
}

pub(crate) fn push_single_session_attachment_chips(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    composer_rect: Rect,
    attachment_chip_motion: Option<&AttachmentChipMotionFrame>,
) {
    let runs = attachment_chip_runs(&app.pending_images);
    if runs.is_empty() && attachment_chip_motion.is_none_or(|motion| motion.exiting().is_empty()) {
        return;
    }

    for run in runs {
        let visual = attachment_chip_motion
            .and_then(|motion| motion.visual_for_key(run.key))
            .unwrap_or_else(AttachmentChipVisual::settled);
        push_single_session_attachment_chip(vertices, composer_rect, run, visual, false, size);
    }

    if let Some(motion) = attachment_chip_motion {
        for (run, visual) in motion.exiting() {
            push_single_session_attachment_chip(vertices, composer_rect, *run, *visual, true, size);
        }
    }
}

pub(crate) fn push_single_session_attachment_chip(
    vertices: &mut Vec<Vertex>,
    composer_rect: Rect,
    run: AttachmentChipRun,
    visual: AttachmentChipVisual,
    exiting: bool,
    size: PhysicalSize<u32>,
) {
    if visual.opacity <= 0.001 || visual.scale <= 0.05 {
        return;
    }
    let scaled_width = ATTACHMENT_CHIP_WIDTH * visual.scale;
    let scaled_height = ATTACHMENT_CHIP_HEIGHT * visual.scale;
    let step = ATTACHMENT_CHIP_WIDTH + ATTACHMENT_CHIP_GAP;
    let x = composer_rect.x
        + 18.0
        + run.index as f32 * step
        + visual.x_offset_pixels
        + (ATTACHMENT_CHIP_WIDTH - scaled_width) * 0.5;
    let y = (composer_rect.y - ATTACHMENT_CHIP_HEIGHT - 8.0).max(PANEL_BODY_TOP_PADDING + 8.0)
        + visual.y_offset_pixels
        + (ATTACHMENT_CHIP_HEIGHT - scaled_height) * 0.5;
    let max_right = composer_rect.x + composer_rect.width - 16.0;
    if x >= max_right || y >= composer_rect.y + composer_rect.height {
        return;
    }
    let chip_rect = Rect {
        x,
        y,
        width: scaled_width.min((max_right - x).max(0.0)),
        height: scaled_height,
    };
    if chip_rect.width <= 5.0 || chip_rect.height <= 5.0 {
        return;
    }
    let fill = if exiting {
        ATTACHMENT_CHIP_EXIT_COLOR
    } else {
        ATTACHMENT_CHIP_BACKGROUND_COLOR
    };
    push_rounded_rect(
        vertices,
        chip_rect,
        chip_rect.height * 0.5,
        with_alpha(fill, fill[3] * visual.opacity),
        size,
    );
    let accent_width = (chip_rect.height * 0.34).clamp(5.0, 8.0);
    push_rounded_rect(
        vertices,
        Rect {
            x: chip_rect.x + 5.0 * visual.scale,
            y: chip_rect.y + (chip_rect.height - accent_width) * 0.5,
            width: accent_width,
            height: accent_width,
        },
        2.5 * visual.scale,
        with_alpha(
            ATTACHMENT_CHIP_ACCENT_COLOR,
            ATTACHMENT_CHIP_ACCENT_COLOR[3] * visual.opacity,
        ),
        size,
    );
    push_rect(
        vertices,
        Rect {
            x: chip_rect.x + chip_rect.width * 0.45,
            y: chip_rect.y + chip_rect.height * 0.43,
            width: chip_rect.width * 0.32,
            height: 2.0 * visual.scale,
        },
        with_alpha(COMPOSER_FOCUS_RING_COLOR, 0.42 * visual.opacity),
        size,
    );
}

pub(crate) fn push_single_session_stdin_overlay(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    rendered_body_lines: &[SingleSessionStyledLine],
    stdin_overlay_motion: Option<&StdinOverlayMotionFrame>,
) {
    let settled_current = stdin_overlay_target(app, rendered_body_lines)
        .map(|target| (target, StdinOverlayVisual::settled(target)));
    let current = stdin_overlay_motion
        .and_then(|motion| motion.current)
        .or(settled_current);
    if let Some((target, visual)) = current {
        push_single_session_stdin_overlay_visual(vertices, app, size, target, visual, false);
    }
    if let Some((target, visual)) = stdin_overlay_motion.and_then(|motion| motion.exiting) {
        push_single_session_stdin_overlay_visual(vertices, app, size, target, visual, true);
    }
}

pub(crate) fn push_single_session_stdin_overlay_visual(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    target: StdinOverlayTarget,
    visual: StdinOverlayVisual,
    exiting: bool,
) {
    if visual.opacity <= 0.001 || visual.scale <= 0.05 {
        return;
    }
    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let left = PANEL_TITLE_LEFT_PADDING - 10.0;
    let width = single_session_content_width(size) + 20.0;
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, target.line_count);
    let height = (visual.height_lines.max(1.0) * line_height + 18.0)
        .min((body_bottom - body_top + 20.0).max(line_height + 18.0));
    let rect = scaled_rect(
        Rect {
            x: left,
            y: body_top - 8.0 + visual.y_offset_pixels,
            width,
            height,
        },
        visual.scale,
    );
    if rect.width <= 12.0 || rect.height <= 10.0 {
        return;
    }

    let background = if exiting {
        mix_color(
            STDIN_OVERLAY_BACKGROUND_COLOR,
            STDIN_OVERLAY_EXIT_COLOR,
            0.42,
        )
    } else if target.password {
        mix_color(
            STDIN_OVERLAY_BACKGROUND_COLOR,
            [0.990, 0.968, 1.000, 0.660],
            0.36,
        )
    } else {
        STDIN_OVERLAY_BACKGROUND_COLOR
    };
    push_rounded_rect(
        vertices,
        inset_rect(rect, 2.0),
        15.0,
        stdin_overlay_alpha([0.020, 0.035, 0.080, 0.070], visual.opacity),
        size,
    );
    push_rounded_rect(
        vertices,
        rect,
        14.0,
        stdin_overlay_alpha(background, visual.opacity),
        size,
    );
    push_rounded_rect(
        vertices,
        Rect {
            x: rect.x + 7.0,
            y: rect.y + 7.0,
            width: 4.0,
            height: (rect.height - 14.0).max(1.0),
        },
        2.0,
        stdin_overlay_alpha(STDIN_OVERLAY_BORDER_COLOR, visual.opacity * 1.35),
        size,
    );
    push_top_and_side_surface_outline(
        vertices,
        rect,
        1.25,
        stdin_overlay_alpha(STDIN_OVERLAY_BORDER_COLOR, visual.opacity),
        size,
    );

    let input_top = body_top
        + target.input_line_start as f32 * line_height
        + visual.y_offset_pixels
        + line_height * 0.12;
    let input_height = (target.input_line_count as f32 * line_height - line_height * 0.24).max(8.0);
    let input_rect = Rect {
        x: rect.x + 16.0,
        y: input_top.max(rect.y + 8.0).min(rect.y + rect.height - 10.0),
        width: (rect.width - 32.0).max(1.0),
        height: input_height.min((rect.y + rect.height - input_top - 8.0).max(8.0)),
    };
    push_rounded_rect(
        vertices,
        input_rect,
        8.0,
        stdin_overlay_alpha(
            STDIN_OVERLAY_INPUT_RAIL_COLOR,
            visual.opacity * (0.55 + 0.45 * visual.input_glow),
        ),
        size,
    );

    if visual.submit_opacity > 0.001 {
        let pill_width = 44.0;
        let pill_height = 20.0;
        let pill = Rect {
            x: rect.x + rect.width - pill_width - 13.0,
            y: rect.y + rect.height - pill_height - 10.0,
            width: pill_width,
            height: pill_height,
        };
        push_rounded_rect(
            vertices,
            pill,
            pill_height * 0.5,
            stdin_overlay_alpha(
                STDIN_OVERLAY_SUBMIT_COLOR,
                visual.opacity * visual.submit_opacity,
            ),
            size,
        );
        let mark_alpha = visual.opacity * visual.submit_opacity * 0.74;
        push_rect(
            vertices,
            Rect {
                x: pill.x + pill.width * 0.30,
                y: pill.y + pill.height * 0.50,
                width: pill.width * 0.36,
                height: 2.0,
            },
            [1.0, 1.0, 1.0, mark_alpha],
            size,
        );
        push_rect(
            vertices,
            Rect {
                x: pill.x + pill.width * 0.55,
                y: pill.y + pill.height * 0.30,
                width: 2.0,
                height: pill.height * 0.42,
            },
            [1.0, 1.0, 1.0, mark_alpha],
            size,
        );
    }
}

pub(crate) fn stdin_overlay_alpha(mut color: [f32; 4], opacity: f32) -> [f32; 4] {
    color[3] = (color[3] * opacity.clamp(0.0, 1.0)).clamp(0.0, 1.0);
    color
}
