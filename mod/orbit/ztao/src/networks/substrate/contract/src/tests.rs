//! Unit tests on ink!'s off-chain environment. The fixture is the NEAR
//! crate's (signed by the CAIRO prover): if this contract accepts it, it
//! verifies the same signatures as every other chain.
use crate::ztao::*;
use crate::{ROLE_MINER, ROLE_VALIDATOR};
use ink::env::hash::Blake2x256;
use ink::primitives::AccountId;
use lsag::{Pt, Sig};
use starknet_types_core::felt::Felt;
use crate::lsag::{self, signer};
use ink::env::{test, DefaultEnvironment as E};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../near/contract/tests/fixture.json");
const TEMPO: u64 = 10;

fn fx() -> Value {
    serde_json::from_str(FIXTURE).unwrap()
}

fn fe(v: &Value) -> Felt {
    lsag::felt(v.as_str().unwrap()).unwrap()
}

fn b(f: &Felt) -> F {
    f.to_bytes_be()
}

fn p(q: &Pt) -> Point {
    Point { x: b(&q.x), y: b(&q.y) }
}

fn fpt(v: &Value) -> Pt {
    Pt { x: fe(&v["x"]), y: fe(&v["y"]) }
}

fn wire(ring: &[Pt], s: &Sig, hp: Option<Point>) -> (Vec<Point>, Signature) {
    (
        ring.iter().map(p).collect(),
        Signature { c0: b(&s.c0), s: s.s.iter().map(b).collect(), image: p(&s.image), hp },
    )
}

fn accounts() -> test::DefaultAccounts<E> {
    test::default_accounts::<E>()
}

/// Move the chain to `block` with `who` as the caller.
fn at(block: u32, who: AccountId) {
    while ink::env::block_number::<E>() < block {
        test::advance_block::<E>();
    }
    test::set_caller::<E>(who);
}

fn sec(i: u64) -> Felt {
    Felt::from(0x5eed_0000 + i)
}

fn sorted(v: &[Pt]) -> Vec<Pt> {
    let mut r = v.to_vec();
    signer::sort_ring(&mut r);
    r
}

fn fill(n: usize, salt: u64) -> Vec<Felt> {
    (0..n as u64).map(|i| Felt::from(salt * 100 + i + 1)).collect()
}

struct World {
    z: ZTao,
    miners: Vec<Pt>,
    vals: Vec<Pt>,
}

fn world() -> World {
    test::set_callee::<E>(AccountId::from([0x7a; 32]));
    at(100, accounts().alice);
    let mut z = ZTao::new(TEMPO, 3, 1000);
    let miners: Vec<Pt> = (0..4).map(|i| signer::public_key(&sec(i))).collect();
    let vals: Vec<Pt> = (10..14).map(|i| signer::public_key(&sec(i))).collect();
    for m in &miners {
        z.register(ROLE_MINER, p(m)).unwrap();
    }
    for v in &vals {
        z.register(ROLE_VALIDATOR, p(v)).unwrap();
    }
    World { z, miners, vals }
}

impl World {
    fn submit(&mut self, i: u64, work: u64) -> Result<F> {
        let ring = sorted(&self.miners);
        let scope = Felt::from_bytes_be(&self.z.mine_scope(self.z.epoch()));
        let s = signer::sign(&ring, &sec(i), &scope, &Felt::from(work), &Felt::from(500 + i), &fill(ring.len(), i));
        let (r, w) = wire(&ring, &s, None);
        self.z.submit(r, b(&Felt::from(work)), w)
    }

    fn score(&mut self, v: u64, tag: F, score: u8) -> Result<F> {
        let ring = sorted(&self.vals);
        let scope = Felt::from_bytes_be(&self.z.score_scope(self.z.epoch(), tag).unwrap());
        let s = signer::sign(&ring, &sec(v), &scope, &Felt::from(score), &Felt::from(700 + v), &fill(ring.len(), v));
        let hp = self.z.hash_point(b(&scope)).ok();
        let (r, w) = wire(&ring, &s, hp);
        self.z.score(r, tag, score, w)
    }

    fn claim(&mut self, i: u64, epoch: u64, to: AccountId) -> Result<u128> {
        let ring = sorted(&self.miners);
        let scope = Felt::from_bytes_be(&self.z.mine_scope(epoch));
        let msg = Felt::from_bytes_be(&self.z.recipient_felt(to));
        let s = signer::sign(&ring, &sec(i), &scope, &msg, &Felt::from(900 + i), &fill(ring.len(), i + 50));
        let (r, w) = wire(&ring, &s, None);
        self.z.claim(epoch, r, to, w)
    }
}

// ---- wire compatibility with Cairo -----------------------------------------

#[ink::test]
fn cairo_signature_verifies_on_substrate() {
    let w = world();
    let x = fx();
    let ring: Vec<Point> = x["sig"]["ring"].as_array().unwrap().iter().map(|v| p(&fpt(v))).collect();
    let scope = b(&fe(&x["scope_mine"]["scope"]));
    let msg = b(&fe(&x["msg"]));
    let s = &x["sig"];
    let sig = Signature {
        c0: b(&fe(&s["c0"])),
        s: s["s"].as_array().unwrap().iter().map(|v| b(&fe(v))).collect(),
        image: p(&fpt(&s["image"])),
        hp: None,
    };
    assert_eq!(w.z.verify(ring.clone(), scope, msg, sig.clone()), Ok(true));
    let mut bad = sig.clone();
    bad.s[2][31] ^= 1;
    assert_eq!(w.z.verify(ring.clone(), scope, msg, bad), Ok(false));
    // a wrong Hp hint is checked, not trusted: same answer, slow path
    let wrong = Signature { hp: Some(p(&signer::public_key(&Felt::from(5u64)))), ..sig.clone() };
    assert_eq!(w.z.verify(ring, scope, msg, wrong), Ok(true));
}

#[ink::test]
fn hash_to_point_matches_cairo() {
    let w = world();
    for row in fx()["hash_to_point"].as_array().unwrap() {
        let q = w.z.hash_point(b(&fe(&row["scope"]))).unwrap();
        assert_eq!(q, p(&fpt(row)));
    }
}

#[ink::test]
fn felts_are_strict() {
    let w = world();
    let prime = b(&lsag::felt("0x800000000000011000000000000000000000000000000000000000000000000").unwrap());
    let mut over = prime;
    over[31] = 1; // = PRIME
    assert!(w.z.hash_point(prime).is_ok());
    assert_eq!(w.z.hash_point(over), Err(Error::NotAFelt));
}

#[ink::test]
fn subnet_binds_the_contract_account() {
    let w = world();
    let mut h = [0u8; 32];
    ink::env::hash_bytes::<Blake2x256>(&[0x7a; 32], &mut h);
    h[0] &= 0x07;
    assert_eq!(w.z.subnet(), h);
    let scope = lsag::h(&[lsag::short("zuma-mine"), Felt::from_bytes_be(&h), Felt::from(10u64)]);
    assert_eq!(w.z.mine_scope(10), b(&scope));
}

// ---- the contract, one epoch end to end ------------------------------------

#[ink::test]
fn full_epoch() {
    let mut w = world();
    assert_eq!(w.z.members(ROLE_MINER, 0, 100).len(), 4);
    at(120, accounts().eve); // a relayer posts everything
    let e = w.z.epoch();
    let tags: Vec<F> = (0..3).map(|i| w.submit(i, 0xa0 + i).unwrap()).collect();
    for v in 10..14 {
        for (k, t) in tags.iter().enumerate() {
            w.score(v, *t, 90 - 20 * k as u8).unwrap();
        }
    }
    let t = w.z.tally(e);
    assert_eq!(t.total, 4 * (90 + 70 + 50));
    assert_eq!((t.tags[0].score_sum, t.validators), (360, 4));
    assert_eq!(w.z.settle(e, tags[0]), Err(Error::EpochOpen));
    assert_eq!(w.claim(0, e, accounts().bob), Err(Error::EpochOpen));
    at(130, accounts().eve);
    // python3: core/yuma.epoch({a:[90]*4, b:[70]*4, c:[50]*4}, 4, 1000)
    for (t, cr) in tags.iter().zip([(90, 360), (70, 280), (50, 200)]) {
        assert_eq!(w.z.settle(e, *t), Ok(cr));
    }
    assert_eq!(w.z.settle(e, tags[0]), Err(Error::AlreadySettled));
    let ye = w.z.yuma_epoch(e);
    assert_eq!((ye.vset, ye.rank_total, ye.nsettled), (4, 840, 3));
    let to = [accounts().bob, accounts().charlie, accounts().django];
    for (i, share) in [428u128, 333, 238].into_iter().enumerate() {
        assert_eq!(w.claim(i as u64, e, to[i]), Ok(share));
        assert_eq!(w.z.earned(to[i]), share);
    }
    assert_eq!(w.claim(0, e, accounts().frank), Err(Error::AlreadyClaimed));
    assert!(w.z.tally(e).tags.iter().all(|r| r.claimed && r.settled));
}

#[ink::test]
fn zuma_clips_outliers() {
    let mut w = world();
    at(120, accounts().eve);
    let e = w.z.epoch();
    let t = w.submit(0, 1).unwrap();
    for (v, s) in [(10, 100), (11, 40), (12, 40)] {
        w.score(v, t, s).unwrap();
    }
    at(130, accounts().eve);
    // V = 4, need 2: consensus 40, the 100 counts as 40 -> rank 120
    assert_eq!(w.z.settle(e, t), Ok((40, 120)));
}

#[ink::test]
fn claim_needs_every_tag_settled() {
    let mut w = world();
    at(120, accounts().eve);
    let e = w.z.epoch();
    let a = w.submit(0, 1).unwrap();
    w.submit(1, 2).unwrap();
    at(130, accounts().eve);
    w.z.settle(e, a).unwrap();
    assert_eq!(w.claim(0, e, accounts().bob), Err(Error::EpochNotSettled));
}

#[ink::test]
fn one_action_per_scope() {
    let mut w = world();
    at(120, accounts().eve);
    let t = w.submit(0, 1).unwrap();
    assert_eq!(w.submit(0, 2), Err(Error::AlreadySubmitted));
    w.score(10, t, 50).unwrap();
    assert_eq!(w.score(10, t, 60), Err(Error::AlreadyScored));
    // next epoch: a fresh, unlinkable tag for the same miner
    at(130, accounts().eve);
    assert_ne!(w.submit(0, 1).unwrap(), t);
}

#[ink::test]
fn rings_are_checked() {
    let mut w = world();
    at(120, accounts().eve);
    let ring = sorted(&w.miners);
    let scope = Felt::from_bytes_be(&w.z.mine_scope(w.z.epoch()));
    let s = signer::sign(&ring, &sec(0), &scope, &Felt::ONE, &Felt::from(5u64), &fill(4, 1));
    let (r, sig) = wire(&ring, &s, None);
    let mut swapped = r.clone();
    swapped.swap(0, 1);
    assert_eq!(w.z.submit(swapped, b(&Felt::ONE), sig.clone()), Err(Error::RingNotSorted));
    let padded = [r.clone(), alloc::vec![r[3]]].concat();
    assert_eq!(w.z.submit(padded, b(&Felt::ONE), sig.clone()), Err(Error::RingNotSorted));
    assert_eq!(w.z.submit(r[..2].to_vec(), b(&Felt::ONE), sig.clone()), Err(Error::RingBelowMin));
    // validators cannot sign as miners
    let vr: Vec<Point> = sorted(&w.vals).iter().map(p).collect();
    assert_eq!(w.z.submit(vr, b(&Felt::ONE), sig.clone()), Err(Error::NonMember));
    // a different message (work) breaks the signature
    assert_eq!(w.z.submit(r, b(&Felt::TWO), sig), Err(Error::BadSignature));
}

#[ink::test]
fn forged_claim_to_other_recipient_rejected() {
    let mut w = world();
    at(120, accounts().eve);
    let e = w.z.epoch();
    let t = w.submit(0, 1).unwrap();
    at(130, accounts().eve);
    w.z.settle(e, t).unwrap();
    let ring = sorted(&w.miners);
    let scope = Felt::from_bytes_be(&w.z.mine_scope(e));
    let msg = Felt::from_bytes_be(&w.z.recipient_felt(accounts().bob));
    let s = signer::sign(&ring, &sec(0), &scope, &msg, &Felt::from(9u64), &fill(4, 3));
    let (r, sig) = wire(&ring, &s, None);
    assert_eq!(w.z.claim(e, r, accounts().frank, sig), Err(Error::BadSignature));
}

#[ink::test]
fn registry_and_admin() {
    let mut w = world();
    let k = p(&signer::public_key(&Felt::from(99u64)));
    test::set_caller::<E>(accounts().bob);
    assert_eq!(w.z.register(ROLE_MINER, k), Err(Error::RegistryClosed));
    assert_eq!(w.z.set_open(true), Err(Error::OnlyOwner));
    test::set_caller::<E>(accounts().alice);
    w.z.set_open(true).unwrap();
    test::set_caller::<E>(accounts().bob);
    assert_eq!(w.z.register(ROLE_MINER, k), Ok(4));
    assert_eq!(w.z.register(ROLE_VALIDATOR, k), Err(Error::AlreadyRegistered));
    assert_eq!(w.z.register(3, k), Err(Error::BadRole));
    let off = Point { x: k.x, y: b(&(Felt::from_bytes_be(&k.y) + Felt::ONE)) };
    assert_eq!(w.z.register(ROLE_MINER, off), Err(Error::NotOnCurve));
}

#[ink::test]
fn score_range_and_unknown_tag() {
    let mut w = world();
    at(120, accounts().eve);
    let t = w.submit(0, 1).unwrap();
    assert_eq!(w.score(10, t, 101), Err(Error::ScoreRange));
    assert_eq!(w.score(10, [0u8; 32], 5), Err(Error::NoSuchTag));
}

#[test]
fn sparse_settle_matches_dense_reference() {
    // the dense walk of core/yuma.py over 101 buckets
    fn dense(scores: &[u8], v: u32) -> (u8, u128) {
        let mut h = [0u32; 101];
        scores.iter().for_each(|&s| h[s as usize] += 1);
        let n = scores.len() as u32;
        let k = crate::need(v.max(n));
        if k == 0 || n < k {
            return (0, 0);
        }
        let total: u128 = scores.iter().map(|&s| s as u128).sum();
        let (mut above, mut mass) = (0u128, 0u128);
        for s in (0..=100u8).rev() {
            above += h[s as usize] as u128;
            mass += h[s as usize] as u128 * s as u128;
            if above >= k as u128 {
                return (s, total - mass + s as u128 * above);
            }
        }
        (0, 0)
    }
    let mut seed = 7u64;
    for _ in 0..500 {
        seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        let n = (seed >> 33) as usize % 12;
        let v = (seed >> 40) as u32 % 14;
        let scores: Vec<u8> = (0..n).map(|i| ((seed >> (i % 7 * 8)) % 101) as u8).collect();
        let mut sparse: Vec<(u8, u32)> = Vec::new();
        for &s in &scores {
            match sparse.binary_search_by_key(&s, |h| h.0) {
                Ok(i) => sparse[i].1 += 1,
                Err(i) => sparse.insert(i, (s, 1)),
            }
        }
        let sum = scores.iter().map(|&s| s as u128).sum();
        assert_eq!(crate::yuma_settle(&sparse, v, sum), dense(&scores, v), "{scores:?} V={v}");
    }
}
