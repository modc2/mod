use ratatui::{
    buffer::Buffer,
    layout::Rect,
    style::{Color, Style},
    widgets::{Block, BorderType, Borders, Widget},
    Frame,
};

/// A status bar component for the TUI
#[derive(Debug, Clone)]
pub struct StatusBar {
    text: String,
    connected: bool,
}

impl StatusBar {
    /// Create a new status bar
    pub fn new(text: impl Into<String>, connected: bool) -> Self {
        Self {
            text: text.into(),
            connected,
        }
    }

    /// Create a status bar from the application state
    pub fn from_state(state: &crate::State) -> Self {
        let status = if state.client.is_some() {
            format!("Connected to {}", state.app.chain_name())
        } else {
            "Disconnected".to_string()
        };
        Self::new(status, state.client.is_some())
    }

    /// Set the status text
    pub fn with_text(mut self, text: impl Into<String>) -> Self {
        self.text = text.into();
        self
    }

    /// Set the connection status
    pub fn with_connected(mut self, connected: bool) -> Self {
        self.connected = connected;
        self
    }

    /// Draw the status bar
    pub fn draw(&self, f: &mut Frame, area: Rect) {
        f.render_widget(self, area);
    }
}

impl Widget for &StatusBar {
    fn render(self, area: Rect, buf: &mut Buffer) {
        let status_style = if self.connected {
            Style::default().fg(Color::Green)
        } else {
            Style::default().fg(Color::Red)
        };

        let status_text = if self.connected { "●" } else { "○" };
        let status_block = Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(status_style)
            .title_style(status_style)
            .title(format!(" {} {}", status_text, self.text));

        status_block.render(area, buf);
    }
}

impl Widget for StatusBar {
    fn render(self, area: Rect, buf: &mut Buffer) {
        (&self).render(area, buf)
    }
}
