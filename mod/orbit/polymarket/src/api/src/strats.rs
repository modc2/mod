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

// ── Public gallery types ──
//
// Private strats live encrypted (the server can't read them). A PUBLISHED
// strat is the opposite by definition — anyone may view and fork it — so the
// gallery stores the SavedIndex as plaintext JSON. `token_id` is the
// publisher's credential for unpublish/republish and is NEVER serialized in
// list responses (skip_serializing) — only `owner` (the EOA they chose to
// sign the card with) is public.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PublicStrat {
    pub id: String,
    #[serde(default)]
    pub owner: String, // publisher EOA, display only
    #[serde(skip_serializing, default)]
    pub token_id: String, // publisher credential — gates unpublish; never listed
    pub strat: Value, // plaintext SavedIndex JSON
    pub updated_at: u64,
}

#[derive(Deserialize)]
pub struct PublishBody {
    pub token_id: String,
    #[serde(default)]
    pub owner: String,
    pub strat: Value,
    pub updated_at: u64,
}

// ── Storage ──

pub struct StratStore {
    cache: RwLock<HashMap<String, TokenStore>>,
    disk_dir: PathBuf,
    /// Where private blobs used to live: `/tmp/polymarket-strats`, under a
    /// 16-char name. Read-only now — see `legacy_disk_path`.
    legacy_dir: PathBuf,
    /// Public gallery — one plaintext JSON file per published strat. Lives on
    /// the persistent data volume (POLYMARKET_DATA_DIR) so published strats
    /// survive container recreates.
    public_dir: PathBuf,
}

impl StratStore {
    pub fn new() -> Self {
        // Private strat blobs are per-deployment state, so they belong on the
        // persistent data volume with the rest of it. They used to live in
        // `std::env::temp_dir()` unconditionally, where a reboot or a tmp
        // sweep silently dropped every saved strat.
        let data_dir = std::env::var("POLYMARKET_DATA_DIR")
            .ok()
            .map(PathBuf::from)
            .unwrap_or_else(crate::access::state_dir);
        let disk_dir = data_dir.join("polymarket-strats");
        std::fs::create_dir_all(&disk_dir).ok();
        let legacy_dir = std::env::temp_dir().join("polymarket-strats");
        let public_dir = data_dir.join("polymarket-public-strats");
        std::fs::create_dir_all(&public_dir).ok();
        Self {
            cache: RwLock::new(HashMap::new()),
            disk_dir,
            legacy_dir,
            public_dir,
        }
    }

    /// Filename for one sync token's blob.
    ///
    /// A digest of the WHOLE token. The old scheme kept the first 16
    /// alphanumeric characters verbatim, which made the filename lossy in two
    /// ways: two tokens sharing a 16-char prefix mapped to one file, and so
    /// did any pair differing only in punctuation (`a-b` and `ab` both became
    /// `ab`). Either way one browser read and overwrote another's strats.
    fn disk_path(&self, token_id: &str) -> PathBuf {
        let mut h = <sha2::Sha256 as sha2::Digest>::new();
        sha2::Digest::update(&mut h, token_id.as_bytes());
        self.disk_dir
            .join(format!("{}.json", hex::encode(sha2::Digest::finalize(h))))
    }

    /// The pre-digest filename, in both the old `/tmp` directory and the
    /// current one. Read-only fallback so a deployment that upgrades doesn't
    /// look like it lost every saved strat; `load` migrates what it finds.
    fn legacy_disk_paths(&self, token_id: &str) -> [PathBuf; 2] {
        let safe: String = token_id
            .chars()
            .filter(|c| c.is_alphanumeric())
            .take(16)
            .collect();
        let name = format!("{}.json", safe);
        [self.disk_dir.join(&name), self.legacy_dir.join(&name)]
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
        // Nothing under the digest name — try the pre-migration locations and
        // rewrite what we find under the new one, so this runs at most once
        // per token.
        for legacy in self.legacy_disk_paths(token_id) {
            let Ok(data) = std::fs::read_to_string(&legacy) else { continue };
            let Ok(store) = serde_json::from_str::<TokenStore>(&data) else { continue };
            tracing::info!(from = %legacy.display(), to = %path.display(), "migrating strat blob");
            self.save(token_id, &store);
            return store;
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

    // ── Public gallery ──

    fn public_path(&self, id: &str) -> PathBuf {
        let safe: String = id.chars().filter(|c| c.is_alphanumeric()).take(32).collect();
        self.public_dir.join(format!("{}.json", safe))
    }

    fn load_public(&self, id: &str) -> Option<PublicStrat> {
        let data = std::fs::read_to_string(self.public_path(id)).ok()?;
        serde_json::from_str(&data).ok()
    }

    /// Every published strat, newest-touched first.
    pub fn list_public(&self) -> Vec<PublicStrat> {
        let mut out = Vec::new();
        if let Ok(read) = std::fs::read_dir(&self.public_dir) {
            for entry in read.flatten() {
                if let Ok(data) = std::fs::read_to_string(entry.path()) {
                    if let Ok(s) = serde_json::from_str::<PublicStrat>(&data) {
                        out.push(s);
                    }
                }
            }
        }
        out.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
        out
    }

    /// Publish (or republish) a strat. The first publisher's token_id claims
    /// the id; a different token republishing it is rejected.
    pub fn publish(
        &self,
        id: &str,
        token_id: &str,
        owner: &str,
        strat: &Value,
        updated_at: u64,
    ) -> Result<(), StatusCode> {
        if let Some(existing) = self.load_public(id) {
            if existing.token_id != token_id {
                return Err(StatusCode::FORBIDDEN);
            }
        }
        let record = PublicStrat {
            id: id.to_string(),
            owner: owner.trim().to_lowercase(),
            token_id: token_id.to_string(),
            strat: strat.clone(),
            updated_at,
        };
        // token_id is skip_serializing (so list responses never leak it) —
        // build the on-disk shape by hand to keep the credential persisted.
        let json = serde_json::json!({
            "id": record.id,
            "owner": record.owner,
            "token_id": record.token_id,
            "strat": record.strat,
            "updated_at": record.updated_at,
        });
        std::fs::write(self.public_path(id), json.to_string())
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
    }

    /// Unpublish. Only the publishing token may take a strat down.
    pub fn unpublish(&self, id: &str, token_id: &str) -> Result<(), StatusCode> {
        match self.load_public(id) {
            None => Ok(()), // already gone — idempotent
            Some(existing) if existing.token_id == token_id => {
                std::fs::remove_file(self.public_path(id))
                    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
            }
            Some(_) => Err(StatusCode::FORBIDDEN),
        }
    }
}

// ── HMAC Validation ──

/// Transport-integrity check on the strat-sync body — NOT authentication.
///
/// The signing key is `NEXT_PUBLIC_STRAT_HMAC_SECRET`, which is compiled into
/// the public client bundle (next.config.mjs), so anyone who loads the console
/// has it. What actually gates these routes is the owner access gate in
/// `access.rs` that wraps every route in the API; this only catches a mangled
/// or truncated body. Treating it as a second credential would be a mistake.
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

    // `mac.verify_slice` is constant-time; `sig != expected` on hex strings
    // short-circuits at the first differing character.
    let sig_bytes = hex::decode(sig).map_err(|_| StatusCode::UNAUTHORIZED)?;
    mac.verify_slice(&sig_bytes)
        .map_err(|_| StatusCode::UNAUTHORIZED)?;
    Ok(())
}

// ── Routes ──

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/strats", get(list_strats))
        // Community gallery: published strats are plaintext and world-readable.
        // Registered before the :id routes on a deeper path so the two never
        // collide.
        .route("/strats/public", get(list_public_strats))
        .route(
            "/strats/public/:id",
            put(publish_strat).delete(unpublish_strat),
        )
        .route("/strats/:id", put(upsert_strat).delete(delete_strat))
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

async fn list_public_strats(State(state): State<AppState>) -> Json<Value> {
    // PublicStrat's Serialize skips token_id, so this can't leak credentials.
    Json(json!({ "strats": state.strat_store.list_public() }))
}

async fn publish_strat(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Result<Json<Value>, StatusCode> {
    validate_hmac(&headers, &body)?;
    let payload: PublishBody =
        serde_json::from_slice(&body).map_err(|_| StatusCode::BAD_REQUEST)?;
    if payload.token_id.len() < 4 || !payload.strat.is_object() {
        return Err(StatusCode::BAD_REQUEST);
    }
    state.strat_store.publish(
        &id,
        &payload.token_id,
        &payload.owner,
        &payload.strat,
        payload.updated_at,
    )?;
    Ok(Json(json!({ "ok": true, "id": id })))
}

async fn unpublish_strat(
    State(state): State<AppState>,
    Path(id): Path<String>,
    headers: HeaderMap,
    Query(q): Query<DeleteQuery>,
) -> Result<Json<Value>, StatusCode> {
    // Same HMAC shape as the private DELETE: sign over token_id+id.
    let body = format!("{}:{}", q.token_id, id);
    validate_hmac(&headers, body.as_bytes())?;
    if q.token_id.len() < 4 {
        return Err(StatusCode::BAD_REQUEST);
    }
    state.strat_store.unpublish(&id, &q.token_id)?;
    Ok(Json(json!({ "ok": true })))
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

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_store() -> (StratStore, PathBuf) {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let base = std::env::temp_dir().join(format!("pubstrat-test-{}", nanos));
        std::fs::create_dir_all(&base).unwrap();
        let store = StratStore {
            cache: RwLock::new(HashMap::new()),
            disk_dir: base.join("private"),
            public_dir: base.join("public"),
            legacy_dir: base.join("legacy"),
        };
        std::fs::create_dir_all(&store.disk_dir).unwrap();
        std::fs::create_dir_all(&store.public_dir).unwrap();
        (store, base)
    }

    #[test]
    fn publish_list_unpublish_roundtrip() {
        let (s, dir) = tmp_store();
        let strat = json!({ "id": "abc", "name": "MY STRAT", "traders": [] });
        s.publish("abc", "tokAAAA", "0xAAAA", &strat, 111).unwrap();

        let listed = s.list_public();
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0].id, "abc");
        assert_eq!(listed[0].owner, "0xaaaa"); // lowercased
        assert_eq!(listed[0].strat["name"], "MY STRAT");

        // The credential survives on disk (needed to gate unpublish)…
        assert_eq!(listed[0].token_id, "tokAAAA");
        // …but never serializes into a response.
        let out = serde_json::to_value(&listed[0]).unwrap();
        assert!(out.get("token_id").is_none());

        // A different token can neither republish nor unpublish.
        assert_eq!(
            s.publish("abc", "tokBBBB", "0xBBBB", &strat, 222),
            Err(StatusCode::FORBIDDEN)
        );
        assert_eq!(s.unpublish("abc", "tokBBBB"), Err(StatusCode::FORBIDDEN));

        // The owner token can do both; unpublish is idempotent.
        s.publish("abc", "tokAAAA", "0xAAAA", &strat, 333).unwrap();
        s.unpublish("abc", "tokAAAA").unwrap();
        assert!(s.list_public().is_empty());
        s.unpublish("abc", "tokAAAA").unwrap();
        let _ = std::fs::remove_dir_all(dir);
    }
}
