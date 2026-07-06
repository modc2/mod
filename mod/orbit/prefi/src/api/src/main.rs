//! PreFi Server — Prediction Market Backend
//!
//! Manages prediction markets, user positions, and market data.
//! Exposes HTTP API for market creation, position tracking, and analytics.
//! Authentication via MetaMask signature verification.

mod auth;
mod markets;
mod api;
mod db;

use std::path::PathBuf;
use std::sync::Arc;
use tracing_subscriber;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt::init();

    // Init HMAC signing secret for bearer tokens
    auth::init_secret();

    let port: u16 = std::env::args()
        .nth(1)
        .and_then(|p| p.parse().ok())
        .unwrap_or(8830);

    let db_dir = dirs_db();
    let market_manager = Arc::new(
        markets::MarketManager::new(db_dir).expect("Failed to init market manager"),
    );

    println!("PreFi server starting on port {}", port);
    api::serve(market_manager, port).await;
}

fn dirs_db() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("prefi")
}
