use super::*;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct StreamingTextArrivalStyle {
    pub(crate) opacity: f32,
    pub(crate) y_offset_pixels: f32,
    pub(crate) active: bool,
}

pub(crate) fn streaming_text_arrival_style_for_elapsed(
    elapsed: Duration,
) -> StreamingTextArrivalStyle {
    if animation::desktop_reduced_motion_enabled() {
        return StreamingTextArrivalStyle {
            opacity: 1.0,
            y_offset_pixels: 0.0,
            active: false,
        };
    }

    let progress =
        (elapsed.as_secs_f32() / STREAMING_TEXT_FADE_DURATION.as_secs_f32()).clamp(0.0, 1.0);
    if progress >= 1.0 {
        return StreamingTextArrivalStyle {
            opacity: 1.0,
            y_offset_pixels: 0.0,
            active: false,
        };
    }
    let eased = animation::ease_out_cubic(progress);
    StreamingTextArrivalStyle {
        opacity: STREAMING_TEXT_FADE_START_OPACITY
            + (1.0 - STREAMING_TEXT_FADE_START_OPACITY) * eased,
        y_offset_pixels: STREAMING_TEXT_RISE_START_OFFSET_PIXELS * (1.0 - eased),
        active: true,
    }
}

#[cfg_attr(not(test), allow(dead_code))]
pub(crate) fn streaming_text_fade_opacity_for_elapsed(elapsed: Duration) -> (f32, bool) {
    let style = streaming_text_arrival_style_for_elapsed(elapsed);
    (style.opacity, style.active)
}

pub(crate) fn streaming_text_fade_start_after_len_change(
    previous_len: usize,
    next_len: usize,
    current_started_at: Option<Instant>,
    now: Instant,
) -> Option<Instant> {
    if next_len == 0 {
        return None;
    }

    let fade_active = current_started_at.is_some_and(|started_at| {
        now.saturating_duration_since(started_at) < STREAMING_TEXT_FADE_DURATION
    });
    if fade_active {
        return current_started_at;
    }

    // Only fade in the beginning of a streaming response. Restarting after
    // every slow delta dims the already-visible response and reads as flicker.
    if previous_len == 0 && next_len > 0 {
        Some(now)
    } else {
        None
    }
}

pub(crate) fn streaming_text_handoff_style_for_elapsed(
    elapsed: Duration,
) -> StreamingTextArrivalStyle {
    if animation::desktop_reduced_motion_enabled() {
        return StreamingTextArrivalStyle {
            opacity: 0.0,
            y_offset_pixels: 0.0,
            active: false,
        };
    }

    let progress =
        (elapsed.as_secs_f32() / STREAMING_TEXT_HANDOFF_DURATION.as_secs_f32()).clamp(0.0, 1.0);
    if progress >= 1.0 {
        return StreamingTextArrivalStyle {
            opacity: 0.0,
            y_offset_pixels: 0.0,
            active: false,
        };
    }

    let eased = animation::ease_out_cubic(progress);
    StreamingTextArrivalStyle {
        opacity: STREAMING_TEXT_HANDOFF_START_OPACITY * (1.0 - eased),
        y_offset_pixels: 0.0,
        active: true,
    }
}

/// Body cache key that also tracks how much of the streaming response is
/// revealed, so the cached wrapped lines rebuild as the reveal advances.
pub(crate) fn streaming_reveal_body_cache_key(
    rendered_body_key: u64,
    streaming_response_empty: bool,
    revealed_bytes: usize,
) -> u64 {
    if streaming_response_empty {
        return rendered_body_key;
    }
    let mut hasher = DefaultHasher::new();
    rendered_body_key.hash(&mut hasher);
    revealed_bytes.hash(&mut hasher);
    hasher.finish()
}

pub(crate) fn streaming_text_handoff_start_after_len_change(
    previous_len: usize,
    next_len: usize,
    has_visible_streaming_buffer: bool,
    current_started_at: Option<Instant>,
    now: Instant,
) -> Option<Instant> {
    if animation::desktop_reduced_motion_enabled() || next_len > 0 {
        return None;
    }

    if previous_len > 0 && has_visible_streaming_buffer {
        return Some(now);
    }

    current_started_at.filter(|started_at| {
        now.saturating_duration_since(*started_at) < STREAMING_TEXT_HANDOFF_DURATION
    })
}
pub(crate) const DESKTOP_120FPS_FRAME_BUDGET: Duration = Duration::from_micros(8_333);
pub(crate) const DESKTOP_PRESENT_STALL_BUDGET: Duration = Duration::from_millis(33);
pub(crate) const DESKTOP_INPUT_LATENCY_BUDGET: Duration = Duration::from_millis(25);
pub(crate) const DESKTOP_NO_PAINT_BUDGET: Duration = Duration::from_millis(250);
pub(crate) const DESKTOP_FRAME_PROFILE_REPORT_INTERVAL: Duration = Duration::from_secs(1);

pub(crate) const CLEAR_COLOR: wgpu::Color = wgpu::Color {
    r: 0.955,
    g: 0.965,
    b: 0.985,
    a: 1.0,
};

pub(crate) const BACKGROUND_TOP_LEFT: [f32; 4] = [0.890, 0.930, 1.000, 1.0];
pub(crate) const BACKGROUND_TOP_RIGHT: [f32; 4] = [0.960, 0.910, 1.000, 1.0];
pub(crate) const BACKGROUND_BOTTOM_RIGHT: [f32; 4] = [0.875, 0.980, 0.930, 1.0];
pub(crate) const BACKGROUND_BOTTOM_LEFT: [f32; 4] = [0.945, 0.960, 0.995, 1.0];
pub(crate) const FOCUS_RING_COLOR: [f32; 4] = [0.135, 0.155, 0.205, 0.90];
pub(crate) const NAV_STATUS_COLOR: [f32; 4] = [0.145, 0.165, 0.220, 1.0];
pub(crate) const INSERT_STATUS_COLOR: [f32; 4] = [0.245, 0.395, 0.340, 1.0];
pub(crate) const STATUS_PREVIEW_ACTIVE_GROUP_COLOR: [f32; 4] = [0.953, 0.965, 0.984, 0.16];
pub(crate) const STATUS_PREVIEW_EMPTY_FOCUSED_COLOR: [f32; 4] = [0.953, 0.965, 0.984, 0.50];
pub(crate) const STATUS_PREVIEW_VIEWPORT_COLOR: [f32; 4] = [0.953, 0.965, 0.984, 0.78];
pub(crate) const WORKSPACE_NUMBER_COLOR: [f32; 4] = [0.953, 0.965, 0.984, 0.90];
pub(crate) const STATUS_TEXT_COLOR: [f32; 4] = [0.953, 0.965, 0.984, 0.88];
pub(crate) const PANEL_TITLE_COLOR: [f32; 4] = [0.010, 0.014, 0.025, 1.0];
pub(crate) const PANEL_BODY_COLOR: [f32; 4] = [0.008, 0.012, 0.020, 1.0];
pub(crate) const ASSISTANT_TEXT_COLOR: [f32; 4] = [0.026, 0.034, 0.052, 1.0];
pub(crate) const ASSISTANT_HEADING_TEXT_COLOR: [f32; 4] = [0.030, 0.095, 0.300, 1.0];
pub(crate) const ASSISTANT_QUOTE_TEXT_COLOR: [f32; 4] = [0.210, 0.090, 0.355, 1.0];
pub(crate) const ASSISTANT_TABLE_TEXT_COLOR: [f32; 4] = [0.000, 0.155, 0.185, 1.0];
pub(crate) const ASSISTANT_LINK_TEXT_COLOR: [f32; 4] = [0.000, 0.170, 0.430, 1.0];
pub(crate) const USER_TEXT_COLOR: [f32; 4] = [0.012, 0.030, 0.180, 1.0];
pub(crate) const USER_CONTINUATION_TEXT_COLOR: [f32; 4] = [0.018, 0.035, 0.155, 1.0];
pub(crate) const TOOL_TEXT_COLOR: [f32; 4] = [0.150, 0.095, 0.325, 1.0];
pub(crate) const TOOL_DETAIL_TEXT_COLOR: [f32; 4] = [0.135, 0.155, 0.220, 1.0];
pub(crate) const TOOL_MUTED_TEXT_COLOR: [f32; 4] = [0.345, 0.365, 0.430, 0.96];
pub(crate) const TOOL_RUNNING_TEXT_COLOR: [f32; 4] = [0.045, 0.265, 0.640, 1.0];
pub(crate) const TOOL_SUCCESS_TEXT_COLOR: [f32; 4] = [0.035, 0.360, 0.220, 1.0];
pub(crate) const TOOL_FAILED_TEXT_COLOR: [f32; 4] = [0.560, 0.070, 0.095, 1.0];
pub(crate) const TOOL_PENDING_TEXT_COLOR: [f32; 4] = [0.320, 0.345, 0.405, 1.0];
pub(crate) const TOOL_CARD_BACKGROUND_COLOR: [f32; 4] = [0.985, 0.990, 1.000, 0.68];
pub(crate) const TOOL_CARD_ACTIVE_BACKGROUND_COLOR: [f32; 4] = [0.890, 0.945, 1.000, 0.72];
pub(crate) const TOOL_CARD_SUCCESS_BACKGROUND_COLOR: [f32; 4] = [0.875, 0.975, 0.925, 0.56];
pub(crate) const TOOL_CARD_FAILED_BACKGROUND_COLOR: [f32; 4] = [1.000, 0.900, 0.910, 0.64];
pub(crate) const TOOL_CARD_GROUP_BACKGROUND_COLOR: [f32; 4] = [0.945, 0.930, 1.000, 0.50];
pub(crate) const TOOL_CARD_BORDER_COLOR: [f32; 4] = [0.105, 0.165, 0.295, 0.22];
pub(crate) const TOOL_CARD_ACTIVE_BORDER_COLOR: [f32; 4] = [0.000, 0.260, 0.720, 0.36];
pub(crate) const TOOL_TIMELINE_RAIL_COLOR: [f32; 4] = [0.105, 0.165, 0.295, 0.20];
pub(crate) const TOOL_TIMELINE_ACTIVE_RAIL_COLOR: [f32; 4] = [0.000, 0.260, 0.720, 0.46];
pub(crate) const TOOL_OUTPUT_DRAWER_COLOR: [f32; 4] = [0.030, 0.055, 0.095, 0.070];
pub(crate) const TOOL_STATUS_CHIP_COLOR: [f32; 4] = [1.000, 1.000, 1.000, 0.42];
pub(crate) const META_TEXT_COLOR: [f32; 4] = [0.095, 0.110, 0.155, 0.98];
pub(crate) const CODE_TEXT_COLOR: [f32; 4] = [0.055, 0.065, 0.095, 1.0];
pub(crate) const STATUS_TEXT_ACCENT_COLOR: [f32; 4] = [0.030, 0.125, 0.080, 1.0];
pub(crate) const ERROR_TEXT_COLOR: [f32; 4] = [0.360, 0.000, 0.000, 1.0];
pub(crate) const OVERLAY_TEXT_COLOR: [f32; 4] = [0.030, 0.045, 0.075, 1.0];
pub(crate) const OVERLAY_SELECTION_TEXT_COLOR: [f32; 4] = [0.010, 0.035, 0.105, 1.0];
pub(crate) const USER_PROMPT_ACCENT_COLOR: [f32; 4] = [0.000, 0.105, 0.250, 1.0];
pub(crate) const PANEL_SECTION_COLOR: [f32; 4] = [0.045, 0.055, 0.080, 0.95];
pub(crate) const SELECTION_HIGHLIGHT_COLOR: [f32; 4] = [0.220, 0.420, 0.700, 0.22];
pub(crate) const WELCOME_AURORA_BLUE: [f32; 4] = [0.250, 0.520, 1.000, 0.145];
pub(crate) const WELCOME_AURORA_VIOLET: [f32; 4] = [0.720, 0.360, 0.980, 0.125];
pub(crate) const WELCOME_AURORA_MINT: [f32; 4] = [0.220, 0.840, 0.660, 0.115];
pub(crate) const WELCOME_AURORA_WARM: [f32; 4] = [1.000, 0.620, 0.360, 0.075];
pub(crate) const WELCOME_HANDWRITING_COLOR: [f32; 4] = [0.012, 0.080, 0.250, 0.94];
pub(crate) const NATIVE_SPINNER_HEAD_COLOR: [f32; 4] = [0.000, 0.260, 0.720, 1.0];
pub(crate) const CODE_BLOCK_BACKGROUND_COLOR: [f32; 4] = [0.075, 0.095, 0.135, 0.105];
pub(crate) const INLINE_CODE_BACKGROUND_COLOR: [f32; 4] = [0.075, 0.095, 0.135, 0.175];
pub(crate) const QUOTE_CARD_BACKGROUND_COLOR: [f32; 4] = [0.520, 0.330, 0.760, 0.090];
pub(crate) const TABLE_CARD_BACKGROUND_COLOR: [f32; 4] = [0.080, 0.460, 0.520, 0.085];
pub(crate) const ERROR_CARD_BACKGROUND_COLOR: [f32; 4] = [0.850, 0.170, 0.170, 0.105];
pub(crate) const OVERLAY_SELECTION_BACKGROUND_COLOR: [f32; 4] = [0.280, 0.470, 0.780, 0.115];
pub(crate) const STATUS_PREVIEW_ACCENTS: [[f32; 3]; 8] = [
    [0.560, 0.690, 0.980],
    [0.780, 0.610, 0.910],
    [0.520, 0.760, 0.620],
    [0.900, 0.650, 0.450],
    [0.600, 0.780, 0.840],
    [0.880, 0.580, 0.690],
    [0.720, 0.740, 0.820],
    [0.810, 0.760, 0.520],
];
