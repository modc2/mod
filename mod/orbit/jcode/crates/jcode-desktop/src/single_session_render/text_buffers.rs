//! Text buffer caching for single-session rendering: SingleSessionTextKey construction, buffer (re)build from keys, body-buffer layout cache, and inline-widget split-buffer sizing.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SingleSessionTextKey {
    pub(crate) size: (u32, u32),
    pub(crate) fresh_welcome_visible: bool,
    pub(crate) title: String,
    pub(crate) version: String,
    pub(crate) welcome_hero: String,
    pub(crate) welcome_hint: Vec<SingleSessionStyledLine>,
    pub(crate) activity_active: bool,
    pub(crate) welcome_handoff_visible: bool,
    pub(crate) text_scale_bits: u32,
    pub(crate) user_font_family: &'static str,
    pub(crate) assistant_font_family: &'static str,
    pub(crate) body: Vec<SingleSessionStyledLine>,
    pub(crate) inline_widget_kind: Option<InlineWidgetKind>,
    pub(crate) inline_widget: Vec<SingleSessionStyledLine>,
    pub(crate) inline_widget_preview: Vec<SingleSessionStyledLine>,
    pub(crate) draft: String,
}

#[cfg(test)]
pub(crate) fn single_session_text_buffers(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    font_system: &mut FontSystem,
) -> Vec<Buffer> {
    let key = single_session_text_key(app, size);
    single_session_text_buffers_from_key(&key, size, font_system)
}

#[cfg(test)]
pub(crate) fn single_session_text_key(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
) -> SingleSessionTextKey {
    single_session_text_key_for_tick(app, size, 0)
}

#[cfg(test)]
pub(crate) fn single_session_text_key_for_tick(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
) -> SingleSessionTextKey {
    single_session_text_key_for_tick_with_scroll(app, size, tick, 0.0)
}

pub(crate) fn single_session_text_key_for_tick_with_scroll(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
) -> SingleSessionTextKey {
    let rendered_body_lines = single_session_rendered_body_lines_for_tick(app, size, tick);
    single_session_text_key_for_tick_with_rendered_body(
        app,
        size,
        tick,
        smooth_scroll_lines,
        &rendered_body_lines,
    )
}

pub(crate) fn single_session_text_key_for_tick_with_rendered_body(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
    rendered_body_lines: &[SingleSessionStyledLine],
) -> SingleSessionTextKey {
    let viewport = single_session_body_viewport_from_lines(
        app,
        size,
        smooth_scroll_lines,
        rendered_body_lines,
    );
    let welcome_chrome_offset_pixels = welcome_timeline_visual_offset_pixels_for_total_lines(
        app,
        size,
        smooth_scroll_lines,
        viewport.total_lines,
    );
    let welcome_chrome_visible =
        welcome_timeline_chrome_visible(app, size, welcome_chrome_offset_pixels);
    single_session_text_key_for_body_lines(
        app,
        size,
        tick,
        viewport.top_offset_pixels,
        viewport.lines,
        welcome_chrome_visible,
    )
}

pub(crate) fn inline_widget_split_preview_start(
    kind: Option<InlineWidgetKind>,
    lines: &[SingleSessionStyledLine],
) -> Option<usize> {
    if kind != Some(InlineWidgetKind::SessionSwitcher) {
        return None;
    }
    lines
        .iter()
        .position(|line| line.text.starts_with("Preview"))
}

pub(crate) fn inline_widget_split_primary_lines(
    kind: Option<InlineWidgetKind>,
    lines: Vec<SingleSessionStyledLine>,
) -> Vec<SingleSessionStyledLine> {
    let Some(preview_start) = inline_widget_split_preview_start(kind, &lines) else {
        return lines;
    };
    lines
        .into_iter()
        .enumerate()
        .map(|(index, line)| {
            if index < preview_start {
                line
            } else {
                blank_render_line()
            }
        })
        .collect()
}

pub(crate) fn inline_widget_split_preview_lines(
    kind: Option<InlineWidgetKind>,
    lines: &[SingleSessionStyledLine],
) -> Vec<SingleSessionStyledLine> {
    let Some(preview_start) = inline_widget_split_preview_start(kind, lines) else {
        return Vec::new();
    };
    lines[preview_start..].to_vec()
}

pub(crate) fn single_session_text_key_for_body_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    _tick: u64,
    _body_top_offset_pixels: f32,
    body: Vec<SingleSessionStyledLine>,
    welcome_chrome_visible: bool,
) -> SingleSessionTextKey {
    let welcome_handoff_visible = false;
    let welcome_input_visible = true;
    let (welcome_hero, welcome_hint) = if welcome_chrome_visible {
        let welcome_hint = if app.draft.is_empty() {
            let mut lines = vec![SingleSessionStyledLine::new(
                "Type a message to start. Ask me to build, debug, explain, or automate something.",
                SingleSessionLineStyle::Meta,
            )];
            if let Some(suggestion) = app.welcome_continuation_suggestion() {
                lines.push(SingleSessionStyledLine::new(
                    format!("Suggestion: {suggestion}"),
                    SingleSessionLineStyle::Status,
                ));
            }
            lines
        } else {
            Vec::new()
        };
        (app.welcome_hero_text(), welcome_hint)
    } else if app.is_fresh_welcome_visible() && app.draft.is_empty() {
        let mut lines = vec![SingleSessionStyledLine::new(
            "Type a message to start. Ask me to build, debug, explain, or automate something.",
            SingleSessionLineStyle::Meta,
        )];
        if let Some(suggestion) = app.welcome_continuation_suggestion() {
            lines.push(SingleSessionStyledLine::new(
                format!("Suggestion: {suggestion}"),
                SingleSessionLineStyle::Status,
            ));
        }
        (String::new(), lines)
    } else {
        (String::new(), Vec::new())
    };
    let inline_widget_kind = app.render_inline_widget_kind();
    let inline_widget = app.render_inline_widget_styled_lines();
    let inline_widget_preview =
        inline_widget_split_preview_lines(inline_widget_kind, &inline_widget);
    let inline_widget = inline_widget_split_primary_lines(inline_widget_kind, inline_widget);
    SingleSessionTextKey {
        size: (size.width, size.height),
        fresh_welcome_visible: welcome_chrome_visible,
        title: if welcome_chrome_visible {
            String::new()
        } else {
            app.header_title()
        },
        version: if welcome_chrome_visible {
            if welcome_input_visible {
                fresh_welcome_version_label()
            } else {
                String::new()
            }
        } else {
            desktop_header_version_label()
        },
        welcome_hero,
        welcome_hint,
        activity_active: app.has_activity_indicator(),
        welcome_handoff_visible,
        text_scale_bits: app.text_scale().to_bits(),
        user_font_family: single_session_user_font_family(),
        assistant_font_family: single_session_assistant_font_family(),
        body,
        inline_widget_kind,
        inline_widget,
        inline_widget_preview,
        draft: if welcome_input_visible {
            visualize_composer_whitespace(&app.composer_text())
        } else {
            String::new()
        },
    }
}

pub(crate) fn single_session_text_buffers_from_key(
    key: &SingleSessionTextKey,
    size: PhysicalSize<u32>,
    font_system: &mut FontSystem,
) -> Vec<Buffer> {
    single_session_text_buffers_from_key_reusing_unchanged(
        key,
        None,
        Vec::new(),
        false,
        size,
        font_system,
    )
}

pub(crate) fn single_session_text_buffers_from_key_reusing_unchanged(
    key: &SingleSessionTextKey,
    previous_key: Option<&SingleSessionTextKey>,
    old_buffers: Vec<Buffer>,
    reuse_body_buffer: bool,
    size: PhysicalSize<u32>,
    font_system: &mut FontSystem,
) -> Vec<Buffer> {
    single_session_text_buffers_from_key_reusing_unchanged_from_options(
        key,
        previous_key,
        old_buffers.into_iter().map(Some).collect(),
        reuse_body_buffer,
        size,
        font_system,
    )
}

pub(crate) fn single_session_text_buffers_from_key_reusing_unchanged_from_options(
    key: &SingleSessionTextKey,
    previous_key: Option<&SingleSessionTextKey>,
    mut old_buffers: Vec<Option<Buffer>>,
    reuse_body_buffer: bool,
    size: PhysicalSize<u32>,
    font_system: &mut FontSystem,
) -> Vec<Buffer> {
    let text_scale = f32::from_bits(key.text_scale_bits);
    let typography = single_session_typography_for_scale(text_scale);
    let content_width = single_session_content_width(size);

    let draft_top = if key.fresh_welcome_visible {
        fresh_welcome_draft_top_for_scale(size, text_scale)
    } else {
        single_session_draft_top_for_fresh_state(size, false)
    };
    let prompt_height = (size.height as f32 - draft_top - 18.0)
        .max(typography.code_size * typography.code_line_height * 2.0);
    let version_font_size = if key.fresh_welcome_visible {
        fresh_welcome_version_font_size()
    } else {
        typography.meta_size
    };

    let user_font_compatible = previous_key.is_some_and(|previous| {
        previous.user_font_family == key.user_font_family
            && previous.assistant_font_family == key.assistant_font_family
    });
    let exact_layout_compatible = previous_key.is_some_and(|previous| {
        previous.size == key.size
            && previous.text_scale_bits == key.text_scale_bits
            && user_font_compatible
    });
    let width_layout_compatible = previous_key.is_some_and(|previous| {
        previous.size.0 == key.size.0
            && previous.text_scale_bits == key.text_scale_bits
            && user_font_compatible
    });
    let body_layout_compatible = previous_key.is_some_and(|previous| {
        previous.text_scale_bits == key.text_scale_bits
            && single_session_body_text_buffer_layout_bucket(previous.size, text_scale)
                == single_session_body_text_buffer_layout_bucket(key.size, text_scale)
            && user_font_compatible
    });
    let take_reusable =
        |old_buffers: &mut Vec<Option<Buffer>>, index: usize, reusable: bool| -> Option<Buffer> {
            if !reusable {
                return None;
            }
            old_buffers.get_mut(index).and_then(Option::take)
        };
    let exact_previous = previous_key.filter(|_| exact_layout_compatible);
    let width_previous = previous_key.filter(|_| width_layout_compatible);
    let body_previous = previous_key.filter(|_| body_layout_compatible);

    let title_buffer = take_reusable(
        &mut old_buffers,
        0,
        width_previous.is_some_and(|previous| previous.title == key.title),
    )
    .unwrap_or_else(|| {
        single_session_text_buffer(
            font_system,
            &key.title,
            typography.title_size,
            typography.title_size * typography.meta_line_height,
            content_width,
            48.0,
        )
    });

    let body_buffer = take_reusable(
        &mut old_buffers,
        1,
        (reuse_body_buffer && user_font_compatible)
            || body_previous.is_some_and(|previous| previous.body == key.body),
    )
    .unwrap_or_else(|| {
        single_session_body_text_buffer_from_lines(font_system, &key.body, size, text_scale)
    });

    let inline_widget_line_count = inline_widget_visual_line_count(
        key.inline_widget_kind,
        &key.inline_widget,
        &key.inline_widget_preview,
    );
    let inline_widget_width = if inline_widget_line_count == 0 {
        content_width
    } else {
        inline_widget_text_width_for_split_buffers(
            key.inline_widget_kind,
            &key.inline_widget,
            &key.inline_widget_preview,
            size,
            text_scale,
        )
        .max(1.0)
        .min(content_width)
    };
    let inline_widget_height = if key.inline_widget.is_empty() {
        prompt_height
    } else {
        let inline_widget_line_height =
            inline_widget_line_height(key.inline_widget_kind, &typography);
        prompt_height
            .max(size.height as f32)
            .max(inline_widget_line_count as f32 * inline_widget_line_height)
    };
    let (inline_widget_primary_width, inline_widget_preview_width) =
        inline_widget_split_text_widths(
            key.inline_widget_kind,
            &typography,
            size,
            inline_widget_line_count,
            inline_widget_width,
        );
    let inline_widget_buffer = take_reusable(
        &mut old_buffers,
        4,
        exact_previous.is_some_and(|previous| {
            previous.inline_widget == key.inline_widget
                && previous.inline_widget_kind == key.inline_widget_kind
        }),
    )
    .unwrap_or_else(|| {
        let inline_widget_font_size = inline_widget_font_size(key.inline_widget_kind, &typography);
        let inline_widget_line_height =
            inline_widget_line_height(key.inline_widget_kind, &typography);
        // All inline widgets are aligned tables or row lists; wrapping any
        // line shifts the rows below it out of alignment with their
        // selection/row chrome. Long lines ellipsis-truncate instead.
        let inline_widget_wrap = Wrap::None;
        // For non-wrapping kinds, pre-truncate each line to the columns that
        // actually fit the rail so text ends with an ellipsis instead of
        // being sliced mid-glyph at the clip edge.
        let truncated_lines;
        let buffer_lines: &[SingleSessionStyledLine] = if inline_widget_wrap == Wrap::None {
            let advance = inline_widget_font_size * 0.6;
            let max_columns = ((inline_widget_primary_width / advance).floor() as usize).max(4);
            truncated_lines = key
                .inline_widget
                .iter()
                .map(|line| {
                    if line.text.chars().count() > max_columns {
                        SingleSessionStyledLine::new(
                            format!(
                                "{}…",
                                line.text.chars().take(max_columns - 1).collect::<String>()
                            ),
                            line.style,
                        )
                    } else {
                        line.clone()
                    }
                })
                .collect::<Vec<_>>();
            &truncated_lines
        } else {
            &key.inline_widget
        };
        single_session_styled_text_buffer(
            font_system,
            buffer_lines,
            inline_widget_font_size,
            inline_widget_line_height,
            inline_widget_primary_width,
            inline_widget_height,
            inline_widget_wrap,
        )
    });

    let inline_widget_preview_buffer = take_reusable(
        &mut old_buffers,
        7,
        exact_previous.is_some_and(|previous| {
            previous.inline_widget_preview == key.inline_widget_preview
                && previous.inline_widget_kind == key.inline_widget_kind
        }),
    )
    .unwrap_or_else(|| {
        let inline_widget_font_size = inline_widget_font_size(key.inline_widget_kind, &typography);
        let inline_widget_line_height =
            inline_widget_line_height(key.inline_widget_kind, &typography);
        let inline_widget_preview_height = inline_widget_estimated_wrapped_text_height(
            key.inline_widget_kind,
            &key.inline_widget_preview,
            inline_widget_preview_width,
            &typography,
        )
        .min(inline_widget_height)
        .max(inline_widget_line_height);
        single_session_styled_text_buffer(
            font_system,
            &key.inline_widget_preview,
            inline_widget_font_size,
            inline_widget_line_height,
            inline_widget_preview_width,
            inline_widget_preview_height,
            Wrap::Word,
        )
    });

    let draft_buffer = take_reusable(
        &mut old_buffers,
        2,
        exact_previous.is_some_and(|previous| previous.draft == key.draft),
    )
    .unwrap_or_else(|| {
        single_session_text_buffer_with_family(
            font_system,
            &key.draft,
            key.user_font_family,
            typography.code_size,
            typography.code_size * typography.code_line_height,
            content_width,
            prompt_height,
        )
    });

    let version_buffer = take_reusable(
        &mut old_buffers,
        3,
        width_previous.is_some_and(|previous| previous.version == key.version),
    )
    .unwrap_or_else(|| {
        single_session_text_buffer(
            font_system,
            &key.version,
            version_font_size,
            version_font_size * typography.meta_line_height,
            content_width,
            24.0,
        )
    });

    let (hero_min, hero_max) = glyph_welcome_hero_bounds(size, text_scale);
    let hero_width = (hero_max[0] - hero_min[0]).max(1.0);
    let hero_height = (hero_max[1] - hero_min[1]).max(1.0);
    let hero_font_size = glyph_welcome_hero_font_size(size, text_scale);
    let hero_buffer = take_reusable(
        &mut old_buffers,
        5,
        exact_previous.is_some_and(|previous| previous.welcome_hero == key.welcome_hero),
    )
    .unwrap_or_else(|| {
        single_session_text_buffer_with_family(
            font_system,
            &key.welcome_hero,
            SINGLE_SESSION_WELCOME_FONT_FAMILY,
            hero_font_size,
            hero_font_size * 1.18,
            hero_width,
            hero_height,
        )
    });

    let welcome_hint_buffer = take_reusable(
        &mut old_buffers,
        6,
        width_previous.is_some_and(|previous| previous.welcome_hint == key.welcome_hint),
    )
    .unwrap_or_else(|| {
        single_session_styled_text_buffer(
            font_system,
            &key.welcome_hint,
            typography.meta_size,
            typography.meta_size * typography.meta_line_height,
            content_width,
            48.0,
            Wrap::Word,
        )
    });

    vec![
        title_buffer,
        body_buffer,
        draft_buffer,
        version_buffer,
        inline_widget_buffer,
        hero_buffer,
        welcome_hint_buffer,
        inline_widget_preview_buffer,
    ]
}

pub(crate) fn inline_widget_visual_line_count(
    kind: Option<InlineWidgetKind>,
    primary: &[SingleSessionStyledLine],
    preview: &[SingleSessionStyledLine],
) -> usize {
    if kind != Some(InlineWidgetKind::SessionSwitcher) || preview.is_empty() {
        return primary.len();
    }
    primary.len().max(preview.len())
}

pub(crate) fn inline_widget_text_width_for_split_buffers(
    kind: Option<InlineWidgetKind>,
    primary: &[SingleSessionStyledLine],
    preview: &[SingleSessionStyledLine],
    size: PhysicalSize<u32>,
    ui_scale: f32,
) -> f32 {
    if kind != Some(InlineWidgetKind::SessionSwitcher) || preview.is_empty() {
        return inline_widget_text_width_for_lines(kind, primary, size, ui_scale);
    }

    // Split layout always uses the full widget width (matching
    // `inline_widget_text_width_for_lines`), so the rail and preview panes get
    // room instead of shrinking to the longest text line.
    inline_widget_max_text_width_for_kind(kind, size)
}

pub(crate) fn inline_widget_estimated_wrapped_text_height(
    kind: Option<InlineWidgetKind>,
    lines: &[SingleSessionStyledLine],
    width: f32,
    typography: &SingleSessionTypography,
) -> f32 {
    let line_height = inline_widget_line_height(kind, typography);
    if lines.is_empty() {
        return line_height;
    }

    let average_char_width = inline_widget_font_size(kind, typography) * 0.6;
    let columns_per_line = (width / average_char_width).floor().max(1.0) as usize;
    let visual_lines = lines
        .iter()
        .map(|line| {
            inline_widget_visual_columns(&line.text)
                .max(1)
                .div_ceil(columns_per_line)
        })
        .sum::<usize>();

    // glyphon::Buffer::shape_until_scroll is intentionally viewport-limited;
    // leave a small amount of slack so the last row is shaped even when glyph
    // metrics or word wrapping round up slightly differently than this cheap
    // column estimate. This keeps split previews compact without restoring the
    // old full-window buffer height.
    visual_lines.saturating_add(2) as f32 * line_height
}

pub(crate) fn inline_widget_split_text_widths(
    kind: Option<InlineWidgetKind>,
    typography: &SingleSessionTypography,
    size: PhysicalSize<u32>,
    line_count: usize,
    full_text_width: f32,
) -> (f32, f32) {
    if kind != Some(InlineWidgetKind::SessionSwitcher) || line_count == 0 {
        return (full_text_width, 1.0);
    }
    let Some(layout) = inline_widget_card_layout(
        size,
        kind,
        typography,
        line_count,
        full_text_width,
        PANEL_TITLE_TOP_PADDING,
        1.0,
    ) else {
        return (full_text_width, full_text_width);
    };
    let Some(columns) = session_switcher_split_columns(&layout) else {
        return (full_text_width, full_text_width);
    };
    (
        (columns.rail.width - INLINE_COMMAND_ROW_INSET_X * 2.0).max(1.0),
        (columns.preview.width - layout.padding_x * 1.8).max(1.0),
    )
}

pub(crate) fn single_session_body_text_buffer_from_lines(
    font_system: &mut FontSystem,
    lines: &[SingleSessionStyledLine],
    size: PhysicalSize<u32>,
    text_scale: f32,
) -> Buffer {
    single_session_body_text_buffer_from_lines_with_opacity(
        font_system,
        lines,
        size,
        text_scale,
        1.0,
    )
}

pub(crate) fn single_session_body_text_buffer_from_lines_with_opacity(
    font_system: &mut FontSystem,
    lines: &[SingleSessionStyledLine],
    size: PhysicalSize<u32>,
    text_scale: f32,
    opacity: f32,
) -> Buffer {
    single_session_body_text_buffer_from_lines_with_opacity_and_tail_fade(
        font_system,
        lines,
        size,
        text_scale,
        opacity,
        0.0,
    )
}

pub(crate) fn single_session_body_text_buffer_from_lines_with_opacity_and_tail_fade(
    font_system: &mut FontSystem,
    lines: &[SingleSessionStyledLine],
    size: PhysicalSize<u32>,
    text_scale: f32,
    opacity: f32,
    tail_fade_chars: f32,
) -> Buffer {
    let typography = single_session_typography_for_scale(text_scale);
    let content_width = single_session_content_width(size);
    let mut buffer = single_session_styled_text_buffer_with_opacity_and_tail_fade(
        font_system,
        lines,
        typography.body_size,
        typography.body_size * typography.body_line_height,
        content_width,
        single_session_body_text_buffer_layout_height(size, text_scale),
        Wrap::None,
        opacity,
        tail_fade_chars,
    );
    buffer.shape_until(font_system, i32::MAX);
    buffer
}

pub(crate) fn single_session_body_layout_cache_size(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
) -> (u32, u32) {
    let max_columns =
        single_session_body_max_columns(size, app.text_scale()).min(u32::MAX as usize) as u32;
    let welcome_virtual_lines =
        if app.is_welcome_timeline_visible() && app.has_welcome_timeline_transcript() {
            welcome_timeline_virtual_body_lines(app, size).min(u32::MAX as usize) as u32
        } else {
            0
        };
    (max_columns, welcome_virtual_lines)
}

pub(crate) fn single_session_body_text_buffer_layout_compatible(
    previous_size: (u32, u32),
    size: PhysicalSize<u32>,
    text_scale: f32,
) -> bool {
    single_session_body_text_buffer_layout_bucket(previous_size, text_scale)
        == single_session_body_text_buffer_layout_bucket((size.width, size.height), text_scale)
}

pub(crate) fn single_session_body_text_buffer_layout_bucket(
    size: (u32, u32),
    text_scale: f32,
) -> (u32, u32) {
    let physical_size = PhysicalSize::new(size.0, size.1);
    let width_columns =
        single_session_body_max_columns(physical_size, text_scale).min(u32::MAX as usize) as u32;
    let height_lines = single_session_body_text_buffer_layout_lines(physical_size, text_scale)
        .min(u32::MAX as usize) as u32;
    (width_columns, height_lines)
}

pub(crate) fn single_session_body_text_buffer_layout_height(
    size: PhysicalSize<u32>,
    text_scale: f32,
) -> f32 {
    let typography = single_session_typography_for_scale(text_scale);
    let line_height = typography.body_size * typography.body_line_height;
    single_session_body_text_buffer_layout_lines(size, text_scale) as f32 * line_height
}

pub(crate) fn single_session_body_text_buffer_layout_lines(
    size: PhysicalSize<u32>,
    text_scale: f32,
) -> usize {
    let typography = single_session_typography_for_scale(text_scale);
    let line_height = typography.body_size * typography.body_line_height;
    let available_height = (size.height as f32 - 150.0).max(line_height);
    ((available_height / line_height).floor() as usize).max(1)
}
