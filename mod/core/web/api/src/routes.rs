//! HTTP surface for the mod-api gateway.
//!
//! The gateway strips the `/api/web` prefix before proxying, so these routes
//! are mounted at the root: a request to `modc2.com/api/web/mods` arrives here
//! as `GET /mods`.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use serde::Serialize;
use std::sync::Arc;

use crate::catalog::Catalog;
use crate::chain::ChainClient;

#[derive(Clone)]
pub struct AppState {
    pub catalog: Arc<Catalog>,
    pub chain: Arc<ChainClient>,
    pub version: &'static str,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(info))
        .route("/info", get(info))
        .route("/health", get(health))
        .route("/mods", get(list_mods))
        .route("/mods/:name", get(get_mod))
        .route("/mods/:name/tree", get(mod_tree))
        .route("/mods/:name/file", get(mod_file))
        .route("/stats", get(stats))
        .route("/search", get(search))
        .route("/registry", get(registry))
        .route("/graph", get(graph))
        .with_state(state)
}

#[derive(Serialize)]
struct Info {
    name: &'static str,
    protocol: &'static str,
    version: &'static str,
    tagline: &'static str,
    description: &'static str,
    stats: crate::catalog::Stats,
}

/// Root — protocol identity + live ecosystem stats. The null call (no path)
/// returns info, per the mod protocol URL convention.
async fn info(State(state): State<AppState>) -> impl IntoResponse {
    Json(Info {
        name: "mod",
        protocol: "mod",
        version: state.version,
        tagline: "Write a module. Register it on-chain. Get paid when it runs.",
        description: "A modular runtime for building, registering, and \
                      monetizing software on-chain. Every module is a \
                      directory with a config — write code, register it, set a \
                      price, and earn every time someone calls it.",
        stats: state.catalog.stats(),
    })
}

#[derive(Serialize)]
struct Health {
    status: &'static str,
    orbit: String,
    modules: usize,
}

async fn health(State(state): State<AppState>) -> impl IntoResponse {
    Json(Health {
        status: "ok",
        orbit: state.catalog.orbit_dir().display().to_string(),
        modules: state.catalog.modules().len(),
    })
}

/// Serialize a module to JSON and stamp on whether it's registered on-chain,
/// so the explorer can badge it without a second round-trip per card.
fn module_json(
    module: &crate::catalog::Module,
    registered: &std::collections::HashSet<String>,
) -> serde_json::Value {
    let mut v = serde_json::to_value(module).unwrap_or_else(|_| serde_json::json!({}));
    if let Some(obj) = v.as_object_mut() {
        obj.insert(
            "registered".into(),
            serde_json::Value::Bool(registered.contains(&module.name.to_lowercase())),
        );
    }
    v
}

async fn list_mods(State(state): State<AppState>) -> impl IntoResponse {
    let registered = state.chain.registered_names().await;
    let mods: Vec<_> = state
        .catalog
        .modules()
        .iter()
        .map(|m| module_json(m, &registered))
        .collect();
    Json(mods)
}

async fn get_mod(
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> impl IntoResponse {
    match state.catalog.get(&name) {
        Some(module) => {
            let registered = state.chain.registered_names().await;
            Json(module_json(&module, &registered)).into_response()
        }
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "module not found", "name": name })),
        )
            .into_response(),
    }
}

/// The on-chain registry view — which modules the chain module reports as
/// registered, and whether chain was reachable at all.
async fn registry(State(state): State<AppState>) -> impl IntoResponse {
    Json(state.chain.registry().await)
}

/// Dependency graph: every module as a node, every declared dep as an edge.
/// Edges are kept only when their target is a known module so the graph stays
/// connected and renderable. Nodes carry enough to render without a join.
async fn graph(State(state): State<AppState>) -> impl IntoResponse {
    let modules = state.catalog.modules();
    let registered = state.chain.registered_names().await;
    let known: std::collections::HashSet<String> =
        modules.iter().map(|m| m.name.clone()).collect();

    let nodes: Vec<serde_json::Value> = modules
        .iter()
        .map(|m| {
            serde_json::json!({
                "name": m.name,
                "icon": m.icon,
                "color": m.color,
                "dep_count": m.deps.iter().filter(|d| known.contains(*d)).count(),
                "registered": registered.contains(&m.name.to_lowercase()),
            })
        })
        .collect();

    let mut edges: Vec<serde_json::Value> = Vec::new();
    for m in &modules {
        for dep in &m.deps {
            if known.contains(dep) {
                edges.push(serde_json::json!({ "from": m.name, "to": dep }));
            }
        }
    }

    Json(serde_json::json!({
        "nodes": nodes,
        "edges": edges,
        "chain_available": state.chain.registry().await.available,
    }))
}

/// Source tree for a module — backs the explorer's file browser.
async fn mod_tree(
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> impl IntoResponse {
    match state.catalog.tree(&name) {
        Some(tree) => Json(serde_json::json!({ "name": name, "tree": tree })).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "module not found", "name": name })),
        )
            .into_response(),
    }
}

#[derive(serde::Deserialize)]
struct FileParams {
    path: String,
}

/// One file's contents — sandboxed to the module dir by the catalog.
async fn mod_file(
    State(state): State<AppState>,
    Path(name): Path<String>,
    Query(params): Query<FileParams>,
) -> impl IntoResponse {
    use crate::catalog::FileError;
    match state.catalog.read_file(&name, &params.path) {
        Ok(file) => Json(file).into_response(),
        Err(err) => {
            let (code, msg) = match err {
                FileError::NotFound => (StatusCode::NOT_FOUND, "file not found"),
                FileError::Forbidden => (StatusCode::FORBIDDEN, "path is outside the module"),
                FileError::TooLarge => (StatusCode::PAYLOAD_TOO_LARGE, "file too large to preview"),
                FileError::Binary => {
                    (StatusCode::UNSUPPORTED_MEDIA_TYPE, "binary file — not previewable")
                }
                FileError::Io => (StatusCode::INTERNAL_SERVER_ERROR, "could not read file"),
            };
            (code, Json(serde_json::json!({ "error": msg, "path": params.path }))).into_response()
        }
    }
}

async fn stats(State(state): State<AppState>) -> impl IntoResponse {
    Json(state.catalog.stats())
}

#[derive(serde::Deserialize)]
struct SearchParams {
    #[serde(default)]
    q: String,
}

async fn search(
    State(state): State<AppState>,
    Query(params): Query<SearchParams>,
) -> impl IntoResponse {
    let registered = state.chain.registered_names().await;
    let mods: Vec<_> = state
        .catalog
        .search(&params.q)
        .iter()
        .map(|m| module_json(m, &registered))
        .collect();
    Json(mods)
}
