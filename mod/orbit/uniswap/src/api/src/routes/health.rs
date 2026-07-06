use axum::{extract::State, routing::get, Json, Router};
use serde_json::{json, Value};
use std::sync::Arc;

use crate::models::chain::Chain;
use crate::state::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/health", get(health))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<Value> {
    let mut chain_status = serde_json::Map::new();

    for chain in Chain::all() {
        let cached_days: Vec<u32> = [1, 7, 14, 30]
            .iter()
            .filter(|&&d| {
                let key = AppState::cache_key(chain.name(), d, 2000);
                state.get_cached(&key).is_some()
            })
            .copied()
            .collect();

        chain_status.insert(
            chain.name().to_string(),
            json!({
                "status": "ok",
                "cached_windows": cached_days,
            }),
        );
    }

    Json(json!({
        "status": "ok",
        "service": "uniswap-trader-api",
        "version": "0.1.0",
        "chains": chain_status,
        "cache_entries": state.memory_cache.len(),
    }))
}
