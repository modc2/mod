//! Validation, the round clock, and the only writer.
//!
//! Every rule the market has is stated exactly once, here, as a refusal to
//! write an event. There is no second path — the HTTP layer parses and calls
//! into this, the CLI goes through HTTP, and the tests call the same methods
//! the routes do. A rule that is not enforced in this file is not enforced.

use std::collections::BTreeSet;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::chain::{Chain, ChainCheck, Entry};
use crate::crypto::{self, norm_addr};
use crate::market;
use crate::state::State;
use crate::types::{
    commitment_hash, rank_hash, spec_hash, Addr, Event, ModelId, Money, Phase, Round, RoundId,
    RoundParams, UsageReceipt, MICRO,
};

#[derive(Debug, thiserror::Error)]
pub enum EngineError {
    #[error("{0}")]
    Denied(String),
    #[error("{0}")]
    Invalid(String),
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    Conflict(String),
    #[error(transparent)]
    Io(#[from] anyhow::Error),
}

pub type Result<T> = std::result::Result<T, EngineError>;

/// Genesis owner when none was configured — a placeholder, not an account.
pub const ZERO_ADDRESS: &str = "0x0000000000000000000000000000000000000000";

fn denied(msg: impl Into<String>) -> EngineError {
    EngineError::Denied(msg.into())
}
fn invalid(msg: impl Into<String>) -> EngineError {
    EngineError::Invalid(msg.into())
}

/// How a day is cut up. The defaults are a real day; the tests and the dev
/// console compress it, which is why the boundaries are computed from these
/// numbers rather than hardcoded to midnight.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Schedule {
    /// Length of one round in seconds. 86400 = one round per UTC day.
    pub day_secs: i64,
    /// When commitments stop and reveals start, in basis points of the day.
    /// 7500 = 18:00 UTC.
    pub reveal_bps: i64,
    /// When reveals stop and grading starts. 9583 = 23:00 UTC.
    pub seal_bps: i64,
}

impl Default for Schedule {
    fn default() -> Self {
        Self { day_secs: 86_400, reveal_bps: 7_500, seal_bps: 9_583 }
    }
}

impl Schedule {
    pub fn from_env() -> Self {
        let mut s = Self::default();
        if let Ok(v) = std::env::var("PRERANK_DAY_SECONDS") {
            if let Ok(n) = v.parse::<i64>() {
                // The floor is low enough that the python suite can watch a
                // whole round open, seal and settle inside one test.
                if n >= 10 {
                    s.day_secs = n;
                }
            }
        }
        if let Ok(v) = std::env::var("PRERANK_REVEAL_BPS") {
            if let Ok(n) = v.parse::<i64>() {
                s.reveal_bps = n.clamp(1, 9_999);
            }
        }
        if let Ok(v) = std::env::var("PRERANK_SEAL_BPS") {
            if let Ok(n) = v.parse::<i64>() {
                s.seal_bps = n.clamp(2, 10_000);
            }
        }
        if s.seal_bps <= s.reveal_bps {
            s.seal_bps = (s.reveal_bps + 1).min(10_000);
        }
        s
    }

    /// The index of the round containing `now`.
    pub fn index(&self, now: i64) -> i64 {
        now.div_euclid(self.day_secs)
    }

    /// The id of round `index`. A real day gets its date; a compressed day
    /// gets an ordinal, because calling a four-minute round "2026-08-13"
    /// twenty times over would be a lie.
    pub fn id(&self, index: i64) -> RoundId {
        if self.day_secs == 86_400 {
            DateTime::<Utc>::from_timestamp(index * 86_400, 0)
                .map(|d| d.format("%Y-%m-%d").to_string())
                .unwrap_or_else(|| format!("r{index}"))
        } else {
            format!("r{index}")
        }
    }

    /// `(opens_at, reveal_at, seal_at, settle_at)` for a round index.
    pub fn bounds(&self, index: i64) -> (i64, i64, i64, i64) {
        let opens = index * self.day_secs;
        let reveal = opens + self.day_secs * self.reveal_bps / 10_000;
        let seal = opens + self.day_secs * self.seal_bps / 10_000;
        (opens, reveal, seal, opens + self.day_secs)
    }
}

/// What a caller presented to prove it is who it says it is.
#[derive(Debug, Clone)]
pub struct Caller {
    pub address: Addr,
    pub signature: String,
}

pub struct Engine {
    chain: Chain,
    state: State,
    pub schedule: Schedule,
    pub params: RoundParams,
    /// Local-development escape hatch: accept unsigned actions. Off unless
    /// `PRERANK_OPEN=1`, and reported by `/health` so it cannot be on in
    /// production without the console saying so.
    pub open_mode: bool,
    /// The largest round index that may be auto-opened. Rounds are not
    /// created ahead of time.
    max_entrants: usize,
}

impl Engine {
    pub fn new(chain: Chain, schedule: Schedule, params: RoundParams, open_mode: bool) -> Self {
        let state = State::replay(chain.events());
        Self { chain, state, schedule, params, open_mode, max_entrants: 12 }
    }

    pub fn in_memory() -> Self {
        Self::new(Chain::in_memory(), Schedule::default(), RoundParams::default(), true)
    }

    pub fn state(&self) -> &State {
        &self.state
    }

    pub fn chain(&self) -> &Chain {
        &self.chain
    }

    pub fn head(&self) -> String {
        self.chain.head()
    }

    fn write(&mut self, event: Event) -> Result<Entry> {
        let entry = self.chain.append(event).map_err(EngineError::Io)?;
        self.state.apply(&entry.event);
        Ok(entry)
    }

    /// Boot: make sure there is a genesis and an owner.
    pub fn bootstrap(&mut self, owner: &str, now: i64) -> Result<()> {
        if self.chain.is_empty() {
            let owner = norm_addr(owner);
            self.write(Event::Genesis {
                chain_id: format!("prerank-{}", &crypto::hash_fields(&["prerank", &owner])[..12]),
                owner,
                at: now,
            })?;
        }
        Ok(())
    }

    // ── identity ─────────────────────────────────────────────────────

    /// Check a signature unless we are in open mode. Open mode is the only
    /// way an unsigned action ever enters the log, and it is recorded as
    /// `"open-mode"` rather than as an empty signature so a reader can tell
    /// the difference between "unsigned" and "signature lost".
    fn authenticate(&self, caller: &Caller, message: &str) -> Result<String> {
        let claimed = norm_addr(&caller.address);
        if !crypto::is_address(&claimed) {
            return Err(invalid(format!("{claimed} is not an address")));
        }
        if self.open_mode && (caller.signature.is_empty() || caller.signature == "open-mode") {
            return Ok("open-mode".to_string());
        }
        let recovered = crypto::recover_signer(message, &caller.signature).map_err(invalid)?;
        if recovered != claimed {
            return Err(denied(format!(
                "signature is by {recovered}, not {claimed} — signed message was: {message}"
            )));
        }
        Ok(caller.signature.clone())
    }

    fn require_owner(&self, caller: &Caller, message: &str) -> Result<Addr> {
        let addr = norm_addr(&caller.address);
        self.authenticate(caller, message)?;
        if !self.state.is_owner(&addr) {
            return Err(denied("only the owner can do that"));
        }
        Ok(addr)
    }

    fn require_fresh_nonce(&self, addr: &str, nonce: u64) -> Result<()> {
        if self.state.used_nonces.contains(&(addr.to_string(), nonce)) {
            return Err(EngineError::Conflict(format!(
                "nonce {nonce} has already been used by {addr} — a signed action is good once"
            )));
        }
        Ok(())
    }

    // ── owner surface ────────────────────────────────────────────────

    pub fn set_owner(&mut self, caller: &Caller, owner: &str, now: i64) -> Result<Addr> {
        let owner = norm_addr(owner);
        let msg = format!("prerank:owner:{owner}");
        // An unclaimed chain can be claimed by whoever signs first; after
        // that only the sitting owner may hand it over. A chain booted with
        // no `PRERANK_OWNER` gets the zero address at genesis, and that
        // counts as unclaimed — otherwise the first start of a fresh market
        // would lock it under an address nobody holds the key to.
        if self.state.owner.as_deref().map_or(false, |o| o != ZERO_ADDRESS) {
            self.require_owner(caller, &msg)?;
        } else {
            self.authenticate(caller, &msg)?;
        }
        self.write(Event::OwnerSet { owner: owner.clone(), at: now })?;
        Ok(owner)
    }

    pub fn register_attestor(&mut self, caller: &Caller, who: &str, label: &str, now: i64) -> Result<Addr> {
        let who = norm_addr(who);
        self.require_owner(caller, &format!("prerank:attestor:add:{who}"))?;
        if !crypto::is_address(&who) {
            return Err(invalid("an attestor is an address"));
        }
        self.write(Event::AttestorRegistered { attestor: who.clone(), label: label.to_string(), at: now })?;
        Ok(who)
    }

    pub fn remove_attestor(&mut self, caller: &Caller, who: &str, now: i64) -> Result<()> {
        let who = norm_addr(who);
        self.require_owner(caller, &format!("prerank:attestor:rm:{who}"))?;
        self.write(Event::AttestorRemoved { attestor: who, at: now })?;
        Ok(())
    }

    pub fn register_meter(&mut self, caller: &Caller, who: &str, label: &str, now: i64) -> Result<Addr> {
        let who = norm_addr(who);
        self.require_owner(caller, &format!("prerank:meter:add:{who}"))?;
        if !crypto::is_address(&who) {
            return Err(invalid("a meter is an address"));
        }
        self.write(Event::MeterRegistered { meter: who.clone(), label: label.to_string(), at: now })?;
        Ok(who)
    }

    pub fn remove_meter(&mut self, caller: &Caller, who: &str, now: i64) -> Result<()> {
        let who = norm_addr(who);
        self.require_owner(caller, &format!("prerank:meter:rm:{who}"))?;
        self.write(Event::MeterRemoved { meter: who, at: now })?;
        Ok(())
    }

    pub fn set_roster(&mut self, caller: &Caller, models: Vec<ModelId>, now: i64) -> Result<Vec<ModelId>> {
        let mut models: Vec<ModelId> = models
            .into_iter()
            .map(|m| m.trim().to_string())
            .filter(|m| !m.is_empty())
            .collect();
        models.sort();
        models.dedup();
        self.require_owner(caller, &format!("prerank:roster:{}", models.join(",")))?;
        self.write(Event::RosterSet { models: models.clone(), at: now })?;
        Ok(models)
    }

    pub fn credit(&mut self, caller: &Caller, account: &str, amount: Money, memo: &str, now: i64) -> Result<Money> {
        let account = if account == "treasury" { "treasury".to_string() } else { norm_addr(account) };
        self.require_owner(caller, &format!("prerank:credit:{account}:{amount}"))?;
        if amount == 0 {
            return Err(invalid("nothing to credit"));
        }
        self.write(Event::Credited { account: account.clone(), amount, memo: memo.to_string(), at: now })?;
        Ok(if account == "treasury" { self.state.treasury } else { self.state.balance(&account) })
    }

    // ── the round clock ──────────────────────────────────────────────

    /// The field a round would open with right now.
    fn field(&self) -> Vec<ModelId> {
        if !self.state.roster.is_empty() {
            return self.state.roster.clone();
        }
        // No roster: rank whoever has actually been used. Ties broken by
        // name so the field is a function of the log and not of hash order.
        let mut by_credits: Vec<(&ModelId, &Money)> = self.state.model_credits.iter().collect();
        by_credits.sort_by(|a, b| b.1.cmp(a.1).then(a.0.cmp(b.0)));
        by_credits
            .into_iter()
            .take(self.max_entrants)
            .map(|(m, _)| m.clone())
            .collect()
    }

    /// Advance the world to `now`: seal what is past sealing, settle what is
    /// past grading, open the round `now` falls in.
    ///
    /// Idempotent and safe to call on every request, which is exactly how it
    /// is used — a market whose state depends on a background task having run
    /// is a market that is wrong every time the task is late.
    pub fn tick(&mut self, now: i64) -> Result<Vec<String>> {
        let mut done = Vec::new();

        let to_seal: Vec<RoundId> = self
            .state
            .rounds
            .values()
            .filter(|r| r.phase == Phase::Open && now >= r.seal_at)
            .map(|r| r.id.clone())
            .collect();
        for id in to_seal {
            self.seal(&id, now)?;
            done.push(format!("sealed {id}"));
        }

        let to_settle: Vec<RoundId> = self
            .state
            .rounds
            .values()
            .filter(|r| r.phase == Phase::Sealed && now >= r.settle_at)
            .map(|r| r.id.clone())
            .collect();
        for id in to_settle {
            let outcome = self.settle(&id, now)?;
            done.push(format!("settled {id} ({outcome})"));
        }

        let index = self.schedule.index(now);
        let id = self.schedule.id(index);
        if !self.state.rounds.contains_key(&id) {
            let entrants = self.field();
            if entrants.len() >= 2 {
                self.open_round(&id, index, entrants, now)?;
                done.push(format!("opened {id}"));
            }
        }
        Ok(done)
    }

    fn open_round(&mut self, id: &str, index: i64, entrants: Vec<ModelId>, now: i64) -> Result<()> {
        let (opens_at, reveal_at, seal_at, settle_at) = self.schedule.bounds(index);
        let params = self.params.clone();
        self.write(Event::RoundOpened {
            round: id.to_string(),
            spec_hash: spec_hash(id, &entrants, &params),
            entrants: entrants.clone(),
            params,
            opens_at,
            reveal_at,
            seal_at,
            settle_at,
        })?;
        self.place_pending_edge(id, now)?;
        Ok(())
    }

    /// Move earned edge credit into the round that just opened.
    ///
    /// Credit earned during round N lands in round N+1 or later — never in
    /// the round that was already taking bets when it was earned. Without
    /// that delay a user could watch a day's rank take shape and then buy in
    /// at the close with the house's own money.
    fn place_pending_edge(&mut self, round_id: &str, now: i64) -> Result<()> {
        let Some(round) = self.state.round(round_id).cloned() else { return Ok(()) };
        let entrants: BTreeSet<&str> = round.entrants.iter().map(|s| s.as_str()).collect();
        let cap = round.params.edge_cap;
        let ttl = round.params.edge_ttl_rounds;
        let pending = self.state.pending_edge.clone();

        let mut placed: std::collections::BTreeMap<(Addr, ModelId), Money> = Default::default();
        for edge in pending {
            if edge.rounds_waited > ttl {
                self.write(Event::EdgeExpired {
                    receipt_id: edge.receipt_id,
                    user: edge.user,
                    model: edge.model,
                    margin: edge.margin,
                    at: now,
                })?;
                continue;
            }
            if !entrants.contains(edge.model.as_str()) {
                continue; // wait for a round that lists this model
            }
            // The house funds edge positions out of the treasury; if the
            // treasury is short, the credit waits rather than being minted
            // out of nothing.
            if self.state.treasury < edge.margin {
                continue;
            }
            let slot = placed.entry((edge.user.clone(), edge.model.clone())).or_insert(0);
            let already = *slot;
            if already >= cap {
                continue;
            }
            let room = cap - already;
            let units = edge.units.min(room);
            if units == 0 {
                continue;
            }
            *slot = already + units;
            self.write(Event::EdgeStaked {
                round: round_id.to_string(),
                receipt_id: edge.receipt_id,
                user: edge.user,
                model: edge.model,
                margin: edge.margin,
                units,
                at: now,
            })?;
        }
        Ok(())
    }

    fn seal(&mut self, id: &str, now: i64) -> Result<()> {
        let Some(round) = self.state.round(id).cloned() else {
            return Err(EngineError::NotFound(format!("no round {id}")));
        };
        // Leaves in ascending commitment order — the one ordering everybody
        // can reproduce from the public commitment set.
        let leaves: Vec<String> = round.commitments.keys().cloned().collect();
        let root = crypto::merkle_root(&leaves);
        let forfeited: Money = round
            .commitments
            .values()
            .filter(|c| c.revealed.is_none())
            .map(|c| c.amount)
            .sum();
        self.write(Event::RoundSealed {
            round: id.to_string(),
            merkle_root: root,
            forfeited,
            pool: round.pool + forfeited,
            at: now,
        })?;
        Ok(())
    }

    fn settle(&mut self, id: &str, now: i64) -> Result<String> {
        let Some(round) = self.state.round(id).cloned() else {
            return Err(EngineError::NotFound(format!("no round {id}")));
        };
        let result = market::finalize(&round);
        let label = format!("{:?}", result.outcome).to_lowercase();
        self.write(Event::RoundSettled { round: id.to_string(), result, at: now })?;
        Ok(label)
    }

    /// Open the next round now, whatever the clock says. Owner only — used to
    /// run a demonstration day in a few minutes without waiting for one.
    pub fn force_round(&mut self, caller: &Caller, entrants: Option<Vec<ModelId>>, now: i64) -> Result<RoundId> {
        self.require_owner(caller, "prerank:round:force")?;
        self.tick(now)?;
        let index = self.schedule.index(now);
        let id = self.schedule.id(index);
        if self.state.rounds.contains_key(&id) {
            return Ok(id);
        }
        let entrants = entrants.unwrap_or_else(|| self.field());
        if entrants.len() < 2 {
            return Err(invalid("a round needs at least two entrants"));
        }
        self.open_round(&id, index, entrants, now)?;
        Ok(id)
    }

    // ── betting ──────────────────────────────────────────────────────

    /// The message a bettor signs to place a sealed bet. The model is not in
    /// it — that is the point — but the commitment is, so the signature
    /// covers the hidden choice without disclosing it.
    pub fn commit_message(round: &str, commitment: &str, amount: Money, nonce: u64) -> String {
        format!("prerank:commit:{round}:{commitment}:{amount}:{nonce}")
    }

    pub fn commit(
        &mut self,
        caller: &Caller,
        round_id: &str,
        commitment: &str,
        amount: Money,
        nonce: u64,
        now: i64,
    ) -> Result<String> {
        let owner = norm_addr(&caller.address);
        let commitment = commitment.trim().to_lowercase();
        if commitment.len() != 64 || !commitment.chars().all(|c| c.is_ascii_hexdigit()) {
            return Err(invalid("a commitment is a 64-character sha256 hex digest"));
        }
        let sig = self.authenticate(caller, &Self::commit_message(round_id, &commitment, amount, nonce))?;
        self.require_fresh_nonce(&owner, nonce)?;

        let Some(round) = self.state.round(round_id) else {
            return Err(EngineError::NotFound(format!("no round {round_id}")));
        };
        if round.phase_at(now) != Phase::Open {
            return Err(EngineError::Conflict(format!(
                "round {round_id} is {:?} — bets close at {}",
                round.phase_at(now),
                round.reveal_at
            )));
        }
        if amount < round.params.min_bet {
            return Err(invalid(format!("the minimum bet is {} µc", round.params.min_bet)));
        }
        if round.commitments.contains_key(&commitment) {
            return Err(EngineError::Conflict(
                "that commitment is already in this round — use a fresh salt".into(),
            ));
        }
        if self.state.balance(&owner) < amount {
            return Err(EngineError::Conflict(format!(
                "balance {} µc is short of {} µc",
                self.state.balance(&owner),
                amount
            )));
        }
        self.write(Event::Committed {
            round: round_id.to_string(),
            commitment: commitment.clone(),
            owner,
            amount,
            nonce,
            signature: sig,
            at: now,
        })?;
        Ok(commitment)
    }

    /// Open a commitment. No signature: knowing the salt *is* the proof, and
    /// requiring a second signature here would only mean a bettor who lost
    /// their key could be forced to forfeit.
    pub fn reveal(
        &mut self,
        round_id: &str,
        commitment: &str,
        model: &str,
        salt: &str,
        now: i64,
    ) -> Result<(Addr, Money)> {
        let commitment = commitment.trim().to_lowercase();
        let Some(round) = self.state.round(round_id).cloned() else {
            return Err(EngineError::NotFound(format!("no round {round_id}")));
        };
        let phase = round.phase_at(now);
        if phase != Phase::Reveal {
            return Err(EngineError::Conflict(format!(
                "round {round_id} is {phase:?} — reveals are open between {} and {}",
                round.reveal_at, round.seal_at
            )));
        }
        let Some(c) = round.commitments.get(&commitment) else {
            return Err(EngineError::NotFound("no such commitment in this round".into()));
        };
        if c.revealed.is_some() {
            return Err(EngineError::Conflict("already revealed".into()));
        }
        if !round.entrants.iter().any(|e| e == model) {
            return Err(invalid(format!("{model} is not in this round's field")));
        }
        // The whole integrity of the sealed bid is this one comparison.
        let expected = commitment_hash(round_id, &c.owner, model, c.amount, salt);
        if expected != commitment {
            return Err(denied(
                "that model and salt do not hash to this commitment — a sealed bet cannot be changed after the fact",
            ));
        }
        self.write(Event::Revealed {
            round: round_id.to_string(),
            commitment,
            owner: c.owner.clone(),
            model: model.to_string(),
            salt: salt.to_string(),
            at: now,
        })?;
        Ok((c.owner.clone(), c.amount))
    }

    // ── credits and the early-user edge ──────────────────────────────

    /// Record a metered call and turn the house's margin on it into a claim
    /// on the model, weighted by how early the call was.
    pub fn post_usage(&mut self, receipt: UsageReceipt, now: i64) -> Result<(Money, Money)> {
        let mut receipt = receipt;
        receipt.user = norm_addr(&receipt.user);
        receipt.meter = norm_addr(&receipt.meter);
        receipt.model = receipt.model.trim().to_string();
        if receipt.id.trim().is_empty() {
            return Err(invalid("a receipt needs an id"));
        }
        if receipt.model.is_empty() {
            return Err(invalid("a receipt needs a model"));
        }
        if self.state.receipts.contains_key(&receipt.id) {
            return Err(EngineError::Conflict(format!(
                "receipt {} has already been posted",
                receipt.id
            )));
        }
        if !self.state.meters.contains_key(&receipt.meter) {
            return Err(denied(format!("{} is not a registered meter", receipt.meter)));
        }
        // Margin, not spend. A user can never get back more claim than the
        // house made on them, so buying usage to farm position is always a
        // net loss unless the model actually wins.
        if receipt.cost > receipt.spend {
            return Err(invalid("cost cannot exceed spend"));
        }
        let meter_caller = Caller { address: receipt.meter.clone(), signature: receipt.signature.clone() };
        self.authenticate(&meter_caller, &receipt.message())?;

        let margin = receipt.margin();
        let before = *self.state.model_credits.get(&receipt.model).unwrap_or(&0);
        let (units, num, den) = market::earliness_units(margin, before, self.params.earliness_k);
        self.write(Event::UsagePosted {
            receipt,
            margin,
            model_credits_before: before,
            weight_num: num,
            weight_den: den,
            units,
        })?;
        let _ = now;
        Ok((margin, units))
    }

    // ── the round's temporary token ──────────────────────────────────

    pub fn transfer_message(round: &str, model: &str, to: &str, units: Money, nonce: u64) -> String {
        format!("prerank:transfer:{round}:{model}:{}:{units}:{nonce}", norm_addr(to))
    }

    /// Hand some of a round's model token to somebody else.
    ///
    /// Only once the round is sealed. During the open window a transfer would
    /// disclose which model the sender is holding, which is precisely what
    /// the commitment is hiding; letting tokens trade before the reveal would
    /// unseal the market through the side door.
    pub fn transfer(
        &mut self,
        caller: &Caller,
        round_id: &str,
        model: &str,
        to: &str,
        units: Money,
        nonce: u64,
        now: i64,
    ) -> Result<Money> {
        let from = norm_addr(&caller.address);
        let to = norm_addr(to);
        let sig = self.authenticate(caller, &Self::transfer_message(round_id, model, &to, units, nonce))?;
        self.require_fresh_nonce(&from, nonce)?;
        if !crypto::is_address(&to) {
            return Err(invalid("the recipient is not an address"));
        }
        if to == from {
            return Err(invalid("that is already where it is"));
        }
        if units == 0 {
            return Err(invalid("nothing to transfer"));
        }
        let Some(round) = self.state.round(round_id) else {
            return Err(EngineError::NotFound(format!("no round {round_id}")));
        };
        if round.phase_at(now) != Phase::Sealed {
            return Err(EngineError::Conflict(
                "the round token trades only between the seal and the settlement".into(),
            ));
        }
        let held = round.book(model).holders.get(&from).copied().unwrap_or(0);
        if held < units {
            return Err(EngineError::Conflict(format!("you hold {held} units of {model}, not {units}")));
        }
        self.write(Event::TokenTransferred {
            round: round_id.to_string(),
            model: model.to_string(),
            from: from.clone(),
            to,
            units,
            nonce,
            signature: sig,
            at: now,
        })?;
        Ok(self.state.book_of(round_id, model).holders.get(&from).copied().unwrap_or(0))
    }

    // ── grading ──────────────────────────────────────────────────────

    pub fn attest_message(round: &str, hash: &str) -> String {
        format!("prerank:attest:{round}:{hash}")
    }

    /// Submit a ranking for a sealed round.
    ///
    /// A grader who holds a position in the round it is grading is recorded
    /// and then ignored: the attestation stays in the log so the conflict is
    /// visible, but it never counts toward quorum.
    pub fn attest(
        &mut self,
        caller: &Caller,
        round_id: &str,
        ranking: Vec<ModelId>,
        now: i64,
    ) -> Result<bool> {
        let attestor = norm_addr(&caller.address);
        let hash = rank_hash(round_id, &ranking);
        let sig = self.authenticate(caller, &Self::attest_message(round_id, &hash))?;
        if !self.state.attestors.contains_key(&attestor) {
            return Err(denied(format!("{attestor} is not a registered grader")));
        }
        let Some(round) = self.state.round(round_id).cloned() else {
            return Err(EngineError::NotFound(format!("no round {round_id}")));
        };
        if round.phase.is_final() {
            return Err(EngineError::Conflict(format!("round {round_id} is already {:?}", round.phase)));
        }
        if now < round.seal_at {
            return Err(EngineError::Conflict("grading opens when the round seals".into()));
        }
        if now >= round.settle_at {
            return Err(EngineError::Conflict("grading for this round has closed".into()));
        }
        // A ranking has to be the field, in some order. Anything else is a
        // different question than the one the round asked.
        let field: BTreeSet<&str> = round.entrants.iter().map(|s| s.as_str()).collect();
        let given: BTreeSet<&str> = ranking.iter().map(|s| s.as_str()).collect();
        if given.len() != ranking.len() {
            return Err(invalid("a ranking cannot list a model twice"));
        }
        if given != field {
            return Err(invalid("a ranking must order exactly this round's field"));
        }
        if round.attestations.contains_key(&attestor) {
            return Err(EngineError::Conflict("you have already graded this round".into()));
        }

        let holds = round
            .books
            .values()
            .any(|b| b.holders.get(&attestor).copied().unwrap_or(0) > 0)
            || round.commitments.values().any(|c| c.owner == attestor);
        let note = holds.then(|| "grader holds a position in this round — not counted".to_string());
        self.write(Event::Attested {
            round: round_id.to_string(),
            attestor,
            ranking,
            rank_hash: hash,
            signature: sig,
            counted: !holds,
            note,
            at: now,
        })?;
        Ok(!holds)
    }

    // ── verification ─────────────────────────────────────────────────

    /// Everything an outsider would check, checked from the inside.
    pub fn verify(&self) -> Verification {
        let chain = self.chain.check();
        let replayed = State::replay(self.chain.events());
        let live = serde_json::to_value(&self.state).unwrap_or_default();
        let replay_value = serde_json::to_value(&replayed).unwrap_or_default();
        let mut problems: Vec<String> = replayed.divergences.clone();
        if !chain.ok {
            problems.push(chain.error.clone().unwrap_or_else(|| "the chain does not link".into()));
        }
        if live != replay_value {
            problems.push("the running state is not the fold of the log".into());
        }
        let conservation = match replayed.conservation() {
            Ok(total) => Some(total),
            Err(e) => {
                problems.push(format!("credits are not conserved: {e}"));
                None
            }
        };
        // Re-derive every sealed round's Merkle root from its commitments.
        for round in replayed.rounds.values() {
            if let Some(root) = &round.merkle_root {
                let leaves: Vec<String> = round.commitments.keys().cloned().collect();
                if crypto::merkle_root(&leaves) != *root {
                    problems.push(format!("round {}: the sealed Merkle root does not match its commitments", round.id));
                }
            }
        }
        Verification {
            ok: problems.is_empty(),
            chain,
            events: self.chain.len(),
            rounds: replayed.rounds.len(),
            conserved: conservation,
            issued: replayed.issued,
            problems,
        }
    }

    /// The inclusion proof for one commitment against its round's sealed root.
    pub fn inclusion_proof(&self, round_id: &str, commitment: &str) -> Result<InclusionProof> {
        let Some(round) = self.state.round(round_id) else {
            return Err(EngineError::NotFound(format!("no round {round_id}")));
        };
        let leaves: Vec<String> = round.commitments.keys().cloned().collect();
        let commitment = commitment.trim().to_lowercase();
        let Some(index) = leaves.iter().position(|l| *l == commitment) else {
            return Err(EngineError::NotFound("that commitment is not in this round".into()));
        };
        let path = crypto::merkle_proof(&leaves, index);
        let root = round.merkle_root.clone().unwrap_or_else(|| crypto::merkle_root(&leaves));
        Ok(InclusionProof {
            round: round_id.to_string(),
            commitment: commitment.clone(),
            index,
            leaves: leaves.len(),
            root: root.clone(),
            sealed: round.merkle_root.is_some(),
            verifies: crypto::merkle_verify(&commitment, &path, &root),
            path: path.into_iter().map(|(h, left)| ProofStep { sibling: h, sibling_is_left: left }).collect(),
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Verification {
    pub ok: bool,
    pub chain: ChainCheck,
    pub events: u64,
    pub rounds: usize,
    /// Total credits accounted for, when they add up.
    pub conserved: Option<Money>,
    pub issued: Money,
    pub problems: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProofStep {
    pub sibling: String,
    pub sibling_is_left: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InclusionProof {
    pub round: RoundId,
    pub commitment: String,
    pub index: usize,
    pub leaves: usize,
    pub root: String,
    pub sealed: bool,
    pub verifies: bool,
    pub path: Vec<ProofStep>,
}

/// A convenience for clients that would rather the server picked the salt.
/// Returned once, never stored: if you lose it, your bet forfeits at seal.
pub fn fresh_salt() -> String {
    use rand::Rng;
    let bytes: [u8; 16] = rand::thread_rng().gen();
    hex::encode(bytes)
}

/// A human summary of a round, for the API and the console.
pub fn round_view(round: &Round, now: i64) -> serde_json::Value {
    let phase = round.phase_at(now);
    let mut books: Vec<serde_json::Value> = Vec::new();
    let sealed_or_later = phase != Phase::Open;
    for model in &round.entrants {
        let b = round.book(model);
        books.push(serde_json::json!({
            "model": model,
            // Before the reveal the per-model pools are the secret the round
            // is keeping. The count of sealed bets is public; their direction
            // is not.
            "units": if sealed_or_later { Some(b.units) } else { None },
            "money": if sealed_or_later { Some(b.money) } else { None },
            "edge_units": if sealed_or_later { Some(b.edge_units) } else { None },
            "holders": if sealed_or_later { Some(b.holders.len()) } else { None },
            "implied_odds": if sealed_or_later && b.units > 0 && round.pool > 0 {
                Some(round.pool as f64 / b.units as f64)
            } else { None },
        }));
    }
    serde_json::json!({
        "id": round.id,
        "phase": phase,
        "entrants": round.entrants,
        "spec_hash": round.spec_hash,
        "opens_at": round.opens_at,
        "reveal_at": round.reveal_at,
        "seal_at": round.seal_at,
        "settle_at": round.settle_at,
        "params": round.params,
        "commitments": round.commitments.len(),
        "revealed": round.commitments.values().filter(|c| c.revealed.is_some()).count(),
        "forfeited": round.commitments.values().filter(|c| c.forfeited).count(),
        "pool": if sealed_or_later { Some(round.pool) } else { None },
        "staked_visible": round.commitments.values().map(|c| c.amount).sum::<Money>(),
        "edge_money": if sealed_or_later { Some(round.edge_money) } else { None },
        "books": books,
        "merkle_root": round.merkle_root,
        "attestations": round.attestations.values().map(|a| serde_json::json!({
            "attestor": a.attestor,
            "rank_hash": a.rank_hash,
            "counted": a.counted,
            "note": a.note,
            "at": a.at,
            // The ranking itself stays sealed until the round settles, so a
            // second grader cannot simply copy the first one's answer.
            "ranking": if round.phase.is_final() { Some(a.ranking.clone()) } else { None },
        })).collect::<Vec<_>>(),
        "result": round.result,
        "quorum": round.params.quorum,
        "micro": MICRO,
    })
}
