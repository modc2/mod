use axum::{
    extract::{Query, State},
    routing::get,
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;

use crate::config;
use crate::models::chain::Chain;
use crate::pipeline::meta;
use crate::state::AppState;

#[derive(Deserialize)]
pub struct PoolParams {
    chain: Option<String>,
}

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/chains", get(list_chains))
        .route("/pools", get(list_pools))
}

async fn list_chains(State(state): State<Arc<AppState>>) -> Json<Value> {
    let chains: Vec<Value> = Chain::all()
        .iter()
        .map(|c| {
            json!({
                "name": c.name(),
                "supported": true,
                "tokens_scanned": config::tokens(c).len(),
                "pools_sampled": state.chain_pools.get(c.name()).map(|p| p.len()).unwrap_or(0),
                "rpc_endpoints": config::rpc_endpoints(c).len(),
                "eth_price_usd": state.get_eth_price(c.name()),
            })
        })
        .collect();

    Json(json!({ "chains": chains }))
}

/// The pools a scrape on this chain actually reads, resolved on chain.
async fn list_pools(
    State(state): State<Arc<AppState>>,
    Query(params): Query<PoolParams>,
) -> Json<Value> {
    let chain_str = params.chain.unwrap_or_else(|| "base".to_string());
    let Some(chain) = Chain::from_str(&chain_str) else {
        return Json(json!({
            "error": format!("unknown chain '{chain_str}'"),
            "chains": Chain::all().iter().map(|c| c.name()).collect::<Vec<_>>(),
        }));
    };

    let pools = meta::resolve_chain_pools(&state, &chain).await;
    let mut list: Vec<Value> = pools
        .values()
        .map(|m| {
            json!({
                "pool": m.pool,
                "pair": m.label(),
                "token0": { "address": m.token0, "symbol": m.symbol0, "decimals": m.decimals0 },
                "token1": { "address": m.token1, "symbol": m.symbol1, "decimals": m.decimals1 },
                "fee_tier": m.fee,
                "fee_pct": m.fee as f64 / 10_000.0,
                "depth": { "amount": m.quote_balance.round(), "token": m.quote_symbol },
            })
        })
        .collect();
    list.sort_by(|a, b| a["pair"].as_str().cmp(&b["pair"].as_str()));

    Json(json!({
        "chain": chain.name(),
        "tokens_scanned": config::tokens(&chain).len(),
        "fee_tiers": config::FEE_TIERS,
        "sampled": pools.len(),
        "pools": list,
    }))
}
