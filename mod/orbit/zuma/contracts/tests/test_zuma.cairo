use snforge_std::{declare, ContractClassTrait, DeclareResultTrait, start_cheat_block_number_global};
use starknet::ContractAddress;
use zuma::lsag;
use zuma::lsag::{Point, Signature};
use zuma::zuma::{IZumaDispatcher, IZumaDispatcherTrait, ROLE_MINER, ROLE_VALIDATOR};

fn owner() -> ContractAddress {
    'owner'.try_into().unwrap()
}

fn deploy(tempo: u64, min_ring: u32, emission: u128) -> IZumaDispatcher {
    let class = declare("Zuma").unwrap().contract_class();
    let mut args: Array<felt252> = array![];
    owner().serialize(ref args);
    tempo.serialize(ref args);
    min_ring.serialize(ref args);
    emission.serialize(ref args);
    let (addr, _) = class.deploy(@args).unwrap();
    IZumaDispatcher { contract_address: addr }
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

fn setup() -> (IZumaDispatcher, Span<Point>, Span<Point>) {
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

    // next epoch: miner A claims to a fresh recipient, re-proving its tag
    start_cheat_block_number_global(31);
    let to: ContractAddress = 'payee'.try_into().unwrap();
    z.claim(e, arr(miners), to, sign_as(miners, 11, sm, to.into()));
    assert(z.earned(to) == 800, 'A gets 160/200 of 1000');
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

fn arr(s: Span<Point>) -> Array<Point> {
    let mut out = array![];
    for p in s {
        out.append(*p);
    };
    out
}
