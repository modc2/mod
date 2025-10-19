//! Subspace Explorer - A TUI client for the Subspace blockchain

// Import modules from the library crate
use subspace_explorer::{
    event::{Event, EventHandler},
    ui::{BlockView, StatusBar},
    State, DEFAULT_NODE_URL,
};

use anyhow::Result;
use crossterm::{
    event::KeyCode,
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout},
    widgets::{Block, Borders, Paragraph},
    Terminal,
};
use std::time::Duration;

/// Main entry point for the application
#[tokio::main]
async fn main() -> Result<()> {
    // Initialize logging
    pretty_env_logger::init();
    log::info!("Starting Subspace Explorer TUI");

    // Create application state
    let mut state = State::new();

    // Initialize the terminal
    let mut stdout = std::io::stdout();
    crossterm::execute!(
        &mut stdout,
        crossterm::terminal::EnterAlternateScreen,
        crossterm::event::EnableMouseCapture,
    )?;

    // Enable raw mode for proper key handling
    crossterm::terminal::enable_raw_mode()?;

    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    terminal.clear()?;
    terminal.hide_cursor()?;

    // Initialize blockchain client
    match state.init_client(DEFAULT_NODE_URL).await {
        Ok(_) => {
            state.app.set_connected(true);
            if let Some(client) = &state.client {
                // Get chain name
                match client.chain_name().await {
                    Ok(chain_name) => {
                        state.app.set_chain_name(chain_name);
                        log::info!("Connected to chain: {}", state.app.chain_name());

                        // Get latest block number
                        match client.latest_block_number().await {
                            Ok(block_number) => {
                                state.app.latest_block = Some(block_number);
                                log::info!("Latest block: #{}", block_number);
                            }
                            Err(e) => {
                                log::error!("Failed to fetch latest block: {}", e);
                            }
                        }
                    }
                    Err(e) => {
                        log::error!("Failed to fetch chain name: {}", e);
                        state.app.set_connected(false);
                    }
                }
            }
        }
        Err(e) => {
            log::error!("Failed to initialize client: {}", e);
            state.app.set_connected(false);
        }
    }

    // Create an event handler
    let mut event_handler = EventHandler::new(250);
    let mut should_quit = false;

    // Main application loop
    while !should_quit {
        // Render the UI
        terminal.draw(|f| {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Length(3), // Header
                    Constraint::Min(1),    // Main content
                    Constraint::Length(3), // Status bar
                ])
                .split(f.size());

            // Draw header
            let header = Paragraph::new("Subspace Explorer")
                .block(Block::default().borders(Borders::ALL));
            f.render_widget(header, chunks[0]);

            // Draw main content - split into left and right panels
            let content_chunks = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([
                    Constraint::Percentage(40), // Left panel (block list in the future)
                    Constraint::Percentage(60), // Right panel (block details)
                ])
                .split(chunks[1]);

            // Draw block list placeholder (left panel)
            let block_list = Paragraph::new("Block list will go here")
                .block(Block::default().borders(Borders::ALL).title("Blocks"));
            f.render_widget(block_list, content_chunks[0]);

            // Draw block details (right panel)
            if let Some(block_view) = &state.app.current_block {
                block_view.draw(f, content_chunks[1]);
            } else {
                // Initialize with a default block view if none exists
                let default_block = BlockView::new(
                    state.app.latest_block.unwrap_or(0)
                );
                default_block.draw(f, content_chunks[1]);
                state.app.current_block = Some(default_block);
            }

            // Draw the status bar
            let status_bar = StatusBar::new(
                format!("Connected to {} | Block: {}", 
                    state.app.chain_name(),
                    state.app.latest_block_number()
                ),
                state.app.connected
            );
            f.render_widget(status_bar, chunks[2]);
        })?;

        // Handle events with a timeout
        match tokio::time::timeout(Duration::from_millis(100), event_handler.next()).await {
            Ok(Some(Event::Key(key))) => match key.code {
                KeyCode::Char('q') => {
                    log::info!("Quit requested");
                    should_quit = true;
                }
                KeyCode::Char('r') => {
                    log::info!("Refreshing data...");
                    if let Some(client) = &state.client {
                        match client.latest_block_number().await {
                            Ok(block_number) => {
                                state.app.latest_block = Some(block_number);
                                log::info!("Refreshed latest block: #{}", block_number);
                                
                                // Update the block view with the latest block details
                                let mut block_view = BlockView::new(block_number);
                                // In a real implementation, we would fetch the actual block details here
                                // For now, we'll just set some placeholder values
                                block_view.hash = format!("0x{:x}", rand::random::<u64>());
                                block_view.timestamp = chrono::Local::now().to_rfc2822();
                                block_view.parent_hash = format!("0x{:x}", rand::random::<u64>());
                                block_view.state_root = format!("0x{:x}", rand::random::<u64>());
                                block_view.extrinsics_count = (rand::random::<u8>() % 10) as usize;
                                
                                state.app.current_block = Some(block_view);
                            }
                            Err(e) => {
                                log::error!("Failed to fetch latest block: {}", e);
                            }
                        }
                    }
                }
                _ => {}
            },
            // Handle other events if needed
            _ => {}
        }
    }

    // Clean up the terminal
    terminal.clear()?;
    terminal.show_cursor()?;
    crossterm::execute!(
        terminal.backend_mut(),
        crossterm::terminal::LeaveAlternateScreen,
        crossterm::event::DisableMouseCapture,
    )?;
    crossterm::terminal::disable_raw_mode()?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serial_test::serial;

    #[test]
    #[serial]
    fn test_main_app_initialization() -> Result<()> {
        // This is a simple test to verify the app can be created
        let app = app::App::new();
        assert!(!app.should_quit);
        Ok(())
    }
}
