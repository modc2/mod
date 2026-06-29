//! HTTP API for Claude Jobs — Axum endpoints + SSE streaming + MetaMask auth

use crate::auth;
use crate::jobs::{ClaudeJobManager, SubmitRequest};
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
            .route("/jobs", get(list_jobs))
            .route("/jobs/:id", get(get_job))
            .route("/jobs/:id", delete(delete_job))
            .route("/jobs/:id/cancel", post(cancel_job))
            .route("/jobs/:id/stream", get(stream_job))
            .route("/modules/:name", delete(delete_module))
            .route("/modules/:name/rename", put(rename_module))
            .route("/modules/:name/snapshot", post(snapshot_module))
            .route("/modules/:name/fork", post(fork_module))
            .route("/modules/:name/restore", post(restore_module))
            .route("/modules/import", post(import_module))
            .route("/modules/:name/process", post(module_process))
            .route("/modules/:name/logs", post(module_logs))
            .route("/files/write", post(file_write))
            .route("/kill", post(kill_process))
            .route("/whitelist", post(add_to_whitelist))
            .route("/whitelist/:address", delete(remove_from_whitelist));

        if local_mode {
            println!("⚡ Local mode — auth disabled");
            base.with_state(manager)
        } else {
            base.layer(middleware::from_fn(auth::auth_middleware))
                .with_state(manager)
        }
    };

    // Public routes (no auth required)
    let public_routes = Router::new()
        .route("/health", get(health))
        .route("/config", get(get_config))
        .route("/repos", get(list_repos))
        .route("/modules", get(list_modules))
        .route("/modules/:name/config", get(get_module_config))
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
        .route("/owner", get(get_owner))
        .route("/whitelist", get(get_whitelist))
        .route("/auth/role", get(get_role))
        .route(
            "/auth/challenge",
            get(auth::challenge).with_state(challenge_store.clone()),
        )
        .route(
            "/auth/verify",
            post(auth::verify).with_state(challenge_store),
        );

    let app = Router::new()
        .merge(job_routes)
        .merge(public_routes)
        .layer(cors);

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
    Json(json!({ "status": "ok", "service": "claude-jobs" }))
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

async fn list_jobs(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
) -> impl IntoResponse {
    let all_jobs = mgr.list_jobs();

    // Filter jobs: only the owner sees all; peers (incl. whitelisted) see only
    // their own.
    let jobs = match auth::extract_address_from_headers(&headers) {
        Ok(addr) if auth::is_owner(&addr) => all_jobs,
        Ok(addr) => all_jobs
            .into_iter()
            .filter(|j| j.user_address.to_lowercase() == addr.to_lowercase())
            .collect(),
        Err(_) => {
            // Local mode or no auth — return all
            if std::env::var("CLAUDE_JOBS_LOCAL").unwrap_or_default() == "1" {
                all_jobs
            } else {
                vec![]
            }
        }
    };

    Json(json!({ "jobs": jobs, "count": jobs.len() }))
}

async fn get_job(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    match mgr.get_job(&id) {
        Some(job) => {
            // Non-owners can only view their own jobs
            if let Ok(user_addr) = auth::extract_address_from_headers(&headers) {
                if !auth::is_owner(&user_addr)
                    && !job.user_address.is_empty()
                    && job.user_address.to_lowercase() != user_addr.to_lowercase()
                {
                    return (
                        StatusCode::FORBIDDEN,
                        Json(json!({ "error": "You can only view your own jobs" })),
                    )
                        .into_response();
                }
            }
            (StatusCode::OK, Json(json!(job))).into_response()
        }
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

    fn walk(dir: &std::path::Path, depth: usize, max_depth: usize, home: &str) -> Vec<serde_json::Value> {
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
            let children = if is_dir { walk(&path, depth + 1, max_depth, home) } else { vec![] };
            entries.push(json!({
                "name": name,
                "path": display,
                "type": if is_dir { "directory" } else { "file" },
                "children": children,
            }));
        }
        entries
    }

    let tree = walk(root, 0, max_depth, &home);
    Json(json!({ "tree": tree, "path": raw_path }))
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

/// List orbit and core modules with config.json data (app_url, api_url, etc.)
async fn list_modules(Query(params): Query<ModuleQuery>) -> impl IntoResponse {
    let query = params.q.unwrap_or_default().to_lowercase();
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let anchor = params.anchor.unwrap_or_else(|| format!("{}/mod", home));
    // Expand ~ to home
    let anchor = anchor.replacen("~", &home, 1);

    let mut modules: Vec<serde_json::Value> = Vec::new();

    // Load registry.json for authoritative CID lookups
    let registry_path = format!("{}/.mod/api/registry.json", home);
    let registry_data: Option<serde_json::Value> = std::fs::read_to_string(&registry_path)
        .ok()
        .and_then(|content| serde_json::from_str(&content).ok());
    let registry_map = registry_data
        .as_ref()
        .and_then(|v| v.get("data"))
        .and_then(|v| v.as_object());

    // Scan both orbit/ and core/ directories
    let scan_dirs = vec![
        (format!("{}/mod/orbit", anchor), "orbit"),
        (format!("{}/mod/core", anchor), "core"),
    ];

    for (scan_dir, category) in &scan_dirs {
        let dir_path = std::path::Path::new(scan_dir);
        if !dir_path.is_dir() { continue; }
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
                    "owner": owner,
                    "version": version,
                    "cid": cid,
                    "deps": deps,
                    "created_at": created_at,
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

    // Search orbit/ and core/ for the module
    let search_dirs = vec![
        format!("{}/mod/orbit/{}", anchor, name),
        format!("{}/mod/core/{}", anchor, name),
    ];

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

/// Delete a module directory (only module owner or system owner can delete)
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
    let mut module_owner: Option<String> = None;

    for module_dir in &search_dirs {
        let base = std::path::Path::new(module_dir);
        if !base.is_dir() {
            continue;
        }
        found_path = Some(module_dir.clone());

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

    // Authorization: must be system owner, module owner, or module has no owner
    let is_sys_owner = auth::is_owner(&user_addr);
    let is_mod_owner = module_owner
        .as_ref()
        .map(|o| o == &user_addr.to_lowercase())
        .unwrap_or(false);
    let is_unowned = module_owner.is_none();

    if !is_sys_owner && !is_mod_owner && !is_unowned {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "You can only delete modules you own" })),
        )
            .into_response();
    }

    // Deleting a module the caller does NOT personally own (the system owner's
    // modules, or unowned repo modules) is a privileged cross-module operation —
    // require a fresh sudo signature from the owner key. A real module-owner may
    // still delete their own module under the per-module check above.
    if name != "claude" && !is_mod_owner {
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
/// Cross-module control (any module except claude itself) requires a fresh sudo
/// signature — restarting/stopping host services is a privileged system op.
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
        // A mutating action against another module touches host services —
        // require a fresh sudo signature bound to "process:<name>". `status` is
        // read-only and claude's own processes are not cross-module.
        if action != "status" && name != "claude" {
            if let Some(denied) = sudo_gate(&headers, "process", &name) {
                return denied;
            }
        }
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

const API_MODULE_URL: &str = "http://host.docker.internal:8000";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct ApiRegResult {
    cid: Option<String>,
    prev: Option<String>,
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
async fn mod_protocol_register(
    module: &str,
    comment: &str,
) -> Result<ApiRegResult, String> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| format!("http client: {e}"))?;
    let resp = client
        .post(format!("{API_MODULE_URL}/api/reg"))
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

fn module_root_for(name: &str) -> Option<std::path::PathBuf> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let candidates = [
        format!("{home}/mod/mod/orbit/{name}"),
        format!("{home}/mod/mod/core/{name}"),
    ];
    for c in &candidates {
        let p = std::path::PathBuf::from(c);
        if p.is_dir() {
            return Some(p);
        }
    }
    None
}

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
        .post(format!("{API_MODULE_URL}/api/mod"))
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
                "error": format!("api module unreachable at {API_MODULE_URL}: {e}"),
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
// Owners drop modules straight into orbit/ (or core/, owner-only). A
// non-owner authed caller is sandboxed into orbit/portal/<addr>/ exactly
// like a fork, so they can never clobber a first-class module.
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

    let is_owner = local_mode || auth::is_owner(&caller);
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
    if category == "core" && !is_owner {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "core modules are owner-only" })),
        )
            .into_response();
    }

    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    // Owners land in the first-class tree; everyone else is namespaced into a
    // per-address portal so an untrusted import can never shadow a real module.
    let (module_id, dest) = if is_owner {
        (
            name.clone(),
            std::path::PathBuf::from(format!("{home}/mod/mod/{category}/{name}")),
        )
    } else {
        let owner = caller.to_lowercase();
        (
            format!("portal/{owner}/{name}"),
            std::path::PathBuf::from(format!("{home}/mod/mod/orbit/portal/{owner}/{name}")),
        )
    };

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
            "category": if is_owner { category } else { "portal".to_string() },
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
