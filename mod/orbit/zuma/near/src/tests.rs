//! Unit tests. The fixture (tests/fixture.json) was signed by the CAIRO
//! prover: if Rust accepts it, the two chains verify the same signatures.
use super::*;
use lsag::signer;
use near_sdk::test_utils::{accounts, VMContextBuilder};
use near_sdk::testing_env;
use serde_json::Value;

const FIXTURE: &str = include_str!("../tests/fixture.json");

fn fx() -> Value {
    serde_json::from_str(FIXTURE).unwrap()
}

fn fe(v: &Value) -> Felt {
    felt(v.as_str().unwrap()).unwrap()
}

fn fpt(v: &Value) -> Pt {
    Pt { x: fe(&v["x"]), y: fe(&v["y"]) }
}

fn fsig(v: &Value) -> Sig {
    Sig {
        c0: fe(&v["c0"]),
        s: v["s"].as_array().unwrap().iter().map(fe).collect(),
        image: fpt(&v["image"]),
    }
}

// ---- wire compatibility with Cairo -----------------------------------------

#[test]
fn hash_to_point_matches_cairo() {
    for row in fx()["hash_to_point"].as_array().unwrap() {
        let p = lsag::hash_to_point(&fe(&row["scope"]));
        assert_eq!(p, fpt(row), "Hp({})", row["scope"]);
    }
}

#[test]
fn keys_and_ids_match_cairo() {
    for k in fx()["keys"].as_array().unwrap() {
        let p = signer::public_key(&Felt::from(k["secret"].as_u64().unwrap()));
        assert_eq!(p, fpt(k));
        assert_eq!(lsag::key_id(&p), fe(&k["key_id"]));
    }
}

#[test]
fn scopes_match_cairo() {
    let x = fx();
    let m = &x["scope_mine"];
    let s = lsag::h(&[lsag::short("zuma-mine"), fe(&m["subnet"]), Felt::from(m["epoch"].as_u64().unwrap())]);
    assert_eq!(s, fe(&m["scope"]));
    let c = &x["score_scope"];
    let s = lsag::h(&[
        lsag::short("zuma-score"),
        fe(&c["subnet"]),
        Felt::from(c["epoch"].as_u64().unwrap()),
        fe(&c["tag"]),
    ]);
    assert_eq!(s, fe(&c["scope"]));
}

#[test]
fn cairo_signature_verifies_in_rust() {
    let x = fx();
    assert_eq!(x["verify_cairo"], serde_json::json!([1]));
    let ring: Vec<Pt> = x["sig"]["ring"].as_array().unwrap().iter().map(fpt).collect();
    let scope = fe(&x["scope_mine"]["scope"]);
    let msg = fe(&x["msg"]);
    let sig = fsig(&x["sig"]);
    assert!(lsag::verify(&ring, &scope, &msg, &sig));
    assert_eq!(hex(&lsag::key_id(&sig.image)), x["sig"]["tag"].as_str().unwrap());
    // the image is signer 22's key image: linkable, but only by the key holder
    assert_eq!(signer::key_image(&Felt::from(22u64), &scope), sig.image);
    // any change breaks it
    assert!(!lsag::verify(&ring, &scope, &(msg + Felt::ONE), &sig));
    assert!(!lsag::verify(&ring, &(scope + Felt::ONE), &msg, &sig));
    let mut bad = fsig(&x["sig"]);
    bad.s[2] += Felt::ONE;
    assert!(!lsag::verify(&ring, &scope, &msg, &bad));
    let mut swapped = ring.clone();
    swapped.swap(0, 1);
    assert!(!lsag::verify(&swapped, &scope, &msg, &sig));
}

#[test]
fn rust_sign_roundtrip_and_linkability() {
    let mut ring: Vec<Pt> = (1..=5u64).map(|s| signer::public_key(&Felt::from(s * 1000 + 7))).collect();
    signer::sort_ring(&mut ring);
    let scope = Felt::from(42u64);
    let fill: Vec<Felt> = (0..5u64).map(|i| Felt::from(9000 + i)).collect();
    let a = signer::sign(&ring, &Felt::from(3007u64), &scope, &Felt::from(1u64), &Felt::from(77u64), &fill);
    let b = signer::sign(&ring, &Felt::from(3007u64), &scope, &Felt::from(2u64), &Felt::from(78u64), &fill);
    assert!(lsag::verify(&ring, &scope, &Felt::from(1u64), &a));
    assert!(lsag::verify(&ring, &scope, &Felt::from(2u64), &b));
    assert_eq!(a.image, b.image, "same key + same scope = same image");
    let c = signer::sign(&ring, &Felt::from(3007u64), &Felt::from(43u64), &Felt::from(1u64), &Felt::from(79u64), &fill);
    assert_ne!(a.image, c.image, "other scope = unlinkable image");
}

#[test]
fn felt_parsing_is_strict() {
    assert!(felt("0x0").is_some());
    assert!(felt("0x800000000000011000000000000000000000000000000000000000000000001").is_none()); // = PRIME
    assert!(felt("0x800000000000011000000000000000000000000000000000000000000000000").is_some()); // PRIME - 1
    assert!(felt("0xzz").is_none());
    assert_eq!(hex(&felt("0x00ab").unwrap()), "0xab");
}

// ---- the contract, one epoch end to end ------------------------------------

const TEMPO: u64 = 10;

fn at(block: u64, who: AccountId) {
    testing_env!(VMContextBuilder::new()
        .current_account_id("zuma.test.near".parse().unwrap())
        .predecessor_account_id(who)
        .block_height(block)
        .prepaid_gas(near_sdk::Gas::from_tgas(300))
        .build());
}

fn p(q: &Pt) -> Point {
    Point { x: hex(&q.x), y: hex(&q.y) }
}

fn sec(i: u64) -> Felt {
    Felt::from(0x5eed_0000 + i)
}

struct World {
    z: Zuma,
    miners: Vec<Pt>,
    vals: Vec<Pt>,
}

fn world() -> World {
    at(100, accounts(0));
    let mut z = Zuma::new(accounts(0), TEMPO, 3, U128(1000));
    let miners: Vec<Pt> = (0..4).map(|i| signer::public_key(&sec(i))).collect();
    let vals: Vec<Pt> = (10..14).map(|i| signer::public_key(&sec(i))).collect();
    for m in &miners {
        z.register(ROLE_MINER, p(m));
    }
    for v in &vals {
        z.register(ROLE_VALIDATOR, p(v));
    }
    World { z, miners, vals }
}

fn sorted(v: &[Pt]) -> Vec<Pt> {
    let mut r = v.to_vec();
    signer::sort_ring(&mut r);
    r
}

fn wire(ring: &[Pt], s: &Sig) -> (Vec<Point>, Signature) {
    (
        ring.iter().map(p).collect(),
        Signature { c0: hex(&s.c0), s: s.s.iter().map(hex).collect(), image: p(&s.image), hp: None },
    )
}

fn fill(n: usize, salt: u64) -> Vec<Felt> {
    (0..n as u64).map(|i| Felt::from(salt * 100 + i + 1)).collect()
}

impl World {
    fn submit(&mut self, i: u64, work: u64) -> String {
        let ring = sorted(&self.miners);
        let scope = felt(&self.z.mine_scope(self.z.epoch())).unwrap();
        let s = signer::sign(&ring, &sec(i), &scope, &Felt::from(work), &Felt::from(500 + i), &fill(ring.len(), i));
        let (r, w) = wire(&ring, &s);
        self.z.submit(r, hex(&Felt::from(work)), w)
    }

    fn score(&mut self, v: u64, tag: &str, score: u8) -> String {
        let ring = sorted(&self.vals);
        let scope = felt(&self.z.score_scope(self.z.epoch(), tag.into())).unwrap();
        let s = signer::sign(&ring, &sec(v), &scope, &Felt::from(score), &Felt::from(700 + v), &fill(ring.len(), v));
        let (r, w) = wire(&ring, &s);
        self.z.score(r, tag.into(), score, w)
    }

    fn claim(&mut self, i: u64, epoch: u64, to: AccountId) -> U128 {
        let ring = sorted(&self.miners);
        let scope = felt(&self.z.mine_scope(epoch)).unwrap();
        let msg = text_felt(to.as_str());
        let s = signer::sign(&ring, &sec(i), &scope, &msg, &Felt::from(900 + i), &fill(ring.len(), i + 50));
        let (r, w) = wire(&ring, &s);
        self.z.claim(epoch, r, to, w)
    }
}

#[test]
fn full_epoch() {
    let mut w = world();
    assert_eq!(w.z.members(ROLE_MINER).len(), 4);
    assert_eq!(w.z.params().subnet, hex(&text_felt("zuma.test.near")));
    at(120, accounts(5)); // a relayer posts everything
    let e = w.z.epoch();
    let tags: Vec<String> = (0..3).map(|i| w.submit(i, 0xa0 + i)).collect();
    for v in 10..14 {
        for (k, t) in tags.iter().enumerate() {
            w.score(v, t, 90 - 20 * k as u8);
        }
    }
    let t = w.z.tally(e);
    assert_eq!(t.total.0, 4 * (90 + 70 + 50));
    assert_eq!(t.tags[0].score_sum.0, 360);
    at(130, accounts(5));
    let paid = w.claim(0, e, accounts(3));
    assert_eq!(paid.0, 1000 * 360 / 840);
    assert_eq!(w.z.earned(accounts(3)).0, paid.0);
    assert!(w.z.tally(e).tags[0].claimed);
}

#[test]
#[should_panic(expected = "already claimed")]
fn double_claim_rejected() {
    let mut w = world();
    at(120, accounts(5));
    let e = w.z.epoch();
    let t = w.submit(1, 1);
    w.score(10, &t, 50);
    at(130, accounts(5));
    w.claim(1, e, accounts(3));
    w.claim(1, e, accounts(4)); // a different recipient changes nothing: same tag
}

#[test]
#[should_panic(expected = "already submitted this epoch")]
fn one_submission_per_miner_per_epoch() {
    let mut w = world();
    at(120, accounts(5));
    w.submit(2, 1);
    w.submit(2, 2);
}

#[test]
fn next_epoch_gives_a_fresh_unlinkable_tag() {
    let mut w = world();
    at(120, accounts(5));
    let a = w.submit(2, 1);
    at(130, accounts(5));
    let b = w.submit(2, 1);
    assert_ne!(a, b);
}

#[test]
#[should_panic(expected = "already scored this tag")]
fn one_score_per_validator_per_tag() {
    let mut w = world();
    at(120, accounts(5));
    let t = w.submit(0, 1);
    w.score(11, &t, 10);
    w.score(11, &t, 99);
}

#[test]
#[should_panic(expected = "epoch still open")]
fn claim_waits_for_epoch_close() {
    let mut w = world();
    at(120, accounts(5));
    let e = w.z.epoch();
    w.submit(0, 1);
    w.claim(0, e, accounts(3));
}

#[test]
#[should_panic(expected = "ring has a non-member")]
fn validators_cannot_sign_as_miners() {
    let mut w = world();
    at(120, accounts(5));
    let mut ring = sorted(&[w.vals[0], w.vals[1], w.miners[0]]);
    signer::sort_ring(&mut ring);
    let scope = felt(&w.z.mine_scope(w.z.epoch())).unwrap();
    let s = signer::sign(&ring, &sec(10), &scope, &Felt::ONE, &Felt::from(3u64), &fill(3, 1));
    let (r, sg) = wire(&ring, &s);
    w.z.submit(r, "0x1".into(), sg);
}

#[test]
#[should_panic(expected = "ring not sorted/unique")]
fn padded_ring_rejected() {
    let mut w = world();
    at(120, accounts(5));
    let ring = vec![w.miners[0], w.miners[0], w.miners[1]];
    let sig = Signature { c0: "0x1".into(), s: vec!["0x1".into(); 3], image: p(&w.miners[0]), hp: None };
    w.z.submit(ring.iter().map(p).collect(), "0x1".into(), sig);
}

#[test]
#[should_panic(expected = "ring below min_ring")]
fn small_ring_rejected() {
    let mut w = world();
    at(120, accounts(5));
    let ring = sorted(&w.miners[..2]);
    let sig = Signature { c0: "0x1".into(), s: vec!["0x1".into(); 2], image: p(&w.miners[0]), hp: None };
    w.z.submit(ring.iter().map(p).collect(), "0x1".into(), sig);
}

#[test]
#[should_panic(expected = "bad ring signature")]
fn forged_claim_to_other_recipient_rejected() {
    let mut w = world();
    at(120, accounts(5));
    let e = w.z.epoch();
    let t = w.submit(0, 1);
    w.score(10, &t, 50);
    at(130, accounts(5));
    // sign for accounts(3), post it naming accounts(4)
    let ring = sorted(&w.miners);
    let scope = felt(&w.z.mine_scope(e)).unwrap();
    let s = signer::sign(&ring, &sec(0), &scope, &text_felt(accounts(3).as_str()), &Felt::from(5u64), &fill(4, 9));
    let (r, sg) = wire(&ring, &s);
    w.z.claim(e, r, accounts(4), sg);
}

#[test]
#[should_panic(expected = "registry closed")]
fn closed_registry() {
    let mut w = world();
    at(120, accounts(5));
    w.z.register(ROLE_MINER, p(&signer::public_key(&Felt::from(99u64))));
}

#[test]
#[should_panic(expected = "score is 0..100")]
fn score_range() {
    let mut w = world();
    at(120, accounts(5));
    let t = w.submit(0, 1);
    w.score(10, &t, 101);
}

// ---- fast arithmetic (fp.rs) == the reference group law ---------------------

use crate::fp::{self, Aff, Fp, Jac};
use starknet_types_core::curve::ProjectivePoint;

/// Deterministic pseudo-random felts (xorshift; no rand dep).
fn rnd(seed: &mut u64) -> Felt {
    let mut b = [0u8; 32];
    for c in b.chunks_mut(8) {
        *seed ^= *seed << 13;
        *seed ^= *seed >> 7;
        *seed ^= *seed << 17;
        c.copy_from_slice(&seed.to_be_bytes());
    }
    b[0] &= 0x07;
    Felt::from_bytes_be(&b)
}

fn ref_pt(p: &ProjectivePoint) -> Pt {
    match p.to_affine() {
        Ok(a) if !p.is_identity() => Pt { x: a.x(), y: a.y() },
        _ => Pt { x: Felt::ZERO, y: Felt::ZERO },
    }
}

fn fast_pt(j: &Jac) -> Pt {
    let a = fp::normalize(&[*j])[0];
    if a.inf { Pt { x: Felt::ZERO, y: Felt::ZERO } } else { Pt { x: a.x.to_felt(), y: a.y.to_felt() } }
}

fn aff_of(p: &Pt) -> Aff {
    Aff { x: Fp::from_felt(&p.x), y: Fp::from_felt(&p.y), inf: false }
}

#[test]
fn field_ops_match_felt() {
    let mut s = 0x1234_5678_9abc_def1u64;
    let edge = [Felt::ZERO, Felt::ONE, Felt::MAX, Felt::MAX - Felt::ONE, Felt::from(2u64)];
    let mut xs: Vec<Felt> = edge.to_vec();
    xs.extend((0..200).map(|_| rnd(&mut s)));
    for w in xs.windows(2) {
        let (a, b) = (w[0], w[1]);
        let (fa, fb) = (Fp::from_felt(&a), Fp::from_felt(&b));
        assert_eq!(fa.to_felt(), a, "roundtrip");
        assert_eq!(fa.mul(&fb).to_felt(), a * b, "mul {a} {b}");
        assert_eq!(fa.add(&fb).to_felt(), a + b, "add");
        assert_eq!(fa.sq().to_felt(), a * a, "sq");
        assert_eq!(fa.sub(&fb).to_felt(), a - b, "sub");
        if a != Felt::ZERO {
            assert_eq!(fa.inv().to_felt(), a.inverse().unwrap(), "inv");
        }
    }
    for e in edge {
        for f in edge {
            assert_eq!(Fp::from_felt(&e).mul(&Fp::from_felt(&f)).to_felt(), e * f);
        }
    }
}

#[test]
fn double_scalar_mul_matches_reference() {
    let mut s = 77u64;
    let g = lsag::generator();
    for _ in 0..12 {
        let (k1, k2, a, b) = (rnd(&mut s), rnd(&mut s), rnd(&mut s), rnd(&mut s));
        let (pa, pb) = (&g * k1, &g * k2);
        let (aa, ab) = (ref_pt(&pa), ref_pt(&pb));
        let want = ref_pt(&(&(&pa * a) + &(&pb * b)));
        let got = fast_pt(&fp::mul2(&fp::nibbles(&a), &fp::table(&aff_of(&aa)),
                                    &fp::nibbles(&b), &fp::table(&aff_of(&ab))));
        assert_eq!(got, want);
    }
}

#[test]
fn group_law_edge_cases_match_reference() {
    let g = lsag::generator();
    let gp = ref_pt(&g);
    let tg = fp::table(&aff_of(&gp));
    let neg = Pt { x: gp.x, y: -gp.y };
    let tn = fp::table(&aff_of(&neg));
    let (one, two, zero) = (Felt::ONE, Felt::from(2u64), Felt::ZERO);
    let n = |f: &Felt| fp::nibbles(f);
    // a*G + b*G (same base: the add hits the doubling branch)
    for (a, b) in [(one, one), (two, two), (one, two), (Felt::from(15u64), Felt::from(15u64))] {
        let want = ref_pt(&(&g * (a + b)));
        assert_eq!(fast_pt(&fp::mul2(&n(&a), &tg, &n(&b), &tg)), want, "{a}+{b}");
    }
    // a*G + a*(-G) = infinity -> (0,0), as the Cairo/ref serialization
    for a in [one, Felt::from(7u64), Felt::from(123456789u64)] {
        assert_eq!(fast_pt(&fp::mul2(&n(&a), &tg, &n(&a), &tn)), Pt { x: zero, y: zero });
    }
    // zero scalars
    assert_eq!(fast_pt(&fp::mul2(&n(&zero), &tg, &n(&zero), &tg)), Pt { x: zero, y: zero });
    assert_eq!(fast_pt(&fp::mul2(&n(&zero), &tg, &n(&one), &tg)), gp);
    // scalar >= ORDER wraps exactly like the reference (Cairo EC semantics)
    let big = Felt::MAX;
    assert_eq!(fast_pt(&fp::mul2(&n(&big), &tg, &n(&zero), &tg)), ref_pt(&(&g * big)));
}

#[test]
fn hinted_hash_to_point_never_changes_the_point() {
    for sc in [0u64, 1, 42, 7777, 123456] {
        let scope = Felt::from(sc);
        let want = lsag::hash_to_point(&scope);
        assert_eq!(lsag::hash_to_point_hinted(&scope, None), want);
        assert_eq!(lsag::hash_to_point_hinted(&scope, Some(&want)), want);
        // the other root, a wrong x, garbage: all ignored
        let other = Pt { x: want.x, y: -want.y };
        assert_eq!(lsag::hash_to_point_hinted(&scope, Some(&other)), want);
        let junk = Pt { x: Felt::from(5u64), y: Felt::from(9u64) };
        assert_eq!(lsag::hash_to_point_hinted(&scope, Some(&junk)), want);
    }
}

#[test]
fn fast_verify_agrees_with_reference() {
    let mut s = 4242u64;
    let mut ring: Vec<Pt> = (1..=4u64).map(|i| signer::public_key(&Felt::from(i * 31337))).collect();
    signer::sort_ring(&mut ring);
    for k in 0..4u64 {
        let scope = rnd(&mut s);
        let msg = rnd(&mut s);
        let fill: Vec<Felt> = (0..4).map(|_| rnd(&mut s)).collect();
        let sig = signer::sign(&ring, &Felt::from((k + 1) * 31337), &scope, &msg, &rnd(&mut s), &fill);
        assert!(lsag::verify_ref(&ring, &scope, &msg, &sig));
        assert!(lsag::verify(&ring, &scope, &msg, &sig));
        let hp = lsag::hash_to_point(&scope);
        assert!(lsag::verify_hinted(&ring, &scope, &msg, &sig, Some(&hp)));
        // tamper: every verifier must reject
        let bad = Sig { c0: sig.c0 + Felt::ONE, s: sig.s.clone(), image: sig.image };
        assert!(!lsag::verify_ref(&ring, &scope, &msg, &bad));
        assert!(!lsag::verify(&ring, &scope, &msg, &bad));
        assert!(!lsag::verify(&ring, &scope, &(msg + Felt::ONE), &sig));
    }
}

#[test]
fn hp_hint_from_the_view_is_accepted() {
    let mut w = world();
    let ring = sorted(&w.miners);
    at(5 * TEMPO, accounts(1));
    let e = w.z.epoch();
    let scope_hex = w.z.mine_scope(e);
    let scope = felt(&scope_hex).unwrap();
    let s = signer::sign(&ring, &sec(0), &scope, &Felt::from(9u64), &Felt::from(11u64), &fill(ring.len(), 3));
    let (r, mut sg) = wire(&ring, &s);
    sg.hp = Some(w.z.hash_point(scope_hex));
    let tag = w.z.submit(r, "0x9".into(), sg);
    assert_eq!(tag, hex(&lsag::key_id(&s.image)));
}

#[test]
fn poseidon_matches_reference() {
    let mut s = 99u64;
    for n in 0..9usize {
        let v: Vec<Felt> = (0..n).map(|_| rnd(&mut s)).collect();
        assert_eq!(lsag::h(&v), lsag::h_ref(&v), "len {n}");
    }
    let edge = [Felt::ZERO, Felt::MAX, Felt::ONE];
    assert_eq!(lsag::h(&edge), lsag::h_ref(&edge));
}
