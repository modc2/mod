//! Subspace Explorer - A TUI client for the Subspace blockchain

pub mod app;
pub mod client;
pub mod error;
pub mod event;
pub mod ui;
pub mod rpc_explorer;

use std::sync::Arc;
use anyhow::Context;
use crate::client::SubspaceClient;

// Re-export commonly used types
pub use app::App;
pub use event::{Event, EventHandler};
pub use ui::{BlockView, StatusBar};

/// Default WebSocket URL for the Subspace node
pub const DEFAULT_NODE_URL: &str = "ws://localhost:9944";

/// Application state
pub struct State {
    /// The TUI application
    pub app: App,
    /// Blockchain client
    pub client: Option<Arc<SubspaceClient>>,
}

impl State {
    /// Create a new application state
    pub fn new() -> Self {
        Self {
            app: App::new(),
            client: None,
        }
    }
    
    /// Initialize the blockchain client
    pub async fn init_client(&mut self, node_url: &str) -> anyhow::Result<()> {
        log::info!("Connecting to node at {}", node_url);
        let client = SubspaceClient::new(node_url)
            .await
            .context("Failed to connect to node")?;
            
        log::info!("Successfully connected to node");
        
        self.client = Some(Arc::new(client));
        Ok(())
    }
}

/// Prelude for convenient imports
pub mod prelude {
    pub use crate::{
        app::App,
        event::{Event, EventHandler},
        ui::{BlockView, StatusBar},
        State, DEFAULT_NODE_URL,
    };
}
