//! HTTP surface for the Venice gateway.
//!
//!   GET    /health          liveness
//!   GET    /models          public Venice model list
//!   GET    /me              caller identity + whether they have a BYOK key /
//!                           whether the paid (backend-funded) path is available
//!   POST   /key   {key}     store the caller's own Venice key (BYOK)
//!   DELETE /key             forget the caller's BYOK key
//!   POST   /chat  {...}     chat completion. Uses the caller's BYOK key if
//!                           present; otherwise requires an x402 payment and
//!                           funds the call with the backend key.
//!
//! Identity comes from a mod protocol-auth bearer token (wallet-signed).

use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::sse::{KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio_stream::wrappers::ReceiverStream;

use crate::keystore::KeyStore;
use crate::media::MediaStore;
use crate::x402::X402Config;
use crate::{agent, auth, venice};

#[derive(Clone)]
pub struct AppState {
    pub http: reqwest::Client,
    pub keys: Arc<KeyStore>,
    pub media: Arc<MediaStore>,
    pub x402: Arc<X402Config>,
    /// Our own Venice key, used to fund paid (non-BYOK) requests. None ⇒ the
    /// paid path is unavailable regardless of x402 config.
    pub backend_key: Option<String>,
    /// Max token age in seconds.
    pub max_age: u64,
}

impl AppState {
    fn paid_available(&self) -> bool {
        self.x402.enabled && self.backend_key.is_some()
    }
}

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health", get(health))
        .route("/", get(root))
        .route("/models", get(models))
        .route("/me", get(me))
        .route("/key", post(set_key))
        .route("/key", delete(rm_key))
        .route("/chat", post(chat))
        .route("/agent", post(run_agent))
        .route("/media", post(upload_media))
        .route("/media/:id", get(get_media))
}

// ── identity helper ────────────────────────────────────────────

/// Pull the bearer token, verify it, return the lowercase signer address.
/// On failure returns a ready-to-send 401 response.
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

fn unauthorized(msg: &str) -> Response {
    (StatusCode::UNAUTHORIZED, Json(json!({ "error": msg }))).into_response()
}

// ── handlers ───────────────────────────────────────────────────

async fn health() -> impl IntoResponse {
    Json(json!({ "ok": true, "service": "venice", "time": chrono::Utc::now().timestamp() }))
}

async fn root(State(state): State<AppState>) -> impl IntoResponse {
    Json(json!({
        "name": "venice",
        "description": "Venice AI gateway — BYOK or pay-per-request (x402)",
        "auth": "mod-protocol",
        "paid_available": state.paid_available(),
        "endpoints": ["/health", "/models", "/me", "/key", "/chat"],
    }))
}

async fn models(State(state): State<AppState>) -> Response {
    venice::list_models(&state.http).await
}

async fn me(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    Json(json!({
        "address": addr,
        "has_key": state.keys.has(&addr),
        "paid_available": state.paid_available(),
        "price": state.x402.price_display,
        "currency": "USDC",
        "network": state.x402.network,
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
    Json(body): Json<SetKeyBody>,
) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    let key = body.key.trim();
    if key.is_empty() {
        return (StatusCode::BAD_REQUEST, Json(json!({ "error": "key is empty" }))).into_response();
    }
    match state.keys.set(&addr, key) {
        Ok(()) => Json(json!({ "ok": true, "has_key": true })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("store key: {e}") })),
        )
            .into_response(),
    }
}

async fn rm_key(State(state): State<AppState>, headers: HeaderMap) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    match state.keys.delete(&addr) {
        Ok(removed) => Json(json!({ "ok": true, "removed": removed, "has_key": false })).into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("delete key: {e}") })),
        )
            .into_response(),
    }
}

/// Resolve which Venice key serves this request:
///   - the caller's BYOK key (free to us), if on file; or
///   - the backend key, after a successful x402 payment (returns the receipt).
/// On the no-key/no-payment paths returns the response to send back directly
/// (a 402 challenge, payment-rejected, or paid-path-unavailable error).
async fn resolve_key(
    state: &AppState,
    addr: &str,
    headers: &HeaderMap,
) -> Result<(String, Option<String>), Response> {
    if let Ok(Some(key)) = state.keys.get(addr) {
        return Ok((key, None));
    }
    if !state.paid_available() {
        return Err((
            StatusCode::PAYMENT_REQUIRED,
            Json(json!({
                "error": "no Venice key on file. Add your own key (BYOK) or the paid path is not configured.",
                "paid_available": false,
            })),
        )
            .into_response());
    }
    let x_payment = headers.get("X-PAYMENT").and_then(|v| v.to_str().ok());
    let x_payment = match x_payment {
        Some(p) if !p.is_empty() => p,
        _ => return Err(state.x402.challenge()),
    };
    match state.x402.verify_and_settle(&state.http, x_payment).await {
        Ok(receipt) => Ok((state.backend_key.clone().unwrap(), Some(receipt))),
        Err(e) => Err((
            StatusCode::PAYMENT_REQUIRED,
            Json(json!({ "error": format!("payment rejected: {e}"), "accepts_retry": true })),
        )
            .into_response()),
    }
}

async fn chat(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    let (key, receipt) = match resolve_key(&state, &addr, &headers).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    let mut resp = venice::chat(&state.http, &key, body).await;
    if let Some(receipt) = receipt {
        if let Ok(hv) = HeaderValue::from_str(&receipt) {
            resp.headers_mut().insert("X-PAYMENT-RESPONSE", hv);
        }
    }
    resp
}

#[derive(Deserialize)]
struct AgentBody {
    model: String,
    #[serde(default)]
    messages: Vec<Value>,
    #[serde(default)]
    attachments: Vec<String>,
}

/// The unified multimodal turn — streams status/media/message events as SSE.
/// Key resolution (BYOK or x402) happens up front; one resolved key funds
/// every Venice call (chat + media tools) made during the turn.
async fn run_agent(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<AgentBody>,
) -> Response {
    let addr = match require_addr(&state, &headers) {
        Ok(a) => a,
        Err(r) => return r,
    };
    let (key, _receipt) = match resolve_key(&state, &addr, &headers).await {
        Ok(x) => x,
        Err(r) => return r,
    };

    let (tx, rx) = tokio::sync::mpsc::channel(64);
    let http = state.http.clone();
    let store = (*state.media).clone();
    let model = if body.model.trim().is_empty() {
        std::env::var("VENICE_AGENT_MODEL").unwrap_or_else(|_| "venice-uncensored".into())
    } else {
        body.model
    };
    tokio::spawn(async move {
        agent::run(http, key, store, model, body.messages, body.attachments, tx).await;
    });

    Sse::new(ReceiverStream::new(rx))
        .keep_alive(KeepAlive::default())
        .into_response()
}

#[derive(Deserialize)]
struct UploadBody {
    /// base64 (optionally a full `data:...;base64,` URL).
    data: String,
    #[serde(default)]
    ext: Option<String>,
}

/// Store a user-attached image so the agent's tools can reference it by id.
async fn upload_media(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<UploadBody>,
) -> Response {
    if let Err(r) = require_addr(&state, &headers) {
        return r;
    }
    use base64::Engine;
    // Accept a raw base64 string or a data URL; derive ext from the data URL
    // mime when present, else from the `ext` field, else default to png.
    let (b64, ext) = if let Some(rest) = body.data.strip_prefix("data:") {
        let mime = rest.split(';').next().unwrap_or("");
        let ext = mime.rsplit('/').next().unwrap_or("png").to_string();
        let payload = rest.splitn(2, ',').nth(1).unwrap_or("").to_string();
        (payload, body.ext.unwrap_or(ext))
    } else {
        (body.data.clone(), body.ext.unwrap_or_else(|| "png".into()))
    };
    let bytes = match base64::engine::general_purpose::STANDARD.decode(b64.trim()) {
        Ok(b) => b,
        Err(e) => {
            return (StatusCode::BAD_REQUEST, Json(json!({ "error": format!("bad base64: {e}") })))
                .into_response()
        }
    };
    match state.media.save(&bytes, &ext) {
        Ok(id) => Json(json!({
            "media_id": id,
            "url": format!("/media/{id}"),
            "kind": crate::media::kind_for(&id),
        }))
        .into_response(),
        Err(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({ "error": format!("save: {e}") })),
        )
            .into_response(),
    }
}

/// Serve a stored media item. Public by capability URL (unguessable id) so it
/// can be used directly in <img>/<video> tags.
async fn get_media(State(state): State<AppState>, Path(id): Path<String>) -> Response {
    match state.media.load(&id) {
        Some((bytes, mime)) => Response::builder()
            .status(StatusCode::OK)
            .header(header::CONTENT_TYPE, mime)
            .header(header::CACHE_CONTROL, "public, max-age=31536000, immutable")
            .body(Body::from(bytes))
            .unwrap_or_else(|_| StatusCode::INTERNAL_SERVER_ERROR.into_response()),
        None => (StatusCode::NOT_FOUND, "not found").into_response(),
    }
}
