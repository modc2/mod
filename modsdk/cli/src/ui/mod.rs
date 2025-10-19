//! UI components and layout for the Subspace Explorer TUI
//!
//! This module provides the core UI components and theming for the Subspace Explorer TUI.

mod block_view;
mod status_bar;
mod theme;

pub use block_view::BlockView;
pub use status_bar::StatusBar;
pub use theme::Theme;

use ratatui::{
    layout::Rect,
    Frame,
};

/// Re-export common Ratatui types for easier access
pub mod prelude {
    pub use ratatui::{
        layout::{Alignment, Rect},
        style::{Color, Style, Modifier},
        text::{Line, Span},
        widgets::{Block, Borders, Paragraph, Widget},
        Frame,
    };

    pub use super::*;
}


/// Trait for UI components that can be rendered
pub trait Draw {
    /// Render the component to the given frame
    fn draw(&self, f: &mut Frame, area: Rect);
}

/// A simple component for testing the UI layout
#[derive(Debug, Clone)]
pub struct TestComponent {
    /// The title of the component
    title: String,
    /// Optional content to display
    content: Option<String>,
}

impl TestComponent {
    /// Create a new test component with the given title
    pub fn new(title: impl Into<String>) -> Self {
        Self {
            title: title.into(),
            content: None,
        }
    }

    /// Set the content of the component
    pub fn with_content(mut self, content: impl Into<String>) -> Self {
        self.content = Some(content.into());
        self
    }
}

impl Default for TestComponent {
    fn default() -> Self {
        Self {
            title: "Test Component".to_string(),
            content: Some("This is a test component".to_string()),
        }
    }
}

impl Draw for TestComponent {
    fn draw(&self, f: &mut Frame, area: Rect) {
        use crate::ui::prelude::*;

        let block = Block::default()
            .title(Line::from(self.title.as_str()))
            .borders(Borders::ALL)
            .border_style(Style::default().fg(Color::White));

        let content = self.content.as_deref().unwrap_or("");
        let paragraph = Paragraph::new(content)
            .block(block)
            .style(Style::default().fg(Color::White))
            .alignment(Alignment::Center);

        f.render_widget(paragraph, area);
    }
}
