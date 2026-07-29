//! Welcome hero and fresh-welcome ambient rendering: handwriting stroke reveal, runtime mask specs, aurora ribbons, gradient primitives, and welcome-timeline offsets.

use super::*;

#[derive(Clone, Copy, Debug)]
pub(crate) struct WelcomeHeroStrokeSegment {
    pub(crate) start: [f32; 2],
    pub(crate) end: [f32; 2],
    pub(crate) start_progress: f32,
    pub(crate) end_progress: f32,
}

#[derive(Clone, Debug)]
pub(crate) struct WelcomeHeroRuntimeMaskSpec {
    pub(crate) phrase: String,
    pub(crate) rect: Rect,
    pub(crate) font_size: f32,
}

#[cfg(test)]
pub(crate) fn welcome_hero_reveal_progress_for_tick(spinner_tick: u64) -> f32 {
    let elapsed =
        Duration::from_millis(spinner_tick.saturating_mul(DESKTOP_SPINNER_FRAME_MS as u64));
    welcome_hero_reveal_progress_for_elapsed(elapsed)
}

pub(crate) fn welcome_hero_reveal_progress_for_elapsed(elapsed: Duration) -> f32 {
    const REVEAL_DURATION: Duration = Duration::from_millis(1350);
    const FIRST_INK_PROGRESS: f32 = 0.018;

    if crate::animation::desktop_reduced_motion_enabled() {
        return 1.0;
    }

    let raw = (elapsed.as_secs_f32() / REVEAL_DURATION.as_secs_f32()).clamp(0.0, 1.0);
    if raw >= 1.0 {
        return 1.0;
    }

    let eased = ease_in_out_cubic(raw);
    FIRST_INK_PROGRESS + (1.0 - FIRST_INK_PROGRESS) * eased
}

pub(crate) fn welcome_hero_runtime_mask_supported(phrase: &str) -> bool {
    let enabled = std::env::var_os("JCODE_DESKTOP_RUNTIME_HERO_MASK").is_none_or(|value| {
        !matches!(
            value.to_string_lossy().trim().to_ascii_lowercase().as_str(),
            "" | "0" | "false" | "off" | "no"
        )
    });
    enabled && phrase.trim().eq_ignore_ascii_case("Hello there")
}

pub(crate) fn welcome_hero_runtime_mask_rect(
    size: PhysicalSize<u32>,
    ui_scale: f32,
    y_offset: f32,
) -> Rect {
    let (hero_min, hero_max) = glyph_welcome_hero_bounds(size, ui_scale);
    Rect {
        x: hero_min[0],
        y: hero_min[1] + y_offset,
        width: (hero_max[0] - hero_min[0]).max(1.0),
        height: (hero_max[1] - hero_min[1]).max(1.0),
    }
}

pub(crate) fn welcome_hero_runtime_font_size(size: PhysicalSize<u32>, ui_scale: f32) -> f32 {
    glyph_welcome_hero_font_size(size, ui_scale)
}

pub(crate) fn welcome_hero_runtime_mask_spec_for_total_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    total_lines: usize,
) -> Option<WelcomeHeroRuntimeMaskSpec> {
    let y_offset = welcome_timeline_visual_offset_pixels_for_total_lines(
        app,
        size,
        smooth_scroll_lines,
        total_lines,
    );
    if !welcome_timeline_chrome_visible(app, size, y_offset) {
        return None;
    }
    welcome_hero_runtime_mask_spec_for_phrase(
        &app.welcome_hero_text(),
        size,
        app.text_scale(),
        y_offset,
    )
}

pub(crate) fn welcome_hero_runtime_mask_spec_for_phrase(
    phrase: &str,
    size: PhysicalSize<u32>,
    ui_scale: f32,
    y_offset: f32,
) -> Option<WelcomeHeroRuntimeMaskSpec> {
    if !welcome_hero_runtime_mask_supported(phrase) {
        return None;
    }
    Some(WelcomeHeroRuntimeMaskSpec {
        phrase: phrase.to_string(),
        rect: welcome_hero_runtime_mask_rect(size, ui_scale, y_offset),
        font_size: welcome_hero_runtime_font_size(size, ui_scale),
    })
}

pub(crate) fn welcome_hero_normalized_stroke_segments(
    phrase: &str,
) -> Vec<WelcomeHeroStrokeSegment> {
    let paths = handwritten_welcome_paths_for_phrase(phrase);
    let total_length = stroke_paths_length(&paths);
    if total_length <= 0.001 {
        return Vec::new();
    }

    let (source_min, source_max) = stroke_paths_bounds(&paths);
    let source_width = (source_max[0] - source_min[0]).max(0.001);
    let source_height = (source_max[1] - source_min[1]).max(0.001);
    let normalize = |point: [f32; 2]| -> [f32; 2] {
        [
            ((point[0] - source_min[0]) / source_width).clamp(0.0, 1.0),
            ((point[1] - source_min[1]) / source_height).clamp(0.0, 1.0),
        ]
    };

    let mut cursor = 0.0;
    let mut segments = Vec::new();
    for path in &paths {
        for pair in path.windows(2) {
            let start = pair[0];
            let end = pair[1];
            let segment_length = distance(start, end);
            if segment_length <= 0.001 {
                continue;
            }
            let start_progress = cursor / total_length;
            cursor += segment_length;
            let end_progress = (cursor / total_length).clamp(start_progress, 1.0);
            segments.push(WelcomeHeroStrokeSegment {
                start: normalize(start),
                end: normalize(end),
                start_progress,
                end_progress,
            });
        }
    }
    segments
}

pub(crate) fn welcome_hero_reveal_is_active(progress: f32) -> bool {
    progress < 0.999
}

pub(crate) fn push_fresh_welcome_ambient(
    vertices: &mut Vec<Vertex>,
    size: PhysicalSize<u32>,
    tick: u64,
    y_offset: f32,
) {
    let draft_top = single_session_draft_top(size);
    let usable_height = (draft_top - PANEL_BODY_TOP_PADDING).max(180.0);
    let t = tick as f32 * 0.055;

    push_aurora_ribbon(
        vertices,
        size,
        PANEL_BODY_TOP_PADDING + usable_height * 0.18 + (t * 0.60).sin() * 18.0 + y_offset,
        usable_height * 0.30,
        t * 0.85,
        WELCOME_AURORA_BLUE,
        WELCOME_AURORA_VIOLET,
    );
    push_aurora_ribbon(
        vertices,
        size,
        PANEL_BODY_TOP_PADDING + usable_height * 0.39 + (t * 0.47).cos() * 24.0 + y_offset,
        usable_height * 0.34,
        t * -0.72 + 1.8,
        WELCOME_AURORA_MINT,
        WELCOME_AURORA_BLUE,
    );
    push_aurora_ribbon(
        vertices,
        size,
        PANEL_BODY_TOP_PADDING + usable_height * 0.58 + (t * 0.52).sin() * 16.0 + y_offset,
        usable_height * 0.24,
        t * 0.64 + 3.2,
        WELCOME_AURORA_WARM,
        WELCOME_AURORA_MINT,
    );
}

pub(crate) fn push_handwritten_welcome_hero_with_offset(
    vertices: &mut Vec<Vertex>,
    phrase: &str,
    size: PhysicalSize<u32>,
    ui_scale: f32,
    reveal_progress: f32,
    y_offset: f32,
) {
    if !welcome_hero_approx_bounds_visible(size, ui_scale, y_offset) {
        return;
    }

    let progress = reveal_progress.clamp(0.0, 1.0);
    if !welcome_hero_reveal_is_active(progress) {
        return;
    }

    if welcome_hero_runtime_mask_supported(phrase) {
        return;
    }

    let paths = handwritten_welcome_paths_for_phrase(phrase);
    let total_length = stroke_paths_length(&paths);
    if total_length <= 0.0 {
        return;
    }

    let (bounds_min, bounds_max) = glyph_welcome_hero_bounds(size, ui_scale);
    let hero_height = (bounds_max[1] - bounds_min[1]).max(1.0);
    let baseline_lift = hero_height * 0.11;
    let bounds_min = [bounds_min[0], bounds_min[1] + y_offset - baseline_lift];
    let bounds_max = [bounds_max[0], bounds_max[1] + y_offset - baseline_lift];
    let (source_min, source_max) = stroke_paths_bounds(&paths);
    let source_width = (source_max[0] - source_min[0]).max(1.0);
    let scale = (bounds_max[0] - bounds_min[0]) / source_width;
    let origin = [
        bounds_min[0] - source_min[0] * scale,
        bounds_min[1] - source_min[1] * scale,
    ];
    let thickness = (scale * 0.036).clamp(1.8, 4.6);
    let mut remaining = total_length * progress;
    let mut lead = None;

    for path in &paths {
        for pair in path.windows(2) {
            let a = pair[0];
            let b = pair[1];
            let segment_length = distance(a, b);
            if segment_length <= 0.001 || remaining <= 0.0 {
                continue;
            }
            let draw_fraction = (remaining / segment_length).clamp(0.0, 1.0);
            let end = lerp_point(a, b, draw_fraction);
            let pa = transform_handwriting_point(a, origin, scale);
            let pb = transform_handwriting_point(end, origin, scale);
            push_stroke_segment(vertices, pa, pb, thickness, WELCOME_HANDWRITING_COLOR, size);
            lead = Some(pb);
            remaining -= segment_length;
            if draw_fraction < 1.0 {
                break;
            }
        }
    }

    if let Some(point) = lead
        && (0.01..0.995).contains(&progress)
    {
        push_stroke_dot(
            vertices,
            point,
            thickness * 1.65,
            WELCOME_HANDWRITING_COLOR,
            size,
        );
    }
}

pub(crate) fn welcome_timeline_chrome_visible(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    y_offset: f32,
) -> bool {
    app.is_welcome_timeline_visible()
        && (!app.has_welcome_timeline_transcript()
            || welcome_hero_approx_bounds_visible(size, app.text_scale(), y_offset))
}

pub(crate) fn welcome_hero_approx_bounds_visible(
    size: PhysicalSize<u32>,
    ui_scale: f32,
    y_offset: f32,
) -> bool {
    let body_top = PANEL_BODY_TOP_PADDING;
    let draft_top = single_session_draft_top(size);
    let top = body_top + (draft_top - body_top) * 0.18 + y_offset;
    let bottom = body_top + (draft_top - body_top) * 0.74 * ui_scale + y_offset;
    bottom >= -64.0 && top <= size.height as f32 + 64.0
}

pub(crate) fn welcome_timeline_visual_offset_pixels(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
) -> f32 {
    welcome_timeline_visual_offset_pixels_for_total_lines(
        app,
        size,
        smooth_scroll_lines,
        welcome_timeline_total_body_lines(app, size),
    )
}

pub(crate) fn welcome_timeline_visual_offset_pixels_for_total_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    total_lines: usize,
) -> f32 {
    if !app.is_welcome_timeline_visible() {
        return 0.0;
    }

    if !app.has_welcome_timeline_transcript() {
        return fresh_welcome_inline_widget_visual_offset(app, size);
    }

    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, total_lines);
    let visible_lines = (((body_bottom - body_top).max(line_height)) / line_height)
        .floor()
        .max(1.0);
    let total_lines = total_lines as f32;
    if total_lines <= visible_lines {
        return 0.0;
    }

    let max_scroll = (total_lines - visible_lines).max(0.0);
    let scroll = (app.body_scroll_lines + smooth_scroll_lines).clamp(0.0, max_scroll);
    let top_line = (total_lines - scroll - visible_lines).max(0.0);
    -top_line * line_height
}

#[cfg(test)]
pub(crate) fn handwritten_welcome_bounds(size: PhysicalSize<u32>) -> ([f32; 2], [f32; 2]) {
    handwritten_welcome_bounds_for_phrase(size, handwritten_welcome_phrase(0))
}

#[cfg(test)]
pub(crate) fn handwritten_welcome_bounds_for_phrase(
    size: PhysicalSize<u32>,
    phrase: &str,
) -> ([f32; 2], [f32; 2]) {
    handwritten_welcome_bounds_for_phrase_with_scale(size, phrase, 1.0)
}

pub(crate) fn handwritten_welcome_bounds_for_phrase_with_scale(
    size: PhysicalSize<u32>,
    phrase: &str,
    ui_scale: f32,
) -> ([f32; 2], [f32; 2]) {
    let paths = handwritten_welcome_paths_for_phrase(phrase);
    let (source_min, source_max) = stroke_paths_bounds(&paths);
    let source_width = (source_max[0] - source_min[0]).max(1.0);
    let source_height = (source_max[1] - source_min[1]).max(1.0);
    let normal_draft_top = single_session_draft_top(size);
    let target_width = size.width as f32 * 0.68 * ui_scale;
    let scale = target_width / source_width;
    let left = (size.width as f32 - target_width) * 0.5;
    let top = PANEL_BODY_TOP_PADDING + (normal_draft_top - PANEL_BODY_TOP_PADDING) * 0.31;
    (
        [left, top],
        [left + target_width, top + source_height * scale],
    )
}

pub(crate) fn glyph_welcome_hero_bounds(
    size: PhysicalSize<u32>,
    ui_scale: f32,
) -> ([f32; 2], [f32; 2]) {
    let normal_draft_top = single_session_draft_top(size);
    let target_width = size.width as f32 * 0.68 * ui_scale;
    let font_size = glyph_welcome_hero_font_size(size, ui_scale);
    let left = (size.width as f32 - target_width) * 0.5;
    let top = PANEL_BODY_TOP_PADDING + (normal_draft_top - PANEL_BODY_TOP_PADDING) * 0.31;
    ([left, top], [left + target_width, top + font_size * 1.35])
}

pub(crate) fn glyph_welcome_hero_font_size(size: PhysicalSize<u32>, ui_scale: f32) -> f32 {
    let normal_draft_top = single_session_draft_top(size);
    let available_height = (normal_draft_top - PANEL_BODY_TOP_PADDING).max(1.0);
    (available_height * 0.24 * ui_scale).clamp(82.0 * ui_scale, 170.0 * ui_scale)
}

pub(crate) fn stroke_paths_bounds(paths: &[Vec<[f32; 2]>]) -> ([f32; 2], [f32; 2]) {
    let mut min = [f32::INFINITY, f32::INFINITY];
    let mut max = [f32::NEG_INFINITY, f32::NEG_INFINITY];
    for point in paths.iter().flatten() {
        min[0] = min[0].min(point[0]);
        min[1] = min[1].min(point[1]);
        max[0] = max[0].max(point[0]);
        max[1] = max[1].max(point[1]);
    }
    if !min[0].is_finite() || !max[0].is_finite() {
        ([0.0, 0.0], [1.0, 1.0])
    } else {
        (min, max)
    }
}

pub(crate) fn stroke_paths_length(paths: &[Vec<[f32; 2]>]) -> f32 {
    paths
        .iter()
        .map(|path| {
            path.windows(2)
                .map(|pair| distance(pair[0], pair[1]))
                .sum::<f32>()
        })
        .sum()
}

pub(crate) fn transform_handwriting_point(
    point: [f32; 2],
    origin: [f32; 2],
    scale: f32,
) -> [f32; 2] {
    [origin[0] + point[0] * scale, origin[1] + point[1] * scale]
}

pub(crate) fn push_stroke_segment(
    vertices: &mut Vec<Vertex>,
    a: [f32; 2],
    b: [f32; 2],
    thickness: f32,
    color: [f32; 4],
    size: PhysicalSize<u32>,
) {
    let dx = b[0] - a[0];
    let dy = b[1] - a[1];
    let length = (dx * dx + dy * dy).sqrt();
    if length <= 0.001 {
        return;
    }
    let nx = -dy / length * thickness * 0.5;
    let ny = dx / length * thickness * 0.5;
    let p0 = [a[0] + nx, a[1] + ny];
    let p1 = [b[0] + nx, b[1] + ny];
    let p2 = [b[0] - nx, b[1] - ny];
    let p3 = [a[0] - nx, a[1] - ny];
    push_pixel_triangle(vertices, p0, p1, p2, color, size);
    push_pixel_triangle(vertices, p0, p2, p3, color, size);
    push_stroke_dot(vertices, a, thickness * 0.52, color, size);
    push_stroke_dot(vertices, b, thickness * 0.52, color, size);
}

pub(crate) fn push_stroke_dot(
    vertices: &mut Vec<Vertex>,
    center: [f32; 2],
    radius: f32,
    color: [f32; 4],
    size: PhysicalSize<u32>,
) {
    let segments = 12;
    for index in 0..segments {
        let a = index as f32 / segments as f32 * std::f32::consts::TAU;
        let b = (index + 1) as f32 / segments as f32 * std::f32::consts::TAU;
        push_pixel_triangle(
            vertices,
            center,
            [center[0] + a.cos() * radius, center[1] + a.sin() * radius],
            [center[0] + b.cos() * radius, center[1] + b.sin() * radius],
            color,
            size,
        );
    }
}

pub(crate) fn push_aurora_ribbon(
    vertices: &mut Vec<Vertex>,
    size: PhysicalSize<u32>,
    center_y: f32,
    height: f32,
    phase: f32,
    left_color: [f32; 4],
    right_color: [f32; 4],
) {
    let width = size.width as f32;
    let segments = 18;
    for segment in 0..segments {
        let a = segment as f32 / segments as f32;
        let b = (segment + 1) as f32 / segments as f32;
        let x0 = -width * 0.08 + a * width * 1.16;
        let x1 = -width * 0.08 + b * width * 1.16;
        let wave0 = (a * std::f32::consts::TAU * 1.35 + phase).sin() * height * 0.23
            + (a * std::f32::consts::TAU * 2.10 + phase * 0.7).cos() * height * 0.10;
        let wave1 = (b * std::f32::consts::TAU * 1.35 + phase).sin() * height * 0.23
            + (b * std::f32::consts::TAU * 2.10 + phase * 0.7).cos() * height * 0.10;
        let color0 = mix_color(left_color, right_color, a);
        let color1 = mix_color(left_color, right_color, b);
        let edge0 = transparent(color0);
        let edge1 = transparent(color1);
        let top0 = [x0, center_y + wave0 - height * 0.55];
        let mid0 = [x0, center_y + wave0];
        let bot0 = [x0, center_y + wave0 + height * 0.55];
        let top1 = [x1, center_y + wave1 - height * 0.55];
        let mid1 = [x1, center_y + wave1];
        let bot1 = [x1, center_y + wave1 + height * 0.55];
        push_gradient_quad(
            vertices, top0, mid0, mid1, top1, edge0, color0, color1, edge1, size,
        );
        push_gradient_quad(
            vertices, mid0, bot0, bot1, mid1, color0, edge0, edge1, color1, size,
        );
    }
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_gradient_quad(
    vertices: &mut Vec<Vertex>,
    a: [f32; 2],
    b: [f32; 2],
    c: [f32; 2],
    d: [f32; 2],
    a_color: [f32; 4],
    b_color: [f32; 4],
    c_color: [f32; 4],
    d_color: [f32; 4],
    size: PhysicalSize<u32>,
) {
    push_gradient_triangle(vertices, a, b, c, a_color, b_color, c_color, size);
    push_gradient_triangle(vertices, a, c, d, a_color, c_color, d_color, size);
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn push_gradient_triangle(
    vertices: &mut Vec<Vertex>,
    a: [f32; 2],
    b: [f32; 2],
    c: [f32; 2],
    a_color: [f32; 4],
    b_color: [f32; 4],
    c_color: [f32; 4],
    size: PhysicalSize<u32>,
) {
    vertices.extend_from_slice(&[
        Vertex {
            position: pixel_to_ndc(a, size),
            color: a_color,
        },
        Vertex {
            position: pixel_to_ndc(b, size),
            color: b_color,
        },
        Vertex {
            position: pixel_to_ndc(c, size),
            color: c_color,
        },
    ]);
}
