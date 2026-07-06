use serde::{Deserialize, Serialize};

/// Full trader metrics returned to frontend
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraderResult {
    pub address: String,
    pub chain: String,
    // Volume
    pub total_volume_usd: f64,
    pub buy_volume_usd: f64,
    pub sell_volume_usd: f64,
    pub swap_count: u32,
    pub active_days: u32,
    pub avg_trade_size: f64,
    // PnL
    pub realized_pnl_usd: f64,
    pub win_rate: f64,
    pub pnl_curve: Vec<f64>,
    pub volume_curve: Vec<f64>,
    // Token flow
    pub top_tokens: Vec<TokenStats>,
    pub token_concentration: f64,
    // Pool diversity
    pub pools_traded: Vec<PoolStats>,
    pub unique_pools: u32,
    pub pool_diversity_score: f64,
    // MEV
    pub is_mev_bot: bool,
    pub mev_indicators: MevIndicators,
    // Scoring
    pub composite_score: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenStats {
    pub symbol: String,
    pub volume_usd: f64,
    pub net_flow_usd: f64,
    pub swap_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PoolStats {
    pub pool_id: String,
    pub token0: String,
    pub token1: String,
    pub fee_tier: u32,
    pub volume_usd: f64,
    pub swap_count: u32,
    pub pnl_usd: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct MevIndicators {
    pub sandwich_count: u32,
    pub arb_count: u32,
    pub avg_swaps_per_day: f64,
    pub min_swap_interval_sec: u64,
    pub high_volume_pool_ratio: f64,
}

/// Intermediate candidate during pipeline
#[derive(Debug, Clone)]
pub struct TraderCandidate {
    pub address: String,
    pub swap_count: u32,
    pub total_volume_usd: f64,
}
