//! model-api — Rust port of the BYOK chat gateway.
//!
//! Endpoints:
//!   GET  /health
//!   GET  /providers
//!   GET  /models?provider=ID    (requires X-API-Key)
//!   POST /chat                  (requires X-API-Key; SSE when stream=true)
//!   POST /gate/verify           (verifies a ModelGate EIP-191 signature)
//!
//! Strict BYOK: keys come from the X-API-Key (or Authorization: Bearer …)
//! header and are forwarded to the upstream provider in-process only. They
//! are never stored, logged, or echoed.

use axum::{
    extract::State,
    http::{header, HeaderMap, StatusCode},
    routing::{get, post},
    Json, Router,
};
use ethers::types::Address;
use serde::{Deserialize, Serialize};
use std::{collections::HashMap, env, net::SocketAddr, sync::Arc};
use tower_http::{cors::CorsLayer, trace::TraceLayer};

mod routes;

#[derive(Clone, Debug, Deserialize)]
pub struct ProviderCfg {
    pub label: String,
    pub url: String,
    pub default_model: String,
    pub key_hint: String,
    pub docs: String,
    /// "x-api-key" for providers (Anthropic) whose /models rejects Bearer.
    #[serde(default)]
    pub models_auth: Option<String>,
    #[serde(default)]
    pub models_headers: HashMap<String, String>,
}

pub struct AppState {
    pub providers: HashMap<String, ProviderCfg>,
    pub http: reqwest::Client,
    pub gate_address: Option<Address>,
    pub gate_chain_id: u64,
}

/// Same registry the Python gateway reads — ../providers.json, baked in at build time.
fn providers_registry() -> HashMap<String, ProviderCfg> {
    #[derive(Deserialize)]
    struct Entry { id: String, #[serde(flatten)] cfg: ProviderCfg }
    #[derive(Deserialize)]
    struct Registry { providers: Vec<Entry> }
    let reg: Registry = serde_json::from_str(include_str!("../../providers.json"))
        .expect("providers.json is valid");
    reg.providers.into_iter().map(|e| (e.id, e.cfg)).collect()
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "model_api=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = env::var("MODEL_API_PORT").ok().and_then(|s| s.parse().ok()).unwrap_or(50110);
    let gate_address = env::var("MODEL_GATE_ADDRESS").ok().and_then(|s| s.parse::<Address>().ok());
    let gate_chain_id: u64 = env::var("MODEL_GATE_CHAIN_ID").ok().and_then(|s| s.parse().ok()).unwrap_or(84532);

    let state = Arc::new(AppState {
        providers: providers_registry(),
        http: reqwest::Client::builder().user_agent("model-api/0.1").build()?,
        gate_address,
        gate_chain_id,
    });

    let app = Router::new()
        .route("/health", get(health))
        .route("/providers", get(routes::providers::list))
        .route("/models", get(routes::models::list))
        .route("/chat", post(routes::chat::chat))
        .route("/gate/verify", post(routes::gate::verify))
        .layer(TraceLayer::new_for_http())
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr: SocketAddr = format!("0.0.0.0:{port}").parse()?;
    tracing::info!("model-api (Rust) listening on {addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

#[derive(Serialize)]
struct Health { ok: bool, providers: Vec<String>, gate: Option<String>, chain_id: u64 }

async fn health(State(s): State<Arc<AppState>>) -> Json<Health> {
    Json(Health {
        ok: true,
        providers: s.providers.keys().map(|k| k.to_string()).collect(),
        gate: s.gate_address.map(|a| format!("{a:#x}")),
        chain_id: s.gate_chain_id,
    })
}

// ── shared helpers (used across routes) ──────────────────────────────

pub fn extract_key(headers: &HeaderMap) -> Result<String, (StatusCode, String)> {
    if let Some(v) = headers.get("x-api-key").and_then(|v| v.to_str().ok()) {
        if !v.is_empty() { return Ok(v.to_string()); }
    }
    if let Some(v) = headers.get(header::AUTHORIZATION).and_then(|v| v.to_str().ok()) {
        if let Some(rest) = v.strip_prefix("Bearer ").or_else(|| v.strip_prefix("bearer ")) {
            if !rest.is_empty() { return Ok(rest.trim().to_string()); }
        }
    }
    Err((StatusCode::UNAUTHORIZED, "BYOK: supply X-API-Key or Authorization: Bearer …".into()))
}
