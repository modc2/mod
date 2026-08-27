//! The fold: events in, world out.
//!
//! `State::apply` is the *only* function in the module that changes anything,
//! and it takes an already-written event. Validation happens before an event
//! exists (in `engine.rs`); by the time the fold sees one, its job is
//! arithmetic, not judgement. Keeping those apart is what makes replay
//! meaningful — replaying the log runs exactly the same arithmetic the live
//! server ran, with none of the live server's inputs.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::market;
use crate::types::{
    Addr, Attestation, Commitment, Event, ModelBook, ModelId, Money, Outcome, PendingEdge, Phase,
    Reveal, Round, RoundId, UsageReceipt,
};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct State {
    pub chain_id: String,
    pub owner: Option<Addr>,
    pub attestors: BTreeMap<Addr, String>,
    pub meters: BTreeMap<Addr, String>,
    /// The field the next round will open with.
    pub roster: Vec<ModelId>,
    pub balances: BTreeMap<Addr, Money>,
    pub locked: BTreeMap<Addr, Money>,
    pub treasury: Money,
    /// Everything ever credited into the system, including the treasury's own
    /// float. The conservation check below is stated against this.
    pub issued: Money,
    pub rounds: BTreeMap<RoundId, Round>,
    pub used_nonces: BTreeSet<(Addr, u64)>,
    pub receipts: BTreeMap<String, UsageReceipt>,
    /// Cumulative credits spent per model, ever. This is the clock the
    /// earliness curve runs on — not wall time, usage.
    pub model_credits: BTreeMap<ModelId, Money>,
    pub model_margin: BTreeMap<ModelId, Money>,
    pub pending_edge: Vec<PendingEdge>,
    /// Filled in only by a replay that disagrees with what was recorded.
    /// Non-empty means the log has been altered.
    pub divergences: Vec<String>,
}

impl State {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn balance(&self, addr: &str) -> Money {
        *self.balances.get(addr).unwrap_or(&0)
    }

    pub fn locked_of(&self, addr: &str) -> Money {
        *self.locked.get(addr).unwrap_or(&0)
    }

    pub fn is_owner(&self, addr: &str) -> bool {
        self.owner.as_deref() == Some(addr)
    }

    pub fn round(&self, id: &str) -> Option<&Round> {
        self.rounds.get(id)
    }

    /// The round taking bets at `now`, if there is one.
    pub fn open_round(&self, now: i64) -> Option<&Round> {
        self.rounds
            .values()
            .filter(|r| !r.phase.is_final() && now >= r.opens_at && now < r.reveal_at)
            .max_by_key(|r| r.opens_at)
    }

    /// The most recent round by id, whatever phase it is in.
    pub fn latest_round(&self) -> Option<&Round> {
        self.rounds.values().next_back()
    }

    fn credit(&mut self, addr: &str, amount: Money) {
        if amount == 0 {
            return;
        }
        *self.balances.entry(addr.to_string()).or_insert(0) += amount;
    }

    fn debit(&mut self, addr: &str, amount: Money) {
        let slot = self.balances.entry(addr.to_string()).or_insert(0);
        *slot = slot.saturating_sub(amount);
    }

    fn lock(&mut self, addr: &str, amount: Money) {
        *self.locked.entry(addr.to_string()).or_insert(0) += amount;
    }

    fn unlock(&mut self, addr: &str, amount: Money) {
        let slot = self.locked.entry(addr.to_string()).or_insert(0);
        *slot = slot.saturating_sub(amount);
    }

    /// Apply one event. Infallible by construction — see the module note.
    pub fn apply(&mut self, event: &Event) {
        match event {
            Event::Genesis { chain_id, owner, .. } => {
                self.chain_id = chain_id.clone();
                self.owner = Some(owner.clone());
            }
            Event::OwnerSet { owner, .. } => {
                self.owner = Some(owner.clone());
            }
            Event::AttestorRegistered { attestor, label, .. } => {
                self.attestors.insert(attestor.clone(), label.clone());
            }
            Event::AttestorRemoved { attestor, .. } => {
                self.attestors.remove(attestor);
            }
            Event::MeterRegistered { meter, label, .. } => {
                self.meters.insert(meter.clone(), label.clone());
            }
            Event::MeterRemoved { meter, .. } => {
                self.meters.remove(meter);
            }
            Event::RosterSet { models, .. } => {
                self.roster = models.clone();
            }
            Event::Credited { account, amount, .. } => {
                // The treasury is an account like any other, except that it
                // is the one the house's edge money comes out of.
                if account == "treasury" {
                    self.treasury += amount;
                } else {
                    self.credit(account, *amount);
                }
                self.issued += amount;
            }
            Event::RoundOpened {
                round,
                entrants,
                params,
                spec_hash,
                opens_at,
                reveal_at,
                seal_at,
                settle_at,
            } => {
                self.rounds.insert(
                    round.clone(),
                    Round {
                        id: round.clone(),
                        phase: Phase::Open,
                        entrants: entrants.clone(),
                        params: params.clone(),
                        spec_hash: spec_hash.clone(),
                        opens_at: *opens_at,
                        reveal_at: *reveal_at,
                        seal_at: *seal_at,
                        settle_at: *settle_at,
                        commitments: BTreeMap::new(),
                        books: BTreeMap::new(),
                        contributions: BTreeMap::new(),
                        forfeits: BTreeMap::new(),
                        edge_money: 0,
                        pool: 0,
                        attestations: BTreeMap::new(),
                        merkle_root: None,
                        result: None,
                    },
                );
                // Every opened round is a round an unplaced edge credit has
                // waited through.
                for pending in self.pending_edge.iter_mut() {
                    pending.rounds_waited += 1;
                }
            }
            Event::Committed { round, commitment, owner, amount, nonce, at, .. } => {
                self.used_nonces.insert((owner.clone(), *nonce));
                self.debit(owner, *amount);
                self.lock(owner, *amount);
                if let Some(r) = self.rounds.get_mut(round) {
                    r.commitments.insert(
                        commitment.clone(),
                        Commitment {
                            commitment: commitment.clone(),
                            owner: owner.clone(),
                            amount: *amount,
                            committed_at: *at,
                            revealed: None,
                            forfeited: false,
                        },
                    );
                }
            }
            Event::Revealed { round, commitment, owner, model, salt, at } => {
                let mut amount = 0;
                if let Some(r) = self.rounds.get_mut(round) {
                    if let Some(c) = r.commitments.get_mut(commitment) {
                        amount = c.amount;
                        c.revealed = Some(Reveal {
                            model: model.clone(),
                            salt: salt.clone(),
                            revealed_at: *at,
                        });
                    }
                    if amount > 0 {
                        let book = r.books.entry(model.clone()).or_default();
                        // A revealed bet mints claim units one-for-one.
                        // Only edge positions are weighted.
                        book.units += amount;
                        book.money += amount;
                        *book.holders.entry(owner.clone()).or_insert(0) += amount;
                        *r.contributions.entry(owner.clone()).or_insert(0) += amount;
                        r.pool += amount;
                    }
                }
                self.unlock(owner, amount);
            }
            Event::UsagePosted { receipt, margin, units, weight_num, weight_den, .. } => {
                self.receipts.insert(receipt.id.clone(), receipt.clone());
                *self.model_credits.entry(receipt.model.clone()).or_insert(0) += receipt.spend;
                *self.model_margin.entry(receipt.model.clone()).or_insert(0) += margin;
                if *units > 0 {
                    self.pending_edge.push(PendingEdge {
                        receipt_id: receipt.id.clone(),
                        user: receipt.user.clone(),
                        model: receipt.model.clone(),
                        margin: *margin,
                        weight_num: *weight_num,
                        weight_den: *weight_den,
                        units: *units,
                        earned_at: receipt.at,
                        rounds_waited: 0,
                    });
                }
            }
            Event::EdgeStaked { round, receipt_id, user, model, margin, units, .. } => {
                self.pending_edge.retain(|p| p.receipt_id != *receipt_id);
                self.treasury = self.treasury.saturating_sub(*margin);
                if let Some(r) = self.rounds.get_mut(round) {
                    let book = r.books.entry(model.clone()).or_default();
                    book.units += units;
                    book.money += margin;
                    book.edge_units += units;
                    *book.holders.entry(user.clone()).or_insert(0) += units;
                    r.edge_money += margin;
                    r.pool += margin;
                }
            }
            Event::EdgeExpired { receipt_id, .. } => {
                self.pending_edge.retain(|p| p.receipt_id != *receipt_id);
            }
            Event::TokenTransferred { round, model, from, to, units, nonce, .. } => {
                self.used_nonces.insert((from.clone(), *nonce));
                if let Some(r) = self.rounds.get_mut(round) {
                    let book = r.books.entry(model.clone()).or_default();
                    let slot = book.holders.entry(from.clone()).or_insert(0);
                    *slot = slot.saturating_sub(*units);
                    if *slot == 0 {
                        book.holders.remove(from);
                    }
                    *book.holders.entry(to.clone()).or_insert(0) += units;
                }
            }
            Event::RoundSealed { round, merkle_root, .. } => {
                // Collect the stakes of everyone who committed and then went
                // quiet. Their money joins the pool and mints nothing, which
                // is what makes staying quiet a bad idea.
                let mut forfeits: Vec<(Addr, Money)> = Vec::new();
                if let Some(r) = self.rounds.get_mut(round) {
                    r.phase = Phase::Sealed;
                    r.merkle_root = Some(merkle_root.clone());
                    for c in r.commitments.values_mut() {
                        if c.revealed.is_none() && !c.forfeited {
                            c.forfeited = true;
                            forfeits.push((c.owner.clone(), c.amount));
                        }
                    }
                    for (owner, amount) in &forfeits {
                        *r.forfeits.entry(owner.clone()).or_insert(0) += *amount;
                        r.pool += *amount;
                    }
                }
                for (owner, amount) in forfeits {
                    self.unlock(&owner, amount);
                }
            }
            Event::Attested { round, attestor, ranking, rank_hash, signature, counted, note, at } => {
                if let Some(r) = self.rounds.get_mut(round) {
                    r.attestations.insert(
                        attestor.clone(),
                        Attestation {
                            attestor: attestor.clone(),
                            ranking: ranking.clone(),
                            rank_hash: rank_hash.clone(),
                            signature: signature.clone(),
                            at: *at,
                            counted: *counted,
                            note: note.clone(),
                        },
                    );
                }
            }
            Event::RoundSettled { round, result, .. } => {
                // Recompute rather than trust. The event carries a result,
                // but the fold works out its own from the round it has been
                // building, and uses that. If the two differ, the log has
                // been edited since it was written and the divergence is
                // recorded where `/verify` will find it.
                let Some(r) = self.rounds.get(round) else { return };
                let recomputed = market::finalize(r);
                if recomputed.payouts != result.payouts
                    || recomputed.outcome != result.outcome
                    || recomputed.fee != result.fee
                    || recomputed.total_pool != result.total_pool
                {
                    self.divergences.push(format!(
                        "round {round}: recorded settlement does not match a replay of the round",
                    ));
                }
                let phase = if recomputed.outcome == Outcome::Void {
                    Phase::Voided
                } else {
                    Phase::Settled
                };
                let payouts = recomputed.payouts.clone();
                let house = recomputed.fee + recomputed.dust;
                if let Some(r) = self.rounds.get_mut(round) {
                    r.phase = phase;
                    r.result = Some(recomputed);
                }
                for (addr, amount) in payouts {
                    self.credit(&addr, amount);
                }
                self.treasury += house;
            }
        }
    }

    /// Every credit ever issued is in exactly one of: somebody's balance,
    /// somebody's locked stake, an unsettled pool, or the treasury.
    ///
    /// This is the invariant that makes the ledger a ledger. It is checked in
    /// the tests after every scenario and exposed at `/verify`, because a
    /// market that can quietly mint or lose money is not cheat-proof no
    /// matter how good its commitments are.
    pub fn conservation(&self) -> Result<Money, String> {
        let balances: Money = self.balances.values().sum();
        let locked: Money = self.locked.values().sum();
        let pools: Money = self
            .rounds
            .values()
            .filter(|r| !r.phase.is_final())
            .map(|r| r.pool)
            .sum();
        let total = balances + locked + pools + self.treasury;
        if total == self.issued {
            Ok(total)
        } else {
            Err(format!(
                "issued {} but accounted {} (balances {}, locked {}, pools {}, treasury {})",
                self.issued, total, balances, locked, pools, self.treasury
            ))
        }
    }

    /// Rebuild a whole world from a log.
    pub fn replay<'a>(events: impl Iterator<Item = &'a Event>) -> State {
        let mut state = State::new();
        for event in events {
            state.apply(event);
        }
        state
    }

    /// One address's positions across every round that has not paid out yet.
    pub fn positions_of(&self, addr: &str) -> Vec<(RoundId, ModelId, Money)> {
        let mut out = Vec::new();
        for round in self.rounds.values() {
            for (model, book) in &round.books {
                if let Some(units) = book.holders.get(addr) {
                    if *units > 0 {
                        out.push((round.id.clone(), model.clone(), *units));
                    }
                }
            }
        }
        out
    }

    pub fn book_of(&self, round: &str, model: &str) -> ModelBook {
        self.rounds
            .get(round)
            .map(|r| r.book(model))
            .unwrap_or_default()
    }
}
