//! Claude Jobs Server — background Claude CLI task manager
//!
//! Manages background Claude CLI processes, persists jobs in SQLite,
//! and exposes an HTTP API + SSE streaming for live output.
//! Authentication via MetaMask signature verification.

mod auth;
mod autosnap;
mod credits;
mod jobs;
mod api;
mod snapshots;
mod merge;
mod screenshots;
mod userspace;
mod sudo;
mod process;

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
        .unwrap_or(8820);

    let db_dir = dirs_db();
    let manager = Arc::new(
        jobs::ClaudeJobManager::new(db_dir).expect("Failed to init job manager"),
    );

    // Mark any previously-running jobs as failed (stale from crash)
    manager.recover_stale_jobs().ok();

    // Background CID minting: once a minute, snapshot+register any module
    // that has no registry CID yet, so hub cards never sit at "no cid".
    autosnap::spawn();

    println!("Claude Jobs server starting on port {}", port);
    api::serve(manager, port).await;
}

fn dirs_db() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("claude")
}

/// One process-wide lock for every test that overrides the global $HOME
/// (sudo, credits, …) — separate per-module locks let those tests race
/// each other and flake under parallel `cargo test`.
#[cfg(test)]
pub(crate) fn home_test_lock() -> std::sync::MutexGuard<'static, ()> {
    use std::sync::{Mutex, OnceLock};
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|e| e.into_inner())
}
