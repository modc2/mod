//! Who is running this arena.
//!
//! A leaderboard is a claim about other people's code, and a claim is worth
//! what its host is worth — so the console should be able to say, without
//! being asked twice, which box this is, which key signs for it, where the
//! bytes went, how long the process has been up and what it can build. All of
//! that is knowable here and none of it was being shown.
//!
//! The address is the box's own mod-protocol key — the same one that signs a
//! store push — read once through the protocol and kept for the life of the
//! process, because it cannot change under a running server.

use crate::{arena, blobs, mcp, mcpout, rustc, storelink};
use serde_json::{json, Value};
use std::sync::OnceLock;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

fn started() -> &'static (Instant, u64) {
    static T: OnceLock<(Instant, u64)> = OnceLock::new();
    T.get_or_init(|| {
        (
            Instant::now(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0),
        )
    })
}

/// Call once at boot so uptime is the process's, not the first caller's.
pub fn mark_start() {
    let _ = started();
}

/// The module directory — `import mod` has to run from a project, and the
/// arena's own `src/` shadows the package with its `mod.py`.
fn module_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../.."))
}

/// The address of the key this box signs with. One subprocess, once — the
/// protocol owns the keyring and there is no second source of truth for it.
fn address() -> Option<String> {
    static A: OnceLock<Option<String>> = OnceLock::new();
    A.get_or_init(|| {
        let out = std::process::Command::new("python3")
            .args(["-I", "-c", "import mod as m; print(m.key().address)"])
            .current_dir(module_dir())
            .output()
            .ok()?;
        if !out.status.success() {
            return None;
        }
        let a = String::from_utf8_lossy(&out.stdout).trim().to_string();
        if a.is_empty() {
            None
        } else {
            Some(a)
        }
    })
    .clone()
}

fn machine() -> String {
    std::fs::read_to_string("/etc/hostname")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".into())
}

/// How much of this box the arena is actually using: the blobs it has kept.
fn state() -> Value {
    let dir = blobs::state_dir();
    let blobs_dir = dir.join("blobs");
    let (files, bytes) = std::fs::read_dir(&blobs_dir)
        .map(|rd| {
            rd.filter_map(Result::ok).fold((0u64, 0u64), |(n, b), e| {
                (n + 1, b + e.metadata().map(|m| m.len()).unwrap_or(0))
            })
        })
        .unwrap_or((0, 0));
    json!({
        "dir": dir.to_string_lossy(),
        "blobs": files,
        "bytes": bytes,
        "registry": dir.join("registry.json").to_string_lossy(),
    })
}

/// The whole card. `store` costs a round trip to the store module and an
/// occasional token mint, so it can be left off by anything that only wants
/// to know who and where.
pub async fn card(with_store: bool) -> Value {
    let (start, epoch) = started();
    let info = arena::info();
    let port = mcp::base()
        .rsplit(':')
        .next()
        .and_then(|p| p.parse::<u16>().ok())
        .unwrap_or(50470);

    let mut v = json!({
        "module": "arena",
        "version": mcp::SERVER_VERSION,
        "protocol": "arena/1.0",
        "mcp_protocol": mcp::PROTOCOL_VERSION,
        "backend": "rust-mcp (axum)",
        // Who: the key that signs for this box, and the box.
        "host": {
            "address": address(),
            "machine": machine(),
            "pid": std::process::id(),
            "os": std::env::consts::OS,
            "arch": std::env::consts::ARCH,
            "signs_with": "the box's own mod-protocol key — the same one a store push is \
                           signed with",
        },
        // Where: every door into this process, and the one out of it.
        "urls": {
            "api": mcp::base(),
            "console": format!("{}/arena", mcp::base()),
            "mcp": format!("{}/mcp", mcp::base()),
            "per_module_mcp": format!("{}/m/<name>/mcp", mcp::base()),
            "gateway": mcpout::gateway(),
            "behind_the_router": "/arena (console) and /api/arena (API)",
        },
        "port": port,
        // How long, and how much.
        "uptime_seconds": start.elapsed().as_secs(),
        "started": epoch,
        "state": state(),
        "counts": {
            "modules": info["modules"].clone(),
            "games": info["games"].clone(),
            "classes": info["classes"].clone(),
            "players": info["players"].clone(),
            "matches": info["matches"].clone(),
        },
        // What it can do: where a match runs, and whether this box can build
        // a Rust class into the wasm the sandbox wants.
        "executes_in": ["browser", "node"],
        "toolchain": rustc::toolchain(),
        "player_kinds": crate::players::KINDS,
    });

    if with_store {
        v["store"] = storelink::status().await;
    }
    v
}
