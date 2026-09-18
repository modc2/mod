use serde::{Deserialize, Serialize};

/// Processed swap for internal use.
///
/// Amounts are decimal-adjusted token units signed from the pool's
/// perspective: positive flowed into the pool (trader sold), negative flowed
/// out (trader bought).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Swap {
    pub id: String,
    pub timestamp: i64,
    pub block: u64,
    /// The contract that called the pool — usually a router, not a person.
    pub sender: String,
    /// The address the output tokens went to. The closest thing a Swap log
    /// carries to "who made this trade".
    pub recipient: String,
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
