//! Long-running copy engine that lives inside the Rust API process.
//!
//! Each live session is a tokio task keyed by EOA. The task polls each
//! watched trader's `/activity` from Polymarket, pushes observed trades
//! into a ring buffer, and stamps `last_cycle_at` / `cycle_count` so the
//! browser (or any HTTP client) can observe progress via `GET /live/status`.
//!
//! **Execution.** Each cycle, every newly-observed leader BUY becomes a
//! proportionally-sized mirror order placed through the backend signer
//! (`order_place::place_order`) — no browser wallet required. Sizing mirrors
//! the frontend `copyEngine.ts` (capital × weight ÷ trader-volume, clamped up
//! to the user's `minOrderSize` floor). Placement is gated by
//! `EngineConfig.auto_execute`: while it's `false` (the default) the engine
//! runs DRY RUN — it computes and logs every order it *would* place but sends
//! nothing to the CLOB, so sizing can be verified before risking real USDC.
//!
//! **Persistence**: every cycle writes the latest state to disk, and the
//! session config is written once on start. On API boot the registry
//! scans for `<eoa>.config.json` files and auto-resumes any session whose
//! config is present (the engine "always runs in the background until the
//! user stops it" — explicit STOP deletes the config file).

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{Context, Result};
use dashmap::DashMap;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::task::JoinHandle;

use crate::order_place::{
    place_order, ClobCreds, OrderSide, OrderTimeInForce, PlaceOrderArgs, PlaceOrderRequest,
};
use crate::signer::SignerStore;

const PERSIST_DIR_NAME: &str = "polymarket-live-engine";
const DATA_API: &str = "https://data-api.polymarket.com";
const GAMMA_API: &str = "https://gamma-api.polymarket.com";
/// CLOB REST base — order book lookups for marketable rebalance-exit pricing.
const CLOB_API: &str = "https://clob.polymarket.com";

// ─── Internal bounds (not strategy — pure memory/disk caps) ─────────────
/// Cap on the copied-id dedup set kept in state (bounded disk + memory).
const COPIED_IDS_CAP: usize = 2000;
/// Max trades held in the observed-trades ring buffer. Bounded so a long
/// session doesn't grow state unbounded (memory + per-cycle disk writes).
const OBSERVED_CAP: usize = 500;
/// Max log entries kept. Older entries fall off.
const LOG_CAP: usize = 1000;

// ─── Strat-supplied tunable defaults ────────────────────────────────────
// Every knob below is owner-configurable through the strat (it travels on
// `EngineConfig`, which the LIVE tab builds from the selected strat). These
// `default_*` fns only supply a value when the strat omits the field, so old
// configs and partial payloads keep working — nothing is hardcoded into the
// engine's behavior.
//
/// Owner's default per-order floor in USDC ($5). Sub-floor proportional
/// mirrors are clamped UP to this so they fill. Polymarket's own hard CLOB
/// floor ($1 / 5 shares) is enforced separately in `clob_min_notional`.
fn default_min_order_size() -> f64 { 5.0 }
/// Polymarket's 5-share minimum per order (sizing floor default).
fn default_min_shares() -> f64 { 5.0 }
/// Limit-price widening toward the fillable side — strat.ts `slippageBps`
/// default (300 = 3¢ on a $1 market).
fn default_max_slippage_bps() -> u32 { 300 }
/// Proportional-sizing lookback window (days) for a trader's volume.
fn default_backtest_days() -> u32 { 3 }
/// Mirror BUYs placed per cycle — the strat's `maxPerCycle` top-N budget.
/// Matches the backtest's per-bucket rank gate and the Strat class default
/// (3), so a live cycle places at most what the sim would keep. Exits
/// (leader-sell mirrors, stop-losses, rebalance sells) don't count against
/// it — an exit signal is never deferred behind a busy buy cycle.
fn default_max_orders_per_cycle() -> usize { 3 }
/// Cap on concurrent open positions (distinct tokens held). A mirror BUY that
/// would open a NEW token while this many are already held is skipped; topping
/// up an existing hold doesn't raise concurrency and always passes.
fn default_max_open_positions() -> usize { 10 }
/// Spacing between successive order placements within a cycle (ms).
fn default_order_delay_ms() -> u64 { 300 }
/// Spacing between successive per-trader `/activity` fetches inside one cycle
/// (ms). Spreads requests so we don't burst past Cloudflare's per-second limit.
fn default_inter_request_delay_ms() -> u64 { 400 }
/// Floor on the cycle interval (ms). Polymarket's data-api sits behind
/// Cloudflare, which 429s once sustained rate crosses a few req/s; a too-small
/// interval × N traders gets rate-limited into zero observations. 30s with the
/// 400ms inter-request spacing keeps a 10-trader watchlist near ~0.3 req/s
/// sustained — well under the limit — while halving mirror lag vs. the old 60s
/// floor. The owner can lower this through the strat, but the default keeps a
/// stale fast config (the persisted 5s era) safe.
fn default_min_interval_ms() -> u64 { 30_000 }
/// Default entry-probability floor: a BUY mirror below this price (= implied
/// probability) is skipped when the strat expresses NO explicit price band.
/// "Likely to win" by default — sub-60¢ longshot flow (the movoaev8 leak) is
/// only copied when a strat opts in with its own `minPrice`/`maxPrice`.
/// Mirror of DEFAULT_MIN_ENTRY_PRICE in app/lib/tradeFilters.ts.
const DEFAULT_MIN_ENTRY_PRICE: f64 = 0.60;
/// Capital-aware rebalancing: when free capital can't fund a higher-score
/// candidate, sell the lowest-score held position(s) to make room. ON by
/// default so a running strat always holds its top-scoring set.
fn default_rebalance_enabled() -> bool { true }
/// A candidate must out-score a held position by at least this fraction before
/// the engine will sell the held one to fund it — covers round-trip spread/fees
/// so it doesn't churn on a marginal score edge. 0.20 = "20% better or skip".
fn default_rebalance_margin_pct() -> f64 { 0.20 }
/// Default per-position stop-loss fraction when the field is omitted. 0.75 =
/// defend three quarters of entry (exit once the bid decays to 75% of the
/// entry price) — the same default the console sends (`strat.stopLoss ??
/// DEFAULT_STOP_LOSS`) and the backtest sim applies (`stopLossPct` 25), so a
/// bare API start behaves like a UI start. An explicit 0 (or null) still
/// means OFF. Pinned cross-language by parity.fixture.json `defaults`.
fn default_stop_loss() -> Option<f64> { Some(0.75) }
/// Default take-profit bid level when the field is omitted. 0.99 is the
/// CLOB's TOP TICK — the book never prints 1.00, so a bid at 99¢ *is* "the
/// market ran to 100%". Liquidating there frees the capital immediately
/// instead of holding a decided market for hours until resolution +
/// auto-redeem. An explicit 0 (or null) means OFF. Pinned cross-language by
/// parity.fixture.json `defaults`.
fn default_take_profit() -> Option<f64> { Some(0.99) }
/// Sharpe/ROI scoring window (days). Matches the frontend's fixed 30d Sharpe
/// window (distinct from `backtest_days`, which sizes the copy ratio).
const SHARPE_WINDOW_DAYS: i64 = 30;
/// How long after the engine places a SELL for a token before the reconciler
/// may re-adopt it from the data-api. Fills usually settle out of the
/// positions feed within a minute; 10 minutes comfortably covers slow
/// settles without leaving a genuinely-unfilled position unprotected forever.
const EXIT_READOPT_COOLDOWN_MS: i64 = 10 * 60_000;

// ─── Types ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineStatus {
    Stopped,
    Starting,
    Running,
    Paused,
    Error,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TraderEntry {
    pub address: String,
    pub weight: f64,
    #[serde(default = "default_true")]
    pub enabled: bool,
}
fn default_true() -> bool { true }

/// Auto-watchlist: instead of a hand-picked trader list, the engine
/// periodically re-derives "the top traders who actually traded in the last
/// N hours" per topic tag and swaps them into the session's watchlist.
/// Discovery walks gamma's highest-24h-volume open events for each
/// `tag_slug`, pulls each market's recent fills from the data-api, ranks
/// wallets by in-window notional (a trader qualifies with ≥1 fill in the
/// window), and weights the merged list proportionally to that notional.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AutoTradersConfig {
    /// gamma tag slugs to discover markets from (e.g. ["bitcoin", "weather"]).
    pub tags: Vec<String>,
    /// Activity window (hours) — a trader must have ≥1 fill within it.
    #[serde(default = "default_auto_hours")]
    pub hours: u32,
    /// Traders kept per tag (merged across tags, notionals summed).
    #[serde(rename = "topPerTag", default = "default_auto_top_per_tag")]
    pub top_per_tag: usize,
    /// How often the watchlist is re-derived.
    #[serde(rename = "refreshMinutes", default = "default_auto_refresh_minutes")]
    pub refresh_minutes: u64,
    /// Markets sampled per tag (bounds discovery HTTP fan-out).
    #[serde(rename = "marketsPerTag", default = "default_auto_markets_per_tag")]
    pub markets_per_tag: usize,
}
fn default_auto_hours() -> u32 { 24 }
fn default_auto_top_per_tag() -> usize { 5 }
fn default_auto_refresh_minutes() -> u64 { 60 }
fn default_auto_markets_per_tag() -> usize { 8 }

/// Price-momentum ORIGINATION — the general, watchlist-free strategy path.
/// Mirror of `MomentumParams` in app/lib/types.ts and the param-driven
/// `Strat.proposeMomentum` in app/lib/strats/strat.ts: instead of copying
/// traders, the engine tracks candidate markets' own price history over time
/// and buys the outcome whose odds are rising (50¢ → 60¢), exiting when the
/// move reverses. Setting `EngineConfig.momentum` (even `{}`) turns it on;
/// an empty `traders` list is then perfectly valid — the session runs on
/// market data alone.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MomentumParams {
    /// Market search query for candidate markets. Default: the strat's
    /// `marketQuery`, else "bitcoin".
    #[serde(default)]
    pub query: Option<String>,
    /// Window (minutes) the rise is measured over. Default 60.
    #[serde(rename = "lookbackMinutes", default)]
    pub lookback_minutes: Option<u64>,
    /// Minimum rise in CENTS of probability over the lookback before an
    /// entry fires. Default 5.
    #[serde(rename = "minRiseCents", default)]
    pub min_rise_cents: Option<f64>,
    /// Exit: sell a held outcome once its price FALLS this many cents over
    /// the lookback. Default = `min_rise_cents`.
    #[serde(rename = "exitDropCents", default)]
    pub exit_drop_cents: Option<f64>,
    /// Entry price band — don't chase near-resolved (or dead) markets.
    /// Defaults 0.5 / 0.85 (likely-to-win side, same bias as the copy path's
    /// DEFAULT_MIN_ENTRY_PRICE); explicit `minPrice` opts into cheaper entries.
    #[serde(rename = "minPrice", default)]
    pub min_price: Option<f64>,
    #[serde(rename = "maxPrice", default)]
    pub max_price: Option<f64>,
    /// Max simultaneous open positions momentum may hold. Default 5.
    #[serde(rename = "maxPositions", default)]
    pub max_positions: Option<usize>,
    /// How many top-volume matching markets to track per cycle. Default 12.
    #[serde(rename = "maxMarkets", default)]
    pub max_markets: Option<usize>,
    /// Skip markets resolving sooner than this (minutes). Sub-hour Up/Down
    /// markets are HFT-bot turf where a polling strat structurally loses.
    /// Default 90.
    #[serde(rename = "minMinutesToClose", default)]
    pub min_minutes_to_close: Option<u64>,
    /// CANDLE MODE (opt-in): instead of searching for candidate markets,
    /// track the ONE live market of a recurring sub-hour series (e.g. BTC
    /// 5-minute Up/Down) by its deterministic slug
    /// (`<slugPrefix>-<candle start unix seconds>`), at 1-minute price
    /// fidelity plus a near-live midpoint. It targets exactly the sub-hour
    /// lane `minMinutesToClose` exists to avoid, so a strat using it must
    /// also set `minMinutesToClose` low (e.g. 1) or no entry can ever fire.
    #[serde(default)]
    pub candles: Option<CandleParams>,
}

/// See `MomentumParams::candles`. Mirror of `MomentumParams["candles"]`
/// in app/lib/types.ts.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CandleParams {
    /// Series slug prefix. Default "btc-updown-5m".
    #[serde(rename = "slugPrefix", default)]
    pub slug_prefix: Option<String>,
    /// Candle length in minutes. Default 5.
    #[serde(rename = "periodMinutes", default)]
    pub period_minutes: Option<u64>,
}

/// What the engine needs to run a session. Persisted to disk verbatim.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineConfig {
    /// User's EOA — used as the registry key and the on-disk filename.
    pub eoa: String,
    /// Saved strat id (echoed back to the client; not used by the loop).
    #[serde(rename = "strategyId")]
    pub strategy_id: String,
    /// Proxy (Safe / POLY_PROXY) address where funds live.
    pub address: String,
    pub traders: Vec<TraderEntry>,
    pub capital: f64,
    #[serde(rename = "intervalMs")]
    pub interval_ms: u64,
    // ── Strat-supplied tunables (the LIVE tab fills these from the strat) ──
    /// Owner's minimum order size in USDC (strat `minTrade`). A mirror whose
    /// proportional notional lands below this — but whose leader trade still
    /// clears the CLOB floor — is clamped UP to it so it fills instead of being
    /// skipped. Defaults to $5 when omitted (CLOB's own hard floor is $1).
    #[serde(rename = "minOrderSize", default = "default_min_order_size")]
    pub min_order_size: f64,
    /// Owner's per-order ceiling in USDC (strat `maxTrade`). `None` ⇒ no cap.
    #[serde(rename = "maxOrderSize", default)]
    pub max_order_size: Option<f64>,
    /// Minimum shares per order used in the CLOB sizing floor (strat-supplied;
    /// defaults to Polymarket's 5-share minimum).
    #[serde(rename = "minShares", default = "default_min_shares")]
    pub min_shares: f64,
    /// Limit-price widening in basis points toward the fillable side (BUY up,
    /// SELL down) so mirrors don't sit unfilled behind the market. Same knob
    /// and 300 default as strat.ts `slippageBps` — omitting it must not fall
    /// back to 0 (leader-exact quotes that routinely miss the book).
    #[serde(rename = "maxSlippageBps", default = "default_max_slippage_bps")]
    pub max_slippage_bps: u32,
    /// Proportional-sizing lookback window (days) — a trader's recent volume
    /// over this window sets the copy ratio (strat `backtestDays`).
    #[serde(rename = "backtestDays", default = "default_backtest_days")]
    pub backtest_days: u32,
    /// Max mirror orders placed per cycle before the rest defer (strat
    /// `maxPerCycle`). Caps fan-out so one bursty cycle can't fire dozens of
    /// fills at once.
    #[serde(rename = "maxOrdersPerCycle", default = "default_max_orders_per_cycle")]
    pub max_orders_per_cycle: usize,
    /// Cap on concurrent open positions (strat `maxOpenPositions`). A mirror
    /// BUY that would open a position in a NEW token while this many are
    /// already held is skipped; adds to an already-held token still go
    /// through. Rebalance SELLs free slots naturally.
    #[serde(rename = "maxOpenPositions", default = "default_max_open_positions")]
    pub max_open_positions: usize,
    /// Spacing between successive order placements within a cycle (ms).
    #[serde(rename = "orderDelayMs", default = "default_order_delay_ms")]
    pub order_delay_ms: u64,
    /// Spacing between successive per-trader activity fetches (ms) — rate-limit
    /// protection the owner can tune through the strat.
    #[serde(rename = "interRequestDelayMs", default = "default_inter_request_delay_ms")]
    pub inter_request_delay_ms: u64,
    /// Floor the effective poll interval is clamped up to (ms). Guards against a
    /// too-fast cadence getting data-api 429s; owner-overridable via the strat.
    #[serde(rename = "minIntervalMs", default = "default_min_interval_ms")]
    pub min_interval_ms: u64,
    /// Master switch for real order placement. `false` (default) = DRY RUN:
    /// the engine computes and logs every mirror it *would* place but sends
    /// nothing to the CLOB, so the user can verify sizing before risking USDC.
    /// `true` = place real orders via the backend signer.
    #[serde(rename = "autoExecute", default)]
    pub auto_execute: bool,
    /// Auto-redeem settled winnings. A resolved market has no order book, so
    /// its proceeds sit stranded as CTF tokens until redeemPositions runs —
    /// when `true` (default) the loop periodically redeems them back into the
    /// trading balance automatically. Independent of `autoExecute`: redeeming
    /// carries no market risk (it only converts already-won tokens to cash),
    /// and the 6h scheduled pass in main.rs already redeems unconditionally.
    #[serde(rename = "autoRedeem", default = "default_true")]
    pub auto_redeem: bool,
    /// Capital-aware rebalancing master switch. When `true` (default), a
    /// higher-score candidate that doesn't fit in free capital triggers a SELL
    /// of the lowest-score held position(s) to fund it (see `execute_mirrors`).
    /// When `false`, the engine only ever buys with already-free capital.
    #[serde(rename = "rebalanceEnabled", default = "default_rebalance_enabled")]
    pub rebalance_enabled: bool,
    /// Minimum fractional score edge a candidate needs over a held position to
    /// justify selling that position for it (anti-churn margin).
    #[serde(rename = "rebalanceMarginPct", default = "default_rebalance_margin_pct")]
    pub rebalance_margin_pct: f64,
    /// Per-position stop-loss: the fraction of avg entry price to defend
    /// (strat `stopLoss`, 0–1). e.g. 0.5 ⇒ the position is sold at the book
    /// bid once that bid decays to ≤ half its entry price — caps a market
    /// that's trending toward 0 at a known loss instead of riding it down.
    /// Resolved markets (no book) are untouched; auto-redeem is the exit
    /// path there. Omitted ⇒ 0.75 (console/backtest default); explicit 0 or
    /// null ⇒ no stop-loss.
    #[serde(rename = "stopLoss", default = "default_stop_loss")]
    pub stop_loss: Option<f64>,
    /// Per-position take-profit: the ABSOLUTE bid level (0–1) at which a held
    /// position is fully liquidated at the book bid. A market whose price has
    /// run to the top tick is decided — there is ≤1¢ left to earn and the
    /// capital is dead until resolution, so the engine sells rather than
    /// waits for auto-redeem. Values above 0.99 are clamped to 0.99 (the
    /// book's top tick — a 1.0 ask never prints, so an unclamped 1.0 could
    /// never fire). Omitted ⇒ 0.99; explicit 0 or null ⇒ off.
    #[serde(rename = "takeProfit", default = "default_take_profit")]
    pub take_profit: Option<f64>,
    /// Free-text market-topic filter (e.g. "price of bitcoin"). When set, the
    /// engine only mirrors leader BUYs whose market title matches the query —
    /// keeps a strat focused instead of copying every fill a watched trader
    /// makes. Trades in non-matching markets are still observed (visible in the
    /// log/rail) but never produce a mirror order. Empty/None ⇒ all markets.
    #[serde(rename = "marketQuery", default)]
    pub market_query: Option<String>,
    /// Semantic per-trade gate (side / leader price band / leader notional
    /// band / market category) — the same `tradeFilters` the strat's backtest
    /// applies through `Strat.shouldMirror`. AND-ed with `market_query`; a
    /// filtered trade is still observed (rail/log) but never mirrored. The
    /// frontend has forwarded this on `/live/start` all along — implementing
    /// it here closes a backtest→live gap where filtered strats traded the
    /// unfiltered flow.
    #[serde(rename = "tradeFilters", default)]
    pub trade_filters: Option<TradeFilters>,
    /// Auto-watchlist. When set, `traders` is engine-managed: re-derived on
    /// start and every `refreshMinutes` from each tag's most active recent
    /// traders, then persisted — any hand-edited list is overwritten on the
    /// next refresh. `None` ⇒ the hand-picked `traders` list is used as-is.
    #[serde(rename = "autoTraders", default)]
    pub auto_traders: Option<AutoTradersConfig>,
    /// Price-momentum origination (see `MomentumParams`). When set, the
    /// engine tracks candidate markets' price history each cycle and
    /// originates entries/exits from the price moves themselves — no
    /// watchlist required. Composable with mirroring: a session may copy
    /// traders AND ride momentum, or do either alone.
    #[serde(default)]
    pub momentum: Option<MomentumParams>,
}

/// Mirror of `TradeFilters` in app/lib/types.ts, applied by
/// `trade_passes_filters` exactly like app/lib/tradeFilters.ts
/// `tradeMatchesFilters` — keep the three in sync. Every set dimension is
/// AND-ed; an unset dimension passes everything.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TradeFilters {
    /// "buy" | "sell" | "both"/None (no side restriction).
    #[serde(default)]
    pub sides: Option<String>,
    /// Leader fill-price band (0–1 probability).
    #[serde(rename = "minPrice", default)]
    pub min_price: Option<f64>,
    #[serde(rename = "maxPrice", default)]
    pub max_price: Option<f64>,
    /// Leader trade USD notional band (price × size).
    #[serde(rename = "minNotional", default)]
    pub min_notional: Option<f64>,
    #[serde(rename = "maxNotional", default)]
    pub max_notional: Option<f64>,
    /// Category slugs the market title must match at least ONE of.
    #[serde(default)]
    pub categories: Option<Vec<String>>,
}

/// Apply the semantic per-trade gate — mirror of `tradeMatchesFilters` in
/// app/lib/tradeFilters.ts. Returns true ⇒ this trade may be mirrored.
///
/// When the strat sets NO explicit price band (neither `minPrice` nor
/// `maxPrice`), BUYs default to the `DEFAULT_MIN_ENTRY_PRICE` favorites-only
/// floor — likely-to-win entries only. An explicit band (even `minPrice: 0`)
/// is the opt-out; SELLs are never floored (exits must always clear).
fn trade_passes_filters(t: &ObservedTrade, filters: &Option<TradeFilters>) -> bool {
    let default_filters = TradeFilters::default();
    let f = filters.as_ref().unwrap_or(&default_filters);
    match f.sides.as_deref() {
        Some("buy") if t.side != "BUY" => return false,
        Some("sell") if t.side != "SELL" => return false,
        _ => {}
    }
    let no_price_band = f.min_price.is_none() && f.max_price.is_none();
    let effective_min = f.min_price.or_else(|| {
        (no_price_band && t.side == "BUY").then_some(DEFAULT_MIN_ENTRY_PRICE)
    });
    if let Some(min) = effective_min {
        if t.price < min { return false; }
    }
    if let Some(max) = f.max_price {
        if t.price > max { return false; }
    }
    let notional = if t.notional > 0.0 { t.notional } else { t.price * t.size };
    if let Some(min) = f.min_notional {
        if notional < min { return false; }
    }
    if let Some(max) = f.max_notional {
        if notional > max { return false; }
    }
    if let Some(cats) = &f.categories {
        if !cats.is_empty()
            && !cats.iter().any(|c| crate::categories::title_in_category(&t.market, c))
        {
            return false;
        }
    }
    true
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObservedTrade {
    pub id: String,
    pub timestamp: i64,
    pub trader: String,
    pub market: String,
    #[serde(rename = "conditionId")]
    pub condition_id: String,
    pub side: String, // "BUY" | "SELL"
    pub size: f64,
    pub price: f64,
    pub notional: f64,
    /// CLOB outcome-token id (the `asset` field from the data-api activity
    /// item) — the exact token to trade, so no gamma token-id lookup is
    /// needed. `#[serde(default)]` keeps old persisted state deserializable.
    #[serde(rename = "tokenId", default)]
    pub token_id: String,
    /// Outcome label the leader traded (e.g. "Yes"/"No"/team name). Mirrored
    /// verbatim — the token id already encodes the branch.
    #[serde(default)]
    pub outcome: String,
    /// Rank score the engine assigned this trade when observed:
    /// `P(success) × trader 30d ROI × rawMirrorNotional` (matches the
    /// frontend `Strat.scoreCandidate`). 0 when the trader has no in-window
    /// closed-trade sample. The frontend live rail reads this as `ot.score`.
    #[serde(default)]
    pub score: f64,
    /// Probability-of-success the playbook priced this trade at — the
    /// trader's Laplace-smoothed 30d win rate (0.5 = coin-flip prior).
    /// Stamped on observed BUYs; the default keeps old persisted state
    /// deserializable at the neutral prior.
    #[serde(rename = "successProb", default = "default_success_prob")]
    pub success_prob: f64,
}
fn default_success_prob() -> f64 { 0.5 }

/// An open mirror position the engine is holding. Tracked so capital-aware
/// rebalancing can sell the lowest-score holdings to fund better candidates.
/// Keyed by `token_id` in `EngineState.positions`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OpenPosition {
    #[serde(rename = "tokenId")]
    pub token_id: String,
    #[serde(rename = "conditionId", default)]
    pub condition_id: String,
    #[serde(default)]
    pub market: String,
    /// Shares currently held (the size we bought; reconciled against on-chain).
    pub size: f64,
    /// Average entry price we paid (USDC/share). Cost basis = size × entry_price.
    #[serde(rename = "entryPrice")]
    pub entry_price: f64,
    /// The EP score FROZEN at entry — the value a candidate must beat (by the
    /// rebalance margin) before this position is sold. Never recomputed.
    #[serde(rename = "entryScore")]
    pub entry_score: f64,
    #[serde(rename = "openedAt")]
    pub opened_at: i64,
    /// Strat that opened this position (`EngineConfig.strategy_id` at BUY
    /// time). Drives the per-strat money-in/PnL ledger; "" on positions
    /// persisted before the ledger existed (surfaced as unassigned).
    #[serde(rename = "strategyId", default)]
    pub strategy_id: String,
}

/// Per-strat fill ledger. BUYs add volume; SELLs and redeems realize PnL
/// against the exiting position's cost basis. Open exposure ("money in") is
/// NOT stored here — it's derivable from `EngineState.positions`, each of
/// which carries the `strategy_id` that opened it.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct StratStats {
    /// Σ realized PnL in USDC: (exit proceeds − cost basis) over every SELL
    /// and redeem attributed to this strat.
    #[serde(default)]
    pub realized: f64,
    /// Σ notional across all fills (BUY + SELL + redeem value).
    #[serde(default)]
    pub volume: f64,
    #[serde(default)]
    pub buys: u64,
    #[serde(default)]
    pub sells: u64,
    #[serde(default)]
    pub redeems: u64,
    #[serde(rename = "lastFillAt", default)]
    pub last_fill_at: i64,
}

/// One realized-PnL event (SELL or redeem) with its cost basis, kept in a
/// bounded rolling window so the UI can compute time-boxed returns (24h ROI)
/// that the cumulative `StratStats` ledger can't answer.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RealizedEvent {
    /// Wall-clock ms of the fill.
    pub t: i64,
    #[serde(rename = "strategyId")]
    pub strategy_id: String,
    /// Realized PnL in USDC: exit proceeds − cost basis.
    pub pnl: f64,
    /// Cost basis exited by this event (denominator for ROI).
    pub basis: f64,
}

/// Keep ~48h of events (24h window + slack for clock skew), hard-capped so a
/// hyperactive strat can't bloat the persisted state.
const REALIZED_EVENTS_MAX_AGE_MS: i64 = 48 * 3600 * 1000;
const REALIZED_EVENTS_CAP: usize = 512;

fn push_realized(events: &mut Vec<RealizedEvent>, strategy_id: String, pnl: f64, basis: f64, t: i64) {
    events.push(RealizedEvent { t, strategy_id, pnl, basis });
    let cutoff = t - REALIZED_EVENTS_MAX_AGE_MS;
    events.retain(|e| e.t >= cutoff);
    if events.len() > REALIZED_EVENTS_CAP {
        let excess = events.len() - REALIZED_EVENTS_CAP;
        events.drain(..excess);
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEntry {
    pub id: String,
    pub timestamp: i64,
    #[serde(rename = "type")]
    pub kind: String,
    pub reason: Option<String>,
    #[serde(rename = "traderAddress", skip_serializing_if = "Option::is_none")]
    pub trader_address: Option<String>,
    #[serde(rename = "tradesSeen", skip_serializing_if = "Option::is_none")]
    pub trades_seen: Option<usize>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineState {
    pub status: EngineStatus,
    #[serde(rename = "lastCycleAt")]
    pub last_cycle_at: Option<i64>,
    #[serde(rename = "nextCycleAt")]
    pub next_cycle_at: Option<i64>,
    #[serde(rename = "cycleCount")]
    pub cycle_count: u64,
    /// Cumulative mirror orders the engine has successfully placed (DRY RUN
    /// placements don't count). Surfaced to the frontend live rail.
    #[serde(rename = "totalOrdersPlaced")]
    pub total_orders_placed: u64,
    #[serde(rename = "totalOrdersFailed")]
    pub total_orders_failed: u64,
    #[serde(rename = "totalVolumeMirrored")]
    pub total_volume_mirrored: f64,
    pub balance: Option<f64>,
    pub log: Vec<LogEntry>,
    #[serde(rename = "observedTrades")]
    pub observed_trades: Vec<ObservedTrade>,
    pub error: Option<String>,
    /// Highest trade timestamp (ms) seen per trader. Used to filter new trades.
    #[serde(rename = "traderCursors", default)]
    pub trader_cursors: HashMap<String, i64>,
    /// Wall-clock ms of the last successful fetch per trader. Surfaces traders
    /// the engine hasn't been able to reach.
    #[serde(rename = "traderLastSync", default)]
    pub trader_last_sync: HashMap<String, i64>,
    /// Trade ids we've already placed a mirror for. Guards against
    /// double-copying a leader trade that reappears across the cursor's 60s
    /// overlap window. Bounded at `COPIED_IDS_CAP`.
    #[serde(rename = "copiedIds", default)]
    pub copied_ids: HashSet<String>,
    /// Open mirror positions keyed by `token_id`. Maintained on BUY/SELL fills
    /// and reconciled each cycle against the proxy wallet's on-chain holdings so
    /// the engine never tries to sell a token it doesn't hold.
    #[serde(default)]
    pub positions: HashMap<String, OpenPosition>,
    /// Per-strat realized ledger keyed by `strategy_id`. Together with the
    /// tagged `positions` this answers "how much money does each strat have
    /// in, and how has it performed" — surfaced verbatim via `/live/status`.
    #[serde(rename = "stratStats", default)]
    pub strat_stats: HashMap<String, StratStats>,
    /// Rolling window (~48h, bounded) of realized fills feeding the UI's
    /// 24h ROI. Cumulative totals stay in `strat_stats`.
    #[serde(rename = "realizedEvents", default)]
    pub realized_events: Vec<RealizedEvent>,
    /// Momentum-proposal cooldown: `condition:outcome:side` → last-acted ms.
    /// An unfilled GTC entry would otherwise be re-proposed (and re-stacked
    /// on the book) every cycle the signal persists. Persisted so a restart
    /// doesn't double-enter; browser parity: PROPOSAL_COOLDOWN_MS.
    #[serde(rename = "proposedRecently", default)]
    pub proposed_recently: HashMap<String, i64>,
    /// Exit cooldown: token_id → ms of the last SELL the engine placed for
    /// it. A just-sold position stays visible in the data-api until the fill
    /// settles, so without this the reconciler RE-ADOPTS the token on the
    /// next cycle and the protective-exit pass sells it again (double order,
    /// double-counted realized PnL — observed live 2026-07-22). Adoption
    /// skips tokens exited within EXIT_READOPT_COOLDOWN_MS.
    #[serde(rename = "exitedRecently", default)]
    pub exited_recently: HashMap<String, i64>,
}

impl EngineState {
    fn empty() -> Self {
        Self {
            status: EngineStatus::Stopped,
            last_cycle_at: None,
            next_cycle_at: None,
            cycle_count: 0,
            total_orders_placed: 0,
            total_orders_failed: 0,
            total_volume_mirrored: 0.0,
            balance: None,
            log: Vec::new(),
            observed_trades: Vec::new(),
            error: None,
            trader_cursors: HashMap::new(),
            trader_last_sync: HashMap::new(),
            copied_ids: HashSet::new(),
            positions: HashMap::new(),
            strat_stats: HashMap::new(),
            realized_events: Vec::new(),
            proposed_recently: HashMap::new(),
            exited_recently: HashMap::new(),
        }
    }
}

/// Ledger key for a fill: the strat stamped on the exiting position when it
/// has one, else the fallback (usually the running session's strat, or
/// "unassigned" when there's no session context at all).
fn strat_key(position_strat: &str, fallback: &str) -> String {
    if !position_strat.is_empty() {
        position_strat.to_string()
    } else if !fallback.is_empty() {
        fallback.to_string()
    } else {
        "unassigned".to_string()
    }
}

// ─── Liquidation ("flatten everything") ────────────────────────────────

/// One position's outcome in a liquidation pass.
#[derive(Debug, Clone, Serialize)]
pub struct LiquidationLeg {
    #[serde(rename = "tokenId")]
    pub token_id: String,
    pub market: String,
    pub size: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price: Option<f64>,
    /// "placed" | "skipped" | "failed"
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<String>,
}

/// Summary of a full-liquidation pass for one account.
#[derive(Debug, Clone, Serialize)]
pub struct LiquidationResult {
    pub eoa: String,
    #[serde(rename = "depositWallet")]
    pub deposit_wallet: String,
    pub positions: usize,
    pub placed: usize,
    pub skipped: usize,
    pub failed: usize,
    pub legs: Vec<LiquidationLeg>,
}

/// One sellable on-chain holding (deposit-wallet CTF balance).
struct HeldPosition {
    token_id: String,
    size: f64,
    condition_id: String,
    market: String,
    /// Average price paid per share, as reported by the data-api — the cost
    /// basis used when the reconciler ADOPTS a holding the engine ledger
    /// doesn't know. 0.0 when the API omits it (stop-loss then can't arm on
    /// the adopted position, but take-profit still can — it needs no entry).
    avg_price: f64,
}

/// All current positions held by `wallet`, read from the data-api. Carries
/// the `conditionId` (needed to resolve negRisk before signing), the market
/// title (for logs), and the average entry price (for ledger adoption).
/// Returns `None` on fetch/parse failure — callers must NOT treat an outage
/// as "the wallet holds nothing" (the reconciler once did, and wiped the
/// ledger every cycle).
async fn fetch_held_positions(http: &reqwest::Client, wallet: &str) -> Option<Vec<HeldPosition>> {
    let mut out = Vec::new();
    if wallet.is_empty() {
        return None;
    }
    let url = format!("{}/positions?user={}&sizeThreshold=0.0&limit=500", DATA_API, wallet);
    let resp = match http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!(error = %e, "positions fetch failed");
            return None;
        }
    };
    let text = resp.text().await.unwrap_or_default();
    let parsed: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(_) => return None,
    };
    if let Some(arr) = parsed.as_array() {
        for p in arr {
            let token_id = p.get("asset").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let size = p
                .get("size")
                .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .unwrap_or(0.0);
            let condition_id = p
                .get("conditionId")
                .or_else(|| p.get("condition_id"))
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let market = p.get("title").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let avg_price = p
                .get("avgPrice")
                .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .unwrap_or(0.0);
            if !token_id.is_empty() && size > 0.0 {
                out.push(HeldPosition { token_id, size, condition_id, market, avg_price });
            }
        }
    }
    Some(out)
}

// ─── Handle / Registry ─────────────────────────────────────────────────

pub struct EngineHandle {
    /// Behind a lock because the auto-watchlist refresh rewrites `traders`
    /// mid-session; `config_of` (→ /live/status) reads the current snapshot.
    pub config: RwLock<EngineConfig>,
    pub state: Arc<RwLock<EngineState>>,
    pub cancel: Arc<AtomicBool>,
    pub task: parking_lot::Mutex<Option<JoinHandle<()>>>,
}

pub struct EngineRegistry {
    engines: DashMap<String, Arc<EngineHandle>>,
    http: reqwest::Client,
    disk_dir: PathBuf,
    /// Per-EOA signing keys — the engine signs and places mirror orders
    /// autonomously through these (no browser wallet needed).
    signer_store: Arc<SignerStore>,
    /// conditionId → negRisk flag, resolved once via gamma and reused.
    neg_risk_cache: DashMap<String, bool>,
}

impl EngineRegistry {
    pub fn new(http: reqwest::Client, signer_store: Arc<SignerStore>) -> Self {
        // Same POLYMARKET_DATA_DIR convention the signer store uses — a
        // single volume-mounted dir holds every persistence artifact so the
        // container can be recycled (or fully recreated) without losing
        // user state. Falls back to OS temp for tests + local dev.
        let base = std::env::var("POLYMARKET_DATA_DIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|_| std::env::temp_dir());
        let disk_dir = base.join(PERSIST_DIR_NAME);
        std::fs::create_dir_all(&disk_dir).ok();
        let reg = Self {
            engines: DashMap::new(),
            http,
            disk_dir,
            signer_store,
            neg_risk_cache: DashMap::new(),
        };
        reg
    }

    /// Toggle real order placement on/off for a running session at runtime,
    /// persisting the change so it survives a restart. Returns the new value,
    /// or `None` if no session exists for this EOA. Because `EngineConfig`
    /// lives behind an immutable `Arc` snapshot inside the spawned task, the
    /// clean way to change it is to restart the session with the patched
    /// config — `start()` already stops + replaces any existing engine and
    /// preserves on-disk state (cursors, copied ids) across the swap.
    pub fn set_auto_execute(self: &Arc<Self>, eoa: &str, on: bool) -> Option<bool> {
        let mut cfg = self.config_of(eoa)?;
        cfg.auto_execute = on;
        self.start(cfg);
        Some(on)
    }

    /// Resolve (and cache) the negRisk flag for a market via gamma. A wrong
    /// value yields a CLOB "bad signature" rejection, so we resolve it rather
    /// than guessing; defaults to `false` only when gamma is unreachable.
    async fn resolve_neg_risk(&self, condition_id: &str) -> bool {
        if condition_id.is_empty() {
            return false;
        }
        if let Some(v) = self.neg_risk_cache.get(condition_id) {
            return *v;
        }
        let url = format!("{}/markets?condition_id={}", GAMMA_API, condition_id);
        let resolved = match self.http.get(&url).send().await {
            Ok(resp) => {
                let txt = resp.text().await.unwrap_or_default();
                serde_json::from_str::<Value>(&txt)
                    .ok()
                    .and_then(|v| {
                        let first = if v.is_array() { v.get(0).cloned() } else { Some(v) };
                        first.and_then(|m| {
                            m.get("negRisk")
                                .or_else(|| m.get("neg_risk"))
                                .and_then(|b| b.as_bool())
                        })
                    })
                    .unwrap_or(false)
            }
            Err(e) => {
                tracing::warn!(condition_id, error = %e, "negRisk resolve failed; defaulting false");
                false
            }
        };
        self.neg_risk_cache.insert(condition_id.to_string(), resolved);
        resolved
    }

    fn path_for_config(&self, eoa: &str) -> PathBuf {
        self.disk_dir.join(format!("{}.config.json", eoa.to_lowercase()))
    }
    fn path_for_state(&self, eoa: &str) -> PathBuf {
        self.disk_dir.join(format!("{}.state.json", eoa.to_lowercase()))
    }

    /// Resume any engines that were persisted to disk. Called once at API
    /// startup so a process restart doesn't kill live sessions.
    pub fn resume_persisted(self: &Arc<Self>) {
        let dir = self.disk_dir.clone();
        let Ok(read_dir) = std::fs::read_dir(&dir) else { return; };
        for entry in read_dir.flatten() {
            let p = entry.path();
            if !p.is_file() { continue; }
            let name = match p.file_name().and_then(|n| n.to_str()) {
                Some(n) => n,
                None => continue,
            };
            if !name.ends_with(".config.json") { continue; }
            let raw = match std::fs::read_to_string(&p) {
                Ok(r) => r,
                Err(e) => { tracing::warn!("resume: read {}: {}", name, e); continue; }
            };
            let cfg: EngineConfig = match serde_json::from_str(&raw) {
                Ok(c) => c,
                Err(e) => { tracing::warn!("resume: parse {}: {}", name, e); continue; }
            };
            tracing::info!("resuming live engine for {}", cfg.eoa);
            // Restore state if present, else start fresh.
            let restored_state = self.load_persisted_state(&cfg.eoa);
            self.start_internal(cfg, restored_state);
        }
    }

    pub fn status_of(&self, eoa: &str) -> Option<EngineState> {
        self.engines
            .get(&eoa.to_lowercase())
            .map(|h| h.state.read().clone())
    }

    pub fn config_of(&self, eoa: &str) -> Option<EngineConfig> {
        self.engines
            .get(&eoa.to_lowercase())
            .map(|h| h.config.read().clone())
    }

    /// Every EOA with a persisted session on disk. Used by the scheduled
    /// liquidation task to flatten all known accounts.
    /// Last persisted (config, state) for an EOA — `/live/status`'s fallback
    /// when no engine is running, so the per-strat ledger and tagged open
    /// positions stay visible across engine stops and API restarts.
    pub fn persisted_snapshot(&self, eoa: &str) -> Option<(EngineConfig, EngineState)> {
        let cfg = self.load_persisted_config(eoa)?;
        let state = self.load_persisted_state(eoa)?;
        Some((cfg, state))
    }

    pub fn persisted_eoas(&self) -> Vec<String> {
        let mut out = Vec::new();
        if let Ok(rd) = std::fs::read_dir(&self.disk_dir) {
            for entry in rd.flatten() {
                if let Some(name) = entry.path().file_name().and_then(|n| n.to_str()) {
                    if let Some(eoa) = name.strip_suffix(".config.json") {
                        out.push(eoa.to_string());
                    }
                }
            }
        }
        out
    }

    /// Sell EVERY position the account currently holds at the marketable
    /// (best-bid, tick-rounded) price — a full "flatten". Positions live on
    /// the V2 *deposit wallet* (CREATE2 from the backend signer EOA), not the
    /// user's EOA, so we derive that address and read its on-chain holdings
    /// directly rather than trusting the engine's tracked `positions` (which
    /// only cover what THIS engine bought). Places real orders regardless of
    /// the session's `auto_execute` flag — liquidation is an explicit command,
    /// not copy-trading. SELLs need no USDC, only the held tokens, so the
    /// "insufficient balance" path never applies here.
    /// `min_price` restricts the flatten to positions whose best bid is at
    /// or above it — the "harvest 99¢ winners" mode. None = sell everything.
    pub async fn liquidate_all(
        self: &Arc<Self>,
        eoa: &str,
        min_price: Option<f64>,
    ) -> Result<LiquidationResult> {
        let backend_addr = self.signer_store.signer_address(eoa)?;
        let deposit_wallet = crate::deposit_wallet::derive_deposit_wallet(&backend_addr)?;
        let held = fetch_held_positions(&self.http, &deposit_wallet).await.unwrap_or_default();

        let mut result = LiquidationResult {
            eoa: eoa.to_lowercase(),
            deposit_wallet: deposit_wallet.clone(),
            positions: held.len(),
            placed: 0,
            skipped: 0,
            failed: 0,
            legs: Vec::with_capacity(held.len()),
        };
        if held.is_empty() {
            tracing::info!(eoa = %eoa, wallet = %deposit_wallet, "liquidate: nothing to sell");
            return Ok(result);
        }

        let handle = self.engines.get(&eoa.to_lowercase()).map(|h| h.value().clone());

        for pos in held {
            // Sizes floor to 2dp in `compute_amounts`; anything under a cent
            // of a share can't form a valid order — skip rather than error.
            if (pos.size * 100.0).floor() < 1.0 {
                result.skipped += 1;
                result.legs.push(LiquidationLeg {
                    token_id: pos.token_id,
                    market: pos.market,
                    size: pos.size,
                    price: None,
                    status: "skipped".into(),
                    detail: Some("sub-cent dust".into()),
                });
                continue;
            }

            // Clamp to the CLOB's valid tick range: a book bidding 100¢
            // (near-settled market) makes tick_round return 1.0, which the
            // CLOB rejects with "invalid price, must be greater than 0 and
            // less than 1". Sell at the 99¢ max tick and let it cross.
            let bid = match fetch_best_bid(&self.http, &pos.token_id).await {
                Some(b) => tick_round_price(b).clamp(0.01, 0.99),
                None => {
                    result.skipped += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id,
                        market: pos.market,
                        size: pos.size,
                        price: None,
                        status: "skipped".into(),
                        detail: Some("no bid on book".into()),
                    });
                    continue;
                }
            };

            if let Some(min) = min_price {
                if bid < min {
                    result.skipped += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id,
                        market: pos.market,
                        size: pos.size,
                        price: Some(bid),
                        status: "skipped".into(),
                        detail: Some(format!("bid {:.0}¢ below minPrice {:.0}¢", bid * 100.0, min * 100.0)),
                    });
                    continue;
                }
            }

            let neg_risk = self.resolve_neg_risk(&pos.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: eoa.to_string(),
                creds: ClobCreds {
                    api_key: String::new(),
                    secret: String::new(),
                    passphrase: String::new(),
                },
                args: PlaceOrderArgs {
                    token_id: pos.token_id.clone(),
                    side: OrderSide::Sell,
                    price: bid,
                    size: pos.size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: deposit_wallet.clone(),
                },
            };

            match place_order(&self.http, &self.signer_store, req).await {
                Ok(_) => {
                    result.placed += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id.clone(),
                        market: pos.market.clone(),
                        size: pos.size,
                        price: Some(bid),
                        status: "placed".into(),
                        detail: None,
                    });
                    if let Some(h) = &handle {
                        let mut s = h.state.write();
                        s.exited_recently
                            .insert(pos.token_id.clone(), chrono::Utc::now().timestamp_millis());
                        // Realize PnL into the owning strat's ledger when the
                        // engine tracked this token (on-chain holds it never
                        // bought have no known entry — nothing to realize).
                        if let Some(tracked) = s.positions.remove(&pos.token_id) {
                            let sold = tracked.size.min(pos.size);
                            let key = strat_key(&tracked.strategy_id, "");
                            let now = chrono::Utc::now().timestamp_millis();
                            let stats = s.strat_stats.entry(key.clone()).or_default();
                            stats.sells += 1;
                            stats.volume += sold * bid;
                            stats.realized += sold * (bid - tracked.entry_price);
                            stats.last_fill_at = now;
                            push_realized(
                                &mut s.realized_events,
                                key,
                                sold * (bid - tracked.entry_price),
                                sold * tracked.entry_price,
                                now,
                            );
                        }
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += pos.size * bid;
                        push_log(&mut s.log, mk_log(
                            "LIQUIDATE_SELL",
                            &pos.token_id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ · ${:.2} · {}",
                                pos.size, bid * 100.0, pos.size * bid, pos.market
                            ),
                            None,
                        ));
                    }
                }
                Err(e) => {
                    result.failed += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id.clone(),
                        market: pos.market.clone(),
                        size: pos.size,
                        price: Some(bid),
                        status: "failed".into(),
                        detail: Some(e.to_string()),
                    });
                    if let Some(h) = &handle {
                        let mut s = h.state.write();
                        s.total_orders_failed += 1;
                        push_log(&mut s.log, mk_log(
                            "ERROR",
                            &pos.token_id,
                            format!("LIQUIDATE_SELL_FAILED: {} · {}", e, pos.market),
                            None,
                        ));
                    }
                }
            }
            // Space out placements so a 17-position flatten doesn't burst the
            // CLOB rate limiter.
            tokio::time::sleep(Duration::from_millis(300)).await;
        }

        if let Some(h) = &handle {
            self.persist_state(eoa, &h.state.read());
        }
        tracing::info!(
            eoa = %eoa,
            wallet = %deposit_wallet,
            positions = result.positions,
            placed = result.placed,
            skipped = result.skipped,
            failed = result.failed,
            "liquidation pass complete",
        );
        Ok(result)
    }

    /// Redeem all RESOLVED positions for an account (settled winnings →
    /// USDC), wrapping the proceeds back into trading collateral. Thin
    /// wrapper over `relayer::redeem_resolved_positions` using the registry's
    /// own http client + signer store, so the scheduled flatten can claim
    /// settled markets the same way the `/redeem` route does. A SELL can't
    /// cash these out — settled markets have no order book — so this is the
    /// companion to `liquidate_all`, not a duplicate of it.
    pub async fn redeem_all(self: &Arc<Self>, eoa: &str) -> Result<crate::relayer::RedeemResult> {
        crate::relayer::redeem_resolved_positions(&self.http, &self.signer_store, eoa, true).await
    }

    /// Start an engine. If one already exists for this EOA, it's stopped
    /// and replaced (lets the user reconfigure mid-session without manual stop).
    ///
    /// Restores the EOA's persisted state across the swap so a reconfigure or
    /// an auto-execute toggle does NOT wipe order counters, the copy log, or
    /// `copied_ids`. Without this, every restart began from an empty state:
    /// the UI showed 0 trades despite real fills, and the engine re-mirrored
    /// trades it had already copied (cleared `copied_ids` → duplicate orders).
    pub fn start(self: &Arc<Self>, cfg: EngineConfig) {
        let lc = cfg.eoa.to_lowercase();
        if let Some((_, existing)) = self.engines.remove(&lc) {
            existing.cancel.store(true, Ordering::Release);
            if let Some(t) = existing.task.lock().take() {
                t.abort();
            }
        }
        self.persist_config(&cfg);
        // Re-load whatever the engine last persisted (counters, log, copied
        // ids, cursors). Falls back to a fresh state for a brand-new session.
        let restored = self.load_persisted_state(&cfg.eoa);
        self.start_internal(cfg, restored);
    }

    /// Read the persisted `EngineState` for an EOA from disk, if any.
    fn load_persisted_state(&self, eoa: &str) -> Option<EngineState> {
        let state_path = self.path_for_state(eoa);
        std::fs::read_to_string(&state_path)
            .ok()
            .and_then(|s| serde_json::from_str::<EngineState>(&s).ok())
    }

    /// Read the persisted `EngineConfig` for an EOA from disk, if any.
    fn load_persisted_config(&self, eoa: &str) -> Option<EngineConfig> {
        let path = self.path_for_config(eoa);
        std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str::<EngineConfig>(&s).ok())
    }

    /// The `auto_execute` currently in effect for an EOA — from the running
    /// session if one exists, else the last persisted config. `None` when
    /// neither exists (a brand-new session, so the caller's own value stands).
    /// Lets `/live/start` keep a live session's execution mode sticky across a
    /// config re-post that omits the flag, instead of reverting to DRY RUN.
    pub fn current_auto_execute(&self, eoa: &str) -> Option<bool> {
        self.config_of(eoa)
            .or_else(|| self.load_persisted_config(eoa))
            .map(|c| c.auto_execute)
    }

    fn persist_config(&self, cfg: &EngineConfig) {
        let path = self.path_for_config(&cfg.eoa);
        if let Ok(json) = serde_json::to_string_pretty(cfg) {
            let _ = std::fs::write(&path, json);
            restrict_perms(&path);
        }
    }

    fn persist_state(&self, eoa: &str, state: &EngineState) {
        let path = self.path_for_state(eoa);
        if let Ok(json) = serde_json::to_string(state) {
            let _ = std::fs::write(&path, json);
            restrict_perms(&path);
        }
    }

    fn start_internal(self: &Arc<Self>, cfg: EngineConfig, restore: Option<EngineState>) {
        let mut initial = restore.unwrap_or_else(EngineState::empty);
        initial.status = EngineStatus::Running;
        initial.error = None;
        let state = Arc::new(RwLock::new(initial));
        let cancel = Arc::new(AtomicBool::new(false));

        let handle = Arc::new(EngineHandle {
            config: RwLock::new(cfg.clone()),
            state: state.clone(),
            cancel: cancel.clone(),
            task: parking_lot::Mutex::new(None),
        });
        let lc = cfg.eoa.to_lowercase();
        self.engines.insert(lc.clone(), handle.clone());

        // Spawn the loop.
        let registry = Arc::clone(self);
        let task_cfg = cfg;
        let task = tokio::spawn(async move {
            registry.run_loop(task_cfg, state, cancel).await;
        });
        *handle.task.lock() = Some(task);
    }

    /// Explicit user stop. Clears the persisted config so the next API boot
    /// doesn't auto-resume the session.
    pub fn stop(&self, eoa: &str) -> bool {
        let lc = eoa.to_lowercase();
        let Some((_, handle)) = self.engines.remove(&lc) else { return false; };
        handle.cancel.store(true, Ordering::Release);
        if let Some(t) = handle.task.lock().take() {
            t.abort();
        }
        // Mark state as stopped, then persist final shape so a quick reload
        // sees the stopped state before the file deletion lands.
        {
            let mut s = handle.state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&lc, &handle.state.read());
        // Delete the config so resume_persisted() skips it on next boot.
        let _ = std::fs::remove_file(self.path_for_config(&lc));
        true
    }

    /// The main loop.
    async fn run_loop(
        self: Arc<Self>,
        mut cfg: EngineConfig,
        state: Arc<RwLock<EngineState>>,
        cancel: Arc<AtomicBool>,
    ) {
        // First cycle cursor: now - intervalMs, NOT now. Without this, a fresh
        // engine's first cycle filters every fetched trade out because
        // `trade.ts > cursor` is false the moment after start (matches the JS
        // engine's bug we already fixed there).
        // Clamp the configured cadence up to the rate-limit floor. Old sessions
        // persisted a 5s interval that hammered the data-api into 429s; the floor
        // makes a stale config self-heal on resume without the user re-saving.
        let effective_interval_ms = cfg.interval_ms.max(cfg.min_interval_ms);

        let now_ms = chrono::Utc::now().timestamp_millis();
        {
            let mut s = state.write();
            for t in &cfg.traders {
                let key = t.address.to_lowercase();
                s.trader_cursors
                    .entry(key)
                    .or_insert_with(|| now_ms - cfg.interval_ms as i64);
            }
        }

        // Auto-watchlist refresh clock. 0 ⇒ refresh before the first cycle so
        // a brand-new auto session never polls a stale hand-picked list.
        let mut last_watchlist_refresh_ms: i64 = 0;

        // Momentum price-series feed, refreshed once per
        // MOMENTUM_SERIES_TTL_MS rather than per cycle (browser parity —
        // 1-min polling would hammer the CLOB price-history endpoint).
        let mut momentum_series: Option<(i64, Vec<MarketPriceSeries>)> = None;
        let mut momentum_last_logged_len: Option<usize> = None;

        // Auto-redeem clock. 0 ⇒ the first cycle redeems immediately, so
        // winnings that settled while the engine was down get freed on start.
        let mut last_redeem_check_ms: i64 = 0;
        // Each check costs one data-api positions read (an on-chain tx only
        // fires when something is actually redeemable), so a few minutes is
        // plenty — sub-hour markets settle fast but never faster than this.
        const AUTO_REDEEM_EVERY_MS: i64 = 5 * 60_000;

        loop {
            if cancel.load(Ordering::Acquire) { break; }

            // ── Auto-watchlist refresh ──
            // Re-derive "top traders active in the window" per tag, swap them
            // into cfg.traders, and persist so a restart resumes the same
            // list. New traders' cursors start at the refresh instant: they
            // must NOT inherit the engine-start default (start − interval) or
            // hours of their old activity would be mirrored as "new" at once.
            if let Some(auto) = cfg.auto_traders.clone() {
                let now = chrono::Utc::now().timestamp_millis();
                let due = last_watchlist_refresh_ms == 0
                    || now - last_watchlist_refresh_ms
                        >= (auto.refresh_minutes.max(5) * 60_000) as i64;
                if due {
                    last_watchlist_refresh_ms = now;
                    let mut exclude: HashSet<String> = HashSet::new();
                    exclude.insert(cfg.eoa.to_lowercase());
                    exclude.insert(cfg.address.to_lowercase());
                    match discover_top_traders(
                        &self.http,
                        &auto,
                        &exclude,
                        cfg.market_query.as_deref(),
                    ).await {
                        Ok(list) if !list.is_empty() => {
                            let stamped = chrono::Utc::now().timestamp_millis();
                            let roster = list
                                .iter()
                                .map(|t| {
                                    format!(
                                        "{}… ({:.0}%)",
                                        t.address.get(..8).unwrap_or(&t.address),
                                        t.weight * 100.0
                                    )
                                })
                                .collect::<Vec<_>>()
                                .join(" · ");
                            {
                                let mut s = state.write();
                                for t in &list {
                                    s.trader_cursors
                                        .entry(t.address.to_lowercase())
                                        .or_insert(stamped);
                                }
                                push_log(&mut s.log, LogEntry {
                                    id: format!("watchlist-{}", stamped),
                                    timestamp: stamped,
                                    kind: "WATCHLIST".into(),
                                    reason: Some(format!(
                                        "auto-watchlist · {} traders active in last {}h across tags [{}]{} · {}",
                                        list.len(),
                                        auto.hours,
                                        auto.tags.join(", "),
                                        cfg.market_query
                                            .as_deref()
                                            .filter(|q| !q.trim().is_empty())
                                            .map(|q| format!(" matching \"{}\"", q))
                                            .unwrap_or_default(),
                                        roster
                                    )),
                                    trader_address: None,
                                    trades_seen: None,
                                });
                            }
                            cfg.traders = list;
                            self.persist_config(&cfg);
                            if let Some(h) = self.engines.get(&cfg.eoa.to_lowercase()) {
                                *h.config.write() = cfg.clone();
                            }
                        }
                        Ok(_) => {
                            let mut s = state.write();
                            push_log(&mut s.log, LogEntry {
                                id: format!("watchlist-{}", now),
                                timestamp: now,
                                kind: "WATCHLIST".into(),
                                reason: Some(
                                    "auto-watchlist refresh found no qualifying traders — keeping current list"
                                        .into(),
                                ),
                                trader_address: None,
                                trades_seen: None,
                            });
                        }
                        Err(e) => {
                            tracing::warn!(eoa = %cfg.eoa, error = %e, "auto-watchlist refresh failed");
                            let mut s = state.write();
                            push_log(&mut s.log, LogEntry {
                                id: format!("watchlist-{}", now),
                                timestamp: now,
                                kind: "ERROR".into(),
                                reason: Some(format!(
                                    "WATCHLIST_REFRESH_FAILED: {} — keeping current list",
                                    e
                                )),
                                trader_address: None,
                                trades_seen: None,
                            });
                        }
                    }
                }
            }

            let cycle_started_at = chrono::Utc::now().timestamp_millis();
            let mut new_observed: Vec<ObservedTrade> = Vec::new();
            let mut trader_sync_updates: Vec<(String, i64)> = Vec::new();
            let mut cursor_updates: Vec<(String, i64)> = Vec::new();
            let mut errors: Vec<(String, String)> = Vec::new();
            // Mirror candidates collected this cycle: (newly-observed BUY, copyRatio).
            // Executed after the state commit so order-placement HTTP never runs
            // under the state lock.
            let mut mirror_candidates: Vec<(ObservedTrade, f64)> = Vec::new();

            // Snapshot cursors so we don't hold the RwLock across the HTTP fan-out.
            let (cursors, enabled_traders): (HashMap<String, i64>, Vec<TraderEntry>) = {
                let s = state.read();
                let cursors = s.trader_cursors.clone();
                (cursors, cfg.traders.iter().filter(|t| t.enabled).cloned().collect())
            };
            // Sum of enabled weights — denominator for each trader's capital
            // allocation. Guarded so a degenerate all-zero-weight config can't
            // divide by zero.
            let total_weight: f64 = enabled_traders
                .iter()
                .map(|t| t.weight)
                .sum::<f64>()
                .max(1e-9);
            // Proportional-sizing window: a trader's volume over the last N days.
            let window_cutoff_ms =
                cycle_started_at - (cfg.backtest_days.max(1) as i64) * 86_400_000;

            for (idx, trader) in enabled_traders.iter().enumerate() {
                if cancel.load(Ordering::Acquire) { break; }
                // Spread the per-trader fetches out so we never burst N requests
                // at once and trip Cloudflare's per-second limit (1015). Skip the
                // delay before the first request so a single-trader engine isn't
                // needlessly slowed.
                if idx > 0 {
                    tokio::time::sleep(Duration::from_millis(cfg.inter_request_delay_ms)).await;
                }
                let key = trader.address.to_lowercase();
                let cursor = *cursors.get(&key).unwrap_or(&(now_ms - cfg.interval_ms as i64));
                match fetch_recent_activity(&self.http, &trader.address).await {
                    Ok(items) => {
                        trader_sync_updates.push((key.clone(), chrono::Utc::now().timestamp_millis()));
                        // Parse the trader's full recent activity once, then use
                        // it both for the proportional copyRatio (volume over the
                        // window) and for new-trade detection.
                        let parsed: Vec<ObservedTrade> = items
                            .iter()
                            .filter_map(|v| parse_activity_trade(v, &trader.address))
                            .collect();

                        // copyRatio = (capital × weight/totalWeight) / traderVol,
                        // where traderVol = max(buyVol, sellVol, 1) over the window,
                        // counting ONLY markets that pass the strat's topic query —
                        // the backtest's `traderCopyRatio` filters the denominator
                        // the same way, so a "bitcoin"-only strat sizes against the
                        // trader's bitcoin volume, not their whole book. Matches
                        // copyEngine.ts / the backtest tab exactly.
                        let query_ok = |market: &str| {
                            cfg.market_query.as_deref().map_or(true, |q| {
                                crate::categories::market_matches_query(market, q)
                            })
                        };
                        let mut buy_vol = 0.0f64;
                        let mut sell_vol = 0.0f64;
                        for t in &parsed {
                            if t.timestamp >= window_cutoff_ms && query_ok(&t.market) {
                                if t.side == "BUY" {
                                    buy_vol += t.notional;
                                } else if t.side == "SELL" {
                                    sell_vol += t.notional;
                                }
                            }
                        }
                        let trader_vol = buy_vol.max(sell_vol).max(1.0);
                        let capital_alloc = cfg.capital * (trader.weight / total_weight);
                        let copy_ratio = capital_alloc / trader_vol;

                        // Sharpe/ROI over the fixed 30d window — the basis for
                        // each candidate's EP score (frozen on the position at
                        // entry, used to rank holds vs. new candidates). Scoped
                        // to the strat's topic query like the backtest's
                        // `traderStatsMap`: a filtered strat ranks traders by
                        // how they perform in the filtered slice.
                        let roi_stats = compute_trader_roi_stats(
                            &items,
                            cycle_started_at,
                            cfg.market_query.as_deref(),
                        );

                        let mut highest_ts = cursor;
                        for mut t in parsed {
                            if t.timestamp <= cursor { continue; }
                            if t.timestamp > highest_ts { highest_ts = t.timestamp; }
                            // Stamp the rank score (P × ROI × rawMirrorNotional)
                            // and the success probability on every observed BUY
                            // so the frontend rail and the rebalancer share the
                            // same numbers.
                            if t.side == "BUY" {
                                t.score = candidate_score(&roi_stats, t.notional * copy_ratio);
                                t.success_prob = roi_stats.success_prob;
                            }
                            // Mirror BUYs (entries) AND SELLs (leader exits of
                            // tokens we hold) — the same replay the backtest
                            // sim runs, so the preview predicts execution.
                            // Honor the strat's market-topic filter both ways:
                            // a trade in a non-matching market is still
                            // observed (rail/log) but never mirrored.
                            let market_ok = cfg.market_query.as_deref().map_or(true, |query| {
                                crate::categories::market_matches_query(&t.market, query)
                            });
                            // Semantic per-trade filters (side / price band /
                            // size band / category) gate ENTRIES only — same
                            // as the backtest, which keeps every leader SELL
                            // and only filters/ranks BUYs. An exit signal for
                            // a token we already hold is always honored.
                            let mirror_ok = match t.side.as_str() {
                                "BUY" => market_ok && trade_passes_filters(&t, &cfg.trade_filters),
                                "SELL" => market_ok,
                                _ => false,
                            };
                            if mirror_ok && !t.token_id.is_empty() {
                                mirror_candidates.push((t.clone(), copy_ratio));
                            }
                            new_observed.push(t);
                        }
                        if highest_ts > cursor {
                            cursor_updates.push((key, highest_ts));
                        }
                    }
                    Err(e) => {
                        tracing::warn!(trader = %trader.address, error = %e, "activity fetch failed");
                        errors.push((trader.address.clone(), e.to_string()));
                    }
                }
            }

            // Commit all the cycle's effects in a single state-lock window.
            let cycle_ended_at = chrono::Utc::now().timestamp_millis();
            {
                let mut s = state.write();
                // Merge observed trades, newest-first, capped at OBSERVED_CAP.
                let mut combined: Vec<ObservedTrade> = new_observed;
                combined.extend(s.observed_trades.drain(..));
                combined.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));
                combined.truncate(OBSERVED_CAP);
                s.observed_trades = combined;

                for (k, ts) in trader_sync_updates {
                    s.trader_last_sync.insert(k, ts);
                }
                for (k, ts) in cursor_updates {
                    s.trader_cursors.insert(k, ts);
                }

                // Log a heartbeat so quiet cycles still produce a signal.
                let mut summary = if errors.is_empty() {
                    format!(
                        "polled {} traders · {} new trades observed",
                        enabled_traders.len(),
                        s.observed_trades.iter().filter(|o| o.timestamp >= cycle_started_at).count(),
                    )
                } else {
                    format!(
                        "polled {} traders · {} fetch errors",
                        enabled_traders.len(),
                        errors.len(),
                    )
                };
                // A watchlist-free momentum session would otherwise read
                // "polled 0 traders" forever and look dead.
                if cfg.momentum.is_some() {
                    let n = momentum_series.as_ref().map_or(0, |(_, s)| s.len());
                    summary.push_str(&format!(" · momentum over {} markets", n));
                }
                let next_cycle_count = s.cycle_count + 1;
                tracing::info!(eoa = %cfg.eoa, cycle = next_cycle_count, interval_ms = effective_interval_ms, "{}", summary);
                push_log(&mut s.log, LogEntry {
                    id: format!("cycle-{}", next_cycle_count),
                    timestamp: cycle_ended_at,
                    kind: "CYCLE_END".into(),
                    reason: Some(summary),
                    trader_address: None,
                    trades_seen: None,
                });
                for (addr, err) in &errors {
                    push_log(&mut s.log, LogEntry {
                        id: format!("err-{}-{}", cycle_ended_at, addr),
                        timestamp: cycle_ended_at,
                        kind: "ERROR".into(),
                        reason: Some(format!("FETCH_FAILED: {}", err)),
                        trader_address: Some(addr.clone()),
                        trades_seen: None,
                    });
                }

                s.cycle_count += 1;
                s.last_cycle_at = Some(cycle_ended_at);
                s.next_cycle_at = Some(cycle_ended_at + effective_interval_ms as i64);
                s.status = EngineStatus::Running;
            }

            // Persist snapshot so a restart restores state.
            self.persist_state(&cfg.eoa, &state.read());

            // ── Position reconciliation ──
            // Sync the internal ledger to the wallet's actual on-chain
            // holdings before any protective-exit/rebalancing decision, so we
            // never try to sell a token we no longer hold (manual exit,
            // resolution, partial fill). Only runs when we might trade
            // (auto_execute) to avoid needless data-api load on dry-run
            // sessions. Positions live on the V2 DEPOSIT WALLET (CREATE2 from
            // the backend signer) — reconciling against `cfg.address` (the
            // user's EOA) always read an empty set and silently wiped every
            // ledger entry each cycle, leaving stop-loss/rebalance nothing to
            // work with.
            if cfg.auto_execute {
                let wallet = self
                    .signer_store
                    .signer_address(&cfg.eoa)
                    .ok()
                    .and_then(|a| crate::deposit_wallet::derive_deposit_wallet(&a).ok());
                let held = match &wallet {
                    Some(w) => fetch_held_positions(&self.http, w).await,
                    None => None,
                };
                // None = fetch/derivation failed — keep the ledger as-is
                // rather than reading an outage as "everything was sold".
                if let Some(held) = held {
                    let onchain: HashMap<&str, &HeldPosition> =
                        held.iter().map(|p| (p.token_id.as_str(), p)).collect();
                    let mut s = state.write();
                    // Drop / shrink ledger entries to match on-chain reality.
                    s.positions.retain(|token_id, pos| {
                        match onchain.get(token_id.as_str()) {
                            Some(h) if h.size > 0.0 => {
                                if h.size + 1e-6 < pos.size { pos.size = h.size; }
                                true
                            }
                            _ => false, // no longer held → drop
                        }
                    });
                    // ADOPT real holdings the ledger doesn't know — bought
                    // before a state wipe, by an earlier session, or manually.
                    // Without a ledger entry the take-profit/stop-loss pass
                    // can't defend them and they'd sit pinned at 100¢ until
                    // resolution. Entry = the data-api's average price paid;
                    // entry_score = MAX so capital-rebalancing never
                    // sacrifices a hold this session didn't buy. Tokens the
                    // engine just SOLD are skipped for the cooldown window —
                    // the data-api still lists them until the fill settles,
                    // and re-adopting one re-fires its exit (double sell).
                    let now_ms = chrono::Utc::now().timestamp_millis();
                    s.exited_recently.retain(|_, t| now_ms - *t < EXIT_READOPT_COOLDOWN_MS);
                    for h in &held {
                        if s.exited_recently.contains_key(&h.token_id) { continue; }
                        if h.size > 0.0 && !s.positions.contains_key(&h.token_id) {
                            s.positions.insert(h.token_id.clone(), OpenPosition {
                                token_id: h.token_id.clone(),
                                condition_id: h.condition_id.clone(),
                                market: h.market.clone(),
                                size: h.size,
                                entry_price: h.avg_price,
                                entry_score: f64::MAX,
                                opened_at: now_ms,
                                strategy_id: String::new(),
                            });
                        }
                    }
                }
            }

            // ── Stop-loss / take-profit pass ──
            // Protective exits run right after reconciliation (so we only ever
            // sell what's really held) and before mirror execution (so freed
            // cash can fund this cycle's buys). Needs auto_execute: in dry-run
            // the ledger holds no real positions to defend.
            let stop_armed = cfg.stop_loss.map_or(false, |sl| sl > 0.0);
            let tp_armed = cfg.take_profit.map_or(false, |tp| tp > 0.0);
            if cfg.auto_execute && (stop_armed || tp_armed) {
                self.check_stop_losses(&cfg, &state, &cancel).await;
            }

            // ── Auto-redeem settled winnings ──
            // Runs BEFORE mirror execution so freed cash can fund this cycle's
            // buys. redeem_all no-ops (no tx) when nothing is redeemable, and
            // ConditionalTokens.redeemPositions on a zero balance pays 0 rather
            // than reverting, so racing the manual REDEEM button is harmless.
            if cfg.auto_redeem {
                let now = chrono::Utc::now().timestamp_millis();
                if now - last_redeem_check_ms >= AUTO_REDEEM_EVERY_MS {
                    last_redeem_check_ms = now;
                    match self.redeem_all(&cfg.eoa).await {
                        Ok(r) if r.conditions > 0 => {
                            tracing::info!(
                                eoa = %cfg.eoa, conditions = r.conditions,
                                value = r.value_redeemed, skipped = r.skipped,
                                "auto-redeem: settled positions freed to cash",
                            );
                            {
                                let mut s = state.write();
                                // Realize each redeemed condition into its
                                // owning strat's ledger: proceeds are the
                                // leg's winning value, cost basis the sum of
                                // tracked positions on that condition (both
                                // sides — losers burn to 0 in the same call).
                                // Consuming the positions here also keeps the
                                // reconcile pass from silently dropping them.
                                for leg in r.legs.iter().filter(|l| l.status == "redeem") {
                                    let matched: Vec<String> = s
                                        .positions
                                        .values()
                                        .filter(|p| p.condition_id.eq_ignore_ascii_case(&leg.condition_id))
                                        .map(|p| p.token_id.clone())
                                        .collect();
                                    if matched.is_empty() { continue; }
                                    let mut cost = 0.0f64;
                                    let mut owner = String::new();
                                    for tid in &matched {
                                        if let Some(p) = s.positions.remove(tid) {
                                            cost += p.size * p.entry_price;
                                            if owner.is_empty() && !p.strategy_id.is_empty() {
                                                owner = p.strategy_id.clone();
                                            }
                                        }
                                    }
                                    let key = strat_key(&owner, &cfg.strategy_id);
                                    let stats = s.strat_stats.entry(key.clone()).or_default();
                                    stats.redeems += 1;
                                    stats.volume += leg.value;
                                    stats.realized += leg.value - cost;
                                    stats.last_fill_at = now;
                                    push_realized(&mut s.realized_events, key, leg.value - cost, cost, now);
                                }
                                push_log(&mut s.log, LogEntry {
                                    id: format!("redeem-{}", now),
                                    timestamp: now,
                                    kind: "REDEEM".into(),
                                    reason: Some(format!(
                                        "auto-redeem · {} settled market(s) · ~${:.2} → cash{}",
                                        r.conditions,
                                        r.value_redeemed,
                                        if r.skipped > 0 {
                                            format!(" · {} deferred", r.skipped)
                                        } else {
                                            String::new()
                                        },
                                    )),
                                    trader_address: None,
                                    trades_seen: None,
                                });
                            }
                            self.persist_state(&cfg.eoa, &state.read());
                        }
                        Ok(_) => {}
                        // Throttled retry next window; a flaky data-api read or
                        // relayer hiccup shouldn't spam the user-facing log.
                        // Exception: "wallet busy" means we raced one of our own
                        // trades for the relayer's single per-wallet action slot
                        // — that clears in seconds, so retry next cycle (~1 min)
                        // instead of stranding winnings for the full window.
                        Err(e) => {
                            if e.to_string().contains("wallet busy") {
                                last_redeem_check_ms = now - AUTO_REDEEM_EVERY_MS + 60_000;
                            }
                            tracing::warn!(eoa = %cfg.eoa, error = %e, "auto-redeem pass failed");
                        }
                    }
                }
            }

            // ── Mirror execution ──
            // Place (or dry-run-log) a proportional order for each new BUY, and
            // (when rebalancing is on) sell lower-score holds to fund better ones.
            if !mirror_candidates.is_empty() {
                self.execute_mirrors(&cfg, &state, &cancel, mirror_candidates).await;
            }

            // ── Momentum origination ──
            // The general, watchlist-free strategy path: apply the strat's
            // momentum params over each candidate market's cached price
            // series (price over time) and act on the signals. Runs after
            // mirrors so entries/exits see this cycle's ledger updates; an
            // empty `traders` list is perfectly valid in this mode.
            if let Some(mo) = cfg.momentum.clone() {
                let now = chrono::Utc::now().timestamp_millis();
                // Candle mode tracks a market that lives ~5 minutes — its
                // feed must refresh far faster than the search-mode one.
                let ttl_ms = if mo.candles.is_some() {
                    CANDLE_SERIES_TTL_MS
                } else {
                    MOMENTUM_SERIES_TTL_MS
                };
                let stale = momentum_series
                    .as_ref()
                    .map_or(true, |(at, _)| now - *at >= ttl_ms);
                if stale {
                    match fetch_momentum_series(
                        &self.http,
                        &mo,
                        cfg.market_query.as_deref(),
                        cfg.inter_request_delay_ms,
                    )
                    .await
                    {
                        Ok(series) => {
                            // Log the feed only when its size changes — a
                            // steady 2-min refresh would otherwise flood the
                            // session log.
                            if momentum_last_logged_len != Some(series.len()) {
                                momentum_last_logged_len = Some(series.len());
                                let q = mo
                                    .query
                                    .clone()
                                    .or_else(|| cfg.market_query.clone())
                                    .unwrap_or_else(|| "bitcoin".into());
                                let mut s = state.write();
                                push_log(&mut s.log, mk_log(
                                    "INFO",
                                    &format!("mo-feed-{}", now),
                                    format!(
                                        "momentum · tracking {} markets' price history for \"{}\"",
                                        series.len(), q
                                    ),
                                    None,
                                ));
                            }
                            momentum_series = Some((now, series));
                        }
                        Err(e) => {
                            // Feed stays stale — momentum idles this cycle
                            // (matches the browser engine's catch {}).
                            tracing::warn!(eoa = %cfg.eoa, error = %e, "momentum series fetch failed");
                        }
                    }
                }
                if let Some((_, series)) = momentum_series.as_ref() {
                    if !series.is_empty() {
                        self.execute_momentum(&cfg, &state, &cancel, series).await;
                    }
                }
            }

            // Sleep until the next cycle, but break out early on cancel.
            // Polled sleep so cancel kicks in within ~200ms instead of waiting
            // for the full interval.
            let mut elapsed = 0u64;
            let step = 200u64;
            while elapsed < effective_interval_ms {
                if cancel.load(Ordering::Acquire) { break; }
                tokio::time::sleep(Duration::from_millis(step.min(effective_interval_ms - elapsed))).await;
                elapsed += step;
            }
        }

        // Loop exited (stop requested). Mark stopped + final persist.
        {
            let mut s = state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&cfg.eoa, &state.read());
    }

    /// Mirror each candidate — leader SELLs first (proportional exits of held
    /// tokens), then proportional BUYs — or, when `auto_execute` is off, log
    /// the order it *would* place (DRY RUN) without touching the CLOB. BUY
    /// candidates are ranked by EP score so the best trades claim capital
    /// first; when free capital is short and `rebalance_enabled` is set, the
    /// lowest-score held positions are sold (at the book bid) to fund a
    /// higher-score candidate it out-scores by `rebalance_margin_pct`.
    /// Runs outside the per-cycle state lock; each placement is an independent
    /// network round-trip through the backend signer.
    async fn execute_mirrors(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        candidates: Vec<(ObservedTrade, f64)>,
    ) {
        // Leader SELLs first: exits free capital that this cycle's BUYs can
        // then deploy — same ordering effect as the auto-redeem pass, and the
        // chronological replay the backtest performs.
        let (sell_candidates, mut candidates): (Vec<_>, Vec<_>) =
            candidates.into_iter().partition(|(t, _)| t.side == "SELL");
        self.execute_mirror_sells(cfg, state, cancel, sell_candidates).await;

        // All knobs come from the strat-supplied config — nothing hardcoded.
        let user_floor = cfg.min_order_size;
        let min_shares = cfg.min_shares;
        let ceiling = cfg.max_order_size.unwrap_or(f64::INFINITY);
        let mut placed_this_cycle = 0usize;

        // Highest EP score first: the best candidates claim free capital and win
        // rebalance contests before weaker ones get a look.
        candidates.sort_by(|a, b| {
            b.0.score.partial_cmp(&a.0.score).unwrap_or(std::cmp::Ordering::Equal)
        });

        // Internal capital budget: the strat allocation minus cost basis already
        // deployed in open positions. Selling a position returns its cost basis
        // here; a BUY subtracts its notional.
        let mut free_capital = {
            let s = state.read();
            let deployed: f64 = s.positions.values().map(|p| p.size * p.entry_price).sum();
            (cfg.capital - deployed).max(0.0)
        };

        for (trade, copy_ratio) in candidates {
            if cancel.load(Ordering::Acquire) { break; }
            // Don't re-mirror a trade we've already acted on.
            if state.read().copied_ids.contains(&trade.id) { continue; }

            // No-edge gate — parity with the backtest's rank filter and the
            // old in-browser engine: a BUY whose EP score isn't positive
            // (trader has no demonstrated 30d ROI) never spends capital. The
            // sim drops these from its curve, so live must not take them.
            if trade.score <= 0.0 {
                if cfg.auto_execute {
                    let mut s = state.write();
                    insert_copied_id(&mut s.copied_ids, trade.id.clone());
                }
                self.log_and_persist(cfg, state, mk_log(
                    "SKIP",
                    &trade.id,
                    format!(
                        "NO_EDGE · EP score {:.3} ≤ 0 — trader has no positive 30d ROI · {}",
                        trade.score, trade.market
                    ),
                    Some(&trade.trader),
                ));
                continue;
            }

            let (size, notional, price) = match plan_mirror(&trade, copy_ratio, user_floor, min_shares, ceiling, cfg.max_slippage_bps) {
                MirrorPlan::Skip(reason) => {
                    self.log_and_persist(cfg, state, mk_log("SKIP", &trade.id, reason, Some(&trade.trader)));
                    continue;
                }
                MirrorPlan::Place { size, notional, price } => (size, notional, price),
            };

            // DRY RUN: surface intent, place nothing, leave the trade un-copied
            // so it isn't retroactively filled when auto_execute is later enabled.
            if !cfg.auto_execute {
                let msg = format!(
                    "DRY RUN · would BUY {:.0} @ {:.0}¢ (${:.2}) · score {:.3} · {} · token {}",
                    size, price * 100.0, notional, trade.score, trade.market, short_token(&trade.token_id),
                );
                tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price, notional, "{}", msg);
                self.log_and_persist(cfg, state, mk_log("DRY_RUN", &trade.id, msg, Some(&trade.trader)));
                continue;
            }

            if placed_this_cycle >= cfg.max_orders_per_cycle {
                self.log_and_persist(cfg, state, mk_log(
                    "INFO",
                    &format!("cap-{}", trade.id),
                    format!("order cap {} reached this cycle — remaining mirrors deferred", cfg.max_orders_per_cycle),
                    None,
                ));
                break;
            }

            // ── Concurrent-positions cap ──
            // Opening a NEW token past `max_open_positions` is skipped and
            // marked copied (same as REBALANCE_SKIP) so the same leader trade
            // isn't reconsidered every cycle; topping up an already-held token
            // doesn't raise concurrency and always passes.
            let (already_held, open_count) = {
                let s = state.read();
                (
                    s.positions.get(&trade.token_id).map_or(false, |p| p.size > 0.0),
                    s.positions.values().filter(|p| p.size > 0.0).count(),
                )
            };
            if !already_held && open_count >= cfg.max_open_positions {
                {
                    let mut s = state.write();
                    insert_copied_id(&mut s.copied_ids, trade.id.clone());
                    push_log(&mut s.log, mk_log(
                        "SKIP",
                        &trade.id,
                        format!(
                            "MAX_POSITIONS · {}/{} open — not opening a new position · {}",
                            open_count, cfg.max_open_positions, trade.market
                        ),
                        Some(&trade.trader),
                    ));
                }
                self.persist_state(&cfg.eoa, &state.read());
                continue;
            }

            // ── Capital-aware rebalance ──
            // If this candidate doesn't fit in free capital, try to sell the
            // lowest-score holdings it sufficiently out-scores to make room.
            if notional > free_capital && cfg.rebalance_enabled {
                let needed = notional - free_capital;
                free_capital += self
                    .free_capital_via_sells(cfg, state, cancel, &trade, needed)
                    .await;
            }
            if notional > free_capital + 1e-6 {
                // No (or not enough) lower-score position to liquidate — skip,
                // but mark copied so we don't reconsider this same leader trade
                // every cycle. A future, higher-score-relative candidate can
                // still trigger sells of these holds later.
                {
                    let mut s = state.write();
                    insert_copied_id(&mut s.copied_ids, trade.id.clone());
                    push_log(&mut s.log, mk_log(
                        "REBALANCE_SKIP",
                        &trade.id,
                        format!(
                            "score {:.3} · need ${:.2}, free ${:.2} — no lower-score hold to sell · {}",
                            trade.score, notional, free_capital, trade.market
                        ),
                        Some(&trade.trader),
                    ));
                }
                self.persist_state(&cfg.eoa, &state.read());
                continue;
            }

            // Real placement through the backend signer (no browser wallet).
            let neg_risk = self.resolve_neg_risk(&trade.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: cfg.eoa.clone(),
                // place_order ignores creds (it mints backend-owned ones), but
                // the field is required by the request shape.
                creds: ClobCreds {
                    api_key: String::new(),
                    secret: String::new(),
                    passphrase: String::new(),
                },
                args: PlaceOrderArgs {
                    token_id: trade.token_id.clone(),
                    side: OrderSide::Buy,
                    price,
                    size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: cfg.address.clone(),
                },
            };
            placed_this_cycle += 1;

            match place_order(&self.http, &self.signer_store, req).await {
                Ok(resp) => {
                    free_capital = (free_capital - notional).max(0.0);
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += notional;
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        // Record / accumulate the open position with its FROZEN
                        // entry score (size-weighted avg entry price; keep the
                        // strongest score seen for this token).
                        let opened_at = chrono::Utc::now().timestamp_millis();
                        let entry = s.positions.entry(trade.token_id.clone()).or_insert_with(|| OpenPosition {
                            token_id: trade.token_id.clone(),
                            condition_id: trade.condition_id.clone(),
                            market: trade.market.clone(),
                            size: 0.0,
                            entry_price: 0.0,
                            entry_score: trade.score,
                            opened_at,
                            strategy_id: cfg.strategy_id.clone(),
                        });
                        let new_size = entry.size + size;
                        if new_size > 0.0 {
                            entry.entry_price = (entry.size * entry.entry_price + size * price) / new_size;
                        }
                        entry.size = new_size;
                        entry.entry_score = entry.entry_score.max(trade.score);
                        // Adopt a pre-ledger ("") position on top-up so its
                        // eventual exit lands in this strat's ledger instead
                        // of the unassigned bucket.
                        if entry.strategy_id.is_empty() {
                            entry.strategy_id = cfg.strategy_id.clone();
                        }
                        let stats = s
                            .strat_stats
                            .entry(strat_key(&cfg.strategy_id, ""))
                            .or_default();
                        stats.buys += 1;
                        stats.volume += notional;
                        stats.last_fill_at = opened_at;
                        push_log(&mut s.log, mk_log(
                            "COPY_BUY",
                            &trade.id,
                            format!("BUY {:.0} @ {:.0}¢ (${:.2}) · score {:.3} · {}", size, price * 100.0, notional, trade.score, trade.market),
                            Some(&trade.trader),
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price, notional, score = trade.score, response = %resp, "mirror order placed");
                }
                Err(e) => {
                    {
                        let mut s = state.write();
                        s.total_orders_failed += 1;
                        // Mark copied so a hard rejection isn't retried every cycle.
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        push_log(&mut s.log, mk_log(
                            "ERROR",
                            &trade.id,
                            format!("ORDER_FAILED: {}", e),
                            Some(&trade.trader),
                        ));
                    }
                    tracing::warn!(eoa = %cfg.eoa, market = %trade.market, error = %e, "mirror order failed");
                }
            }
            self.persist_state(&cfg.eoa, &state.read());
            tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
        }
    }

    /// One cycle of momentum origination — the general (non-copy) strategy
    /// path. Derives proposals from the price-series feed + the engine's own
    /// ledger via `propose_momentum`, then acts on them: DRY RUN logs when
    /// `auto_execute` is off, real CLOB orders through the backend signer
    /// otherwise. SELL exits run first (freed capital funds entries) and
    /// never count toward the BUY budget; BUY entries respect
    /// `max_orders_per_cycle` and the strat's free-capital envelope. Every
    /// acted signal starts a `PROPOSAL_COOLDOWN_MS` cooldown so a persisting
    /// signal doesn't re-fire (or re-log) every cycle.
    async fn execute_momentum(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        series: &[MarketPriceSeries],
    ) {
        let Some(mo) = cfg.momentum.as_ref() else { return };
        let now = chrono::Utc::now().timestamp_millis();
        let user_ceiling = cfg.max_order_size.unwrap_or(f64::INFINITY);
        let (positions_snapshot, mut free_capital) = {
            let mut s = state.write();
            // Prune expired cooldowns while we hold the lock (bounded map).
            s.proposed_recently.retain(|_, t| now - *t < PROPOSAL_COOLDOWN_MS);
            let deployed: f64 = s.positions.values().map(|p| p.size * p.entry_price).sum();
            (s.positions.clone(), (cfg.capital - deployed).max(0.0))
        };
        let mut proposals = propose_momentum(
            mo,
            series,
            &positions_snapshot,
            now,
            cfg.min_order_size,
            user_ceiling,
            cfg.min_shares,
            cfg.capital,
        );
        if proposals.is_empty() {
            return;
        }
        // Exits first — freed capital funds this cycle's entries.
        proposals.sort_by_key(|p| if p.side == "SELL" { 0u8 } else { 1u8 });

        let mut placed_this_cycle = 0usize;
        for p in proposals {
            if cancel.load(Ordering::Acquire) {
                break;
            }
            let dedup_key = format!(
                "{}:{}:{}",
                p.condition_id.to_lowercase(),
                p.outcome.to_lowercase(),
                p.side
            );
            let last = state.read().proposed_recently.get(&dedup_key).copied().unwrap_or(0);
            if now - last < PROPOSAL_COOLDOWN_MS {
                continue;
            }
            let mark_proposed = |registry: &Arc<Self>| {
                let mut s = state.write();
                s.proposed_recently.insert(dedup_key.clone(), now);
                drop(s);
                registry.persist_state(&cfg.eoa, &state.read());
            };
            let limit_price = tick_round_price(p.limit_price);
            if !(limit_price > 0.0) {
                continue;
            }
            let log_id = format!("mo-{}-{}", now, &dedup_key);

            if p.side == "SELL" {
                let Some(pos) = positions_snapshot.get(&p.token_id) else { continue };
                if pos.size <= 0.0 {
                    continue;
                }
                let size = pos.size;
                if !cfg.auto_execute {
                    let msg = format!(
                        "DRY RUN · would SELL {:.0} @ {:.0}¢ (${:.2}) · {} · {}",
                        size, limit_price * 100.0, size * limit_price, p.reason, p.market
                    );
                    tracing::info!(eoa = %cfg.eoa, market = %p.market, "{}", msg);
                    self.log_and_persist(cfg, state, mk_log("DRY_RUN", &log_id, msg, None));
                    mark_proposed(self);
                    continue;
                }
                let neg_risk = self.resolve_neg_risk(&pos.condition_id).await;
                let req = PlaceOrderRequest {
                    eoa: cfg.eoa.clone(),
                    creds: ClobCreds { api_key: String::new(), secret: String::new(), passphrase: String::new() },
                    args: PlaceOrderArgs {
                        token_id: p.token_id.clone(),
                        side: OrderSide::Sell,
                        price: limit_price,
                        size,
                        fee_rate_bps: 0,
                        expiration: 0,
                        signature_type: 3,
                        order_type: OrderTimeInForce::Gtc,
                        neg_risk,
                        maker: cfg.address.clone(),
                    },
                };
                match place_order(&self.http, &self.signer_store, req).await {
                    Ok(resp) => {
                        let proceeds = size * limit_price;
                        let cost_basis = size * pos.entry_price;
                        free_capital += cost_basis;
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        let close = match s.positions.get_mut(&p.token_id) {
                            Some(held) => { held.size = (held.size - size).max(0.0); held.size <= 1e-9 }
                            None => false,
                        };
                        if close { s.positions.remove(&p.token_id); }
                        let key = strat_key(&pos.strategy_id, &cfg.strategy_id);
                        let stats = s.strat_stats.entry(key.clone()).or_default();
                        stats.sells += 1;
                        stats.volume += proceeds;
                        stats.realized += proceeds - cost_basis;
                        stats.last_fill_at = now;
                        push_realized(&mut s.realized_events, key, proceeds - cost_basis, cost_basis, now);
                        push_log(&mut s.log, mk_log(
                            "COPY_SELL",
                            &log_id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ · realized {:+.2} · {} · {}",
                                size, limit_price * 100.0, proceeds - cost_basis, p.reason, p.market
                            ),
                            None,
                        ));
                        drop(s);
                        tracing::info!(eoa = %cfg.eoa, market = %p.market, size, price = limit_price, response = %resp, "momentum sell placed");
                    }
                    Err(e) => {
                        let mut s = state.write();
                        s.total_orders_failed += 1;
                        push_log(&mut s.log, mk_log(
                            "ERROR",
                            &log_id,
                            format!("MOMENTUM_SELL_FAILED: {} · {}", e, p.market),
                            None,
                        ));
                        drop(s);
                        tracing::warn!(eoa = %cfg.eoa, market = %p.market, error = %e, "momentum sell failed");
                    }
                }
                mark_proposed(self);
                tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
            } else {
                // BUY entry.
                if placed_this_cycle >= cfg.max_orders_per_cycle {
                    self.log_and_persist(cfg, state, mk_log(
                        "INFO",
                        &format!("mo-cap-{}", now),
                        format!(
                            "order cap {} reached this cycle — remaining momentum entries deferred",
                            cfg.max_orders_per_cycle
                        ),
                        None,
                    ));
                    break;
                }
                let size = ((p.notional / limit_price).ceil().max(cfg.min_shares) * 100.0).round() / 100.0;
                let notional = size * limit_price;
                if !cfg.auto_execute {
                    let msg = format!(
                        "DRY RUN · would BUY {:.0} @ {:.0}¢ (${:.2}) · {} · {}",
                        size, limit_price * 100.0, notional, p.reason, p.market
                    );
                    tracing::info!(eoa = %cfg.eoa, market = %p.market, "{}", msg);
                    self.log_and_persist(cfg, state, mk_log("DRY_RUN", &log_id, msg, None));
                    mark_proposed(self);
                    continue;
                }
                if notional > free_capital + 1e-6 {
                    self.log_and_persist(cfg, state, mk_log(
                        "SKIP",
                        &log_id,
                        format!(
                            "MOMENTUM_NO_CAPITAL · need ${:.2}, free ${:.2} · {}",
                            notional, free_capital, p.market
                        ),
                        None,
                    ));
                    mark_proposed(self);
                    continue;
                }
                let neg_risk = self.resolve_neg_risk(&p.condition_id).await;
                let req = PlaceOrderRequest {
                    eoa: cfg.eoa.clone(),
                    creds: ClobCreds { api_key: String::new(), secret: String::new(), passphrase: String::new() },
                    args: PlaceOrderArgs {
                        token_id: p.token_id.clone(),
                        side: OrderSide::Buy,
                        price: limit_price,
                        size,
                        fee_rate_bps: 0,
                        expiration: 0,
                        signature_type: 3,
                        order_type: OrderTimeInForce::Gtc,
                        neg_risk,
                        maker: cfg.address.clone(),
                    },
                };
                placed_this_cycle += 1;
                match place_order(&self.http, &self.signer_store, req).await {
                    Ok(resp) => {
                        free_capital = (free_capital - notional).max(0.0);
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += notional;
                        let entry = s.positions.entry(p.token_id.clone()).or_insert_with(|| OpenPosition {
                            token_id: p.token_id.clone(),
                            condition_id: p.condition_id.clone(),
                            market: p.market.clone(),
                            size: 0.0,
                            entry_price: 0.0,
                            // Momentum entries carry no EP score (no trader
                            // stats behind them) — 0 keeps them first in line
                            // for capital rotation against scored mirrors.
                            entry_score: 0.0,
                            opened_at: now,
                            strategy_id: cfg.strategy_id.clone(),
                        });
                        let new_size = entry.size + size;
                        if new_size > 0.0 {
                            entry.entry_price = (entry.size * entry.entry_price + size * limit_price) / new_size;
                        }
                        entry.size = new_size;
                        if entry.strategy_id.is_empty() {
                            entry.strategy_id = cfg.strategy_id.clone();
                        }
                        let stats = s
                            .strat_stats
                            .entry(strat_key(&cfg.strategy_id, ""))
                            .or_default();
                        stats.buys += 1;
                        stats.volume += notional;
                        stats.last_fill_at = now;
                        push_log(&mut s.log, mk_log(
                            "COPY_BUY",
                            &log_id,
                            format!(
                                "BUY {:.0} @ {:.0}¢ (${:.2}) · {} · {}",
                                size, limit_price * 100.0, notional, p.reason, p.market
                            ),
                            None,
                        ));
                        drop(s);
                        tracing::info!(eoa = %cfg.eoa, market = %p.market, size, price = limit_price, notional, response = %resp, "momentum entry placed");
                    }
                    Err(e) => {
                        let mut s = state.write();
                        s.total_orders_failed += 1;
                        push_log(&mut s.log, mk_log(
                            "ERROR",
                            &log_id,
                            format!("MOMENTUM_BUY_FAILED: {} · {}", e, p.market),
                            None,
                        ));
                        drop(s);
                        tracing::warn!(eoa = %cfg.eoa, market = %p.market, error = %e, "momentum entry failed");
                    }
                }
                mark_proposed(self);
                tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
            }
        }
    }

    /// Mirror a leader's SELL: proportionally exit a token the engine holds
    /// because a watched trader just exited it — the same exit replay the
    /// backtest sim performs (`shares = min(mirrorNotional/price, held)`), so
    /// live PnL tracks the preview instead of holding every position to its
    /// stop/redeem. Sizing reuses `plan_mirror` (same floors/ceiling as
    /// entries) capped at the held size; the fill is a marketable SELL at the
    /// tick-rounded best bid. Exits deliberately do NOT count toward
    /// `max_orders_per_cycle` (like stop-losses — an exit signal is never
    /// deferred) and, like the backtest, bypass the semantic entry filters.
    /// Leader exits of tokens we never entered are skipped silently — the
    /// per-trader cursor guarantees each trade is only considered once.
    async fn execute_mirror_sells(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        candidates: Vec<(ObservedTrade, f64)>,
    ) {
        for (trade, copy_ratio) in candidates {
            if cancel.load(Ordering::Acquire) { break; }
            if state.read().copied_ids.contains(&trade.id) { continue; }

            let Some(pos) = state.read().positions.get(&trade.token_id).cloned() else { continue };
            if pos.size <= 0.0 { continue; }

            // Proportional exit through the same clamps entries use, capped at
            // what we hold. If the remainder would be an un-sellable stub
            // (< min_shares), exit the whole position instead.
            let ceiling = cfg.max_order_size.unwrap_or(f64::INFINITY);
            let planned = match plan_mirror(&trade, copy_ratio, cfg.min_order_size, cfg.min_shares, ceiling, cfg.max_slippage_bps) {
                MirrorPlan::Skip(reason) => {
                    self.log_and_persist(cfg, state, mk_log(
                        "SKIP", &trade.id, format!("SELL {}", reason), Some(&trade.trader),
                    ));
                    continue;
                }
                MirrorPlan::Place { size, .. } => size,
            };
            let mut size = planned.min(pos.size);
            if pos.size - size < cfg.min_shares { size = pos.size; }

            // DRY RUN: surface the exit intent, place nothing, leave the trade
            // un-copied — mirrors the BUY-side dry-run contract.
            if !cfg.auto_execute {
                let msg = format!(
                    "DRY RUN · would SELL {:.0} of {:.0} held @ bid · leader exited · {} · token {}",
                    size, pos.size, trade.market, short_token(&trade.token_id),
                );
                tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, "{}", msg);
                self.log_and_persist(cfg, state, mk_log("DRY_RUN", &trade.id, msg, Some(&trade.trader)));
                continue;
            }

            let bid = match fetch_best_bid(&self.http, &trade.token_id).await {
                Some(b) => tick_round_price(b),
                None => {
                    // No book — market likely resolved; auto-redeem's job.
                    self.log_and_persist(cfg, state, mk_log(
                        "SKIP",
                        &trade.id,
                        format!("SELL_NO_BID · can't price exit for {} — left to auto-redeem", trade.market),
                        Some(&trade.trader),
                    ));
                    continue;
                }
            };

            let neg_risk = self.resolve_neg_risk(&pos.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: cfg.eoa.clone(),
                creds: ClobCreds { api_key: String::new(), secret: String::new(), passphrase: String::new() },
                args: PlaceOrderArgs {
                    token_id: trade.token_id.clone(),
                    side: OrderSide::Sell,
                    price: bid,
                    size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: cfg.address.clone(),
                },
            };
            match place_order(&self.http, &self.signer_store, req).await {
                Ok(resp) => {
                    let proceeds = size * bid;
                    let cost_basis = size * pos.entry_price;
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        // Shrink (or close) the tracked position; a partial
                        // exit leaves the avg entry price unchanged.
                        let close = match s.positions.get_mut(&trade.token_id) {
                            Some(p) => { p.size = (p.size - size).max(0.0); p.size <= 1e-9 }
                            None => false,
                        };
                        if close { s.positions.remove(&trade.token_id); }
                        let key = strat_key(&pos.strategy_id, &cfg.strategy_id);
                        let now = chrono::Utc::now().timestamp_millis();
                        s.exited_recently.insert(trade.token_id.clone(), now);
                        let stats = s.strat_stats.entry(key.clone()).or_default();
                        stats.sells += 1;
                        stats.volume += proceeds;
                        stats.realized += proceeds - cost_basis;
                        stats.last_fill_at = now;
                        push_realized(&mut s.realized_events, key, proceeds - cost_basis, cost_basis, now);
                        push_log(&mut s.log, mk_log(
                            "COPY_SELL",
                            &trade.id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ · leader exited · realized {:+.2} · {}",
                                size, bid * 100.0, proceeds - cost_basis, trade.market
                            ),
                            Some(&trade.trader),
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price = bid, response = %resp, "mirror sell placed");
                }
                Err(e) => {
                    let mut s = state.write();
                    s.total_orders_failed += 1;
                    // Mark copied so a hard rejection isn't retried every cycle.
                    insert_copied_id(&mut s.copied_ids, trade.id.clone());
                    push_log(&mut s.log, mk_log(
                        "ERROR",
                        &trade.id,
                        format!("SELL_FAILED: {} · {}", e, trade.market),
                        Some(&trade.trader),
                    ));
                }
            }
            self.persist_state(&cfg.eoa, &state.read());
            tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
        }
    }

    /// Sell the lowest-score held positions that `candidate` out-scores by the
    /// configured margin, until at least `needed` USDC of cost basis is freed.
    /// Returns the cost basis actually freed (so the caller can update its
    /// capital budget). Each exit is a marketable SELL at the token's best book
    /// bid; like all exits it does NOT count toward `max_orders_per_cycle`
    /// (that cap is the BUY budget, matching the strat's `maxPerCycle`).
    /// Positions whose book can't be priced are left untouched.
    async fn free_capital_via_sells(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        candidate: &ObservedTrade,
        needed: f64,
    ) -> f64 {
        let margin = 1.0 + cfg.rebalance_margin_pct.max(0.0);
        // Snapshot the sell-eligible holds: out-scored by the margin, a
        // different token, non-empty — weakest score first (sacrifice the worst
        // hold before a better one).
        let mut sellable: Vec<OpenPosition> = {
            let s = state.read();
            s.positions
                .values()
                .filter(|p| {
                    p.token_id != candidate.token_id
                        && p.size > 0.0
                        && candidate.score >= p.entry_score * margin
                })
                .cloned()
                .collect()
        };
        sellable.sort_by(|a, b| {
            a.entry_score.partial_cmp(&b.entry_score).unwrap_or(std::cmp::Ordering::Equal)
        });

        let mut freed = 0.0f64;
        for pos in sellable {
            if cancel.load(Ordering::Acquire) { break; }
            if freed >= needed { break; }

            // Marketable exit price: the current best bid, tick-rounded.
            let bid = match fetch_best_bid(&self.http, &pos.token_id).await {
                Some(b) => tick_round_price(b),
                None => {
                    self.log_and_persist(cfg, state, mk_log(
                        "SKIP",
                        &pos.token_id,
                        format!("REBALANCE_NO_BID · can't price exit for {}", pos.market),
                        None,
                    ));
                    continue;
                }
            };
            let neg_risk = self.resolve_neg_risk(&pos.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: cfg.eoa.clone(),
                creds: ClobCreds { api_key: String::new(), secret: String::new(), passphrase: String::new() },
                args: PlaceOrderArgs {
                    token_id: pos.token_id.clone(),
                    side: OrderSide::Sell,
                    price: bid,
                    size: pos.size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: cfg.address.clone(),
                },
            };

            match place_order(&self.http, &self.signer_store, req).await {
                Ok(resp) => {
                    let cost_basis = pos.size * pos.entry_price;
                    freed += cost_basis;
                    {
                        let mut s = state.write();
                        s.positions.remove(&pos.token_id);
                        s.total_orders_placed += 1;
                        let proceeds = pos.size * bid;
                        let key = strat_key(&pos.strategy_id, &cfg.strategy_id);
                        let now = chrono::Utc::now().timestamp_millis();
                        s.exited_recently.insert(pos.token_id.clone(), now);
                        let stats = s.strat_stats.entry(key.clone()).or_default();
                        stats.sells += 1;
                        stats.volume += proceeds;
                        stats.realized += proceeds - cost_basis;
                        stats.last_fill_at = now;
                        push_realized(&mut s.realized_events, key, proceeds - cost_basis, cost_basis, now);
                        push_log(&mut s.log, mk_log(
                            "REBALANCE_SELL",
                            &pos.token_id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ · freed ${:.2} (score {:.3}) to fund score {:.3} · {}",
                                pos.size, bid * 100.0, cost_basis, pos.entry_score, candidate.score, pos.market
                            ),
                            None,
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %pos.market, size = pos.size, price = bid, freed = cost_basis, response = %resp, "rebalance sell placed");
                }
                Err(e) => {
                    self.log_and_persist(cfg, state, mk_log(
                        "ERROR",
                        &pos.token_id,
                        format!("REBALANCE_SELL_FAILED: {}", e),
                        None,
                    ));
                }
            }
            self.persist_state(&cfg.eoa, &state.read());
            tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
        }
        freed
    }

    /// Protective/terminal exits over every held position, one bid fetch each:
    /// TAKE-PROFIT sells a position whose best bid has run to ≥ `take_profit`
    /// (0.99 = the book's top tick, i.e. the market ran to 100% — decided,
    /// nothing left to earn, free the capital now instead of waiting for
    /// resolution + auto-redeem); STOP-LOSS sells one whose bid has decayed
    /// to ≤ entry price × `stop_loss` (0.75 = three quarters of entry).
    /// Exits are whole-position marketable SELLs at the tick-rounded bid —
    /// partial trims just re-trigger next cycle at a worse price. Protective
    /// exits deliberately do NOT count toward `max_orders_per_cycle`: a busy
    /// buy cycle must never defer damage control. Positions with no book
    /// (market resolved → auto-redeem's job) or no bid are skipped.
    async fn check_stop_losses(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
    ) {
        let held: Vec<OpenPosition> = {
            let s = state.read();
            s.positions.values().filter(|p| p.size > 0.0).cloned().collect()
        };
        for (idx, pos) in held.iter().enumerate() {
            if cancel.load(Ordering::Acquire) { break; }
            // Same request spacing as the trader fan-out — bounded by
            // max_open_positions holds, so this stays well under rate limits.
            if idx > 0 {
                tokio::time::sleep(Duration::from_millis(cfg.inter_request_delay_ms)).await;
            }
            let Some(raw_bid) = fetch_best_bid(&self.http, &pos.token_id).await else { continue };
            // Take-profit wins when both could read as true (a >1¢-entry
            // position can't hit both, but the label should never lie).
            let tp_hit = take_profit_hit(raw_bid, cfg.take_profit);
            let sl_hit = !tp_hit && stop_loss_hit(pos.entry_price, raw_bid, cfg.stop_loss);
            if !tp_hit && !sl_hit { continue; }
            let bid = tick_round_price(raw_bid);

            let neg_risk = self.resolve_neg_risk(&pos.condition_id).await;
            let req = PlaceOrderRequest {
                eoa: cfg.eoa.clone(),
                creds: ClobCreds { api_key: String::new(), secret: String::new(), passphrase: String::new() },
                args: PlaceOrderArgs {
                    token_id: pos.token_id.clone(),
                    side: OrderSide::Sell,
                    price: bid,
                    size: pos.size,
                    fee_rate_bps: 0,
                    expiration: 0,
                    signature_type: 3,
                    order_type: OrderTimeInForce::Gtc,
                    neg_risk,
                    maker: cfg.address.clone(),
                },
            };
            match place_order(&self.http, &self.signer_store, req).await {
                Ok(resp) => {
                    let cost_basis = pos.size * pos.entry_price;
                    let proceeds = pos.size * bid;
                    {
                        let mut s = state.write();
                        s.positions.remove(&pos.token_id);
                        s.total_orders_placed += 1;
                        let key = strat_key(&pos.strategy_id, &cfg.strategy_id);
                        let now = chrono::Utc::now().timestamp_millis();
                        s.exited_recently.insert(pos.token_id.clone(), now);
                        let stats = s.strat_stats.entry(key.clone()).or_default();
                        stats.sells += 1;
                        stats.volume += proceeds;
                        stats.realized += proceeds - cost_basis;
                        stats.last_fill_at = now;
                        push_realized(&mut s.realized_events, key, proceeds - cost_basis, cost_basis, now);
                        let entry = if tp_hit {
                            mk_log(
                                "TAKE_PROFIT",
                                &pos.token_id,
                                format!(
                                    "SELL {:.0} @ {:.0}¢ · market ran to the top — liquidated at the bid · realized {:+.2} · {}",
                                    pos.size, bid * 100.0, proceeds - cost_basis, pos.market
                                ),
                                None,
                            )
                        } else {
                            mk_log(
                                "STOP_LOSS",
                                &pos.token_id,
                                format!(
                                    "SELL {:.0} @ {:.0}¢ · entry {:.0}¢ decayed past the {:.0}% stop · realized {:+.2} · {}",
                                    pos.size, bid * 100.0, pos.entry_price * 100.0,
                                    cfg.stop_loss.unwrap_or(0.0) * 100.0, proceeds - cost_basis, pos.market
                                ),
                                None,
                            )
                        };
                        push_log(&mut s.log, entry);
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %pos.market, size = pos.size, entry = pos.entry_price, bid, take_profit = tp_hit, response = %resp, "protective exit placed");
                }
                Err(e) => {
                    self.log_and_persist(cfg, state, mk_log(
                        "ERROR",
                        &pos.token_id,
                        format!(
                            "{}_SELL_FAILED: {} · {}",
                            if tp_hit { "TAKE_PROFIT" } else { "STOP_LOSS" }, e, pos.market
                        ),
                        None,
                    ));
                    continue;
                }
            }
            self.persist_state(&cfg.eoa, &state.read());
            tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
        }
    }

    fn log_and_persist(&self, cfg: &EngineConfig, state: &Arc<RwLock<EngineState>>, entry: LogEntry) {
        {
            let mut s = state.write();
            push_log(&mut s.log, entry);
        }
        self.persist_state(&cfg.eoa, &state.read());
    }
}

// ─── Mirror sizing ──────────────────────────────────────────────────────

enum MirrorPlan {
    Place { size: f64, notional: f64, price: f64 },
    Skip(String),
}

/// Polymarket's own hard floor: $1 notional. Distinct from the owner's
/// configurable `min_order` floor — conflating them made the LEADER_DUST
/// test use the owner's $5 default, so live skipped leader trades in the
/// $2.50–$5 band that the backtest clamped up and executed.
const POLYMARKET_MIN_USD: f64 = 1.0;

/// Smallest mirror notional the CLOB accepts at this price — the larger of
/// the $1 hard floor and the `min_shares × price` floor. Matches strat.ts
/// `clobMinNotional` (parity-fixture-tested); the owner's floor is applied
/// separately in `plan_mirror`.
fn clob_min_notional(price: f64, min_shares: f64) -> f64 {
    (min_shares * price.max(1e-9)).max(POLYMARKET_MIN_USD)
}

/// Round a price to the 1¢ tick grid. Leader fills arrive with full f64
/// precision which trips "Price breaks minimum tick size"; 2dp always lands
/// on a tick. Matches copyEngine.ts `tickRoundPrice`.
/// Stop-loss trigger: sell once the exit price (best bid) has decayed to
/// ≤ `stop_loss` × entry. Fractions outside (0, 1) are treated as "off" —
/// a 1.0+ stop would liquidate every position on the spread alone, and 0
/// can never trigger.
fn stop_loss_hit(entry_price: f64, bid: f64, stop_loss: Option<f64>) -> bool {
    match stop_loss {
        Some(sl) if sl > 0.0 && sl < 1.0 && entry_price > 0.0 && bid > 0.0 => {
            bid <= entry_price * sl
        }
        _ => false,
    }
}

/// Take-profit trigger: liquidate once the exit price (best bid) has run to
/// ≥ `take_profit`. Levels above 0.99 clamp to 0.99 — the book's top tick;
/// a literal 1.00 bid never prints, so an unclamped "sell at 100%" could
/// never fire. 0/None = off. Mirror of strat.ts `takeProfitTriggered`
/// (parity-fixture-tested).
fn take_profit_hit(bid: f64, take_profit: Option<f64>) -> bool {
    match take_profit {
        Some(tp) if tp > 0.0 && bid > 0.0 => bid >= tp.min(0.99),
        _ => false,
    }
}

fn tick_round_price(p: f64) -> f64 {
    if !p.is_finite() { return 0.0; }
    (p * 100.0).round() / 100.0
}

/// Widen a leader's price by `slippage_bps` toward the fillable side
/// (BUY = up, SELL = down) so mirrors don't sit unfilled behind the market.
/// Line-for-line port of strat.ts `adjustPrice` + `tickRoundPrice`.
fn widen_limit_price(price: f64, side: &str, slippage_bps: u32) -> f64 {
    let bps = slippage_bps as f64 / 10_000.0;
    let widened = if side == "SELL" {
        (price * (1.0 - bps)).max(0.01)
    } else {
        (price * (1.0 + bps)).min(0.99)
    };
    tick_round_price(widened.clamp(0.01, 0.99))
}

/// Decide what to mirror for one leader trade. `user_floor` is the user's
/// configured minimum order; a proportional notional below the effective
/// floor (max of user floor and CLOB hard floor) is clamped UP so it fills,
/// unless the leader's own trade was sub-CLOB-floor dust (then we skip
/// rather than over-mirror). Line-for-line port of strat.ts `sizeAndPrice`
/// — the same hook the backtest sim and browser engine call.
fn plan_mirror(
    trade: &ObservedTrade,
    copy_ratio: f64,
    user_floor: f64,
    min_shares: f64,
    ceiling: f64,
    slippage_bps: u32,
) -> MirrorPlan {
    let pm_floor = clob_min_notional(trade.price, min_shares);
    if ceiling < pm_floor {
        return MirrorPlan::Skip(format!(
            "CEILING_BELOW_FLOOR · ${:.2} < CLOB min ${:.2}",
            ceiling, pm_floor
        ));
    }
    let raw = trade.notional * copy_ratio;
    let min_notional = user_floor.max(pm_floor);
    let notional = if raw < min_notional {
        if trade.notional < pm_floor {
            return MirrorPlan::Skip(format!(
                "LEADER_DUST · leader ${:.2} < CLOB floor ${:.2}",
                trade.notional, pm_floor
            ));
        }
        // Clamp the sub-floor mirror UP to the floor so it actually fills.
        min_notional.min(ceiling)
    } else {
        raw.min(ceiling)
    };
    let price = widen_limit_price(trade.price, &trade.side, slippage_bps);
    // Whole shares on the 1¢ grid, with the strat's share floor as a backstop.
    let size = (notional / trade.price.max(1e-9)).ceil().max(min_shares);
    MirrorPlan::Place { size, notional, price }
}

fn insert_copied_id(set: &mut HashSet<String>, id: String) {
    if set.len() >= COPIED_IDS_CAP {
        // Cheap bound: clear when full. Worst case we briefly lose dedup
        // history, but the per-trader cursor already prevents re-observing
        // old trades, so a rare double-copy window is acceptable.
        set.clear();
    }
    set.insert(id);
}

fn short_token(t: &str) -> String {
    if t.len() <= 12 { t.to_string() } else { format!("{}…{}", &t[..6], &t[t.len() - 4..]) }
}

/// Build a `LogEntry` stamped at the current wall-clock ms.
fn mk_log(kind: &str, id: &str, reason: String, trader: Option<&str>) -> LogEntry {
    LogEntry {
        id: format!("{}-{}", kind.to_lowercase(), id),
        timestamp: chrono::Utc::now().timestamp_millis(),
        kind: kind.to_string(),
        reason: Some(reason),
        trader_address: trader.map(|t| t.to_string()),
        trades_seen: None,
    }
}

// ─── HTTP helpers ──────────────────────────────────────────────────────

async fn fetch_recent_activity(http: &reqwest::Client, address: &str) -> Result<Vec<Value>> {
    let url = format!(
        "{}/activity?user={}&limit=500&offset=0",
        DATA_API,
        address,
    );
    let resp = http.get(&url).send().await.context("activity GET")?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(anyhow::anyhow!("activity HTTP {}: {}", status, text));
    }
    let arr: Value = serde_json::from_str(&text).context("activity parse")?;
    if let Some(items) = arr.as_array() {
        Ok(items.clone())
    } else {
        Ok(Vec::new())
    }
}

// ─── Momentum origination (general, watchlist-free strategies) ──────────
//
// Port of app/lib/strats/strat.ts `proposeMomentum` + copyEngine.ts
// `assembleMarketPrices`, so a strategy needs NO copy-trading watchlist:
// the engine applies the strat's rules over cached market data — each
// candidate market's own price over time — and originates entries/exits
// from those series alone. Keep the three copies (strat.ts, copyEngine.ts,
// here) in sync.

/// Browser parity: PROPOSAL_COOLDOWN_MS in copyEngine.ts. A signal that
/// persists across cycles acts at most once per window.
const PROPOSAL_COOLDOWN_MS: i64 = 30 * 60_000;
/// Browser parity: MARKET_PRICES_TTL_MS — the price-series feed refreshes
/// once per TTL, not per cycle (1-min polling would hammer the CLOB).
const MOMENTUM_SERIES_TTL_MS: i64 = 2 * 60_000;
/// Browser parity: CANDLE_PRICES_TTL_MS — candle mode tracks a market that
/// lives ~5 minutes end to end; the 2-minute TTL above would hand momentum
/// the same frozen series for half the candle's life.
const CANDLE_SERIES_TTL_MS: i64 = 15_000;

/// One market's observed price series. Prices are the FIRST outcome's
/// (index 0); the second outcome's price is its complement (binary
/// markets), so one series covers both sides.
#[derive(Debug, Clone)]
pub struct MarketPriceSeries {
    pub condition_id: String,
    pub market: String,
    /// Outcome names, index-aligned with `token_ids`.
    pub outcomes: [String; 2],
    /// CLOB token ids, index-aligned with `outcomes`.
    pub token_ids: [String; 2],
    /// Market end date (ms epoch); None when unknown.
    pub end_date_ms: Option<i64>,
    /// (ms timestamp, price of outcomes[0]), ascending.
    pub points: Vec<(i64, f64)>,
}

/// A strat-originated trade intent — no upstream trade behind it.
#[derive(Debug, Clone)]
struct MomentumProposal {
    condition_id: String,
    outcome: String,
    token_id: String,
    market: String,
    side: String, // "BUY" | "SELL"
    notional: f64,
    limit_price: f64,
    reason: String,
}

/// Gamma market fields can arrive as real arrays or JSON-encoded strings
/// (`"[\"Yes\",\"No\"]"`) — accept both, like the app's normalizeMarkets.
fn json_string_array(v: Option<&Value>) -> Vec<String> {
    match v {
        Some(Value::Array(a)) => a
            .iter()
            .map(|x| x.as_str().map(str::to_string).unwrap_or_else(|| x.to_string()))
            .collect(),
        Some(Value::String(s)) => serde_json::from_str::<Vec<Value>>(s)
            .map(|a| {
                a.iter()
                    .map(|x| x.as_str().map(str::to_string).unwrap_or_else(|| x.to_string()))
                    .collect()
            })
            .unwrap_or_default(),
        _ => Vec::new(),
    }
}

fn value_as_f64(v: Option<&Value>) -> f64 {
    match v {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => s.parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

/// Momentum price feed: top-volume active markets matching the momentum
/// query (default: the strat's `marketQuery`, else "bitcoin"), each with
/// its first outcome's CLOB price history over the last 6 hours at 5-min
/// fidelity. Per-market failures are skipped, not fatal — momentum works
/// off whatever slice of the feed resolved. Mirrors copyEngine.ts
/// `assembleMarketPrices`.
async fn fetch_momentum_series(
    http: &reqwest::Client,
    mo: &MomentumParams,
    market_query: Option<&str>,
    inter_request_delay_ms: u64,
) -> Result<Vec<MarketPriceSeries>> {
    // Candle mode: one deterministic live market, no search.
    if let Some(candles) = &mo.candles {
        return fetch_candle_series(http, candles).await;
    }
    let query = mo
        .query
        .as_deref()
        .filter(|q| !q.trim().is_empty())
        .or(market_query.filter(|q| !q.trim().is_empty()))
        .unwrap_or("bitcoin");
    let resp = http
        .get(format!("{}/public-search", GAMMA_API))
        .query(&[("q", query), ("_limit", "60")])
        .send()
        .await
        .context("public-search GET")?;
    let status = resp.status();
    let text = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(anyhow::anyhow!("public-search HTTP {}: {}", status, text));
    }
    let raw: Value = serde_json::from_str(&text).context("public-search parse")?;
    // Search returns {events: [{..., markets: [...]}]} — flatten to markets;
    // an event without embedded markets may itself be market-shaped.
    let mut markets: Vec<Value> = Vec::new();
    let events = raw
        .get("events")
        .and_then(|e| e.as_array())
        .cloned()
        .or_else(|| raw.as_array().cloned())
        .unwrap_or_default();
    for evt in events {
        match evt.get("markets").and_then(|m| m.as_array()) {
            Some(ms) => markets.extend(ms.iter().cloned()),
            None => markets.push(evt),
        }
    }

    struct Candidate {
        condition_id: String,
        question: String,
        outcomes: Vec<String>,
        token_ids: Vec<String>,
        volume: f64,
        end_date_ms: Option<i64>,
    }
    let mut candidates: Vec<Candidate> = markets
        .iter()
        .filter_map(|m| {
            if m.get("active").and_then(|a| a.as_bool()) == Some(false) {
                return None;
            }
            let condition_id = m
                .get("conditionId")
                .or_else(|| m.get("condition_id"))
                .and_then(|c| c.as_str())
                .unwrap_or("")
                .to_string();
            if condition_id.is_empty() {
                return None;
            }
            let token_ids = json_string_array(m.get("clobTokenIds"));
            let outcomes = json_string_array(m.get("outcomes"));
            if token_ids.len() < 2 || outcomes.len() < 2 {
                return None;
            }
            let end_date_ms = m
                .get("endDate")
                .or_else(|| m.get("end_date_iso"))
                .and_then(|d| d.as_str())
                .and_then(|d| chrono::DateTime::parse_from_rfc3339(d).ok())
                .map(|d| d.timestamp_millis());
            Some(Candidate {
                condition_id,
                question: m
                    .get("question")
                    .or_else(|| m.get("title"))
                    .and_then(|q| q.as_str())
                    .unwrap_or("")
                    .to_string(),
                outcomes,
                token_ids,
                volume: value_as_f64(m.get("volume").or_else(|| m.get("volumeNum"))),
                end_date_ms,
            })
        })
        .collect();
    candidates.sort_by(|a, b| b.volume.partial_cmp(&a.volume).unwrap_or(std::cmp::Ordering::Equal));
    candidates.truncate(mo.max_markets.unwrap_or(12).max(1));

    let mut out = Vec::new();
    for (idx, c) in candidates.iter().enumerate() {
        if idx > 0 {
            tokio::time::sleep(Duration::from_millis(inter_request_delay_ms)).await;
        }
        // 6h window at 5-minute fidelity comfortably covers the default
        // 60-minute lookback with points to spare on both sides.
        let url = format!(
            "{}/prices-history?market={}&interval=6h&fidelity=5",
            CLOB_API, c.token_ids[0]
        );
        let Ok(resp) = http.get(&url).send().await else { continue };
        let Ok(text) = resp.text().await else { continue };
        let Ok(parsed) = serde_json::from_str::<Value>(&text) else { continue };
        let Some(history) = parsed.get("history").and_then(|h| h.as_array()) else { continue };
        let mut points: Vec<(i64, f64)> = history
            .iter()
            .filter_map(|pt| {
                let t = pt.get("t").and_then(|t| t.as_i64())?;
                let p = pt.get("p").and_then(|p| p.as_f64())?;
                // prices-history stamps unix SECONDS; series are ms here.
                Some((if t > 1_000_000_000_000 { t } else { t * 1000 }, p))
            })
            .collect();
        points.sort_by_key(|(t, _)| *t);
        if points.len() < 2 {
            continue;
        }
        out.push(MarketPriceSeries {
            condition_id: c.condition_id.clone(),
            market: c.question.clone(),
            outcomes: [c.outcomes[0].clone(), c.outcomes[1].clone()],
            token_ids: [c.token_ids[0].clone(), c.token_ids[1].clone()],
            end_date_ms: c.end_date_ms,
            points,
        });
    }
    Ok(out)
}

/// Deterministic slug of the candle LIVE at `now_ms` for a recurring
/// short-candle series: `<prefix>-<candle start unix seconds>`. Port of
/// strat.ts `candleSlug`.
fn candle_slug(prefix: &str, period_minutes: u64, now_ms: i64) -> String {
    let period = period_minutes.max(1) as i64 * 60;
    format!("{}-{}", prefix, (now_ms / 1000 / period) * period)
}

/// Candle-mode feed — port of copyEngine.ts `assembleCandleSeries`: resolve
/// the live candle market by slug, read its 1-minute price history, and
/// append the current CLOB midpoint as a synthetic "now" point (fidelity-1
/// history can lag ~90s — a third of a 5-minute candle).
async fn fetch_candle_series(
    http: &reqwest::Client,
    candles: &CandleParams,
) -> Result<Vec<MarketPriceSeries>> {
    let now = chrono::Utc::now().timestamp_millis();
    let slug = candle_slug(
        candles.slug_prefix.as_deref().unwrap_or("btc-updown-5m"),
        candles.period_minutes.unwrap_or(5),
        now,
    );
    let resp = http
        .get(format!("{}/markets", GAMMA_API))
        .query(&[("slug", slug.as_str())])
        .send()
        .await
        .context("candle market GET")?;
    let text = resp.text().await.unwrap_or_default();
    let raw: Value = serde_json::from_str(&text).context("candle market parse")?;
    let Some(m) = raw.as_array().and_then(|a| a.first()) else {
        return Ok(Vec::new()); // candle not listed (yet) — idle this refresh
    };
    let condition_id = m
        .get("conditionId")
        .or_else(|| m.get("condition_id"))
        .and_then(|c| c.as_str())
        .unwrap_or("")
        .to_string();
    let token_ids = json_string_array(m.get("clobTokenIds"));
    let outcomes = json_string_array(m.get("outcomes"));
    if condition_id.is_empty() || token_ids.len() < 2 || outcomes.len() < 2 {
        return Ok(Vec::new());
    }
    let url = format!(
        "{}/prices-history?market={}&interval=1h&fidelity=1",
        CLOB_API, token_ids[0]
    );
    let mut points: Vec<(i64, f64)> = Vec::new();
    if let Ok(resp) = http.get(&url).send().await {
        if let Ok(text) = resp.text().await {
            if let Ok(parsed) = serde_json::from_str::<Value>(&text) {
                if let Some(history) = parsed.get("history").and_then(|h| h.as_array()) {
                    points = history
                        .iter()
                        .filter_map(|pt| {
                            let t = pt.get("t").and_then(|t| t.as_i64())?;
                            let p = pt.get("p").and_then(|p| p.as_f64())?;
                            Some((if t > 1_000_000_000_000 { t } else { t * 1000 }, p))
                        })
                        .collect();
                    points.sort_by_key(|(t, _)| *t);
                }
            }
        }
    }
    // Synthetic "now" point from the near-live midpoint.
    if let Ok(resp) = http
        .get(format!("{}/midpoint?token_id={}", CLOB_API, token_ids[0]))
        .send()
        .await
    {
        if let Ok(text) = resp.text().await {
            if let Ok(parsed) = serde_json::from_str::<Value>(&text) {
                let mid = parsed
                    .get("mid")
                    .map(|v| value_as_f64(Some(v)))
                    .unwrap_or(0.0);
                if mid > 0.0 && mid < 1.0
                    && points.last().map_or(true, |(t, _)| *t < now)
                {
                    points.push((now, mid));
                }
            }
        }
    }
    if points.len() < 2 {
        return Ok(Vec::new());
    }
    let end_date_ms = m
        .get("endDate")
        .or_else(|| m.get("end_date_iso"))
        .and_then(|d| d.as_str())
        .and_then(|d| chrono::DateTime::parse_from_rfc3339(d).ok())
        .map(|d| d.timestamp_millis());
    Ok(vec![MarketPriceSeries {
        condition_id,
        market: m
            .get("question")
            .or_else(|| m.get("title"))
            .and_then(|q| q.as_str())
            .unwrap_or("")
            .to_string(),
        outcomes: [outcomes[0].clone(), outcomes[1].clone()],
        token_ids: [token_ids[0].clone(), token_ids[1].clone()],
        end_date_ms,
        points,
    }])
}

/// Price move over a lookback window from an ascending series: latest point
/// vs the latest point AT/BEFORE the window start. Requires real coverage —
/// a brand-new market whose series starts inside the window returns None
/// rather than faking a full-window rise from partial data. Terminal prices
/// (0/1 — resolved) also return None. Port of strat.ts `seriesMomentum`.
fn series_momentum(points: &[(i64, f64)], now: i64, lookback_ms: i64) -> Option<(f64, f64, f64)> {
    if points.len() < 2 {
        return None;
    }
    let cutoff = now - lookback_ms;
    let mut then: Option<(i64, f64)> = None;
    for pt in points {
        if pt.0 <= cutoff {
            then = Some(*pt);
        } else {
            break;
        }
    }
    let (_, p_then) = then?;
    let (_, p_now) = *points.last()?;
    if !(p_now > 0.0 && p_now < 1.0) || !(p_then > 0.0 && p_then < 1.0) {
        return None;
    }
    Some((p_then, p_now, p_now - p_then))
}

/// Port of strat.ts `proposeMomentum`: ENTRIES buy the outcome that rose ≥
/// minRiseCents inside the price band, in markets not already held and not
/// about to resolve; EXITS sell a held outcome once its own price fell ≥
/// exitDropCents over the window. Binary markets: outcomes[1] moves by the
/// complement of the tracked series, so both sides are candidates.
/// `positions` is the engine's ledger keyed by token id.
fn propose_momentum(
    mo: &MomentumParams,
    series: &[MarketPriceSeries],
    positions: &HashMap<String, OpenPosition>,
    now: i64,
    user_floor: f64,
    user_ceiling: f64,
    min_shares: f64,
    capital: f64,
) -> Vec<MomentumProposal> {
    if series.is_empty() {
        return Vec::new();
    }
    let lookback_ms = (mo.lookback_minutes.unwrap_or(60) as i64) * 60_000;
    let min_rise = mo.min_rise_cents.unwrap_or(5.0) / 100.0;
    let exit_drop = mo.exit_drop_cents.or(mo.min_rise_cents).unwrap_or(5.0) / 100.0;
    // Entry floor defaults to the favorite side (≥50¢): momentum rides a
    // leader crossing toward resolution, not sub-50¢ longshots.
    let min_price = mo.min_price.unwrap_or(0.5);
    let max_price = mo.max_price.unwrap_or(0.85);
    let max_positions = mo.max_positions.unwrap_or(5).max(1);
    let min_close_ms = (mo.min_minutes_to_close.unwrap_or(90) as i64) * 60_000;
    let cents = |p: f64| format!("{:.0}", p * 100.0);

    let held_count = positions.values().filter(|p| p.size > 0.0).count();
    let mut proposals: Vec<MomentumProposal> = Vec::new();
    struct Entry<'a> {
        s: &'a MarketPriceSeries,
        idx: usize,
        rise: f64,
        p_now: f64,
        p_then: f64,
    }
    let mut entries: Vec<Entry> = Vec::new();

    for s in series {
        let Some((m_then, m_now, m_rise)) = series_momentum(&s.points, now, lookback_ms) else {
            continue;
        };
        // Holding EITHER side blocks new entries in this market — when a held
        // Yes decays, the No side reads as "rising" the same cycle, and buying
        // it before the exit fills would just lock in a hedged loss.
        let held_in_market = s
            .token_ids
            .iter()
            .any(|tid| positions.get(tid).map_or(false, |p| p.size > 0.0));
        for idx in 0..2usize {
            // outcomes[1] moves by the complement of the tracked series.
            let rise = if idx == 0 { m_rise } else { -m_rise };
            let p_now = if idx == 0 { m_now } else { 1.0 - m_now };
            let p_then = if idx == 0 { m_then } else { 1.0 - m_then };
            let pos = positions
                .get(&s.token_ids[idx])
                .filter(|p| p.size > 0.0);

            // EXIT — we hold this outcome and its price is now falling.
            if let Some(pos) = pos {
                if rise <= -exit_drop {
                    proposals.push(MomentumProposal {
                        condition_id: s.condition_id.clone(),
                        outcome: s.outcomes[idx].clone(),
                        token_id: s.token_ids[idx].clone(),
                        market: s.market.clone(),
                        side: "SELL".into(),
                        notional: pos.size * p_now,
                        limit_price: tick_round_price(p_now * 0.97),
                        reason: format!(
                            "MOMENTUM FLIPPED · {} {}¢→{}¢ (−{}¢) in {}m",
                            s.outcomes[idx],
                            cents(p_then),
                            cents(p_now),
                            cents(-rise),
                            lookback_ms / 60_000
                        ),
                    });
                    continue;
                }
            }

            // ENTRY candidate — rising, in band, market not held, not near
            // resolution.
            if held_in_market || rise < min_rise {
                continue;
            }
            if p_now < min_price || p_now > max_price {
                continue;
            }
            if let Some(end) = s.end_date_ms {
                if end - now < min_close_ms {
                    continue;
                }
            }
            entries.push(Entry { s, idx, rise, p_now, p_then });
        }
    }

    // Strongest rise first, capped to the free position slots.
    let open_slots = max_positions.saturating_sub(held_count);
    entries.sort_by(|a, b| b.rise.partial_cmp(&a.rise).unwrap_or(std::cmp::Ordering::Equal));
    for e in entries.into_iter().take(open_slots) {
        // Equal-slice sizing (capital / maxPositions) clamped into the user's
        // trade band — momentum has no leader notional to scale by.
        let limit_price = tick_round_price(e.p_now + 0.02);
        let raw = capital / max_positions as f64;
        let floor = user_floor.max(clob_min_notional(limit_price, min_shares));
        let notional = raw.max(floor).min(user_ceiling.max(floor));
        proposals.push(MomentumProposal {
            condition_id: e.s.condition_id.clone(),
            outcome: e.s.outcomes[e.idx].clone(),
            token_id: e.s.token_ids[e.idx].clone(),
            market: e.s.market.clone(),
            side: "BUY".into(),
            notional,
            // Chase up to 2¢ past the last observed price so the entry fills.
            limit_price,
            reason: format!(
                "MOMENTUM · {} {}¢→{}¢ (+{}¢) in {}m",
                e.s.outcomes[e.idx],
                cents(e.p_then),
                cents(e.p_now),
                cents(e.rise),
                lookback_ms / 60_000
            ),
        });
    }
    proposals
}

// ─── Auto-watchlist discovery ────────────────────────────────────────────

/// Derive the top traders active in the last `auto.hours` for each gamma tag.
///
/// Per tag: take the highest-24h-volume open events (`tag_slug`), sample up to
/// `markets_per_tag` of their markets, pull each market's recent fills from
/// the data-api, and sum per-wallet notional inside the window (dust fills
/// under $1 are ignored — the 0.001¢ spam buys would otherwise pollute the
/// ranking). Keep the `top_per_tag` wallets per tag, merge across tags by
/// summing notionals, and weight the final list proportionally to notional.
/// `exclude` (own EOA/proxy) never qualifies — copying yourself feeds back.
///
/// `market_query` is the strat's topic filter: discovery only samples (and
/// ranks wallets by) markets whose title passes the SAME
/// `market_matches_query` gate the mirror loop applies to trades. Without
/// this, a tag could elect traders on the strength of markets whose trades
/// the engine would then never choose to copy.
async fn discover_top_traders(
    http: &reqwest::Client,
    auto: &AutoTradersConfig,
    exclude: &HashSet<String>,
    market_query: Option<&str>,
) -> Result<Vec<TraderEntry>> {
    let cutoff_ms =
        chrono::Utc::now().timestamp_millis() - (auto.hours.max(1) as i64) * 3_600_000;
    let mut merged: HashMap<String, f64> = HashMap::new();

    for tag in &auto.tags {
        // Highest-24h-volume open events for the tag; a handful of events is
        // plenty — each carries its own market list.
        let url = format!(
            "{}/events?closed=false&limit=4&order=volume24hr&ascending=false&tag_slug={}",
            GAMMA_API, tag
        );
        let events: Value = match http.get(&url).send().await {
            Ok(r) => serde_json::from_str(&r.text().await.unwrap_or_default())
                .unwrap_or(Value::Null),
            Err(e) => {
                tracing::warn!(tag = %tag, error = %e, "auto-watchlist: events fetch failed");
                continue;
            }
        };
        let mut condition_ids: Vec<String> = Vec::new();
        if let Some(evs) = events.as_array() {
            'outer: for ev in evs {
                let Some(markets) = ev.get("markets").and_then(|m| m.as_array()) else {
                    continue;
                };
                for m in markets {
                    // Same title gate the mirror loop applies to trades —
                    // a market the strat would never copy from must not
                    // elect a trader either.
                    let title = m
                        .get("question")
                        .or_else(|| m.get("title"))
                        .and_then(|q| q.as_str())
                        .unwrap_or("");
                    if let Some(q) = market_query {
                        if !q.trim().is_empty()
                            && !crate::categories::market_matches_query(title, q)
                        {
                            continue;
                        }
                    }
                    if let Some(cid) = m.get("conditionId").and_then(|c| c.as_str()) {
                        condition_ids.push(cid.to_string());
                        if condition_ids.len() >= auto.markets_per_tag.max(1) {
                            break 'outer;
                        }
                    }
                }
            }
        }

        let mut per_tag: HashMap<String, f64> = HashMap::new();
        for (i, cid) in condition_ids.iter().enumerate() {
            if i > 0 {
                // Same Cloudflare rate-limit spacing the poll loop uses.
                tokio::time::sleep(Duration::from_millis(300)).await;
            }
            let url = format!("{}/trades?market={}&limit=200", DATA_API, cid);
            let trades: Value = match http.get(&url).send().await {
                Ok(r) => serde_json::from_str(&r.text().await.unwrap_or_default())
                    .unwrap_or(Value::Null),
                Err(e) => {
                    tracing::warn!(market = %cid, error = %e, "auto-watchlist: trades fetch failed");
                    continue;
                }
            };
            let Some(items) = trades.as_array() else { continue };
            for t in items {
                let wallet = t
                    .get("proxyWallet")
                    .and_then(|w| w.as_str())
                    .unwrap_or("")
                    .to_lowercase();
                if wallet.is_empty() || exclude.contains(&wallet) {
                    continue;
                }
                // data-api trade timestamps are unix SECONDS.
                let ts_ms = t.get("timestamp").and_then(|v| v.as_i64()).unwrap_or(0) * 1000;
                if ts_ms < cutoff_ms {
                    continue;
                }
                let size = t.get("size").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let price = t.get("price").and_then(|v| v.as_f64()).unwrap_or(0.0);
                let notional = size * price;
                if notional < 1.0 {
                    continue;
                }
                *per_tag.entry(wallet).or_insert(0.0) += notional;
            }
        }

        let mut ranked: Vec<(String, f64)> = per_tag.into_iter().collect();
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        for (wallet, notional) in ranked.into_iter().take(auto.top_per_tag.max(1)) {
            *merged.entry(wallet).or_insert(0.0) += notional;
        }
    }

    let total: f64 = merged.values().sum();
    if total <= 0.0 {
        return Ok(Vec::new());
    }
    let mut out: Vec<TraderEntry> = merged
        .into_iter()
        .map(|(address, notional)| TraderEntry {
            address,
            // Floor tiny weights so a whale in one tag can't zero out the
            // other tag's traders entirely.
            weight: (notional / total).max(0.02),
            enabled: true,
        })
        .collect();
    out.sort_by(|a, b| b.weight.partial_cmp(&a.weight).unwrap_or(std::cmp::Ordering::Equal));
    Ok(out)
}

// ─── Server-side Sharpe / ROI scoring ───────────────────────────────────
// Ported from the frontend `CopyIndex.tsx` traderStatsMap so live ranking
// matches the backtest preview byte-for-byte: walk the trader's activity in
// time order keeping a FIFO cost-basis book; each in-window SELL contributes a
// per-trade return `(price − avgCost) / avgCost`. roi = mean(returns),
// sharpe = roi / sample-stdev (n≥3, stdev>1e-6), else 0.

#[derive(Debug, Clone, Copy)]
pub struct TraderRoiStats {
    pub roi: f64,
    pub stdev: f64,
    pub sharpe: f64,
    pub sample_size: usize,
    /// Closed trades that realized a profit (returns > 0) in the window.
    pub wins: usize,
    /// Laplace-smoothed win rate `(wins + 2) / (n + 4)` — the trader's
    /// probability-of-success estimate, shrunk toward 50% on thin samples
    /// (exactly 0.5 with no closed trades). Mirrors `statsFromReturns` in
    /// app/lib/strats/strat.ts; the parity fixture test pins the two.
    pub success_prob: f64,
}

/// The ONE stats formula — everything downstream of a returns series
/// (mean ROI, sample stdev, Sharpe, win rate, success probability) in a
/// single pure function, byte-matched to `statsFromReturns` in strat.ts.
pub fn stats_from_returns(returns: &[f64]) -> TraderRoiStats {
    let n = returns.len();
    let mut roi = 0.0;
    let mut stdev = 0.0;
    let mut wins = 0usize;
    if n > 0 {
        roi = returns.iter().sum::<f64>() / n as f64;
        if n >= 2 {
            let var = returns.iter().map(|r| (r - roi).powi(2)).sum::<f64>() / (n as f64 - 1.0);
            stdev = var.sqrt();
        }
        wins = returns.iter().filter(|r| **r > 0.0).count();
    }
    // Epsilon guard, not `> 0.0`: near-identical returns leave a
    // quantization-noise stdev (float noise ~1e-17, tick-scalper noise
    // ~1e-7) that explodes Sharpe to 1e5–1e15 and puts junk traders on
    // top of the sharpe-sorted leaderboard. Genuine per-trade return
    // dispersion is ≥1e-3; anything under 1e-6 is degenerate → 0.
    let sharpe = if n >= 3 && stdev > 1e-6 { roi / stdev } else { 0.0 };
    let success_prob = (wins as f64 + 2.0) / (n as f64 + 4.0);
    TraderRoiStats { roi, stdev, sharpe, sample_size: n, wins, success_prob }
}

/// Compute a trader's 30d ROI/Sharpe stats from their raw `/activity` items.
/// `now_ms` is the cycle clock (passed in so this stays pure/testable).
/// `market_query` scopes the stats to the strat's topic filter — the backtest
/// (`traderStatsMap`) computes returns from query-matching trades only, so
/// the live score must rank traders on the same filtered slice.
pub fn compute_trader_roi_stats(
    items: &[Value],
    now_ms: i64,
    market_query: Option<&str>,
) -> TraderRoiStats {
    let cutoff_ms = now_ms - SHARPE_WINDOW_DAYS * 86_400_000;

    // Sort TRADE items oldest→newest so FIFO basis is built in order.
    let mut trades: Vec<&Value> = items
        .iter()
        .filter(|t| t.get("type").and_then(|v| v.as_str()).unwrap_or("TRADE") == "TRADE")
        .filter(|t| {
            market_query.map_or(true, |q| {
                let title = t.get("title").and_then(|v| v.as_str()).unwrap_or("");
                crate::categories::market_matches_query(title, q)
            })
        })
        .collect();
    trades.sort_by_key(|t| {
        let raw = t.get("timestamp").and_then(|v| v.as_i64()).unwrap_or(0);
        if raw > 1_000_000_000_000 { raw } else { raw * 1000 }
    });

    // token/condition key → (size, cost) cost-basis book.
    let mut book: HashMap<String, (f64, f64)> = HashMap::new();
    let mut returns: Vec<f64> = Vec::new();

    for t in trades {
        let raw_ts = t.get("timestamp").and_then(|v| v.as_i64()).unwrap_or(0);
        let ts_ms = if raw_ts > 1_000_000_000_000 { raw_ts } else { raw_ts * 1000 };
        let key = t
            .get("conditionId")
            .or_else(|| t.get("asset"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let price = t.get("price").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let size = t.get("size").and_then(|v| v.as_f64()).unwrap_or(0.0);
        let side = t.get("side").and_then(|v| v.as_str()).unwrap_or("").to_uppercase();
        if !price.is_finite() || !size.is_finite() || size <= 0.0 {
            continue;
        }
        let pos = book.entry(key).or_insert((0.0, 0.0));
        if side == "BUY" {
            pos.1 += price * size;
            pos.0 += size;
        } else if side == "SELL" && pos.0 > 0.0 {
            let avg = pos.1 / pos.0;
            let sold = size.min(pos.0);
            pos.1 -= avg * sold;
            pos.0 -= sold;
            // Per-trade ROI on realized SELLs inside the Sharpe window.
            if ts_ms >= cutoff_ms && avg > 0.0 {
                returns.push((price - avg) / avg);
            }
        }
    }

    stats_from_returns(&returns)
}

/// Rank score for one candidate: `P(success) × trader ROI × rawMirrorNotional`,
/// matching the frontend `Strat.scoreCandidate`. `raw_mirror_notional =
/// notional × copy_ratio` (what we'd actually deploy). P is always > 0, so the
/// sign still comes from ROI: a losing trader's copies score negative and are
/// skipped by the NO_EDGE gate, and among winners the higher-win-rate trader's
/// trades rank first at equal dollar edge.
fn candidate_score(stats: &TraderRoiStats, raw_mirror_notional: f64) -> f64 {
    stats.success_prob * stats.roi * raw_mirror_notional
}

/// Best bid (highest buy price) on a token's CLOB order book — the marketable
/// price for an immediate SELL. Tick-rounded by the caller. `None` if the book
/// is empty/unreachable, in which case the exit is skipped this cycle.
async fn fetch_best_bid(http: &reqwest::Client, token_id: &str) -> Option<f64> {
    let url = format!("{}/book?token_id={}", CLOB_API, token_id);
    let resp = http.get(&url).send().await.ok()?;
    let text = resp.text().await.ok()?;
    let parsed: Value = serde_json::from_str(&text).ok()?;
    let bids = parsed.get("bids")?.as_array()?;
    // The CLOB returns bids ascending; the best (highest) bid is the max price.
    let mut best: Option<f64> = None;
    for b in bids {
        let price = b
            .get("price")
            .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))?;
        best = Some(best.map_or(price, |m: f64| m.max(price)));
    }
    best.filter(|p| p.is_finite() && *p > 0.0)
}

fn parse_activity_trade(v: &Value, trader: &str) -> Option<ObservedTrade> {
    if v.get("type").and_then(|t| t.as_str()) != Some("TRADE") { return None; }
    let price = v.get("price").and_then(|p| p.as_f64()).unwrap_or(0.0);
    let size = v.get("size").and_then(|p| p.as_f64()).unwrap_or(0.0);
    if !(price.is_finite()) || !(size.is_finite()) || size <= 0.0 { return None; }
    let raw_ts = v.get("timestamp").and_then(|t| t.as_i64()).unwrap_or(0);
    if raw_ts <= 0 { return None; }
    let timestamp_ms = if raw_ts > 1_000_000_000_000 { raw_ts } else { raw_ts * 1000 };
    let id = v.get("transactionHash").and_then(|h| h.as_str()).unwrap_or("").to_string();
    let side = v.get("side").and_then(|s| s.as_str()).unwrap_or("BUY").to_uppercase();
    Some(ObservedTrade {
        id,
        timestamp: timestamp_ms,
        trader: trader.to_string(),
        market: v.get("title").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        condition_id: v.get("conditionId").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        side,
        size,
        price,
        notional: price * size,
        token_id: v.get("asset").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        outcome: v.get("outcome").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        score: 0.0,                          // stamped later once the trader's
        success_prob: default_success_prob(), // ROI/win-rate stats are known
    })
}

fn push_log(log: &mut Vec<LogEntry>, entry: LogEntry) {
    log.push(entry);
    if log.len() > LOG_CAP {
        let drop = log.len() - LOG_CAP;
        log.drain(0..drop);
    }
}

#[cfg(unix)]
fn restrict_perms(path: &PathBuf) {
    use std::os::unix::fs::PermissionsExt;
    if let Ok(meta) = std::fs::metadata(path) {
        let mut perms = meta.permissions();
        perms.set_mode(0o600);
        let _ = std::fs::set_permissions(path, perms);
    }
}
#[cfg(not(unix))]
fn restrict_perms(_path: &PathBuf) {}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parse_activity_trade_happy_path() {
        let v = json!({
            "type": "TRADE",
            "price": 0.55,
            "size": 10.0,
            "side": "BUY",
            "timestamp": 1700000000,
            "transactionHash": "0xabc",
            "title": "Will X happen?",
            "conditionId": "0xcid",
        });
        let t = parse_activity_trade(&v, "0xtrader").unwrap();
        assert_eq!(t.size, 10.0);
        assert_eq!(t.price, 0.55);
        assert_eq!(t.notional, 5.5);
        assert_eq!(t.timestamp, 1_700_000_000_000); // promoted to ms
        assert_eq!(t.side, "BUY");
    }

    #[test]
    fn parse_activity_skips_non_trade() {
        let v = json!({"type": "MERGE", "size": 1, "price": 0.5, "timestamp": 1});
        assert!(parse_activity_trade(&v, "0x").is_none());
    }

    #[test]
    fn parse_activity_handles_millisecond_timestamps() {
        let v = json!({"type": "TRADE", "price": 0.5, "size": 1.0, "timestamp": 1_700_000_000_000i64});
        let t = parse_activity_trade(&v, "0x").unwrap();
        assert_eq!(t.timestamp, 1_700_000_000_000);
    }

    #[test]
    fn log_buffer_respects_cap() {
        let mut log = Vec::new();
        for i in 0..(LOG_CAP + 50) {
            push_log(&mut log, LogEntry {
                id: format!("e{}", i),
                timestamp: i as i64,
                kind: "T".into(),
                reason: None,
                trader_address: None,
                trades_seen: None,
            });
        }
        assert_eq!(log.len(), LOG_CAP);
        // Oldest entries got dropped — first kept entry's id is e{LOG_CAP+50-1 - LOG_CAP + 1}? Just check it's not e0.
        assert_ne!(log.first().unwrap().id, "e0");
    }

    // Build one activity TRADE item for the scoring tests.
    fn trade_item(cid: &str, side: &str, price: f64, size: f64, ts_ms: i64) -> Value {
        json!({
            "type": "TRADE", "conditionId": cid, "side": side,
            "price": price, "size": size, "timestamp": ts_ms,
        })
    }

    #[test]
    fn roi_stats_fifo_returns_and_sharpe() {
        let now = 2_000_000_000_000i64; // ms, fixed clock
        let d = 86_400_000i64; // 1 day in ms
        // Three round-trips, each its own market, all inside the 30d window.
        // Returns: +0.20, +0.10, −0.10.
        let items = vec![
            trade_item("m1", "BUY", 0.50, 10.0, now - 5 * d),
            trade_item("m1", "SELL", 0.60, 10.0, now - 4 * d), // (0.60-0.50)/0.50 = 0.20
            trade_item("m2", "BUY", 0.40, 10.0, now - 5 * d),
            trade_item("m2", "SELL", 0.44, 10.0, now - 4 * d), // 0.10
            trade_item("m3", "BUY", 0.50, 10.0, now - 5 * d),
            trade_item("m3", "SELL", 0.45, 10.0, now - 4 * d), // -0.10
        ];
        let s = compute_trader_roi_stats(&items, now, None);
        assert_eq!(s.sample_size, 3);
        assert!((s.roi - 0.066_667).abs() < 1e-4, "roi was {}", s.roi);
        assert!((s.stdev - 0.152_753).abs() < 1e-4, "stdev was {}", s.stdev);
        assert!((s.sharpe - 0.436_4).abs() < 1e-3, "sharpe was {}", s.sharpe);
    }

    #[test]
    fn roi_stats_excludes_out_of_window_sells() {
        let now = 2_000_000_000_000i64;
        let d = 86_400_000i64;
        // A SELL 40 days ago is outside the 30d Sharpe window → no samples.
        let items = vec![
            trade_item("m1", "BUY", 0.50, 10.0, now - 41 * d),
            trade_item("m1", "SELL", 0.60, 10.0, now - 40 * d),
        ];
        let s = compute_trader_roi_stats(&items, now, None);
        assert_eq!(s.sample_size, 0);
        assert_eq!(s.roi, 0.0);
        assert_eq!(s.sharpe, 0.0);
    }

    #[test]
    fn roi_stats_sharpe_needs_three_samples() {
        let now = 2_000_000_000_000i64;
        let d = 86_400_000i64;
        // Two realized trades → roi computed, but sharpe stays 0 (n < 3).
        let items = vec![
            trade_item("m1", "BUY", 0.50, 10.0, now - 5 * d),
            trade_item("m1", "SELL", 0.60, 10.0, now - 4 * d),
            trade_item("m2", "BUY", 0.40, 10.0, now - 5 * d),
            trade_item("m2", "SELL", 0.44, 10.0, now - 4 * d),
        ];
        let s = compute_trader_roi_stats(&items, now, None);
        assert_eq!(s.sample_size, 2);
        assert!(s.roi > 0.0);
        assert_eq!(s.sharpe, 0.0);
    }

    #[test]
    fn candidate_score_is_prob_weighted_expected_edge() {
        // score = P(success) × roi × rawMirrorNotional.
        // 6 wins / 8 closed → P = (6+2)/(8+4) = 2/3; roi 0.20, raw 12.5 →
        // (2/3) × 2.50 ≈ $1.667.
        let winner = TraderRoiStats { roi: 0.20, success_prob: 8.0 / 12.0, ..neutral_stats() };
        assert!((candidate_score(&winner, 12.5) - (8.0 / 12.0) * 2.5).abs() < 1e-9);
        // Losing trader → negative score regardless of P (sign comes from
        // ROI), ranks below any break-even hold and trips the NO_EDGE gate.
        let loser = TraderRoiStats { roi: -0.10, success_prob: 0.6, ..neutral_stats() };
        assert!(candidate_score(&loser, 50.0) < 0.0);
        // Equal dollar edge, higher win rate → ranks first.
        let steady = TraderRoiStats { roi: 0.20, success_prob: 0.7, ..neutral_stats() };
        let streaky = TraderRoiStats { roi: 0.20, success_prob: 0.5, ..neutral_stats() };
        assert!(candidate_score(&steady, 10.0) > candidate_score(&streaky, 10.0));
    }

    fn neutral_stats() -> TraderRoiStats {
        stats_from_returns(&[])
    }

    #[test]
    fn rebalance_margin_predicate() {
        // The sell gate: candidate.score >= held.entry_score × (1 + margin).
        let margin = 0.20;
        let held = 1.0;
        let gate = held * (1.0 + margin);
        assert!(1.20 >= gate); // exactly 20% better → eligible
        assert!(1.25 >= gate); // more than 20% better → eligible
        assert!(!(1.19 >= gate)); // under the margin → NOT eligible (no churn)
    }

    /// Cross-language playbook parity — the SAME fixture is asserted by
    /// `npx tsx app/lib/strats/__test__.ts`. If the Rust and TS math for
    /// stats, scoring, stop-loss, take-profit, exit defaults, or the
    /// rebalance margin ever drift, one of the two suites goes red.
    #[test]
    fn playbook_parity_fixture() {
        let fx: Value = serde_json::from_str(include_str!(
            "../../app/app/lib/strats/parity.fixture.json"
        ))
        .expect("parity fixture parses");
        let close = |a: f64, b: f64| (a - b).abs() <= 1e-9 * b.abs().max(1.0);
        let floats = |v: &Value| -> Vec<f64> {
            v.as_array().unwrap().iter().map(|x| x.as_f64().unwrap()).collect()
        };

        for case in fx["statsCases"].as_array().expect("statsCases") {
            let name = case["name"].as_str().unwrap_or("?");
            let got = stats_from_returns(&floats(&case["returns"]));
            let exp = &case["expected"];
            assert!(close(got.roi, exp["roi"].as_f64().unwrap()), "roi[{name}]: {}", got.roi);
            assert!(close(got.stdev, exp["stdev"].as_f64().unwrap()), "stdev[{name}]: {}", got.stdev);
            assert!(close(got.sharpe, exp["sharpe"].as_f64().unwrap()), "sharpe[{name}]: {}", got.sharpe);
            assert_eq!(got.sample_size as u64, exp["sampleSize"].as_u64().unwrap(), "n[{name}]");
            assert_eq!(got.wins as u64, exp["wins"].as_u64().unwrap(), "wins[{name}]");
            assert!(
                close(got.success_prob, exp["successProb"].as_f64().unwrap()),
                "successProb[{name}]: {}",
                got.success_prob
            );
        }

        for case in fx["scoreCases"].as_array().expect("scoreCases") {
            let name = case["name"].as_str().unwrap_or("?");
            let stats = stats_from_returns(&floats(&case["returns"]));
            let raw = case["notional"].as_f64().unwrap() * case["copyRatio"].as_f64().unwrap();
            let got = candidate_score(&stats, raw);
            assert!(
                close(got, case["expectedScore"].as_f64().unwrap()),
                "score[{name}]: got {got}, want {}",
                case["expectedScore"]
            );
        }

        for case in fx["stopLossCases"].as_array().expect("stopLossCases") {
            let entry = case["entry"].as_f64().unwrap();
            let mark = case["mark"].as_f64().unwrap();
            let sl = case["stopLoss"].as_f64().unwrap();
            let want = case["hit"].as_bool().unwrap();
            assert_eq!(
                stop_loss_hit(entry, mark, Some(sl)),
                want,
                "stopLoss(entry={entry}, mark={mark}, sl={sl})"
            );
        }

        // Sizing pipeline — plan_mirror must reproduce the fixture the TS
        // Strat.sizeAndPrice also asserts: same clamps, same skip classes,
        // same slippage-widened tick-rounded limit price. This is what makes
        // "what you backtest is what live places" true beyond the score math.
        for case in fx["sizingCases"].as_array().expect("sizingCases") {
            let name = case["name"].as_str().unwrap_or("?");
            let price = case["price"].as_f64().unwrap();
            let leader_notional = case["leaderNotional"].as_f64().unwrap();
            let trade = observed(
                case["side"].as_str().unwrap(),
                price,
                leader_notional / price,
                "MarketX",
            );
            let plan = plan_mirror(
                &trade,
                case["copyRatio"].as_f64().unwrap(),
                case["userFloor"].as_f64().unwrap(),
                case["minShares"].as_f64().unwrap(),
                case["ceiling"].as_f64().unwrap(),
                case["slippageBps"].as_u64().unwrap() as u32,
            );
            let want_skip = case["expected"]["skip"].as_str();
            match plan {
                MirrorPlan::Skip(reason) => {
                    let code = if reason.starts_with("CEILING") {
                        "CEILING"
                    } else if reason.starts_with("LEADER_DUST") {
                        "DUST"
                    } else {
                        "OTHER"
                    };
                    assert_eq!(Some(code), want_skip, "sizing[{name}]: skipped ({reason})");
                }
                MirrorPlan::Place { notional, price: limit, .. } => {
                    assert!(want_skip.is_none(), "sizing[{name}]: placed but fixture wants skip");
                    let want_notional = case["expected"]["notional"].as_f64().unwrap();
                    let want_limit = case["expected"]["limitPrice"].as_f64().unwrap();
                    assert!(
                        close(notional, want_notional),
                        "sizing[{name}]: notional {notional} want {want_notional}"
                    );
                    assert!(
                        close(limit, want_limit),
                        "sizing[{name}]: limit {limit} want {want_limit}"
                    );
                }
            }
        }

        for case in fx["takeProfitCases"].as_array().expect("takeProfitCases") {
            let mark = case["mark"].as_f64().unwrap();
            let tp = case["takeProfit"].as_f64().unwrap();
            let want = case["hit"].as_bool().unwrap();
            assert_eq!(
                take_profit_hit(mark, Some(tp)),
                want,
                "takeProfit(mark={mark}, tp={tp})"
            );
        }

        assert!(
            close(default_rebalance_margin_pct(), fx["rebalanceMarginPct"].as_f64().unwrap()),
            "rebalance margin drifted from the fixture"
        );
        assert_eq!(
            default_stop_loss(),
            fx["defaults"]["stopLoss"].as_f64(),
            "stop-loss default drifted from the fixture"
        );
        assert_eq!(
            default_take_profit(),
            fx["defaults"]["takeProfit"].as_f64(),
            "take-profit default drifted from the fixture"
        );
    }

    fn observed(side: &str, price: f64, size: f64, market: &str) -> ObservedTrade {
        ObservedTrade {
            id: "t".into(),
            timestamp: 0,
            trader: "0xabc".into(),
            market: market.into(),
            condition_id: "0x1".into(),
            side: side.into(),
            size,
            price,
            notional: price * size,
            token_id: "tok".into(),
            outcome: "Yes".into(),
            score: 0.0,
            success_prob: default_success_prob(),
        }
    }

    #[test]
    fn trade_filters_gate_matches_frontend() {
        // No explicit price band ⇒ BUYs get the favorites-only default floor
        // (mirror of tradeMatchesFilters): ≥60¢ passes, below is skipped.
        let fav = observed("BUY", 0.65, 100.0, "Bitcoin above $100k?");
        let long = observed("BUY", 0.50, 100.0, "Bitcoin above $100k?");
        assert!(trade_passes_filters(&fav, &None));
        assert!(trade_passes_filters(&fav, &Some(TradeFilters::default())));
        assert!(!trade_passes_filters(&long, &None));
        assert!(!trade_passes_filters(&long, &Some(TradeFilters::default())));
        // SELLs (exits) are never floored by the default.
        assert!(trade_passes_filters(&observed("SELL", 0.05, 10.0, "x"), &None));
        // Explicit minPrice: 0 opts out of the default floor entirely.
        let opt_out = Some(TradeFilters { min_price: Some(0.0), ..Default::default() });
        assert!(trade_passes_filters(&long, &opt_out));

        // Side gate.
        let buys_only = Some(TradeFilters { sides: Some("buy".into()), ..Default::default() });
        assert!(trade_passes_filters(&fav, &buys_only));
        assert!(!trade_passes_filters(&observed("SELL", 0.7, 10.0, "x"), &buys_only));

        // Explicit price band overrides the default floor: longshots only.
        let longshots = Some(TradeFilters {
            min_price: Some(0.01), max_price: Some(0.20), ..Default::default()
        });
        assert!(trade_passes_filters(&observed("BUY", 0.10, 10.0, "x"), &longshots));
        assert!(!trade_passes_filters(&fav, &longshots));
        // A maxPrice-only band also counts as an explicit opinion — no floor.
        let cap_only = Some(TradeFilters { max_price: Some(0.20), ..Default::default() });
        assert!(trade_passes_filters(&observed("BUY", 0.10, 10.0, "x"), &cap_only));

        // Notional band: skip dust, skip whales.
        let mid_size = Some(TradeFilters {
            min_notional: Some(10.0), max_notional: Some(1000.0), ..Default::default()
        });
        assert!(trade_passes_filters(&observed("BUY", 0.65, 100.0, "x"), &mid_size)); // $65
        assert!(!trade_passes_filters(&observed("BUY", 0.65, 1.0, "x"), &mid_size)); // $0.65
        assert!(!trade_passes_filters(&observed("BUY", 0.65, 10_000.0, "x"), &mid_size)); // $6.5k

        // Category: title must match at least one selected bucket.
        let crypto = Some(TradeFilters {
            categories: Some(vec!["crypto".into()]), ..Default::default()
        });
        assert!(trade_passes_filters(&fav, &crypto)); // "Bitcoin …"
        assert!(!trade_passes_filters(&observed("BUY", 0.65, 10.0, "Presidential debate?"), &crypto));
        // Empty categories list is a no-op, same as the frontend.
        let empty_cats = Some(TradeFilters { categories: Some(vec![]), ..Default::default() });
        assert!(trade_passes_filters(&observed("BUY", 0.65, 10.0, "anything"), &empty_cats));
    }

    #[test]
    fn stop_loss_trigger() {
        // 0.5 stop on a 40¢ entry ⇒ trigger at ≤ 20¢, hold above.
        assert!(stop_loss_hit(0.40, 0.20, Some(0.5)));
        assert!(stop_loss_hit(0.40, 0.05, Some(0.5)));
        assert!(!stop_loss_hit(0.40, 0.21, Some(0.5)));
        assert!(!stop_loss_hit(0.40, 0.39, Some(0.5)));
        // Off: None, 0, and degenerate fractions never fire.
        assert!(!stop_loss_hit(0.40, 0.01, None));
        assert!(!stop_loss_hit(0.40, 0.01, Some(0.0)));
        assert!(!stop_loss_hit(0.40, 0.39, Some(1.0))); // ≥1 would fire on spread alone
        // Unpriceable inputs never fire.
        assert!(!stop_loss_hit(0.0, 0.0, Some(0.5)));
        assert!(!stop_loss_hit(0.40, 0.0, Some(0.5)));
    }

    // ── Momentum origination (port of strat.ts seriesMomentum/proposeMomentum) ──

    fn mo_series(points: Vec<(i64, f64)>) -> MarketPriceSeries {
        MarketPriceSeries {
            condition_id: "0xc1".into(),
            market: "Bitcoin above 100k?".into(),
            outcomes: ["Yes".into(), "No".into()],
            token_ids: ["tokYes".into(), "tokNo".into()],
            end_date_ms: Some(10_000_000 + 24 * 3_600_000),
            points,
        }
    }

    #[test]
    fn series_momentum_matches_strat_ts() {
        let now = 10_000_000i64;
        let lookback = 60 * 60_000i64;
        // 50¢ → 60¢ over the window.
        let pts = vec![(now - lookback - 1000, 0.50), (now - 30 * 60_000, 0.55), (now, 0.60)];
        let (p_then, p_now, rise) = series_momentum(&pts, now, lookback).unwrap();
        assert!((p_then - 0.50).abs() < 1e-9);
        assert!((p_now - 0.60).abs() < 1e-9);
        assert!((rise - 0.10).abs() < 1e-9);
        // No coverage before the window start → None (partial data must not
        // fake a full-window rise).
        let young = vec![(now - 10 * 60_000, 0.50), (now, 0.60)];
        assert!(series_momentum(&young, now, lookback).is_none());
        // Terminal (resolved) prices → None.
        let resolved = vec![(now - lookback - 1000, 0.50), (now, 1.0)];
        assert!(series_momentum(&resolved, now, lookback).is_none());
        // Fewer than 2 points → None.
        assert!(series_momentum(&[(now, 0.5)], now, lookback).is_none());
    }

    #[test]
    fn momentum_proposes_rising_entry_no_watchlist() {
        let now = 10_000_000i64;
        let lookback = 60 * 60_000i64;
        let s = mo_series(vec![(now - lookback - 1000, 0.50), (now, 0.60)]);
        let mo = MomentumParams {
            query: None, lookback_minutes: None, min_rise_cents: None,
            exit_drop_cents: None, min_price: None, max_price: None,
            max_positions: None, max_markets: None, min_minutes_to_close: None, candles: None,
        };
        let props = propose_momentum(&mo, &[s], &HashMap::new(), now, 5.0, f64::INFINITY, 5.0, 1000.0);
        assert_eq!(props.len(), 1);
        let p = &props[0];
        assert_eq!(p.side, "BUY");
        assert_eq!(p.outcome, "Yes");
        assert_eq!(p.token_id, "tokYes");
        // Equal-slice sizing: 1000 / 5 default max positions.
        assert!((p.notional - 200.0).abs() < 1e-6);
        // Entry chases 2¢ past the last print.
        assert!((p.limit_price - 0.62).abs() < 1e-9);

        // The complement side reads as falling — a falling series flips the
        // signal to the No outcome instead.
        let falling = mo_series(vec![(now - lookback - 1000, 0.50), (now, 0.40)]);
        let props = propose_momentum(&mo, &[falling], &HashMap::new(), now, 5.0, f64::INFINITY, 5.0, 1000.0);
        assert_eq!(props.len(), 1);
        assert_eq!(props[0].outcome, "No");
        assert_eq!(props[0].token_id, "tokNo");
    }

    #[test]
    fn momentum_gates_band_close_and_held() {
        let now = 10_000_000i64;
        let lookback = 60 * 60_000i64;
        let mo = MomentumParams {
            query: None, lookback_minutes: None, min_rise_cents: None,
            exit_drop_cents: None, min_price: None, max_price: None,
            max_positions: None, max_markets: None, min_minutes_to_close: None, candles: None,
        };
        // Above the default 85¢ band → no chase.
        let rich = mo_series(vec![(now - lookback - 1000, 0.80), (now, 0.90)]);
        assert!(propose_momentum(&mo, &[rich], &HashMap::new(), now, 5.0, f64::INFINITY, 5.0, 1000.0).is_empty());
        // Below the default 50¢ favorites floor → no longshot entries.
        let longshot = mo_series(vec![(now - lookback - 1000, 0.10), (now, 0.20)]);
        assert!(propose_momentum(&mo, &[longshot], &HashMap::new(), now, 5.0, f64::INFINITY, 5.0, 1000.0).is_empty());
        // Resolving inside minMinutesToClose (90m default) → skipped: sub-hour
        // markets are HFT turf (the movoaev8 postmortem).
        let mut near_close = mo_series(vec![(now - lookback - 1000, 0.50), (now, 0.60)]);
        near_close.end_date_ms = Some(now + 30 * 60_000);
        assert!(propose_momentum(&mo, &[near_close], &HashMap::new(), now, 5.0, f64::INFINITY, 5.0, 1000.0).is_empty());
        // Holding EITHER side of the market blocks new entries in it (a held
        // No reads the rising-Yes cycle as its own decay; buying Yes before
        // the exit fills would lock in a hedged loss). Exit disabled via a
        // high exitDropCents so only the entry gate is under test.
        let s = mo_series(vec![(now - lookback - 1000, 0.50), (now, 0.60)]);
        let mut held = HashMap::new();
        held.insert("tokNo".to_string(), OpenPosition {
            token_id: "tokNo".into(), condition_id: "0xc1".into(), market: "m".into(),
            size: 10.0, entry_price: 0.40, entry_score: 0.0, opened_at: now,
            strategy_id: "s1".into(),
        });
        let no_exit = MomentumParams { exit_drop_cents: Some(20.0), ..mo.clone() };
        assert!(propose_momentum(&no_exit, &[s], &held, now, 5.0, f64::INFINITY, 5.0, 1000.0).is_empty());
    }

    #[test]
    fn momentum_exits_held_outcome_on_drop() {
        let now = 10_000_000i64;
        let lookback = 60 * 60_000i64;
        // Held Yes decays 65¢ → 55¢ (≥ default 5¢ exit drop) → SELL, and the
        // "rising" No side must NOT be bought the same cycle.
        let s = mo_series(vec![(now - lookback - 1000, 0.65), (now, 0.55)]);
        let mut held = HashMap::new();
        held.insert("tokYes".to_string(), OpenPosition {
            token_id: "tokYes".into(), condition_id: "0xc1".into(), market: "m".into(),
            size: 100.0, entry_price: 0.65, entry_score: 0.0, opened_at: now,
            strategy_id: "s1".into(),
        });
        let mo = MomentumParams {
            query: None, lookback_minutes: None, min_rise_cents: None,
            exit_drop_cents: None, min_price: None, max_price: None,
            max_positions: None, max_markets: None, min_minutes_to_close: None, candles: None,
        };
        let props = propose_momentum(&mo, &[s], &held, now, 5.0, f64::INFINITY, 5.0, 1000.0);
        assert_eq!(props.len(), 1);
        let p = &props[0];
        assert_eq!(p.side, "SELL");
        assert_eq!(p.token_id, "tokYes");
        // Exit prices 3% under the current mark so it fills.
        assert!((p.limit_price - tick_round_price(0.55 * 0.97)).abs() < 1e-9);
    }

    #[test]
    fn momentum_config_roundtrips_and_defaults_off() {
        // Old persisted configs (no momentum key) must deserialize with
        // momentum off; a momentum config must survive a round-trip.
        let old = json!({
            "eoa": "0xe", "strategyId": "s", "address": "0xa",
            "traders": [], "capital": 100.0, "intervalMs": 60000
        });
        let cfg: EngineConfig = serde_json::from_value(old).unwrap();
        assert!(cfg.momentum.is_none());
        let with_mo = json!({
            "eoa": "0xe", "strategyId": "s", "address": "0xa",
            "traders": [], "capital": 100.0, "intervalMs": 60000,
            "momentum": {"lookbackMinutes": 30, "minRiseCents": 7}
        });
        let cfg: EngineConfig = serde_json::from_value(with_mo).unwrap();
        let mo = cfg.momentum.clone().expect("momentum should parse");
        assert_eq!(mo.lookback_minutes, Some(30));
        assert_eq!(mo.min_rise_cents, Some(7.0));
        let back = serde_json::to_value(&cfg).unwrap();
        assert_eq!(back["momentum"]["lookbackMinutes"], 30);
    }

    #[test]
    fn candle_slug_matches_strat_ts_fixture() {
        // Same fixtures as app/lib/strats/__test__.ts — the two languages
        // must address the same live candle.
        assert_eq!(
            candle_slug("btc-updown-5m", 5, 1_784_659_300_000),
            "btc-updown-5m-1784659200"
        );
        assert_eq!(
            candle_slug("btc-updown-5m", 5, 1_784_659_500_000),
            "btc-updown-5m-1784659500"
        );
    }

    #[test]
    fn gamma_field_parsing_tolerates_stringified_arrays() {
        // Gamma serves clobTokenIds/outcomes as JSON-encoded strings on some
        // endpoints and real arrays on others — both must parse.
        let as_string = json!("[\"Yes\",\"No\"]");
        assert_eq!(json_string_array(Some(&as_string)), vec!["Yes", "No"]);
        let as_array = json!(["tok1", "tok2"]);
        assert_eq!(json_string_array(Some(&as_array)), vec!["tok1", "tok2"]);
        assert!(json_string_array(None).is_empty());
        assert_eq!(value_as_f64(Some(&json!("123.5"))), 123.5);
        assert_eq!(value_as_f64(Some(&json!(7))), 7.0);
    }
}
