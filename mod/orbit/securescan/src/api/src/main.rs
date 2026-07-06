//! securescan API — Rust (axum) HTTP server.
//!
//! Endpoints
//!   GET  /health           — liveness
//!   POST /scan             — start a scan; body { repo, branch?, steps?, provider?, model? }
//!   GET  /scans            — list recent scans
//!   GET  /scans/:id        — get a single scan status / report
//!   DELETE /scans/:id      — remove a scan
//!
//! Scans are executed by shelling out to the framework CLI:
//!     m securescan scan_github repo=<url> scan_id=<id> [...]
//!
//! Status & report files are written by the Python module to:
//!     ~/.securescan/scans/<id>/status.json
//!
//! The API simply orchestrates: it allocates the id, spawns the background
//! process, and serves the JSON files back to clients.

use axum::{
    extract::{Path, State},
    http::{HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha1::{Digest, Sha1};
use std::{
    env,
    net::SocketAddr,
    path::PathBuf,
    sync::Arc,
    time::{SystemTime, UNIX_EPOCH},
};
use tokio::process::Command;
use tower_http::{cors::CorsLayer, limit::RequestBodyLimitLayer, trace::TraceLayer};

#[derive(Clone)]
struct AppState {
    store_dir: PathBuf,
    scans_dir: PathBuf,
}

impl AppState {
    fn new() -> anyhow::Result<Self> {
        let home = env::var("HOME").unwrap_or_else(|_| "/tmp".into());
        let store_dir = env::var("SECURESCAN_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from(home).join(".securescan"));
        let scans_dir = store_dir.join("scans");
        std::fs::create_dir_all(&scans_dir)?;
        Ok(Self { store_dir, scans_dir })
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "securescan_api=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = env::var("PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50092);

    let state = Arc::new(AppState::new()?);

    let cors = CorsLayer::new()
        .allow_origin(tower_http::cors::Any)
        .allow_methods(tower_http::cors::Any)
        .allow_headers([axum::http::header::CONTENT_TYPE]);
    let _ = HeaderValue::from_static("*"); // suppress unused import warning if any

    let app = Router::new()
        .route("/", get(health))
        .route("/health", get(health))
        .route("/scan", post(start_scan))
        .route("/scans", get(list_scans))
        .route("/scans/:id", get(get_scan))
        .route("/scans/:id", delete(delete_scan))
        .with_state(state)
        .layer(RequestBodyLimitLayer::new(64 * 1024))
        .layer(TraceLayer::new_for_http())
        .layer(cors);

    let addr: SocketAddr = format!("0.0.0.0:{port}").parse()?;
    tracing::info!("securescan API listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app.into_make_service()).await?;
    Ok(())
}

// ── handlers ────────────────────────────────────────────────────

async fn health(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "module": "securescan",
        "store_dir": state.store_dir.display().to_string(),
    }))
}

#[derive(Deserialize)]
struct ScanRequest {
    repo: String,
    branch: Option<String>,
    /// Optional path inside the repo to scan (e.g. "mod/orbit/bridge").
    /// Validated server-side: must be a relative path with no traversal.
    subdir: Option<String>,
    steps: Option<u32>,
    provider: Option<String>,
    model: Option<String>,
    key: Option<String>,
    /// EVM address of the reviewer (from a connected wallet). When set, this
    /// is recorded as the scan's reviewer instead of a key-derived wallet.
    reviewer: Option<String>,
}

#[derive(Serialize)]
struct ScanResponse {
    scan_id: String,
    repo: String,
    status: &'static str,
}

async fn start_scan(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ScanRequest>,
) -> Result<Json<ScanResponse>, ApiError> {
    let repo_url = normalize_github(&req.repo)
        .ok_or_else(|| ApiError::bad("invalid github repo (expected owner/name or https://github.com/owner/name)"))?;

    // Validate subdir at API edge — reject path traversal before passing it
    // through to the spawned `m` invocation.
    if let Some(s) = &req.subdir {
        if let Err(msg) = validate_subdir(s) {
            return Err(ApiError::bad(msg));
        }
    }

    // Validate the reviewer (EVM address) at the edge so junk addresses can't
    // get persisted and so we never shell out a malformed value.
    let reviewer = match req.reviewer.as_deref() {
        Some(r) => Some(validate_reviewer(r).map_err(ApiError::bad)?),
        None => None,
    };

    let scan_id = make_scan_id(&repo_url);
    let scan_dir = state.scans_dir.join(&scan_id);
    std::fs::create_dir_all(&scan_dir).map_err(|e| ApiError::internal(e.to_string()))?;

    let status = json!({
        "scan_id": scan_id,
        "repo": repo_url,
        "branch": req.branch,
        "subdir": req.subdir,
        "reviewer": reviewer,
        "status": "queued",
        "started_at": now_ts(),
    });
    write_status(&scan_dir, &status).map_err(|e| ApiError::internal(e.to_string()))?;

    spawn_scan_worker(&scan_id, &repo_url, &req);

    Ok(Json(ScanResponse {
        scan_id,
        repo: repo_url,
        status: "queued",
    }))
}

/// Validate an EVM-style reviewer address (`0x` + 40 hex chars). Returns the
/// lowercased canonical form on success so the value persisted everywhere is
/// consistent.
fn validate_reviewer(r: &str) -> Result<String, &'static str> {
    let t = r.trim();
    if t.len() != 42 || !t.starts_with("0x") {
        return Err("reviewer must be a 0x-prefixed 40-char hex address");
    }
    if !t[2..].chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("reviewer must be a 0x-prefixed 40-char hex address");
    }
    Ok(t.to_ascii_lowercase())
}

fn validate_subdir(s: &str) -> Result<(), &'static str> {
    let t = s.trim();
    if t.is_empty() {
        return Err("subdir must not be empty");
    }
    if t.starts_with('/') || t.contains("..") {
        return Err("subdir must be a relative path with no '..'");
    }
    if t.len() > 256 {
        return Err("subdir too long");
    }
    // Conservative charset: letters, digits, dashes, underscores, dots, slashes.
    if !t
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.' | '/'))
    {
        return Err("subdir must be ascii alphanumeric plus -_./");
    }
    Ok(())
}

async fn list_scans(State(state): State<Arc<AppState>>) -> Result<Json<Value>, ApiError> {
    let mut entries: Vec<_> = match std::fs::read_dir(&state.scans_dir) {
        Ok(rd) => rd.flatten().collect(),
        Err(_) => vec![],
    };
    entries.sort_by_key(|e| {
        e.metadata()
            .and_then(|m| m.modified())
            .ok()
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| std::cmp::Reverse(d.as_secs()))
            .unwrap_or(std::cmp::Reverse(0))
    });
    let mut out = Vec::with_capacity(entries.len());
    for e in entries.iter().take(200) {
        if let Some(s) = read_status(&e.path()) {
            out.push(s);
        }
    }
    Ok(Json(json!({ "scans": out, "total": out.len() })))
}

async fn get_scan(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let scan_dir = state.scans_dir.join(safe_id(&id)?);
    if !scan_dir.exists() {
        return Err(ApiError::not_found("scan not found"));
    }
    let status = read_status(&scan_dir).ok_or_else(|| ApiError::not_found("status missing"))?;
    Ok(Json(status))
}

async fn delete_scan(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let id = safe_id(&id)?;
    let scan_dir = state.scans_dir.join(&id);
    let repos_dir = state.store_dir.join("repos").join(&id);
    let mut removed = false;
    for d in [&scan_dir, &repos_dir] {
        if d.exists() {
            let _ = std::fs::remove_dir_all(d);
            removed = true;
        }
    }
    Ok(Json(json!({ "scan_id": id, "deleted": removed })))
}

// ── worker ──────────────────────────────────────────────────────

fn spawn_scan_worker(scan_id: &str, repo_url: &str, req: &ScanRequest) {
    let scan_id = scan_id.to_string();
    let repo_url = repo_url.to_string();
    let branch = req.branch.clone();
    let subdir = req.subdir.clone();
    let steps = req.steps.unwrap_or(15);
    let provider = req.provider.clone();
    let model = req.model.clone();
    let key = req.key.clone();
    // Reviewer was already validated in start_scan; re-validate defensively.
    let reviewer = req
        .reviewer
        .as_deref()
        .and_then(|r| validate_reviewer(r).ok());

    tokio::spawn(async move {
        let mut args: Vec<String> = vec![
            "securescan".into(),
            "scan_github".into(),
            format!("repo={repo_url}"),
            format!("scan_id={scan_id}"),
            format!("steps={steps}"),
        ];
        if let Some(b) = branch {
            args.push(format!("branch={b}"));
        }
        if let Some(s) = subdir {
            args.push(format!("subdir={s}"));
        }
        if let Some(p) = provider {
            args.push(format!("provider={p}"));
        }
        if let Some(m) = model {
            args.push(format!("model={m}"));
        }
        if let Some(k) = key {
            args.push(format!("key={k}"));
        }
        if let Some(r) = reviewer {
            args.push(format!("reviewer={r}"));
        }

        tracing::info!(scan_id = %scan_id, "spawning scan: m {}", args.join(" "));

        let result = Command::new("m")
            .args(&args)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();

        match result {
            Ok(mut child) => {
                if let Err(e) = child.wait().await {
                    tracing::error!(scan_id = %scan_id, "scan child failed: {e}");
                }
            }
            Err(e) => {
                tracing::error!(scan_id = %scan_id, "failed to spawn `m`: {e}");
            }
        }
    });
}

// ── helpers ─────────────────────────────────────────────────────

fn now_ts() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn make_scan_id(repo_url: &str) -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let mut hasher = Sha1::new();
    hasher.update(format!("{repo_url}-{nanos}").as_bytes());
    let h = hex::encode(hasher.finalize());
    let slug = slugify(repo_url);
    let slug = if slug.len() > 40 {
        slug[slug.len() - 40..].to_string()
    } else {
        slug
    };
    format!("{slug}-{}", &h[..12])
}

fn slugify(s: &str) -> String {
    let lower = s.to_lowercase();
    let mut out = String::with_capacity(lower.len());
    let mut prev_dash = false;
    for c in lower.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    out.trim_matches('-').to_string()
}

fn safe_id(id: &str) -> Result<String, ApiError> {
    if id.is_empty() || id.contains('/') || id.contains("..") || id.contains('\\') {
        return Err(ApiError::bad("invalid scan id"));
    }
    Ok(id.to_string())
}

fn normalize_github(repo: &str) -> Option<String> {
    let repo = repo.trim().trim_end_matches('/');
    if repo.is_empty() {
        return None;
    }
    let short = regex::Regex::new(r"^[\w.-]+/[\w.-]+$").ok()?;
    if short.is_match(repo) {
        return Some(format!("https://github.com/{repo}.git"));
    }
    let url = regex::Regex::new(r"^(https?://github\.com/[\w.-]+/[\w.-]+?)(\.git)?$").ok()?;
    if let Some(c) = url.captures(repo) {
        return Some(format!("{}.git", &c[1]));
    }
    if repo.starts_with("git@github.com:") {
        return Some(if repo.ends_with(".git") {
            repo.to_string()
        } else {
            format!("{repo}.git")
        });
    }
    None
}

fn write_status(scan_dir: &std::path::Path, value: &Value) -> std::io::Result<()> {
    let path = scan_dir.join("status.json");
    let tmp = scan_dir.join("status.json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(value).unwrap_or_default())?;
    std::fs::rename(tmp, path)
}

fn read_status(scan_dir: &std::path::Path) -> Option<Value> {
    let path = scan_dir.join("status.json");
    let bytes = std::fs::read(path).ok()?;
    serde_json::from_slice(&bytes).ok()
}

// ── error type ──────────────────────────────────────────────────

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    msg: String,
}

impl ApiError {
    fn bad(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::BAD_REQUEST, msg: msg.into() }
    }
    fn not_found(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::NOT_FOUND, msg: msg.into() }
    }
    fn internal(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::INTERNAL_SERVER_ERROR, msg: msg.into() }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.status, Json(json!({ "error": self.msg }))).into_response()
    }
}

#[cfg(test)]
mod tests {
    use super::validate_subdir;

    #[test]
    fn accepts_normal_paths() {
        assert!(validate_subdir("mod/orbit/bridge").is_ok());
        assert!(validate_subdir("src/api").is_ok());
        assert!(validate_subdir("a").is_ok());
        assert!(validate_subdir("a-b_c.d").is_ok());
    }

    #[test]
    fn rejects_traversal() {
        assert!(validate_subdir("..").is_err());
        assert!(validate_subdir("../etc/passwd").is_err());
        assert!(validate_subdir("a/../../b").is_err());
        assert!(validate_subdir("/abs/path").is_err());
    }

    #[test]
    fn rejects_bad_chars() {
        assert!(validate_subdir("a; rm -rf /").is_err());
        assert!(validate_subdir("a$(whoami)").is_err());
        assert!(validate_subdir("a b").is_err()); // space
        assert!(validate_subdir("").is_err());
    }
}
