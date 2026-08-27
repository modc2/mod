//! The market working: a day from open to payout, and the arithmetic that
//! has to hold at the end of it.

mod harness;

use harness::*;
use prerank_api::types::{Outcome, Phase, MICRO};

#[test]
fn a_day_runs_from_open_to_payout() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet", "haiku"]);
    h.fund(ALICE, 100 * MICRO);
    h.fund(BOB, 100 * MICRO);
    h.fund(CAROL, 100 * MICRO);

    h.tick(1_000);
    let round = "r1".to_string();
    assert_eq!(h.e.state().round(&round).unwrap().phase_at(1_100), Phase::Open);

    // Two on opus, one on sonnet. Nobody can see the split while it forms.
    h.bet(ALICE, &round, "opus", 30 * MICRO, 1, 1);
    h.bet(BOB, &round, "opus", 10 * MICRO, 2, 1);
    h.bet(CAROL, &round, "sonnet", 60 * MICRO, 3, 1);

    h.tick(1_900);
    assert_eq!(h.e.state().round(&round).unwrap().phase, Phase::Sealed);
    assert!(h.e.state().round(&round).unwrap().merkle_root.is_some());

    h.attest(GRADER_A, &round, &["opus", "sonnet", "haiku"], 1_910).unwrap();
    h.attest(GRADER_B, &round, &["opus", "sonnet", "haiku"], 1_920).unwrap();
    h.tick(2_000);

    let result = h.result(&round);
    assert_eq!(result.outcome, Outcome::Paid);
    assert_eq!(result.winner.as_deref(), Some("opus"));
    assert_eq!(result.total_pool, 100 * MICRO);
    assert_eq!(result.winning_units, 40 * MICRO);
    assert_eq!(result.fee, 2 * MICRO, "2% of the pool");

    // 98 credits split 3:1 between the opus backers, and Carol's 60 gone.
    let distributable = 98 * MICRO;
    assert_eq!(h.balance(ALICE), 70 * MICRO + distributable * 3 / 4);
    assert_eq!(h.balance(BOB), 90 * MICRO + distributable / 4);
    assert_eq!(h.balance(CAROL), 40 * MICRO);
    h.assert_sound();
}

#[test]
fn every_settlement_adds_back_up_to_the_pool() {
    // Fees, stake sizes and field sizes varied; the identity that has to hold
    // is payouts + fee + dust == pool, exactly, with no floating point in
    // sight.
    for fee_bps in [0u64, 1, 200, 999, 2_000] {
        for stakes in [
            vec![(ALICE, "opus", 1u128), (BOB, "sonnet", 1)],
            vec![(ALICE, "opus", 3), (BOB, "opus", 7), (CAROL, "sonnet", 11)],
            vec![(ALICE, "opus", 999_983), (BOB, "opus", 1), (CAROL, "sonnet", 7_777_777)],
            vec![(ALICE, "sonnet", 5_000_000), (BOB, "haiku", 1_234_567), (CAROL, "opus", 3)],
        ] {
            let params = prerank_api::RoundParams { fee_bps, quorum: 2, min_bet: 1, ..Default::default() };
            let mut h = H::with_params(params);
            h.set_roster(&["opus", "sonnet", "haiku"]);
            for who in [ALICE, BOB, CAROL] {
                h.fund(who, 100 * MICRO);
            }
            let bets: Vec<(u64, &str, u128)> = stakes.clone();
            let round = run_round(&mut h, 1, &bets, &["opus", "sonnet", "haiku"]);
            let r = h.result(&round);
            let paid: u128 = r.payouts.values().sum();
            assert_eq!(
                paid + r.fee + r.dust,
                r.total_pool,
                "fee_bps {fee_bps}, stakes {stakes:?}: the pool must be fully accounted for",
            );
            h.assert_sound();
        }
    }
}

#[test]
fn a_bet_that_is_never_opened_forfeits_its_stake() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.fund(BOB, 100 * MICRO);
    h.tick(1_000);

    h.bet(ALICE, "r1", "opus", 10 * MICRO, 1, 1);
    // Bob commits and goes quiet. His stake is locked, and at the seal it
    // joins the pool without minting him anything.
    h.commit(BOB, "r1", "sonnet", 40 * MICRO, "bob-salt", 2, 1_100).unwrap();
    assert_eq!(h.e.state().locked_of(&addr(BOB)), 40 * MICRO);

    h.tick(1_900);
    assert_eq!(h.e.state().locked_of(&addr(BOB)), 0, "the seal releases the lock");
    let round = h.e.state().round("r1").unwrap().clone();
    assert_eq!(round.pool, 50 * MICRO);
    assert_eq!(round.book("sonnet").units, 0, "an unopened bet backs nothing");

    h.attest(GRADER_A, "r1", &["opus", "sonnet"], 1_910).unwrap();
    h.attest(GRADER_B, "r1", &["opus", "sonnet"], 1_920).unwrap();
    h.tick(2_000);

    let r = h.result("r1");
    assert_eq!(r.outcome, Outcome::Paid);
    // Alice was the only revealed bet; she takes the pool less the fee,
    // Bob's included.
    assert_eq!(h.balance(BOB), 60 * MICRO, "the forfeit is not returned");
    assert_eq!(h.balance(ALICE), 90 * MICRO + (50 * MICRO - r.fee));
    h.assert_sound();
}

#[test]
fn a_winner_nobody_backed_returns_the_stakes() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet", "haiku"]);
    h.fund(ALICE, 100 * MICRO);
    h.fund(BOB, 100 * MICRO);

    let round = run_round(
        &mut h,
        1,
        &[(ALICE, "sonnet", 20 * MICRO), (BOB, "haiku", 30 * MICRO)],
        &["opus", "sonnet", "haiku"], // opus wins; nobody is on opus
    );
    let r = h.result(&round);
    assert_eq!(r.outcome, Outcome::NoWinner);
    assert_eq!(r.fee, 0, "a market that failed to form is not a market to tax");
    assert_eq!(h.balance(ALICE), 100 * MICRO);
    assert_eq!(h.balance(BOB), 100 * MICRO);
    h.assert_sound();
}

#[test]
fn the_round_token_can_change_hands_after_the_seal() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.fund(BOB, 100 * MICRO);
    h.tick(1_000);
    h.bet(ALICE, "r1", "opus", 40 * MICRO, 1, 1);
    h.bet(BOB, "r1", "sonnet", 60 * MICRO, 2, 1);
    h.tick(1_900);

    // Alice sells half her position to Dave, who never bet at all.
    h.transfer(ALICE, "r1", "opus", DAVE, 20 * MICRO, 3, 1_950).unwrap();
    assert_eq!(h.units("r1", "opus", ALICE), 20 * MICRO);
    assert_eq!(h.units("r1", "opus", DAVE), 20 * MICRO);

    h.attest(GRADER_A, "r1", &["opus", "sonnet"], 1_960).unwrap();
    h.attest(GRADER_B, "r1", &["opus", "sonnet"], 1_970).unwrap();
    h.tick(2_000);

    let r = h.result("r1");
    let half = (r.total_pool - r.fee) / 2;
    assert_eq!(r.payouts.get(&addr(ALICE)).copied().unwrap_or(0), half);
    assert_eq!(r.payouts.get(&addr(DAVE)).copied().unwrap_or(0), half);
    assert_eq!(h.balance(DAVE), half, "a holder who never bet still gets paid");
    h.assert_sound();
}

#[test]
fn spending_credits_on_a_model_buys_a_position_in_it_and_being_early_pays_more() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);

    // Alice uses opus when it has taken nothing yet; Carol uses it after
    // several hundred credits have gone through it. Identical spend, identical
    // margin — Carol gets a smaller claim, because she was later.
    h.usage("u1", ALICE, "opus", 10 * MICRO, 6 * MICRO, 1_100).unwrap();
    h.usage("bulk", DAVE, "opus", 900 * MICRO, 900 * MICRO, 1_200).unwrap();
    let (carol_margin, carol_units) = h.usage("u2", CAROL, "opus", 10 * MICRO, 6 * MICRO, 1_300).unwrap();
    assert_eq!(carol_margin, 4 * MICRO);

    let alice_units = h
        .e
        .state()
        .pending_edge
        .iter()
        .find(|p| p.user == addr(ALICE))
        .map(|p| p.units)
        .unwrap();
    assert_eq!(alice_units, 4 * MICRO, "the first user's margin is worth its face value");
    assert!(
        carol_units < alice_units / 4,
        "after 910 credits the same margin should buy far less: {carol_units} vs {alice_units}",
    );

    // The credit is earned during r1 and lands when r2 opens — never in the
    // round that was already taking bets.
    assert_eq!(h.units("r1", "opus", ALICE), 0);
    h.tick(1_900);
    h.tick(2_000);
    assert_eq!(h.units("r2", "opus", ALICE), alice_units);
    assert_eq!(h.units("r2", "opus", CAROL), carol_units);

    // The house funded it: the pool grew by exactly what the treasury lost.
    let round = h.e.state().round("r2").unwrap();
    assert_eq!(round.edge_money, 4 * MICRO + carol_margin);
    assert_eq!(h.e.state().treasury, 10_000 * MICRO - round.edge_money);
    h.assert_sound();
}

#[test]
fn one_address_cannot_own_a_round_by_spending_its_way_in() {
    let mut h = H::with_params(prerank_api::RoundParams {
        quorum: 2,
        edge_cap: 5 * MICRO,
        ..Default::default()
    });
    h.set_roster(&["opus", "sonnet"]);
    h.tick(1_000);
    for i in 0..20 {
        h.usage(&format!("whale{i}"), DAVE, "opus", 20 * MICRO, 0, 1_100 + i).unwrap();
    }
    h.tick(1_900);
    h.tick(2_000);
    assert_eq!(
        h.units("r2", "opus", DAVE),
        5 * MICRO,
        "the per-model cap holds however much is spent",
    );
    h.assert_sound();
}

#[test]
fn a_model_can_only_win_a_round_it_was_entered_in() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 100 * MICRO);
    h.tick(1_000);
    // Betting on a model outside the field is refused at the reveal, which is
    // the first moment the market learns what was bet on.
    let err = h
        .commit(ALICE, "r1", "gpt", 10 * MICRO, "s", 1, 1_100)
        .and_then(|_| h.reveal("r1", ALICE, "gpt", 10 * MICRO, "s", 1_600))
        .unwrap_err();
    assert!(err.to_string().contains("not in this round's field"), "got: {err}");
    h.tick(1_900);
    // The stake is forfeit rather than refunded — an unopenable commitment is
    // indistinguishable from one its owner chose not to open.
    assert_eq!(h.e.state().round("r1").unwrap().pool, 10 * MICRO);
    h.assert_sound();
}

#[test]
fn the_leaderboard_is_the_history_of_settled_rounds() {
    let mut h = H::new();
    h.set_roster(&["opus", "sonnet"]);
    h.fund(ALICE, 500 * MICRO);
    h.fund(BOB, 500 * MICRO);

    run_round(&mut h, 1, &[(ALICE, "opus", 10 * MICRO), (BOB, "sonnet", 10 * MICRO)], &["opus", "sonnet"]);
    run_round(&mut h, 2, &[(ALICE, "opus", 10 * MICRO), (BOB, "sonnet", 10 * MICRO)], &["sonnet", "opus"]);
    run_round(&mut h, 3, &[(ALICE, "opus", 10 * MICRO), (BOB, "sonnet", 10 * MICRO)], &["opus", "sonnet"]);

    let winners: Vec<String> = (1..=3)
        .map(|i| h.result(&format!("r{i}")).winner.unwrap())
        .collect();
    assert_eq!(winners, vec!["opus", "sonnet", "opus"]);
    h.assert_sound();
}
