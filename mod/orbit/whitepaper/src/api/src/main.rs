// Rust API for the MOD whitepaper module.
// Implements the off-chain Merkle-tree registry described in whitepaper.tex
// and interfaces with the surrounding mod protocol (config.json, ~/.mod state,
// `m` CLI calls to peer modules).

mod mod_protocol;
mod tree;

use std::net::SocketAddr;
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use tower_http::cors::{Any, CorsLayer};

use mod_protocol::ModProtocol;
use tree::{build_tree, proof_for, verify, Manifest};

#[derive(Clone)]
struct AppState {
    proto: Arc<ModProtocol>,
}

#[derive(Serialize)]
struct InfoResp {
    name: &'static str,
    description: &'static str,
    tex: String,
    tex_exists: bool,
    ports: PortInfo,
    proxy: Option<Value>,
    tree_state: Option<String>,
}

#[derive(Serialize)]
struct PortInfo {
    api: u16,
    app: u16,
}

#[derive(Deserialize)]
struct BuildReq {
    records: Vec<Value>,
}

#[derive(Deserialize)]
struct ProofReq {
    name: String,
}

#[derive(Deserialize)]
struct VerifyReq {
    leaf: String,
    proof: Vec<String>,
    root: Option<String>,
}

struct ApiError(anyhow::Error);

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let body = serde_json::json!({ "error": self.0.to_string() });
        (StatusCode::INTERNAL_SERVER_ERROR, Json(body)).into_response()
    }
}

impl<E: Into<anyhow::Error>> From<E> for ApiError {
    fn from(e: E) -> Self {
        Self(e.into())
    }
}

async fn info(State(s): State<AppState>) -> Result<Json<InfoResp>, ApiError> {
    let proto = &s.proto;
    Ok(Json(InfoResp {
        name: "whitepaper",
        description: "Off-chain Merkle-tree registry whitepaper + reference impl (Rust API).",
        tex: proto.tex_path().display().to_string(),
        tex_exists: proto.tex_path().exists(),
        ports: PortInfo {
            api: proto.config_u16("port").unwrap_or(50106),
            app: proto.config_u16("app_port").unwrap_or(3106),
        },
        proxy: proto.config_field("proxy").cloned(),
        tree_state: if proto.tree_file().exists() {
            Some(proto.tree_file().display().to_string())
        } else {
            None
        },
    }))
}

async fn paper_tex(State(s): State<AppState>) -> Result<String, ApiError> {
    Ok(std::fs::read_to_string(s.proto.tex_path())?)
}

async fn paper(State(s): State<AppState>) -> Result<Json<Value>, ApiError> {
    let tex = std::fs::read_to_string(s.proto.tex_path())?;
    Ok(Json(serde_json::json!({ "length": tex.len(), "tex": tex })))
}

async fn tree_root(State(s): State<AppState>) -> Result<Json<Value>, ApiError> {
    if !s.proto.tree_file().exists() {
        return Ok(Json(serde_json::json!({ "root": null, "epoch": null })));
    }
    let manifest: Manifest = serde_json::from_str(&std::fs::read_to_string(s.proto.tree_file())?)?;
    Ok(Json(serde_json::json!({
        "root": manifest.root,
        "epoch": manifest.epoch,
        "count": manifest.count,
    })))
}

async fn tree_build(
    State(s): State<AppState>,
    Json(req): Json<BuildReq>,
) -> Result<Json<Value>, ApiError> {
    let manifest = build_tree(req.records)?;
    std::fs::write(s.proto.tree_file(), serde_json::to_vec(&manifest)?)?;
    Ok(Json(serde_json::json!({
        "root": manifest.root,
        "count": manifest.count,
        "epoch": manifest.epoch,
    })))
}

async fn tree_proof(
    State(s): State<AppState>,
    Json(req): Json<ProofReq>,
) -> Result<Json<Value>, ApiError> {
    let manifest: Manifest = serde_json::from_str(&std::fs::read_to_string(s.proto.tree_file())?)?;
    let p = proof_for(&manifest, &req.name)?;
    Ok(Json(p))
}

async fn tree_verify(
    State(s): State<AppState>,
    Json(req): Json<VerifyReq>,
) -> Result<Json<Value>, ApiError> {
    let root = match req.root {
        Some(r) => r,
        None => {
            let manifest: Manifest =
                serde_json::from_str(&std::fs::read_to_string(s.proto.tree_file())?)?;
            manifest.root
        }
    };
    let ok = verify(&req.leaf, &req.proof, &root)?;
    Ok(Json(serde_json::json!({ "ok": ok, "root": root })))
}

// Bridge: invoke an arbitrary mod-protocol function on this host (subprocess `m`).
//   POST /mod/call  { "fn": "agent/info", "args": { ... } }
async fn mod_call(
    State(s): State<AppState>,
    Json(req): Json<Value>,
) -> Result<Json<Value>, ApiError> {
    let func = req
        .get("fn")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("missing `fn`"))?;
    let args = req.get("args").cloned().unwrap_or(serde_json::json!({}));
    let out = s.proto.call(func, args)?;
    Ok(Json(out))
}

// ── Orbit + core surface ─────────────────────────────────────

async fn list_mods(
    State(s): State<AppState>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Json<Value>, ApiError> {
    let scope = q.get("scope").map(String::as_str).unwrap_or("orbit");
    let mods = s.proto.list_modules(scope);
    Ok(Json(serde_json::json!({ "scope": scope, "count": mods.len(), "mods": mods })))
}

async fn mod_info(
    State(s): State<AppState>,
    Path(name): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let dir = s.proto.module_dir_of(&name)?;
    let cfg = s.proto.module_config(&name).unwrap_or(Value::Null);
    Ok(Json(serde_json::json!({
        "name": name,
        "path": dir.display().to_string(),
        "scope": if dir.starts_with(&s.proto.orbit_dir) { "orbit" } else { "core" },
        "config": cfg,
    })))
}

async fn mod_config(
    State(s): State<AppState>,
    Path(name): Path<String>,
) -> Result<Json<Value>, ApiError> {
    Ok(Json(s.proto.module_config(&name)?))
}

// HTTP-forward to a peer module's API. Faster than the `m` subprocess.
//   POST /mod/http  { "name": "polymarket", "method": "GET", "path": "/info" }
async fn mod_http(
    State(s): State<AppState>,
    Json(req): Json<Value>,
) -> Result<Json<Value>, ApiError> {
    let name = req
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("missing `name`"))?;
    let method = req.get("method").and_then(|v| v.as_str()).unwrap_or("GET");
    let path = req.get("path").and_then(|v| v.as_str()).unwrap_or("/");
    let body = req.get("body").cloned();
    Ok(Json(s.proto.http_call(name, method, path, body)?))
}

// Build a Merkle tree over the *live* orbit (and optionally core) ecosystem.
//   POST /tree/merkle  { "scope": "orbit" | "core" | "all" }
async fn tree_merkle(
    State(s): State<AppState>,
    Json(req): Json<Value>,
) -> Result<Json<Value>, ApiError> {
    let scope = req.get("scope").and_then(|v| v.as_str()).unwrap_or("orbit");
    let records = s.proto.merkle_records(scope);
    let manifest = build_tree(records)?;
    std::fs::write(s.proto.tree_file(), serde_json::to_vec(&manifest)?)?;
    Ok(Json(serde_json::json!({
        "scope": scope,
        "root": manifest.root,
        "epoch": manifest.epoch,
        "count": manifest.count,
    })))
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let proto = Arc::new(ModProtocol::discover()?);
    let port = proto.config_u16("port").unwrap_or(50106);
    let state = AppState { proto: proto.clone() };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    let app = Router::new()
        .route("/info", get(info))
        .route("/paper", get(paper))
        .route("/paper.tex", get(paper_tex))
        .route("/tree/root", get(tree_root))
        .route("/tree/build", post(tree_build))
        .route("/tree/merkle", post(tree_merkle))
        .route("/tree/proof", post(tree_proof))
        .route("/tree/verify", post(tree_verify))
        .route("/mods", get(list_mods))
        .route("/mods/:name", get(mod_info))
        .route("/mods/:name/config", get(mod_config))
        .route("/mod/call", post(mod_call))
        .route("/mod/http", post(mod_http))
        .with_state(state)
        .layer(cors);

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    tracing::info!("whitepaper-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
