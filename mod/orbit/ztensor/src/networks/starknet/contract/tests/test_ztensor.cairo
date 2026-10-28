use snforge_std::{declare, ContractClassTrait, DeclareResultTrait, start_cheat_block_number_global};
use starknet::ContractAddress;
use ztensor::lsag;
use ztensor::lsag::{Point, Signature};
use ztensor::ztensor::{IZTensorDispatcher, IZTensorDispatcherTrait, ROLE_MINER, ROLE_VALIDATOR};

fn owner() -> ContractAddress {
    'owner'.try_into().unwrap()
}

fn deploy(tempo: u64, min_ring: u32, emission: u128) -> IZTensorDispatcher {
    let class = declare("ZTensor").unwrap().contract_class();
    let mut args: Array<felt252> = array![];
    owner().serialize(ref args);
    tempo.serialize(ref args);
    min_ring.serialize(ref args);
    emission.serialize(ref args);
    let (addr, _) = class.deploy(@args).unwrap();
    IZTensorDispatcher { contract_address: addr }
}

// secrets: miners 11..14, validators 21..24
fn keys(base: felt252) -> Array<Point> {
    array![
        lsag::public_key(base + 1),
        lsag::public_key(base + 2),
        lsag::public_key(base + 3),
        lsag::public_key(base + 4),
    ]
}

fn index_of(ring: Span<Point>, p: Point) -> usize {
    let mut i = 0;
    loop {
        if *ring.at(i) == p {
            break i;
        }
        i += 1;
    }
}

fn fill(n: usize, seed: felt252) -> Span<felt252> {
    let mut out = array![];
    let mut i: usize = 0;
    while i < n {
        out.append(core::poseidon::poseidon_hash_span(array![seed, i.into()].span()));
        i += 1;
    };
    out.span()
}

fn sign_as(ring: Span<Point>, secret: felt252, scope: felt252, msg: felt252) -> Signature {
    let me = lsag::public_key(secret);
    lsag::sign(ring, index_of(ring, me), secret, scope, msg, secret * 7919 + msg, fill(ring.len(), msg + secret))
}

fn setup() -> (IZTensorDispatcher, Span<Point>, Span<Point>) {
    let z = deploy(10, 3, 1000);
    snforge_std::start_cheat_caller_address(z.contract_address, owner());
    for k in keys(10) {
        z.register(ROLE_MINER, k);
    };
    for k in keys(20) {
        z.register(ROLE_VALIDATOR, k);
    };
    snforge_std::stop_cheat_caller_address(z.contract_address);
    let miners = lsag::sort_ring(keys(10).span()).span();
    let vals = lsag::sort_ring(keys(20).span()).span();
    (z, miners, vals)
}

#[test]
fn lsag_roundtrip_and_linkability() {
    let ring = lsag::sort_ring(keys(10).span()).span();
    let s1 = sign_as(ring, 12, 'scope-a', 'm1');
    assert(lsag::verify(ring, 'scope-a', 'm1', s1), 'valid sig rejected');
    assert(!lsag::verify(ring, 'scope-a', 'm2', s1), 'wrong msg accepted');
    assert(!lsag::verify(ring, 'scope-b', 'm1', s1), 'wrong scope accepted');
    // same key, same scope -> same image; other scope -> different image
    let s2 = sign_as(ring, 12, 'scope-a', 'other');
    assert(s1.image == s2.image, 'image not linkable in scope');
    let s3 = sign_as(ring, 12, 'scope-b', 'm1');
    assert(s1.image != s3.image, 'image linkable across scope');
    // a different signer in the same scope gets a different image
    let s4 = sign_as(ring, 13, 'scope-a', 'm1');
    assert(s1.image != s4.image, 'two keys share an image');
    assert(lsag::key_image(12, 'scope-a') == s1.image, 'key_image mismatch');
}

#[test]
fn lsag_rejects_tampering_and_outsiders() {
    let ring = lsag::sort_ring(keys(10).span()).span();
    let s = sign_as(ring, 11, 'sc', 'm');
    let bad_c = Signature { c0: s.c0 + 1, s: s.s, image: s.image };
    assert(!lsag::verify(ring, 'sc', 'm', bad_c), 'tampered c0 accepted');
    let bad_img = Signature { c0: s.c0, s: s.s, image: lsag::public_key(99) };
    assert(!lsag::verify(ring, 'sc', 'm', bad_img), 'swapped image accepted');
    // an outsider's ring (key 99 swapped in) does not verify a member's sig
    let other = lsag::sort_ring(
        array![*ring.at(0), *ring.at(1), *ring.at(2), lsag::public_key(99)].span(),
    )
        .span();
    assert(!lsag::verify(other, 'sc', 'm', s), 'ring swap accepted');
}

#[test]
fn full_epoch_anonymous_flow() {
    let (z, miners, vals) = setup();
    start_cheat_block_number_global(25); // epoch 2
    let e = z.epoch();
    assert(e == 2, 'epoch');

    // two miners submit anonymously
    let sm = z.mine_scope(e);
    let a = sign_as(miners, 11, sm, 'work-a');
    z.submit(arr(miners), 'work-a', a);
    let b = sign_as(miners, 13, sm, 'work-b');
    z.submit(arr(miners), 'work-b', b);
    let tag_a = lsag::key_id(a.image);
    let tag_b = lsag::key_id(b.image);
    assert(z.submitted(e, tag_a) && z.submitted(e, tag_b), 'not submitted');
    assert(z.epoch_tags(e) == 2, 'tag count');

    // validators score tags without knowing who is behind them
    z.score(arr(vals), tag_a, 90, sign_as(vals, 21, z.score_scope(e, tag_a), 90));
    z.score(arr(vals), tag_a, 70, sign_as(vals, 22, z.score_scope(e, tag_a), 70));
    z.score(arr(vals), tag_b, 40, sign_as(vals, 21, z.score_scope(e, tag_b), 40));
    let (sum_a, n_a) = z.tally(e, tag_a);
    assert(sum_a == 160 && n_a == 2, 'tally a');
    assert(z.epoch_total(e) == 200, 'total');

    // next epoch: Yuma. V = 4 so consensus needs 2 votes: A {90,70} -> C 70,
    // R 140; B has one vote of 4 -> no consensus, R 0
    start_cheat_block_number_global(31);
    z.settle(e, tag_a);
    z.settle(e, tag_b);
    assert(z.yuma(e, tag_a) == (70, 140, true), 'yuma a');
    assert(z.yuma(e, tag_b) == (0, 0, true), 'yuma b');
    assert(z.yuma_epoch(e) == (4, 140, 2), 'yuma epoch');
    // miner A claims to a fresh recipient, re-proving its tag
    let to: ContractAddress = 'payee'.try_into().unwrap();
    z.claim(e, arr(miners), to, sign_as(miners, 11, sm, to.into()));
    assert(z.earned(to) == 1000, 'A gets 140/140 of 1000');
    assert(z.claimed(e, tag_a), 'claimed flag');
}

#[test]
#[should_panic(expected: 'already submitted this epoch')]
fn one_submission_per_miner_per_epoch() {
    let (z, miners, _) = setup();
    let sm = z.mine_scope(z.epoch());
    z.submit(arr(miners), 'w1', sign_as(miners, 12, sm, 'w1'));
    z.submit(arr(miners), 'w2', sign_as(miners, 12, sm, 'w2'));
}

#[test]
#[should_panic(expected: 'already scored this tag')]
fn one_score_per_validator_per_tag() {
    let (z, miners, vals) = setup();
    let e = z.epoch();
    let a = sign_as(miners, 11, z.mine_scope(e), 'w');
    z.submit(arr(miners), 'w', a);
    let tag = lsag::key_id(a.image);
    let sc = z.score_scope(e, tag);
    z.score(arr(vals), tag, 50, sign_as(vals, 23, sc, 50));
    z.score(arr(vals), tag, 99, sign_as(vals, 23, sc, 99));
}

#[test]
#[should_panic(expected: 'ring has a non-member')]
fn miner_cannot_score() {
    let (z, miners, _) = setup();
    let e = z.epoch();
    let a = sign_as(miners, 11, z.mine_scope(e), 'w');
    z.submit(arr(miners), 'w', a);
    let tag = lsag::key_id(a.image);
    // a miner ring is not a validator ring
    z.score(arr(miners), tag, 100, sign_as(miners, 12, z.score_scope(e, tag), 100));
}

#[test]
#[should_panic(expected: 'ring below min_ring')]
fn small_rings_rejected() {
    let (z, miners, _) = setup();
    let ring = array![*miners.at(0), *miners.at(1)].span();
    let me = if lsag::public_key(11) == *ring.at(0) || lsag::public_key(11) == *ring.at(1) {
        11
    } else if lsag::public_key(12) == *ring.at(0) || lsag::public_key(12) == *ring.at(1) {
        12
    } else if lsag::public_key(13) == *ring.at(0) || lsag::public_key(13) == *ring.at(1) {
        13
    } else {
        14
    };
    z.submit(arr(ring), 'w', sign_as(ring, me, z.mine_scope(z.epoch()), 'w'));
}

#[test]
#[should_panic(expected: 'ring not sorted/unique')]
fn padded_rings_rejected() {
    let (z, miners, _) = setup();
    let ring = array![*miners.at(0), *miners.at(0), *miners.at(1)].span();
    let sig = Signature { c0: 0, s: array![0, 0, 0].span(), image: *miners.at(0) };
    z.submit(arr(ring), 'w', sig);
}

#[test]
#[should_panic(expected: 'epoch still open')]
fn cannot_claim_open_epoch() {
    let (z, miners, _) = setup();
    let e = z.epoch();
    let sm = z.mine_scope(e);
    z.submit(arr(miners), 'w', sign_as(miners, 11, sm, 'w'));
    let to: ContractAddress = 'x'.try_into().unwrap();
    z.claim(e, arr(miners), to, sign_as(miners, 11, sm, to.into()));
}

#[test]
#[should_panic(expected: 'registry closed')]
fn registry_is_owner_gated() {
    let (z, _, _) = setup();
    z.register(ROLE_MINER, lsag::public_key(77));
}

// two tags, every validator scores both; `b_scores` by validators 21..24
fn scored_epoch(
    a_scores: Span<u8>, b_scores: Span<u8>,
) -> (IZTensorDispatcher, Span<Point>, u64, felt252, felt252, felt252) {
    let (z, miners, vals) = setup();
    let e = z.epoch();
    let sm = z.mine_scope(e);
    let a = sign_as(miners, 11, sm, 'wa');
    z.submit(arr(miners), 'wa', a);
    let b = sign_as(miners, 12, sm, 'wb');
    z.submit(arr(miners), 'wb', b);
    let (ta, tb) = (lsag::key_id(a.image), lsag::key_id(b.image));
    let mut i: usize = 0;
    while i < a_scores.len() {
        let v: felt252 = 21 + i.into();
        let s = *a_scores.at(i);
        z.score(arr(vals), ta, s, sign_as(vals, v, z.score_scope(e, ta), s.into()));
        i += 1;
    };
    i = 0;
    while i < b_scores.len() {
        let v: felt252 = 21 + i.into();
        let s = *b_scores.at(i);
        z.score(arr(vals), tb, s, sign_as(vals, v, z.score_scope(e, tb), s.into()));
        i += 1;
    };
    (z, miners, e, sm, ta, tb)
}

#[test]
fn yuma_clips_an_outlier_validator() {
    // one validator pumps B to 100 while the other three say 10: consensus
    // is 10, so B's rank is 40, not 130 (raw sums would pay B 260 of 1000)
    let (z, miners, e, sm, ta, tb) = scored_epoch(
        array![90, 90, 90, 100].span(), array![100, 10, 10, 10].span(),
    );
    start_cheat_block_number_global(z.epoch() * 10 + 10);
    z.settle(e, ta);
    z.settle(e, tb);
    assert(z.yuma(e, ta) == (90, 360, true), 'yuma a');
    assert(z.yuma(e, tb) == (10, 40, true), 'yuma b');
    assert(z.yuma_epoch(e) == (4, 400, 2), 'yuma epoch');
    let to: ContractAddress = 'pb'.try_into().unwrap();
    z.claim(e, arr(miners), to, sign_as(miners, 12, sm, to.into()));
    assert(z.earned(to) == 100, 'B gets 40/400');
    let to_a: ContractAddress = 'pa'.try_into().unwrap();
    z.claim(e, arr(miners), to_a, sign_as(miners, 11, sm, to_a.into()));
    assert(z.earned(to_a) == 900, 'A gets 360/400');
}

#[test]
fn yuma_counts_silent_validators_as_zero() {
    // 3 of 4 score: the silent one is a 0, need stays ceil(4/2) = 2
    let (z, _, e, _, ta, tb) = scored_epoch(array![90, 90, 100].span(), array![60, 60, 60].span());
    start_cheat_block_number_global(z.epoch() * 10 + 10);
    z.settle(e, ta);
    z.settle(e, tb);
    assert(z.yuma(e, ta) == (90, 270, true), 'yuma a');
    assert(z.yuma(e, tb) == (60, 180, true), 'yuma b');
}

#[test]
fn yuma_set_is_snapshotted_at_first_submit() {
    let (z, miners, vals) = setup();
    let e = z.epoch();
    let a = sign_as(miners, 11, z.mine_scope(e), 'w');
    z.submit(arr(miners), 'w', a);
    // the owner floods the set after the epoch started: V stays 4
    snforge_std::start_cheat_caller_address(z.contract_address, owner());
    z.register(ROLE_VALIDATOR, lsag::public_key(31));
    z.register(ROLE_VALIDATOR, lsag::public_key(32));
    z.register(ROLE_VALIDATOR, lsag::public_key(33));
    snforge_std::stop_cheat_caller_address(z.contract_address);
    let tag = lsag::key_id(a.image);
    z.score(arr(vals), tag, 80, sign_as(vals, 21, z.score_scope(e, tag), 80));
    z.score(arr(vals), tag, 80, sign_as(vals, 22, z.score_scope(e, tag), 80));
    start_cheat_block_number_global(z.epoch() * 10 + 10);
    z.settle(e, tag);
    assert(z.yuma(e, tag) == (80, 160, true), '2 of 4 is consensus');
    let (v, _, _) = z.yuma_epoch(e);
    assert(v == 4, 'snapshot');
}

#[test]
#[should_panic(expected: 'epoch not settled')]
fn cannot_claim_before_every_tag_settles() {
    let (z, miners, e, sm, ta, _) = scored_epoch(array![50, 50].span(), array![50, 50].span());
    start_cheat_block_number_global(z.epoch() * 10 + 10);
    z.settle(e, ta);
    let to: ContractAddress = 'x'.try_into().unwrap();
    z.claim(e, arr(miners), to, sign_as(miners, 11, sm, to.into()));
}

#[test]
#[should_panic(expected: 'already settled')]
fn settle_once() {
    let (z, _, e, _, ta, _) = scored_epoch(array![50].span(), array![].span());
    start_cheat_block_number_global(z.epoch() * 10 + 10);
    z.settle(e, ta);
    z.settle(e, ta);
}

#[test]
#[should_panic(expected: 'epoch still open')]
fn cannot_settle_open_epoch() {
    let (z, _, e, _, ta, _) = scored_epoch(array![50].span(), array![].span());
    z.settle(e, ta);
}

fn arr(s: Span<Point>) -> Array<Point> {
    let mut out = array![];
    for p in s {
        out.append(*p);
    };
    out
}
