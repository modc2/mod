//! The domain: money, rounds, positions, and the event that is the only way
//! any of them ever changes.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

/// Micro-credits. Every amount in this module is an integer count of these;
/// there is no floating point anywhere on the money path, because a market
/// that has to be recomputable by a stranger cannot afford to be
/// approximately recomputable.
pub type Money = u128;

pub const MICRO: Money = 1_000_000;

pub type Addr = String;
pub type ModelId = String;
pub type RoundId = String;

/// Where a round is in its day.
///
/// The phases exist to make the market sealed-bid: during `Open` a bet is a
/// hash, so the pools cannot be read; during `Reveal` those hashes are opened
/// and the pools become public; after `Seal` nothing about the round's stakes
/// can change and the graders speak.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    /// Accepting commitments. Amounts public, models hidden.
    Open,
    /// Accepting reveals only. No new money enters the round.
    Reveal,
    /// Pools frozen and published; graders attest; tokens may trade.
    Sealed,
    /// Rank agreed by quorum, payouts made.
    Settled,
    /// The graders disagreed, or nobody graded. Everything refunded.
    Voided,
}

impl Phase {
    pub fn is_final(&self) -> bool {
        matches!(self, Phase::Settled | Phase::Voided)
    }
}

/// The knobs a round is opened with. They are hashed into the round's
/// `spec_hash` at open and never read from anywhere else afterwards, so a
/// mid-round change to the server's config cannot change a round in flight.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoundParams {
    /// House cut of the distributable pool, in basis points.
    pub fee_bps: u64,
    /// How many independent graders must agree before a round can settle.
    pub quorum: usize,
    /// The earliness constant K, in micro-credits: a model that has already
    /// absorbed K credits gives half-weight edge positions. Small K = a
    /// steep early-adopter premium.
    pub earliness_k: Money,
    /// Ceiling on the edge position one address can be granted per model
    /// per round, so that a whale's spend cannot own a round outright.
    pub edge_cap: Money,
    /// How many rounds an unplaced edge credit waits for its model to be
    /// listed before it expires.
    pub edge_ttl_rounds: u64,
    /// The minimum a single commitment may lock.
    pub min_bet: Money,
}

impl Default for RoundParams {
    fn default() -> Self {
        Self {
            fee_bps: 200,
            quorum: 2,
            earliness_k: 100 * MICRO,
            edge_cap: 50 * MICRO,
            edge_ttl_rounds: 7,
            min_bet: MICRO / 100,
        }
    }
}

/// A sealed bet. `amount` is public from the moment it is placed — the money
/// is locked, so it has to be — but which model it backs is only known once
/// `revealed` is filled in.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Commitment {
    pub commitment: String,
    pub owner: Addr,
    pub amount: Money,
    pub committed_at: i64,
    pub revealed: Option<Reveal>,
    /// Set at seal for commitments that were never opened: the stake is
    /// forfeit to the pool.
    pub forfeited: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Reveal {
    pub model: ModelId,
    pub salt: String,
    pub revealed_at: i64,
}

/// One model's line in a round.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ModelBook {
    /// Claim units outstanding. Units are what payouts divide by.
    pub units: Money,
    /// Money that entered the pool on this model's account.
    pub money: Money,
    /// The slice of `units` that came from credit-margin edge staking.
    pub edge_units: Money,
    /// Who holds the round's temporary token for this model, and how much.
    pub holders: BTreeMap<Addr, Money>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Round {
    pub id: RoundId,
    pub phase: Phase,
    /// The field, fixed at open. A model that is not here cannot be bet on,
    /// cannot receive edge credit, and cannot appear in a ranking.
    pub entrants: Vec<ModelId>,
    pub params: RoundParams,
    /// `sha256` over the id, the entrants and the params — the promise that
    /// none of the three moved after bets were taken.
    pub spec_hash: String,
    pub opens_at: i64,
    pub reveal_at: i64,
    pub seal_at: i64,
    /// When grading closes. Quorum is counted once, here, so that a late
    /// contradicting grader can never be outrun by an early settlement.
    pub settle_at: i64,
    pub commitments: BTreeMap<String, Commitment>,
    pub books: BTreeMap<ModelId, ModelBook>,
    /// Money each address put in, for refunds. Edge money is the house's and
    /// is refunded to the treasury, so it is not in here.
    pub contributions: BTreeMap<Addr, Money>,
    /// Forfeited-at-seal stake, by address. Kept separate from
    /// `contributions` because a void refunds it and a settlement does not.
    pub forfeits: BTreeMap<Addr, Money>,
    pub edge_money: Money,
    pub pool: Money,
    pub attestations: BTreeMap<Addr, Attestation>,
    pub merkle_root: Option<String>,
    pub result: Option<RoundResult>,
}

impl Round {
    pub fn book(&self, model: &str) -> ModelBook {
        self.books.get(model).cloned().unwrap_or_default()
    }

    /// The phase this round *should* be in at `now`, ignoring settlement —
    /// a pure function of the timestamps sealed in at open, so any client can
    /// compute it without asking.
    pub fn phase_at(&self, now: i64) -> Phase {
        if self.phase.is_final() {
            return self.phase;
        }
        if now < self.reveal_at {
            Phase::Open
        } else if now < self.seal_at {
            Phase::Reveal
        } else {
            Phase::Sealed
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Attestation {
    pub attestor: Addr,
    pub ranking: Vec<ModelId>,
    pub rank_hash: String,
    pub signature: String,
    pub at: i64,
    /// A grader holding a position in the round it grades is recorded and
    /// then ignored. Conflicted attestations never count toward quorum.
    pub counted: bool,
    pub note: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoundResult {
    pub outcome: Outcome,
    pub winner: Option<ModelId>,
    pub ranking: Vec<ModelId>,
    pub rank_hash: Option<String>,
    pub votes: usize,
    pub total_pool: Money,
    pub winning_units: Money,
    pub fee: Money,
    pub dust: Money,
    pub payouts: BTreeMap<Addr, Money>,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    /// A quorum agreed and somebody held the winner.
    Paid,
    /// A quorum agreed but nobody backed the winning model; stakes returned.
    NoWinner,
    /// No quorum, or two quorums. Everything refunded, including forfeits.
    Void,
}

/// A metered use of a model, signed by a registered meter.
///
/// `cost` is what the model actually cost to run and `spend` is what the user
/// paid; the difference is the house's margin on that call, and the margin is
/// the only thing that ever becomes an edge position. That is the whole
/// anti-farming argument: the most a user can get back by pumping usage is
/// strictly less than what they paid for it.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageReceipt {
    pub id: String,
    pub user: Addr,
    pub model: ModelId,
    pub spend: Money,
    pub cost: Money,
    pub at: i64,
    pub meter: Addr,
    pub signature: String,
}

impl UsageReceipt {
    /// The exact string a meter signs. Includes the receipt id, so the same
    /// signature cannot be re-presented for a different amount.
    pub fn message(&self) -> String {
        format!(
            "prerank:usage:{}:{}:{}:{}:{}:{}",
            self.id, self.user, self.model, self.spend, self.cost, self.at
        )
    }

    pub fn margin(&self) -> Money {
        self.spend.saturating_sub(self.cost)
    }
}

/// Edge credit that has been earned but has no round to sit in yet.
///
/// Usage on day D funds a position in a round that opens *after* it — never
/// the round that is already taking bets. Without that, a user could watch a
/// rank form over the day and buy in at the end with money the house gives
/// back, which is the one free lunch this design cannot allow.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingEdge {
    pub receipt_id: String,
    pub user: Addr,
    pub model: ModelId,
    pub margin: Money,
    /// The weight numerator/denominator, computed from the model's cumulative
    /// credits at the moment of use and frozen there. Being early is a fact
    /// about when you paid, so it cannot be re-derived later.
    pub weight_num: Money,
    pub weight_den: Money,
    pub units: Money,
    pub earned_at: i64,
    pub rounds_waited: u64,
}

/// Everything that has ever happened, in the only vocabulary the state knows.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    Genesis {
        chain_id: String,
        owner: Addr,
        at: i64,
    },
    OwnerSet {
        owner: Addr,
        at: i64,
    },
    AttestorRegistered {
        attestor: Addr,
        label: String,
        at: i64,
    },
    AttestorRemoved {
        attestor: Addr,
        at: i64,
    },
    MeterRegistered {
        meter: Addr,
        label: String,
        at: i64,
    },
    MeterRemoved {
        meter: Addr,
        at: i64,
    },
    /// The standing field. A round opens with the roster as it stands at
    /// that moment and then ignores it, so changing the roster mid-round
    /// cannot change a round that is already taking bets.
    RosterSet {
        models: Vec<ModelId>,
        at: i64,
    },
    Credited {
        account: Addr,
        amount: Money,
        memo: String,
        at: i64,
    },
    RoundOpened {
        round: RoundId,
        entrants: Vec<ModelId>,
        params: RoundParams,
        spec_hash: String,
        opens_at: i64,
        reveal_at: i64,
        seal_at: i64,
        settle_at: i64,
    },
    Committed {
        round: RoundId,
        commitment: String,
        owner: Addr,
        amount: Money,
        nonce: u64,
        signature: String,
        at: i64,
    },
    Revealed {
        round: RoundId,
        commitment: String,
        owner: Addr,
        model: ModelId,
        salt: String,
        at: i64,
    },
    UsagePosted {
        receipt: UsageReceipt,
        margin: Money,
        model_credits_before: Money,
        weight_num: Money,
        weight_den: Money,
        units: Money,
    },
    EdgeStaked {
        round: RoundId,
        receipt_id: String,
        user: Addr,
        model: ModelId,
        margin: Money,
        units: Money,
        at: i64,
    },
    EdgeExpired {
        receipt_id: String,
        user: Addr,
        model: ModelId,
        margin: Money,
        at: i64,
    },
    TokenTransferred {
        round: RoundId,
        model: ModelId,
        from: Addr,
        to: Addr,
        units: Money,
        nonce: u64,
        signature: String,
        at: i64,
    },
    RoundSealed {
        round: RoundId,
        merkle_root: String,
        forfeited: Money,
        pool: Money,
        at: i64,
    },
    Attested {
        round: RoundId,
        attestor: Addr,
        ranking: Vec<ModelId>,
        rank_hash: String,
        signature: String,
        counted: bool,
        note: Option<String>,
        at: i64,
    },
    RoundSettled {
        round: RoundId,
        result: RoundResult,
        at: i64,
    },
}

impl Event {
    pub fn kind(&self) -> &'static str {
        match self {
            Event::Genesis { .. } => "genesis",
            Event::OwnerSet { .. } => "owner_set",
            Event::AttestorRegistered { .. } => "attestor_registered",
            Event::AttestorRemoved { .. } => "attestor_removed",
            Event::MeterRegistered { .. } => "meter_registered",
            Event::MeterRemoved { .. } => "meter_removed",
            Event::RosterSet { .. } => "roster_set",
            Event::Credited { .. } => "credited",
            Event::RoundOpened { .. } => "round_opened",
            Event::Committed { .. } => "committed",
            Event::Revealed { .. } => "revealed",
            Event::UsagePosted { .. } => "usage_posted",
            Event::EdgeStaked { .. } => "edge_staked",
            Event::EdgeExpired { .. } => "edge_expired",
            Event::TokenTransferred { .. } => "token_transferred",
            Event::RoundSealed { .. } => "round_sealed",
            Event::Attested { .. } => "attested",
            Event::RoundSettled { .. } => "round_settled",
        }
    }

    pub fn round(&self) -> Option<&str> {
        match self {
            Event::RoundOpened { round, .. }
            | Event::Committed { round, .. }
            | Event::Revealed { round, .. }
            | Event::EdgeStaked { round, .. }
            | Event::TokenTransferred { round, .. }
            | Event::RoundSealed { round, .. }
            | Event::Attested { round, .. }
            | Event::RoundSettled { round, .. } => Some(round),
            _ => None,
        }
    }
}

/// The commitment a bettor hashes at home and the market checks at reveal.
pub fn commitment_hash(
    round: &str,
    owner: &str,
    model: &str,
    amount: Money,
    salt: &str,
) -> String {
    crate::crypto::hash_fields(&[
        "prerank:bet",
        round,
        &crate::crypto::norm_addr(owner),
        model,
        &amount.to_string(),
        salt,
    ])
}

/// What a ranking hashes to. Two graders agree when these match, and only
/// then; the order is the whole content, so a permutation is a disagreement.
pub fn rank_hash(round: &str, ranking: &[ModelId]) -> String {
    crate::crypto::hash_fields(&["prerank:rank", round, &ranking.join(">")])
}

#[cfg(test)]
mod parity {
    use super::*;

    /// Three implementations hash these: this file, `mod.py`, and the
    /// console's `lib/api.ts`. The reveal is a comparison against the first,
    /// so a drift in any of them would show up as everybody's bets suddenly
    /// being unopenable. The vectors are pinned here and asserted again in
    /// `tests/test_prerank.py`.
    #[test]
    fn the_hashes_match_the_other_two_implementations() {
        assert_eq!(
            commitment_hash(
                "2026-08-13",
                "0x00000000000000000000000000000000000000AB",
                "opus",
                5_000_000,
                "deadbeef",
            ),
            "dab3fbf2b2e4ba2b2558e1ae7851292fee965d6cc6ee5403162ac0bdb1560031",
        );
        assert_eq!(
            rank_hash(
                "2026-08-13",
                &["opus".into(), "sonnet".into(), "haiku".into()],
            ),
            "657a9ee887cdeb54604789fbbdcb4a49f3d8d835c1623aa32817e8f40ed5f85a",
        );
    }
}

/// The commitment a round is opened under: id, field, and every parameter
/// that will be used to pay it out.
pub fn spec_hash(round: &str, entrants: &[ModelId], params: &RoundParams) -> String {
    let mut field = entrants.to_vec();
    field.sort();
    crate::crypto::hash_fields(&[
        "prerank:spec",
        round,
        &field.join(","),
        &params.fee_bps.to_string(),
        &params.quorum.to_string(),
        &params.earliness_k.to_string(),
        &params.edge_cap.to_string(),
        &params.edge_ttl_rounds.to_string(),
        &params.min_bet.to_string(),
    ])
}
