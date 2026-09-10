use axum::{extract::State, routing::get, Json, Router};
use serde_json::{json, Value};
use std::sync::Arc;

use crate::config;
use crate::models::chain::Chain;
use crate::state::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/health", get(health))
        .route("/", get(info))
}

async fn info(State(state): State<Arc<AppState>>) -> Json<Value> {
    Json(json!({
        "name": "uniswap",
        "description": "Uniswap V3 multi-chain trader scraper",
        "version": env!("CARGO_PKG_VERSION"),
        "chains": Chain::all().iter().map(|c| c.name()).collect::<Vec<_>>(),
        "pools_resolved": state.pool_meta.len(),
        "endpoints": {
            "GET /health": "service status and cached windows",
            "GET /chains": "supported chains and their pool sets",
            "GET /pools?chain=": "resolved pool metadata for one chain",
            "GET /traders?chain=&days=&limit=&sort=&min_swaps=&pool=&refresh=": "leaderboard",
            "GET /traders/:address?chain=&days=": "one trader's full profile",
            "GET /traders/stream?chain=&days=&pool=": "NDJSON scrape with progress events",
            "GET /mcp": "MCP tool registry",
            "POST /mcp": "MCP JSON-RPC 2.0 endpoint",
        },
        "mcp": {
            "http": "POST /mcp",
            "stdio": "python3 mcp.py",
            "tools": crate::routes::mcp::tools().len(),
        },
    }))
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
                "tokens_scanned": config::tokens(chain).len(),
                "pools_sampled": state.chain_pools.get(chain.name()).map(|p| p.len()).unwrap_or(0),
                "eth_price_usd": state.get_eth_price(chain.name()),
            }),
        );
    }

    Json(json!({
        "status": "ok",
        "service": "uniswap-trader-api",
        "version": env!("CARGO_PKG_VERSION"),
        "uptime_seconds": chrono::Utc::now().timestamp() - state.started_at,
        "chains": chain_status,
        "cache_entries": state.memory_cache.len(),
        "pools_resolved": state.pool_meta.len(),
        "mcp": { "http": "POST /mcp", "tools": crate::routes::mcp::tools().len() },
    }))
}
