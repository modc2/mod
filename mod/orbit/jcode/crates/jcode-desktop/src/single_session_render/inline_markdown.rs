//! Inline markdown decorations over the transcript body: inline code/math cards, markdown pills, rule lines, span run extraction, and glyph-accurate span measurement.

use super::*;

pub(crate) fn push_single_session_inline_code_cards(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
) {
    let viewport = single_session_body_viewport_for_tick(app, size, tick, smooth_scroll_lines);
    push_single_session_inline_code_cards_from_viewport(
        vertices,
        app,
        size,
        &viewport,
        viewport.total_lines,
        None,
    );
}

/// A thread-local, lazily-initialized `FontSystem` used purely for measuring
/// glyph layout (inline-code/math pill bounds) during geometry building.
///
/// Building a `FontSystem` rescans every system font from disk, costing several
/// milliseconds per call. The inline-code/math card builder runs on every frame
/// whose visible window contains inline code or math, so constructing a fresh
/// `FontSystem` there made scrolling over code blocks janky (multi-ms spikes per
/// frame). Caching one per render thread keeps repeated measurement cheap. The
/// system is only used for transient measurement buffers, never for the glyphs
/// actually uploaded to the GPU, so reuse is safe.
pub(crate) fn with_measurement_font_system<R>(f: impl FnOnce(&mut FontSystem) -> R) -> R {
    thread_local! {
        static MEASUREMENT_FONT_SYSTEM: std::cell::RefCell<FontSystem> =
            std::cell::RefCell::new(FontSystem::new());
    }
    MEASUREMENT_FONT_SYSTEM.with(|cell| f(&mut cell.borrow_mut()))
}

/// Horizontal glyph bounds (left, right) for each inline-code span on a single
/// styled line, in line-local pixel coordinates (i.e. before the panel's left
/// padding is added). `None` entries mean the span could not be measured and the
/// caller should fall back to the cheap column-width estimate.
///
/// These bounds depend only on the line's own text/spans and the text scale: the
/// transcript body buffer is laid out with `Wrap::None`, so each logical line is
/// shaped independently and its glyph positions never depend on neighbouring
/// lines. That makes them perfectly cacheable per line, which is the whole point
/// of this helper.
pub(crate) type InlineCodeSpanBounds = Vec<Option<(f32, f32)>>;

/// Shape exactly one line and read the horizontal bounds of each of its
/// inline-code spans. This is the expensive step (cosmic-text Advanced shaping),
/// so callers should go through `inline_code_span_bounds_for_line`, which caches
/// the result per (line, scale).
pub(crate) fn shape_inline_code_span_bounds(
    line: &SingleSessionStyledLine,
    size: PhysicalSize<u32>,
    text_scale: f32,
) -> InlineCodeSpanBounds {
    let code_spans: Vec<&SingleSessionInlineSpan> = line
        .inline_spans
        .iter()
        .filter(|span| span.kind == SingleSessionInlineSpanKind::Code)
        .collect();
    if code_spans.is_empty() {
        return Vec::new();
    }
    with_measurement_font_system(|font_system| {
        let buffer = single_session_body_text_buffer_from_lines(
            font_system,
            std::slice::from_ref(line),
            size,
            text_scale,
        );
        let Some(layout_run) = buffer.layout_runs().next() else {
            return vec![None; code_spans.len()];
        };
        code_spans
            .iter()
            .map(|span| {
                layout_run
                    .highlight(
                        glyphon::Cursor::new(layout_run.line_i, span.start),
                        glyphon::Cursor::new(layout_run.line_i, span.end),
                    )
                    .and_then(|(left, width)| (width > 0.0).then_some((left, left + width)))
            })
            .collect()
    })
}

/// Cached per-line inline-code span bounds.
///
/// The inline-code/math pill builder runs on every frame whose visible window
/// contains inline code, and it previously re-shaped the ENTIRE visible viewport
/// into a throwaway buffer every frame just to read these bounds. During a
/// continuous scroll the viewport content barely changes frame-to-frame, so
/// caching the bounds keyed by the line's content hash + text scale turns that
/// full reshape into shaping only the one or two newly revealed lines. The cache
/// is bounded: once it grows past `MAX` entries it is cleared wholesale (cheap
/// and rare relative to the per-frame savings).
pub(crate) fn inline_code_span_bounds_for_line(
    line: &SingleSessionStyledLine,
    size: PhysicalSize<u32>,
    text_scale: f32,
) -> InlineCodeSpanBounds {
    const MAX_ENTRIES: usize = 8192;
    thread_local! {
        static INLINE_CODE_BOUNDS_CACHE: std::cell::RefCell<HashMap<u64, InlineCodeSpanBounds>> =
            std::cell::RefCell::new(HashMap::new());
    }

    // Glyph layout is invariant to content width here (Wrap::None) but does
    // depend on the rendered width bucket via font metrics rounding, so fold the
    // body content width into the key alongside the scale.
    let mut hasher = DefaultHasher::new();
    line.hash(&mut hasher);
    text_scale.to_bits().hash(&mut hasher);
    single_session_content_width(size)
        .to_bits()
        .hash(&mut hasher);
    let key = hasher.finish();

    INLINE_CODE_BOUNDS_CACHE.with(|cell| {
        if let Some(bounds) = cell.borrow().get(&key) {
            return bounds.clone();
        }
        let bounds = shape_inline_code_span_bounds(line, size, text_scale);
        let mut cache = cell.borrow_mut();
        if cache.len() >= MAX_ENTRIES {
            cache.clear();
        }
        cache.insert(key, bounds.clone());
        bounds
    })
}

pub(crate) fn push_single_session_inline_code_cards_from_viewport(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    viewport: &SingleSessionBodyViewport,
    total_lines: usize,
    inline_markdown_motion: Option<&InlineMarkdownPillMotionFrame>,
) {
    if !viewport
        .lines
        .iter()
        .any(single_session_line_has_inline_code_or_math)
    {
        return;
    }

    let text_scale = app.text_scale();
    let typography = single_session_typography_for_scale(text_scale);
    let line_height = typography.body_size * typography.body_line_height;
    let char_width = single_session_body_char_width_for_scale(text_scale);
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, total_lines);
    let card_height = inline_code_card_height(&typography);
    let radius = (5.0 * text_scale).clamp(4.0, 8.0);
    let horizontal_pad = (3.5 * text_scale).clamp(3.0, 6.0);
    let pill_context = InlineMarkdownPillGeometryContext {
        size,
        line_height,
        char_width,
        body_top,
        body_bottom,
        card_height,
        radius,
        horizontal_pad,
        top_offset_pixels: viewport.top_offset_pixels,
    };

    let mut occurrences = HashMap::new();
    for (line_index, line) in viewport.lines.iter().enumerate() {
        if !single_session_line_style_supports_inline_code_cards(line.style) {
            continue;
        }
        // The transcript body buffer is laid out with `Wrap::None`, so each
        // logical line occupies exactly one visual row: `line_top` is simply
        // `line_index * line_height`. That lets us avoid shaping the entire
        // viewport here and only (cache-)shape lines that actually carry inline
        // code spans below.
        let line_y = body_top + viewport.top_offset_pixels + line_index as f32 * line_height;
        let code_runs = single_session_inline_code_runs_for_line(line);
        let code_span_bounds = if code_runs.is_empty() {
            Vec::new()
        } else {
            inline_code_span_bounds_for_line(line, size, text_scale)
        };
        for (run_index, run) in code_runs.iter().enumerate() {
            let glyph_bounds = code_span_bounds.get(run_index).copied().flatten();
            let (x, width) = if let Some((glyph_left, glyph_right)) = glyph_bounds {
                let x = PANEL_TITLE_LEFT_PADDING + glyph_left - horizontal_pad;
                (x, glyph_right - glyph_left + horizontal_pad * 2.0)
            } else {
                (
                    PANEL_TITLE_LEFT_PADDING + run.start_column as f32 * char_width
                        - horizontal_pad,
                    run.column_count as f32 * char_width + horizontal_pad * 2.0,
                )
            };
            let clipped_right = (x + width).min(size.width as f32);
            if clipped_right <= x {
                continue;
            }
            let rect = Rect {
                x,
                y: line_y + (line_height - card_height) * 0.5,
                width: clipped_right - x,
                height: card_height,
            };
            let pill_run = InlineMarkdownPillRun {
                line: line_index,
                start_column: run.start_column,
                column_count: run.column_count,
                kind: InlineMarkdownPillKind::Code,
            };
            let motion_key =
                inline_markdown_pill_motion_key(&viewport.lines, &pill_run, &mut occurrences);
            let visual = inline_markdown_motion
                .and_then(|motion| motion.visual_for_key(motion_key))
                .unwrap_or_default();
            push_single_session_inline_markdown_pill_rect(
                vertices,
                rect,
                InlineMarkdownPillKind::Code,
                visual,
                pill_context,
            );
        }
        for run in single_session_inline_math_runs_for_line(line) {
            if code_runs.iter().any(|code_run| {
                inline_markdown_runs_overlap(
                    run.start_column,
                    run.column_count,
                    code_run.start_column,
                    code_run.column_count,
                )
            }) {
                continue;
            }
            let x =
                PANEL_TITLE_LEFT_PADDING + run.start_column as f32 * char_width - horizontal_pad;
            let width = run.column_count as f32 * char_width + horizontal_pad * 2.0;
            let clipped_right = (x + width).min(size.width as f32);
            if clipped_right <= x {
                continue;
            }
            let rect = Rect {
                x,
                y: line_y + (line_height - card_height) * 0.5,
                width: clipped_right - x,
                height: card_height,
            };
            let pill_run = InlineMarkdownPillRun {
                line: line_index,
                start_column: run.start_column,
                column_count: run.column_count,
                kind: InlineMarkdownPillKind::Math,
            };
            let motion_key =
                inline_markdown_pill_motion_key(&viewport.lines, &pill_run, &mut occurrences);
            let visual = inline_markdown_motion
                .and_then(|motion| motion.visual_for_key(motion_key))
                .unwrap_or_default();
            push_single_session_inline_markdown_pill_rect(
                vertices,
                rect,
                InlineMarkdownPillKind::Math,
                visual,
                pill_context,
            );
        }
    }

    if let Some(inline_markdown_motion) = inline_markdown_motion {
        for (run, visual) in inline_markdown_motion.exiting() {
            push_single_session_inline_markdown_pill_run(vertices, *run, *visual, pill_context);
        }
    }
}

#[derive(Clone, Copy)]
pub(crate) struct InlineMarkdownPillGeometryContext {
    pub(crate) size: PhysicalSize<u32>,
    pub(crate) line_height: f32,
    pub(crate) char_width: f32,
    pub(crate) body_top: f32,
    pub(crate) body_bottom: f32,
    pub(crate) card_height: f32,
    pub(crate) radius: f32,
    pub(crate) horizontal_pad: f32,
    pub(crate) top_offset_pixels: f32,
}

pub(crate) fn push_single_session_inline_markdown_pill_run(
    vertices: &mut Vec<Vertex>,
    run: InlineMarkdownPillRun,
    visual: InlineMarkdownPillVisual,
    context: InlineMarkdownPillGeometryContext,
) {
    let x = PANEL_TITLE_LEFT_PADDING + run.start_column as f32 * context.char_width
        - context.horizontal_pad;
    let width = run.column_count as f32 * context.char_width + context.horizontal_pad * 2.0;
    let clipped_right = (x + width).min(context.size.width as f32);
    if clipped_right <= x {
        return;
    }
    let line_y =
        context.body_top + context.top_offset_pixels + run.line as f32 * context.line_height;
    let rect = Rect {
        x,
        y: line_y + (context.line_height - context.card_height) * 0.5,
        width: clipped_right - x,
        height: context.card_height,
    };
    push_single_session_inline_markdown_pill_rect(vertices, rect, run.kind, visual, context);
}

pub(crate) fn push_single_session_inline_markdown_pill_rect(
    vertices: &mut Vec<Vertex>,
    rect: Rect,
    kind: InlineMarkdownPillKind,
    visual: InlineMarkdownPillVisual,
    context: InlineMarkdownPillGeometryContext,
) {
    if visual.opacity <= 0.001 {
        return;
    }
    let rect = inline_markdown_pill_visual_rect(rect, visual);
    let Some(rect) = clip_rect_to_vertical_bounds(rect, context.body_top, context.body_bottom)
    else {
        return;
    };
    push_rounded_rect(
        vertices,
        rect,
        context.radius,
        inline_markdown_pill_alpha(inline_markdown_pill_color(kind), visual.opacity),
        context.size,
    );
}

pub(crate) fn inline_markdown_pill_color(kind: InlineMarkdownPillKind) -> [f32; 4] {
    match kind {
        InlineMarkdownPillKind::Code => INLINE_CODE_BACKGROUND_COLOR,
        InlineMarkdownPillKind::Math => INLINE_MATH_BACKGROUND_COLOR,
    }
}

pub(crate) fn single_session_line_has_inline_code_or_math(line: &SingleSessionStyledLine) -> bool {
    line.inline_spans.iter().any(|span| {
        matches!(
            span.kind,
            SingleSessionInlineSpanKind::Code | SingleSessionInlineSpanKind::Math
        )
    }) || line.text.contains('$')
}

pub(crate) fn inline_code_card_height(typography: &SingleSessionTypography) -> f32 {
    let line_height = typography.body_size * typography.body_line_height;
    line_height + 2.0
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct SingleSessionInlineCodeRun {
    pub(crate) start_column: usize,
    pub(crate) column_count: usize,
}

pub(crate) fn single_session_inline_code_runs(text: &str) -> Vec<SingleSessionInlineCodeRun> {
    let mut runs = Vec::new();
    let mut search_start = 0;

    while let Some(open_rel) = text[search_start..].find('`') {
        let open = search_start + open_rel;
        let code_start = open + '`'.len_utf8();
        let Some(close_rel) = text[code_start..].find('`') else {
            break;
        };
        let close = code_start + close_rel;
        let after_close = close + '`'.len_utf8();
        let start_column = text[..open].chars().count();
        let column_count = text[open..after_close].chars().count();
        if column_count > 1 {
            runs.push(SingleSessionInlineCodeRun {
                start_column,
                column_count,
            });
        }
        search_start = after_close;
    }

    runs
}

pub(crate) fn single_session_inline_code_runs_for_line(
    line: &SingleSessionStyledLine,
) -> Vec<SingleSessionInlineCodeRun> {
    if line.inline_spans.is_empty() {
        return single_session_inline_code_runs(&line.text);
    }
    line.inline_spans
        .iter()
        .filter(|span| span.kind == SingleSessionInlineSpanKind::Code)
        .filter_map(|span| inline_code_run_from_span(&line.text, span))
        .collect()
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct SingleSessionInlineMathRun {
    pub(crate) start_column: usize,
    pub(crate) column_count: usize,
}

pub(crate) fn single_session_inline_math_runs(text: &str) -> Vec<SingleSessionInlineMathRun> {
    let mut runs = Vec::new();
    let mut search_start = 0;
    let code_ranges = single_session_inline_code_byte_ranges(text);

    while let Some(open_rel) = text[search_start..].find('$') {
        let open = search_start + open_rel;
        if byte_index_inside_any_range(open, &code_ranges) {
            search_start = open + '$'.len_utf8();
            continue;
        }
        if text[open..].starts_with("$$") {
            search_start = open + '$'.len_utf8();
            continue;
        }
        let math_start = open + '$'.len_utf8();
        let Some(close_rel) = text[math_start..].find('$') else {
            break;
        };
        let close = math_start + close_rel;
        if text[close..].starts_with("$$") || close == math_start {
            search_start = close + '$'.len_utf8();
            continue;
        }
        let after_close = close + '$'.len_utf8();
        if byte_range_overlaps_any_range(open, after_close, &code_ranges) {
            search_start = after_close;
            continue;
        }
        let start_column = text[..open].chars().count();
        let column_count = text[open..after_close].chars().count();
        runs.push(SingleSessionInlineMathRun {
            start_column,
            column_count,
        });
        search_start = after_close;
    }

    runs
}

pub(crate) fn single_session_inline_math_runs_for_line(
    line: &SingleSessionStyledLine,
) -> Vec<SingleSessionInlineMathRun> {
    if line.inline_spans.is_empty() {
        return single_session_inline_math_runs(&line.text);
    }
    line.inline_spans
        .iter()
        .filter(|span| span.kind == SingleSessionInlineSpanKind::Math)
        .filter_map(|span| inline_math_run_from_span(&line.text, span))
        .collect()
}

pub(crate) fn single_session_inline_markdown_pill_runs(
    lines: &[SingleSessionStyledLine],
) -> Vec<InlineMarkdownPillRun> {
    let mut runs = Vec::new();
    for (line_index, line) in lines.iter().enumerate() {
        if !single_session_line_style_supports_inline_code_cards(line.style) {
            continue;
        }
        let code_runs = single_session_inline_code_runs_for_line(line);
        runs.extend(code_runs.iter().map(|run| InlineMarkdownPillRun {
            line: line_index,
            start_column: run.start_column,
            column_count: run.column_count,
            kind: InlineMarkdownPillKind::Code,
        }));
        runs.extend(
            single_session_inline_math_runs_for_line(line)
                .into_iter()
                .filter(|math_run| {
                    !code_runs.iter().any(|code_run| {
                        inline_markdown_runs_overlap(
                            math_run.start_column,
                            math_run.column_count,
                            code_run.start_column,
                            code_run.column_count,
                        )
                    })
                })
                .map(|run| InlineMarkdownPillRun {
                    line: line_index,
                    start_column: run.start_column,
                    column_count: run.column_count,
                    kind: InlineMarkdownPillKind::Math,
                }),
        );
    }
    runs
}

pub(crate) fn inline_code_run_from_span(
    text: &str,
    span: &SingleSessionInlineSpan,
) -> Option<SingleSessionInlineCodeRun> {
    let (start_column, column_count) = inline_run_columns_from_span(text, span)?;
    (column_count > 0).then_some(SingleSessionInlineCodeRun {
        start_column,
        column_count,
    })
}

pub(crate) fn inline_math_run_from_span(
    text: &str,
    span: &SingleSessionInlineSpan,
) -> Option<SingleSessionInlineMathRun> {
    let (start_column, column_count) = inline_run_columns_from_span(text, span)?;
    (column_count > 0).then_some(SingleSessionInlineMathRun {
        start_column,
        column_count,
    })
}

pub(crate) fn inline_run_columns_from_span(
    text: &str,
    span: &SingleSessionInlineSpan,
) -> Option<(usize, usize)> {
    if span.start >= span.end || span.end > text.len() {
        return None;
    }
    let content = text.get(span.start..span.end)?;
    let start_column = text.get(..span.start)?.chars().count();
    let column_count = content.chars().count();
    Some((start_column, column_count))
}

pub(crate) fn single_session_inline_code_byte_ranges(text: &str) -> Vec<(usize, usize)> {
    let mut ranges = Vec::new();
    let mut search_start = 0;

    while let Some(open_rel) = text[search_start..].find('`') {
        let open = search_start + open_rel;
        let code_start = open + '`'.len_utf8();
        let Some(close_rel) = text[code_start..].find('`') else {
            break;
        };
        let close = code_start + close_rel;
        let after_close = close + '`'.len_utf8();
        ranges.push((open, after_close));
        search_start = after_close;
    }

    ranges
}

pub(crate) fn byte_index_inside_any_range(index: usize, ranges: &[(usize, usize)]) -> bool {
    ranges
        .iter()
        .any(|(start, end)| *start <= index && index < *end)
}

pub(crate) fn byte_range_overlaps_any_range(
    start: usize,
    end: usize,
    ranges: &[(usize, usize)],
) -> bool {
    ranges
        .iter()
        .any(|(range_start, range_end)| start < *range_end && *range_start < end)
}

pub(crate) fn inline_markdown_runs_overlap(
    start_a: usize,
    count_a: usize,
    start_b: usize,
    count_b: usize,
) -> bool {
    let end_a = start_a.saturating_add(count_a);
    let end_b = start_b.saturating_add(count_b);
    start_a < end_b && start_b < end_a
}

pub(crate) fn single_session_line_style_supports_inline_code_cards(
    style: SingleSessionLineStyle,
) -> bool {
    matches!(
        style,
        SingleSessionLineStyle::Assistant
            | SingleSessionLineStyle::AssistantHeading
            | SingleSessionLineStyle::AssistantQuote
            | SingleSessionLineStyle::AssistantLink
            | SingleSessionLineStyle::AssistantMedia
    )
}

pub(crate) fn push_single_session_markdown_rule_lines(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
) {
    let viewport = single_session_body_viewport_for_tick(app, size, tick, smooth_scroll_lines);
    push_single_session_markdown_rule_lines_from_viewport(
        vertices,
        app,
        size,
        &viewport,
        viewport.total_lines,
    );
}

pub(crate) fn push_single_session_markdown_rule_lines_from_viewport(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    viewport: &SingleSessionBodyViewport,
    total_lines: usize,
) {
    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, total_lines);
    let left = PANEL_TITLE_LEFT_PADDING - 2.0;
    let right = single_session_content_right(size).max(left + 1.0);
    let thickness = (1.7 * app.text_scale()).clamp(1.0, 3.0);

    for (line_index, line) in viewport.lines.iter().enumerate() {
        if !is_single_session_markdown_rule_line(line) {
            continue;
        }
        let center_y = body_top
            + viewport.top_offset_pixels
            + line_index as f32 * line_height
            + line_height * 0.5;
        let rect = Rect {
            x: left,
            y: center_y - thickness * 0.5,
            width: right - left,
            height: thickness,
        };
        let Some(rect) = clip_rect_to_vertical_bounds(rect, body_top, body_bottom) else {
            continue;
        };
        push_rounded_rect(vertices, rect, thickness, MARKDOWN_RULE_COLOR, size);
    }
}

pub(crate) fn is_single_session_markdown_rule_line(line: &SingleSessionStyledLine) -> bool {
    if line.style != SingleSessionLineStyle::Meta {
        return false;
    }
    let trimmed = line.text.trim();
    trimmed.chars().count() >= 3 && trimmed.chars().all(|ch| ch == '─')
}
