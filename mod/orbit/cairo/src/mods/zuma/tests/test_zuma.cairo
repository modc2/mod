use snforge_std::{ContractClassTrait, DeclareResultTrait, declare, start_cheat_block_timestamp_global};
use starknet::ContractAddress;
use zuma::lsag;
use zuma::lsag::{Point, Signature};
use zuma::{IZumaDispatcher, IZumaDispatcherTrait, ONE, ROLE_MINER, ROLE_VALIDATOR, seal_of};

fn addr(name: felt252) -> ContractAddress {
    name.try_into().unwrap()
}

fn owner() -> ContractAddress {
    addr('owner')
}

// min ring 3; 100s per stage; 1000 emission per round
fn deploy() -> IZumaDispatcher {
    start_cheat_block_timestamp_global(1000);
    let class = declare("Zuma").unwrap().contract_class();
    let (a, _) = class.deploy(@array![owner().into(), 3, 100, 100, 100, 1000]).unwrap();
    IZumaDispatcher { contract_address: a }
}

// secrets: miners 11..14, validators 21..24
fn keys(base: felt252) -> Array<Point> {
    array![
        lsag::public_key(base + 1), lsag::public_key(base + 2), lsag::public_key(base + 3),
        lsag::public_key(base + 4),
    ]
}

fn ring_of(base: felt252) -> Span<Point> {
    lsag::sort_ring(keys(base).span()).span()
}

fn arr(ring: Span<Point>) -> Array<Point> {
    let mut out = array![];
    for p in ring {
        out.append(*p);
    };
    out
}

fn fill(n: usize, seed: felt252) -> Span<felt252> {
    let mut out = array![];
    for i in 0..n {
        out.append(core::poseidon::poseidon_hash_span(array![seed, i.into()].span()));
    };
    out.span()
}

fn sign_as(ring: Span<Point>, secret: felt252, scope: felt252, msg: felt252) -> Signature {
    let me = lsag::public_key(secret);
    let mut idx = 0;
    while *ring.at(idx) != me {
        idx += 1;
    };
    lsag::sign(ring, idx, secret, scope, msg, secret * 7919 + msg, fill(ring.len(), msg + secret))
}

fn setup() -> IZumaDispatcher {
    let z = deploy();
    snforge_std::start_cheat_caller_address(z.contract_address, owner());
    for k in keys(10) {
        z.register(ROLE_MINER, k, 0);
    };
    for k in keys(20) {
        z.register(ROLE_VALIDATOR, k, 10);
    };
    snforge_std::stop_cheat_caller_address(z.contract_address);
    z
}

fn submit(z: IZumaDispatcher, secret: felt252, work: felt252) -> felt252 {
    let sig = sign_as(ring_of(10), secret, z.mine_scope(z.epoch()), work);
    z.submit(arr(ring_of(10)), work, sig);
    lsag::key_id(sig.image)
}

// a validator's nullifier this round is just its key image's id: it can
// compute it locally, before committing
fn nullifier(z: IZumaDispatcher, secret: felt252) -> felt252 {
    lsag::key_id(lsag::key_image(secret, z.vote_scope(z.epoch())))
}

fn vote(z: IZumaDispatcher, secret: felt252, w: Span<u16>, salt: felt252) -> felt252 {
    let n = nullifier(z, secret);
    let seal = seal_of(z.contract_address, z.epoch(), n, w, salt);
    z.commit(arr(ring_of(20)), seal, sign_as(ring_of(20), secret, z.vote_scope(z.epoch()), seal));
    n
}

fn honest() -> Span<u16> {
    array![4, 3, 2, 1].span()
}

// validator 24 pumps the last tag, which nobody else rates highly
fn pump() -> Span<u16> {
    array![0, 0, 0, 1].span()
}

// one full round; returns (tags, nullifiers)
fn round(z: IZumaDispatcher) -> (Array<felt252>, Array<felt252>) {
    let tags = array![submit(z, 11, 'w1'), submit(z, 12, 'w2'), submit(z, 13, 'w3'), submit(z, 14, 'w4')];
    assert(z.phase() == 1, 'all miners in -> commit');
    let ns = array![
        vote(z, 21, honest(), 'a'), vote(z, 22, honest(), 'b'), vote(z, 23, honest(), 'c'),
        vote(z, 24, pump(), 'd'),
    ];
    assert(z.phase() == 2, 'all sealed -> reveal');
    z.reveal(*ns.at(0), honest(), 'a');
    z.reveal(*ns.at(1), honest(), 'b');
    z.reveal(*ns.at(2), honest(), 'c');
    z.reveal(*ns.at(3), pump(), 'd');
    assert(z.phase() == 3, 'all revealed -> finalize');
    z.finalize();
    (tags, ns)
}

#[test]
fn lsag_links_in_scope_only() {
    let ring = ring_of(10);
    let s1 = sign_as(ring, 12, 'scope-a', 'm1');
    assert(lsag::verify(ring, 'scope-a', 'm1', s1), 'valid sig rejected');
    assert(!lsag::verify(ring, 'scope-a', 'm2', s1), 'wrong msg accepted');
    assert(sign_as(ring, 12, 'scope-a', 'x').image == s1.image, 'not linkable in scope');
    assert(sign_as(ring, 12, 'scope-b', 'm1').image != s1.image, 'linkable across scopes');
}

#[test]
fn yuma_round_clips_the_pump_and_pays_anonymously() {
    let z = setup();
    let (tags, ns) = round(z);
    assert(z.epoch() == 1, 'round rolled');

    // consensus on the pumped tag is the honest weight (1/10), not the pump's 1.0
    let c = z.consensus(0);
    assert(*c.at(3) == ONE / 10, 'pump not clipped');
    let inc = z.incentive(0);
    assert(*inc.at(0) > *inc.at(1) && *inc.at(1) > *inc.at(2), 'incentive order');

    // the pumper earns least among validators
    let d = z.dividends(0);
    assert(*d.at(3) < *d.at(0), 'pumper not penalized');
    assert(*d.at(0) == *d.at(1) && *d.at(1) == *d.at(2), 'honest paid equally');

    // miner 11 claims to a fresh address by re-proving its tag
    let payee = addr('payee');
    let tag0 = *tags.at(0);
    let ring = ring_of(10);
    z.claim_miner(0, arr(ring), payee, sign_as(ring, 11, z.mine_scope(0), payee.into()));
    assert(z.earned(payee) == z.miner_reward(0, tag0), 'miner credit');
    assert(z.earned(payee) > 0 && z.claimed(0, tag0), 'miner claimed');

    // validator 21 claims its dividend by re-proving its nullifier
    let vpay = addr('vpay');
    let vr = ring_of(20);
    z.claim_validator(0, arr(vr), vpay, sign_as(vr, 21, z.vote_scope(0), vpay.into()));
    assert(z.earned(vpay) == z.validator_reward(0, *ns.at(0)), 'validator credit');

    // everything paid adds up to (about) the emission
    let mut total: u128 = 0;
    for t in tags.span() {
        total += z.miner_reward(0, *t);
    };
    for n in ns.span() {
        total += z.validator_reward(0, *n);
    };
    assert(total <= 1000 && total >= 990, 'emission accounted');
}

#[test]
fn tags_and_nullifiers_change_every_round() {
    let z = setup();
    let (t0, n0) = round(z);
    let (t1, n1) = round(z);
    // the same miner (11) and validator (21) look like strangers next round
    assert(*t0.at(0) != *t1.at(0), 'miner tag linkable');
    assert(*n0.at(0) != *n1.at(0), 'nullifier linkable');
}

#[test]
#[should_panic(expected: 'already submitted this round')]
fn one_submission_per_miner() {
    let z = setup();
    submit(z, 12, 'w1');
    submit(z, 12, 'w2');
}

#[test]
#[should_panic(expected: 'already voted this round')]
fn one_vote_per_validator() {
    let z = setup();
    submit(z, 11, 'w');
    start_cheat_block_timestamp_global(1100); // submit window over
    vote(z, 23, array![1].span(), 's1');
    vote(z, 23, array![2].span(), 's2');
}

#[test]
#[should_panic(expected: 'already claimed')]
fn claim_once() {
    let z = setup();
    round(z);
    let ring = ring_of(10);
    let p = addr('p');
    z.claim_miner(0, arr(ring), p, sign_as(ring, 12, z.mine_scope(0), p.into()));
    let q = addr('q');
    z.claim_miner(0, arr(ring), q, sign_as(ring, 12, z.mine_scope(0), q.into()));
}

#[test]
#[should_panic(expected: 'ring has a non-member')]
fn validator_ring_cant_submit_as_miner() {
    let z = setup();
    let vr = ring_of(20);
    z.submit(arr(vr), 'w', sign_as(vr, 21, z.mine_scope(0), 'w'));
}

#[test]
#[should_panic(expected: 'ring below min_ring')]
fn ring_too_small() {
    let z = setup();
    let tiny = lsag::sort_ring(array![lsag::public_key(11), lsag::public_key(12)].span()).span();
    z.submit(arr(tiny), 'w', sign_as(tiny, 11, z.mine_scope(0), 'w'));
}

#[test]
#[should_panic(expected: 'ring mixes stake tiers')]
fn ring_must_be_one_stake_tier() {
    let z = deploy();
    snforge_std::start_cheat_caller_address(z.contract_address, owner());
    for k in keys(10) {
        z.register(ROLE_MINER, k, 0);
    };
    z.register(ROLE_VALIDATOR, lsag::public_key(21), 10);
    z.register(ROLE_VALIDATOR, lsag::public_key(22), 10);
    z.register(ROLE_VALIDATOR, lsag::public_key(23), 99);
    snforge_std::stop_cheat_caller_address(z.contract_address);
    let r = lsag::sort_ring(
        array![lsag::public_key(21), lsag::public_key(22), lsag::public_key(23)].span(),
    )
        .span();
    submit(z, 11, 'w');
    start_cheat_block_timestamp_global(1100);
    z.commit(arr(r), 'seal', sign_as(r, 21, z.vote_scope(0), 'seal'));
}

#[test]
#[should_panic(expected: 'bad reveal')]
fn reveal_must_match_seal() {
    let z = setup();
    round_to_reveal(z);
    z.reveal(nullifier(z, 21), array![9, 9, 9, 9].span(), 'a');
}

#[test]
#[should_panic(expected: 'not commit phase')]
fn no_votes_while_miners_submit() {
    let z = setup();
    submit(z, 11, 'w');
    vote(z, 21, array![1].span(), 's');
}

#[test]
#[should_panic(expected: 'round already running')]
fn no_joining_mid_round() {
    let z = setup();
    submit(z, 11, 'w');
    snforge_std::start_cheat_caller_address(z.contract_address, owner());
    z.register(ROLE_MINER, lsag::public_key(15), 0);
}

#[test]
fn reveal_window_lapses_and_silent_voters_drop_out() {
    let z = setup();
    submit(z, 11, 'w1');
    submit(z, 12, 'w2');
    start_cheat_block_timestamp_global(1100);
    let a = vote(z, 21, array![1, 1].span(), 'a');
    vote(z, 22, array![5, 0].span(), 'b'); // never reveals
    start_cheat_block_timestamp_global(1200);
    assert(z.phase() == 2, 'reveal');
    z.reveal(a, array![1, 1].span(), 'a');
    start_cheat_block_timestamp_global(1300);
    z.finalize();
    let d = z.dividends(0);
    assert(*d.at(0) == ONE && *d.at(1) == 0, 'silent voter paid');
}

fn round_to_reveal(z: IZumaDispatcher) {
    submit(z, 11, 'w1');
    submit(z, 12, 'w2');
    submit(z, 13, 'w3');
    submit(z, 14, 'w4');
    vote(z, 21, honest(), 'a');
    vote(z, 22, honest(), 'b');
    vote(z, 23, honest(), 'c');
    vote(z, 24, pump(), 'd');
}
