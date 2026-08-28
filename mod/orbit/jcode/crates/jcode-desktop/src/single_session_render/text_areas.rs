//! Glyphon TextArea assembly for single-session rendering: mapping cached text buffers to positioned text areas, plus header/version label helpers.

use super::*;

pub(crate) fn single_session_text_areas(
    buffers: &[Buffer],
    size: PhysicalSize<u32>,
) -> Vec<TextArea<'_>> {
    single_session_text_areas_for_fresh_state(buffers, size, false)
}

#[cfg(test)]
pub(crate) fn single_session_text_areas_for_app<'a>(
    app: &SingleSessionApp,
    buffers: &'a [Buffer],
    size: PhysicalSize<u32>,
) -> Vec<TextArea<'a>> {
    single_session_text_areas_for_app_with_scroll(app, buffers, size, 0, 0.0)
}

pub(crate) fn single_session_text_areas_for_app_with_scroll<'a>(
    app: &SingleSessionApp,
    buffers: &'a [Buffer],
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
) -> Vec<TextArea<'a>> {
    let inline_widget_kind = app.render_inline_widget_kind();
    let inline_widget_lines = app.render_inline_widget_styled_lines();
    let inline_widget_preview_start_line =
        inline_widget_split_preview_start(inline_widget_kind, &inline_widget_lines);
    let inline_widget_text_width = inline_widget_text_width_for_lines(
        inline_widget_kind,
        &inline_widget_lines,
        size,
        app.text_scale(),
    );
    let viewport = single_session_body_viewport_for_tick(app, size, tick, smooth_scroll_lines);
    let layout = single_session_layout_for_total_lines(app, size, viewport.total_lines);
    let welcome_chrome_offset_pixels =
        welcome_timeline_visual_offset_pixels(app, size, smooth_scroll_lines);
    let welcome_chrome_visible =
        welcome_timeline_chrome_visible(app, size, welcome_chrome_offset_pixels);
    single_session_text_areas_for_state(
        buffers,
        size,
        welcome_chrome_visible,
        false,
        viewport.top_offset_pixels,
        layout.body.y,
        layout.body_text_bounds_bottom(),
        app.render_inline_widget_visible_line_count(),
        inline_widget_kind,
        inline_widget_preview_start_line,
        inline_widget_text_width,
        inline_widget_bottom_limit_for_layout(app, layout, welcome_chrome_visible),
        layout.draft_top,
        welcome_chrome_offset_pixels,
        welcome_status_lane_visible(app),
        app.is_fresh_welcome_visible() && app.draft.is_empty(),
        app.text_scale(),
        welcome_hero_runtime_mask_supported(&app.welcome_hero_text()),
        1.0,
        app.render_inline_widget_reveal_progress(),
    )
}

pub(crate) fn single_session_text_areas_for_app_with_cached_body<'a>(
    app: &SingleSessionApp,
    buffers: &'a [Buffer],
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    rendered_body_lines: &[SingleSessionStyledLine],
) -> Vec<TextArea<'a>> {
    let viewport = single_session_body_viewport_from_lines(
        app,
        size,
        smooth_scroll_lines,
        rendered_body_lines,
    );
    single_session_text_areas_for_app_with_cached_body_viewport(
        app,
        buffers,
        size,
        smooth_scroll_lines,
        viewport,
    )
}

pub(crate) fn single_session_text_areas_for_app_with_cached_body_viewport<'a>(
    app: &SingleSessionApp,
    buffers: &'a [Buffer],
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    viewport: SingleSessionBodyViewport,
) -> Vec<TextArea<'a>> {
    single_session_text_areas_for_app_with_cached_body_viewport_and_reveal(
        app,
        buffers,
        size,
        smooth_scroll_lines,
        viewport,
        1.0,
    )
}

pub(crate) fn single_session_text_areas_for_app_with_cached_body_viewport_and_reveal<'a>(
    app: &SingleSessionApp,
    buffers: &'a [Buffer],
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    viewport: SingleSessionBodyViewport,
    welcome_hero_reveal_progress: f32,
) -> Vec<TextArea<'a>> {
    let inline_widget_kind = app.render_inline_widget_kind();
    let inline_widget_lines = app.render_inline_widget_styled_lines();
    let inline_widget_preview_start_line =
        inline_widget_split_preview_start(inline_widget_kind, &inline_widget_lines);
    let inline_widget_text_width = inline_widget_text_width_for_lines(
        inline_widget_kind,
        &inline_widget_lines,
        size,
        app.text_scale(),
    );
    let welcome_chrome_offset_pixels = welcome_timeline_visual_offset_pixels_for_total_lines(
        app,
        size,
        smooth_scroll_lines,
        viewport.total_lines,
    );
    let layout = single_session_layout_for_total_lines(app, size, viewport.total_lines);
    let welcome_chrome_visible =
        welcome_timeline_chrome_visible(app, size, welcome_chrome_offset_pixels);
    single_session_text_areas_for_state(
        buffers,
        size,
        welcome_chrome_visible,
        false,
        viewport.top_offset_pixels,
        layout.body.y,
        layout.body_text_bounds_bottom(),
        app.render_inline_widget_visible_line_count(),
        inline_widget_kind,
        inline_widget_preview_start_line,
        inline_widget_text_width,
        inline_widget_bottom_limit_for_layout(app, layout, welcome_chrome_visible),
        layout.draft_top,
        welcome_chrome_offset_pixels,
        welcome_status_lane_visible(app),
        app.is_fresh_welcome_visible() && app.draft.is_empty(),
        app.text_scale(),
        welcome_hero_runtime_mask_supported(&app.welcome_hero_text()),
        welcome_hero_reveal_progress,
        app.render_inline_widget_reveal_progress(),
    )
}

pub(crate) fn single_session_streaming_text_area_for_cached_body_viewport<'a>(
    app: &SingleSessionApp,
    buffer: &'a Buffer,
    size: PhysicalSize<u32>,
    viewport: SingleSessionBodyViewport,
    streaming_start_line: usize,
    opacity: f32,
    y_offset_pixels: f32,
) -> TextArea<'a> {
    let layout = single_session_layout_for_total_lines(app, size, viewport.total_lines);
    let line_height = layout.metrics.body_line_height;
    let left = PANEL_TITLE_LEFT_PADDING;
    let right = single_session_content_right(size) as i32;
    let body_top = layout.body.y;
    let top = body_top
        + viewport.top_offset_pixels
        + streaming_start_line.saturating_sub(viewport.start_line) as f32 * line_height
        + y_offset_pixels.max(0.0);
    TextArea {
        buffer,
        left,
        top,
        scale: 1.0,
        bounds: TextBounds {
            left: 0,
            top: body_top as i32,
            right,
            bottom: layout.body_text_bounds_bottom(),
        },
        default_color: text_color([
            ASSISTANT_TEXT_COLOR[0],
            ASSISTANT_TEXT_COLOR[1],
            ASSISTANT_TEXT_COLOR[2],
            ASSISTANT_TEXT_COLOR[3] * opacity.clamp(0.0, 1.0),
        ]),
    }
}

pub(crate) fn single_session_text_areas_for_fresh_state(
    buffers: &[Buffer],
    size: PhysicalSize<u32>,
    fresh_welcome_visible: bool,
) -> Vec<TextArea<'_>> {
    single_session_text_areas_for_state(
        buffers,
        size,
        fresh_welcome_visible,
        false,
        0.0,
        PANEL_BODY_TOP_PADDING,
        text_bounds_bottom(single_session_body_bottom(size)),
        0,
        None,
        None,
        0.0,
        single_session_draft_top_for_fresh_state(size, fresh_welcome_visible),
        single_session_draft_top_for_fresh_state(size, fresh_welcome_visible),
        0.0,
        false,
        false,
        1.0,
        false,
        1.0,
        1.0,
    )
}

pub(crate) fn welcome_status_lane_visible(app: &SingleSessionApp) -> bool {
    let _ = app;
    false
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn single_session_text_areas_for_state(
    buffers: &[Buffer],
    size: PhysicalSize<u32>,
    welcome_chrome_visible: bool,
    welcome_handoff_visible: bool,
    body_top_offset_pixels: f32,
    body_top: f32,
    body_bottom: i32,
    inline_widget_line_count: usize,
    inline_widget_kind: Option<InlineWidgetKind>,
    _inline_widget_preview_start_line: Option<usize>,
    inline_widget_text_width: f32,
    inline_widget_bottom_limit: f32,
    draft_top: f32,
    welcome_chrome_offset_pixels: f32,
    status_lane_visible: bool,
    startup_hint_visible: bool,
    ui_scale: f32,
    welcome_hero_runtime_mask_available: bool,
    welcome_hero_reveal_progress: f32,
    inline_widget_reveal_progress: f32,
) -> Vec<TextArea<'_>> {
    if buffers.len() < 4 {
        return Vec::new();
    }

    let left = PANEL_TITLE_LEFT_PADDING;
    let right = single_session_content_right(size) as i32;
    let bottom = size.height.saturating_sub(PANEL_TITLE_TOP_PADDING as u32) as i32;
    let body_top = if welcome_handoff_visible {
        draft_top
    } else {
        body_top
    };
    let body_bottom = if welcome_handoff_visible {
        bottom
    } else {
        body_bottom
    };
    let version_label = fresh_welcome_version_label();
    let version_font_size = fresh_welcome_version_font_size() * ui_scale;
    let version_left = if welcome_chrome_visible {
        fresh_welcome_version_left(&version_label, size, version_font_size)
    } else {
        (size.width as f32 * 0.42).max(left + 220.0)
    };
    let version_top = if welcome_chrome_visible {
        fresh_welcome_version_top_for_scale(size, ui_scale) + welcome_chrome_offset_pixels
    } else {
        PANEL_TITLE_TOP_PADDING + 3.0
    };
    let version_bounds_top = if welcome_chrome_visible {
        version_top as i32
    } else {
        0
    };
    let version_bounds_bottom = if welcome_chrome_visible {
        (version_top + version_font_size * 1.4) as i32
    } else {
        64
    };

    let typography = single_session_typography_for_scale(ui_scale);
    let inline_widget_layout = if inline_widget_line_count > 0 {
        let target_top = inline_widget_target_top(
            size,
            inline_widget_kind,
            ui_scale,
            body_bottom as f32,
            welcome_chrome_visible,
            welcome_chrome_offset_pixels,
        );
        inline_widget_card_layout_with_bottom_limit(
            size,
            inline_widget_kind,
            &typography,
            inline_widget_line_count,
            inline_widget_text_width,
            target_top,
            inline_widget_reveal_progress,
            inline_widget_bottom_limit,
        )
    } else {
        None
    };

    let mut areas = Vec::new();

    // Keep the composer lane first in glyphon preparation order. The visual
    // positions are unchanged, but fresh keystrokes get shaped before the
    // heavier transcript/chrome text on frames where both changed.
    if !status_lane_visible && !welcome_handoff_visible {
        areas.push(TextArea {
            buffer: &buffers[2],
            left,
            top: draft_top,
            scale: 1.0,
            bounds: TextBounds {
                left: 0,
                top: draft_top as i32,
                right,
                bottom,
            },
            default_color: text_color(PANEL_SECTION_COLOR),
        });
    }

    if startup_hint_visible
        && !welcome_handoff_visible
        && !status_lane_visible
        && let Some(hint_buffer) = buffers.get(6)
    {
        let hint_top = draft_top + typography.code_size * typography.code_line_height * 1.35;
        areas.push(TextArea {
            buffer: hint_buffer,
            left,
            top: hint_top,
            scale: 1.0,
            bounds: TextBounds {
                left: 0,
                top: hint_top as i32,
                right,
                bottom,
            },
            default_color: text_color(META_TEXT_COLOR),
        });
    }

    areas.push(TextArea {
        buffer: &buffers[0],
        left,
        top: PANEL_TITLE_TOP_PADDING,
        scale: 1.0,
        bounds: TextBounds {
            left: 0,
            top: 0,
            right,
            bottom: 64,
        },
        default_color: text_color(PANEL_TITLE_COLOR),
    });
    areas.push(TextArea {
        buffer: &buffers[3],
        left: version_left,
        top: version_top,
        scale: 1.0,
        bounds: TextBounds {
            left: 0,
            top: version_bounds_top,
            right,
            bottom: version_bounds_bottom,
        },
        default_color: text_color(META_TEXT_COLOR),
    });
    areas.push(TextArea {
        buffer: &buffers[1],
        left,
        top: body_top + body_top_offset_pixels,
        scale: 1.0,
        bounds: TextBounds {
            left: 0,
            top: body_top as i32,
            right,
            bottom: body_bottom,
        },
        default_color: text_color(ASSISTANT_TEXT_COLOR),
    });

    if welcome_chrome_visible
        && !welcome_hero_runtime_mask_available
        && !welcome_hero_reveal_is_active(welcome_hero_reveal_progress)
        && let Some(hero_buffer) = buffers.get(5)
    {
        let (hero_min, hero_max) = glyph_welcome_hero_bounds(size, ui_scale);
        areas.push(TextArea {
            buffer: hero_buffer,
            left: hero_min[0],
            top: hero_min[1] + welcome_chrome_offset_pixels,
            scale: 1.0,
            bounds: TextBounds {
                left: hero_min[0] as i32,
                top: (hero_min[1] + welcome_chrome_offset_pixels) as i32,
                right: hero_max[0].ceil() as i32,
                bottom: (hero_max[1] + welcome_chrome_offset_pixels).ceil() as i32,
            },
            default_color: text_color(WELCOME_HANDWRITING_COLOR),
        });
    }

    if inline_widget_line_count > 0
        && let Some(buffer) = buffers.get(4)
        && let Some(layout) = inline_widget_layout
    {
        let split_columns = (inline_widget_kind == Some(InlineWidgetKind::SessionSwitcher))
            .then(|| session_switcher_split_columns(&layout))
            .flatten();
        let rail_bounds_right = split_columns
            .map(|columns| columns.rail.x + columns.rail.width - layout.padding_x * 0.75);
        let inline_bounds_right = rail_bounds_right
            .unwrap_or(layout.visible_text_right)
            .min(right as f32)
            .max(layout.text_left);
        let inline_bounds_bottom = layout
            .visible_text_bottom
            .min(draft_top)
            .max(layout.text_top);
        if inline_bounds_right > layout.text_left && inline_bounds_bottom > layout.text_top {
            areas.push(TextArea {
                buffer,
                left: layout.text_left,
                top: layout.text_top,
                scale: 1.0,
                bounds: TextBounds {
                    left: 0,
                    top: layout.text_top as i32,
                    right: inline_bounds_right as i32,
                    bottom: inline_bounds_bottom as i32,
                },
                default_color: text_color(ASSISTANT_TEXT_COLOR),
            });
        }
        if inline_widget_kind == Some(InlineWidgetKind::SessionSwitcher)
            && let Some(preview_buffer) = buffers.get(7)
        {
            let columns = split_columns.unwrap_or_else(|| {
                let fallback_gap = (layout.card.width * 0.018).clamp(9.0, 15.0);
                let rail_width = session_switcher_fallback_rail_width(layout.card.width);
                let rail = Rect {
                    x: layout.card.x + layout.padding_x * 0.72,
                    y: layout.card.y + layout.padding_x * 0.18,
                    width: rail_width,
                    height: (layout.card.height - layout.padding_x * 0.36).max(1.0),
                };
                let gap = Rect {
                    x: rail.x + rail.width,
                    y: rail.y,
                    width: fallback_gap,
                    height: rail.height,
                };
                let preview = Rect {
                    x: gap.x + gap.width,
                    y: rail.y,
                    width: (layout.card.x + layout.card.width
                        - gap.x
                        - gap.width
                        - layout.padding_x * 0.72)
                        .max(96.0),
                    height: rail.height,
                };
                SessionSwitcherSplitColumns { rail, preview, gap }
            });
            let preview_left = columns.preview.x + layout.padding_x * 0.95;
            let preview_right = (columns.preview.x + columns.preview.width
                - layout.padding_x * 0.85)
                .min(right as f32)
                .max(preview_left);
            // Anchor the preview to the top of its pane. Positioning it at
            // the "Preview" header's row offset within the combined line
            // list pushes it below the visible card whenever the session
            // list is long, leaving the pane empty.
            let preview_top = columns.preview.y + 8.0;
            let preview_bottom = (columns.preview.y + columns.preview.height - 8.0)
                .min(draft_top)
                .max(preview_top + 1.0);
            if preview_right > preview_left {
                areas.push(TextArea {
                    buffer: preview_buffer,
                    left: preview_left,
                    top: preview_top,
                    scale: 1.0,
                    bounds: TextBounds {
                        left: preview_left as i32,
                        top: preview_top as i32,
                        right: preview_right as i32,
                        bottom: preview_bottom as i32,
                    },
                    default_color: text_color(ASSISTANT_TEXT_COLOR),
                });
            }
        }
    }

    areas
}

pub(crate) fn visualize_composer_whitespace(text: &str) -> String {
    text.to_string()
}

pub(crate) fn desktop_header_version_label() -> String {
    format!(
        "{} · {}",
        crate::DESKTOP_RELEASE_CHANNEL,
        desktop_app_directory_label()
    )
}

pub(crate) fn desktop_app_directory_label() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|path| {
            path.parent()
                .map(|directory| directory.display().to_string())
        })
        .unwrap_or_else(|| "unknown app directory".to_string())
}

pub(crate) fn fresh_welcome_version_label() -> String {
    desktop_header_version_label()
}

pub(crate) fn fresh_welcome_version_font_size() -> f32 {
    (single_session_typography().meta_size * 0.58).clamp(11.0, 14.0)
}

pub(crate) fn fresh_welcome_version_top_for_scale(size: PhysicalSize<u32>, ui_scale: f32) -> f32 {
    handwritten_welcome_bounds_for_phrase_with_scale(size, handwritten_welcome_phrase(0), ui_scale)
        .1[1]
        + fresh_welcome_version_gap_for_scale(ui_scale)
}

pub(crate) fn fresh_welcome_version_gap_for_scale(ui_scale: f32) -> f32 {
    (fresh_welcome_version_font_size() * ui_scale * 2.25).max(30.0 * ui_scale)
}

pub(crate) fn fresh_welcome_version_left(
    label: &str,
    size: PhysicalSize<u32>,
    font_size: f32,
) -> f32 {
    let estimated_width = label.chars().count() as f32 * font_size * 0.58;
    ((size.width as f32 - estimated_width) * 0.5).max(PANEL_TITLE_LEFT_PADDING)
}

pub(crate) fn text_color(color: [f32; 4]) -> TextColor {
    TextColor::rgba(
        (color[0].clamp(0.0, 1.0) * 255.0).round() as u8,
        (color[1].clamp(0.0, 1.0) * 255.0).round() as u8,
        (color[2].clamp(0.0, 1.0) * 255.0).round() as u8,
        (color[3].clamp(0.0, 1.0) * 255.0).round() as u8,
    )
}
