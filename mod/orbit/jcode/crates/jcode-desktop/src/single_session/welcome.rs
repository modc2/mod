use super::*;

#[cfg_attr(not(test), allow(dead_code))]
pub(crate) fn welcome_styled_lines(
    name: &Option<String>,
    tick: u64,
    recovery_session_count: usize,
) -> Vec<SingleSessionStyledLine> {
    let greeting = welcome_greeting_text(name, 0);
    let prompts = [
        "Start with a prompt",
        "Ask anything",
        "Ready when you are",
        "Enter sends · Shift+Enter adds a line",
    ];
    let prompt = prompts[((tick / 42) as usize) % prompts.len()];
    let ellipsis = match (tick / 14) % 4 {
        0 => "",
        1 => ".",
        2 => "..",
        _ => "...",
    };

    let mut lines = vec![
        styled_line(greeting, SingleSessionLineStyle::AssistantHeading),
        blank_styled_line(),
        styled_line(
            format!("{prompt}{ellipsis}"),
            SingleSessionLineStyle::Status,
        ),
        styled_line("Ctrl+P opens recent sessions", SingleSessionLineStyle::Meta),
    ];

    if recovery_session_count > 0 {
        lines.push(blank_styled_line());
        lines.push(styled_line(
            format!(
                "Found {recovery_session_count} crashed session(s). Press Ctrl+R to open them in new windows."
            ),
            SingleSessionLineStyle::Status,
        ));
    }

    lines
}

pub(crate) fn welcome_recovery_styled_lines(
    recovery_session_count: usize,
) -> Vec<SingleSessionStyledLine> {
    vec![styled_line(
        format!(
            "Found {recovery_session_count} crashed session(s). Press Ctrl+R to open them in new windows."
        ),
        SingleSessionLineStyle::Status,
    )]
}

pub(crate) fn welcome_greeting_text(name: &Option<String>, phrase_index: usize) -> String {
    name.as_deref()
        .map(|name| format!("Welcome, {name}"))
        .unwrap_or_else(|| handwritten_welcome_phrase(phrase_index).to_string())
}

pub(crate) fn handwritten_welcome_phrase(index: usize) -> &'static str {
    HANDWRITTEN_WELCOME_PHRASES[index % HANDWRITTEN_WELCOME_PHRASES.len()]
}

pub(crate) fn welcome_phrase_index(name: &Option<String>) -> usize {
    let time_seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.subsec_nanos() as usize)
        .unwrap_or(0);
    let name_seed = name
        .as_deref()
        .unwrap_or_default()
        .bytes()
        .fold(0usize, |hash, byte| {
            hash.wrapping_mul(31).wrapping_add(byte as usize)
        });
    (time_seed ^ name_seed) % HANDWRITTEN_WELCOME_PHRASES.len()
}

#[cfg(any(target_os = "macos", windows))]
pub(crate) fn desktop_welcome_name() -> Option<String> {
    sanitize_welcome_name(&whoami::realname())
}

#[cfg(not(any(target_os = "macos", windows)))]
pub(crate) fn desktop_welcome_name() -> Option<String> {
    None
}

#[cfg_attr(not(any(test, target_os = "macos", windows)), allow(dead_code))]
pub(crate) fn sanitize_welcome_name(raw: &str) -> Option<String> {
    let name = raw
        .trim()
        .trim_matches(|ch: char| ch == ',' || ch == ';')
        .split_whitespace()
        .next()?;
    if name.is_empty() || name.eq_ignore_ascii_case("unknown") {
        return None;
    }
    Some(name.to_string())
}

#[derive(Clone, Debug)]
pub(crate) struct ExternalCliSessionCandidate {
    pub(crate) source: &'static str,
    pub(crate) modified: SystemTime,
    pub(crate) working_dir: Option<String>,
    pub(crate) context: Option<String>,
}

#[cfg(test)]
thread_local! {
    pub(crate) static EXTERNAL_CLI_SCAN_CALLS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

pub(crate) fn latest_external_cli_continuation_suggestion() -> Option<String> {
    #[cfg(test)]
    EXTERNAL_CLI_SCAN_CALLS.with(|calls| calls.set(calls.get() + 1));
    // Tests must stay hermetic: scanning the real ~/.codex/~/.claude history makes
    // the welcome-hint layout depend on the developer's machine state and breaks
    // deterministic rendering assertions. Skip the scan under test.
    if cfg!(test) {
        return None;
    }
    let home = std::env::var_os("HOME").map(PathBuf::from)?;
    std::panic::catch_unwind(AssertUnwindSafe(|| {
        latest_external_cli_continuation_suggestion_from_home(&home)
    }))
    .ok()
    .flatten()
}

pub(crate) fn latest_external_cli_continuation_suggestion_from_home(home: &Path) -> Option<String> {
    let mut candidates = Vec::new();
    candidates.extend(latest_jsonl_candidates(
        &home.join(".codex/sessions"),
        "Codex",
        32,
    ));
    candidates.extend(latest_jsonl_candidates(
        &home.join(".claude/projects"),
        "Claude Code",
        32,
    ));
    latest_external_cli_continuation_suggestion_from_candidates(candidates)
}

pub(crate) fn latest_external_cli_continuation_suggestion_from_candidates(
    candidates: Vec<ExternalCliSessionCandidate>,
) -> Option<String> {
    let candidate = candidates
        .into_iter()
        .max_by_key(|candidate| candidate.modified)?;
    let location = candidate
        .working_dir
        .as_deref()
        .and_then(|dir| Path::new(dir).file_name())
        .and_then(|name| name.to_str())
        .filter(|name| !name.trim().is_empty())
        .map(|name| format!(" in {name}"))
        .unwrap_or_default();
    let context = candidate
        .context
        .as_deref()
        .map(|context| format!(": {}", compact_tool_text(context, 72)))
        .unwrap_or_default();
    Some(format!(
        "continue the latest {source} session{location}{context}",
        source = candidate.source
    ))
}

pub(crate) fn latest_jsonl_candidates(
    root: &Path,
    source: &'static str,
    scan_limit: usize,
) -> Vec<ExternalCliSessionCandidate> {
    if !root.is_dir() {
        return Vec::new();
    }
    let mut files = Vec::new();
    collect_recent_jsonl_files(root, &mut files, scan_limit.saturating_mul(8));
    files.sort_by_key(|file| std::cmp::Reverse(file.1));
    files.truncate(scan_limit);
    files
        .into_iter()
        .filter_map(|(path, modified)| external_cli_candidate_from_jsonl(&path, source, modified))
        .collect()
}

pub(crate) fn collect_recent_jsonl_files(
    root: &Path,
    files: &mut Vec<(PathBuf, SystemTime)>,
    max_files: usize,
) {
    if files.len() >= max_files {
        return;
    }
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        if files.len() >= max_files {
            break;
        }
        let path = entry.path();
        let Ok(metadata) = entry.metadata() else {
            continue;
        };
        if metadata.is_dir() {
            collect_recent_jsonl_files(&path, files, max_files);
        } else if path.extension().and_then(|ext| ext.to_str()) == Some("jsonl") {
            files.push((path, metadata.modified().unwrap_or(SystemTime::UNIX_EPOCH)));
        }
    }
}

pub(crate) fn external_cli_candidate_from_jsonl(
    path: &Path,
    source: &'static str,
    modified: SystemTime,
) -> Option<ExternalCliSessionCandidate> {
    let file = File::open(path).ok()?;
    let reader = BufReader::new(file);
    let mut working_dir = None;
    let mut last_user_text = None;
    let mut summary_text = None;
    for line in reader.lines().map_while(Result::ok) {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) else {
            continue;
        };
        if working_dir.is_none() {
            working_dir = value
                .get("cwd")
                .or_else(|| value.get("payload").and_then(|payload| payload.get("cwd")))
                .and_then(|value| value.as_str())
                .map(str::to_string);
        }
        if summary_text.is_none() {
            summary_text = value
                .get("summary")
                .or_else(|| {
                    value
                        .get("payload")
                        .and_then(|payload| payload.get("summary"))
                })
                .and_then(|value| value.as_str())
                .map(str::trim)
                .filter(|text| !text.is_empty())
                .map(str::to_string);
        }
        if jsonl_message_role(&value) == Some("user")
            && let Some(text) = jsonl_message_text(&value)
            && !text.trim().is_empty()
        {
            last_user_text = Some(text);
        }
    }
    if working_dir.is_none() && last_user_text.is_none() && summary_text.is_none() {
        return None;
    }
    Some(ExternalCliSessionCandidate {
        source,
        modified,
        working_dir,
        context: last_user_text.or(summary_text),
    })
}

pub(crate) fn jsonl_message_role(value: &serde_json::Value) -> Option<&str> {
    value
        .get("message")
        .and_then(|message| message.get("role"))
        .or_else(|| value.get("role"))
        .or_else(|| value.get("payload").and_then(|payload| payload.get("role")))
        .and_then(|role| role.as_str())
}

pub(crate) fn jsonl_message_text(value: &serde_json::Value) -> Option<String> {
    let content = value
        .get("message")
        .and_then(|message| message.get("content"))
        .or_else(|| value.get("content"))
        .or_else(|| {
            value
                .get("payload")
                .and_then(|payload| payload.get("content"))
        })?;
    text_from_json_content(content)
}

pub(crate) fn text_from_json_content(value: &serde_json::Value) -> Option<String> {
    if let Some(text) = value
        .as_str()
        .map(str::trim)
        .filter(|text| !text.is_empty())
    {
        return Some(text.to_string());
    }
    let blocks = value.as_array()?;
    let text = blocks
        .iter()
        .filter_map(|block| {
            block
                .get("text")
                .or_else(|| block.get("content"))
                .and_then(|value| value.as_str())
                .map(str::trim)
                .filter(|text| !text.is_empty())
        })
        .collect::<Vec<_>>()
        .join(" ");
    if text.is_empty() { None } else { Some(text) }
}
