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
    order_id_of, order_is_resting, place_order, ClobCreds, OrderSide, OrderTimeInForce,
    PlaceOrderArgs, PlaceOrderRequest,
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
/// Wall-clock budget allowed per trader fetch when deriving the fan-out floor.
/// A data-api `/activity` round trip lands in the 200–600ms band, so the upper
/// end is what a cycle must be able to afford for EVERY watched trader.
const TRADER_FETCH_ALLOWANCE_MS: u64 = 600;

/// The cadence a session actually runs at: the strat's requested interval,
/// floored by `min_interval_ms`, and never tighter than the per-trader fan-out
/// itself can physically complete (each trader costs one spacing delay plus a
/// fetch). Without the fan-out term a large watchlist silently drifts — "sync
/// every 30s" with 60 traders needs ~60s of fetching alone — so the engine
/// widens the period instead of pretending, and `/live/status` reports the
/// widened value so the console shows the truth rather than the request.
fn effective_interval_for(cfg: &EngineConfig, enabled_traders: usize) -> u64 {
    let fanout_ms = enabled_traders as u64 * (cfg.inter_request_delay_ms + TRADER_FETCH_ALLOWANCE_MS);
    cfg.interval_ms.max(cfg.min_interval_ms).max(fanout_ms)
}
// There is deliberately no implicit entry-price floor. A 60¢ "likely to win"
// default used to apply to BUYs whenever a strat set no price band, and it was
// the single largest source of "the engine sees 57 leader entries and copies
// none": a filter nobody chose, silently rejecting most of the flow. A price
// band is now exactly what the strat says it is — set `minPrice` to get one.
// Mirror of app/lib/tradeFilters.ts, which carries the same note.
/// What a mirror is sized proportionally to — see `copy_ratio_for`. Mirrors
/// the TS `SizingModel`; serialized as the same lowercase strings.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Sizing {
    /// Proportional to the leader's net worth: risk fidelity.
    #[default]
    Bankroll,
    /// Proportional to the capital the leader deployed this window:
    /// conviction fidelity, placeable on a small account.
    Flow,
}

/// Default `Sizing::Flow` turnover — one window of leader flow per allocation.
fn default_turnover() -> f64 { 1.0 }

/// Default proportional-fidelity limit — see `EngineConfig::max_upscale`.
/// 2× is the most distortion a floor-clamped mirror may carry before the
/// engine would rather place nothing.
fn default_max_upscale() -> Option<f64> { Some(2.0) }
/// Default time-to-resolution floor for MIRRORS (minutes) — see
/// `EngineConfig::min_minutes_to_close`. 60 excludes the 5m/15m/hourly
/// candle games while keeping every ordinary market.
fn default_copy_min_minutes_to_close() -> Option<f64> { Some(60.0) }
/// Default staleness cutoff for MIRRORS (seconds) — see
/// `EngineConfig::max_trade_age_sec`. OFF by default: a 5-minute cutoff
/// refused 85% of observed flow (mostly the history backlog a fresh session
/// pulls on its first cycle) and the console went days without a fill. The
/// gate still exists — set `maxTradeAgeSec` on the strat to re-arm it — but
/// a strat that says nothing about age filters nothing on age.
fn default_max_trade_age_sec() -> Option<f64> { None }
/// How long a leader's bankroll + position book is reused before refetching.
/// Bankrolls move slowly relative to a 30s poll, and each refresh costs one
/// data-api call plus one Polygon read per trader.
const LEADER_BOOK_TTL_MS: i64 = 10 * 60_000;
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
/// Page size for the data-api positions feed. It is also the API's hard cap,
/// so a response of exactly this many rows means the book may be TRUNCATED —
/// `LeaderBook::complete` records that, and the leader-flat sweep sits out
/// rather than read a missing row as "they sold it".
const HELD_POSITIONS_LIMIT: usize = 500;

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
    /// `marketQuery`, else "bitcoin". Comma/pipe-separated groups are searched
    /// SEPARATELY and merged (deduped by condition id) — gamma ranks a
    /// multi-word query by whole-phrase relevance, so one search for five
    /// coins returns the event family that NAMES five coins rather than the
    /// coins' own markets. See `fetch_momentum_series`.
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
    /// ENTRY confirmation window (minutes): the rise must still be intact over
    /// the last `confirm_minutes` — the outcome may not have given ground back
    /// inside that sub-window. 0/absent ⇒ off. `min_rise_cents` compares two
    /// points a lookback apart and is blind to the shape between them, so a
    /// market that ran early and has been sliding since still reads as a rise;
    /// this is what tells a move still going from one that already happened.
    /// ENTRIES only — exits stay on the raw lookback.
    #[serde(rename = "confirmMinutes", default)]
    pub confirm_minutes: Option<u64>,
    /// Entry price band — don't chase near-resolved (or dead) markets.
    /// Defaults 0.5 / 0.85 — momentum rides a leader crossing toward
    /// resolution. This one IS a default; the copy path has none.
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
    /// User's EOA. Together with `strategy_id` it forms the session key —
    /// one wallet can run several strats at once (see `session_key`).
    pub eoa: String,
    /// Saved strat id. Second half of the session key, and the tag every
    /// fill / open position carries so the per-strat ledger can split one
    /// wallet's activity across concurrently-funded strats.
    #[serde(rename = "strategyId")]
    pub strategy_id: String,
    /// Set when the user stopped this session. The config is KEPT (so the
    /// strat's realized ledger and tagged positions stay readable in the
    /// console's per-strat money column) but `resume_persisted` skips it, so
    /// a stopped strat never comes back to life on an API restart.
    #[serde(default)]
    pub stopped: bool,
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
    /// Trader-quality gate. When set, every cycle ranks the enabled watchlist
    /// by each trader's own realized stats (in the strat's market slice) and
    /// mirrors ONLY the top `top_n`. Traders that fall out keep being polled
    /// and observed — they just stop being copied until they climb back.
    /// `None` ⇒ copy every enabled trader. Mirror of `TraderFilter` in
    /// app/lib/types.ts; parity-fixture-tested.
    #[serde(default)]
    pub filter: Option<TraderFilter>,
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
    // ── Proportional-copy fidelity (see `copy_ratio_for`) ──
    /// How far a mirror may be UPSIZED past its proportional notional when
    /// that notional lands below the effective order floor. 2.0 = "never
    /// place more than 2× what proportionality calls for" — everything
    /// smaller is skipped as `SUB_SCALE` rather than silently placed at the
    /// floor. Unbounded upscaling is what turned a $0.20 intent into a $5
    /// order 1064 times and burned the account, so this defaults ON.
    /// `None`/0 ⇒ legacy clamp-to-floor with no fidelity limit.
    #[serde(rename = "maxUpscale", default = "default_max_upscale")]
    pub max_upscale: Option<f64>,
    /// What mirrors are sized proportionally TO — see `copy_ratio_for`.
    /// `bankroll` (default) copies the leader's RISK, `flow` copies their
    /// CONVICTION and is what keeps a small account's mirrors above the order
    /// floor instead of `SUB_SCALE`-skipped.
    #[serde(rename = "sizing", default)]
    pub sizing: Sizing,
    /// `Sizing::Flow` only — how many times the allocation may be deployed
    /// across one lookback window of leader flow. 1.0 ⇒ the window's mirrors
    /// sum to roughly the allocation.
    #[serde(rename = "turnover", default = "default_turnover")]
    pub turnover: f64,
    /// Don't mirror a leader BUY in a market resolving sooner than this many
    /// minutes. Sub-hour Up/Down candles are HFT turf: by the time a 30s
    /// poller sees the fill the candle has already moved, so copying them is
    /// a structural loss, not a strategy. Omitted ⇒ 60; explicit 0 ⇒ off.
    #[serde(rename = "minMinutesToClose", default = "default_copy_min_minutes_to_close")]
    pub min_minutes_to_close: Option<f64>,
    /// Don't mirror a leader BUY older than this many seconds. A fetch
    /// outage (or a paused session) hands the engine a backlog whose prices
    /// are long gone — copying it enters at a level the leader never paid.
    /// Omitted ⇒ 300s; explicit 0 ⇒ off.
    #[serde(rename = "maxTradeAgeSec", default = "default_max_trade_age_sec")]
    pub max_trade_age_sec: Option<f64>,
}

/// Mirror of `TradeFilters` in app/lib/types.ts, applied by
/// `trade_passes_filters` exactly like app/lib/tradeFilters.ts
/// `tradeMatchesFilters` — keep the three in sync. Every set dimension is
/// AND-ed; an unset dimension passes everything.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct TradeFilters {
    // Unset dimensions are OMITTED on the wire, not sent as null: an
    // allocation's filters round-trip through the copy book and through
    // identity.fixture.json, where they are compared key for key against the
    // browser's object (app/lib/identityStrat.ts), which omits them too.
    /// "buy" | "sell" | "both"/None (no side restriction).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sides: Option<String>,
    /// Leader fill-price band (0–1 probability).
    #[serde(rename = "minPrice", default, skip_serializing_if = "Option::is_none")]
    pub min_price: Option<f64>,
    #[serde(rename = "maxPrice", default, skip_serializing_if = "Option::is_none")]
    pub max_price: Option<f64>,
    /// Leader trade USD notional band (price × size).
    #[serde(rename = "minNotional", default, skip_serializing_if = "Option::is_none")]
    pub min_notional: Option<f64>,
    #[serde(rename = "maxNotional", default, skip_serializing_if = "Option::is_none")]
    pub max_notional: Option<f64>,
    /// Category slugs the market title must match at least ONE of.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub categories: Option<Vec<String>>,
    /// MARKET SENTIMENT — which way the crowd had moved the odds on the
    /// leader's own outcome token when they took the trade (sentiment.rs).
    /// The one dimension here that is a property of the MARKET rather than of
    /// the trade, so it needs data the trade doesn't carry: it is applied in a
    /// second pass over the cycle's candidates, NOT by `trade_filter_reject`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sentiment: Option<crate::sentiment::SentimentFilter>,
}

/// Apply the semantic per-trade gate — mirror of `tradeMatchesFilters` in
/// app/lib/tradeFilters.ts. Returns true ⇒ this trade may be mirrored.
///
/// Only the strat's own filters gate anything: no price band set means no
/// price gate. Nothing is imposed that the strat didn't ask for.
/// Boolean form of the gate — the parity tests assert on it; the cycle loop
/// wants the reason, so it calls `trade_filter_reject` directly.
#[cfg(test)]
fn trade_passes_filters(t: &ObservedTrade, filters: &Option<TradeFilters>) -> bool {
    trade_filter_reject(t, filters).is_none()
}

/// Same gate as `trade_passes_filters`, but names the dimension that rejected
/// the trade. A strat whose filters exclude 100% of its leaders' flow is
/// indistinguishable from a broken engine unless the cycle can say WHY it
/// mirrored nothing — this is what the heartbeat reports.
///
/// The SENTIMENT dimension is deliberately absent here. Every gate in this
/// function answers from the trade alone; sentiment answers from the market's
/// price history, which is a network call. Applying it inside the per-trader
/// parse loop would mean one blocking fetch per trade. It runs instead as a
/// batched pass over `mirror_candidates` after the loop — same AND, same gate
/// names, one round of requests. See `apply_sentiment_gate`.
fn trade_filter_reject(t: &ObservedTrade, filters: &Option<TradeFilters>) -> Option<&'static str> {
    let default_filters = TradeFilters::default();
    let f = filters.as_ref().unwrap_or(&default_filters);
    match f.sides.as_deref() {
        Some("buy") if t.side != "BUY" => return Some("side"),
        Some("sell") if t.side != "SELL" => return Some("side"),
        _ => {}
    }
    // Price band, and only the one the strat actually set.
    if let Some(min) = f.min_price {
        if t.price < min { return Some("price"); }
    }
    if let Some(max) = f.max_price {
        if t.price > max { return Some("price"); }
    }
    let notional = if t.notional > 0.0 { t.notional } else { t.price * t.size };
    if let Some(min) = f.min_notional {
        if notional < min { return Some("size"); }
    }
    if let Some(max) = f.max_notional {
        if notional > max { return Some("size"); }
    }
    if let Some(cats) = &f.categories {
        if !cats.is_empty()
            && !cats.iter().any(|c| crate::categories::title_in_category(&t.market, c))
        {
            return Some("category");
        }
    }
    None
}

/// Mirror of `TraderFilter` in app/lib/types.ts — the strat-level counterpart
/// to `TradeFilters`. Ranks the watchlist by one of four metrics derived from
/// the stats the cycle already computes, and keeps only the best.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TraderFilter {
    /// "score" (default) | "sharpe" | "roi" | "winRate".
    #[serde(default)]
    pub metric: Option<String>,
    /// Keep the top N by that metric; 0 ⇒ no rank cut. Default 5.
    #[serde(rename = "topN", default)]
    pub top_n: Option<usize>,
    /// Hard floor on the metric — cuts a top-N trader that is below it.
    #[serde(rename = "minScore", default)]
    pub min_score: Option<f64>,
    /// Minimum closed trades in the 30d window before a trader is rankable.
    #[serde(rename = "minSamples", default)]
    pub min_samples: Option<usize>,
    /// FRESHNESS gate — cut any trader whose most recent trade is older than
    /// this many hours. None/0 = off. Unlike the quality thresholds this one
    /// runs BEFORE the sort (stale traders never occupy a top-N slot), and a
    /// trader with no known last trade counts as stale.
    #[serde(rename = "maxStaleHours", default)]
    pub max_stale_hours: Option<f64>,
}

/// Default watchlist cut when a strat sets `filter` without a `topN`.
/// Mirrors `DEFAULT_FILTER_TOP_N` in strat.ts.
pub const DEFAULT_FILTER_TOP_N: usize = 5;

/// THE trader score — mirror of `traderScore` in app/lib/strats/strat.ts.
/// The default metric is `candidate_score` with the notional divided out:
/// expected edge per dollar copied.
pub fn trader_score(stats: &TraderRoiStats, metric: &str) -> f64 {
    match metric {
        "sharpe" => stats.sharpe,
        "roi" => stats.roi,
        "winRate" => stats.success_prob,
        _ => stats.success_prob * stats.roi,
    }
}

/// "0x89bc…4f21" — log-friendly address, same shape the console renders.
fn short_addr(a: &str) -> String {
    if a.len() < 12 { return a.to_string(); }
    format!("{}…{}", &a[..6], &a[a.len() - 4..])
}

/// One trader's place in the cycle's ranking.
#[derive(Debug, Clone)]
pub struct RankedTrader {
    /// Lowercased address.
    pub address: String,
    pub score: f64,
    pub sample_size: usize,
    /// 1-based position (thresholds don't renumber).
    pub rank: usize,
    pub kept: bool,
    /// Why it was cut — empty when kept.
    pub reason: String,
    /// Age of the trader's most recent trade at ranking time (ms).
    /// `f64::INFINITY` when no trade is known.
    pub age_ms: f64,
    /// Failed the `max_stale_hours` gate. Always false when the gate is off.
    pub stale: bool,
}

/// Age (ms) of a trader's most recent trade — mirror of `traderAgeMs` in
/// strat.ts. No known last trade ⇒ infinitely old, never "fresh by default".
pub fn trader_age_ms(stats: &TraderRoiStats, now_ms: i64) -> f64 {
    if stats.last_trade_at <= 0 {
        return f64::INFINITY;
    }
    ((now_ms - stats.last_trade_at).max(0)) as f64
}

/// "6h" / "17d" / "never" — mirror of `formatAgeShort` in strat.ts, so the
/// engine's cut reasons read identically to the console's.
fn format_age_short(age_ms: f64) -> String {
    if !age_ms.is_finite() {
        return "never".to_string();
    }
    let sec = (age_ms / 1000.0).floor() as i64;
    if sec < 60 { return format!("{sec}s"); }
    let min = sec / 60;
    if min < 60 { return format!("{min}m"); }
    let hr = min / 60;
    if hr < 24 { return format!("{hr}h"); }
    format!("{}d", hr / 24)
}

/// Format a stale-gate threshold the way TS template-interpolates a number:
/// `6` not `6.0`, but `1.5` keeps its fraction.
fn fmt_hours(h: f64) -> String {
    if h.fract() == 0.0 { format!("{}", h as i64) } else { format!("{h}") }
}

/// Rank a watchlist and decide who gets copied — mirror of `rankTraders` in
/// strat.ts, down to the address tie-break, so the console's preview and the
/// live engine never disagree about who is in the top N.
///
/// `now_ms` is the cycle clock, passed in for the staleness gate (and so this
/// stays pure/testable).
pub fn select_top_traders(
    traders: &[(String, TraderRoiStats)],
    filter: &TraderFilter,
    now_ms: i64,
) -> Vec<RankedTrader> {
    let metric = filter.metric.as_deref().unwrap_or("score");
    let top_n = filter.top_n.unwrap_or(DEFAULT_FILTER_TOP_N);
    let min_samples = filter.min_samples.unwrap_or(0);
    let stale_hours = filter.max_stale_hours.unwrap_or(0.0);
    let max_age_ms = if stale_hours > 0.0 { stale_hours * 3_600_000.0 } else { f64::INFINITY };
    let mut rows: Vec<RankedTrader> = traders
        .iter()
        .map(|(addr, stats)| {
            let age_ms = trader_age_ms(stats, now_ms);
            RankedTrader {
                address: addr.to_lowercase(),
                score: trader_score(stats, metric),
                sample_size: stats.sample_size,
                rank: 0,
                kept: true,
                reason: String::new(),
                age_ms,
                stale: stale_hours > 0.0 && age_ms > max_age_ms,
            }
        })
        .collect();
    // Stale first (false < true), then score, then byte-order address.
    rows.sort_by(|a, b| {
        a.stale
            .cmp(&b.stale)
            .then_with(|| {
                b.score
                    .partial_cmp(&a.score)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| a.address.cmp(&b.address))
    });
    let total = rows.len();
    for (i, r) in rows.iter_mut().enumerate() {
        r.rank = i + 1;
        if r.stale {
            r.kept = false;
            r.reason = if r.age_ms.is_finite() {
                format!(
                    "last trade {} ago > {}h max",
                    format_age_short(r.age_ms),
                    fmt_hours(stale_hours),
                )
            } else {
                format!("no trade seen — needs one inside {}h", fmt_hours(stale_hours))
            };
        } else if min_samples > 0 && r.sample_size < min_samples {
            r.kept = false;
            r.reason = format!("{} closed trades < {} required", r.sample_size, min_samples);
        } else if filter.min_score.map_or(false, |floor| r.score < floor) {
            r.kept = false;
            r.reason = format!(
                "{} {:.3} < floor {:.3}",
                metric,
                r.score,
                filter.min_score.unwrap_or(0.0)
            );
        } else if top_n > 0 && r.rank > top_n {
            r.kept = false;
            r.reason = format!("rank {} of {} — outside top {} by {}", r.rank, total, top_n, metric);
        }
    }
    rows
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
    /// Watched trader (lowercased) whose BUY we mirrored to open this. The
    /// leader-flat sweep needs it to ask "do you still hold this?" — without
    /// it a position whose exit signal was missed can never be swept. Empty
    /// on momentum entries and on positions adopted from the chain, which
    /// have no leader to follow out.
    #[serde(default)]
    pub leader: String,
}

/// Per-strat fill ledger. BUYs add volume; SELLs and redeems realize PnL
/// against the exiting position's cost basis. Open exposure ("money in") is
/// NOT stored here — it's derivable from `EngineState.positions`, each of
/// which carries the `strategy_id` that opened it.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct StratStats {
    /// Σ realized PnL in USDC: (exit proceeds − cost basis) over every SELL
    /// and redeem attributed to this strat. GROSS — before the taker fees in
    /// `fees` below, exactly like `BacktestSim.grossPnl` is before its own.
    /// The net the wallet actually holds is `realized - fees`.
    #[serde(default)]
    pub realized: f64,
    /// Σ Polymarket taker fees this strat has paid, entry and exit, priced at
    /// each market's own rate (`crate::fees`). This used to be structurally
    /// absent — the engine signed every order with fee_rate_bps 0 and
    /// concluded from that that trading was free. It is not: a round trip in a
    /// crypto market at 50¢ costs 7% of the position.
    ///
    /// No gas rides along, because a trading engine pays none: CLOB fills are
    /// matched on-chain by Polymarket's operator and redeems go through its
    /// relayer.
    #[serde(default)]
    pub fees: f64,
    /// Σ notional across all fills (BUY + SELL + redeem value).
    #[serde(default)]
    pub volume: f64,
    #[serde(default)]
    pub buys: u64,
    #[serde(default)]
    pub sells: u64,
    #[serde(default)]
    pub redeems: u64,
    /// Positions that resolved in the wallet rather than being sold —
    /// counted separately because a burned loser produces no SELL and no
    /// redeem, and used to be dropped from the ledger unbooked.
    #[serde(default)]
    pub settled: u64,
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

/// One gate's recent damage: how many leader entries it blocked inside the
/// current `GATE_WINDOW_MS` window, when it last fired, and which leaders the
/// blocked entries came from. The leader list is what turns "your filters
/// blocked 41 entries" into something actionable: a bot that only trades
/// 5-minute candles has 100% of its flow refused forever, and the fix is to
/// drop that leader, not to lower the gate that is saving you money.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GateTally {
    pub count: u64,
    #[serde(rename = "lastAt")]
    pub last_at: i64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub traders: Vec<String>,
}

/// How many distinct leaders a gate names before the console just says "…".
const GATE_TRADERS_MAX: usize = 8;

/// Per-cycle gate hits: how many entries, and whose. Collected per cycle and
/// folded into the windowed `gated_recently` tally at CYCLE_END.
#[derive(Default)]
struct GateHits {
    count: usize,
    traders: Vec<String>,
}

impl GateHits {
    fn hit(&mut self, trader: &str) {
        self.count += 1;
        if !trader.is_empty()
            && self.traders.len() < GATE_TRADERS_MAX
            && !self.traders.iter().any(|t| t == trader)
        {
            self.traders.push(trader.to_string());
        }
    }
}

/// How far back `gated_recently` looks. Long enough that a slow leader still
/// registers, short enough that fixing your filters clears the console's
/// warning within a couple of cycles rather than at next restart.
const GATE_WINDOW_MS: i64 = 30 * 60_000;

/// Record `n` entries blocked by `reason`. Counts restart when the gate has
/// been quiet for a whole window, so the number always means "in the last
/// half hour" rather than "since this session was armed".
fn tally_gate(map: &mut HashMap<String, GateTally>, reason: &str, n: u64, traders: &[String], now: i64) {
    let e = map.entry(reason.to_string()).or_default();
    if now - e.last_at > GATE_WINDOW_MS {
        e.count = 0;
        e.traders.clear();
    }
    e.count += n;
    for t in traders {
        if e.traders.len() >= GATE_TRADERS_MAX {
            break;
        }
        if !e.traders.iter().any(|x| x == t) {
            e.traders.push(t.clone());
        }
    }
    e.last_at = now;
}

/// Record one mirror that cleared every filter and was suppressed only by dry
/// run. Same windowing as `tally_gate` so the console's two "nothing is being
/// placed" warnings age out identically.
fn tally_dry_run(t: &mut GateTally, trader: Option<&str>, now: i64) {
    if now - t.last_at > GATE_WINDOW_MS {
        t.count = 0;
        t.traders.clear();
    }
    t.count += 1;
    if let Some(a) = trader.filter(|a| !a.is_empty()) {
        if t.traders.len() < GATE_TRADERS_MAX && !t.traders.iter().any(|x| x == a) {
            t.traders.push(a.to_string());
        }
    }
    t.last_at = now;
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
    /// Free USDC (dollars) this session may still deploy — the deposit
    /// wallet's real trading balance capped at the strat's allocation.
    /// Refreshed at the top of every cycle; `None` only before the first
    /// successful read (or while the Polygon RPCs are all down), which the
    /// budget treats as "unknown, fall back to the ledger estimate".
    pub balance: Option<f64>,
    /// `balance` + mark value of the positions this session holds — what the
    /// strat is actually worth. Every proportional copy is a fraction of
    /// this, so the position size tracks the account instead of a config
    /// number that stopped being true after the first fill.
    #[serde(rename = "accountValue", default)]
    pub account_value: Option<f64>,
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
    /// The cadence the loop is actually running at (ms) — the strat's request
    /// after the rate-limit floor and the fan-out widening (see
    /// `effective_interval_ms`). The console shows this rather than the
    /// requested `intervalMs`, so a clamped strat can't read as if it were
    /// polling faster than it is.
    #[serde(rename = "effectiveIntervalMs", default)]
    pub effective_interval_ms: u64,
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
    /// Why the leader flow this session DID see never became orders, keyed by
    /// gate ("price", "market query", "resolves too soon", "stale", …)
    /// over the last `GATE_WINDOW_MS`. The cycle heartbeat already says this
    /// in prose, but a strat whose filters exclude 100% of its leaders' flow
    /// reads as a broken engine unless the console can state the reason
    /// standing still — one line of text scrolling past in a log is not that.
    #[serde(rename = "gatedRecently", default)]
    pub gated_recently: HashMap<String, GateTally>,
    /// Mirrors that cleared EVERY filter and were still not placed, because
    /// the session is in dry run — counted over the same `GATE_WINDOW_MS`.
    ///
    /// The gate tally deliberately cannot report this: the DRY RUN path
    /// *clears* it, because saying "your filters blocked them all" about flow
    /// the filters just passed is a lie. That left the most expensive failure
    /// mode on this console completely silent — a session polling on schedule,
    /// logging "would BUY" hundreds of times an hour, and placing nothing,
    /// with a small pill in one tab as the only tell. It cost a week of no
    /// trading (2026-08-01 → 08-08) before anyone noticed. Count it here so
    /// the console can say it standing still, next to the gate warning.
    #[serde(rename = "dryRunRecently", default)]
    pub dry_run_recently: GateTally,
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
            account_value: None,
            log: Vec::new(),
            observed_trades: Vec::new(),
            error: None,
            trader_cursors: HashMap::new(),
            trader_last_sync: HashMap::new(),
            effective_interval_ms: 0,
            copied_ids: HashSet::new(),
            positions: HashMap::new(),
            strat_stats: HashMap::new(),
            realized_events: Vec::new(),
            proposed_recently: HashMap::new(),
            exited_recently: HashMap::new(),
            gated_recently: HashMap::new(),
            dry_run_recently: GateTally::default(),
        }
    }
}

/// Ledger key for a fill: the strat stamped on the exiting position when it
/// has one, else the fallback (usually the running session's strat, or
/// "unassigned" when there's no session context at all).
/// `(proceeds, cost basis)` to book for a ledger position the wallet no
/// longer holds, or `None` to book nothing.
///
/// Three cases, and the middle one is the bug this exists to close:
///   * `sold` — the SELL path already booked it and cleared the entry; the
///     data-api just hasn't dropped the token yet. Booking again double-counts.
///   * settled at a known price — the market RESOLVED under us. A winner was
///     redeemed to cash, a loser burned to nothing. Either way the position is
///     realized, and the loser is the half that used to disappear unbooked:
///     no SELL, no redeem, so `realized` never moved while the cash did.
///   * price unknown — resolution isn't indexed yet. Book nothing and let a
///     later cycle catch it, rather than inventing a total loss.
fn settlement_booking(pos: &OpenPosition, sold: bool, settled_price: Option<f64>) -> Option<(f64, f64)> {
    if sold {
        return None;
    }
    let price = settled_price?;
    let basis = pos.size * pos.entry_price;
    let proceeds = pos.size * price;
    if basis <= 0.0 && proceeds <= 0.0 {
        return None;
    }
    Some((proceeds, basis))
}

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
    /// "placed" (crossed) | "resting" (accepted, unfilled) | "skipped" | "failed"
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
    /// Mark-to-market value of the holding right now (`size × current
    /// price`), as reported by the data-api. Summed into the account value
    /// that proportional sizing divides by. 0.0 when the API omits it.
    current_value: f64,
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
    let url = format!(
        "{}/positions?user={}&sizeThreshold=0.0&limit={}",
        DATA_API, wallet, HELD_POSITIONS_LIMIT,
    );
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
            let current_value = p
                .get("currentValue")
                .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
                .unwrap_or(0.0);
            if !token_id.is_empty() && size > 0.0 {
                out.push(HeldPosition { token_id, size, condition_id, market, avg_price, current_value });
            }
        }
    }
    Some(out)
}

/// Settlement price per token for positions the wallet has CLOSED, from
/// data-api `/closed-positions`. `curPrice` there is the resolved value of
/// that outcome — 1.0 if it won, 0.0 if it burned.
///
/// The reconciler needs this to tell the two apart. A tracked position that
/// vanishes from `/positions` has settled one way or the other, and the
/// difference is the whole PnL: a redeemed winner paid `size × 1.0`, a loser
/// paid nothing. Guessing "loser" would slander every redeem the engine
/// didn't perform itself (the manual REDEEM button, or a redeem racing the
/// auto-redeem pass).
async fn fetch_closed_outcomes(http: &reqwest::Client, wallet: &str) -> Option<HashMap<String, f64>> {
    if wallet.is_empty() {
        return None;
    }
    let url = format!(
        "{}/closed-positions?user={}&limit={}",
        DATA_API, wallet, HELD_POSITIONS_LIMIT,
    );
    let resp = match http.get(&url).send().await {
        Ok(r) => r,
        Err(e) => {
            tracing::warn!(error = %e, "closed-positions fetch failed");
            return None;
        }
    };
    let text = resp.text().await.unwrap_or_default();
    let parsed: Value = serde_json::from_str(&text).ok()?;
    let mut out = HashMap::new();
    for p in parsed.as_array()? {
        let token_id = p.get("asset").and_then(|v| v.as_str()).unwrap_or("");
        if token_id.is_empty() {
            continue;
        }
        let cur = p
            .get("curPrice")
            .and_then(|v| v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())))
            .unwrap_or(0.0);
        out.insert(token_id.to_string(), cur);
    }
    Some(out)
}

/// Per-trader bankroll cache lifetime, jittered across [TTL, 1.5×TTL) by a
/// hash of the address. Without the jitter every trader's entry expires on
/// the same cycle — the whole watchlist refetches at once and that one cycle's
/// fan-out doubles. Deterministic per address, so it survives restarts.
fn leader_book_ttl(key: &str) -> i64 {
    let h = key.bytes().fold(0u64, |a, b| a.wrapping_mul(31).wrapping_add(b as u64));
    LEADER_BOOK_TTL_MS + (h % (LEADER_BOOK_TTL_MS as u64 / 2)) as i64
}

/// Does this gamma market object actually describe the condition we asked
/// for? Gamma answers an unrecognised filter with its default market page
/// instead of an error, so every by-id lookup checks the id came back.
fn verify_condition(market: &Value, condition_id: &str) -> bool {
    market
        .get("conditionId")
        .or_else(|| market.get("condition_id"))
        .and_then(|v| v.as_str())
        .map_or(false, |id| id.eq_ignore_ascii_case(condition_id))
}

/// `(negRisk, endDate ms)` out of a gamma market object.
fn market_meta_of(market: &Value) -> (bool, Option<i64>) {
    let neg_risk = market
        .get("negRisk")
        .or_else(|| market.get("neg_risk"))
        .and_then(|b| b.as_bool())
        .unwrap_or(false);
    let end = market
        .get("endDate")
        .or_else(|| market.get("end_date_iso"))
        .and_then(|v| v.as_str())
        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
        .map(|d| d.timestamp_millis());
    (neg_risk, end)
}

/// A watched trader's balance sheet: what their whole book is worth, and how
/// much of each token they still hold. Proportional copying needs both —
/// the bankroll to size entries as the same *fraction of net worth* the
/// leader risked, the per-token sizes to exit in the same *fraction of the
/// position* the leader exited.
#[derive(Clone, Debug, Default)]
struct LeaderBook {
    /// Positions mark value + free USDC cash. The denominator of the copy
    /// ratio: "the leader put 2% of their net worth on this".
    bankroll: f64,
    /// token_id → shares still held, AFTER the trades we're reacting to.
    sizes: HashMap<String, f64>,
    /// False when the positions feed came back at its row cap — the book may
    /// be truncated, so an ABSENT token can't be read as "the leader went
    /// flat". Only a complete book can drive the leader-flat sweep.
    complete: bool,
}

/// Read a leader's bankroll and position book. Positions (and their mark
/// value) come from the data-api; free cash is the V2 collateral balance on
/// their proxy wallet — the same token our own balance check reads, so both
/// sides of the ratio are measured the same way. Returns `None` when the
/// positions fetch fails: an outage must never read as "bankroll 0", which
/// would make the copy ratio explode.
async fn fetch_leader_book(http: &reqwest::Client, address: &str) -> Option<LeaderBook> {
    let held = fetch_held_positions(http, address).await?;
    let positions_value: f64 = held.iter().map(|p| p.current_value).sum();
    // Cash is best-effort: a Polygon outage degrades the bankroll to
    // positions-only (a smaller denominator ⇒ a *larger* copy) rather than
    // dropping the whole measurement, so it's floored into the ratio guard
    // below by the caller's sanity clamp.
    let cash = crate::relayer::usdc_balance(http, address)
        .await
        .ok()
        .and_then(|s| s.parse::<f64>().ok())
        .map(|u| u / 1e6)
        .unwrap_or(0.0);
    Some(LeaderBook {
        bankroll: positions_value + cash,
        complete: held.len() < HELD_POSITIONS_LIMIT,
        sizes: held.into_iter().map(|p| (p.token_id, p.size)).collect(),
    })
}

/// What this session is actually worth right now — the numerator of every
/// proportional copy. Cash is the deposit wallet's free USDC capped at the
/// strat's allocation (one wallet funds several strats; a session may only
/// claim its own slice), plus the mark value of the positions it holds.
///
/// Sizing against THIS instead of the static `capital` config is what makes
/// the copy self-scaling: a session that doubles copies twice as big, one
/// that's drawn down copies smaller, and one whose wallet is empty copies
/// nothing at all instead of firing orders the CLOB rejects.
/// One leader trade the engine decided to act on, with everything the
/// execution pass needs to size it in proportion.
struct MirrorCandidate {
    trade: ObservedTrade,
    /// Multiplier on the leader's notional — see `copy_ratio_for`.
    copy_ratio: f64,
    /// Shares the leader still holds in this token after the trade.
    /// `Some(0.0)` on a SELL means they went flat, so we go flat too.
    /// `None` = their book couldn't be read; exits fall back to ratio sizing.
    leader_remaining: Option<f64>,
    /// True when this exit came from the leader-flat sweep rather than from a
    /// SELL we actually observed — worth saying out loud in the log, since it
    /// means the feed missed their exit and the book caught it.
    swept: bool,
}

/// Synthesize an exit for every position whose leader has since gone flat.
///
/// The activity feed is the FAST path for exits, but it is not a complete
/// one: the engine may have been stopped when the leader sold, the sell may
/// have aged off their recent-activity page, or their address may have been
/// unreachable for a cycle. Any of those leaves us holding a bag the leader
/// already dropped. So each cycle we also ask the leader's own book the
/// direct question — "do you still hold this?" — and treat "no" as the SELL
/// we never saw.
///
/// A missing token only counts as an exit when the book can actually prove
/// it: the snapshot must be COMPLETE (an unpaged book has no missing rows to
/// misread) and must have been read AFTER we opened (a snapshot predating our
/// own BUY has never seen the token, which is not the same as the leader
/// dropping it). Tokens already queued for a mirror-sell this cycle, or sold
/// within the exit cooldown, are left alone so we never double-sell.
fn leader_flat_exits(
    positions: &HashMap<String, OpenPosition>,
    exited_recently: &HashMap<String, i64>,
    leader_books: &HashMap<String, (i64, LeaderBook)>,
    queued_sells: &HashSet<String>,
    now: i64,
) -> Vec<MirrorCandidate> {
    let mut out: Vec<MirrorCandidate> = positions
        .values()
        .filter(|p| p.size > 0.0 && !p.leader.is_empty())
        .filter(|p| !queued_sells.contains(&p.token_id))
        // A just-placed exit lingers in the positions feed until it settles;
        // the cooldown that stops the reconciler re-adopting it stops us
        // re-selling it too.
        .filter(|p| now - exited_recently.get(&p.token_id).copied().unwrap_or(0) >= EXIT_READOPT_COOLDOWN_MS)
        .filter_map(|p| {
            let (read_at, book) = leader_books.get(&p.leader)?;
            if !book.complete || *read_at <= p.opened_at { return None; }
            // A book old enough to have expired can't speak for the present:
            // the leader may have re-entered since it was taken.
            if now - *read_at > leader_book_ttl(&p.leader) { return None; }
            if book.sizes.get(&p.token_id).copied().unwrap_or(0.0) > 0.0 { return None; }
            Some(MirrorCandidate {
                trade: ObservedTrade {
                    // Keyed on the snapshot, so re-reading the same book can't
                    // queue the exit twice, while a genuinely fresher read
                    // retries an exit that failed to place.
                    id: format!("flat-{}-{}", p.token_id, read_at),
                    timestamp: *read_at,
                    trader: p.leader.clone(),
                    market: p.market.clone(),
                    condition_id: p.condition_id.clone(),
                    side: "SELL".into(),
                    size: p.size,
                    price: p.entry_price,
                    notional: p.size * p.entry_price,
                    token_id: p.token_id.clone(),
                    outcome: String::new(),
                    score: 0.0,
                    success_prob: default_success_prob(),
                },
                copy_ratio: 1.0,
                // They hold nothing, so we hold nothing: a full exit.
                leader_remaining: Some(0.0),
                swept: true,
            })
        })
        .collect();
    // `positions` is a HashMap — sort so the exits (and their log lines) come
    // out in a stable order rather than a different one every cycle.
    out.sort_by(|a, b| a.trade.token_id.cmp(&b.trade.token_id));
    out
}

#[derive(Clone, Copy, Debug, Default)]
struct AccountValue {
    /// Free USDC available to THIS session (dollars).
    cash: f64,
    /// Cash + mark value of the positions this session holds — the session's
    /// account value, and the numerator of every proportional copy.
    total: f64,
}

// ─── Handle / Registry ─────────────────────────────────────────────────

/// Registry key for one live session: `<eoa>::<strategyId>`.
///
/// A wallet can fund and run SEVERAL strats at once. Each session budgets
/// against its own `cfg.capital` allocation minus the positions IT opened
/// (`execute_mirrors`' `free_capital`), and tags every fill with its
/// `strategy_id`, so concurrent sessions on one deposit wallet keep separate
/// books. The console is responsible for keeping the sum of allocations
/// inside the wallet's USDC — over-allocating just means the later order
/// fails on-chain for insufficient balance.
fn session_key(eoa: &str, strategy_id: &str) -> String {
    format!("{}::{}", eoa.to_lowercase(), strategy_id)
}

/// Filesystem-safe form of a session key — the on-disk config/state basename.
/// Strat ids are base-36 timestamps today, but a shared strat could carry
/// anything, so anything outside `[A-Za-z0-9._-]` collapses to `_`.
fn session_file_stem(eoa: &str, strategy_id: &str) -> String {
    let safe: String = strategy_id
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') { c } else { '_' })
        .collect();
    format!("{}__{}", eoa.to_lowercase(), safe)
}

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
    /// conditionId → (negRisk flag, market end date ms) resolved once via
    /// gamma and reused. The end date gates mirrors in markets about to
    /// resolve; `None` = gamma didn't report one.
    neg_risk_cache: DashMap<String, (bool, Option<i64>)>,
    /// trader address → (fetched-at ms, balance sheet). Shared by every
    /// session so two strats watching the same leader pay for one fetch.
    leader_books: DashMap<String, (i64, LeaderBook)>,
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
            leader_books: DashMap::new(),
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
    pub fn set_auto_execute(
        self: &Arc<Self>,
        eoa: &str,
        strategy_id: Option<&str>,
        on: bool,
    ) -> Option<bool> {
        let mut cfg = self.config_of(eoa, strategy_id)?;
        cfg.auto_execute = on;
        // The dry-run warning is answered the moment execution is armed —
        // don't leave it up for a whole window telling the owner they aren't
        // trading after they just fixed it.
        if on {
            if let Some(h) = self
                .resolve_key(eoa, strategy_id)
                .and_then(|k| self.engines.get(&k))
            {
                h.state.write().dry_run_recently = GateTally::default();
            }
        }
        self.start(cfg);
        Some(on)
    }

    /// Resolve (and cache) a market's `(negRisk, endDate)` from gamma in one
    /// call. negRisk must be exact — a wrong value yields a CLOB "bad
    /// signature" rejection — so we resolve rather than guess; the end date
    /// feeds the time-to-resolution mirror gate. Both fall back to "unknown"
    /// (false / None) when gamma can't answer.
    ///
    /// The filter is `condition_ids` (plural). The singular `condition_id`
    /// gamma ACCEPTS and silently IGNORES, answering with its default page of
    /// 20 unrelated markets — so reading `[0]` off that response returned some
    /// other market's negRisk for every lookup. `verify_condition` below is
    /// the guard against that class of bug recurring.
    async fn resolve_market_meta(&self, condition_id: &str) -> (bool, Option<i64>) {
        if condition_id.is_empty() {
            return (false, None);
        }
        if let Some(v) = self.neg_risk_cache.get(condition_id) {
            return *v;
        }
        // Gamma's default page excludes closed markets; exits touch resolved
        // ones, so fall through to an explicit closed lookup.
        let mut resolved = None;
        for suffix in ["", "&closed=true"] {
            let url = format!("{}/markets?condition_ids={}{}", GAMMA_API, condition_id, suffix);
            match self.http.get(&url).send().await {
                Ok(resp) => {
                    let txt = resp.text().await.unwrap_or_default();
                    if let Some(m) = serde_json::from_str::<Value>(&txt)
                        .ok()
                        .and_then(|v| if v.is_array() { v.get(0).cloned() } else { Some(v) })
                        .filter(|m| verify_condition(m, condition_id))
                    {
                        resolved = Some(market_meta_of(&m));
                        break;
                    }
                }
                Err(e) => {
                    tracing::warn!(condition_id, error = %e, "market meta resolve failed; defaulting negRisk=false");
                    break;
                }
            }
        }
        let resolved = resolved.unwrap_or((false, None));
        self.neg_risk_cache.insert(condition_id.to_string(), resolved);
        resolved
    }

    async fn resolve_neg_risk(&self, condition_id: &str) -> bool {
        self.resolve_market_meta(condition_id).await.0
    }

    /// Sync the internal ledger to the wallet's actual on-chain holdings and
    /// price the session's account value — the two things every later
    /// decision this cycle depends on.
    ///
    /// Runs FIRST so nothing downstream reasons about a stale book: mirrors
    /// are sized as a fraction of the value measured here, protective exits
    /// only ever sell what's really held, and the BUY budget is bounded by
    /// cash that actually exists (the engine used to size against the static
    /// `capital` config and fired tens of thousands of orders the CLOB
    /// rejected with "not enough balance").
    ///
    /// Positions live on the V2 DEPOSIT WALLET (CREATE2 from the backend
    /// signer) — reconciling against `cfg.address` (the user's EOA) reads an
    /// empty set and silently wipes every ledger entry.
    async fn reconcile_and_value(
        &self,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
    ) -> AccountValue {
        // DRY RUN holds no real positions and spends no real cash, so both
        // reads would be pure API load. The allocation IS the account value
        // there — which is exactly what a preview should size against.
        if !cfg.auto_execute {
            return AccountValue { cash: cfg.capital, total: cfg.capital };
        }
        let wallet = self
            .signer_store
            .signer_address(&cfg.eoa)
            .ok()
            .and_then(|a| crate::deposit_wallet::derive_deposit_wallet(&a).ok());
        let Some(wallet) = wallet else {
            // No deposit wallet yet (unfunded strat) — same fallback.
            return AccountValue { cash: cfg.capital, total: cfg.capital };
        };
        let (held, cash_raw) = tokio::join!(
            fetch_held_positions(&self.http, &wallet),
            crate::relayer::usdc_balance(&self.http, &wallet),
        );
        // Taken before any state lock — see `sibling_claimed_tokens`.
        let sibling_claimed = self.sibling_claimed_tokens(&cfg.eoa, &cfg.strategy_id);

        // None = fetch/derivation failed — keep the ledger as-is rather than
        // reading an outage as "everything was sold".
        if let Some(held) = &held {
            let onchain: HashMap<&str, &HeldPosition> =
                held.iter().map(|p| (p.token_id.as_str(), p)).collect();
            // Only worth asking when the ledger actually claims something the
            // wallet no longer holds — the common cycle settles nothing.
            let vanished = {
                let s = state.read();
                s.positions.keys().any(|t| {
                    !onchain.get(t.as_str()).map_or(false, |h| h.size > 0.0)
                })
            };
            let settled = if vanished {
                fetch_closed_outcomes(&self.http, &wallet).await
            } else {
                None
            };
            let mut s = state.write();
            let now_ms = chrono::Utc::now().timestamp_millis();
            // Expire the SELL cooldown first: the set below has to answer
            // "did this session sell it" for the same cycle it's read in.
            s.exited_recently.retain(|_, t| now_ms - *t < EXIT_READOPT_COOLDOWN_MS);
            let sold: std::collections::HashSet<String> =
                s.exited_recently.keys().cloned().collect();
            // Drop / shrink ledger entries to match on-chain reality.
            let mut settled_out: Vec<(OpenPosition, (f64, f64))> = Vec::new();
            s.positions.retain(|token_id, pos| {
                match onchain.get(token_id.as_str()) {
                    Some(h) if h.size > 0.0 => {
                        if h.size + 1e-6 < pos.size { pos.size = h.size; }
                        true
                    }
                    _ => {
                        // No longer held. A SELL already booked its own
                        // realized PnL and removed its ledger entry, so
                        // anything still here that the wallet dropped
                        // RESOLVED — and resolution is a realized outcome
                        // whether it paid or burned. Booking only the paying
                        // half is what let a strat that emptied the account
                        // report a profit: losers leave no SELL and no
                        // redeem, so the ledger never saw them.
                        let price = settled.as_ref().and_then(|m| m.get(token_id)).copied();
                        if let Some(booking) = settlement_booking(pos, sold.contains(token_id), price) {
                            settled_out.push((pos.clone(), booking));
                        }
                        false
                    }
                }
            });
            for (pos, (proceeds, basis)) in settled_out {
                let key = strat_key(&pos.strategy_id, &cfg.strategy_id);
                {
                    let stats = s.strat_stats.entry(key.clone()).or_default();
                    stats.realized += proceeds - basis;
                    stats.volume += proceeds;
                    stats.settled += 1;
                    stats.last_fill_at = now_ms;
                }
                push_realized(&mut s.realized_events, key, proceeds - basis, basis, now_ms);
                push_log(&mut s.log, LogEntry {
                    id: format!("settled-{}-{}", pos.token_id, now_ms),
                    timestamp: now_ms,
                    kind: "SETTLED".into(),
                    reason: Some(format!(
                        "{} · {:.0} shares @ {:.0}¢ · realized {:+.2} · {}",
                        if proceeds > 0.0 { "WON — resolved in the money" } else { "LOST — resolved worthless" },
                        pos.size,
                        pos.entry_price * 100.0,
                        proceeds - basis,
                        pos.market,
                    )),
                    trader_address: if pos.leader.is_empty() { None } else { Some(pos.leader.clone()) },
                    trades_seen: None,
                });
            }
            // ADOPT real holdings the ledger doesn't know — bought before a
            // state wipe, by an earlier session, or manually. Without a
            // ledger entry the take-profit/stop-loss pass can't defend them
            // and they'd sit pinned at 100¢ until resolution. Entry = the
            // data-api's average price paid; entry_score = MAX so
            // capital-rebalancing never sacrifices a hold this session didn't
            // buy. Tokens the engine just SOLD are skipped for the cooldown
            // window — the data-api still lists them until the fill settles,
            // and re-adopting one re-fires its exit (double sell).
            //
            // Orphaned is the whole point: a holding another LIVE session on
            // this wallet already claims has an owner, and adopting it makes
            // two strats manage one position off two ledgers (see
            // `sibling_claimed_tokens`). Only genuinely unowned tokens are
            // adopted; if that sibling stops, its claim goes with it and the
            // next cycle picks the position up as intended.
            for h in held {
                if s.exited_recently.contains_key(&h.token_id) { continue; }
                if sibling_claimed.contains(&h.token_id) { continue; }
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
                        leader: String::new(),
                    });
                }
            }
        }

        // Value only the tokens THIS session's ledger claims — one wallet can
        // fund several strats, and each must size against its own book.
        let marks: HashMap<&str, f64> = held
            .iter()
            .flatten()
            .map(|p| (p.token_id.as_str(), p.current_value))
            .collect();
        let positions_value: f64 = {
            let s = state.read();
            s.positions
                .values()
                .map(|p| match marks.get(p.token_id.as_str()) {
                    Some(v) => *v,
                    // Not in the on-chain read (fetch failed, or a fill that
                    // hasn't indexed yet) — fall back to cost basis.
                    None => p.size * p.entry_price,
                })
                .sum()
        };
        // A session may only claim its slice of a shared wallet: whatever the
        // allocation has left after what's already deployed, bounded by the
        // real balance. An unreadable balance leaves the ledger estimate.
        let deployed_basis: f64 = {
            let s = state.read();
            s.positions.values().map(|p| p.size * p.entry_price).sum()
        };
        let unallocated = (cfg.capital - deployed_basis).max(0.0);
        let cash = match cash_raw.ok().and_then(|s| s.parse::<f64>().ok()) {
            Some(units) => (units / 1e6).min(unallocated),
            None => unallocated,
        };
        let value = AccountValue { cash, total: cash + positions_value };
        {
            let mut s = state.write();
            s.balance = Some(value.cash);
            s.account_value = Some(value.total);
        }
        value
    }

    /// Public read of a trader's bankroll (positions mark value + free USDC),
    /// through the same cache the engine sizes from — so the console's
    /// backtest previews the exact ratio live will use.
    pub async fn bankroll_of(&self, address: &str) -> Option<f64> {
        self.leader_book(address).await.map(|b| b.bankroll)
    }

    /// A watched trader's bankroll + position book, cached for
    /// `LEADER_BOOK_TTL_MS`. `None` = never successfully read (the caller
    /// falls back to volume-based sizing and full-size exits).
    ///
    /// `spacing_ms` is slept AFTER a real fetch (never on a cache hit): the
    /// caller's next move is another data-api call for the same trader, and
    /// two back-to-back requests are what trips Cloudflare's per-second limit.
    ///
    /// Returns `(read_at_ms, book)`. The timestamp is the sweep's proof of
    /// order: only a snapshot taken AFTER we opened a position can say the
    /// leader has since exited it.
    async fn leader_book_spaced(&self, address: &str, spacing_ms: u64) -> Option<(i64, LeaderBook)> {
        let key = address.to_lowercase();
        let now = chrono::Utc::now().timestamp_millis();
        if let Some(hit) = self.leader_books.get(&key) {
            if now - hit.0 < leader_book_ttl(&key) {
                return Some((hit.0, hit.1.clone()));
            }
        }
        let fetched = fetch_leader_book(&self.http, address).await;
        if spacing_ms > 0 {
            tokio::time::sleep(Duration::from_millis(spacing_ms)).await;
        }
        match fetched {
            Some(book) => {
                self.leader_books.insert(key, (now, book.clone()));
                Some((now, book))
            }
            // Serve a stale entry rather than silently reverting a running
            // strat to the fallback sizing model on one bad fetch.
            None => self.leader_books.get(&key).map(|hit| (hit.0, hit.1.clone())),
        }
    }

    async fn leader_book(&self, address: &str) -> Option<LeaderBook> {
        self.leader_book_spaced(address, 0).await.map(|(_, b)| b)
    }

    fn path_for_config(&self, eoa: &str, strategy_id: &str) -> PathBuf {
        self.disk_dir.join(format!("{}.config.json", session_file_stem(eoa, strategy_id)))
    }
    fn path_for_state(&self, eoa: &str, strategy_id: &str) -> PathBuf {
        self.disk_dir.join(format!("{}.state.json", session_file_stem(eoa, strategy_id)))
    }
    /// Pre-multi-session layout: one `<eoa>.config.json` per wallet. Still
    /// read (and migrated away from on resume) so an in-flight session
    /// survives the upgrade.
    fn legacy_path_for_config(&self, eoa: &str) -> PathBuf {
        self.disk_dir.join(format!("{}.config.json", eoa.to_lowercase()))
    }
    fn legacy_path_for_state(&self, eoa: &str) -> PathBuf {
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
            // Explicitly stopped — the config only survives so the console can
            // still read this strat's ledger. Do NOT restart it.
            if cfg.stopped { continue; }
            tracing::info!(strat = %cfg.strategy_id, "resuming live engine for {}", cfg.eoa);
            // Restore state if present, else start fresh.
            let restored_state = self.load_persisted_state(&cfg.eoa, &cfg.strategy_id);
            // Migrate a pre-multi-session `<eoa>.config.json` onto the keyed
            // layout. Leaving it would resume the SAME session twice on the
            // next boot (once per filename) and leak a second trading task.
            if p == self.legacy_path_for_config(&cfg.eoa) {
                self.persist_config(&cfg);
                if let Some(st) = restored_state.as_ref() {
                    self.persist_state(&cfg.eoa, &cfg.strategy_id, st);
                }
                let _ = std::fs::remove_file(&p);
                let _ = std::fs::remove_file(self.legacy_path_for_state(&cfg.eoa));
            }
            self.start_internal(cfg, restored_state);
        }
    }

    /// Resolve a session key. With an explicit `strategy_id` the lookup is
    /// exact; without one it falls back to this EOA's single running session
    /// (any of them when several run — callers that care pass the id).
    fn resolve_key(&self, eoa: &str, strategy_id: Option<&str>) -> Option<String> {
        if let Some(sid) = strategy_id {
            let k = session_key(eoa, sid);
            return self.engines.contains_key(&k).then_some(k);
        }
        let prefix = format!("{}::", eoa.to_lowercase());
        self.engines
            .iter()
            .find(|e| e.key().starts_with(&prefix))
            .map(|e| e.key().clone())
    }

    /// Token ids the OTHER live sessions on this EOA's deposit wallet already
    /// claim in their ledgers.
    ///
    /// One EOA derives ONE deposit wallet, so every session for it reads the
    /// same on-chain holdings. Adoption (below, in `reconcile_and_value`) is
    /// what turns an unclaimed holding into a managed position — and with two
    /// sessions running it fired both ways: the BTC copy strat bought a tennis
    /// market, and the general strat's next cycle adopted it as its own,
    /// tagged `strategy_id: ""`. That is not a cosmetic mislabel. The adopting
    /// session then counts the position in its `accountValue` (so one dollar
    /// of exposure is banked twice across the wallet, and every proportional
    /// mirror is sized off the inflated number), defends it with its own
    /// stop-loss/take-profit, and can rotate it out to fund an unrelated
    /// entry — one strat trading another strat's book.
    ///
    /// Read WITHOUT holding our own state lock: two sessions reconciling at
    /// once would otherwise each hold a write lock while waiting to read the
    /// other's, which deadlocks.
    fn sibling_claimed_tokens(&self, eoa: &str, strategy_id: &str) -> HashSet<String> {
        let own = session_key(eoa, strategy_id);
        let prefix = format!("{}::", eoa.to_lowercase());
        let mut claimed = HashSet::new();
        for e in self.engines.iter() {
            if !e.key().starts_with(&prefix) || e.key() == &own {
                continue;
            }
            claimed.extend(e.value().state.read().positions.keys().cloned());
        }
        claimed
    }

    /// Record a just-sold token's exit cooldown on EVERY session of this EOA.
    ///
    /// One EOA derives ONE deposit wallet, so a wallet-wide flatten sells
    /// tokens that ANY of its sessions may be tracking or eligible to adopt.
    /// Stamping the cooldown on a single arbitrary session left the siblings
    /// free to adopt the still-listed holding on their next cycle (the
    /// data-api lists it until the fill settles) and sell it a second time.
    fn mark_exited_all_sessions(&self, eoa: &str, token_id: &str, now_ms: i64) {
        let prefix = format!("{}::", eoa.to_lowercase());
        for e in self.engines.iter() {
            if !e.key().starts_with(&prefix) {
                continue;
            }
            e.value()
                .state
                .write()
                .exited_recently
                .insert(token_id.to_string(), now_ms);
        }
    }

    pub fn status_of(&self, eoa: &str, strategy_id: Option<&str>) -> Option<EngineState> {
        let key = self.resolve_key(eoa, strategy_id)?;
        self.engines.get(&key).map(|h| h.state.read().clone())
    }

    pub fn config_of(&self, eoa: &str, strategy_id: Option<&str>) -> Option<EngineConfig> {
        let key = self.resolve_key(eoa, strategy_id)?;
        self.engines.get(&key).map(|h| h.config.read().clone())
    }

    /// Strat ids this EOA has a session for — running ones first, then any
    /// stopped-but-persisted snapshot still on disk. Backs `/live/sessions`,
    /// which is how the console renders several funded strats side by side.
    pub fn session_ids(&self, eoa: &str) -> Vec<String> {
        let prefix = format!("{}::", eoa.to_lowercase());
        let mut out: Vec<String> = self
            .engines
            .iter()
            .filter_map(|e| e.key().strip_prefix(&prefix).map(str::to_string))
            .collect();
        for (persisted_eoa, sid) in self.persisted_sessions() {
            if persisted_eoa == eoa.to_lowercase() && !out.contains(&sid) {
                out.push(sid);
            }
        }
        out
    }

    /// `(eoa, strategyId)` for every session with a config on disk.
    fn persisted_sessions(&self) -> Vec<(String, String)> {
        let mut out = Vec::new();
        let Ok(rd) = std::fs::read_dir(&self.disk_dir) else { return out };
        for entry in rd.flatten() {
            let p = entry.path();
            if !p.is_file() { continue; }
            let Some(name) = p.file_name().and_then(|n| n.to_str()) else { continue };
            if !name.ends_with(".config.json") { continue; }
            // Read the config rather than parsing the filename — the strat id
            // is sanitized on the way to disk, so it isn't round-trippable.
            let Ok(raw) = std::fs::read_to_string(&p) else { continue };
            let Ok(cfg) = serde_json::from_str::<EngineConfig>(&raw) else { continue };
            out.push((cfg.eoa.to_lowercase(), cfg.strategy_id));
        }
        out
    }

    /// Last persisted (config, state) for one session — `/live/status`'s
    /// fallback when no engine is running, so the per-strat ledger and tagged
    /// open positions stay visible across engine stops and API restarts.
    pub fn persisted_snapshot(
        &self,
        eoa: &str,
        strategy_id: Option<&str>,
    ) -> Option<(EngineConfig, EngineState)> {
        let cfg = self.load_persisted_config(eoa, strategy_id)?;
        let state = self.load_persisted_state(eoa, &cfg.strategy_id)?;
        Some((cfg, state))
    }

    /// EOAs the scheduled liquidation task is allowed to flatten — deduped,
    /// since one wallet now writes one file per funded strat.
    ///
    /// NOT "every EOA with a config on disk". A flatten sells the deposit
    /// wallet's ENTIRE on-chain book at best bid, including positions the
    /// engine never bought, so it must only ever fire for a wallet whose
    /// owner has actually opted into real order placement: the session has
    /// to be still running (`stopped: false` — `stop_one` deliberately keeps
    /// the config file so the console can still read the ledger) AND have
    /// `auto_execute` on. A wallet that only ever dry-ran, or whose sessions
    /// were all stopped, is never touched.
    pub fn persisted_eoas(&self) -> Vec<String> {
        let mut out: Vec<String> = Vec::new();
        let Ok(rd) = std::fs::read_dir(&self.disk_dir) else { return out };
        for entry in rd.flatten() {
            let p = entry.path();
            if !p.is_file() { continue; }
            let Some(name) = p.file_name().and_then(|n| n.to_str()) else { continue };
            if !name.ends_with(".config.json") { continue; }
            let Ok(raw) = std::fs::read_to_string(&p) else { continue };
            let Ok(cfg) = serde_json::from_str::<EngineConfig>(&raw) else { continue };
            if cfg.stopped || !cfg.auto_execute { continue; }
            let eoa = cfg.eoa.to_lowercase();
            if !out.contains(&eoa) { out.push(eoa); }
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

        // A flatten is wallet-wide, not strat-scoped, so its log rows land on
        // whichever of the wallet's sessions is running (any one of them).
        let log_session = self.resolve_key(eoa, None);
        let handle = log_session
            .as_ref()
            .and_then(|k| self.engines.get(k).map(|h| h.value().clone()));

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
                // Accepted but RESTING — the flatten is not done for this leg.
                // Say so in the leg (an operator reading `placed: 17` needs to
                // know how many actually crossed) and realize nothing. The
                // wallet-wide cooldown still applies so the exit passes don't
                // stack another order on this one.
                Ok(resp) if order_is_resting(&resp) => {
                    result.skipped += 1;
                    result.legs.push(LiquidationLeg {
                        token_id: pos.token_id.clone(),
                        market: pos.market.clone(),
                        size: pos.size,
                        price: Some(bid),
                        status: "resting".into(),
                        detail: Some(match order_id_of(&resp) {
                            Some(id) => format!("accepted but unfilled — resting on the book (order {})", id),
                            None => "accepted but unfilled — resting on the book".into(),
                        }),
                    });
                    self.mark_exited_all_sessions(
                        eoa,
                        &pos.token_id,
                        chrono::Utc::now().timestamp_millis(),
                    );
                    if let Some(h) = &handle {
                        let mut s = h.state.write();
                        s.total_orders_placed += 1;
                        push_log(&mut s.log, mk_log(
                            "RESTING",
                            &pos.token_id,
                            format!(
                                "LIQUIDATE SELL {:.0} @ {:.0}¢ accepted but UNFILLED — resting on the book · {}",
                                pos.size, bid * 100.0, pos.market
                            ),
                            None,
                        ));
                    }
                }
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
                    // Cooldown first, on every session of the wallet — see
                    // `mark_exited_all_sessions`. The ledger booking below
                    // still belongs to the one session that tracked the token.
                    self.mark_exited_all_sessions(
                        eoa,
                        &pos.token_id,
                        chrono::Utc::now().timestamp_millis(),
                    );
                    if let Some(h) = &handle {
                        let mut s = h.state.write();
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
                            stats.fees += crate::fees::fee_for_fill(&pos.market, sold, bid);
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
            let sid = h.config.read().strategy_id.clone();
            self.persist_state(eoa, &sid, &h.state.read());
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

    /// Start an engine. If one already exists for this (EOA, strat), it's
    /// stopped and replaced (lets the user reconfigure mid-session without a
    /// manual stop). Sessions for the wallet's OTHER strats keep running —
    /// funding a second strat adds a session, it doesn't take over the wallet.
    ///
    /// Restores the session's persisted state across the swap so a reconfigure
    /// or an auto-execute toggle does NOT wipe order counters, the copy log, or
    /// `copied_ids`. Without this, every restart began from an empty state:
    /// the UI showed 0 trades despite real fills, and the engine re-mirrored
    /// trades it had already copied (cleared `copied_ids` → duplicate orders).
    pub fn start(self: &Arc<Self>, cfg: EngineConfig) {
        let key = session_key(&cfg.eoa, &cfg.strategy_id);
        if let Some((_, existing)) = self.engines.remove(&key) {
            existing.cancel.store(true, Ordering::Release);
            if let Some(t) = existing.task.lock().take() {
                t.abort();
            }
        }
        self.persist_config(&cfg);
        // Re-load whatever the engine last persisted (counters, log, copied
        // ids, cursors). Falls back to a fresh state for a brand-new session.
        let restored = self.load_persisted_state(&cfg.eoa, &cfg.strategy_id);
        self.start_internal(cfg, restored);
    }

    /// Read the persisted `EngineState` for one session from disk, if any.
    /// Falls back to the pre-multi-session `<eoa>.state.json`.
    fn load_persisted_state(&self, eoa: &str, strategy_id: &str) -> Option<EngineState> {
        let read = |p: PathBuf| {
            std::fs::read_to_string(&p)
                .ok()
                .and_then(|s| serde_json::from_str::<EngineState>(&s).ok())
        };
        read(self.path_for_state(eoa, strategy_id))
            .or_else(|| read(self.legacy_path_for_state(eoa)))
    }

    /// Read the persisted `EngineConfig` for one session from disk, if any.
    /// With no strat id, returns whichever of the EOA's persisted sessions is
    /// found first (plus the legacy single-session file).
    fn load_persisted_config(&self, eoa: &str, strategy_id: Option<&str>) -> Option<EngineConfig> {
        let read = |p: PathBuf| {
            std::fs::read_to_string(&p)
                .ok()
                .and_then(|s| serde_json::from_str::<EngineConfig>(&s).ok())
        };
        if let Some(sid) = strategy_id {
            return read(self.path_for_config(eoa, sid))
                .or_else(|| read(self.legacy_path_for_config(eoa)).filter(|c| c.strategy_id == sid));
        }
        for (persisted_eoa, sid) in self.persisted_sessions() {
            if persisted_eoa == eoa.to_lowercase() {
                if let Some(c) = read(self.path_for_config(eoa, &sid)) { return Some(c); }
            }
        }
        read(self.legacy_path_for_config(eoa))
    }

    /// The `auto_execute` currently in effect for a session — from the running
    /// engine if one exists, else the last persisted config. `None` when
    /// neither exists (a brand-new session, so the caller's own value stands).
    /// Lets `/live/start` keep a live session's execution mode sticky across a
    /// config re-post that omits the flag, instead of reverting to DRY RUN.
    pub fn current_auto_execute(&self, eoa: &str, strategy_id: &str) -> Option<bool> {
        self.config_of(eoa, Some(strategy_id))
            .or_else(|| self.load_persisted_config(eoa, Some(strategy_id)))
            .map(|c| c.auto_execute)
    }

    fn persist_config(&self, cfg: &EngineConfig) {
        let path = self.path_for_config(&cfg.eoa, &cfg.strategy_id);
        if let Ok(json) = serde_json::to_string_pretty(cfg) {
            let _ = std::fs::write(&path, json);
            restrict_perms(&path);
        }
    }

    fn persist_state(&self, eoa: &str, strategy_id: &str, state: &EngineState) {
        let path = self.path_for_state(eoa, strategy_id);
        if let Ok(json) = serde_json::to_string(state) {
            let _ = std::fs::write(&path, json);
            restrict_perms(&path);
        }
    }

    fn start_internal(self: &Arc<Self>, cfg: EngineConfig, restore: Option<EngineState>) {
        let mut initial = restore.unwrap_or_else(EngineState::empty);
        initial.status = EngineStatus::Running;
        initial.error = None;
        // `minMinutesToClose: 0` is a legitimate setting (the candle lane
        // needs it), but on a REAL-MONEY copy session it opens the sub-hour
        // Up/Down lane the gate exists to keep the engine out of — measured
        // at −$580 across 2040 mirrors on this deployment. Silence is what
        // let that run for weeks, so say it out loud, once, at start.
        if cfg.auto_execute
            && cfg.momentum.is_none()
            && cfg.min_minutes_to_close.map_or(true, |m| m <= 0.0)
        {
            push_log(&mut initial.log, LogEntry {
                id: format!("guard-off-{}", chrono::Utc::now().timestamp_millis()),
                timestamp: chrono::Utc::now().timestamp_millis(),
                kind: "WARN".into(),
                reason: Some(
                    "minMinutesToClose is OFF — this session may mirror 5m/15m Up-or-Down candles, \
                     which resolve faster than a poller can react and lose structurally. \
                     Set it to 60 unless you mean to trade them."
                        .into(),
                ),
                trader_address: None,
                trades_seen: None,
            });
        }
        let state = Arc::new(RwLock::new(initial));
        let cancel = Arc::new(AtomicBool::new(false));

        let handle = Arc::new(EngineHandle {
            config: RwLock::new(cfg.clone()),
            state: state.clone(),
            cancel: cancel.clone(),
            task: parking_lot::Mutex::new(None),
        });
        // Never silently replace a live handle — the old task would keep
        // trading with nobody holding its cancel flag. `start()` clears the
        // slot first; a resume hitting an occupied key means duplicate config
        // files, so drop the newcomer rather than leak a second trader.
        let key = session_key(&cfg.eoa, &cfg.strategy_id);
        if self.engines.contains_key(&key) {
            tracing::warn!(session = %key, "engine already running for this strat; skipping duplicate start");
            cancel.store(true, Ordering::Release);
            return;
        }
        self.engines.insert(key, handle.clone());

        // Spawn the loop.
        let registry = Arc::clone(self);
        let task_cfg = cfg;
        let task = tokio::spawn(async move {
            registry.run_loop(task_cfg, state, cancel).await;
        });
        *handle.task.lock() = Some(task);
    }

    /// Explicit user stop. Clears the persisted config so the next API boot
    /// doesn't auto-resume the session. With no `strategy_id` this stops EVERY
    /// session the wallet has running — the "stop everything" the old
    /// single-session `/live/stop` meant.
    pub fn stop(&self, eoa: &str, strategy_id: Option<&str>) -> bool {
        let ids: Vec<String> = match strategy_id {
            Some(sid) => vec![sid.to_string()],
            None => self.session_ids(eoa),
        };
        let mut stopped = false;
        for sid in ids {
            stopped |= self.stop_one(eoa, &sid);
        }
        stopped
    }

    fn stop_one(&self, eoa: &str, strategy_id: &str) -> bool {
        let lc = eoa.to_lowercase();
        let key = session_key(&lc, strategy_id);
        // Flag the persisted config stopped rather than deleting it: the
        // console still needs it to show this strat's realized ledger and
        // open positions, and `resume_persisted` honors the flag so a stopped
        // strat never revives on an API restart. Runs even with no live
        // handle, so a session stopped twice stays stopped.
        let mut marked = false;
        if let Some(mut cfg) = self.load_persisted_config(&lc, Some(strategy_id)) {
            cfg.stopped = true;
            self.persist_config(&cfg);
            marked = true;
        }
        let _ = std::fs::remove_file(self.legacy_path_for_config(&lc));
        let Some((_, handle)) = self.engines.remove(&key) else { return marked; };
        handle.cancel.store(true, Ordering::Release);
        if let Some(t) = handle.task.lock().take() {
            t.abort();
        }
        // Mark state as stopped, then persist final shape so a quick reload
        // sees the stopped state (the per-strat ledger survives the stop).
        {
            let mut s = handle.state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&lc, strategy_id, &handle.state.read());
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
        // The cadence is re-derived every cycle (see `effective_interval_ms`):
        // clamped up to the rate-limit floor — so a stale 5s config self-heals
        // on resume without the user re-saving — and widened when the watchlist
        // grows past what one period can fetch. Auto-watchlist sessions change
        // roster size mid-run, so this can't be computed once at start.
        let mut effective_interval_ms =
            effective_interval_for(&cfg, cfg.traders.iter().filter(|t| t.enabled).count());

        let now_ms = chrono::Utc::now().timestamp_millis();
        {
            let mut s = state.write();
            for t in &cfg.traders {
                let key = t.address.to_lowercase();
                s.trader_cursors
                    .entry(key)
                    .or_insert_with(|| now_ms - effective_interval_ms as i64);
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
                            // Engines are keyed `eoa::strategyId`, not by EOA
                            // alone — a bare-EOA lookup here always missed, so
                            // the handle kept the pre-refresh roster and the
                            // next `set_auto_execute` (which reads the handle's
                            // config) persisted it back, reverting the refresh.
                            if let Some(h) = self
                                .engines
                                .get(&session_key(&cfg.eoa, &cfg.strategy_id))
                            {
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
            // Ledger ↔ chain reconciliation + account pricing, before any
            // sizing decision reads either. See `reconcile_and_value`.
            let account = self.reconcile_and_value(&cfg, &state).await;
            let mut new_observed: Vec<ObservedTrade> = Vec::new();
            let mut trader_sync_updates: Vec<(String, i64)> = Vec::new();
            let mut cursor_updates: Vec<(String, i64)> = Vec::new();
            let mut errors: Vec<(String, String)> = Vec::new();
            // Mirror candidates collected this cycle: (newly-observed trade,
            // copyRatio, leader shares still held in that token). Executed
            // after the state commit so order-placement HTTP never runs under
            // the state lock.
            let mut mirror_candidates: Vec<MirrorCandidate> = Vec::new();
            // (address, stats) for every trader polled this cycle — the input
            // to the trader FILTER, which can only rank once the whole
            // watchlist has been scored.
            let mut cycle_trader_stats: Vec<(String, TraderRoiStats)> = Vec::new();
            // Why newly-seen BUYs never became candidates, tallied by gate.
            // Silently dropping them is what makes a strat whose filters
            // exclude all of its leaders' flow look like a dead engine.
            let mut gated: HashMap<&'static str, GateHits> = HashMap::new();
            // Every leader book read this cycle, with the ms it was read at —
            // the input to the leader-flat sweep below.
            let mut leader_books: HashMap<String, (i64, LeaderBook)> = HashMap::new();

            // Snapshot cursors so we don't hold the RwLock across the HTTP fan-out.
            // `held_tokens` rides along so an exit signal can be recognised as
            // one for a token we actually hold without re-taking the lock.
            let (cursors, enabled_traders, held_tokens): (HashMap<String, i64>, Vec<TraderEntry>, HashSet<String>) = {
                let s = state.read();
                let cursors = s.trader_cursors.clone();
                let held = s.positions.iter().filter(|(_, p)| p.size > 0.0).map(|(t, _)| t.clone()).collect();
                (cursors, cfg.traders.iter().filter(|t| t.enabled).cloned().collect(), held)
            };
            // Re-derive the cadence against THIS cycle's roster — an
            // auto-watchlist refresh (or a mid-run enable/disable) can change
            // how long the fan-out needs.
            effective_interval_ms = effective_interval_for(&cfg, enabled_traders.len());
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
                let cursor = *cursors.get(&key).unwrap_or(&(now_ms - effective_interval_ms as i64));
                // The leader's balance sheet (cached ~10m) — the denominator
                // of proportional sizing and the reference for proportional
                // exits. Spaced from the activity fetch below on a cache miss.
                let book = match self
                    .leader_book_spaced(&trader.address, cfg.inter_request_delay_ms)
                    .await
                {
                    Some((read_at, b)) => {
                        leader_books.insert(key.clone(), (read_at, b.clone()));
                        Some(b)
                    }
                    None => None,
                };
                match fetch_recent_activity(&self.http, &trader.address).await {
                    Ok(items) => {
                        trader_sync_updates.push((key.clone(), chrono::Utc::now().timestamp_millis()));
                        // Parse the trader's full recent activity once, then use
                        // it both for the proportional copyRatio (volume over the
                        // window) and for new-trade detection.
                        // Fills first, then collapsed to one trade per leader
                        // action — see `aggregate_fills`. Volume accounting
                        // below is unaffected (the sums are identical); what
                        // changes is that the copy loop can no longer drop the
                        // 2nd..Nth fill of a book-walking order.
                        let parsed: Vec<ObservedTrade> = aggregate_fills(
                            items
                                .iter()
                                .filter_map(|v| parse_activity_trade(v, &trader.address))
                                .collect(),
                        );

                        // Fallback sizing denominator: traderVol =
                        // max(buyVol, sellVol, 1) over the window, counting ONLY
                        // markets that pass the strat's topic query — the
                        // backtest's `traderCopyRatio` filters it the same way, so
                        // a "bitcoin"-only strat sizes against the trader's
                        // bitcoin volume, not their whole book. Used only when the
                        // leader's bankroll can't be read (see `copy_ratio_for`).
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
                        let weight_fraction = trader.weight / total_weight;
                        let capital_alloc = cfg.capital * weight_fraction;
                        let copy_ratio = copy_ratio_for(
                            account.total,
                            weight_fraction,
                            book.as_ref().map(|b| b.bankroll),
                            capital_alloc,
                            trader_vol,
                            cfg.sizing,
                            cfg.turnover,
                        );

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
                        cycle_trader_stats.push((key.clone(), roi_stats));

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
                            // A SELL in a token we HOLD is honored whatever the
                            // topic query says: editing `marketQuery` mid-run
                            // would otherwise strand every position opened
                            // under the old one, with no signal left that can
                            // ever close it.
                            let mirror_ok = match t.side.as_str() {
                                "BUY" => {
                                    if !market_ok {
                                        gated.entry("market query").or_default().hit(&t.trader);
                                        false
                                    } else if let Some(why) =
                                        trade_filter_reject(&t, &cfg.trade_filters)
                                    {
                                        gated.entry(why).or_default().hit(&t.trader);
                                        false
                                    } else {
                                        true
                                    }
                                }
                                "SELL" => market_ok || held_tokens.contains(&t.token_id),
                                _ => false,
                            };
                            if mirror_ok && !t.token_id.is_empty() {
                                mirror_candidates.push(MirrorCandidate {
                                    // The leader's book was read this cycle,
                                    // i.e. AFTER this trade settled — so for
                                    // a SELL this is what they kept.
                                    leader_remaining: book
                                        .as_ref()
                                        .map(|b| b.sizes.get(&t.token_id).copied().unwrap_or(0.0)),
                                    trade: t.clone(),
                                    copy_ratio,
                                    swept: false,
                                });
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

            // ── MARKET SENTIMENT — the gate that needs the tape ──
            // The last dimension of `tradeFilters`, applied here instead of in
            // the loop above because it is the only one that cannot be
            // answered from the trade: it asks which way the crowd had moved
            // the odds on the outcome the leader bought, which is price
            // history. Batched into one round of requests over the cycle's
            // distinct candidate tokens, TTL-cached, and read AT THE TRADE'S
            // OWN TIMESTAMP so a fill discovered ten minutes late is still
            // judged by the market it was actually taken in.
            //
            // ENTRIES only, exactly like the rest of the trade filter — a
            // leader's exit of something we hold is never gated, or the
            // position would have nothing left that can close it.
            let sentiment_note: Option<String> = if crate::sentiment::filter_active(
                &cfg.trade_filters.as_ref().and_then(|f| f.sentiment.clone()),
            ) {
                let sf = cfg.trade_filters.as_ref().and_then(|f| f.sentiment.clone());
                let tokens: Vec<String> = mirror_candidates
                    .iter()
                    .filter(|c| c.trade.side == "BUY")
                    .map(|c| c.trade.token_id.clone())
                    .filter(|t| !t.is_empty())
                    .collect();
                let asked = tokens.len();
                let (readings, over_budget) =
                    crate::sentiment::fetch_sentiment(&self.http, &tokens, cycle_started_at, &sf).await;
                let before = mirror_candidates.len();
                let mut unreadable = 0usize;
                mirror_candidates.retain(|c| {
                    if c.trade.side != "BUY" {
                        return true;
                    }
                    let reading = readings.get(&c.trade.token_id);
                    if reading.map(|r| r.lean.as_str()).unwrap_or("unknown") == "unknown" {
                        unreadable += 1;
                    }
                    crate::sentiment::sentiment_reject(reading, &sf).is_none()
                });
                let dropped = before - mirror_candidates.len();
                if dropped > 0 {
                    // One bucket, named the way the gate names itself, so the
                    // heartbeat's "why did nothing get copied" tally reads the
                    // same as every other dimension.
                    gated.entry("sentiment").or_default().count += dropped;
                }
                Some(format!(
                    "SENTIMENT {} · read {}/{} markets · {} cut{}",
                    crate::sentiment::describe(&sf),
                    asked.saturating_sub(unreadable),
                    asked,
                    dropped,
                    if over_budget > 0 {
                        format!(" · {} past budget", over_budget)
                    } else {
                        String::new()
                    },
                ))
            } else {
                None
            };

            // ── Trader FILTER — keep only the top-ranked traders ──
            // Ranking is a whole-watchlist decision, so it runs once the loop
            // above has scored everyone, and it drops CANDIDATES rather than
            // skipping the fetch: a filtered-out trader stays observed (the
            // console still shows their flow, and their stats keep updating,
            // which is how they climb back in). It gates ENTRIES only: a
            // trader can fall out of the top N while we still hold what we
            // bought copying them, and dropping their exit would leave that
            // position with nothing left to close it.
            let filter_note: Option<String> = cfg.filter.as_ref().map(|f| {
                let ranked =
                    select_top_traders(&cycle_trader_stats, f, chrono::Utc::now().timestamp_millis());
                let kept: HashSet<String> =
                    ranked.iter().filter(|r| r.kept).map(|r| r.address.clone()).collect();
                let before = mirror_candidates.len();
                mirror_candidates
                    .retain(|c| c.trade.side == "SELL" || kept.contains(&c.trade.trader.to_lowercase()));
                let dropped = before - mirror_candidates.len();
                let metric = f.metric.as_deref().unwrap_or("score");
                // Stale traders read as "·0x1234…abcd 17d" — the roster is the
                // only place an operator sees WHY a name stopped being copied,
                // and "score 0.081, not copied" without the age is a mystery.
                let roster = ranked
                    .iter()
                    .take(8)
                    .map(|r| {
                        if r.stale {
                            format!("·{} {}", short_addr(&r.address), format_age_short(r.age_ms))
                        } else {
                            format!(
                                "{}{} {:.3}",
                                if r.kept { "✓" } else { "·" },
                                short_addr(&r.address),
                                r.score,
                            )
                        }
                    })
                    .collect::<Vec<_>>()
                    .join(" ");
                let stale_note = match f.max_stale_hours {
                    Some(h) if h > 0.0 => {
                        let n = ranked.iter().filter(|r| r.stale).count();
                        format!(" · {} stale (>{}h)", n, fmt_hours(h))
                    }
                    _ => String::new(),
                };
                format!(
                    "FILTER · top {} by {} · {}/{} traders copied{} · {} candidate(s) dropped · {}",
                    f.top_n.unwrap_or(DEFAULT_FILTER_TOP_N),
                    metric,
                    kept.len(),
                    ranked.len(),
                    stale_note,
                    dropped,
                    roster,
                )
            });

            // Commit all the cycle's effects in a single state-lock window.
            let cycle_ended_at = chrono::Utc::now().timestamp_millis();
            {
                let mut s = state.write();
                // Merge observed trades, newest-first, capped at OBSERVED_CAP.
                let new_trade_count = new_observed.len();
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
                // The count is the trades this cycle actually pulled past each
                // trader's cursor — NOT observed trades stamped after the cycle
                // started, which is what it used to compare: a leader trade is
                // timestamped when THEY traded, always before we polled for it,
                // so that filter read 0 on virtually every cycle and a working
                // engine looked asleep.
                let mut summary = if errors.is_empty() {
                    format!(
                        "polled {} traders · {} new trades observed",
                        enabled_traders.len(),
                        new_trade_count,
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
                // Which gate ate this cycle's entries. A strat that observes
                // flow every cycle and mirrors none of it is a filter
                // decision, not a fault — say so instead of leaving the
                // console to imply the engine is broken.
                // Same tally, kept as structured state so the LIVE panel can
                // show it standing still instead of only in this one log line.
                for (why, hits) in &gated {
                    tally_gate(&mut s.gated_recently, why, hits.count as u64, &hits.traders, cycle_ended_at);
                }
                s.gated_recently
                    .retain(|_, t| cycle_ended_at - t.last_at <= GATE_WINDOW_MS);
                if !gated.is_empty() {
                    let mut by_gate: Vec<(&&str, &GateHits)> = gated.iter().collect();
                    by_gate.sort_by(|a, b| b.1.count.cmp(&a.1.count).then(a.0.cmp(b.0)));
                    let total: usize = gated.values().map(|h| h.count).sum();
                    summary.push_str(&format!(
                        " · {} BUY(s) gated ({})",
                        total,
                        by_gate
                            .iter()
                            .map(|(k, h)| format!("{} {}", h.count, k))
                            .collect::<Vec<_>>()
                            .join(", "),
                    ));
                }
                // The trader ranking gets its own row: which traders are being
                // copied right now is the single thing a FILTER strat's owner
                // needs to see, and burying it in the cycle summary hides it.
                if let Some(note) = &filter_note {
                    push_log(&mut s.log, LogEntry {
                        id: format!("filter-{}", cycle_ended_at),
                        timestamp: cycle_ended_at,
                        kind: "FILTER".into(),
                        reason: Some(note.clone()),
                        trader_address: None,
                        trades_seen: None,
                    });
                }
                // Same reasoning for SENTIMENT, and one number in particular:
                // how many of the cycle's candidate markets the mood could
                // actually be READ for. A sentiment gate over markets with no
                // price history is a gate over nothing, and that has to be
                // visible in the feed rather than inferred from a quiet book.
                if let Some(note) = &sentiment_note {
                    push_log(&mut s.log, LogEntry {
                        id: format!("sentiment-{}", cycle_ended_at),
                        timestamp: cycle_ended_at,
                        kind: "SENTIMENT".into(),
                        reason: Some(note.clone()),
                        trader_address: None,
                        trades_seen: None,
                    });
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
                // Cycles are a fixed PERIOD from their own start, not a fixed
                // gap after the work finishes — otherwise every fetch's latency
                // is added to the cadence and a "30s" 10-trader session actually
                // syncs every ~35s.
                s.next_cycle_at = Some(cycle_started_at + effective_interval_ms as i64);
                s.effective_interval_ms = effective_interval_ms;
                s.status = EngineStatus::Running;
            }

            // Persist snapshot so a restart restores state.
            self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());

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
                            self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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

            // ── Leader-flat sweep ──
            // Backstop for every exit the activity feed can miss (engine
            // downtime, a sell aged off the recent page, an unreachable
            // trader): if a leader's own book no longer shows a token we
            // opened copying them, they are out and so are we. Runs after the
            // FILTER so a swept exit can't be dropped by it, and before
            // execution so it rides the same sell path as a seen SELL.
            {
                let flat = {
                    let s = state.read();
                    let queued: HashSet<String> = mirror_candidates
                        .iter()
                        .filter(|c| c.trade.side == "SELL")
                        .map(|c| c.trade.token_id.clone())
                        .collect();
                    leader_flat_exits(
                        &s.positions,
                        &s.exited_recently,
                        &leader_books,
                        &queued,
                        chrono::Utc::now().timestamp_millis(),
                    )
                };
                if !flat.is_empty() {
                    let markets = flat
                        .iter()
                        .map(|c| c.trade.market.as_str())
                        .collect::<Vec<_>>()
                        .join(" · ");
                    self.log_and_persist(&cfg, &state, mk_log(
                        "INFO",
                        &format!("flat-sweep-{}", cycle_started_at),
                        format!(
                            "LEADER_FLAT · {} held position(s) their leader no longer holds — exiting · {}",
                            flat.len(), markets,
                        ),
                        None,
                    ));
                    mirror_candidates.extend(flat);
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

            // Sleep until this cycle's PERIOD is up — measured from when the
            // cycle started, so the poll cadence the user picked is the cadence
            // traders are actually synced at (the fan-out + order placement
            // time is absorbed, not added). A cycle that overran its period
            // sleeps zero and the next one starts immediately; the fan-out
            // floor in `effective_interval_for` keeps that from becoming a
            // no-gap hot loop. Polled in 200ms steps so cancel kicks in fast.
            let deadline_ms = cycle_started_at + effective_interval_ms as i64;
            let step = 200u64;
            loop {
                if cancel.load(Ordering::Acquire) { break; }
                let remaining = deadline_ms - chrono::Utc::now().timestamp_millis();
                if remaining <= 0 { break; }
                tokio::time::sleep(Duration::from_millis(step.min(remaining as u64))).await;
            }
        }

        // Loop exited (stop requested). Mark stopped + final persist.
        {
            let mut s = state.write();
            s.status = EngineStatus::Stopped;
            s.next_cycle_at = None;
        }
        self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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
        candidates: Vec<MirrorCandidate>,
    ) {
        // Leader SELLs first: exits free capital that this cycle's BUYs can
        // then deploy — same ordering effect as the auto-redeem pass, and the
        // chronological replay the backtest performs.
        let (sell_candidates, mut candidates): (Vec<_>, Vec<_>) =
            candidates.into_iter().partition(|c| c.trade.side == "SELL");
        self.execute_mirror_sells(cfg, state, cancel, sell_candidates).await;

        // All knobs come from the strat-supplied config — nothing hardcoded.
        let user_floor = cfg.min_order_size;
        let min_shares = cfg.min_shares;
        let ceiling = cfg.max_order_size.unwrap_or(f64::INFINITY);
        let mut placed_this_cycle = 0usize;

        // Highest EP score first: the best candidates claim free capital and win
        // rebalance contests before weaker ones get a look.
        candidates.sort_by(|a, b| {
            b.trade.score.partial_cmp(&a.trade.score).unwrap_or(std::cmp::Ordering::Equal)
        });

        // Internal capital budget: the strat allocation minus cost basis
        // already deployed, and never more than the wallet's REAL free USDC
        // (`state.balance`, refreshed this cycle by `reconcile_and_value`).
        // Budgeting off the config alone is what produced tens of thousands of
        // "not enough balance" CLOB rejections on an empty wallet. Selling a
        // position returns its cost basis here; a BUY subtracts its notional.
        let mut free_capital = {
            let s = state.read();
            let deployed: f64 = s.positions.values().map(|p| p.size * p.entry_price).sum();
            let allocated = (cfg.capital - deployed).max(0.0);
            match s.balance {
                Some(cash) => allocated.min(cash),
                None => allocated,
            }
        };
        // Nothing to spend and nothing to sell for it — say so once instead of
        // once per candidate, and don't fire orders the CLOB will reject.
        // Holdings + rebalancing still count as fundable: the loop below can
        // liquidate a lower-score position to pay for a better candidate.
        let can_rebalance = cfg.rebalance_enabled
            && state.read().positions.values().any(|p| p.size > 0.0);
        if cfg.auto_execute && free_capital < cfg.min_order_size && !can_rebalance && !candidates.is_empty() {
            self.log_and_persist(cfg, state, mk_log(
                "SKIP",
                &format!("nocash-{}", chrono::Utc::now().timestamp_millis()),
                format!(
                    "NO_CASH · ${:.2} free < ${:.2} min order — {} mirror(s) deferred until an exit or a deposit frees capital",
                    free_capital, cfg.min_order_size, candidates.len(),
                ),
                None,
            ));
            return;
        }

        for MirrorCandidate { trade, copy_ratio, .. } in candidates {
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

            // ── Staleness gate ──
            // Copying is a race we're already losing by one poll interval; a
            // trade we only just discovered minutes late is being entered at a
            // price the leader never paid. Cheap (no fetch), so it runs first.
            if let Some(max_age) = cfg.max_trade_age_sec.filter(|s| *s > 0.0) {
                let age_sec = (chrono::Utc::now().timestamp_millis() - trade.timestamp) as f64 / 1000.0;
                if age_sec > max_age {
                    let mut s = state.write();
                    insert_copied_id(&mut s.copied_ids, trade.id.clone());
                    let now_ms = chrono::Utc::now().timestamp_millis();
                    tally_gate(&mut s.gated_recently, "stale", 1, std::slice::from_ref(&trade.trader), now_ms);
                    push_log(&mut s.log, mk_log(
                        "SKIP",
                        &trade.id,
                        format!(
                            "STALE · leader traded {:.0}m ago (limit {:.0}m) — the price has moved · {}",
                            age_sec / 60.0, max_age / 60.0, trade.market
                        ),
                        Some(&trade.trader),
                    ));
                    continue;
                }
            }

            // ── Time-to-resolution gate ──
            // Sub-hour Up/Down candles resolve before a 30s poller can even
            // react, so copying them is a structural loss (measured: −$253
            // realized across 1064 such mirrors). Gamma lookup is cached per
            // market, and only reached by trades that already cleared every
            // cheap filter. Unknown end date ⇒ allow (never block on an outage).
            if let Some(min_close) = cfg.min_minutes_to_close.filter(|m| *m > 0.0) {
                let (_, end_ms) = self.resolve_market_meta(&trade.condition_id).await;
                if let Some(end) = end_ms {
                    let mins_left = (end - chrono::Utc::now().timestamp_millis()) as f64 / 60_000.0;
                    if mins_left < min_close {
                        let mut s = state.write();
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        let now_ms = chrono::Utc::now().timestamp_millis();
                        tally_gate(&mut s.gated_recently, "resolves too soon", 1, std::slice::from_ref(&trade.trader), now_ms);
                        push_log(&mut s.log, mk_log(
                            "SKIP",
                            &trade.id,
                            format!(
                                "TOO_SOON · resolves in {:.0}m (min {:.0}m) — short-dated flow is HFT turf · {}",
                                mins_left.max(0.0), min_close, trade.market
                            ),
                            Some(&trade.trader),
                        ));
                        continue;
                    }
                }
            }

            let (size, notional, price) = match plan_mirror(&trade, copy_ratio, user_floor, min_shares, ceiling, cfg.max_slippage_bps, cfg.max_upscale) {
                MirrorPlan::Skip(reason) => {
                    self.log_and_persist(cfg, state, mk_log("SKIP", &trade.id, reason, Some(&trade.trader)));
                    continue;
                }
                MirrorPlan::Place { size, notional, price } => (size, notional, price),
            };

            // DRY RUN: surface intent, place nothing, leave the trade un-copied
            // so it isn't retroactively filled when auto_execute is later enabled.
            if !cfg.auto_execute {
                // This entry cleared every gate — only DRY RUN kept it
                // un-copied. Drop the gate tally exactly as a placed order
                // does, so the console can't tell the owner "your filters
                // blocked them all" about flow the filters just let through.
                {
                    let mut s = state.write();
                    s.gated_recently.clear();
                    // …and say the thing the gate tally can't: this one was
                    // ready to go and only dry run stopped it.
                    tally_dry_run(&mut s.dry_run_recently, Some(&trade.trader), chrono::Utc::now().timestamp_millis());
                }
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
                self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
                continue;
            }

            // ── Capital-aware rebalance ──
            // If this candidate doesn't fit in free capital, try to sell the
            // lowest-score holdings it sufficiently out-scores to make room.
            // Free the fee too. The matcher debits `notional + taker fee`, so
            // freeing exactly the notional funds an order that then bounces
            // for insufficient balance — see crate::fees::fee_headroom.
            // Budget against the cash the matcher will really debit —
            // `size * price` AFTER the whole-share ceil, the `min_shares`
            // floor and the slippage widening. Each of those raises the spend
            // above the planned `notional`, and on a floor-clamped mirror the
            // gap is large (a $3.45 plan at 90¢ with a 5-share floor commits
            // $4.55+), so debiting `notional` let a cycle spend several times
            // its budget. `notional` stays the planned figure the TS engine,
            // the backtest and the logs all agree on.
            let committed = size * price;
            let with_fee = committed + crate::fees::fee_headroom_at(
                committed, price, crate::fees::rate_for_market(&trade.market),
            );
            if with_fee > free_capital && cfg.rebalance_enabled {
                let needed = with_fee - free_capital;
                free_capital += self
                    .free_capital_via_sells(cfg, state, cancel, &trade, needed)
                    .await;
            }
            if with_fee > free_capital + 1e-6 {
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
                            "score {:.3} · need ${:.2} (incl. ${:.2} taker fee), free ${:.2} — no lower-score hold to sell · {}",
                            trade.score, with_fee, with_fee - committed, free_capital, trade.market
                        ),
                        Some(&trade.trader),
                    ));
                }
                self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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
                // Accepted but RESTING on the book — nothing was bought, so
                // record no position and no volume. The capital IS committed
                // (the order can cross at any moment), so the budget is
                // debited exactly as a fill would debit it, and the trade is
                // marked copied so the next cycle doesn't stack a second
                // order on top of this one. If it later trades, the on-chain
                // reconciler adopts the holding.
                Ok(resp) if order_is_resting(&resp) => {
                    free_capital = (free_capital - committed).max(0.0);
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        // The gates let this one through — only the book
                        // didn't cross. Clearing the tallies keeps the console
                        // from telling the owner their filters blocked
                        // everything about flow that reached the CLOB.
                        s.gated_recently.clear();
                        s.dry_run_recently = GateTally::default();
                        insert_copied_id(&mut s.copied_ids, trade.id.clone());
                        push_log(&mut s.log, mk_log(
                            "RESTING",
                            &trade.id,
                            format!(
                                "BUY {:.0} @ {:.0}¢ (${:.2}) accepted but UNFILLED — resting on the book{} · {}",
                                size, price * 100.0, notional,
                                order_id_of(&resp).map(|id| format!(" (order {})", id)).unwrap_or_default(),
                                trade.market
                            ),
                            Some(&trade.trader),
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price, response = %resp, "mirror buy resting (unfilled)");
                }
                Ok(resp) => {
                    free_capital = (free_capital - committed).max(0.0);
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += notional;
                        // An entry landed, so this strat IS copying — drop the
                        // gate tally. Non-empty `gated_recently` then means
                        // "gates fired and nothing has been mirrored since",
                        // which is the only version of it worth warning about.
                        s.gated_recently.clear();
                        s.dry_run_recently = GateTally::default();
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
                            leader: trade.trader.to_lowercase(),
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
                        // Same for an unattributed position (adopted from
                        // chain, or opened before positions carried a leader):
                        // topping it up by copying someone makes them the
                        // leader to follow back out.
                        if entry.leader.is_empty() {
                            entry.leader = trade.trader.to_lowercase();
                        }
                        let stats = s
                            .strat_stats
                            .entry(strat_key(&cfg.strategy_id, ""))
                            .or_default();
                        stats.buys += 1;
                        stats.volume += notional;
                        stats.fees += crate::fees::fee_for_fill(&trade.market, size, price);
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
            self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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
                registry.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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
                    tally_dry_run(&mut state.write().dry_run_recently, None, now);
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
                    // Resting, not filled: the shares are still ours, so keep
                    // the ledger position (and its stop) intact and book no
                    // realized PnL or freed capital. The exit cooldown stops
                    // this cycle's order from being stacked on next cycle;
                    // once it lapses the exit re-prices and retries.
                    Ok(resp) if order_is_resting(&resp) => {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.exited_recently.insert(p.token_id.clone(), now);
                        push_log(&mut s.log, mk_log(
                            "RESTING",
                            &log_id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ accepted but UNFILLED — resting on the book{} · position and stop kept · {}",
                                size, limit_price * 100.0,
                                order_id_of(&resp).map(|id| format!(" (order {})", id)).unwrap_or_default(),
                                p.market
                            ),
                            None,
                        ));
                        drop(s);
                        tracing::info!(eoa = %cfg.eoa, market = %p.market, size, price = limit_price, response = %resp, "momentum sell resting (unfilled)");
                    }
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
                        stats.fees += crate::fees::fee_for_fill(&p.market, size, limit_price);
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
                    tally_dry_run(&mut state.write().dry_run_recently, None, now);
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
                    // Resting, not filled — no position to record. The budget
                    // is still debited: the order is live and can cross.
                    Ok(resp) if order_is_resting(&resp) => {
                        free_capital = (free_capital - notional).max(0.0);
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.gated_recently.clear();
                        s.dry_run_recently = GateTally::default();
                        push_log(&mut s.log, mk_log(
                            "RESTING",
                            &log_id,
                            format!(
                                "BUY {:.0} @ {:.0}¢ (${:.2}) accepted but UNFILLED — resting on the book{} · {}",
                                size, limit_price * 100.0, notional,
                                order_id_of(&resp).map(|id| format!(" (order {})", id)).unwrap_or_default(),
                                p.market
                            ),
                            None,
                        ));
                        drop(s);
                        tracing::info!(eoa = %cfg.eoa, market = %p.market, size, price = limit_price, notional, response = %resp, "momentum entry resting (unfilled)");
                    }
                    Ok(resp) => {
                        free_capital = (free_capital - notional).max(0.0);
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.total_volume_mirrored += notional;
                        s.gated_recently.clear();
                        s.dry_run_recently = GateTally::default();
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
                            // Origination, not copying — no leader to follow out.
                            leader: String::new(),
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
                        stats.fees += crate::fees::fee_for_fill(&p.market, size, limit_price);
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

    /// Mirror a leader's SELL: exit the SAME FRACTION OF OUR POSITION that
    /// the leader exited of theirs. If they sold 40% of their shares we sell
    /// 40% of ours; if they went flat, so do we. That symmetry is what keeps
    /// a copy portfolio tracking its leader — sizing exits off the leader's
    /// *notional* instead (the old behaviour) systematically under-sold, and
    /// the book filled up with positions the leader had long since left
    /// (1064 mirrored entries against 79 exits, measured live).
    ///
    /// Falls back to `plan_mirror` ratio sizing when the leader's book can't
    /// be read. The fill is a marketable SELL at the tick-rounded best bid.
    /// Exits deliberately do NOT count toward `max_orders_per_cycle` (like
    /// stop-losses — an exit signal is never deferred) and, like the
    /// backtest, bypass the semantic entry filters. Leader exits of tokens we
    /// never entered are skipped silently — the per-trader cursor guarantees
    /// each trade is only considered once.
    async fn execute_mirror_sells(
        self: &Arc<Self>,
        cfg: &EngineConfig,
        state: &Arc<RwLock<EngineState>>,
        cancel: &Arc<AtomicBool>,
        candidates: Vec<MirrorCandidate>,
    ) {
        for MirrorCandidate { trade, copy_ratio, leader_remaining, swept } in candidates {
            if cancel.load(Ordering::Acquire) { break; }
            if state.read().copied_ids.contains(&trade.id) { continue; }
            let why = if swept { "leader no longer holds it" } else { "leader exited" };

            let Some(pos) = state.read().positions.get(&trade.token_id).cloned() else { continue };
            if pos.size <= 0.0 { continue; }

            let ceiling = cfg.max_order_size.unwrap_or(f64::INFINITY);
            let mut size = match leader_remaining {
                // Proportional exit: sold / (sold + kept) of our own holding.
                Some(kept) => {
                    let before = trade.size + kept.max(0.0);
                    let fraction = if before > 0.0 { (trade.size / before).clamp(0.0, 1.0) } else { 1.0 };
                    pos.size * fraction
                }
                // Book unreadable — fall back to ratio sizing on the leader's
                // notional, through the same clamps entries use.
                None => match plan_mirror(&trade, copy_ratio, cfg.min_order_size, cfg.min_shares, ceiling, cfg.max_slippage_bps, cfg.max_upscale) {
                    MirrorPlan::Skip(reason) => {
                        self.log_and_persist(cfg, state, mk_log(
                            "SKIP", &trade.id, format!("SELL {}", reason), Some(&trade.trader),
                        ));
                        continue;
                    }
                    MirrorPlan::Place { size, .. } => size,
                },
            }
            .min(pos.size);
            // If the remainder would be an un-sellable stub (< min_shares),
            // exit the whole position instead of stranding it.
            if pos.size - size < cfg.min_shares { size = pos.size; }
            if size <= 0.0 { continue; }

            // DRY RUN: surface the exit intent, place nothing, leave the trade
            // un-copied — mirrors the BUY-side dry-run contract.
            if !cfg.auto_execute {
                let msg = format!(
                    "DRY RUN · would SELL {:.0} of {:.0} held @ bid · {} · {} · token {}",
                    size, pos.size, why, trade.market, short_token(&trade.token_id),
                );
                tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, "{}", msg);
                tally_dry_run(
                    &mut state.write().dry_run_recently,
                    Some(&trade.trader),
                    chrono::Utc::now().timestamp_millis(),
                );
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
                // Resting, not filled — the shares are still held, so leave
                // the ledger position (and its stop) alone and book nothing.
                Ok(resp) if order_is_resting(&resp) => {
                    let mut s = state.write();
                    s.total_orders_placed += 1;
                    insert_copied_id(&mut s.copied_ids, trade.id.clone());
                    s.exited_recently.insert(
                        trade.token_id.clone(),
                        chrono::Utc::now().timestamp_millis(),
                    );
                    push_log(&mut s.log, mk_log(
                        "RESTING",
                        &trade.id,
                        format!(
                            "SELL {:.0} @ {:.0}¢ accepted but UNFILLED — resting on the book{} · position and stop kept · {}",
                            size, bid * 100.0,
                            order_id_of(&resp).map(|id| format!(" (order {})", id)).unwrap_or_default(),
                            trade.market
                        ),
                        Some(&trade.trader),
                    ));
                    drop(s);
                    tracing::info!(eoa = %cfg.eoa, market = %trade.market, size, price = bid, response = %resp, "mirror sell resting (unfilled)");
                }
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
                        stats.fees += crate::fees::fee_for_fill(&trade.market, size, bid);
                        stats.last_fill_at = now;
                        push_realized(&mut s.realized_events, key, proceeds - cost_basis, cost_basis, now);
                        push_log(&mut s.log, mk_log(
                            "COPY_SELL",
                            &trade.id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ · {} · realized {:+.2} · {}",
                                size, bid * 100.0, why, proceeds - cost_basis, trade.market
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
            self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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
                // Resting, not filled — no capital was actually freed, so
                // report none. Reporting it would fund a BUY against money
                // this wallet does not have yet and the matcher would bounce
                // the entry for insufficient balance. Position kept.
                Ok(resp) if order_is_resting(&resp) => {
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.exited_recently.insert(
                            pos.token_id.clone(),
                            chrono::Utc::now().timestamp_millis(),
                        );
                        push_log(&mut s.log, mk_log(
                            "RESTING",
                            &pos.token_id,
                            format!(
                                "SELL {:.0} @ {:.0}¢ accepted but UNFILLED — resting on the book{} · no capital freed · {}",
                                pos.size, bid * 100.0,
                                order_id_of(&resp).map(|id| format!(" (order {})", id)).unwrap_or_default(),
                                pos.market
                            ),
                            None,
                        ));
                    }
                    tracing::info!(eoa = %cfg.eoa, market = %pos.market, size = pos.size, price = bid, response = %resp, "rebalance sell resting (unfilled)");
                }
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
                        stats.fees += crate::fees::fee_for_fill(&pos.market, pos.size, bid);
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
            self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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
                // THE case this whole branch exists for: a protective exit
                // that was accepted and never crossed. Booking it as a fill
                // dropped the position from the ledger, which silently
                // removed the only stop protecting it. Keep the position, book
                // nothing, and let the cooldown lapse into a retry at a fresh
                // bid.
                Ok(resp) if order_is_resting(&resp) => {
                    {
                        let mut s = state.write();
                        s.total_orders_placed += 1;
                        s.exited_recently.insert(
                            pos.token_id.clone(),
                            chrono::Utc::now().timestamp_millis(),
                        );
                        push_log(&mut s.log, mk_log(
                            "RESTING",
                            &pos.token_id,
                            format!(
                                "{} SELL {:.0} @ {:.0}¢ accepted but UNFILLED — resting on the book{} · position and stop kept, will re-price · {}",
                                if tp_hit { "TAKE_PROFIT" } else { "STOP_LOSS" },
                                pos.size, bid * 100.0,
                                order_id_of(&resp).map(|id| format!(" (order {})", id)).unwrap_or_default(),
                                pos.market
                            ),
                            None,
                        ));
                    }
                    tracing::warn!(eoa = %cfg.eoa, market = %pos.market, size = pos.size, bid, take_profit = tp_hit, response = %resp, "protective exit resting (unfilled)");
                }
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
                        stats.fees += crate::fees::fee_for_fill(&pos.market, pos.size, bid);
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
            self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
            tokio::time::sleep(Duration::from_millis(cfg.order_delay_ms)).await;
        }
    }

    fn log_and_persist(&self, cfg: &EngineConfig, state: &Arc<RwLock<EngineState>>, entry: LogEntry) {
        {
            let mut s = state.write();
            push_log(&mut s.log, entry);
        }
        self.persist_state(&cfg.eoa, &cfg.strategy_id, &state.read());
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
/// THE copy ratio — multiply a leader's trade notional by this to get the
/// notional we should trade. Mirror of `copyRatio` in app/lib/strats/strat.ts
/// and the backtest's `traderCopyRatio`; pinned cross-language by
/// parity.fixture.json `copyRatio`.
///
/// The proportional model: **the leader risked `n / bankroll` of their net
/// worth, so we risk the same fraction of ours**, scaled by the trader's
/// share of the watchlist. That makes a $10,000 leader entry copy 100× larger
/// than a $100 one — which is the whole point of copying, and precisely what
/// the old volume model destroyed: `capital / leaderVolume` produced a ratio
/// so small that virtually every mirror landed under the order floor and got
/// clamped to the same flat $5, so a conviction bet and a throwaway punt were
/// copied identically.
///
/// The volume model survives as the FALLBACK for when the leader's balance
/// sheet can't be read (data-api outage, brand-new wallet): a bad ratio is
/// better than no copying, and the `SUB_SCALE` gate in `plan_mirror` still
/// stops it from placing wildly out-of-scale orders.
///
/// `Sizing::Flow` swaps the denominator from the leader's NET WORTH to the
/// capital they actually deployed in the window, so our allocation is split
/// across their flow in proportion to what each trade was worth to THEM. The
/// bankroll model is the honest risk mirror, but it is unusable on a small
/// account: a $223 strat copying a $200k leader gets a ratio near 1e-4, every
/// proportional mirror lands cents under the $3.45 CLOB floor, and `SUB_SCALE`
/// skips the very trades the FILTER just selected. Dividing by the few thousand
/// dollars the leader moved this window keeps the relative ordering (a $10k
/// conviction entry still copies 100× a $100 punt) at a size that clears the
/// floor. `turnover` is how many times over the window we'll redeploy.
fn copy_ratio_for(
    account_value: f64,
    weight_fraction: f64,
    leader_bankroll: Option<f64>,
    capital_alloc: f64,
    trader_vol: f64,
    sizing: Sizing,
    turnover: f64,
) -> f64 {
    if sizing == Sizing::Flow {
        return (capital_alloc * turnover.max(0.0)) / trader_vol.max(1.0);
    }
    // Bankrolls under $1 are noise (a wallet mid-withdrawal, a parse of an
    // empty book) and would blow the ratio up by orders of magnitude.
    match leader_bankroll {
        Some(b) if b >= 1.0 && account_value > 0.0 => (account_value * weight_fraction) / b,
        _ => capital_alloc / trader_vol.max(1.0),
    }
}

fn plan_mirror(
    trade: &ObservedTrade,
    copy_ratio: f64,
    user_floor: f64,
    min_shares: f64,
    ceiling: f64,
    slippage_bps: u32,
    max_upscale: Option<f64>,
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
        // ── Proportional fidelity ──
        // Clamping up to the floor is a lie about size: the strat wanted
        // `raw` and would place `min_notional`. A small lie (< max_upscale×)
        // is the cost of a discrete order floor and is accepted; a big one
        // means the account is simply too small to copy this leader in
        // proportion, and placing anyway is how you end up with a book full
        // of identical floor-sized bets. Say so instead, with the account
        // value that WOULD make this trade copyable.
        if let Some(mx) = max_upscale.filter(|m| *m > 0.0) {
            if min_notional > raw * mx {
                return MirrorPlan::Skip(format!(
                    "SUB_SCALE · proportional ${:.2} vs ${:.2} min order — needs ~{:.0}× the account value to copy this leader in proportion",
                    raw,
                    min_notional,
                    min_notional / raw.max(1e-9),
                ));
            }
        }
        // Small enough distortion to swallow — clamp up so it fills.
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
/// Browser parity: MAX_MOMENTUM_QUERIES in strats/strat.ts. How many of the
/// momentum query's OR-groups are actually searched — each is one gamma
/// request per feed refresh, so this is what stops a 30-coin query from
/// turning one cycle into 30 round-trips against an API that 429s.
const MAX_MOMENTUM_QUERIES: usize = 6;

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
    // One search PER OR-GROUP of the query, merged. Gamma ranks a multi-word
    // query by whole-phrase relevance, so a single search for five coins lands
    // on the event family that NAMES five coins ("top performing crypto this
    // week") instead of the coins' own markets — measured, 50 markets with
    // $315k of top-20 volume against 250 markets and $53M for the same query
    // fanned out, the two sets sharing not one market. That fan-out is what
    // lets one momentum strat trade a whole asset class. Mirror of
    // copyEngine.ts `assembleMarketPrices` and momentumTape.ts `queryTape`.
    let groups: Vec<&str> = {
        let g: Vec<&str> = query
            .split([',', '|'])
            .map(|s| s.trim())
            .filter(|s| !s.is_empty())
            .take(MAX_MOMENTUM_QUERIES)
            .collect();
        if g.is_empty() { vec![query] } else { g }
    };
    let mut markets: Vec<Value> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    for (gi, group) in groups.iter().enumerate() {
        if gi > 0 {
            tokio::time::sleep(Duration::from_millis(inter_request_delay_ms)).await;
        }
        // A failing group is skipped, not fatal — momentum works off whatever
        // slice of the feed resolved, and losing Solana must not cost Bitcoin.
        // Only if EVERY group fails does the caller hear about it.
        let fetched = async {
            let resp = http
                .get(format!("{}/public-search", GAMMA_API))
                .query(&[("q", *group), ("_limit", "60")])
                .send()
                .await
                .context("public-search GET")?;
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            if !status.is_success() {
                return Err(anyhow::anyhow!("public-search HTTP {}: {}", status, text));
            }
            serde_json::from_str::<Value>(&text).context("public-search parse")
        }
        .await;
        let raw = match fetched {
            Ok(raw) => raw,
            Err(e) => {
                errors.push(format!("\"{}\": {}", group, e));
                continue;
            }
        };
        // Search returns {events: [{..., markets: [...]}]} — flatten to markets;
        // an event without embedded markets may itself be market-shaped.
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
    }
    if markets.is_empty() && errors.len() == groups.len() {
        return Err(anyhow::anyhow!("public-search failed for every query group — {}", errors.join("; ")));
    }

    struct Candidate {
        condition_id: String,
        question: String,
        outcomes: Vec<String>,
        token_ids: Vec<String>,
        volume: f64,
        end_date_ms: Option<i64>,
    }
    // Groups overlap — "bitcoin" and "btc" return the same markets — so the
    // merged pool is deduped by condition id before anything is ranked.
    let mut seen_cids: HashSet<String> = HashSet::new();
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
            if !seen_cids.insert(condition_id.to_lowercase()) {
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
/// about to resolve, and (with `confirm_minutes` set) whose rise is still
/// intact over that shorter recent window; EXITS sell a held outcome once its
/// own price fell ≥ exitDropCents over the window. Binary markets: outcomes[1] moves by the
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
    // Recent-window confirmation, ENTRIES only — see `MomentumParams::confirm_minutes`.
    let confirm_ms = mo.confirm_minutes.unwrap_or(0) as i64 * 60_000;
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
        // The same series over the short confirmation window. None = no point
        // at or before that window's start (market younger than the window, or
        // a tape too coarse to resolve it) — unconfirmable, which blocks the
        // entry rather than waving it through.
        let confirm = if confirm_ms > 0 {
            series_momentum(&s.points, now, confirm_ms)
        } else {
            None
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

            // ENTRY candidate — rising, still rising, in band, market not
            // held, not near resolution.
            if held_in_market || rise < min_rise {
                continue;
            }
            if confirm_ms > 0 {
                // outcomes[1] moves by the complement here too. `< 0`, not
                // `<= 0`: a flat recent window is a move that paused, and
                // pausing on the way to resolution is the normal shape — only
                // giving ground back disqualifies it.
                let recent = confirm.map(|(_, _, r)| if idx == 0 { r } else { -r });
                match recent {
                    Some(r) if r >= 0.0 => {}
                    _ => continue,
                }
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
    /// Ms-epoch of the trader's most recent in-window trade — freshness for
    /// the `max_stale_hours` gate. 0 = nothing seen in the window, which the
    /// gate reads as infinitely stale. Not produced by `stats_from_returns`
    /// (which only sees a returns series); `compute_trader_roi_stats` stamps
    /// it. Mirrors `lastTradeAt` on the TS TraderRoiStats.
    pub last_trade_at: i64,
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
    // `last_trade_at` isn't derivable from a returns series — the caller that
    // has the trades stamps it.
    TraderRoiStats { roi, stdev, sharpe, sample_size: n, wins, success_prob, last_trade_at: 0 }
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
    // Freshest trade of ANY side inside the window — a trader still buying is
    // active even with no closed trade to show for it.
    let mut last_trade_at: i64 = 0;

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
        if ts_ms >= cutoff_ms && ts_ms > last_trade_at {
            last_trade_at = ts_ms;
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

    TraderRoiStats { last_trade_at, ..stats_from_returns(&returns) }
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
    // `usdcSize` is the USDC that actually moved; `price * size` is the gross
    // before Polymarket's sell-side fee (`k · p · (1-p) · shares`, k observed
    // at 4–7%). Measured on a live roster: ~46% of SELL rows carry one, worth
    // ~0.5% of sell notional — booked as gross it shows up as PnL that never
    // hit the wallet. pipeline.rs already prefers `usdcSize`; this is parity.
    let usdc_size = v
        .get("usdcSize")
        .and_then(|u| u.as_f64().or_else(|| u.as_str().and_then(|s| s.parse().ok())))
        .filter(|u: &f64| u.is_finite() && *u > 0.0)
        .unwrap_or(price * size);
    Some(ObservedTrade {
        id,
        timestamp: timestamp_ms,
        trader: trader.to_string(),
        market: v.get("title").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        condition_id: v.get("conditionId").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        side,
        size,
        price,
        notional: usdc_size,
        token_id: v.get("asset").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        outcome: v.get("outcome").and_then(|s| s.as_str()).unwrap_or("").to_string(),
        score: 0.0,                          // stamped later once the trader's
        success_prob: default_success_prob(), // ROI/win-rate stats are known
    })
}

/// Collapse the fills of one leader action into one trade.
///
/// A data-api `/activity` row is a FILL, not an order. A leader who walks the
/// book gets one row per price level — one observed transaction carried nine
/// BUYs of the same token at 80.5¢→81.3¢ in a live sample. Every row shares
/// the transaction hash, and `id` is the transaction hash, so the copy loop's
/// `copied_ids` check (`if copied_ids.contains(&trade.id) { continue }`)
/// mirrored the FIRST fill and dropped the other eight: the leader bought 306
/// shares, the engine copied 22. Measured against upstream, 6.5% of a busy
/// leader's fills were being thrown away this way, and the loss is biased
/// toward exactly the aggressive order-walking traders a strat wants to copy.
///
/// Aggregating rather than making each fill its own candidate is deliberate:
/// nine mirror orders for one leader action would each be sized at a ninth,
/// and most would land under `min_order_size` and be rejected outright. One
/// order at the fill-weighted average price is what the leader actually did.
///
/// Grouped by `(tx hash, token, side)` — a transaction that touches two
/// tokens is two actions. The FIRST group keeps the bare hash as its id so
/// ids already persisted in `copied_ids` still match and an engine restart
/// after this change can't re-copy what it already mirrored; extra groups get
/// a `#n` suffix in first-seen order.
fn aggregate_fills(fills: Vec<ObservedTrade>) -> Vec<ObservedTrade> {
    // (hash, token, side) → index into `out`, plus per-hash group counter.
    let mut slot: std::collections::HashMap<(String, String, String), usize> =
        std::collections::HashMap::new();
    let mut groups_per_hash: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    // Fill-weighted price accumulator, parallel to `out`.
    let mut px_notional: Vec<f64> = Vec::new();
    let mut out: Vec<ObservedTrade> = Vec::new();

    for f in fills {
        let key = (f.id.clone(), f.token_id.clone(), f.side.clone());
        match slot.get(&key) {
            Some(&i) => {
                px_notional[i] += f.price * f.size;
                out[i].size += f.size;
                out[i].notional += f.notional;
                // Same transaction, so these are equal in practice; taking the
                // newest keeps the cursor honest if upstream ever staggers them.
                out[i].timestamp = out[i].timestamp.max(f.timestamp);
            }
            None => {
                let n = groups_per_hash.entry(f.id.clone()).or_insert(0);
                let suffix = *n;
                *n += 1;
                slot.insert(key, out.len());
                px_notional.push(f.price * f.size);
                let mut t = f;
                if suffix > 0 {
                    t.id = format!("{}#{}", t.id, suffix);
                }
                out.push(t);
            }
        }
    }

    for (i, t) in out.iter_mut().enumerate() {
        if t.size > 0.0 {
            // Fill-weighted average of the PRICES paid, not `notional / size`:
            // notional is fee-adjusted, and `price` still has to mean "the
            // level the leader traded at" for the gates and the mirror order.
            t.price = px_notional[i] / t.size;
        }
    }
    out
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

    /// A book-walking BUY arrives as N rows sharing one transaction hash. The
    /// copy loop dedupes on `id`, so before aggregation it mirrored the first
    /// row and dropped the rest — the leader's 306 shares became 22.
    #[test]
    fn fills_of_one_transaction_collapse_into_one_action() {
        let rows: Vec<ObservedTrade> = [(0.805, 100.0), (0.81, 100.0), (0.815, 106.0)]
            .iter()
            .map(|(p, s)| {
                parse_activity_trade(
                    &json!({
                        "type": "TRADE", "price": p, "size": s,
                        "timestamp": 1_700_000_000i64,
                        "transactionHash": "0xdead", "asset": "tok1",
                        "conditionId": "0xcond", "side": "BUY", "outcome": "Yes",
                        "title": "Texas governor",
                    }),
                    "0xtrader",
                )
                .unwrap()
            })
            .collect();

        let agg = aggregate_fills(rows);
        assert_eq!(agg.len(), 1, "one transaction, one token, one side = one action");
        assert_eq!(agg[0].size, 306.0, "every fill's shares are kept");
        // 0.805·100 + 0.81·100 + 0.815·106 = 247.89, over 306 shares.
        assert!((agg[0].notional - 247.89).abs() < 1e-6, "notional is the sum, got {}", agg[0].notional);
        assert!((agg[0].price - 247.89 / 306.0).abs() < 1e-9, "price is the VWAP, got {}", agg[0].price);
        // The bare hash survives so ids already in `copied_ids` still match.
        assert_eq!(agg[0].id, "0xdead");
    }

    /// One transaction can touch two tokens — that's two leader actions, and
    /// collapsing them would lose one entirely.
    #[test]
    fn one_transaction_across_two_tokens_stays_two_actions() {
        let mk = |asset: &str, side: &str| {
            parse_activity_trade(
                &json!({
                    "type": "TRADE", "price": 0.5, "size": 10.0,
                    "timestamp": 1_700_000_000i64,
                    "transactionHash": "0xbeef", "asset": asset,
                    "conditionId": "0xcond", "side": side, "outcome": "Yes",
                    "title": "m",
                }),
                "0xtrader",
            )
            .unwrap()
        };
        let agg = aggregate_fills(vec![mk("tokA", "BUY"), mk("tokB", "BUY"), mk("tokA", "BUY")]);
        assert_eq!(agg.len(), 2);
        assert_eq!(agg[0].size, 20.0);
        assert_eq!(agg[1].size, 10.0);
        // First group keeps the legacy id; the second is disambiguated.
        assert_eq!(agg[0].id, "0xbeef");
        assert_eq!(agg[1].id, "0xbeef#1");
    }

    /// `usdcSize` is the money that moved. On a SELL it is below `price*size`
    /// by Polymarket's fee, and booking the gross is PnL that never landed.
    #[test]
    fn sell_notional_comes_from_usdc_size_not_gross() {
        let t = parse_activity_trade(
            &json!({
                "type": "TRADE", "price": 0.81, "size": 20.0, "usdcSize": 16.07688,
                "timestamp": 1_700_000_000i64, "transactionHash": "0x1",
                "conditionId": "0xc", "side": "SELL", "title": "m",
            }),
            "0xtrader",
        )
        .unwrap();
        assert!((t.notional - 16.07688).abs() < 1e-6, "got {}", t.notional);
        assert_eq!(t.price, 0.81, "the fill price itself is untouched");

        // No usdcSize upstream (older rows, and every BUY) → gross.
        let b = parse_activity_trade(
            &json!({
                "type": "TRADE", "price": 0.5, "size": 10.0,
                "timestamp": 1_700_000_000i64, "transactionHash": "0x2",
                "conditionId": "0xc", "side": "BUY", "title": "m",
            }),
            "0xtrader",
        )
        .unwrap();
        assert_eq!(b.notional, 5.0);
    }

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

    fn pos_for_settlement(size: f64, entry: f64) -> OpenPosition {
        OpenPosition {
            token_id: "tok".into(),
            condition_id: "0xcond".into(),
            market: "Bitcoin Up or Down - 5m".into(),
            size,
            entry_price: entry,
            entry_score: 0.0,
            opened_at: 0,
            strategy_id: "s1".into(),
            leader: String::new(),
        }
    }

    /// The regression that emptied a live account while the console showed a
    /// PROFIT: a losing 5-minute candle burns to zero, produces no SELL and
    /// no redeem, and used to be dropped from the ledger unbooked.
    #[test]
    fn worthless_expiry_books_the_full_loss() {
        let p = pos_for_settlement(40.0, 0.55);
        let (proceeds, basis) = settlement_booking(&p, false, Some(0.0)).expect("must book");
        assert_eq!(proceeds, 0.0);
        assert!((basis - 22.0).abs() < 1e-9);
        assert!((proceeds - basis + 22.0).abs() < 1e-9, "loss must be the whole basis");
    }

    /// The other half of the same path — a winner that resolved and was
    /// redeemed pays out at 1.0/share, so it must NOT be slandered as a loss.
    #[test]
    fn resolved_winner_books_a_gain_not_a_loss() {
        let p = pos_for_settlement(40.0, 0.55);
        let (proceeds, basis) = settlement_booking(&p, false, Some(1.0)).expect("must book");
        assert!((proceeds - 40.0).abs() < 1e-9);
        assert!((proceeds - basis - 18.0).abs() < 1e-9);
    }

    /// A token the session SOLD is already booked by the sell path; the
    /// data-api simply lists it until the fill indexes. Booking here again
    /// would double-count it.
    #[test]
    fn sold_position_is_not_rebooked() {
        let p = pos_for_settlement(40.0, 0.55);
        assert!(settlement_booking(&p, true, Some(0.0)).is_none());
    }

    /// Resolution not indexed yet ⇒ book nothing and retry next cycle. The
    /// engine must never invent a total loss from a missing lookup.
    #[test]
    fn unindexed_settlement_books_nothing() {
        let p = pos_for_settlement(40.0, 0.55);
        assert!(settlement_booking(&p, false, None).is_none());
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
    fn roi_stats_stamp_last_trade_of_any_side() {
        let now = 2_000_000_000_000i64;
        let d = 86_400_000i64;
        // The freshest activity is a BUY with nothing sold against it yet —
        // the trader is plainly active, so freshness must NOT be read off the
        // closed-trade series (which stops 4 days ago).
        let items = vec![
            trade_item("m1", "BUY", 0.50, 10.0, now - 5 * d),
            trade_item("m1", "SELL", 0.60, 10.0, now - 4 * d),
            trade_item("m2", "BUY", 0.30, 10.0, now - 2 * 3_600_000),
        ];
        let s = compute_trader_roi_stats(&items, now, None);
        assert_eq!(s.last_trade_at, now - 2 * 3_600_000);
        assert!(trader_age_ms(&s, now) < 3.0 * 3_600_000.0);

        // Everything outside the window ⇒ 0, which the gate reads as
        // infinitely stale rather than "unknown, let them through".
        let old = vec![trade_item("m1", "BUY", 0.50, 10.0, now - 41 * d)];
        let s_old = compute_trader_roi_stats(&old, now, None);
        assert_eq!(s_old.last_trade_at, 0);
        assert!(trader_age_ms(&s_old, now).is_infinite());
    }

    #[test]
    fn stale_traders_never_hold_a_top_n_slot() {
        let now = 2_000_000_000_000i64;
        let h = 3_600_000i64;
        // The best scorer went quiet 48h ago; two mediocre names are active.
        let stats = |roi: f64, last: i64| TraderRoiStats {
            roi,
            success_prob: 0.6,
            last_trade_at: last,
            ..neutral_stats()
        };
        let traders = vec![
            ("0xaaa".to_string(), stats(0.50, now - 48 * h)),
            ("0xbbb".to_string(), stats(0.20, now - 1 * h)),
            ("0xccc".to_string(), stats(0.10, now - 2 * h)),
        ];
        // No gate: the dormant trader wins the only slot.
        let f = TraderFilter { top_n: Some(1), ..Default::default() };
        let ranked = select_top_traders(&traders, &f, now);
        assert_eq!(ranked[0].address, "0xaaa");
        assert!(ranked[0].kept);

        // With a 6h gate: they sort last, and the slot goes to a live trader.
        let f = TraderFilter { top_n: Some(1), max_stale_hours: Some(6.0), ..Default::default() };
        let ranked = select_top_traders(&traders, &f, now);
        let kept: Vec<&str> =
            ranked.iter().filter(|r| r.kept).map(|r| r.address.as_str()).collect();
        assert_eq!(kept, vec!["0xbbb"]);
        let dormant = ranked.iter().find(|r| r.address == "0xaaa").unwrap();
        assert!(dormant.stale && !dormant.kept);
        assert_eq!(dormant.rank, 3, "stale rows rank below every fresh one");
        assert!(dormant.reason.contains("2d ago > 6h max"), "reason was {}", dormant.reason);
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

        // Copy ratio — the account-value proportional model and its
        // volume-based fallback. This is the number that decides how big a
        // mirror is, so the two languages agreeing on it IS the guarantee
        // that a backtest predicts live position sizes.
        for case in fx["copyRatioCases"].as_array().expect("copyRatioCases") {
            let name = case["name"].as_str().unwrap_or("?");
            let sizing = match case["sizing"].as_str() {
                Some("flow") => Sizing::Flow,
                _ => Sizing::Bankroll,
            };
            let got = copy_ratio_for(
                case["accountValue"].as_f64().unwrap(),
                case["weightFraction"].as_f64().unwrap(),
                case["leaderBankroll"].as_f64(),
                case["capitalAlloc"].as_f64().unwrap(),
                case["traderVol"].as_f64().unwrap(),
                sizing,
                case["turnover"].as_f64().unwrap_or_else(default_turnover),
            );
            let want = case["expected"].as_f64().unwrap();
            assert!(close(got, want), "copyRatio[{name}]: got {got}, want {want}");
        }

        // Trader FILTER — WHO gets copied. The console previews this ranking
        // while the engine enforces it, so a drift makes the preview a lie.
        {
            let now_ms = fx["traderFilterNowMs"].as_i64().expect("traderFilterNowMs");
            let traders: Vec<(String, TraderRoiStats)> = fx["traderFilterTraders"]
                .as_array()
                .expect("traderFilterTraders")
                .iter()
                .map(|t| {
                    // null = never traded ⇒ last_trade_at 0, which the
                    // freshness gate reads as infinitely stale.
                    let last_trade_at = t["lastTradeMinutesAgo"]
                        .as_i64()
                        .map_or(0, |m| now_ms - m * 60_000);
                    (
                        t["address"].as_str().unwrap().to_string(),
                        TraderRoiStats {
                            last_trade_at,
                            ..stats_from_returns(&floats(&t["returns"]))
                        },
                    )
                })
                .collect();
            for case in fx["traderFilterCases"].as_array().expect("traderFilterCases") {
                let name = case["name"].as_str().unwrap_or("?");
                let filter: TraderFilter =
                    serde_json::from_value(case["filter"].clone()).expect("filter parses");
                let ranked = select_top_traders(&traders, &filter, now_ms);
                let order: Vec<String> = ranked.iter().map(|r| r.address.clone()).collect();
                let kept: Vec<String> = ranked
                    .iter()
                    .filter(|r| r.kept)
                    .map(|r| r.address.clone())
                    .collect();
                let want_order: Vec<String> = case["expectedOrder"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_string())
                    .collect();
                let want_kept: Vec<String> = case["expectedKept"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_str().unwrap().to_string())
                    .collect();
                assert_eq!(order, want_order, "traderFilter[{name}] ranking");
                assert_eq!(kept, want_kept, "traderFilter[{name}] kept set");
            }
            assert_eq!(
                DEFAULT_FILTER_TOP_N as u64,
                fx["defaults"]["filterTopN"].as_u64().unwrap(),
                "DEFAULT_FILTER_TOP_N"
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
                case["maxUpscale"].as_f64(),
            );
            let want_skip = case["expected"]["skip"].as_str();
            match plan {
                MirrorPlan::Skip(reason) => {
                    let code = if reason.starts_with("CEILING") {
                        "CEILING"
                    } else if reason.starts_with("LEADER_DUST") {
                        "DUST"
                    } else if reason.starts_with("SUB_SCALE") {
                        "SUB_SCALE"
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

        // ORIGINATION parity — `propose_momentum` against the same tape the TS
        // `Strat.propose` replays. This is the path the console's origination
        // backtest (app/lib/originationBacktest.ts) runs cycle by cycle, so
        // drift here is drift between what a card promises and what this engine
        // places. Two real divergences were found writing these: the exit was
        // sized off the position's stale reported value in TS, and the TS entry
        // floor ignored the CLOB's per-price minimum.
        for case in fx["momentumCases"].as_array().expect("momentumCases") {
            let name = case["name"].as_str().unwrap_or("?");
            let now = case["now"].as_i64().unwrap();
            let mo: MomentumParams =
                serde_json::from_value(case["momentum"].clone()).expect("momentum params");
            let series: Vec<MarketPriceSeries> = case["series"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| {
                    let outcomes = json_string_array(s.get("outcomes"));
                    let tokens = json_string_array(s.get("tokenIds"));
                    MarketPriceSeries {
                        condition_id: s["conditionId"].as_str().unwrap().to_string(),
                        market: s["market"].as_str().unwrap_or("").to_string(),
                        outcomes: [outcomes[0].clone(), outcomes[1].clone()],
                        token_ids: [tokens[0].clone(), tokens[1].clone()],
                        end_date_ms: s["endDateMs"].as_i64(),
                        points: s["points"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|p| (p["t"].as_i64().unwrap(), p["p"].as_f64().unwrap()))
                            .collect(),
                    }
                })
                .collect();
            let mut held: HashMap<String, OpenPosition> = HashMap::new();
            for p in case["positions"].as_array().unwrap() {
                let cid = p["conditionId"].as_str().unwrap();
                let idx = p["outcomeIndex"].as_u64().unwrap() as usize;
                let s = series.iter().find(|s| s.condition_id == cid).expect("series for position");
                held.insert(
                    s.token_ids[idx].clone(),
                    OpenPosition {
                        token_id: s.token_ids[idx].clone(),
                        condition_id: cid.to_string(),
                        market: s.market.clone(),
                        size: p["size"].as_f64().unwrap(),
                        entry_price: p["entryPrice"].as_f64().unwrap(),
                        entry_score: 0.0,
                        opened_at: now,
                        strategy_id: "s1".into(),
                        leader: String::new(),
                    },
                );
            }
            let got = propose_momentum(
                &mo,
                &series,
                &held,
                now,
                case["userFloor"].as_f64().unwrap(),
                case["userCeiling"].as_f64().unwrap(),
                case["minShares"].as_f64().unwrap(),
                case["capital"].as_f64().unwrap(),
            );
            let want = case["expected"].as_array().unwrap();
            assert_eq!(got.len(), want.len(), "momentum[{name}]: proposal count — got {got:?}");
            for (g, w) in got.iter().zip(want) {
                let cid = w["conditionId"].as_str().unwrap();
                let idx = w["outcomeIndex"].as_u64().unwrap() as usize;
                let s = series.iter().find(|s| s.condition_id == cid).unwrap();
                assert_eq!(g.side, w["side"].as_str().unwrap(), "momentum[{name}]: side");
                assert_eq!(g.condition_id, cid, "momentum[{name}]: market");
                assert_eq!(g.outcome, s.outcomes[idx], "momentum[{name}]: outcome");
                assert_eq!(g.token_id, s.token_ids[idx], "momentum[{name}]: token");
                let want_notional = w["notional"].as_f64().unwrap();
                let want_limit = w["limitPrice"].as_f64().unwrap();
                assert!(
                    close(g.notional, want_notional),
                    "momentum[{name}]: notional {} want {want_notional}",
                    g.notional
                );
                assert!(
                    close(g.limit_price, want_limit),
                    "momentum[{name}]: limit {} want {want_limit}",
                    g.limit_price
                );
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
        assert_eq!(
            default_max_upscale(),
            fx["defaults"]["maxUpscale"].as_f64(),
            "proportional-fidelity default drifted from the fixture"
        );
        assert_eq!(
            default_copy_min_minutes_to_close(),
            fx["defaults"]["minMinutesToClose"].as_f64(),
            "mirror time-to-close default drifted from the fixture"
        );
        assert_eq!(
            default_max_trade_age_sec(),
            fx["defaults"]["maxTradeAgeSec"].as_f64(),
            "mirror staleness default drifted from the fixture"
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
        // No price band ⇒ nothing is gated on price. There is no implicit
        // floor in either language; a strat that says nothing filters nothing.
        let fav = observed("BUY", 0.65, 100.0, "Bitcoin above $100k?");
        let long = observed("BUY", 0.50, 100.0, "Bitcoin above $100k?");
        assert!(trade_passes_filters(&fav, &None));
        assert!(trade_passes_filters(&fav, &Some(TradeFilters::default())));
        assert!(trade_passes_filters(&long, &None));
        assert!(trade_passes_filters(&long, &Some(TradeFilters::default())));
        assert!(trade_passes_filters(&observed("SELL", 0.05, 10.0, "x"), &None));
        // An explicit floor is still a floor.
        let floored = Some(TradeFilters { min_price: Some(0.6), ..Default::default() });
        assert!(trade_passes_filters(&fav, &floored));
        assert!(!trade_passes_filters(&long, &floored));

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

    /// A held position opened at `opened_at` by `leader`.
    fn held(token: &str, leader: &str, opened_at: i64) -> OpenPosition {
        OpenPosition {
            token_id: token.into(),
            condition_id: "0x1".into(),
            market: format!("market {}", token),
            size: 100.0,
            entry_price: 0.40,
            entry_score: 1.0,
            opened_at,
            strategy_id: "s1".into(),
            leader: leader.into(),
        }
    }

    /// A complete leader book read at `read_at` holding `sizes`.
    fn book(read_at: i64, sizes: &[(&str, f64)]) -> (i64, LeaderBook) {
        (read_at, LeaderBook {
            bankroll: 1000.0,
            sizes: sizes.iter().map(|(t, s)| (t.to_string(), *s)).collect(),
            complete: true,
        })
    }

    #[test]
    fn leader_flat_sweep_exits_what_the_feed_missed() {
        let now = 10_000_000i64;
        let opened = now - 3_600_000; // an hour ago
        let mut positions = HashMap::new();
        positions.insert("gone".to_string(), held("gone", "0xlead", opened));
        positions.insert("kept".to_string(), held("kept", "0xlead", opened));
        // Book read AFTER we opened both, and it only shows one of them —
        // the other was sold while we weren't looking.
        let mut books = HashMap::new();
        books.insert("0xlead".to_string(), book(now - 1000, &[("kept", 250.0)]));

        let out = leader_flat_exits(&positions, &HashMap::new(), &books, &HashSet::new(), now);
        assert_eq!(out.len(), 1, "only the token the leader dropped is swept");
        let c = &out[0];
        assert_eq!(c.trade.token_id, "gone");
        assert_eq!(c.trade.side, "SELL");
        assert_eq!(c.trade.trader, "0xlead");
        assert!(c.swept);
        // `Some(0.0)` remaining is what makes the sell path exit in full.
        assert_eq!(c.leader_remaining, Some(0.0));
    }

    #[test]
    fn leader_flat_sweep_never_sells_on_a_book_that_cant_prove_it() {
        let now = 10_000_000i64;
        let opened = now - 3_600_000;
        let mut positions = HashMap::new();
        positions.insert("gone".to_string(), held("gone", "0xlead", opened));
        let none: HashMap<String, i64> = HashMap::new();

        // 1. Snapshot predates our entry — it never saw the token, which is
        //    not the same as the leader dropping it. (This is the guard that
        //    stops a cached book selling a position the moment we open it.)
        let mut stale = HashMap::new();
        stale.insert("0xlead".to_string(), book(opened - 1, &[]));
        assert!(leader_flat_exits(&positions, &none, &stale, &HashSet::new(), now).is_empty());

        // 2. Truncated book — an absent row may just be off the end of the page.
        let mut capped = HashMap::new();
        let (at, mut b) = book(now - 1000, &[]);
        b.complete = false;
        capped.insert("0xlead".to_string(), (at, b));
        assert!(leader_flat_exits(&positions, &none, &capped, &HashSet::new(), now).is_empty());

        // 3. Expired snapshot can't speak for the present — they may have
        //    re-entered since it was taken.
        let mut expired = HashMap::new();
        expired.insert("0xlead".to_string(), book(now - leader_book_ttl("0xlead") - 1, &[]));
        assert!(leader_flat_exits(&positions, &none, &expired, &HashSet::new(), now).is_empty());

        // 4. No book at all for that leader (unreachable this cycle).
        assert!(leader_flat_exits(&positions, &none, &HashMap::new(), &HashSet::new(), now).is_empty());

        // 5. No leader recorded (momentum entry / adopted from chain) — there
        //    is nobody to follow out.
        let mut orphan = HashMap::new();
        orphan.insert("gone".to_string(), held("gone", "", opened));
        let mut fresh = HashMap::new();
        fresh.insert("0xlead".to_string(), book(now - 1000, &[]));
        assert!(leader_flat_exits(&orphan, &none, &fresh, &HashSet::new(), now).is_empty());
    }

    #[test]
    fn leader_flat_sweep_never_double_sells() {
        let now = 10_000_000i64;
        let opened = now - 3_600_000;
        let mut positions = HashMap::new();
        positions.insert("gone".to_string(), held("gone", "0xlead", opened));
        let mut books = HashMap::new();
        books.insert("0xlead".to_string(), book(now - 1000, &[]));

        // Already queued from an observed SELL this cycle.
        let queued: HashSet<String> = ["gone".to_string()].into_iter().collect();
        assert!(leader_flat_exits(&positions, &HashMap::new(), &books, &queued, now).is_empty());

        // Sold moments ago — the fill hasn't settled out of the feed yet.
        let mut cooling = HashMap::new();
        cooling.insert("gone".to_string(), now - 1000);
        assert!(leader_flat_exits(&positions, &cooling, &books, &HashSet::new(), now).is_empty());

        // Once the cooldown lapses it's fair game again (an exit that never
        // filled must not be stranded forever).
        let mut cold = HashMap::new();
        cold.insert("gone".to_string(), now - EXIT_READOPT_COOLDOWN_MS - 1);
        assert_eq!(leader_flat_exits(&positions, &cold, &books, &HashSet::new(), now).len(), 1);
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
            exit_drop_cents: None, confirm_minutes: None, min_price: None, max_price: None,
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
            exit_drop_cents: None, confirm_minutes: None, min_price: None, max_price: None,
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
            strategy_id: "s1".into(), leader: String::new(),
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
            strategy_id: "s1".into(), leader: String::new(),
        });
        let mo = MomentumParams {
            query: None, lookback_minutes: None, min_rise_cents: None,
            exit_drop_cents: None, confirm_minutes: None, min_price: None, max_price: None,
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
    fn session_keys_separate_concurrently_funded_strats() {
        // One wallet, two funded strats ⇒ two distinct registry keys and two
        // distinct on-disk configs. Before multi-session both collapsed to the
        // EOA, so starting the second strat killed the first.
        let eoa = "0x89BC";
        assert_ne!(session_key(eoa, "s1"), session_key(eoa, "s2"));
        assert_ne!(
            session_file_stem(eoa, "s1"),
            session_file_stem(eoa, "s2"),
        );
        // EOA case never forks a session.
        assert_eq!(session_key("0x89bc", "s1"), session_key("0x89BC", "s1"));
        assert_eq!(session_file_stem("0x89bc", "s1"), session_file_stem("0x89BC", "s1"));
        // Path separators in a shared strat id can't escape the data dir.
        let stem = session_file_stem(eoa, "../../etc/passwd");
        // No separator survives, so the id stays one path component — the
        // remaining dots can't traverse out of the data dir.
        assert!(!stem.contains('/'), "unsanitized strat id in filename: {stem}");
        assert_eq!(stem, "0x89bc__.._.._etc_passwd");
    }

    #[test]
    fn a_siblings_position_is_not_adopted_by_the_other_strat() {
        // The bug this pins: one EOA derives ONE deposit wallet, so both of a
        // wallet's sessions read the same holdings. The BTC copy strat bought
        // a tennis market; the general strat's reconcile saw a token its own
        // ledger didn't know and adopted it — double-banking the exposure in
        // two accountValues and handing two strats' exit logic one position.
        let reg = EngineRegistry::new(reqwest::Client::new(), Arc::new(SignerStore::new()));
        let eoa = "0x89bc";

        let mut owner_state = EngineState::empty();
        owner_state.positions.insert("tokTennis".into(), OpenPosition {
            token_id: "tokTennis".into(),
            condition_id: "0xcid".into(),
            market: "Augsburg: Cezar Cretu vs Niels McDonald".into(),
            size: 25.0,
            entry_price: 0.48,
            entry_score: 0.034,
            opened_at: 0,
            strategy_id: "btc".into(),
            leader: "0xf148".into(),
        });
        for (sid, st) in [("btc", owner_state), ("general", EngineState::empty())] {
            let cfg: EngineConfig = serde_json::from_value(json!({
                "eoa": eoa, "strategyId": sid, "address": eoa,
                "traders": [], "capital": 100.0, "intervalMs": 30000
            })).unwrap();
            reg.engines.insert(session_key(eoa, sid), Arc::new(EngineHandle {
                config: RwLock::new(cfg),
                state: Arc::new(RwLock::new(st)),
                cancel: Arc::new(AtomicBool::new(false)),
                task: parking_lot::Mutex::new(None),
            }));
        }

        // The general strat sees the token as spoken for and leaves it alone.
        assert!(reg.sibling_claimed_tokens(eoa, "general").contains("tokTennis"));
        // The strat that BOUGHT it doesn't count its own hold as a sibling's —
        // otherwise reconcile would drop it from the only ledger tracking it.
        assert!(!reg.sibling_claimed_tokens(eoa, "btc").contains("tokTennis"));
        // A different wallet shares nothing: separate deposit wallet, separate
        // holdings, and its unowned tokens must still be adoptable.
        assert!(reg.sibling_claimed_tokens("0xd779", "general").is_empty());
    }

    #[test]
    fn cadence_honors_the_floor_and_widens_for_big_watchlists() {
        let base = json!({
            "eoa": "0xe", "strategyId": "s", "address": "0xa",
            "traders": [], "capital": 100.0, "intervalMs": 30000
        });
        let cfg: EngineConfig = serde_json::from_value(base).unwrap();
        // A small watchlist syncs at exactly the requested 30s.
        assert_eq!(effective_interval_for(&cfg, 1), 30_000);
        assert_eq!(effective_interval_for(&cfg, 30), 30_000);
        // 400ms spacing + 600ms fetch allowance ⇒ 31 traders need >30s of
        // fan-out, so the period widens rather than silently drifting.
        assert_eq!(effective_interval_for(&cfg, 31), 31_000);
        // A stale sub-floor config self-heals up to the 30s floor.
        let stale = json!({
            "eoa": "0xe", "strategyId": "s", "address": "0xa",
            "traders": [], "capital": 100.0, "intervalMs": 5000
        });
        let cfg: EngineConfig = serde_json::from_value(stale).unwrap();
        assert_eq!(effective_interval_for(&cfg, 1), 30_000);
        // An explicitly slower cadence is honored as-is.
        let slow = json!({
            "eoa": "0xe", "strategyId": "s", "address": "0xa",
            "traders": [], "capital": 100.0, "intervalMs": 300000
        });
        let cfg: EngineConfig = serde_json::from_value(slow).unwrap();
        assert_eq!(effective_interval_for(&cfg, 4), 300_000);
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

    #[test]
    fn market_lookups_reject_gammas_default_page() {
        // Gamma answers an unrecognised filter (the singular `condition_id`)
        // with its default page of unrelated markets rather than an error —
        // so every by-id lookup must confirm the id it got back. Without this
        // the engine read some other market's negRisk on every resolve, and
        // signed orders with the wrong flag.
        let want = "0xa467b14d51f01b957109d9cbb1d6c124fab2a089d52ed8f471d23c2812e743b7";
        let other = json!({
            "conditionId": "0x6b4608b5184bfe17c6718ab07a5fb3e2d6b0903cddb88f0c4184dd54c5fca934",
            "negRisk": true,
            "endDate": "2026-08-02T08:10:00Z",
        });
        assert!(!verify_condition(&other, want), "a different market must not satisfy the lookup");

        let mine = json!({ "conditionId": want, "negRisk": true, "endDate": "2026-12-31T00:00:00Z" });
        assert!(verify_condition(&mine, want));
        // Case-insensitive: gamma and the data-api disagree on hex casing.
        assert!(verify_condition(&mine, &want.to_uppercase().replace("0X", "0x")));

        let (neg_risk, end) = market_meta_of(&mine);
        assert!(neg_risk);
        assert_eq!(end, Some(1798675200000), "endDate must parse as RFC3339 with a Z offset");
        // A market with no end date is "unknown", never "resolves now" — the
        // time-to-close gate must not block on missing data.
        assert_eq!(market_meta_of(&json!({"conditionId": want})), (false, None));
    }
}
