use super::*;

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct GitHubIssueSyncUiState {
    pub(crate) syncing: bool,
    pub(crate) last_message: Option<String>,
    pub(crate) last_error: Option<String>,
}

impl GitHubIssueSyncUiState {
    pub(crate) fn label(&self) -> Option<String> {
        if self.syncing {
            return Some("syncing from GitHub in the background".to_string());
        }
        if let Some(error) = &self.last_error {
            return Some(format!("sync failed · {error}"));
        }
        self.last_message.clone()
    }

    pub(crate) fn guidance(&self) -> Option<String> {
        let error = self.last_error.as_deref()?;
        Some(issue_sync_error_guidance(error).to_string())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct GitHubIssueBrowserState {
    pub(crate) repo: String,
    pub(crate) filter_label: String,
    pub(crate) selected: usize,
    pub(crate) list_scroll: usize,
    pub(crate) preview_scroll: usize,
    pub(crate) issues: Vec<GitHubIssuePreview>,
}

impl GitHubIssueBrowserState {
    pub(crate) fn sample() -> Self {
        Self {
            repo: "1jehuang/jcode".to_string(),
            filter_label: "priority · open · local cache".to_string(),
            selected: 0,
            list_scroll: 0,
            preview_scroll: 0,
            issues: vec![
                GitHubIssuePreview {
                    number: 342,
                    priority: "P0".to_string(),
                    title: "Desktop reload can lose the active chat surface".to_string(),
                    labels: vec!["bug".to_string(), "desktop".to_string(), "regression".to_string()],
                    age: "2d".to_string(),
                    comments: 8,
                    state: GitHubIssueVisualState::Selected,
                    body_lines: vec![
                        "When the desktop process reloads while a session is streaming, the window sometimes returns to the welcome state instead of the active chat.".to_string(),
                        "Expected: reload handoff preserves the session id, transcript, draft, and scroll position.".to_string(),
                        "Observed: the app opens, paints the shell, then falls back to a fresh session.".to_string(),
                    ],
                    comment_lines: vec![
                        "maintainer: happens more often after resizing during handoff".to_string(),
                        "agent note: likely snapshot restore ordering or worker init race".to_string(),
                    ],
                    priority_reason: "explicit regression label, data-loss risk, bounded desktop repro".to_string(),
                },
                GitHubIssuePreview {
                    number: 337,
                    priority: "P1".to_string(),
                    title: "Tool-card animation does too much work offscreen".to_string(),
                    labels: vec!["performance".to_string(), "desktop".to_string()],
                    age: "5d".to_string(),
                    comments: 4,
                    state: GitHubIssueVisualState::Idle,
                    body_lines: vec![
                        "Large transcripts still spend frame time walking tool-card metadata for rows far outside the viewport.".to_string(),
                        "The UI remains correct, but long sessions can miss frame budget during streaming.".to_string(),
                    ],
                    comment_lines: vec![
                        "profiling: check viewport clipping before card motion".to_string(),
                    ],
                    priority_reason: "perf label plus objective frame-time validation path".to_string(),
                },
                GitHubIssuePreview {
                    number: 329,
                    priority: "P2".to_string(),
                    title: "Provider auth errors should link to doctor output".to_string(),
                    labels: vec!["auth".to_string(), "ux".to_string()],
                    age: "1w".to_string(),
                    comments: 2,
                    state: GitHubIssueVisualState::Idle,
                    body_lines: vec![
                        "Desktop auth failures currently show a terse provider error.".to_string(),
                        "It should offer a one-click path to the same diagnostic information as `jcode auth doctor`.".to_string(),
                    ],
                    comment_lines: vec!["nice to have after core desktop stability".to_string()],
                    priority_reason: "important UX improvement, but not blocking active work".to_string(),
                },
            ],
        }
    }

    pub(crate) fn selected_issue(&self) -> Option<&GitHubIssuePreview> {
        self.issues.get(self.selected)
    }

    pub(crate) fn selected_issue_mut(&mut self) -> Option<&mut GitHubIssuePreview> {
        self.issues.get_mut(self.selected)
    }

    pub(crate) fn select_first(&mut self) {
        self.set_selected(0);
    }

    pub(crate) fn select_last(&mut self) {
        self.set_selected(self.issues.len().saturating_sub(1));
    }

    pub(crate) fn move_selection(&mut self, delta: i32) {
        if self.issues.is_empty() {
            self.selected = 0;
            self.list_scroll = 0;
            self.preview_scroll = 0;
            return;
        }
        let selected = self.selected as i32 + delta;
        self.set_selected(selected.clamp(0, self.issues.len().saturating_sub(1) as i32) as usize);
    }

    pub(crate) fn set_selected(&mut self, selected: usize) {
        if self.issues.is_empty() {
            self.selected = 0;
            self.list_scroll = 0;
            self.preview_scroll = 0;
            return;
        }
        self.selected = selected.min(self.issues.len() - 1);
        self.preview_scroll = 0;
        let visible_rows = 6usize;
        if self.selected < self.list_scroll {
            self.list_scroll = self.selected;
        } else if self.selected >= self.list_scroll.saturating_add(visible_rows) {
            self.list_scroll = self.selected.saturating_sub(visible_rows - 1);
        }
        self.sync_visual_selection_state();
    }

    pub(crate) fn sync_visual_selection_state(&mut self) {
        for (index, issue) in self.issues.iter_mut().enumerate() {
            if issue.state != GitHubIssueVisualState::Active {
                issue.state = if index == self.selected {
                    GitHubIssueVisualState::Selected
                } else {
                    GitHubIssueVisualState::Idle
                };
            }
        }
    }

    pub(crate) fn scroll_preview_lines(&mut self, lines: i32) {
        let max_scroll = self
            .selected_issue()
            .map(|issue| issue.body_lines.len().saturating_sub(1))
            .unwrap_or_default();
        if lines > 0 {
            self.preview_scroll = self.preview_scroll.saturating_sub(lines as usize);
        } else if lines < 0 {
            self.preview_scroll = self
                .preview_scroll
                .saturating_add(lines.unsigned_abs() as usize)
                .min(max_scroll);
        }
    }

    pub(crate) fn mark_selected_active(&mut self) {
        for issue in &mut self.issues {
            if issue.state == GitHubIssueVisualState::Active {
                issue.state = GitHubIssueVisualState::Idle;
            }
        }
        if let Some(issue) = self.selected_issue_mut() {
            issue.state = GitHubIssueVisualState::Active;
        }
    }

    pub(crate) fn selected_issue_context_prompt(&self) -> Option<String> {
        let issue = self.selected_issue()?;
        Some(issue_context_prompt(&self.repo, issue))
    }
}

pub(crate) fn issue_context_prompt(repo: &str, issue: &GitHubIssuePreview) -> String {
    let labels = if issue.labels.is_empty() {
        "none".to_string()
    } else {
        issue.labels.join(", ")
    };
    let body = issue.body_lines.join("\n");
    let comments = if issue.comment_lines.is_empty() {
        "none".to_string()
    } else {
        issue.comment_lines.join("\n")
    };
    format!(
        "GitHub issue mission\n\nRepository: {repo}\nIssue: #{}\nTitle: {}\nPriority: {}\nLabels: {labels}\nAge: {}\nComment count: {}\nPriority rationale: {}\n\nIssue body:\n{body}\n\nRecent comments:\n{comments}\n\nMission objective: investigate and, when safe, implement a fix for this issue in the local repository.\n\nOperating instructions:\n1. Start by inspecting the relevant code and reproducing or narrowing the behavior.\n2. Preserve existing user changes and avoid destructive actions.\n3. If implementing a fix, add or update targeted tests.\n4. Run the maximum reasonable validation before reporting completion.\n5. Report evidence, remaining gaps, and any follow-up work.\n6. Do not rely on the GitHub web UI unless local cache context is insufficient.",
        issue.number, issue.title, issue.priority, issue.age, issue.comments, issue.priority_reason
    )
}

pub(crate) fn issue_sync_error_guidance(error: &str) -> &'static str {
    let lower = error.to_ascii_lowercase();
    if lower.contains("not installed")
        || lower.contains("not on path")
        || lower.contains("no such file")
    {
        "Install GitHub CLI `gh`, authenticate it, then press r or Ctrl+R to sync."
    } else if lower.contains("auth")
        || lower.contains("authentication")
        || lower.contains("login")
        || lower.contains("unauthorized")
        || lower.contains("forbidden")
    {
        "Run `gh auth login` or refresh GitHub CLI auth, then press r or Ctrl+R to sync."
    } else if lower.contains("could not find a github origin") || lower.contains("origin remote") {
        "Add a GitHub origin remote for this repository, then press r or Ctrl+R to sync."
    } else {
        "Using cached GitHub issues. Press r or Ctrl+R to retry background sync."
    }
}

pub(crate) fn compact_issue_sync_error(error: &str) -> String {
    let mut compact = error.split_whitespace().collect::<Vec<_>>().join(" ");
    if compact.len() > 160 {
        compact.truncate(157);
        compact.push_str("...");
    }
    if compact.is_empty() {
        "unknown error".to_string()
    } else {
        compact
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum GitHubIssueVisualState {
    Idle,
    Selected,
    Active,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) struct GitHubIssuePreview {
    pub(crate) number: u64,
    pub(crate) priority: String,
    pub(crate) title: String,
    pub(crate) labels: Vec<String>,
    pub(crate) age: String,
    pub(crate) comments: u32,
    pub(crate) state: GitHubIssueVisualState,
    pub(crate) body_lines: Vec<String>,
    pub(crate) comment_lines: Vec<String>,
    pub(crate) priority_reason: String,
}

impl SingleSessionApp {
    pub(crate) fn take_github_issue_sync_request(&mut self) -> bool {
        std::mem::take(&mut self.pending_issue_sync_request)
    }

    pub(crate) fn note_github_issue_sync_already_running(&mut self) {
        self.side_panel.github_issue_sync.syncing = true;
        self.side_panel.github_issue_sync.last_error = None;
        self.side_panel.github_issue_sync.last_message =
            Some("GitHub issue sync already running; cached issues remain interactive".to_string());
    }

    pub(crate) fn apply_github_issue_sync_result(
        &mut self,
        result: std::result::Result<crate::desktop_issue_cache::GitHubIssueSyncSummary, String>,
    ) {
        self.pending_issue_sync_request = false;
        self.side_panel.github_issue_sync.syncing = false;
        match result {
            Ok(summary) => {
                let warning_label = if summary.comment_fetch_errors == 0 {
                    String::new()
                } else {
                    format!(
                        " · {} comment refresh warning(s)",
                        summary.comment_fetch_errors
                    )
                };
                let message = format!(
                    "synced {} GitHub issues for {} in {}ms · cache {}{}",
                    summary.issue_count,
                    summary.repo,
                    summary.elapsed.as_millis(),
                    summary.cache_path.display(),
                    warning_label
                );
                self.side_panel.github_issues = summary.browser;
                self.side_panel.github_issue_sync.last_error = None;
                self.side_panel.github_issue_sync.last_message = Some(message.clone());
                self.set_status(SingleSessionStatus::Info(message));
            }
            Err(error) => {
                let compact_error = compact_issue_sync_error(&error);
                self.side_panel.github_issue_sync.last_error = Some(compact_error.clone());
                self.side_panel.github_issue_sync.last_message =
                    Some(issue_sync_error_guidance(&error).to_string());
                self.set_status(SingleSessionStatus::Info(format!(
                    "GitHub issue sync failed · {compact_error}"
                )));
            }
        }
    }

    pub(crate) fn issue_browser_visible(&self) -> bool {
        self.side_panel.visible
    }

    pub(crate) fn request_issue_browser_sync(&mut self) {
        self.pending_issue_sync_request = true;
        self.side_panel.github_issue_sync.syncing = true;
        self.side_panel.github_issue_sync.last_error = None;
        self.side_panel.github_issue_sync.last_message =
            Some("syncing from GitHub via gh; cached issues remain interactive".to_string());
    }

    pub(crate) fn toggle_issue_browser(&mut self, visible: Option<bool>) -> KeyOutcome {
        let visible = visible.unwrap_or(!self.side_panel.visible);
        self.side_panel.visible = visible;
        self.side_panel.focus = if visible {
            DesktopSidePanelFocus::IssueList
        } else {
            DesktopSidePanelFocus::Chat
        };
        let cache_status = visible
            .then(|| self.refresh_issue_browser_from_cache())
            .flatten();
        if visible {
            self.request_issue_browser_sync();
        }
        self.draft.clear();
        self.draft_cursor = 0;
        self.composer.input_undo_stack.clear();
        self.set_status(SingleSessionStatus::Info(cache_status.unwrap_or_else(
            || {
                if visible {
                    "showing local GitHub issue browser".to_string()
                } else {
                    "hid local GitHub issue browser".to_string()
                }
            },
        )));
        KeyOutcome::Redraw
    }

    #[cfg(not(test))]
    pub(crate) fn refresh_issue_browser_from_cache(&mut self) -> Option<String> {
        match crate::desktop_issue_cache::load_current_repo_issue_browser() {
            Ok(Some(browser)) => {
                let repo = browser.repo.clone();
                let count = browser.issues.len();
                self.side_panel.github_issues = browser;
                Some(format!("showing {count} cached GitHub issues for {repo}"))
            }
            Ok(None) => None,
            Err(error) => Some(format!(
                "showing sample issues; cache unavailable: {error:#}"
            )),
        }
    }

    #[cfg(test)]
    pub(crate) fn refresh_issue_browser_from_cache(&mut self) -> Option<String> {
        None
    }

    pub(crate) fn handle_issue_browser_key(&mut self, key: &KeyInput) -> Option<KeyOutcome> {
        if !self.side_panel.visible {
            return None;
        }

        if matches!(key, KeyInput::Autocomplete) && self.draft.is_empty() {
            self.side_panel.focus_next();
            return Some(KeyOutcome::Redraw);
        }

        if let KeyInput::Character(text) = key
            && text.starts_with('/')
        {
            self.side_panel.focus = DesktopSidePanelFocus::Chat;
            return None;
        }

        if matches!(key, KeyInput::RefreshSessions) {
            self.request_issue_browser_sync();
            return Some(KeyOutcome::Redraw);
        }

        match self.side_panel.focus {
            DesktopSidePanelFocus::Chat => None,
            DesktopSidePanelFocus::IssueList => Some(self.handle_issue_list_key(key)),
            DesktopSidePanelFocus::IssuePreview => Some(self.handle_issue_preview_key(key)),
        }
    }

    pub(crate) fn handle_issue_list_key(&mut self, key: &KeyInput) -> KeyOutcome {
        match key {
            KeyInput::Escape => {
                self.side_panel.focus = DesktopSidePanelFocus::Chat;
                KeyOutcome::Redraw
            }
            KeyInput::SubmitDraft => self.investigate_selected_issue(),
            KeyInput::Character(text) if text.eq_ignore_ascii_case("r") => {
                self.request_issue_browser_sync();
                KeyOutcome::Redraw
            }
            KeyInput::ModelPickerMove(delta) => {
                self.side_panel.github_issues.move_selection(*delta);
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyPages(pages) => {
                self.side_panel.github_issues.move_selection(-pages * 5);
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "j" => {
                self.side_panel.github_issues.move_selection(1);
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "k" => {
                self.side_panel.github_issues.move_selection(-1);
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "g" => {
                self.side_panel.github_issues.select_first();
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "G" => {
                self.side_panel.github_issues.select_last();
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "l" => {
                self.side_panel.focus = DesktopSidePanelFocus::IssuePreview;
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "h" => {
                self.side_panel.focus_previous();
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text.eq_ignore_ascii_case("q") => {
                self.toggle_issue_browser(Some(false))
            }
            _ => KeyOutcome::None,
        }
    }

    pub(crate) fn handle_issue_preview_key(&mut self, key: &KeyInput) -> KeyOutcome {
        match key {
            KeyInput::Escape => {
                self.side_panel.focus = DesktopSidePanelFocus::Chat;
                KeyOutcome::Redraw
            }
            KeyInput::SubmitDraft => self.investigate_selected_issue(),
            KeyInput::Character(text) if text.eq_ignore_ascii_case("r") => {
                self.request_issue_browser_sync();
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyLines(lines) => {
                self.side_panel.github_issues.scroll_preview_lines(*lines);
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyPages(pages) => {
                self.side_panel
                    .github_issues
                    .scroll_preview_lines(*pages * 6);
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyToTop => {
                self.side_panel.github_issues.preview_scroll = 0;
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyToBottom => {
                self.side_panel
                    .github_issues
                    .scroll_preview_lines(i32::MIN + 1);
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "j" => {
                self.side_panel.github_issues.scroll_preview_lines(-1);
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "k" => {
                self.side_panel.github_issues.scroll_preview_lines(1);
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "h" => {
                self.side_panel.focus = DesktopSidePanelFocus::IssueList;
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text == "l" => {
                self.side_panel.focus = DesktopSidePanelFocus::Chat;
                KeyOutcome::Redraw
            }
            KeyInput::Character(text) if text.eq_ignore_ascii_case("q") => {
                self.toggle_issue_browser(Some(false))
            }
            KeyInput::ModelPickerMove(delta) => {
                self.side_panel.github_issues.move_selection(*delta);
                KeyOutcome::Redraw
            }
            _ => KeyOutcome::None,
        }
    }

    pub(crate) fn investigate_selected_issue(&mut self) -> KeyOutcome {
        let Some(message) = self
            .side_panel
            .github_issues
            .selected_issue_context_prompt()
        else {
            return KeyOutcome::None;
        };
        self.side_panel.github_issues.mark_selected_active();
        self.side_panel.focus = DesktopSidePanelFocus::Chat;
        self.record_user_submit(&message, &[]);
        if let Some(session) = &self.session {
            KeyOutcome::SendDraft {
                session_id: session.session_id.clone(),
                title: session.title.clone(),
                message,
                images: Vec::new(),
            }
        } else {
            KeyOutcome::StartFreshSession {
                message,
                images: Vec::new(),
            }
        }
    }
}
