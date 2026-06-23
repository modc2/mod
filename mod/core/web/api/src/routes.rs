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

#[derive(Clone)]
pub struct AppState {
    pub catalog: Arc<Catalog>,
    pub version: &'static str,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/", get(info))
        .route("/info", get(info))
        .route("/health", get(health))
        .route("/mods", get(list_mods))
        .route("/mods/:name", get(get_mod))
        .route("/stats", get(stats))
        .route("/search", get(search))
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

async fn list_mods(State(state): State<AppState>) -> impl IntoResponse {
    Json(state.catalog.modules())
}

async fn get_mod(
    State(state): State<AppState>,
    Path(name): Path<String>,
) -> impl IntoResponse {
    match state.catalog.get(&name) {
        Some(module) => Json(module).into_response(),
        None => (
            StatusCode::NOT_FOUND,
            Json(serde_json::json!({ "error": "module not found", "name": name })),
        )
            .into_response(),
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
    Json(state.catalog.search(&params.q))
}
