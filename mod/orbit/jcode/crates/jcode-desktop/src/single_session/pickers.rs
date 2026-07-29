use super::*;

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub(crate) struct StdinResponseState {
    pub(crate) request_id: String,
    pub(crate) prompt: String,
    pub(crate) is_password: bool,
    pub(crate) tool_call_id: String,
    pub(crate) input: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub(crate) struct ModelPickerState {
    pub(crate) open: bool,
    pub(crate) loading: bool,
    pub(crate) preview: bool,
    pub(crate) filter: String,
    pub(crate) selected: usize,
    pub(crate) column: usize,
    pub(crate) current_model: Option<String>,
    pub(crate) provider_name: Option<String>,
    pub(crate) choices: Vec<DesktopModelChoice>,
    pub(crate) search_texts: Vec<String>,
    pub(crate) visible_indices: Vec<usize>,
    pub(crate) error: Option<String>,
}

impl ModelPickerState {
    pub(crate) fn open_loading(&mut self) {
        self.open = true;
        self.loading = true;
        self.preview = false;
        self.error = None;
        self.refresh_visible_indices();
        self.selected = self.current_choice_index().unwrap_or(0);
        self.column = 0;
    }

    pub(crate) fn open_preview_loading(&mut self, filter: String) {
        self.open = true;
        self.loading = true;
        self.preview = true;
        self.filter = filter;
        self.error = None;
        self.refresh_visible_indices();
        self.selected = self.current_visible_position().unwrap_or(0);
        self.column = 0;
    }

    pub(crate) fn close(&mut self) {
        self.open = false;
        self.loading = false;
        self.preview = false;
        self.error = None;
        self.column = 0;
    }

    pub(crate) fn apply_catalog(
        &mut self,
        current_model: Option<String>,
        provider_name: Option<String>,
        choices: Vec<DesktopModelChoice>,
    ) {
        if current_model.is_some() {
            self.current_model = current_model;
        }
        if provider_name.is_some() {
            self.provider_name = provider_name;
        }
        if !choices.is_empty() {
            self.choices = dedupe_model_choices(choices);
            self.rebuild_search_texts();
        }
        self.loading = false;
        self.error = None;
        self.ensure_current_choice_present();
        self.refresh_visible_indices();
        self.selected = self.current_visible_position().unwrap_or(0);
        self.clamp_selection();
        self.column = self.column.min(2);
    }

    pub(crate) fn apply_error(&mut self, error: String) {
        self.open = true;
        self.loading = false;
        self.error = Some(error);
    }

    pub(crate) fn apply_model_change(&mut self, model: String, provider_name: Option<String>) {
        self.current_model = Some(model);
        if provider_name.is_some() {
            self.provider_name = provider_name;
        }
        self.ensure_current_choice_present();
        self.refresh_visible_indices();
        self.selected = self.current_visible_position().unwrap_or(self.selected);
        self.clamp_selection();
    }

    pub(crate) fn selected_model(&self) -> Option<String> {
        let visible = self.filtered_indices();
        visible
            .get(self.selected)
            .and_then(|index| self.choices.get(*index))
            .map(desktop_model_choice_switch_spec)
    }

    pub(crate) fn move_selection(&mut self, delta: i32) {
        let visible_len = self.filtered_indices().len();
        if visible_len == 0 {
            self.selected = 0;
            return;
        }
        if delta < 0 {
            self.selected = self.selected.saturating_sub(delta.unsigned_abs() as usize);
        } else {
            self.selected = (self.selected + delta as usize).min(visible_len - 1);
        }
    }

    pub(crate) fn select_first(&mut self) {
        self.selected = 0;
    }

    pub(crate) fn select_last(&mut self) {
        self.selected = self.filtered_indices().len().saturating_sub(1);
    }

    pub(crate) fn push_filter_text(&mut self, text: &str) {
        self.filter.push_str(text);
        self.refresh_visible_indices();
        self.selected = 0;
        self.column = 0;
    }

    pub(crate) fn pop_filter_char(&mut self) {
        self.filter.pop();
        self.refresh_visible_indices();
        self.selected = 0;
        self.column = 0;
    }

    pub(crate) fn set_filter(&mut self, filter: String) {
        if self.filter != filter {
            self.filter = filter;
            self.refresh_visible_indices();
            self.selected = 0;
            self.column = 0;
        }
        self.clamp_selection();
    }

    pub(crate) fn filtered_indices(&self) -> &[usize] {
        &self.visible_indices
    }

    pub(crate) fn refresh_visible_indices(&mut self) {
        self.ensure_search_texts_current();
        let query = self.filter.trim().to_lowercase();
        if query.is_empty() {
            self.visible_indices = (0..self.choices.len()).collect();
            return;
        }

        let mut substring_matches = self
            .search_texts
            .iter()
            .enumerate()
            .filter_map(|(index, search_text)| {
                search_text.contains(&query).then_some((
                    self.choices[index].model.eq_ignore_ascii_case(&query),
                    search_text.len(),
                    index,
                ))
            })
            .collect::<Vec<_>>();
        if !substring_matches.is_empty() {
            substring_matches.sort_by(|a, b| {
                b.0.cmp(&a.0)
                    .then_with(|| a.1.cmp(&b.1))
                    .then_with(|| a.2.cmp(&b.2))
            });
            self.visible_indices = substring_matches
                .into_iter()
                .map(|(_, _, index)| index)
                .collect();
            return;
        }

        let mut fuzzy_matches = Vec::new();
        for (index, search_text) in self.search_texts.iter().enumerate() {
            if let Some(score) = model_picker_fuzzy_score(&query, search_text) {
                fuzzy_matches.push((score, search_text.len(), index));
            }
        }
        fuzzy_matches.sort_by(|a, b| {
            b.0.cmp(&a.0)
                .then_with(|| a.1.cmp(&b.1))
                .then_with(|| a.2.cmp(&b.2))
        });
        self.visible_indices = fuzzy_matches
            .into_iter()
            .map(|(_, _, index)| index)
            .collect();
    }

    pub(crate) fn visible_row_window(&self, limit: usize) -> (usize, Vec<usize>) {
        let visible = self.filtered_indices();
        if visible.is_empty() || limit == 0 {
            return (0, Vec::new());
        }
        let max_start = visible.len().saturating_sub(limit);
        let selected = self.selected.min(visible.len() - 1);
        let start = selected.saturating_sub(limit / 2).min(max_start);
        let end = (start + limit).min(visible.len());
        (start, visible[start..end].to_vec())
    }

    pub(crate) fn current_choice_index(&self) -> Option<usize> {
        let current = self.current_model.as_deref()?;
        self.choices
            .iter()
            .position(|choice| choice.model == current)
    }

    pub(crate) fn current_visible_position(&self) -> Option<usize> {
        let current = self.current_choice_index()?;
        self.filtered_indices()
            .iter()
            .position(|index| *index == current)
    }

    pub(crate) fn clamp_selection(&mut self) {
        let visible_len = self.filtered_indices().len();
        if visible_len == 0 {
            self.selected = 0;
        } else if self.selected >= visible_len {
            self.selected = visible_len - 1;
        }
    }

    pub(crate) fn rebuild_search_texts(&mut self) {
        self.search_texts = self.choices.iter().map(model_choice_search_text).collect();
    }

    pub(crate) fn ensure_search_texts_current(&mut self) {
        if self.search_texts.len() != self.choices.len() {
            self.rebuild_search_texts();
        }
    }

    pub(crate) fn ensure_current_choice_present(&mut self) {
        let Some(current_model) = self.current_model.clone() else {
            return;
        };
        if self
            .choices
            .iter()
            .any(|choice| choice.model == current_model)
        {
            return;
        }
        let choice = DesktopModelChoice {
            model: current_model,
            provider: self.provider_name.clone(),
            api_method: Some("current".to_string()),
            detail: Some("current model".to_string()),
            available: true,
        };
        let search_text = model_choice_search_text(&choice);
        self.choices.insert(0, choice);
        self.search_texts.insert(0, search_text);
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Default)]
pub(crate) struct SessionSwitcherState {
    pub(crate) open: bool,
    pub(crate) loading: bool,
    pub(crate) preview: bool,
    pub(crate) filter: String,
    pub(crate) selected: usize,
    pub(crate) sessions: Vec<workspace::SessionCard>,
    pub(crate) visible_indices: Vec<usize>,
    pub(crate) preview_scroll: usize,
    pub(crate) focus: SessionSwitcherPane,
}

#[derive(Clone, Copy, Debug, Default, Eq, Hash, PartialEq)]
pub(crate) enum SessionSwitcherPane {
    #[default]
    Sessions,
    Preview,
}

impl SessionSwitcherState {
    pub(crate) fn open_loading(&mut self, current_session_id: Option<&str>) {
        self.open_loading_with_filter(current_session_id, String::new(), false);
    }

    pub(crate) fn open_preview_loading(
        &mut self,
        current_session_id: Option<&str>,
        filter: String,
    ) {
        self.open_loading_with_filter(current_session_id, filter, true);
    }

    pub(crate) fn refresh_loading(&mut self, current_session_id: Option<&str>) {
        let filter = self.filter.clone();
        let preview = self.preview;
        self.open_loading_with_filter(current_session_id, filter, preview);
    }

    pub(crate) fn open_loading_with_filter(
        &mut self,
        current_session_id: Option<&str>,
        filter: String,
        preview: bool,
    ) {
        self.open = true;
        self.loading = true;
        self.preview = preview;
        self.filter = filter;
        self.refresh_visible_indices();
        self.focus = SessionSwitcherPane::Sessions;
        self.preview_scroll = 0;
        self.selected = self
            .current_visible_position(current_session_id)
            .unwrap_or(self.selected);
        self.clamp_selection();
    }

    pub(crate) fn close(&mut self) {
        self.open = false;
        self.loading = false;
        self.preview = false;
    }

    pub(crate) fn apply_sessions(
        &mut self,
        sessions: Vec<workspace::SessionCard>,
        current_session_id: Option<&str>,
    ) {
        self.sessions = sessions;
        self.refresh_visible_indices();
        self.loading = false;
        self.selected = self
            .current_visible_position(current_session_id)
            .unwrap_or(0);
        self.preview_scroll = 0;
        self.clamp_selection();
    }

    pub(crate) fn selected_session(&self) -> Option<workspace::SessionCard> {
        self.selected_session_ref().cloned()
    }

    pub(crate) fn selected_session_ref(&self) -> Option<&workspace::SessionCard> {
        let visible = self.filtered_indices();
        visible
            .get(self.selected)
            .and_then(|index| self.sessions.get(*index))
    }

    pub(crate) fn move_selection(&mut self, delta: i32) {
        let visible_len = self.filtered_indices().len();
        if visible_len == 0 {
            self.selected = 0;
            return;
        }
        if delta < 0 {
            self.selected = self.selected.saturating_sub(delta.unsigned_abs() as usize);
        } else {
            self.selected = (self.selected + delta as usize).min(visible_len - 1);
        }
        self.preview_scroll = 0;
    }

    pub(crate) fn select_first(&mut self) {
        self.selected = 0;
        self.preview_scroll = 0;
    }

    pub(crate) fn select_last(&mut self) {
        self.selected = self.filtered_indices().len().saturating_sub(1);
        self.preview_scroll = 0;
    }

    pub(crate) fn push_filter_text(&mut self, text: &str) {
        self.filter.push_str(text);
        self.refresh_visible_indices();
        self.selected = 0;
        self.preview_scroll = 0;
    }

    pub(crate) fn pop_filter_char(&mut self) {
        self.filter.pop();
        self.refresh_visible_indices();
        self.selected = 0;
        self.preview_scroll = 0;
    }

    pub(crate) fn set_filter(&mut self, filter: String) {
        if self.filter != filter {
            self.filter = filter;
            self.refresh_visible_indices();
            self.selected = 0;
            self.preview_scroll = 0;
        }
        self.clamp_selection();
    }

    pub(crate) fn filtered_indices(&self) -> &[usize] {
        &self.visible_indices
    }

    pub(crate) fn refresh_visible_indices(&mut self) {
        let query = self.filter.trim().to_lowercase();
        if query.is_empty() {
            self.visible_indices = (0..self.sessions.len()).collect();
            return;
        }

        let mut substring_matches = Vec::new();
        let mut fuzzy_matches = Vec::new();
        for (index, session) in self.sessions.iter().enumerate() {
            let search_text = session_card_search_text(session);
            if search_text.contains(&query) {
                substring_matches.push(index);
            } else if let Some(score) = session_switcher_fuzzy_score(&query, &search_text) {
                fuzzy_matches.push((score, search_text.len(), index));
            }
        }
        fuzzy_matches.sort_by(|a, b| {
            b.0.cmp(&a.0)
                .then_with(|| a.1.cmp(&b.1))
                .then_with(|| a.2.cmp(&b.2))
        });

        self.visible_indices = substring_matches
            .into_iter()
            .chain(fuzzy_matches.into_iter().map(|(_, _, index)| index))
            .collect()
    }

    pub(crate) fn current_visible_position(
        &self,
        current_session_id: Option<&str>,
    ) -> Option<usize> {
        let current_session_id = current_session_id?;
        self.filtered_indices().iter().position(|index| {
            self.sessions
                .get(*index)
                .is_some_and(|session| session.session_id == current_session_id)
        })
    }

    pub(crate) fn clamp_selection(&mut self) {
        let visible_len = self.filtered_indices().len();
        if visible_len == 0 {
            self.selected = 0;
        } else if self.selected >= visible_len {
            self.selected = visible_len - 1;
        }
    }

    pub(crate) fn toggle_focus(&mut self) {
        self.focus = match self.focus {
            SessionSwitcherPane::Sessions => SessionSwitcherPane::Preview,
            SessionSwitcherPane::Preview => SessionSwitcherPane::Sessions,
        };
    }

    pub(crate) fn focus_sessions(&mut self) {
        self.focus = SessionSwitcherPane::Sessions;
    }

    pub(crate) fn focus_preview(&mut self) {
        self.focus = SessionSwitcherPane::Preview;
    }

    pub(crate) fn scroll_preview(&mut self, delta: i32) {
        if delta < 0 {
            self.preview_scroll = self
                .preview_scroll
                .saturating_sub(delta.unsigned_abs() as usize);
        } else {
            self.preview_scroll = self.preview_scroll.saturating_add(delta as usize);
        }
        let max_scroll = self.preview_line_count().saturating_sub(1);
        self.preview_scroll = self.preview_scroll.min(max_scroll);
    }

    pub(crate) fn preview_line_count(&self) -> usize {
        self.selected_session_ref()
            .map(session_switcher_preview_line_count_for_session)
            .unwrap_or(0)
    }

    pub(crate) fn visible_row_window(&self, limit: usize) -> (usize, Vec<usize>) {
        let visible = self.filtered_indices();
        if visible.is_empty() || limit == 0 {
            return (0, Vec::new());
        }
        let max_start = visible.len().saturating_sub(limit);
        let selected = self.selected.min(visible.len() - 1);
        let start = selected.saturating_sub(limit / 2).min(max_start);
        let end = (start + limit).min(visible.len());
        (start, visible[start..end].to_vec())
    }
}
