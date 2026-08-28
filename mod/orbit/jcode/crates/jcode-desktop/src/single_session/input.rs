use super::*;

impl SingleSessionApp {
    pub(crate) fn handle_key(&mut self, key: KeyInput) -> KeyOutcome {
        if key == KeyInput::ExitApp {
            return KeyOutcome::Exit;
        }

        if self.stdin_response.is_some() {
            return self.handle_stdin_response_key(key);
        }

        if self.session_switcher.open
            && self.session_switcher.preview
            && let Some(outcome) = self.handle_session_switcher_preview_key(&key)
        {
            return outcome;
        }

        if self.session_switcher.open {
            return self.handle_session_switcher_key(key);
        }

        if matches!(
            self.active_inline_widget_mode(),
            Some(InlineWidgetMode::Interactive)
        ) && self.model_picker.open
        {
            return self.handle_model_picker_key(key);
        }

        if self.model_picker.open
            && self.model_picker.preview
            && let Some(outcome) = self.handle_model_picker_preview_key(&key)
        {
            return outcome;
        }

        if self.active_inline_widget() == Some(InlineWidgetKind::SlashSuggestions)
            && let Some(outcome) = self.handle_slash_suggestion_key(&key)
        {
            return outcome;
        }

        if let Some(outcome) = self.handle_issue_browser_key(&key) {
            return outcome;
        }

        match key {
            KeyInput::SpawnPanel => KeyOutcome::SpawnSession,
            KeyInput::SpawnSelfDevSession => KeyOutcome::SpawnSelfDevSession,
            KeyInput::SpawnHomeSession => KeyOutcome::SpawnHomeSession,
            KeyInput::OpenSessionSwitcher => self.open_session_switcher(),
            KeyInput::OpenModelPicker => self.open_model_picker(),
            KeyInput::HotkeyHelp => {
                self.toggle_read_only_inline_widget(InlineWidgetKind::HotkeyHelp)
            }
            KeyInput::ToggleSessionInfo => {
                self.toggle_read_only_inline_widget(InlineWidgetKind::SessionInfo)
            }
            KeyInput::RefreshSessions if self.welcome.recovery_session_count > 0 => {
                KeyOutcome::RestoreCrashedSessions
            }
            KeyInput::RefreshSessions => KeyOutcome::Redraw,
            KeyInput::ExitApp => KeyOutcome::Exit,
            KeyInput::AdjustTextScale(direction) => {
                self.adjust_text_scale(direction);
                KeyOutcome::Redraw
            }
            KeyInput::ResetTextScale => {
                self.view.text_scale = 1.0;
                KeyOutcome::Redraw
            }
            KeyInput::CancelGeneration => {
                if self.is_processing {
                    KeyOutcome::CancelGeneration
                } else {
                    KeyOutcome::None
                }
            }
            KeyInput::ScrollBodyPages(pages) => {
                self.scroll_body_lines((pages * 12) as f32);
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyLines(lines) => {
                self.scroll_body_lines(lines as f32);
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyToTop => {
                self.scroll_body_to_top();
                KeyOutcome::Redraw
            }
            KeyInput::ScrollBodyToBottom => {
                self.scroll_body_to_bottom();
                KeyOutcome::Redraw
            }
            KeyInput::JumpPrompt(direction) => {
                self.jump_prompt(direction);
                KeyOutcome::Redraw
            }
            KeyInput::CopyLatestResponse => self
                .latest_assistant_response()
                .map(KeyOutcome::CopyLatestResponse)
                .unwrap_or(KeyOutcome::None),
            KeyInput::CopyLatestCodeBlock => self.copy_latest_code_block(),
            KeyInput::CopyTranscript => self.copy_transcript(),
            KeyInput::ModelPickerMove(_) => KeyOutcome::None,
            KeyInput::CycleModel(direction) => KeyOutcome::CycleModel(direction),
            KeyInput::CycleReasoningEffort(direction) => {
                KeyOutcome::CycleReasoningEffort(direction)
            }
            KeyInput::AttachClipboardImage => KeyOutcome::AttachClipboardImage,
            KeyInput::ClearAttachedImages => {
                if self.clear_attached_images() {
                    KeyOutcome::Redraw
                } else {
                    KeyOutcome::None
                }
            }
            KeyInput::PasteText => KeyOutcome::PasteText,
            KeyInput::QueueDraft if self.is_processing => self.queue_draft(),
            KeyInput::RetrieveQueuedDraft => self.retrieve_queued_draft_for_edit(),
            KeyInput::QueueDraft => self.submit_draft(),
            KeyInput::SubmitDraft => self.submit_draft(),
            KeyInput::Escape if self.show_help => {
                self.close_inline_widgets();
                KeyOutcome::Redraw
            }
            KeyInput::Escape if self.show_session_info => {
                self.close_inline_widgets();
                KeyOutcome::Redraw
            }
            KeyInput::Character(text)
                if (self.show_help || self.show_session_info) && text.eq_ignore_ascii_case("q") =>
            {
                self.close_inline_widgets();
                KeyOutcome::Redraw
            }
            KeyInput::Escape => {
                if self.is_processing {
                    KeyOutcome::CancelGeneration
                } else {
                    self.clear_draft_for_escape()
                }
            }
            KeyInput::Enter => {
                self.insert_draft_text("\n");
                KeyOutcome::Redraw
            }
            KeyInput::Backspace => {
                self.delete_previous_char();
                self.sync_inline_previews_from_draft()
                    .unwrap_or(KeyOutcome::Redraw)
            }
            KeyInput::DeletePreviousWord => {
                self.delete_previous_word();
                self.sync_inline_previews_from_draft()
                    .unwrap_or(KeyOutcome::Redraw)
            }
            KeyInput::DeleteNextWord => {
                self.delete_next_word();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::DeleteNextChar => {
                self.delete_next_char();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::MoveCursorWordLeft => {
                self.move_cursor_word_left();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::MoveCursorWordRight => {
                self.move_cursor_word_right();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::MoveCursorLeft => {
                self.move_cursor_left();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::MoveCursorRight => {
                self.move_cursor_right();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::MoveToLineStart => {
                self.move_to_line_start();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::MoveToLineEnd => {
                self.move_to_line_end();
                self.sync_slash_suggestions_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::DeleteToLineStart => {
                self.delete_to_line_start();
                self.sync_inline_previews_from_draft()
                    .unwrap_or(KeyOutcome::Redraw)
            }
            KeyInput::DeleteToLineEnd => {
                self.delete_to_line_end();
                self.sync_inline_previews_from_draft()
                    .unwrap_or(KeyOutcome::Redraw)
            }
            KeyInput::CutInputLine => self.cut_input_line(),
            KeyInput::UndoInput => {
                self.undo_input_change();
                self.sync_inline_previews_from_draft();
                KeyOutcome::Redraw
            }
            KeyInput::Autocomplete => self.autocomplete_draft(),
            KeyInput::Character(text) => {
                self.insert_draft_text(&text);
                self.sync_inline_previews_from_draft()
                    .unwrap_or(KeyOutcome::Redraw)
            }
            _ => KeyOutcome::None,
        }
    }

    pub(crate) fn text_scale(&self) -> f32 {
        self.view.text_scale
    }

    pub(crate) fn has_active_selection(&self) -> bool {
        self.selection.anchor.is_some()
            || self.selection.focus.is_some()
            || self.selection.draft_anchor.is_some()
            || self.selection.draft_focus.is_some()
    }

    pub(crate) fn adjust_text_scale(&mut self, direction: i8) {
        let delta = direction as f32 * SINGLE_SESSION_TEXT_SCALE_STEP;
        self.view.text_scale = (self.view.text_scale + delta)
            .clamp(SINGLE_SESSION_MIN_TEXT_SCALE, SINGLE_SESSION_MAX_TEXT_SCALE);
    }

    pub(crate) fn draft_cursor_line_col(&self) -> (usize, usize) {
        let before_cursor = &self.draft[..self.draft_cursor.min(self.draft.len())];
        let line = before_cursor.chars().filter(|ch| *ch == '\n').count();
        let column = before_cursor
            .rsplit('\n')
            .next()
            .unwrap_or_default()
            .chars()
            .count();
        (line, column)
    }

    pub(crate) fn draft_cursor_line_byte_index(&self) -> (usize, usize) {
        let cursor = self.draft_cursor.min(self.draft.len());
        let line = self.draft[..cursor]
            .chars()
            .filter(|ch| *ch == '\n')
            .count();
        let line_start = line_start(&self.draft, cursor);
        (line, cursor - line_start)
    }

    pub(crate) fn composer_cursor_line_byte_index(&self) -> (usize, usize) {
        let (line, index) = self.draft_cursor_line_byte_index();
        if line == 0 {
            (line, self.composer_prompt().len() + index)
        } else {
            (line, index)
        }
    }

    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) fn set_draft_cursor_line_col(&mut self, target_line: usize, target_col: usize) {
        self.draft_cursor = self.draft_byte_index_for_line_col(target_line, target_col);
        self.clamp_draft_cursor();
        self.clear_selection();
        self.clear_draft_selection();
    }

    pub(crate) fn draft_byte_index_for_line_col(
        &self,
        target_line: usize,
        target_col: usize,
    ) -> usize {
        let mut line = 0usize;
        let mut line_start = 0usize;
        for (index, ch) in self.draft.char_indices() {
            if line == target_line {
                break;
            }
            if ch == '\n' {
                line += 1;
                line_start = index + ch.len_utf8();
            }
        }

        if line < target_line {
            return self.draft.len();
        }

        let line_end = line_end(&self.draft, line_start);
        self.draft[line_start..line_end]
            .char_indices()
            .map(|(offset, _)| line_start + offset)
            .chain(std::iter::once(line_end))
            .nth(target_col)
            .unwrap_or(line_end)
    }

    pub(crate) fn cut_input_line(&mut self) -> KeyOutcome {
        if self.draft.is_empty() {
            return KeyOutcome::None;
        }
        self.remember_input_undo_state();
        let text = std::mem::take(&mut self.draft);
        self.draft_cursor = 0;
        self.set_status(SingleSessionStatus::Info("cut input line".to_string()));
        KeyOutcome::CutDraftToClipboard(text)
    }

    pub(crate) fn begin_selection(&mut self, point: SelectionPoint) {
        self.selection.anchor = Some(point);
        self.selection.focus = Some(point);
    }

    pub(crate) fn update_selection(&mut self, point: SelectionPoint) {
        if self.selection.anchor.is_some() {
            self.selection.focus = Some(point);
        }
    }

    pub(crate) fn clear_selection(&mut self) {
        self.selection.anchor = None;
        self.selection.focus = None;
    }

    pub(crate) fn begin_draft_selection(&mut self, point: SelectionPoint) {
        self.clear_selection();
        self.selection.draft_anchor = Some(point);
        self.selection.draft_focus = Some(point);
        self.draft_cursor = self.draft_byte_index_for_line_col(point.line, point.column);
        self.clamp_draft_cursor();
    }

    pub(crate) fn update_draft_selection(&mut self, point: SelectionPoint) {
        if self.selection.draft_anchor.is_some() {
            self.selection.draft_focus = Some(point);
            self.draft_cursor = self.draft_byte_index_for_line_col(point.line, point.column);
            self.clamp_draft_cursor();
        }
    }

    pub(crate) fn clear_draft_selection(&mut self) {
        self.selection.draft_anchor = None;
        self.selection.draft_focus = None;
    }

    pub(crate) fn draft_selection_points(&self) -> Option<(SelectionPoint, SelectionPoint)> {
        let anchor = self.selection.draft_anchor?;
        let focus = self.selection.draft_focus?;
        if selection_point_cmp(anchor, focus).is_gt() {
            Some((focus, anchor))
        } else {
            Some((anchor, focus))
        }
    }

    pub(crate) fn draft_selection_segments(&self) -> Vec<SelectionLineSegment> {
        let lines: Vec<String> = self.draft.split('\n').map(ToString::to_string).collect();
        let Some((start, end)) = self.draft_selection_points() else {
            return Vec::new();
        };
        if start == end || start.line >= lines.len() {
            return Vec::new();
        }
        let end_line = end.line.min(lines.len().saturating_sub(1));
        let mut segments = Vec::new();
        for (line_index, line) in lines.iter().enumerate().take(end_line + 1).skip(start.line) {
            let line_len = line.chars().count();
            let prompt_columns = if line_index == 0 {
                self.composer_prompt().chars().count()
            } else {
                0
            };
            let start_column = if line_index == start.line {
                start.column.min(line_len)
            } else {
                0
            };
            let end_column = if line_index == end_line {
                end.column.min(line_len)
            } else {
                line_len
            };
            if start_column != end_column || (start.line != end.line && line_len == 0) {
                segments.push(SelectionLineSegment {
                    line: line_index,
                    start_column: start_column + prompt_columns,
                    end_column: end_column + prompt_columns,
                });
            }
        }
        segments
    }

    pub(crate) fn selected_draft_text(&mut self) -> Option<String> {
        let (start, end) = self.draft_selection_points()?;
        if start == end {
            self.clear_draft_selection();
            return None;
        }
        let start_index = self.draft_byte_index_for_line_col(start.line, start.column);
        let end_index = self.draft_byte_index_for_line_col(end.line, end.column);
        let (start_index, end_index) = if start_index <= end_index {
            (start_index, end_index)
        } else {
            (end_index, start_index)
        };
        let selected = self.draft.get(start_index..end_index).map(str::to_string);
        self.clear_draft_selection();
        selected.filter(|text| !text.is_empty())
    }

    pub(crate) fn draft_selection_range(&self) -> Option<(usize, usize)> {
        let (start, end) = self.draft_selection_points()?;
        if start == end {
            return None;
        }
        let start_index = self.draft_byte_index_for_line_col(start.line, start.column);
        let end_index = self.draft_byte_index_for_line_col(end.line, end.column);
        if start_index <= end_index {
            Some((start_index, end_index)).filter(|(start, end)| start != end)
        } else {
            Some((end_index, start_index)).filter(|(start, end)| start != end)
        }
    }

    pub(crate) fn replace_draft_selection_with(&mut self, text: &str) -> bool {
        let Some((start, end)) = self.draft_selection_range() else {
            return false;
        };
        self.remember_input_undo_state();
        self.draft.replace_range(start..end, text);
        self.draft_cursor = start + text.len();
        self.clear_draft_selection();
        true
    }

    pub(crate) fn delete_draft_selection(&mut self) -> bool {
        self.replace_draft_selection_with("")
    }

    pub(crate) fn selection_points(&self) -> Option<(SelectionPoint, SelectionPoint)> {
        let anchor = self.selection.anchor?;
        let focus = self.selection.focus?;
        if selection_point_cmp(anchor, focus).is_gt() {
            Some((focus, anchor))
        } else {
            Some((anchor, focus))
        }
    }

    pub(crate) fn selection_segments(&self, lines: &[String]) -> Vec<SelectionLineSegment> {
        let Some((start, end)) = self.selection_points() else {
            return Vec::new();
        };
        if start == end || start.line >= lines.len() {
            return Vec::new();
        }

        let end_line = end.line.min(lines.len().saturating_sub(1));
        let mut segments = Vec::new();
        for (line_index, line) in lines.iter().enumerate().take(end_line + 1).skip(start.line) {
            let line_len = line.chars().count();
            let start_column = if line_index == start.line {
                start.column.min(line_len)
            } else {
                0
            };
            let end_column = if line_index == end_line {
                end.column.min(line_len)
            } else {
                line_len
            };
            if start_column != end_column || (start.line != end.line && line_len == 0) {
                segments.push(SelectionLineSegment {
                    line: line_index,
                    start_column,
                    end_column,
                });
            }
        }
        segments
    }

    pub(crate) fn has_body_selection(&self) -> bool {
        self.selection.anchor.is_some() && self.selection.focus.is_some()
    }

    pub(crate) fn has_draft_selection(&self) -> bool {
        self.selection.draft_anchor.is_some() && self.selection.draft_focus.is_some()
    }

    pub(crate) fn selected_text_from_lines(&self, lines: &[String]) -> Option<String> {
        let (start, end) = self.selection_points()?;
        if start == end || start.line >= lines.len() {
            return None;
        }
        let end_line = end.line.min(lines.len().saturating_sub(1));
        let mut selected = Vec::new();
        for (line_index, line) in lines.iter().enumerate().take(end_line + 1).skip(start.line) {
            let line_len = line.chars().count();
            let start_column = if line_index == start.line {
                start.column.min(line_len)
            } else {
                0
            };
            let end_column = if line_index == end_line {
                end.column.min(line_len)
            } else {
                line_len
            };
            selected.push(slice_by_char_columns(line, start_column, end_column));
        }
        let text = selected.join("\n");
        (!text.is_empty()).then_some(text)
    }

    pub(crate) fn insert_draft_text(&mut self, text: &str) {
        if self.replace_draft_selection_with(text) {
            return;
        }
        if !text.is_empty() {
            self.remember_input_undo_state();
        }
        self.clamp_draft_cursor();
        self.draft.insert_str(self.draft_cursor, text);
        self.draft_cursor += text.len();
    }

    pub(crate) fn delete_previous_char(&mut self) {
        if self.delete_draft_selection() {
            return;
        }
        self.clamp_draft_cursor();
        if self.draft_cursor == 0 {
            return;
        }
        self.remember_input_undo_state();
        let previous = previous_char_boundary(&self.draft, self.draft_cursor);
        self.draft.replace_range(previous..self.draft_cursor, "");
        self.draft_cursor = previous;
    }

    pub(crate) fn delete_next_char(&mut self) {
        if self.delete_draft_selection() {
            return;
        }
        self.clamp_draft_cursor();
        if self.draft_cursor >= self.draft.len() {
            return;
        }
        self.remember_input_undo_state();
        let next = next_char_boundary(&self.draft, self.draft_cursor);
        self.draft.replace_range(self.draft_cursor..next, "");
    }

    pub(crate) fn delete_previous_word(&mut self) {
        if self.delete_draft_selection() {
            return;
        }
        self.clamp_draft_cursor();
        let start = previous_word_start(&self.draft, self.draft_cursor);
        if start < self.draft_cursor {
            self.remember_input_undo_state();
        }
        self.draft.replace_range(start..self.draft_cursor, "");
        self.draft_cursor = start;
    }

    pub(crate) fn delete_next_word(&mut self) {
        if self.delete_draft_selection() {
            return;
        }
        self.clamp_draft_cursor();
        let end = next_word_end(&self.draft, self.draft_cursor);
        if end > self.draft_cursor {
            self.remember_input_undo_state();
        }
        self.draft.replace_range(self.draft_cursor..end, "");
    }

    pub(crate) fn move_cursor_left(&mut self) {
        self.clamp_draft_cursor();
        self.draft_cursor = previous_char_boundary(&self.draft, self.draft_cursor);
        self.clear_draft_selection();
    }

    pub(crate) fn move_cursor_right(&mut self) {
        self.clamp_draft_cursor();
        self.draft_cursor = next_char_boundary(&self.draft, self.draft_cursor);
        self.clear_draft_selection();
    }

    pub(crate) fn move_cursor_word_left(&mut self) {
        self.clamp_draft_cursor();
        self.draft_cursor = previous_word_start(&self.draft, self.draft_cursor);
        self.clear_draft_selection();
    }

    pub(crate) fn move_cursor_word_right(&mut self) {
        self.clamp_draft_cursor();
        self.draft_cursor = next_word_end(&self.draft, self.draft_cursor);
        self.clear_draft_selection();
    }

    pub(crate) fn move_to_line_start(&mut self) {
        self.clamp_draft_cursor();
        self.draft_cursor = line_start(&self.draft, self.draft_cursor);
        self.clear_draft_selection();
    }

    pub(crate) fn move_to_line_end(&mut self) {
        self.clamp_draft_cursor();
        self.draft_cursor = line_end(&self.draft, self.draft_cursor);
        self.clear_draft_selection();
    }

    pub(crate) fn delete_to_line_start(&mut self) {
        if self.delete_draft_selection() {
            return;
        }
        self.clamp_draft_cursor();
        let start = line_start(&self.draft, self.draft_cursor);
        if start < self.draft_cursor {
            self.remember_input_undo_state();
        }
        self.draft.replace_range(start..self.draft_cursor, "");
        self.draft_cursor = start;
    }

    pub(crate) fn delete_to_line_end(&mut self) {
        if self.delete_draft_selection() {
            return;
        }
        self.clamp_draft_cursor();
        let end = line_end(&self.draft, self.draft_cursor);
        if end > self.draft_cursor {
            self.remember_input_undo_state();
        }
        self.draft.replace_range(self.draft_cursor..end, "");
    }

    pub(crate) fn clear_draft_for_escape(&mut self) -> KeyOutcome {
        if self.draft.is_empty() {
            return KeyOutcome::None;
        }
        self.remember_input_undo_state();
        self.draft.clear();
        self.draft_cursor = 0;
        self.clear_draft_selection();
        if self.model_picker.open && self.model_picker.preview {
            self.capture_inline_widget_exit();
            self.model_picker.close();
        }
        if self.session_switcher.open && self.session_switcher.preview {
            self.capture_inline_widget_exit();
            self.session_switcher.close();
        }
        self.set_status(SingleSessionStatus::Info(
            "Input cleared - Ctrl+Z to restore".to_string(),
        ));
        KeyOutcome::Redraw
    }

    pub(crate) fn autocomplete_draft(&mut self) -> KeyOutcome {
        let completions = DESKTOP_SLASH_COMMANDS
            .iter()
            .map(|(usage, _)| usage.split_whitespace().next().unwrap_or(*usage))
            .collect::<Vec<_>>();
        let Some((draft, cursor)) =
            complete_slash_command(&self.draft, self.draft_cursor, &completions)
        else {
            return KeyOutcome::None;
        };
        self.remember_input_undo_state();
        self.draft = draft;
        self.draft_cursor = cursor;
        self.clear_draft_selection();
        self.sync_model_picker_preview_from_draft()
            .unwrap_or(KeyOutcome::Redraw)
    }

    pub(crate) fn remember_input_undo_state(&mut self) {
        if self
            .composer
            .input_undo_stack
            .last()
            .is_some_and(|(draft, cursor)| draft == &self.draft && *cursor == self.draft_cursor)
        {
            return;
        }
        self.composer
            .input_undo_stack
            .push((self.draft.clone(), self.draft_cursor));
        const MAX_UNDO: usize = 64;
        if self.composer.input_undo_stack.len() > MAX_UNDO {
            self.composer.input_undo_stack.remove(0);
        }
    }

    pub(crate) fn undo_input_change(&mut self) {
        if let Some((draft, cursor)) = self.composer.input_undo_stack.pop() {
            self.draft = draft;
            self.draft_cursor = cursor.min(self.draft.len());
            self.clamp_draft_cursor();
            self.clear_draft_selection();
        }
    }

    pub(crate) fn clamp_draft_cursor(&mut self) {
        self.draft_cursor = self.draft_cursor.min(self.draft.len());
        while !self.draft.is_char_boundary(self.draft_cursor) {
            self.draft_cursor -= 1;
        }
    }
}
