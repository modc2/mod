//! Inline-widget card chrome rendering: the widget card itself, preview panes, structured chrome, model picker and command rows, session switcher panels, reflow rows, selection highlight, and their palette constants.

use super::*;

pub(crate) fn fresh_welcome_inline_widget_visual_offset(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
) -> f32 {
    if app.render_inline_widget_line_count() == 0 {
        return 0.0;
    }

    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let visual_bottom = fresh_welcome_visual_bottom_for_scale(size, app.text_scale());
    let gap = fresh_welcome_inline_widget_gap_for_scale(app.text_scale());
    let draft_top = single_session_draft_top_for_app(app, size);
    let inline_height = inline_widget_visible_text_height(app).max(line_height);
    let available = (draft_top - visual_bottom - gap).max(0.0);

    if inline_height <= available {
        0.0
    } else {
        -(inline_height - available)
    }
}

/// Resolved inline-widget card geometry for headless captures and quality
/// metrics: (card_rect, text_top, line_height, visible_text_bottom,
/// visible_text_right).
pub(crate) fn inline_widget_capture_geometry(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    total_lines: usize,
) -> Option<(Rect, f32, f32, f32, f32)> {
    let line_count = app.render_inline_widget_visible_line_count();
    if line_count == 0 {
        return None;
    }
    let progress = app.render_inline_widget_reveal_progress().clamp(0.0, 1.0);
    if progress <= 0.001 {
        return None;
    }
    let kind = app.render_inline_widget_kind();
    let typography = single_session_typography_for_scale(app.text_scale());
    let session_layout = single_session_layout_for_total_lines(app, size, total_lines);
    let body_bottom = session_layout.body_bottom();
    let welcome_chrome_offset_pixels = welcome_timeline_visual_offset_pixels(app, size, 0.0);
    let welcome_chrome_visible =
        welcome_timeline_chrome_visible(app, size, welcome_chrome_offset_pixels);
    let inline_bottom_limit =
        inline_widget_bottom_limit_for_layout(app, session_layout, welcome_chrome_visible);
    let target_top = inline_widget_target_top(
        size,
        kind,
        app.text_scale(),
        body_bottom,
        welcome_chrome_visible,
        welcome_chrome_offset_pixels,
    );
    let inline_lines = app.render_inline_widget_styled_lines();
    let layout = inline_widget_card_layout_with_bottom_limit(
        size,
        kind,
        &typography,
        line_count,
        inline_widget_text_width_for_lines(kind, &inline_lines, size, app.text_scale()),
        target_top,
        progress,
        inline_bottom_limit,
    )?;
    Some((
        layout.card,
        layout.text_top,
        inline_widget_line_height(kind, &typography),
        layout.visible_text_bottom,
        layout.visible_text_right,
    ))
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_single_session_inline_widget_card(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    welcome_chrome_offset_pixels: f32,
    total_lines: usize,
    inline_selection_motion: Option<&InlineWidgetSelectionMotionFrame>,
    inline_list_reflow_motion: Option<&InlineWidgetListReflowMotionFrame>,
    inline_preview_pane_motion: Option<&InlineWidgetPreviewPaneMotionFrame>,
) {
    let line_count = app.render_inline_widget_visible_line_count();
    if line_count == 0 {
        return;
    }

    let progress = app.render_inline_widget_reveal_progress().clamp(0.0, 1.0);
    if progress <= 0.001 {
        return;
    }

    let typography = single_session_typography_for_scale(app.text_scale());
    let session_layout = single_session_layout_for_total_lines(app, size, total_lines);
    let body_bottom = session_layout.body_bottom();
    let welcome_chrome_visible =
        welcome_timeline_chrome_visible(app, size, welcome_chrome_offset_pixels);
    let inline_bottom_limit =
        inline_widget_bottom_limit_for_layout(app, session_layout, welcome_chrome_visible);
    let target_top = inline_widget_target_top(
        size,
        app.render_inline_widget_kind(),
        app.text_scale(),
        body_bottom,
        welcome_chrome_visible,
        welcome_chrome_offset_pixels,
    );
    let inline_lines = app.render_inline_widget_styled_lines();
    let Some(layout) = inline_widget_card_layout_with_bottom_limit(
        size,
        app.render_inline_widget_kind(),
        &typography,
        line_count,
        inline_widget_text_width_for_lines(
            app.render_inline_widget_kind(),
            &inline_lines,
            size,
            app.text_scale(),
        ),
        target_top,
        progress,
        inline_bottom_limit,
    ) else {
        return;
    };

    if app.render_inline_widget_kind().is_some() {
        let kind = app.render_inline_widget_kind();
        let card_style = inline_widget_card_style(kind);
        let card_rect = if kind == Some(InlineWidgetKind::ModelPicker) {
            inset_rect(layout.card, -6.0)
        } else {
            layout.card
        };
        let card_radius = if kind == Some(InlineWidgetKind::ModelPicker) {
            layout.radius + 2.0
        } else {
            layout.radius
        };
        push_rounded_rect(
            vertices,
            Rect {
                x: card_rect.x,
                y: card_rect.y + 5.0,
                width: card_rect.width,
                height: card_rect.height,
            },
            card_radius + 2.0,
            with_alpha(
                INLINE_WIDGET_CARD_SHADOW_COLOR,
                INLINE_WIDGET_CARD_SHADOW_COLOR[3] * progress,
            ),
            size,
        );
        push_rounded_rect(
            vertices,
            card_rect,
            card_radius,
            with_alpha(card_style.border, card_style.border[3] * progress),
            size,
        );
        push_rounded_rect(
            vertices,
            inset_rect(card_rect, 1.0),
            (card_radius - 1.0).max(1.0),
            with_alpha(card_style.background, card_style.background[3] * progress),
            size,
        );
        if kind != Some(InlineWidgetKind::ModelPicker) {
            push_rounded_rect(
                vertices,
                Rect {
                    x: card_rect.x + 1.5,
                    y: card_rect.y + 1.5,
                    width: 3.0,
                    height: (card_rect.height - 3.0).max(0.0),
                },
                2.0,
                with_alpha(card_style.accent, card_style.accent[3] * progress),
                size,
            );
        }
        push_rounded_rect(
            vertices,
            Rect {
                x: card_rect.x + 8.0,
                y: card_rect.y + 1.5,
                width: (card_rect.width - 16.0).max(0.0),
                height: 1.0,
            },
            0.5,
            with_alpha(card_style.highlight, card_style.highlight[3] * progress),
            size,
        );
    }

    if app.render_inline_widget_kind() == Some(InlineWidgetKind::ModelPicker) {
        push_single_session_inline_widget_structured_chrome(
            vertices,
            app,
            app.render_inline_widget_kind(),
            &inline_lines,
            line_count,
            &typography,
            &layout,
            progress,
            size,
        );
    } else {
        push_single_session_inline_widget_preview_panes(
            vertices,
            app.render_inline_widget_kind(),
            &inline_lines,
            line_count,
            &typography,
            &layout,
            progress,
            inline_preview_pane_motion,
            size,
        );
    }

    push_single_session_inline_widget_list_reflow(
        vertices,
        app.render_inline_widget_kind(),
        &inline_lines,
        line_count,
        &typography,
        &layout,
        progress,
        inline_list_reflow_motion,
        size,
    );

    push_single_session_inline_widget_selection(
        vertices,
        app.render_inline_widget_kind(),
        &inline_lines,
        line_count,
        &typography,
        &layout,
        progress,
        inline_selection_motion,
        size,
    );
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineWidgetPreviewPaneGeometry {
    pub(crate) sessions: Rect,
    pub(crate) preview: Rect,
    pub(crate) radius: f32,
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_single_session_inline_widget_preview_panes(
    vertices: &mut Vec<Vertex>,
    kind: Option<InlineWidgetKind>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    inline_preview_pane_motion: Option<&InlineWidgetPreviewPaneMotionFrame>,
    size: PhysicalSize<u32>,
) {
    let Some(geometry) =
        inline_widget_preview_pane_geometry(kind, inline_lines, line_count, typography, layout)
    else {
        return;
    };
    let visual = inline_preview_pane_motion
        .and_then(InlineWidgetPreviewPaneMotionFrame::visual)
        .unwrap_or(InlineWidgetPreviewPaneVisual {
            focus_pane_position: inline_widget_preview_pane_target(kind, inline_lines, line_count)
                .map(|target| target.focus_pane as f32)
                .unwrap_or_default(),
            preview_opacity: 1.0,
            preview_y_offset_pixels: 0.0,
        });
    let alpha = reveal_progress.clamp(0.0, 1.0);
    if alpha <= 0.001 {
        return;
    }

    for pane in [geometry.sessions, geometry.preview] {
        push_rounded_rect(
            vertices,
            pane,
            geometry.radius,
            with_alpha(
                INLINE_WIDGET_PREVIEW_PANE_BACKGROUND_COLOR,
                INLINE_WIDGET_PREVIEW_PANE_BACKGROUND_COLOR[3] * alpha,
            ),
            size,
        );
        push_rounded_rect(
            vertices,
            inset_rect(pane, 0.8),
            (geometry.radius - 1.0).max(1.0),
            with_alpha(
                INLINE_WIDGET_PREVIEW_PANE_BORDER_COLOR,
                INLINE_WIDGET_PREVIEW_PANE_BORDER_COLOR[3] * alpha,
            ),
            size,
        );
    }

    let content_rect = Rect {
        x: geometry.preview.x + 5.0,
        y: geometry.preview.y + 4.0 + visual.preview_y_offset_pixels,
        width: (geometry.preview.width - 10.0).max(0.0),
        height: (geometry.preview.height - 8.0).max(0.0),
    };
    push_rounded_rect(
        vertices,
        content_rect,
        (geometry.radius - 2.0).max(1.0),
        with_alpha(
            INLINE_WIDGET_PREVIEW_PANE_CONTENT_COLOR,
            INLINE_WIDGET_PREVIEW_PANE_CONTENT_COLOR[3] * alpha * visual.preview_opacity,
        ),
        size,
    );

    let focus_rect = interpolate_inline_widget_preview_pane_rect(
        geometry.sessions,
        geometry.preview,
        visual.focus_pane_position,
    );
    push_rounded_rect(
        vertices,
        inset_rect(focus_rect, -1.4),
        geometry.radius + 1.4,
        with_alpha(
            INLINE_WIDGET_PREVIEW_PANE_FOCUS_COLOR,
            INLINE_WIDGET_PREVIEW_PANE_FOCUS_COLOR[3] * alpha,
        ),
        size,
    );
}

pub(crate) fn inline_widget_preview_pane_geometry(
    kind: Option<InlineWidgetKind>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
) -> Option<InlineWidgetPreviewPaneGeometry> {
    if kind != Some(InlineWidgetKind::SessionSwitcher) {
        return None;
    }
    if line_count > 0
        && let Some(columns) = session_switcher_split_columns(layout)
    {
        return Some(InlineWidgetPreviewPaneGeometry {
            sessions: columns.rail,
            preview: columns.preview,
            radius: 13.0,
        });
    }
    let visible_len = line_count.min(inline_lines.len());
    let visible_lines = &inline_lines[..visible_len];
    let header_line = visible_lines
        .iter()
        .position(|line| line.text.contains("sessions") && line.text.contains("preview"))?;
    let end_line = visible_lines
        .iter()
        .enumerate()
        .skip(header_line + 1)
        .find_map(|(index, line)| {
            (line.text.contains('╰') || line.text.contains("preview lines ")).then_some(index)
        })
        .unwrap_or(visible_len);

    let line_height = inline_widget_line_height(kind, typography);
    let top = layout.text_top + header_line as f32 * line_height - 2.0;
    let bottom = (layout.text_top + end_line as f32 * line_height + 4.0)
        .min(layout.visible_text_bottom)
        .max(top + line_height);
    let inner_left = layout.card.x + layout.padding_x * 0.72;
    let inner_right = layout.card.x + layout.card.width - layout.padding_x * 0.72;
    let inner_width = (inner_right - inner_left).max(1.0);
    let gap = 10.0_f32.min(inner_width * 0.08);
    let sessions_width = ((inner_width - gap) * 0.42).max(1.0);
    let preview_width = (inner_width - gap - sessions_width).max(1.0);
    let height = bottom - top;

    Some(InlineWidgetPreviewPaneGeometry {
        sessions: Rect {
            x: inner_left,
            y: top,
            width: sessions_width,
            height,
        },
        preview: Rect {
            x: inner_left + sessions_width + gap,
            y: top,
            width: preview_width,
            height,
        },
        radius: 13.0,
    })
}

pub(crate) fn interpolate_inline_widget_preview_pane_rect(
    sessions: Rect,
    preview: Rect,
    position: f32,
) -> Rect {
    let position = position.clamp(0.0, 1.0);
    Rect {
        x: lerp_f32(sessions.x, preview.x, position),
        y: lerp_f32(sessions.y, preview.y, position),
        width: lerp_f32(sessions.width, preview.width, position),
        height: lerp_f32(sessions.height, preview.height, position),
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_single_session_inline_widget_structured_chrome(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    kind: Option<InlineWidgetKind>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    match kind {
        Some(InlineWidgetKind::ModelPicker) => {
            push_inline_command_row_cards(
                vertices,
                kind,
                inline_lines,
                line_count,
                typography,
                layout,
                reveal_progress,
                size,
            );
            push_model_picker_component_chrome(
                vertices,
                app,
                kind,
                inline_lines,
                line_count,
                typography,
                layout,
                reveal_progress,
                size,
            );
        }
        Some(InlineWidgetKind::SessionSwitcher) => {
            push_session_switcher_section_panels(
                vertices,
                inline_lines,
                line_count,
                typography,
                layout,
                reveal_progress,
                size,
            );
            push_session_switcher_preview_bubbles(
                vertices,
                inline_lines,
                line_count,
                typography,
                layout,
                reveal_progress,
                size,
            );
            push_inline_command_row_cards(
                vertices,
                kind,
                inline_lines,
                line_count,
                typography,
                layout,
                reveal_progress,
                size,
            );
        }
        _ => {}
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_model_picker_component_chrome(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    kind: Option<InlineWidgetKind>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    let line_height = inline_widget_line_height(kind, typography);
    let alpha = reveal_progress.clamp(0.0, 1.0);

    let runs = inline_widget_list_row_runs(kind, inline_lines, line_count);
    let (_, visible_window) = app
        .model_picker
        .visible_row_window(MODEL_PICKER_INLINE_ROW_LIMIT);
    let current = app.model_picker.current_model.as_deref();
    for (run, choice_index) in runs.iter().zip(visible_window.iter()) {
        let Some(choice) = app.model_picker.choices.get(*choice_index) else {
            continue;
        };
        let row_top =
            layout.text_top + run.line as f32 * line_height - INLINE_COMMAND_MODEL_ROW_GAP_Y;
        // Two text sub-lines per row; center the icon between them.
        let center_y = row_top + line_height * 1.05;
        if center_y + 11.0 > layout.visible_text_bottom {
            continue;
        }
        let is_current = Some(choice.model.as_str()) == current;
        // Circular icon badge, centered in the row text's 4-space gutter so
        // icon and label stay grouped at every scale.
        let advance = inline_widget_font_size(kind, typography) * 0.6;
        let badge_radius = 15.0_f32.min(advance * 1.9).max(9.0);
        let badge_cx = layout.text_left + advance * 2.0;
        let badge_bg = if is_current {
            [0.815, 0.935, 0.870, 0.95]
        } else {
            [0.880, 0.905, 0.962, 0.92]
        };
        let badge_border = if is_current {
            [0.220, 0.560, 0.420, 0.45]
        } else {
            [0.180, 0.300, 0.560, 0.30]
        };
        push_rounded_rect(
            vertices,
            Rect {
                x: badge_cx - badge_radius,
                y: center_y - badge_radius,
                width: badge_radius * 2.0,
                height: badge_radius * 2.0,
            },
            badge_radius,
            with_alpha(badge_bg, badge_bg[3] * alpha),
            size,
        );
        push_rounded_rect_border(
            vertices,
            Rect {
                x: badge_cx - badge_radius,
                y: center_y - badge_radius,
                width: badge_radius * 2.0,
                height: badge_radius * 2.0,
            },
            badge_radius,
            1.0,
            with_alpha(badge_border, badge_border[3] * alpha),
            size,
        );
        let icon = if is_current {
            LucideIcon::CircleCheck
        } else {
            LucideIcon::Bot
        };
        let icon_color = if is_current {
            [0.045, 0.400, 0.235, 0.98]
        } else {
            [0.085, 0.215, 0.520, 0.92]
        };
        let icon_size = badge_radius * 1.2;
        push_lucide_icon(
            vertices,
            icon,
            Rect {
                x: badge_cx - icon_size * 0.5,
                y: center_y - icon_size * 0.5,
                width: icon_size,
                height: icon_size,
            },
            with_alpha(icon_color, icon_color[3] * alpha),
            1.8,
            size,
        );
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_inline_command_row_cards(
    vertices: &mut Vec<Vertex>,
    kind: Option<InlineWidgetKind>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    let line_height = inline_widget_line_height(kind, typography);
    for run in inline_widget_list_row_runs(kind, inline_lines, line_count) {
        let primary_text = inline_lines
            .get(run.line)
            .map(|line| line.text.as_str())
            .unwrap_or_default();
        let selected = inline_lines
            .get(run.line)
            .is_some_and(|line| line.style == SingleSessionLineStyle::OverlaySelection);
        let palette = inline_command_row_palette(kind, primary_text, selected);
        push_inline_command_row_card(
            vertices,
            kind,
            run.line,
            run.line_span,
            palette,
            line_height,
            layout,
            reveal_progress,
            size,
        );
        push_inline_command_row_icon(
            vertices,
            kind,
            run.line,
            palette,
            line_height,
            layout,
            reveal_progress,
            size,
        );

        if selected && !matches!(kind, Some(InlineWidgetKind::ModelPicker)) {
            push_inline_command_current_chip(
                vertices,
                kind,
                primary_text,
                run.line,
                line_height,
                layout,
                reveal_progress,
                size,
            );
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_inline_command_row_card(
    vertices: &mut Vec<Vertex>,
    kind: Option<InlineWidgetKind>,
    line: usize,
    line_span: usize,
    palette: InlineCommandRowPalette,
    line_height: f32,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    let is_session = matches!(kind, Some(InlineWidgetKind::SessionSwitcher));
    let is_model = matches!(kind, Some(InlineWidgetKind::ModelPicker));
    let row_top = layout.text_top
        + line as f32 * line_height
        + if is_session {
            INLINE_COMMAND_SESSION_ROW_TOP_INSET
        } else if is_model {
            -INLINE_COMMAND_MODEL_ROW_GAP_Y
        } else {
            -INLINE_COMMAND_ROW_GAP_Y
        };
    let row_height = (line_span as f32 * line_height
        + if is_session {
            -INLINE_COMMAND_SESSION_ROW_BOTTOM_INSET
        } else if is_model {
            INLINE_COMMAND_MODEL_ROW_GAP_Y * 1.65
        } else {
            INLINE_COMMAND_ROW_GAP_Y * 1.4
        })
    .max(line_height * 0.9);
    let visible_height = (layout.visible_text_bottom - row_top).min(row_height);
    let row_width = (layout.card.width - INLINE_COMMAND_ROW_INSET_X * 2.0).max(0.0);
    // A row whose visible area is only a sliver at the clip edge reads as a
    // rendering glitch (a strip of background peeking from under the card
    // bottom). Draw the row only when most of it fits.
    if visible_height < row_height * 0.55 || row_width <= 12.0 {
        return;
    }

    let rect = if is_session {
        session_switcher_split_columns(layout)
            .map(|columns| Rect {
                x: columns.rail.x + INLINE_COMMAND_ROW_INSET_X,
                y: row_top,
                width: (columns.rail.width - INLINE_COMMAND_ROW_INSET_X * 2.0).max(0.0),
                height: visible_height,
            })
            .unwrap_or(Rect {
                x: layout.card.x + INLINE_COMMAND_ROW_INSET_X,
                y: row_top,
                width: row_width,
                height: visible_height,
            })
    } else {
        Rect {
            x: layout.card.x + INLINE_COMMAND_ROW_INSET_X,
            y: row_top,
            width: row_width,
            height: visible_height,
        }
    };
    if rect.width <= 12.0 {
        return;
    }
    push_rounded_rect(
        vertices,
        rect,
        INLINE_COMMAND_ROW_RADIUS,
        with_alpha(palette.fill, palette.fill[3] * reveal_progress),
        size,
    );
    push_rounded_rect_border(
        vertices,
        rect,
        INLINE_COMMAND_ROW_RADIUS,
        1.0,
        with_alpha(palette.border, palette.border[3] * reveal_progress),
        size,
    );
    if palette.selected && !is_model {
        push_rounded_rect(
            vertices,
            Rect {
                x: rect.x + 6.0,
                y: rect.y + 7.0,
                width: 3.0,
                height: (rect.height - 14.0).max(1.0),
            },
            2.0,
            with_alpha(palette.accent, palette.accent[3] * reveal_progress),
            size,
        );
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_inline_command_row_icon(
    vertices: &mut Vec<Vertex>,
    kind: Option<InlineWidgetKind>,
    line: usize,
    palette: InlineCommandRowPalette,
    line_height: f32,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    let Some(icon) = palette.icon else {
        return;
    };
    let is_session = matches!(kind, Some(InlineWidgetKind::SessionSwitcher));
    let is_model = matches!(kind, Some(InlineWidgetKind::ModelPicker));
    let icon_size = if is_session {
        19.0
    } else if is_model {
        18.0
    } else {
        17.0
    };
    let top = layout.text_top
        + line as f32 * line_height
        + if is_session {
            10.0
        } else if is_model {
            6.0
        } else {
            4.0
        };
    let left = if is_session {
        session_switcher_split_columns(layout)
            .map(|columns| columns.rail.x + INLINE_COMMAND_ROW_INSET_X + 10.0)
            .unwrap_or(layout.card.x + INLINE_COMMAND_ROW_INSET_X + 10.0)
    } else if is_model {
        layout.card.x + INLINE_COMMAND_ROW_INSET_X + 13.0
    } else {
        layout.card.x + layout.card.width - INLINE_COMMAND_ROW_INSET_X - icon_size - 10.0
    };
    if top + icon_size > layout.visible_text_bottom || left + icon_size > layout.visible_text_right
    {
        return;
    }
    if is_session || is_model {
        let halo = Rect {
            x: left - 5.0,
            y: top - 5.0,
            width: icon_size + 10.0,
            height: icon_size + 10.0,
        };
        push_rounded_rect(
            vertices,
            halo,
            halo.height * 0.5,
            with_alpha(
                palette.icon_background,
                palette.icon_background[3] * reveal_progress,
            ),
            size,
        );
    }
    push_lucide_icon(
        vertices,
        icon,
        Rect {
            x: left,
            y: top,
            width: icon_size,
            height: icon_size,
        },
        with_alpha(palette.icon_color, palette.icon_color[3] * reveal_progress),
        if is_session { 1.75 } else { 1.55 },
        size,
    );
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_inline_command_current_chip(
    vertices: &mut Vec<Vertex>,
    kind: Option<InlineWidgetKind>,
    primary_text: &str,
    line: usize,
    line_height: f32,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    let chip_width = (layout.card.width * 0.16).clamp(54.0, 98.0);
    let chip_height = (line_height * 0.74).clamp(14.0, 22.0);
    let x = if matches!(kind, Some(InlineWidgetKind::SessionSwitcher)) {
        session_switcher_split_columns(layout)
            .map(|columns| {
                columns.rail.x + columns.rail.width - chip_width - INLINE_COMMAND_ROW_INSET_X - 10.0
            })
            .unwrap_or(
                layout.card.x + layout.card.width - chip_width - INLINE_COMMAND_ROW_INSET_X - 10.0,
            )
    } else {
        layout.card.x + layout.card.width - chip_width - INLINE_COMMAND_ROW_INSET_X - 10.0
    };
    let y = layout.text_top + line as f32 * line_height + (line_height - chip_height) * 0.5;
    if x <= layout.text_left || y + chip_height > layout.visible_text_bottom {
        return;
    }
    push_rounded_rect(
        vertices,
        Rect {
            x,
            y,
            width: chip_width,
            height: chip_height,
        },
        chip_height * 0.5,
        with_alpha(
            INLINE_COMMAND_CHIP_COLOR,
            INLINE_COMMAND_CHIP_COLOR[3] * reveal_progress,
        ),
        size,
    );
    let chip_icon = if matches!(kind, Some(InlineWidgetKind::SessionSwitcher))
        && resume_session_row_is_current(primary_text)
    {
        LucideIcon::BookmarkCheck
    } else {
        LucideIcon::CircleCheck
    };
    let icon_size = chip_height * 0.62;
    push_lucide_icon(
        vertices,
        chip_icon,
        Rect {
            x: x + (chip_width - icon_size) * 0.5,
            y: y + (chip_height - icon_size) * 0.5,
            width: icon_size,
            height: icon_size,
        },
        with_alpha(
            INLINE_COMMAND_CHIP_ICON_COLOR,
            INLINE_COMMAND_CHIP_ICON_COLOR[3] * reveal_progress,
        ),
        1.35,
        size,
    );
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct InlineCommandRowPalette {
    pub(crate) fill: [f32; 4],
    pub(crate) border: [f32; 4],
    pub(crate) accent: [f32; 4],
    pub(crate) icon_background: [f32; 4],
    pub(crate) icon_color: [f32; 4],
    pub(crate) icon: Option<LucideIcon>,
    pub(crate) selected: bool,
}

pub(crate) fn inline_command_row_palette(
    kind: Option<InlineWidgetKind>,
    primary_text: &str,
    selected: bool,
) -> InlineCommandRowPalette {
    if matches!(kind, Some(InlineWidgetKind::SessionSwitcher)) {
        return resume_session_row_palette(primary_text, selected);
    }

    InlineCommandRowPalette {
        fill: if selected {
            INLINE_COMMAND_ROW_SELECTED_COLOR
        } else {
            INLINE_COMMAND_ROW_BACKGROUND_COLOR
        },
        border: if selected {
            INLINE_COMMAND_ROW_SELECTED_BORDER_COLOR
        } else {
            INLINE_COMMAND_ROW_BORDER_COLOR
        },
        accent: if matches!(kind, Some(InlineWidgetKind::ModelPicker)) {
            MODEL_PICKER_ROW_ACCENT_COLOR
        } else {
            INLINE_COMMAND_ROW_ACCENT_COLOR
        },
        icon_background: INLINE_COMMAND_MODEL_ICON_BACKGROUND_COLOR,
        icon_color: INLINE_COMMAND_MODEL_ICON_COLOR,
        icon: None,
        selected,
    }
}

pub(crate) fn resume_session_row_palette(
    primary_text: &str,
    selected: bool,
) -> InlineCommandRowPalette {
    let status = resume_session_status_from_row(primary_text);
    let (fill, border, accent, icon_background, icon_color, icon) = match status {
        "active" => (
            RESUME_SESSION_ACTIVE_FILL,
            RESUME_SESSION_ACTIVE_BORDER,
            RESUME_SESSION_ACTIVE_ACCENT,
            RESUME_SESSION_ACTIVE_ICON_BACKGROUND,
            RESUME_SESSION_ACTIVE_ICON_COLOR,
            LucideIcon::CirclePlay,
        ),
        "closed" | "done" | "finished" => (
            RESUME_SESSION_CLOSED_FILL,
            RESUME_SESSION_CLOSED_BORDER,
            RESUME_SESSION_CLOSED_ACCENT,
            RESUME_SESSION_CLOSED_ICON_BACKGROUND,
            RESUME_SESSION_CLOSED_ICON_COLOR,
            LucideIcon::CircleCheck,
        ),
        "crashed" | "failed" | "error" => (
            RESUME_SESSION_ERROR_FILL,
            RESUME_SESSION_ERROR_BORDER,
            RESUME_SESSION_ERROR_ACCENT,
            RESUME_SESSION_ERROR_ICON_BACKGROUND,
            RESUME_SESSION_ERROR_ICON_COLOR,
            LucideIcon::CircleX,
        ),
        "compacted" => (
            RESUME_SESSION_SPECIAL_FILL,
            RESUME_SESSION_SPECIAL_BORDER,
            RESUME_SESSION_SPECIAL_ACCENT,
            RESUME_SESSION_SPECIAL_ICON_BACKGROUND,
            RESUME_SESSION_SPECIAL_ICON_COLOR,
            LucideIcon::Package,
        ),
        "reloaded" => (
            RESUME_SESSION_RELOADED_FILL,
            RESUME_SESSION_RELOADED_BORDER,
            RESUME_SESSION_RELOADED_ACCENT,
            RESUME_SESSION_RELOADED_ICON_BACKGROUND,
            RESUME_SESSION_RELOADED_ICON_COLOR,
            LucideIcon::RefreshCw,
        ),
        _ => (
            RESUME_SESSION_NEUTRAL_FILL,
            RESUME_SESSION_NEUTRAL_BORDER,
            RESUME_SESSION_NEUTRAL_ACCENT,
            RESUME_SESSION_NEUTRAL_ICON_BACKGROUND,
            RESUME_SESSION_NEUTRAL_ICON_COLOR,
            LucideIcon::MessageSquare,
        ),
    };

    InlineCommandRowPalette {
        fill: if selected {
            mix_rgba(fill, RESUME_SESSION_SELECTED_TINT, 0.58)
        } else {
            fill
        },
        border: if selected {
            mix_rgba(border, RESUME_SESSION_SELECTED_BORDER_TINT, 0.46)
        } else {
            border
        },
        accent,
        icon_background: if selected {
            mix_rgba(icon_background, RESUME_SESSION_SELECTED_TINT, 0.28)
        } else {
            icon_background
        },
        icon_color,
        icon: Some(icon),
        selected,
    }
}

pub(crate) fn resume_session_status_from_row(primary_text: &str) -> &str {
    primary_text
        .trim_start()
        .split_once(" session ·")
        .map(|(status, _)| status.trim())
        .unwrap_or("unknown")
}

pub(crate) fn resume_session_row_is_current(primary_text: &str) -> bool {
    primary_text.contains(" current ·")
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct SessionSwitcherSplitColumns {
    pub(crate) rail: Rect,
    pub(crate) preview: Rect,
    pub(crate) gap: Rect,
}

/// Rail width for the session-switcher fallback layout used when the card is too
/// narrow for the full split layout.
///
/// Guards against narrow cards: the preferred minimum rail width is 220px, but
/// `card_width * 0.55` (the max) can drop below that on small windows. Passing
/// `min > max` to `f32::clamp` panics, which previously crashed the desktop app
/// on resume into a narrow window. Cap the minimum at the available max so the
/// rail just shrinks instead.
pub(crate) fn session_switcher_fallback_rail_width(card_width: f32) -> f32 {
    let card_width = card_width.max(0.0);
    let rail_max = (card_width * 0.55).max(1.0);
    let rail_min = 220.0_f32.min(rail_max);
    (card_width * 0.38).clamp(rail_min, rail_max)
}

pub(crate) fn session_switcher_split_columns(
    layout: &InlineWidgetCardLayout,
) -> Option<SessionSwitcherSplitColumns> {
    let content_x = layout.card.x + layout.padding_x * 0.72;
    let content_width = (layout.card.width - layout.padding_x * 1.44).max(0.0);
    if content_width <= 260.0 {
        return None;
    }

    let gap_width = (content_width * 0.018).clamp(9.0, 15.0);
    // With the compact switcher font the rail needs a larger share to show
    // meaningful session titles next to the wrapped preview pane.
    let preferred_rail_width = (content_width * 0.46).clamp(280.0, 430.0);
    let max_rail_width = (content_width - gap_width - 210.0)
        .max(content_width * 0.42)
        .min(content_width - gap_width - 96.0);
    let rail_width = preferred_rail_width
        .min(max_rail_width)
        .max((content_width * 0.32).min(content_width - gap_width - 96.0));
    let preview_width = content_width - rail_width - gap_width;
    if rail_width <= 96.0 || preview_width <= 96.0 {
        return None;
    }

    let y = layout.card.y + layout.padding_x * 0.18;
    let height = (layout.card.height - layout.padding_x * 0.36).max(1.0);
    let rail = Rect {
        x: content_x,
        y,
        width: rail_width,
        height,
    };
    let gap = Rect {
        x: rail.x + rail.width,
        y,
        width: gap_width,
        height,
    };
    let preview = Rect {
        x: gap.x + gap.width,
        y,
        width: preview_width,
        height,
    };
    Some(SessionSwitcherSplitColumns { rail, preview, gap })
}

pub(crate) fn session_switcher_split_panel_rects(
    layout: &InlineWidgetCardLayout,
    top: f32,
    height: f32,
) -> Option<(Rect, Rect, Rect)> {
    let columns = session_switcher_split_columns(layout)?;
    let bottom = (top + height).min(layout.visible_text_bottom);
    if bottom <= top + 8.0 {
        return None;
    }
    let height = bottom - top;
    Some((
        Rect {
            y: top,
            height,
            ..columns.rail
        },
        Rect {
            y: top,
            height,
            ..columns.preview
        },
        Rect {
            y: top,
            height,
            ..columns.gap
        },
    ))
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_session_switcher_section_panels(
    vertices: &mut Vec<Vertex>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    let visible_len = line_count.min(inline_lines.len());
    let Some(sessions_header) = inline_lines[..visible_len]
        .iter()
        .position(|line| line.text.starts_with("Recent sessions"))
    else {
        return;
    };
    let preview_header = inline_lines[..visible_len]
        .iter()
        .position(|line| line.text.starts_with("Preview"));
    let sessions_end = preview_header
        .unwrap_or(visible_len)
        .max(sessions_header + 1);
    let line_height =
        inline_widget_line_height(Some(InlineWidgetKind::SessionSwitcher), typography);

    let top = layout.text_top + sessions_header as f32 * line_height - 7.0;
    let height = (visible_len - sessions_header) as f32 * line_height + 12.0;
    if let Some((rail, preview, gap)) = session_switcher_split_panel_rects(layout, top, height) {
        push_rounded_rect(
            vertices,
            rail,
            INLINE_COMMAND_ROW_RADIUS + 4.0,
            with_alpha(
                INLINE_COMMAND_SECTION_BACKGROUND_COLOR,
                INLINE_COMMAND_SECTION_BACKGROUND_COLOR[3] * reveal_progress,
            ),
            size,
        );
        push_rounded_rect(
            vertices,
            preview,
            INLINE_COMMAND_ROW_RADIUS + 4.0,
            with_alpha(
                INLINE_COMMAND_PREVIEW_BACKGROUND_COLOR,
                INLINE_COMMAND_PREVIEW_BACKGROUND_COLOR[3] * reveal_progress,
            ),
            size,
        );
        push_rounded_rect(
            vertices,
            Rect {
                x: gap.x + gap.width * 0.5 - 0.5,
                y: gap.y + 9.0,
                width: 1.0,
                height: (gap.height - 18.0).max(1.0),
            },
            0.5,
            with_alpha(
                INLINE_COMMAND_SPLIT_DIVIDER_COLOR,
                INLINE_COMMAND_SPLIT_DIVIDER_COLOR[3] * reveal_progress,
            ),
            size,
        );
    } else {
        push_inline_command_section_panel(
            vertices,
            sessions_header,
            sessions_end,
            line_height,
            layout,
            INLINE_COMMAND_SECTION_BACKGROUND_COLOR,
            reveal_progress,
            size,
        );
        if let Some(preview_header) = preview_header {
            push_inline_command_section_panel(
                vertices,
                preview_header,
                visible_len,
                line_height,
                layout,
                INLINE_COMMAND_PREVIEW_BACKGROUND_COLOR,
                reveal_progress,
                size,
            );
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_session_switcher_preview_bubbles(
    vertices: &mut Vec<Vertex>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    let visible_len = line_count.min(inline_lines.len());
    let Some(preview_header) = inline_lines[..visible_len]
        .iter()
        .position(|line| line.text.starts_with("Preview"))
    else {
        return;
    };
    let line_height =
        inline_widget_line_height(Some(InlineWidgetKind::SessionSwitcher), typography);
    let radius = (line_height * 0.12).clamp(2.5, 4.5);
    let y = layout.text_top + preview_header as f32 * line_height + line_height * 0.44;
    let right = layout.card.x + layout.card.width - layout.padding_x * 0.72;
    if y + radius > layout.visible_text_bottom {
        return;
    }
    for index in 0..3 {
        let alpha_scale = 1.0 - index as f32 * 0.18;
        push_rounded_rect(
            vertices,
            Rect {
                x: right - (index as f32 + 1.0) * (radius * 2.7),
                y: y - radius,
                width: radius * 2.0,
                height: radius * 2.0,
            },
            radius,
            with_alpha(
                INLINE_COMMAND_ROW_ACCENT_COLOR,
                INLINE_COMMAND_ROW_ACCENT_COLOR[3] * reveal_progress * alpha_scale,
            ),
            size,
        );
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_inline_command_section_panel(
    vertices: &mut Vec<Vertex>,
    start_line: usize,
    end_line: usize,
    line_height: f32,
    layout: &InlineWidgetCardLayout,
    color: [f32; 4],
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    if end_line <= start_line {
        return;
    }
    let top = layout.text_top + start_line as f32 * line_height - 7.0;
    let height = (end_line - start_line) as f32 * line_height + 12.0;
    let visible_height = (layout.visible_text_bottom - top).min(height);
    if visible_height <= 8.0 {
        return;
    }
    let rect = Rect {
        x: layout.card.x + layout.padding_x * 0.42,
        y: top,
        width: (layout.card.width - layout.padding_x * 0.84).max(1.0),
        height: visible_height,
    };
    push_rounded_rect(
        vertices,
        rect,
        INLINE_COMMAND_ROW_RADIUS + 4.0,
        with_alpha(color, color[3] * reveal_progress),
        size,
    );
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_single_session_inline_widget_list_reflow(
    vertices: &mut Vec<Vertex>,
    kind: Option<InlineWidgetKind>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    inline_list_reflow_motion: Option<&InlineWidgetListReflowMotionFrame>,
    size: PhysicalSize<u32>,
) {
    let Some(motion) = inline_list_reflow_motion else {
        return;
    };
    let line_height = inline_widget_line_height(kind, typography);
    for run in inline_widget_list_row_runs(kind, inline_lines, line_count) {
        if let Some(visual) = motion.visual_for_key(run.key) {
            push_single_session_inline_widget_reflow_row(
                vertices,
                run,
                visual,
                line_height,
                layout,
                reveal_progress,
                size,
            );
        }
    }
    for (run, visual) in motion.exiting() {
        push_single_session_inline_widget_reflow_row(
            vertices,
            *run,
            *visual,
            line_height,
            layout,
            reveal_progress,
            size,
        );
    }
}

pub(crate) fn push_single_session_inline_widget_reflow_row(
    vertices: &mut Vec<Vertex>,
    run: InlineWidgetListRowRun,
    visual: InlineWidgetListReflowVisual,
    line_height: f32,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    size: PhysicalSize<u32>,
) {
    if visual.opacity <= 0.001 || visual.line_span <= 0.05 {
        return;
    }
    let row_top = layout.text_top
        + (run.line as f32 + visual.y_offset_lines) * line_height
        + inline_widget_selection_top_offset(Some(run.kind));
    let row_height =
        visual.line_span * line_height + inline_widget_selection_extra_height(Some(run.kind));
    let row_visible_height = (layout.visible_text_bottom - row_top).min(row_height);
    let row_width = (layout.card.width - layout.padding_x).max(0.0);
    if row_visible_height <= 3.0 || row_width <= 6.0 {
        return;
    }
    push_rounded_rect(
        vertices,
        Rect {
            x: layout.card.x + layout.padding_x * 0.5,
            y: row_top,
            width: row_width,
            height: row_visible_height.max(1.0),
        },
        layout.selection_radius,
        with_alpha(
            INLINE_WIDGET_LIST_REFLOW_COLOR,
            INLINE_WIDGET_LIST_REFLOW_COLOR[3] * reveal_progress * visual.opacity,
        ),
        size,
    );
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_single_session_inline_widget_selection(
    vertices: &mut Vec<Vertex>,
    kind: Option<InlineWidgetKind>,
    inline_lines: &[SingleSessionStyledLine],
    line_count: usize,
    typography: &SingleSessionTypography,
    layout: &InlineWidgetCardLayout,
    reveal_progress: f32,
    inline_selection_motion: Option<&InlineWidgetSelectionMotionFrame>,
    size: PhysicalSize<u32>,
) {
    let Some(target) = inline_widget_selection_target(kind, inline_lines, line_count) else {
        return;
    };
    let visual = inline_selection_motion
        .and_then(|motion| motion.visual_for_target(target))
        .unwrap_or_else(|| InlineWidgetSelectionVisual::settled(target));
    if visual.opacity <= 0.001 || visual.line_span <= 0.05 {
        return;
    }

    let line_height = inline_widget_line_height(kind, typography);
    let row_top = layout.text_top
        + (target.line as f32 + visual.y_offset_lines) * line_height
        + inline_widget_selection_top_offset(kind);
    let row_height = visual.line_span * line_height + inline_widget_selection_extra_height(kind);
    let row_visible_height = (layout.visible_text_bottom - row_top).min(row_height);
    let row_width = (layout.card.width - layout.padding_x).max(0.0);
    if row_visible_height <= 3.0 || row_width <= 6.0 {
        return;
    }

    let color = inline_widget_selection_background_color(kind);
    push_rounded_rect(
        vertices,
        Rect {
            x: layout.card.x + layout.padding_x * 0.5,
            y: row_top,
            width: row_width,
            height: row_visible_height.max(1.0),
        },
        layout.selection_radius,
        with_alpha(color, color[3] * reveal_progress * visual.opacity),
        size,
    );
}

pub(crate) const INLINE_WIDGET_SIDE_GUTTER_EXTRA: f32 = 24.0;

pub(crate) const INLINE_WIDGET_CARD_PADDING_X: f32 = 14.0;

pub(crate) const INLINE_WIDGET_CARD_PADDING_Y: f32 = 8.0;

pub(crate) const INLINE_WIDGET_BODY_GAP: f32 = 8.0;

pub(crate) const INLINE_WIDGET_CARD_RADIUS: f32 = 18.0;

pub(crate) const INLINE_WIDGET_SELECTION_RADIUS: f32 = 10.0;

pub(crate) const SLASH_SUGGESTIONS_INLINE_CARD_PADDING_X: f32 = 8.0;

pub(crate) const SLASH_SUGGESTIONS_INLINE_CARD_PADDING_Y: f32 = 5.0;

pub(crate) const SLASH_SUGGESTIONS_INLINE_CARD_RADIUS: f32 = 13.0;

pub(crate) const SLASH_SUGGESTIONS_INLINE_SELECTION_RADIUS: f32 = 7.0;

pub(crate) const SLASH_SUGGESTIONS_INLINE_FONT_SCALE: f32 = 0.88;

pub(crate) const INLINE_COMMAND_ROW_RADIUS: f32 = 10.0;

pub(crate) const INLINE_COMMAND_ROW_INSET_X: f32 = 10.0;

pub(crate) const INLINE_COMMAND_ROW_GAP_Y: f32 = 4.0;

pub(crate) const INLINE_COMMAND_MODEL_ROW_GAP_Y: f32 = 5.5;

pub(crate) const INLINE_COMMAND_ROW_BACKGROUND_COLOR: [f32; 4] = [0.960, 0.972, 0.992, 0.74];

pub(crate) const INLINE_COMMAND_ROW_BORDER_COLOR: [f32; 4] = [0.120, 0.160, 0.250, 0.14];

pub(crate) const INLINE_COMMAND_ROW_SELECTED_COLOR: [f32; 4] = [0.890, 0.928, 1.000, 0.92];

pub(crate) const INLINE_COMMAND_ROW_SELECTED_BORDER_COLOR: [f32; 4] = [0.090, 0.250, 0.650, 0.34];

pub(crate) const INLINE_COMMAND_ROW_ACCENT_COLOR: [f32; 4] = [0.100, 0.300, 0.760, 0.40];

pub(crate) const INLINE_COMMAND_SECTION_BACKGROUND_COLOR: [f32; 4] = [0.955, 0.972, 1.000, 0.30];

pub(crate) const INLINE_COMMAND_PREVIEW_BACKGROUND_COLOR: [f32; 4] = [0.985, 0.990, 1.000, 0.34];

pub(crate) const INLINE_COMMAND_SPLIT_DIVIDER_COLOR: [f32; 4] = [0.120, 0.220, 0.440, 0.16];

pub(crate) const INLINE_COMMAND_CHIP_COLOR: [f32; 4] = [0.900, 0.930, 0.985, 0.54];

pub(crate) const INLINE_COMMAND_CHIP_ICON_COLOR: [f32; 4] = [0.075, 0.230, 0.620, 0.86];

pub(crate) const INLINE_COMMAND_MODEL_ICON_BACKGROUND_COLOR: [f32; 4] = [0.915, 0.940, 0.985, 0.50];

pub(crate) const INLINE_COMMAND_MODEL_ICON_COLOR: [f32; 4] = [0.080, 0.230, 0.590, 0.84];

pub(crate) const MODEL_PICKER_ROW_ACCENT_COLOR: [f32; 4] = [0.075, 0.280, 0.740, 0.46];

pub(crate) const INLINE_COMMAND_SESSION_ROW_TOP_INSET: f32 = 3.0;

pub(crate) const INLINE_COMMAND_SESSION_ROW_BOTTOM_INSET: f32 = 10.0;

pub(crate) const RESUME_SESSION_SELECTED_TINT: [f32; 4] = [0.835, 0.905, 1.000, 0.66];

pub(crate) const RESUME_SESSION_SELECTED_BORDER_TINT: [f32; 4] = [0.075, 0.290, 0.900, 0.34];

pub(crate) const RESUME_SESSION_ACTIVE_FILL: [f32; 4] = [0.925, 0.992, 0.955, 0.50];

pub(crate) const RESUME_SESSION_ACTIVE_BORDER: [f32; 4] = [0.050, 0.530, 0.300, 0.22];

pub(crate) const RESUME_SESSION_ACTIVE_ACCENT: [f32; 4] = [0.045, 0.650, 0.355, 0.62];

pub(crate) const RESUME_SESSION_ACTIVE_ICON_BACKGROUND: [f32; 4] = [0.790, 0.970, 0.865, 0.54];

pub(crate) const RESUME_SESSION_ACTIVE_ICON_COLOR: [f32; 4] = [0.025, 0.455, 0.250, 0.92];

pub(crate) const RESUME_SESSION_CLOSED_FILL: [f32; 4] = [0.965, 0.978, 0.994, 0.46];

pub(crate) const RESUME_SESSION_CLOSED_BORDER: [f32; 4] = [0.160, 0.235, 0.360, 0.16];

pub(crate) const RESUME_SESSION_CLOSED_ACCENT: [f32; 4] = [0.290, 0.400, 0.560, 0.44];

pub(crate) const RESUME_SESSION_CLOSED_ICON_BACKGROUND: [f32; 4] = [0.905, 0.935, 0.975, 0.50];

pub(crate) const RESUME_SESSION_CLOSED_ICON_COLOR: [f32; 4] = [0.170, 0.260, 0.420, 0.82];

pub(crate) const RESUME_SESSION_ERROR_FILL: [f32; 4] = [1.000, 0.930, 0.930, 0.50];

pub(crate) const RESUME_SESSION_ERROR_BORDER: [f32; 4] = [0.760, 0.120, 0.160, 0.25];

pub(crate) const RESUME_SESSION_ERROR_ACCENT: [f32; 4] = [0.850, 0.120, 0.180, 0.64];

pub(crate) const RESUME_SESSION_ERROR_ICON_BACKGROUND: [f32; 4] = [1.000, 0.820, 0.835, 0.56];

pub(crate) const RESUME_SESSION_ERROR_ICON_COLOR: [f32; 4] = [0.670, 0.060, 0.110, 0.92];

pub(crate) const RESUME_SESSION_SPECIAL_FILL: [f32; 4] = [0.964, 0.940, 1.000, 0.50];

pub(crate) const RESUME_SESSION_SPECIAL_BORDER: [f32; 4] = [0.405, 0.190, 0.780, 0.23];

pub(crate) const RESUME_SESSION_SPECIAL_ACCENT: [f32; 4] = [0.500, 0.245, 0.900, 0.58];

pub(crate) const RESUME_SESSION_SPECIAL_ICON_BACKGROUND: [f32; 4] = [0.900, 0.830, 1.000, 0.54];

pub(crate) const RESUME_SESSION_SPECIAL_ICON_COLOR: [f32; 4] = [0.360, 0.150, 0.720, 0.90];

pub(crate) const RESUME_SESSION_RELOADED_FILL: [f32; 4] = [0.930, 0.982, 1.000, 0.50];

pub(crate) const RESUME_SESSION_RELOADED_BORDER: [f32; 4] = [0.050, 0.470, 0.680, 0.22];

pub(crate) const RESUME_SESSION_RELOADED_ACCENT: [f32; 4] = [0.050, 0.520, 0.760, 0.56];

pub(crate) const RESUME_SESSION_RELOADED_ICON_BACKGROUND: [f32; 4] = [0.800, 0.940, 1.000, 0.52];

pub(crate) const RESUME_SESSION_RELOADED_ICON_COLOR: [f32; 4] = [0.035, 0.370, 0.620, 0.90];

pub(crate) const RESUME_SESSION_NEUTRAL_FILL: [f32; 4] = [0.972, 0.982, 1.000, 0.44];

pub(crate) const RESUME_SESSION_NEUTRAL_BORDER: [f32; 4] = [0.100, 0.170, 0.320, 0.14];

pub(crate) const RESUME_SESSION_NEUTRAL_ACCENT: [f32; 4] = [0.135, 0.280, 0.620, 0.42];

pub(crate) const RESUME_SESSION_NEUTRAL_ICON_BACKGROUND: [f32; 4] = [0.900, 0.930, 1.000, 0.46];

pub(crate) const RESUME_SESSION_NEUTRAL_ICON_COLOR: [f32; 4] = [0.120, 0.220, 0.460, 0.82];
