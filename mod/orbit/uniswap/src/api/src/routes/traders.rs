use axum::{
    extract::{Path, Query, State},
    routing::get,
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;

use crate::models::chain::Chain;
use crate::pipeline;
use crate::state::AppState;

#[derive(Deserialize)]
pub struct TraderListParams {
    chain: Option<String>,
    days: Option<u32>,
    limit: Option<usize>,
    min_swaps: Option<u32>,
    sort: Option<String>,
    pool: Option<u32>,
    refresh: Option<bool>,
}

#[derive(Deserialize)]
pub struct TraderDetailParams {
    chain: Option<String>,
    days: Option<u32>,
    pool: Option<u32>,
}

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        // matchit 0.7 (axum 0.7) spells a path parameter `:name`. Written as
        // `{address}` it is a literal path segment, so every trader profile
        // request 404'd.
        .route("/traders/:address", get(get_trader))
        .route("/traders", get(list_traders))
}

async fn list_traders(
    State(state): State<Arc<AppState>>,
    Query(params): Query<TraderListParams>,
) -> Json<Value> {
    let chain_str = params.chain.unwrap_or_else(|| "base".to_string());
    let Some(chain) = Chain::from_str(&chain_str) else {
        return Json(json!({
            "error": format!("unknown chain '{chain_str}'"),
            "chains": Chain::all().iter().map(|c| c.name()).collect::<Vec<_>>(),
        }));
    };

    let days = params.days.unwrap_or(7).clamp(1, 30);
    let limit = params.limit.unwrap_or(50).clamp(1, 500);
    let min_swaps = params.min_swaps.unwrap_or(5);
    let pool = params.pool.unwrap_or(2000).clamp(100, 20_000);
    let sort = params.sort.unwrap_or_else(|| "score".to_string());

    let scrape = pipeline::collect(
        state,
        chain,
        days,
        pool,
        min_swaps,
        params.refresh.unwrap_or(false),
    )
    .await;

    if let Some(err) = scrape.error {
        return Json(json!({ "error": err, "chain": chain.name(), "days": days }));
    }

    let mut sorted = scrape.traders;
    pipeline::sort_traders(&mut sorted, &sort);

    let total = sorted.len();
    sorted.truncate(limit);

    Json(json!({
        "traders": sorted,
        "total": total,
        "returned": sorted.len(),
        "chain": chain.name(),
        "days": days,
        "sort": sort,
        "min_swaps": min_swaps,
        "source": scrape.source,
        "coverage": scrape.coverage,
    }))
}

async fn get_trader(
    State(state): State<Arc<AppState>>,
    Path(address): Path<String>,
    Query(params): Query<TraderDetailParams>,
) -> Json<Value> {
    let chain_str = params.chain.unwrap_or_else(|| "base".to_string());
    let Some(chain) = Chain::from_str(&chain_str) else {
        return Json(json!({
            "error": format!("unknown chain '{chain_str}'"),
            "chains": Chain::all().iter().map(|c| c.name()).collect::<Vec<_>>(),
        }));
    };

    let days = params.days.unwrap_or(7).clamp(1, 30);
    let pool = params.pool.unwrap_or(2000).clamp(100, 20_000);
    let addr = address.to_lowercase();

    // A profile is one row of the same leaderboard, so run the same scrape the
    // leaderboard runs. Widening the sample here (the old code forced pool
    // 5000, min_swaps 1) only guaranteed a second cache key and a second full
    // scrape for every profile click.
    let scrape = pipeline::collect(state, chain, days, pool, 1, false).await;

    if let Some(err) = scrape.error {
        return Json(json!({ "error": err, "address": addr, "chain": chain.name() }));
    }

    match scrape.traders.iter().find(|t| t.address == addr) {
        Some(trader) => Json(json!({
            "trader": trader,
            "chain": chain.name(),
            "days": days,
            "source": scrape.source,
            "coverage": scrape.coverage,
        })),
        None => Json(json!({
            "error": "trader not in this sample",
            "detail": format!(
                "{} did not appear in the {}d {} sample of {} traders. The scrape reads a sample \
                 of the top pools, not every swap on the chain.",
                addr, days, chain.name(), scrape.traders.len()
            ),
            "address": addr,
            "chain": chain.name(),
            "days": days,
            "sampled_traders": scrape.traders.len(),
            "coverage": scrape.coverage,
        })),
    }
}
