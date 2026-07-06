use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{get, put};
use axum::{Json, Router};
use hmac::{Hmac, Mac};
use parking_lot::RwLock;
use sha2::Sha256;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::PathBuf;

use crate::AppState;

type HmacSha256 = Hmac<Sha256>;

// ── Types ──

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EncryptedStrat {
    pub id: String,
    pub ciphertext: String, // base64-encoded AES-256-GCM ciphertext (IV prepended)
    pub updated_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct TokenStore {
    strats: Vec<EncryptedStrat>,
}

#[derive(Deserialize)]
pub struct ListQuery {
    pub token_id: String, // first 8 chars of user's local token
}

#[derive(Deserialize)]
pub struct UpsertBody {
    pub token_id: String,
    pub ciphertext: String,
    pub updated_at: u64,
}

#[derive(Deserialize)]
pub struct DeleteQuery {
    pub token_id: String,
}

// ── Storage ──

pub struct StratStore {
    cache: RwLock<HashMap<String, TokenStore>>,
    disk_dir: PathBuf,
}

impl StratStore {
    pub fn new() -> Self {
        let disk_dir = std::env::temp_dir().join("polymarket-strats");
        std::fs::create_dir_all(&disk_dir).ok();
        Self {
            cache: RwLock::new(HashMap::new()),
            disk_dir,
        }
    }

    fn disk_path(&self, token_id: &str) -> PathBuf {
        let safe: String = token_id
            .chars()
            .filter(|c| c.is_alphanumeric())
            .take(16)
            .collect();
        self.disk_dir.join(format!("{}.json", safe))
    }

    fn load(&self, token_id: &str) -> TokenStore {
        // Memory first
        {
            let cache = self.cache.read();
            if let Some(store) = cache.get(token_id) {
                return store.clone();
            }
        }
        // Disk fallback
        let path = self.disk_path(token_id);
        if path.exists() {
            if let Ok(data) = std::fs::read_to_string(&path) {
                if let Ok(store) = serde_json::from_str::<TokenStore>(&data) {
                    let mut cache = self.cache.write();
                    cache.insert(token_id.to_string(), store.clone());
                    return store;
                }
            }
        }
        TokenStore { strats: vec![] }
    }

    fn save(&self, token_id: &str, store: &TokenStore) {
        // Memory
        {
            let mut cache = self.cache.write();
            cache.insert(token_id.to_string(), store.clone());
        }
        // Disk
        let path = self.disk_path(token_id);
        if let Ok(json) = serde_json::to_string(store) {
            std::fs::write(path, json).ok();
        }
    }

    pub fn list(&self, token_id: &str) -> Vec<EncryptedStrat> {
        self.load(token_id).strats
    }

    pub fn upsert(&self, token_id: &str, id: &str, ciphertext: &str, updated_at: u64) {
        let mut store = self.load(token_id);
        if let Some(existing) = store.strats.iter_mut().find(|s| s.id == id) {
            existing.ciphertext = ciphertext.to_string();
            existing.updated_at = updated_at;
        } else {
            store.strats.push(EncryptedStrat {
                id: id.to_string(),
                ciphertext: ciphertext.to_string(),
                updated_at,
            });
        }
        self.save(token_id, &store);
    }

    pub fn remove(&self, token_id: &str, id: &str) {
        let mut store = self.load(token_id);
        store.strats.retain(|s| s.id != id);
        self.save(token_id, &store);
    }
}

// ── HMAC Validation ──

fn validate_hmac(headers: &HeaderMap, body: &[u8]) -> Result<(), StatusCode> {
    let secret = std::env::var("STRAT_HMAC_SECRET").unwrap_or_default();
    if secret.is_empty() {
        // No HMAC configured — skip validation (dev mode)
        return Ok(());
    }

    let sig = headers
        .get("x-strat-sig")
        .and_then(|v| v.to_str().ok())
        .ok_or(StatusCode::UNAUTHORIZED)?;

    let mut mac = HmacSha256::new_from_slice(secret.as_bytes())
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    mac.update(body);

    let expected = hex::encode(mac.finalize().into_bytes());
    if sig != expected {
        return Err(StatusCode::UNAUTHORIZED);
    }
    Ok(())
}

// ── Routes ──

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/strats", get(list_strats))
        .route("/strats/{id}", put(upsert_strat).delete(delete_strat))
}

async fn list_strats(
    State(state): State<AppState>,
    Query(q): Query<ListQuery>,
) -> Result<Json<Value>, StatusCode> {
    if q.token_id.len() < 4 {
        return Err(StatusCode::BAD_REQUEST);
    }
    let strats = state.strat_store.list(&q.token_id);
    Ok(Json(json!({ "strats": strats })))
}

async fn upsert_strat(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, StatusCode> {
    validate_hmac(&headers, &body)?;

    let payload: UpsertBody =
        serde_json::from_slice(&body).map_err(|_| StatusCode::BAD_REQUEST)?;

    if payload.token_id.len() < 4 || payload.ciphertext.is_empty() {
        return Err(StatusCode::BAD_REQUEST);
    }

    state
        .strat_store
        .upsert(&payload.token_id, &id, &payload.ciphertext, payload.updated_at);

    Ok(Json(json!({ "ok": true, "id": id })))
}

async fn delete_strat(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Query(q): Query<DeleteQuery>,
) -> Result<Json<Value>, StatusCode> {
    // For DELETE, HMAC over the token_id+id
    let body = format!("{}:{}", q.token_id, id);
    validate_hmac(&headers, body.as_bytes())?;

    if q.token_id.len() < 4 {
        return Err(StatusCode::BAD_REQUEST);
    }

    state.strat_store.remove(&q.token_id, &id);
    Ok(Json(json!({ "ok": true })))
}
