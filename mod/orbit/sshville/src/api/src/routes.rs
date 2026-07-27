use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Json};
use axum::routing::{get, post};
use axum::Router;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::auth::{verify_header, Wallet};
use crate::store::{Connection, KeyEntry, VaultMeta};
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
        .route("/vault", get(get_vault).post(set_vault))
        .route("/keys", get(list_keys))
        .route("/keys/add", post(add_key))
        .route("/keys/generate", post(generate_key))
        .route("/keys/:id", get(get_key).delete(delete_key))
        .route("/qr", post(qr_svg))
}

async fn health() -> Json<Value> {
    Json(json!({"status": "ok", "name": "sshville"}))
}

async fn info(State(state): State<AppState>) -> Json<Value> {
    let (wallets, conns) = state.store.counts();
    let (vaults, keys) = state.keys.counts();
    Json(json!({
        "name": "sshville",
        "challenge": state.challenge,
        "store_path": state.store.store_path().to_string_lossy(),
        "wallets": wallets,
        "connections": conns,
        "vaults": vaults,
        "keys": keys,
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
    #[serde(default)]
    ciphertext: String,
    #[serde(default)]
    iv: String,
    #[serde(default)]
    key_id: Option<String>,
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
    match &body.key_id {
        Some(kid) => {
            if state.keys.get_key(&wallet.id, kid).is_none() {
                return Err(ApiError::bad_request(format!(
                    "key_id {kid:?} not found in your vault"
                )));
            }
        }
        None => {
            if body.ciphertext.is_empty() || body.iv.is_empty() {
                return Err(ApiError::bad_request(
                    "either key_id or ciphertext+iv required",
                ));
            }
        }
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
        key_id: body.key_id,
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

// ── key vault ─────────────────────────────────────────────────────────────
// The server is a dumb ciphertext shelf: the AES key is derived in the
// browser from a vault passphrase that is never transmitted. These handlers
// only enforce wallet ownership and rotation safety.

async fn get_vault(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    Ok(Json(json!({
        "wallet": wallet.id,
        "vault": state.keys.vault(&wallet.id),
        "keys": state.keys.key_count(&wallet.id),
    })))
}

#[derive(Debug, Deserialize)]
struct SetVaultRequest {
    salt: String,
    verifier_ct: String,
    verifier_iv: String,
    #[serde(default = "crate::store::default_kdf_iters")]
    kdf_iters: u32,
    /// On passphrase rotation: the full key set re-encrypted under the new
    /// passphrase, applied atomically with the new vault metadata.
    #[serde(default)]
    keys: Option<Vec<RotatedKey>>,
    /// Must be true to replace an existing vault without re-encrypted keys
    /// (this orphans any stored ciphertext — only sane for an empty vault).
    #[serde(default)]
    force: bool,
}

#[derive(Debug, Deserialize)]
struct RotatedKey {
    id: String,
    name: String,
    kind: String,
    ciphertext: String,
    iv: String,
    #[serde(default)]
    public_key: Option<String>,
    #[serde(default)]
    fingerprint: Option<String>,
}

async fn set_vault(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<SetVaultRequest>,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    let existing_keys = state.keys.key_count(&wallet.id);
    let has_vault = state.keys.vault(&wallet.id).is_some();
    if has_vault && body.keys.is_none() && existing_keys > 0 && !body.force {
        return Err(ApiError::bad_request(
            "vault already exists with keys — rotation must include the re-encrypted `keys` set (or pass force=true to abandon them)",
        ));
    }
    let vault = VaultMeta {
        salt: body.salt,
        verifier_ct: body.verifier_ct,
        verifier_iv: body.verifier_iv,
        kdf_iters: body.kdf_iters,
        updated_at: 0, // filled by store
    };
    let keys = body.keys.map(|ks| {
        ks.into_iter()
            .map(|k| KeyEntry {
                id: k.id,
                name: k.name,
                kind: k.kind,
                ciphertext: k.ciphertext,
                iv: k.iv,
                public_key: k.public_key,
                fingerprint: k.fingerprint,
                created_at: 0,
                updated_at: 0,
            })
            .collect()
    });
    let saved = state
        .keys
        .set_vault(&wallet.id, vault, keys)
        .map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(json!({"ok": true, "wallet": wallet.id, "vault": saved})))
}

async fn list_keys(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    Ok(Json(json!({
        "wallet": wallet.id,
        "vault": state.keys.vault(&wallet.id),
        "keys": state.keys.list_keys(&wallet.id),
    })))
}

#[derive(Debug, Deserialize)]
struct AddKeyRequest {
    name: String,
    ciphertext: String,
    iv: String,
    #[serde(default = "default_auth_type")]
    kind: String,
    #[serde(default)]
    id: Option<String>,
    #[serde(default)]
    public_key: Option<String>,
    #[serde(default)]
    fingerprint: Option<String>,
}

async fn add_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<AddKeyRequest>,
) -> Result<Json<KeyEntry>, ApiError> {
    let wallet = auth(&headers, &state)?;
    if !matches!(body.kind.as_str(), "password" | "key") {
        return Err(ApiError::bad_request("kind must be password or key"));
    }
    if state.keys.vault(&wallet.id).is_none() {
        return Err(ApiError::bad_request(
            "no vault yet — POST /vault (salt + verifier) before adding keys",
        ));
    }
    if body.ciphertext.is_empty() || body.iv.is_empty() {
        return Err(ApiError::bad_request("ciphertext and iv required"));
    }
    let id = body.id.unwrap_or_else(|| slug(&body.name));
    let key = KeyEntry {
        id,
        name: body.name,
        kind: body.kind,
        ciphertext: body.ciphertext,
        iv: body.iv,
        public_key: body.public_key,
        fingerprint: body.fingerprint,
        created_at: 0, // filled by store
        updated_at: 0,
    };
    let saved = state
        .keys
        .upsert_key(&wallet.id, key)
        .map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(saved))
}

async fn get_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<KeyEntry>, ApiError> {
    let wallet = auth(&headers, &state)?;
    state
        .keys
        .get_key(&wallet.id, &id)
        .map(Json)
        .ok_or_else(|| ApiError::not_found(format!("key {id:?} not found")))
}

async fn delete_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    let in_use: Vec<String> = state
        .store
        .list(&wallet.id)
        .into_iter()
        .filter(|c| c.key_id.as_deref() == Some(id.as_str()))
        .map(|c| c.id)
        .collect();
    if !in_use.is_empty() {
        return Err(ApiError::bad_request(format!(
            "key {id:?} is referenced by connections {in_use:?} — delete or repoint them first"
        )));
    }
    let removed = state
        .keys
        .remove_key(&wallet.id, &id)
        .map_err(|e| ApiError::internal(e.to_string()))?;
    Ok(Json(json!({"ok": removed, "wallet": wallet.id, "id": id})))
}

// ── backend keygen ────────────────────────────────────────────────────────
// The keypair is minted in memory and returned exactly once — the private
// key is NEVER persisted here. The client encrypts it under the vault
// passphrase and stores it via /keys/add like any hand-pasted key; only the
// public half (safe to share) lives server-side in plaintext.

#[derive(Debug, Deserialize)]
struct GenerateKeyRequest {
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    comment: Option<String>,
}

async fn generate_key(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<GenerateKeyRequest>,
) -> Result<Json<Value>, ApiError> {
    let wallet = auth(&headers, &state)?;
    if state.keys.vault(&wallet.id).is_none() {
        return Err(ApiError::bad_request(
            "no vault yet — the private key must be stored encrypted; create the vault first",
        ));
    }
    let comment = body
        .comment
        .or(body.name)
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "sshville".to_string());
    let mut key = ssh_key::PrivateKey::random(
        &mut ssh_key::rand_core::OsRng,
        ssh_key::Algorithm::Ed25519,
    )
    .map_err(|e| ApiError::internal(format!("keygen failed: {e}")))?;
    key.set_comment(&comment);
    let private_key = key
        .to_openssh(ssh_key::LineEnding::LF)
        .map_err(|e| ApiError::internal(format!("private key encode failed: {e}")))?;
    let public_key = key
        .public_key()
        .to_openssh()
        .map_err(|e| ApiError::internal(format!("public key encode failed: {e}")))?;
    let fingerprint = key
        .public_key()
        .fingerprint(ssh_key::HashAlg::Sha256)
        .to_string();
    Ok(Json(json!({
        "algorithm": "ed25519",
        "comment": comment,
        "private_key": private_key.as_str(),
        "public_key": public_key,
        "fingerprint": fingerprint,
        "stored": false,
    })))
}

// ── QR rendering ──────────────────────────────────────────────────────────
// Stateless text→SVG echo for sharing keys across devices. POST (not GET)
// so key material never lands in URL/access logs; nothing is stored.

#[derive(Debug, Deserialize)]
struct QrRequest {
    data: String,
}

async fn qr_svg(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<QrRequest>,
) -> Result<Json<Value>, ApiError> {
    auth(&headers, &state)?;
    if body.data.is_empty() {
        return Err(ApiError::bad_request("data required"));
    }
    if body.data.len() > 2953 {
        return Err(ApiError::bad_request(
            "data too large for a QR code (max 2953 bytes)",
        ));
    }
    let code = qrcode::QrCode::new(body.data.as_bytes())
        .map_err(|e| ApiError::bad_request(format!("qr encode failed: {e}")))?;
    let svg = code
        .render::<qrcode::render::svg::Color>()
        .min_dimensions(220, 220)
        .quiet_zone(true)
        .dark_color(qrcode::render::svg::Color("#0b0f14"))
        .light_color(qrcode::render::svg::Color("#f5f7fa"))
        .build();
    Ok(Json(json!({"svg": svg, "bytes": body.data.len()})))
}

fn slug(name: &str) -> String {
    let s: String = name
        .to_lowercase()
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '-' })
        .collect();
    let s = s.trim_matches('-').to_string();
    if s.is_empty() {
        "key".to_string()
    } else {
        s
    }
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
