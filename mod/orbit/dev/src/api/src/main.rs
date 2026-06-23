use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::http::{header, Method};
use axum::Router;
use parking_lot::RwLock;
use serde_json::Value;
use tower_http::cors::{Any, CorsLayer};
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

use dev_api::keystore::KeyStore;
use dev_api::providers::{Provider, Registry};
use dev_api::AppState;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "dev_api=info,tower_http=info".into()),
        )
        .init();

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(8870);

    let config = load_config();
    let owner = std::env::var("DEV_OWNER")
        .ok()
        .filter(|s| !s.trim().is_empty())
        .or_else(|| config.get("owner").and_then(|v| v.as_str()).map(str::to_string))
        .unwrap_or_default()
        .to_lowercase();

    let seed = seed_providers(&config);
    tracing::info!(
        "seeded {} provider(s): {}",
        seed.len(),
        seed.iter().map(|p| p.name.as_str()).collect::<Vec<_>>().join(", ")
    );
    let registry = Registry::load_or_seed(seed);
    for p in registry.list() {
        if p.has_backend() {
            tracing::info!("provider {}: backend key present ({})", p.name, p.backend_env);
        }
    }

    let http = reqwest::Client::builder()
        .pool_max_idle_per_host(32)
        .timeout(std::time::Duration::from_secs(600)) // long-running streamed completions
        .build()?;

    // Token freshness window (seconds). 7 days mirrors the venice/store gateways.
    let max_age: u64 = std::env::var("DEV_SESSION_TTL")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(86_400 * 7);

    if owner.is_empty() {
        tracing::warn!("no owner configured (config.json:owner / DEV_OWNER) — add/remove provider routes are closed");
    } else {
        tracing::info!("owner: {owner}");
    }

    let state = AppState {
        http,
        keys: Arc::new(KeyStore::new()),
        providers: Arc::new(RwLock::new(registry)),
        owner,
        max_age,
    };

    // CORS: allow the browser to send Authorization and read JSON/SSE back.
    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods([Method::GET, Method::POST, Method::DELETE, Method::OPTIONS])
        .allow_headers([header::CONTENT_TYPE, header::AUTHORIZATION]);

    let app = Router::new()
        .merge(dev_api::router())
        .with_state(state)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let addr: SocketAddr = ([0, 0, 0, 0], port).into();
    tracing::info!("dev-api listening on {}", addr);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

/// Find and parse the module's config.json. Tries `DEV_CONFIG`, then locations
/// relative to a binary launched with cwd = `src/api` (pm2) or run from
/// `target/release`, then `/app/config.json` (container).
fn load_config() -> Value {
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(p) = std::env::var("DEV_CONFIG") {
        candidates.push(PathBuf::from(p));
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join("../../config.json")); // cwd = src/api
        candidates.push(cwd.join("../../../config.json")); // cwd = src/api/target/release
        candidates.push(cwd.join("config.json"));
    }
    candidates.push(PathBuf::from("/app/config.json"));

    for path in candidates {
        if let Ok(raw) = std::fs::read_to_string(&path) {
            if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                tracing::info!("loaded config from {:?}", path);
                return v;
            }
        }
    }
    tracing::warn!("no config.json found — starting with no seeded providers");
    Value::Object(Default::default())
}

/// Build the seed provider list from `config.json:providers`. Each entry there
/// is `{ base_url, backend_env, default_model, icon, color, byok? }` keyed by
/// provider name; seeded providers are marked `builtin` (deletion-protected).
fn seed_providers(config: &Value) -> Vec<Provider> {
    let map = match config.get("providers").and_then(|v| v.as_object()) {
        Some(m) => m,
        None => return Vec::new(),
    };
    let mut out = Vec::new();
    for (name, spec) in map {
        let base_url = spec.get("base_url").and_then(|v| v.as_str()).unwrap_or("").to_string();
        if base_url.is_empty() {
            tracing::warn!("provider {name}: no base_url in config — skipped");
            continue;
        }
        out.push(Provider {
            name: name.clone(),
            label: spec.get("label").and_then(|v| v.as_str()).unwrap_or(name).to_string(),
            base_url: base_url.trim_end_matches('/').to_string(),
            backend_env: spec.get("backend_env").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            default_model: spec.get("default_model").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            icon: spec.get("icon").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            color: spec.get("color").and_then(|v| v.as_str()).unwrap_or("#6366f1").to_string(),
            byok: spec.get("byok").and_then(|v| v.as_bool()).unwrap_or(true),
            builtin: true,
        });
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    out
}
