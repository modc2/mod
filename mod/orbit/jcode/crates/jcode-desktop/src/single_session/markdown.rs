use super::*;

pub(crate) fn append_chat_message_lines(
    lines: &mut Vec<SingleSessionStyledLine>,
    message: &SingleSessionMessage,
    user_turn: &mut usize,
    is_active_tool: bool,
    active_tool_input: Option<&str>,
    tool_run: Option<&SingleSessionToolRun>,
) {
    match message.role() {
        SingleSessionRole::User => {
            append_user_lines(lines, *user_turn, message.content().trim());
            *user_turn += 1;
        }
        SingleSessionRole::Assistant => append_assistant_lines(lines, message.content().trim()),
        SingleSessionRole::Tool => append_tool_lines(
            lines,
            message.content().trim(),
            is_active_tool,
            active_tool_input,
            tool_run,
        ),
        SingleSessionRole::System | SingleSessionRole::Meta => {
            append_meta_lines(lines, message.content().trim())
        }
    }
}

pub(crate) fn append_user_lines(
    lines: &mut Vec<SingleSessionStyledLine>,
    turn: usize,
    content: &str,
) {
    let mut content_lines = content.lines();
    let Some(first) = content_lines.next() else {
        return;
    };
    lines.push(styled_line(
        format!("{turn}  {}", compact_single_session_visible_line(first)),
        SingleSessionLineStyle::User,
    ));
    for line in content_lines {
        lines.push(styled_line(
            format!("   {}", compact_single_session_visible_line(line)),
            SingleSessionLineStyle::UserContinuation,
        ));
    }
}

pub(crate) fn compact_single_session_visible_line(line: &str) -> String {
    const MAX_VISIBLE_BYTES: usize = 512;

    if line.len() <= MAX_VISIBLE_BYTES {
        return line.to_string();
    }

    let prefix_len = safe_utf8_prefix_len(line, MAX_VISIBLE_BYTES);
    let omitted = line.len().saturating_sub(prefix_len);
    format!("{}… <{} bytes omitted>", &line[..prefix_len], omitted)
}

pub(crate) fn is_user_prompt_line(line: &str) -> bool {
    let Some((number, rest)) = line.split_once("  ") else {
        return false;
    };
    !number.is_empty() && number.chars().all(|ch| ch.is_ascii_digit()) && !rest.trim().is_empty()
}

pub(crate) fn append_assistant_lines(lines: &mut Vec<SingleSessionStyledLine>, content: &str) {
    lines.extend(render_assistant_markdown_lines(content));
}

pub(crate) fn append_streaming_assistant_lines(
    lines: &mut Vec<SingleSessionStyledLine>,
    content: &str,
) {
    lines.extend(render_assistant_markdown_lines(content));
}

pub(crate) fn take_current_inline_spans(
    inline_spans: &mut Vec<SingleSessionInlineSpan>,
    trimmed_len: usize,
) -> Vec<SingleSessionInlineSpan> {
    let mut spans = std::mem::take(inline_spans);
    spans = spans
        .into_iter()
        .filter_map(|span| {
            let start = span.start.min(trimmed_len);
            let end = span.end.min(trimmed_len);
            (start < end).then_some(SingleSessionInlineSpan {
                start,
                end,
                kind: span.kind,
            })
        })
        .collect();
    spans.sort_by_key(|span| (span.start, span.end));
    spans
}

pub(crate) fn safe_utf8_prefix_len(text: &str, desired_len: usize) -> usize {
    let mut len = desired_len.min(text.len());
    while len > 0 && !text.is_char_boundary(len) {
        len -= 1;
    }
    len
}

pub(crate) fn single_session_trimmed_line_end_preserving_inline_code_whitespace(
    text: &str,
    inline_spans: &[SingleSessionInlineSpan],
) -> usize {
    let trimmed_len = text.trim_end().len();
    let inline_code_end = inline_spans
        .iter()
        .filter(|span| span.kind == SingleSessionInlineSpanKind::Code)
        .filter_map(|span| {
            let end = span.end.min(text.len());
            (end > trimmed_len && text.is_char_boundary(end)).then_some(end)
        })
        .max()
        .unwrap_or(trimmed_len);

    trimmed_len.max(inline_code_end)
}

pub(crate) fn render_assistant_markdown_lines(content: &str) -> Vec<SingleSessionStyledLine> {
    let markdown_options = Options::ENABLE_TABLES
        | Options::ENABLE_STRIKETHROUGH
        | Options::ENABLE_TASKLISTS
        | Options::ENABLE_FOOTNOTES
        | Options::ENABLE_MATH
        | Options::ENABLE_GFM
        | Options::ENABLE_DEFINITION_LIST;
    let mut renderer = AssistantMarkdownRenderer::default();

    for event in Parser::new_ext(content, markdown_options) {
        renderer.handle_event(event);
    }

    let mut lines = renderer.finish();
    if lines.is_empty() && !content.trim().is_empty() {
        lines.extend(
            content
                .lines()
                .map(|line| styled_line(line, SingleSessionLineStyle::Assistant)),
        );
    }
    lines
}

#[derive(Default)]
pub(crate) struct AssistantMarkdownRenderer {
    lines: Vec<SingleSessionStyledLine>,
    current: String,
    current_inline_spans: Vec<SingleSessionInlineSpan>,
    active_inline_spans: Vec<AssistantMarkdownActiveInlineSpan>,
    current_style: SingleSessionLineStyle,
    line_style_override: Option<SingleSessionLineStyle>,
    quote_depth: usize,
    list_stack: Vec<AssistantMarkdownList>,
    item_continuation_prefixes: Vec<String>,
    pending_line_prefix: String,
    continuation_prefix: String,
    in_code_block: bool,
    in_footnote_definition: bool,
    table: Option<AssistantMarkdownTable>,
    image_stack: Vec<AssistantMarkdownImage>,
    link_stack: Vec<AssistantMarkdownLink>,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct AssistantMarkdownActiveInlineSpan {
    kind: SingleSessionInlineSpanKind,
    start: usize,
}

#[derive(Clone, Debug)]
pub(crate) struct AssistantMarkdownList {
    next_number: Option<u64>,
}

#[derive(Clone, Debug)]
pub(crate) struct AssistantMarkdownLink {
    dest_url: String,
    start_byte: usize,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct AssistantMarkdownImage {
    dest_url: String,
    alt_text: String,
}

#[derive(Clone, Debug, Default)]
pub(crate) struct AssistantMarkdownTable {
    rows: Vec<Vec<String>>,
    current_row: Vec<String>,
    current_cell: String,
    header_rows: usize,
    alignments: Vec<Alignment>,
}

impl AssistantMarkdownRenderer {
    fn handle_event(&mut self, event: Event<'_>) {
        match event {
            Event::Start(Tag::Heading { level, .. }) => self.start_heading(level),
            Event::End(TagEnd::Heading(_)) => self.end_heading(),
            Event::Start(Tag::Paragraph) => self.start_paragraph(),
            Event::End(TagEnd::Paragraph) => self.end_paragraph(),
            Event::Start(Tag::BlockQuote(kind)) => self.start_block_quote(kind),
            Event::End(TagEnd::BlockQuote(_)) => self.end_block_quote(),
            Event::Start(Tag::List(start)) => self.start_list(start),
            Event::End(TagEnd::List(_)) => self.end_list(),
            Event::Start(Tag::Item) => self.start_list_item(),
            Event::End(TagEnd::Item) => self.end_list_item(),
            Event::Start(Tag::FootnoteDefinition(label)) => {
                self.start_footnote_definition(label.as_ref())
            }
            Event::End(TagEnd::FootnoteDefinition) => self.end_footnote_definition(),
            Event::Start(Tag::DefinitionList) => self.start_definition_list(),
            Event::End(TagEnd::DefinitionList) => self.end_definition_list(),
            Event::Start(Tag::DefinitionListTitle) => self.start_definition_list_title(),
            Event::End(TagEnd::DefinitionListTitle) => self.end_definition_list_title(),
            Event::Start(Tag::DefinitionListDefinition) => self.start_definition_list_definition(),
            Event::End(TagEnd::DefinitionListDefinition) => self.end_definition_list_definition(),
            Event::TaskListMarker(checked) => self.apply_task_marker(checked),
            Event::Start(Tag::CodeBlock(kind)) => self.start_code_block(kind),
            Event::End(TagEnd::CodeBlock) => self.end_code_block(),
            Event::Start(Tag::Table(alignments)) => self.start_table(alignments),
            Event::End(TagEnd::Table) => self.end_table(),
            Event::Start(Tag::TableHead) => self.start_table_head(),
            Event::End(TagEnd::TableHead) => self.end_table_head(),
            Event::Start(Tag::TableRow) => self.start_table_row(),
            Event::End(TagEnd::TableRow) => self.end_table_row(),
            Event::Start(Tag::TableCell) => self.start_table_cell(),
            Event::End(TagEnd::TableCell) => self.end_table_cell(),
            Event::Start(Tag::Link { dest_url, .. }) => self.start_link(dest_url.as_ref()),
            Event::End(TagEnd::Link) => self.end_link(),
            Event::Start(Tag::Image { dest_url, .. }) => self.start_image(dest_url.as_ref()),
            Event::End(TagEnd::Image) => self.end_image(),
            Event::Start(Tag::Emphasis) => {
                self.start_inline_span(SingleSessionInlineSpanKind::Emphasis)
            }
            Event::End(TagEnd::Emphasis) => {
                self.end_inline_span(SingleSessionInlineSpanKind::Emphasis)
            }
            Event::Start(Tag::Strong) => {
                self.start_inline_span(SingleSessionInlineSpanKind::Strong)
            }
            Event::End(TagEnd::Strong) => self.end_inline_span(SingleSessionInlineSpanKind::Strong),
            Event::Start(Tag::Strikethrough) => {
                self.start_inline_span(SingleSessionInlineSpanKind::Strike)
            }
            Event::End(TagEnd::Strikethrough) => {
                self.end_inline_span(SingleSessionInlineSpanKind::Strike)
            }
            Event::Text(text) => self.push_text(text.as_ref()),
            Event::Code(code) => self.push_inline_code(code.as_ref()),
            Event::InlineMath(math) => self.push_inline_math(math.as_ref()),
            Event::DisplayMath(math) => self.push_display_math(math.as_ref()),
            Event::SoftBreak => self.soft_break(),
            Event::HardBreak => self.hard_break(),
            Event::Rule => self.rule(),
            Event::Html(html) => self.push_html_block(html.as_ref()),
            Event::InlineHtml(html) => self.push_inline_code(html.as_ref()),
            Event::FootnoteReference(name) => {
                self.push_text("[^");
                self.push_text(name.as_ref());
                self.push_text("]");
            }
            _ => {}
        }
    }

    fn finish(mut self) -> Vec<SingleSessionStyledLine> {
        self.flush_current_line();
        if self
            .lines
            .last()
            .is_some_and(|line| line.style == SingleSessionLineStyle::Blank)
        {
            self.lines.pop();
        }
        self.lines
    }

    fn start_heading(&mut self, level: HeadingLevel) {
        self.flush_current_line();
        self.ensure_block_gap();
        self.current_style = SingleSessionLineStyle::AssistantHeading;
        self.pending_line_prefix = heading_prefix(level).to_string();
    }

    fn end_heading(&mut self) {
        self.flush_current_line_as(SingleSessionLineStyle::AssistantHeading);
        self.current_style = self.prose_style();
        self.pending_line_prefix.clear();
    }

    fn start_paragraph(&mut self) {
        if self.list_stack.is_empty() && self.quote_depth == 0 {
            self.ensure_block_gap();
        }
        self.current_style = self.prose_style();
    }

    fn end_paragraph(&mut self) {
        self.flush_current_line();
        if !self.item_continuation_prefixes.is_empty() {
            self.pending_line_prefix = self.continuation_prefix.clone();
        }
    }

    fn start_block_quote(&mut self, kind: Option<BlockQuoteKind>) {
        self.flush_current_line();
        self.ensure_block_gap();
        let parent_quote_prefix = self.quote_prefix();
        self.quote_depth += 1;
        self.current_style = SingleSessionLineStyle::AssistantQuote;
        if let Some(kind) = kind {
            self.pending_line_prefix =
                format!("{parent_quote_prefix}{} │ ", block_quote_kind_label(kind));
        }
    }

    fn end_block_quote(&mut self) {
        self.flush_current_line_as(SingleSessionLineStyle::AssistantQuote);
        self.quote_depth = self.quote_depth.saturating_sub(1);
        self.current_style = self.prose_style();
        self.pending_line_prefix.clear();
        self.continuation_prefix.clear();
    }

    fn start_list(&mut self, start: Option<u64>) {
        self.flush_current_line();
        if self.list_stack.is_empty() && self.quote_depth == 0 {
            self.ensure_block_gap();
        }
        self.list_stack
            .push(AssistantMarkdownList { next_number: start });
    }

    fn end_list(&mut self) {
        self.flush_current_line();
        self.list_stack.pop();
        if self.list_stack.is_empty() {
            self.pending_line_prefix.clear();
            self.continuation_prefix.clear();
            self.item_continuation_prefixes.clear();
        }
    }

    fn start_list_item(&mut self) {
        self.flush_current_line();
        let (prefix, continuation) = self.list_item_prefix(false);
        self.pending_line_prefix = prefix;
        self.continuation_prefix = continuation.clone();
        self.item_continuation_prefixes.push(continuation);
        self.current_style = self.prose_style();
    }

    fn end_list_item(&mut self) {
        self.flush_current_line();
        self.item_continuation_prefixes.pop();
        self.continuation_prefix = self
            .item_continuation_prefixes
            .last()
            .cloned()
            .unwrap_or_default();
        self.pending_line_prefix.clear();
    }

    fn apply_task_marker(&mut self, checked: bool) {
        let (prefix, continuation) = self.task_item_prefix(checked);
        if self.current.is_empty() {
            self.pending_line_prefix = prefix;
            self.continuation_prefix = continuation.clone();
            if let Some(last) = self.item_continuation_prefixes.last_mut() {
                *last = continuation;
            }
        } else {
            self.current.push_str(if checked { "✓ " } else { "☐ " });
        }
    }

    fn start_footnote_definition(&mut self, label: &str) {
        self.flush_current_line();
        self.ensure_block_gap();
        self.in_footnote_definition = true;
        self.current_style = SingleSessionLineStyle::Meta;
        self.pending_line_prefix = format!("[^{label}]: ");
    }

    fn end_footnote_definition(&mut self) {
        self.flush_current_line_as(SingleSessionLineStyle::Meta);
        self.in_footnote_definition = false;
        self.current_style = self.prose_style();
        self.pending_line_prefix.clear();
    }

    fn start_definition_list(&mut self) {
        self.flush_current_line();
        self.ensure_block_gap();
    }

    fn end_definition_list(&mut self) {
        self.flush_current_line();
        self.pending_line_prefix.clear();
        self.current_style = self.prose_style();
    }

    fn start_definition_list_title(&mut self) {
        self.flush_current_line();
        self.current_style = SingleSessionLineStyle::AssistantHeading;
    }

    fn end_definition_list_title(&mut self) {
        self.flush_current_line_as(SingleSessionLineStyle::AssistantHeading);
        self.current_style = self.prose_style();
    }

    fn start_definition_list_definition(&mut self) {
        self.flush_current_line();
        self.current_style = self.prose_style();
        self.pending_line_prefix = "  : ".to_string();
    }

    fn end_definition_list_definition(&mut self) {
        self.flush_current_line();
        self.pending_line_prefix.clear();
    }

    fn start_code_block(&mut self, kind: CodeBlockKind<'_>) {
        self.flush_current_line();
        self.ensure_block_gap();
        self.in_code_block = true;
        if let CodeBlockKind::Fenced(language) = kind {
            let language = language.as_ref().trim();
            if !language.is_empty() {
                self.lines.push(styled_line(
                    format!("  {language}"),
                    SingleSessionLineStyle::CodeHeader,
                ));
            }
        }
    }

    fn end_code_block(&mut self) {
        self.in_code_block = false;
    }

    fn start_table(&mut self, alignments: Vec<Alignment>) {
        self.flush_current_line();
        self.ensure_block_gap();
        self.table = Some(AssistantMarkdownTable {
            alignments,
            ..AssistantMarkdownTable::default()
        });
    }

    fn end_table(&mut self) {
        if let Some(table) = self.table.take() {
            self.render_table(table);
        }
    }

    fn start_table_head(&mut self) {}

    fn end_table_head(&mut self) {
        if let Some(table) = &mut self.table {
            if !table.current_cell.trim().is_empty() {
                table.finish_cell();
            }
            table.finish_row();
            table.header_rows = table.rows.len();
        }
    }

    fn start_table_row(&mut self) {
        if let Some(table) = &mut self.table {
            table.current_row.clear();
        }
    }

    fn end_table_row(&mut self) {
        if let Some(table) = &mut self.table {
            if !table.current_cell.trim().is_empty() {
                table.finish_cell();
            }
            table.finish_row();
        }
    }

    fn start_table_cell(&mut self) {
        if let Some(table) = &mut self.table {
            table.current_cell.clear();
        }
    }

    fn end_table_cell(&mut self) {
        if let Some(table) = &mut self.table {
            table.finish_cell();
        }
    }

    fn start_link(&mut self, dest_url: &str) {
        self.begin_line_if_needed();
        self.link_stack.push(AssistantMarkdownLink {
            dest_url: dest_url.to_string(),
            start_byte: self.current.len(),
        });
    }

    fn end_link(&mut self) {
        let Some(link) = self.link_stack.pop() else {
            return;
        };
        if link.dest_url.is_empty() {
            return;
        }
        self.begin_line_if_needed();
        let label = self
            .current
            .get(link.start_byte..)
            .unwrap_or_default()
            .trim();
        if !label.contains(&link.dest_url) {
            self.current.push_str(" ↗ ");
            self.current.push_str(&link.dest_url);
        }
        if self.current_style == SingleSessionLineStyle::Assistant {
            self.line_style_override = Some(SingleSessionLineStyle::AssistantLink);
        }
    }

    fn start_image(&mut self, dest_url: &str) {
        self.image_stack.push(AssistantMarkdownImage {
            dest_url: dest_url.to_string(),
            alt_text: String::new(),
        });
    }

    fn end_image(&mut self) {
        let Some(image) = self.image_stack.pop() else {
            return;
        };
        self.begin_line_if_needed();
        let alt = image.alt_text.trim();
        if alt.is_empty() {
            self.current.push_str("🖼 image");
        } else {
            self.current.push_str("🖼 ");
            self.current.push_str(alt);
        }
        if !image.dest_url.is_empty() {
            self.current.push_str(" ↗ ");
            self.current.push_str(&image.dest_url);
        }
        if self.current_style == SingleSessionLineStyle::Assistant {
            self.line_style_override = Some(SingleSessionLineStyle::AssistantMedia);
        }
    }

    fn push_text(&mut self, text: &str) {
        if let Some(image) = self.image_stack.last_mut() {
            image.alt_text.push_str(text);
            return;
        }
        if let Some(table) = &mut self.table {
            table.push_text(text);
            return;
        }
        if self.in_code_block {
            self.push_code_text(text);
            return;
        }
        self.begin_line_if_needed();
        self.current.push_str(&text.replace('\n', " "));
    }

    fn push_inline_code(&mut self, code: &str) {
        if let Some(image) = self.image_stack.last_mut() {
            image.alt_text.push_str(code);
            return;
        }
        if let Some(table) = &mut self.table {
            table.push_text(code);
            return;
        }
        self.begin_line_if_needed();
        let start = self.current.len();
        self.current.push_str(code);
        self.push_current_inline_span(start, self.current.len(), SingleSessionInlineSpanKind::Code);
    }

    fn push_inline_math(&mut self, math: &str) {
        if let Some(image) = self.image_stack.last_mut() {
            image.alt_text.push_str(math);
            return;
        }
        if let Some(table) = &mut self.table {
            table.push_text(math);
            return;
        }
        self.begin_line_if_needed();
        let start = self.current.len();
        self.current.push_str(math);
        self.push_current_inline_span(start, self.current.len(), SingleSessionInlineSpanKind::Math);
    }

    fn start_inline_span(&mut self, kind: SingleSessionInlineSpanKind) {
        if self.image_stack.last_mut().is_some() || self.table.is_some() {
            return;
        }
        self.begin_line_if_needed();
        self.active_inline_spans
            .push(AssistantMarkdownActiveInlineSpan {
                kind,
                start: self.current.len(),
            });
    }

    fn end_inline_span(&mut self, kind: SingleSessionInlineSpanKind) {
        if self.image_stack.last_mut().is_some() || self.table.is_some() {
            return;
        }
        let Some(index) = self
            .active_inline_spans
            .iter()
            .rposition(|span| span.kind == kind)
        else {
            return;
        };
        let active = self.active_inline_spans.remove(index);
        self.push_current_inline_span(active.start, self.current.len(), kind);
    }

    fn push_current_inline_span(
        &mut self,
        start: usize,
        end: usize,
        kind: SingleSessionInlineSpanKind,
    ) {
        if start < end {
            self.current_inline_spans
                .push(SingleSessionInlineSpan { start, end, kind });
        }
    }

    fn push_display_math(&mut self, math: &str) {
        if let Some(image) = self.image_stack.last_mut() {
            image.alt_text.push_str("$$");
            image.alt_text.push_str(math);
            image.alt_text.push_str("$$");
            return;
        }
        if let Some(table) = &mut self.table {
            table.push_text("$$ ");
            table.push_text(math.trim());
            table.push_text(" $$");
            return;
        }

        self.flush_current_line();
        self.ensure_block_gap();
        self.lines
            .push(styled_line("  $$", SingleSessionLineStyle::Code));
        for line in math.trim_matches('\n').lines() {
            self.lines.push(styled_line(
                format!("  {line}"),
                SingleSessionLineStyle::Code,
            ));
        }
        self.lines
            .push(styled_line("  $$", SingleSessionLineStyle::Code));
    }

    fn push_html_block(&mut self, html: &str) {
        if let Some(image) = self.image_stack.last_mut() {
            image.alt_text.push_str(html.trim());
            return;
        }
        if let Some(table) = &mut self.table {
            table.push_text("html ");
            table.push_text(html.trim());
            return;
        }
        if self.in_code_block {
            self.push_code_text(html);
            return;
        }

        self.flush_current_line();
        self.ensure_block_gap();
        for line in html.trim_matches('\n').lines() {
            let trimmed = line.trim();
            if trimmed.is_empty() {
                continue;
            }
            self.lines.push(styled_line(
                format!("html │ {trimmed}"),
                SingleSessionLineStyle::Meta,
            ));
        }
    }

    fn soft_break(&mut self) {
        if let Some(table) = &mut self.table {
            table.push_space();
            return;
        }
        if self.in_code_block {
            self.lines
                .push(styled_line("  ", SingleSessionLineStyle::Code));
            return;
        }
        self.push_space();
    }

    fn hard_break(&mut self) {
        if let Some(table) = &mut self.table {
            table.push_space();
            return;
        }
        self.flush_current_line();
        if !self.continuation_prefix.is_empty() {
            self.pending_line_prefix = self.continuation_prefix.clone();
        } else if self.quote_depth > 0 {
            self.pending_line_prefix = self.quote_prefix();
        }
    }

    fn rule(&mut self) {
        self.flush_current_line();
        self.ensure_block_gap();
        self.lines
            .push(styled_line("────────────", SingleSessionLineStyle::Meta));
    }

    fn begin_line_if_needed(&mut self) {
        if !self.current.is_empty() {
            return;
        }
        if !self.pending_line_prefix.is_empty() {
            self.current.push_str(&self.pending_line_prefix);
            self.pending_line_prefix.clear();
            self.reset_active_inline_span_starts();
            return;
        }
        if self.quote_depth > 0 {
            self.current.push_str(&self.quote_prefix());
            self.reset_active_inline_span_starts();
        }
    }

    fn reset_active_inline_span_starts(&mut self) {
        let start = self.current.len();
        for span in &mut self.active_inline_spans {
            span.start = start;
        }
    }

    fn push_space(&mut self) {
        self.begin_line_if_needed();
        if !self.current.chars().last().is_some_and(char::is_whitespace) {
            self.current.push(' ');
        }
    }

    fn push_code_text(&mut self, text: &str) {
        if text.is_empty() {
            self.lines
                .push(styled_line("  ", SingleSessionLineStyle::Code));
            return;
        }
        for line in text.lines() {
            self.lines.push(styled_line(
                format!("  {line}"),
                SingleSessionLineStyle::Code,
            ));
        }
    }

    fn flush_current_line(&mut self) {
        let style = self
            .line_style_override
            .take()
            .unwrap_or(self.current_style);
        self.flush_current_line_as(style);
    }

    fn flush_current_line_as(&mut self, style: SingleSessionLineStyle) {
        let trimmed_len = single_session_trimmed_line_end_preserving_inline_code_whitespace(
            &self.current,
            &self.current_inline_spans,
        );
        if trimmed_len > 0 {
            let safe_trimmed_len = safe_utf8_prefix_len(&self.current, trimmed_len);
            let trimmed = &self.current[..safe_trimmed_len];
            let inline_spans =
                take_current_inline_spans(&mut self.current_inline_spans, safe_trimmed_len);
            self.lines.push(SingleSessionStyledLine::with_inline_spans(
                trimmed,
                style,
                inline_spans,
            ));
        } else {
            self.current_inline_spans.clear();
        }
        self.current.clear();
        self.active_inline_spans.clear();
        self.line_style_override = None;
    }

    fn ensure_block_gap(&mut self) {
        if self
            .lines
            .last()
            .is_some_and(|line| line.style != SingleSessionLineStyle::Blank)
        {
            self.lines.push(blank_styled_line());
        }
    }

    fn prose_style(&self) -> SingleSessionLineStyle {
        if self.in_footnote_definition {
            SingleSessionLineStyle::Meta
        } else if self.quote_depth > 0 {
            SingleSessionLineStyle::AssistantQuote
        } else {
            SingleSessionLineStyle::Assistant
        }
    }

    fn quote_prefix(&self) -> String {
        "│ ".repeat(self.quote_depth)
    }

    fn list_item_prefix(&mut self, task: bool) -> (String, String) {
        let quote_prefix = self.quote_prefix();
        let depth = self.list_stack.len().saturating_sub(1);
        let indent = "  ".repeat(depth);
        let marker = if task {
            "☐ ".to_string()
        } else if let Some(list) = self.list_stack.last_mut() {
            if let Some(next_number) = &mut list.next_number {
                let marker = format!("{next_number}. ");
                *next_number += 1;
                marker
            } else {
                bullet_for_depth(depth).to_string()
            }
        } else {
            "• ".to_string()
        };
        let continuation = format!(
            "{quote_prefix}{indent}{}",
            " ".repeat(marker.chars().count())
        );
        (format!("{quote_prefix}{indent}{marker}"), continuation)
    }

    fn task_item_prefix(&self, checked: bool) -> (String, String) {
        let quote_prefix = self.quote_prefix();
        let depth = self.list_stack.len().saturating_sub(1);
        let indent = "  ".repeat(depth);
        let marker = if checked { "✓ " } else { "☐ " };
        let continuation = format!(
            "{quote_prefix}{indent}{}",
            " ".repeat(marker.chars().count())
        );
        (format!("{quote_prefix}{indent}{marker}"), continuation)
    }

    fn render_table(&mut self, table: AssistantMarkdownTable) {
        let header_rows = table.header_rows;
        let alignments = table.alignments.clone();
        let rows = table.non_empty_rows();
        if rows.is_empty() {
            return;
        }
        let column_count = rows.iter().map(Vec::len).max().unwrap_or(0);
        if column_count == 0 {
            return;
        }
        let mut widths = vec![0usize; column_count];
        for row in &rows {
            for (column, cell) in row.iter().enumerate() {
                widths[column] = widths[column].max(cell.chars().count());
            }
        }
        for (row_index, row) in rows.iter().enumerate() {
            self.lines.push(styled_line(
                format_table_row(row, &widths, &alignments),
                SingleSessionLineStyle::AssistantTable,
            ));
            if header_rows > 0 && row_index + 1 == header_rows.min(rows.len()) {
                self.lines.push(styled_line(
                    format_table_separator(&widths, &alignments),
                    SingleSessionLineStyle::AssistantTable,
                ));
            }
        }
    }
}

impl AssistantMarkdownTable {
    fn push_text(&mut self, text: &str) {
        self.current_cell.push_str(&text.replace('\n', " "));
    }

    fn push_space(&mut self) {
        if !self
            .current_cell
            .chars()
            .last()
            .is_some_and(char::is_whitespace)
        {
            self.current_cell.push(' ');
        }
    }

    fn finish_cell(&mut self) {
        self.current_row.push(self.current_cell.trim().to_string());
        self.current_cell.clear();
    }

    fn finish_row(&mut self) {
        if !self.current_row.is_empty() {
            self.rows.push(std::mem::take(&mut self.current_row));
        }
    }

    fn non_empty_rows(mut self) -> Vec<Vec<String>> {
        if !self.current_cell.trim().is_empty() {
            self.finish_cell();
        }
        self.finish_row();
        self.rows
            .into_iter()
            .filter(|row| row.iter().any(|cell| !cell.is_empty()))
            .collect()
    }
}

pub(crate) fn heading_prefix(level: HeadingLevel) -> &'static str {
    match level {
        HeadingLevel::H1 | HeadingLevel::H2 => "",
        HeadingLevel::H3 => "› ",
        _ => "· ",
    }
}

pub(crate) fn block_quote_kind_label(kind: BlockQuoteKind) -> &'static str {
    match kind {
        BlockQuoteKind::Note => "NOTE",
        BlockQuoteKind::Tip => "TIP",
        BlockQuoteKind::Important => "IMPORTANT",
        BlockQuoteKind::Warning => "WARNING",
        BlockQuoteKind::Caution => "CAUTION",
    }
}

pub(crate) fn bullet_for_depth(depth: usize) -> &'static str {
    match depth % 3 {
        0 => "• ",
        1 => "◦ ",
        _ => "▪ ",
    }
}

pub(crate) fn format_table_row(
    row: &[String],
    widths: &[usize],
    alignments: &[Alignment],
) -> String {
    let mut rendered = String::new();
    for (column, width) in widths.iter().enumerate() {
        if column > 0 {
            rendered.push_str(" │ ");
        }
        let cell = row.get(column).map(String::as_str).unwrap_or_default();
        let alignment = alignments.get(column).copied().unwrap_or(Alignment::None);
        rendered.push_str(&format_table_cell(cell, *width, alignment));
    }
    rendered.trim_end().to_string()
}

pub(crate) fn format_table_cell(cell: &str, width: usize, alignment: Alignment) -> String {
    let padding = width.saturating_sub(cell.chars().count());
    match alignment {
        Alignment::Right => format!("{}{cell}", " ".repeat(padding)),
        Alignment::Center => {
            let left = padding / 2;
            let right = padding.saturating_sub(left);
            format!("{}{cell}{}", " ".repeat(left), " ".repeat(right))
        }
        Alignment::Left | Alignment::None => format!("{cell}{}", " ".repeat(padding)),
    }
}

pub(crate) fn format_table_separator(widths: &[usize], alignments: &[Alignment]) -> String {
    let mut rendered = String::new();
    for (column, width) in widths.iter().enumerate() {
        if column > 0 {
            rendered.push_str("─┼─");
        }
        let width = (*width).max(1);
        match alignments.get(column).copied().unwrap_or(Alignment::None) {
            Alignment::Left => {
                rendered.push('╾');
                rendered.push_str(&"─".repeat(width.saturating_sub(1)));
            }
            Alignment::Right => {
                rendered.push_str(&"─".repeat(width.saturating_sub(1)));
                rendered.push('╼');
            }
            Alignment::Center => {
                rendered.push('╾');
                if width > 1 {
                    rendered.push_str(&"─".repeat(width.saturating_sub(2)));
                    rendered.push('╼');
                }
            }
            Alignment::None => rendered.push_str(&"─".repeat(width)),
        }
    }
    rendered
}
