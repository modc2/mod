//! Per-user, per-tool filesystem sandboxing for the claude API.
//!
//! Delegates the actual path math to `agent_base` (shared with codex/cursor)
//! so all coding-tool modules use the same canonicalization + containment
//! check. This file is the claude-specific glue: tool name, auth-header
//! plumbing, and an "owner gets a wider view" policy.

use crate::auth;
use axum::http::HeaderMap;
use std::path::{Path, PathBuf};

pub const TOOL: &str = "claude";

/// Extract the calling address from request headers. Empty string when
/// no valid token + the server is in local mode (CLAUDE_JOBS_LOCAL=1).
/// Returns an Err(msg) when auth is required but missing/invalid.
pub fn caller(headers: &HeaderMap) -> Result<String, String> {
    match auth::extract_address_from_headers(headers) {
        Ok(addr) => Ok(addr),
        Err(e) => {
            if std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1" {
                Ok(String::new())
            } else {
                Err(e)
            }
        }
    }
}

pub fn is_owner(addr: &str) -> bool {
    if addr.is_empty() {
        return false;
    }
    match auth::get_owner_address() {
        Some(owner) => owner.eq_ignore_ascii_case(addr),
        None => false,
    }
}

/// Resolve a user-facing path. Owner gets unrestricted access to the host
/// filesystem (the existing behavior). Non-owners are confined to their
/// workspace under ~/.mod/workspaces/<addr>/claude/workspace/.
///
/// `raw_path` may be:
///   - `~/...`     → expanded to $HOME (owner only)
///   - `/abs/...`  → owner: as-is; non-owner: workspace-relative
///   - `rel/path`  → workspace-relative for non-owners; cwd-relative for owners
pub fn resolve_path(caller_addr: &str, raw_path: &str) -> Result<PathBuf, String> {
    if caller_addr.is_empty() || is_owner(caller_addr) {
        // Owner / local-mode: keep existing semantics.
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        return Ok(PathBuf::from(raw_path.replacen('~', &home, 1)));
    }
    // Non-owner: sandbox.
    agent_base::resolve_user_path(caller_addr, TOOL, raw_path)
}

/// Ensure the calling user's workspace exists; returns its path.
pub fn ensure_workspace(caller_addr: &str) -> Result<PathBuf, String> {
    if caller_addr.is_empty() || is_owner(caller_addr) {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        return Ok(PathBuf::from(home));
    }
    agent_base::ensure_user_dirs(caller_addr, TOOL)
}

/// Per-user jobs DB path. Falls back to the legacy shared DB for owner /
/// local-mode so existing data keeps working.
pub fn jobs_db_path(caller_addr: &str) -> Result<PathBuf, String> {
    if caller_addr.is_empty() || is_owner(caller_addr) {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        return Ok(PathBuf::from(home).join(".mod").join("claude"));
    }
    let root = agent_base::user_root(caller_addr, TOOL)?;
    std::fs::create_dir_all(&root).map_err(|e| format!("mkdir {}: {e}", root.display()))?;
    Ok(root)
}

/// Display a real path back to the user with their workspace hidden as `~/`.
/// Owner sees `~/...` (existing); non-owner sees `~/foo` for files actually
/// at <workspace>/foo. This keeps absolute system paths out of the UI.
pub fn display_path(caller_addr: &str, p: &Path) -> String {
    if caller_addr.is_empty() || is_owner(caller_addr) {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        return p.to_string_lossy().replacen(&home, "~", 1);
    }
    if let Ok(ws) = agent_base::user_workspace(caller_addr, TOOL) {
        if let Ok(canon_ws) = std::fs::canonicalize(&ws) {
            let s = p.to_string_lossy().to_string();
            let ws_s = canon_ws.to_string_lossy().to_string();
            if let Some(stripped) = s.strip_prefix(&ws_s) {
                let cleaned = stripped.trim_start_matches('/');
                return if cleaned.is_empty() {
                    "~".to_string()
                } else {
                    format!("~/{cleaned}")
                };
            }
        }
    }
    p.to_string_lossy().to_string()
}
