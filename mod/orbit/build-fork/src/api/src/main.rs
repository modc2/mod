//! Build Jobs Server — background Claude CLI task manager
//!
//! Manages background Claude CLI processes, persists jobs in SQLite,
//! and exposes an HTTP API + SSE streaming for live output.
//! Authentication via MetaMask signature verification.

// The MCP tool table is one big json! literal — a schema per tool blows the
// default macro recursion budget.
#![recursion_limit = "512"]

mod agent_auth;
mod arena;
mod auth;
mod keys;
mod autosnap;
mod mcp;
mod costs;
mod credits;
mod audits;
mod jobs;
mod api;
mod snapshots;
mod merge;
mod suggestions;
mod github;
mod privacy;
mod screenshots;
mod userspace;
mod vault;
mod sudo;
mod process;
mod reaper;
mod system;

use std::path::PathBuf;
use std::sync::Arc;
use tracing_subscriber;

#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();

    // MCP over stdio. This is a bridge to the running HTTP server rather than
    // a second copy of the API: one job database, one auth gate, one set of
    // running processes — two servers over the same SQLite file would race.
    if args.iter().any(|a| a == "--stdio") {
        // Still needed here: $BUILD_FORK_TOKEN is validated in-process before any
        // request goes out, and that reads the same HMAC secret the server
        // signed it with. Without this, presenting a token panics.
        auth::init_secret();
        mcp::run_stdio().await;
        return;
    }

    tracing_subscriber::fmt::init();

    // Init HMAC signing secret for bearer tokens
    auth::init_secret();

    let port: u16 = args
        .get(1)
        .and_then(|p| p.parse().ok())
        .unwrap_or(8894);

    let db_dir = dirs_db();
    let manager = Arc::new(
        jobs::ClaudeJobManager::new(db_dir).expect("Failed to init job manager"),
    );

    // Mark any previously-running jobs as failed (stale from crash)
    manager.recover_stale_jobs().ok();

    // Give every finished task a localfs CID in the store index — this pass
    // covers jobs that finished before the store bridge existed.
    manager.spawn_cid_backfill();

    // Background CID minting: once a minute, snapshot+register any module
    // that has no registry CID yet, so hub cards never sit at "no cid".
    autosnap::spawn();

    println!("Build Jobs server starting on port {}", port);
    api::serve(manager, port).await;
}

fn dirs_db() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(home).join(".mod").join("build-fork")
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
