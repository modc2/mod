//! A compressed market for the tests.
//!
//! Rounds here are 1000 seconds instead of a day, which changes nothing about
//! the rules — the phase boundaries are computed from the schedule either
//! way. Signatures are real: `open_mode` is off in every test, so each action
//! is signed by a deterministic wallet and recovered by the same code the
//! server runs. A test that passes with signatures switched off would prove
//! very little about a module whose whole claim is that it cannot be cheated.

#![allow(dead_code)]

use prerank_api::engine::{Caller, Engine, Schedule};
use prerank_api::types::{commitment_hash, Money, RoundParams, UsageReceipt, MICRO};
use prerank_api::{testkit, Chain};

/// Round n opens at n*1000, reveals from n*1000+500, seals at n*1000+900,
/// settles at (n+1)*1000.
pub const DAY: i64 = 1000;

pub const OWNER: u64 = 0;
pub const GRADER_A: u64 = 1;
pub const GRADER_B: u64 = 2;
pub const METER: u64 = 3;
pub const ALICE: u64 = 4;
pub const BOB: u64 = 5;
pub const CAROL: u64 = 6;
pub const DAVE: u64 = 7;

pub fn addr(n: u64) -> String {
    testkit::address(n)
}

pub fn caller(n: u64, message: &str) -> Caller {
    let (address, signature) = testkit::sign(n, message);
    Caller { address, signature }
}

/// A caller that presents somebody else's address with its own signature —
/// the shape of every impersonation attempt in the suite.
pub fn caller_as(signer: u64, claims_to_be: u64, message: &str) -> Caller {
    let (_, signature) = testkit::sign(signer, message);
    Caller { address: testkit::address(claims_to_be), signature }
}

pub struct H {
    pub e: Engine,
}

impl H {
    /// An engine with an owner, two graders, one meter, and a funded
    /// treasury — but no roster and no rounds yet.
    pub fn new() -> Self {
        Self::with_params(RoundParams { quorum: 2, ..Default::default() })
    }

    pub fn with_params(params: RoundParams) -> Self {
        let schedule = Schedule { day_secs: DAY, reveal_bps: 5_000, seal_bps: 9_000 };
        let mut e = Engine::new(Chain::in_memory(), schedule, params, false);
        e.bootstrap(&addr(OWNER), 0).expect("genesis");
        let mut h = Self { e };
        h.add_grader(GRADER_A, "grader-a");
        h.add_grader(GRADER_B, "grader-b");
        h.add_meter(METER, "the-gateway");
        h.grant("treasury", 10_000 * MICRO);
        h
    }

    pub fn add_grader(&mut self, n: u64, label: &str) {
        let who = addr(n);
        let c = caller(OWNER, &format!("prerank:attestor:add:{who}"));
        self.e.register_attestor(&c, &who, label, 0).expect("register grader");
    }

    pub fn add_meter(&mut self, n: u64, label: &str) {
        let who = addr(n);
        let c = caller(OWNER, &format!("prerank:meter:add:{who}"));
        self.e.register_meter(&c, &who, label, 0).expect("register meter");
    }

    pub fn grant(&mut self, account: &str, amount: Money) {
        let c = caller(OWNER, &format!("prerank:credit:{account}:{amount}"));
        self.e.credit(&c, account, amount, "test", 0).expect("credit");
    }

    pub fn fund(&mut self, n: u64, amount: Money) {
        let account = addr(n);
        self.grant(&account, amount);
    }

    pub fn set_roster(&mut self, models: &[&str]) {
        let mut sorted: Vec<String> = models.iter().map(|m| m.to_string()).collect();
        sorted.sort();
        sorted.dedup();
        let c = caller(OWNER, &format!("prerank:roster:{}", sorted.join(",")));
        self.e.set_roster(&c, sorted, 0).expect("roster");
    }

    pub fn tick(&mut self, now: i64) {
        self.e.tick(now).expect("tick");
    }

    /// Seal a sealed bet the way a client would: hash at home, sign the hash,
    /// send the hash. The model never leaves this function.
    pub fn commit(
        &mut self,
        n: u64,
        round: &str,
        model: &str,
        amount: Money,
        salt: &str,
        nonce: u64,
        now: i64,
    ) -> prerank_api::engine::Result<String> {
        let commitment = commitment_hash(round, &addr(n), model, amount, salt);
        let msg = Engine::commit_message(round, &commitment, amount, nonce);
        let c = caller(n, &msg);
        self.e.commit(&c, round, &commitment, amount, nonce, now)
    }

    pub fn reveal(
        &mut self,
        round: &str,
        n: u64,
        model: &str,
        amount: Money,
        salt: &str,
        now: i64,
    ) -> prerank_api::engine::Result<(String, Money)> {
        let commitment = commitment_hash(round, &addr(n), model, amount, salt);
        self.e.reveal(round, &commitment, model, salt, now)
    }

    /// Commit and reveal in one go, at the natural times for round `index`.
    pub fn bet(&mut self, n: u64, round: &str, model: &str, amount: Money, nonce: u64, index: i64) {
        let salt = format!("salt-{n}-{nonce}");
        self.commit(n, round, model, amount, &salt, nonce, index * DAY + 100)
            .expect("commit");
        self.reveal(round, n, model, amount, &salt, index * DAY + 600)
            .expect("reveal");
    }

    pub fn attest(&mut self, n: u64, round: &str, ranking: &[&str], now: i64) -> prerank_api::engine::Result<bool> {
        let ranking: Vec<String> = ranking.iter().map(|m| m.to_string()).collect();
        let hash = prerank_api::types::rank_hash(round, &ranking);
        let c = caller(n, &Engine::attest_message(round, &hash));
        self.e.attest(&c, round, ranking, now)
    }

    /// A signed usage receipt from the registered meter.
    pub fn usage(&mut self, id: &str, user: u64, model: &str, spend: Money, cost: Money, at: i64)
        -> prerank_api::engine::Result<(Money, Money)>
    {
        let mut receipt = UsageReceipt {
            id: id.to_string(),
            user: addr(user),
            model: model.to_string(),
            spend,
            cost,
            at,
            meter: addr(METER),
            signature: String::new(),
        };
        let (_, sig) = testkit::sign(METER, &receipt.message());
        receipt.signature = sig;
        self.e.post_usage(receipt, at)
    }

    pub fn transfer(&mut self, from: u64, round: &str, model: &str, to: u64, units: Money, nonce: u64, now: i64)
        -> prerank_api::engine::Result<Money>
    {
        let msg = Engine::transfer_message(round, model, &addr(to), units, nonce);
        let c = caller(from, &msg);
        self.e.transfer(&c, round, model, &addr(to), units, nonce, now)
    }

    pub fn balance(&self, n: u64) -> Money {
        self.e.state().balance(&addr(n))
    }

    pub fn units(&self, round: &str, model: &str, n: u64) -> Money {
        self.e
            .state()
            .book_of(round, model)
            .holders
            .get(&addr(n))
            .copied()
            .unwrap_or(0)
    }

    pub fn result(&self, round: &str) -> prerank_api::types::RoundResult {
        self.e
            .state()
            .round(round)
            .and_then(|r| r.result.clone())
            .unwrap_or_else(|| panic!("round {round} has not settled"))
    }

    /// The check that has to hold after every scenario, no exceptions.
    pub fn assert_sound(&self) {
        let v = self.e.verify();
        assert!(v.ok, "the market should verify, but: {:?}", v.problems);
        self.e
            .state()
            .conservation()
            .unwrap_or_else(|e| panic!("credits went missing: {e}"));
    }
}

/// Run one whole round: open, bets, reveal, seal, two graders agree, settle.
/// Returns the round id.
pub fn run_round(h: &mut H, index: i64, bets: &[(u64, &str, Money)], ranking: &[&str]) -> String {
    let base = index * DAY;
    h.tick(base);
    let id = format!("r{index}");
    for (i, (who, model, amount)) in bets.iter().enumerate() {
        // Nonces are per-address and single-use for the life of the chain,
        // so they have to be unique across rounds too, not just within one.
        h.bet(*who, &id, model, *amount, index as u64 * 100 + i as u64, index);
    }
    h.tick(base + 900);
    h.attest(GRADER_A, &id, ranking, base + 910).expect("grader a");
    h.attest(GRADER_B, &id, ranking, base + 920).expect("grader b");
    h.tick(base + DAY);
    id
}
