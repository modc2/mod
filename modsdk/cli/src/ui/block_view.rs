use ratatui::{
    layout::Rect,
    style::{Color, Style},
    text::{Line, Span},
    widgets::{Block, Borders, Paragraph},
    Frame,
};

#[derive(Debug)]
pub struct BlockView {
    pub block_number: u64,
    pub hash: String,
    pub timestamp: String,
    pub parent_hash: String,
    pub state_root: String,
    pub extrinsics_count: usize,
}

impl BlockView {
    pub fn new(block_number: u64) -> Self {
        Self {
            block_number,
            hash: "Loading...".to_string(),
            timestamp: "Loading...".to_string(),
            parent_hash: "Loading...".to_string(),
            state_root: "Loading...".to_string(),
            extrinsics_count: 0,
        }
    }

    pub fn draw(&self, f: &mut Frame, area: Rect) {
        let block = Block::default()
            .title("Block Details")
            .borders(Borders::ALL)
            .border_style(Style::default().fg(Color::White));

        let content = vec![
            Line::from(vec![
                Span::styled("Block: ", Style::default().fg(Color::Yellow)),
                Span::raw(self.block_number.to_string()),
            ]),
            Line::from(vec![
                Span::styled("Hash: ", Style::default().fg(Color::Yellow)),
                Span::raw(&self.hash),
            ]),
            Line::from(vec![
                Span::styled("Timestamp: ", Style::default().fg(Color::Yellow)),
                Span::raw(&self.timestamp),
            ]),
            Line::from(vec![
                Span::styled("Parent: ", Style::default().fg(Color::Yellow)),
                Span::raw(&self.parent_hash),
            ]),
            Line::from(vec![
                Span::styled("State Root: ", Style::default().fg(Color::Yellow)),
                Span::raw(&self.state_root),
            ]),
            Line::from(vec![
                Span::styled("Extrinsics: ", Style::default().fg(Color::Yellow)),
                Span::raw(self.extrinsics_count.to_string()),
            ]),
        ];

        let paragraph = Paragraph::new(content).block(block);
        f.render_widget(paragraph, area);
    }
}
