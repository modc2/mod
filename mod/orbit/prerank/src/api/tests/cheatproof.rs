//! One test per way of cheating.
//!
//! The claims on the module's info card are the index for this file. Each one
//! is written here as an attempt to break it, and the assertion is that the
//! attempt fails — a market that only tests its happy path has tested the
//! part nobody was going to attack.

mod harness;

use harness::*;
use prerank_api::crypto;
use prerank_api::types::{commitment_hash, Event, Outcome, MICRO};
use prerank_api::{state::State, testkit};

// ── the sealed bid ───────────────────────────────────────────────────

#[test]
fn a_bet_cannot_be_switched_to_the_winner_after_the_fact() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);
    let commitment = h.commit(ALICE, "r1", "sonnet", 20 * MICRO, "s1", 1, 1_100).unwrap();

    // She now wants that same locked stake on opus instead. Presenting her
    // real commitment with the other model is the attack; the hash is what
    // refuses it.
    let err = h.e.reveal("r1", &commitment, "opus", "s1", 1_600).unwrap_err();
    assert!(err.to_string().contains("cannot be changed after the fact"), "got: {err}");

    // Hunting for a salt that opens her commitment onto opus fails too —
    // that is a preimage search, not a bet.
    for guess in ["s1 ", "S1", "s2", "opus", ""] {
        assert!(h.e.reveal("r1", &commitment, "opus", guess, 1_600).is_err());
    }

    // Nor can she inflate the stake she is opening: a bigger amount hashes
    // to a commitment that was never placed.
    let err = h.reveal("r1", ALICE, "sonnet", 90 * MICRO, "s1", 1_600).unwrap_err();
    assert!(err.to_string().contains("no such commitment"), "got: {err}");

    // The honest reveal still works.
    h.reveal("r1", ALICE, "sonnet", 20 * MICRO, "s1", 1_600).unwrap();
    assert_eq!(h.units("r1", "sonnet", ALICE), 20 * MICRO);
    h.assert_sound();
}

#[test]
fn the_pools_are_invisible_while_bets_are_open() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);
    h.bet(ALICE, "r1", "opus", 40 * MICRO, 1, 1);

    // Mid-round, a reader can see that 40 credits are committed but not which
    // way. Without that, a late bettor could simply follow the money.
    let view = prerank_api::engine::round_view(h.e.state().round("r1").unwrap(), 1_100);
    assert_eq!(view["staked_visible"], serde_json::json!(40 * MICRO));
    assert!(view["pool"].is_null(), "the pool is not public yet");
    for book in view["books"].as_array().unwrap() {
        assert!(book["units"].is_null(), "per-model stakes must not leak: {book}");
    }

    // After the seal it is all public.
    h.tick(1_900);
    let view = prerank_api::engine::round_view(h.e.state().round("r1").unwrap(), 1_950);
    assert_eq!(view["books"][0]["units"], serde_json::json!(40 * MICRO));
    h.assert_sound();
}

#[test]
fn no_bet_can_be_placed_once_the_reveals_have_started() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.fund(BOB, 100 * MICRO);
    h.tick(1_000);
    h.bet(ALICE, "r1", "opus", 40 * MICRO, 1, 1);

    // Bob has now watched Alice's reveal. He is too late, by design.
    let err = h.commit(BOB, "r1", "opus", 40 * MICRO, "late", 2, 1_700).unwrap_err();
    assert!(err.to_string().contains("bets close"), "got: {err}");
    let err = h.commit(BOB, "r1", "opus", 40 * MICRO, "later", 3, 1_950).unwrap_err();
    assert!(err.to_string().contains("Sealed"), "got: {err}");
    h.assert_sound();
}

#[test]
fn a_reveal_credits_the_bettor_not_whoever_opens_it() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);
    let salt = "public-salt";
    h.commit(ALICE, "r1", "opus", 25 * MICRO, salt, 1, 1_100).unwrap();

    // Bob learns the salt and opens Alice's bet. He can do that — reveals are
    // unauthenticated on purpose — and it does him no good at all.
    let commitment = commitment_hash("r1", &addr(ALICE), "opus", 25 * MICRO, salt);
    h.e.reveal("r1", &commitment, "opus", salt, 1_600).unwrap();
    assert_eq!(h.units("r1", "opus", ALICE), 25 * MICRO);
    assert_eq!(h.units("r1", "opus", BOB), 0);
    h.assert_sound();
}

// ── signatures and replay ────────────────────────────────────────────

#[test]
fn a_bet_signed_by_someone_else_is_not_a_bet() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);

    let commitment = commitment_hash("r1", &addr(ALICE), "opus", 10 * MICRO, "x");
    let msg = prerank_api::Engine::commit_message("r1", &commitment, 10 * MICRO, 1);
    // Bob signs, but the request claims to be Alice — the classic attempt to
    // spend from an account you do not hold the key to.
    let forged = caller_as(BOB, ALICE, &msg);
    let err = h.e.commit(&forged, "r1", &commitment, 10 * MICRO, 1, 1_100).unwrap_err();
    assert!(err.to_string().contains("signature is by"), "got: {err}");
    assert_eq!(h.balance(ALICE), 100 * MICRO, "nothing was taken");
    h.assert_sound();
}

#[test]
fn a_signature_is_good_for_one_action_and_not_two() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);

    h.commit(ALICE, "r1", "opus", 10 * MICRO, "s1", 7, 1_100).unwrap();
    // The same nonce again, even with a different commitment: refused. A
    // captured signed request cannot be resubmitted.
    let err = h.commit(ALICE, "r1", "opus", 10 * MICRO, "s2", 7, 1_110).unwrap_err();
    assert!(err.to_string().contains("nonce 7 has already been used"), "got: {err}");
    h.assert_sound();
}

#[test]
fn a_bet_cannot_exceed_the_balance_behind_it() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 10 * MICRO);
    h.tick(1_000);
    let err = h.commit(ALICE, "r1", "opus", 11 * MICRO, "s", 1, 1_100).unwrap_err();
    assert!(err.to_string().contains("short of"), "got: {err}");

    // And two bets cannot spend the same credits twice: the first one locks.
    h.commit(ALICE, "r1", "opus", 8 * MICRO, "s", 2, 1_100).unwrap();
    let err = h.commit(ALICE, "r1", "sonnet", 8 * MICRO, "s2", 3, 1_100).unwrap_err();
    assert!(err.to_string().contains("short of"), "got: {err}");
    h.assert_sound();
}

// ── grading ──────────────────────────────────────────────────────────

#[test]
fn a_stranger_cannot_declare_the_winner() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    let round = "r1";
    h.tick(1_000);
    h.bet(ALICE, round, "opus", 10 * MICRO, 1, 1);
    h.tick(1_900);

    let err = h.attest(ALICE, round, &["opus", "sonnet"], 1_910).unwrap_err();
    assert!(err.to_string().contains("not a registered grader"), "got: {err}");

    // Even the owner cannot settle a round alone: one grader is short of the
    // quorum, so the round voids and everyone is refunded.
    h.attest(GRADER_A, round, &["opus", "sonnet"], 1_910).unwrap();
    h.tick(2_000);
    let r = h.result(round);
    assert_eq!(r.outcome, Outcome::Void);
    assert!(r.reason.unwrap().contains("quorum"));
    assert_eq!(h.balance(ALICE), 100 * MICRO);
    h.assert_sound();
}

#[test]
fn graders_who_contradict_each_other_void_the_round() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.fund(BOB, 100 * MICRO);
    h.add_grader(8, "grader-c");
    h.add_grader(9, "grader-d");

    h.tick(1_000);
    h.bet(ALICE, "r1", "opus", 40 * MICRO, 1, 1);
    h.bet(BOB, "r1", "sonnet", 60 * MICRO, 2, 1);
    h.tick(1_900);

    // Two graders say opus, two say sonnet. Two quorums is not a tie to be
    // broken — it is a reason not to pay anybody.
    h.attest(GRADER_A, "r1", &["opus", "sonnet"], 1_910).unwrap();
    h.attest(GRADER_B, "r1", &["opus", "sonnet"], 1_911).unwrap();
    h.attest(8, "r1", &["sonnet", "opus"], 1_912).unwrap();
    h.attest(9, "r1", &["sonnet", "opus"], 1_913).unwrap();
    h.tick(2_000);

    let r = h.result("r1");
    assert_eq!(r.outcome, Outcome::Void);
    assert!(r.reason.unwrap().contains("contradict"));
    assert_eq!(h.balance(ALICE), 100 * MICRO);
    assert_eq!(h.balance(BOB), 100 * MICRO);
    h.assert_sound();
}

#[test]
fn a_grader_cannot_grade_a_round_it_is_betting_on() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(GRADER_A, 100 * MICRO);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);

    // Grader A backs opus, then rules that opus won.
    h.bet(GRADER_A, "r1", "opus", 50 * MICRO, 1, 1);
    h.bet(ALICE, "r1", "sonnet", 50 * MICRO, 2, 1);
    h.tick(1_900);

    let counted = h.attest(GRADER_A, "r1", &["opus", "sonnet"], 1_910).unwrap();
    assert!(!counted, "a grader with a position must not count");
    h.attest(GRADER_B, "r1", &["opus", "sonnet"], 1_920).unwrap();
    h.tick(2_000);

    // One clean vote is not a quorum of two, so the self-interested vote does
    // not get to be the deciding one.
    let r = h.result("r1");
    assert_eq!(r.outcome, Outcome::Void);
    assert_eq!(h.balance(GRADER_A), 100 * MICRO);
    let att = &h.e.state().round("r1").unwrap().attestations[&addr(GRADER_A)];
    assert!(!att.counted);
    assert!(att.note.as_ref().unwrap().contains("holds a position"), "the conflict is on the record");
    h.assert_sound();
}

#[test]
fn a_grader_cannot_rank_a_field_that_was_not_the_field() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet", "haiku"]);
    h.tick(1_000);
    h.tick(1_900);

    for (bad, why) in [
        (vec!["opus", "sonnet"], "must order exactly this round's field"),
        (vec!["opus", "sonnet", "haiku", "gpt"], "must order exactly this round's field"),
        (vec!["opus", "opus", "haiku"], "cannot list a model twice"),
    ] {
        let err = h.attest(GRADER_A, "r1", &bad, 1_910).unwrap_err();
        assert!(err.to_string().contains(why), "for {bad:?} got: {err}");
    }
    h.assert_sound();
}

#[test]
fn grading_cannot_start_early_or_arrive_late() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);
    let err = h.attest(GRADER_A, "r1", &["opus", "sonnet"], 1_500).unwrap_err();
    assert!(err.to_string().contains("grading opens when the round seals"), "got: {err}");

    h.tick(1_900);
    let err = h.attest(GRADER_A, "r1", &["opus", "sonnet"], 2_001).unwrap_err();
    assert!(err.to_string().contains("has closed"), "got: {err}");
    h.assert_sound();
}

#[test]
fn the_rankings_stay_sealed_until_the_round_settles() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);
    h.tick(1_900);
    h.attest(GRADER_A, "r1", &["opus", "sonnet"], 1_910).unwrap();

    // The second grader can see that a first grader has spoken, and the hash
    // of what they said, but not the ranking — otherwise "independent
    // agreement" would just be copying.
    let view = prerank_api::engine::round_view(h.e.state().round("r1").unwrap(), 1_915);
    let att = &view["attestations"][0];
    assert!(att["ranking"].is_null(), "the ranking must not be readable yet");
    assert!(att["rank_hash"].is_string());
    h.assert_sound();
}

// ── the credit edge ──────────────────────────────────────────────────

#[test]
fn usage_that_no_meter_signed_is_not_usage() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);

    // An unregistered address presenting a perfectly valid signature.
    let mut receipt = prerank_api::UsageReceipt {
        id: "forged".into(),
        user: addr(DAVE),
        model: "opus".into(),
        spend: 1_000 * MICRO,
        cost: 0,
        at: 1_100,
        meter: addr(DAVE),
        signature: String::new(),
    };
    let (_, sig) = testkit::sign(DAVE, &receipt.message());
    receipt.signature = sig;
    let err = h.e.post_usage(receipt.clone(), 1_100).unwrap_err();
    assert!(err.to_string().contains("not a registered meter"), "got: {err}");

    // The registered meter's address with somebody else's signature.
    receipt.meter = addr(METER);
    let err = h.e.post_usage(receipt, 1_100).unwrap_err();
    assert!(err.to_string().contains("signature is by"), "got: {err}");
    assert!(h.e.state().pending_edge.is_empty());
    h.assert_sound();
}

#[test]
fn the_same_receipt_cannot_be_banked_twice() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);
    h.usage("r-1", ALICE, "opus", 10 * MICRO, 5 * MICRO, 1_100).unwrap();
    let err = h.usage("r-1", ALICE, "opus", 10 * MICRO, 5 * MICRO, 1_100).unwrap_err();
    assert!(err.to_string().contains("already been posted"), "got: {err}");
    assert_eq!(h.e.state().pending_edge.len(), 1);
    h.assert_sound();
}

#[test]
fn the_edge_can_never_be_worth_more_than_what_was_paid_for_it() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);

    // A receipt claiming the call cost more than the user paid would mint
    // margin out of nothing.
    let err = h.usage("bad", ALICE, "opus", 5 * MICRO, 9 * MICRO, 1_100).unwrap_err();
    assert!(err.to_string().contains("cost cannot exceed spend"), "got: {err}");

    // The honest case: the position handed back is the house's margin, which
    // is by definition less than the user's spend. Farming usage to build a
    // position is always a net outlay.
    for (i, (spend, cost)) in [(10, 9), (10, 0), (1, 0), (1_000, 999)].iter().enumerate() {
        let (margin, units) = h
            .usage(&format!("ok{i}"), BOB, "sonnet", spend * MICRO, cost * MICRO, 1_100 + i as i64)
            .unwrap();
        assert_eq!(margin, (spend - cost) * MICRO);
        assert!(units <= margin, "edge units must not exceed the margin");
        assert!(margin < spend * MICRO || *cost == 0);
        assert!(units < spend * MICRO, "the rebate is never the whole spend");
    }
    h.assert_sound();
}

#[test]
fn edge_credit_cannot_land_in_the_round_that_is_already_running() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);
    // Deep into round 1, when the field is starting to be legible, Dave buys
    // a pile of usage on the model he thinks is winning.
    h.usage("late", DAVE, "opus", 500 * MICRO, 0, 1_890).unwrap();
    h.tick(1_900);
    assert_eq!(h.units("r1", "opus", DAVE), 0, "not in this round");
    h.tick(2_000);
    assert!(h.units("r2", "opus", DAVE) > 0, "in the next one");
    h.assert_sound();
}

#[test]
fn edge_credit_the_house_cannot_fund_is_not_minted() {
    let mut h = H::with_params(prerank_api::RoundParams { quorum: 2, edge_cap: u128::MAX, ..Default::default() });
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);
    // A margin larger than the entire treasury.
    h.usage("huge", DAVE, "opus", 50_000 * MICRO, 0, 1_100).unwrap();
    h.tick(1_900);
    h.tick(2_000);
    assert_eq!(h.units("r2", "opus", DAVE), 0, "the pool is not funded out of nothing");
    assert_eq!(h.e.state().treasury, 10_000 * MICRO);
    assert_eq!(h.e.state().pending_edge.len(), 1, "it waits for a treasury that can pay it");
    h.assert_sound();
}

// ── the token ────────────────────────────────────────────────────────

#[test]
fn the_round_token_does_not_trade_before_the_seal() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);
    h.bet(ALICE, "r1", "opus", 40 * MICRO, 1, 1);

    // A transfer during the open window would announce which model Alice is
    // holding, which is exactly what her commitment is hiding.
    let err = h.transfer(ALICE, "r1", "opus", BOB, 10 * MICRO, 2, 1_650).unwrap_err();
    assert!(err.to_string().contains("between the seal and the settlement"), "got: {err}");
    h.tick(1_900);
    h.transfer(ALICE, "r1", "opus", BOB, 10 * MICRO, 3, 1_950).unwrap();
    assert_eq!(h.units("r1", "opus", BOB), 10 * MICRO);
    h.assert_sound();
}

#[test]
fn nobody_can_send_a_token_they_do_not_hold() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);
    h.bet(ALICE, "r1", "opus", 40 * MICRO, 1, 1);
    h.tick(1_900);

    let err = h.transfer(BOB, "r1", "opus", CAROL, MICRO, 2, 1_950).unwrap_err();
    assert!(err.to_string().contains("you hold 0 units"), "got: {err}");
    let err = h.transfer(ALICE, "r1", "opus", CAROL, 41 * MICRO, 3, 1_950).unwrap_err();
    assert!(err.to_string().contains("not 41000000"), "got: {err}");
    h.assert_sound();
}

// ── the log ──────────────────────────────────────────────────────────

#[test]
fn a_sealed_bet_can_prove_it_was_in_the_set_and_a_forged_one_cannot() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    for who in [ALICE, BOB, CAROL] {
        h.fund(who, 100 * MICRO);
    }
    h.tick(1_000);
    h.bet(ALICE, "r1", "opus", 10 * MICRO, 1, 1);
    h.bet(BOB, "r1", "sonnet", 10 * MICRO, 2, 1);
    h.bet(CAROL, "r1", "opus", 10 * MICRO, 3, 1);
    h.tick(1_900);

    let commitment = commitment_hash("r1", &addr(ALICE), "opus", 10 * MICRO, "salt-4-1");
    let proof = h.e.inclusion_proof("r1", &commitment).unwrap();
    assert!(proof.sealed && proof.verifies);
    assert_eq!(proof.root, h.e.state().round("r1").unwrap().merkle_root.clone().unwrap());

    // Verified independently, the way a client would, against the published
    // root alone.
    let path: Vec<(String, bool)> =
        proof.path.iter().map(|s| (s.sibling.clone(), s.sibling_is_left)).collect();
    assert!(crypto::merkle_verify(&commitment, &path, &proof.root));
    let forged = crypto::sha256_hex(b"a bet that was never placed");
    assert!(!crypto::merkle_verify(&forged, &path, &proof.root));
    assert!(h.e.inclusion_proof("r1", &forged).is_err());
    h.assert_sound();
}

#[test]
fn editing_the_log_after_the_fact_shows_up_in_the_replay() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.fund(BOB, 100 * MICRO);
    run_round(&mut h, 1, &[(ALICE, "opus", 40 * MICRO), (BOB, "sonnet", 60 * MICRO)], &["opus", "sonnet"]);
    h.assert_sound();

    // Take the log and rewrite Bob's losing bet onto the winning model — the
    // edit an operator with database access would make.
    let mut events: Vec<Event> = h.e.chain().events().cloned().collect();
    let mut edited = false;
    for event in events.iter_mut() {
        if let Event::Revealed { owner, model, .. } = event {
            if *owner == addr(BOB) {
                *model = "opus".to_string();
                edited = true;
            }
        }
    }
    assert!(edited, "the test needs a reveal to rewrite");

    let replayed = State::replay(events.iter());
    assert!(
        !replayed.divergences.is_empty(),
        "a rewritten log must not fold back into the settlement that was recorded",
    );

    // And the hash chain refuses it outright: the entries still carry the
    // hashes of what was actually written.
    let mut chain = prerank_api::Chain::in_memory();
    for event in &events {
        chain.append(event.clone()).unwrap();
    }
    assert_ne!(chain.head(), h.e.head(), "an edited log cannot reach the published head");
}

#[test]
fn the_rules_of_a_round_cannot_move_once_it_is_taking_bets() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);
    let spec = h.e.state().round("r1").unwrap().spec_hash.clone();
    let entrants = h.e.state().round("r1").unwrap().entrants.clone();
    h.bet(ALICE, "r1", "opus", 10 * MICRO, 1, 1);

    // The owner adds a model mid-round. The running round does not notice.
    h.set_roster(&["opus", "sonnet", "haiku"]);
    assert_eq!(h.e.state().round("r1").unwrap().spec_hash, spec);
    assert_eq!(h.e.state().round("r1").unwrap().entrants, entrants);
    assert_eq!(
        spec,
        prerank_api::types::spec_hash("r1", &entrants, &h.e.state().round("r1").unwrap().params),
        "the spec hash is a commitment to the field and the payout rules",
    );

    // The new model only enters the next round.
    h.tick(1_900);
    h.attest(GRADER_A, "r1", &["opus", "sonnet"], 1_910).unwrap();
    h.attest(GRADER_B, "r1", &["opus", "sonnet"], 1_920).unwrap();
    h.tick(2_000);
    assert!(h.e.state().round("r2").unwrap().entrants.contains(&"haiku".to_string()));
    h.assert_sound();
}

#[test]
fn a_market_booted_without_an_owner_can_be_claimed_once_and_only_once() {
    use prerank_api::engine::{Engine, Schedule, ZERO_ADDRESS};
    use prerank_api::Chain;

    // How a fresh deployment starts when PRERANK_OWNER was never set.
    let mut e = Engine::new(
        Chain::in_memory(),
        Schedule { day_secs: DAY, reveal_bps: 5_000, seal_bps: 9_000 },
        Default::default(),
        false,
    );
    e.bootstrap(ZERO_ADDRESS, 0).unwrap();
    assert_eq!(e.state().owner.as_deref(), Some(ZERO_ADDRESS));

    // The first address to sign for it takes it.
    let alice = addr(ALICE);
    e.set_owner(&caller(ALICE, &format!("prerank:owner:{alice}")), &alice, 1).unwrap();
    assert!(e.state().is_owner(&alice));

    // And nobody can take it from them afterwards.
    let bob = addr(BOB);
    let err = e
        .set_owner(&caller(BOB, &format!("prerank:owner:{bob}")), &bob, 2)
        .unwrap_err();
    assert!(err.to_string().contains("only the owner"), "got: {err}");
    assert!(e.state().is_owner(&alice));
}

#[test]
fn only_the_owner_can_change_who_grades_and_who_meters() {
    let mut h = H::new();
    let target = addr(DAVE);
    for (msg, call) in [
        (format!("prerank:attestor:add:{target}"), 0),
        (format!("prerank:meter:add:{target}"), 1),
        (format!("prerank:credit:{target}:1000"), 2),
        ("prerank:roster:gpt".to_string(), 3),
    ] {
        let c = caller(ALICE, &msg);
        let err = match call {
            0 => h.e.register_attestor(&c, &target, "x", 0).err(),
            1 => h.e.register_meter(&c, &target, "x", 0).err(),
            2 => h.e.credit(&c, &target, 1_000, "x", 0).err(),
            _ => h.e.set_roster(&c, vec!["gpt".into()], 0).err(),
        };
        let err = err.expect("a stranger should be refused");
        assert!(err.to_string().contains("only the owner"), "got: {err}");
    }
    assert_eq!(h.balance(DAVE), 0);
    h.assert_sound();
}
