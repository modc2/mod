use anyhow::Result;
use std::sync::atomic::{AtomicUsize, Ordering};
use tokio::sync::mpsc;

use crate::config::{self, BLOCK_RANGE};
use crate::models::chain::Chain;
use crate::models::swap::Swap;
use crate::pipeline::PipelineEvent;

/// Round-robin counter for RPC endpoint selection
static RPC_INDEX: AtomicUsize = AtomicUsize::new(0);

/// Get next RPC endpoint (round-robin)
fn next_rpc(chain: &Chain) -> &'static str {
    let endpoints = config::rpc_endpoints(chain);
    let idx = RPC_INDEX.fetch_add(1, Ordering::Relaxed) % endpoints.len();
    endpoints[idx]
}

/// Fetch recent block number from chain
async fn get_block_number(http: &reqwest::Client, chain: &Chain) -> Result<u64> {
    let rpc = next_rpc(chain);
    let body = serde_json::json!({
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1
    });

    let resp: serde_json::Value = http.post(rpc).json(&body).send().await?.json().await?;

    let hex = resp["result"]
        .as_str()
        .ok_or_else(|| anyhow::anyhow!("No block number from {rpc}"))?;

    Ok(u64::from_str_radix(hex.trim_start_matches("0x"), 16)?)
}

/// Fetch Uniswap V3 Swap logs from public archive RPCs
/// Uses eth_getLogs with the Swap event topic, paginated by block range
pub async fn fetch_swaps(
    http: &reqwest::Client,
    chain: &Chain,
    cutoff_timestamp: i64,
    max_swaps: usize,
    tx: &mpsc::Sender<PipelineEvent>,
) -> Result<Vec<Swap>> {
    let chain_name = chain.name().to_string();
    let topic = config::swap_event_topic();
    let pools = config::top_pools(chain);

    // Get current block
    let head_block = get_block_number(http, chain).await?;

    // Estimate start block from cutoff timestamp
    let now_ts = chrono::Utc::now().timestamp();
    let seconds_back = (now_ts - cutoff_timestamp).max(0) as u64;
    let bpd = config::blocks_per_day(chain);
    let blocks_back = (seconds_back * bpd) / 86400;
    let from_block = head_block.saturating_sub(blocks_back);

    let total_blocks = head_block - from_block;
    let total_chunks = ((total_blocks / BLOCK_RANGE) + 1) * pools.len() as u64;

    tracing::info!(
        "Fetching swaps on {}: blocks {}..{} across {} pools ({} total chunks)",
        chain_name,
        from_block,
        head_block,
        pools.len(),
        total_chunks
    );

    let mut all_swaps: Vec<Swap> = Vec::new();
    let mut chunks_done = 0u64;

    // Query each pool separately (public RPCs need address filter)
    for pool_addr in pools {
        let mut current_from = from_block;

        while current_from < head_block && all_swaps.len() < max_swaps {
            let current_to = (current_from + BLOCK_RANGE).min(head_block);
            let rpc = next_rpc(chain);

            let body = serde_json::json!({
                "jsonrpc": "2.0",
                "method": "eth_getLogs",
                "params": [{
                    "address": pool_addr,
                    "fromBlock": format!("0x{:x}", current_from),
                    "toBlock": format!("0x{:x}", current_to),
                    "topics": [topic]
                }],
                "id": 1
            });

            match http.post(rpc).json(&body).send().await {
                Ok(resp) => {
                    match resp.json::<serde_json::Value>().await {
                        Ok(json) => {
                            if let Some(logs) = json["result"].as_array() {
                                for log in logs {
                                    if let Some(swap) = parse_swap_log(log, chain) {
                                        if swap.timestamp >= cutoff_timestamp {
                                            all_swaps.push(swap);
                                        }
                                    }
                                }
                            } else if let Some(err) = json.get("error") {
                                tracing::warn!("RPC error from {}: {:?}", rpc, err);
                            }
                        }
                        Err(e) => {
                            tracing::warn!("JSON parse error from {}: {}", rpc, e);
                        }
                    }
                }
                Err(e) => {
                    tracing::warn!("Request failed to {}: {}", rpc, e);
                }
            }

            chunks_done += 1;
            current_from = current_to + 1;

            // Emit progress every 10 chunks
            if chunks_done % 10 == 0 || current_from >= head_block {
                let _ = tx
                    .send(PipelineEvent::Progress {
                        phase: "collect".to_string(),
                        chain: chain_name.clone(),
                        done: chunks_done as usize,
                        total: total_chunks as usize,
                        kept: Some(all_swaps.len()),
                    })
                    .await;
            }

            // Small delay every 5 requests to be respectful to public RPCs
            if chunks_done % 5 == 0 {
                tokio::time::sleep(std::time::Duration::from_millis(50)).await;
            }
        }
    }

    tracing::info!(
        "Fetched {} swaps from {} ({} chunks)",
        all_swaps.len(),
        chain_name,
        chunks_done
    );

    Ok(all_swaps)
}

/// Parse a raw EVM log into a Swap struct
/// Swap event: Swap(address sender, address recipient, int256 amount0, int256 amount1, uint160 sqrtPriceX96, uint128 liquidity, int24 tick)
fn parse_swap_log(log: &serde_json::Value, chain: &Chain) -> Option<Swap> {
    let topics = log["topics"].as_array()?;
    if topics.len() < 3 {
        return None;
    }

    let data_hex = log["data"].as_str()?.trim_start_matches("0x");
    if data_hex.len() < 320 {
        // Need at least 5 * 64 hex chars (amount0, amount1, sqrtPriceX96, liquidity, tick)
        return None;
    }

    // Topics[1] = sender (indexed), Topics[2] = recipient (indexed)
    let sender = format!("0x{}", &topics[1].as_str()?[26..]);
    let _recipient = format!("0x{}", &topics[2].as_str()?[26..]);

    // Data: amount0 (int256) | amount1 (int256) | sqrtPriceX96 (uint160) | liquidity (uint128) | tick (int24)
    let amount0 = parse_int256(&data_hex[0..64]);
    let amount1 = parse_int256(&data_hex[64..128]);

    // Pool address = log.address
    let pool_id = log["address"].as_str()?.to_lowercase();

    // Block number for timestamp approximation
    let block_hex = log["blockNumber"].as_str()?.trim_start_matches("0x");
    let block_number = u64::from_str_radix(block_hex, 16).ok()?;

    // Estimate timestamp from block number using known reference points
    let timestamp = estimate_timestamp(block_number, chain);

    // Estimate USD value (rough: use amount * price approximation)
    // Without token metadata, we estimate based on amount magnitudes
    let amount_usd = estimate_usd(amount0, amount1);

    let tx_hash = log["transactionHash"].as_str().unwrap_or("").to_string();

    Some(Swap {
        id: format!("{}:{}", tx_hash, log["logIndex"].as_str().unwrap_or("0")),
        timestamp,
        sender,
        amount_usd,
        amount0,
        amount1,
        pool_id,
        token0_symbol: String::new(), // Populated during enrichment
        token1_symbol: String::new(),
        fee_tier: 0,
        is_buy_token0: amount0 < 0.0, // Negative = out of pool = trader received
    })
}

/// Parse int256 from 64-char hex string
fn parse_int256(hex: &str) -> f64 {
    // Check sign bit
    let first_nibble = u8::from_str_radix(&hex[0..1], 16).unwrap_or(0);
    let is_negative = first_nibble >= 8;

    if is_negative {
        // Two's complement for negative
        let mut bytes = [0u8; 32];
        for i in 0..32 {
            bytes[i] = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap_or(0);
        }
        // Invert and add 1
        for b in bytes.iter_mut() {
            *b = !*b;
        }
        // Add 1 (simple carry)
        let mut carry = true;
        for b in bytes.iter_mut().rev() {
            if carry {
                let (new_val, overflow) = b.overflowing_add(1);
                *b = new_val;
                carry = overflow;
            }
        }
        // Take first 8 bytes as magnitude (sufficient precision for our use)
        let magnitude = u64::from_be_bytes([
            bytes[24], bytes[25], bytes[26], bytes[27],
            bytes[28], bytes[29], bytes[30], bytes[31],
        ]);
        -(magnitude as f64)
    } else {
        // Positive: take last 8 bytes
        let val = u64::from_str_radix(&hex[48..64], 16).unwrap_or(0);
        val as f64
    }
}

/// Rough timestamp estimation from block number
fn estimate_timestamp(block_number: u64, chain: &Chain) -> i64 {
    let now = chrono::Utc::now().timestamp();
    let bpd = config::blocks_per_day(chain);
    let spb = 86400.0 / bpd as f64; // seconds per block

    // Known reference points (approximate head block per chain)
    let (ref_block, ref_ts) = match chain {
        Chain::Base => (30_000_000u64, 1735000000i64),       // ~Dec 2024
        Chain::Ethereum => (21_500_000, 1735000000),
        Chain::Arbitrum => (280_000_000, 1735000000),
        Chain::Polygon => (65_000_000, 1735000000),
        Chain::Optimism => (130_000_000, 1735000000),
    };

    // Extrapolate from reference
    let block_diff = block_number as i64 - ref_block as i64;
    let ts = ref_ts + (block_diff as f64 * spb) as i64;

    // Clamp to reasonable range
    ts.max(now - 86400 * 365).min(now)
}

/// Estimate USD value from raw amounts
/// This is approximate — in production you'd look up token decimals and prices
fn estimate_usd(amount0: f64, amount1: f64) -> f64 {
    // Take the larger absolute value as a rough proxy
    // Most Uniswap pools have one side as a stablecoin or ETH
    // For 18-decimal tokens, raw amounts are in wei (divide by 1e18)
    // For 6-decimal tokens (USDC/USDT), divide by 1e6
    let abs0 = amount0.abs();
    let abs1 = amount1.abs();

    // Heuristic: if one side is small (<1e12) it's likely a 6-decimal token (stablecoin)
    // if both are large (>1e15) they're both 18-decimal
    let usd = if abs0 < 1e12 && abs0 > 0.0 {
        abs0 / 1e6 // Likely USDC/USDT (6 decimals)
    } else if abs1 < 1e12 && abs1 > 0.0 {
        abs1 / 1e6
    } else if abs0 > 1e15 {
        abs0 / 1e18 * 2500.0 // Assume ETH at ~$2500
    } else if abs1 > 1e15 {
        abs1 / 1e18 * 2500.0
    } else {
        (abs0.max(abs1)) / 1e18 * 2500.0
    };

    usd.min(10_000_000.0) // Cap at $10M per swap (sanity)
}
