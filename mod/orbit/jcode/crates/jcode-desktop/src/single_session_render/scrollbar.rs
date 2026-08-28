//! Transcript scrollbar rendering and body scroll metrics, plus transcript card/message run detection over styled body lines.

use super::*;

pub(crate) fn push_single_session_scrollbar(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
    smooth_scroll_lines: f32,
    motion: Option<&SingleSessionScrollbarMotionFrame>,
) {
    if single_session_scrollbar_suppressed(app) {
        return;
    }
    let Some(metrics) = single_session_body_scroll_metrics(app, size, tick) else {
        return;
    };
    push_single_session_scrollbar_for_metrics(vertices, size, smooth_scroll_lines, metrics, motion);
}

pub(crate) fn push_single_session_scrollbar_for_total_lines(
    vertices: &mut Vec<Vertex>,
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    total_lines: usize,
    motion: Option<&SingleSessionScrollbarMotionFrame>,
) {
    if single_session_scrollbar_suppressed(app) {
        return;
    }
    let Some(metrics) = single_session_body_scroll_metrics_for_total_lines(app, size, total_lines)
    else {
        return;
    };
    push_single_session_scrollbar_for_metrics(vertices, size, smooth_scroll_lines, metrics, motion);
}

/// The transcript scrollbar is suppressed while an inline widget (model
/// picker, session switcher, help, slash suggestions) is up: the widget owns
/// interaction focus, and the widget reserving body height would otherwise
/// make a scrollbar thumb pop up floating next to empty space.
pub(crate) fn single_session_scrollbar_suppressed(app: &SingleSessionApp) -> bool {
    app.render_inline_widget_line_count() > 0
}

pub(crate) fn push_single_session_scrollbar_for_metrics(
    vertices: &mut Vec<Vertex>,
    size: PhysicalSize<u32>,
    smooth_scroll_lines: f32,
    metrics: SingleSessionBodyScrollMetrics,
    motion: Option<&SingleSessionScrollbarMotionFrame>,
) {
    let track_top = single_session_scrollbar_track_top();
    let track_bottom = single_session_scrollbar_track_bottom(size);
    let track_height = (track_bottom - track_top).max(1.0);
    let x = single_session_scrollbar_track_x(size);
    let fallback_geometry = single_session_scrollbar_geometry(size, smooth_scroll_lines, metrics);
    let visual = match motion {
        Some(motion) => match motion.visual() {
            Some(visual) => visual,
            None => return,
        },
        None => SingleSessionScrollbarVisual {
            thumb_y: fallback_geometry.thumb_y,
            thumb_height: fallback_geometry.thumb_height,
            opacity: 1.0,
        },
    };
    if visual.opacity <= 0.001 {
        return;
    }

    push_rounded_rect(
        vertices,
        Rect {
            x,
            y: track_top,
            width: SINGLE_SESSION_SCROLLBAR_TRACK_WIDTH,
            height: track_height,
        },
        2.0,
        with_alpha(
            SINGLE_SESSION_SCROLLBAR_TRACK_COLOR,
            SINGLE_SESSION_SCROLLBAR_TRACK_COLOR[3] * visual.opacity,
        ),
        size,
    );
    push_rounded_rect(
        vertices,
        Rect {
            x: x - 0.5,
            y: visual.thumb_y,
            width: 4.0,
            height: visual.thumb_height,
        },
        2.0,
        with_alpha(
            SINGLE_SESSION_SCROLLBAR_THUMB_COLOR,
            SINGLE_SESSION_SCROLLBAR_THUMB_COLOR[3] * visual.opacity,
        ),
        size,
    );
}

pub(crate) fn single_session_scrollbar_track_top() -> f32 {
    PANEL_BODY_TOP_PADDING + 4.0
}

pub(crate) fn single_session_scrollbar_track_bottom(size: PhysicalSize<u32>) -> f32 {
    single_session_body_bottom(size) - 4.0
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct SingleSessionBodyScrollMetrics {
    pub(crate) total_lines: usize,
    pub(crate) visible_lines: usize,
    pub(crate) scroll_lines: f32,
    pub(crate) max_scroll_lines: usize,
}

pub(crate) fn single_session_body_scroll_metrics(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    tick: u64,
) -> Option<SingleSessionBodyScrollMetrics> {
    let _ = tick;
    let total_lines = welcome_timeline_total_body_lines(app, size);
    single_session_body_scroll_metrics_for_total_lines(app, size, total_lines)
}

pub(crate) fn single_session_body_scroll_metrics_for_total_lines(
    app: &SingleSessionApp,
    size: PhysicalSize<u32>,
    total_lines: usize,
) -> Option<SingleSessionBodyScrollMetrics> {
    let typography = single_session_typography_for_scale(app.text_scale());
    let line_height = typography.body_size * typography.body_line_height;
    let body_top = single_session_body_top_for_app(app, size);
    let body_bottom = single_session_body_bottom_for_total_lines(app, size, total_lines);
    let available_height = (body_bottom - body_top).max(line_height);
    let visible_lines = ((available_height / line_height).floor() as usize).max(1);
    let max_scroll_lines = total_lines.saturating_sub(visible_lines);
    (max_scroll_lines > 0).then_some(SingleSessionBodyScrollMetrics {
        total_lines,
        visible_lines,
        scroll_lines: app.body_scroll_lines.min(max_scroll_lines as f32),
        max_scroll_lines,
    })
}

pub(crate) fn single_session_transcript_card_runs(
    lines: &[SingleSessionStyledLine],
) -> Vec<SingleSessionTranscriptCardRun> {
    let mut runs = Vec::new();
    let mut current: Option<SingleSessionTranscriptCardRun> = None;

    for (line, styled_line) in lines.iter().enumerate() {
        if single_session_line_card_color(styled_line.style).is_none() {
            if let Some(run) = current.take() {
                runs.push(run);
            }
            continue;
        }

        match &mut current {
            Some(run)
                if single_session_line_card_color(run.style)
                    == single_session_line_card_color(styled_line.style)
                    && run.line + run.line_count == line =>
            {
                run.line_count += 1;
            }
            Some(run) => {
                runs.push(*run);
                current = Some(SingleSessionTranscriptCardRun {
                    line,
                    line_count: 1,
                    style: styled_line.style,
                });
            }
            None => {
                current = Some(SingleSessionTranscriptCardRun {
                    line,
                    line_count: 1,
                    style: styled_line.style,
                });
            }
        }
    }

    if let Some(run) = current {
        runs.push(run);
    }
    runs
}

pub(crate) fn single_session_transcript_message_runs(
    lines: &[SingleSessionStyledLine],
) -> Vec<TranscriptMessageRun> {
    let mut runs = Vec::new();
    let mut current: Option<TranscriptMessageRun> = None;

    for (line, styled_line) in lines.iter().enumerate() {
        let Some(role) = transcript_message_role_for_style(styled_line.style) else {
            if let Some(run) = current.take() {
                runs.push(run);
            }
            continue;
        };

        match &mut current {
            Some(run) if run.role == role && run.line + run.line_count == line => {
                run.line_count += 1;
            }
            Some(run) => {
                runs.push(*run);
                current = Some(TranscriptMessageRun {
                    line,
                    line_count: 1,
                    role,
                });
            }
            None => {
                current = Some(TranscriptMessageRun {
                    line,
                    line_count: 1,
                    role,
                });
            }
        }
    }

    if let Some(run) = current {
        runs.push(run);
    }
    runs
}

pub(crate) fn transcript_message_role_for_style(
    style: SingleSessionLineStyle,
) -> Option<TranscriptMessageRole> {
    match style {
        SingleSessionLineStyle::User | SingleSessionLineStyle::UserContinuation => {
            Some(TranscriptMessageRole::User)
        }
        SingleSessionLineStyle::Assistant
        | SingleSessionLineStyle::AssistantHeading
        | SingleSessionLineStyle::AssistantQuote
        | SingleSessionLineStyle::AssistantTable
        | SingleSessionLineStyle::AssistantLink
        | SingleSessionLineStyle::AssistantMedia
        | SingleSessionLineStyle::CodeHeader
        | SingleSessionLineStyle::Code => Some(TranscriptMessageRole::Assistant),
        SingleSessionLineStyle::Meta | SingleSessionLineStyle::Status => {
            Some(TranscriptMessageRole::Meta)
        }
        SingleSessionLineStyle::Error => Some(TranscriptMessageRole::Error),
        SingleSessionLineStyle::Tool
        | SingleSessionLineStyle::OverlayTitle
        | SingleSessionLineStyle::Overlay
        | SingleSessionLineStyle::OverlaySelection
        | SingleSessionLineStyle::Blank => None,
    }
}

pub(crate) fn single_session_line_card_color(style: SingleSessionLineStyle) -> Option<[f32; 4]> {
    match style {
        SingleSessionLineStyle::AssistantHeading => Some(MARKDOWN_HEADING_BACKGROUND_COLOR),
        SingleSessionLineStyle::CodeHeader | SingleSessionLineStyle::Code => {
            Some(CODE_BLOCK_BACKGROUND_COLOR)
        }
        SingleSessionLineStyle::AssistantQuote => Some(QUOTE_CARD_BACKGROUND_COLOR),
        SingleSessionLineStyle::AssistantTable => Some(TABLE_CARD_BACKGROUND_COLOR),
        SingleSessionLineStyle::AssistantMedia => Some(MARKDOWN_MEDIA_BACKGROUND_COLOR),
        SingleSessionLineStyle::Error => Some(ERROR_CARD_BACKGROUND_COLOR),
        SingleSessionLineStyle::OverlaySelection => Some(OVERLAY_SELECTION_BACKGROUND_COLOR),
        _ => None,
    }
}
