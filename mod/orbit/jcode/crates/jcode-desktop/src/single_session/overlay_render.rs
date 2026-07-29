use super::*;

pub(crate) fn session_switcher_styled_lines(
    switcher: &SessionSwitcherState,
    current_session_id: Option<&str>,
) -> Vec<SingleSessionStyledLine> {
    let visible = switcher.filtered_indices();
    let session_count = if switcher.filter.trim().is_empty() {
        switcher.sessions.len().to_string()
    } else {
        format!("{}/{}", visible.len(), switcher.sessions.len())
    };
    let filter_label = if switcher.filter.trim().is_empty() {
        "all".to_string()
    } else {
        format!("filter {}", switcher.filter.trim())
    };
    let mut lines = vec![
        styled_line(
            format!("Resume sessions · {session_count} sessions · {filter_label}"),
            SingleSessionLineStyle::OverlayTitle,
        ),
        styled_line(
            "type filter · ↑/↓ · Tab preview · Enter resume · Esc",
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            // Kept to one short line: the selected row is already highlighted
            // in the list, and long header text wraps inside the narrow rail
            // and pushes the session rows out of the visible card.
            format!(
                "focus: {}{}",
                session_switcher_focus_label(switcher.focus),
                if switcher.filter.is_empty() {
                    String::new()
                } else {
                    format!(" · filter: {}", switcher.filter.as_str())
                },
            ),
            SingleSessionLineStyle::Meta,
        ),
        blank_styled_line(),
    ];

    if switcher.loading {
        lines.push(styled_line(
            "loading recent sessions from ~/.jcode/sessions...",
            SingleSessionLineStyle::Status,
        ));
    }

    if visible.is_empty() && !switcher.loading {
        let message = if switcher.sessions.is_empty() {
            "no recent sessions found"
        } else {
            "no matching sessions"
        };
        lines.push(styled_line(message, SingleSessionLineStyle::Status));
        lines.push(styled_line(
            "try clearing the filter, pressing Ctrl+R, or starting a fresh session with Ctrl+;",
            SingleSessionLineStyle::Overlay,
        ));
        return lines;
    }

    const CARD_LIMIT: usize = 5;
    const BODY_ROW_LIMIT: usize = 9;
    const CONTENT_COLUMNS: usize = 92;

    let sessions_header = if switcher.focus == SessionSwitcherPane::Sessions {
        "Recent sessions · focused"
    } else {
        "Recent sessions"
    };
    lines.push(styled_line(
        format!("{sessions_header} · newest first"),
        SingleSessionLineStyle::OverlayTitle,
    ));

    let (window_start, row_indices) = switcher.visible_row_window(CARD_LIMIT);
    let row_count = row_indices.len();
    for (offset, index) in row_indices.iter().enumerate() {
        if let Some(session) = switcher.sessions.get(*index) {
            let position = window_start + offset;
            lines.extend(
                session_switcher_list_card_lines(
                    switcher,
                    current_session_id,
                    position,
                    session,
                    CONTENT_COLUMNS,
                )
                .into_iter()
                .map(|line| styled_line(line.text, line.style)),
            );
            if offset + 1 < row_count {
                lines.push(blank_styled_line());
            }
        }
    }

    if window_start + row_indices.len() < visible.len() {
        lines.push(styled_line(
            format!(
                "{} more sessions · keep pressing ↓ or type to filter",
                visible.len() - window_start - row_indices.len()
            ),
            SingleSessionLineStyle::Overlay,
        ));
    }

    lines.push(blank_styled_line());
    let preview_header = if switcher.focus == SessionSwitcherPane::Preview {
        "Preview · focused"
    } else {
        "Preview"
    };
    lines.push(styled_line(
        format!("{preview_header} · selected session transcript"),
        SingleSessionLineStyle::OverlayTitle,
    ));

    let preview_lines = switcher
        .selected_session()
        .map(|session| session_switcher_preview_lines_for_session(&session))
        .unwrap_or_else(|| {
            vec![SessionSwitcherRenderedLine::new(
                "No session selected".to_string(),
                SingleSessionLineStyle::Meta,
            )]
        });
    let preview_scroll = switcher
        .preview_scroll
        .min(preview_lines.len().saturating_sub(1));
    let preview_visible = preview_lines
        .iter()
        .skip(preview_scroll)
        .take(BODY_ROW_LIMIT)
        .cloned()
        .collect::<Vec<_>>();
    let preview_visible_len = preview_visible.len();
    lines.extend(
        preview_visible
            .into_iter()
            .map(|line| styled_line(truncate_chars(&line.text, CONTENT_COLUMNS), line.style)),
    );
    if preview_scroll > 0 || preview_scroll + preview_visible_len < preview_lines.len() {
        lines.push(styled_line(
            format!(
                "preview lines {}-{} of {}",
                preview_scroll + 1,
                preview_scroll + preview_visible_len,
                preview_lines.len()
            ),
            SingleSessionLineStyle::Meta,
        ));
    }

    lines
}

pub(crate) fn session_switcher_line_count(
    switcher: &SessionSwitcherState,
    current_session_id: Option<&str>,
) -> usize {
    let visible_len = switcher.filtered_indices().len();
    let mut count = 4;

    if switcher.loading {
        count += 1;
    }

    if visible_len == 0 && !switcher.loading {
        return count + 2;
    }

    const CARD_LIMIT: usize = 5;
    const BODY_ROW_LIMIT: usize = 9;
    count += 1; // Recent sessions header.
    let (window_start, window_len) = row_window_bounds(visible_len, switcher.selected, CARD_LIMIT);
    count += window_len * 4 + window_len.saturating_sub(1);
    if window_start + window_len < visible_len {
        count += 1;
    }

    count += 2; // Blank spacer and preview header.
    let preview_len = switcher
        .selected_session_ref()
        .map(session_switcher_preview_line_count_for_session)
        .unwrap_or(1);
    let preview_scroll = switcher.preview_scroll.min(preview_len.saturating_sub(1));
    let preview_visible_len = preview_len
        .saturating_sub(preview_scroll)
        .min(BODY_ROW_LIMIT);
    count += preview_visible_len;
    if preview_scroll > 0 || preview_scroll + preview_visible_len < preview_len {
        count += 1;
    }

    let _ = current_session_id;
    count
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct SessionSwitcherRenderedLine {
    text: String,
    style: SingleSessionLineStyle,
}

impl SessionSwitcherRenderedLine {
    fn new(text: impl Into<String>, style: SingleSessionLineStyle) -> Self {
        Self {
            text: text.into(),
            style,
        }
    }
}

pub(crate) fn session_switcher_focus_label(focus: SessionSwitcherPane) -> &'static str {
    match focus {
        SessionSwitcherPane::Sessions => "sessions",
        SessionSwitcherPane::Preview => "preview",
    }
}

pub(crate) fn session_switcher_list_card_lines(
    switcher: &SessionSwitcherState,
    current_session_id: Option<&str>,
    position: usize,
    session: &workspace::SessionCard,
    width: usize,
) -> Vec<SessionSwitcherRenderedLine> {
    let current_marker = if Some(session.session_id.as_str()) == current_session_id {
        "current · "
    } else {
        ""
    };
    let selected = position == switcher.selected;
    let primary_style = if selected {
        SingleSessionLineStyle::OverlaySelection
    } else {
        SingleSessionLineStyle::Overlay
    };
    let meta_style = if selected {
        SingleSessionLineStyle::OverlaySelection
    } else {
        SingleSessionLineStyle::Meta
    };
    let preview_style = if selected {
        SingleSessionLineStyle::OverlaySelection
    } else {
        SingleSessionLineStyle::Overlay
    };
    let status = session_status_badge(session);
    let model = session_model_label(session).unwrap_or_else(|| "model unknown".to_string());
    let preview = session
        .preview_lines
        .last()
        .or_else(|| session.detail_lines.last())
        .map(|line| session_switcher_compact_transcript_line(line, width.saturating_sub(8)))
        .unwrap_or_else(|| "no transcript preview yet".to_string());
    let card_text = |text: String| -> String {
        format!("      {}", truncate_chars(&text, width.saturating_sub(6)))
    };
    vec![
        SessionSwitcherRenderedLine::new(
            card_text(format!(
                "{} session · {current_marker}{}",
                session_status_label(session),
                session.title
            )),
            primary_style,
        ),
        SessionSwitcherRenderedLine::new(
            card_text(format!("Status {status} · Model {model}")),
            meta_style,
        ),
        SessionSwitcherRenderedLine::new(card_text(session.detail.clone()), meta_style),
        SessionSwitcherRenderedLine::new(card_text(preview), preview_style),
    ]
}

pub(crate) fn session_switcher_preview_lines_for_session(
    session: &workspace::SessionCard,
) -> Vec<SessionSwitcherRenderedLine> {
    let mut lines = vec![
        SessionSwitcherRenderedLine::new(
            session.title.clone(),
            SingleSessionLineStyle::OverlayTitle,
        ),
        SessionSwitcherRenderedLine::new(
            format!("id: {}", session.session_id),
            SingleSessionLineStyle::Meta,
        ),
    ];
    if !session.subtitle.is_empty() {
        lines.push(SessionSwitcherRenderedLine::new(
            session.subtitle.to_string(),
            SingleSessionLineStyle::Status,
        ));
    }
    if !session.detail.is_empty() {
        lines.push(SessionSwitcherRenderedLine::new(
            session.detail.clone(),
            SingleSessionLineStyle::Meta,
        ));
    }
    let transcript = if session.detail_lines.is_empty() {
        &session.preview_lines
    } else {
        &session.detail_lines
    };
    if transcript.is_empty() {
        lines.push(SessionSwitcherRenderedLine::new(
            "no transcript preview available".to_string(),
            SingleSessionLineStyle::Meta,
        ));
    } else {
        lines.push(SessionSwitcherRenderedLine::new(
            "recent transcript".to_string(),
            SingleSessionLineStyle::OverlayTitle,
        ));
        let mut user_turn = 1usize;
        for line in transcript {
            lines.push(session_switcher_transcript_preview_line(
                line,
                &mut user_turn,
            ));
        }
    }
    lines
}

pub(crate) fn session_switcher_preview_line_count_for_session(
    session: &workspace::SessionCard,
) -> usize {
    let mut count = 2;
    if !session.subtitle.is_empty() {
        count += 1;
    }
    if !session.detail.is_empty() {
        count += 1;
    }
    let transcript_len = if session.detail_lines.is_empty() {
        session.preview_lines.len()
    } else {
        session.detail_lines.len()
    };
    if transcript_len == 0 {
        count + 1
    } else {
        count + 1 + transcript_len
    }
}

pub(crate) fn session_status_badge(session: &workspace::SessionCard) -> String {
    let status = session_status_label(session);
    status.to_string()
}

pub(crate) fn session_status_label(session: &workspace::SessionCard) -> &str {
    session
        .subtitle
        .split('·')
        .next()
        .map(str::trim)
        .filter(|status| !status.is_empty())
        .unwrap_or("unknown")
}

pub(crate) fn session_model_label(session: &workspace::SessionCard) -> Option<String> {
    session
        .subtitle
        .split('·')
        .nth(1)
        .map(str::trim)
        .filter(|model| !model.is_empty())
        .map(ToOwned::to_owned)
}

pub(crate) fn session_switcher_compact_transcript_line(line: &str, width: usize) -> String {
    let (role, content) = session_switcher_split_preview_role(line);
    let compact = match role {
        Some("user") => format!("latest prompt: {content}"),
        Some("asst" | "assistant") => format!("latest answer: {content}"),
        Some("tool") => format!("tool: {content}"),
        Some("sys" | "system") => format!("system: {content}"),
        Some("task" | "background_task") => format!("task: {content}"),
        Some("meta") => format!("meta: {content}"),
        _ => line.trim().to_string(),
    };
    truncate_chars(&compact, width)
}

pub(crate) fn session_switcher_transcript_preview_line(
    line: &str,
    user_turn: &mut usize,
) -> SessionSwitcherRenderedLine {
    let (role, content) = session_switcher_split_preview_role(line);
    match role {
        Some("user") => {
            let rendered = format!("Prompt {}  {}", *user_turn, content);
            *user_turn = (*user_turn).saturating_add(1);
            SessionSwitcherRenderedLine::new(rendered, SingleSessionLineStyle::User)
        }
        Some("asst" | "assistant") => SessionSwitcherRenderedLine::new(
            format!("Assistant  {content}"),
            SingleSessionLineStyle::Assistant,
        ),
        Some("tool") => SessionSwitcherRenderedLine::new(
            format!("Tool  {content}"),
            SingleSessionLineStyle::Tool,
        ),
        Some("sys" | "system") => SessionSwitcherRenderedLine::new(
            format!("System  {content}"),
            SingleSessionLineStyle::Meta,
        ),
        Some("task" | "background_task") => SessionSwitcherRenderedLine::new(
            format!("Task  {content}"),
            SingleSessionLineStyle::Meta,
        ),
        Some("meta") => SessionSwitcherRenderedLine::new(
            format!("Meta  {content}"),
            SingleSessionLineStyle::Meta,
        ),
        _ => SessionSwitcherRenderedLine::new(
            line.trim().to_string(),
            SingleSessionLineStyle::Overlay,
        ),
    }
}

pub(crate) fn session_switcher_split_preview_role(line: &str) -> (Option<&str>, &str) {
    let trimmed = line.trim();
    let Some((role, content)) = trimmed.split_once(char::is_whitespace) else {
        return (None, trimmed);
    };
    match role {
        "user" | "asst" | "assistant" | "tool" | "sys" | "system" | "task" | "background_task"
        | "meta" => (Some(role), content.trim()),
        _ => (None, trimmed),
    }
}

pub(crate) fn session_card_search_text(session: &workspace::SessionCard) -> String {
    let mut text = format!(
        "{} {} {} {}",
        session.session_id, session.title, session.subtitle, session.detail
    );
    for line in session
        .preview_lines
        .iter()
        .chain(session.detail_lines.iter())
    {
        text.push(' ');
        text.push_str(line);
    }
    text.to_lowercase()
}

pub(crate) fn session_switcher_fuzzy_score(needle: &str, haystack: &str) -> Option<i32> {
    let needle = needle.trim();
    if needle.is_empty() {
        return Some(0);
    }

    jcode_fuzzy::fuzzy_score_tokens(needle, haystack)
}

pub(crate) fn session_info_inline_styled_lines(
    app: &SingleSessionApp,
) -> Vec<SingleSessionStyledLine> {
    let (user_count, assistant_count, tool_count, system_count, meta_count) =
        session_message_role_counts(&app.messages);
    let session_id = app
        .current_session_id()
        .map(|id| format!("{} ({})", short_session_id(id), id))
        .unwrap_or_else(|| "fresh / not started".to_string());
    let model = model_picker_current_label(
        app.model_picker.provider_name.as_deref(),
        app.model_picker.current_model.as_deref(),
    );
    let status = app.status.as_deref().unwrap_or("ready");
    let transcript_chars: usize = app
        .messages
        .iter()
        .map(|message| message.content().len())
        .sum();
    let streaming_chars = app.streaming_response.len();
    let streaming_lines = app.streaming_response.lines().count();
    let body_lines = app.body_styled_lines_without_inline_widgets().len();
    let selection = if app.has_body_selection() || app.has_draft_selection() {
        "active"
    } else {
        "none"
    };
    let stdin = app
        .stdin_response
        .as_ref()
        .map(|state| {
            if state.is_password {
                "password requested"
            } else {
                "input requested"
            }
        })
        .unwrap_or("none");
    let active_tool = app
        .tool
        .active_message_index
        .map(|index| format!("message #{index}"))
        .unwrap_or_else(|| "none".to_string());

    let mut lines = vec![
        styled_line(
            "╭─ session info · Ctrl+Shift+S/Esc close",
            SingleSessionLineStyle::OverlayTitle,
        ),
        styled_line(
            format!("│ title        {}", compact_tool_text(&app.title(), 92)),
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            format!("│ session id   {}", compact_tool_text(&session_id, 92)),
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            format!(
                "│ status       {} · model {}",
                compact_tool_text(status, 46),
                compact_tool_text(&model, 40)
            ),
            SingleSessionLineStyle::Status,
        ),
        styled_line(
            format!(
                "│ work         {} · worker {} · active tool {}",
                if app.is_processing { "running" } else { "idle" },
                if app.runtime.session_handle.is_some() {
                    "attached"
                } else {
                    "none"
                },
                active_tool
            ),
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            format!(
                "│ messages     {} total · {user_count} user · {assistant_count} assistant · {tool_count} tool · {system_count} system · {meta_count} meta",
                app.messages.len()
            ),
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            format!(
                "│ transcript   {body_lines} visible lines · {transcript_chars} chars · streaming {streaming_chars} chars/{streaming_lines} lines"
            ),
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            format!(
                "│ composer     prompt #{} · draft {} chars · {} image(s) · {} queued · stdin {}",
                app.next_prompt_number(),
                app.draft.len(),
                app.pending_images.len(),
                app.composer.queued_drafts.len(),
                stdin
            ),
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            format!(
                "│ viewport     scroll {} · text scale {:.0}% · selection {} · welcome {}",
                scroll_status_fragment(app.body_scroll_lines).trim_start_matches(" · "),
                app.view.text_scale * 100.0,
                selection,
                if app.is_welcome_timeline_visible() {
                    "visible"
                } else {
                    "hidden"
                }
            ),
            SingleSessionLineStyle::Overlay,
        ),
    ];

    if let Some(session) = &app.session {
        if !session.subtitle.trim().is_empty() {
            lines.push(styled_line(
                format!(
                    "│ subtitle     {}",
                    compact_tool_text(&session.subtitle, 92)
                ),
                SingleSessionLineStyle::Meta,
            ));
        }
        if !session.detail.trim().is_empty() {
            lines.push(styled_line(
                format!("│ detail       {}", compact_tool_text(&session.detail, 92)),
                SingleSessionLineStyle::Meta,
            ));
        }
    }

    if let Some(error) = &app.error {
        lines.push(styled_line(
            format!("│ error        {}", compact_tool_text(error, 92)),
            SingleSessionLineStyle::Error,
        ));
    }

    lines.push(styled_line(
        "╰─ /status opens this panel",
        SingleSessionLineStyle::Overlay,
    ));
    lines
}

pub(crate) fn session_info_inline_line_count(app: &SingleSessionApp) -> usize {
    10 + usize::from(
        app.session
            .as_ref()
            .is_some_and(|session| !session.subtitle.trim().is_empty()),
    ) + usize::from(
        app.session
            .as_ref()
            .is_some_and(|session| !session.detail.trim().is_empty()),
    ) + usize::from(app.error.is_some())
}

pub(crate) fn session_message_role_counts(
    messages: &[SingleSessionMessage],
) -> (usize, usize, usize, usize, usize) {
    let mut user = 0;
    let mut assistant = 0;
    let mut tool = 0;
    let mut system = 0;
    let mut meta = 0;
    for message in messages {
        match message.role() {
            SingleSessionRole::User => user += 1,
            SingleSessionRole::Assistant => assistant += 1,
            SingleSessionRole::Tool => tool += 1,
            SingleSessionRole::System => system += 1,
            SingleSessionRole::Meta => meta += 1,
        }
    }
    (user, assistant, tool, system, meta)
}

pub(crate) fn model_picker_inline_styled_lines(
    picker: &ModelPickerState,
) -> Vec<SingleSessionStyledLine> {
    let visible = picker.filtered_indices();
    let count = if visible.len() == picker.choices.len() {
        format!("{} models", picker.choices.len())
    } else {
        format!("{} of {} models", visible.len(), picker.choices.len())
    };
    let filter = if picker.filter.trim().is_empty() {
        "type to filter".to_string()
    } else {
        format!("filter \"{}\"", truncate_chars(picker.filter.trim(), 28))
    };
    let current_label = model_picker_current_label(
        picker.provider_name.as_deref(),
        picker.current_model.as_deref(),
    );
    let mut lines = vec![
        styled_line(
            "Choose model".to_string(),
            SingleSessionLineStyle::OverlayTitle,
        ),
        styled_line(
            format!("Current  {current_label}"),
            SingleSessionLineStyle::Overlay,
        ),
        styled_line(
            format!("{filter}  ·  {count}"),
            SingleSessionLineStyle::Overlay,
        ),
    ];

    if picker.loading {
        lines.push(styled_line(
            "Loading models from shared server...",
            SingleSessionLineStyle::Status,
        ));
    }

    if let Some(error) = &picker.error {
        lines.push(styled_line(
            format!("Error: {error}"),
            SingleSessionLineStyle::Error,
        ));
    }

    if visible.is_empty() && !picker.loading {
        lines.push(styled_line(
            "No matching models",
            SingleSessionLineStyle::Status,
        ));
        lines.push(styled_line(
            "Clear the filter or press Ctrl+R to reload",
            SingleSessionLineStyle::Overlay,
        ));
        return lines;
    }

    let (window_start, window) = picker.visible_row_window(MODEL_PICKER_INLINE_ROW_LIMIT);
    for (row_offset, index) in window.iter().enumerate() {
        let Some(choice) = picker.choices.get(*index) else {
            continue;
        };
        let visible_position = window_start + row_offset;
        let provider = choice.provider.as_deref().unwrap_or("auto");
        let method = choice.api_method.as_deref().unwrap_or("auto");
        let availability = if choice.available {
            "available"
        } else {
            "unavailable"
        };
        let detail = choice
            .detail
            .as_deref()
            .filter(|detail| !detail.is_empty())
            .unwrap_or(availability);
        let row_style = if visible_position == picker.selected {
            SingleSessionLineStyle::OverlaySelection
        } else {
            SingleSessionLineStyle::Overlay
        };
        lines.push(styled_line(
            format!("    {}", truncate_chars(&choice.model, 46)),
            row_style,
        ));
        lines.push(styled_line(
            format!(
                "    {} · {} · {}",
                truncate_chars(provider, 18),
                truncate_chars(method, 16),
                truncate_chars(detail, 24),
            ),
            SingleSessionLineStyle::Meta,
        ));
    }
    if visible.len() > window_start + window.len() {
        lines.push(styled_line(
            format!(
                "{} more models",
                visible.len() - window_start - window.len()
            ),
            SingleSessionLineStyle::Overlay,
        ));
    }
    let footer = if picker.preview {
        "↑↓ select  ·  PgUp/PgDn jump  ·  Enter use model  ·  Esc clear /model"
    } else {
        "↑↓ select  ·  type to filter  ·  Enter use model  ·  Esc close"
    };
    lines.push(styled_line(footer, SingleSessionLineStyle::Overlay));

    lines
}

pub(crate) fn model_picker_inline_line_count(picker: &ModelPickerState) -> usize {
    let visible_len = picker.filtered_indices().len();
    let mut count = 3;
    if picker.loading {
        count += 1;
    }
    if picker.error.is_some() {
        count += 1;
    }
    if visible_len == 0 && !picker.loading {
        return count + 2;
    }

    let (window_start, window_len) =
        row_window_bounds(visible_len, picker.selected, MODEL_PICKER_INLINE_ROW_LIMIT);
    count += window_len * 2;
    if visible_len > window_start + window_len {
        count += 1;
    }
    count + 1
}

pub(crate) fn row_window_bounds(
    visible_len: usize,
    selected: usize,
    limit: usize,
) -> (usize, usize) {
    if visible_len == 0 || limit == 0 {
        return (0, 0);
    }
    let max_start = visible_len.saturating_sub(limit);
    let selected = selected.min(visible_len - 1);
    let start = selected.saturating_sub(limit / 2).min(max_start);
    let end = (start + limit).min(visible_len);
    (start, end - start)
}

pub(crate) fn model_picker_preview_filter(input: &str) -> Option<String> {
    let trimmed = input.trim_start();
    let rest = trimmed
        .strip_prefix("/model")
        .or_else(|| trimmed.strip_prefix("/models"))?;
    if rest.is_empty() {
        return Some(String::new());
    }
    rest.chars()
        .next()
        .filter(|ch| ch.is_whitespace())
        .map(|_| rest.trim_start().to_string())
}

pub(crate) fn session_switcher_preview_filter(input: &str) -> Option<String> {
    let trimmed = input.trim_start();
    let rest = trimmed
        .strip_prefix("/resume")
        .or_else(|| trimmed.strip_prefix("/sessions"))
        .or_else(|| trimmed.strip_prefix("/session"))?;
    if rest.is_empty() {
        return Some(String::new());
    }
    rest.chars()
        .next()
        .filter(|ch| ch.is_whitespace())
        .map(|_| rest.trim_start().to_string())
}

pub(crate) fn truncate_chars(text: &str, max_chars: usize) -> String {
    if text.chars().count() <= max_chars {
        return text.to_string();
    }
    if max_chars == 0 {
        return String::new();
    }
    if max_chars == 1 {
        return "…".to_string();
    }
    format!("{}…", text.chars().take(max_chars - 1).collect::<String>())
}

pub(crate) fn model_picker_current_label(
    provider_name: Option<&str>,
    current_model: Option<&str>,
) -> String {
    match (provider_name, current_model) {
        (Some(provider), Some(model)) if !provider.is_empty() => format!("{provider} · {model}"),
        (_, Some(model)) => model.to_string(),
        (Some(provider), None) if !provider.is_empty() => provider.to_string(),
        _ => "unknown".to_string(),
    }
}

pub(crate) fn inferred_desktop_reasoning_efforts(
    provider_name: Option<&str>,
    model_name: Option<&str>,
    current_effort: Option<&str>,
) -> &'static [&'static str] {
    let provider = provider_name.unwrap_or_default().to_ascii_lowercase();
    let model = model_name.unwrap_or_default().to_ascii_lowercase();
    let current = current_effort.unwrap_or_default().to_ascii_lowercase();

    if provider.contains("openrouter") {
        if model.contains("deepseek") || current == "max" {
            return DESKTOP_REASONING_EFFORTS_DEEPSEEK;
        }
        return DESKTOP_REASONING_EFFORTS_OPENAI;
    }

    if provider.contains("deepseek") || model.contains("deepseek") || current == "max" {
        return DESKTOP_REASONING_EFFORTS_DEEPSEEK;
    }

    let is_anthropic = provider.contains("anthropic")
        || provider.contains("claude")
        || model.starts_with("claude-")
        || model.contains("/claude-");
    if is_anthropic {
        if model.contains("claude-opus-4-7")
            || model.contains("claude-opus-4-8")
            || model.contains("claude-fable-5")
            || current == "xhigh"
        {
            return DESKTOP_REASONING_EFFORTS_ANTHROPIC_XHIGH;
        }
        return DESKTOP_REASONING_EFFORTS_ANTHROPIC_STANDARD;
    }

    // Before the model catalog arrives, the desktop may only know the current
    // runtime setting. Keep the shortcut responsive by falling back to the
    // common OpenAI/Anthropic order instead of doing a blocking history lookup.
    DESKTOP_REASONING_EFFORTS_OPENAI
}

pub(crate) fn desktop_model_choice_switch_spec(choice: &DesktopModelChoice) -> String {
    let model = choice.model.as_str();
    let provider = choice.provider.as_deref().unwrap_or_default();
    let api_method = choice.api_method.as_deref().unwrap_or_default();

    if api_method == "copilot" {
        format!("copilot:{model}")
    } else if api_method == "claude-oauth"
        || (api_method == "oauth" && desktop_model_choice_is_anthropic(provider, model))
    {
        format!("claude-oauth:{model}")
    } else if (api_method == "api-key" || api_method == "claude-api")
        && desktop_model_choice_is_anthropic(provider, model)
    {
        format!("claude-api:{model}")
    } else if api_method == "cursor" {
        format!("cursor:{model}")
    } else if api_method == "bedrock" {
        format!("bedrock:{model}")
    } else if api_method == "openai-api-key" || api_method == "openai-api" {
        format!("openai-api:{model}")
    } else if api_method == "openai-oauth" {
        format!("openai-oauth:{model}")
    } else if provider == "Antigravity" {
        format!("antigravity:{model}")
    } else if let Some(profile_id) = desktop_openai_compatible_profile_id_for_route(api_method) {
        format!("{profile_id}:{model}")
    } else if api_method == "openrouter" && !provider.is_empty() && provider != "auto" {
        format!("{model}@{provider}")
    } else {
        model.to_string()
    }
}

pub(crate) fn desktop_model_choice_is_anthropic(provider: &str, model: &str) -> bool {
    let provider = provider.to_ascii_lowercase();
    provider.contains("anthropic")
        || provider.contains("claude")
        || model.starts_with("claude-")
        || model.contains("/claude-")
}

pub(crate) fn desktop_openai_compatible_profile_id_for_route(api_method: &str) -> Option<&str> {
    let (kind, profile_id) = api_method.split_once(':')?;
    if kind == "openai-compatible" {
        let profile_id = profile_id.trim();
        if !profile_id.is_empty() {
            return Some(profile_id);
        }
    }
    None
}

pub(crate) fn model_choice_search_text(choice: &DesktopModelChoice) -> String {
    format!(
        "{} {} {} {}",
        choice.model,
        choice.provider.as_deref().unwrap_or_default(),
        choice.api_method.as_deref().unwrap_or_default(),
        choice.detail.as_deref().unwrap_or_default()
    )
    .to_lowercase()
}

pub(crate) fn model_picker_fuzzy_score(needle: &str, haystack: &str) -> Option<i32> {
    let needle = needle.trim();
    if needle.is_empty() {
        return Some(0);
    }

    jcode_fuzzy::fuzzy_score_tokens(needle, haystack)
}

pub(crate) fn desktop_slash_fuzzy_score(needle: &str, haystack: &str) -> Option<i32> {
    jcode_fuzzy::command_fuzzy_score(needle, haystack)
}

pub(crate) fn dedupe_model_choices(choices: Vec<DesktopModelChoice>) -> Vec<DesktopModelChoice> {
    let mut seen = HashSet::with_capacity(choices.len());
    let mut deduped: Vec<DesktopModelChoice> = Vec::with_capacity(choices.len());
    for choice in choices {
        let key = (
            choice.model.clone(),
            choice.provider.clone(),
            choice.api_method.clone(),
            choice.detail.clone(),
        );
        if !seen.insert(key) {
            continue;
        }
        deduped.push(choice);
    }
    deduped
}

pub(crate) struct HelpSection {
    title: &'static str,
    shortcuts: &'static [(&'static str, &'static str)],
}

pub(crate) const SINGLE_SESSION_HELP_SECTIONS: &[HelpSection] = &[
    HelpSection {
        title: "chat",
        shortcuts: &[
            ("Enter", "send prompt"),
            ("Shift/Alt+Enter", "insert newline"),
            ("Ctrl+Enter", "queue while running, send when idle"),
            ("Esc", "interrupt running generation"),
            ("Ctrl+C/D", "interrupt running generation"),
            ("Ctrl+Shift+C", "copy latest assistant response"),
            ("Ctrl+Shift+K", "copy latest code block"),
            ("Ctrl+Shift+T", "copy transcript"),
            ("Ctrl+V", "paste clipboard text"),
            ("Ctrl+V", "paste clipboard image when no text is present"),
            ("Alt+V", "attach clipboard image, terminal-style"),
            ("Ctrl+I", "attach clipboard image to next prompt"),
            ("Ctrl+Shift+I", "clear pending image attachments"),
            ("Ctrl+Shift+M", "open model/account picker"),
            ("Ctrl+M/N", "switch to next/previous model"),
            ("Ctrl+Tab", "switch to next model"),
            ("Ctrl+Shift+Tab", "switch to previous model"),
            ("Alt+←/→", "change thinking level"),
            ("Ctrl+P/O", "open recent session switcher"),
            ("Ctrl+Shift+S", "toggle inline session info/stats"),
        ],
    },
    HelpSection {
        title: "navigation",
        shortcuts: &[
            ("Ctrl+Up", "pull latest queued prompt back into the input"),
            ("PageUp/PageDown", "scroll transcript"),
            ("Ctrl+Home/End", "jump transcript to top/bottom"),
            ("Super+K/J", "jump between user prompts"),
            ("Alt+Up/Down", "jump between user prompts"),
            ("Ctrl+[/]", "jump between user prompts"),
            ("Mouse wheel", "scroll transcript"),
        ],
    },
    HelpSection {
        title: "editing",
        shortcuts: &[
            ("Ctrl+A/E", "start/end of line"),
            ("Ctrl+U/K", "delete to line start/end"),
            ("Ctrl+W/Ctrl+Backspace", "delete previous word"),
            ("Alt/Super+Backspace", "delete previous word"),
            ("Ctrl+←/→, Ctrl+B/F", "move by word"),
            ("Alt+B/F", "move by word, terminal-style"),
            ("Alt+D", "delete next word"),
            ("Tab", "complete slash command suggestion"),
            ("↑/↓ PgUp/PgDn", "navigate slash suggestions"),
            ("Ctrl+X", "cut input line to clipboard"),
            ("Ctrl+Z", "undo input edit"),
        ],
    },
    HelpSection {
        title: "window",
        shortcuts: &[
            ("Ctrl+;", "reset/spawn fresh desktop session"),
            ("Super+;", "spawn a self-dev jcode session"),
            ("Super+'", "spawn a jcode session in home"),
            ("Ctrl+R", "reload sessions/models while a picker is open"),
            ("Ctrl+?", "toggle this help"),
            ("q", "close help or session info"),
            ("Ctrl+Q/Super+Q", "quit desktop app"),
            ("Esc", "close help; interrupt while running; idle no-op"),
        ],
    },
];

pub(crate) fn single_session_help_styled_lines() -> Vec<SingleSessionStyledLine> {
    let mut lines = Vec::new();

    lines.push(styled_line(
        "slash commands",
        SingleSessionLineStyle::OverlayTitle,
    ));
    lines.extend(DESKTOP_SLASH_COMMANDS.iter().map(|(command, description)| {
        let separator = if command.len() >= 16 { " " } else { "" };
        styled_line(
            format!("  {command:<16}{separator}{description}"),
            SingleSessionLineStyle::Overlay,
        )
    }));

    for (section_index, section) in SINGLE_SESSION_HELP_SECTIONS.iter().enumerate() {
        let _ = section_index;
        lines.push(blank_styled_line());
        lines.push(styled_line(
            section.title,
            SingleSessionLineStyle::OverlayTitle,
        ));
        lines.extend(section.shortcuts.iter().map(|(shortcut, description)| {
            let separator = if shortcut.len() >= 12 { " " } else { "" };
            styled_line(
                format!("  {shortcut:<12}{separator}{description}"),
                SingleSessionLineStyle::Overlay,
            )
        }));
    }

    lines
}

pub(crate) fn hotkey_help_inline_widget() -> ReadOnlyInlineWidget {
    ReadOnlyInlineWidget::new("desktop shortcuts", single_session_help_styled_lines())
}

pub(crate) fn hotkey_help_inline_line_count() -> usize {
    single_session_help_styled_line_count() + 2
}

pub(crate) fn single_session_help_styled_line_count() -> usize {
    DESKTOP_SLASH_COMMANDS.len()
        + 1
        + SINGLE_SESSION_HELP_SECTIONS
            .iter()
            .map(|section| 2 + section.shortcuts.len())
            .sum::<usize>()
}
