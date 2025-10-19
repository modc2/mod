//! Main application state and logic for the Subspace Explorer TUI

use std::time::Duration;
use crate::ui::BlockView;

/// Main application state
#[derive(Debug, Default)]
pub struct App {
    /// Whether the application should quit
    pub should_quit: bool,
    /// The tick rate for the application
    pub tick_rate: Duration,
    /// The title of the application
    pub title: String,
    /// The latest block number
    pub latest_block: Option<u64>,
    /// Whether the application is connected to a node
    pub connected: bool,
    /// The name of the chain
    pub chain_name: String,
    /// The current block view
    pub current_block: Option<BlockView>,
}

impl App {
    /// Create a new instance of the application
    pub fn new() -> Self {
        Self {
            should_quit: false,
            tick_rate: Duration::from_millis(250),
            title: "Subspace Explorer".to_string(),
            latest_block: None,
            connected: false,
            chain_name: "Subspace".to_string(),
            current_block: None,
        }
    }

    /// Handle the tick event
    pub fn on_tick(&mut self) {
        // Update application state on tick
    }

    /// Set whether the application should quit
    pub fn quit(&mut self) {
        self.should_quit = true;
    }

    /// Get the current tick rate
    pub fn tick_rate(&self) -> Duration {
        self.tick_rate
    }
    
    /// Update the latest block number
    pub fn update_latest_block(&mut self, block_number: u64) {
        self.latest_block = Some(block_number);
    }
    
    /// Get the latest block number
    pub fn latest_block_number(&self) -> u64 {
        self.latest_block.unwrap_or(0)
    }
    
    /// Get the application title
    pub fn title(&self) -> &str {
        &self.title
    }
    
    /// Set connection status
    pub fn set_connected(&mut self, connected: bool) {
        self.connected = connected;
    }
    
    /// Set the chain name
    pub fn set_chain_name(&mut self, name: String) {
        self.chain_name = name;
    }
    
    /// Get the chain name
    pub fn chain_name(&self) -> &str {
        &self.chain_name
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_app_initialization() {
        let app = App::new();
        assert_eq!(app.title(), "Subspace Explorer");
        assert!(!app.should_quit);
        assert_eq!(app.tick_rate(), Duration::from_millis(250));
    }

    #[test]
    fn test_app_quit() {
        let mut app = App::new();
        assert!(!app.should_quit);
        app.quit();
        assert!(app.should_quit);
    }
}
