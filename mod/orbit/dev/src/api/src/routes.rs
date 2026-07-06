//! HTTP surface for the dev gateway.
//!
//!   GET    /health                  liveness
//!   GET    /                         service banner
//!   GET    /providers                list configured providers (public, no secrets)
//!   POST   /providers                add/replace a provider           (owner)
//!   DELETE /providers/:name          remove a non-builtin provider    (owner)
//!   GET    /providers/:name/models   proxy the provider's model list  (bearer)
//!   GET    /me                       caller identity + per-provider key status (bearer)
//!   POST   /key/:provider  {key}     store the caller's BYOK key      (bearer)
//!   DELETE /key/:provider            forget the caller's BYOK key     (bearer)
//!   POST   /chat/:provider {...}     OpenAI-style chat completion     (bearer)
//!
//! Identity comes from a mod protocol-auth bearer token (wallet-signed).

use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use parking_lot::RwLock;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::keystore::KeyStore;
use crate::providers::{Provider, Registry};
use crate::{auth, upstream};

#[derive(Clone)]
pub struct AppState {
    pub http: reqwest::Client,
    pub keys: Arc<KeyStore>,
    pub providers: Arc<RwLock<Registry>>,
    /// Lowercase owner address — gates provider add/remove. Empty ⇒ no owner set,
    /// so owner-only routes are closed to everyone.
    pub owner: String,
    /// Max bearer-token age in seconds.
    pub max_age: u64,
}

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health", get(health))
        .route("/", get(root))
        .route("/providers", get(list_providers).post(add_provider))
        .route("/providers/:name", delete(remove_provider))
        .route("/providers/:name/models", get(provider_models))
        .route("/me", get(me))
        .route("/key/:provider", post(set_key).delete(rm_key))
        .route("/chat/:provider", post(chat))
}

// ── identity helpers ───────────────────────────────────────────

/// Verify the bearer token; return the lowercase signer address or a 401.
fn require_addr(state: &AppState, headers: &HeaderMap) -> Result<String, Response> {
    let auth_header = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let token = auth_header.strip_prefix("Bearer ").map(str::trim).unwrap_or("");
    if token.is_empty() {
        return Err(unauthorized("missing bearer token"));
    }
    auth::verify_token(token, state.max_age)
        .map_err(|e| unauthorized(&format!("invalid token: {e}")))
}

/// Verify the bearer token AND require the signer to be the module owner.
fn require_owner(state: &AppState, headers: &HeaderMap) -> Result<String, Response> {
    let addr = require_addr(state, headers)?;
    if state.owner.is_empty() {
        return Err(forbidden("no owner configured — owner-only route is closed"));
    }
    if !addr.eq_ignore_ascii_case(&state.owner) {
        return Err(forbidden("owner only"));
    }
    Ok(addr)
}

fn unauthorized(msg: &str) -> Response {
    (StatusCode::UNAUTHORIZED, Json(json!({ "error": msg }))).into_response()
}

fn forbidden(msg: &str) -> Response {
    (StatusCode::FORBIDDEN, Json(json!({ "error": msg }))).into_response()
}

fn not_found(msg: String) -> Response {
    (StatusCode::NOT_FOUND, Json(json!({ "error": msg }))).into_response()
}

// ── handlers ───────────────────────────────────────────────────

async fn health() -> impl IntoResponse {
    Json(json!({ "ok": true, "service": "dev", "time": chrono::Utc::now().timestamp() }))
}

async fn root(State(state): State<AppState>) -> impl IntoResponse {
    let names = state.providers.read().names();
    Json(json!({
        "name": "dev",
        "description": "Modular OpenAI-standard multi-provider LLM gateway (BYOK or backend key). Wallet (mod protocol) auth.",
        "auth": "mod-protocol",
        "providers": names,
        "endpoints": ["/health", "/providers", "/providers/:name/models", "/me", "/key/:provider", "/chat/:provider"],
    }))
}

async fn list_providers(State(state): State<AppState>) -> impl IntoResponse {
    let list: Vec<Value> = state.providers.read().list().iter().map(|p| p.public()).collect();
    Json(json!({ "providers": list }))
}

async fn add_provider(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(p): Json<Provider>,
) -> Response {
    if let Err(r) = require_owner(&state, &headers) {
        return r;
    }
    match state.providers.write().add(p) {
        Ok(stored) => Json(json!({ "ok": true, "provider": stored.public() })).into_response(),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

async fn remove_provider(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(name): Path<String>,
) -> Response {
    if let Err(r) = require_owner(&state, &headers) {
        return r;
    }
    match state.providers.write().remove(&name) {
        Ok(true) => Json(json!({ "ok": true, "removed": name })).into_response(),
        Ok(false) => not_found(format!("unknown provider: {name}")),
        Err(e) => (StatusCode::BAD_REQUEST, Json(json!({ "error": e }))).into_response(),
    }
}

async fn provider_models(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(name): Path<String>,
) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    let provider = match state.providers.read().get(&name) {
        Some(p) => p,
        None => return not_found(format!("unknown provider: {name}")),
    };
    // Use whatever key we can — caller BYOK first, else backend — so providers
    // that require auth for /models still work. Listing without a key is fine
    // for the public ones (venice/openrouter).
    let key = state
        .keys
        .get(&name, &addr)
        .ok()
        .flatten()
        .or_else(|| provider.backend_key());
    upstream::list_models(&state.http, &provider, key.as_deref()).await
}

async fn me(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    let providers = state.providers.read();
    let status: Vec<Value> = providers
        .list()
        .iter()
        .map(|p| {
            json!({
                "name": p.name,
                "has_key": state.keys.has(&p.name, &addr),
                "has_backend": p.has_backend(),
            })
        })
        .collect();
    Json(json!({
        "address": addr,
        "is_owner": !state.owner.is_empty() && addr.eq_ignore_ascii_case(&state.owner),
        "providers": status,
    }))
    .into_response()
}

#[derive(Deserialize)]
struct SetKeyBody {
    key: String,
}

async fn set_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(provider): Path<String>,
    Json(body): Json<SetKeyBody>,
) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    if state.providers.read().get(&provider).is_none() {
        return not_found(format!("unknown provider: {provider}"));
    }
    let key = body.key.trim();
    if key.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "key is empty" }))).into_response();
    }
    match state.keys.set(&provider, &addr, key) {
        Ok(()) => Json(json!({ "ok": true, "provider": provider, "has_key": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("store key: {e}") })),
        )
            .into_response(),
    }
}

async fn rm_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(provider): Path<String>,
) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    match state.keys.delete(&provider, &addr) {
        Ok(removed) => {
            Json(json!({ "ok": true, "provider": provider, "removed": removed, "has_key": false }))
                .into_response()
        }
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("delete key: {e}") })),
        )
            .into_response(),
    }
}

/// Resolve which key serves this request for `provider`:
///   1. the caller's BYOK key (free to us), if on file; else
///   2. the provider's backend key from the environment; else
///   3. a 402-style "no key" error response.
fn resolve_key(state: &AppState, provider: &Provider, addr: &str) -> Result<String, Response> {
    if provider.byok {
        if let Ok(Some(key)) = state.keys.get(&provider.name, addr) {
            return Ok(key);
        }
    }
    if let Some(key) = provider.backend_key() {
        return Ok(key);
    }
    Err((
        StatusCode::PAYMENT_REQUIRED,
        Json(json!({
            "error": format!(
                "no key for provider '{}'. Add your own key (BYOK) or set the {} backend key.",
                provider.name, provider.backend_env
            ),
            "provider": provider.name,
            "byok": provider.byok,
            "has_backend": provider.has_backend(),
        })),
    )
        .into_response())
}

async fn chat(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(provider_name): Path<String>,
    Json(mut body): Json<Value>,
) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    let provider = match state.providers.read().get(&provider_name) {
        Some(p) => p,
        None => return not_found(format!("unknown provider: {provider_name}")),
    };
    let key = match resolve_key(&state, &provider, &addr) {
        Ok(k) => k,
        Err(r) => return r,
    };
    // Default the model to the provider's default when the client omits it.
    if body.get("model").and_then(|v| v.as_str()).unwrap_or("").is_empty()
        && !provider.default_model.is_empty()
    {
        if let Some(obj) = body.as_object_mut() {
            obj.insert("model".into(), Value::String(provider.default_model.clone()));
        }
    }
    upstream::chat(&state.http, &provider, &key, body).await
}
