//! HTTP API for Claude Jobs — Axum endpoints + SSE streaming + MetaMask auth

use crate::auth;
use crate::credits;
use crate::jobs::{ClaudeJobManager, SubmitRequest};
use crate::merge;
use crate::process;
use crate::sudo;
use crate::userspace;
use crate::snapshots::{
    append_version, default_store, read_versions, restore_into, snapshot_dir, VersionRecord,
};
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    middleware,
    response::{
        sse::{Event, KeepAlive, Sse},
        IntoResponse,
    },
    routing::{delete, get, post, put},
    Json, Router,
};
use serde::Deserialize;
use serde_json::json;
use std::convert::Infallible;
use std::sync::Arc;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;
use tower_http::cors::{Any, CorsLayer};

type AppState = Arc<ClaudeJobManager>;

fn local_mode() -> bool {
    std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1"
}

/// The claude module's own directory on the host: `$HOME/mod/mod/orbit/claude`.
/// Writes inside it are "self-edits" and stay on the normal owner gate; anything
/// outside it touches OTHER modules / the system and requires a sudo signature.
fn claude_module_dir() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    std::path::PathBuf::from(home)
        .join("mod")
        .join("mod")
        .join("orbit")
        .join("claude")
}

/// True if `path` resolves to somewhere inside the claude module's own directory.
/// Canonicalizes the nearest existing ancestor so a not-yet-created file still
/// classifies correctly.
#[allow(dead_code)] // owner now edits any module without the cross-module sudo gate
fn is_within_claude_dir(path: &std::path::Path) -> bool {
    let base = std::fs::canonicalize(claude_module_dir()).unwrap_or_else(|_| claude_module_dir());
    let mut probe = path.to_path_buf();
    let canon = loop {
        if let Ok(c) = std::fs::canonicalize(&probe) {
            break c;
        }
        match probe.parent() {
            Some(p) if p != probe => probe = p.to_path_buf(),
            _ => break path.to_path_buf(),
        }
    };
    canon.starts_with(&base)
}

/// Reject a privileged request that lacks a valid sudo signature for `(action, target)`.
/// Returns `Some(response)` to short-circuit with 401, or `None` to proceed. Local mode
/// (host CLI, no auth) is fully trusted and bypasses the sudo requirement.
fn sudo_gate(
    headers: &axum::http::HeaderMap,
    action: &str,
    target: &str,
) -> Option<axum::response::Response> {
    if local_mode() {
        return None;
    }
    match sudo::verify_sudo(headers, action, target) {
        Ok(addr) => {
            println!("✓ sudo authorized: {} {} (by {})", action, target, addr);
            None
        }
        Err(e) => Some(
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({ "error": e, "sudo_required": true, "action": action, "target": target })),
            )
                .into_response(),
        ),
    }
}

pub async fn serve(manager: AppState, port: u16) {
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let local_mode = std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1";

    let challenge_store = auth::new_challenge_store();

    // Job routes — skip auth in local mode
    let job_routes = {
        let base = Router::new()
            .route("/jobs", post(submit_job))
            .route("/jobs/:id", delete(delete_job))
            .route("/jobs/:id/cancel", post(cancel_job))
            .route("/modules/:name", delete(delete_module))
            .route("/modules/:name/rename", put(rename_module))
            .route("/modules/:name/snapshot", post(snapshot_module))
            .route("/modules/:name/fork", post(fork_module))
            .route("/modules/:name/restore", post(restore_module))
            .route("/modules/import", post(import_module))
            // Merge requests: fork→propose→review are open to every signed-in
            // user (that's the point); the merge itself is owner-gated inside.
            .route("/modules/:name/mr-fork", post(mr_fork_module))
            .route("/modules/:name/merge-requests", post(open_merge_request))
            .route("/merge-requests/:id/comment", post(mr_comment))
            .route("/merge-requests/:id/update", post(mr_update))
            .route("/merge-requests/:id/close", post(mr_close))
            .route("/merge-requests/:id/merge", post(mr_merge))
            .route("/modules/:name/process", post(module_process))
            .route("/modules/:name/logs", post(module_logs))
            .route("/files/write", post(file_write))
            .route("/kill", post(kill_process))
            .route("/sudo/status", get(sudo_status))
            .route("/sudo/policy", post(sudo_policy_set))
            .route("/sudo/lock", post(sudo_lock))
            .route("/whitelist", post(add_to_whitelist))
            .route("/whitelist/:address", delete(remove_from_whitelist))
            .route("/grants", post(create_grant))
            .route("/grants", get(list_grants))
            .route("/grants/:id", delete(revoke_grant))
            // Session handoff: any signed-in user mints a QR code that signs
            // ANOTHER DEVICE in as themselves (redeem is public, below).
            .route("/auth/handoff", post(create_handoff))
            .route("/credits", get(get_credits))
            .route("/credits/sync", post(sync_credits))
            .route("/credits/accounts", get(credits_accounts))
            .route("/credits/debit", post(credits_debit));

        if local_mode {
            println!("⚡ Local mode — auth disabled");
            base.with_state(manager.clone())
        } else {
            base.layer(middleware::from_fn(auth::auth_middleware))
                .with_state(manager.clone())
        }
    };

    // Public routes (no auth required)
    let public_routes = Router::new()
        .route("/health", get(health))
        .route("/schema", get(api_schema))
        // Public code ledger: every task is world-readable (list, detail,
        // live stream). Mutations (submit/cancel/delete) stay authenticated.
        .route("/jobs", get(list_jobs))
        .route("/jobs/:id", get(get_job))
        .route("/jobs/:id/stream", get(stream_job))
        .route("/config", get(get_config))
        .route("/repos", get(list_repos))
        .route("/modules", get(list_modules))
        .route("/modules/:name/config", get(get_module_config))
        .route("/modules/:name/screenshot", get(crate::screenshots::module_screenshot))
        .route("/folders", get(list_folders))
        .route("/suggest_folders", get(suggest_folders))
        .route("/files/tree", get(file_tree))
        .route("/files/content", get(file_content))
        .route("/files/raw", get(file_raw))
        .route("/files/search", get(file_search))
        .route("/files/grep", get(file_grep))
        .route("/changelog", get(get_changelog))
        .route("/versions/:version", get(get_version))
        .route("/modules/:name/versions", get(list_module_versions))
        .route("/modules/:name/registry", get(module_registry))
        .route("/autosnap/status", get(crate::autosnap::status_handler))
        // MR metadata is part of the public code ledger (like /jobs); the
        // diff/file bodies are default-deny like the /files/* endpoints.
        .route("/merge-requests", get(list_all_mrs))
        .route("/modules/:name/merge-requests", get(list_module_mrs))
        .route("/merge-requests/:id", get(get_mr))
        .route("/merge-requests/:id/diff", get(mr_diff))
        .route("/merge-requests/:id/file", get(mr_file))
        .route("/owner", get(get_owner))
        // Public by design: the grant id (from the owner's QR) IS the capability.
        .route("/grants/:id/redeem", post(redeem_grant_guest))
        // Public by design: the single-use handoff code IS the capability.
        .route("/auth/handoff/redeem", post(redeem_handoff))
        .route("/whitelist", get(get_whitelist))
        .route("/auth/role", get(get_role))
        .route(
            "/auth/challenge",
            get(auth::challenge).with_state(challenge_store.clone()),
        )
        .route(
            "/auth/verify",
            post(auth::verify).with_state(challenge_store),
        )
        .route("/auth/terms", get(auth::terms))
        // The public jobs reads (list/get/stream) pull from the same manager.
        .with_state(manager);

    let app = Router::new()
        .merge(job_routes)
        .merge(public_routes)
        .layer(cors);

    // Credit system: poll registered deposit wallets, forward funds to the owner
    credits::spawn_watcher();

    // Bind host defaults to all interfaces (needed inside Docker so the Caddy
    // gateway in a sibling container can reach it). For host-only single-port
    // exposure, set BIND_HOST=127.0.0.1 so only the local Caddy can reach it.
    let host = std::env::var("BIND_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let addr = format!("{}:{}", host, port);
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("Failed to bind");

    println!("Listening on http://{}", addr);
    axum::serve(listener, app).await.expect("Server error");
}

async fn health() -> impl IntoResponse {
    // `local` is authoritative for the app's local-mode probe — never infer
    // local mode from an unauthenticated read succeeding (public endpoints
    // exist), or a browser can trap itself in a tokenless session.
    Json(json!({ "status": "ok", "service": "claude-jobs", "local": local_mode() }))
}

/// Public: machine-readable endpoint catalog for the console's API tab.
/// auth levels: "public" (no token), "bearer" (any signed-in user),
/// "owner" (bearer + owner address; mutations on other modules also need x-sudo).
async fn api_schema() -> impl IntoResponse {
    let e = |method: &str, path: &str, auth: &str, desc: &str| {
        json!({ "method": method, "path": path, "auth": auth, "desc": desc })
    };
    Json(json!({
        "service": "claude-jobs",
        "base": "/api/claude",
        "auth_note": "Bearer token from /auth/challenge + /auth/verify (wallet signature). Non-owner wallets sign the Terms of Use embedded in their first challenge.",
        "endpoints": [
            e("GET", "/health", "public", "Liveness + whether auth is disabled (local mode)"),
            e("GET", "/schema", "public", "This endpoint catalog"),
            e("GET", "/owner", "public", "Configured owner address"),
            e("GET", "/auth/terms?address=0x..", "public", "Terms of Use text + whether the address still needs to accept them"),
            e("GET", "/auth/challenge?address=0x..", "public", "Nonce message to sign (embeds Terms on first sign-in)"),
            e("POST", "/auth/verify", "public", "{address, message, signature} → bearer token"),
            e("GET", "/auth/role", "public", "Role of the presented token (owner/peer/guest)"),
            e("GET", "/jobs", "public", "World-readable task ledger (all jobs)"),
            e("GET", "/jobs/:id", "public", "One job with full output"),
            e("GET", "/jobs/:id/stream", "public", "Server-sent events stream of live job output"),
            e("POST", "/jobs", "bearer", "Submit a task: {prompt, model?, work_dir?, module_name?, system_prompt?, agent_type?}"),
            e("POST", "/jobs/:id/cancel", "bearer", "Cancel a running job (yours, or any if owner)"),
            e("DELETE", "/jobs/:id", "bearer", "Delete a job record (yours, or any if owner)"),
            e("GET", "/modules", "public", "All modules in the anchor tree"),
            e("GET", "/modules/:name/config", "public", "A module's config.json"),
            e("GET", "/modules/:name/screenshot", "public", "PNG screenshot of the module's app (?fresh=1 captures now)"),
            e("GET", "/modules/:name/versions", "public", "Snapshot chain for a module"),
            e("GET", "/modules/:name/registry", "public", "On-chain registry status"),
            e("POST", "/modules/import", "owner", "Import a module: {github} or {cid}"),
            e("POST", "/modules/:name/mr-fork", "bearer", "Fork a module into your workspace pinned to a base CID: {refresh?}"),
            e("GET", "/merge-requests", "public", "All merge requests (public ledger)"),
            e("GET", "/modules/:name/merge-requests", "public", "Merge requests for a module"),
            e("POST", "/modules/:name/merge-requests", "bearer", "Open an MR: {title, description?, head_cid?, base_cid?} (defaults to your fork)"),
            e("GET", "/merge-requests/:id", "public", "One merge request"),
            e("GET", "/merge-requests/:id/diff", "bearer", "Changed files + conflicts vs the live tree"),
            e("GET", "/merge-requests/:id/file?path=&which=base|head|live", "bearer", "File content from either side of an MR"),
            e("POST", "/merge-requests/:id/comment", "bearer", "Comment: {body, action?: approve|request_changes (trusted only)}"),
            e("POST", "/merge-requests/:id/update", "bearer", "Author pushes a new revision: {head_cid?, message?}"),
            e("POST", "/merge-requests/:id/close", "bearer", "Close an MR (author or trusted)"),
            e("POST", "/merge-requests/:id/merge", "owner", "Agentic merge: stages base/head trees and submits a three-way merge job (x-sudo for non-claude modules)"),
            e("POST", "/modules/:name/snapshot", "owner", "Snapshot module to content-addressed storage"),
            e("POST", "/modules/:name/fork", "owner", "Fork a module"),
            e("POST", "/modules/:name/restore", "owner", "Restore module from a snapshot CID"),
            e("PUT", "/modules/:name/rename", "owner", "Rename a module"),
            e("DELETE", "/modules/:name", "owner", "Delete a module (x-sudo)"),
            e("POST", "/modules/:name/process", "owner", "status|start|stop|restart the module's api/app"),
            e("POST", "/modules/:name/logs", "owner", "Tail the module's process logs: {target: api|app, lines?}"),
            e("GET", "/files/tree", "bearer", "File tree (default-deny without token)"),
            e("GET", "/files/content", "bearer", "Read a file"),
            e("POST", "/files/write", "bearer", "Write a file (peers sandboxed to their workspace)"),
            e("GET", "/files/search", "bearer", "Filename search"),
            e("GET", "/files/grep", "bearer", "Content grep"),
            e("GET", "/whitelist", "public", "Whitelisted addresses"),
            e("POST", "/whitelist", "owner", "Add address to whitelist"),
            e("DELETE", "/whitelist/:address", "owner", "Remove address from whitelist"),
            e("GET", "/grants", "owner", "List timed QR access grants"),
            e("POST", "/grants", "owner", "Create a timed access grant"),
            e("DELETE", "/grants/:id", "owner", "Revoke a grant"),
            e("POST", "/grants/:id/redeem", "public", "Redeem a QR grant as a walletless guest"),
            e("POST", "/auth/handoff", "bearer", "Mint a one-time phone sign-in code (optional ttl secs, 60–86400)"),
            e("POST", "/auth/handoff/redeem", "public", "Redeem a handoff code for a token"),
            e("GET", "/sudo/status", "owner", "Sudo session + policy"),
            e("POST", "/sudo/policy", "owner", "Set sudo session policy"),
            e("POST", "/sudo/lock", "owner", "End the sudo session now"),
            e("GET", "/credits", "bearer", "Your deposit address + balance"),
            e("GET", "/credits/accounts", "owner", "All credit accounts"),
            e("POST", "/credits/sync", "bearer", "Re-scan chain deposits"),
            e("GET", "/config", "public", "This module's config.json"),
            e("GET", "/changelog", "public", "Snapshot changelog"),
            e("GET", "/repos", "public", "Known repo roots"),
            e("GET", "/folders", "public", "Anchor folder listing"),
        ]
    }))
}

async fn get_config() -> impl IntoResponse {
    // Walk up from the binary's location to find config.json in the module root
    let config_path = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .and_then(|d| {
            // Binary is in server/target/release/ — walk up to module root
            let mut dir = d.as_path();
            for _ in 0..5 {
                let candidate = dir.join("config.json");
                if candidate.exists() {
                    return Some(candidate);
                }
                dir = dir.parent()?;
            }
            None
        });

    // Fallback: check known module path
    let config_path = config_path.unwrap_or_else(|| {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
        std::path::PathBuf::from(format!("{}/mod/mod/orbit/claude/config.json", home))
    });

    if !config_path.exists() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "config.json not found" })),
        )
            .into_response();
    }

    match std::fs::read_to_string(&config_path) {
        Ok(content) => match serde_json::from_str::<serde_json::Value>(&content) {
            Ok(config) => (StatusCode::OK, Json(config)).into_response(),
            Err(e) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": format!("Invalid JSON: {}", e) })),
            )
                .into_response(),
        },
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("Failed to read config: {}", e) })),
        )
            .into_response(),
    }
}

async fn get_owner() -> impl IntoResponse {
    // Priority 1: Check config.json "owner" field (live-editable)
    if let Some(owner) = read_config_owner() {
        return Json(json!({
            "has_owner": true,
            "owner": owner,
            "message": "Owner set via config.json"
        }));
    }

    // Priority 2: Fall back to owner.json
    let owner_path = dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(".mod")
        .join("claude")
        .join("owner.json");

    if !owner_path.exists() {
        return Json(json!({
            "has_owner": false,
            "owner": null,
            "message": "No owner set - first authenticated user will become owner"
        }));
    }

    match std::fs::read_to_string(&owner_path) {
        Ok(content) => {
            match serde_json::from_str::<serde_json::Value>(&content) {
                Ok(data) => {
                    let owner = data.get("owner").and_then(|v| v.as_str());
                    Json(json!({
                        "has_owner": owner.is_some(),
                        "owner": owner,
                        "message": if owner.is_some() {
                            "Owner is set"
                        } else {
                            "Owner file exists but is invalid"
                        }
                    }))
                }
                Err(_) => Json(json!({
                    "has_owner": false,
                    "owner": null,
                    "message": "Owner file is corrupted"
                }))
            }
        }
        Err(_) => Json(json!({
            "has_owner": false,
            "owner": null,
            "message": "Failed to read owner file"
        }))
    }
}

/// Read the "owner" field from config.json (re-read each call so live edits take effect)
fn read_config_owner() -> Option<String> {
    let config_path = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .and_then(|d| {
            let mut dir = d.as_path();
            for _ in 0..5 {
                let candidate = dir.join("config.json");
                if candidate.exists() {
                    return Some(candidate);
                }
                dir = dir.parent()?;
            }
            None
        })
        .unwrap_or_else(|| {
            let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
            std::path::PathBuf::from(format!("{}/mod/mod/orbit/claude/config.json", home))
        });

    let content = std::fs::read_to_string(&config_path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    let owner = data.get("owner").and_then(|v| v.as_str())?.to_lowercase();
    if owner.is_empty() { None } else { Some(owner) }
}

/// Returns the role for the given address: "owner" or "user"
async fn get_role(Query(params): Query<RoleQuery>) -> impl IntoResponse {
    let address = params.address.to_lowercase();
    let is_owner = auth::is_owner(&address);
    // Whitelisted editors are not the owner but are trusted to edit the host repo.
    let is_editor = !is_owner && auth::is_trusted(&address);
    let role = if is_owner {
        "owner"
    } else if is_editor {
        "editor"
    } else {
        "user"
    };
    Json(json!({
        "address": address,
        "role": role,
        "is_owner": is_owner,
        "is_editor": is_editor,
        // Trusted = owner OR whitelisted editor; both may edit the orbit.
        "can_edit": is_owner || is_editor,
    }))
}

#[derive(Deserialize)]
struct RoleQuery {
    address: String,
}

async fn get_whitelist() -> impl IntoResponse {
    Json(json!({ "whitelist": auth::read_whitelist() }))
}

#[derive(Deserialize)]
struct WhitelistAddRequest {
    address: String,
}

fn require_owner(headers: &axum::http::HeaderMap) -> Result<(), (StatusCode, Json<serde_json::Value>)> {
    let caller = auth::extract_address_from_headers(headers).map_err(|e| (
        StatusCode::UNAUTHORIZED,
        Json(json!({ "error": e })),
    ))?;
    if !auth::is_owner(&caller) {
        return Err((
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: whitelist edits require the configured owner" })),
        ));
    }
    Ok(())
}

async fn add_to_whitelist(
    headers: axum::http::HeaderMap,
    Json(req): Json<WhitelistAddRequest>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let addr = req.address.trim().to_lowercase();
    if !addr.starts_with("0x") || addr.len() != 42 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "address must be a 0x-prefixed 40-hex string" })),
        ).into_response();
    }
    let mut list = auth::read_whitelist();
    if !list.contains(&addr) {
        list.push(addr.clone());
        if let Err(e) = auth::write_whitelist(&list) {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
        }
    }
    (StatusCode::OK, Json(json!({ "whitelist": list, "added": addr }))).into_response()
}

async fn remove_from_whitelist(
    headers: axum::http::HeaderMap,
    Path(address): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let target = address.trim().to_lowercase();
    let before = auth::read_whitelist();
    let after: Vec<String> = before.iter().filter(|a| *a != &target).cloned().collect();
    if after.len() == before.len() {
        return (StatusCode::NOT_FOUND, Json(json!({ "error": "address not in whitelist" }))).into_response();
    }
    if let Err(e) = auth::write_whitelist(&after) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    (StatusCode::OK, Json(json!({ "whitelist": after, "removed": target }))).into_response()
}

// ── Sudo session & policy ────────────────────────────────────────────
// One signature unlocks sudo for policy.session_secs (default 1h, like Unix
// sudo's credential cache); the owner tailors duration + per-action always-ask
// here. Changing the policy always demands a fresh signature so a cached
// session can never loosen the rules. Locking is free.

async fn sudo_status(headers: axum::http::HeaderMap) -> impl IntoResponse {
    if local_mode() {
        return Json(json!({ "local": true, "session_active": false })).into_response();
    }
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let policy = sudo::read_policy();
    let now = chrono::Utc::now().timestamp();
    let session = sudo::active_session();
    Json(json!({
        "session_active": session.is_some(),
        "expires": session.as_ref().map(|(_, exp)| exp),
        "remaining_secs": session.as_ref().map(|(_, exp)| (exp - now).max(0)),
        "policy": policy,
        "default_session_secs": sudo::DEFAULT_SESSION_SECS,
        "max_session_secs": sudo::MAX_SESSION_SECS,
    }))
    .into_response()
}

#[derive(Deserialize)]
struct SudoPolicyRequest {
    #[serde(default)]
    session_secs: Option<i64>,
    #[serde(default)]
    always_ask: Option<Vec<String>>,
}

async fn sudo_policy_set(
    headers: axum::http::HeaderMap,
    Json(req): Json<SudoPolicyRequest>,
) -> impl IntoResponse {
    if !local_mode() {
        if let Err(e) = require_owner(&headers) { return e.into_response(); }
        // Auth requirements can only be changed by proving key possession NOW —
        // an active session deliberately does not cover this.
        if let Err(e) = sudo::verify_sudo_fresh(&headers, "policy", "sudo") {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({ "error": e, "sudo_required": true, "action": "policy", "target": "sudo" })),
            )
                .into_response();
        }
    }
    let mut policy = sudo::read_policy();
    if let Some(secs) = req.session_secs {
        if !(0..=sudo::MAX_SESSION_SECS).contains(&secs) {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": format!("session_secs must be 0..={}", sudo::MAX_SESSION_SECS) })),
            )
                .into_response();
        }
        policy.session_secs = secs;
    }
    if let Some(list) = req.always_ask {
        policy.always_ask = list
            .into_iter()
            .map(|a| a.trim().to_lowercase())
            .filter(|a| !a.is_empty())
            .collect();
    }
    if let Err(e) = sudo::write_policy(&policy) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    Json(json!({ "success": true, "policy": sudo::read_policy() })).into_response()
}

async fn sudo_lock(headers: axum::http::HeaderMap) -> impl IntoResponse {
    if !local_mode() {
        if let Err(e) = require_owner(&headers) { return e.into_response(); }
    }
    sudo::end_session();
    Json(json!({ "success": true, "session_active": false })).into_response()
}

// ── Time-boxed edit grants (QR hand-off) ─────────────────────────────
// The owner mints a grant → a QR-shareable id that confers temporary edit
// access (default 1h), optionally gated by a second-factor key. Redemption
// happens inside /auth/verify at sign-in; these endpoints are owner-only and
// just mint / list / revoke. See auth.rs for the grant model.

const GRANT_TTL_MIN: i64 = 60; // 1 minute floor
const GRANT_TTL_MAX: i64 = 30 * 24 * 3600; // 30 days ceiling
const GRANT_TTL_DEFAULT: i64 = 3600; // 1 hour

#[derive(Deserialize)]
struct CreateGrantRequest {
    #[serde(default)]
    ttl: Option<i64>,
    #[serde(default)]
    key: Option<String>,
    #[serde(default)]
    label: Option<String>,
}

/// Strip the secret hash before sending a grant to the client; expose only
/// whether a key is required.
fn grant_json(g: &auth::Grant) -> serde_json::Value {
    json!({
        "id": g.id,
        "exp": g.exp,
        "ttl": g.ttl,
        "label": g.label,
        "created": g.created,
        "key_required": g.key_hash.is_some(),
    })
}

async fn create_grant(
    headers: axum::http::HeaderMap,
    Json(req): Json<CreateGrantRequest>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let ttl = req.ttl.unwrap_or(GRANT_TTL_DEFAULT).clamp(GRANT_TTL_MIN, GRANT_TTL_MAX);
    let key = req.key.as_deref().map(str::trim).filter(|s| !s.is_empty());
    let label = req.label.as_deref();
    match auth::create_grant(ttl, key, label) {
        Ok(g) => (StatusCode::OK, Json(grant_json(&g))).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response(),
    }
}

async fn list_grants(headers: axum::http::HeaderMap) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let file = auth::list_grants();
    let grants: Vec<_> = file.grants.iter().map(grant_json).collect();
    let redemptions: Vec<_> = file
        .redemptions
        .iter()
        .map(|r| json!({
            "address": r.address,
            "exp": r.exp,
            "grant": r.grant,
            "redeemed": r.redeemed,
        }))
        .collect();
    (StatusCode::OK, Json(json!({ "grants": grants, "redemptions": redemptions }))).into_response()
}

#[derive(Deserialize, Default)]
struct RedeemGrantRequest {
    #[serde(default)]
    key: Option<String>,
}

/// Walletless QR redemption — anyone opening the invite trades the grant id
/// (+ optional key) for a guest identity and bearer token that both expire
/// with the grant. No auth: possession of the id is the capability.
async fn redeem_grant_guest(
    Path(id): Path<String>,
    body: Option<Json<RedeemGrantRequest>>,
) -> impl IntoResponse {
    let req = body.map(|Json(r)| r).unwrap_or_default();
    let key = req.key.as_deref().map(str::trim).filter(|s| !s.is_empty());
    match auth::redeem_grant_guest(&id, key) {
        Ok((address, exp)) => {
            println!("✓ Guest grant redeemed: {} via {} (until {})", address, id, exp);
            let token = auth::mint_token(&address);
            (StatusCode::OK, Json(json!({ "token": token, "address": address, "exp": exp }))).into_response()
        }
        Err(e) => (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response(),
    }
}

async fn revoke_grant(
    headers: axum::http::HeaderMap,
    Path(id): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    if auth::revoke_grant(&id) {
        (StatusCode::OK, Json(json!({ "revoked": id }))).into_response()
    } else {
        (StatusCode::NOT_FOUND, Json(json!({ "error": "grant not found" }))).into_response()
    }
}

// ── Session handoff — QR sign-in on another device ───────────────────
// A signed-in browser mints a single-use code bound to its OWN identity
// (default 5 minutes, minter-chosen TTL clamped in auth.rs); another device
// (the same person's phone) trades the code for a fresh bearer token as that
// address — no wallet or signing needed there. Minting requires auth (any
// signed-in user, not just the owner: you can only hand off yourself).
// Redemption is public: the code is the capability.

#[derive(Deserialize)]
struct CreateHandoffRequest {
    ttl: Option<i64>,
}

async fn create_handoff(
    headers: axum::http::HeaderMap,
    body: Option<Json<CreateHandoffRequest>>,
) -> impl IntoResponse {
    let address = match auth::extract_address_from_headers(&headers) {
        Ok(a) => a,
        Err(e) => {
            return (StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response()
        }
    };
    let ttl = body.and_then(|Json(b)| b.ttl);
    let (code, exp) = auth::create_handoff(&address, ttl);
    println!("✓ Handoff code minted for {} (until {})", address, exp);
    (StatusCode::OK, Json(json!({ "code": code, "exp": exp, "address": address }))).into_response()
}

#[derive(Deserialize)]
struct RedeemHandoffRequest {
    code: String,
}

async fn redeem_handoff(Json(req): Json<RedeemHandoffRequest>) -> impl IntoResponse {
    match auth::redeem_handoff(&req.code) {
        Ok(address) => {
            println!("✓ Handoff redeemed — new device signed in as {}", address);
            let token = auth::mint_token(&address);
            (StatusCode::OK, Json(json!({ "token": token, "address": address }))).into_response()
        }
        Err(e) => (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response(),
    }
}

// ── Credits — per-key deposit wallets, owner-swept ledger ────────────
// Backed by credits.rs; state under ~/.mod/{module}/credits/. Authed routes:
// the caller's identity is their signed-in address ("local" in local mode).

fn caller_identity(headers: &axum::http::HeaderMap) -> Result<String, axum::response::Response> {
    match auth::extract_address_from_headers(headers) {
        Ok(addr) => Ok(addr),
        Err(_) if local_mode() => Ok("local".to_string()),
        Err(e) => Err((StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response()),
    }
}

fn account_json(identity: &str, a: &credits::Account) -> serde_json::Value {
    json!({
        "identity": identity,
        "deposit_address": a.deposit_address,
        "credited_wei": a.credited_wei().to_string(),
        "spent_wei": a.spent_wei().to_string(),
        "balance_wei": a.balance_wei().to_string(),
        "deposits": a.deposits,
        "spends": a.spends,
    })
}

/// Caller's credit account: ensures it exists (which registers the deposit
/// wallet with the sweep watcher) and returns balances + chain settings.
async fn get_credits(headers: axum::http::HeaderMap) -> impl IntoResponse {
    let identity = match caller_identity(&headers) { Ok(i) => i, Err(r) => return r };
    match credits::ensure_account(&identity) {
        Ok(a) => (StatusCode::OK, Json(json!({
            "account": account_json(&identity, &a),
            "chain": credits::chain_cfg(),
        }))).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response(),
    }
}

/// On-demand sweep: check the caller's deposit wallet on-chain and credit
/// anything that landed (the 60s watcher does the same in the background).
async fn sync_credits(headers: axum::http::HeaderMap) -> impl IntoResponse {
    let identity = match caller_identity(&headers) { Ok(i) => i, Err(r) => return r };
    if let Err(e) = credits::ensure_account(&identity) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    match credits::sweep(&identity).await {
        Ok(r) => {
            let a = credits::read_ledger().accounts.get(&identity).cloned().unwrap_or_default();
            (StatusCode::OK, Json(json!({
                "sweep": r,
                "account": account_json(&identity, &a),
            }))).into_response()
        }
        Err(e) => (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    }
}

/// Owner-only: every account in the ledger, for the console's admin view.
async fn credits_accounts(headers: axum::http::HeaderMap) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let ledger = credits::read_ledger();
    let accounts: Vec<_> = ledger
        .accounts
        .iter()
        .map(|(id, a)| account_json(id, a))
        .collect();
    (StatusCode::OK, Json(json!({ "accounts": accounts }))).into_response()
}

#[derive(Deserialize)]
struct CreditsDebitRequest {
    identity: String,
    /// Wei as a decimal string — u128 doesn't survive JS numbers.
    wei: String,
    #[serde(default)]
    reason: Option<String>,
}

/// Owner-only: charge an account (subscription / pay-per-use settlement).
async fn credits_debit(
    headers: axum::http::HeaderMap,
    Json(req): Json<CreditsDebitRequest>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let wei: u128 = match req.wei.trim().parse() {
        Ok(w) => w,
        Err(_) => return (StatusCode::BAD_REQUEST, Json(json!({ "error": "wei must be a decimal string" }))).into_response(),
    };
    let identity = req.identity.trim().to_lowercase();
    match credits::debit(&identity, wei, req.reason.as_deref().unwrap_or("debit")) {
        Ok(a) => (StatusCode::OK, Json(json!({ "account": account_json(&identity, &a) }))).into_response(),
        Err(e) => (StatusCode::PAYMENT_REQUIRED, Json(json!({ "error": e }))).into_response(),
    }
}

async fn submit_job(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Json(req): Json<SubmitRequest>,
) -> impl IntoResponse {
    // Extract user address from auth token
    let auth_header = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    let user_address = match auth::extract_address_from_header(auth_header) {
        Ok(addr) => addr,
        Err(_) => {
            // In local mode, skip permission check
            if std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1" {
                String::new()
            } else {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(json!({ "error": "Could not extract address from token" })),
                )
                    .into_response();
            }
        }
    };

    // Peers (every non-owner) default + sandbox the job's work_dir to their
    // private root (~/.mod/peers/<addr>). Owner / local-mode work in the module
    // tree. The whitelist no longer widens this — whitelisted users are peers.
    let mut req = req;
    if !user_address.is_empty() && !auth::is_owner(&user_address) {
        // Default work_dir to the caller's workspace root.
        let ws = match userspace::ensure_workspace(&user_address) {
            Ok(p) => p,
            Err(e) => return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": format!("workspace init: {e}") })),
            ).into_response(),
        };
        let requested = req.work_dir.clone().unwrap_or_else(|| ".".to_string());
        match userspace::resolve_path(&user_address, &requested) {
            Ok(p) => req.work_dir = Some(p.to_string_lossy().into_owned()),
            Err(e) => return (
                StatusCode::FORBIDDEN,
                Json(json!({ "error": format!("work_dir outside your workspace: {e}") })),
            ).into_response(),
        }
        // module_name: non-owners cannot create at arbitrary orbit/ paths.
        // They may scaffold inside their workspace only (treated as a sub-path).
        if req.module_name.is_some() {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({
                    "error": "Module creation requires owner. Non-owners can only modify files inside their workspace."
                })),
            ).into_response();
        }
        // Surface the workspace root in logs so the user knows where their files land.
        tracing::info!(addr = %user_address, ws = %ws.display(), "scoped submit");
    }

    req.user_address = Some(user_address);

    let job = mgr.submit(req).await;
    (StatusCode::CREATED, Json(json!(job))).into_response()
}

async fn list_jobs(State(mgr): State<AppState>) -> impl IntoResponse {
    // Public code ledger: every task is world-readable, no auth required.
    let jobs = mgr.list_jobs();
    Json(json!({ "jobs": jobs, "count": jobs.len() }))
}

async fn get_job(
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    // Public code ledger: any task is world-readable by id.
    match mgr.get_job(&id) {
        Some(job) => (StatusCode::OK, Json(json!(job))).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "Job not found" })),
        )
            .into_response(),
    }
}

async fn delete_job(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    // Check that user owns this job or is the system owner
    if let Some(job) = mgr.get_job(&id) {
        let auth_header = headers
            .get(axum::http::header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        if let Ok(user_addr) = auth::extract_address_from_header(auth_header) {
            if !auth::is_owner(&user_addr)
                && !job.user_address.is_empty()
                && job.user_address.to_lowercase() != user_addr.to_lowercase()
            {
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({ "error": "You can only delete your own jobs" })),
                )
                    .into_response();
            }
        }
    }

    mgr.delete_job(&id);
    Json(json!({ "success": true })).into_response()
}

async fn cancel_job(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    // Check that user owns this job or is the system owner
    if let Some(job) = mgr.get_job(&id) {
        let auth_header = headers
            .get(axum::http::header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        if let Ok(user_addr) = auth::extract_address_from_header(auth_header) {
            if !auth::is_owner(&user_addr)
                && !job.user_address.is_empty()
                && job.user_address.to_lowercase() != user_addr.to_lowercase()
            {
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({ "error": "You can only cancel your own jobs" })),
                )
                    .into_response();
            }
        }
    }

    match mgr.cancel_job(&id).await {
        Ok(()) => (StatusCode::OK, Json(json!({ "success": true }))).into_response(),
        Err(e) => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": e })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
struct RepoQuery {
    q: Option<String>,
}

#[derive(Deserialize)]
struct ModuleQuery {
    q: Option<String>,
    anchor: Option<String>,
}

#[derive(Deserialize)]
struct TreeQuery {
    path: Option<String>,
    depth: Option<usize>,
}

async fn file_tree(
    headers: axum::http::HeaderMap,
    Query(params): Query<TreeQuery>,
) -> impl IntoResponse {
    // Default-deny: only authenticated callers (or local-mode) reach the FS.
    let caller = match userspace::caller(&headers) {
        Ok(a) => a,
        Err(e) => return Json(json!({ "tree": [], "error": e })),
    };
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    // Owner/local default to the module tree (~/mod/mod); peers to their own
    // root ("." resolves to ~/.mod/peers/<addr>). resolve_path enforces the
    // boundary either way.
    let default_root = if caller.is_empty() || userspace::is_owner(&caller) {
        "~/mod/mod".to_string()
    } else {
        ".".to_string()
    };
    let raw_path = params.path.unwrap_or(default_root);
    let resolved_pb = match userspace::resolve_path(&caller, &raw_path) {
        Ok(p) => p,
        Err(e) => return Json(json!({ "tree": [], "error": e })),
    };
    let max_depth = params.depth.unwrap_or(3);

    let root = resolved_pb.as_path();
    if !root.is_dir() {
        return Json(json!({ "tree": [], "error": "Directory not found" }));
    }

    // Per-file CIDs + root hash use the exact snapshot scheme (file CID =
    // sha256(bytes), tree CID = sha256(sorted manifest)) so what the file
    // browser shows lines up with VERSIONS snapshot CIDs. Hash-only — no
    // blobs are written. Skipped (root_hash: null) for oversized trees.
    let (root_hash, cid_map) = match crate::snapshots::hash_dir(root, 128 * 1024 * 1024) {
        Ok((tree_cid, manifest)) => {
            let map: std::collections::HashMap<String, (String, u64)> = manifest
                .files
                .into_iter()
                .map(|f| (f.path.clone(), (f.cid, f.size)))
                .collect();
            (Some(tree_cid), map)
        }
        Err(_) => (None, std::collections::HashMap::new()),
    };

    fn walk(
        root: &std::path::Path,
        dir: &std::path::Path,
        depth: usize,
        max_depth: usize,
        home: &str,
        cids: &std::collections::HashMap<String, (String, u64)>,
    ) -> Vec<serde_json::Value> {
        if depth >= max_depth {
            return vec![];
        }
        let mut entries: Vec<serde_json::Value> = Vec::new();
        let Ok(rd) = std::fs::read_dir(dir) else { return vec![] };
        let mut items: Vec<_> = rd.flatten().collect();
        items.sort_by(|a, b| {
            let a_is_dir = a.path().is_dir();
            let b_is_dir = b.path().is_dir();
            b_is_dir.cmp(&a_is_dir).then_with(|| {
                a.file_name().to_string_lossy().to_lowercase().cmp(
                    &b.file_name().to_string_lossy().to_lowercase(),
                )
            })
        });
        for entry in items {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') { continue; }
            if name == "node_modules" || name == "__pycache__" || name == "target" || name == ".git" { continue; }
            let full = path.to_string_lossy().to_string();
            let display = full.replacen(home, "~", 1);
            let is_dir = path.is_dir();
            let children = if is_dir { walk(root, &path, depth + 1, max_depth, home, cids) } else { vec![] };
            let rel = path
                .strip_prefix(root)
                .ok()
                .map(|p| p.to_string_lossy().replace('\\', "/"));
            let hashed = rel.as_deref().and_then(|r| cids.get(r));
            entries.push(json!({
                "name": name,
                "path": display,
                "type": if is_dir { "directory" } else { "file" },
                "cid": hashed.map(|(c, _)| c.clone()),
                "size": hashed.map(|(_, s)| *s),
                "children": children,
            }));
        }
        entries
    }

    let tree = walk(root, root, 0, max_depth, &home, &cid_map);
    Json(json!({ "tree": tree, "path": raw_path, "root_hash": root_hash }))
}

async fn list_repos(Query(params): Query<RepoQuery>) -> impl IntoResponse {
    let query = params.q.unwrap_or_default().to_lowercase();
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());

    // Directories to scan for git repos (check for .git)
    let git_scan_dirs = vec![
        home.clone(),
        format!("{}/mod", home),
    ];

    // Directories to list as project folders (subfolders of a git repo)
    let project_scan_dirs = vec![
        format!("{}/mod/mod/orbit", home),
        format!("{}/mod/mod/core", home),
    ];

    let mut repos: Vec<serde_json::Value> = Vec::new();
    let mut seen = std::collections::HashSet::new();

    // Scan for git repos
    for scan_dir in &git_scan_dirs {
        let dir_path = std::path::Path::new(scan_dir);
        if !dir_path.is_dir() { continue; }
        if let Ok(entries) = std::fs::read_dir(dir_path) {
            for entry in entries.flatten() {
                let path = entry.path();
                if !path.is_dir() { continue; }
                let git_dir = path.join(".git");
                if !git_dir.exists() { continue; }
                let full_path = path.to_string_lossy().to_string();
                if seen.contains(&full_path) { continue; }
                let name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                let display_path = full_path.replacen(&home, "~", 1);
                if !query.is_empty()
                    && !name.to_lowercase().contains(&query)
                    && !display_path.to_lowercase().contains(&query)
                { continue; }
                seen.insert(full_path.clone());
                repos.push(json!({ "name": name, "path": full_path, "display": display_path }));
            }
        }
    }

    // Scan project folders (modules/components within a repo)
    for scan_dir in &project_scan_dirs {
        let dir_path = std::path::Path::new(scan_dir);
        if !dir_path.is_dir() { continue; }
        if let Ok(entries) = std::fs::read_dir(dir_path) {
            for entry in entries.flatten() {
                let path = entry.path();
                if !path.is_dir() { continue; }
                // Skip hidden dirs and __pycache__
                let name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                if name.starts_with('.') || name.starts_with('_') { continue; }
                let full_path = path.to_string_lossy().to_string();
                if seen.contains(&full_path) { continue; }
                let display_path = full_path.replacen(&home, "~", 1);
                if !query.is_empty()
                    && !name.to_lowercase().contains(&query)
                    && !display_path.to_lowercase().contains(&query)
                { continue; }
                seen.insert(full_path.clone());
                repos.push(json!({ "name": name, "path": full_path, "display": display_path }));
            }
        }
    }

    // Sort by name
    repos.sort_by(|a, b| {
        let a_name = a["name"].as_str().unwrap_or("");
        let b_name = b["name"].as_str().unwrap_or("");
        a_name.cmp(b_name)
    });

    Json(json!({ "repos": repos }))
}

/// Newest file mtime under a module dir — "when was this last touched".
/// Depth-capped and skipping dot/build/dependency dirs so scanning the whole
/// fleet on every /modules call stays cheap.
fn newest_mtime(dir: &std::path::Path, depth: usize, newest: &mut u64) {
    if depth == 0 { return; }
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for entry in rd.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let path = entry.path();
        if path.is_dir() {
            if name.starts_with('.')
                || matches!(name.as_str(), "node_modules" | "target" | "dist" | "build" | "out" | "__pycache__" | "venv" | "logs")
            { continue; }
            newest_mtime(&path, depth - 1, newest);
        } else if let Ok(meta) = entry.metadata() {
            if let Some(secs) = meta.modified().ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
            {
                if secs > *newest { *newest = secs; }
            }
        }
    }
}

/// List orbit and core modules with config.json data (app_url, api_url, etc.)
async fn list_modules(Query(params): Query<ModuleQuery>) -> impl IntoResponse {
    let query = params.q.unwrap_or_default().to_lowercase();
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let anchor = params.anchor.unwrap_or_else(|| format!("{}/mod", home));
    // Expand ~ to home
    let anchor = anchor.replacen("~", &home, 1);

    let mut modules: Vec<serde_json::Value> = Vec::new();

    // Everything under the local core/ and orbit/ trees is the host owner's —
    // the per-module config.json "owner" fields are historical noise (old keys,
    // other chains) and would make the UI invent phantom owners. Attribute all
    // scanned modules to the configured owner; a module's own config value is
    // only used when no host owner is configured at all.
    let host_owner = auth::get_owner_address();

    // Load registry.json for authoritative CID lookups
    let registry_path = format!("{}/.mod/api/registry.json", home);
    let registry_data: Option<serde_json::Value> = std::fs::read_to_string(&registry_path)
        .ok()
        .and_then(|content| serde_json::from_str(&content).ok());
    // The file is `{owner: {mod: cid}}` at the root; older writes wrapped the
    // same map in a `data` key — accept both so CIDs don't silently vanish.
    let registry_map = registry_data
        .as_ref()
        .and_then(|v| v.get("data").and_then(|d| d.as_object()).or_else(|| v.as_object()));

    // The module tree root itself is selectable as the "mod" module. Its
    // work_dir spans orbit/ AND core/, so a single job can edit any module —
    // the cross-module escape hatch. Its config.json lives at the anchor
    // root ({anchor}/config.json, name "mod"), one level above the tree.
    let mod_root = format!("{}/mod", anchor);
    if std::path::Path::new(&mod_root).is_dir()
        && (query.is_empty() || "mod".contains(&query))
    {
        let root_config: serde_json::Value =
            std::fs::read_to_string(format!("{}/config.json", anchor))
                .ok()
                .and_then(|c| serde_json::from_str(&c).ok())
                .unwrap_or_else(|| json!({}));
        let mut newest: u64 = 0;
        newest_mtime(std::path::Path::new(&mod_root), 1, &mut newest);
        modules.push(json!({
            "name": "mod",
            "path": mod_root,
            "display": mod_root.replacen(&home, "~", 1),
            "category": "root",
            "has_config": true,
            "app_url": serde_json::Value::Null,
            "api_url": serde_json::Value::Null,
            "description": "The whole module tree — every mod under orbit/ and core/. Select it for cross-module work: one job can read and edit any module.",
            "fns": Vec::<String>::new(),
            "has_app_dir": false,
            "has_server_dir": false,
            "has_api_dir": false,
            "owner": host_owner.clone(),
            "version": root_config.get("version").and_then(|v| v.as_str()),
            "cid": serde_json::Value::Null,
            "deps": Vec::<String>::new(),
            "created_at": std::fs::metadata(&mod_root).ok()
                .and_then(|m| m.created().ok())
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs()),
            "updated_at": if newest > 0 { Some(newest) } else { None },
        }));
    }

    // Scan both orbit/ and core/ directories
    let scan_dirs = vec![
        (format!("{}/mod/orbit", anchor), "orbit"),
        (format!("{}/mod/core", anchor), "core"),
    ];

    for (scan_dir, category) in &scan_dirs {
        let dir_path = std::path::Path::new(scan_dir);
        if !dir_path.is_dir() { continue; }
        // The tree roots are themselves mods: a config.json at orbit/ or
        // core/ names the tree, and the hub lists it as a card under its own
        // tab. Dir hints (app/, api/) are ignored here — at the root those
        // are sibling MODULES named "app"/"api", not this mod's services.
        let root_config = dir_path.join("config.json");
        if let Some(config) = std::fs::read_to_string(&root_config)
            .ok()
            .and_then(|c| serde_json::from_str::<serde_json::Value>(&c).ok())
        {
            let name = config.get("name").and_then(|v| v.as_str()).unwrap_or(category).to_string();
            if query.is_empty() || name.to_lowercase().contains(&query) {
                let full_path = dir_path.to_string_lossy().to_string();
                let urls = config.get("urls");
                let app_url = urls.and_then(|u| u.get("app")).and_then(|v| v.as_str())
                    .or_else(|| config.get("app_url").and_then(|v| v.as_str()));
                let api_url = urls.and_then(|u| u.get("api")).and_then(|v| v.as_str())
                    .or_else(|| config.get("api_url").and_then(|v| v.as_str()));
                let str_list = |key: &str| -> Vec<String> {
                    config.get(key).and_then(|v| v.as_array())
                        .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                        .unwrap_or_default()
                };
                let cid = registry_map.as_ref().and_then(|reg| {
                    reg.iter().find_map(|(_, owner_mods)| {
                        owner_mods.as_object().and_then(|m| {
                            m.get(&name.to_lowercase()).or_else(|| m.get(&name)).and_then(|v| v.as_str())
                        })
                    })
                }).map(String::from);
                // Depth 1: the root's own files only — recursing here would
                // walk every module's tree on each /modules call.
                let mut newest: u64 = 0;
                newest_mtime(dir_path, 1, &mut newest);
                modules.push(json!({
                    "name": name,
                    "path": full_path,
                    "display": full_path.replacen(&home, "~", 1),
                    "category": category,
                    "has_config": true,
                    "app_url": app_url,
                    "api_url": api_url,
                    "description": config.get("description").and_then(|v| v.as_str()),
                    "fns": str_list("fns"),
                    "has_app_dir": false,
                    "has_server_dir": false,
                    "has_api_dir": false,
                    "owner": host_owner.clone().or_else(|| config.get("owner").and_then(|v| v.as_str()).map(String::from)),
                    "version": config.get("version").and_then(|v| v.as_str()),
                    "cid": cid,
                    "deps": str_list("deps"),
                    "created_at": std::fs::metadata(dir_path).ok()
                        .and_then(|m| m.created().ok())
                        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                        .map(|d| d.as_secs()),
                    "updated_at": if newest > 0 { Some(newest) } else { None },
                }));
            }
        }
        if let Ok(entries) = std::fs::read_dir(dir_path) {
            for entry in entries.flatten() {
                let path = entry.path();
                if !path.is_dir() { continue; }
                let name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
                if name.starts_with('.') || name.starts_with('_') { continue; }

                // Filter by query (search name only, ignoring category prefix)
                if !query.is_empty() && !name.to_lowercase().contains(&query) {
                    continue;
                }

                let full_path = path.to_string_lossy().to_string();
                let display_path = full_path.replacen(&home, "~", 1);

                // Try to read config.json from multiple locations
                let mut app_url: Option<String> = None;
                let mut api_url: Option<String> = None;
                let mut description: Option<String> = None;
                let mut has_config = false;
                let mut fns: Vec<String> = Vec::new();
                let mut owner: Option<String> = None;
                let mut version: Option<String> = None;
                let mut cid: Option<String> = None;
                // Module dependency edges, declared in config.json as
                // `"deps": ["chain", "store", ...]`. Used to draw the hub
                // dependency graph; modules that declare none are isolated.
                let mut deps: Vec<String> = Vec::new();

                let config_paths = vec![
                    path.join("config.json"),
                    path.join(&name).join("config.json"),
                ];

                for config_path in &config_paths {
                    if config_path.exists() {
                        if let Ok(content) = std::fs::read_to_string(config_path) {
                            if let Ok(config) = serde_json::from_str::<serde_json::Value>(&content) {
                                has_config = true;
                                // Check for urls.app and urls.api
                                if let Some(urls) = config.get("urls") {
                                    if let Some(v) = urls.get("app").and_then(|v| v.as_str()) {
                                        app_url = Some(v.to_string());
                                    }
                                    if let Some(v) = urls.get("api").and_then(|v| v.as_str()) {
                                        api_url = Some(v.to_string());
                                    }
                                }
                                // Also check top-level app_url / api_url
                                if app_url.is_none() {
                                    if let Some(v) = config.get("app_url").and_then(|v| v.as_str()) {
                                        app_url = Some(v.to_string());
                                    }
                                }
                                if api_url.is_none() {
                                    if let Some(v) = config.get("api_url").and_then(|v| v.as_str()) {
                                        api_url = Some(v.to_string());
                                    }
                                }
                                if let Some(v) = config.get("description").and_then(|v| v.as_str()) {
                                    description = Some(v.to_string());
                                }
                                if let Some(arr) = config.get("fns").and_then(|v| v.as_array()) {
                                    fns = arr.iter().filter_map(|v| v.as_str().map(String::from)).collect();
                                }
                                if let Some(v) = config.get("owner").and_then(|v| v.as_str()) {
                                    owner = Some(v.to_string());
                                }
                                if let Some(v) = config.get("version").and_then(|v| v.as_str()) {
                                    version = Some(v.to_string());
                                }
                                if let Some(arr) = config.get("deps").and_then(|v| v.as_array()) {
                                    deps = arr.iter().filter_map(|v| v.as_str().map(String::from)).collect();
                                }
                                // CID from local config is ignored; registry is authoritative
                                break; // Use first found config
                            }
                        }
                    }
                }

                // Look up CID from registry (authoritative source over local config)
                if let Some(reg) = &registry_map {
                    let name_lower = name.to_lowercase();
                    for (_owner_key, owner_mods) in reg.iter() {
                        if let Some(mods) = owner_mods.as_object() {
                            if let Some(reg_cid) = mods.get(&name_lower).or_else(|| mods.get(&name)).and_then(|v| v.as_str()) {
                                cid = Some(reg_cid.to_string());
                                break;
                            }
                        }
                    }
                }

                // Check for app/ directory as hint for frontend
                let has_app_dir = path.join("app").is_dir();
                // Check for server/ directory as hint for backend
                let has_server_dir = path.join("server").is_dir();
                // Check for api/ directory
                let has_api_dir = path.join("api").is_dir();

                // Get directory creation time
                let created_at: Option<u64> = std::fs::metadata(&path)
                    .ok()
                    .and_then(|m| m.created().ok())
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_secs());

                // Last-updated = newest source-file mtime in the module tree.
                let mut newest: u64 = 0;
                newest_mtime(&path, 6, &mut newest);
                let updated_at: Option<u64> = if newest > 0 { Some(newest) } else { None };

                modules.push(json!({
                    "name": name,
                    "path": full_path,
                    "display": display_path,
                    "category": category,
                    "has_config": has_config,
                    "app_url": app_url,
                    "api_url": api_url,
                    "description": description,
                    "fns": fns,
                    "has_app_dir": has_app_dir,
                    "has_server_dir": has_server_dir,
                    "has_api_dir": has_api_dir,
                    "owner": host_owner.clone().or(owner),
                    "version": version,
                    "cid": cid,
                    "deps": deps,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }));
            }
        }
    }

    // Sort by name
    modules.sort_by(|a, b| {
        let a_name = a["name"].as_str().unwrap_or("");
        let b_name = b["name"].as_str().unwrap_or("");
        a_name.cmp(b_name)
    });

    Json(json!({ "modules": modules, "count": modules.len(), "anchor": anchor.replacen(&home, "~", 1) }))
}

#[derive(Deserialize)]
struct FolderQuery {
    q: Option<String>,
    path: Option<String>,
    depth: Option<usize>,
}

/// List folders under a path, with optional search filter
async fn list_folders(Query(params): Query<FolderQuery>) -> impl IntoResponse {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let raw_path = params.path.unwrap_or_else(|| "~/mod".to_string());
    let resolved = raw_path.replacen("~", &home, 1);
    let max_depth = params.depth.unwrap_or(2);
    let query = params.q.unwrap_or_default().to_lowercase();

    let root = std::path::Path::new(&resolved);
    if !root.is_dir() {
        return Json(json!({ "folders": [], "error": "Directory not found" }));
    }

    fn walk_folders(
        dir: &std::path::Path,
        base: &std::path::Path,
        depth: usize,
        max_depth: usize,
        query: &str,
        home: &str,
        results: &mut Vec<serde_json::Value>,
    ) {
        if depth > max_depth { return; }
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        for entry in rd.flatten() {
            let path = entry.path();
            if !path.is_dir() { continue; }
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || name == "node_modules" || name == "__pycache__"
                || name == "target" || name == "build" || name == "dist"
                || name == ".next" || name == "venv" || name == ".venv" { continue; }
            let full = path.to_string_lossy().to_string();
            let rel = path.strip_prefix(base).unwrap_or(&path).to_string_lossy().to_string();
            if !query.is_empty() && !rel.to_lowercase().contains(query) {
                // still recurse — subfolders might match
                walk_folders(&path, base, depth + 1, max_depth, query, home, results);
                continue;
            }
            let has_config = path.join("config.json").exists();
            let has_mod = path.join("mod.py").exists();
            let display = full.replacen(home, "~", 1);
            results.push(json!({
                "name": rel,
                "path": full,
                "display": display,
                "has_config": has_config,
                "has_mod": has_mod,
            }));
            walk_folders(&path, base, depth + 1, max_depth, query, home, results);
        }
    }

    let mut results = Vec::new();
    walk_folders(root, root, 0, max_depth, &query, &home, &mut results);
    results.sort_by(|a, b| {
        let a_name = a["name"].as_str().unwrap_or("");
        let b_name = b["name"].as_str().unwrap_or("");
        a_name.cmp(b_name)
    });

    Json(json!({ "folders": results, "count": results.len(), "path": raw_path }))
}

#[derive(Deserialize)]
struct SuggestQuery {
    query: String,
    path: Option<String>,
    top_k: Option<usize>,
    embedcode_url: Option<String>,
}

/// Suggest folders using embedcode similarity search
async fn suggest_folders(Query(params): Query<SuggestQuery>) -> impl IntoResponse {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let raw_path = params.path.unwrap_or_else(|| "~/mod".to_string());
    let resolved = raw_path.replacen("~", &home, 1);
    let top_k = params.top_k.unwrap_or(10);
    let ec_url = params.embedcode_url.unwrap_or_else(|| "http://localhost:8920".to_string());

    // Call embedcode search API
    let client = reqwest::Client::new();
    let search_resp = client
        .post(format!("{}/search", ec_url))
        .json(&json!({
            "query": params.query,
            "path": resolved,
            "top_k": top_k * 5,
        }))
        .send()
        .await;

    let results = match search_resp {
        Ok(resp) => {
            match resp.json::<Vec<serde_json::Value>>().await {
                Ok(items) => items,
                Err(_) => return Json(json!({ "suggestions": [], "error": "Failed to parse embedcode response" })),
            }
        }
        Err(e) => {
            return Json(json!({ "suggestions": [], "error": format!("Embedcode not reachable at {}: {}", ec_url, e) }));
        }
    };

    // Group by folder, keep best score per folder
    let mut folder_scores: std::collections::HashMap<String, serde_json::Value> = std::collections::HashMap::new();
    let base = std::path::Path::new(&resolved);

    for item in &results {
        let file_path = item.get("path").and_then(|v| v.as_str()).unwrap_or("");
        let score = item.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let preview = item.get("preview").and_then(|v| v.as_str()).unwrap_or("");

        let folder = std::path::Path::new(file_path)
            .parent()
            .unwrap_or(std::path::Path::new(""))
            .to_string_lossy()
            .to_string();
        let rel = std::path::Path::new(&folder)
            .strip_prefix(base)
            .unwrap_or(std::path::Path::new(&folder))
            .to_string_lossy()
            .to_string();

        let existing_score = folder_scores
            .get(&rel)
            .and_then(|v| v.get("score"))
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);

        if score > existing_score {
            let display = folder.replacen(&home, "~", 1);
            let has_config = std::path::Path::new(&folder).join("config.json").exists();
            let has_mod = std::path::Path::new(&folder).join("mod.py").exists();
            folder_scores.insert(rel.clone(), json!({
                "name": rel,
                "path": folder,
                "display": display,
                "score": score,
                "preview": &preview[..preview.len().min(120)],
                "has_config": has_config,
                "has_mod": has_mod,
            }));
        }
    }

    let mut suggestions: Vec<serde_json::Value> = folder_scores.into_values().collect();
    suggestions.sort_by(|a, b| {
        let a_score = a.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let b_score = b.get("score").and_then(|v| v.as_f64()).unwrap_or(0.0);
        b_score.partial_cmp(&a_score).unwrap_or(std::cmp::Ordering::Equal)
    });
    suggestions.truncate(top_k);

    Json(json!({ "suggestions": suggestions, "count": suggestions.len() }))
}

/// Return raw config.json for a specific module
async fn get_module_config(
    Path(name): Path<String>,
    Query(params): Query<ModuleQuery>,
) -> impl IntoResponse {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let anchor = params
        .anchor
        .unwrap_or_else(|| format!("{}/mod", home))
        .replacen("~", &home, 1);

    // Search orbit/ and core/ for the module. The tree-root mods resolve
    // above the module dirs: "mod" (the whole tree) keeps its config at the
    // anchor root, "orbit"/"core" at their tree roots.
    let search_dirs = match name.as_str() {
        "mod" => vec![anchor.clone()],
        "orbit" | "core" => vec![format!("{}/mod/{}", anchor, name)],
        _ => vec![
            format!("{}/mod/orbit/{}", anchor, name),
            format!("{}/mod/core/{}", anchor, name),
        ],
    };

    for module_dir in &search_dirs {
        let base = std::path::Path::new(module_dir);
        if !base.is_dir() {
            continue;
        }
        // Try config.json at root level and nested (module_name/config.json)
        let config_paths = vec![
            base.join("config.json"),
            base.join(&name).join("config.json"),
        ];
        for config_path in &config_paths {
            if config_path.exists() {
                if let Ok(content) = std::fs::read_to_string(config_path) {
                    if let Ok(config) = serde_json::from_str::<serde_json::Value>(&content) {
                        return (
                            StatusCode::OK,
                            Json(json!({
                                "name": name,
                                "path": config_path.to_string_lossy(),
                                "config": config,
                            })),
                        )
                            .into_response();
                    }
                }
            }
        }
    }

    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": format!("No config.json found for module '{}'", name) })),
    )
        .into_response()
}

/// Delete a module directory. Owner-only: module creation is owner-only, so
/// every module in the tree is the configured owner's to remove. Deleting any
/// module other than claude itself additionally requires sudo (fresh x-sudo
/// signature or an open sudo session).
async fn delete_module(
    headers: axum::http::HeaderMap,
    State(_mgr): State<AppState>,
    Path(name): Path<String>,
) -> impl IntoResponse {
    // Extract user address from auth token
    let auth_header = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let user_addr = match auth::extract_address_from_header(auth_header) {
        Ok(addr) => addr,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({ "error": "Authentication required" })),
            )
                .into_response();
        }
    };

    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());

    // Search orbit/ and core/ for the module
    let search_dirs = vec![
        format!("{}/mod/mod/orbit/{}", home, name),
        format!("{}/mod/mod/core/{}", home, name),
    ];

    let mut found_path: Option<String> = None;

    for module_dir in &search_dirs {
        let base = std::path::Path::new(module_dir);
        if !base.is_dir() {
            continue;
        }
        found_path = Some(module_dir.clone());
        break;
    }

    let module_path = match found_path {
        Some(p) => p,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": format!("Module '{}' not found", name) })),
            )
                .into_response();
        }
    };

    // Authorization: deletion is owner-only. Creation is owner-only too, so
    // every module in the tree is the configured owner's to remove.
    if !auth::is_owner(&user_addr) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: deleting a module requires the configured owner" })),
        )
            .into_response();
    }

    // Deleting any module other than claude itself is a privileged cross-module
    // operation — require sudo (fresh x-sudo signature or an open sudo session).
    if name != "claude" {
        if let Some(denied) = sudo_gate(&headers, "delete", &name) {
            return denied;
        }
    }

    // Delete the module directory
    match std::fs::remove_dir_all(&module_path) {
        Ok(_) => Json(json!({ "success": true, "deleted": name })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("Failed to delete module: {}", e) })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
struct RenameRequest {
    new_name: String,
}

/// Rename a module directory (only module owner or system owner can rename)
async fn rename_module(
    headers: axum::http::HeaderMap,
    State(_mgr): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<RenameRequest>,
) -> impl IntoResponse {
    let new_name = body.new_name.trim().to_string();
    if new_name.is_empty() || new_name.contains('/') || new_name.contains('\\') || new_name.starts_with('.') {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "Invalid module name" })),
        )
            .into_response();
    }

    // Extract user address from auth token
    let auth_header = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let user_addr = match auth::extract_address_from_header(auth_header) {
        Ok(addr) => addr,
        Err(_) => {
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({ "error": "Authentication required" })),
            )
                .into_response();
        }
    };

    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());

    // Search orbit/ and core/ for the module
    let search_dirs = vec![
        ("orbit", format!("{}/mod/mod/orbit/{}", home, name)),
        ("core", format!("{}/mod/mod/core/{}", home, name)),
    ];

    let mut found_path: Option<String> = None;
    let mut found_category: Option<String> = None;
    let mut module_owner: Option<String> = None;

    for (category, module_dir) in &search_dirs {
        let base = std::path::Path::new(module_dir);
        if !base.is_dir() {
            continue;
        }
        found_path = Some(module_dir.clone());
        found_category = Some(category.to_string());

        // Read owner from config.json
        let config_paths = vec![
            base.join("config.json"),
            base.join(&name).join("config.json"),
        ];
        for config_path in &config_paths {
            if config_path.exists() {
                if let Ok(content) = std::fs::read_to_string(config_path) {
                    if let Ok(config) = serde_json::from_str::<serde_json::Value>(&content) {
                        if let Some(v) = config.get("owner").and_then(|v| v.as_str()) {
                            module_owner = Some(v.to_lowercase());
                        }
                    }
                }
            }
        }
        break;
    }

    let module_path = match found_path {
        Some(p) => p,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": format!("Module '{}' not found", name) })),
            )
                .into_response();
        }
    };

    // Authorization: must be system owner or module owner
    let is_sys_owner = auth::is_owner(&user_addr);
    let is_mod_owner = module_owner
        .as_ref()
        .map(|o| o == &user_addr.to_lowercase())
        .unwrap_or(false);

    if !is_sys_owner && !is_mod_owner {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "You can only rename modules you own" })),
        )
            .into_response();
    }

    // Renaming a module the caller does not personally own is privileged.
    if name != "claude" && !is_mod_owner {
        if let Some(denied) = sudo_gate(&headers, "rename", &name) {
            return denied;
        }
    }

    // Build new path in the same category directory
    let category = found_category.unwrap_or_else(|| "orbit".to_string());
    let new_path = format!("{}/mod/mod/{}/{}", home, category, new_name);

    // Check that the target doesn't already exist
    if std::path::Path::new(&new_path).exists() {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("Module '{}' already exists", new_name) })),
        )
            .into_response();
    }

    // Rename (move) the directory
    match std::fs::rename(&module_path, &new_path) {
        Ok(_) => {
            // Update config.json name field if it exists
            let config_path = std::path::Path::new(&new_path).join("config.json");
            if config_path.exists() {
                if let Ok(content) = std::fs::read_to_string(&config_path) {
                    if let Ok(mut config) = serde_json::from_str::<serde_json::Value>(&content) {
                        if let Some(obj) = config.as_object_mut() {
                            obj.insert("name".to_string(), serde_json::Value::String(new_name.clone()));
                            if let Ok(updated) = serde_json::to_string_pretty(&config) {
                                let _ = std::fs::write(&config_path, updated);
                            }
                        }
                    }
                }
            }
            Json(json!({ "success": true, "old_name": name, "new_name": new_name })).into_response()
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("Failed to rename module: {}", e) })),
        )
            .into_response(),
    }
}

async fn stream_job(
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> Result<Sse<std::pin::Pin<Box<dyn tokio_stream::Stream<Item = Result<Event, Infallible>> + Send>>>, (StatusCode, &'static str)> {
    let job = mgr.get_job(&id);
    if job.is_none() {
        return Err((StatusCode::NOT_FOUND, "Job not found"));
    }

    match mgr.subscribe(&id).await {
        Some(rx) => {
            // Send any already-accumulated output first so late subscribers don't miss it
            let existing = job.as_ref().map(|j| j.output.clone()).unwrap_or_default();
            let initial = if !existing.is_empty() {
                vec![Ok::<_, Infallible>(Event::default().data(existing))]
            } else {
                vec![]
            };
            let live = BroadcastStream::new(rx).filter_map(|result| {
                match result {
                    Ok(text) => Some(Ok::<_, Infallible>(Event::default().data(text))),
                    Err(_) => None,
                }
            });
            let stream = tokio_stream::iter(initial).chain(live);
            let pinned: std::pin::Pin<Box<dyn tokio_stream::Stream<Item = Result<Event, Infallible>> + Send>> = Box::pin(stream);
            Ok(Sse::new(pinned).keep_alive(KeepAlive::default()))
        }
        None => {
            let job = job.unwrap();
            let stream = tokio_stream::once(Ok::<_, Infallible>(
                Event::default().data(job.output).event("complete"),
            ));
            let pinned: std::pin::Pin<Box<dyn tokio_stream::Stream<Item = Result<Event, Infallible>> + Send>> = Box::pin(stream);
            Ok(Sse::new(pinned).keep_alive(KeepAlive::default()))
        }
    }
}

#[derive(Deserialize)]
struct ContentQuery {
    path: String,
}

async fn file_content(
    headers: axum::http::HeaderMap,
    Query(params): Query<ContentQuery>,
) -> impl IntoResponse {
    let caller = match userspace::caller(&headers) {
        Ok(a) => a,
        Err(e) => return (StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response(),
    };
    let resolved = match userspace::resolve_path(&caller, &params.path) {
        Ok(p) => p,
        Err(e) => return (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response(),
    };
    let file_path = resolved.as_path();

    if !file_path.exists() || !file_path.is_file() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "File not found" })),
        )
            .into_response();
    }

    match std::fs::read_to_string(file_path) {
        Ok(content) => (
            StatusCode::OK,
            Json(json!({ "content": content, "path": params.path })),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("Failed to read file: {}", e) })),
        )
            .into_response(),
    }
}

async fn file_raw(
    headers: axum::http::HeaderMap,
    Query(params): Query<ContentQuery>,
) -> impl IntoResponse {
    let caller = match userspace::caller(&headers) {
        Ok(a) => a,
        Err(e) => return (StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response(),
    };
    let resolved = match userspace::resolve_path(&caller, &params.path) {
        Ok(p) => p,
        Err(e) => return (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response(),
    };
    let file_path = resolved.as_path();

    if !file_path.exists() || !file_path.is_file() {
        return (StatusCode::NOT_FOUND, "Not found").into_response();
    }

    let content_type = match file_path.extension().and_then(|e| e.to_str()) {
        Some("png") => "image/png",
        Some("jpg") | Some("jpeg") => "image/jpeg",
        Some("gif") => "image/gif",
        Some("webp") => "image/webp",
        Some("svg") => "image/svg+xml",
        _ => "application/octet-stream",
    };

    match std::fs::read(file_path) {
        Ok(bytes) => (
            StatusCode::OK,
            [(axum::http::header::CONTENT_TYPE, content_type)],
            bytes,
        )
            .into_response(),
        Err(_) => (StatusCode::INTERNAL_SERVER_ERROR, "Failed to read file").into_response(),
    }
}

#[derive(Deserialize)]
struct SearchQuery {
    path: String,
    query: String,
}

async fn file_search(
    headers: axum::http::HeaderMap,
    Query(params): Query<SearchQuery>,
) -> impl IntoResponse {
    let caller = match userspace::caller(&headers) {
        Ok(a) => a,
        Err(e) => return Json(json!({ "results": [], "error": e })),
    };
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let resolved_pb = match userspace::resolve_path(&caller, &params.path) {
        Ok(p) => p,
        Err(e) => return Json(json!({ "results": [], "error": e })),
    };
    let dir_path = resolved_pb.as_path();

    if !dir_path.is_dir() {
        return Json(json!({ "results": [], "error": "Directory not found" }));
    }

    let query = params.query.to_lowercase();
    let mut results = Vec::new();

    fn search_recursive(
        dir: &std::path::Path,
        query: &str,
        home: &str,
        results: &mut Vec<serde_json::Value>,
        depth: usize,
    ) {
        if depth > 10 || results.len() > 100 {
            return;
        }
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || name == "node_modules" || name == "__pycache__" || name == "target" {
                continue;
            }
            if path.is_file() && name.to_lowercase().contains(query) {
                let full = path.to_string_lossy().to_string();
                let display = full.replacen(home, "~", 1);
                results.push(json!({
                    "filename": name,
                    "path": display,
                    "matches": 1,
                }));
            } else if path.is_dir() {
                search_recursive(&path, query, home, results, depth + 1);
            }
        }
    }

    search_recursive(dir_path, &query, &home, &mut results, 0);
    Json(json!({ "results": results }))
}

/// Get version changelog from ~/.mod/claude/changelog.json
async fn get_changelog(Query(params): Query<ChangelogQuery>) -> impl IntoResponse {
    let home = dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp"));
    let changelog_path = home.join(".mod").join("claude").join("changelog.json");

    if !changelog_path.exists() {
        return Json(json!({ "changelog": [], "count": 0 }));
    }

    match std::fs::read_to_string(&changelog_path) {
        Ok(content) => {
            match serde_json::from_str::<Vec<serde_json::Value>>(&content) {
                Ok(mut entries) => {
                    entries.reverse(); // newest first
                    let total = entries.len();
                    if let Some(limit) = params.limit {
                        entries.truncate(limit);
                    }
                    Json(json!({ "changelog": entries, "count": total }))
                }
                Err(e) => Json(json!({ "changelog": [], "count": 0, "error": format!("Invalid JSON: {}", e) })),
            }
        }
        Err(e) => Json(json!({ "changelog": [], "count": 0, "error": format!("Failed to read: {}", e) })),
    }
}

#[derive(Deserialize)]
struct ChangelogQuery {
    limit: Option<usize>,
}

/// Get a specific version entry from the changelog by version string
async fn get_version(Path(version): Path<String>) -> impl IntoResponse {
    let home = dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp"));
    let changelog_path = home.join(".mod").join("claude").join("changelog.json");

    if !changelog_path.exists() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "No changelog found" })),
        ).into_response();
    }

    match std::fs::read_to_string(&changelog_path) {
        Ok(content) => {
            match serde_json::from_str::<Vec<serde_json::Value>>(&content) {
                Ok(entries) => {
                    for entry in &entries {
                        if entry.get("version").and_then(|v| v.as_str()) == Some(&version) {
                            return (StatusCode::OK, Json(json!({
                                "version": entry,
                                "gateway": format!("https://ipfs.io/ipfs/{}", entry.get("cid").and_then(|v| v.as_str()).unwrap_or("")),
                            }))).into_response();
                        }
                    }
                    (
                        StatusCode::NOT_FOUND,
                        Json(json!({ "error": format!("Version '{}' not found", version) })),
                    ).into_response()
                }
                Err(e) => (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({ "error": format!("Invalid JSON: {}", e) })),
                ).into_response(),
            }
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("Failed to read changelog: {}", e) })),
        ).into_response(),
    }
}

#[derive(Deserialize)]
struct GrepQuery {
    path: String,
    query: String,
    #[serde(default)]
    caseSensitive: bool,
    #[serde(default)]
    regex: bool,
}

async fn file_grep(
    headers: axum::http::HeaderMap,
    Query(params): Query<GrepQuery>,
) -> impl IntoResponse {
    let caller = match userspace::caller(&headers) {
        Ok(a) => a,
        Err(e) => return Json(json!({ "matches": [], "error": e })),
    };
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let resolved_pb = match userspace::resolve_path(&caller, &params.path) {
        Ok(p) => p,
        Err(e) => return Json(json!({ "matches": [], "error": e })),
    };
    let dir_path = resolved_pb.as_path();

    if !dir_path.is_dir() {
        return Json(json!({ "matches": [], "error": "Directory not found" }));
    }

    let mut matches = Vec::new();
    let query = if params.caseSensitive {
        params.query.clone()
    } else {
        params.query.to_lowercase()
    };

    fn grep_recursive(
        dir: &std::path::Path,
        query: &str,
        case_sensitive: bool,
        home: &str,
        matches: &mut Vec<serde_json::Value>,
        depth: usize,
    ) {
        if depth > 10 || matches.len() > 200 {
            return;
        }
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || name == "node_modules" || name == "__pycache__" || name == "target" {
                continue;
            }
            if path.is_file() {
                if let Ok(content) = std::fs::read_to_string(&path) {
                    for (line_num, line) in content.lines().enumerate() {
                        let search_line = if case_sensitive {
                            line.to_string()
                        } else {
                            line.to_lowercase()
                        };
                        if let Some(pos) = search_line.find(query) {
                            let full = path.to_string_lossy().to_string();
                            let display = full.replacen(home, "~", 1);
                            matches.push(json!({
                                "filename": name,
                                "path": display,
                                "line": line_num + 1,
                                "content": line.trim(),
                                "matchStart": pos,
                                "matchEnd": pos + query.len(),
                            }));
                            if matches.len() >= 200 {
                                return;
                            }
                        }
                    }
                }
            } else if path.is_dir() {
                grep_recursive(&path, query, case_sensitive, home, matches, depth + 1);
            }
        }
    }

    grep_recursive(dir_path, &query, params.caseSensitive, &home, &mut matches, 0);
    Json(json!({ "matches": matches }))
}

// ── File Write ───────────────────────────────────────────────────────

#[derive(Deserialize)]
struct WriteBody {
    path: String,
    content: String,
}

async fn file_write(
    headers: axum::http::HeaderMap,
    Json(body): Json<WriteBody>,
) -> impl IntoResponse {
    // Auth: bearer required (or local-mode skip). Owner = wider write
    // surface (~/mod/), non-owner = sandboxed under their workspace.
    let caller = match userspace::caller(&headers) {
        Ok(a) => a,
        Err(e) => return (StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response(),
    };
    // resolve_path is the single boundary chokepoint: it confines the owner to
    // the module tree (~/mod/mod), each peer to ~/.mod/peers/<addr>, and leaves
    // local-mode (empty caller, trusted CLI) on the host. A path that escapes the
    // caller's role-root already returned FORBIDDEN above, so no further gate is
    // needed here. Destructive ops (delete/rename/restore/kill) still require a
    // fresh owner sudo signature elsewhere — only file edits flow through here.
    let resolved_pb = match userspace::resolve_path(&caller, &body.path) {
        Ok(p) => p,
        Err(e) => return (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response(),
    };
    let file_path = resolved_pb.as_path();

    // Create parent dirs if needed
    if let Some(p) = file_path.parent() {
        if !p.exists() {
            if let Err(e) = std::fs::create_dir_all(p) {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({ "error": format!("Failed to create directories: {}", e) })),
                )
                    .into_response();
            }
        }
    }

    match std::fs::write(file_path, &body.content) {
        Ok(_) => (
            StatusCode::OK,
            Json(json!({ "ok": true, "path": body.path })),
        )
            .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("Failed to write file: {}", e) })),
        )
            .into_response(),
    }
}

// ── Kill Process (owner-only) ────────────────────────────────────────

#[derive(Deserialize)]
struct KillRequest {
    pid: Option<u32>,
    port: Option<u16>,
    signal: Option<String>, // "SIGTERM" or "SIGKILL", default SIGKILL
}

async fn kill_process(
    headers: axum::http::HeaderMap,
    Json(body): Json<KillRequest>,
) -> impl IntoResponse {
    // Owner-only: extract address and verify ownership
    let auth_header = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    let local_mode = std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1";
    if !local_mode {
        match auth::extract_address_from_header(auth_header) {
            Ok(addr) if auth::is_owner(&addr) => {}
            Ok(_) => {
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({ "error": "Owner-only: only the host can kill processes" })),
                )
                    .into_response();
            }
            Err(e) => {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(json!({ "error": e })),
                )
                    .into_response();
            }
        }

        // Killing host processes is a privileged system op — require a fresh sudo
        // signature bound to the exact pid/port target.
        let target = match (body.pid, body.port) {
            (Some(pid), _) => format!("pid:{}", pid),
            (None, Some(port)) => format!("port:{}", port),
            _ => "unspecified".to_string(),
        };
        if let Some(denied) = sudo_gate(&headers, "kill", &target) {
            return denied;
        }
    }

    let sig = match body.signal.as_deref() {
        Some("SIGTERM") | Some("sigterm") | Some("term") => libc::SIGTERM,
        _ => libc::SIGKILL,
    };
    let sig_name = if sig == libc::SIGTERM { "SIGTERM" } else { "SIGKILL" };

    // Resolve PIDs: either direct PID or find by port
    let pids: Vec<u32> = if let Some(pid) = body.pid {
        vec![pid]
    } else if let Some(port) = body.port {
        match find_pids_by_port(port) {
            Ok(p) => p,
            Err(e) => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({ "error": e })),
                )
                    .into_response();
            }
        }
    } else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "Provide 'pid' or 'port'" })),
        )
            .into_response();
    };

    if pids.is_empty() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "No process found", "killed": [] })),
        )
            .into_response();
    }

    let mut killed = Vec::new();
    let mut errors = Vec::new();

    for pid in &pids {
        let rc = unsafe { libc::kill(*pid as i32, sig) };
        if rc == 0 {
            killed.push(*pid);
        } else {
            let err = std::io::Error::last_os_error();
            errors.push(format!("pid {}: {}", pid, err));
        }
    }

    (
        StatusCode::OK,
        Json(json!({
            "killed": killed,
            "signal": sig_name,
            "errors": errors,
        })),
    )
        .into_response()
}

/// Use lsof to find PIDs listening on a given port
fn find_pids_by_port(port: u16) -> Result<Vec<u32>, String> {
    let output = std::process::Command::new("lsof")
        .args(["-ti", &format!(":{}", port)])
        .output()
        .map_err(|e| format!("lsof failed: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let pids: Vec<u32> = stdout
        .lines()
        .filter_map(|line| line.trim().parse::<u32>().ok())
        .collect();
    Ok(pids)
}

// ── Module process management ────────────────────────────────────────
//
// The owner drives another module's lifecycle (status/stop/start/restart) here.
// The actual supervisor differs per module — pm2, systemd, or bare processes on
// a port — so the work is delegated to a pluggable backend (`process.rs`) chosen
// from MOD_PM / the module's config `process_manager` / auto-detection. The HTTP
// shape below is identical regardless of backend. Going through the real
// supervisor is what makes "stop stays stopped" and "restart" behave correctly
// (a raw port kill is just undone by pm2/systemd autorestart). Modules that ship
// a flake.nix/shell.nix have their self-launched commands wrapped in nix.

#[derive(Deserialize)]
struct ProcessRequest {
    /// "status" | "stop" | "start" | "restart". Defaults to "status".
    action: Option<String>,
    /// Optional filter to a single service, e.g. "api" or "app". When set, only
    /// processes whose name carries that token (`<mod>-api`, `<mod>-app`) are
    /// acted on — lets the UI restart one service without touching the other.
    target: Option<String>,
}

/// Read a module's config.json (handles the nested `<name>/config.json` layout).
fn read_module_config(module_path: &std::path::Path, name: &str) -> serde_json::Value {
    for p in [module_path.join("config.json"), module_path.join(name).join("config.json")] {
        if let Ok(s) = std::fs::read_to_string(&p) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
                return v;
            }
        }
    }
    json!({})
}

/// Apply the optional `target` (api/app) filter to a backend's process list.
fn filter_by_target(procs: Vec<process::Proc>, target: &Option<String>) -> Vec<process::Proc> {
    match target.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        None => procs,
        Some(t) => {
            let tok = t.to_lowercase();
            procs
                .into_iter()
                .filter(|p| {
                    let n = p.name.to_lowercase();
                    n.ends_with(&format!("-{tok}")) || n.ends_with(&tok) || n.contains(&tok)
                })
                .collect()
        }
    }
}

/// Owner-only: control another module's processes (status/stop/start/restart)
/// through its configured process-manager backend (pm2 / systemd / generic).
async fn module_process(
    headers: axum::http::HeaderMap,
    State(_mgr): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<ProcessRequest>,
) -> impl IntoResponse {
    let action = body.action.clone().unwrap_or_else(|| "status".to_string()).to_lowercase();
    if !matches!(action.as_str(), "status" | "stop" | "start" | "restart" | "reap") {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "action must be one of: status, stop, start, restart, reap" })),
        )
            .into_response();
    }

    // Owner-only (skip in local mode — host CLI is trusted).
    if !local_mode() {
        let auth_header = headers
            .get(axum::http::header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        match auth::extract_address_from_header(auth_header) {
            Ok(addr) if auth::is_owner(&addr) => {}
            Ok(_) => {
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({ "error": "Owner-only: only the host can manage module processes" })),
                )
                    .into_response();
            }
            Err(e) => {
                return (StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response();
            }
        }
        // Start/stop/restart of any module is owner-bearer only — no sudo
        // signature. Destructive ops (delete/kill/rename/restore) keep the gate.
    }

    // Resolve the module directory under orbit/ or core/.
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let candidates = [
        format!("{}/mod/mod/orbit/{}", home, name),
        format!("{}/mod/mod/core/{}", home, name),
    ];
    let module_path = match candidates.iter().map(std::path::PathBuf::from).find(|p| p.is_dir()) {
        Some(p) => p,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": format!("Module '{}' not found", name) })),
            )
                .into_response();
        }
    };

    let config = read_module_config(&module_path, &name);
    let backend = process::select(&module_path, &config, &name);

    let procs = match process::list(backend, &module_path, &name, &config) {
        Ok(p) => filter_by_target(p, &body.target),
        Err(e) => {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
        }
    };

    let meta = json!({
        "backend": backend.as_str(),
        "nix_env": process::has_nix_env(&module_path),
    });

    // Canonical pids = the legit backend-managed processes; their ports are
    // protected from reaping. The module's declared api/app ports are ALSO
    // protected inside find_orphans (via generic_services), so even with a
    // target=api filter the app's port is never reaped.
    let canonical_pids = |procs: &[process::Proc]| -> Vec<i64> {
        procs.iter().filter_map(|p| p.pid).collect()
    };

    // ── status: report what's running + any orphan processes ──
    if action == "status" {
        let running = procs.iter().any(|p| p.status == "online");
        let orphans = process::find_orphans(&module_path, &name, &config, &canonical_pids(&procs));
        return Json(json!({
            "module": name,
            "action": "status",
            "backend": backend.as_str(),
            "nix_env": meta["nix_env"],
            "running": running,
            "processes": procs.iter().map(process::Proc::to_json).collect::<Vec<_>>(),
            "orphans": orphans.iter().map(process::Orphan::to_json).collect::<Vec<_>>(),
        }))
        .into_response();
    }

    // ── reap: kill stale duplicates serving from the module dir, no lifecycle change ──
    if action == "reap" {
        let (reaped, log) = process::reap_orphans(&module_path, &name, &config, &canonical_pids(&procs));
        return Json(json!({
            "module": name,
            "action": "reap",
            "backend": backend.as_str(),
            "ok": true,
            "output": log,
            "reaped": reaped.iter().map(process::Orphan::to_json).collect::<Vec<_>>(),
            "processes": procs.iter().map(process::Proc::to_json).collect::<Vec<_>>(),
        }))
        .into_response();
    }

    let (ok, output) = process::act(backend, &action, &procs, &module_path, &name, &config);

    // Re-read state so the caller sees the result of their action.
    let after = process::list(backend, &module_path, &name, &config)
        .map(|p| filter_by_target(p, &body.target))
        .unwrap_or_default();

    // Auto-reap on start/restart: once the canonical process is (re)launched, kill
    // any stale duplicate squatting a non-canonical port so the live app/api is
    // unambiguously the one built from the edited source. (Not on stop — leaving a
    // module fully down shouldn't trigger collateral kills.)
    let reaped = if matches!(action.as_str(), "start" | "restart") {
        let (reaped, _log) = process::reap_orphans(&module_path, &name, &config, &canonical_pids(&after));
        reaped
    } else {
        Vec::new()
    };

    let status = if ok { StatusCode::OK } else { StatusCode::INTERNAL_SERVER_ERROR };
    (
        status,
        Json(json!({
            "module": name,
            "action": action,
            "backend": backend.as_str(),
            "nix_env": meta["nix_env"],
            "ok": ok,
            "output": output.trim(),
            "processes": after.iter().map(process::Proc::to_json).collect::<Vec<_>>(),
            "reaped": reaped.iter().map(process::Orphan::to_json).collect::<Vec<_>>(),
        })),
    )
        .into_response()
}

#[derive(Debug, Clone, serde::Deserialize)]
struct LogsRequest {
    /// How many trailing lines of each stream to return. Defaults to 200, capped
    /// at 2000 so a runaway request can't tail an unbounded file.
    lines: Option<usize>,
}

/// Tail the pm2 (or systemd) logs for a module's processes. Read-only, but logs
/// can leak secrets, so it is owner-gated exactly like `module_process`.
async fn module_logs(
    headers: axum::http::HeaderMap,
    State(_mgr): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<LogsRequest>,
) -> impl IntoResponse {
    if !local_mode() {
        let auth_header = headers
            .get(axum::http::header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        match auth::extract_address_from_header(auth_header) {
            Ok(addr) if auth::is_owner(&addr) => {}
            Ok(_) => {
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({ "error": "Owner-only: only the host can read module logs" })),
                )
                    .into_response();
            }
            Err(e) => {
                return (StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response();
            }
        }
    }

    let lines = body.lines.unwrap_or(200).min(2000);

    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let candidates = [
        format!("{}/mod/mod/orbit/{}", home, name),
        format!("{}/mod/mod/core/{}", home, name),
    ];
    let module_path = match candidates.iter().map(std::path::PathBuf::from).find(|p| p.is_dir()) {
        Some(p) => p,
        None => {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": format!("Module '{}' not found", name) })),
            )
                .into_response();
        }
    };

    let config = read_module_config(&module_path, &name);
    let backend = process::select(&module_path, &config, &name);

    match process::logs(backend, &module_path, &name, lines) {
        Ok(map) => Json(serde_json::Value::Object(map)).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response(),
    }
}

// ── Snapshots / Versions / Fork / Restore ────────────────────────────
//
// Content-addressed via the Store enum (default: LocalFs). Any future backend
// (ipfs/bitstore/dstore) is a one-line variant + match arm; nothing else moves.
// Versions log lives at ~/.mod/claude/versions/{module}.json — append-only.
//
// Each change is also pushed to the mod-protocol api module (FastAPI on :8000)
// via /api/reg — that gives us a git-like linked list of registry CIDs (each
// entry has a `prev` pointer to the previous one). Rollback = restoring an old
// localfs CID then re-registering it so the api module's "latest" pointer
// moves backwards along the chain.

/// The mod-protocol api module (FastAPI). Under the old docker deployment it
/// lived at host.docker.internal; under pm2-on-host that name doesn't resolve
/// and every registry push silently failed (= "no cid" cards). Default to
/// loopback, keep an env override for containerized runs.
fn api_module_url() -> String {
    std::env::var("CLAUDE_API_MODULE_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8000".to_string())
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub(crate) struct ApiRegResult {
    pub(crate) cid: Option<String>,
    pub(crate) prev: Option<String>,
    #[allow(dead_code)]
    key: Option<String>,
    #[allow(dead_code)]
    name: Option<String>,
    #[allow(dead_code)]
    updated: Option<f64>,
}

/// Push a change through the mod-protocol api module. `comment` distinguishes
/// snapshots, restores, forks etc. — useful when browsing registry history.
/// 30s timeout: api/reg locally takes ~15-20s for non-cached snapshots.
pub(crate) async fn mod_protocol_register(
    module: &str,
    comment: &str,
) -> Result<ApiRegResult, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| format!("http client: {e}"))?;
    let resp = client
        .post(format!("{}/api/reg", api_module_url()))
        .json(&serde_json::json!({ "mod": module, "comment": comment }))
        .send()
        .await
        .map_err(|e| format!("api/reg POST failed: {e}"))?;
    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("api/reg parse: {e}"))?;
    if let Some(err) = body.get("error").and_then(|v| v.as_str()) {
        return Err(format!("api/reg error: {err}"));
    }
    let result = body
        .get("result")
        .ok_or_else(|| "api/reg missing 'result'".to_string())?;
    serde_json::from_value::<ApiRegResult>(result.clone())
        .map_err(|e| format!("api/reg shape: {e}"))
}

use crate::snapshots::module_root_for;

#[derive(Deserialize)]
struct SnapshotBody {
    #[serde(default)]
    message: String,
}

async fn snapshot_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<SnapshotBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let local_mode = std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1";
    if caller.is_empty() && !local_mode {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({ "error": "auth required" })),
        )
            .into_response();
    }
    let Some(root) = module_root_for(&name) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    };
    let store = default_store();
    let (cid, manifest) = match snapshot_dir(&root, &store) {
        Ok(t) => t,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": e })),
            )
                .into_response();
        }
    };
    let history = read_versions(&name);
    let parent = history.last().map(|v| v.cid.clone());
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    // Push through the mod-protocol api module so this change is also a node
    // in the global registry chain. Failure non-fatal — local snapshot still
    // succeeds even if api is down; UI flags it.
    let reg_comment = if body.message.is_empty() {
        format!("snapshot: cid={}", &cid[..16])
    } else {
        format!("snapshot: {}", body.message)
    };
    let (registry_cid, registry_prev, registry_err) =
        match mod_protocol_register(&name, &reg_comment).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };

    let record = VersionRecord {
        cid: cid.clone(),
        message: body.message.clone(),
        author: caller.clone(),
        timestamp: ts,
        parent: parent.clone(),
        registry_cid: registry_cid.clone(),
        registry_prev: registry_prev.clone(),
        action: Some("snapshot".to_string()),
        job_id: None,
    };
    if let Err(e) = append_version(&name, record) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response();
    }
    (
        StatusCode::CREATED,
        Json(json!({
            "ok": true,
            "module": name,
            "cid": cid,
            "store": store.name(),
            "file_count": manifest.files.len(),
            "parent": parent,
            "author": caller,
            "registry_cid": registry_cid,
            "registry_prev": registry_prev,
            "registry_error": registry_err,
        })),
    )
        .into_response()
}

async fn list_module_versions(Path(name): Path<String>) -> impl IntoResponse {
    let history = read_versions(&name);
    Json(json!({
        "module": name,
        "count": history.len(),
        "versions": history,
    }))
}

/// Fetch the global mod-protocol api registry entry for a module so the UI
/// can show "the registry currently points at CID X" next to the local log.
async fn module_registry(Path(name): Path<String>) -> impl IntoResponse {
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": format!("http client: {e}") })),
            )
                .into_response();
        }
    };
    let resp = client
        .post(format!("{}/api/mod", api_module_url()))
        .json(&json!({ "key": name }))
        .send()
        .await;
    match resp {
        Ok(r) => match r.json::<serde_json::Value>().await {
            Ok(body) => (StatusCode::OK, Json(body)).into_response(),
            Err(e) => (
                StatusCode::BAD_GATEWAY,
                Json(json!({ "error": format!("parse api/mod: {e}") })),
            )
                .into_response(),
        },
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({
                "error": format!("api module unreachable at {}: {e}", api_module_url()),
                "hint": "is `m api/serve` running on port 8000?"
            })),
        )
            .into_response(),
    }
}

#[derive(Deserialize)]
struct ForkBody {
    cid: String,
    #[serde(default)]
    target_name: Option<String>,
}

async fn fork_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<ForkBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let local_mode = std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1";
    if caller.is_empty() && !local_mode {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({ "error": "auth required to fork" })),
        )
            .into_response();
    }
    // Forking materializes a new module directory — module creation is
    // owner-only, so peers cannot fork (not even into the portal sandbox).
    if !local_mode && !auth::is_owner(&caller) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: forking creates a new module; only the configured owner may do it" })),
        )
            .into_response();
    }
    let owner_for_path = if caller.is_empty() {
        "local".to_string()
    } else {
        caller.to_lowercase()
    };
    let target_name = body.target_name.unwrap_or_else(|| name.clone());
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let target = std::path::PathBuf::from(format!(
        "{home}/mod/mod/orbit/portal/{owner_for_path}/{target_name}"
    ));
    if target.exists() {
        return (
            StatusCode::CONFLICT,
            Json(json!({
                "error": format!("fork target already exists: {}", target.display())
            })),
        )
            .into_response();
    }
    let store = default_store();
    let written = match restore_into(&target, &body.cid, &store) {
        Ok(n) => n,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": e })),
            )
                .into_response();
        }
    };
    // Seed a fork version record so the new module starts with history
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let target_module = format!("portal/{owner_for_path}/{target_name}");
    let fork_msg = format!("forked from {name}@{}", &body.cid[..16]);
    let (registry_cid, registry_prev, registry_err) =
        match mod_protocol_register(&target_module, &fork_msg).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };
    let fork_record = VersionRecord {
        cid: body.cid.clone(),
        message: fork_msg,
        author: caller.clone(),
        timestamp: ts,
        parent: None,
        registry_cid: registry_cid.clone(),
        registry_prev: registry_prev.clone(),
        action: Some("fork".to_string()),
        job_id: None,
    };
    let _ = append_version(&target_module, fork_record);
    (
        StatusCode::CREATED,
        Json(json!({
            "ok": true,
            "from_module": name,
            "from_cid": body.cid,
            "target_module": target_module,
            "target_path": target.display().to_string(),
            "file_count": written,
            "store": store.name(),
            "registry_cid": registry_cid,
            "registry_prev": registry_prev,
            "registry_error": registry_err,
        })),
    )
        .into_response()
}

#[derive(Deserialize)]
struct RestoreBody {
    cid: String,
}

async fn restore_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<RestoreBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let local_mode = std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1";
    if caller.is_empty() && !local_mode {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({ "error": "auth required to restore" })),
        )
            .into_response();
    }
    // Restore overwrites a module's files from an arbitrary snapshot CID — a
    // powerful tampering primitive. Only the system owner (or local mode) may do
    // it, and never a plain authenticated user.
    if !local_mode && !auth::is_owner(&caller) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: restoring a module requires the configured owner" })),
        )
            .into_response();
    }
    let Some(root) = module_root_for(&name) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    };
    // Restoring overwrites a module's files from a snapshot — privileged when it
    // targets a module other than claude itself, so require a fresh sudo signature.
    if name != "claude" {
        if let Some(denied) = sudo_gate(&headers, "restore", &name) {
            return denied;
        }
    }
    // Auto-snapshot current state first so the rollback is itself reversible.
    let store = default_store();
    if let Ok((auto_cid, _)) = snapshot_dir(&root, &store) {
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let auto_msg = format!("auto-snapshot before rollback to {}", &body.cid[..16]);
        let (rcid, rprev, _) = match mod_protocol_register(&name, &auto_msg).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };
        let _ = append_version(
            &name,
            VersionRecord {
                cid: auto_cid,
                message: auto_msg,
                author: caller.clone(),
                timestamp: ts,
                parent: None,
                registry_cid: rcid,
                registry_prev: rprev,
                action: Some("auto-snapshot".to_string()),
                job_id: None,
            },
        );
    }
    let written = match restore_into(&root, &body.cid, &store) {
        Ok(n) => n,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": e })),
            )
                .into_response();
        }
    };
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Re-register through the api module so the global registry's "latest"
    // pointer moves backwards along the chain to reflect the rollback.
    let restore_msg = format!("rollback to {}", &body.cid[..16]);
    let (registry_cid, registry_prev, registry_err) =
        match mod_protocol_register(&name, &restore_msg).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };
    let _ = append_version(
        &name,
        VersionRecord {
            cid: body.cid.clone(),
            message: restore_msg,
            author: caller.clone(),
            timestamp: ts,
            parent: None,
            registry_cid: registry_cid.clone(),
            registry_prev: registry_prev.clone(),
            action: Some("restore".to_string()),
            job_id: None,
        },
    );
    (
        StatusCode::OK,
        Json(json!({
            "ok": true,
            "module": name,
            "restored_to": body.cid,
            "file_count": written,
            "store": store.name(),
            "registry_cid": registry_cid,
            "registry_prev": registry_prev,
            "registry_error": registry_err,
        })),
    )
        .into_response()
}

// ── Import a brand-new module from a GitHub repo or a snapshot CID ──────
//
// Two deterministic, no-AI paths for getting code onto disk as a fresh
// module (contrast with the agent-job "BUILD" flow which asks Claude to
// write files):
//   • source="github": shallow `git clone` the repo into the module dir,
//     then strip `.git` so it joins the mono-repo cleanly.
//   • source="cid": materialize a claude snapshot CID out of the shared
//     blob store via `restore_into` — the same primitive `fork_module`
//     uses, just into a user-named module instead of a portal fork.
//
// Module creation is owner-only: only the configured owner (or local mode)
// may import, and modules land straight in orbit/ or core/. Peers keep
// read access and workspace jobs but cannot mint new modules.
#[derive(Deserialize)]
struct ImportBody {
    source: String,
    name: String,
    #[serde(default)]
    url: Option<String>,
    #[serde(default)]
    cid: Option<String>,
    #[serde(default)]
    category: Option<String>,
}

// A module name becomes a directory and a registry key, so keep it to a
// conservative slug — no separators, traversal, or shell-hostile chars.
fn valid_module_slug(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

// Accept http(s) git URLs. The leading scheme guard also stops a
// `-`-prefixed value from being read by git as a flag (args are passed as
// a vec, so there's no shell — only argv injection to worry about).
fn is_safe_clone_url(u: &str) -> bool {
    let u = u.trim();
    if !(u.starts_with("https://") || u.starts_with("http://")) {
        return false;
    }
    if u.len() > 512 || u.chars().any(|c| c.is_control() || c.is_whitespace()) {
        return false;
    }
    true
}

async fn import_module(
    headers: axum::http::HeaderMap,
    Json(body): Json<ImportBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let local_mode = std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1";
    if caller.is_empty() && !local_mode {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({ "error": "auth required to import a module" })),
        )
            .into_response();
    }

    let source = body.source.trim().to_lowercase();
    let name = body.name.trim().to_string();
    if !valid_module_slug(&name) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "module name must be 1–64 chars of [a-zA-Z0-9_-]" })),
        )
            .into_response();
    }

    // Creation is owner-only — reject before touching the filesystem so a
    // peer token can never grow the module tree (not even into a sandbox).
    let is_owner = local_mode || auth::is_owner(&caller);
    if !is_owner {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: module creation is restricted to the configured owner" })),
        )
            .into_response();
    }
    let category = body
        .category
        .as_deref()
        .unwrap_or("orbit")
        .trim()
        .to_lowercase();
    if category != "orbit" && category != "core" {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "category must be 'orbit' or 'core'" })),
        )
            .into_response();
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let (module_id, dest) = (
        name.clone(),
        std::path::PathBuf::from(format!("{home}/mod/mod/{category}/{name}")),
    );

    if dest.exists() {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("module already exists at {}", dest.display()) })),
        )
            .into_response();
    }

    // ── source-specific materialization ────────────────────────────────
    let (file_count, origin): (usize, serde_json::Value) = match source.as_str() {
        "github" | "git" => {
            let url = body.url.clone().unwrap_or_default();
            if !is_safe_clone_url(&url) {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({ "error": "url must be an http(s) git URL" })),
                )
                    .into_response();
            }
            if let Some(parent) = dest.parent() {
                if let Err(e) = std::fs::create_dir_all(parent) {
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({ "error": format!("mkdir parent: {e}") })),
                    )
                        .into_response();
                }
            }
            let dest_str = dest.to_string_lossy().to_string();
            let clone = tokio::time::timeout(
                std::time::Duration::from_secs(180),
                tokio::process::Command::new("git")
                    .env("GIT_TERMINAL_PROMPT", "0")
                    .args(["clone", "--depth", "1", url.trim(), &dest_str])
                    .output(),
            )
            .await;
            match clone {
                Ok(Ok(out)) if out.status.success() => {}
                Ok(Ok(out)) => {
                    let _ = std::fs::remove_dir_all(&dest);
                    let stderr = String::from_utf8_lossy(&out.stderr);
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(json!({ "error": format!("git clone failed: {}", stderr.trim()) })),
                    )
                        .into_response();
                }
                Ok(Err(e)) => {
                    let _ = std::fs::remove_dir_all(&dest);
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({ "error": format!("git not available: {e}") })),
                    )
                        .into_response();
                }
                Err(_) => {
                    let _ = std::fs::remove_dir_all(&dest);
                    return (
                        StatusCode::GATEWAY_TIMEOUT,
                        Json(json!({ "error": "git clone timed out after 180s" })),
                    )
                        .into_response();
                }
            }
            // Detach from the upstream repo so it lives inside the mono-repo.
            let _ = std::fs::remove_dir_all(dest.join(".git"));
            let count = std::fs::read_dir(&dest).map(|d| d.count()).unwrap_or(0);
            (count, json!({ "source": "github", "url": url }))
        }
        "cid" | "snapshot" => {
            let cid = body.cid.clone().unwrap_or_default();
            let store = default_store();
            match restore_into(&dest, &cid, &store) {
                Ok(n) => (n, json!({ "source": "cid", "cid": cid, "store": store.name() })),
                Err(e) => {
                    let _ = std::fs::remove_dir_all(&dest);
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(json!({
                            "error": format!("could not restore snapshot: {e}"),
                            "hint": "only snapshot CIDs present in the shared blob store can be imported",
                        })),
                    )
                        .into_response();
                }
            }
        }
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": format!("unknown source '{other}'; use 'github' or 'cid'") })),
            )
                .into_response();
        }
    };

    // Seed version history + the global registry so the freshly-imported
    // module shows up like any other (best-effort — a registry hiccup must
    // not fail an import whose files already landed).
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let store = default_store();
    let snap_cid = snapshot_dir(&dest, &store).ok().map(|(c, _)| c);
    let msg = format!("imported via {source}");
    let (registry_cid, registry_prev, registry_err) =
        match mod_protocol_register(&module_id, &msg).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };
    if let Some(cid) = snap_cid.clone() {
        let _ = append_version(
            &module_id,
            VersionRecord {
                cid,
                message: msg,
                author: caller.clone(),
                timestamp: ts,
                parent: None,
                registry_cid: registry_cid.clone(),
                registry_prev: registry_prev.clone(),
                action: Some("import".to_string()),
                job_id: None,
            },
        );
    }

    (
        StatusCode::CREATED,
        Json(json!({
            "ok": true,
            "module": module_id,
            "name": name,
            "path": dest.display().to_string(),
            "category": category,
            "file_count": file_count,
            "snapshot_cid": snap_cid,
            "registry_cid": registry_cid,
            "registry_prev": registry_prev,
            "registry_error": registry_err,
            "origin": origin,
        })),
    )
        .into_response()
}

// ══════════════════════ Merge Requests ══════════════════════════════
//
// CID-native contribution flow for modules:
//   1. POST /modules/:name/mr-fork        — any signed-in user pins the live
//      tree to a base CID and materializes it in THEIR sandboxed workspace
//      (~/.mod/peers/<addr>/forks/<name>). They edit it with the same
//      sandboxed jobs / file writes they already have.
//   2. POST /modules/:name/merge-requests — snapshot the fork to a head CID
//      and open an MR = {base_cid, head_cid} + review metadata. A raw
//      head_cid is also accepted, so proposals can arrive purely as CIDs.
//   3. comment / request_changes / approve / update — the review loop.
//   4. POST /merge-requests/:id/merge     — owner-approved AGENTIC MERGE:
//      base+head trees are staged on disk, the live tree auto-snapshotted,
//      and an agent job runs a three-way merge brief (conflicts precomputed
//      from manifests). The job's completion auto-snapshot mints the merged
//      version CID, which reconciles back onto the MR lazily on read.

/// Resolve the identity string an MR flow files things under: the lowercased
/// caller address, or "local" for the trusted host CLI (empty caller).
fn mr_identity(caller: &str) -> String {
    if caller.is_empty() {
        "local".to_string()
    } else {
        caller.to_lowercase()
    }
}

fn mr_fork_dir(caller: &str, module: &str) -> std::path::PathBuf {
    userspace::peer_root(&mr_identity(caller))
        .join("forks")
        .join(module)
}

/// 401 unless the request carries a valid bearer token (or local mode).
fn require_bearer(headers: &axum::http::HeaderMap) -> Result<String, axum::response::Response> {
    let caller = auth::extract_address_from_headers(headers).unwrap_or_default();
    if caller.is_empty() && !local_mode() {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(json!({ "error": "auth required" })),
        )
            .into_response());
    }
    Ok(caller)
}

fn mr_not_found(id: &str) -> axum::response::Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": format!("merge request '{id}' not found") })),
    )
        .into_response()
}

fn mr_not_found_module(name: &str) -> axum::response::Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": format!("module '{name}' not found") })),
    )
        .into_response()
}

/// If an MR is `merging`, fold the merge job's terminal state back into the
/// record: completed → merged (+ merged_cid from the job's auto-snapshot
/// version record), failed/cancelled → merge_failed. Lazy on read so no
/// background reconciler is needed.
fn reconcile_mr(mgr: &ClaudeJobManager, mut mr: merge::MergeRequest) -> merge::MergeRequest {
    if mr.status != "merging" {
        return mr;
    }
    let Some(job_id) = mr.merge_job_id.clone() else {
        mr.status = "merge_failed".to_string();
        let _ = merge::save_mr(&mr);
        return mr;
    };
    let Some(job) = mgr.get_job(&job_id) else {
        return mr;
    };
    match format!("{}", job.status).as_str() {
        "completed" => {
            mr.status = "merged".to_string();
            mr.merged_cid = read_versions(&mr.module)
                .iter()
                .rev()
                .find(|v| v.job_id.as_deref() == Some(job_id.as_str()))
                .map(|v| v.cid.clone());
            mr.updated_at = merge::now_ts();
            mr.comments.push(merge::MrComment {
                author: "agent".to_string(),
                body: match &mr.merged_cid {
                    Some(c) => format!("merge job {job_id} completed — merged tree cid {c}"),
                    None => format!("merge job {job_id} completed (no tree change recorded — see the job output)"),
                },
                action: Some("system".to_string()),
                timestamp: merge::now_ts(),
            });
            let _ = merge::save_mr(&mr);
        }
        "failed" | "cancelled" => {
            mr.status = "merge_failed".to_string();
            mr.updated_at = merge::now_ts();
            mr.comments.push(merge::MrComment {
                author: "agent".to_string(),
                body: format!("merge job {job_id} ended without completing — inspect the job and retry"),
                action: Some("system".to_string()),
                timestamp: merge::now_ts(),
            });
            let _ = merge::save_mr(&mr);
        }
        _ => {}
    }
    mr
}

#[derive(Deserialize, Default)]
struct MrForkBody {
    /// Discard an existing fork working copy and re-fork from the live tree.
    #[serde(default)]
    refresh: bool,
}

async fn mr_fork_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    body: Option<Json<MrForkBody>>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    if !valid_module_slug(&name) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "invalid module name" })),
        )
            .into_response();
    }
    let Some(root) = module_root_for(&name) else {
        return mr_not_found_module(&name);
    };
    let body = body.map(|Json(b)| b).unwrap_or_default();
    let fork_dir = mr_fork_dir(&caller, &name);
    if fork_dir.exists() {
        if body.refresh {
            if let Err(e) = std::fs::remove_dir_all(&fork_dir) {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({ "error": format!("clearing old fork: {e}") })),
                )
                    .into_response();
            }
        } else {
            return (
                StatusCode::CONFLICT,
                Json(json!({
                    "error": "you already have a fork of this module",
                    "fork_path": userspace::display_path(&caller, &fork_dir),
                    "hint": "keep editing it, or pass {\"refresh\": true} to discard it and re-fork from the live tree",
                })),
            )
                .into_response();
        }
    }
    // Pin the live tree: snapshot writes the blobs, the manifest CID is base.
    let store = default_store();
    let (base_cid, manifest) = match snapshot_dir(&root, &store) {
        Ok(t) => t,
        Err(e) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": format!("snapshotting live tree: {e}") })),
            )
                .into_response();
        }
    };
    if let Err(e) = restore_into(&fork_dir, &base_cid, &store) {
        let _ = std::fs::remove_dir_all(&fork_dir);
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("materializing fork: {e}") })),
        )
            .into_response();
    }
    let meta = merge::ForkMeta {
        module: name.clone(),
        base_cid: base_cid.clone(),
        author: mr_identity(&caller),
        forked_at: merge::now_ts(),
    };
    let _ = std::fs::write(
        fork_dir.join(merge::FORK_META_FILE),
        serde_json::to_vec_pretty(&meta).unwrap_or_default(),
    );
    (
        StatusCode::CREATED,
        Json(json!({
            "ok": true,
            "module": name,
            "base_cid": base_cid,
            "fork_path": userspace::display_path(&caller, &fork_dir),
            "file_count": manifest.files.len(),
            "next": "edit the fork (agent jobs / file writes in your workspace), then POST /modules/{name}/merge-requests",
        })),
    )
        .into_response()
}

#[derive(Deserialize)]
struct OpenMrBody {
    title: String,
    #[serde(default)]
    description: String,
    /// CID-native path: propose an already-snapshotted tree.
    #[serde(default)]
    head_cid: Option<String>,
    /// Override the recorded fork base (required if head_cid has no fork).
    #[serde(default)]
    base_cid: Option<String>,
    #[serde(default)]
    message: String,
}

async fn open_merge_request(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<OpenMrBody>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let title = body.title.trim().to_string();
    if title.is_empty() || title.len() > 200 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "title required (≤200 chars)" })),
        )
            .into_response();
    }
    if body.description.len() > 5000 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "description too long (≤5000 chars)" })),
        )
            .into_response();
    }
    let Some(root) = module_root_for(&name) else {
        return mr_not_found_module(&name);
    };
    let store = default_store();
    let author = mr_identity(&caller);
    let fork_dir = mr_fork_dir(&caller, &name);

    let (base_cid, head_cid, fork_path) = if let Some(head) = body.head_cid.clone() {
        // CID-native proposal: both trees must already be manifests in the store.
        if let Err(e) = merge::load_manifest(&store, &head) {
            return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response();
        }
        let base = match body.base_cid.clone() {
            Some(b) => {
                if let Err(e) = merge::load_manifest(&store, &b) {
                    return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response();
                }
                b
            }
            // No base stated: pin the live tree now — the diff is "what this
            // proposal changes relative to current upstream".
            None => match snapshot_dir(&root, &store) {
                Ok((c, _)) => c,
                Err(e) => {
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({ "error": format!("snapshotting live tree: {e}") })),
                    )
                        .into_response();
                }
            },
        };
        (base, head, None)
    } else {
        // Default path: snapshot the caller's fork working copy.
        if !fork_dir.is_dir() {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({
                    "error": "no fork found — POST /modules/{name}/mr-fork first, or pass head_cid",
                })),
            )
                .into_response();
        }
        let meta: Option<merge::ForkMeta> = std::fs::read_to_string(fork_dir.join(merge::FORK_META_FILE))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok());
        let base = match body.base_cid.clone().or(meta.map(|m| m.base_cid)) {
            Some(b) => b,
            None => {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({ "error": "fork has no recorded base — pass base_cid explicitly" })),
                )
                    .into_response();
            }
        };
        if let Err(e) = merge::load_manifest(&store, &base) {
            return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response();
        }
        let head = match snapshot_dir(&fork_dir, &store) {
            Ok((c, _)) => c,
            Err(e) => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(json!({ "error": format!("snapshotting fork: {e}") })),
                )
                    .into_response();
            }
        };
        (base, head, Some(userspace::display_path(&caller, &fork_dir)))
    };

    if base_cid == head_cid {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "no changes: head tree is identical to base" })),
        )
            .into_response();
    }

    let ts = merge::now_ts();
    let mr = merge::MergeRequest {
        id: merge::new_mr_id(),
        module: name,
        author,
        title,
        description: body.description,
        base_cid,
        head_cid: head_cid.clone(),
        revisions: vec![merge::MrRevision {
            head_cid,
            message: if body.message.is_empty() { "initial revision".into() } else { body.message },
            timestamp: ts,
        }],
        status: "open".to_string(),
        comments: vec![],
        created_at: ts,
        updated_at: ts,
        merge_job_id: None,
        merged_cid: None,
        fork_path,
    };
    if let Err(e) = merge::save_mr(&mr) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response();
    }
    (StatusCode::CREATED, Json(json!(mr))).into_response()
}

async fn list_all_mrs(State(mgr): State<AppState>) -> impl IntoResponse {
    let mrs: Vec<_> = merge::list_mrs(None)
        .into_iter()
        .map(|m| reconcile_mr(&mgr, m))
        .collect();
    Json(json!({ "count": mrs.len(), "merge_requests": mrs }))
}

async fn list_module_mrs(
    State(mgr): State<AppState>,
    Path(name): Path<String>,
) -> impl IntoResponse {
    let mrs: Vec<_> = merge::list_mrs(Some(&name))
        .into_iter()
        .map(|m| reconcile_mr(&mgr, m))
        .collect();
    Json(json!({ "module": name, "count": mrs.len(), "merge_requests": mrs }))
}

async fn get_mr(State(mgr): State<AppState>, Path(id): Path<String>) -> impl IntoResponse {
    match merge::load_mr(&id) {
        Some(mr) => Json(json!(reconcile_mr(&mgr, mr))).into_response(),
        None => mr_not_found(&id),
    }
}

/// Cap for hashing the live tree when computing upstream drift.
const MR_LIVE_HASH_MAX: u64 = 200 * 1024 * 1024;

async fn mr_diff(headers: axum::http::HeaderMap, Path(id): Path<String>) -> impl IntoResponse {
    // Diff bodies expose module code — same default-deny stance as /files/*.
    if let Err(r) = require_bearer(&headers) {
        return r;
    }
    let Some(mr) = merge::load_mr(&id) else {
        return mr_not_found(&id);
    };
    let store = default_store();
    let base = match merge::load_manifest(&store, &mr.base_cid) {
        Ok(m) => m,
        Err(e) => return (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    };
    let head = match merge::load_manifest(&store, &mr.head_cid) {
        Ok(m) => m,
        Err(e) => return (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    };
    let changes = merge::diff_manifests(&base, &head);
    // Conflicts vs the CURRENT live tree — best-effort (diff still renders
    // if the module vanished or is too big to hash).
    let (conflicts, live_cid, live_error) = match module_root_for(&mr.module) {
        Some(root) => match crate::snapshots::hash_dir(&root, MR_LIVE_HASH_MAX) {
            Ok((cid, live)) => (merge::conflict_paths(&changes, &base, &live), Some(cid), None),
            Err(e) => (vec![], None, Some(e)),
        },
        None => (vec![], None, Some(format!("module '{}' not found", mr.module))),
    };
    Json(json!({
        "id": mr.id,
        "module": mr.module,
        "status": mr.status,
        "base_cid": mr.base_cid,
        "head_cid": mr.head_cid,
        "changes": changes,
        "conflicts": conflicts,
        "live_cid": live_cid,
        "live_error": live_error,
    }))
    .into_response()
}

#[derive(Deserialize)]
struct MrFileQuery {
    path: String,
    /// base | head | live
    #[serde(default = "default_mr_which")]
    which: String,
}

fn default_mr_which() -> String {
    "head".to_string()
}

const MR_FILE_MAX: u64 = 2 * 1024 * 1024;

async fn mr_file(
    headers: axum::http::HeaderMap,
    Path(id): Path<String>,
    Query(q): Query<MrFileQuery>,
) -> impl IntoResponse {
    if let Err(r) = require_bearer(&headers) {
        return r;
    }
    let Some(mr) = merge::load_mr(&id) else {
        return mr_not_found(&id);
    };
    let rel = q.path.trim_start_matches('/');
    if rel.contains("..") || rel.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "invalid path" })),
        )
            .into_response();
    }
    let store = default_store();
    let bytes: Result<Vec<u8>, String> = match q.which.as_str() {
        "live" => match module_root_for(&mr.module) {
            Some(root) => {
                let p = root.join(rel);
                match p.metadata() {
                    Ok(md) if md.len() > MR_FILE_MAX => Err("file too large".into()),
                    _ => std::fs::read(&p).map_err(|e| format!("read live {rel}: {e}")),
                }
            }
            None => Err(format!("module '{}' not found", mr.module)),
        },
        which @ ("base" | "head") => {
            let cid = if which == "base" { &mr.base_cid } else { &mr.head_cid };
            match merge::load_manifest(&store, cid) {
                Ok(m) => match m.files.iter().find(|f| f.path == rel) {
                    Some(entry) if entry.size > MR_FILE_MAX => Err("file too large".into()),
                    Some(entry) => store.get(&entry.cid),
                    None => Err(format!("{rel} not present in {which} tree")),
                },
                Err(e) => Err(e),
            }
        }
        other => Err(format!("which must be base|head|live, got '{other}'")),
    };
    match bytes {
        Ok(b) => match String::from_utf8(b) {
            Ok(content) => Json(json!({
                "id": mr.id, "path": rel, "which": q.which,
                "size": content.len(), "content": content,
            }))
            .into_response(),
            Err(_) => (
                StatusCode::UNPROCESSABLE_ENTITY,
                Json(json!({ "error": "binary file" })),
            )
                .into_response(),
        },
        Err(e) => (StatusCode::NOT_FOUND, Json(json!({ "error": e }))).into_response(),
    }
}

#[derive(Deserialize)]
struct MrCommentBody {
    #[serde(default)]
    body: String,
    /// comment (default) | approve | request_changes — the latter two are
    /// review verdicts and require a trusted reviewer (owner or whitelist).
    #[serde(default)]
    action: Option<String>,
}

async fn mr_comment(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<MrCommentBody>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let Some(mr) = merge::load_mr(&id) else {
        return mr_not_found(&id);
    };
    let mut mr = reconcile_mr(&mgr, mr);
    let action = body.action.unwrap_or_else(|| "comment".to_string());
    let trusted = auth::is_trusted(&caller) || (caller.is_empty() && local_mode());
    let text = body.body.trim().to_string();
    if text.len() > 5000 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "comment too long (≤5000 chars)" })),
        )
            .into_response();
    }
    let text = match action.as_str() {
        "comment" => {
            if text.is_empty() {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({ "error": "comment body required" })),
                )
                    .into_response();
            }
            text
        }
        "approve" | "request_changes" => {
            if !trusted {
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({ "error": "review verdicts (approve/request_changes) require a trusted reviewer" })),
                )
                    .into_response();
            }
            if !mr.is_actionable() {
                return (
                    StatusCode::CONFLICT,
                    Json(json!({ "error": format!("MR is {} — no further review verdicts", mr.status) })),
                )
                    .into_response();
            }
            mr.status = if action == "approve" { "approved" } else { "changes_requested" }.to_string();
            if text.is_empty() {
                if action == "approve" { "approved".to_string() } else { "changes requested".to_string() }
            } else {
                text
            }
        }
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": format!("unknown action '{other}' (comment|approve|request_changes)") })),
            )
                .into_response();
        }
    };
    mr.comments.push(merge::MrComment {
        author: mr_identity(&caller),
        body: text,
        action: Some(action),
        timestamp: merge::now_ts(),
    });
    mr.updated_at = merge::now_ts();
    if let Err(e) = merge::save_mr(&mr) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response();
    }
    Json(json!(mr)).into_response()
}

#[derive(Deserialize)]
struct MrUpdateBody {
    /// New head tree; omitted → re-snapshot the author's fork working copy.
    #[serde(default)]
    head_cid: Option<String>,
    #[serde(default)]
    message: String,
}

async fn mr_update(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<MrUpdateBody>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let Some(mr) = merge::load_mr(&id) else {
        return mr_not_found(&id);
    };
    let mut mr = reconcile_mr(&mgr, mr);
    if mr_identity(&caller) != mr.author {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "only the MR author can push revisions" })),
        )
            .into_response();
    }
    if matches!(mr.status.as_str(), "merged" | "merging" | "closed") {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("MR is {} — open a new one", mr.status) })),
        )
            .into_response();
    }
    let store = default_store();
    let head = match body.head_cid {
        Some(h) => {
            if let Err(e) = merge::load_manifest(&store, &h) {
                return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response();
            }
            h
        }
        None => {
            let fork_dir = mr_fork_dir(&caller, &mr.module);
            if !fork_dir.is_dir() {
                return (
                    StatusCode::BAD_REQUEST,
                    Json(json!({ "error": "no fork working copy found — pass head_cid" })),
                )
                    .into_response();
            }
            match snapshot_dir(&fork_dir, &store) {
                Ok((c, _)) => c,
                Err(e) => {
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({ "error": format!("snapshotting fork: {e}") })),
                    )
                        .into_response();
                }
            }
        }
    };
    if head == mr.head_cid {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "no changes since the current revision" })),
        )
            .into_response();
    }
    let ts = merge::now_ts();
    mr.head_cid = head.clone();
    mr.revisions.push(merge::MrRevision {
        head_cid: head,
        message: if body.message.is_empty() { "revision".into() } else { body.message },
        timestamp: ts,
    });
    // A new revision reopens a changes_requested / merge_failed MR.
    mr.status = "open".to_string();
    mr.updated_at = ts;
    if let Err(e) = merge::save_mr(&mr) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response();
    }
    Json(json!(mr)).into_response()
}

async fn mr_close(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let Some(mr) = merge::load_mr(&id) else {
        return mr_not_found(&id);
    };
    let mut mr = reconcile_mr(&mgr, mr);
    let trusted = auth::is_trusted(&caller) || (caller.is_empty() && local_mode());
    if mr_identity(&caller) != mr.author && !trusted {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "only the author or a trusted reviewer can close an MR" })),
        )
            .into_response();
    }
    if matches!(mr.status.as_str(), "merged" | "merging") {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("MR is {} — cannot close", mr.status) })),
        )
            .into_response();
    }
    mr.status = "closed".to_string();
    mr.updated_at = merge::now_ts();
    if let Err(e) = merge::save_mr(&mr) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response();
    }
    Json(json!(mr)).into_response()
}

#[derive(Deserialize, Default)]
struct MrMergeBody {
    #[serde(default)]
    model: Option<String>,
    /// Extra owner guidance appended to the merge brief.
    #[serde(default)]
    instructions: Option<String>,
}

async fn mr_merge(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    body: Option<Json<MrMergeBody>>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    // Merging rewrites a live module from contributed code — owner-only, like
    // /restore, and sudo-bound when the target is any module but claude.
    if !local_mode() && !auth::is_owner(&caller) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: merging changes a live module; only the configured owner may approve it" })),
        )
            .into_response();
    }
    let Some(mr) = merge::load_mr(&id) else {
        return mr_not_found(&id);
    };
    let mut mr = reconcile_mr(&mgr, mr);
    if !matches!(mr.status.as_str(), "open" | "approved" | "changes_requested" | "merge_failed") {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("MR is {} — nothing to merge", mr.status) })),
        )
            .into_response();
    }
    let Some(root) = module_root_for(&mr.module) else {
        return mr_not_found_module(&mr.module);
    };
    if mr.module != "claude" {
        if let Some(denied) = sudo_gate(&headers, "merge", &mr.module) {
            return denied;
        }
    }
    let body = body.map(|Json(b)| b).unwrap_or_default();
    let store = default_store();
    let base = match merge::load_manifest(&store, &mr.base_cid) {
        Ok(m) => m,
        Err(e) => return (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    };
    let head = match merge::load_manifest(&store, &mr.head_cid) {
        Ok(m) => m,
        Err(e) => return (StatusCode::BAD_GATEWAY, Json(json!({ "error": e }))).into_response(),
    };
    let changes = merge::diff_manifests(&base, &head);
    if changes.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "MR contains no file changes" })),
        )
            .into_response();
    }
    // Stage BASE and HEAD on disk for the merge agent.
    let staging = merge::staging_dir(&mr.id);
    let _ = std::fs::remove_dir_all(&staging);
    for (sub, cid) in [("base", &mr.base_cid), ("head", &mr.head_cid)] {
        if let Err(e) = restore_into(&staging.join(sub), cid, &store) {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": format!("staging {sub}: {e}") })),
            )
                .into_response();
        }
    }
    let conflicts = crate::snapshots::hash_dir(&root, MR_LIVE_HASH_MAX)
        .map(|(_, live)| merge::conflict_paths(&changes, &base, &live))
        .unwrap_or_default();
    // Safety net: pin the pre-merge live tree so the merge is one restore
    // away from undone.
    if let Ok((auto_cid, _)) = snapshot_dir(&root, &store) {
        let auto_msg = format!("auto-snapshot before merging {}", mr.id);
        let (rcid, rprev) = match mod_protocol_register(&mr.module, &auto_msg).await {
            Ok(r) => (r.cid, r.prev),
            Err(_) => (None, None),
        };
        let _ = append_version(
            &mr.module,
            VersionRecord {
                cid: auto_cid,
                message: auto_msg,
                author: caller.clone(),
                timestamp: merge::now_ts(),
                parent: None,
                registry_cid: rcid,
                registry_prev: rprev,
                action: Some("auto-snapshot".to_string()),
                job_id: None,
            },
        );
    }
    let mut prompt = merge::build_merge_prompt(&mr, &root, &staging, &changes, &conflicts);
    if let Some(extra) = body.instructions.filter(|s| !s.trim().is_empty()) {
        prompt.push_str(&format!("\n\nOwner instructions for this merge:\n{}", extra.trim()));
    }
    // Submitted under the approving owner's identity → runs unsandboxed with
    // write access to the live module tree, like any owner edit job.
    let job = mgr
        .submit(SubmitRequest {
            prompt,
            model: body.model.unwrap_or_else(|| "claude-fable-5".to_string()),
            work_dir: Some(root.to_string_lossy().into_owned()),
            module_name: None,
            creation_mode: None,
            fork_source: None,
            anchor_dir: None,
            images: None,
            agent_type: None,
            system_prompt: None,
            user_address: Some(caller.clone()),
        })
        .await;
    mr.status = "merging".to_string();
    mr.merge_job_id = Some(job.id.clone());
    mr.updated_at = merge::now_ts();
    mr.comments.push(merge::MrComment {
        author: mr_identity(&caller),
        body: format!("agentic merge started → job {}", job.id),
        action: Some("system".to_string()),
        timestamp: merge::now_ts(),
    });
    if let Err(e) = merge::save_mr(&mr) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response();
    }
    (
        StatusCode::ACCEPTED,
        Json(json!({ "ok": true, "merge_request": mr, "job_id": job.id })),
    )
        .into_response()
}
