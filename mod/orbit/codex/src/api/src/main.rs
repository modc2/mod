//! Codex Jobs Server — background Codex CLI task manager
//!
//! Manages background Codex CLI processes, persists jobs in SQLite,
//! and exposes an HTTP API + SSE streaming for live output.
//! Authentication via MetaMask signature verification.

mod auth;
mod jobs;
mod api;

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
    let manager = Arc::new(
        jobs::CodexJobManager::new(db_dir).expect("Failed to init job manager"),
    );

    // Mark any previously-running jobs as failed (stale from crash)
    manager.recover_stale_jobs().ok();

    println!("Codex Jobs server starting on port {}", port);
    api::serve(manager, port).await;
}

fn dirs_db() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("codex")
}
