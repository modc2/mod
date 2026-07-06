use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
pub struct Health {
    pub status: &'static str,
    pub module: &'static str,
    pub sources: Vec<String>,
    pub destination: String,
}

#[derive(Debug, Serialize)]
pub struct Status {
    pub orders: usize,
    pub by_state: BTreeMap<String, usize>,
    pub fee_bps: u32,
    pub sources_supported: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct RatesView {
    pub ts: i64,
    pub usd: BTreeMap<String, f64>,
    pub pairs: BTreeMap<String, f64>,
    #[serde(default)]
    pub stale: bool,
}

#[derive(Debug, Deserialize)]
pub struct QuoteIn {
    pub from_token: String,
    pub amount: f64,
}

#[derive(Debug, Serialize)]
pub struct Quote {
    pub from: String,
    pub amount_in: f64,
    pub rate: f64,
    pub gross_tao: f64,
    pub fee_bps: u32,
    pub fee_tao: f64,
    pub tao_out: f64,
    pub rates_ts: i64,
    pub rates_stale: bool,
}

#[derive(Debug, Deserialize)]
pub struct SwapIn {
    pub from_token: String,
    pub amount: f64,
    pub source_address: String,
    pub destination_ss58: String,
    #[serde(default = "default_slippage")]
    pub slippage_bps: u32,
    /// Optional: stake the fee on a TAO/USDT direction prediction settling 24h
    /// after the order opens. Win → fee refunded + half of the price-move
    /// profit; lose → fee retained as normal.
    #[serde(default)]
    pub prediction: Option<PredictionIn>,
}
fn default_slippage() -> u32 { 100 }

#[derive(Debug, Deserialize)]
pub struct PredictionIn {
    /// "up" or "down" — direction TAO/USDT will move over the next 24h.
    pub direction: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Prediction {
    pub direction: String,
    pub reference_price: f64,
    pub fee_tao: f64,
    pub opened_at: i64,
    pub settles_at: i64,
    #[serde(default)]
    pub settled: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settled_price: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub settled_at: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delta_pct: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user_payout_tao: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub owner_share_tao: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub payout_tx: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct SettlePredictionIn {
    #[serde(default)]
    pub admin_key: String,
    /// Optional: record an on-chain tx that paid the user out in the same call.
    #[serde(default)]
    pub payout_tx: Option<String>,
    /// Force-settle even if `settles_at` hasn't been reached. Admin-only.
    #[serde(default)]
    pub force: bool,
}

#[derive(Debug, Serialize)]
pub struct DepositAddress {
    pub from: String,
    pub chain: String,
    pub deposit_address: String,
}

#[derive(Debug, Deserialize)]
pub struct ConfirmIn {
    pub source_tx: String,
}

#[derive(Debug, Deserialize)]
pub struct MarkPaidIn {
    pub delivery_tx: String,
    #[serde(default)]
    pub admin_key: String,
}

#[derive(Debug, Deserialize)]
pub struct CancelIn {
    #[serde(default = "default_cancel_reason")]
    pub reason: String,
}
fn default_cancel_reason() -> String { "user_cancelled".into() }

#[derive(Debug, Deserialize)]
pub struct OrdersQuery {
    pub source_address: Option<String>,
    #[serde(default = "default_limit")]
    pub limit: usize,
}
fn default_limit() -> usize { 50 }

#[derive(Debug, Deserialize)]
pub struct RatesQuery {
    #[serde(default)]
    pub refresh: bool,
}

#[derive(Debug, Deserialize)]
pub struct DepositQuery {
    pub from_token: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct HistoryEntry {
    pub ts: i64,
    pub state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tx: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Order {
    pub id: String,
    pub created: i64,
    pub from: String,
    pub amount_in: f64,
    pub source_address: String,
    pub destination_ss58: String,
    pub deposit_address: String,
    pub quoted_rate: f64,
    pub quoted_tao_out: f64,
    pub fee_bps: u32,
    pub slippage_bps: u32,
    pub state: String,
    pub source_tx: Option<String>,
    pub delivery_tx: Option<String>,
    pub history: Vec<HistoryEntry>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prediction: Option<Prediction>,
}
