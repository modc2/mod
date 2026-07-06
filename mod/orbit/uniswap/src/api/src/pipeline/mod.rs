pub mod aggregator;
pub mod graph;
pub mod mev;
pub mod pnl;
pub mod scoring;
pub mod warmup;

use std::sync::Arc;
use tokio::sync::mpsc;

use crate::models::chain::Chain;
use crate::models::swap::Swap;
use crate::models::trader::{TraderCandidate, TraderResult};
use crate::state::AppState;

use self::aggregator::aggregate_traders;
use self::graph::fetch_swaps;

/// Progress event emitted during pipeline execution
#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "type")]
pub enum PipelineEvent {
    #[serde(rename = "progress")]
    Progress {
        phase: String,
        chain: String,
        done: usize,
        total: usize,
        #[serde(skip_serializing_if = "Option::is_none")]
        kept: Option<usize>,
    },
    #[serde(rename = "partial")]
    Partial { traders: Vec<TraderResult> },
    #[serde(rename = "result")]
    Result {
        traders: Vec<TraderResult>,
        source: String,
    },
    #[serde(rename = "error")]
    Error { message: String },
}

/// Run the full trader scraping pipeline
pub async fn run_pipeline(
    state: Arc<AppState>,
    chain: Chain,
    days: u32,
    pool_size: u32,
    min_swaps: u32,
    tx: mpsc::Sender<PipelineEvent>,
) {
    let chain_name = chain.name().to_string();

    // Check cache first
    let cache_key = AppState::cache_key(chain.name(), days, pool_size);
    if let Some(cached) = state.get_cached(&cache_key) {
        let _ = tx
            .send(PipelineEvent::Result {
                traders: cached,
                source: "cache".to_string(),
            })
            .await;
        return;
    }

    // Phase 1: Collect swaps
    let cutoff = chrono::Utc::now().timestamp() - (days as i64 * 86400);
    let swaps = match fetch_swaps(&state.http, &chain, cutoff, pool_size as usize, &tx).await {
        Ok(s) => s,
        Err(e) => {
            let _ = tx
                .send(PipelineEvent::Error {
                    message: format!("Failed to fetch swaps: {e}"),
                })
                .await;
            return;
        }
    };

    let _ = tx
        .send(PipelineEvent::Progress {
            phase: "collect".to_string(),
            chain: chain_name.clone(),
            done: swaps.len(),
            total: swaps.len(),
            kept: None,
        })
        .await;

    // Phase 2: Aggregate by sender
    let candidates = aggregate_traders(&swaps, min_swaps);
    let total_candidates = candidates.len();

    let _ = tx
        .send(PipelineEvent::Progress {
            phase: "aggregate".to_string(),
            chain: chain_name.clone(),
            done: total_candidates,
            total: total_candidates,
            kept: Some(candidates.len()),
        })
        .await;

    // Phase 3: Enrich (concurrent)
    let results =
        enrich_traders(state.clone(), &chain, &candidates, &swaps, cutoff, &tx).await;

    // Cache results
    state.set_cached(&cache_key, &results);

    let _ = tx
        .send(PipelineEvent::Result {
            traders: results,
            source: "fresh".to_string(),
        })
        .await;
}

/// Enrich trader candidates with full metrics (64-way concurrent)
async fn enrich_traders(
    state: Arc<AppState>,
    chain: &Chain,
    candidates: &[TraderCandidate],
    all_swaps: &[Swap],
    cutoff: i64,
    tx: &mpsc::Sender<PipelineEvent>,
) -> Vec<TraderResult> {
    use crate::config::ENRICHMENT_CONCURRENCY;

    let chain_name = chain.name().to_string();
    let total = candidates.len();
    let mut results: Vec<TraderResult> = Vec::new();
    let mut done = 0;

    // Process in batches of ENRICHMENT_CONCURRENCY
    for chunk in candidates.chunks(ENRICHMENT_CONCURRENCY) {
        let mut handles = Vec::new();

        for candidate in chunk {
            let addr = candidate.address.clone();
            let chain_n = chain_name.clone();
            // Filter swaps for this trader from the already-fetched set
            let trader_swaps: Vec<Swap> = all_swaps
                .iter()
                .filter(|s| s.sender == addr)
                .cloned()
                .collect();

            handles.push(tokio::spawn(async move {
                compute_trader_metrics(&addr, &chain_n, &trader_swaps, cutoff)
            }));
        }

        for handle in handles {
            if let Ok(result) = handle.await {
                results.push(result);
                done += 1;
            } else {
                done += 1;
            }
        }

        // Emit progress
        let _ = tx
            .send(PipelineEvent::Progress {
                phase: "enrich".to_string(),
                chain: chain_name.clone(),
                done,
                total,
                kept: Some(results.len()),
            })
            .await;

        // Emit partial results every batch
        if !results.is_empty() {
            let mut sorted = results.clone();
            sorted.sort_by(|a, b| b.composite_score.partial_cmp(&a.composite_score).unwrap());
            let _ = tx
                .send(PipelineEvent::Partial {
                    traders: sorted.iter().take(50).cloned().collect(),
                })
                .await;
        }
    }

    // Final sort by composite score
    results.sort_by(|a, b| b.composite_score.partial_cmp(&a.composite_score).unwrap());
    results
}

/// Compute full metrics for a single trader
fn compute_trader_metrics(
    address: &str,
    chain: &str,
    swaps: &[Swap],
    cutoff: i64,
) -> TraderResult {
    let window_swaps: Vec<&Swap> = swaps.iter().filter(|s| s.timestamp >= cutoff).collect();

    // Volume metrics
    let total_volume: f64 = window_swaps.iter().map(|s| s.amount_usd).sum();
    let buy_volume: f64 = window_swaps
        .iter()
        .filter(|s| s.is_buy_token0)
        .map(|s| s.amount_usd)
        .sum();
    let sell_volume = total_volume - buy_volume;
    let swap_count = window_swaps.len() as u32;

    // Active days
    let mut days_set = std::collections::HashSet::new();
    for s in &window_swaps {
        days_set.insert(s.timestamp / 86400);
    }
    let active_days = days_set.len() as u32;
    let avg_trade_size = if swap_count > 0 {
        total_volume / swap_count as f64
    } else {
        0.0
    };

    // PnL via FIFO
    let (realized_pnl, win_rate, pool_pnls) = pnl::compute_fifo_pnl(swaps, cutoff);

    // Curves (12 buckets)
    let pnl_curve = pnl::compute_pnl_curve(swaps, cutoff, 12);
    let volume_curve = compute_volume_curve(&window_swaps, cutoff, 12);

    // Token flow
    let (top_tokens, token_concentration) = aggregator::compute_token_stats(&window_swaps);

    // Pool diversity
    let (pools_traded, pool_diversity_score) =
        aggregator::compute_pool_stats(&window_swaps, &pool_pnls);
    let unique_pools = pools_traded.len() as u32;

    // MEV detection
    let (is_mev_bot, mev_indicators) = mev::detect_mev(swaps, active_days);

    // Composite score
    let composite_score =
        scoring::compute_score(total_volume, realized_pnl, win_rate, swap_count, is_mev_bot);

    TraderResult {
        address: address.to_string(),
        chain: chain.to_string(),
        total_volume_usd: total_volume,
        buy_volume_usd: buy_volume,
        sell_volume_usd: sell_volume,
        swap_count,
        active_days,
        avg_trade_size,
        realized_pnl_usd: realized_pnl,
        win_rate,
        pnl_curve,
        volume_curve,
        top_tokens,
        token_concentration,
        pools_traded,
        unique_pools,
        pool_diversity_score,
        is_mev_bot,
        mev_indicators,
        composite_score,
    }
}

/// Compute 12-bucket volume curve
fn compute_volume_curve(swaps: &[&Swap], cutoff: i64, buckets: usize) -> Vec<f64> {
    let now = chrono::Utc::now().timestamp();
    let window = now - cutoff;
    let bucket_size = window as f64 / buckets as f64;
    let mut curve = vec![0.0; buckets];

    for s in swaps {
        let offset = s.timestamp - cutoff;
        let bucket = ((offset as f64 / bucket_size) as usize).min(buckets - 1);
        curve[bucket] += s.amount_usd;
    }

    // Make cumulative
    for i in 1..curve.len() {
        curve[i] += curve[i - 1];
    }
    curve
}
