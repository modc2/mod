//! Unit tests: the Cairo-signed fixture verifies through this contract's
//! wire, and a whole epoch runs through `step` with real signatures (the
//! NEAR crate's native signer), reverts included.
use super::*;
use lsag::signer;
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../near/contract/tests/fixture.json");

fn fe(v: &Value) -> Felt {
    lsag::felt(v.as_str().unwrap()).unwrap()
}

fn fpt(v: &Value) -> Pt {
    Pt { x: fe(&v["x"]), y: fe(&v["y"]) }
}

fn sig_bytes(w: &mut Wr, ring: &[Pt], sig: &Sig, hint: Option<Pt>) {
    w.u8(ring.len() as u8);
    for p in ring {
        w.pt(p);
    }
    w.felt(&sig.c0);
    for x in &sig.s {
        w.felt(x);
    }
    w.pt(&sig.image);
    w.pt(&hint.unwrap_or(Pt { x: Felt::ZERO, y: Felt::ZERO }));
}

struct Net {
    state: Vec<u8>,
    height: u64,
}

impl Net {
    fn run(&mut self, op: u8, args: impl FnOnce(&mut Wr)) -> R<()> {
        let mut w = Wr::default();
        w.u8(op);
        w.u64(self.height);
        args(&mut w);
        self.state = step(&self.state, &w.0)?;
        Ok(())
    }
    fn st(&self) -> State {
        State::read(&self.state).unwrap()
    }
}

fn keys(base: u64, n: u64) -> Vec<(Felt, Pt)> {
    (0..n).map(|i| {
        let x = Felt::from(base + i);
        (x, signer::public_key(&x))
    }).collect()
}

fn ring_of(ks: &[(Felt, Pt)]) -> Vec<Pt> {
    let mut r: Vec<Pt> = ks.iter().map(|k| k.1).collect();
    signer::sort_ring(&mut r);
    r
}

fn sign(ring: &[Pt], x: &Felt, scope: &Felt, msg: &Felt, salt: u64) -> Sig {
    let fill: Vec<Felt> = (0..ring.len() as u64).map(|i| Felt::from(salt * 1000 + i + 7)).collect();
    signer::sign(ring, x, scope, msg, &Felt::from(salt + 99), &fill)
}

const SUBNET: u64 = 0xabc;

fn net() -> Net {
    let mut n = Net { state: vec![], height: 0 };
    n.run(0x00, |w| {
        w.felt(&Felt::from(SUBNET));
        w.u64(10);
        w.u32(3);
        w.u128(1000);
    }).unwrap();
    n
}

#[test]
fn cairo_signature_verifies_through_the_wire() {
    let x: Value = serde_json::from_str(FIXTURE).unwrap();
    let ring: Vec<Pt> = x["sig"]["ring"].as_array().unwrap().iter().map(fpt).collect();
    let sig = Sig {
        c0: fe(&x["sig"]["c0"]),
        s: x["sig"]["s"].as_array().unwrap().iter().map(fe).collect(),
        image: fpt(&x["sig"]["image"]),
    };
    let mut w = Wr::default();
    sig_bytes(&mut w, &ring, &sig, None);
    let (r2, s2, h2) = read_sig(&mut Rd::new(&w.0)).unwrap();
    assert!(h2.is_none());
    let scope = fe(&x["scope_mine"]["scope"]);
    let mut st = State { subnet: fe(&x["scope_mine"]["subnet"]), tempo: 1, ..Default::default() };
    assert_eq!(st.scope_mine(x["scope_mine"]["epoch"].as_u64().unwrap()), scope);
    st.subnet = fe(&x["score_scope"]["subnet"]);
    assert_eq!(st.scope_score(7, &fe(&x["score_scope"]["tag"])), fe(&x["score_scope"]["scope"]));
    assert!(verify(&r2, &scope, &fe(&x["msg"]), &s2, None).is_ok());
    assert_eq!(verify(&r2, &scope, &(fe(&x["msg"]) + Felt::ONE), &s2, None), Err(E_SIG));
    // a right hint is accepted, a wrong one only costs the sqrt
    let hp = lsag::hash_to_point(&scope);
    assert!(verify(&r2, &scope, &fe(&x["msg"]), &s2, Some(hp)).is_ok());
    let bad = Pt { x: hp.x, y: -hp.y };
    assert!(verify(&r2, &scope, &fe(&x["msg"]), &s2, Some(bad)).is_ok());
}

#[test]
fn full_epoch_and_every_revert() {
    let mut n = net();
    assert_eq!(n.run(0x00, |w| { w.felt(&Felt::ONE); w.u64(1); w.u32(1); w.u128(1); }), Err(E_INIT));
    let miners = keys(100, 3);
    let vals = keys(200, 4);
    for (_, p) in &miners {
        n.run(0x01, |w| { w.u8(1); w.pt(p); }).unwrap();
    }
    for (_, p) in &vals {
        n.run(0x01, |w| { w.u8(2); w.pt(p); }).unwrap();
    }
    assert_eq!(n.run(0x01, |w| { w.u8(2); w.pt(&miners[0].1); }), Err(E_REGISTERED));
    assert_eq!(n.run(0x01, |w| { w.u8(3); w.pt(&miners[0].1); }), Err(E_ROLE));
    assert_eq!(n.run(0x01, |w| { w.u8(1); w.pt(&Pt { x: Felt::ONE, y: Felt::ONE }); }), Err(E_CURVE));

    n.height = 20; // epoch 2
    let st = n.st();
    let mring = ring_of(&miners);
    let vring = ring_of(&vals);
    let mut tags = vec![];
    for (i, (x, _)) in miners.iter().enumerate() {
        let work = Felt::from(0x77 + i as u64);
        let scope = st.scope_mine(2);
        let sig = sign(&mring, x, &scope, &work, i as u64);
        let hp = lsag::hash_to_point(&scope);
        n.run(0x03, |w| { w.felt(&work); sig_bytes(w, &mring, &sig, Some(hp)); }).unwrap();
        tags.push(lsag::key_id(&sig.image));
        if i == 0 {
            let again = sign(&mring, x, &scope, &Felt::from(5u64), 50);
            assert_eq!(n.run(0x03, |w| { w.felt(&Felt::from(5u64)); sig_bytes(w, &mring, &again, None); }),
                       Err(E_DUP_SUBMIT));
            assert_eq!(n.run(0x03, |w| { w.felt(&(work + Felt::ONE)); sig_bytes(w, &mring, &sig, None); }),
                       Err(E_SIG));
            let small = &mring[..2];
            assert_eq!(n.run(0x03, |w| { w.felt(&work); sig_bytes(w, small, &sig, None); }), Err(E_RING_SMALL));
            let mut rev = mring.clone();
            rev.reverse();
            assert_eq!(n.run(0x03, |w| { w.felt(&work); sig_bytes(w, &rev, &sig, None); }), Err(E_RING_ORDER));
            assert_eq!(n.run(0x03, |w| { w.felt(&work); sig_bytes(w, &vring[..3], &sig, None); }), Err(E_NON_MEMBER));
        }
    }
    // validators: tag 0 gets 80,80,90,100 -> C = 90 (need 2), R = 80+80+90+90
    let grid: [[u8; 4]; 3] = [[80, 80, 90, 100], [50, 60, 0, 0], [10, 0, 0, 0]];
    let mut want = vec![];
    for (t, tag) in tags.iter().enumerate() {
        let mut scores = vec![];
        for (v, (x, _)) in vals.iter().enumerate() {
            let sc = grid[t][v];
            if t > 0 && sc == 0 {
                continue; // did not score
            }
            scores.push(sc as u32);
            let scope = st.scope_score(2, tag);
            let sig = sign(&vring, x, &scope, &Felt::from(sc), 300 + (t * 4 + v) as u64);
            n.run(0x04, |w| { w.felt(tag); w.u8(sc); sig_bytes(w, &vring, &sig, None); }).unwrap();
            if v == 0 && t == 0 {
                let again = sign(&vring, x, &scope, &Felt::from(1u64), 999);
                assert_eq!(n.run(0x04, |w| { w.felt(tag); w.u8(1); sig_bytes(w, &vring, &again, None); }),
                           Err(E_DUP_SCORE));
                assert_eq!(n.run(0x04, |w| { w.felt(tag); w.u8(101); sig_bytes(w, &vring, &again, None); }),
                           Err(E_SCORE_RANGE));
                assert_eq!(n.run(0x04, |w| { w.felt(&Felt::from(5u64)); w.u8(1); sig_bytes(w, &vring, &again, None); }),
                           Err(E_NO_TAG));
            }
        }
        want.push(scores);
    }
    assert_eq!(n.st().epochs[0].nulls.len(), 4 + 2 + 1);
    assert_eq!(n.run(0x05, |w| { w.u64(2); w.felt(&tags[0]); }), Err(E_OPEN_EPOCH));

    n.height = 30; // epoch 3: epoch 2 closed, its nullifiers dropped
    assert_eq!(n.st().epochs[0].nulls.len(), 7, "pruned on the next run, not before");
    n.run(0x07, |_| {}).unwrap();
    assert!(n.st().epochs[0].nulls.is_empty());
    let recip = Felt::from(0xbeefu64);
    let csig = sign(&mring, &miners[0].0, &n.st().scope_mine(2), &recip, 77);
    assert_eq!(n.run(0x06, |w| { w.u64(2); w.felt(&recip); sig_bytes(w, &mring, &csig, None); }),
               Err(E_UNSETTLED));
    for t in &tags {
        n.run(0x05, |w| { w.u64(2); w.felt(t); }).unwrap();
    }
    assert_eq!(n.run(0x05, |w| { w.u64(2); w.felt(&tags[0]); }), Err(E_SETTLED));
    let ep = n.st().epochs[0].clone();
    assert_eq!(ep.vset, 4);
    let expect: Vec<(u8, u128)> = want.iter().map(|sc| {
        let mut h: Vec<(u8, u32)> = vec![];
        for &s in sc {
            match h.binary_search_by_key(&(s as u8), |x| x.0) {
                Ok(k) => h[k].1 += 1,
                Err(k) => h.insert(k, (s as u8, 1)),
            }
        }
        yuma_settle(&h, 4, sc.iter().map(|&s| s as u128).sum())
    }).collect();
    assert_eq!(expect[0], (90, 340));
    assert_eq!(expect[1], (50, 100)); // 2 of 4 scored: need 2, C = 50
    assert_eq!(expect[2], (0, 0));    // 1 of 4: below quorum
    for (t, e) in ep.tags.iter().zip(&expect) {
        assert_eq!((t.consensus, t.rank), *e);
    }
    assert_eq!(ep.rank_total, 440);

    n.run(0x06, |w| { w.u64(2); w.felt(&recip); sig_bytes(w, &mring, &csig, None); }).unwrap();
    let paid = 1000u128 * 340 / 440;
    assert_eq!(n.st().credit, vec![(recip, paid)]);
    assert_eq!(n.run(0x06, |w| { w.u64(2); w.felt(&recip); sig_bytes(w, &mring, &csig, None); }),
               Err(E_CLAIMED));
    // the claim's image is the submit's tag, from the same scope
    assert!(n.st().epochs[0].tags.iter().find(|t| t.tag == tags[0]).unwrap().claimed);

    n.height = 29;
    assert_eq!(n.run(0x07, |_| {}), Err(E_CLOCK));
    n.height = 10 * (3 + KEEP_EPOCHS);
    n.run(0x07, |_| {}).unwrap();
    assert!(n.st().epochs.is_empty(), "epochs past KEEP are pruned");
    assert_eq!(n.st().credit.len(), 1, "credits are never pruned");
}

#[test]
fn state_roundtrip_and_garbage() {
    let n = net();
    let st = n.st();
    assert_eq!(State::read(&st.write()).unwrap(), st);
    assert_eq!(State::read(b"nope"), Err(E_STATE));
    let mut extra = st.write();
    extra.push(0);
    assert_eq!(State::read(&extra), Err(E_STATE));
    assert_eq!(step(&[], &[1, 0, 0, 0, 0, 0, 0, 0, 0]), Err(E_UNINIT));
    assert_eq!(step(&st.write(), &[9, 0, 0, 0, 0, 0, 0, 0, 0]), Err(E_INPUT));
    assert_eq!(step(&[], &[0, 0, 0, 0, 0, 0, 0, 0, 0]), Err(E_INPUT));
}
