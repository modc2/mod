use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Json};
use axum::routing::{get, post};
use axum::Router;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::auth::{verify_header, Wallet};
use crate::store::Connection;
use crate::{ssh, AppState};

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/health", get(health))
        .route("/info", get(info))
        .route("/challenge", get(challenge))
        .route("/connections", get(list_conns))
        .route("/connections/add", post(add_conn))
        .route("/connections/:id", get(get_conn).delete(delete_conn))
        .route("/connections/:id/test", post(test_conn))
        .route("/connections/:id/exec", post(exec_conn))
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok", "name": "sshville"}))
}

async fn info(State(state): State<AppState>) -> Json<Value> {
    let (wallets, conns) = state.store.counts();
    Json(json!({
        "name": "sshville",
        "challenge": state.challenge,
        "store_path": state.store.store_path().to_string_lossy(),
        "wallets": wallets,
        "connections": conns,
        "schemes": ["eth", "sub"],
    }))
}

async fn challenge(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "challenge": state.challenge,
        "schemes": [
            {"name": "eth", "label": "MetaMask / Ethereum personal_sign"},
            {"name": "sub", "label": "Polkadot.js / SubWallet sr25519 (Bittensor)"},
        ],
        "header": "X-Mod-Auth",
        "format": {
            "eth": "eth <0x-prefixed 65-byte signature>",
            "sub": "sub <hex pubkey 32B> <hex sig 64B> [ss58_prefix=42]",
        }
    }))
}

// ── auth helper ───────────────────────────────────────────────────────────

fn auth(headers: &HeaderMap, state: &AppState) -> Result<Wallet, ApiError> {
    let raw = headers
        .get("x-mod-auth")
        .or_else(|| headers.get("X-Mod-Auth"))
        .ok_or_else(|| ApiError::unauthorized("missing X-Mod-Auth"))?
        .to_str()
        .map_err(|_| ApiError::unauthorized("non-ascii X-Mod-Auth"))?;
    verify_header(raw, &state.challenge)
        .map_err(|e| ApiError::unauthorized(format!("auth failed: {e}")))
}

// ── handlers ─────────────────────────────────────────────────────────────

async fn list_conns(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    let items = state.store.list(&wallet.id);
    Ok(Json(json!({
        "wallet": wallet.id,
        "address": wallet.address,
        "connections": items,
    })))
}

async fn get_conn(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Connection>, ApiError> {
    let wallet = auth(&headers, &state)?;
    state
        .store
        .get(&wallet.id, &id)
        .map(Json)
        .ok_or_else(|| ApiError::not_found(format!("connection {id:?} not found")))
}

#[derive(Debug, Deserialize)]
struct AddRequest {
    name: String,
    host: String,
    user: String,
    ciphertext: String,
    iv: String,
    #[serde(default = "default_port")]
    port: u16,
    #[serde(default = "default_auth_type")]
    auth_type: String,
    #[serde(default)]
    id: Option<String>,
}

fn default_port() -> u16 {
    22
}
fn default_auth_type() -> String {
    "password".to_string()
}

async fn add_conn(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<AddRequest>,
) -> Result<Json<Connection>, ApiError> {
    let wallet = auth(&headers, &state)?;
    if !matches!(body.auth_type.as_str(), "password" | "key") {
        return Err(ApiError::bad_request("auth_type must be password or key"));
    }
    let id = body
        .id
        .unwrap_or_else(|| format!("{}@{}:{}", body.user, body.host, body.port));
    let conn = Connection {
        id,
        name: body.name,
        host: body.host,
        port: body.port,
        user: body.user,
        auth_type: body.auth_type,
        ciphertext: body.ciphertext,
        iv: body.iv,
        created_at: 0, // filled by store
        updated_at: 0,
    };
    let saved = state
        .store
        .upsert(&wallet.id, conn)
        .map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(saved))
}

async fn delete_conn(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    let removed = state
        .store
        .remove(&wallet.id, &id)
        .map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(json!({"ok": removed, "wallet": wallet.id, "id": id})))
}

#[derive(Debug, Deserialize)]
struct ExecRequest {
    secret: String,
    #[serde(default)]
    command: Option<String>,
}

async fn test_conn(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(body): Json<ExecRequest>,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    let conn = state
        .store
        .get(&wallet.id, &id)
        .ok_or_else(|| ApiError::not_found(format!("connection {id:?} not found")))?;
    let out = ssh::run(&conn, &body.secret, "whoami && uname -a")
        .await
        .map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(serde_json::to_value(out).unwrap()))
}

async fn exec_conn(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
    Json(body): Json<ExecRequest>,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    let command = body
        .command
        .ok_or_else(|| ApiError::bad_request("command required"))?;
    let conn = state
        .store
        .get(&wallet.id, &id)
        .ok_or_else(|| ApiError::not_found(format!("connection {id:?} not found")))?;
    let out = ssh::run(&conn, &body.secret, &command)
        .await
        .map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(serde_json::to_value(out).unwrap()))
}

// ── error type ────────────────────────────────────────────────────────────

pub struct ApiError {
    status: StatusCode,
    detail: String,
}

impl ApiError {
    fn unauthorized(d: impl Into<String>) -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            detail: d.into(),
        }
    }
    fn not_found(d: impl Into<String>) -> Self {
        Self {
            status: StatusCode::NOT_FOUND,
            detail: d.into(),
        }
    }
    fn bad_request(d: impl Into<String>) -> Self {
        Self {
            status: StatusCode::BAD_REQUEST,
            detail: d.into(),
        }
    }
    fn internal(d: impl Into<String>) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            detail: d.into(),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> axum::response::Response {
        (
            self.status,
            Json(json!({ "error": self.detail, "status": self.status.as_u16() })),
        )
            .into_response()
    }
}
