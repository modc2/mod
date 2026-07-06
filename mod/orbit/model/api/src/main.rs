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
use serde::Serialize;
use std::{collections::HashMap, env, net::SocketAddr, sync::Arc};
use tower_http::{cors::CorsLayer, trace::TraceLayer};

mod routes;

#[derive(Clone, Debug)]
pub struct ProviderCfg {
    pub label: &'static str,
    pub url: &'static str,
    pub default_model: &'static str,
    pub key_hint: &'static str,
    pub docs: &'static str,
}

pub struct AppState {
    pub providers: HashMap<&'static str, ProviderCfg>,
    pub http: reqwest::Client,
    pub gate_address: Option<Address>,
    pub gate_chain_id: u64,
}

fn providers_registry() -> HashMap<&'static str, ProviderCfg> {
    let mut p = HashMap::new();
    p.insert("openrouter", ProviderCfg {
        label: "OpenRouter",
        url: "https://openrouter.ai/api/v1",
        default_model: "anthropic/claude-opus-4",
        key_hint: "sk-or-…",
        docs: "https://openrouter.ai/keys",
    });
    p.insert("chutes", ProviderCfg {
        label: "Chutes",
        url: "https://llm.chutes.ai/v1",
        default_model: "deepseek-ai/DeepSeek-V3",
        key_hint: "cpk_…",
        docs: "https://chutes.ai",
    });
    p.insert("targon", ProviderCfg {
        label: "Targon",
        url: "https://api.targon.com/v1",
        default_model: "deepseek-ai/DeepSeek-V3",
        key_hint: "sn4_…",
        docs: "https://targon.com",
    });
    p.insert("venice", ProviderCfg {
        label: "Venice",
        url: "https://api.venice.ai/api/v1",
        default_model: "llama-3.3-70b",
        key_hint: "venice key",
        docs: "https://venice.ai",
    });
    p
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
