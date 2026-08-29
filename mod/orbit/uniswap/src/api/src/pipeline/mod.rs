pub mod aggregator;
pub mod blocks;
pub mod contracts;
pub mod logs;
pub mod meta;
pub mod mev;
pub mod pnl;
pub mod price;
pub mod rpc;
pub mod scoring;
pub mod warmup;

use std::sync::Arc;
use tokio::sync::mpsc;

use crate::models::chain::Chain;
use crate::models::swap::Swap;
use crate::models::trader::{TraderCandidate, TraderResult};
use crate::state::AppState;

use self::aggregator::aggregate_traders;
use self::logs::Coverage;

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
        #[serde(skip_serializing_if = "Option::is_none")]
        coverage: Option<Coverage>,
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
                coverage: state.get_coverage(&cache_key),
            })
            .await;
        return;
    }

    // Phase 0: what the pool addresses actually are, and where the window is.
    let _ = tx
        .send(PipelineEvent::Progress {
            phase: "resolve".to_string(),
            chain: chain_name.clone(),
            done: 0,
            total: 2,
            kept: None,
        })
        .await;

    let pools = meta::resolve_chain_pools(&state, &chain).await;
    if pools.is_empty() {
        let _ = tx
            .send(PipelineEvent::Error {
                message: format!("no Uniswap V3 pools resolved on {chain_name}"),
            })
            .await;
        return;
    }

    let window = match blocks::resolve_window(&state.http, &chain, days).await {
        Ok(w) => w,
        Err(e) => {
            let _ = tx
                .send(PipelineEvent::Error {
                    message: format!("could not resolve the {days}d block window on {chain_name}: {e}"),
                })
                .await;
            return;
        }
    };

    let _ = tx
        .send(PipelineEvent::Progress {
            phase: "resolve".to_string(),
            chain: chain_name.clone(),
            done: 2,
            total: 2,
            kept: Some(pools.len()),
        })
        .await;

    // The window's own start timestamp is the cutoff — it came off the chain,
    // so it agrees with the swap timestamps derived from the same anchors.
    let cutoff = window.from_ts;

    // Phase 1: Collect swaps
    let (swaps, coverage) =
        match logs::fetch_swaps(&state, &chain, &window, &pools, pool_size as usize, &tx).await {
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
    let results = enrich_traders(state.clone(), &chain, &candidates, &swaps, cutoff, &tx).await;

    // Cache results
    state.set_cached(&cache_key, &results);
    state.set_coverage(&cache_key, coverage);

    let _ = tx
        .send(PipelineEvent::Result {
            traders: results,
            source: "fresh".to_string(),
            coverage: Some(coverage),
        })
        .await;
}

/// Enrich trader candidates with full metrics (64-way concurrent)
async fn enrich_traders(
    _state: Arc<AppState>,
    chain: &Chain,
    candidates: &[TraderCandidate],
    all_swaps: &[Swap],
    cutoff: i64,
    tx: &mpsc::Sender<PipelineEvent>,
) -> Vec<TraderResult> {
    use crate::config::ENRICHMENT_CONCURRENCY;
    use std::collections::HashMap;

    let chain_name = chain.name().to_string();
    let total = candidates.len();
    let mut results: Vec<TraderResult> = Vec::new();
    let mut done = 0;

    // Bucket the sample by trader once. Re-scanning every swap for every
    // candidate was quadratic: 5k swaps and 1k candidates is 5M comparisons
    // per request, repeated for each of the twelve metric passes downstream.
    let mut by_trader: HashMap<&str, Vec<Swap>> = HashMap::new();
    for s in all_swaps {
        by_trader.entry(s.recipient.as_str()).or_default().push(s.clone());
    }

    for chunk in candidates.chunks(ENRICHMENT_CONCURRENCY) {
        let mut handles = Vec::new();

        for candidate in chunk {
            let addr = candidate.address.clone();
            let chain_n = chain_name.clone();
            let trader_swaps = by_trader.get(addr.as_str()).cloned().unwrap_or_default();

            handles.push(tokio::spawn(async move {
                compute_trader_metrics(&addr, &chain_n, &trader_swaps, cutoff)
            }));
        }

        for handle in handles {
            if let Ok(result) = handle.await {
                results.push(result);
            }
            done += 1;
        }

        let _ = tx
            .send(PipelineEvent::Progress {
                phase: "enrich".to_string(),
                chain: chain_name.clone(),
                done,
                total,
                kept: Some(results.len()),
            })
            .await;

        if !results.is_empty() {
            let mut sorted = results.clone();
            sorted.sort_by(TraderResult::by(|t| t.composite_score));
            let _ = tx
                .send(PipelineEvent::Partial {
                    traders: sorted.iter().take(50).cloned().collect(),
                })
                .await;
        }
    }

    results.sort_by(TraderResult::by(|t| t.composite_score));

    // Mark which of these addresses hold code. Done once for the whole result
    // set so callers can separate wallets from routers and bot contracts.
    let addresses: Vec<String> = results.iter().map(|t| t.address.clone()).collect();
    let codes = contracts::classify(&_state, chain, &addresses).await;
    for t in results.iter_mut() {
        t.is_contract = codes.get(&t.address).copied();
    }

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
        is_contract: None,
    }
}

/// Compute 12-bucket cumulative volume curve
fn compute_volume_curve(swaps: &[&Swap], cutoff: i64, buckets: usize) -> Vec<f64> {
    let buckets = buckets.max(1);
    let now = chrono::Utc::now().timestamp();
    let window = (now - cutoff).max(1);
    let bucket_size = window as f64 / buckets as f64;
    let mut curve = vec![0.0; buckets];

    for s in swaps {
        let offset = (s.timestamp - cutoff).max(0);
        let bucket = ((offset as f64 / bucket_size) as usize).min(buckets - 1);
        curve[bucket] += s.amount_usd;
    }

    for i in 1..curve.len() {
        curve[i] += curve[i - 1];
    }
    curve
}

/// What one scrape produced, whatever its source.
pub struct Scrape {
    pub traders: Vec<TraderResult>,
    pub source: String,
    pub coverage: Option<Coverage>,
    pub error: Option<String>,
}

/// Run the pipeline to completion and collect the result.
///
/// The single place REST, the NDJSON stream and MCP all go through, so the
/// three surfaces cannot disagree about what a "7-day base scrape" means.
pub async fn collect(
    state: Arc<AppState>,
    chain: Chain,
    days: u32,
    pool_size: u32,
    min_swaps: u32,
    force: bool,
) -> Scrape {
    let cache_key = AppState::cache_key(chain.name(), days, pool_size);
    if force {
        state.invalidate(&cache_key);
    }

    let (tx, mut rx) = mpsc::channel(100);
    let state_clone = state.clone();
    tokio::spawn(async move {
        run_pipeline(state_clone, chain, days, pool_size, min_swaps, tx).await;
    });

    let mut out = Scrape {
        traders: Vec::new(),
        source: "none".to_string(),
        coverage: None,
        error: None,
    };

    while let Some(event) = rx.recv().await {
        match event {
            PipelineEvent::Result {
                traders,
                source,
                coverage,
            } => {
                out.traders = traders;
                out.source = source;
                out.coverage = coverage;
                break;
            }
            // An error event ends the run — the pipeline sends nothing after
            // it. Waiting for a Result that will never arrive is how a dead
            // RPC turned into a hung request.
            PipelineEvent::Error { message } => {
                out.error = Some(message);
                break;
            }
            _ => {}
        }
    }

    out
}

/// Sort a leaderboard in place by one of the supported keys.
pub fn sort_traders(traders: &mut [TraderResult], sort: &str) {
    match sort {
        "volume" => traders.sort_by(TraderResult::by(|t| t.total_volume_usd)),
        "pnl" => traders.sort_by(TraderResult::by(|t| t.realized_pnl_usd)),
        "winrate" => traders.sort_by(TraderResult::by(|t| t.win_rate)),
        "swaps" => traders.sort_by(TraderResult::by(|t| t.swap_count as f64)),
        "trades" => traders.sort_by(TraderResult::by(|t| t.swap_count as f64)),
        _ => traders.sort_by(TraderResult::by(|t| t.composite_score)),
    }
}

/// The sort keys `sort_traders` understands.
pub const SORT_KEYS: &[&str] = &["score", "volume", "pnl", "winrate", "swaps"];
