//! Theme and styling for the Subspace Explorer TUI

use ratatui::{
    style::{Color, Style},
    widgets::{Block, Borders},
};

/// Color palette for the application
pub struct Palette {
    pub _primary: Color,
    pub _secondary: Color,
    pub _success: Color,
    pub _warning: Color,
    pub _error: Color,
    pub _background: Color,
    pub _foreground: Color,
    pub _highlight: Color,
}

impl Default for Palette {
    fn default() -> Self {
        Self {
            _primary: Color::Rgb(41, 127, 255),     // Bright blue
            _secondary: Color::Rgb(100, 100, 100),  // Gray
            _success: Color::Rgb(46, 204, 113),     // Green
            _warning: Color::Rgb(241, 196, 15),     // Yellow
            _error: Color::Rgb(231, 76, 60),        // Red
            _background: Color::Rgb(28, 28, 28),    // Dark gray
            _foreground: Color::Rgb(230, 230, 230), // Light gray
            _highlight: Color::Rgb(41, 127, 255),   // Same as primary
        }
    }
}

/// Application theme
#[derive(Default)]
pub struct Theme {
    pub _palette: Palette,
}

impl Theme {
    /// Create a new theme with default colors
    pub fn _new() -> Self {
        Self::default()
    }

    /// Get the default block style
    pub fn _block(&self) -> Block {
        Block::default().borders(Borders::ALL).style(
            Style::default()
                .fg(self._palette._foreground)
                .bg(self._palette._background),
        )
    }

    /// Get the style for the application title
    pub fn _title_style(&self) -> Style {
        Style::default()
            .fg(self._palette._primary)
            .add_modifier(ratatui::style::Modifier::BOLD)
    }

    /// Get the style for success messages
    pub fn _success_style(&self) -> Style {
        Style::default().fg(self._palette._success)
    }

    /// Get the style for warning messages
    pub fn _warning_style(&self) -> Style {
        Style::default().fg(self._palette._warning)
    }

    /// Get the style for error messages
    pub fn _error_style(&self) -> Style {
        Style::default().fg(self._palette._error)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_theme_creation() {
        let theme = Theme::default();
        assert_eq!(theme._palette._primary, Color::Rgb(41, 127, 255));
    }

    #[test]
    fn test_theme_styles() {
        let theme = Theme::default();

        // Test block style
        let block = theme._block().clone();
        assert_eq!(
            block.borders(Borders::ALL),
            Block::default().borders(Borders::ALL)
        );
        // Test title style
        let title_style = theme._title_style();
        assert_eq!(title_style.fg, Some(theme._palette._primary));
        assert!(title_style
            .add_modifier
            .contains(ratatui::style::Modifier::BOLD));
    }
}
