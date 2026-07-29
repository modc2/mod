//! Body viewport and rendered-line pipeline: visible body selection, viewport windows, rendered/wrapped body line production, streaming append, hit-testing, and vertical body geometry.

use super::*;

#[derive(Clone, Debug)]
pub(crate) struct SingleSessionBodyViewport {
    pub(crate) lines: Vec<SingleSessionStyledLine>,
    pub(crate) top_offset_pixels: f32,
    pub(crate) start_line: usize,
    pub(crate) total_lines: usize,
}

pub(crate) fn single_session_body_viewport_for_tick(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
) -> SingleSessionBodyViewport {
    // Borrow the memoized full body lines and only clone the visible slice via
    // `single_session_body_viewport_from_lines`, instead of cloning the whole
    // transcript. This keeps input-side callers (selection hit-testing on every
    // mouse-move) O(visible) rather than O(transcript).
    let lines = single_session_rendered_body_lines_for_tick_shared(app, size, tick);
    single_session_body_viewport_from_lines(app, size, smooth_scroll_lines, &lines)
}

pub(crate) fn single_session_body_viewport_from_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    lines: &[SingleSessionStyledLine],
) -> SingleSessionBodyViewport {
    let total_lines = lines.len();
    let layout = single_session_layout_for_total_lines(app, size, total_lines);
    let line_height = layout.metrics.body_line_height;
    let available_height = layout.body.height.max(line_height);
    let visible_lines = ((available_height / line_height).floor() as usize).max(1);
    if lines.len() <= visible_lines {
        return SingleSessionBodyViewport {
            lines: lines.to_vec(),
            top_offset_pixels: 0.0,
            start_line: 0,
            total_lines,
        };
    }

    let max_scroll = lines.len().saturating_sub(visible_lines);
    let scroll = (app.body_scroll_lines + smooth_scroll_lines).clamp(0.0, max_scroll as f32);
    let bottom_line = lines.len() as f32 - scroll;
    let top_line = bottom_line - visible_lines as f32;
    let start = top_line.floor().max(0.0) as usize;
    let end = bottom_line.ceil().min(lines.len() as f32) as usize;
    let top_offset_pixels = (start as f32 - top_line) * line_height;
    SingleSessionBodyViewport {
        lines: lines[start..end.max(start)].to_vec(),
        top_offset_pixels,
        start_line: start,
        total_lines,
    }
}

pub(crate) fn single_session_rendered_body_lines_for_tick(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
) -> Vec<SingleSessionStyledLine> {
    (*single_session_rendered_body_lines_for_tick_shared(app, size, tick)).clone()
}

/// Shared, memoized rendered body lines for the current transcript+layout.
///
/// This re-parses markdown and re-wraps the ENTIRE transcript (O(transcript)),
/// and is called from input handling (every selection mouse-move during a
/// drag), scroll-metric probing, and several geometry builders. Returning a
/// shared `Rc` lets callers that only need a slice (the viewport) avoid cloning
/// the whole transcript on every pointer event. The render hot path uses a
/// separate Canvas-side cache (`cached_single_session_body_lines`); this
/// thread-local single-entry memo accelerates the remaining callers. The key is
/// the body cache key, which already captures the message fingerprint, size,
/// text scale, and welcome/streaming state, so the cache invalidates whenever
/// any of those change.
pub(crate) fn single_session_rendered_body_lines_for_tick_shared(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
) -> std::rc::Rc<Vec<SingleSessionStyledLine>> {
    let layout_size = single_session_body_layout_cache_size(app, size);
    let key = app.rendered_body_cache_key(layout_size);
    thread_local! {
        static RENDERED_BODY_LINES_MEMO: std::cell::RefCell<Option<(u64, std::rc::Rc<Vec<SingleSessionStyledLine>>)>> =
            const { std::cell::RefCell::new(None) };
    }
    // Allow disabling the memo for A/B perf measurement in debug builds only;
    // the production memo can never be turned off by an env var.
    let memo_disabled =
        cfg!(debug_assertions) && std::env::var_os("JCODE_DESKTOP_DISABLE_BODY_MEMO").is_some();
    if !memo_disabled
        && let Some(cached) = RENDERED_BODY_LINES_MEMO.with(|cell| {
            cell.borrow()
                .as_ref()
                .filter(|(cached_key, _)| *cached_key == key)
                .map(|(_, lines)| lines.clone())
        })
    {
        return cached;
    }
    let lines = single_session_rendered_body_lines_from_raw(
        app,
        size,
        app.body_styled_lines_for_tick(tick),
    );
    let shared = std::rc::Rc::new(lines);
    if !memo_disabled {
        RENDERED_BODY_LINES_MEMO.with(|cell| {
            *cell.borrow_mut() = Some((key, shared.clone()));
        });
    }
    shared
}

pub(crate) fn single_session_rendered_body_lines_from_raw(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    raw_lines: Vec<SingleSessionStyledLine>,
) -> Vec<SingleSessionStyledLine> {
    let lines = single_session_wrapped_body_lines(raw_lines, size, app.text_scale());
    single_session_rendered_body_lines_from_wrapped(app, size, lines)
}

pub(crate) fn single_session_rendered_body_lines_from_raw_ref(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    raw_lines: &[SingleSessionStyledLine],
) -> Vec<SingleSessionStyledLine> {
    let lines = single_session_wrapped_body_lines_ref(raw_lines, size, app.text_scale());
    single_session_rendered_body_lines_from_wrapped(app, size, lines)
}

pub(crate) fn single_session_rendered_body_lines_from_wrapped(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    lines: Vec<SingleSessionStyledLine>,
) -> Vec<SingleSessionStyledLine> {
    if !(app.is_welcome_timeline_visible() && app.has_welcome_timeline_transcript()) {
        return lines;
    }

    // The welcome hero is visual chrome. These blank prelude rows make it
    // scroll like the first timeline block while keeping transcript text pure.
    let virtual_lines = welcome_timeline_virtual_body_lines(app, size);
    let mut rendered = Vec::with_capacity(virtual_lines + lines.len());
    rendered.extend((0..virtual_lines).map(|_| blank_render_line()));
    rendered.extend(lines);
    rendered
}

pub(crate) fn single_session_rendered_static_body_lines_for_streaming(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    _tick: u64,
) -> Option<Vec<SingleSessionStyledLine>> {
    let lines = single_session_wrapped_body_lines(
        app.body_styled_lines_without_streaming_response()?,
        size,
        app.text_scale(),
    );
    if !(app.is_welcome_timeline_visible() && app.has_welcome_timeline_transcript()) {
        return Some(lines);
    }

    let virtual_lines = welcome_timeline_virtual_body_lines(app, size);
    let mut rendered = Vec::with_capacity(virtual_lines + lines.len());
    rendered.extend((0..virtual_lines).map(|_| blank_render_line()));
    rendered.extend(lines);
    Some(rendered)
}

pub(crate) fn append_single_session_streaming_response_rendered_body_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    rendered_lines: &mut Vec<SingleSessionStyledLine>,
) {
    append_single_session_streaming_response_rendered_body_lines_with_reveal(
        app,
        size,
        rendered_lines,
        app.streaming_response.len(),
    );
}

/// Append the wrapped streaming-response lines, limited to the first
/// `revealed_bytes` of the response. Drives the adaptive streaming reveal.
pub(crate) fn append_single_session_streaming_response_rendered_body_lines_with_reveal(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    rendered_lines: &mut Vec<SingleSessionStyledLine>,
    revealed_bytes: usize,
) {
    if app.streaming_response.is_empty() {
        return;
    }
    let lines = app.streaming_response_revealed_styled_lines(revealed_bytes);
    if lines.is_empty() {
        return;
    }
    if !app.messages.is_empty() {
        rendered_lines.push(blank_render_line());
    }
    rendered_lines.extend(single_session_wrapped_body_lines(
        lines,
        size,
        app.text_scale(),
    ));
}

pub(crate) fn single_session_streaming_response_rendered_body_line_count(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
) -> usize {
    if app.streaming_response.is_empty() {
        return 0;
    }
    let separator = usize::from(!app.messages.is_empty());
    separator
        + single_session_wrapped_body_line_count(
            app.streaming_response_styled_lines(),
            size,
            app.text_scale(),
        )
}

pub(crate) fn blank_render_line() -> SingleSessionStyledLine {
    SingleSessionStyledLine::new(String::new(), SingleSessionLineStyle::Blank)
}

pub(crate) fn single_session_body_line_at_y(size: PhysicalSize<u32>, y: f32) -> Option<usize> {
    let typography = single_session_typography();
    let line_height = typography.body_size * typography.body_line_height;
    if y < PANEL_BODY_TOP_PADDING || y >= single_session_body_bottom(size) {
        return None;
    }
    Some(((y - PANEL_BODY_TOP_PADDING) / line_height).floor() as usize)
}

pub(crate) fn single_session_body_point_at_position(
    size: PhysicalSize<u32>,
    x: f32,
    y: f32,
    lines: &[String],
) -> Option<SelectionPoint> {
    let line = single_session_body_line_at_y(size, y)?;
    let text = lines.get(line)?;
    Some(SelectionPoint {
        line,
        column: single_session_body_column_at_x(x, text),
    })
}

pub(crate) fn single_session_body_column_at_x(x: f32, line: &str) -> usize {
    let char_count = line.chars().count();
    if x <= PANEL_TITLE_LEFT_PADDING {
        return 0;
    }
    let raw = ((x - PANEL_TITLE_LEFT_PADDING) / single_session_body_char_width()).round();
    raw.max(0.0).min(char_count as f32) as usize
}

pub(crate) fn single_session_body_char_width() -> f32 {
    single_session_body_char_width_for_scale(1.0)
}

pub(crate) fn single_session_body_char_width_for_scale(text_scale: f32) -> f32 {
    let typography = single_session_typography_for_scale(text_scale);
    typography.body_size * 0.58
}

pub(crate) fn single_session_body_top_for_app(
    _app: &SingleSessionApp,
    _size: PhysicalSize<u32>,
) -> f32 {
    PANEL_BODY_TOP_PADDING
}

pub(crate) fn single_session_body_bottom_base_for_app(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
) -> f32 {
    if app.is_welcome_timeline_visible() {
        // Treat the welcome hero as the first visual item in the chat timeline.
        // Anything inline, such as the /model picker, must reserve space between
        // that timeline and the composer instead of floating over the hero.
        return (single_session_draft_top_for_app(app, size) - welcome_timeline_body_draft_gap())
            .max(single_session_body_top_for_app(app, size));
    }

    single_session_body_bottom(size)
}

pub(crate) fn single_session_body_bottom_base_for_total_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    total_lines: usize,
) -> f32 {
    if app.is_welcome_timeline_visible() {
        return (single_session_draft_top_for_total_lines(app, size, total_lines)
            - welcome_timeline_body_draft_gap())
        .max(single_session_body_top_for_app(app, size));
    }

    single_session_body_bottom(size)
}

pub(crate) fn single_session_body_bottom_for_total_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    total_lines: usize,
) -> f32 {
    single_session_layout_for_total_lines(app, size, total_lines).body_bottom()
}

pub(crate) fn streaming_activity_reserved_height(app: &SingleSessionApp) -> f32 {
    if !app.streaming_activity_pill_visible() {
        return 0.0;
    }

    let typography = single_session_typography_for_scale(app.text_scale());
    typography.body_size * typography.body_line_height
}

pub(crate) fn inline_widget_visible_text_height(app: &SingleSessionApp) -> f32 {
    let lines = app.render_inline_widget_visible_line_count();
    if lines == 0 {
        return 0.0;
    }
    let typography = single_session_typography_for_scale(app.text_scale());
    lines as f32 * inline_widget_line_height(app.render_inline_widget_kind(), &typography)
}

pub(crate) fn inline_widget_reserved_height(app: &SingleSessionApp) -> f32 {
    if app.render_inline_widget_line_count() == 0 {
        0.0
    } else {
        let padding_y = inline_widget_card_padding_y(app.render_inline_widget_kind());
        (inline_widget_visible_text_height(app) + padding_y * 2.0 + INLINE_WIDGET_BODY_GAP)
            * app.render_inline_widget_reveal_progress().clamp(0.0, 1.0)
    }
}

pub(crate) fn inline_widget_target_top(
    size: PhysicalSize<u32>,
    kind: Option<InlineWidgetKind>,
    ui_scale: f32,
    body_bottom: f32,
    welcome_chrome_visible: bool,
    welcome_chrome_offset_pixels: f32,
) -> f32 {
    if welcome_chrome_visible {
        fresh_welcome_visual_bottom_for_scale(size, ui_scale)
            + welcome_chrome_offset_pixels
            + fresh_welcome_inline_widget_gap_for_scale(ui_scale)
    } else {
        body_bottom + INLINE_WIDGET_BODY_GAP + inline_widget_card_padding_y(kind)
    }
}

#[cfg(test)]
#[expect(
    clippy::too_many_arguments,
    reason = "test-only geometry probe mirrors the render pipeline's parameters"
)]
pub(crate) fn inline_widget_body_and_card_vertical_geometry_for_test(
    size: PhysicalSize<u32>,
    kind: Option<InlineWidgetKind>,
    ui_scale: f32,
    body_base_bottom: f32,
    line_count: usize,
    text_width: f32,
    reveal_progress: f32,
    activity_reserved_height: f32,
) -> Option<(f32, f32)> {
    let typography = single_session_typography_for_scale(ui_scale);
    let padding_y = inline_widget_card_padding_y(kind);
    let visible_text_height = line_count as f32 * inline_widget_line_height(kind, &typography);
    let reserved_height =
        (visible_text_height + padding_y * 2.0 + INLINE_WIDGET_BODY_GAP) * reveal_progress;
    let body_bottom =
        (body_base_bottom - reserved_height - activity_reserved_height).max(PANEL_BODY_TOP_PADDING);
    let target_top = inline_widget_target_top(size, kind, ui_scale, body_bottom, false, 0.0);
    let bottom_limit =
        (body_base_bottom - activity_reserved_height).min(single_session_draft_top(size));
    inline_widget_card_layout_with_bottom_limit(
        size,
        kind,
        &typography,
        line_count,
        text_width,
        target_top,
        reveal_progress,
        bottom_limit,
    )
    .map(|layout| (body_bottom, layout.card.y))
}

pub(crate) fn single_session_body_bottom(size: PhysicalSize<u32>) -> f32 {
    single_session_draft_top(size) - 12.0
}

pub(crate) fn clip_rect_to_vertical_bounds(rect: Rect, top: f32, bottom: f32) -> Option<Rect> {
    let clipped_y = rect.y.max(top);
    let clipped_bottom = (rect.y + rect.height).min(bottom);
    (clipped_bottom > clipped_y).then_some(Rect {
        y: clipped_y,
        height: clipped_bottom - clipped_y,
        ..rect
    })
}

pub(crate) fn text_bounds_bottom(value: f32) -> i32 {
    value.ceil().clamp(0.0, i32::MAX as f32) as i32
}

pub(crate) fn single_session_visible_body(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
) -> Vec<String> {
    single_session_visible_styled_body(app, size)
        .into_iter()
        .map(|line| line.text)
        .collect()
}

pub(crate) fn single_session_visible_styled_body(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
) -> Vec<SingleSessionStyledLine> {
    single_session_visible_styled_body_for_tick(app, size, 0)
}

pub(crate) fn single_session_visible_styled_body_for_tick(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
) -> Vec<SingleSessionStyledLine> {
    single_session_body_viewport_for_tick(app, size, tick, 0.0).lines
}
