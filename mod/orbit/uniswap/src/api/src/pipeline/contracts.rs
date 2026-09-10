use futures_util::stream::{self, StreamExt};
use std::collections::HashMap;
use std::sync::Arc;

use crate::config;
use crate::models::chain::Chain;
use crate::pipeline::rpc;
use crate::state::AppState;

/// Is this address a contract?
///
/// A Uniswap V3 Swap event names the contract that called the pool as
/// `sender` and the address that received the output as `recipient`. Neither
/// is guaranteed to be a person: routers, aggregators and MEV bots are all
/// contracts, and a leaderboard that cannot tell them from wallets ends up
/// ranking the Uniswap SwapRouter first on every chain. `eth_getCode` settles
/// it in one call, cached per chain for the life of the process — code at an
/// address does not come and go.
pub async fn is_contract(state: &Arc<AppState>, chain: &Chain, address: &str) -> Option<bool> {
    let key = format!("{}:{}", chain.name(), address.to_lowercase());
    if let Some(v) = state.is_contract.get(&key) {
        return Some(*v);
    }

    let code = rpc::call(
        &state.http,
        chain,
        "eth_getCode",
        serde_json::json!([address, "latest"]),
    )
    .await
    .ok()?;

    let code = code.as_str()?;
    let has_code = code.len() > 2 && code != "0x";
    state.is_contract.insert(key, has_code);
    Some(has_code)
}

/// Classify a batch of addresses at once.
pub async fn classify(
    state: &Arc<AppState>,
    chain: &Chain,
    addresses: &[String],
) -> HashMap<String, bool> {
    let owned: Vec<String> = addresses.to_vec();

    let results: Vec<Option<(String, bool)>> = stream::iter(owned)
        .map(|a| {
            let state = state.clone();
            let chain = *chain;
            async move {
                is_contract(&state, &chain, &a)
                    .await
                    .map(|c| (a.to_lowercase(), c))
            }
        })
        .buffer_unordered(config::CODE_CONCURRENCY)
        .collect()
        .await;

    results.into_iter().flatten().collect()
}
