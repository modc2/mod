use std::sync::Arc;

use axum::Router;
use tower_http::cors::{Any, CorsLayer};

// Import from the binary crate's modules
// We rebuild the app the same way main.rs does
use polymarket_api::{AppState, ProxyCache, PipelineState};

pub fn test_app() -> Router {
    let http = reqwest::Client::builder()
        .pool_max_idle_per_host(16)
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .unwrap();

    let proxy_cache = Arc::new(ProxyCache::new(100));
    let pipeline = Arc::new(PipelineState::new(http.clone()));
    let strat_store = Arc::new(polymarket_api::StratStore::new());
    let signer_store = Arc::new(polymarket_api::SignerStore::new());
    let engines = Arc::new(polymarket_api::EngineRegistry::new(
        http.clone(),
        signer_store.clone(),
    ));
    let user_strats = Arc::new(polymarket_api::UserStratStore::new());
    let share = polymarket_api::ShareStore::from_env();

    let state = AppState {
        http,
        proxy_cache,
        pipeline,
        strat_store,
        signer_store,
        engines,
        user_strats,
        share,
    };

    let cors = CorsLayer::new()
        .allow_origin(Any)
        .allow_methods(Any)
        .allow_headers(Any);

    polymarket_api::router()
        .with_state(state)
        .layer(cors)
}
