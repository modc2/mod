//! The investment book — one noun for "I put money into something that trades".
//!
//! Hyperliquid gives you two ways to hand your capital to someone who trades
//! better than you do, and until now this module exposed them as two unrelated
//! machines: a vault deposit form, and an expert-mode copy engine configured in
//! *percent of the leader's size*. Neither answers the only question an
//! investor actually asks — **"what happens to my $500?"**
//!
//! So both become one thing: a `Position`.
//!
//!   * `kind = Vault`  — USDC deposited into a Hyperliquid vault. The leader
//!     trades it inside HL's own vault machinery; equity and PnL are read back
//!     from `vaultDetails.followerState`. We hold the ledger of what you put in.
//!
//!   * `kind = Trader` — a **sleeve** inside your own Hyperliquid account that
//!     tracks one trader's *portfolio shape*, scaled to your money. Not "copy
//!     10% of their size" (which means nothing if you don't know their size),
//!     but "hold what they hold, in your proportion": `scale = your basis /
//!     their account value`. The engine (see `invest_engine.rs`) reconciles the
//!     sleeve toward that target every cycle, so a missed poll, a partial fill
//!     or a rounding step is self-healing instead of permanent drift.
//!
//! Everything in this file is pure book-keeping and arithmetic — no HTTP, no
//! signing, no clock beyond a passed-in timestamp — so the money math is unit
//! testable. The engine does the I/O; the routes do the HTTP.
//!
//! ## Sleeve accounting, honestly
//!
//! All of an investor's sleeves live in one Hyperliquid account, so HL cannot
//! tell us what a *sleeve* is worth — we have to keep that ledger ourselves.
//! Every fill the engine causes is applied here with average-cost accounting:
//! realized PnL accrues on the closing side, unrealized is marked from the live
//! mid. It excludes fees and funding (HL does not return them on the order
//! response), which is stated in the API payload as `basis: "mirrored fills,
//! excl. fees and funding"` rather than quietly rounded away.
//!
//! A position may run in `Paper` mode: identical planning, identical
//! book-keeping, fills simulated at the mark with the same slippage assumption
//! — no orders, no money. It is the honest way to watch a trader for a week
//! before funding them.

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::path::{Path, PathBuf};

/// Keep at most this many mirrored fills per position (newest first).
const FILL_LOG_CAP: usize = 400;
/// Per-position event log cap.
const EVENT_CAP: usize = 200;

// ─── Types ──────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    /// Hyperliquid native vault deposit.
    Vault,
    /// A mirrored sleeve of one trader's portfolio, inside your own account.
    Trader,
}

impl Kind {
    pub fn as_str(&self) -> &'static str {
        match self { Kind::Vault => "vault", Kind::Trader => "trader" }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    /// Funded and tracking.
    Active,
    /// Holding whatever it holds; no new orders until resumed.
    Paused,
    /// Flattening — the engine drives every leg to zero, then marks Closed.
    Closing,
    /// Done. Kept for the record (and its PnL) until deleted.
    Closed,
}

impl Status {
    pub fn as_str(&self) -> &'static str {
        match self {
            Status::Active => "active", Status::Paused => "paused",
            Status::Closing => "closing", Status::Closed => "closed",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// Real orders, real money.
    Live,
    /// Simulated fills at the mark. Same math, no orders.
    Paper,
}

impl Mode {
    pub fn as_str(&self) -> &'static str {
        match self { Mode::Live => "live", Mode::Paper => "paper" }
    }
}

/// The dials an investor is allowed to turn. Defaults are the ones a person
/// who has never traded perps should be able to accept without reading them.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Risk {
    /// Gross exposure ceiling as a multiple of the sleeve's basis. 1.0 means
    /// "never hold more notional than the money I put in", which is what an
    /// investor assumes by default even when the leader runs 5×.
    #[serde(default = "d_leverage")]
    pub max_leverage: f64,
    /// Per-order slippage padding on the marketable IOC price.
    #[serde(default = "d_slippage")]
    pub max_slippage_bps: u32,
    /// Don't trade drift smaller than this. Also HL's own floor is $10, so
    /// anything under that would be rejected anyway.
    #[serde(default = "d_min_order")]
    pub min_order_usd: f64,
    /// Empty = every coin the leader trades.
    #[serde(default)]
    pub coins_allow: Vec<String>,
    #[serde(default)]
    pub coins_deny: Vec<String>,
    /// Auto-close the sleeve when its PnL falls this far below the money put
    /// in, as a percent of contributions. 0 = off.
    #[serde(default)]
    pub stop_loss_pct: f64,
}

fn d_leverage() -> f64 { 1.0 }
fn d_slippage() -> u32 { 100 }
fn d_min_order() -> f64 { 12.0 }

impl Default for Risk {
    fn default() -> Self {
        Self {
            max_leverage: d_leverage(),
            max_slippage_bps: d_slippage(),
            min_order_usd: d_min_order(),
            coins_allow: Vec::new(),
            coins_deny: Vec::new(),
            stop_loss_pct: 0.0,
        }
    }
}

impl Risk {
    /// Clamp anything a client can send into a range that can't hurt someone.
    pub fn sanitized(mut self) -> Self {
        self.max_leverage = if self.max_leverage.is_finite() { self.max_leverage.clamp(0.1, 10.0) } else { 1.0 };
        self.max_slippage_bps = self.max_slippage_bps.clamp(5, 2_000);
        self.min_order_usd = if self.min_order_usd.is_finite() { self.min_order_usd.clamp(10.0, 100_000.0) } else { d_min_order() };
        self.stop_loss_pct = if self.stop_loss_pct.is_finite() { self.stop_loss_pct.clamp(0.0, 100.0) } else { 0.0 };
        self.coins_allow = self.coins_allow.iter().map(|c| c.trim().to_uppercase()).filter(|c| !c.is_empty()).collect();
        self.coins_deny = self.coins_deny.iter().map(|c| c.trim().to_uppercase()).filter(|c| !c.is_empty()).collect();
        self
    }

    pub fn allows(&self, coin: &str) -> bool {
        let c = coin.to_uppercase();
        if self.coins_deny.iter().any(|d| *d == c) { return false; }
        self.coins_allow.is_empty() || self.coins_allow.iter().any(|a| *a == c)
    }
}

/// One coin's exposure inside a sleeve. `size` is signed (long positive).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Leg {
    pub size: f64,
    /// Average entry price of the open size (average-cost basis).
    pub avg_px: f64,
}

/// A fill the engine caused on this position's behalf.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SleeveFill {
    pub ts_ms: i64,
    pub coin: String,
    /// "buy" | "sell"
    pub side: String,
    pub size: f64,
    pub price: f64,
    pub notional: f64,
    /// Realized PnL booked by this fill (only nonzero on closing trades).
    pub realized: f64,
    /// Why the engine wanted it: "track" (following the leader) or "exit".
    pub reason: String,
    /// False when the fill was simulated (paper mode).
    pub live: bool,
}

/// Money in and out of the position, so returns are measured against what the
/// investor actually contributed rather than a number we chose.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Flow {
    pub ts_ms: i64,
    /// "in" | "out"
    pub dir: String,
    pub amount_usd: f64,
    #[serde(default)]
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub ts_ms: i64,
    pub kind: String,
    pub text: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Sleeve {
    /// coin → open leg. BTreeMap so serialization is stable/diffable.
    #[serde(default)]
    pub legs: BTreeMap<String, Leg>,
    #[serde(default)]
    pub realized_pnl: f64,
    #[serde(default)]
    pub fills: Vec<SleeveFill>,
}

impl Sleeve {
    pub fn open_legs(&self) -> impl Iterator<Item = (&String, &Leg)> {
        self.legs.iter().filter(|(_, l)| l.size.abs() > 0.0)
    }

    /// Mark-to-market PnL of the open legs at the given prices.
    pub fn unrealized(&self, marks: &HashMap<String, f64>) -> f64 {
        self.open_legs()
            .map(|(coin, leg)| match marks.get(coin) {
                Some(px) => (px - leg.avg_px) * leg.size,
                None => 0.0,
            })
            .sum()
    }

    /// Gross notional currently held, at the given prices.
    pub fn gross_notional(&self, marks: &HashMap<String, f64>) -> f64 {
        self.open_legs()
            .map(|(coin, leg)| leg.size.abs() * marks.get(coin).copied().unwrap_or(leg.avg_px))
            .sum()
    }

    pub fn push_fill(&mut self, f: SleeveFill) {
        self.fills.insert(0, f);
        self.fills.truncate(FILL_LOG_CAP);
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub id: String,
    /// Lowercase investor address — the owner of this row. Everything is
    /// scoped by it; the auth guard binds it to the signed-in wallet.
    pub investor: String,
    pub kind: Kind,
    /// Vault address, or the leader wallet being tracked.
    pub target: String,
    /// Human label, captured at creation (vault name / trader shortname).
    #[serde(default)]
    pub name: String,
    pub status: Status,
    #[serde(default = "d_mode")]
    pub mode: Mode,
    /// Set when this position is one leg of a basket (a strat invested in as
    /// a whole). Positions sharing a group are shown and closed together.
    #[serde(default)]
    pub group_id: Option<String>,
    #[serde(default)]
    pub group_name: Option<String>,
    /// Weight inside the group (0..1), for display.
    #[serde(default)]
    pub group_weight: f64,

    /// Lifetime money in / out.
    #[serde(default)]
    pub contributed_usd: f64,
    #[serde(default)]
    pub withdrawn_usd: f64,

    #[serde(default)]
    pub risk: Risk,
    #[serde(default)]
    pub sleeve: Sleeve,
    #[serde(default)]
    pub flows: Vec<Flow>,
    #[serde(default)]
    pub events: Vec<Event>,

    pub created_ms: i64,
    #[serde(default)]
    pub updated_ms: i64,
    #[serde(default)]
    pub last_sync_ms: i64,
    /// Last thing that went wrong, surfaced in the UI verbatim.
    #[serde(default)]
    pub last_error: Option<String>,
    /// Engine backoff: don't touch this position before this timestamp.
    #[serde(default)]
    pub next_attempt_ms: i64,
    /// Consecutive failures — drives the backoff above.
    #[serde(default)]
    pub fail_streak: u32,
}

fn d_mode() -> Mode { Mode::Live }

impl Position {
    /// Net money the investor has in this position right now.
    pub fn net_contributed(&self) -> f64 {
        (self.contributed_usd - self.withdrawn_usd).max(0.0)
    }

    /// The number the sleeve is sized against: money in, compounded by
    /// *realized* PnL only. Unrealized is deliberately excluded — otherwise
    /// every tick of an open position would resize every target and the
    /// engine would chase its own tail.
    pub fn basis(&self) -> f64 {
        (self.net_contributed() + self.sleeve.realized_pnl).max(0.0)
    }

    pub fn is_trader(&self) -> bool { self.kind == Kind::Trader }

    /// Should the engine be putting on / adjusting exposure?
    pub fn tracking(&self) -> bool {
        self.kind == Kind::Trader && self.status == Status::Active
    }

    /// Should the engine be flattening?
    pub fn flattening(&self) -> bool {
        self.kind == Kind::Trader && self.status == Status::Closing
    }

    pub fn log(&mut self, kind: &str, text: impl Into<String>, now_ms: i64) {
        self.events.insert(0, Event { ts_ms: now_ms, kind: kind.into(), text: text.into() });
        self.events.truncate(EVENT_CAP);
    }

    pub fn add_flow(&mut self, dir: &str, amount: f64, note: &str, now_ms: i64) {
        self.flows.insert(0, Flow {
            ts_ms: now_ms, dir: dir.into(), amount_usd: amount, note: note.into(),
        });
        self.flows.truncate(EVENT_CAP);
    }
}

// ─── Valuation ──────────────────────────────────────────────────────────

/// What a position is worth, and how it got there. Computed on read (never
/// stored), so it can never go stale against the market.
#[derive(Debug, Clone, Serialize)]
pub struct Valuation {
    /// Current mark-to-market value of the investment, USD.
    pub equity: f64,
    /// equity − net contributed.
    pub pnl: f64,
    /// pnl / contributed, percent. 0 when nothing was contributed.
    pub roi_pct: f64,
    pub realized: f64,
    pub unrealized: f64,
    /// Gross notional the position is holding right now.
    pub exposure: f64,
    /// exposure / basis — the effective leverage being run.
    pub leverage: f64,
    /// Where the numbers come from, verbatim for the UI.
    pub basis_note: &'static str,
    /// True when this is HL's own accounting rather than ours.
    pub authoritative: bool,
}

/// Value a trader sleeve from our own ledger plus live marks.
pub fn value_sleeve(p: &Position, marks: &HashMap<String, f64>) -> Valuation {
    // `+ 0.0` is not decoration: Rust sums floats from an identity of -0.0,
    // so an empty sleeve otherwise renders as "-0.00x" everywhere.
    let unrealized = p.sleeve.unrealized(marks) + 0.0;
    let realized = p.sleeve.realized_pnl;
    let equity = p.net_contributed() + realized + unrealized;
    let exposure = p.sleeve.gross_notional(marks) + 0.0;
    let basis = p.basis();
    Valuation {
        equity,
        pnl: realized + unrealized,
        roi_pct: pct(realized + unrealized, p.contributed_usd),
        realized,
        unrealized,
        exposure,
        leverage: if basis > 0.0 { exposure / basis + 0.0 } else { 0.0 },
        basis_note: "mirrored fills, marked live — excludes fees and funding",
        authoritative: false,
    }
}

/// Value a vault position from HL's own `followerState` when we have it,
/// falling back to our flow ledger when the vault detail call failed.
pub fn value_vault(p: &Position, follower_state: Option<&serde_json::Value>) -> Valuation {
    let num = |v: &serde_json::Value, k: &str| -> f64 {
        v.get(k).and_then(|x| x.as_str()).and_then(|s| s.parse::<f64>().ok())
            .or_else(|| v.get(k).and_then(|x| x.as_f64()))
            .unwrap_or(0.0)
    };
    match follower_state {
        Some(fs) => {
            let equity = num(fs, "vaultEquity");
            let hl_pnl = num(fs, "pnl");
            Valuation {
                equity,
                pnl: hl_pnl,
                roi_pct: pct(hl_pnl, p.contributed_usd.max(equity - hl_pnl)),
                realized: 0.0,
                unrealized: hl_pnl,
                exposure: equity,
                leverage: 1.0,
                basis_note: "Hyperliquid's own vault accounting (followerState)",
                authoritative: true,
            }
        }
        None => Valuation {
            equity: p.net_contributed(),
            pnl: 0.0,
            roi_pct: 0.0,
            realized: 0.0,
            unrealized: 0.0,
            exposure: p.net_contributed(),
            leverage: 1.0,
            basis_note: "vault not reachable — showing money in, not value",
            authoritative: false,
        },
    }
}

fn pct(num: f64, den: f64) -> f64 {
    if den.abs() < 1e-9 { 0.0 } else { num / den * 100.0 }
}

// ─── Planning: leader portfolio → my targets ────────────────────────────

/// One of the leader's open positions, priced.
#[derive(Debug, Clone)]
pub struct LeaderPos {
    pub coin: String,
    /// Signed size, leader's units.
    pub size: f64,
    pub mark: f64,
}

/// What this sleeve should hold.
#[derive(Debug, Clone, PartialEq)]
pub struct TargetLeg {
    pub coin: String,
    pub size: f64,
    pub mark: f64,
}

/// The full sizing decision, kept as data so `/invest/preview` can show the
/// investor exactly what their money would do *before* they commit it.
#[derive(Debug, Clone)]
pub struct Plan {
    /// basis / leader equity — the fraction of the leader you are.
    pub scale: f64,
    pub targets: Vec<TargetLeg>,
    /// Gross notional the targets imply, after the leverage cap.
    pub gross: f64,
    /// Factor applied because the leader runs more leverage than allowed.
    /// 1.0 = untouched.
    pub deleverage: f64,
    /// Coins dropped by allow/deny.
    pub filtered: Vec<String>,
}

/// Scale a leader's portfolio down to this sleeve's money.
///
/// The rule is one line: **hold what they hold, times `basis / their equity`**,
/// then shrink everything proportionally if that would exceed the investor's
/// leverage ceiling. Proportional shrink (rather than dropping coins) keeps the
/// *shape* of the leader's book, which is the thing being bought.
pub fn plan(leader: &[LeaderPos], leader_equity: f64, basis: f64, risk: &Risk) -> Plan {
    let mut out = Plan { scale: 0.0, targets: Vec::new(), gross: 0.0, deleverage: 1.0, filtered: Vec::new() };
    if !(leader_equity > 0.0) || !(basis > 0.0) { return out; }
    out.scale = basis / leader_equity;

    let mut gross = 0.0;
    for lp in leader {
        if lp.size.abs() <= 0.0 || !(lp.mark > 0.0) { continue; }
        if !risk.allows(&lp.coin) { out.filtered.push(lp.coin.clone()); continue; }
        let size = lp.size * out.scale;
        gross += size.abs() * lp.mark;
        out.targets.push(TargetLeg { coin: lp.coin.clone(), size, mark: lp.mark });
    }

    let cap = basis * risk.max_leverage;
    if gross > cap && gross > 0.0 {
        out.deleverage = cap / gross;
        for t in out.targets.iter_mut() { t.size *= out.deleverage; }
        gross = cap;
    }
    out.gross = gross;
    out
}

/// An order the engine should send to move the sleeve toward its target.
#[derive(Debug, Clone, PartialEq)]
pub struct Intent {
    pub coin: String,
    /// Signed size to trade (positive = buy).
    pub delta: f64,
    pub target: f64,
    pub current: f64,
    pub mark: f64,
    pub notional: f64,
    /// Purely reducing an existing position — safe to send reduce-only, and
    /// never blocked by the minimum-order rule.
    pub reduce_only: bool,
    pub reason: &'static str,
}

/// Diff the sleeve against its targets.
///
/// Two rules that matter more than they look:
///   * **Exits are never gated.** A drift of $3 isn't worth a trade, but
///     *getting out* always is — a min-order rule that silently strands you in
///     a position the leader has already left is how copy-trading loses money.
///   * **Never cross zero in one order.** Flipping long→short in a single fill
///     would be rejected as reduce-only and mis-sized otherwise, so a flip is
///     expressed as "close now, re-open next cycle".
pub fn reconcile(
    sleeve: &Sleeve,
    targets: &[TargetLeg],
    marks: &HashMap<String, f64>,
    min_order_usd: f64,
    flatten: bool,
) -> Vec<Intent> {
    let mut want: HashMap<&str, &TargetLeg> = HashMap::new();
    if !flatten {
        for t in targets { want.insert(t.coin.as_str(), t); }
    }

    // Union of "coins I hold" and "coins I should hold", stable order.
    let mut coins: Vec<String> = sleeve.legs.keys().cloned().collect();
    for t in targets {
        if !coins.iter().any(|c| c == &t.coin) { coins.push(t.coin.clone()); }
    }

    let mut out = Vec::new();
    for coin in coins {
        let current = sleeve.legs.get(&coin).map(|l| l.size).unwrap_or(0.0);
        let target = want.get(coin.as_str()).map(|t| t.size).unwrap_or(0.0);
        if current == 0.0 && target == 0.0 { continue; }

        let mark = marks.get(&coin).copied()
            .or_else(|| want.get(coin.as_str()).map(|t| t.mark))
            .or_else(|| sleeve.legs.get(&coin).map(|l| l.avg_px))
            .unwrap_or(0.0);
        if !(mark > 0.0) { continue; }

        // A sign flip is done in two steps: this cycle closes, the next opens.
        let crosses_zero = current != 0.0 && target != 0.0 && current.signum() != target.signum();
        let effective_target = if crosses_zero { 0.0 } else { target };
        let delta = effective_target - current;
        if delta == 0.0 { continue; }

        let reducing = current != 0.0
            && (effective_target == 0.0
                || (effective_target.signum() == current.signum()
                    && effective_target.abs() < current.abs()));
        let notional = delta.abs() * mark;
        let exiting = effective_target == 0.0;

        if !exiting && notional < min_order_usd { continue; }

        out.push(Intent {
            coin,
            delta,
            target: effective_target,
            current,
            mark,
            notional,
            reduce_only: reducing,
            reason: if flatten { "exit" } else if exiting { "exit" } else { "track" },
        });
    }
    // Reductions first: they free margin for the additions in the same cycle.
    out.sort_by(|a, b| b.reduce_only.cmp(&a.reduce_only));
    out
}

/// Book a fill into a leg with average-cost accounting. Returns realized PnL.
///
/// Handles the three cases in one place so the engine never has to reason
/// about them: opening/adding (no PnL, blended entry), reducing (PnL on the
/// closed portion, entry untouched), and flipping (PnL on everything closed,
/// entry resets to the fill price for the remainder).
pub fn apply_fill(leg: &mut Leg, signed_size: f64, px: f64) -> f64 {
    if signed_size == 0.0 || !(px > 0.0) { return 0.0; }
    let old = leg.size;

    if old == 0.0 || old.signum() == signed_size.signum() {
        let total = old.abs() + signed_size.abs();
        leg.avg_px = if total > 0.0 {
            (old.abs() * leg.avg_px + signed_size.abs() * px) / total
        } else { px };
        leg.size = old + signed_size;
        return 0.0;
    }

    let closed = old.abs().min(signed_size.abs());
    // Long closed above entry = profit; short closed below entry = profit.
    let realized = closed * (px - leg.avg_px) * old.signum();
    let remaining = old + signed_size;
    if remaining == 0.0 || remaining.signum() == old.signum() {
        leg.size = remaining;
        if remaining == 0.0 { leg.avg_px = 0.0; }
    } else {
        // Flipped through zero — the residue is a brand-new position.
        leg.size = remaining;
        leg.avg_px = px;
    }
    realized
}

/// Apply a fill to a whole position (leg book-keeping + realized PnL + log).
pub fn book_fill(
    p: &mut Position,
    coin: &str,
    signed_size: f64,
    px: f64,
    reason: &str,
    live: bool,
    now_ms: i64,
) -> f64 {
    let leg = p.sleeve.legs.entry(coin.to_string()).or_default();
    let realized = apply_fill(leg, signed_size, px);
    if leg.size.abs() <= 1e-12 {
        p.sleeve.legs.remove(coin);
    }
    p.sleeve.realized_pnl += realized;
    p.sleeve.push_fill(SleeveFill {
        ts_ms: now_ms,
        coin: coin.to_string(),
        side: if signed_size > 0.0 { "buy".into() } else { "sell".into() },
        size: signed_size.abs(),
        price: px,
        notional: signed_size.abs() * px,
        realized,
        reason: reason.to_string(),
        live,
    });
    realized
}

/// Parse Hyperliquid's order response into (signed-agnostic size, avg price).
///
/// The exchange answers a filled IOC with
/// `response.data.statuses[0].filled = {totalSz, avgPx, oid}`, an unfilled one
/// with `resting`, and a rejected one with `error` — as a *200*. Treating the
/// HTTP status as the outcome is the classic way to book a trade that never
/// happened, so every branch is handled explicitly.
pub fn parse_order_fill(resp: &serde_json::Value) -> Result<(f64, f64), String> {
    if resp.get("status").and_then(|s| s.as_str()) == Some("err") {
        let msg = resp.get("response").and_then(|r| r.as_str()).unwrap_or("exchange error");
        return Err(msg.to_string());
    }
    let statuses = resp
        .get("response").and_then(|r| r.get("data")).and_then(|d| d.get("statuses"))
        .and_then(|s| s.as_array())
        .ok_or_else(|| format!("unexpected exchange response: {resp}"))?;
    let st = statuses.first().ok_or_else(|| "empty statuses".to_string())?;
    if let Some(e) = st.get("error").and_then(|e| e.as_str()) {
        return Err(e.to_string());
    }
    if let Some(f) = st.get("filled") {
        let sz = f.get("totalSz").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
        let px = f.get("avgPx").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
        if sz > 0.0 && px > 0.0 { return Ok((sz, px)); }
        return Err("filled with zero size".into());
    }
    if st.get("resting").is_some() {
        // IOC shouldn't rest; if it does, nothing was traded.
        return Err("order rested unfilled (no liquidity at the limit)".into());
    }
    Err(format!("no fill in response: {st}"))
}

// ─── Store ──────────────────────────────────────────────────────────────

/// File-backed book of positions. Its own file (`invest.json`), deliberately
/// separate from `state.json` — two writers on one file is how a config gets
/// clobbered, and this one is written by an engine loop.
pub struct InvestStore {
    path: PathBuf,
    inner: RwLock<Vec<Position>>,
}

impl InvestStore {
    pub fn load(dir: &str) -> Self {
        let path = Path::new(dir).join("invest.json");
        let inner = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str::<Vec<Position>>(&s).ok())
            .unwrap_or_default();
        Self { path, inner: RwLock::new(inner) }
    }

    fn flush(&self) {
        let snapshot = self.inner.read().clone();
        match serde_json::to_string_pretty(&snapshot) {
            Ok(j) => {
                // Write-then-rename: an engine tick and an HTTP write can land
                // together, and a half-written book would lose every position.
                let tmp = self.path.with_extension("json.tmp");
                if std::fs::write(&tmp, j).is_ok() {
                    let _ = std::fs::rename(&tmp, &self.path);
                }
            }
            Err(e) => tracing::warn!("invest store serialize failed: {e}"),
        }
    }

    pub fn all(&self) -> Vec<Position> { self.inner.read().clone() }

    pub fn list(&self, investor: &str) -> Vec<Position> {
        let inv = investor.to_lowercase();
        let mut v: Vec<Position> = self.inner.read().iter()
            .filter(|p| p.investor == inv)
            .cloned()
            .collect();
        v.sort_by(|a, b| b.created_ms.cmp(&a.created_ms));
        v
    }

    pub fn get(&self, id: &str) -> Option<Position> {
        self.inner.read().iter().find(|p| p.id == id).cloned()
    }

    pub fn insert(&self, p: Position) -> Position {
        self.inner.write().push(p.clone());
        self.flush();
        p
    }

    /// Mutate one position in place; the closure's return value is passed out.
    pub fn update<T>(&self, id: &str, f: impl FnOnce(&mut Position) -> T) -> Option<T> {
        let out = {
            let mut g = self.inner.write();
            let p = g.iter_mut().find(|p| p.id == id)?;
            let out = f(p);
            p.updated_ms = chrono::Utc::now().timestamp_millis();
            out
        };
        self.flush();
        Some(out)
    }

    /// Batch-mutate (used by the engine, one flush per cycle).
    pub fn update_many(&self, updates: Vec<Position>) {
        if updates.is_empty() { return; }
        {
            let mut g = self.inner.write();
            for u in updates {
                if let Some(slot) = g.iter_mut().find(|p| p.id == u.id) { *slot = u; }
            }
        }
        self.flush();
    }

    pub fn delete(&self, id: &str) -> bool {
        let removed = {
            let mut g = self.inner.write();
            let n = g.len();
            g.retain(|p| p.id != id);
            g.len() != n
        };
        if removed { self.flush(); }
        removed
    }

    pub fn count(&self) -> usize { self.inner.read().len() }

    /// Positions the engine has work for, cheapest filter first.
    pub fn engine_queue(&self, now_ms: i64) -> Vec<Position> {
        self.inner.read().iter()
            .filter(|p| p.is_trader()
                && matches!(p.status, Status::Active | Status::Closing)
                && p.next_attempt_ms <= now_ms)
            .cloned()
            .collect()
    }
}

// ─── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn marks(pairs: &[(&str, f64)]) -> HashMap<String, f64> {
        pairs.iter().map(|(c, p)| (c.to_string(), *p)).collect()
    }

    #[test]
    fn plan_scales_the_leader_to_my_money() {
        // Leader: $1M equity, 10 BTC at 100k = $1M gross (1× leverage).
        // Me: $1,000 → 0.1% of them → 0.01 BTC.
        let leader = vec![LeaderPos { coin: "BTC".into(), size: 10.0, mark: 100_000.0 }];
        let p = plan(&leader, 1_000_000.0, 1_000.0, &Risk::default());
        assert!((p.scale - 0.001).abs() < 1e-12);
        assert_eq!(p.targets.len(), 1);
        assert!((p.targets[0].size - 0.01).abs() < 1e-12);
        assert!((p.gross - 1_000.0).abs() < 1e-6);
        assert!((p.deleverage - 1.0).abs() < 1e-12);
    }

    #[test]
    fn leverage_cap_shrinks_proportionally_and_keeps_the_shape() {
        // Leader runs 4× ($4M gross on $1M). Default cap is 1×.
        let leader = vec![
            LeaderPos { coin: "BTC".into(), size: 30.0, mark: 100_000.0 },
            LeaderPos { coin: "ETH".into(), size: 250.0, mark: 4_000.0 },
        ];
        let p = plan(&leader, 1_000_000.0, 1_000.0, &Risk::default());
        assert!((p.gross - 1_000.0).abs() < 1e-6, "gross {} should be capped at basis", p.gross);
        assert!(p.deleverage < 1.0);
        // Shape preserved: BTC was 3/4 of the book, still is.
        let btc = p.targets.iter().find(|t| t.coin == "BTC").unwrap();
        let eth = p.targets.iter().find(|t| t.coin == "ETH").unwrap();
        let btc_n = btc.size * btc.mark;
        let eth_n = eth.size * eth.mark;
        assert!(((btc_n / (btc_n + eth_n)) - 0.75).abs() < 1e-9);
    }

    #[test]
    fn plan_respects_allow_and_deny() {
        let leader = vec![
            LeaderPos { coin: "BTC".into(), size: 1.0, mark: 100_000.0 },
            LeaderPos { coin: "DOGE".into(), size: 100.0, mark: 0.2 },
        ];
        let mut risk = Risk::default();
        risk.coins_deny = vec!["DOGE".into()];
        let p = plan(&leader, 1_000_000.0, 10_000.0, &risk);
        assert_eq!(p.targets.len(), 1);
        assert_eq!(p.filtered, vec!["DOGE".to_string()]);
    }

    #[test]
    fn plan_is_empty_without_a_priceable_leader() {
        let leader = vec![LeaderPos { coin: "BTC".into(), size: 1.0, mark: 100_000.0 }];
        assert!(plan(&leader, 0.0, 1_000.0, &Risk::default()).targets.is_empty());
        assert!(plan(&leader, 1_000_000.0, 0.0, &Risk::default()).targets.is_empty());
    }

    #[test]
    fn reconcile_opens_adjusts_and_skips_noise() {
        let mut s = Sleeve::default();
        let m = marks(&[("BTC", 100_000.0)]);
        // Nothing held, target 0.01 BTC ($1000) → one buy.
        let t = vec![TargetLeg { coin: "BTC".into(), size: 0.01, mark: 100_000.0 }];
        let out = reconcile(&s, &t, &m, 12.0, false);
        assert_eq!(out.len(), 1);
        assert!((out[0].delta - 0.01).abs() < 1e-12);
        assert!(!out[0].reduce_only);

        // Now hold it; a $5 drift is below the floor and must not trade.
        s.legs.insert("BTC".into(), Leg { size: 0.01, avg_px: 100_000.0 });
        let t2 = vec![TargetLeg { coin: "BTC".into(), size: 0.01005, mark: 100_000.0 }];
        assert!(reconcile(&s, &t2, &m, 12.0, false).is_empty());
    }

    #[test]
    fn exits_are_never_gated_by_the_minimum() {
        let mut s = Sleeve::default();
        // A $4 dust position the leader has fully exited.
        s.legs.insert("BTC".into(), Leg { size: 0.00004, avg_px: 100_000.0 });
        let m = marks(&[("BTC", 100_000.0)]);
        let out = reconcile(&s, &[], &m, 12.0, false);
        assert_eq!(out.len(), 1, "must still be able to get out");
        assert!(out[0].reduce_only);
        assert_eq!(out[0].target, 0.0);
        assert_eq!(out[0].reason, "exit");
    }

    #[test]
    fn flip_closes_before_it_reverses() {
        let mut s = Sleeve::default();
        s.legs.insert("ETH".into(), Leg { size: 1.0, avg_px: 4_000.0 });
        let m = marks(&[("ETH", 4_000.0)]);
        let t = vec![TargetLeg { coin: "ETH".into(), size: -1.0, mark: 4_000.0 }];
        let out = reconcile(&s, &t, &m, 12.0, false);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].target, 0.0, "close first, reverse next cycle");
        assert!((out[0].delta + 1.0).abs() < 1e-12);
        assert!(out[0].reduce_only);
    }

    #[test]
    fn flatten_ignores_targets_entirely() {
        let mut s = Sleeve::default();
        s.legs.insert("BTC".into(), Leg { size: 0.5, avg_px: 100_000.0 });
        let m = marks(&[("BTC", 100_000.0)]);
        let t = vec![TargetLeg { coin: "BTC".into(), size: 0.5, mark: 100_000.0 }];
        let out = reconcile(&s, &t, &m, 12.0, true);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].target, 0.0);
    }

    #[test]
    fn reductions_are_ordered_before_additions() {
        let mut s = Sleeve::default();
        s.legs.insert("BTC".into(), Leg { size: 1.0, avg_px: 100_000.0 });
        let m = marks(&[("BTC", 100_000.0), ("ETH", 4_000.0)]);
        let t = vec![
            TargetLeg { coin: "BTC".into(), size: 0.5, mark: 100_000.0 },
            TargetLeg { coin: "ETH".into(), size: 10.0, mark: 4_000.0 },
        ];
        let out = reconcile(&s, &t, &m, 12.0, false);
        assert_eq!(out.len(), 2);
        assert!(out[0].reduce_only, "sell BTC before buying ETH — it pays for it");
    }

    #[test]
    fn average_cost_accounting_books_pnl_on_the_way_out() {
        let mut leg = Leg::default();
        assert_eq!(apply_fill(&mut leg, 1.0, 100.0), 0.0);
        assert_eq!(apply_fill(&mut leg, 1.0, 200.0), 0.0);
        assert!((leg.avg_px - 150.0).abs() < 1e-9, "blended entry");
        assert!((leg.size - 2.0).abs() < 1e-9);
        // Sell one at 250 → +100 realized on the closed unit.
        let r = apply_fill(&mut leg, -1.0, 250.0);
        assert!((r - 100.0).abs() < 1e-9);
        assert!((leg.avg_px - 150.0).abs() < 1e-9, "entry untouched by a partial close");
        // Close the rest.
        let r2 = apply_fill(&mut leg, -1.0, 50.0);
        assert!((r2 + 100.0).abs() < 1e-9);
        assert_eq!(leg.size, 0.0);
        assert_eq!(leg.avg_px, 0.0);
    }

    #[test]
    fn shorts_profit_when_price_falls() {
        let mut leg = Leg::default();
        apply_fill(&mut leg, -2.0, 100.0);
        let r = apply_fill(&mut leg, 2.0, 90.0);
        assert!((r - 20.0).abs() < 1e-9);
    }

    #[test]
    fn a_flip_realizes_everything_and_re_enters_at_the_fill() {
        let mut leg = Leg::default();
        apply_fill(&mut leg, 1.0, 100.0);
        let r = apply_fill(&mut leg, -3.0, 120.0);
        assert!((r - 20.0).abs() < 1e-9, "only the closed unit books PnL");
        assert!((leg.size + 2.0).abs() < 1e-9);
        assert!((leg.avg_px - 120.0).abs() < 1e-9);
    }

    #[test]
    fn sleeve_valuation_adds_up() {
        let mut p = Position {
            id: "x".into(), investor: "0x1".into(), kind: Kind::Trader,
            target: "0x2".into(), name: "t".into(), status: Status::Active, mode: Mode::Paper,
            group_id: None, group_name: None, group_weight: 0.0,
            contributed_usd: 1_000.0, withdrawn_usd: 0.0,
            risk: Risk::default(), sleeve: Sleeve::default(), flows: vec![], events: vec![],
            created_ms: 0, updated_ms: 0, last_sync_ms: 0,
            last_error: None, next_attempt_ms: 0, fail_streak: 0,
        };
        book_fill(&mut p, "BTC", 0.01, 100_000.0, "track", false, 0);
        let v = value_sleeve(&p, &marks(&[("BTC", 110_000.0)]));
        assert!((v.unrealized - 100.0).abs() < 1e-6);
        assert!((v.equity - 1_100.0).abs() < 1e-6);
        assert!((v.roi_pct - 10.0).abs() < 1e-6);
        assert!((v.exposure - 1_100.0).abs() < 1e-6);
        // Realized gains raise the basis; unrealized ones do not.
        assert!((p.basis() - 1_000.0).abs() < 1e-9);
        book_fill(&mut p, "BTC", -0.01, 110_000.0, "exit", false, 0);
        assert!((p.basis() - 1_100.0).abs() < 1e-6);
    }

    #[test]
    fn order_response_parsing_covers_every_branch() {
        let filled = serde_json::json!({"status":"ok","response":{"type":"order","data":{
            "statuses":[{"filled":{"totalSz":"0.01","avgPx":"99000.0","oid":1}}]}}});
        assert_eq!(parse_order_fill(&filled).unwrap(), (0.01, 99_000.0));

        let rejected = serde_json::json!({"status":"ok","response":{"type":"order","data":{
            "statuses":[{"error":"Order must have minimum value of $10"}]}}});
        assert!(parse_order_fill(&rejected).unwrap_err().contains("minimum value"));

        let resting = serde_json::json!({"status":"ok","response":{"type":"order","data":{
            "statuses":[{"resting":{"oid":7}}]}}});
        assert!(parse_order_fill(&resting).is_err());

        let err = serde_json::json!({"status":"err","response":"User or API Wallet does not exist"});
        assert!(parse_order_fill(&err).unwrap_err().contains("does not exist"));

        assert!(parse_order_fill(&serde_json::json!({"nonsense": true})).is_err());
    }

    #[test]
    fn risk_sanitizes_out_of_range_input() {
        let r = Risk { max_leverage: 500.0, max_slippage_bps: 99_999, min_order_usd: 0.01,
                       coins_allow: vec![" btc ".into(), "".into()], coins_deny: vec![],
                       stop_loss_pct: -5.0 }.sanitized();
        assert_eq!(r.max_leverage, 10.0);
        assert_eq!(r.max_slippage_bps, 2_000);
        assert_eq!(r.min_order_usd, 10.0);
        assert_eq!(r.coins_allow, vec!["BTC".to_string()]);
        assert_eq!(r.stop_loss_pct, 0.0);
        assert!(r.allows("btc"));
        assert!(!r.allows("eth"));
    }
}
