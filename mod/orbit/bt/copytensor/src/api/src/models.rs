use serde::{Deserialize, Serialize};

// ── request types ───────────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct WatchRequest {
    pub ss58: String,
    pub label: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct CopyRequest {
    pub target_ss58: String,
    pub our_hotkey: String,
    pub label: Option<String>,
    pub max_tao_per_tx: Option<f64>,
    pub daily_limit_tao: Option<f64>,
    pub min_balance_tao: Option<f64>,
    pub subnet_allowlist: Option<Vec<u16>>,
    pub subnet_denylist: Option<Vec<u16>>,
    pub rebalance_threshold_pct: Option<f64>,
    pub poll_interval_sec: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub struct WalletSetRequest {
    pub name: Option<String>,
    pub hotkey: Option<String>,
    pub mnemonic: Option<String>,
    pub seed_hex: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ConfigSetRequest {
    pub key: String,
    pub value: serde_json::Value,
}

#[derive(Debug, Deserialize)]
pub struct LeaderboardQuery {
    pub days: Option<u32>,
    pub top: Option<usize>,
    pub min_subnets: Option<usize>,
}

#[derive(Debug, Deserialize)]
pub struct AccountQuery {
    pub days: Option<u32>,
}

#[derive(Debug, Deserialize)]
pub struct TradesQuery {
    pub copy_id: Option<String>,
    pub limit: Option<usize>,
}

// ── response types ──────────────────────────────────────────────

#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub connected: bool,
    pub network: String,
    pub block: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub endpoint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct StatusResponse {
    pub running: bool,
    pub network: String,
    pub block_height: u64,
    pub tracked_accounts: usize,
    pub active_copies: usize,
    pub wallet_set: bool,
}

#[derive(Debug, Serialize, Clone)]
pub struct SubnetInfo {
    pub netuid: u16,
    pub name: String,
    pub alpha_price_tao: f64,
    pub total_stake_tao: f64,
    pub tempo: u16,
    pub emission: f64,
}

#[derive(Debug, Serialize, Clone)]
pub struct Allocation {
    pub netuid: u16,
    pub subnet_name: String,
    pub hotkey: String,
    pub alpha_amount: f64,
    pub alpha_price_tao: f64,
    pub value_tao: f64,
    pub pct_of_total: f64,
}

#[derive(Debug, Serialize)]
pub struct AccountResponse {
    pub ss58: String,
    pub total_stake_tao: f64,
    pub allocations: Vec<Allocation>,
    pub pnl_tao: f64,
    pub pnl_pct: f64,
    pub days: u32,
}

#[derive(Debug, Serialize, Clone)]
pub struct SubnetPnl {
    pub netuid: u16,
    pub subnet_name: String,
    pub alpha_start: f64,
    pub alpha_end: f64,
    pub price_start_tao: f64,
    pub price_end_tao: f64,
    pub value_start_tao: f64,
    pub value_end_tao: f64,
    pub pnl_tao: f64,
    pub pnl_pct: f64,
}

#[derive(Debug, Serialize)]
pub struct PnlResponse {
    pub ss58: String,
    pub days: u32,
    pub block_start: u64,
    pub block_end: u64,
    pub start_value_tao: f64,
    pub end_value_tao: f64,
    pub pnl_tao: f64,
    pub pnl_pct: f64,
    pub by_subnet: Vec<SubnetPnl>,
}

#[derive(Debug, Serialize, Clone)]
pub struct LeaderboardEntry {
    pub ss58: String,
    pub label: Option<String>,
    pub total_stake_tao: f64,
    pub pnl_tao: f64,
    pub pnl_pct: f64,
    pub num_subnets: usize,
    pub top_subnet: Option<u16>,
    pub top_subnet_pnl: f64,
}

#[derive(Debug, Serialize, Clone)]
pub struct TargetTraderInfo {
    pub ss58: String,
    pub label: Option<String>,
    pub total_stake_tao: f64,
    pub num_subnets: usize,
    pub pnl_tao: f64,
    pub pnl_pct: f64,
    pub pnl_days: u32,
    pub top_allocations: Vec<Allocation>,
}

#[derive(Debug, Serialize)]
pub struct TraderResponse {
    pub ss58: String,
    pub label: Option<String>,
    pub total_stake_tao: f64,
    pub num_subnets: usize,
    pub days: u32,
    pub pnl: serde_json::Value,
    pub allocations: Vec<Allocation>,
}

#[derive(Debug, Serialize, Clone)]
pub struct CopyInfo {
    pub id: String,
    pub target_ss58: String,
    pub label: Option<String>,
    pub status: String,
    pub config: serde_json::Value,
    pub last_sync_block: Option<u64>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_info: Option<TargetTraderInfo>,
}

#[derive(Debug, Serialize, Clone)]
pub struct TradeInfo {
    pub id: String,
    pub copy_id: String,
    pub block: Option<u64>,
    pub timestamp: String,
    pub action: String,
    pub netuid: u16,
    pub amount_tao: f64,
    pub tx_hash: Option<String>,
    pub status: String,
    pub error: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct WatchResponse {
    pub watched: String,
    pub total: usize,
}

#[derive(Debug, Serialize)]
pub struct UnwatchResponse {
    pub unwatched: String,
    pub total: usize,
}

#[derive(Debug, Serialize)]
pub struct AccountWatch {
    pub ss58: String,
    pub label: Option<String>,
    pub added_at: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct SyncResult {
    pub synced: bool,
    pub trades: Vec<TradeExecResult>,
}

#[derive(Debug, Serialize)]
pub struct TradeExecResult {
    pub action: String,
    pub netuid: u16,
    pub amount_tao: f64,
    pub status: String,
    pub error: Option<String>,
}

// ── internal types ──────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct AlphaPosition {
    pub netuid: u16,
    pub hotkey: String,
    pub alpha_amount: f64,
    pub alpha_price_tao: f64,
    pub value_tao: f64,
}

#[derive(Debug, Clone)]
pub struct AccountPositions {
    pub ss58: String,
    pub block: u64,
    pub total_value_tao: f64,
    pub positions: Vec<AlphaPosition>,
}

#[derive(Debug, Clone)]
pub struct Delta {
    pub netuid: u16,
    pub action: String, // "stake" or "unstake"
    pub amount_tao: f64,
    pub pct_change: f64,
}
