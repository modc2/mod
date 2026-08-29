//! COPY BOOK — the per-trader copy-trading desk.
//!
//! The console's older unit of work is a *strat*: a watchlist of many traders
//! blended into one portfolio. This module is the other shape, and the one
//! this deployment leads with — **one leader, one allocation**:
//!
//!   > "Copy 0xab… with $250, copy 0xcd… with $100."
//!
//! A COPY BOOK is a list of `Allocation`s, each naming a trader and the
//! dollars behind them. Everything downstream — the live engine, the backtest
//! worker, the per-strat ledger — already speaks *strats*, so an allocation is
//! **materialized** into one through the IDENTITY TEMPLATE (`identity_strat`):
//! a strat whose watchlist is exactly that one trader at weight 1, with
//! `identity` set to their address. Nothing new runs; the same engine that
//! copies a 12-trader index copies a 1-trader one, and the ledger splits P&L
//! per trader for free because each allocation gets its own `strategyId`.
//!
//! ## Why the book lives here and not in the browser
//!
//! Saved strats live in localStorage and sync to the server ENCRYPTED with a
//! key the browser never uploads (`strats.rs`) — deliberately, but it means
//! nothing outside that one browser tab can read them. An agent driving this
//! deployment over MCP therefore could not see, let alone change, what it was
//! copying.
//!
//! The copy book is **server-owned and plaintext**: `<state dir>/copy/book.json`.
//! It holds no keys and no wallet state — trader addresses, dollar amounts and
//! a handful of tunables — and every route below is already behind the
//! owner-only access gate (`access.rs`) on a single-owner deployment. That is
//! what lets the COPY DESK in the browser and `pm_copy_*` over MCP be the same
//! desk rather than two that drift.
//!
//! ## `strategyId` is derived, not allocated
//!
//! `copy-<address without 0x>`. Deterministic, so the engine session key, the
//! ledger bucket, the persisted config on disk and the backtest card all line
//! up from the address alone with no lookup table to get out of sync. It also
//! makes "copy this trader" idempotent: adding a leader who is already in the
//! book updates their allocation instead of forking a second session.

use std::path::PathBuf;

use anyhow::{anyhow, Result};
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

/// Schema version of `book.json`. Bump on a breaking change; `load` refuses a
/// newer file rather than silently dropping fields it doesn't understand.
pub const BOOK_VERSION: u32 = 1;

/// Hard ceiling on book size. Each allocation is a polling engine session, and
/// the data-api rate limit — not ambition — is the binding constraint.
pub const MAX_ALLOCATIONS: usize = 50;

// ── The identity template's defaults ──
//
// One leader, copied honestly. Every number here is a deliberate answer to
// something that cost money before it was a default (see the module README):
// sub-hour candles are unwinnable by a poller, a stale backlog enters at
// prices the leader never paid, and a small account copying a whale's
// *bankroll fraction* places nothing at all.

/// Backtest/sizing lookback in days.
pub const DEFAULT_BACKTEST_DAYS: u32 = 7;
/// Live poll cadence in minutes. 30s: fast enough to catch a leader's fill
/// while the price is still near theirs, slow enough not to draw 429s.
pub const DEFAULT_POLL_MINUTES: f64 = 0.5;
/// Order floor in USDC. The CLOB's own hard floor is $1.
pub const DEFAULT_MIN_TRADE: f64 = 1.0;
/// Per-order ceiling in USDC.
pub const DEFAULT_MAX_TRADE: f64 = 100.0;
/// Mirrors placed per cycle before the rest defer.
pub const DEFAULT_MAX_PER_CYCLE: usize = 3;
/// Concurrent open positions per leader.
pub const DEFAULT_MAX_OPEN_POSITIONS: usize = 10;
/// Sell a position once the bid decays to this fraction of entry.
pub const DEFAULT_STOP_LOSS: f64 = 0.75;
/// Liquidate a position that has run to this absolute price.
pub const DEFAULT_TAKE_PROFIT: f64 = 0.99;
/// Refuse a market resolving sooner than this many minutes.
pub const DEFAULT_MIN_MINUTES_TO_CLOSE: f64 = 60.0;
/// Refuse a leader trade older than this many seconds.
pub const DEFAULT_MAX_TRADE_AGE_SEC: f64 = 300.0;
/// `flow` sizing — copy the leader's CONVICTION (our allocation spread across
/// the capital they deployed), not their bankroll fraction. A $250 book copying
/// a $2M whale places real orders under `flow` and nothing at all under
/// `bankroll`, because a whale's 0.1%-of-net-worth bet is $2000 of theirs and
/// 25¢ of ours — below every floor there is.
pub const DEFAULT_SIZING: &str = "flow";
/// How many times the allocation may be deployed across one lookback window.
pub const DEFAULT_TURNOVER: f64 = 1.0;

/// Per-allocation overrides on the identity template. Every field is optional:
/// absent means "use the template default", which is what keeps a book of ten
/// leaders readable — the ones you never tuned carry no settings at all.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct AllocationParams {
    #[serde(rename = "minTrade", skip_serializing_if = "Option::is_none", default)]
    pub min_trade: Option<f64>,
    #[serde(rename = "maxTrade", skip_serializing_if = "Option::is_none", default)]
    pub max_trade: Option<f64>,
    #[serde(rename = "maxPerCycle", skip_serializing_if = "Option::is_none", default)]
    pub max_per_cycle: Option<usize>,
    #[serde(rename = "maxOpenPositions", skip_serializing_if = "Option::is_none", default)]
    pub max_open_positions: Option<usize>,
    #[serde(rename = "pollMinutes", skip_serializing_if = "Option::is_none", default)]
    pub poll_minutes: Option<f64>,
    #[serde(rename = "backtestDays", skip_serializing_if = "Option::is_none", default)]
    pub backtest_days: Option<u32>,
    /// "flow" | "bankroll" — see `DEFAULT_SIZING`.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub sizing: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub turnover: Option<f64>,
    /// `Some(0.0)` is a real value — stop-loss OFF — and is why these are
    /// `Option<f64>` rather than defaulted floats.
    #[serde(rename = "stopLoss", skip_serializing_if = "Option::is_none", default)]
    pub stop_loss: Option<f64>,
    #[serde(rename = "takeProfit", skip_serializing_if = "Option::is_none", default)]
    pub take_profit: Option<f64>,
    #[serde(rename = "minMinutesToClose", skip_serializing_if = "Option::is_none", default)]
    pub min_minutes_to_close: Option<f64>,
    #[serde(rename = "maxTradeAgeSec", skip_serializing_if = "Option::is_none", default)]
    pub max_trade_age_sec: Option<f64>,
    /// Free-text market-topic gate — copy this leader only where they trade
    /// this subject ("bitcoin", "election"). Absent ⇒ everything they trade.
    #[serde(rename = "marketQuery", skip_serializing_if = "Option::is_none", default)]
    pub market_query: Option<String>,
    /// Per-trade gate: side, the leader's fill-price band, their notional band.
    /// `market_query` says WHICH MARKETS, this says WHICH TRADES inside them —
    /// the two halves the console's sentence box compiles a query into (see
    /// app/lib/semanticFilter.ts). Absent ⇒ mirror every trade in those
    /// markets, which is what every allocation did before this existed.
    #[serde(rename = "tradeFilters", skip_serializing_if = "Option::is_none", default)]
    pub trade_filters: Option<crate::live_engine::TradeFilters>,
}

/// One line of the book: a leader and the money behind them.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Allocation {
    /// Leader's wallet, lowercased. The book's primary key.
    pub address: String,
    /// Display name. Absent ⇒ the UI shows a short address.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub label: Option<String>,
    /// Dollars allocated to copying this leader. This is the strat's
    /// `capital`: the budget the live engine sizes against and refuses to
    /// exceed, and the notional the backtest replays with.
    #[serde(rename = "allocationUsd")]
    pub allocation_usd: f64,
    /// Paused leaders stay in the book with their allocation and their
    /// history intact; they are simply not started and not backtested.
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default, skip_serializing_if = "AllocationParams::is_empty")]
    pub params: AllocationParams,
    /// Free-text — why you're copying them. Read by the strat-chat agent.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub notes: Option<String>,
    #[serde(rename = "addedAt", default)]
    pub added_at: i64,
    #[serde(rename = "updatedAt", default)]
    pub updated_at: i64,
}

impl AllocationParams {
    fn is_empty(&self) -> bool {
        self == &AllocationParams::default()
    }
}

fn default_true() -> bool {
    true
}

impl Allocation {
    /// The session key / ledger bucket / backtest key for this leader.
    /// Derived from the address so every one of those agrees without a table.
    pub fn strategy_id(&self) -> String {
        strategy_id_for(&self.address)
    }

    /// Display name, and the name on the backtest card.
    pub fn name(&self) -> String {
        match &self.label {
            Some(l) if !l.trim().is_empty() => l.trim().to_string(),
            _ => format!("COPY {}", short_address(&self.address)),
        }
    }
}

/// `copy-<address without 0x>` — see the module docs.
pub fn strategy_id_for(address: &str) -> String {
    format!("copy-{}", address.trim().to_lowercase().trim_start_matches("0x"))
}

/// The inverse of `strategy_id_for`, for reading a strat id off a ledger
/// bucket or an engine session back to the leader it copies.
pub fn address_from_strategy_id(strategy_id: &str) -> Option<String> {
    let rest = strategy_id.strip_prefix("copy-")?;
    (rest.len() == 40 && rest.chars().all(|c| c.is_ascii_hexdigit()))
        .then(|| format!("0x{}", rest.to_lowercase()))
}

pub fn short_address(address: &str) -> String {
    let a = address.trim();
    if a.len() < 12 {
        return a.to_string();
    }
    format!("{}…{}", &a[..6], &a[a.len() - 4..])
}

/// The whole desk.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CopyBook {
    #[serde(default = "default_version")]
    pub version: u32,
    /// Total dollars the desk is meant to deploy. Advisory: it is what
    /// `/copy/rebalance` splits and what the UI compares the sum of the
    /// allocations against. The engine budgets per allocation, not against
    /// this, so an over-allocated book is a warning, not an error.
    #[serde(default)]
    pub bankroll: f64,
    #[serde(default)]
    pub allocations: Vec<Allocation>,
    #[serde(rename = "updatedAt", default)]
    pub updated_at: i64,
}

fn default_version() -> u32 {
    BOOK_VERSION
}

impl Default for CopyBook {
    fn default() -> Self {
        Self {
            version: BOOK_VERSION,
            bankroll: 0.0,
            allocations: Vec::new(),
            updated_at: 0,
        }
    }
}

impl CopyBook {
    pub fn allocated(&self) -> f64 {
        // `+ 0.0` normalizes the negative zero f64's Sum identity produces on
        // an empty book — an empty desk reporting "-0" reads like a bug.
        self.allocations
            .iter()
            .filter(|a| a.enabled)
            .map(|a| a.allocation_usd)
            .sum::<f64>()
            + 0.0
    }

    pub fn get(&self, address: &str) -> Option<&Allocation> {
        let a = address.trim().to_lowercase();
        self.allocations.iter().find(|x| x.address == a)
    }
}

// ── The IDENTITY TEMPLATE ──

/// Materialize an allocation as a saved strat — the ONE place a copied trader
/// becomes something the rest of the system can run.
///
/// The shape is `SavedIndex` from `app/lib/types.ts`, and the `identity` field
/// is what marks the class: a strat that copies exactly one leader, whose
/// watchlist is that leader at weight 1 and is never re-seeded from a
/// leaderboard. The console badges it, the backtest worker replays it, and
/// `engine_config` below turns the same fields into a live session — so the
/// backtest card and the live session are provably the same strategy rather
/// than two hand-built configs that agree until someone edits one.
///
/// Keep in sync with `app/lib/identityStrat.ts`; `identity.fixture.json` pins
/// both against the same expected output.
pub fn identity_strat(alloc: &Allocation) -> Value {
    let p = &alloc.params;
    let mut strat = json!({
        "id": alloc.strategy_id(),
        "name": alloc.name(),
        // The class marker. Present ⇒ this strat IS a trader.
        "identity": alloc.address,
        "traders": [{"address": alloc.address, "weight": 1.0, "enabled": true}],
        "capital": alloc.allocation_usd,
        "backtestDays": p.backtest_days.unwrap_or(DEFAULT_BACKTEST_DAYS),
        // The backtest's replay granularity and the live poll cadence are the
        // same number on purpose: a backtest aggregated coarser than the
        // engine polls would promise fills at prices the engine never sees.
        "rebalanceMinutes": p.poll_minutes.unwrap_or(DEFAULT_POLL_MINUTES),
        "livePollMinutes": p.poll_minutes.unwrap_or(DEFAULT_POLL_MINUTES),
        "minTrade": p.min_trade.unwrap_or(DEFAULT_MIN_TRADE),
        "maxTrade": p.max_trade.unwrap_or(DEFAULT_MAX_TRADE),
        "maxPerCycle": p.max_per_cycle.unwrap_or(DEFAULT_MAX_PER_CYCLE),
        "maxOpenPositions": p.max_open_positions.unwrap_or(DEFAULT_MAX_OPEN_POSITIONS),
        "sizing": p.sizing.clone().unwrap_or_else(|| DEFAULT_SIZING.to_string()),
        "turnover": p.turnover.unwrap_or(DEFAULT_TURNOVER),
        "stopLoss": p.stop_loss.unwrap_or(DEFAULT_STOP_LOSS),
        "takeProfit": p.take_profit.unwrap_or(DEFAULT_TAKE_PROFIT),
        "minMinutesToClose": p.min_minutes_to_close.unwrap_or(DEFAULT_MIN_MINUTES_TO_CLOSE),
        "maxTradeAgeSec": p.max_trade_age_sec.unwrap_or(DEFAULT_MAX_TRADE_AGE_SEC),
        "marketQuery": p.market_query.clone().unwrap_or_default(),
        "fundsMode": "SIM",
        // Lineage: every book line says where it came from, so a strat that
        // shows up in the hub is identifiable as a desk allocation.
        "forkedFrom": "copy-desk-identity",
        "liveEnabled": alloc.enabled,
        "createdAt": alloc.added_at,
        "updatedAt": alloc.updated_at,
    });
    // Emitted only when the allocation actually carries one: an explicit
    // `"tradeFilters": null` on every strat would differ from the browser's
    // object (which simply omits the key) and break the fixture's exact
    // comparison — see lib/strats/identity.fixture.json.
    if let Some(f) = &p.trade_filters {
        strat["tradeFilters"] = serde_json::to_value(f).unwrap_or(Value::Null);
    }
    strat
}

/// The same allocation as a live engine session.
///
/// `auto_execute` is NOT set here — `EngineRegistry::start` is what decides,
/// and the route below defaults it to `false` (DRY RUN). A config that
/// silently arrived with `autoExecute: true` is the single most expensive
/// mistake this file could make.
pub fn engine_config(
    alloc: &Allocation,
    eoa: &str,
    proxy_address: &str,
) -> crate::live_engine::EngineConfig {
    let p = &alloc.params;
    let poll_minutes = p.poll_minutes.unwrap_or(DEFAULT_POLL_MINUTES);
    // serde_json round-trip rather than a 30-field struct literal: EngineConfig
    // has a dozen `#[serde(default)]` knobs this desk deliberately doesn't
    // expose, and defaulting them by name here would silently freeze whatever
    // they meant on the day this was written.
    let v = json!({
        "eoa": eoa.trim().to_lowercase(),
        "strategyId": alloc.strategy_id(),
        "address": proxy_address,
        "traders": [{"address": alloc.address, "weight": 1.0, "enabled": true}],
        "capital": alloc.allocation_usd,
        "intervalMs": (poll_minutes * 60_000.0).max(1000.0) as u64,
        "minOrderSize": p.min_trade.unwrap_or(DEFAULT_MIN_TRADE),
        "maxOrderSize": p.max_trade.unwrap_or(DEFAULT_MAX_TRADE),
        "backtestDays": p.backtest_days.unwrap_or(DEFAULT_BACKTEST_DAYS),
        "maxOrdersPerCycle": p.max_per_cycle.unwrap_or(DEFAULT_MAX_PER_CYCLE),
        "maxOpenPositions": p.max_open_positions.unwrap_or(DEFAULT_MAX_OPEN_POSITIONS),
        "sizing": p.sizing.clone().unwrap_or_else(|| DEFAULT_SIZING.to_string()),
        "turnover": p.turnover.unwrap_or(DEFAULT_TURNOVER),
        "stopLoss": p.stop_loss.unwrap_or(DEFAULT_STOP_LOSS),
        "takeProfit": p.take_profit.unwrap_or(DEFAULT_TAKE_PROFIT),
        "minMinutesToClose": p.min_minutes_to_close.unwrap_or(DEFAULT_MIN_MINUTES_TO_CLOSE),
        "maxTradeAgeSec": p.max_trade_age_sec.unwrap_or(DEFAULT_MAX_TRADE_AGE_SEC),
        "marketQuery": p.market_query.clone(),
        // The engine's own field name; `None` deserializes to no gate, which
        // is the same thing an allocation without filters has always meant.
        "tradeFilters": p.trade_filters.clone(),
    });
    serde_json::from_value(v).expect("identity template produces a valid EngineConfig")
}

// ── Store ──

pub struct CopyBookStore {
    path: PathBuf,
    book: RwLock<CopyBook>,
}

impl CopyBookStore {
    pub fn from_env() -> std::sync::Arc<Self> {
        let dir = crate::access::state_dir().join("copy");
        std::fs::create_dir_all(&dir).ok();
        Self::at(dir.join("book.json"))
    }

    /// Open a book at an explicit path. `from_env` is the real entry point;
    /// this exists so tests can exercise the store without touching the
    /// deployment's book.
    pub fn at(path: PathBuf) -> std::sync::Arc<Self> {
        let book = match std::fs::read_to_string(&path) {
            Ok(raw) => match serde_json::from_str::<CopyBook>(&raw) {
                Ok(b) if b.version <= BOOK_VERSION => b,
                Ok(b) => {
                    // Refuse rather than round-trip: writing a v1 file back
                    // over a v2 book would drop whatever v2 added.
                    tracing::error!(
                        version = b.version,
                        "copy book is newer than this build understands — serving empty, NOT overwriting"
                    );
                    CopyBook::default()
                }
                Err(e) => {
                    tracing::error!(error = %e, "copy book unreadable — serving empty");
                    CopyBook::default()
                }
            },
            Err(_) => CopyBook::default(),
        };
        tracing::info!(
            traders = book.allocations.len(),
            allocated = book.allocated(),
            path = %path.display(),
            "copy book loaded",
        );
        std::sync::Arc::new(Self {
            path,
            book: RwLock::new(book),
        })
    }

    pub fn read(&self) -> CopyBook {
        self.book.read().clone()
    }

    /// Mutate under the lock and persist. The closure's return value is passed
    /// through, so a caller can hand back the allocation it just wrote.
    fn write<T>(&self, f: impl FnOnce(&mut CopyBook) -> Result<T>) -> Result<T> {
        let mut guard = self.book.write();
        let mut next = guard.clone();
        let out = f(&mut next)?;
        next.version = BOOK_VERSION;
        next.updated_at = now_ms();
        // Persist BEFORE publishing in memory: a book the console can see but
        // that didn't survive a restart is worse than a write that failed loudly.
        let raw = serde_json::to_string_pretty(&next)?;
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, raw)?;
        std::fs::rename(&tmp, &self.path)?;
        *guard = next;
        Ok(out)
    }

    /// Add a leader, or update the one already there. Idempotent by address:
    /// "copy this trader" twice is one allocation, not two sessions.
    pub fn upsert(&self, req: UpsertRequest) -> Result<Allocation> {
        let address = normalize_address(&req.address)?;
        if !(req.allocation_usd.is_finite() && req.allocation_usd >= 0.0) {
            return Err(anyhow!("allocationUsd must be a non-negative number"));
        }
        if req.allocation_usd > 1_000_000.0 {
            return Err(anyhow!("allocationUsd looks like a typo (> $1,000,000)"));
        }
        if let Some(s) = &req.params.sizing {
            if s != "flow" && s != "bankroll" {
                return Err(anyhow!("sizing must be \"flow\" or \"bankroll\""));
            }
        }
        self.write(|book| {
            let now = now_ms();
            match book.allocations.iter_mut().find(|a| a.address == address) {
                Some(existing) => {
                    existing.allocation_usd = req.allocation_usd;
                    if req.label.is_some() {
                        existing.label = req.label.clone();
                    }
                    if req.notes.is_some() {
                        existing.notes = req.notes.clone();
                    }
                    if let Some(e) = req.enabled {
                        existing.enabled = e;
                    }
                    // Params are a PATCH: an omitted knob keeps its current
                    // value, so an agent nudging maxTrade can't silently reset
                    // the stop-loss someone set in the browser.
                    existing.params.merge(&req.params);
                    existing.updated_at = now;
                    Ok(existing.clone())
                }
                None => {
                    if book.allocations.len() >= MAX_ALLOCATIONS {
                        return Err(anyhow!(
                            "copy book is full ({} traders) — remove one first",
                            MAX_ALLOCATIONS
                        ));
                    }
                    let alloc = Allocation {
                        address: address.clone(),
                        label: req.label.clone(),
                        allocation_usd: req.allocation_usd,
                        enabled: req.enabled.unwrap_or(true),
                        params: req.params.clone(),
                        notes: req.notes.clone(),
                        added_at: now,
                        updated_at: now,
                    };
                    book.allocations.push(alloc.clone());
                    Ok(alloc)
                }
            }
        })
    }

    /// Drop a leader from the book. Returns whether they were in it.
    /// Stopping their live session is the caller's job — the route does it,
    /// because leaving a session running for an allocation that no longer
    /// exists is how money gets spent by a desk that shows nothing.
    pub fn remove(&self, address: &str) -> Result<bool> {
        let address = normalize_address(address)?;
        self.write(|book| {
            let before = book.allocations.len();
            book.allocations.retain(|a| a.address != address);
            Ok(book.allocations.len() != before)
        })
    }

    pub fn set_bankroll(&self, bankroll: f64) -> Result<CopyBook> {
        if !(bankroll.is_finite() && bankroll >= 0.0) {
            return Err(anyhow!("bankroll must be a non-negative number"));
        }
        self.write(|book| {
            book.bankroll = bankroll;
            Ok(book.clone())
        })
    }

    /// Split a bankroll across the enabled leaders.
    ///
    /// `Equal` gives everyone the same dollars. `Weighted` keeps the existing
    /// proportions and just rescales them to the new total — which is what you
    /// want after a deposit: conviction stays where you put it.
    pub fn rebalance(&self, bankroll: f64, mode: RebalanceMode) -> Result<CopyBook> {
        if !(bankroll.is_finite() && bankroll > 0.0) {
            return Err(anyhow!("bankroll must be a positive number"));
        }
        self.write(|book| {
            let enabled: Vec<usize> = book
                .allocations
                .iter()
                .enumerate()
                .filter(|(_, a)| a.enabled)
                .map(|(i, _)| i)
                .collect();
            if enabled.is_empty() {
                return Err(anyhow!("no enabled traders to rebalance"));
            }
            let now = now_ms();
            match mode {
                RebalanceMode::Equal => {
                    let each = round_cents(bankroll / enabled.len() as f64);
                    for i in enabled {
                        book.allocations[i].allocation_usd = each;
                        book.allocations[i].updated_at = now;
                    }
                }
                RebalanceMode::Weighted => {
                    let total: f64 = enabled
                        .iter()
                        .map(|&i| book.allocations[i].allocation_usd)
                        .sum();
                    // Nothing allocated yet ⇒ there are no proportions to
                    // preserve, so "keep the weights" degenerates to equal.
                    if total <= 0.0 {
                        let each = round_cents(bankroll / enabled.len() as f64);
                        for i in enabled {
                            book.allocations[i].allocation_usd = each;
                            book.allocations[i].updated_at = now;
                        }
                    } else {
                        for i in enabled {
                            let share = book.allocations[i].allocation_usd / total;
                            book.allocations[i].allocation_usd = round_cents(bankroll * share);
                            book.allocations[i].updated_at = now;
                        }
                    }
                }
            }
            book.bankroll = bankroll;
            Ok(book.clone())
        })
    }
}

impl AllocationParams {
    /// Field-wise patch — `None` in the incoming patch means "leave alone".
    fn merge(&mut self, patch: &AllocationParams) {
        macro_rules! take {
            ($($f:ident),* $(,)?) => { $( if patch.$f.is_some() { self.$f = patch.$f.clone(); } )* };
        }
        take!(
            min_trade,
            max_trade,
            max_per_cycle,
            max_open_positions,
            poll_minutes,
            backtest_days,
            sizing,
            turnover,
            stop_loss,
            take_profit,
            min_minutes_to_close,
            max_trade_age_sec,
            market_query,
            trade_filters,
        );
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RebalanceMode {
    Equal,
    Weighted,
}

impl RebalanceMode {
    pub fn parse(s: &str) -> Result<Self> {
        match s.trim().to_lowercase().as_str() {
            "equal" | "" => Ok(RebalanceMode::Equal),
            "weighted" | "weights" | "proportional" => Ok(RebalanceMode::Weighted),
            other => Err(anyhow!("unknown rebalance mode {:?} (equal|weighted)", other)),
        }
    }
}

/// Body of `POST /copy/allocations`.
#[derive(Debug, Clone, Deserialize)]
pub struct UpsertRequest {
    pub address: String,
    #[serde(rename = "allocationUsd")]
    pub allocation_usd: f64,
    #[serde(default)]
    pub label: Option<String>,
    #[serde(default)]
    pub notes: Option<String>,
    #[serde(default)]
    pub enabled: Option<bool>,
    #[serde(default)]
    pub params: AllocationParams,
}

pub fn normalize_address(address: &str) -> Result<String> {
    let a = address.trim().to_lowercase();
    if a.len() == 42 && a.starts_with("0x") && a[2..].chars().all(|c| c.is_ascii_hexdigit()) {
        Ok(a)
    } else {
        Err(anyhow!("not an EOA address: {:?}", address))
    }
}

fn round_cents(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

pub fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

// ── HTTP ──
//
// One surface, two clients. The COPY DESK in the browser and the `pm_copy_*`
// MCP tools an agent drives BOTH call exactly these routes — there is no
// second, browser-only path into the book. That is the point: an agent asked
// "what am I copying and how is it doing?" reads the same bytes the screen
// does, and an allocation it changes shows up on the screen without a sync.
//
// Everything here sits behind the owner-only access gate (access.rs).

use axum::extract::{Path, Query, State};
use axum::response::IntoResponse;
use axum::routing::{delete, get, post};
use axum::http::StatusCode;
use axum::{Json, Router};

use crate::AppState;

pub fn router() -> Router<AppState> {
    Router::new()
        // GET  /copy/book?eoa=       the desk: every allocation + its live
        //                            session and realized ledger
        // POST /copy/book            {bankroll} — the desk's target size
        .route("/copy/book", get(get_book).post(set_book))
        // POST /copy/allocations     {address, allocationUsd, …} upsert
        .route("/copy/allocations", post(upsert_allocation))
        // DELETE /copy/allocations/:address?eoa=   stop + forget
        .route("/copy/allocations/:address", delete(remove_allocation))
        // POST /copy/rebalance       {bankroll, mode: equal|weighted}
        .route("/copy/rebalance", post(rebalance))
        // POST /copy/start           {eoa, address?, autoExecute?}
        // POST /copy/stop            {eoa, address?}
        .route("/copy/start", post(start))
        .route("/copy/stop", post(stop))
        // GET  /copy/strats          the book as identity strats — what the
        //                            backtest worker replays
        .route("/copy/strats", get(strats))
}

#[derive(Deserialize)]
struct EoaQuery {
    /// Whose sessions to report. Optional: without it the book still reads,
    /// just with no live column — which is the right answer for an agent
    /// asking "what is in the book" before any wallet is connected.
    #[serde(default)]
    eoa: Option<String>,
}

/// One allocation's live session, flattened to what a desk row needs.
/// `None` ⇒ this leader has never been started for this wallet.
fn session_view(state: &AppState, eoa: &str, strategy_id: &str) -> Option<Value> {
    let sid = Some(strategy_id);
    // A running engine wins; otherwise the last snapshot persisted to disk, so
    // a stopped allocation still shows what it did rather than going blank.
    let (running, cfg, st) = match state.engines.status_of(eoa, sid) {
        Some(st) => (true, state.engines.config_of(eoa, sid), Some(st)),
        None => match state.engines.persisted_snapshot(eoa, sid) {
            Some((cfg, st)) => (false, Some(cfg), Some(st)),
            None => return None,
        },
    };
    let st = st?;
    let ledger = st.strat_stats.get(strategy_id);
    Some(json!({
        "running": running,
        // DRY RUN vs real money. The single most important bit on this row:
        // "why did it place no trades" is answered here more often than
        // anywhere else (see the module README).
        "autoExecute": cfg.as_ref().map(|c| c.auto_execute).unwrap_or(false),
        "status": st.status,
        "lastCycleAt": st.last_cycle_at,
        "nextCycleAt": st.next_cycle_at,
        "cycles": st.cycle_count,
        "ordersPlaced": st.total_orders_placed,
        "ordersFailed": st.total_orders_failed,
        "volumeMirrored": st.total_volume_mirrored,
        "balance": st.balance,
        "accountValue": st.account_value,
        "error": st.error,
        "ledger": ledger.map(|l| json!({
            "realized": l.realized,
            "volume": l.volume,
            "buys": l.buys,
            "sells": l.sells,
            "redeems": l.redeems,
            "settled": l.settled,
            // No fill EVER, on a session that has been running, means the
            // gates are refusing everything — not that the leader is quiet.
            "lastFillAt": l.last_fill_at,
        })),
    }))
}

/// The desk, assembled: the book plus, per leader, what their session is doing.
fn book_response(state: &AppState, eoa: Option<&str>) -> Value {
    let book = state.copy_book.read();
    let rows: Vec<Value> = book
        .allocations
        .iter()
        .map(|a| {
            let sid = a.strategy_id();
            let live = eoa.and_then(|e| session_view(state, e, &sid));
            json!({
                "address": a.address,
                "label": a.label,
                "name": a.name(),
                "allocationUsd": a.allocation_usd,
                "enabled": a.enabled,
                "params": a.params,
                "notes": a.notes,
                "addedAt": a.added_at,
                "updatedAt": a.updated_at,
                // Everything downstream keys off this, so it is on the wire
                // rather than something each client re-derives.
                "strategyId": sid,
                "live": live,
            })
        })
        .collect();
    let allocated = book.allocated();
    let running = rows
        .iter()
        .filter(|r| r["live"]["running"].as_bool().unwrap_or(false))
        .count();
    let executing = rows
        .iter()
        .filter(|r| {
            r["live"]["running"].as_bool().unwrap_or(false)
                && r["live"]["autoExecute"].as_bool().unwrap_or(false)
        })
        .count();
    json!({
        "version": book.version,
        "bankroll": book.bankroll,
        "updatedAt": book.updated_at,
        "eoa": eoa,
        "allocations": rows,
        "totals": {
            "traders": book.allocations.len(),
            "enabled": book.allocations.iter().filter(|a| a.enabled).count(),
            "allocatedUsd": allocated,
            // Negative ⇒ the desk is over-allocated against its own target.
            // Advisory only; the engine budgets per allocation.
            "unallocatedUsd": round_cents(book.bankroll - allocated),
            "running": running,
            // Sessions actually placing orders. `running - executing` are in
            // DRY RUN: they compute every mirror and send nothing.
            "executing": executing,
        },
    })
}

async fn get_book(State(state): State<AppState>, Query(q): Query<EoaQuery>) -> impl IntoResponse {
    Json(book_response(&state, q.eoa.as_deref()))
}

#[derive(Deserialize)]
struct SetBookBody {
    bankroll: f64,
}

async fn set_book(
    State(state): State<AppState>,
    Query(q): Query<EoaQuery>,
    Json(body): Json<SetBookBody>,
) -> impl IntoResponse {
    match state.copy_book.set_bankroll(body.bankroll) {
        Ok(_) => Json(book_response(&state, q.eoa.as_deref())).into_response(),
        Err(e) => bad_request(e),
    }
}

fn bad_request(e: anyhow::Error) -> axum::response::Response {
    (StatusCode::BAD_REQUEST, Json(json!({"error": e.to_string()}))).into_response()
}

async fn upsert_allocation(
    State(state): State<AppState>,
    Query(q): Query<EoaQuery>,
    Json(body): Json<UpsertRequest>,
) -> impl IntoResponse {
    let alloc = match state.copy_book.upsert(body) {
        Ok(a) => a,
        Err(e) => return bad_request(e),
    };
    // A live session was started from the OLD allocation — its capital, its
    // gates. Re-post the config so an allocation change takes effect now
    // instead of at the next manual restart, and keep the session's current
    // execution mode (re-posting must never flip DRY RUN to live, or the
    // reverse).
    let mut reconfigured = false;
    if let Some(eoa) = q.eoa.as_deref() {
        let sid = alloc.strategy_id();
        if state.engines.status_of(eoa, Some(&sid)).is_some() {
            if let Some(prev) = state.engines.config_of(eoa, Some(&sid)) {
                let mut cfg = engine_config(&alloc, eoa, &prev.address);
                cfg.auto_execute = prev.auto_execute;
                state.engines.start(cfg);
                reconfigured = true;
            }
        }
    }
    Json(json!({
        "ok": true,
        "allocation": alloc,
        "strategyId": alloc.strategy_id(),
        "reconfigured": reconfigured,
        "book": book_response(&state, q.eoa.as_deref()),
    }))
    .into_response()
}

async fn remove_allocation(
    State(state): State<AppState>,
    Path(address): Path<String>,
    Query(q): Query<EoaQuery>,
) -> impl IntoResponse {
    let addr = match normalize_address(&address) {
        Ok(a) => a,
        Err(e) => return bad_request(e),
    };
    // Stop FIRST. A session left running for an allocation that no longer
    // appears on the desk is money being spent by something invisible.
    let mut stopped = false;
    if let Some(eoa) = q.eoa.as_deref() {
        stopped = state.engines.stop(eoa, Some(&strategy_id_for(&addr)));
    }
    match state.copy_book.remove(&addr) {
        Ok(removed) => Json(json!({
            "ok": true,
            "removed": removed,
            "stopped": stopped,
            "book": book_response(&state, q.eoa.as_deref()),
        }))
        .into_response(),
        Err(e) => bad_request(e),
    }
}

#[derive(Deserialize)]
struct RebalanceBody {
    bankroll: f64,
    #[serde(default)]
    mode: Option<String>,
}

async fn rebalance(
    State(state): State<AppState>,
    Query(q): Query<EoaQuery>,
    Json(body): Json<RebalanceBody>,
) -> impl IntoResponse {
    let mode = match RebalanceMode::parse(body.mode.as_deref().unwrap_or("equal")) {
        Ok(m) => m,
        Err(e) => return bad_request(e),
    };
    let book = match state.copy_book.rebalance(body.bankroll, mode) {
        Ok(b) => b,
        Err(e) => return bad_request(e),
    };
    // Push the new capital into every RUNNING session, same rule as an
    // individual edit: sizes change, execution mode doesn't.
    let mut reconfigured = 0usize;
    if let Some(eoa) = q.eoa.as_deref() {
        for a in &book.allocations {
            let sid = a.strategy_id();
            if state.engines.status_of(eoa, Some(&sid)).is_some() {
                if let Some(prev) = state.engines.config_of(eoa, Some(&sid)) {
                    let mut cfg = engine_config(a, eoa, &prev.address);
                    cfg.auto_execute = prev.auto_execute;
                    state.engines.start(cfg);
                    reconfigured += 1;
                }
            }
        }
    }
    Json(json!({
        "ok": true,
        "reconfigured": reconfigured,
        "book": book_response(&state, q.eoa.as_deref()),
    }))
    .into_response()
}

#[derive(Deserialize)]
struct StartBody {
    eoa: String,
    /// One leader, or omitted for every enabled leader in the book.
    #[serde(default)]
    address: Option<String>,
    /// Proxy/deposit wallet holding the funds. Omitted ⇒ derived from the
    /// EOA's backend signer, which is what the console does anyway.
    #[serde(rename = "proxyAddress", default)]
    proxy_address: Option<String>,
    /// **Real money.** Omitted ⇒ `false` ⇒ DRY RUN: the engine computes every
    /// mirror and places none. This default is load-bearing — see the README's
    /// "no trades" triage.
    #[serde(rename = "autoExecute", default)]
    auto_execute: bool,
}

/// Resolve where the money is: the deposit wallet derived from this EOA's
/// backend signer, unless the caller named one.
fn resolve_proxy(state: &AppState, eoa: &str, given: Option<&str>) -> Result<String> {
    if let Some(a) = given {
        return normalize_address(a);
    }
    let signer = state.signer_store.signer_address(eoa)?;
    crate::deposit_wallet::derive_deposit_wallet(&signer)
}

async fn start(State(state): State<AppState>, Json(body): Json<StartBody>) -> impl IntoResponse {
    let eoa = match normalize_address(&body.eoa) {
        Ok(e) => e,
        Err(e) => return bad_request(e),
    };
    let proxy = match resolve_proxy(&state, &eoa, body.proxy_address.as_deref()) {
        Ok(p) => p,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({"error": format!("could not resolve the deposit wallet: {}", e)})),
            )
                .into_response()
        }
    };
    let book = state.copy_book.read();
    let targets: Vec<Allocation> = match body.address.as_deref() {
        Some(a) => {
            let addr = match normalize_address(a) {
                Ok(x) => x,
                Err(e) => return bad_request(e),
            };
            match book.get(&addr) {
                Some(alloc) => vec![alloc.clone()],
                None => {
                    return (
                        StatusCode::NOT_FOUND,
                        Json(json!({"error": format!("{} is not in the copy book", addr)})),
                    )
                        .into_response()
                }
            }
        }
        // Paused leaders are skipped, not started — that is what pausing is.
        None => book.allocations.iter().filter(|a| a.enabled).cloned().collect(),
    };
    if targets.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "nothing to start — the copy book has no enabled traders"})),
        )
            .into_response();
    }
    // An allocation of $0 would run a session that can never size an order.
    // Refuse it here rather than have it show up as a silent no-trade session.
    if let Some(broke) = targets.iter().find(|a| a.allocation_usd <= 0.0) {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({
                "error": format!("{} has no allocation — give them dollars before starting",
                                 short_address(&broke.address))
            })),
        )
            .into_response();
    }
    let started: Vec<Value> = targets
        .iter()
        .map(|a| {
            let mut cfg = engine_config(a, &eoa, &proxy);
            cfg.auto_execute = body.auto_execute;
            let sid = cfg.strategy_id.clone();
            state.engines.start(cfg);
            json!({"address": a.address, "strategyId": sid, "capital": a.allocation_usd})
        })
        .collect();
    tracing::info!(
        eoa = %eoa, sessions = started.len(), auto_execute = body.auto_execute,
        "copy desk started",
    );
    Json(json!({
        "ok": true,
        "autoExecute": body.auto_execute,
        "mode": if body.auto_execute { "LIVE" } else { "DRY RUN" },
        "proxyAddress": proxy,
        "started": started,
        "book": book_response(&state, Some(&eoa)),
    }))
    .into_response()
}

#[derive(Deserialize)]
struct StopBody {
    eoa: String,
    /// One leader, or omitted for every leader in the book.
    #[serde(default)]
    address: Option<String>,
}

async fn stop(State(state): State<AppState>, Json(body): Json<StopBody>) -> impl IntoResponse {
    let eoa = match normalize_address(&body.eoa) {
        Ok(e) => e,
        Err(e) => return bad_request(e),
    };
    let book = state.copy_book.read();
    let addrs: Vec<String> = match body.address.as_deref() {
        Some(a) => match normalize_address(a) {
            Ok(x) => vec![x],
            Err(e) => return bad_request(e),
        },
        // Stop every leader in the book, INCLUDING paused ones: a leader
        // paused after being started still has a session to shut down.
        None => book.allocations.iter().map(|a| a.address.clone()).collect(),
    };
    let stopped: Vec<String> = addrs
        .into_iter()
        .filter(|a| state.engines.stop(&eoa, Some(&strategy_id_for(a))))
        .collect();
    Json(json!({
        "ok": true,
        "stopped": stopped,
        "book": book_response(&state, Some(&eoa)),
    }))
    .into_response()
}

/// The book as strats. This is the handoff to everything that replays or runs
/// strategies: the backtest worker publishes exactly these into its manifest,
/// so each card on the desk is a replay of the same identity strat the live
/// session executes.
async fn strats(State(state): State<AppState>) -> impl IntoResponse {
    let book = state.copy_book.read();
    let strats: Vec<Value> = book
        .allocations
        .iter()
        .filter(|a| a.enabled)
        .map(identity_strat)
        .collect();
    Json(json!({"strats": strats, "count": strats.len()}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn alloc(address: &str, usd: f64) -> Allocation {
        Allocation {
            address: address.to_string(),
            label: None,
            allocation_usd: usd,
            enabled: true,
            params: AllocationParams::default(),
            notes: None,
            added_at: 1_700_000_000_000,
            updated_at: 1_700_000_000_000,
        }
    }

    #[test]
    fn strategy_id_round_trips_through_the_address() {
        let a = "0x1234567890abcdef1234567890abcdef12345678";
        let sid = strategy_id_for(a);
        assert_eq!(sid, "copy-1234567890abcdef1234567890abcdef12345678");
        assert_eq!(address_from_strategy_id(&sid).as_deref(), Some(a));
        // A strat id from the OLD hub (opaque base36) must not decode to an
        // address — the ledger would attribute another strat's P&L to a leader.
        assert_eq!(address_from_strategy_id("mrjg86gf"), None);
        assert_eq!(address_from_strategy_id("copy-not-hex"), None);
    }

    #[test]
    fn identity_template_marks_the_class_and_carries_the_allocation() {
        let s = identity_strat(&alloc("0xabc0000000000000000000000000000000000001", 250.0));
        assert_eq!(s["identity"], "0xabc0000000000000000000000000000000000001");
        assert_eq!(s["capital"], 250.0);
        // Exactly one trader, full weight — that IS the identity template.
        assert_eq!(s["traders"].as_array().unwrap().len(), 1);
        assert_eq!(s["traders"][0]["weight"], 1.0);
        assert_eq!(s["traders"][0]["address"], s["identity"]);
        assert_eq!(s["id"], "copy-abc0000000000000000000000000000000000001");
        // The defaults that cost money before they were defaults.
        assert_eq!(s["minMinutesToClose"], 60.0);
        assert_eq!(s["sizing"], "flow");
        assert_eq!(s["stopLoss"], 0.75);
    }

    #[test]
    fn engine_config_matches_the_identity_strat_it_was_built_from() {
        let a = alloc("0xabc0000000000000000000000000000000000001", 250.0);
        let strat = identity_strat(&a);
        let cfg = engine_config(&a, "0xdEaD00000000000000000000000000000000BeEf", "0xproxy");
        // The two representations must not drift: a backtest card that
        // promises one thing while the session runs another is the whole bug
        // class this template exists to close.
        assert_eq!(cfg.strategy_id, strat["id"].as_str().unwrap());
        assert_eq!(cfg.capital, strat["capital"].as_f64().unwrap());
        assert_eq!(cfg.min_order_size, strat["minTrade"].as_f64().unwrap());
        assert_eq!(cfg.max_order_size, strat["maxTrade"].as_f64());
        assert_eq!(cfg.max_open_positions as u64, strat["maxOpenPositions"].as_u64().unwrap());
        assert_eq!(cfg.min_minutes_to_close, strat["minMinutesToClose"].as_f64());
        assert_eq!(cfg.stop_loss, strat["stopLoss"].as_f64());
        assert_eq!(cfg.traders.len(), 1);
        assert_eq!(cfg.traders[0].address, a.address);
        // 30s poll → 30_000ms, and the eoa is lowercased for the session key.
        assert_eq!(cfg.interval_ms, 30_000);
        assert_eq!(cfg.eoa, "0xdead00000000000000000000000000000000beef");
        // DRY RUN unless something explicitly turns it on.
        assert!(!cfg.auto_execute);
    }

    #[test]
    fn params_patch_leaves_untouched_knobs_alone() {
        let mut p = AllocationParams {
            stop_loss: Some(0.5),
            max_trade: Some(20.0),
            ..Default::default()
        };
        p.merge(&AllocationParams {
            max_trade: Some(40.0),
            ..Default::default()
        });
        assert_eq!(p.max_trade, Some(40.0));
        assert_eq!(p.stop_loss, Some(0.5), "an omitted knob must not reset");
    }

    #[test]
    fn explicit_zero_is_a_value_not_an_absence() {
        // stopLoss: 0 means "no stop-loss", and must survive the template.
        let mut a = alloc("0xabc0000000000000000000000000000000000001", 100.0);
        a.params.stop_loss = Some(0.0);
        assert_eq!(identity_strat(&a)["stopLoss"], 0.0);
        let cfg = engine_config(&a, "0xeoa", "0xproxy");
        assert_eq!(cfg.stop_loss, Some(0.0));
    }

    #[test]
    fn address_validation_rejects_the_near_misses() {
        assert!(normalize_address("0xABC0000000000000000000000000000000000001").is_ok());
        assert_eq!(
            normalize_address("  0xABC0000000000000000000000000000000000001 ").unwrap(),
            "0xabc0000000000000000000000000000000000001"
        );
        assert!(normalize_address("0xabc").is_err());
        assert!(normalize_address("abc0000000000000000000000000000000000001").is_err());
        assert!(normalize_address("0xzz00000000000000000000000000000000000001").is_err());
    }

    #[test]
    fn weighted_rebalance_preserves_conviction() {
        let mut book = CopyBook::default();
        book.allocations = vec![
            alloc("0xaaa0000000000000000000000000000000000001", 300.0),
            alloc("0xbbb0000000000000000000000000000000000002", 100.0),
        ];
        // Simulate the store's arithmetic on a doubled bankroll.
        let total: f64 = book.allocations.iter().map(|a| a.allocation_usd).sum();
        let scaled: Vec<f64> = book
            .allocations
            .iter()
            .map(|a| round_cents(800.0 * (a.allocation_usd / total)))
            .collect();
        assert_eq!(scaled, vec![600.0, 200.0]);
    }

    /// Numbers are the whole point of this comparison and JSON has one number
    /// type; `1` and `1.0` must not be a parity failure.
    fn normalize(v: &Value) -> Value {
        match v {
            Value::Number(n) => json!(n.as_f64().unwrap_or(f64::NAN)),
            Value::Array(a) => Value::Array(a.iter().map(normalize).collect()),
            Value::Object(o) => {
                Value::Object(o.iter().map(|(k, x)| (k.clone(), normalize(x))).collect())
            }
            other => other.clone(),
        }
    }

    /// The identity template exists in Rust (here) and in TypeScript
    /// (`app/lib/identityStrat.ts`). This file is the contract between them;
    /// `app/lib/strats/__test__.ts` asserts the same cases from the other side.
    #[test]
    fn identity_template_matches_the_shared_fixture() {
        let raw = include_str!("../../app/app/lib/strats/identity.fixture.json");
        let fixture: Value = serde_json::from_str(raw).expect("fixture parses");
        let cases = fixture["cases"].as_array().expect("cases");
        assert!(!cases.is_empty());
        for case in cases {
            let alloc: Allocation =
                serde_json::from_value(case["allocation"].clone()).expect("allocation parses");
            let got = identity_strat(&alloc);
            assert_eq!(
                normalize(&got),
                normalize(&case["strat"]),
                "identity template drifted for case {:?}",
                case["name"].as_str().unwrap_or("?"),
            );
        }
    }

    fn temp_store(name: &str) -> std::sync::Arc<CopyBookStore> {
        let path = std::env::temp_dir().join(format!("polymarket-copy-test-{name}.json"));
        std::fs::remove_file(&path).ok();
        CopyBookStore::at(path)
    }

    fn upsert(usd: f64, address: &str) -> UpsertRequest {
        UpsertRequest {
            address: address.to_string(),
            allocation_usd: usd,
            label: None,
            notes: None,
            enabled: None,
            params: AllocationParams::default(),
        }
    }

    #[test]
    fn copying_the_same_trader_twice_is_one_allocation() {
        let store = temp_store("idempotent");
        let a = "0xAAA0000000000000000000000000000000000001";
        store.upsert(upsert(100.0, a)).unwrap();
        store.upsert(upsert(250.0, a)).unwrap();
        let book = store.read();
        assert_eq!(book.allocations.len(), 1, "one leader, one line");
        assert_eq!(book.allocations[0].allocation_usd, 250.0);
        // …and one session key, so the ledger doesn't split across two ids.
        assert_eq!(
            book.allocations[0].strategy_id(),
            "copy-aaa0000000000000000000000000000000000001"
        );
    }

    #[test]
    fn the_book_survives_a_restart() {
        let path = std::env::temp_dir().join("polymarket-copy-test-persist.json");
        std::fs::remove_file(&path).ok();
        {
            let store = CopyBookStore::at(path.clone());
            store
                .upsert(upsert(75.0, "0xAAA0000000000000000000000000000000000001"))
                .unwrap();
            store.set_bankroll(1000.0).unwrap();
        }
        let reopened = CopyBookStore::at(path).read();
        assert_eq!(reopened.bankroll, 1000.0);
        assert_eq!(reopened.allocations.len(), 1);
        assert_eq!(reopened.allocations[0].allocation_usd, 75.0);
    }

    #[test]
    fn rebalance_splits_the_bankroll_and_skips_the_paused() {
        let store = temp_store("rebalance");
        store.upsert(upsert(10.0, "0xAAA0000000000000000000000000000000000001")).unwrap();
        store.upsert(upsert(30.0, "0xBBB0000000000000000000000000000000000002")).unwrap();
        let mut paused = upsert(999.0, "0xCCC0000000000000000000000000000000000003");
        paused.enabled = Some(false);
        store.upsert(paused).unwrap();

        let book = store.rebalance(400.0, RebalanceMode::Weighted).unwrap();
        // 10:30 conviction preserved at the new size; the paused line untouched.
        assert_eq!(book.allocations[0].allocation_usd, 100.0);
        assert_eq!(book.allocations[1].allocation_usd, 300.0);
        assert_eq!(book.allocations[2].allocation_usd, 999.0);
        assert_eq!(book.allocated(), 400.0);

        let equal = store.rebalance(400.0, RebalanceMode::Equal).unwrap();
        assert_eq!(equal.allocations[0].allocation_usd, 200.0);
        assert_eq!(equal.allocations[1].allocation_usd, 200.0);
    }

    #[test]
    fn the_store_refuses_nonsense_before_it_reaches_the_engine() {
        let store = temp_store("validation");
        assert!(store.upsert(upsert(-5.0, "0xAAA0000000000000000000000000000000000001")).is_err());
        assert!(store.upsert(upsert(f64::NAN, "0xAAA0000000000000000000000000000000000001")).is_err());
        assert!(store.upsert(upsert(5.0, "0xnope")).is_err());
        assert!(store.rebalance(0.0, RebalanceMode::Equal).is_err(),
                "a zero bankroll would silently zero every allocation");
        assert!(store.read().allocations.is_empty());
    }

    #[test]
    fn removing_a_trader_who_was_never_in_the_book_is_not_an_error() {
        let store = temp_store("remove");
        assert!(!store.remove("0xAAA0000000000000000000000000000000000001").unwrap());
        store.upsert(upsert(5.0, "0xAAA0000000000000000000000000000000000001")).unwrap();
        assert!(store.remove("0xaaa0000000000000000000000000000000000001").unwrap());
        assert!(store.read().allocations.is_empty());
    }

    #[test]
    fn allocated_ignores_paused_traders() {
        let mut book = CopyBook::default();
        let mut paused = alloc("0xbbb0000000000000000000000000000000000002", 100.0);
        paused.enabled = false;
        book.allocations = vec![alloc("0xaaa0000000000000000000000000000000000001", 300.0), paused];
        assert_eq!(book.allocated(), 300.0);
    }
}
