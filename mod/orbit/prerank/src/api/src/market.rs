//! The payout math, as pure functions over a round.
//!
//! Nothing in here reads the clock, the disk or the network, and nothing in
//! here mutates anything. That is deliberate: settlement is the one
//! calculation an outsider must be able to redo, so it is written as a
//! function of a round and nothing else. The server runs it to produce the
//! settlement event; the replay in `state.rs` runs it again to check the
//! server; the tests run it on hand-built rounds.
//!
//! The market is parimutuel. Nobody quotes a price, nobody is on the other
//! side of your bet, and the house cannot lose — the winners divide what
//! everyone staked. Combined with sealed commitments this gives the property
//! the module is named for: you have to back a model *before* you can see
//! what anyone else thinks, so the odds you get are the odds of being early.

use std::collections::BTreeMap;

use crate::types::{Money, Outcome, Round, RoundResult};

/// Who the graders agreed on, if they agreed at all.
pub struct Tally {
    pub winner_hash: Option<String>,
    pub ranking: Vec<String>,
    pub votes: usize,
    /// Every distinct ranking that reached quorum. More than one is a
    /// contradiction, not a tie-break — the round voids.
    pub quorums: usize,
    pub counted: usize,
    pub ignored: usize,
}

/// Count the attestations. Only `counted` ones (graders with no position in
/// the round) are eligible, and a ranking wins only by being the sole one to
/// reach quorum.
pub fn tally(round: &Round) -> Tally {
    let mut by_hash: BTreeMap<String, (Vec<String>, usize)> = BTreeMap::new();
    let mut counted = 0usize;
    let mut ignored = 0usize;
    for att in round.attestations.values() {
        if !att.counted {
            ignored += 1;
            continue;
        }
        counted += 1;
        let slot = by_hash
            .entry(att.rank_hash.clone())
            .or_insert_with(|| (att.ranking.clone(), 0));
        slot.1 += 1;
    }
    let reached: Vec<(&String, &(Vec<String>, usize))> = by_hash
        .iter()
        .filter(|(_, (_, votes))| *votes >= round.params.quorum)
        .collect();
    if reached.len() == 1 {
        let (hash, (ranking, votes)) = reached[0];
        Tally {
            winner_hash: Some(hash.clone()),
            ranking: ranking.clone(),
            votes: *votes,
            quorums: 1,
            counted,
            ignored,
        }
    } else {
        Tally {
            winner_hash: None,
            ranking: Vec::new(),
            votes: reached.iter().map(|(_, (_, v))| *v).max().unwrap_or(0),
            quorums: reached.len(),
            counted,
            ignored,
        }
    }
}

/// Turn a sealed round into a result. Deterministic, integer-only, and total:
/// every path returns a result whose payouts, fee and dust add back up to the
/// pool exactly.
pub fn finalize(round: &Round) -> RoundResult {
    let t = tally(round);
    let pool = round.pool;

    let Some(rank_hash) = t.winner_hash else {
        let reason = if t.quorums > 1 {
            format!(
                "{} different rankings each reached quorum — the graders contradict each other",
                t.quorums
            )
        } else if t.counted == 0 {
            "no eligible grader attested".to_string()
        } else {
            format!(
                "no ranking reached quorum ({} of {} needed, {} attestation(s) counted, {} ignored)",
                t.votes, round.params.quorum, t.counted, t.ignored
            )
        };
        return void_result(round, pool, reason);
    };

    let winner = match t.ranking.first() {
        Some(w) => w.clone(),
        None => return void_result(round, pool, "the agreed ranking is empty".to_string()),
    };

    let book = round.book(&winner);
    let winning_units = book.units;

    // Nobody backed the winner. Give the bettors their stake back rather than
    // handing the whole pool to the house: a round with no takers on the
    // winning side is a market that failed to form, not a market someone won.
    if winning_units == 0 {
        let mut payouts: BTreeMap<String, Money> = BTreeMap::new();
        for (addr, amount) in &round.contributions {
            if *amount > 0 {
                *payouts.entry(addr.clone()).or_insert(0) += *amount;
            }
        }
        let paid: Money = payouts.values().sum();
        return RoundResult {
            outcome: Outcome::NoWinner,
            winner: Some(winner),
            ranking: t.ranking,
            rank_hash: Some(rank_hash),
            votes: t.votes,
            total_pool: pool,
            winning_units: 0,
            fee: 0,
            dust: pool.saturating_sub(paid),
            payouts,
            reason: Some("nobody held the winning model — stakes returned".to_string()),
        };
    }

    let fee = pool * (round.params.fee_bps as Money) / 10_000;
    let distributable = pool - fee;
    let mut payouts: BTreeMap<String, Money> = BTreeMap::new();
    for (holder, units) in &book.holders {
        if *units == 0 {
            continue;
        }
        // Floor division, every time. The remainder is collected as dust
        // below rather than distributed by some rounding rule that would
        // depend on iteration order.
        let cut = distributable * *units / winning_units;
        if cut > 0 {
            payouts.insert(holder.clone(), cut);
        }
    }
    let paid: Money = payouts.values().sum();
    RoundResult {
        outcome: Outcome::Paid,
        winner: Some(winner),
        ranking: t.ranking,
        rank_hash: Some(rank_hash),
        votes: t.votes,
        total_pool: pool,
        winning_units,
        fee,
        dust: distributable - paid,
        payouts,
        reason: None,
    }
}

/// A void returns everything to where it came from: revealed stakes and
/// forfeited stakes alike go back to the bettor. Only the house's own edge
/// money stays with the house.
fn void_result(round: &Round, pool: Money, reason: String) -> RoundResult {
    let mut payouts: BTreeMap<String, Money> = BTreeMap::new();
    for (addr, amount) in &round.contributions {
        if *amount > 0 {
            *payouts.entry(addr.clone()).or_insert(0) += *amount;
        }
    }
    for (addr, amount) in &round.forfeits {
        if *amount > 0 {
            *payouts.entry(addr.clone()).or_insert(0) += *amount;
        }
    }
    let paid: Money = payouts.values().sum();
    RoundResult {
        outcome: Outcome::Void,
        winner: None,
        ranking: Vec::new(),
        rank_hash: None,
        votes: 0,
        total_pool: pool,
        winning_units: 0,
        fee: 0,
        dust: pool.saturating_sub(paid),
        payouts,
        reason: Some(reason),
    }
}

/// The earliness weight for a unit of margin: `K / (K + c)`, where `c` is how
/// many credits the model had already absorbed when the call was made.
///
/// The first credit ever spent on a model is worth a full unit of claim; by
/// the time the model has taken K credits, the same margin buys half as much.
/// This is the only place the module rewards anything other than money, and
/// what it rewards is having been early.
pub fn earliness_units(margin: Money, credits_before: Money, k: Money) -> (Money, Money, Money) {
    let den = k.saturating_add(credits_before);
    if den == 0 {
        return (margin, 1, 1);
    }
    let units = margin.saturating_mul(k) / den;
    (units, k, den)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::MICRO;

    #[test]
    fn earliness_halves_at_k_and_never_exceeds_the_margin() {
        let k = 100 * MICRO;
        let (first, _, _) = earliness_units(10 * MICRO, 0, k);
        assert_eq!(first, 10 * MICRO, "the first user gets full weight");
        let (at_k, _, _) = earliness_units(10 * MICRO, k, k);
        assert_eq!(at_k, 5 * MICRO, "by K credits the weight has halved");
        let (late, _, _) = earliness_units(10 * MICRO, 900 * MICRO, k);
        assert_eq!(late, MICRO, "at 9K the same margin buys a tenth");
        // Monotonic: later is never better.
        let mut prev = Money::MAX;
        for c in 0..40 {
            let (u, _, _) = earliness_units(7 * MICRO, c * 13 * MICRO, k);
            assert!(u <= prev, "weight must not increase with usage");
            prev = u;
        }
    }
}
