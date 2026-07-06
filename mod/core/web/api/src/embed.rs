//! Embeddings-backed semantic search over the module catalog.
//!
//! Substring search finds the word you typed; semantic search finds the module
//! you *meant*. Each module is projected to a short document (name, description,
//! functions, deps) and embedded into a vector; a query is embedded the same
//! way and modules are ranked by cosine similarity — so "trade crypto" surfaces
//! `polymarket`/`hyperliquid` even with no literal word overlap.
//!
//! The provider is an OpenAI-standard `/embeddings` endpoint, configurable so it
//! can point at the mod `dev`/`venice` LLM gateways or OpenAI directly:
//!
//!   WEB_EMBED_URL    base URL, default https://api.openai.com/v1
//!   WEB_EMBED_MODEL  model id,  default text-embedding-3-small
//!   WEB_EMBED_KEY    bearer key (falls back to OPENAI_API_KEY)
//!
//! When no provider is configured or it's unreachable, semantic search reports
//! itself unavailable and the API falls back to substring matching — the
//! explorer never hard-fails on a missing or flaky provider.

use parking_lot::RwLock;
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::time::Duration;

use crate::catalog::Module;

/// Thin client for an OpenAI-standard embeddings endpoint.
struct EmbedClient {
    base_url: String,
    model: String,
    key: Option<String>,
    /// Set when `WEB_EMBED_URL` was provided explicitly — a local gateway may
    /// accept requests without a bearer key, so its mere presence enables search.
    url_configured: bool,
    http: reqwest::Client,
}

impl EmbedClient {
    fn from_env() -> Self {
        let url_configured = std::env::var("WEB_EMBED_URL").is_ok();
        let base_url = std::env::var("WEB_EMBED_URL")
            .unwrap_or_else(|_| "https://api.openai.com/v1".to_string());
        let model = std::env::var("WEB_EMBED_MODEL")
            .unwrap_or_else(|_| "text-embedding-3-small".to_string());
        let key = std::env::var("WEB_EMBED_KEY")
            .or_else(|_| std::env::var("OPENAI_API_KEY"))
            .ok()
            .filter(|k| !k.trim().is_empty());
        let http = reqwest::Client::builder()
            .timeout(Duration::from_secs(20))
            .build()
            .unwrap_or_default();
        Self { base_url, model, key, url_configured, http }
    }

    /// Whether semantic search can run: we need a key (hosted provider) or an
    /// explicit local gateway URL that may not require one.
    fn available(&self) -> bool {
        self.key.is_some() || self.url_configured
    }

    /// Embed a batch of inputs in one request, returning vectors in input order.
    async fn embed(&self, inputs: &[String]) -> Result<Vec<Vec<f32>>, String> {
        if inputs.is_empty() {
            return Ok(Vec::new());
        }
        let url = format!("{}/embeddings", self.base_url.trim_end_matches('/'));
        let mut req = self.http.post(&url).json(&serde_json::json!({
            "model": self.model,
            "input": inputs,
        }));
        if let Some(k) = &self.key {
            req = req.bearer_auth(k);
        }
        let resp = req.send().await.map_err(|e| e.to_string())?;
        if !resp.status().is_success() {
            return Err(format!("embeddings provider returned {}", resp.status()));
        }
        let body: EmbedResponse = resp.json().await.map_err(|e| e.to_string())?;
        // The provider tags each item with its request index; sort to be safe.
        let mut data = body.data;
        data.sort_by_key(|d| d.index);
        Ok(data.into_iter().map(|d| d.embedding).collect())
    }
}

#[derive(serde::Deserialize)]
struct EmbedResponse {
    data: Vec<EmbedDatum>,
}

#[derive(serde::Deserialize)]
struct EmbedDatum {
    #[serde(default)]
    index: usize,
    embedding: Vec<f32>,
}

/// A cached, embedded view of the catalog. Rebuilt only when the catalog's
/// content signature changes (a module added, renamed, or its text edited).
struct IndexSnapshot {
    sig: u64,
    names: Vec<String>,
    /// L2-normalized embedding per module, parallel to `names`.
    vecs: Vec<Vec<f32>>,
}

/// One ranked match: a module name and its cosine similarity to the query.
pub struct Hit {
    pub name: String,
    pub score: f32,
}

/// Semantic search engine: an embeddings client plus a lazily-built, cached
/// index over the catalog.
pub struct Semantic {
    client: EmbedClient,
    cache: RwLock<Option<IndexSnapshot>>,
    /// Serializes index builds so a burst of first-time queries triggers one
    /// embedding pass over the catalog, not one per request.
    build_lock: tokio::sync::Mutex<()>,
}

impl Semantic {
    pub fn from_env() -> Self {
        Self {
            client: EmbedClient::from_env(),
            cache: RwLock::new(None),
            build_lock: tokio::sync::Mutex::new(()),
        }
    }

    /// Whether semantic ranking is configured. The frontend uses the per-query
    /// result flag, but this lets the boot log say which mode is live.
    pub fn available(&self) -> bool {
        self.client.available()
    }

    /// Rank `modules` by semantic similarity to `q`, returning up to `limit`
    /// hits highest-first. Returns `None` when semantic search is unavailable or
    /// the provider errors — callers then fall back to substring search.
    pub async fn search(&self, modules: &[Module], q: &str, limit: usize) -> Option<Vec<Hit>> {
        if !self.client.available() {
            return None;
        }
        let q = q.trim();
        if q.is_empty() {
            return None;
        }
        if let Err(e) = self.ensure_index(modules).await {
            tracing::warn!("semantic index unavailable, falling back: {e}");
            return None;
        }
        let qvec = match self.client.embed(std::slice::from_ref(&q.to_string())).await {
            Ok(mut v) if !v.is_empty() => normalize(v.remove(0)),
            Ok(_) => return None,
            Err(e) => {
                tracing::warn!("query embed failed, falling back: {e}");
                return None;
            }
        };
        let guard = self.cache.read();
        let snap = guard.as_ref()?;
        let mut hits: Vec<Hit> = snap
            .names
            .iter()
            .zip(snap.vecs.iter())
            .map(|(name, v)| Hit { name: name.clone(), score: dot(&qvec, v) })
            .collect();
        hits.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        hits.truncate(limit);
        Some(hits)
    }

    /// Ensure the cached index matches the current catalog, rebuilding it via one
    /// batched embedding call if the catalog's content signature has changed.
    async fn ensure_index(&self, modules: &[Module]) -> Result<(), String> {
        let sig = signature(modules);
        if self.cache.read().as_ref().is_some_and(|s| s.sig == sig) {
            return Ok(());
        }
        let _g = self.build_lock.lock().await;
        // Another task may have built the index while we waited for the lock.
        if self.cache.read().as_ref().is_some_and(|s| s.sig == sig) {
            return Ok(());
        }
        let names: Vec<String> = modules.iter().map(|m| m.name.clone()).collect();
        let docs: Vec<String> = modules.iter().map(doc_text).collect();
        let vecs = self.client.embed(&docs).await?;
        if vecs.len() != names.len() {
            return Err(format!(
                "provider returned {} vectors for {} modules",
                vecs.len(),
                names.len()
            ));
        }
        let vecs = vecs.into_iter().map(normalize).collect();
        *self.cache.write() = Some(IndexSnapshot { sig, names, vecs });
        tracing::info!("semantic index built: {} modules embedded", modules.len());
        Ok(())
    }
}

/// The text we embed for a module: its identity plus what it does and connects
/// to, so similarity captures purpose, not just the name.
fn doc_text(m: &Module) -> String {
    let mut s = format!("{}. {}", m.name, m.description);
    if !m.fns.is_empty() {
        s.push_str(". functions: ");
        s.push_str(&m.fns.join(", "));
    }
    if !m.deps.is_empty() {
        s.push_str(". depends on: ");
        s.push_str(&m.deps.join(", "));
    }
    s
}

/// Content fingerprint of the catalog — changes iff a module's searchable text
/// changes, so the embedding index is rebuilt exactly when it must be.
fn signature(modules: &[Module]) -> u64 {
    let mut h = DefaultHasher::new();
    modules.len().hash(&mut h);
    for m in modules {
        m.name.hash(&mut h);
        m.description.hash(&mut h);
        m.fns.hash(&mut h);
        m.deps.hash(&mut h);
    }
    h.finish()
}

/// Scale a vector to unit length so a dot product equals cosine similarity.
fn normalize(mut v: Vec<f32>) -> Vec<f32> {
    let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        for x in &mut v {
            *x /= norm;
        }
    }
    v
}

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}
