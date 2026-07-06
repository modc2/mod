use serde::{Deserialize, Serialize};

/// Processed swap for internal use
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Swap {
    pub id: String,
    pub timestamp: i64,
    pub sender: String,
    pub amount_usd: f64,
    pub amount0: f64,
    pub amount1: f64,
    pub pool_id: String,
    pub token0_symbol: String,
    pub token1_symbol: String,
    pub fee_tier: u32,
    /// true if amount0 < 0 (trader received token0 from pool)
    pub is_buy_token0: bool,
}
