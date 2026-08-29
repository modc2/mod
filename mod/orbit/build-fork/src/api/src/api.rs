//! HTTP API for Build Jobs — Axum endpoints + SSE streaming + MetaMask auth

use crate::auth;
use crate::audits;
use crate::costs;
use crate::credits;
use crate::jobs::{ClaudeJobManager, JobStatus, SubmitRequest};
use crate::merge;
use crate::process;
use crate::suggestions;
use crate::sudo;
use crate::userspace;
use crate::snapshots::{
    append_version, default_store, read_versions, restore_into, snapshot_dir, VersionRecord,
};
use axum::{
    extract::{DefaultBodyLimit, Path, Query, State},
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
    std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1"
}

/// The build module's own directory on the host: `$HOME/mod/mod/orbit/build-fork`.
/// Writes inside it are "self-edits" and stay on the normal owner gate; anything
/// outside it touches OTHER modules / the system and requires a sudo signature.
fn claude_module_dir() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    std::path::PathBuf::from(home)
        .join("mod")
        .join("mod")
        .join("orbit")
        .join("build-fork")
}

/// True if `path` resolves to somewhere inside the build module's own directory.
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

// ── Module-tree confinement for the public module readers ────────────
//
// `/modules` and `/modules/:name/config` are world-readable by design — a
// visitor can see what the orbit holds. They take caller-supplied `anchor`
// and `path` hints, though, so without a boundary they read config.json out
// of anywhere on the host. Both are pinned to the module tree here.

/// The tree these readers may look inside: `$HOME/mod` (holds `mod/orbit` and
/// `mod/core`).
fn module_anchor_root() -> std::path::PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    std::path::PathBuf::from(home).join("mod")
}

/// A module name safe to splice into a filesystem path: the segments the
/// orbit actually uses (`store`, `bloctime/app`), nothing that can climb out.
fn valid_module_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 128
        && !name.starts_with('/')
        && name.split('/').all(|seg| {
            !seg.is_empty()
                && seg != ".."
                && seg != "."
                && seg
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'))
        })
}

/// Resolve a caller-supplied directory hint (`?anchor=`, `?path=`) inside the
/// module tree. `~` expands; anything that lands outside the tree is refused.
fn confine_to_module_tree(raw: &str) -> Option<std::path::PathBuf> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let expanded = std::path::PathBuf::from(raw.replacen('~', &home, 1));
    if expanded
        .components()
        .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return None;
    }
    let root = module_anchor_root();
    let root_real = std::fs::canonicalize(&root).unwrap_or(root);
    let real = std::fs::canonicalize(&expanded).unwrap_or(expanded);
    if real == root_real || real.starts_with(&root_real) {
        Some(real)
    } else {
        None
    }
}

/// The anchor a public module reader should scan: the caller's hint when it
/// stays inside the tree, else the tree root itself.
fn safe_anchor(raw: Option<String>) -> String {
    raw.as_deref()
        .and_then(confine_to_module_tree)
        .unwrap_or_else(module_anchor_root)
        .to_string_lossy()
        .into_owned()
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

    let local_mode = std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1";

    let challenge_store = auth::new_challenge_store();

    // Job routes — skip auth in local mode
    let job_routes = {
        let base = Router::new()
            .route("/jobs", post(submit_job))
            .route("/jobs/:id", delete(delete_job))
            .route("/jobs/:id/cancel", post(cancel_job))
            .route("/jobs/:id/message", post(message_job))
            .route("/modules/:name", delete(delete_module))
            .route("/modules/:name/rename", put(rename_module))
            .route("/modules/:name/snapshot", post(snapshot_module))
            .route("/modules/:name/fork", post(fork_module))
            .route("/modules/:name/copy", post(copy_module))
            .route("/modules/:name/restore", post(restore_module))
            // Owner's undo — walks the version log back without the owner
            // having to name a CID. Same gate as /restore.
            .route("/modules/:name/undo", post(undo_module))
            .route("/modules/import", post(import_module))
            // Private repos: server-held password + encrypted-only
            // publishing. All owner-gated inside (privacy handlers below).
            .route(
                "/modules/:name/privacy",
                get(privacy_status).post(privacy_enable).delete(privacy_disable),
            )
            .route(
                "/modules/:name/privacy/password",
                get(privacy_password_get).delete(privacy_password_delete),
            )
            .route("/modules/:name/privacy/verify", post(privacy_verify))
            // Merge requests: fork→propose→review are open to every signed-in
            // user (that's the point); the merge itself is owner-gated inside.
            .route("/modules/:name/mr-fork", post(mr_fork_module))
            .route("/modules/:name/merge-requests", post(open_merge_request))
            .route("/merge-requests/:id/comment", post(mr_comment))
            .route("/merge-requests/:id/update", post(mr_update))
            .route("/merge-requests/:id/close", post(mr_close))
            .route("/merge-requests/:id/merge", post(mr_merge))
            .route("/modules/:name/suggestions", post(open_suggestion))
            .route("/suggestions/:id", delete(delete_suggestion))
            // (the discussion itself is public — see /suggestions/:id/comment
            // in the public routes below)
            .route("/suggestions/:id/vote", post(suggestion_vote))
            .route("/suggestions/:id/status", post(suggestion_status))
            .route("/suggestions/:id/play", post(play_suggestion))
            .route("/modules/:name/process", post(module_process))
            .route("/modules/:name/logs", post(module_logs))
            // GitHub bridge: link a module to a real repo, then push its
            // tree to allowlisted branches or merge dev branches in. All
            // owner-gated inside the handlers (token + policy live off-tree).
            .route("/modules/:name/github", get(crate::github::status).delete(crate::github::disconnect))
            .route("/modules/:name/github/connect", post(crate::github::connect))
            .route("/modules/:name/github/push", post(crate::github::push))
            .route("/modules/:name/github/merge", post(crate::github::merge))
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
            // Session handoff: the wallet-signed OWNER mints a QR code that signs
            // ANOTHER DEVICE in as themselves (redeem is public, below).
            .route("/auth/handoff", post(create_handoff))
            // Per-user agent credentials — connect YOUR Claude session (and
            // sibling agents when they land); tracked in the AGENT sidebar tab.
            .route("/agent/auth", get(crate::agent_auth::status))
            .route(
                "/agent/auth/:agent",
                post(crate::agent_auth::connect).delete(crate::agent_auth::disconnect),
            )
            // Power saver: the person's idle-timeout knob + per-module controls.
            .route("/reaper/config", post(crate::reaper::config_handler))
            .route("/reaper/control", post(crate::reaper::control_handler))
            // Task vault — your own ledger, encrypted with your own password.
            // Everything here is per-caller: the address in the bearer token
            // is the only wallet a request can ever touch.
            .route("/vault", get(crate::vault::status))
            .route("/vault/enable", post(crate::vault::enable))
            .route("/vault/unlock", post(crate::vault::unlock_handler))
            .route("/vault/lock", post(crate::vault::lock_handler))
            .route("/vault/rotate", post(crate::vault::rotate))
            .route("/vault/disable", post(crate::vault::disable))
            .route("/credits", get(get_credits))
            .route("/credits/sync", post(sync_credits))
            .route("/credits/accounts", get(credits_accounts))
            .route("/credits/grant", post(credits_grant))
            .route("/credits/debit", post(credits_debit))
            // Spend metering: your own costs, and the owner's spend policy.
            .route("/costs/me", get(get_my_costs))
            .route("/costs/policy", post(set_cost_policy))
            // Audit any module — snapshot-pinned, run in the caller's own
            // workspace, so this is open to every signed-in peer.
            .route("/modules/:name/audit", post(start_audit));

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
        .route("/tasks/:cid", get(get_task_by_cid))
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
        // Cost ledger — public like /jobs, and the settlement oracle the
        // costmarket prediction market reads.
        .route("/costs", get(get_costs))
        .route("/costs/epoch/:month", get(get_cost_epoch))
        .route("/costs/policy", get(get_cost_policy))
        // Audit ledger — reading is public by design.
        .route("/audits", get(list_audits))
        .route("/audits/stats", get(audit_stats))
        .route("/audits/:id", get(get_audit))
        .route("/modules/:name/audits", get(list_module_audits))
        .route("/changelog", get(get_changelog))
        .route("/versions/:version", get(get_version))
        .route("/modules/:name/versions", get(list_module_versions))
        .route("/modules/:name/registry", get(module_registry))
        .route("/autosnap/status", get(crate::autosnap::status_handler))
        // Same-origin pass-through to the orbit/agent module (sibling API).
        // The agent module authenticates via the `key` field in the JSON
        // body / query string, so the proxy itself is public: it forwards,
        // the agent enforces. Powers the AGENT params panel + credits card.
        .route(
            "/agentmod/*path",
            get(agentmod_proxy)
                .post(agentmod_proxy)
                // PUT carries agent edits (`PUT /agents/{name}`) — the AGENTS
                // panel rebinds an agent's model and goal through it.
                .put(agentmod_proxy)
                .delete(agentmod_proxy),
        )
        // Power saver state: what's awake, what's idle, memory headroom.
        .route("/reaper", get(crate::reaper::status_handler))
        .route("/providers", get(crate::reaper::providers_handler))
        // Host resources + per-app usage — the HUB's SYSTEM panel.
        .route("/system", get(crate::system::status_handler))
        // htop-grade readout (per-core CPU + raw process table with cmdlines).
        // Route lives here but the handler itself is owner-gated — cmdlines
        // can leak secrets passed as CLI args, so no public variant.
        .route("/system/htop", get(crate::system::htop_handler))
        // MR metadata is part of the public code ledger (like /jobs); the
        // diff/file bodies are default-deny like the /files/* endpoints.
        .route("/merge-requests", get(list_all_mrs))
        .route("/modules/:name/merge-requests", get(list_module_mrs))
        .route("/merge-requests/:id", get(get_mr))
        .route("/merge-requests/:id/diff", get(mr_diff))
        .route("/merge-requests/:id/file", get(mr_file))
        .route("/suggestions", get(list_all_suggestions))
        .route("/modules/:name/suggestions", get(list_module_suggestions))
        .route("/suggestions/:id", get(get_suggestion))
        // The discussion is open to everyone — no wallet, no whitelist, no
        // association with the module. Reading the whole thread is one call
        // (lists only carry its tail), and writing to it is the one mutation
        // on this API an anonymous caller may make.
        .route("/suggestions/:id/comments", get(suggestion_comments))
        .route("/suggestions/:id/comment", post(suggestion_comment))
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
        // Which curves this server will accept a sign-in from — the sign-in
        // screen builds its wallet list from this.
        .route("/auth/key-types", get(auth::key_types))
        // MCP, Streamable HTTP. The route is public because the transport is:
        // every tool underneath re-enters the REST surface above carrying this
        // request's own Authorization header, so an anonymous MCP client gets
        // exactly the public tools and nothing else.
        .route("/mcp", post(mcp_endpoint).get(mcp_hint))
        // Arena interop, inbound: what a sibling arena calls when this console
        // is entered as a competitor. Both are refused unless the owner has
        // entered us and the caller holds the shared key (see arena.rs).
        .route("/arena", get(crate::arena::status_handler))
        .route("/arena/solve", post(crate::arena::solve))
        .route("/arena/play", post(crate::arena::play))
        // The public jobs reads (list/get/stream) pull from the same manager.
        .with_state(manager);

    let app = Router::new()
        .merge(job_routes)
        .merge(public_routes)
        // Axum defaults to a 2 MB request body cap — a single base64-encoded
        // image attachment (POST /jobs `images`) blows past that and the client
        // sees a bare "SUBMIT FAILED (413)". Raise it to 32 MB so attaching a
        // few screenshots works. Applies to every route on this app.
        .layer(DefaultBodyLimit::max(32 * 1024 * 1024))
        .layer(cors);

    // Bind host defaults to all interfaces (needed inside Docker so the Caddy
    // gateway in a sibling container can reach it). For host-only single-port
    // exposure, set BIND_HOST=127.0.0.1 so only the local Caddy can reach it.
    let host = std::env::var("BIND_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let addr = format!("{}:{}", host, port);
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .expect("Failed to bind");

    // Where MCP tools call back in. Always loopback, never `addr` — that may
    // be 0.0.0.0, which is a bind address and not somewhere you can connect to.
    crate::mcp::set_base(format!("http://127.0.0.1:{}", port));

    println!("Listening on http://{}", addr);
    println!("MCP (Streamable HTTP) on http://127.0.0.1:{}/mcp", port);
    axum::serve(listener, app).await.expect("Server error");
}

/// MCP Streamable HTTP. Accepts a single JSON-RPC message or a batch; a batch
/// of nothing but notifications correctly answers with no body at all.
async fn mcp_endpoint(headers: axum::http::HeaderMap, body: axum::body::Bytes) -> impl IntoResponse {
    let ctx = crate::mcp::Ctx::from_headers(&headers);
    let msg: serde_json::Value = match serde_json::from_slice(&body) {
        Ok(v) => v,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({
                    "jsonrpc": "2.0",
                    "id": null,
                    "error": { "code": -32700, "message": format!("parse error: {e}") }
                })),
            )
                .into_response()
        }
    };

    match msg {
        serde_json::Value::Array(batch) => {
            let mut out = Vec::new();
            for one in &batch {
                if let Some(r) = crate::mcp::handle_message(one, &ctx).await {
                    out.push(r);
                }
            }
            if out.is_empty() {
                StatusCode::ACCEPTED.into_response()
            } else {
                Json(serde_json::Value::Array(out)).into_response()
            }
        }
        one => match crate::mcp::handle_message(&one, &ctx).await {
            Some(r) => Json(r).into_response(),
            None => StatusCode::ACCEPTED.into_response(),
        },
    }
}

async fn mcp_hint() -> impl IntoResponse {
    Json(json!({
        "transport": "Streamable HTTP — POST JSON-RPC 2.0 messages to /mcp",
        "protocolVersion": crate::mcp::PROTOCOL_VERSION,
        "server": { "name": crate::mcp::SERVER_NAME, "version": crate::mcp::SERVER_VERSION },
        "auth": "Send the same `Authorization: Bearer …` you would send a REST route. \
                 Without one you get the tools tagged [public].",
        "stdio": "build-fork-jobs --stdio",
        "tools": crate::mcp::tool_list(),
    }))
}

async fn health() -> impl IntoResponse {
    // `local` is authoritative for the app's local-mode probe — never infer
    // local mode from an unauthenticated read succeeding (public endpoints
    // exist), or a browser can trap itself in a tokenless session.
    Json(json!({ "status": "ok", "service": "build-fork-jobs", "local": local_mode() }))
}

/// Pass-through to the orbit/agent module's API so the browser reaches it
/// same-origin (the gateway doesn't route :50117). Responses are buffered,
/// so long-lived SSE paths (/run/stream) should NOT go through here — jobs
/// stream through the normal /jobs pipeline instead.
async fn agentmod_proxy(
    method: axum::http::Method,
    Path(path): Path<String>,
    axum::extract::RawQuery(query): axum::extract::RawQuery,
    body: axum::body::Bytes,
) -> impl IntoResponse {
    let base = std::env::var("AGENT_API_URL")
        .unwrap_or_else(|_| "http://localhost:50117".to_string());
    let mut url = format!("{}/{}", base, path);
    if let Some(q) = query {
        url.push('?');
        url.push_str(&q);
    }
    let client = reqwest::Client::new();
    let mut req = match method {
        axum::http::Method::GET => client.get(&url),
        axum::http::Method::POST => client.post(&url),
        axum::http::Method::PUT => client.put(&url),
        axum::http::Method::DELETE => client.delete(&url),
        _ => {
            return (
                StatusCode::METHOD_NOT_ALLOWED,
                Json(json!({ "error": "method not allowed" })),
            )
                .into_response()
        }
    };
    if !body.is_empty() {
        req = req
            .header("content-type", "application/json")
            .body(body.to_vec());
    }
    match req
        .timeout(std::time::Duration::from_secs(60))
        .send()
        .await
    {
        Ok(resp) => {
            let status = StatusCode::from_u16(resp.status().as_u16()).unwrap_or(StatusCode::OK);
            let text = resp.text().await.unwrap_or_default();
            (
                status,
                [(axum::http::header::CONTENT_TYPE, "application/json")],
                text,
            )
                .into_response()
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(json!({ "error": format!("agent module unreachable: {}", e) })),
        )
            .into_response(),
    }
}

/// Public: machine-readable endpoint catalog for the console's API tab.
/// auth levels: "public" (no token), "bearer" (any signed-in user),
/// "owner" (bearer + owner address; mutations on other modules also need x-sudo).
async fn api_schema() -> impl IntoResponse {
    let e = |method: &str, path: &str, auth: &str, desc: &str| {
        json!({ "method": method, "path": path, "auth": auth, "desc": desc })
    };
    Json(json!({
        "service": "build-fork-jobs",
        "base": "/api/build-fork",
        "auth_note": "Bearer token from /auth/challenge + /auth/verify (wallet signature). Non-owner wallets sign the Terms of Use embedded in their first challenge.",
        "endpoints": [
            e("GET", "/health", "public", "Liveness + whether auth is disabled (local mode)"),
            e("GET", "/schema", "public", "This endpoint catalog"),
            e("GET", "/owner", "public", "Configured owner address"),
            e("GET", "/auth/terms?address=0x..", "public", "Terms of Use text + whether the address still needs to accept them"),
            e("GET", "/auth/challenge?address=0x..", "public", "Nonce message to sign (embeds Terms on first sign-in)"),
            e("POST", "/auth/verify", "public", "{address, message, signature} → bearer token"),
            e("GET", "/auth/role", "public", "Role of the presented token (owner/peer/guest)"),
            e("GET", "/jobs", "public", "World-readable task ledger — minus tasks run inside private modules"),
            e("GET", "/jobs/:id", "public", "One job with full output"),
            e("GET", "/jobs/:id/stream", "public", "Server-sent events stream of live job output"),
            e("GET", "/tasks/:cid", "public", "Task bundle by localfs CID — powers the shared-session link (?task=<cid>) and the replay QR (?replay=<cid>); falls back to the shared blob store for tasks minted elsewhere"),
            e("POST", "/jobs", "bearer", "Submit a task: {prompt, model?, work_dir?, module_name?, system_prompt?, agent_type?, agent?, agent_params?} — agent picks the backend (claude CLI | orbit/agent module)"),
            e("POST", "/jobs/:id/cancel", "bearer", "Cancel a running job (yours, or any if owner)"),
            e("POST", "/jobs/:id/message", "bearer", "Guide a RUNNING job mid-task: {message} is injected into the agent's session at its next tool boundary (yours, or any if owner)"),
            e("DELETE", "/jobs/:id", "bearer", "Delete a job record (yours, or any if owner)"),
            e("GET", "/modules", "public", "All PUBLIC modules in the anchor tree; private ones appear only for their owner (rows carry `private`)"),
            e("GET", "/modules/:name/config", "public", "A module's config.json"),
            e("GET", "/modules/:name/screenshot", "public", "PNG screenshot of the module's app (?fresh=1 captures now)"),
            e("GET", "/modules/:name/versions", "public", "Snapshot chain for a module"),
            e("GET", "/modules/:name/registry", "public", "On-chain registry status"),
            e("POST", "/modules/import", "owner", "Import a module: {github} or {cid}"),
            e("GET", "/modules/:name/github", "owner", "GitHub link status + live remote branches (token never returned)"),
            e("POST", "/modules/:name/github/connect", "owner", "Link a repo: {repo: 'owner/name', token?, push_branches?: [..], allow_merge?} — omit token on update to keep the stored one"),
            e("DELETE", "/modules/:name/github", "owner", "Disconnect the repo and drop the working clone"),
            e("POST", "/modules/:name/github/push", "owner", "Publish the module's live tree as one commit: {branch, message?} — branch must be allowlisted; new branches cut from the default branch"),
            e("POST", "/modules/:name/github/merge", "owner", "Merge a dev branch: {base, head} merges origin/head into base and pushes — base must be allowlisted, conflicts abort with a file list"),
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
            e("POST", "/merge-requests/:id/merge", "owner", "Agentic merge: stages base/head trees and submits a three-way merge job (x-sudo for non-build modules)"),
            e("POST", "/modules/:name/snapshot", "owner", "Snapshot module to content-addressed storage"),
            e("GET", "/modules/:name/privacy", "module owner", "Privacy status: private flag + whether the server still holds the key"),
            e("POST", "/modules/:name/privacy", "module owner", "Make a module private: generates a key (held server-side), hides it from every other caller, and switches publishing to encrypted-only"),
            e("DELETE", "/modules/:name/privacy", "module owner", "Back to public: {password?} — key material is kept so old encrypted versions stay restorable"),
            e("GET", "/modules/:name/privacy/password", "module owner", "Read the server-held key (copy it before deleting the server copy)"),
            e("DELETE", "/modules/:name/privacy/password", "module owner", "Delete the server-held key copy — only a verifier remains; the key becomes unrecoverable here"),
            e("POST", "/modules/:name/privacy/verify", "module owner", "Verify a key against the stored verifier: {password}"),
            e("POST", "/modules/:name/fork", "owner", "Fork a module"),
            e("POST", "/modules/:name/copy", "owner", "Copy a module's live tree to a new name: {new_name, category?} — instant, no AI job; ports auto-remapped"),
            e("POST", "/modules/:name/restore", "owner (owner key only)", "Revert a module to any snapshot CID — the owner's last word: editors and sudo delegates are refused, and the owner's own signature is required (x-sudo)"),
            e("POST", "/modules/:name/undo", "owner (owner key only)", "Undo the last change: {steps?, password?} — reverts to the previous state in the version log (x-sudo)"),
            e("PUT", "/modules/:name/rename", "owner", "Rename a module and everything wired to its name — tree, config, in-tree references, ~/.mod state, router override, sibling deps, pm2 entries: {new_name, dry_run?, refs?: paths|all|none, restart?, reroute?} (x-sudo)"),
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
            e("POST", "/grants", "owner", "Create a timed access grant: {ttl?, key?, label?, modules?[] — omit modules for all}"),
            e("DELETE", "/grants/:id", "owner", "Revoke a grant"),
            e("POST", "/grants/:id/redeem", "public", "Redeem a QR grant as a walletless guest"),
            e("POST", "/auth/handoff", "owner", "Mint a one-time phone sign-in code (optional ttl secs, 60–86400) — wallet-signed owner session only; handed-off sessions are refused"),
            e("POST", "/auth/handoff/redeem", "public", "Redeem a handoff code for a token"),
            e("GET", "/sudo/status", "owner", "Sudo session + policy"),
            e("POST", "/sudo/policy", "owner", "Set sudo session policy"),
            e("POST", "/sudo/lock", "owner", "End the sudo session now"),
            e("GET", "/credits", "bearer", "Your credit balance in USD + chain settings"),
            e("GET", "/credits/accounts", "owner", "All credit accounts"),
            e("POST", "/credits/sync", "bearer", "Re-read your on-chain credit"),
            e("POST", "/credits/grant", "owner", "Grant an account credit ({identity, usd})"),
            e("POST", "/credits/debit", "owner", "Charge an account ({identity, usd})"),
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
        std::path::PathBuf::from(format!("{}/mod/mod/orbit/build-fork/config.json", home))
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
        .join("build-fork")
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
            std::path::PathBuf::from(format!("{}/mod/mod/orbit/build-fork/config.json", home))
        });

    let content = std::fs::read_to_string(&config_path).ok()?;
    let data: serde_json::Value = serde_json::from_str(&content).ok()?;
    let owner = data.get("owner").and_then(|v| v.as_str())?.to_lowercase();
    if owner.is_empty() { None } else { Some(owner) }
}

/// Returns the tier for the given address: "owner", "editor", or "viewer"
/// (signed in, read-only — every key that isn't trusted to edit).
async fn get_role(Query(params): Query<RoleQuery>) -> impl IntoResponse {
    let address = params.address.to_lowercase();
    let is_owner = auth::is_owner(&address);
    // Whitelisted editors are not the owner but are trusted to edit the host repo.
    let is_editor = !is_owner && auth::is_trusted(&address);
    let role = auth::role_of(&address);
    // null = unrestricted (owner / whitelist / unscoped grant); a list means
    // the editor's grant confined their powers to just those modules.
    let edit_modules = match auth::edit_scope(&address) {
        Some(auth::EditScope::Modules(list)) => json!(list),
        _ => serde_json::Value::Null,
    };
    // What a live QR invite opens: "all" (unscoped invite), the module names
    // it carried, or null for no invite. This — not the coarse can_edit flag —
    // is what actually widens a guest past their peer workspace, so it's what
    // the console keys its edit affordances on.
    let invite_scope = match auth::grant_edit_scope(&address) {
        Some(auth::EditScope::All) => json!("all"),
        Some(auth::EditScope::Modules(list)) => json!(list),
        None => serde_json::Value::Null,
    };
    Json(json!({
        "address": address,
        "role": role,
        "is_owner": is_owner,
        "is_editor": is_editor,
        // Trusted = owner OR whitelisted editor; both may edit the orbit.
        "can_edit": is_owner || is_editor,
        // Viewers can read everything and write nothing — the API refuses any
        // non-GET from them, so the console greys its write affordances out
        // rather than letting them fail at submit time.
        "can_write": is_owner || is_editor,
        // The owner's last word: reverting a module to an earlier version is
        // NOT part of the edit surface — delegated sudo passes `is_owner`
        // everywhere else and is still refused here.
        "can_revert": auth::is_root_owner(&address),
        "is_root_owner": auth::is_root_owner(&address),
        "edit_modules": edit_modules,
        "invite_scope": invite_scope,
        "open_signin": auth::open_signin(),
    }))
}

#[derive(Deserialize)]
struct RoleQuery {
    address: String,
}

/// The whitelist is the owner's private list of trusted keys — who to phish,
/// read from outside. Only the owner sees it; everyone else gets an empty list
/// (the console only renders it on the owner's ACCESS card anyway).
async fn get_whitelist(headers: axum::http::HeaderMap) -> impl IntoResponse {
    if require_owner_or_local(&headers).is_err() {
        return Json(json!({ "whitelist": [] }));
    }
    Json(json!({ "whitelist": auth::read_whitelist() }))
}

#[derive(Deserialize)]
struct WhitelistAddRequest {
    address: String,
}

/// Owner gate for handlers living outside this file (reaper): honors
/// BUILD_FORK_JOBS_LOCAL like the job routes' auth middleware does.
pub(crate) fn require_owner_or_local(
    headers: &axum::http::HeaderMap,
) -> Result<(), (StatusCode, Json<serde_json::Value>)> {
    if local_mode() {
        return Ok(());
    }
    require_owner(headers)
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
    let mut entries = auth::read_whitelist_entries();
    if !entries.iter().any(|e| e.address == addr) {
        // Unscoped by default — narrow it afterwards by editing the entry's
        // `access` (see auth::WhitelistAccess).
        entries.push(auth::WhitelistEntry { address: addr.clone(), access: auth::WhitelistAccess::All });
        if let Err(e) = auth::write_whitelist_entries(&entries) {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
        }
    }
    let list: Vec<String> = entries.iter().map(|e| e.address.clone()).collect();
    (StatusCode::OK, Json(json!({ "whitelist": list, "added": addr }))).into_response()
}

async fn remove_from_whitelist(
    headers: axum::http::HeaderMap,
    Path(address): Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let target = address.trim().to_lowercase();
    let before = auth::read_whitelist_entries();
    let after: Vec<auth::WhitelistEntry> =
        before.iter().filter(|e| e.address != target).cloned().collect();
    if after.len() == before.len() {
        return (StatusCode::NOT_FOUND, Json(json!({ "error": "address not in whitelist" }))).into_response();
    }
    if let Err(e) = auth::write_whitelist_entries(&after) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    let list: Vec<String> = after.iter().map(|e| e.address.clone()).collect();
    (StatusCode::OK, Json(json!({ "whitelist": list, "removed": target }))).into_response()
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
    /// Optional module scope — omit (or send []) for every module.
    #[serde(default)]
    modules: Option<Vec<String>>,
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
        // null = every module; a list = compartmentalized edit scope.
        "modules": g.modules,
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
    match auth::create_grant(ttl, key, label, req.modules) {
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
            "modules": r.modules,
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
// The owner's wallet-signed session mints a single-use code bound to its
// OWN identity (default 5 minutes, minter-chosen TTL clamped in auth.rs);
// another device (the owner's phone) trades the code for a fresh bearer
// token as that address — no wallet or signing needed there. Minting is
// OWNER-ONLY and refused to handed-off sessions themselves (their tokens
// carry a signed marker), so a scanned QR can never mint further QRs —
// session access always originates at the wallet. Redemption is public:
// the code is the capability.

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
    if !auth::is_owner(&address) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: sign-in QRs can only be minted by the owner wallet" })),
        )
            .into_response();
    }
    if auth::headers_carry_handoff_token(&headers) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "This session was itself opened by a sign-in QR — mint new codes from your wallet-signed session" })),
        )
            .into_response();
    }
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
            // Marked token: this session can do everything the wallet one
            // can EXCEPT mint further handoffs (see create_handoff).
            let token = auth::mint_handoff_token(&address);
            (StatusCode::OK, Json(json!({ "token": token, "address": address }))).into_response()
        }
        Err(e) => (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response(),
    }
}

// ── Credits — dollars, backed by the chain module's credit token ─────
// Backed by credits.rs: your balance is the chain module's Market credit token
// ($1 = 1.00) held by your own wallet, plus owner grants, minus what this
// module metered. State under ~/.mod/{module}/credits/. Authed routes: the
// caller's identity is their signed-in address ("local" in local mode).

fn caller_identity(headers: &axum::http::HeaderMap) -> Result<String, axum::response::Response> {
    match auth::extract_address_from_headers(headers) {
        Ok(addr) => Ok(addr),
        Err(_) if local_mode() => Ok("local".to_string()),
        Err(e) => Err((StatusCode::UNAUTHORIZED, Json(json!({ "error": e }))).into_response()),
    }
}

/// Caller's credit account in dollars: on-chain credit + grants − spends,
/// alongside the chain settings the console needs to send a top-up.
async fn get_credits(headers: axum::http::HeaderMap) -> impl IntoResponse {
    let identity = match caller_identity(&headers) { Ok(i) => i, Err(r) => return r };
    if let Err(e) = credits::ensure_account(&identity) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    (StatusCode::OK, Json(json!({
        "account": credits::account_view(&identity).await,
        "chain": credits::chain_cfg(),
    }))).into_response()
}

/// Re-read the chain — the same thing GET /credits does, as an explicit
/// refresh for right after a top-up lands.
async fn sync_credits(headers: axum::http::HeaderMap) -> impl IntoResponse {
    let identity = match caller_identity(&headers) { Ok(i) => i, Err(r) => return r };
    if let Err(e) = credits::ensure_account(&identity) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    let view = credits::account_view(&identity).await;
    let status = if view.chain_error.is_some() { StatusCode::BAD_GATEWAY } else { StatusCode::OK };
    (status, Json(json!({ "account": view, "chain": credits::chain_cfg() }))).into_response()
}

/// Owner-only: every account in the ledger, for the console's admin view.
async fn credits_accounts(headers: axum::http::HeaderMap) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let identities: Vec<String> = credits::read_ledger().accounts.keys().cloned().collect();
    let mut accounts = Vec::with_capacity(identities.len());
    for id in identities {
        accounts.push(credits::account_view(&id).await);
    }
    (StatusCode::OK, Json(json!({ "accounts": accounts }))).into_response()
}

#[derive(Deserialize)]
struct CreditsAmountRequest {
    identity: String,
    /// Dollars as a string ("2.50") — floats don't belong in a ledger.
    usd: String,
    #[serde(default)]
    reason: Option<String>,
}

/// Owner-only: hand an account credit directly (promo, refund, off-chain
/// payment). The on-chain path needs no server help — it's the user's wallet
/// buying credit from the chain module's Market.
async fn credits_grant(
    headers: axum::http::HeaderMap,
    Json(req): Json<CreditsAmountRequest>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let usd6 = match credits::parse_usd(&req.usd) {
        Ok(v) => v,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    };
    let identity = req.identity.trim().to_lowercase();
    match credits::grant(&identity, usd6, req.reason.as_deref().unwrap_or("grant")) {
        Ok(_) => (StatusCode::OK, Json(json!({ "account": credits::account_view(&identity).await }))).into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response(),
    }
}

/// Owner-only: charge an account (subscription / pay-per-use settlement).
async fn credits_debit(
    headers: axum::http::HeaderMap,
    Json(req): Json<CreditsAmountRequest>,
) -> impl IntoResponse {
    if let Err(e) = require_owner(&headers) { return e.into_response(); }
    let usd6 = match credits::parse_usd(&req.usd) {
        Ok(v) => v,
        Err(e) => return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    };
    let identity = req.identity.trim().to_lowercase();
    match credits::debit(&identity, usd6, req.reason.as_deref().unwrap_or("debit")).await {
        Ok(_) => (StatusCode::OK, Json(json!({ "account": credits::account_view(&identity).await }))).into_response(),
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
            if std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1" {
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

    // Out of credit ⇒ only the owner may still run tasks. Every run spends
    // real money on someone's account, so a peer whose balance is gone is
    // turned away here rather than discovering it as a failed job — but the
    // console never locks its own owner out of it.
    {
        let identity = if user_address.trim().is_empty() {
            "local".to_string()
        } else {
            user_address.to_lowercase()
        };
        let (allowed, why) = can_spend(&identity).await;
        if !allowed {
            return (
                StatusCode::PAYMENT_REQUIRED,
                Json(json!({ "error": why, "out_of_credit": true })),
            )
                .into_response();
        }
    }

    // A locked vault refuses new work rather than writing it down in the
    // clear: the whole point is that nothing of yours lands in the ledger
    // unsealed. The console turns this into the unlock prompt.
    if crate::vault::is_enabled(&user_address) && !crate::vault::is_unlocked(&user_address) {
        return (
            StatusCode::LOCKED,
            Json(json!({
                "error": "your task vault is locked — enter your password to start a task",
                "vault_locked": true
            })),
        )
            .into_response();
    }

    // Peers (every non-owner) default + sandbox the job's work_dir to their
    // private root (~/.mod/peers/<addr>). Owner / local-mode work in the module
    // tree. The whitelist no longer widens this — whitelisted users are peers.
    // The one exception is a live QR invite: resolve_path lets its holder into
    // exactly the modules the invite named (userspace::invited_roots).
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
            Err(e) => {
                // Name what they DO hold — "outside your workspace" reads like
                // a bug when you're holding an invite to another module.
                let held = match auth::grant_edit_scope(&user_address) {
                    Some(auth::EditScope::Modules(list)) => {
                        format!(" — your invite covers {}", list.join(", "))
                    }
                    Some(auth::EditScope::All) => String::new(),
                    None => " — ask the owner for an edit invite".to_string(),
                };
                return (
                    StatusCode::FORBIDDEN,
                    Json(json!({ "error": format!("work_dir outside your workspace: {e}{held}") })),
                ).into_response();
            }
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
        // Surface where the job actually lands (an invited run lands in the
        // module, not the workspace) so the log doesn't imply the wrong root.
        let landed = req.work_dir.clone().unwrap_or_else(|| ws.display().to_string());
        tracing::info!(addr = %user_address, dir = %landed, "scoped submit");
    }

    // ↻ Replay in place — the caller is redoing an existing card rather than
    // filing a new one. Overwriting a task is destructive (its output and its
    // bundle CID are gone), so the card has to be theirs and it has to be
    // finished; anything else falls back to nothing rather than quietly
    // minting a new task the user didn't ask for.
    if let Some(target) = req.replace_job_id.clone().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()) {
        let Some(existing) = mgr.get_job(&target) else {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": format!("no task {target} to redo") })),
            )
                .into_response();
        };
        let mine = !user_address.is_empty()
            && existing.user_address.eq_ignore_ascii_case(&user_address);
        let local_mode = std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1";
        if !(mine || auth::is_owner(&user_address) || local_mode) {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({ "error": "that task belongs to someone else — replay it as a new task" })),
            )
                .into_response();
        }
        if matches!(existing.status, JobStatus::Running | JobStatus::Pending) {
            return (
                StatusCode::CONFLICT,
                Json(json!({ "error": "that task is still running — cancel it before redoing it" })),
            )
                .into_response();
        }
        req.replace_job_id = Some(existing.id);
    }

    req.user_address = Some(user_address);

    let job = mgr.submit(req).await;
    (StatusCode::CREATED, Json(json!(job))).into_response()
}

/// The address this (optional) bearer token belongs to — "" for anonymous
/// readers. Only used to decide whose sealed tasks a response may open.
fn reader(headers: &axum::http::HeaderMap) -> String {
    auth::extract_address_from_headers(headers).unwrap_or_default()
}

/// A job carries its module's name, prompt and output, so a task run inside
/// a private module is as private as the tree it edited: it leaves the public
/// ledger for everyone but that module's owner.
fn job_hidden(hidden: &std::collections::HashSet<String>, work_dir: &str) -> bool {
    if hidden.is_empty() {
        return false;
    }
    crate::privacy::module_of_work_dir(work_dir)
        .map(|m| hidden.contains(&m.to_lowercase()))
        .unwrap_or(false)
}

async fn list_jobs(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
) -> impl IntoResponse {
    // Public code ledger: every task is world-readable, no auth required —
    // except the content of vaulted tasks, which reads as sealed for everyone
    // but its own author with an unlocked session, and tasks against private
    // modules, which are gone entirely.
    let who = reader(&headers);
    let hidden = crate::privacy::hidden_names(&who);
    let mut jobs = mgr.list_jobs();
    jobs.retain(|job| !job_hidden(&hidden, &job.work_dir));
    for job in &mut jobs {
        crate::vault::unmask_job(job, &who);
    }
    Json(json!({ "jobs": jobs, "count": jobs.len() }))
}

/// Resolve a task bundle by its localfs CID — both shared-session links
/// (`?task=<cid>`) and replay QRs (`?replay=<cid>`) land here. The local
/// jobs ledger answers first;
/// otherwise the bundle is pulled from the shared localfs blob store, so a
/// QR minted on another console still replays as long as the blob is
/// reachable. Public for the same reason /jobs/:id is: the ledger is a
/// world-readable code trail.
async fn get_task_by_cid(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(cid): Path<String>,
) -> impl IntoResponse {
    let who = reader(&headers);
    if let Some(mut job) = mgr.get_job_by_cid(&cid) {
        if job_hidden(&crate::privacy::hidden_names(&who), &job.work_dir) {
            return (
                StatusCode::NOT_FOUND,
                Json(json!({ "error": "No task found for that CID" })),
            )
                .into_response();
        }
        crate::vault::unmask_job(&mut job, &who);
        let module = crate::snapshots::module_for_work_dir(&job.work_dir).map(|(n, _)| n);
        let version_cid = module.as_deref().and_then(|name| {
            read_versions(name)
                .iter()
                .rev()
                .find(|v| v.job_id.as_deref() == Some(job.id.as_str()))
                .map(|v| v.cid.clone())
        });
        let bundle = crate::jobs::task_bundle_json(&job, module, version_cid);
        return (StatusCode::OK, Json(bundle)).into_response();
    }
    let fetched = tokio::task::spawn_blocking(move || crate::jobs::fetch_task_bundle(&cid))
        .await
        .ok()
        .flatten();
    match fetched {
        // A bundle minted by a vaulted wallet carries ciphertext — it opens
        // here for its author (unlocked) and reads as sealed for anyone else.
        Some(mut bundle) => {
            crate::vault::unmask_bundle(&mut bundle, &who);
            (StatusCode::OK, Json(bundle)).into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "No task found for that CID" })),
        )
            .into_response(),
    }
}

async fn get_job(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    // Public code ledger: any task is world-readable by id (sealed tasks
    // read as sealed — see list_jobs).
    let who = reader(&headers);
    match mgr.get_job(&id) {
        Some(mut job) if !job_hidden(&crate::privacy::hidden_names(&who), &job.work_dir) => {
            crate::vault::unmask_job(&mut job, &who);
            (StatusCode::OK, Json(json!(job))).into_response()
        }
        // Missing and private-to-someone-else answer the same way: a private
        // module's tasks aren't discoverable by id either.
        _ => (
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
struct SteerRequest {
    message: String,
}

/// Guide a running job mid-task — the console's "add a comment while the
/// agent works" feature (Claude Code steering). The message is written into
/// the CLI's stream-json stdin and picked up by the agent at its next tool
/// boundary. Same ownership rule as cancel: your own jobs, or any if owner.
async fn message_job(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<SteerRequest>,
) -> impl IntoResponse {
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
                    Json(json!({ "error": "You can only guide your own jobs" })),
                )
                    .into_response();
            }
        }
    } else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": "Job not found" })),
        )
            .into_response();
    }

    let message = req.message.trim().to_string();
    if message.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "message is required" })),
        )
            .into_response();
    }

    match mgr.steer_job(&id, &message).await {
        Ok(()) => (StatusCode::OK, Json(json!({ "success": true }))).into_response(),
        Err(e) => (StatusCode::CONFLICT, Json(json!({ "error": e }))).into_response(),
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
    // Module directory to read config.json from when name resolution fails —
    // nested mods (bloctime/app, agent skills) live below the orbit/{name}
    // lookup and their rel elides src/, so the console passes the dir it got
    // from /modules instead.
    path: Option<String>,
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
    // Private modules are invisible to non-owners: deny a root inside one,
    // and prune their dirs out of wider walks below.
    if let Err(e) = crate::privacy::read_guard(&caller, root) {
        return Json(json!({ "tree": [], "error": e }));
    }
    let denied = crate::privacy::denied_roots(&caller);

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
        denied: &[std::path::PathBuf],
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
            if denied.iter().any(|d| d.as_path() == path) { continue; }
            let full = path.to_string_lossy().to_string();
            let display = full.replacen(home, "~", 1);
            let is_dir = path.is_dir();
            let children = if is_dir { walk(root, &path, depth + 1, max_depth, home, cids, denied) } else { vec![] };
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

    let tree = walk(root, root, 0, max_depth, &home, &cid_map, &denied);
    Json(json!({ "tree": tree, "path": raw_path, "root_hash": root_hash }))
}

async fn list_repos(
    headers: axum::http::HeaderMap,
    Query(params): Query<RepoQuery>,
) -> impl IntoResponse {
    // The folder picker is a module listing by another name — a private
    // module must not surface here either, not even as a path.
    let hidden = crate::privacy::hidden_names(&reader(&headers));
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
                if hidden.contains(&name.to_lowercase()) { continue; }
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
/// Depth-capped and skipping dot/build-fork/dependency dirs so scanning the whole
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

/// Does this module speak MCP? Four tells, cheapest first: a config that
/// declares one (`"mcp"`, `"mcp_port"`), an fn whose name says so, a server
/// file named after the protocol, and — last — a `/mcp` route inside one of
/// the handful of entry files a module keeps its router in. Everything is a
/// fixed path list rather than a walk, so asking this of all ~300 modules on
/// every /modules call stays as cheap as the config read next to it.
fn mcp_detect(path: &std::path::Path, name: &str, declared: bool, fns: &[String]) -> bool {
    if declared { return true; }
    if fns.iter().any(|f| f.to_lowercase().contains("mcp")) { return true; }
    let dirs = ["", "src/", "api/", "src/api/", "server/"];
    for d in dirs.iter() {
        for f in ["mcp.py", "mcp_server.py", "mcp.js", "mcp.ts"] {
            if path.join(format!("{}{}", d, f)).is_file() { return true; }
        }
    }
    // Layouts that nest the implementation under the module's own name
    // (bt/bt/server.py) — one more dir, same two questions.
    let owned = format!("{}/", name);
    for d in dirs.iter().map(|d| d.to_string()).chain(std::iter::once(owned.clone())) {
        for f in ["mod.py", "api.py", "server.py", "app.py", "main.py"] {
            let p = path.join(format!("{}{}", d, f));
            let Ok(meta) = std::fs::metadata(&p) else { continue };
            // Entry files are small; a megabyte-plus file is a data blob that
            // would cost more to scan than the answer is worth.
            if !meta.is_file() || meta.len() > 1_500_000 { continue; }
            if std::fs::read_to_string(&p).map(|t| t.contains("/mcp")).unwrap_or(false) { return true; }
        }
    }
    false
}

/// Nested mods — subdirectories of a module that are mods in their own right
/// (they carry their own mod.py or config.json, addressable as
/// `m {module}/{rel}`). Depth-capped with the same skip list as newest_mtime
/// so computing this for the whole fleet on each /modules call stays cheap.
fn find_nested_mods(root: &std::path::Path, dir: &std::path::Path, depth: usize, out: &mut Vec<serde_json::Value>) {
    if depth == 0 { return; }
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for entry in rd.flatten() {
        let path = entry.path();
        if !path.is_dir() { continue; }
        let name = entry.file_name().to_string_lossy().to_string();
        if name.starts_with('.') || name.starts_with('_')
            || matches!(name.as_str(), "node_modules" | "target" | "dist" | "build" | "out" | "__pycache__" | "venv" | "logs" | "cache" | "artifacts")
        { continue; }
        // `src` is a module's implementation directory, never a mod of its
        // own: markers under */src belong to */ (credited to the parent when
        // it was visited). Walk through it transparently so mods deeper in
        // (src/app, src/skills/*) still surface — with the segment elided.
        if name == "src" {
            find_nested_mods(root, &path, depth - 1, out);
            continue;
        }
        let has_mod_py = path.join("mod.py").is_file() || path.join("src").join("mod.py").is_file();
        let config_path = {
            let direct = path.join("config.json");
            if direct.is_file() { direct } else { path.join("src").join("config.json") }
        };
        let has_config = config_path.is_file();
        if has_mod_py || has_config {
            let rel = path.strip_prefix(root)
                .map(|p| p.components()
                    .map(|c| c.as_os_str().to_string_lossy())
                    .filter(|c| c != "src")
                    .collect::<Vec<_>>()
                    .join("/"))
                .unwrap_or_else(|_| name.clone());
            if out.iter().any(|m| m["rel"].as_str() == Some(rel.as_str())) {
                find_nested_mods(root, &path, depth - 1, out);
                continue;
            }
            let config: Option<serde_json::Value> = if has_config {
                std::fs::read_to_string(&config_path).ok().and_then(|c| serde_json::from_str(&c).ok())
            } else { None };
            // A config.json makes the row a module in its own right — ship
            // enough of it (path, urls, fns) for the console to open the mod
            // in place, not just copy its address.
            let urls = config.as_ref().and_then(|c| c.get("urls"));
            let app_url = urls.and_then(|u| u.get("app")).and_then(|v| v.as_str())
                .or_else(|| config.as_ref().and_then(|c| c.get("app_url")).and_then(|v| v.as_str()));
            let api_url = urls.and_then(|u| u.get("api")).and_then(|v| v.as_str())
                .or_else(|| config.as_ref().and_then(|c| c.get("api_url")).and_then(|v| v.as_str()));
            let fns: Vec<String> = config.as_ref()
                .and_then(|c| c.get("fns")).and_then(|v| v.as_array())
                .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                .unwrap_or_default();
            out.push(json!({
                "rel": rel,
                "name": config.as_ref().and_then(|c| c.get("name")).and_then(|v| v.as_str()).unwrap_or(&name),
                "description": config.as_ref().and_then(|c| c.get("description")).and_then(|v| v.as_str()),
                "has_config": has_config,
                "has_mod_py": has_mod_py,
                "path": path.to_string_lossy(),
                "app_url": app_url,
                "api_url": api_url,
                "version": config.as_ref().and_then(|c| c.get("version")).and_then(|v| v.as_str()),
                "fns": fns,
            }));
        }
        // A nested mod can itself contain mods (agent/src → agent/src/skills/*),
        // so recursion continues past a hit.
        find_nested_mods(root, &path, depth - 1, out);
    }
}

/// List orbit and core modules with config.json data (app_url, api_url, etc.)
///
/// Public — the hub is browsable signed-out. Private modules are the one
/// exception: they are dropped for everyone but their owner, so a guest's
/// card wall is exactly the public fleet. Rows the caller CAN see carry
/// `private: true` so the hub can badge them.
async fn list_modules(
    headers: axum::http::HeaderMap,
    Query(params): Query<ModuleQuery>,
) -> impl IntoResponse {
    let caller = reader(&headers);
    let hidden = crate::privacy::hidden_names(&caller);
    let private_names: std::collections::HashSet<String> = crate::privacy::records()
        .into_iter()
        .filter(|r| r.enabled)
        .map(|r| r.module.to_lowercase())
        .collect();
    let query = params.q.unwrap_or_default().to_lowercase();
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    // Public endpoint: the anchor hint may only ever point inside the module
    // tree, so it can't be turned into a directory scanner for the host.
    let anchor = safe_anchor(params.anchor);

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
            "mcp": false,
            "mcp_tools": serde_json::Value::Null,
            // Every module in the tree is nested under the root — the list
            // is filled from the scan results after the loop below, so the
            // walk isn't done twice.
            "mods": Vec::<serde_json::Value>::new(),
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
                    "mcp": false,
                    "mcp_tools": serde_json::Value::Null,
                    // A tree root's nested mods are its child modules —
                    // filled from the scan results after the loop below.
                    "mods": Vec::<serde_json::Value>::new(),
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

                // A private module doesn't exist as far as anyone but its
                // owner is concerned — not even as a name on the hub.
                if hidden.contains(&name.to_lowercase()) { continue; }

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
                // MCP: `"mcp": {...}` / `"mcp_port"` in config is the
                // module saying so itself; mcp_detect() falls back to disk.
                let mut mcp_declared = false;
                let mut mcp_tools: Option<usize> = None;

                let config_paths = vec![
                    path.join("config.json"),
                    path.join(&name).join("config.json"),
                    // */src is the module itself, not a nested dir of note —
                    // a config living there names this module.
                    path.join("src").join("config.json"),
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
                                if config.as_object().map(|o| o.keys().any(|k| k == "mcp" || k.starts_with("mcp_"))).unwrap_or(false) {
                                    mcp_declared = true;
                                }
                                // A declared tool list is the honest count;
                                // everything else leaves the badge countless.
                                if let Some(n) = config.get("mcp")
                                    .and_then(|m| m.get("tools").or_else(|| m.get("native_tools")))
                                    .and_then(|t| t.as_array()).map(|a| a.len())
                                {
                                    if n > 0 { mcp_tools = Some(n); }
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

                // Dir hints for the frontend badges. */src is transparent —
                // src/app and src/api count as the module's own app/api.
                let has_app_dir = path.join("app").is_dir() || path.join("src").join("app").is_dir();
                // Check for server/ directory as hint for backend
                let has_server_dir = path.join("server").is_dir() || path.join("src").join("server").is_dir();
                // Check for api/ directory
                let has_api_dir = path.join("api").is_dir() || path.join("src").join("api").is_dir();
                // Speaks MCP — the hub's badge and its "mcp" filter.
                let has_mcp = mcp_detect(&path, &name, mcp_declared, &fns);

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

                // Mods nested inside this module (m {name}/{rel}) — depth 4
                // reaches e.g. agent/src/skills/bash without walking deep trees.
                let mut nested_mods: Vec<serde_json::Value> = Vec::new();
                find_nested_mods(&path, &path, 4, &mut nested_mods);
                nested_mods.sort_by(|a, b| {
                    a["rel"].as_str().unwrap_or("").cmp(b["rel"].as_str().unwrap_or(""))
                });

                // A directory is only a mod where a config.json or mod.py
                // actually is — its own (src/mod.py counts: */src IS */),
                // or one nested deeper (archive/dev). Marker-less dirs
                // (empty scaffolds, stray folders) would otherwise surface
                // as phantom hub modules.
                let has_mod_py = path.join("mod.py").is_file() || path.join("src").join("mod.py").is_file();
                if !has_config && !has_mod_py && nested_mods.is_empty() {
                    continue;
                }

                modules.push(json!({
                    "name": name,
                    "path": full_path,
                    "display": display_path,
                    "category": category,
                    "has_config": has_config,
                    "has_mod_py": has_mod_py,
                    "app_url": app_url,
                    "api_url": api_url,
                    "description": description,
                    "fns": fns,
                    "has_app_dir": has_app_dir,
                    "has_server_dir": has_server_dir,
                    "has_api_dir": has_api_dir,
                    "mcp": has_mcp,
                    "mcp_tools": mcp_tools,
                    "mods": nested_mods,
                    "owner": host_owner.clone().or(owner),
                    "version": version,
                    "cid": cid,
                    "deps": deps,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    // Only ever true on a row the caller is allowed to see —
                    // hidden ones never made it this far.
                    "private": private_names.contains(&name.to_lowercase()),
                }));
            }
        }
    }

    // The tree roots ("mod" and the orbit/core roots) skipped the nested-mods
    // filesystem walk above — their nested mods ARE the fleet just scanned.
    // Fill their `mods` from the collected entries instead of re-walking, and
    // carry each row's canonical address: these are top-level modules, so the
    // protocol reaches them as `m {name}`, not `m mod/{rel}`.
    let orbit_root = format!("{}/mod/orbit", anchor);
    let core_root = format!("{}/mod/core", anchor);
    let mut root_mods: Vec<serde_json::Value> = Vec::new();
    let mut tree_mods: std::collections::HashMap<String, Vec<serde_json::Value>> =
        std::collections::HashMap::new();
    for m in &modules {
        let category = m["category"].as_str().unwrap_or("");
        if category != "orbit" && category != "core" { continue; }
        let path_str = m["path"].as_str().unwrap_or("");
        let path = std::path::Path::new(path_str);
        let has_config = m["has_config"].as_bool().unwrap_or(false);
        let has_mod_py = path.join("mod.py").is_file() || path.join("src").join("mod.py").is_file();
        // Bare directories under orbit/ and core/ appear in the hub, but only
        // ones carrying a config.json or mod.py are mods in their own right.
        if !has_config && !has_mod_py { continue; }
        let name = m["name"].as_str().unwrap_or("");
        let dir = path.file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| name.to_string());
        let row = |rel: String| json!({
            "rel": rel,
            "name": name,
            "description": m["description"],
            "has_config": has_config,
            "has_mod_py": has_mod_py,
            "addr": format!("m {}", name),
            "path": path_str,
            "app_url": m["app_url"],
            "api_url": m["api_url"],
        });
        if path_str == orbit_root || path_str == core_root {
            // The orbit/core roots are themselves mods nested under `mod`.
            root_mods.push(row(dir));
        } else {
            root_mods.push(row(format!("{}/{}", category, dir)));
            tree_mods.entry(category.to_string()).or_default().push(row(dir));
        }
    }
    root_mods.sort_by(|a, b| a["rel"].as_str().unwrap_or("").cmp(b["rel"].as_str().unwrap_or("")));
    for list in tree_mods.values_mut() {
        list.sort_by(|a, b| a["rel"].as_str().unwrap_or("").cmp(b["rel"].as_str().unwrap_or("")));
    }
    for m in modules.iter_mut() {
        let path_str = m["path"].as_str().unwrap_or("").to_string();
        if m["category"].as_str() == Some("root") {
            m["mods"] = json!(root_mods.clone());
        } else if path_str == orbit_root {
            m["mods"] = json!(tree_mods.get("orbit").cloned().unwrap_or_default());
        } else if path_str == core_root {
            m["mods"] = json!(tree_mods.get("core").cloned().unwrap_or_default());
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
async fn list_folders(
    headers: axum::http::HeaderMap,
    Query(params): Query<FolderQuery>,
) -> impl IntoResponse {
    // Folder names inside a private module are its shape — the walk stops at
    // the root of every tree this caller may not read.
    let denied = crate::privacy::denied_roots(&reader(&headers));
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let raw_path = params.path.unwrap_or_else(|| "~/mod".to_string());
    let resolved = raw_path.replacen("~", &home, 1);
    let max_depth = params.depth.unwrap_or(2);
    let query = params.q.unwrap_or_default().to_lowercase();

    let root = std::path::Path::new(&resolved);
    if !root.is_dir() {
        return Json(json!({ "folders": [], "error": "Directory not found" }));
    }
    // Pointing the walk straight at a private tree is the same leak as
    // reaching it from above: a caller who can't see the module can't see
    // that the directory is there at all.
    {
        let canon = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
        if denied.iter().any(|d| canon.starts_with(d)) {
            return Json(json!({ "folders": [], "error": "Directory not found" }));
        }
    }

    fn walk_folders(
        dir: &std::path::Path,
        base: &std::path::Path,
        depth: usize,
        max_depth: usize,
        query: &str,
        home: &str,
        denied: &[std::path::PathBuf],
        results: &mut Vec<serde_json::Value>,
    ) {
        if depth > max_depth { return; }
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        for entry in rd.flatten() {
            let path = entry.path();
            if !path.is_dir() { continue; }
            // A private module is not a folder as far as anyone else is
            // concerned: neither listed nor descended into.
            if denied.iter().any(|root| path.starts_with(root)) { continue; }
            let name = entry.file_name().to_string_lossy().to_string();
            if name.starts_with('.') || name == "node_modules" || name == "__pycache__"
                || name == "target" || name == "build" || name == "dist"
                || name == ".next" || name == "venv" || name == ".venv" { continue; }
            let full = path.to_string_lossy().to_string();
            let rel = path.strip_prefix(base).unwrap_or(&path).to_string_lossy().to_string();
            if !query.is_empty() && !rel.to_lowercase().contains(query) {
                // still recurse — subfolders might match
                walk_folders(&path, base, depth + 1, max_depth, query, home, denied, results);
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
            walk_folders(&path, base, depth + 1, max_depth, query, home, denied, results);
        }
    }

    let mut results = Vec::new();
    walk_folders(root, root, 0, max_depth, &query, &home, &denied, &mut results);
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
async fn suggest_folders(
    headers: axum::http::HeaderMap,
    Query(params): Query<SuggestQuery>,
) -> impl IntoResponse {
    // embedcode indexes the whole tree and answers with paths AND file
    // previews, so hits inside a private module are dropped before scoring.
    let denied = crate::privacy::denied_roots(&reader(&headers));
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
        if !file_path.is_empty() {
            let p = std::path::Path::new(file_path);
            let canon = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
            if denied.iter().any(|d| canon.starts_with(d)) { continue; }
        }
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
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Query(params): Query<ModuleQuery>,
) -> impl IntoResponse {
    // Public reader, so everything caller-supplied is bounded: the name may
    // not climb out of the tree, and the anchor/path hints are confined to it.
    if !valid_module_name(&name) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "bad module name" })),
        )
            .into_response();
    }
    // Private modules stay owner-only, config.json included — it carries the
    // module's ports, routes, and owner key.
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let anchor = safe_anchor(params.anchor);

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
                if crate::privacy::read_guard(&caller, config_path).is_err() {
                    continue;
                }
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

    // Name resolution only reaches orbit/{name} and core/{name} — nested mods
    // (bloctime/app, agent skills whose rel elides src/) live deeper, so fall
    // back to the module dir the console got from /modules. Only a config.json
    // directly in that dir (or its src/) is read — same exposure class as the
    // name lookup above.
    if let Some(base) = params.path.as_deref().and_then(confine_to_module_tree) {
        for config_path in [base.join("config.json"), base.join("src").join("config.json")] {
            if crate::privacy::read_guard(&caller, &config_path).is_err() {
                continue;
            }
            if let Ok(content) = std::fs::read_to_string(&config_path) {
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

    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": format!("No config.json found for module '{}'", name) })),
    )
        .into_response()
}

/// Delete a module directory. Owner-only: module creation is owner-only, so
/// every module in the tree is the configured owner's to remove. Deleting any
/// module other than build itself additionally requires sudo (fresh x-sudo
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

    // Search orbit/ and core/ for the module. A dir holding module content
    // sorts ahead of one that merely exists, so a stray `orbit/<name>` can
    // never shadow the real module in core/ (the sort is stable, so orbit
    // still wins when both are real modules).
    let mut search_dirs = vec![
        format!("{}/mod/mod/orbit/{}", home, name),
        format!("{}/mod/mod/core/{}", home, name),
    ];
    search_dirs.sort_by_key(|d| !crate::snapshots::is_module_dir(std::path::Path::new(d)));

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

    // Deleting any module other than build itself is a privileged cross-module
    // operation — require sudo (fresh x-sudo signature or an open sudo session).
    if name != "build" {
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
    /// Report everything the rename would touch and change nothing. The plan
    /// it returns has the same shape as the report a real rename returns.
    #[serde(default)]
    dry_run: bool,
    /// How far to chase the old name through the module's own files:
    ///   "paths" (default) — only the wiring a module runs on: its place in
    ///                       the tree, its route prefix, its state dir, its
    ///                       pm2 process names, `m <mod>/fn` call forms.
    ///   "all"             — that, plus every whole-word mention (prose too).
    ///   "none"            — move the directory, touch nothing inside it.
    #[serde(default)]
    refs: Option<String>,
    /// Bring the module back up under its new name if it was running.
    #[serde(default = "rename_default_true")]
    restart: bool,
    /// Re-generate and reload the fleet's caddy routes afterwards.
    #[serde(default = "rename_default_true")]
    reroute: bool,
}

fn rename_default_true() -> bool {
    true
}

/// Directories a rename never rewrites: build output, vendored dependencies,
/// git internals. A hit in there is either generated or somebody else's code.
const RENAME_SKIP_DIRS: &[&str] = &[
    ".git",
    "node_modules",
    ".next",
    ".next-old",
    ".next-stage",
    "target",
    "vendor",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    ".turbo",
    ".history",
];

/// The literal forms in which a module's own name is *wiring* rather than
/// prose: where it sits in the tree, the route prefix it answers on, its
/// state directory, its pm2 process names, and the `m <mod>/fn` call form.
/// Deliberately narrow — modules called `store`, `chain` or `build` say their
/// own name in English all day, and English is not wiring.
fn rename_wiring_patterns(old: &str, new: &str) -> Vec<(String, String)> {
    let mut v: Vec<(String, String)> = Vec::new();
    for base in ["mod/orbit", "mod/core"] {
        v.push((format!("{base}/{old}"), format!("{base}/{new}")));
    }
    v.push((format!(".mod/{old}"), format!(".mod/{new}")));
    for q in ['"', '\'', '`'] {
        v.push((format!("{q}/{old}{q}"), format!("{q}/{new}{q}")));
        v.push((format!("{q}/{old}/"), format!("{q}/{new}/")));
    }
    for tail in ["api", "_api", "app", "mcp", "docs", "health"] {
        v.push((format!("/{old}/{tail}"), format!("/{new}/{tail}")));
    }
    for tail in ["-api", "-app", "-jobs", "-web", "-server", "-worker", "-mcp"] {
        v.push((format!("{old}{tail}"), format!("{new}{tail}")));
    }
    v.push((format!("m {old}/"), format!("m {new}/")));
    v.push((format!("mod('{old}')"), format!("mod('{new}')")));
    v.push((format!("mod(\"{old}\")"), format!("mod(\"{new}\")")));
    v
}

fn is_name_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-'
}

/// Replace `old` with `new` only where it stands as a whole word — so renaming
/// `bt` doesn't maul `debt`.
fn replace_whole_word(text: &str, old: &str, new: &str) -> (String, usize) {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0usize;
    let mut hits = 0usize;
    while let Some(rel) = text[i..].find(old) {
        let at = i + rel;
        let end = at + old.len();
        let before_ok = at == 0 || !is_name_char(bytes[at - 1] as char);
        let after_ok = end >= bytes.len() || !is_name_char(bytes[end] as char);
        out.push_str(&text[i..at]);
        if before_ok && after_ok {
            out.push_str(new);
            hits += 1;
        } else {
            out.push_str(old);
        }
        i = end;
    }
    out.push_str(&text[i..]);
    (out, hits)
}

fn apply_wiring(text: &str, pats: &[(String, String)]) -> (String, usize) {
    let mut out = text.to_string();
    let mut hits = 0usize;
    for (from, to) in pats {
        if from == to {
            continue;
        }
        let n = out.matches(from.as_str()).count();
        if n > 0 {
            hits += n;
            out = out.replace(from.as_str(), to.as_str());
        }
    }
    (out, hits)
}

/// Every rewritable text file under a module, skipping build output and
/// anything that looks binary or oversized.
fn collect_rewritable_files(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for e in entries.flatten() {
        let Ok(ty) = e.file_type() else { continue };
        if ty.is_symlink() {
            continue;
        }
        let name = e.file_name().to_string_lossy().to_string();
        if ty.is_dir() {
            if RENAME_SKIP_DIRS.contains(&name.as_str()) {
                continue;
            }
            collect_rewritable_files(&e.path(), out);
        } else if ty.is_file() {
            let big = e.metadata().map(|m| m.len() > 2_000_000).unwrap_or(true);
            if !big {
                out.push(e.path());
            }
        }
    }
}

fn read_text_file(path: &std::path::Path) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    if bytes.iter().take(8192).any(|b| *b == 0) {
        return None; // binary
    }
    String::from_utf8(bytes).ok()
}

/// Walk the module and either count (dry run) or perform the rewrite. Returns
/// (per-file hits, files changed, total occurrences, errors).
fn rewrite_module_refs(
    root: &std::path::Path,
    old: &str,
    new: &str,
    mode: &str,
    write: bool,
) -> (Vec<serde_json::Value>, usize, usize, Vec<String>) {
    let mut files = Vec::new();
    collect_rewritable_files(root, &mut files);
    files.sort();
    let pats = rename_wiring_patterns(old, new);
    let mut per_file = Vec::new();
    let mut changed = 0usize;
    let mut total = 0usize;
    let mut errors = Vec::new();
    for path in files {
        let Some(text) = read_text_file(&path) else {
            continue;
        };
        let (wired, wiring_hits) = apply_wiring(&text, &pats);
        // Word hits are counted on the ALREADY-wired text so the two numbers
        // add up instead of double-counting the same occurrence.
        let (worded, word_hits) = replace_whole_word(&wired, old, new);
        if wiring_hits == 0 && word_hits == 0 {
            continue;
        }
        let rel = path
            .strip_prefix(root)
            .unwrap_or(&path)
            .to_string_lossy()
            .to_string();
        let applied = match mode {
            "all" => wiring_hits + word_hits,
            "none" => 0,
            _ => wiring_hits,
        };
        per_file.push(json!({
            "path": rel,
            "wiring": wiring_hits,
            "words": word_hits,
            "applied": applied,
        }));
        if applied == 0 {
            continue;
        }
        total += applied;
        let updated = if mode == "all" { worded } else { wired };
        if write {
            match std::fs::write(&path, updated) {
                Ok(_) => changed += 1,
                Err(e) => errors.push(format!("{rel}: {e}")),
            }
        } else {
            changed += 1;
        }
    }
    (per_file, changed, total, errors)
}

/// Set config.json's `name` outright — the one reference that is definitional
/// rather than textual, so it never depends on a pattern matching.
fn set_config_name(root: &std::path::Path, new: &str) -> Option<String> {
    let cfg_path = root.join("config.json");
    let text = std::fs::read_to_string(&cfg_path).ok()?;
    let mut cfg: serde_json::Value = serde_json::from_str(&text).ok()?;
    let obj = cfg.as_object_mut()?;
    obj.insert("name".into(), json!(new));
    let out = serde_json::to_string_pretty(&cfg).ok()?;
    match std::fs::write(&cfg_path, out + "\n") {
        Ok(_) => None,
        Err(e) => Some(format!("config.json: {e}")),
    }
}

/// Everything on this host that is filed under the module's NAME rather than
/// its path: its own state dir, and the console's per-module records.
fn rename_state_paths(old: &str, new: &str) -> Vec<(std::path::PathBuf, std::path::PathBuf)> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let me = self_module_name();
    let pairs = vec![
        (format!("{home}/.mod/{old}"), format!("{home}/.mod/{new}")),
        (
            format!("{home}/.mod/{me}/versions/{old}.json"),
            format!("{home}/.mod/{me}/versions/{new}.json"),
        ),
        (
            format!("{home}/.mod/{me}/private/{old}.json"),
            format!("{home}/.mod/{me}/private/{new}.json"),
        ),
        (
            format!("{home}/.mod/{me}/github/{old}.json"),
            format!("{home}/.mod/{me}/github/{new}.json"),
        ),
        (
            format!("{home}/.mod/{me}/github/work/{old}"),
            format!("{home}/.mod/{me}/github/work/{new}"),
        ),
        (
            format!("{home}/.mod/{me}/screenshots/{old}.png"),
            format!("{home}/.mod/{me}/screenshots/{new}.png"),
        ),
        (
            format!("{home}/.mod/{me}/screenshots/{old}.fail"),
            format!("{home}/.mod/{me}/screenshots/{new}.fail"),
        ),
    ];
    pairs
        .into_iter()
        .map(|(a, b)| (std::path::PathBuf::from(a), std::path::PathBuf::from(b)))
        .collect()
}

/// This console's own module name — the one module it must not rename out
/// from under itself.
fn self_module_name() -> String {
    claude_module_dir()
        .file_name()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| "build".to_string())
}

/// Fleet registries keyed by module name: the router's per-module upstream
/// overrides and the activator's disabled/pinned lists. Left stale, the first
/// keeps routing a name that no longer exists and the second stops pinning a
/// module that is still meant to stay awake.
fn retarget_name_registries(old: &str, new: &str, write: bool) -> Vec<serde_json::Value> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let mut out = Vec::new();

    let caddy = std::path::PathBuf::from(format!("{home}/.mod/caddy/overrides.json"));
    if let Ok(text) = std::fs::read_to_string(&caddy) {
        if let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&text) {
            if let Some(obj) = v.as_object_mut() {
                if let Some(entry) = obj.remove(old) {
                    obj.insert(new.to_string(), entry);
                    let mut rec = json!({ "file": caddy.display().to_string(), "change": format!("route override {old} → {new}") });
                    if write {
                        if let Ok(s) = serde_json::to_string_pretty(&v) {
                            if let Err(e) = std::fs::write(&caddy, s + "\n") {
                                rec["error"] = json!(e.to_string());
                            }
                        }
                    }
                    out.push(rec);
                }
            }
        }
    }

    let act = std::path::PathBuf::from(format!("{home}/.mod/activator/overrides.json"));
    if let Ok(text) = std::fs::read_to_string(&act) {
        if let Ok(mut v) = serde_json::from_str::<serde_json::Value>(&text) {
            let mut touched: Vec<String> = Vec::new();
            for list in ["disabled", "pinned"] {
                if let Some(arr) = v.get_mut(list).and_then(|x| x.as_array_mut()) {
                    for item in arr.iter_mut() {
                        if item.as_str() == Some(old) {
                            *item = json!(new);
                            touched.push(list.to_string());
                        }
                    }
                }
            }
            if !touched.is_empty() {
                let mut rec = json!({ "file": act.display().to_string(), "change": format!("activator {} → {new}", touched.join("+")) });
                if write {
                    if let Ok(s) = serde_json::to_string_pretty(&v) {
                        if let Err(e) = std::fs::write(&act, s + "\n") {
                            rec["error"] = json!(e.to_string());
                        }
                    }
                }
                out.push(rec);
            }
        }
    }
    out
}

/// Sibling modules that declare this one in `deps` — their dependency is a
/// name, so a rename breaks it unless the name moves with it.
fn retarget_dependents(old: &str, new: &str, write: bool) -> Vec<serde_json::Value> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let mut out = Vec::new();
    for category in ["orbit", "core"] {
        let Ok(entries) = std::fs::read_dir(format!("{home}/mod/mod/{category}")) else {
            continue;
        };
        for e in entries.flatten() {
            let dir = e.path();
            if !dir.is_dir() {
                continue;
            }
            let cfg_path = dir.join("config.json");
            let Ok(text) = std::fs::read_to_string(&cfg_path) else {
                continue;
            };
            let Ok(mut cfg) = serde_json::from_str::<serde_json::Value>(&text) else {
                continue;
            };
            let Some(deps) = cfg.get_mut("deps").and_then(|d| d.as_array_mut()) else {
                continue;
            };
            let mut hit = false;
            for d in deps.iter_mut() {
                if d.as_str() == Some(old) {
                    *d = json!(new);
                    hit = true;
                }
            }
            if !hit {
                continue;
            }
            let who = dir
                .file_name()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_default();
            let mut rec = json!({ "module": who, "category": category, "change": format!("deps: {old} → {new}") });
            if write {
                if let Ok(s) = serde_json::to_string_pretty(&cfg) {
                    if let Err(err) = std::fs::write(&cfg_path, s + "\n") {
                        rec["error"] = json!(err.to_string());
                    }
                }
            }
            out.push(rec);
        }
    }
    out
}

/// Regenerate + reload the fleet's caddy routes so the module answers on its
/// new URL immediately. Best-effort: `m caddy/apply` validates and rolls back
/// on its own, and a router hiccup must not fail a rename already on disk.
fn reapply_routes() -> serde_json::Value {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let caddy_dir = format!("{home}/mod/mod/orbit/caddy");
    if !std::path::Path::new(&caddy_dir).is_dir() {
        return json!({ "ok": false, "skipped": "orbit/caddy is not on this host" });
    }
    match std::process::Command::new("bash")
        .arg("-lc")
        .arg("timeout 120 m caddy/apply")
        .current_dir(&caddy_dir)
        .output()
    {
        Ok(out) => {
            let tail = String::from_utf8_lossy(&out.stdout);
            let tail: Vec<&str> = tail.lines().collect();
            json!({
                "ok": out.status.success(),
                "output": tail[tail.len().saturating_sub(12)..].join("\n"),
                "error": String::from_utf8_lossy(&out.stderr).trim().chars().take(400).collect::<String>(),
            })
        }
        Err(e) => json!({ "ok": false, "error": e.to_string() }),
    }
}

/// The whole rename, off the async runtime: stop → move → rewrite → re-file →
/// re-route → start. Returns the report (or the plan, when `dry_run`).
#[allow(clippy::too_many_arguments)]
fn rename_execute(
    old: String,
    new: String,
    category: String,
    src: std::path::PathBuf,
    dest: std::path::PathBuf,
    mode: String,
    restart: bool,
    reroute: bool,
    dry_run: bool,
) -> serde_json::Value {
    let mut warnings: Vec<String> = Vec::new();

    // ── what is running right now ───────────────────────────────────
    let config = std::fs::read_to_string(src.join("config.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .unwrap_or_else(|| json!({}));
    let backend = process::select(&src, &config, &old);
    let procs = process::list(backend, &src, &old, &config).unwrap_or_default();
    let running: Vec<&process::Proc> = procs.iter().filter(|p| p.status == "online").collect();
    let proc_json: Vec<serde_json::Value> = procs.iter().map(|p| p.to_json()).collect();

    // ── what moves ──────────────────────────────────────────────────
    let state_moves: Vec<(std::path::PathBuf, std::path::PathBuf)> = rename_state_paths(&old, &new)
        .into_iter()
        .filter(|(from, _)| from.exists())
        .collect();

    if dry_run {
        let (ref_files, ref_changed, ref_hits, _) =
            rewrite_module_refs(&src, &old, &new, &mode, false);
        let state_json: Vec<serde_json::Value> = state_moves
            .iter()
            .map(|(a, b)| {
                json!({
                    "from": a.display().to_string(),
                    "to": b.display().to_string(),
                    "blocked": b.exists(),
                })
            })
            .collect();
        return json!({
            "ok": true,
            "dry_run": true,
            "old_name": old,
            "new_name": new,
            "category": category,
            "from": src.display().to_string(),
            "to": dest.display().to_string(),
            "processes": { "backend": backend.as_str(), "procs": proc_json, "will_restart": restart && !running.is_empty() },
            "refs": {
                "mode": mode,
                "files": ref_files.iter().take(60).cloned().collect::<Vec<_>>(),
                "files_total": ref_files.len(),
                "files_changed": ref_changed,
                "occurrences": ref_hits,
            },
            "state": state_json,
            "registries": retarget_name_registries(&old, &new, false),
            "dependents": retarget_dependents(&old, &new, false),
            "reroute": reroute,
        });
    }

    // ── stop, and forget the old supervisor entries ─────────────────
    let mut proc_log = String::new();
    if !procs.is_empty() {
        let (ok, out) = process::act(backend, "stop", &procs, &src, &old, &config);
        proc_log.push_str(out.trim());
        if !ok {
            warnings.push("could not stop the module cleanly before moving it".into());
        }
        if backend == process::Backend::Pm2 {
            // pm2 entries hold the OLD name and the OLD cwd; left behind, a
            // `pm2 resurrect` would raise ghosts pointing at a moved tree.
            let (_ok, out) = process::pm2_forget(&procs);
            proc_log.push('\n');
            proc_log.push_str(out.trim());
        }
    }

    // ── move the tree ───────────────────────────────────────────────
    if let Err(e) = std::fs::rename(&src, &dest) {
        let stopped = !procs.is_empty();
        return json!({
            "ok": false,
            "error": format!(
                "failed to move {} → {}: {e}{}",
                src.display(),
                dest.display(),
                if stopped { " — the module is still at its old name but STOPPED; start it again from the API/APP tiles" } else { "" }
            ),
            "processes": { "backend": backend.as_str(), "output": proc_log },
        });
    }

    // ── rewrite what points at the old name ─────────────────────────
    let mut ref_errors: Vec<String> = Vec::new();
    if let Some(e) = set_config_name(&dest, &new) {
        ref_errors.push(e);
    }
    let (ref_files, ref_changed, ref_hits, errs) =
        rewrite_module_refs(&dest, &old, &new, &mode, true);
    ref_errors.extend(errs);

    // ── re-file the host state that is keyed by name ────────────────
    let mut state_json = Vec::new();
    for (from, to) in state_moves {
        let mut rec = json!({ "from": from.display().to_string(), "to": to.display().to_string() });
        if to.exists() {
            rec["skipped"] = json!("target already exists");
            warnings.push(format!("left {} in place — {} already exists", from.display(), to.display()));
        } else {
            match std::fs::rename(&from, &to) {
                Ok(_) => rec["moved"] = json!(true),
                Err(e) => {
                    rec["error"] = json!(e.to_string());
                    warnings.push(format!("could not move {}: {e}", from.display()));
                }
            }
        }
        state_json.push(rec);
    }
    let registries = retarget_name_registries(&old, &new, true);
    let dependents = retarget_dependents(&old, &new, true);

    // A Next app carries its route prefix INTO the bundle (basePath), so a
    // moved-and-restarted prod app keeps serving assets under the old name
    // until it is rebuilt. Restarting it from the APP tile does that rebuild.
    for probe in [".next", "app/.next", "src/app/.next"] {
        if dest.join(probe).is_dir() {
            warnings.push(format!(
                "{new}'s app bundle was built for /{old} — restart it from the APP tile (that rebuilds a prod Next app) so its assets move to /{new}"
            ));
            break;
        }
    }

    // ── re-route, then bring it back up under the new name ──────────
    let routes = if reroute { reapply_routes() } else { json!({ "skipped": true }) };

    let mut started = json!({ "attempted": false });
    if restart && !running.is_empty() {
        let config = std::fs::read_to_string(dest.join("config.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .unwrap_or_else(|| json!({}));
        let backend = process::select(&dest, &config, &new);
        let procs = process::list(backend, &dest, &new, &config).unwrap_or_default();
        let (ok, out) = process::act(backend, "start", &procs, &dest, &new, &config);
        if backend == process::Backend::Pm2 {
            process::pm2_save();
        }
        if !ok {
            warnings.push(format!("{new} did not come back up — start it from the APP/API tiles"));
        }
        started = json!({ "attempted": true, "ok": ok, "backend": backend.as_str(), "output": out.trim() });
    } else if !running.is_empty() {
        warnings.push(format!("{new} is stopped — it was running before the rename"));
    }

    json!({
        "ok": true,
        "success": true,
        "dry_run": false,
        "old_name": old,
        "new_name": new,
        "category": category,
        "from": src.display().to_string(),
        "to": dest.display().to_string(),
        "processes": { "backend": backend.as_str(), "stopped": proc_json, "output": proc_log, "started": started },
        "refs": {
            "mode": mode,
            "files": ref_files.iter().take(60).cloned().collect::<Vec<_>>(),
            "files_total": ref_files.len(),
            "files_changed": ref_changed,
            "occurrences": ref_hits,
            "errors": ref_errors,
        },
        "state": state_json,
        "registries": registries,
        "dependents": dependents,
        "caddy": routes,
        "warnings": warnings,
    })
}

/// PUT /modules/:name/rename — rename a module and everything wired to its
/// name: the directory, its config, the references inside its own files, the
/// host state filed under it (`~/.mod/<name>`, versions, github link,
/// screenshots), the router override, the activator's lists, sibling `deps`,
/// and the supervisor entries — stopping it first and starting it again after
/// if it was up. `dry_run` returns the same report without doing any of it.
/// Only the module's owner or the system owner may rename.
async fn rename_module(
    headers: axum::http::HeaderMap,
    State(_mgr): State<AppState>,
    Path(name): Path<String>,
    Json(body): Json<RenameRequest>,
) -> impl IntoResponse {
    let new_name = body.new_name.trim().to_string();
    if !valid_module_slug(&new_name) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "module name must be 1–64 chars of [a-zA-Z0-9_-]" })),
        )
            .into_response();
    }
    if new_name == name {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "that is already its name" })),
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
            if local_mode() {
                String::new()
            } else {
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(json!({ "error": "Authentication required" })),
                )
                    .into_response();
            }
        }
    };

    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());

    // Search orbit/ and core/ for the module — real module dirs first, so a
    // stray `orbit/<name>` cannot shadow the real module in core/.
    let mut search_dirs = vec![
        ("orbit", format!("{}/mod/mod/orbit/{}", home, name)),
        ("core", format!("{}/mod/mod/core/{}", home, name)),
    ];
    search_dirs.sort_by_key(|(_, d)| !crate::snapshots::is_module_dir(std::path::Path::new(d)));

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

    if !local_mode() && !is_sys_owner && !is_mod_owner {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "You can only rename modules you own" })),
        )
            .into_response();
    }

    // The console cannot rename itself: the tree it would move is the one it
    // is running from, the state dir it would re-file is its own, and the
    // process it would stop is the one answering this request.
    if name == self_module_name() {
        return (
            StatusCode::CONFLICT,
            Json(json!({
                "error": format!("'{name}' is this console's own module — it can't move the ground it stands on. Rename it from another console, or by hand while it is stopped."),
            })),
        )
            .into_response();
    }

    // Renaming a module the caller does not personally own is privileged.
    // A dry run changes nothing, so it stays on the plain owner gate.
    if !is_mod_owner && !body.dry_run {
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
    // Names are path-derived and core wins over orbit: an orbit module named
    // after a core one is unreachable dead code, so refuse the collision
    // rather than quietly burying the module under a name that never routes.
    let shadowed = format!("{}/mod/mod/core/{}", home, new_name);
    if category == "orbit" && std::path::Path::new(&shadowed).is_dir() {
        return (
            StatusCode::CONFLICT,
            Json(json!({
                "error": format!("core/{new_name} already owns that name — an orbit module called '{new_name}' would never be reachable"),
            })),
        )
            .into_response();
    }

    let mode = match body.refs.as_deref().unwrap_or("paths") {
        "all" => "all",
        "none" => "none",
        "paths" => "paths",
        other => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": format!("refs must be paths|all|none (got '{other}')") })),
            )
                .into_response()
        }
    }
    .to_string();

    let (old, new, cat) = (name.clone(), new_name.clone(), category.clone());
    let src = std::path::PathBuf::from(&module_path);
    let dest = std::path::PathBuf::from(&new_path);
    let (dry, restart, reroute) = (body.dry_run, body.restart, body.reroute);
    let report = tokio::task::spawn_blocking(move || {
        rename_execute(old, new, cat, src, dest, mode, restart, reroute, dry)
    })
    .await;

    match report {
        Ok(v) => {
            let ok = v.get("ok").and_then(|b| b.as_bool()).unwrap_or(false);
            let code = if ok {
                StatusCode::OK
            } else {
                StatusCode::INTERNAL_SERVER_ERROR
            };
            (code, Json(v)).into_response()
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("rename task failed: {e}") })),
        )
            .into_response(),
    }
}

/// EventSource can't send an Authorization header, so a session that needs to
/// prove who it is passes its bearer token here instead. Only vaulted tasks
/// care — everything else streams to anyone, same as before.
#[derive(Deserialize)]
struct StreamQuery {
    #[serde(default)]
    token: Option<String>,
}

async fn stream_job(
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    Query(q): Query<StreamQuery>,
) -> Result<Sse<std::pin::Pin<Box<dyn tokio_stream::Stream<Item = Result<Event, Infallible>> + Send>>>, (StatusCode, &'static str)> {
    let Some(mut job) = mgr.get_job(&id) else {
        return Err((StatusCode::NOT_FOUND, "Job not found"));
    };

    // Live output moves through the broadcast channel in the clear — it has
    // to, the agent is producing it right now. So for a sealed task the
    // stream itself is what needs gating: only its author, with the vault
    // open, may attach. Everyone else reads the sealed ledger instead.
    let who = q
        .token
        .as_deref()
        .map(|t| format!("Bearer {t}"))
        .and_then(|h| auth::extract_address_from_header(&h).ok())
        .unwrap_or_default();
    // Same door as the ledger: a task inside a private module isn't watchable
    // by anyone but that module's owner.
    if job_hidden(&crate::privacy::hidden_names(&who), &job.work_dir) {
        return Err((StatusCode::NOT_FOUND, "Job not found"));
    }
    let sealed = crate::vault::is_job_sealed(&job);
    crate::vault::unmask_job(&mut job, &who);
    if sealed && job.locked {
        return Err((
            StatusCode::FORBIDDEN,
            "sealed task — sign in as its owner and unlock the vault to watch it live",
        ));
    }
    let job = Some(job);

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
    if let Err(e) = crate::privacy::read_guard(&caller, file_path) {
        return (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response();
    }

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
    if let Err(e) = crate::privacy::read_guard(&caller, file_path) {
        return (StatusCode::FORBIDDEN, e).into_response();
    }

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
    if let Err(e) = crate::privacy::read_guard(&caller, dir_path) {
        return Json(json!({ "results": [], "error": e }));
    }
    let denied = crate::privacy::denied_roots(&caller);

    let query = params.query.to_lowercase();
    let mut results = Vec::new();

    fn search_recursive(
        dir: &std::path::Path,
        query: &str,
        home: &str,
        results: &mut Vec<serde_json::Value>,
        depth: usize,
        denied: &[std::path::PathBuf],
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
            if denied.iter().any(|d| d.as_path() == path) {
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
                search_recursive(&path, query, home, results, depth + 1, denied);
            }
        }
    }

    search_recursive(dir_path, &query, &home, &mut results, 0, &denied);
    Json(json!({ "results": results }))
}

/// Get version changelog from ~/.mod/build-fork/changelog.json
async fn get_changelog(Query(params): Query<ChangelogQuery>) -> impl IntoResponse {
    let home = dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp"));
    let changelog_path = home.join(".mod").join("build-fork").join("changelog.json");

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
    let changelog_path = home.join(".mod").join("build-fork").join("changelog.json");

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
    if let Err(e) = crate::privacy::read_guard(&caller, dir_path) {
        return Json(json!({ "matches": [], "error": e }));
    }
    let denied = crate::privacy::denied_roots(&caller);

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
        denied: &[std::path::PathBuf],
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
            if denied.iter().any(|d| d.as_path() == path) {
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
                grep_recursive(&path, query, case_sensitive, home, matches, depth + 1, denied);
            }
        }
    }

    grep_recursive(dir_path, &query, params.caseSensitive, &home, &mut matches, 0, &denied);
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

    let local_mode = std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1";
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
    /// Override the self-restart guard. Restarting/stopping build's own api
    /// process kills the job manager and orphans every in-flight job, so we
    /// refuse while jobs are running unless the caller explicitly forces it.
    #[serde(default)]
    force: Option<bool>,
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
    let module_path = match crate::snapshots::module_root_for(&name) {
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

    // Self-restart guard. Restarting/stopping build's OWN api process tears
    // down *this* job manager and orphans every in-flight job — the #1 cause of
    // tasks "hanging" (their SSE stream dies, then they fail with "Server
    // restarted"). A job that edits the build module and then bounces it kills
    // itself and its siblings. So while jobs are in flight, refuse to touch the
    // api process unless the caller passes force:true. App-only restarts are
    // always fine — the jobs live in the api, and the app is `next dev` (it
    // hot-reloads without a restart anyway).
    if name == "build"
        && matches!(action.as_str(), "restart" | "stop" | "start")
        && body.target.as_deref() != Some("app")
        && !body.force.unwrap_or(false)
    {
        let busy = _mgr
            .list_jobs()
            .into_iter()
            .filter(|j| matches!(j.status, JobStatus::Running | JobStatus::Pending))
            .count();
        if busy > 0 {
            return (
                StatusCode::CONFLICT,
                Json(json!({
                    "error": format!(
                        "Refusing to {} build-fork-api while {} job(s) are still running — that would \
                         kill the job manager and orphan them. Let the jobs finish, target the app \
                         only (\"target\":\"app\"), or pass \"force\":true to override.",
                        action, busy
                    ),
                    "running_jobs": busy,
                    "module": name,
                    "action": action,
                })),
            )
                .into_response();
        }
    }

    let (ok, mut output) = process::act(backend, &action, &procs, &module_path, &name, &config);

    // Stop means DEAD. A supervisor stop only reaches what the supervisor
    // registered — bare processes, detached children and hand-launched dev
    // servers attributed to the module all survive it (the #1 "kill didn't
    // kill it"). Sweep them after a full-module stop. Skipped when the stop
    // was filtered to one service: sweeping would take the other one down too.
    let full_module = body.target.as_deref().map(str::trim).filter(|s| !s.is_empty()).is_none();
    let stragglers = if action == "stop" && full_module {
        let (killed, klog) = process::kill_stragglers(&module_path);
        output.push_str(&format!("\n{}", klog));
        killed
    } else {
        Vec::new()
    };

    // Re-read state so the caller sees the result of their action — including
    // the config and the backend choice. A bare module launched by `m serve`
    // only learns its port at launch (the protocol writes it back into
    // config.json) and only then has pm2 entries, so listing it against the
    // config we read BEFORE the action would report it as still down.
    let config = read_module_config(&module_path, &name);
    let backend = process::select(&module_path, &config, &name);
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
            "stragglers": stragglers,
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

    let module_path = match crate::snapshots::module_root_for(&name) {
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
// Versions log lives at ~/.mod/build-fork/versions/{module}.json — append-only.
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
    std::env::var("BUILD_FORK_API_MODULE_URL")
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
    // Choke point for private modules: the api module re-reads the module's
    // plaintext tree to build the registry entry, so a single push would
    // republish everything the encryption is protecting. Every call site
    // (snapshot, restore, fork, import, autosnap, MR merge) funnels through
    // here, so blocking once blocks them all.
    if crate::privacy::is_private(module) {
        return Err(format!(
            "private module '{module}' — plaintext registry push blocked (publishing is encrypted-only)"
        ));
    }
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
    /// Private modules only: needed when the server-side password copy was
    /// deleted (otherwise the stored copy is used).
    #[serde(default)]
    password: Option<String>,
}

async fn snapshot_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<SnapshotBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let local_mode = std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1";
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
    // Private module: publish one encrypted bundle blob instead — owner-only,
    // password-gated, no plaintext blobs, no registry push.
    if crate::privacy::is_private(&name) {
        if !local_mode && !auth::is_owner(&caller) {
            return (
                StatusCode::FORBIDDEN,
                Json(json!({ "error": "Owner-only: this module is private; only the owner may publish updates" })),
            )
                .into_response();
        }
        let password = match crate::privacy::resolve_password(&name, body.password.as_deref()) {
            Ok(p) => p,
            Err(e) => {
                return (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response();
            }
        };
        let snap_root = root.clone();
        let snap_name = name.clone();
        let snap = match tokio::task::spawn_blocking(move || {
            crate::privacy::snapshot_encrypted(&snap_name, &snap_root, &password)
        })
        .await
        .map_err(|e| format!("snapshot task: {e}"))
        .and_then(|r| r)
        {
            Ok(s) => s,
            Err(e) => {
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e })))
                    .into_response();
            }
        };
        let history = read_versions(&name);
        let parent = history.last().map(|v| v.cid.clone());
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let record = VersionRecord {
            cid: snap.cipher_cid.clone(),
            message: body.message.clone(),
            author: caller.clone(),
            timestamp: ts,
            parent: parent.clone(),
            action: Some("snapshot".to_string()),
            encrypted: Some(true),
            plain_cid: Some(snap.plain_cid.clone()),
            ..Default::default()
        };
        if let Err(e) = append_version(&name, record) {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e })))
                .into_response();
        }
        return (
            StatusCode::CREATED,
            Json(json!({
                "ok": true,
                "module": name,
                "cid": snap.cipher_cid,
                "encrypted": true,
                "store": default_store().name(),
                "file_count": snap.file_count,
                "parent": parent,
                "author": caller,
                "registry_error": "skipped: private module publishes encrypted-only",
            })),
        )
            .into_response();
    }
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
        ..Default::default()
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

async fn list_module_versions(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
) -> impl IntoResponse {
    // Private modules keep even their history (messages, authors, CIDs)
    // owner-only; everything else stays a public ledger.
    if !crate::privacy::can_access(&reader(&headers), &name) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({
                "module": name,
                "private": true,
                "error": "private module — history is owner-only",
            })),
        )
            .into_response();
    }
    let history = read_versions(&name);
    // Reverting is owner-only (see `revert_gate`), and only versions whose
    // blob is still in the store can actually be reverted to — say both here
    // so the console can show the owner an honest history instead of buttons
    // that 403 or 400 on click.
    let store = default_store();
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let can_revert = local_mode() || auth::is_root_owner(&caller);
    let versions: Vec<serde_json::Value> = history
        .iter()
        .map(|v| {
            let mut j = serde_json::to_value(v).unwrap_or_else(|_| json!({}));
            if let Some(o) = j.as_object_mut() {
                o.insert("restorable".to_string(), json!(store.has(&v.cid)));
            }
            j
        })
        .collect();
    Json(json!({
        "module": name,
        "count": history.len(),
        "versions": versions,
        // Who may roll this history back — never the caller's edit rights.
        "revert": {
            "can_revert": can_revert,
            "owner_only": true,
            "requires_signature": !local_mode(),
            "note": "editors can change this module; only the owner can revert it",
        },
    }))
    .into_response()
}

// ── Private repos: password lifecycle + status ───────────────────────
//
// The key itself lives in ~/.mod/build-fork/private/<module>.json (0600).
// Everything here belongs to the module's owner: privacy state, the key, and
// even the verify check are nobody else's business. See privacy.rs for the
// crypto and the publishing/read-guard integration.

/// Gate shared by the privacy handlers: whoever turned a module private owns
/// its privacy from then on, and a still-public module needs edit rights to
/// be turned private at all. Err is the ready response to return.
fn require_privacy_manager(
    headers: &axum::http::HeaderMap,
    module: &str,
) -> Result<String, axum::response::Response> {
    let caller = auth::extract_address_from_headers(headers).unwrap_or_default();
    if crate::privacy::may_manage(&caller, module) {
        Ok(caller)
    } else {
        Err((
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "Privacy is controlled by the module's owner — the address that made it private (or an editor of a still-public module)"
            })),
        )
            .into_response())
    }
}

async fn privacy_status(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
) -> impl IntoResponse {
    if let Err(resp) = require_privacy_manager(&headers, &name) {
        return resp;
    }
    let (opt_in, routed) = crate::privacy::public_route(&name);
    // The router is a separate program with its own generated config, so a
    // module can be private HERE and still answering at {host}/{name} until
    // that config is regenerated. Say which, rather than implying a takedown
    // this process did not perform.
    let route = json!({
        "opt_in": opt_in,
        "routed": routed,
        "path": format!("/{name}"),
        "note": if routed {
            "the fleet router still serves this module at {host}/<name> — run `m caddy/apply` to regenerate; private modules are skipped there"
        } else {
            "the fleet router publishes no route for this module"
        },
    });
    match crate::privacy::record(&name) {
        Some(rec) => Json(json!({
            "module": name,
            "private": rec.enabled,
            "password_held": rec.password.is_some(),
            "owner": rec.owner,
            "created": rec.created,
            "updated": rec.updated,
            "public_route": route,
        }))
        .into_response(),
        None => Json(json!({
            "module": name,
            "private": false,
            "password_held": false,
            "public_route": route,
        }))
        .into_response(),
    }
}

async fn privacy_enable(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
) -> impl IntoResponse {
    let caller = match require_privacy_manager(&headers, &name) {
        Ok(c) => c,
        Err(resp) => return resp,
    };
    if module_root_for(&name).is_none() {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    }
    match crate::privacy::enable(&name, &caller) {
        Ok(rec) => (
            StatusCode::CREATED,
            Json(json!({
                "ok": true,
                "module": name,
                "private": true,
                // Returned so the UI can show it immediately; also readable
                // via GET …/privacy/password until the owner deletes it.
                "password": rec.password,
                "password_held": rec.password.is_some(),
                "note": "publishing is now encrypted-only; the registry keeps its last plaintext CID and goes stale",
            })),
        )
            .into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response(),
    }
}

#[derive(Deserialize)]
struct PrivacyPasswordBody {
    #[serde(default)]
    password: Option<String>,
}

async fn privacy_disable(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<PrivacyPasswordBody>,
) -> impl IntoResponse {
    if let Err(resp) = require_privacy_manager(&headers, &name) {
        return resp;
    }
    // Turning privacy off resumes plaintext publishing — prove possession of
    // the password (server-held or supplied) before flipping the switch.
    if let Err(e) = crate::privacy::resolve_password(&name, body.password.as_deref()) {
        return (StatusCode::FORBIDDEN, Json(json!({ "error": e }))).into_response();
    }
    match crate::privacy::disable(&name) {
        Ok(_) => Json(json!({
            "ok": true,
            "module": name,
            "private": false,
            "note": "key material kept — old encrypted versions stay restorable with the password",
        }))
        .into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

async fn privacy_password_get(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
) -> impl IntoResponse {
    if let Err(resp) = require_privacy_manager(&headers, &name) {
        return resp;
    }
    match crate::privacy::record(&name) {
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' is not private") })),
        )
            .into_response(),
        Some(rec) => match rec.password {
            Some(p) => Json(json!({ "module": name, "password": p })).into_response(),
            None => (
                StatusCode::GONE,
                Json(json!({
                    "error": "the server-side password copy was deleted; only you hold it now"
                })),
            )
                .into_response(),
        },
    }
}

async fn privacy_password_delete(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
) -> impl IntoResponse {
    if let Err(resp) = require_privacy_manager(&headers, &name) {
        return resp;
    }
    match crate::privacy::delete_password(&name) {
        Ok(_) => Json(json!({
            "ok": true,
            "module": name,
            "password_held": false,
            "note": "unrecoverable server-side from here on — background edit snapshots pause until a password is supplied per request",
        }))
        .into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

async fn privacy_verify(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<PrivacyPasswordBody>,
) -> impl IntoResponse {
    if let Err(resp) = require_privacy_manager(&headers, &name) {
        return resp;
    }
    let Some(password) = body.password.filter(|p| !p.is_empty()) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "missing 'password'" })),
        )
            .into_response();
    };
    match crate::privacy::verify(&name, &password) {
        Ok(valid) => Json(json!({ "module": name, "valid": valid })).into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

/// Fetch the global mod-protocol api registry entry for a module so the UI
/// can show "the registry currently points at CID X" next to the local log.
async fn module_registry(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
) -> impl IntoResponse {
    if !crate::privacy::can_access(&reader(&headers), &name) {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    }
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
    let local_mode = std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1";
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
        ..Default::default()
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
struct CopyBody {
    new_name: String,
    #[serde(default)]
    category: Option<String>,
}

/// Build artifacts and dependency caches — the copy is still the complete
/// source without them, and the fork rebuilds its own.
const COPY_SKIP_DIRS: &[&str] = &[
    ".git",
    "node_modules",
    ".next",
    "target",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    ".turbo",
    ".history",
];

/// Recursively copy a module tree, skipping COPY_SKIP_DIRS. Symlinks are
/// skipped too: they'd point back into the source module (or out of the tree
/// entirely) and a fork should be self-contained.
fn copy_module_tree(src: &std::path::Path, dest: &std::path::Path) -> Result<usize, String> {
    std::fs::create_dir_all(dest).map_err(|e| format!("mkdir {}: {e}", dest.display()))?;
    let mut copied = 0usize;
    let entries =
        std::fs::read_dir(src).map_err(|e| format!("read {}: {e}", src.display()))?;
    for entry in entries {
        let entry = entry.map_err(|e| e.to_string())?;
        let ty = entry.file_type().map_err(|e| e.to_string())?;
        let name = entry.file_name();
        if ty.is_dir() {
            if COPY_SKIP_DIRS.contains(&name.to_string_lossy().as_ref()) {
                continue;
            }
            copied += copy_module_tree(&entry.path(), &dest.join(&name))?;
        } else if ty.is_file() {
            std::fs::copy(entry.path(), dest.join(&name))
                .map_err(|e| format!("copy {}: {e}", entry.path().display()))?;
            copied += 1;
        }
    }
    Ok(copied)
}

/// `port`-shaped config keys. gateway_port names the SHARED front gateway,
/// not a port the module itself owns, so it never gets remapped.
fn is_port_key(k: &str) -> bool {
    k != "gateway_port" && (k == "port" || k.ends_with("_port"))
}

/// Every port claimed by any config.json in the fleet — a fresh copy must not
/// collide with a module that isn't even running right now.
fn fleet_claimed_ports() -> std::collections::HashSet<u16> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
    let mut ports = std::collections::HashSet::new();
    for tree in ["orbit", "core"] {
        let Ok(dirs) = std::fs::read_dir(format!("{home}/mod/mod/{tree}")) else {
            continue;
        };
        for d in dirs.flatten() {
            let Ok(text) = std::fs::read_to_string(d.path().join("config.json")) else {
                continue;
            };
            let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) else {
                continue;
            };
            if let Some(obj) = v.as_object() {
                for (k, val) in obj {
                    if is_port_key(k) {
                        if let Some(p) = val.as_u64() {
                            if p > 0 && p <= u16::MAX as u64 {
                                ports.insert(p as u16);
                            }
                        }
                    }
                }
            }
        }
    }
    ports
}

/// First port after `start` that no fleet config claims, this copy hasn't
/// already taken, and nothing is currently listening on.
fn pick_free_port(
    start: u16,
    claimed: &std::collections::HashSet<u16>,
    taken: &std::collections::HashSet<u16>,
) -> Option<u16> {
    let mut p = start;
    for _ in 0..2000 {
        p = p.checked_add(1)?;
        if claimed.contains(&p) || taken.contains(&p) {
            continue;
        }
        if std::net::TcpListener::bind(("127.0.0.1", p)).is_ok() {
            return Some(p);
        }
    }
    None
}

/// Rewrite the copied config.json so the fork can run right next to its
/// source: new name, fresh ports for every owned `port`/`*_port` key, and
/// every ":oldport" inside string values (urls.api / urls.app …) updated to
/// match. Returns (remapped ports, warning) — a missing/unparseable config is
/// a warning, not a failure: the files still copied fine.
fn rewrite_copied_config(
    dest: &std::path::Path,
    new_name: &str,
) -> (Vec<(String, u16, u16)>, Option<String>) {
    let cfg_path = dest.join("config.json");
    let Ok(text) = std::fs::read_to_string(&cfg_path) else {
        return (vec![], Some("no config.json in copy".to_string()));
    };
    let Ok(mut cfg) = serde_json::from_str::<serde_json::Value>(&text) else {
        return (vec![], Some("config.json is not valid JSON".to_string()));
    };
    let mut remapped: Vec<(String, u16, u16)> = vec![];
    if let Some(obj) = cfg.as_object_mut() {
        obj.insert("name".into(), json!(new_name));
        let claimed = fleet_claimed_ports();
        let mut taken: std::collections::HashSet<u16> = std::collections::HashSet::new();
        let keys: Vec<String> = obj.keys().cloned().collect();
        for k in keys {
            if !is_port_key(&k) {
                continue;
            }
            let Some(old) = obj.get(&k).and_then(|v| v.as_u64()) else {
                continue;
            };
            if old == 0 || old > u16::MAX as u64 {
                continue;
            }
            let old = old as u16;
            if let Some(fresh) = pick_free_port(old, &claimed, &taken) {
                taken.insert(fresh);
                obj.insert(k.clone(), json!(fresh));
                remapped.push((k, old, fresh));
            }
        }
    }
    fn patch_strings(v: &mut serde_json::Value, maps: &[(String, u16, u16)]) {
        match v {
            serde_json::Value::String(s) => {
                for (_, old, fresh) in maps {
                    *s = s.replace(&format!(":{old}"), &format!(":{fresh}"));
                }
            }
            serde_json::Value::Array(a) => a.iter_mut().for_each(|x| patch_strings(x, maps)),
            serde_json::Value::Object(o) => o.values_mut().for_each(|x| patch_strings(x, maps)),
            _ => {}
        }
    }
    patch_strings(&mut cfg, &remapped);
    let out = serde_json::to_string_pretty(&cfg).unwrap_or(text);
    let warn = std::fs::write(&cfg_path, out)
        .err()
        .map(|e| format!("write config.json: {e}"));
    (remapped, warn)
}

/// POST /modules/:name/copy — the deterministic fork: copy a module's LIVE
/// source tree to a new name in the tree, instantly and without an AI job or
/// a snapshot CID. The copy gets a rewritten config.json (new name + fresh
/// ports) so it can run beside its source, plus seeded version history, and
/// is then the caller's to reshape however they want.
async fn copy_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<CopyBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    let local_mode = std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1";
    if caller.is_empty() && !local_mode {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({ "error": "auth required to copy" })),
        )
            .into_response();
    }
    // Copying materializes a new module directory — module creation is
    // owner-only, the same rule as import and fork.
    if !local_mode && !auth::is_owner(&caller) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "Owner-only: copying creates a new module; only the configured owner may do it" })),
        )
            .into_response();
    }
    let new_name = body.new_name.trim().to_string();
    if !valid_module_slug(&new_name) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "module name must be 1–64 chars of [a-zA-Z0-9_-]" })),
        )
            .into_response();
    }
    let Some(src) = module_root_for(&name) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    };
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
    let dest = std::path::PathBuf::from(format!("{home}/mod/mod/{category}/{new_name}"));
    if dest.exists() || module_root_for(&new_name).is_some() {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": format!("module '{new_name}' already exists") })),
        )
            .into_response();
    }
    // The tree walk + file copies can be big — keep them off the async runtime.
    let (src_c, dest_c) = (src.clone(), dest.clone());
    let copied = match tokio::task::spawn_blocking(move || copy_module_tree(&src_c, &dest_c)).await
    {
        Ok(Ok(n)) => n,
        Ok(Err(e)) => {
            let _ = std::fs::remove_dir_all(&dest);
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e })))
                .into_response();
        }
        Err(e) => {
            let _ = std::fs::remove_dir_all(&dest);
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(json!({ "error": format!("copy task failed: {e}") })),
            )
                .into_response();
        }
    };
    let (ports, config_warning) = rewrite_copied_config(&dest, &new_name);
    // Seed version history + the global registry so the copy shows up like
    // any other module (best-effort — a registry hiccup must not fail a copy
    // whose files already landed).
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let store = default_store();
    let snap_cid = snapshot_dir(&dest, &store).ok().map(|(c, _)| c);
    let msg = format!("copied from {name}");
    let (registry_cid, registry_prev, registry_err) =
        match mod_protocol_register(&new_name, &msg).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };
    if let Some(cid) = snap_cid.clone() {
        let _ = append_version(
            &new_name,
            VersionRecord {
                cid,
                message: msg,
                author: caller.clone(),
                timestamp: ts,
                parent: None,
                registry_cid: registry_cid.clone(),
                registry_prev: registry_prev.clone(),
                action: Some("copy".to_string()),
                job_id: None,
                ..Default::default()
            },
        );
    }
    (
        StatusCode::CREATED,
        Json(json!({
            "ok": true,
            "module": new_name,
            "from": name,
            "path": dest.display().to_string(),
            "category": category,
            "files": copied,
            "ports": ports
                .iter()
                .map(|(k, old, fresh)| json!({ "key": k, "from": old, "to": fresh }))
                .collect::<Vec<_>>(),
            "config_warning": config_warning,
            "cid": snap_cid,
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
    /// For encrypted (private-module) versions: needed when the server-side
    /// password copy was deleted.
    #[serde(default)]
    password: Option<String>,
}

#[derive(Deserialize)]
struct UndoBody {
    /// How many distinct states to walk back. 1 (the default) = undo the last
    /// change; 2 = the change before that, and so on.
    #[serde(default)]
    steps: Option<usize>,
    #[serde(default)]
    password: Option<String>,
}

// ── Reverting: the owner's last word ────────────────────────────────────
//
// Editing and reverting are deliberately different powers here. Anyone the
// owner trusts to edit — whitelisted editors, QR-invite holders, even
// sudo-delegated addresses that pass every other owner gate — may change a
// module. Only the owner may decide that a change does not stand: rolling a
// module back to any earlier version answers to the owner's OWN key
// (config.json `owner` + ~/.mod/build-fork/owners.json co-owners) and to nothing
// else. That is why this gate uses `auth::is_root_owner` rather than
// `auth::is_owner`, and `sudo::verify_sudo_owner` rather than `sudo_gate`.
//
// Two locks, both required for every revert of every module (build included —
// the console can be rolled back too, and that is exactly the case where a
// delegate must not be the one doing it):
//   1. identity — the session belongs to the owner;
//   2. possession — a fresh wallet signature bound to ("restore", module),
//      recovered to an owner address, replay-rejected server-side.
//
// Local mode (host CLI, no auth) bypasses both, as it does everywhere else.
fn revert_gate(
    headers: &axum::http::HeaderMap,
    caller: &str,
    name: &str,
) -> Option<axum::response::Response> {
    if local_mode() {
        return None;
    }
    if caller.is_empty() {
        return Some(
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({ "error": "auth required to revert" })),
            )
                .into_response(),
        );
    }
    if !auth::is_root_owner(caller) {
        let editor = auth::is_trusted(caller);
        return Some(
            (
                StatusCode::FORBIDDEN,
                Json(json!({
                    "error": if editor {
                        "Owner-only: editors may change this module, but only the owner may revert it"
                    } else {
                        "Owner-only: reverting a module requires the configured owner"
                    },
                    "owner_only": true,
                    "revert": true,
                    "is_editor": editor,
                })),
            )
                .into_response(),
        );
    }
    match sudo::verify_sudo_owner(headers, "restore", name) {
        Ok(addr) => {
            println!("✓ revert authorized: {} (by owner {})", name, addr);
            None
        }
        Err(e) => Some(
            (
                StatusCode::UNAUTHORIZED,
                Json(json!({
                    "error": e,
                    "sudo_required": true,
                    "action": "restore",
                    "target": name,
                })),
            )
                .into_response(),
        ),
    }
}

/// The revert itself, shared by `/restore` (any version the owner picks) and
/// `/undo` (walk back one state). Callers must have cleared `revert_gate`
/// first. Always pins the current tree as a version of its own before
/// overwriting, so a revert is itself revertible — the owner can never paint
/// themselves into a corner by undoing too far.
async fn perform_revert(
    name: &str,
    root: &std::path::Path,
    cid: &str,
    caller: &str,
    password_in: Option<&str>,
) -> Result<serde_json::Value, (StatusCode, serde_json::Value)> {
    let store = default_store();
    let target_blob = store
        .get(cid)
        .map_err(|e| (StatusCode::BAD_REQUEST, json!({ "error": e })))?;
    let target_encrypted = crate::privacy::is_encrypted_blob(&target_blob);
    let is_private = crate::privacy::is_private(name);
    // One resolved password serves both the decrypt and the pre-rollback
    // auto-snapshot. A plaintext rollback of a non-private module skips this.
    let password = if target_encrypted || is_private {
        Some(
            crate::privacy::resolve_password(name, password_in)
                .map_err(|e| (StatusCode::FORBIDDEN, json!({ "error": e })))?,
        )
    } else {
        None
    };
    let ts_now = || {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    };
    let short = &cid[..cid.len().min(16)];
    // Auto-snapshot current state first so the rollback is itself reversible —
    // encrypted for private modules so the rollback safety net leaks nothing.
    if is_private {
        // password is always Some when is_private (resolved above)
        let pw = password.as_deref().unwrap_or("");
        if let Ok(snap) = crate::privacy::snapshot_encrypted(name, root, pw) {
            let auto_msg = format!("auto-snapshot before rollback to {short}");
            let _ = append_version(
                name,
                VersionRecord {
                    cid: snap.cipher_cid,
                    message: auto_msg,
                    author: caller.to_string(),
                    timestamp: ts_now(),
                    action: Some("auto-snapshot".to_string()),
                    encrypted: Some(true),
                    plain_cid: Some(snap.plain_cid),
                    ..Default::default()
                },
            );
        }
    } else if let Ok((auto_cid, _)) = snapshot_dir(root, &store) {
        let auto_msg = format!("auto-snapshot before rollback to {short}");
        let (rcid, rprev, _) = match mod_protocol_register(name, &auto_msg).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };
        let _ = append_version(
            name,
            VersionRecord {
                cid: auto_cid,
                message: auto_msg,
                author: caller.to_string(),
                timestamp: ts_now(),
                parent: None,
                registry_cid: rcid,
                registry_prev: rprev,
                action: Some("auto-snapshot".to_string()),
                job_id: None,
                ..Default::default()
            },
        );
    }
    let restore_result = if target_encrypted {
        // password is always Some when target_encrypted (resolved above)
        crate::privacy::restore_encrypted(root, &target_blob, password.as_deref().unwrap_or(""))
    } else {
        restore_into(root, cid, &store)
    };
    let written =
        restore_result.map_err(|e| (StatusCode::BAD_REQUEST, json!({ "error": e })))?;
    // Re-register through the api module so the global registry's "latest"
    // pointer moves backwards along the chain to reflect the rollback.
    let restore_msg = format!("rollback to {short}");
    let (registry_cid, registry_prev, registry_err) =
        match mod_protocol_register(name, &restore_msg).await {
            Ok(r) => (r.cid, r.prev, None),
            Err(e) => (None, None, Some(e)),
        };
    let _ = append_version(
        name,
        VersionRecord {
            cid: cid.to_string(),
            message: restore_msg,
            author: caller.to_string(),
            timestamp: ts_now(),
            parent: None,
            registry_cid: registry_cid.clone(),
            registry_prev: registry_prev.clone(),
            action: Some("restore".to_string()),
            job_id: None,
            encrypted: target_encrypted.then_some(true),
            ..Default::default()
        },
    );
    Ok(json!({
        "ok": true,
        "module": name,
        "restored_to": cid,
        "encrypted": target_encrypted,
        "file_count": written,
        "store": store.name(),
        "registry_cid": registry_cid,
        "registry_prev": registry_prev,
        "registry_error": registry_err,
    }))
}

async fn restore_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<RestoreBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    // Restore overwrites a module's files from an arbitrary snapshot CID — the
    // owner's undo, and nobody else's. See `revert_gate`.
    if let Some(denied) = revert_gate(&headers, &caller, &name) {
        return denied;
    }
    let Some(root) = module_root_for(&name) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    };
    match perform_revert(&name, &root, &body.cid, &caller, body.password.as_deref()).await {
        Ok(v) => (StatusCode::OK, Json(v)).into_response(),
        Err((code, v)) => (code, Json(v)).into_response(),
    }
}

/// One-click undo: revert to the state before the last change, without the
/// owner having to know which CID that was. `steps` walks further back.
///
/// The target comes from the module's own version log, newest first, with
/// consecutive identical states collapsed (an edit that changed nothing, or a
/// snapshot taken twice, is one state — undoing into it would look like
/// nothing happened). Records whose blob has fallen out of the store are
/// skipped rather than offered, so undo never half-lands.
async fn undo_module(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    Json(body): Json<UndoBody>,
) -> impl IntoResponse {
    let caller = auth::extract_address_from_headers(&headers).unwrap_or_default();
    if let Some(denied) = revert_gate(&headers, &caller, &name) {
        return denied;
    }
    let Some(root) = module_root_for(&name) else {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    };
    let steps = body.steps.unwrap_or(1).max(1);
    let store = default_store();
    let ident = |r: &VersionRecord| r.plain_cid.clone().unwrap_or_else(|| r.cid.clone());
    // Newest → oldest, collapsing repeats. Encrypted records compare on their
    // plaintext tree CID: the ciphertext CID changes on every snapshot (fresh
    // nonce), so raw CIDs would make every state look distinct.
    let history = read_versions(&name);
    let mut states: Vec<VersionRecord> = Vec::new();
    for v in history.iter().rev() {
        if !store.has(&v.cid) {
            continue;
        }
        if states.last().map(|p| ident(p) == ident(v)).unwrap_or(false) {
            continue;
        }
        states.push(v.clone());
    }
    // "One step back" is measured from what is ON DISK, not from the newest
    // log entry — a module edited outside the console (or since its last
    // snapshot) has a live tree the log doesn't name yet, and undoing to the
    // log head would then be the change, not the undo of it. Pinning the tree
    // costs one content-addressed snapshot and is idempotent. Private modules
    // skip it: their blobs are encrypted on purpose and a plaintext pin here
    // would defeat that, so their undo walks the log as written.
    let current = if crate::privacy::is_private(&name) {
        None
    } else {
        snapshot_dir(&root, &store).ok().map(|(cid, _)| cid)
    };
    let head_is_current = current
        .as_ref()
        .map(|c| states.first().map(|v| &ident(v) == c).unwrap_or(false))
        .unwrap_or(true);
    // Drop the states the module is already in, so step 1 always lands
    // somewhere different from now.
    let prior: Vec<&VersionRecord> = match &current {
        Some(c) => states.iter().skip_while(|v| &ident(v) == c).collect(),
        None => states.iter().skip(1).collect(),
    };
    let Some(target) = prior.get(steps - 1) else {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "error": format!(
                    "nothing to undo {} step(s) back — '{}' has {} earlier restorable state(s) on record",
                    steps, name, prior.len()
                ),
                "states": states.len(),
                "earlier_states": prior.len(),
            })),
        )
            .into_response();
    };
    let (cid, message) = (target.cid.clone(), target.message.clone());
    // What this undo overrules: the newest logged state, when that is in fact
    // what is on disk. If the tree drifted past the log we say so rather than
    // naming an edit the owner isn't actually undoing.
    let undone = states.first().filter(|_| head_is_current).map(|v| {
        json!({ "cid": v.cid, "message": v.message, "author": v.author, "timestamp": v.timestamp })
    });
    // Does this actually move the tree? An oscillating history (revert, then
    // revert the revert) can put an earlier state and the current one at the
    // same content — worth saying out loud instead of reporting a no-op as a
    // successful undo.
    let changed = current.as_ref().map(|c| &ident(target) != c);
    match perform_revert(&name, &root, &cid, &caller, body.password.as_deref()).await {
        Ok(mut v) => {
            v["undo"] = json!({
                "steps": steps,
                "back_to": { "cid": cid, "message": message, "timestamp": target.timestamp },
                "changed": changed,
                "tree_ahead_of_log": !head_is_current,
                // What this undo threw away — so the console can name the edit
                // (and its author) the owner just overruled.
                "undone": undone,
            });
            (StatusCode::OK, Json(v)).into_response()
        }
        Err((code, v)) => (code, Json(v)).into_response(),
    }
}

// ── Import a brand-new module from a GitHub repo or a snapshot CID ──────
//
// Two deterministic, no-AI paths for getting code onto disk as a fresh
// module (contrast with the agent-job "BUILD" flow which asks Claude to
// write files):
//   • source="github": shallow `git clone` the repo into the module dir,
//     then strip `.git` so it joins the mono-repo cleanly.
//   • source="cid": materialize a build snapshot CID out of the shared
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
    let local_mode = std::env::var("BUILD_FORK_JOBS_LOCAL").unwrap_or_default() == "1";
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
                ..Default::default()
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

async fn list_all_mrs(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
) -> impl IntoResponse {
    // Same stance as the task ledger: a merge request against a private
    // module is only a row for people who can see the module.
    let hidden = crate::privacy::hidden_names(&reader(&headers));
    let mrs: Vec<_> = merge::list_mrs(None)
        .into_iter()
        .filter(|m| !hidden.contains(&m.module.to_lowercase()))
        .map(|m| reconcile_mr(&mgr, m))
        .collect();
    Json(json!({ "count": mrs.len(), "merge_requests": mrs }))
}

async fn list_module_mrs(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(name): Path<String>,
) -> impl IntoResponse {
    if !crate::privacy::can_access(&reader(&headers), &name) {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    }
    let mrs: Vec<_> = merge::list_mrs(Some(&name))
        .into_iter()
        .map(|m| reconcile_mr(&mgr, m))
        .collect();
    Json(json!({ "module": name, "count": mrs.len(), "merge_requests": mrs })).into_response()
}

async fn get_mr(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let caller = reader(&headers);
    match merge::load_mr(&id).filter(|mr| crate::privacy::can_access(&caller, &mr.module)) {
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
    let Some(mr) = merge::load_mr(&id).filter(|mr| crate::privacy::can_access(&reader(&headers), &mr.module))
    else {
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
    let Some(mr) = merge::load_mr(&id).filter(|mr| crate::privacy::can_access(&reader(&headers), &mr.module))
    else {
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
    // Module-aware: a scoped grant only confers reviewer powers over the
    // modules it whitelists (unscoped grants / whitelist / owner = all).
    let trusted = auth::can_edit_module(&caller, &mr.module) || (caller.is_empty() && local_mode());
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
    // Same module-aware gate as review verdicts: scoped editors only close
    // MRs targeting modules inside their grant's whitelist.
    let trusted = auth::can_edit_module(&caller, &mr.module) || (caller.is_empty() && local_mode());
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
    // /restore, and sudo-bound when the target is any module but build.
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
    if mr.module != "build" {
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
                ..Default::default()
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
            agent: None,
            agent_params: None,
            replace_job_id: None,
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

// ── Suggestions: collaborate without forking ─────────────────────────
//
// A merge request needs a contributor who can write the code. A suggestion
// needs only someone with an opinion: any signed-in caller files one against
// any module, in words. The module's admin then triages the queue — reject,
// delete, mark done — or PLAYS it, which hands the suggestion to the agent as
// an ordinary edit job submitted under the ADMIN's own identity. So the text
// never touches the tree by itself: a play is the owner typing it into the
// ask bar, with the contributor's intent carried along.
//
//   1. POST /modules/{name}/suggestions   — anyone signed in: {title, body}
//   2. POST /suggestions/{id}/comment     — ANYONE, wallet or not
//      GET  /suggestions/{id}/comments    — the thread, whole, every time
//      POST /suggestions/{id}/vote        — signed in (a count of people)
//   3. POST /suggestions/{id}/play        — admin: run it as an edit job
//   4. POST /suggestions/{id}/status      — admin: open | rejected | done
//   5. DELETE /suggestions/{id}           — admin, or the author while open

fn sg_not_found(id: &str) -> axum::response::Response {
    (
        StatusCode::NOT_FOUND,
        Json(json!({ "error": format!("suggestion '{id}' not found") })),
    )
        .into_response()
}

/// Who may triage a module's queue: the owner, and editors whose grant covers
/// this module. Same gate as an MR review verdict.
fn suggestion_admin(caller: &str, module: &str) -> bool {
    auth::can_edit_module(caller, module) || (caller.is_empty() && local_mode())
}

/// Fold a finished play job back into the record — `playing` becomes `played`
/// (with the snapshot CID the job minted) or `play_failed`. Lazy on read, like
/// `reconcile_mr`.
fn reconcile_suggestion(
    mgr: &ClaudeJobManager,
    mut sg: suggestions::Suggestion,
) -> suggestions::Suggestion {
    if sg.status != "playing" {
        return sg;
    }
    let Some(play) = sg.plays.last().cloned() else {
        sg.status = "play_failed".to_string();
        let _ = suggestions::save(&sg);
        return sg;
    };
    let Some(job) = mgr.get_job(&play.job_id) else {
        return sg;
    };
    let outcome = format!("{}", job.status);
    let (status, note) = match outcome.as_str() {
        "completed" => ("played", None),
        "failed" | "cancelled" => (
            "play_failed",
            Some(format!("play job {} ended without completing", play.job_id)),
        ),
        _ => return sg,
    };
    let cid = read_versions(&sg.module)
        .iter()
        .rev()
        .find(|v| v.job_id.as_deref() == Some(play.job_id.as_str()))
        .map(|v| v.cid.clone());
    sg.status = status.to_string();
    sg.updated_at = suggestions::now_ts();
    if let Some(last) = sg.plays.last_mut() {
        last.outcome = Some(outcome);
        last.cid = cid.clone();
    }
    sg.comments.push(suggestions::SuggestionComment {
        author: "agent".to_string(),
        body: note.unwrap_or_else(|| match &cid {
            Some(c) => format!("played — job {} completed, new tree cid {c}", play.job_id),
            None => format!("played — job {} completed with no tree change (see the job output)", play.job_id),
        }),
        action: Some("system".to_string()),
        timestamp: suggestions::now_ts(),
    });
    let _ = suggestions::save(&sg);
    sg
}

/// A suggestion as it rides on a LIST response. Commenting is open to the
/// whole internet, so one busy thread can be larger than every other field in
/// the queue put together — a list carries the tail of the discussion plus the
/// honest `comment_count`, and whoever opens the thread refreshes the entire
/// history from `/suggestions/{id}/comments`.
fn sg_for_list(sg: &suggestions::Suggestion) -> serde_json::Value {
    let mut v = json!(sg);
    let total = sg.comments.len();
    if let Some(o) = v.as_object_mut() {
        o.insert("comment_count".to_string(), json!(total));
        if total > suggestions::MAX_INLINE_COMMENTS {
            o.insert("comments".to_string(), json!(suggestions::tail_comments(&sg.comments)));
            o.insert("comments_truncated".to_string(), json!(true));
        }
    }
    v
}

/// Suggestions the caller may see: a private module's queue is as private as
/// the module, exactly like its tasks.
fn visible_suggestions(
    mgr: &ClaudeJobManager,
    headers: &axum::http::HeaderMap,
    module: Option<&str>,
) -> Vec<suggestions::Suggestion> {
    let hidden = crate::privacy::hidden_names(&reader(headers));
    suggestions::list(module)
        .into_iter()
        .filter(|s| !hidden.contains(&s.module.to_lowercase()))
        .map(|s| reconcile_suggestion(mgr, s))
        .collect()
}

#[derive(Deserialize)]
struct NewSuggestionBody {
    title: String,
    #[serde(default)]
    body: String,
}

async fn open_suggestion(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(name): Path<String>,
    Json(input): Json<NewSuggestionBody>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    if !valid_module_slug(&name) {
        return mr_not_found_module(&name);
    }
    if module_root_for(&name).is_none() {
        return mr_not_found_module(&name);
    }
    let title = input.title.trim().to_string();
    if title.is_empty() || title.len() > 200 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "title required (≤200 chars)" })),
        )
            .into_response();
    }
    if input.body.len() > 10000 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "body too long (≤10000 chars)" })),
        )
            .into_response();
    }
    let author = mr_identity(&caller);
    // Filing costs nothing, so the queue needs a bound per author per module.
    // Admins are exempt: triaging their own module isn't spam.
    if !suggestion_admin(&caller, &name)
        && suggestions::open_count_for(&author, &name) >= suggestions::MAX_OPEN_PER_AUTHOR
    {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            Json(json!({
                "error": format!(
                    "you already have {} open suggestions for '{}' — wait for those to be triaged",
                    suggestions::MAX_OPEN_PER_AUTHOR, name
                )
            })),
        )
            .into_response();
    }
    let ts = suggestions::now_ts();
    let sg = suggestions::Suggestion {
        id: suggestions::new_id(),
        module: name,
        author,
        title,
        body: input.body.trim().to_string(),
        status: "open".to_string(),
        comments: vec![],
        votes: vec![],
        plays: vec![],
        created_at: ts,
        updated_at: ts,
    };
    if let Err(e) = suggestions::save(&sg) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    let _ = mgr; // state is taken for symmetry with the other suggestion routes
    (StatusCode::CREATED, Json(json!(sg))).into_response()
}

#[derive(Deserialize)]
struct SuggestionQuery {
    #[serde(default)]
    module: Option<String>,
    #[serde(default)]
    status: Option<String>,
    #[serde(default)]
    author: Option<String>,
}

async fn list_all_suggestions(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Query(q): Query<SuggestionQuery>,
) -> impl IntoResponse {
    let mut out = visible_suggestions(&mgr, &headers, q.module.as_deref());
    if let Some(status) = q.status.as_deref().filter(|s| !s.is_empty()) {
        out.retain(|s| s.status == status);
    }
    if let Some(author) = q.author.as_deref().filter(|a| !a.is_empty()) {
        let want = author.to_lowercase();
        out.retain(|s| s.author == want);
    }
    let rows: Vec<_> = out.iter().map(sg_for_list).collect();
    Json(json!({ "count": rows.len(), "suggestions": rows }))
}

async fn list_module_suggestions(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(name): Path<String>,
) -> impl IntoResponse {
    let out = visible_suggestions(&mgr, &headers, Some(&name));
    let open = out.iter().filter(|s| s.status == "open").count();
    let rows: Vec<_> = out.iter().map(sg_for_list).collect();
    Json(json!({ "module": name, "count": rows.len(), "open": open, "suggestions": rows }))
}

async fn get_suggestion(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    match suggestions::load(&id) {
        Some(sg) => {
            if crate::privacy::hidden_names(&reader(&headers)).contains(&sg.module.to_lowercase()) {
                return sg_not_found(&id);
            }
            Json(json!(reconcile_suggestion(&mgr, sg))).into_response()
        }
        None => sg_not_found(&id),
    }
}

#[derive(Deserialize)]
struct SuggestionCommentBody {
    body: String,
}

/// The ENTIRE discussion on one suggestion, oldest first.
///
/// Lists deliberately carry only the tail of a thread, so this is the call a
/// reader makes to hold the whole conversation — and remakes to see what has
/// landed since. It is the read side of an open comment box: no `since`, no
/// paging, no partial view that could quietly hide what someone said. Public,
/// like the rest of the ledger.
async fn suggestion_comments(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let Some(sg) = suggestions::load(&id) else {
        return sg_not_found(&id);
    };
    if crate::privacy::hidden_names(&reader(&headers)).contains(&sg.module.to_lowercase()) {
        return sg_not_found(&id);
    }
    let sg = reconcile_suggestion(&mgr, sg);
    Json(json!({
        "id": sg.id,
        "module": sg.module,
        "status": sg.status,
        "count": sg.comments.len(),
        "comments": sg.comments,
        "updated_at": sg.updated_at,
    }))
    .into_response()
}

/// Comment on a suggestion — open to anyone, signed in or not.
///
/// Reading the queue is public and filing a suggestion needs a session; the
/// discussion sits below both, because the person who hit the thing, has no
/// wallet and never intends to get one is often the one holding the detail
/// that makes the suggestion actionable. Requiring an association with the
/// module would filter for exactly the people who already have other ways to
/// speak.
///
/// What keeps that safe is that a comment is the weakest write on this API: it
/// appends text to a record an admin must read and act on by hand. It cannot
/// vote (that stays wallet-gated, or the count stops meaning people), cannot
/// change status, and reaches the agent only if an admin plays the suggestion
/// — where the brief marks unsigned entries and caps how much of the thread is
/// quoted at all (`suggestions::build_play_prompt`).
///
/// The cost is noise, which is expected rather than prevented: identity here
/// is only whatever the caller brought — their address if signed in, otherwise
/// a stable `anon:` handle — and the only bar is a repeat of the same text
/// from the same handle inside a minute, which is a stuck client, not a person.
async fn suggestion_comment(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    Json(input): Json<SuggestionCommentBody>,
) -> impl IntoResponse {
    let caller = reader(&headers);
    let Some(sg) = suggestions::load(&id) else {
        return sg_not_found(&id);
    };
    // A private module's queue is as private as the module — including its
    // discussion, which is now reachable without a token.
    if crate::privacy::hidden_names(&caller).contains(&sg.module.to_lowercase()) {
        return sg_not_found(&id);
    }
    let sg = reconcile_suggestion(&mgr, sg);
    let text = input.body.trim().to_string();
    if text.is_empty() || text.len() > 5000 {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "comment body required (≤5000 chars)" })),
        )
            .into_response();
    }
    let author = if caller.is_empty() && !local_mode() {
        auth::anon_handle(&headers)
    } else {
        mr_identity(&caller)
    };
    let now = suggestions::now_ts();
    // Not a rate limit — a stuck client. The same handle re-posting identical
    // text within the minute is a retry loop, and nobody loses a sentence.
    if sg.comments.iter().rev().take(20).any(|c| {
        c.author == author && c.body == text && now.saturating_sub(c.timestamp) < 60
    }) {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": "you just posted that exact comment" })),
        )
            .into_response();
    }
    let saved = match suggestions::append_comment(
        &id,
        suggestions::SuggestionComment {
            author,
            body: text,
            action: Some("comment".to_string()),
            timestamp: now,
        },
    ) {
        Ok(s) => s,
        Err(e) => {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response()
        }
    };
    // Answer with the whole thread as it now stands, not just the new line:
    // an open box means other people wrote while you were typing.
    Json(json!(saved)).into_response()
}

/// Second a suggestion (or take it back) — a toggle, so the count is people,
/// not clicks. What an admin sorts the queue by.
async fn suggestion_vote(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let Some(sg) = suggestions::load(&id) else {
        return sg_not_found(&id);
    };
    let mut sg = reconcile_suggestion(&mgr, sg);
    let who = mr_identity(&caller);
    let voted = if let Some(pos) = sg.votes.iter().position(|v| v == &who) {
        sg.votes.remove(pos);
        false
    } else {
        sg.votes.push(who);
        true
    };
    sg.updated_at = suggestions::now_ts();
    if let Err(e) = suggestions::save(&sg) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    Json(json!({ "voted": voted, "votes": sg.votes.len(), "suggestion": sg })).into_response()
}

#[derive(Deserialize)]
struct SuggestionStatusBody {
    status: String,
    #[serde(default)]
    note: Option<String>,
}

async fn suggestion_status(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    Json(input): Json<SuggestionStatusBody>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let Some(sg) = suggestions::load(&id) else {
        return sg_not_found(&id);
    };
    let mut sg = reconcile_suggestion(&mgr, sg);
    if !suggestion_admin(&caller, &sg.module) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": format!("only an admin of '{}' can triage its suggestions", sg.module) })),
        )
            .into_response();
    }
    let want = input.status.trim().to_lowercase();
    if !suggestions::SETTABLE.contains(&want.as_str()) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "error": format!("status must be one of {:?} (playing/played are set by playing it)", suggestions::SETTABLE)
            })),
        )
            .into_response();
    }
    if sg.status == "playing" {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": "this suggestion is being played right now — cancel its job first" })),
        )
            .into_response();
    }
    sg.status = want.clone();
    sg.updated_at = suggestions::now_ts();
    sg.comments.push(suggestions::SuggestionComment {
        author: mr_identity(&caller),
        body: match input.note.as_deref().map(str::trim).filter(|n| !n.is_empty()) {
            Some(note) => format!("marked {want} — {note}"),
            None => format!("marked {want}"),
        },
        action: Some("system".to_string()),
        timestamp: suggestions::now_ts(),
    });
    if let Err(e) = suggestions::save(&sg) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    Json(json!(sg)).into_response()
}

async fn delete_suggestion(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let Some(sg) = suggestions::load(&id) else {
        return sg_not_found(&id);
    };
    let sg = reconcile_suggestion(&mgr, sg);
    let admin = suggestion_admin(&caller, &sg.module);
    // The author may withdraw their own — but only while nothing has been
    // done with it, so a played suggestion keeps its provenance.
    let own_and_untouched = mr_identity(&caller) == sg.author && sg.plays.is_empty();
    if !admin && !own_and_untouched {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({ "error": "only an admin, or the author of an unplayed suggestion, can delete it" })),
        )
            .into_response();
    }
    if sg.status == "playing" {
        return (
            StatusCode::CONFLICT,
            Json(json!({ "error": "this suggestion is being played right now — cancel its job first" })),
        )
            .into_response();
    }
    if let Err(e) = suggestions::remove(&sg.id) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    Json(json!({ "ok": true, "deleted": sg.id, "module": sg.module })).into_response()
}

#[derive(Deserialize, Default)]
struct PlaySuggestionBody {
    #[serde(default)]
    model: Option<String>,
    /// Extra admin guidance appended to the brief — outranks the suggestion.
    #[serde(default)]
    instructions: Option<String>,
    /// Replace the generated brief outright (the console's "edit before you
    /// play" box). The suggestion is still recorded as the origin.
    #[serde(default)]
    prompt: Option<String>,
}

async fn play_suggestion(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
    body: Option<Json<PlaySuggestionBody>>,
) -> impl IntoResponse {
    let caller = match require_bearer(&headers) {
        Ok(c) => c,
        Err(r) => return r,
    };
    let Some(sg) = suggestions::load(&id) else {
        return sg_not_found(&id);
    };
    let mut sg = reconcile_suggestion(&mgr, sg);
    // Playing writes to a live module, so it is the admin's call — and it runs
    // on the admin's own account, exactly like an edit they typed themselves.
    // No sudo signature: this is the same power as the ask bar, not a merge of
    // contributed bytes.
    if !suggestion_admin(&caller, &sg.module) {
        return (
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": format!("only an admin of '{}' can play a suggestion — it runs as an edit on their account", sg.module)
            })),
        )
            .into_response();
    }
    if sg.status == "playing" {
        return (
            StatusCode::CONFLICT,
            Json(json!({
                "error": "already playing",
                "job_id": sg.latest_play().map(|p| p.job_id.clone()),
            })),
        )
            .into_response();
    }
    let Some(root) = module_root_for(&sg.module) else {
        return mr_not_found_module(&sg.module);
    };
    let body = body.map(|Json(b)| b).unwrap_or_default();
    let prompt = match body.prompt.as_deref().map(str::trim).filter(|p| !p.is_empty()) {
        Some(custom) => custom.to_string(),
        None => suggestions::build_play_prompt(&sg, &root, body.instructions.as_deref()),
    };
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
            agent: None,
            agent_params: None,
            replace_job_id: None,
            user_address: Some(caller.clone()),
        })
        .await;
    let ts = suggestions::now_ts();
    sg.status = "playing".to_string();
    sg.updated_at = ts;
    sg.plays.push(suggestions::Play {
        job_id: job.id.clone(),
        played_by: mr_identity(&caller),
        timestamp: ts,
        outcome: None,
        cid: None,
    });
    sg.comments.push(suggestions::SuggestionComment {
        author: mr_identity(&caller),
        body: format!("playing this as an edit → job {}", job.id),
        action: Some("system".to_string()),
        timestamp: ts,
    });
    if let Err(e) = suggestions::save(&sg) {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(json!({ "error": e }))).into_response();
    }
    (
        StatusCode::ACCEPTED,
        Json(json!({ "ok": true, "suggestion": sg, "job_id": job.id })),
    )
        .into_response()
}

#[cfg(test)]
mod nested_mods_tests {
    use super::find_nested_mods;

    // */src is the module itself, not a mod named "src": no row for src
    // dirs, markers under src/ credit the enclosing dir, and rel paths
    // elide the segment (src/app → app).
    #[test]
    fn src_is_transparent() {
        let root = std::env::temp_dir().join(format!("nested-mods-test-{}", std::process::id()));
        let mk = |rel: &str| std::fs::create_dir_all(root.join(rel)).unwrap();
        let touch = |rel: &str| std::fs::write(root.join(rel), "").unwrap();
        mk("src/app");
        touch("src/mod.py"); // the module's own mod.py — belongs to root, no row
        touch("src/app/mod.py"); // nested mod, addressed as `app` not `src/app`
        mk("tests");
        touch("tests/mod.py");
        mk("worker"); // marker lives under worker/src → worker is the mod
        mk("worker/src");
        touch("worker/src/mod.py");

        let mut out = Vec::new();
        find_nested_mods(&root, &root, 4, &mut out);
        std::fs::remove_dir_all(&root).ok();

        let mut rels: Vec<&str> = out.iter().filter_map(|m| m["rel"].as_str()).collect();
        rels.sort();
        assert_eq!(rels, vec!["app", "tests", "worker"]);
    }
}

#[cfg(test)]
mod module_reader_tests {
    use super::{confine_to_module_tree, module_anchor_root, safe_anchor, valid_module_name};

    // The public /modules readers splice the name straight into a path, so
    // anything that could climb out of the tree is refused up front.
    #[test]
    fn module_names_cannot_climb_out() {
        assert!(valid_module_name("store"));
        assert!(valid_module_name("bloctime/app"));
        assert!(valid_module_name("agent-2_x.1"));

        assert!(!valid_module_name(""));
        assert!(!valid_module_name(".."));
        assert!(!valid_module_name("../../etc"));
        assert!(!valid_module_name("a/../../b"));
        assert!(!valid_module_name("/etc/passwd"));
        assert!(!valid_module_name("a//b"));
        assert!(!valid_module_name("evil$(whoami)"));
        assert!(!valid_module_name(&"x".repeat(129)));
    }

    // `?anchor=` / `?path=` are caller-supplied directory hints on a public
    // endpoint: inside the module tree they pass, everywhere else they don't.
    #[test]
    fn directory_hints_stay_in_the_tree() {
        let root = module_anchor_root();
        assert_eq!(confine_to_module_tree(&root.to_string_lossy()), Some(root.clone()));
        assert!(confine_to_module_tree("/etc").is_none());
        assert!(confine_to_module_tree("/root/.ssh").is_none());
        assert!(confine_to_module_tree("~/.mod/build-fork/private").is_none());
        assert!(confine_to_module_tree(&format!("{}/../..", root.display())).is_none());

        // A refused hint falls back to the tree root rather than honoring it.
        assert_eq!(safe_anchor(Some("/etc".into())), root.to_string_lossy());
        assert_eq!(safe_anchor(None), root.to_string_lossy());
    }
}

#[cfg(test)]
mod rename_tests {
    use super::{apply_wiring, rename_wiring_patterns, replace_whole_word, rewrite_module_refs};

    // The whole-word pass is what makes `refs=all` safe to offer: a module
    // named `bt` must not turn `debt` into `debeta`.
    #[test]
    fn whole_word_leaves_substrings_alone() {
        let (out, hits) = replace_whole_word("alpha is alphabet, non-alpha, \"/alpha\"", "alpha", "beta");
        assert_eq!(hits, 2); // `alpha is` and the quoted route; NOT alphabet
        assert_eq!(out, "beta is alphabet, non-alpha, \"/beta\"");
    }

    // The default pass rewrites wiring and nothing else — prose about a
    // module called `demo` survives a rename to `showcase`.
    #[test]
    fn wiring_patterns_skip_prose() {
        let pats = rename_wiring_patterns("demo", "showcase");
        let src = "\
# the demo module, a demo of demos
ROOT = \"/root/mod/mod/orbit/demo\"
STATE = \"~/.mod/demo/state.json\"
API = \"/demo/api\"
PM2 = \"demo-api\"
CALL = m.mod(\"demo\")
CLI: m demo/go
";
        let (out, hits) = apply_wiring(src, &pats);
        assert!(hits >= 6, "expected the wiring hits, got {hits}");
        assert!(out.contains("# the demo module, a demo of demos"));
        assert!(out.contains("/root/mod/mod/orbit/showcase"));
        assert!(out.contains("~/.mod/showcase/state.json"));
        assert!(out.contains("\"/showcase/api\""));
        assert!(out.contains("showcase-api"));
        assert!(out.contains("m.mod(\"showcase\")"));
        assert!(out.contains("m showcase/go"));
    }

    // Over a real tree: build output is skipped, binaries are skipped, and a
    // dry run reports without writing.
    #[test]
    fn rewrite_skips_build_output_and_can_dry_run() {
        let root = std::env::temp_dir().join(format!("rename-refs-test-{}", std::process::id()));
        std::fs::create_dir_all(root.join("node_modules/pkg")).unwrap();
        std::fs::create_dir_all(root.join("src")).unwrap();
        std::fs::write(root.join("src/mod.py"), "ROOT = \"/root/mod/mod/orbit/demo\"\n").unwrap();
        std::fs::write(root.join("node_modules/pkg/index.js"), "require('/root/mod/mod/orbit/demo')\n").unwrap();
        std::fs::write(root.join("blob.bin"), [0u8, 1, 2, b'd', b'e', b'm', b'o']).unwrap();

        let (files, changed, hits, errors) = rewrite_module_refs(&root, "demo", "showcase", "paths", false);
        assert_eq!(changed, 1, "only src/mod.py is rewritable: {files:?}");
        assert_eq!(hits, 1);
        assert!(errors.is_empty());
        // Dry run wrote nothing.
        let before = std::fs::read_to_string(root.join("src/mod.py")).unwrap();
        assert!(before.contains("orbit/demo"));

        let (_, changed, _, errors) = rewrite_module_refs(&root, "demo", "showcase", "paths", true);
        assert_eq!(changed, 1);
        assert!(errors.is_empty());
        let after = std::fs::read_to_string(root.join("src/mod.py")).unwrap();
        assert!(after.contains("orbit/showcase"));
        // The vendored copy still names the old module — not ours to rewrite.
        let vendored = std::fs::read_to_string(root.join("node_modules/pkg/index.js")).unwrap();
        assert!(vendored.contains("orbit/demo"));

        std::fs::remove_dir_all(&root).ok();
    }
}

// ── Cost metering + spend limits ─────────────────────────────────────
//
// Backed by costs.rs. Two audiences:
//
//   * the person running tasks — "what have I spent, what's left"
//   * everyone else — the aggregate, because the average cost per user is
//     what the costmarket module's prediction market settles on, and a
//     settlement oracle only one party can read is not an oracle.
//
// Per-user rows are public for the same reason /jobs is: tasks are already a
// public ledger, so what they cost is not a new disclosure. Nothing here
// exposes a balance or a grant — that stays behind /credits.

#[derive(Deserialize)]
struct CostQuery {
    /// "YYYY-MM" — a calendar month in UTC. Defaults to the current month.
    month: Option<String>,
    /// Trailing window in days; wins over `month` when both are given.
    days: Option<i64>,
}

/// GET /costs — spend over a window, plus the average per user.
async fn get_costs(Query(q): Query<CostQuery>) -> impl IntoResponse {
    let summary = match q.days {
        Some(d) if d > 0 => {
            let now = chrono::Utc::now().timestamp();
            costs::summarize("window", now - d * 86_400, now + 1)
        }
        _ => {
            let month = q.month.unwrap_or_else(costs::current_month);
            match costs::summarize_month(&month) {
                Some(s) => s,
                None => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(json!({ "error": "month must be YYYY-MM" })),
                    )
                        .into_response()
                }
            }
        }
    };
    Json(json!({ "summary": summary, "policy_metering": costs::policy().metering })).into_response()
}

/// GET /costs/epoch/:month — the settlement view for one calendar month.
/// Deliberately narrow: the number, the inputs to it, and whether the month
/// is closed. A market must never settle on an open month.
async fn get_cost_epoch(Path(month): Path<String>) -> impl IntoResponse {
    match costs::summarize_month(&month) {
        Some(s) => Json(json!({
            "epoch": s.epoch,
            "from_ts": s.from_ts,
            "to_ts": s.to_ts,
            "final": s.final_,
            "users": s.users,
            "tasks": s.tasks,
            "total_usd": s.total_usd,
            "avg_usd_per_user": s.avg_usd_per_user,
            "avg_usd6_per_user": s.avg_usd6_per_user,
            "avg_usd_per_task": s.avg_usd_per_task,
        }))
        .into_response(),
        None => (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": "month must be YYYY-MM" })),
        )
            .into_response(),
    }
}

/// GET /costs/me — the caller's own spend, balance, and recent tasks.
async fn get_my_costs(headers: axum::http::HeaderMap) -> impl IntoResponse {
    let identity = identity_of(&headers);
    let month = costs::current_month();
    let summary = costs::summarize_month(&month);
    let mine = summary
        .as_ref()
        .and_then(|s| s.rows.iter().find(|r| r.identity == identity).cloned());
    let account = credits::account_view(&identity).await;
    let policy = costs::policy();
    Json(json!({
        "identity": identity,
        "month": month,
        "spent_this_month_usd": mine.as_ref().map(|m| m.usd.clone()).unwrap_or_else(|| "0.00".into()),
        "tasks_this_month": mine.as_ref().map(|m| m.tasks).unwrap_or(0),
        "balance_usd": account.usd,
        "lifetime_spent_usd": account.spent_usd,
        "gated": !can_spend(&identity).await.0,
        "free_tier_usd": credits::fmt_usd(policy.free_tier_usd6 as u128),
        "margin_bps": policy.margin_bps,
        "recent": costs::recent_for(&identity, 25),
    }))
    .into_response()
}

/// GET /costs/policy — the spend rules in force (public: they decide who can
/// use the console, so people are entitled to read them).
async fn get_cost_policy() -> impl IntoResponse {
    let p = costs::policy();
    Json(json!({
        "metering": p.metering,
        "gate_when_empty": p.gate_when_empty,
        "min_balance_usd": credits::fmt_usd(p.min_balance_usd6 as u128),
        "free_tier_usd": credits::fmt_usd(p.free_tier_usd6 as u128),
        "margin_bps": p.margin_bps,
    }))
    .into_response()
}

#[derive(Deserialize)]
struct CostPolicyBody {
    metering: Option<bool>,
    gate_when_empty: Option<bool>,
    min_balance_usd: Option<String>,
    free_tier_usd: Option<String>,
    margin_bps: Option<u32>,
}

/// POST /costs/policy — owner only. Omitted fields keep their current value.
async fn set_cost_policy(
    headers: axum::http::HeaderMap,
    Json(body): Json<CostPolicyBody>,
) -> impl IntoResponse {
    if let Err(r) = require_owner(&headers) {
        return r.into_response();
    }
    let mut p = costs::policy();
    if let Some(v) = body.metering {
        p.metering = v;
    }
    if let Some(v) = body.gate_when_empty {
        p.gate_when_empty = v;
    }
    if let Some(v) = body.min_balance_usd {
        match credits::parse_usd(&v) {
            Ok(u) => p.min_balance_usd6 = u as u64,
            Err(e) => return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
        }
    }
    if let Some(v) = body.free_tier_usd {
        match credits::parse_usd(&v) {
            Ok(u) => p.free_tier_usd6 = u as u64,
            Err(e) => return (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
        }
    }
    if let Some(v) = body.margin_bps {
        // 100% markup is already absurd; beyond that it is a typo.
        p.margin_bps = v.min(10_000);
    }
    match costs::save_policy(&p) {
        Ok(()) => Json(json!({ "ok": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response(),
    }
}

/// The identity a request spends as — "local" for an unauthenticated call in
/// local mode, which is also how costs.rs groups those rows.
fn identity_of(headers: &axum::http::HeaderMap) -> String {
    let a = auth::extract_address_from_headers(headers).unwrap_or_default();
    if a.trim().is_empty() {
        "local".to_string()
    } else {
        a.to_lowercase()
    }
}

/// May this identity start a metered task, and if not, why not?
///
/// The owner is never gated. That is the point of the rule: when the console
/// has no funds it stops being a service to other people and goes back to
/// being the owner's own tool, rather than bricking itself.
async fn can_spend(identity: &str) -> (bool, String) {
    let p = costs::policy();
    if !p.gate_when_empty {
        return (true, String::new());
    }
    if identity.is_empty() || identity == "local" || auth::is_owner(identity) {
        return (true, String::new());
    }
    // Free tier is measured against lifetime metered spend, so it can't be
    // reset by topping up and spending back down.
    let spent: u128 = credits::read_ledger()
        .accounts
        .get(identity)
        .map(|a| a.spent_usd6())
        .unwrap_or(0);
    if spent < p.free_tier_usd6 as u128 {
        return (true, String::new());
    }
    let available = credits::available_usd6(identity).await.unwrap_or(0);
    let floor = p.min_balance_usd6 as i128;
    if available > floor {
        return (true, String::new());
    }
    (
        false,
        format!(
            "out of credit — balance ${}, minimum ${}. Top up on-chain or ask the owner for a grant; \
tasks are metered per run and only the owner can run them once the credit is gone.",
            credits::fmt_usd(available.max(0) as u128),
            credits::fmt_usd(p.min_balance_usd6 as u128)
        ),
    )
}

// ── Peer audits ──────────────────────────────────────────────────────
//
// See audits.rs. Reading is public (an audit nobody can read is not an
// audit); requesting one needs a bearer token, because it spends money and
// the record carries the requester's name.

/// Fold a finished job back into its audit record. Lazy, on read.
fn reconcile_audit(mgr: &ClaudeJobManager, a: audits::Audit) -> audits::Audit {
    if a.status != "running" {
        return a;
    }
    match mgr.get_job(&a.job_id) {
        Some(job) => audits::reconcile(a, &job),
        None => a,
    }
}

#[derive(Deserialize)]
struct AuditListQuery {
    limit: Option<usize>,
    module: Option<String>,
}

/// GET /audits — the cross-module feed, newest first (default 10).
async fn list_audits(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Query(q): Query<AuditListQuery>,
) -> impl IntoResponse {
    // An audit is a report on one module's code — it leaves the public
    // ledger with the module it reviewed.
    let hidden = crate::privacy::hidden_names(&reader(&headers));
    let limit = q.limit.unwrap_or(audits::DEFAULT_LIMIT);
    let rows: Vec<audits::Audit> = audits::list(q.module.as_deref(), limit)
        .into_iter()
        .filter(|a| !hidden.contains(&a.module.to_lowercase()))
        .map(|a| reconcile_audit(&mgr, a))
        .collect();
    Json(json!({ "audits": rows, "count": rows.len() })).into_response()
}

/// GET /modules/:name/audits — the previous N audits of one module.
async fn list_module_audits(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(name): Path<String>,
    Query(q): Query<AuditListQuery>,
) -> impl IntoResponse {
    if !crate::privacy::can_access(&reader(&headers), &name) {
        return (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("module '{name}' not found") })),
        )
            .into_response();
    }
    let limit = q.limit.unwrap_or(audits::DEFAULT_LIMIT);
    let rows: Vec<audits::Audit> = audits::list(Some(&name), limit)
        .into_iter()
        .map(|a| reconcile_audit(&mgr, a))
        .collect();
    Json(json!({ "module": name, "audits": rows, "count": rows.len() })).into_response()
}

/// GET /audits/stats — per-module rollup for the hub.
async fn audit_stats(headers: axum::http::HeaderMap) -> impl IntoResponse {
    let hidden = crate::privacy::hidden_names(&reader(&headers));
    let stats: Vec<_> = audits::stats()
        .into_iter()
        .filter(|s| !hidden.contains(&s.module.to_lowercase()))
        .collect();
    Json(json!({ "stats": stats })).into_response()
}

/// GET /audits/:id — one audit, reconciled.
async fn get_audit(
    headers: axum::http::HeaderMap,
    State(mgr): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    let hidden = crate::privacy::hidden_names(&reader(&headers));
    match audits::get(&id).filter(|a| !hidden.contains(&a.module.to_lowercase())) {
        Some(a) => {
            let a = reconcile_audit(&mgr, a);
            Json(json!({ "audit": a })).into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(json!({ "error": format!("audit '{id}' not found") })),
        )
            .into_response(),
    }
}

#[derive(Deserialize, Default)]
struct StartAuditBody {
    /// What the auditor wants looked at, if anything in particular.
    #[serde(default)]
    note: String,
    /// Model to review with. Defaults to the console's default.
    #[serde(default)]
    model: Option<String>,
    /// Audit a specific past snapshot instead of the live tree.
    #[serde(default)]
    base_cid: Option<String>,
}

/// POST /modules/:name/audit — audit any module.
///
/// The module is snapshotted and restored into the caller's own workspace,
/// and the agent reviews that copy. So a stranger can audit a module they
/// have no write access to without ever touching it, and the audit is pinned
/// to the exact tree it read.
async fn start_audit(
    headers: axum::http::HeaderMap,
    Path(name): Path<String>,
    State(mgr): State<AppState>,
    body: Option<Json<StartAuditBody>>,
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
    let identity = if caller.trim().is_empty() {
        "local".to_string()
    } else {
        caller.to_lowercase()
    };

    // Audits cost money like any other task, so they answer to the same gate.
    let (allowed, why) = can_spend(&identity).await;
    if !allowed {
        return (
            StatusCode::PAYMENT_REQUIRED,
            Json(json!({ "error": why, "out_of_credit": true })),
        )
            .into_response();
    }

    let body = body.map(|Json(b)| b).unwrap_or_default();
    let store = default_store();

    // Pin the tree: either an explicit past snapshot, or the live one.
    let base_cid = match body.base_cid.clone() {
        Some(cid) => {
            if !store.has(&cid) {
                return (
                    StatusCode::NOT_FOUND,
                    Json(json!({ "error": format!("snapshot '{cid}' is not in the local store") })),
                )
                    .into_response();
            }
            cid
        }
        None => {
            let Some(root) = module_root_for(&name) else {
                return mr_not_found_module(&name);
            };
            match snapshot_dir(&root, &store) {
                Ok((cid, _)) => cid,
                Err(e) => {
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        Json(json!({ "error": format!("snapshotting {name}: {e}") })),
                    )
                        .into_response()
                }
            }
        }
    };

    let audit_id = uuid::Uuid::new_v4().to_string();
    let short: String = audit_id.chars().take(8).collect();
    let dir = userspace::peer_root(&identity)
        .join("audits")
        .join(format!("{name}-{short}"));
    if let Err(e) = restore_into(&dir, &base_cid, &store) {
        let _ = std::fs::remove_dir_all(&dir);
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("staging the tree to audit: {e}") })),
        )
            .into_response();
    }

    let job = mgr
        .submit(SubmitRequest {
            prompt: audits::audit_prompt(&name, &body.note),
            model: body.model.unwrap_or_else(|| "claude-fable-5".to_string()),
            work_dir: Some(dir.to_string_lossy().into_owned()),
            module_name: None,
            creation_mode: None,
            fork_source: None,
            anchor_dir: None,
            images: None,
            agent_type: None,
            system_prompt: None,
            agent: Some("claude".to_string()),
            agent_params: None,
            replace_job_id: None,
            user_address: Some(caller.clone()),
        })
        .await;

    let now = chrono::Utc::now().timestamp();
    let audit = audits::Audit {
        id: audit_id,
        module: name.clone(),
        auditor: identity,
        created_at: now,
        updated_at: now,
        status: "running".to_string(),
        job_id: job.id.clone(),
        base_cid: Some(base_cid.clone()),
        cid: None,
        note: body.note.clone(),
        verdict: String::new(),
        score: -1,
        findings: 0,
        summary: String::new(),
        cost_usd: "0.00".to_string(),
    };
    if let Err(e) = audits::insert(&audit) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": e })),
        )
            .into_response();
    }

    (
        StatusCode::CREATED,
        Json(json!({
            "audit": audit,
            "job_id": job.id,
            "base_cid": base_cid,
            "stream": format!("/jobs/{}/stream", job.id),
            "next": "watch the job stream; the audit gets its own CID when it finishes",
        })),
    )
        .into_response()
}
