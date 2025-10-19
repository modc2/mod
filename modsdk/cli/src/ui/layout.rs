//! Layout system for the Subspace Explorer TUI

use ratatui::{
    layout::{Constraint, Direction, Layout, Rect},
    style::Style,
};

/// Represents a layout area with constraints
#[derive(Debug, Clone, Copy)]
pub struct LayoutArea {
    /// The area to render in
    pub _area: Rect,
    /// The constraints for this area
    pub _constraints: &'static [Constraint],
    /// The direction to split the area
    pub _direction: Direction,
    /// The style for this area
    pub _style: Style,
}

impl Default for LayoutArea {
    fn default() -> Self {
        Self {
            _area: Rect::default(),
            _constraints: &[Constraint::Percentage(100)],
            _direction: Direction::Vertical,
            _style: Style::default(),
        }
    }
}

impl LayoutArea {
    /// Create a new layout area
    pub fn _new(area: Rect) -> Self {
        Self {
            _area: area,
            ..Default::default()
        }
    }

    /// Set the constraints for this layout
    pub fn _constraints(mut self, constraints: &'static [Constraint]) -> Self {
        self._constraints = constraints;
        self
    }

    /// Set the direction for this layout
    pub fn _direction(mut self, direction: Direction) -> Self {
        self._direction = direction;
        self
    }

    /// Set the style for this layout
    pub fn _style(mut self, style: Style) -> Self {
        self._style = style;
        self
    }

    /// Split the layout into chunks
    pub fn _split(&self) -> Vec<Rect> {
        let layout = Layout::default()
            .direction(self._direction)
            .constraints(self._constraints)
            .split(self._area);
        layout.iter().cloned().collect()
    }

    /// Create a centered rectangle within the area
    pub fn _centered_rect(&self, percent_x: u16, percent_y: u16) -> Rect {
        let popup_layout = Layout::default()
            .direction(Direction::Vertical)
            .constraints(
                [
                    Constraint::Percentage((100 - percent_y) / 2),
                    Constraint::Percentage(percent_y),
                    Constraint::Percentage((100 - percent_y) / 2),
                ]
                .as_ref(),
            )
            .split(self._area);

        Layout::default()
            .direction(Direction::Horizontal)
            .constraints(
                [
                    Constraint::Percentage((100 - percent_x) / 2),
                    Constraint::Percentage(percent_x),
                    Constraint::Percentage((100 - percent_x) / 2),
                ]
                .as_ref(),
            )
            .split(popup_layout[1])[1]
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::layout::Rect;

    #[test]
    fn test_layout_area_creation() {
        let area = Rect::new(0, 0, 100, 50);
        let layout = LayoutArea::_new(area);
        assert_eq!(layout._area, area);
        assert_eq!(layout._direction, Direction::Vertical);
        assert_eq!(layout._constraints, &[Constraint::Percentage(100)]);
    }

    #[test]
    fn test_layout_split() {
        let area = Rect::new(0, 0, 100, 100);
        let layout = LayoutArea::_new(area)
            ._constraints(&[Constraint::Percentage(50), Constraint::Percentage(50)]);

        let chunks = layout._split();
        assert_eq!(chunks.len(), 2);
        assert_eq!(chunks[0].height, 50);
        assert_eq!(chunks[1].height, 50);
    }
}
