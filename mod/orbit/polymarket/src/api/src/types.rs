use serde::{Deserialize, Serialize};

/// Serde default for stats whose absence means "unknown", not zero —
/// matches the `-1` sentinel `winRate` uses for undecided traders.
fn unknown_stat() -> f64 {
    -1.0
}

/// Per-market breakdown of a trader's activity within the analysis window.
/// Stored in memory cache only (`#[serde(skip)]` on parent) — used by
/// `apply_pagination` to recompute aggregate stats when a search/category
/// filter narrows the view to specific markets.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketMetric {
    pub title: String,
    pub volume: f64,
    pub buy_volume: f64,
    pub sell_volume: f64,
    pub pnl: f64,
    pub trades: u32,
    /// Settled positions in this market that returned more than they cost.
    /// Numerator of buy-accuracy. Sourced from `/closed-positions` (see
    /// `settled.rs`), not from observable exits — a losing position leaves no
    /// sell and no redeem, so counting exits drops losers only.
    pub wins: u32,
    /// Settled positions in this market — the market has finished deciding
    /// them, win or burn. Denominator of buy-accuracy.
    pub decided: u32,
    /// Per-closed-SELL fractional returns `(price − avgCost) / avgCost` in
    /// this market — lets `apply_pagination` recompute Sharpe scoped to the
    /// markets matching a search/category/topic query.
    pub returns: Vec<f64>,
    /// 12-bucket realized-PnL DELTAS in this market over the window — same
    /// bucketing as the trader-level `pnlCurve`. `apply_pagination` sums
    /// these element-wise across the markets matching a query and cum-sums
    /// the result into a query-scoped curve, instead of clearing the
    /// all-markets one. `#[serde(default)]` keeps older cached payloads
    /// loadable (they surface as empty → no scoped curve until next sync).
    #[serde(default)]
    pub curve: Vec<f64>,
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
    /// Sharpe ratio over the analysis window: mean of per-closed-trade
    /// fractional returns / their sample stdev (0 below 3 closed trades) —
    /// same `stats_from_returns` formula the live engine ranks copies with.
    /// Default score in the leaderboard UI. `#[serde(default)]` keeps older
    /// cached payloads loadable (they surface as 0 until the next sync).
    #[serde(default)]
    pub sharpe: f64,
    /// Average exit÷entry price ratio over the window's closed trades —
    /// `1 + mean per-closed-trade return`, so 1.0 = break-even and 1.15 means
    /// positions were exited at 15% above cost on average. A SCORE preset in
    /// the leaderboard UI. `-1` = no closed trades in the window (same
    /// "unknown" sentinel as `winRate`); the default keeps older cached
    /// payloads loadable.
    #[serde(rename = "exitEntry", default = "unknown_stat")]
    pub exit_entry: f64,
    /// How many positions the win rate is computed over — the denominator,
    /// carried so the UI never renders "100%" off four settled legs the same
    /// way it renders it off four hundred. `0` = nothing settled in the
    /// window, which is what `winRate: -1` means.
    #[serde(rename = "decidedPositions", default)]
    pub decided_positions: u32,
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
    /// Unix-seconds of this wallet's FIRST activity ever — not the first in
    /// the ranking window. It is what "this account is 6 days old" is made
    /// of, and the only honest answer to "why is the 30D backtest flat for
    /// 25 days": the trader did not exist for most of it.
    ///
    /// Deliberately window-independent. Deriving it from the enrichment pull
    /// would cap it at the window (the 7D board would say every trader is 7
    /// days old), so it comes from a separate `sortDirection=ASC&limit=1`
    /// call, cached forever — a first trade never moves. `None` = not
    /// resolved yet, and every filter that reads it fails OPEN, same rule as
    /// `lastTradeTs`: unknown history must not silently empty the board.
    #[serde(rename = "firstTradeTs", default, skip_serializing_if = "Option::is_none")]
    pub first_trade_ts: Option<u64>,
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
    /// Activity floor: drop traders with fewer than this many trades in the
    /// last 24h. Reads `trades24h` off the cached row — no re-aggregation.
    #[serde(rename = "minTrades24h")]
    pub min_trades_24h: Option<u32>,
    /// Recency floor: drop traders whose most recent trade is older than this
    /// many hours (a trader who went dark is not one you can copy). Reads
    /// `lastTradeTs` off the cached row, so it filters the WHOLE board from
    /// the warm cache instead of thinning one already-paginated page — which
    /// is what the console did while this lived client-side. 0/absent = off.
    #[serde(rename = "maxLastTradeHrs")]
    pub max_last_trade_hrs: Option<f64>,
    /// Track-record floor, in days: drop traders whose first-ever trade is
    /// more recent than this. The user picks the number — a 30D backtest of
    /// a 6-day-old wallet is 24 days of flat line and a "+24%" that rests on
    /// six days, so "only show me traders with at least N days behind them"
    /// is the filter that makes a long window mean something.
    ///
    /// Reads `firstTradeTs` off the cached row (no re-aggregation). Traders
    /// whose first trade hasn't been resolved yet are KEPT — see the field
    /// docs on `Trader::first_trade_ts`. 0/absent = off.
    #[serde(rename = "minHistoryDays")]
    pub min_history_days: Option<f64>,
    pub status: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct ProxyQuery {
    pub endpoint: Option<String>,
}
