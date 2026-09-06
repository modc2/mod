//! The reconciler — the only thing that moves money for a `Position`.
//!
//! There is no "start" button. The book *is* the instruction: an active trader
//! position means "this sleeve should look like that trader, scaled to my
//! money", and this loop makes reality match, forever, from whatever state it
//! finds. That is the whole design decision here, and it buys three things a
//! fill-mirroring engine can never have:
//!
//!   * **Self-healing.** A missed cycle, a rejected order, an API restart, a
//!     rate limit — the next pass simply diffs again and corrects. Nothing to
//!     replay, no cursor to lose.
//!   * **Late joining.** Invest at 3pm in a trader who opened at 9am and you
//!     get their *book*, not just whatever they happen to do next.
//!   * **Honest sizing.** Targets come from the leader's live portfolio and
//!     your basis, so "invest $500" means $500 of exposure — not "10% of
//!     whatever size a stranger felt like".
//!
//! Everything the loop decides is computed by pure functions in `invest.rs`
//! (`plan` → `reconcile` → `apply_fill`), which are unit-tested against the
//! cases that actually cost money: leverage caps, sign flips, dust exits.
//!
//! Safety rails, in order of how much they matter:
//!   1. A `Paper` position never sends an order. Same planning, same ledger,
//!      simulated fills at the mark — real measurement, no risk.
//!   2. `HYPERLIQUID_INVEST_DRY=1` forces every position into paper mode at
//!      the host level, no matter what the book says.
//!   3. Live orders need the investor's agent approval on-chain; without it
//!      the position parks with a readable error instead of retry-storming.
//!   4. Failures back off exponentially per position, so one broken sleeve
//!      cannot burn the account's rate limit for everyone else.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};

use dashmap::DashMap;
use parking_lot::RwLock;
use serde::Serialize;
use serde_json::Value;

use crate::actions::{self, MetaCache, OrderSide};
use crate::hl::Client;
use crate::invest::{
    self, InvestStore, Intent, LeaderPos, Mode, Position, Status,
};
use crate::signer::SignerStore;

/// How often the book is reconciled against the market.
const DEFAULT_TICK_MS: u64 = 20_000;
/// Mid prices are shared by every position in a cycle.
const MARKS_TTL: Duration = Duration::from_secs(10);
/// One leader's portfolio is read once per window however many people copy it.
const LEADER_TTL: Duration = Duration::from_secs(30);
/// Agent-approval lookups are slow and change ~never.
const AGENT_TTL: Duration = Duration::from_secs(300);
/// Most orders one position may send in a single cycle. A leader who rotates
/// their whole book shouldn't turn into a 40-order burst.
const MAX_INTENTS_PER_CYCLE: usize = 8;
/// Backoff ceiling after repeated failures.
const MAX_BACKOFF_MS: i64 = 15 * 60_000;

#[derive(Debug, Clone, Default, Serialize)]
pub struct EngineStats {
    pub cycles: u64,
    pub last_cycle_ms: i64,
    pub last_cycle_duration_ms: i64,
    pub positions_tracked: usize,
    pub orders_placed: u64,
    pub orders_failed: u64,
    pub paper_fills: u64,
    pub volume_usd: f64,
    pub last_error: Option<String>,
}

/// A leader's live book, priced. Public because the routes render the same
/// snapshot the engine trades from — one source of truth for "what do they
/// hold right now".
#[derive(Clone)]
pub struct LeaderSnapshot {
    pub equity: f64,
    pub positions: Vec<LeaderPos>,
}

pub struct InvestEngine {
    hl: Arc<Client>,
    http: reqwest::Client,
    signer: Arc<SignerStore>,
    meta: Arc<MetaCache>,
    pub store: Arc<InvestStore>,
    marks: RwLock<Option<(Instant, HashMap<String, f64>)>>,
    leaders: DashMap<String, (Instant, LeaderSnapshot)>,
    agents: DashMap<String, (Instant, bool)>,
    stats: RwLock<EngineStats>,
    /// Host-level kill switch: everything runs as paper.
    dry_run: bool,
}

impl InvestEngine {
    pub fn new(
        hl: Arc<Client>,
        http: reqwest::Client,
        signer: Arc<SignerStore>,
        meta: Arc<MetaCache>,
        store: Arc<InvestStore>,
    ) -> Self {
        let dry_run = std::env::var("HYPERLIQUID_INVEST_DRY").ok().as_deref() == Some("1");
        if dry_run {
            tracing::warn!("invest engine: HYPERLIQUID_INVEST_DRY=1 — every position runs as paper");
        }
        Self {
            hl, http, signer, meta, store,
            marks: RwLock::new(None),
            leaders: DashMap::new(),
            agents: DashMap::new(),
            stats: RwLock::new(EngineStats::default()),
            dry_run,
        }
    }

    pub fn stats(&self) -> EngineStats { self.stats.read().clone() }
    pub fn is_dry(&self) -> bool { self.dry_run }

    // ── Shared market/leader reads ──────────────────────────────────────

    /// Every mid price, keyed by coin. One fetch per cycle for the whole book.
    pub async fn marks(&self) -> HashMap<String, f64> {
        if let Some((t, m)) = &*self.marks.read() {
            if t.elapsed() < MARKS_TTL { return m.clone(); }
        }
        let mut out = HashMap::new();
        if let Ok(v) = self.hl.all_mids().await {
            if let Some(map) = v.as_object() {
                for (k, val) in map {
                    if let Some(px) = val.as_str().and_then(|s| s.parse::<f64>().ok()) {
                        out.insert(k.clone(), px);
                    }
                }
            }
        }
        if !out.is_empty() {
            *self.marks.write() = Some((Instant::now(), out.clone()));
        }
        out
    }

    /// A leader's live portfolio: account equity + signed positions, priced.
    /// Cached per leader, so ten investors copying one trader cost one call.
    async fn leader(&self, addr: &str, marks: &HashMap<String, f64>) -> anyhow::Result<LeaderSnapshot> {
        let key = addr.to_lowercase();
        if let Some(e) = self.leaders.get(&key) {
            if e.0.elapsed() < LEADER_TTL { return Ok(e.1.clone()); }
        }
        let st = self.hl.user_state(&key).await?;
        let snap = parse_leader_state(&st, marks);
        self.leaders.insert(key, (Instant::now(), snap.clone()));
        Ok(snap)
    }

    /// Has this investor authorized our agent wallet to sign for them?
    async fn agent_approved(&self, eoa: &str) -> bool {
        let key = eoa.to_lowercase();
        if let Some(e) = self.agents.get(&key) {
            if e.0.elapsed() < AGENT_TTL { return e.1; }
        }
        let ok = match self.signer.signer_address(&key) {
            Ok(agent) => self.hl.extra_agents(&key).await.ok()
                .and_then(|v| v.as_array().map(|arr| arr.iter().any(|a| {
                    a.get("address").and_then(|x| x.as_str())
                        .map(|x| x.eq_ignore_ascii_case(&agent)).unwrap_or(false)
                })))
                .unwrap_or(false),
            Err(_) => false,
        };
        self.agents.insert(key, (Instant::now(), ok));
        ok
    }

    /// Drop the cached approval for one wallet — called right after the user
    /// signs, so the next cycle acts instead of waiting out the TTL.
    pub fn forget_agent(&self, eoa: &str) {
        self.agents.remove(&eoa.to_lowercase());
    }

    // ── The loop ────────────────────────────────────────────────────────

    pub async fn run(self: Arc<Self>) {
        let tick = std::env::var("INVEST_TICK_MS").ok()
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(DEFAULT_TICK_MS)
            .max(3_000);
        let mut ticker = tokio::time::interval(Duration::from_millis(tick));
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        tracing::info!("invest engine: reconciling every {}ms", tick);
        loop {
            ticker.tick().await;
            let started = Instant::now();
            if let Err(e) = self.cycle().await {
                tracing::warn!("invest cycle failed: {e}");
                self.stats.write().last_error = Some(e.to_string());
            }
            let mut s = self.stats.write();
            s.cycles += 1;
            s.last_cycle_ms = chrono::Utc::now().timestamp_millis();
            s.last_cycle_duration_ms = started.elapsed().as_millis() as i64;
        }
    }

    async fn cycle(&self) -> anyhow::Result<()> {
        let now = chrono::Utc::now().timestamp_millis();
        let queue = self.store.engine_queue(now);
        self.stats.write().positions_tracked = queue.len();
        if queue.is_empty() { return Ok(()); }

        let marks = self.marks().await;
        if marks.is_empty() {
            anyhow::bail!("no mid prices available this cycle");
        }

        let mut dirty: Vec<Position> = Vec::new();
        for mut p in queue {
            match self.step(&mut p, &marks).await {
                Ok(_changed) => {
                    // Any pass that got through clears the slate — including
                    // one that found nothing to do. A transient 429 must not
                    // leave "needs attention" on the position forever.
                    p.fail_streak = 0;
                    p.next_attempt_ms = 0;
                    p.last_error = None;
                    p.last_sync_ms = chrono::Utc::now().timestamp_millis();
                    dirty.push(p);
                }
                Err(e) => {
                    let msg = e.to_string();
                    p.fail_streak = p.fail_streak.saturating_add(1);
                    let backoff = (60_000i64 << p.fail_streak.min(4)).min(MAX_BACKOFF_MS);
                    p.next_attempt_ms = chrono::Utc::now().timestamp_millis() + backoff;
                    if p.last_error.as_deref() != Some(msg.as_str()) {
                        p.log("error", msg.clone(), chrono::Utc::now().timestamp_millis());
                    }
                    p.last_error = Some(msg);
                    p.last_sync_ms = chrono::Utc::now().timestamp_millis();
                    dirty.push(p);
                }
            }
        }
        self.store.update_many(dirty);
        Ok(())
    }

    /// One position, one cycle. Returns whether anything was written.
    async fn step(&self, p: &mut Position, marks: &HashMap<String, f64>) -> anyhow::Result<bool> {
        let now = chrono::Utc::now().timestamp_millis();

        // Flattening: drive every leg to zero, then the position is done.
        if p.flattening() {
            let intents = invest::reconcile(&p.sleeve, &[], marks, p.risk.min_order_usd, true);
            if intents.is_empty() {
                p.status = Status::Closed;
                p.log("closed", "position closed — every leg is flat", now);
                return Ok(true);
            }
            self.execute(p, intents, marks).await?;
            if p.sleeve.legs.is_empty() {
                p.status = Status::Closed;
                p.log("closed", "position closed — every leg is flat", now);
            }
            return Ok(true);
        }

        if !p.tracking() { return Ok(false); }

        // Stop loss is checked before new exposure, never after.
        if p.risk.stop_loss_pct > 0.0 {
            let v = invest::value_sleeve(p, marks);
            let floor = -(p.contributed_usd * p.risk.stop_loss_pct / 100.0);
            if v.pnl <= floor && p.contributed_usd > 0.0 {
                p.status = Status::Closing;
                p.log("stop", format!(
                    "stop-loss hit: {:.2} USD is past your -{:.0}% line — closing out",
                    v.pnl, p.risk.stop_loss_pct), now);
                return Ok(true);
            }
        }

        let leader = self.leader(&p.target, marks).await
            .map_err(|e| anyhow::anyhow!("could not read the trader's portfolio: {e}"))?;
        if leader.equity <= 0.0 {
            anyhow::bail!("this trader's Hyperliquid account reads as empty — nothing to track");
        }

        let plan = invest::plan(&leader.positions, leader.equity, p.basis(), &p.risk);
        let intents = invest::reconcile(&p.sleeve, &plan.targets, marks, p.risk.min_order_usd, false);
        if intents.is_empty() { return Ok(false); }
        self.execute(p, intents, marks).await?;
        Ok(true)
    }

    /// Send (or simulate) the orders and book every resulting fill.
    async fn execute(
        &self,
        p: &mut Position,
        intents: Vec<Intent>,
        marks: &HashMap<String, f64>,
    ) -> anyhow::Result<()> {
        let paper = self.dry_run || p.mode == Mode::Paper;
        if !paper && !self.agent_approved(&p.investor).await {
            anyhow::bail!(
                "your wallet hasn't authorized the trading agent yet — open the position and press \"enable trading\""
            );
        }

        let mut errors: Vec<String> = Vec::new();
        for intent in intents.into_iter().take(MAX_INTENTS_PER_CYCLE) {
            let now = chrono::Utc::now().timestamp_millis();

            // Round to the exchange's size grid before deciding anything —
            // an order HL would reject is not an order.
            let step_dec = match self.meta.get(&intent.coin).await {
                Ok(spec) => spec.sz_decimals,
                Err(e) => { errors.push(format!("{}: {e}", intent.coin)); continue; }
            };
            let size = round_to(intent.delta.abs(), step_dec);
            if size <= 0.0 { continue; }

            let signed = size * intent.delta.signum();
            let side = if intent.delta > 0.0 { OrderSide::Buy } else { OrderSide::Sell };
            let mark = marks.get(&intent.coin).copied().unwrap_or(intent.mark);

            if paper {
                // Pay the same spread a live order would: the slippage
                // assumption is applied, so paper numbers are comparable to
                // live ones rather than flattering.
                let slip = p.risk.max_slippage_bps as f64 / 10_000.0;
                let px = match side { OrderSide::Buy => mark * (1.0 + slip), OrderSide::Sell => mark * (1.0 - slip) };
                invest::book_fill(p, &intent.coin, signed, px, intent.reason, false, now);
                self.stats.write().paper_fills += 1;
                continue;
            }

            let res = actions::place_market_order(
                &self.http, &self.hl, &self.signer, &self.meta,
                &p.investor, &intent.coin, side, size,
                p.risk.max_slippage_bps, intent.reduce_only, None,
            ).await;

            match res.map_err(|e| e.to_string()).and_then(|v| invest::parse_order_fill(&v)) {
                Ok((filled_sz, avg_px)) => {
                    let signed_filled = filled_sz * intent.delta.signum();
                    invest::book_fill(p, &intent.coin, signed_filled, avg_px, intent.reason, true, now);
                    let mut s = self.stats.write();
                    s.orders_placed += 1;
                    s.volume_usd += filled_sz * avg_px;
                }
                Err(e) => {
                    self.stats.write().orders_failed += 1;
                    errors.push(format!("{}: {e}", intent.coin));
                    p.log("order-failed", format!("{} {:.6} — {}", intent.coin, size, e), now);
                }
            }
        }

        if !errors.is_empty() {
            anyhow::bail!("{}", errors.join(" · "));
        }
        Ok(())
    }
}

/// Read a leader's `clearinghouseState` into equity + priced positions.
pub fn parse_leader_state(state: &Value, marks: &HashMap<String, f64>) -> LeaderSnapshot {
    let f = |v: Option<&Value>| v.and_then(|x| x.as_str()).and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
    let equity = f(state.get("marginSummary").and_then(|m| m.get("accountValue")));
    let mut positions = Vec::new();
    if let Some(arr) = state.get("assetPositions").and_then(|a| a.as_array()) {
        for ap in arr {
            let pos = ap.get("position").unwrap_or(ap);
            let coin = pos.get("coin").and_then(|c| c.as_str()).unwrap_or("").to_string();
            if coin.is_empty() { continue; }
            let size = f(pos.get("szi"));
            if size == 0.0 { continue; }
            // Prefer the live mid; fall back to the position's own valuation
            // so a coin missing from /allMids doesn't silently vanish.
            let mark = marks.get(&coin).copied().unwrap_or_else(|| {
                let value = f(pos.get("positionValue"));
                if size != 0.0 && value > 0.0 { value / size.abs() } else { f(pos.get("entryPx")) }
            });
            if !(mark > 0.0) { continue; }
            positions.push(LeaderPos { coin, size, mark });
        }
    }
    LeaderSnapshot { equity, positions }
}

/// Round a size down onto the exchange's decimal grid for that asset.
fn round_to(x: f64, decimals: u32) -> f64 {
    let f = 10f64.powi(decimals as i32);
    (x * f).floor() / f
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn leader_state_is_read_with_live_marks() {
        let st = json!({
            "marginSummary": {"accountValue": "1000000.0"},
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "10.0", "entryPx": "90000", "positionValue": "1000000"}},
                {"position": {"coin": "ETH", "szi": "-100.0", "entryPx": "4000", "positionValue": "400000"}},
                {"position": {"coin": "SOL", "szi": "0.0", "entryPx": "200", "positionValue": "0"}}
            ]
        });
        let marks: HashMap<String, f64> = [("BTC".to_string(), 100_000.0)].into_iter().collect();
        let snap = parse_leader_state(&st, &marks);
        assert_eq!(snap.equity, 1_000_000.0);
        assert_eq!(snap.positions.len(), 2, "flat legs are dropped");
        let btc = snap.positions.iter().find(|p| p.coin == "BTC").unwrap();
        assert_eq!(btc.mark, 100_000.0, "live mid wins");
        let eth = snap.positions.iter().find(|p| p.coin == "ETH").unwrap();
        assert_eq!(eth.size, -100.0);
        assert_eq!(eth.mark, 4_000.0, "falls back to positionValue/|size|");
    }

    #[test]
    fn empty_state_is_harmless() {
        let snap = parse_leader_state(&json!({}), &HashMap::new());
        assert_eq!(snap.equity, 0.0);
        assert!(snap.positions.is_empty());
    }

    #[test]
    fn sizes_round_down_onto_the_exchange_grid() {
        assert_eq!(round_to(0.123456, 3), 0.123);
        assert_eq!(round_to(0.0009, 3), 0.0);
        assert_eq!(round_to(12.7, 0), 12.0);
    }
}
