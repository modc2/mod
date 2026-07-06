use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SwapEvent {
    pub chain_id: u64,
    pub tx_hash: String,
    pub block_number: u64,
    pub timestamp: u64,
    pub trader: String,
    pub router: String,
    pub token_in: String,
    pub token_out: String,
    pub amount_in: String,
    pub amount_out: String,
    pub pool: String,
    pub dex: String, // "uniswap_v3", "uniswap_v2", "sushiswap", etc.
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraderScore {
    pub address: String,
    pub pnl_1h: f64,
    pub pnl_24h: f64,
    pub pnl_7d: f64,
    pub win_rate: f64,
    pub trade_count: u64,
    pub avg_trade_size_usd: f64,
    pub last_trade: u64,
    pub score: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeRecord {
    pub timestamp: u64,
    pub chain_id: u64,
    pub token_in: String,
    pub token_out: String,
    pub amount_in: String,
    pub amount_out: String,
    pub tx_hash: String,
    pub pnl_usd: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeOrder {
    pub chain_id: u64,
    pub router: String,
    pub token_in: String,
    pub token_out: String,
    pub amount_in: String,
    pub min_amount_out: String,
    pub call_data: String, // hex-encoded
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChainConfig {
    pub chain_id: u64,
    pub name: String,
    pub enabled: bool,
    pub rpc_urls: Vec<String>,
    pub routers: Vec<RouterConfig>,
    pub proxy_address: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RouterConfig {
    pub address: String,
    pub name: String,
    pub dex_type: DexType,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum DexType {
    UniswapV2,
    UniswapV3,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineConfig {
    pub chains: Vec<ChainConfig>,
    pub wallets: Vec<String>,
    pub max_trade_usd: f64,
    pub slippage_bps: u64,
    pub position_pct: f64,
    pub daily_limit_usd: f64,
    pub auto_discover: bool,
    pub min_score: f64,
    pub private_key: Option<String>,
    pub poll_interval_ms: u64,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            chains: Vec::new(),
            wallets: Vec::new(),
            max_trade_usd: 100.0,
            slippage_bps: 50,
            position_pct: 10.0,
            daily_limit_usd: 1000.0,
            auto_discover: false,
            min_score: 70.0,
            private_key: None,
            poll_interval_ms: 4000,
        }
    }
}
