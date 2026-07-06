use axum::{
    extract::{Path, Query, State},
    routing::get,
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::mpsc;

use crate::models::chain::Chain;
use crate::pipeline::{self, PipelineEvent};
use crate::state::AppState;

#[derive(Deserialize)]
pub struct TraderListParams {
    chain: Option<String>,
    days: Option<u32>,
    limit: Option<usize>,
    min_swaps: Option<u32>,
    sort: Option<String>,
    pool: Option<u32>,
}

#[derive(Deserialize)]
pub struct TraderDetailParams {
    chain: Option<String>,
    days: Option<u32>,
}

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/traders", get(list_traders))
        .route("/traders/{address}", get(get_trader))
}

async fn list_traders(
    State(state): State<Arc<AppState>>,
    Query(params): Query<TraderListParams>,
) -> Json<Value> {
    let chain_str = params.chain.unwrap_or_else(|| "base".to_string());
    let chain = match Chain::from_str(&chain_str) {
        Some(c) => c,
        None => return Json(json!({ "error": "Invalid chain" })),
    };

    let days = params.days.unwrap_or(7);
    let limit = params.limit.unwrap_or(50);
    let min_swaps = params.min_swaps.unwrap_or(5);
    let pool = params.pool.unwrap_or(2000);
    let sort = params.sort.unwrap_or_else(|| "score".to_string());

    // Check cache
    let cache_key = AppState::cache_key(chain.name(), days, pool);
    let traders = if let Some(cached) = state.get_cached(&cache_key) {
        cached
    } else {
        // Run pipeline synchronously
        let (tx, mut rx) = mpsc::channel(100);
        let state_clone = state.clone();

        tokio::spawn(async move {
            pipeline::run_pipeline(state_clone, chain, days, pool, min_swaps, tx).await;
        });

        let mut result = Vec::new();
        while let Some(event) = rx.recv().await {
            if let PipelineEvent::Result { traders, .. } = event {
                result = traders;
                break;
            }
        }
        result
    };

    // Sort
    let mut sorted = traders;
    match sort.as_str() {
        "volume" => sorted.sort_by(|a, b| b.total_volume_usd.partial_cmp(&a.total_volume_usd).unwrap()),
        "pnl" => sorted.sort_by(|a, b| b.realized_pnl_usd.partial_cmp(&a.realized_pnl_usd).unwrap()),
        "winrate" => sorted.sort_by(|a, b| b.win_rate.partial_cmp(&a.win_rate).unwrap()),
        "swaps" => sorted.sort_by(|a, b| b.swap_count.cmp(&a.swap_count)),
        _ => sorted.sort_by(|a, b| b.composite_score.partial_cmp(&a.composite_score).unwrap()),
    }

    let total = sorted.len();
    sorted.truncate(limit);

    Json(json!({
        "traders": sorted,
        "total": total,
        "chain": chain.name(),
        "days": days,
    }))
}

async fn get_trader(
    State(state): State<Arc<AppState>>,
    Path(address): Path<String>,
    Query(params): Query<TraderDetailParams>,
) -> Json<Value> {
    let chain_str = params.chain.unwrap_or_else(|| "base".to_string());
    let chain = match Chain::from_str(&chain_str) {
        Some(c) => c,
        None => return Json(json!({ "error": "Invalid chain" })),
    };

    let days = params.days.unwrap_or(30);
    let addr = address.to_lowercase();

    // Check cache for this trader
    let cache_key = AppState::cache_key(chain.name(), days, 5000);
    let traders = if let Some(cached) = state.get_cached(&cache_key) {
        cached
    } else {
        // Run pipeline with larger pool to find the specific trader
        let (tx, mut rx) = mpsc::channel(100);
        let state_clone = state.clone();

        tokio::spawn(async move {
            pipeline::run_pipeline(state_clone, chain, days, 5000, 1, tx).await;
        });

        let mut result = Vec::new();
        while let Some(event) = rx.recv().await {
            if let PipelineEvent::Result { traders, .. } = event {
                result = traders;
                break;
            }
        }
        result
    };

    match traders.iter().find(|t| t.address == addr) {
        Some(trader) => Json(json!({ "trader": trader })),
        None => Json(json!({ "error": "Trader not found", "address": addr })),
    }
}
