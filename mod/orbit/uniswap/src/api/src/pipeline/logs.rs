use anyhow::Result;
use futures_util::stream::{self, StreamExt};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::mpsc;

use crate::config;
use crate::models::chain::Chain;
use crate::models::swap::Swap;
use crate::pipeline::blocks::Window;
use crate::pipeline::meta::PoolMeta;
use crate::pipeline::price::{self, parse_int256};
use crate::pipeline::{rpc, PipelineEvent};
use crate::state::AppState;

/// How much of the requested period the sample actually read.
#[derive(Debug, Clone, Copy, serde::Serialize)]
pub struct Coverage {
    pub blocks_sampled: u64,
    pub blocks_total: u64,
    pub windows: usize,
    pub pools: usize,
    pub requests: usize,
    pub failed_requests: usize,
    /// blocks_sampled / blocks_total
    pub fraction: f64,
}

/// One probe: a pool and a block range to read it over.
#[derive(Clone)]
struct Probe {
    pool: String,
    from: u64,
    to: u64,
}

/// Lay out probes evenly across the window, for every pool.
///
/// The old collector walked forward from the start of the window, pool by
/// pool, and stopped as soon as the swap budget filled. On a busy chain that
/// budget is gone inside the first hour of the first pool, so a 30-day request
/// returned one hour of one pool — and reported it as a month of the chain.
/// Spreading fixed-size probes over the whole window costs the same number of
/// requests and describes the period that was actually asked for.
fn plan_probes(pools: &[String], window: &Window, chain: &Chain) -> Vec<Probe> {
    let range = config::block_range(chain);
    let total = window.total_blocks();
    let n = config::SAMPLE_WINDOWS.max(1);

    // If the whole window fits in the probe budget, read it contiguously
    // instead of sampling — no reason to leave gaps when there are none.
    let contiguous = total <= range * n as u64;
    let step = if n > 1 { total / n as u64 } else { total };

    let mut probes = Vec::new();
    for pool in pools {
        for i in 0..n {
            let start = if contiguous {
                window.from_block + range * i as u64
            } else {
                window.from_block + step * i as u64
            };
            if start >= window.head_block {
                break;
            }
            let end = (start + range).min(window.head_block);
            probes.push(Probe {
                pool: pool.clone(),
                from: start,
                to: end,
            });
        }
    }
    probes
}

/// Fetch Uniswap V3 Swap logs across the window and turn them into priced,
/// decimal-adjusted, real-timestamped swaps.
pub async fn fetch_swaps(
    state: &Arc<AppState>,
    chain: &Chain,
    window: &Window,
    pools: &HashMap<String, PoolMeta>,
    max_swaps: usize,
    tx: &mpsc::Sender<PipelineEvent>,
) -> Result<(Vec<Swap>, Coverage)> {
    let chain_name = chain.name().to_string();
    let topic = config::swap_event_topic();

    let pool_ids: Vec<String> = pools.keys().cloned().collect();
    let probes = plan_probes(&pool_ids, window, chain);
    let total_probes = probes.len();

    // Split the swap budget evenly over the probes so no single pool or slice
    // of time can crowd out the rest of the sample.
    let per_probe = (max_swaps / total_probes.max(1)).max(20);

    tracing::info!(
        "{}: {} probes ({} pools x {} windows), budget {} swaps ({}/probe)",
        chain_name,
        total_probes,
        pool_ids.len(),
        config::SAMPLE_WINDOWS,
        max_swaps,
        per_probe,
    );

    let eth_price = price::eth_price_usd(state, chain, pools).await;

    let done = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let failed = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let kept = Arc::new(std::sync::atomic::AtomicUsize::new(0));

    let results: Vec<Vec<Swap>> = stream::iter(probes)
        .map(|probe| {
            let state = state.clone();
            let chain = *chain;
            let pools = pools.clone();
            let done = done.clone();
            let failed = failed.clone();
            let kept = kept.clone();
            let tx = tx.clone();
            let chain_name = chain_name.clone();

            async move {
                let params = serde_json::json!([{
                    "address": probe.pool,
                    "fromBlock": format!("0x{:x}", probe.from),
                    "toBlock": format!("0x{:x}", probe.to),
                    "topics": [topic],
                }]);

                let mut out = Vec::new();
                match rpc::call(&state.http, &chain, "eth_getLogs", params).await {
                    Ok(result) => {
                        let logs = result.as_array().cloned().unwrap_or_default();
                        // Thin evenly rather than taking the head of the page:
                        // the first N logs of a range are the first N seconds
                        // of it.
                        let stride = (logs.len() / per_probe.max(1)).max(1);
                        for log in logs.iter().step_by(stride).take(per_probe) {
                            if let Some(meta) = pools.get(
                                &log["address"]
                                    .as_str()
                                    .unwrap_or_default()
                                    .to_lowercase(),
                            ) {
                                if let Some(swap) = parse_swap_log(log, meta, window, eth_price) {
                                    out.push(swap);
                                }
                            }
                        }
                    }
                    Err(e) => {
                        failed.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        tracing::warn!("{}: getLogs {}..{} failed: {}", chain_name, probe.from, probe.to, e);
                    }
                }

                let n = done.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
                let k = kept.fetch_add(out.len(), std::sync::atomic::Ordering::Relaxed) + out.len();
                if n % 8 == 0 || n == total_probes {
                    let _ = tx
                        .send(PipelineEvent::Progress {
                            phase: "collect".to_string(),
                            chain: chain_name.clone(),
                            done: n,
                            total: total_probes,
                            kept: Some(k),
                        })
                        .await;
                }

                out
            }
        })
        .buffer_unordered(config::FETCH_CONCURRENCY)
        .collect()
        .await;

    let mut all_swaps: Vec<Swap> = results.into_iter().flatten().collect();
    all_swaps.sort_by_key(|s| s.timestamp);

    let failed_requests = failed.load(std::sync::atomic::Ordering::Relaxed);
    let range = config::block_range(chain);
    let ok_probes = total_probes - failed_requests;
    let blocks_sampled = (ok_probes as u64 * range).min(window.total_blocks() * pool_ids.len().max(1) as u64);
    let blocks_total = window.total_blocks().max(1);

    let coverage = Coverage {
        blocks_sampled: (blocks_sampled / pool_ids.len().max(1) as u64).min(blocks_total),
        blocks_total,
        windows: config::SAMPLE_WINDOWS,
        pools: pool_ids.len(),
        requests: total_probes,
        failed_requests,
        fraction: ((blocks_sampled / pool_ids.len().max(1) as u64).min(blocks_total) as f64
            / blocks_total as f64)
            .min(1.0),
    };

    tracing::info!(
        "{}: {} swaps from {} probes ({} failed), {:.1}% of window sampled",
        chain_name,
        all_swaps.len(),
        total_probes,
        failed_requests,
        coverage.fraction * 100.0,
    );

    if all_swaps.is_empty() && failed_requests == total_probes && total_probes > 0 {
        anyhow::bail!(
            "every eth_getLogs request failed on {} — no usable RPC endpoint",
            chain_name
        );
    }

    Ok((all_swaps, coverage))
}

/// Parse a raw EVM log into a Swap.
///
/// Swap(address sender, address recipient, int256 amount0, int256 amount1,
///      uint160 sqrtPriceX96, uint128 liquidity, int24 tick)
///
/// Amounts are from the pool's perspective: positive means the token flowed
/// into the pool (the trader sold it), negative means it flowed out (the
/// trader bought it).
fn parse_swap_log(
    log: &serde_json::Value,
    meta: &PoolMeta,
    window: &Window,
    eth_price: Option<f64>,
) -> Option<Swap> {
    let topics = log["topics"].as_array()?;
    if topics.len() < 3 {
        return None;
    }

    let data_hex = log["data"].as_str()?.trim_start_matches("0x");
    if data_hex.len() < 320 {
        return None;
    }

    let sender = format!("0x{}", &topics[1].as_str()?[26..].to_lowercase());
    let recipient = format!("0x{}", &topics[2].as_str()?[26..].to_lowercase());

    let raw0 = parse_int256(&data_hex[0..64]);
    let raw1 = parse_int256(&data_hex[64..128]);

    // Decimal-adjusted token amounts — the numbers a human would recognise.
    let amount0 = raw0 / 10f64.powi(meta.decimals0 as i32);
    let amount1 = raw1 / 10f64.powi(meta.decimals1 as i32);

    let block_number = rpc::hex_u64(&log["blockNumber"])?;
    let timestamp = window.timestamp_of(block_number);

    let amount_usd = price::swap_usd(meta, amount0, amount1, eth_price);

    let tx_hash = log["transactionHash"].as_str().unwrap_or("").to_string();
    let log_index = log["logIndex"].as_str().unwrap_or("0x0");

    Some(Swap {
        id: format!("{tx_hash}:{log_index}"),
        timestamp,
        block: block_number,
        sender,
        recipient,
        amount_usd,
        amount0,
        amount1,
        pool_id: meta.pool.clone(),
        token0_symbol: meta.symbol0.clone(),
        token1_symbol: meta.symbol1.clone(),
        fee_tier: meta.fee,
        // amount0 < 0: token0 left the pool, so the trader received it.
        is_buy_token0: amount0 < 0.0,
    })
}
