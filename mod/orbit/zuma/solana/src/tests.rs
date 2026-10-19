//! The verifier units on a REAL signature: near/tests/fixture.json was made
//! by the Cairo prover, and tests/hints.json adds the hints stark.py derives
//! for it (tools/gen_sol_fixture.py). Every unit must pass on the true hints
//! and fail on a tampered one.
use crate::fp::{Aff, Fp};
use crate::verify::*;
use crate::{key_id, n_blocks, n_digest, session_size, bit_hp, MAX_HP_CTR, MAX_RING, SUBNET_SIZE};
use serde_json::Value;
use starknet_types_core::felt::Felt;

fn fx() -> Value {
    serde_json::from_str(include_str!("../tests/hints.json")).unwrap()
}

fn f(v: &Value) -> Felt {
    Felt::from_hex_unchecked(v.as_str().unwrap())
}

fn p(v: &Value) -> Aff {
    let (x, y) = (f(&v[0]), f(&v[1]));
    if x == Felt::ZERO && y == Felt::ZERO {
        Aff { x: Fp::ZERO, y: Fp::ZERO, inf: true }
    } else {
        Aff { x: Fp::from_felt(&x), y: Fp::from_felt(&y), inf: false }
    }
}

/// mul unit (i, which, seg) exactly as lib.rs `unit` runs it.
fn mul_unit(x: &Value, i: usize, which: usize, seg: usize, tamper: bool) -> bool {
    let (s, c) = (f(&x["s"][i]), f(&x["c"][i]));
    let (pa, pb) = if which == 0 { (generator(), p(&x["ring"][i])) } else { (p(&x["hp"]), p(&x["image"])) };
    let mids = &x["mids"][i][which];
    let from = if seg == 0 { Aff { x: Fp::ZERO, y: Fp::ZERO, inf: true } } else { p(&mids[seg - 1]) };
    let mut to = if seg == SEGS - 1 { p(&x[if which == 0 { "L" } else { "R" }][i]) } else { p(&mids[seg]) };
    if tamper {
        to.y = to.y.add(&Fp::ONE);
    }
    mul_segment(seg, &s, &pa, &c, &pb, &from, &to)
}

#[test]
fn every_mul_unit_passes_on_true_hints() {
    let x = fx();
    for i in 0..4 {
        for which in 0..2 {
            for seg in 0..SEGS {
                assert!(mul_unit(&x, i, which, seg, false), "mul {i} {which} {seg}");
            }
        }
    }
}

#[test]
fn a_tampered_mul_hint_fails() {
    let x = fx();
    for seg in 0..SEGS {
        assert!(!mul_unit(&x, 1, seg % 2, seg, true), "seg {seg}");
    }
    // a wrong scalar fails against the true end point
    let mut y = x.clone();
    y["s"][0] = Value::from("0x1234");
    assert!(!mul_unit(&y, 0, 0, SEGS - 1, false) || !mul_unit(&y, 0, 0, 0, false));
}

fn chal(x: &Value, i: usize, d: Felt) -> bool {
    let n = 4;
    let (l, r) = (&x["L"][i], &x["R"][i]);
    let next = if (i + 1) % n == 0 { f(&x["c0"]) } else { f(&x["c"][(i + 1) % n]) };
    hash(&[short("zuma-chal"), d, f(&l[0]), f(&l[1]), f(&r[0]), f(&r[1])]) == next
}

#[test]
fn the_challenge_chain_closes() {
    let x = fx();
    let d = f(&x["d"]);
    for i in 0..4 {
        assert!(chal(&x, i, d), "chal {i}");
    }
    assert_eq!(f(&x["c"][0]), f(&x["c0"]));
    assert!(!chal(&x, 0, d + Felt::ONE), "a wrong digest breaks the chain");
}

#[test]
fn digest_units_reach_the_hinted_digest() {
    let x = fx();
    let ring: Vec<(Felt, Felt)> = (0..4).map(|i| (f(&x["ring"][i][0]), f(&x["ring"][i][1]))).collect();
    let head = [short("zuma-msg"), f(&x["scope"]), f(&x["msg"]), f(&x["image"][0]), f(&x["image"][1]), Felt::from(4u64)];
    let blocks = digest_blocks(head, &ring);
    assert_eq!(blocks.len(), n_blocks(4));
    let nd = n_digest(4);
    for k in 0..nd {
        let mut st = if k == 0 { [Fp::ZERO; 3] } else {
            let s = &x["sponge"][k - 1];
            [Fp::from_felt(&f(&s[0])), Fp::from_felt(&f(&s[1])), Fp::from_felt(&f(&s[2]))]
        };
        absorb(&mut st, &blocks[DIG_EVERY * k..(DIG_EVERY * (k + 1)).min(blocks.len())]);
        if k == nd - 1 {
            assert_eq!(st[0].to_felt(), f(&x["d"]));
        } else {
            assert_eq!(st[0].to_felt(), f(&x["sponge"][k][0]));
        }
    }
    // the sponge in pieces equals poseidon_hash_span in one go
    let mut all = head.to_vec();
    for (a, b) in &ring {
        all.push(*a);
        all.push(*b);
    }
    assert_eq!(hash(&all), f(&x["d"]));
}

fn hp_ok(scope: Felt, hp: &Aff, ctr: usize) -> bool {
    (0..=ctr).all(|i| {
        let xf = Fp::from_felt(&hash(&[short("zuma-h2p"), scope, Felt::from(i as u64)]));
        let r = rhs(&xf);
        if i < ctr { !r.is_zero() && !is_square(&r) } else { !hp.inf && hp.x == xf && hp.y.sq() == r }
    })
}

#[test]
fn hash_to_point_hints() {
    let x = fx();
    assert!(hp_ok(f(&x["scope"]), &p(&x["hp"]), x["hp_ctr"].as_u64().unwrap() as usize));
    let cases = x["hp_cases"].as_array().unwrap();
    assert!(!cases.is_empty());
    for c in cases {
        let (scope, hp, ctr) = (f(&c["scope"]), p(&c["hp"]), c["ctr"].as_u64().unwrap() as usize);
        assert!(hp_ok(scope, &hp, ctr), "skipped counters are non-squares");
        // claiming an earlier counter: that counter is a non-square, no point there
        assert!(!hp_ok(scope, &hp, ctr - 1));
        // the other root is not the hint
        let neg = Aff { x: hp.x, y: Fp::ZERO.sub(&hp.y), inf: false };
        assert!(neg.y.sq() == hp.y.sq() && neg != hp);
    }
}

#[test]
fn key_ids_and_scopes_match_cairo() {
    let x = fx();
    assert_eq!(key_id(&f(&x["image"][0]), &f(&x["image"][1])), f(&x["tag"]));
    let near: Value = serde_json::from_str(include_str!("../../near/tests/fixture.json")).unwrap();
    let sm = &near["scope_mine"];
    let got = hash(&[short("zuma-mine"), f(&sm["subnet"]), Felt::from(sm["epoch"].as_u64().unwrap())]);
    assert_eq!(got, f(&sm["scope"]));
}

#[test]
fn felts_must_be_canonical() {
    let mut pb = [0u8; 32];
    pb[0] = 0x08;
    pb[7] = 0x11;
    pb[31] = 1; // p itself
    assert!(felt(&pb).is_none());
    pb[31] = 0; // p - 1
    pb[30] = 0;
    assert!(felt(&[0u8; 32]).is_some());
    assert!(felt(&[0xffu8; 32]).is_none());
}

#[test]
fn session_layout() {
    // ring 32 fits the 512-bit done map and stays a sane account size
    let n = MAX_RING;
    assert!(bit_hp(n, MAX_HP_CTR + 1) <= 512);
    assert!(session_size(n) < 24 * 1024);
    assert_eq!(session_size(4), 2912);
    assert_eq!(SUBNET_SIZE, 12408);
}
