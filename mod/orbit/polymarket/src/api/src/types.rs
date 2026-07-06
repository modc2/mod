use serde::{Deserialize, Serialize};

/// Per-market breakdown of a trader's activity within the analysis window.
/// Stored in memory cache only (`#[serde(skip)]` on parent) — used by
/// `apply_pagination` to recompute aggregate stats when a search/category
/// filter narrows the view to specific markets.
#[derive(Debug, Clone)]
pub struct MarketMetric {
    pub title: String,
    pub volume: f64,
    pub buy_volume: f64,
    pub sell_volume: f64,
    pub pnl: f64,
    pub trades: u32,
    pub wins: u32,
    pub sells: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Trader {
    pub address: String,
    pub volume: f64,
    #[serde(rename = "buyVolume")]
    pub buy_volume: f64,
    #[serde(rename = "sellVolume")]
    pub sell_volume: f64,
    pub pnl: f64,
    #[serde(rename = "winRate")]
    pub win_rate: f64,
    pub positions: u32,
    #[serde(rename = "marketTitles")]
    pub market_titles: Vec<String>,
    #[serde(rename = "recentTrades")]
    pub recent_trades: u32,
    /// Count of trades in the last 24h — surfaces "still active" traders
    /// vs. those who looked good in week 1 but went dark afterward.
    /// `#[serde(default)]` keeps older cached payloads loadable.
    #[serde(rename = "trades24h", default)]
    pub trades_24h: u32,
    /// Unix-seconds timestamp of this trader's most recent trade in the
    /// enriched window. Lets the leaderboard show "last trade 4m ago" so
    /// the user can tell whether a trader is firing now vs. went dormant
    /// mid-window. `Option` keeps older cached payloads loadable.
    #[serde(rename = "lastTradeTs", default, skip_serializing_if = "Option::is_none")]
    pub last_trade_ts: Option<u64>,
    #[serde(rename = "pnlCurve", skip_serializing_if = "Option::is_none")]
    pub pnl_curve: Option<Vec<f64>>,
    /// Per-market metrics — memory-only, not serialized to JSON / disk cache.
    #[serde(skip)]
    pub market_metrics: Option<Vec<MarketMetric>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AggPayload {
    pub count: usize,
    #[serde(rename = "candidatePool")]
    pub candidate_pool: usize,
    #[serde(rename = "daysWindow")]
    pub days_window: u32,
    #[serde(rename = "minTradesPerDay")]
    pub min_trades_per_day: f64,
    /// Wall-clock unix-seconds when this aggregate was last (re)computed from
    /// Polymarket source data. Distinct from the cache hit time the client
    /// sees — gives the user a true "data is N minutes old" reading.
    /// `#[serde(default)]` keeps old disk payloads loadable.
    #[serde(rename = "syncedAt", default)]
    pub synced_at: i64,
    pub traders: Vec<Trader>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum StreamEvent {
    #[serde(rename = "progress")]
    Progress {
        phase: String,
        done: usize,
        total: usize,
        #[serde(skip_serializing_if = "Option::is_none")]
        kept: Option<usize>,
    },
    #[serde(rename = "partial")]
    Partial { traders: Vec<Trader> },
    #[serde(rename = "result")]
    Result {
        source: String,
        count: usize,
        #[serde(rename = "candidatePool")]
        candidate_pool: usize,
        #[serde(rename = "daysWindow")]
        days_window: u32,
        #[serde(rename = "minTradesPerDay")]
        min_trades_per_day: f64,
        traders: Vec<Trader>,
    },
    #[serde(rename = "error")]
    Error { message: String },
}

#[derive(Debug, Deserialize)]
pub struct ActiveTradersQuery {
    pub days: Option<u32>,
    #[serde(rename = "minPerDay")]
    pub min_per_day: Option<f64>,
    pub pool: Option<u32>,
    pub stream: Option<String>,
    pub paged: Option<String>,
    /// When "1", bypass the agg + per-trader trade caches and re-fetch from
    /// Polymarket. Used by the SYNC button so the user can force a refresh
    /// without waiting for the 60s warmup cycle.
    pub force: Option<String>,
    pub sort: Option<String>,
    pub order: Option<String>,
    pub page: Option<u32>,
    #[serde(rename = "pageSize")]
    pub page_size: Option<u32>,
    pub search: Option<String>,
    pub category: Option<String>,
    /// Free-text market-topic filter (e.g. "bitcoin", "price of bitcoin").
    /// Narrows traders to those active in matching markets and recomputes their
    /// aggregate stats from only those markets. Finer than `category`'s fixed
    /// keyword buckets and, unlike `search`, never matches the wallet address.
    #[serde(rename = "marketQuery")]
    pub market_query: Option<String>,
    #[serde(rename = "minVolume")]
    pub min_volume: Option<f64>,
    #[serde(rename = "minPnl")]
    pub min_pnl: Option<f64>,
    #[serde(rename = "minTrades")]
    pub min_trades: Option<u32>,
    #[serde(rename = "minBuyVolume")]
    pub min_buy_volume: Option<f64>,
    #[serde(rename = "minSellVolume")]
    pub min_sell_volume: Option<f64>,
    pub status: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ProxyQuery {
    pub endpoint: Option<String>,
}
